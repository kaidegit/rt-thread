#
# Copyright (c) 2025, RT-Thread Development Team
#
# SPDX-License-Identifier: Apache-2.0
#
# Change Logs:
# Date           Author       Notes
# 2026-09-01     kaidegit     first version: inject C++ config, build and
#                             analyze .init_array in the linker map file
#
"""
CI check for C++ constructor (.init_array) auto loading.

For each BSP given by the SRTT_BSP environment variable (same format as
bsp_buildings.py), this script:

1. appends CONFIG_RT_USING_CPLUSPLUS=y to the BSP .config (backup/restore),
2. builds the BSP with a linker map file (via the RTT_GEN_MAP_FILE hook in
   tools/building.py),
3. analyzes the map file: checks the .init_array output section, counts the
   constructors kept after --gc-sections, and checks the __ctors_start__ /
   __ctors_end__ symbols used by cplusplus_system_init(),
4. writes a markdown report to output/cpp_ctor_check/cpp_ctor_check_report.md.

Usage:
    SRTT_BSP="hpmicro/hpm6750evk,hpmicro/hpm6750evkmini" \
        python tools/ci/bsp_cpp_ctor_check.py
"""
import glob
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bsp_buildings
from bsp_buildings import add_summary, run_cmd

CPP_CTOR_KCONFIG = 'CONFIG_RT_USING_CPLUSPLUS=y'
CI_MAP_NAME = 'rtthread-ci.map'
REPORT_DIR = os.path.join('output', 'cpp_ctor_check')
REPORT_FILE = os.path.join(REPORT_DIR, 'cpp_ctor_check_report.md')


def inject_cpp_config(bsp):
    """
    backup bsp/{bsp}/.config and append the C++ kconfig item.
    """
    config_file = os.path.join(rtt_root, 'bsp', bsp, '.config')
    if not os.path.isfile(config_file):
        return None

    config_backup = config_file + '.cppctor.origin'
    shutil.copyfile(config_file, config_backup)
    with open(config_file, 'a') as destination:
        destination.write(CPP_CTOR_KCONFIG + '\n')

    return config_backup


def restore_cpp_config(bsp, config_backup):
    """
    restore .config and regenerate rtconfig.h from the original .config.
    """
    config_file = os.path.join(rtt_root, 'bsp', bsp, '.config')
    if config_backup and os.path.isfile(config_backup):
        shutil.copyfile(config_backup, config_file)
        os.remove(config_backup)
        os.chdir(rtt_root)
        run_cmd(f'scons -C bsp/{bsp} --pyconfig-silent', output_info=False)


def check_rtconfig_h(bsp):
    """
    check whether RT_USING_CPLUSPLUS really landed in rtconfig.h.
    """
    rtconfig_h = os.path.join(rtt_root, 'bsp', bsp, 'rtconfig.h')
    if not os.path.isfile(rtconfig_h):
        return False

    with open(rtconfig_h, 'r', errors='ignore') as file:
        return '#define RT_USING_CPLUSPLUS' in file.read()


def find_map_file(bsp):
    """
    find the linker map file of the BSP, preferring the CI generated one.
    """
    ci_map = os.path.join(rtt_root, 'bsp', bsp, CI_MAP_NAME)
    if os.path.isfile(ci_map):
        return ci_map

    maps = glob.glob(os.path.join(rtt_root, 'bsp', bsp, '*.map'))
    if maps:
        return max(maps, key=os.path.getmtime)

    return None


def analyze_map(map_path):
    """
    parse a GNU ld map file for .init_array / ctor related information.
    """
    result = {
        'ctors_start': False,
        'ctors_end': False,
        'cplusplus_init': False,
        'init_array_addr': None,
        'init_array_size': None,
        'init_array_entries': [],
    }

    with open(map_path, 'r', errors='ignore') as file:
        text = file.read()

    result['ctors_start'] = '__ctors_start__' in text
    result['ctors_end'] = '__ctors_end__' in text
    result['cplusplus_init'] = 'cplusplus_system_init' in text

    match = re.search(r'^\.init_array\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)', text, re.M)
    if match:
        result['init_array_addr'] = int(match.group(1), 16)
        result['init_array_size'] = int(match.group(2), 16)
        # input section lines are indented under the output section header,
        # stop at the next non-indented (next output section) line
        for line in text[match.end():].splitlines():
            if line.strip() == '':
                continue
            if re.match(r'^\S', line):
                break
            if re.match(r'^\s+\.init_array', line):
                result['init_array_entries'].append(line.strip())

    return result


def report_bsp(report_lines, bsp, verdict, details):
    line = f'- {verdict} `{bsp}`: {details}'
    print(line)
    report_lines.append(line)
    add_summary(line)


if __name__ == "__main__":
    failed = 0
    count = 0

    rtt_root = os.getcwd()
    bsp_buildings.rtt_root = rtt_root

    srtt_bsp = os.getenv('SRTT_BSP')
    if not srtt_bsp:
        print('::error::SRTT_BSP environment variable is not set')
        exit(1)

    os.makedirs(os.path.join(rtt_root, REPORT_DIR), exist_ok=True)
    report_lines = [
        '# BSP C++ ctor (.init_array) check report',
        '',
        f'inject: `{CPP_CTOR_KCONFIG}`, map hook: `RTT_GEN_MAP_FILE={CI_MAP_NAME}`',
        '',
    ]

    for bsp in srtt_bsp.split(','):
        bsp = bsp.strip()
        if not bsp:
            continue
        count += 1
        print(f"::group::C++ ctor check: =={count}=== {bsp} ====")

        config_backup = inject_cpp_config(bsp)
        if config_backup is None:
            report_bsp(report_lines, bsp, '⏭️', 'skip: no .config, cannot inject kconfig')
            print("::endgroup::")
            continue

        os.environ['RTT_GEN_MAP_FILE'] = CI_MAP_NAME
        try:
            build_ok = bsp_buildings.build_bsp(bsp, name='cpp_ctor_check')
        finally:
            del os.environ['RTT_GEN_MAP_FILE']

        injected = check_rtconfig_h(bsp)
        restore_cpp_config(bsp, config_backup)

        if not build_ok:
            failed += 1
            report_bsp(report_lines, bsp, '❌', 'build failed with CONFIG_RT_USING_CPLUSPLUS=y')
            print("::endgroup::")
            continue

        if not injected:
            failed += 1
            report_bsp(report_lines, bsp, '❌', 'build ok but RT_USING_CPLUSPLUS not in rtconfig.h')
            print("::endgroup::")
            continue

        map_path = find_map_file(bsp)
        if map_path is None:
            report_bsp(report_lines, bsp, '⚠️', 'build ok, but no map file generated (non-gcc toolchain or BSP own -Map= missing)')
            print("::endgroup::")
            continue

        info = analyze_map(map_path)
        details = [
            f"map: `{os.path.relpath(map_path, rtt_root)}`",
            f"ctors symbols: {'✅' if info['ctors_start'] and info['ctors_end'] else '❌ __ctors_start__/__ctors_end__ missing'}",
            f"cplusplus_system_init: {'✅' if info['cplusplus_init'] else '❌ missing'}",
        ]
        if info['init_array_size'] is None:
            verdict = '⚠️'
            details.append('.init_array: section not found in map')
        else:
            entry_count = len(info['init_array_entries'])
            details.append(
                f".init_array: addr 0x{info['init_array_addr']:x}, size 0x{info['init_array_size']:x}, {entry_count} input section(s)")
            if info['init_array_size'] == 0:
                verdict = '⚠️'
                details.append('.init_array is empty, no ctor kept (check KEEP in linker script)')
            else:
                verdict = '✅'
            if entry_count:
                details.append('entries: ' + '; '.join(info['init_array_entries'][:10])
                               + ('; ...' if entry_count > 10 else ''))
        if not (info['ctors_start'] and info['ctors_end'] and info['cplusplus_init']):
            verdict = '⚠️'

        report_bsp(report_lines, bsp, verdict, '; '.join(details))
        print("::endgroup::")

    summary_line = f'\nC++ ctor check done: {count} BSP(s), {failed} failed.'
    print(summary_line)
    report_lines.append(summary_line)
    add_summary(summary_line)

    with open(os.path.join(rtt_root, REPORT_FILE), 'w', encoding='utf-8') as file:
        file.write('\n'.join(report_lines) + '\n')

    exit(min(failed, 100))
