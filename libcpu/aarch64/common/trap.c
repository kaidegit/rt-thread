/*
 * Copyright (c) 2006-2018, RT-Thread Development Team
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Change Logs:
 * Date           Author       Notes
 * 2013-07-20     Bernard      first version
 */

#include <rtthread.h>
#include <rthw.h>
#include <board.h>

#include <armv8.h>
#include "interrupt.h"

void rt_unwind(struct rt_hw_exp_stack *regs, int pc_adj)
{
}

#ifdef RT_USING_FINSH
extern long list_thread(void);
#endif

#ifdef RT_USING_LWP
#include <lwp.h>
#include <lwp_arch.h>

#ifdef LWP_USING_CORE_DUMP
#include <lwp_core_dump.h>
#endif

void sys_exit(int value);
void check_user_fault(struct rt_hw_exp_stack *regs, uint32_t pc_adj, char *info)
{
    uint32_t mode = regs->spsr;

    if ((mode & 0x1f) == 0x00)
    {
        rt_kprintf("%s! pc = 0x%08x\n", info, regs->pc - pc_adj);
#ifdef LWP_USING_CORE_DUMP
        lwp_core_dump(regs, pc_adj);
#endif
        sys_exit(-1);
    }
}

int check_user_stack(unsigned long esr, struct rt_hw_exp_stack *regs)
{
    unsigned char ec;
    void *dfar;
    int ret = 0;

    ec = (unsigned char)((esr >> 26) & 0x3fU);
    switch (ec)
    {
    case 0x20:
    case 0x21:
    case 0x24:
        asm volatile ("mrs %0, far_el1":"=r"(dfar));
        if (arch_expand_user_stack(dfar))
        {
            ret = 1;
        }
        break;
    default:
        break;
    }
    return ret;
}
#endif

/**
 * this function will show registers of CPU
 *
 * @param regs the registers point
 */
void rt_hw_show_register(struct rt_hw_exp_stack *regs)
{
    rt_kprintf("Execption:\n");
    rt_kprintf("X00:0x%16.16p X01:0x%16.16p X02:0x%16.16p X03:0x%16.16p\n", (void *)regs->x0, (void *)regs->x1, (void *)regs->x2, (void *)regs->x3);
    rt_kprintf("X04:0x%16.16p X05:0x%16.16p X06:0x%16.16p X07:0x%16.16p\n", (void *)regs->x4, (void *)regs->x5, (void *)regs->x6, (void *)regs->x7);
    rt_kprintf("X08:0x%16.16p X09:0x%16.16p X10:0x%16.16p X11:0x%16.16p\n", (void *)regs->x8, (void *)regs->x9, (void *)regs->x10, (void *)regs->x11);
    rt_kprintf("X12:0x%16.16p X13:0x%16.16p X14:0x%16.16p X15:0x%16.16p\n", (void *)regs->x12, (void *)regs->x13, (void *)regs->x14, (void *)regs->x15);
    rt_kprintf("X16:0x%16.16p X17:0x%16.16p X18:0x%16.16p X19:0x%16.16p\n", (void *)regs->x16, (void *)regs->x17, (void *)regs->x18, (void *)regs->x19);
    rt_kprintf("X20:0x%16.16p X21:0x%16.16p X22:0x%16.16p X23:0x%16.16p\n", (void *)regs->x20, (void *)regs->x21, (void *)regs->x22, (void *)regs->x23);
    rt_kprintf("X24:0x%16.16p X25:0x%16.16p X26:0x%16.16p X27:0x%16.16p\n", (void *)regs->x24, (void *)regs->x25, (void *)regs->x26, (void *)regs->x27);
    rt_kprintf("X28:0x%16.16p X29:0x%16.16p X30:0x%16.16p\n", (void *)regs->x28, (void *)regs->x29, (void *)regs->x30);
    rt_kprintf("SP_EL0:0x%16.16p\n", (void *)regs->sp_el0);
    rt_kprintf("SPSR  :0x%16.16p\n", (void *)regs->spsr);
    rt_kprintf("EPC   :0x%16.16p\n", (void *)regs->pc);
}

/**
 * An abort indicates that the current memory access cannot be completed,
 * which occurs during an instruction prefetch.
 *
 * @param regs system registers
 *
 * @note never invoke this function in application
 */
void rt_hw_trap_pabt(struct rt_hw_exp_stack *regs)
{
#ifdef RT_USING_LWP
#ifdef RT_USING_GDBSERVER
    if (check_debug_event(regs, 4))
    {
        return;
    }
#endif
    check_user_fault(regs, 4, "User prefetch abort");
#endif
    rt_unwind(regs, 4);
    rt_kprintf("prefetch abort:\n");
    rt_hw_show_register(regs);
#ifdef RT_USING_FINSH
    list_thread();
#endif
    rt_hw_cpu_shutdown();
}

void rt_hw_trap_irq(void)
{
#ifdef SOC_BCM283x
    extern rt_uint8_t core_timer_flag;
    void *param;
    uint32_t irq;
    rt_isr_handler_t isr_func;
    extern struct rt_irq_desc isr_table[];
    uint32_t value = 0;
    value = IRQ_PEND_BASIC & 0x3ff;

    if(core_timer_flag != 0)
    {
        uint32_t cpu_id = rt_hw_cpu_id();
        uint32_t int_source = CORE_IRQSOURCE(cpu_id);
        if (int_source & 0x0f)
        {
            if (int_source & 0x08)
            {
                isr_func = isr_table[IRQ_ARM_TIMER].handler;
                #ifdef RT_USING_INTERRUPT_INFO
                            isr_table[IRQ_ARM_TIMER].counter++;
                #endif
                if (isr_func)
                {
                    param = isr_table[IRQ_ARM_TIMER].param;
                    isr_func(IRQ_ARM_TIMER, param);
                }
            }
        }
    }

    /* local interrupt*/
    if (value)
    {
        if (value & (1 << 8))
        {
            value = IRQ_PEND1;
            irq = __rt_ffs(value) - 1;
        }
        else if (value & (1 << 9))
        {
            value = IRQ_PEND2;
            irq = __rt_ffs(value) + 31;
        }
        else
        {
            value &= 0x0f;
            irq = __rt_ffs(value) + 63;
        }

        /* get interrupt service routine */
        isr_func = isr_table[irq].handler;
#ifdef RT_USING_INTERRUPT_INFO
        isr_table[irq].counter++;
#endif
        if (isr_func)
        {
            /* Interrupt for myself. */
            param = isr_table[irq].param;
            /* turn to interrupt service routine */
            isr_func(irq, param);
        }
    }
#else
    void *param;
    int ir;
    rt_isr_handler_t isr_func;
    extern struct rt_irq_desc isr_table[];

    ir = rt_hw_interrupt_get_irq();

    if (ir == 1023)
    {
        /* Spurious interrupt */
        return;
    }

    /* get interrupt service routine */
    isr_func = isr_table[ir].handler;
#ifdef RT_USING_INTERRUPT_INFO
    isr_table[ir].counter++;
#endif
    if (isr_func)
    {
        /* Interrupt for myself. */
        param = isr_table[ir].param;
        /* turn to interrupt service routine */
        isr_func(ir, param);
    }

    /* end of interrupt */
    rt_hw_interrupt_ack(ir);
#endif
}

void rt_hw_trap_fiq(void)
{
    void *param;
    int ir;
    rt_isr_handler_t isr_func;
    extern struct rt_irq_desc isr_table[];

    ir = rt_hw_interrupt_get_irq();

    /* get interrupt service routine */
    isr_func = isr_table[ir].handler;
    param = isr_table[ir].param;

    /* turn to interrupt service routine */
    isr_func(ir, param);

    /* end of interrupt */
    rt_hw_interrupt_ack(ir);
}

void process_exception(unsigned long esr, unsigned long epc);
void SVC_Handler(struct rt_hw_exp_stack *regs);
void rt_hw_trap_exception(struct rt_hw_exp_stack *regs)
{
    unsigned long esr;
    unsigned char ec;

    asm volatile ("mrs %0, esr_el1":"=r"(esr));
    ec = (unsigned char)((esr >> 26) & 0x3fU);

    if (ec == 0x15) /* is 64bit syscall ? */
    {
        SVC_Handler(regs);
        /* never return here */
    }

    if (check_user_stack(esr, regs))
    {
        return;
    }

    process_exception(esr, regs->pc);
    rt_hw_show_register(regs);
    rt_kprintf("current: %s\n", rt_thread_self()->name);
    check_user_fault(regs, 0, "user fault");
#ifdef RT_USING_FINSH
    list_thread();
#endif
    rt_hw_cpu_shutdown();
}

void rt_hw_trap_serror(struct rt_hw_exp_stack *regs)
{
    rt_kprintf("SError\n");
    rt_hw_show_register(regs);
    rt_kprintf("current: %s\n", rt_thread_self()->name);
#ifdef RT_USING_FINSH
    list_thread();
#endif
    rt_hw_cpu_shutdown();
}