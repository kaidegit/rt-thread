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


def match_init_array_entry(lines, i):
    """
    match a .init_array* input section at lines[i]; returns
    ((name, addr, size, module), consumed_lines) or (None, 0).
    GNU ld may wrap a long section name onto its own line, with the
    address/size/module on the next line, so handle both forms.
    """
    entry = re.match(r'^\s+(\.init_array\S*)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s*(.*)', lines[i])
    if entry:
        return (entry.group(1), int(entry.group(2), 16), int(entry.group(3), 16),
                entry.group(4).strip()), 1

    wrapped = re.match(r'^\s+(\.init_array\S+)\s*$', lines[i])
    if wrapped and i + 1 < len(lines):
        detail = re.match(r'^\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s*(.*)', lines[i + 1])
        if detail:
            return (wrapped.group(1), int(detail.group(1), 16), int(detail.group(2), 16),
                    detail.group(3).strip()), 2

    return None, 0


def analyze_map(map_path):
    """
    parse a GNU ld map file for .init_array / ctor related information.

    Handles both layouts:
    - a dedicated `.init_array` output section at column 0;
    - `.init_array*` input sections nested inside another output section
      (e.g. RT-Thread linker scripts often KEEP them inside .data).
    Also checks the "Discarded input sections" block: .init_array entries
    collected there mean --gc-sections would drop real ctors (missing KEEP).
    """
    result = {
        'ctors_start': False,
        'ctors_end': False,
        'cplusplus_init': False,
        'init_array_addr': None,
        'init_array_size': None,
        'init_array_entries': [],    # dicts: name, addr, size, output, module
        'init_array_discarded': [],  # .init_array* input section names gc'ed
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

    discarded = re.search(r'^Discarded input sections\n(.*?)\n\n', text, re.S | re.M)
    if discarded:
        lines = discarded.group(1).splitlines()
        i = 0
        while i < len(lines):
            entry, consumed = match_init_array_entry(lines, i)
            if entry:
                result['init_array_discarded'].append(
                    f'{entry[0]} ({entry[3]})' if entry[3] else entry[0])
            i += consumed or 1

    memory_map = text.split('Linker script and memory map', 1)
    if len(memory_map) == 2:
        lines = memory_map[1].splitlines()
        current_output = None
        i = 0
        while i < len(lines):
            output_sec = re.match(r'^(\.\S+)\s+0x[0-9a-fA-F]+', lines[i])
            if output_sec:
                current_output = output_sec.group(1)
                i += 1
                continue
            entry, consumed = match_init_array_entry(lines, i)
            if entry:
                result['init_array_entries'].append({
                    'name': entry[0],
                    'addr': entry[1],
                    'size': entry[2],
                    'output': current_output,
                    'module': entry[3],
                })
            i += consumed or 1

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
        ctors_ok = info['ctors_start'] and info['ctors_end']
        details = [
            f"map: `{os.path.relpath(map_path, rtt_root)}`",
            f"ctors symbols: {'✅' if ctors_ok else '⚠️ __ctors_start__/__ctors_end__ not in map'}",
            f"cplusplus_system_init: {'✅' if info['cplusplus_init'] else '⚠️ not in map'}",
        ]
        if info['init_array_size'] is not None:
            details.append(
                f".init_array output section: addr 0x{info['init_array_addr']:x}, size 0x{info['init_array_size']:x}")

        entries = info['init_array_entries']
        if entries:
            total_size = sum(e['size'] for e in entries)
            outputs = sorted({e['output'] for e in entries if e['output']})
            details.append(f"{len(entries)} ctor entry(ies), total 0x{total_size:x} byte(s), in {', '.join(outputs)}")
            preview = '; '.join(f"{e['name']} ({e['module']})" for e in entries[:5])
            details.append('entries: ' + preview + ('; ...' if len(entries) > 5 else ''))
        if info['init_array_discarded']:
            details.append(f"{len(info['init_array_discarded'])} .init_array input section(s) discarded by --gc-sections: "
                           + ', '.join(info['init_array_discarded'][:5]))

        if not (ctors_ok and info['cplusplus_init']):
            verdict = '⚠️'
            details.append('ctor init plumbing not visible in map')
        elif info['init_array_discarded']:
            verdict = '⚠️'
            details.append('ctors would be dropped by --gc-sections, linker script needs KEEP(*(.init_array*))')
        elif entries:
            verdict = '✅'
        elif info['init_array_size'] is not None:
            verdict = '✅'
            details.append('no ctor entries (nothing to run, plumbing OK)')
        else:
            verdict = '⚠️'
            details.append('no .init_array output section or input entries found in map')

        report_bsp(report_lines, bsp, verdict, '; '.join(details))
        print("::endgroup::")

    summary_line = f'\nC++ ctor check done: {count} BSP(s), {failed} failed.'
    print(summary_line)
    report_lines.append(summary_line)
    add_summary(summary_line)

    with open(os.path.join(rtt_root, REPORT_FILE), 'w', encoding='utf-8') as file:
        file.write('\n'.join(report_lines) + '\n')

    exit(min(failed, 100))
