// Bit-banged JTAG master for one Xilinx 7-series part behind U2 (SN74LVC125A).
// Every function except jtag_init/jtag_enable starts and ends in Run-Test/Idle.
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// 7-series JTAG instructions (6-bit IR on the XC7A100T)
#define XC7_IR_LEN      6
#define XC7_CFG_IN      0x05
#define XC7_IDCODE      0x09
#define XC7_JPROGRAM    0x0B
#define XC7_JSTART      0x0C
#define XC7_ISC_NOOP    0x14
#define XC7_BYPASS      0x3F

// IR capture bits (UG470): the only way to see INIT on this card, since INIT_B is not on J3
#define XC7_IRCAP_INIT_COMPLETE (1u << 4)
#define XC7_IRCAP_DONE          (1u << 5)

void jtag_init(void);

// true: enable U2 (drive the FPGA's JTAG). false: tri-state U2 so the Pt's FT2232H owns the bus.
// The card must leave this false whenever it is not actively shifting (README §4.2).
void jtag_enable(bool on);

void jtag_reset(void);                                   // TLR, then RTI
uint32_t jtag_shift_ir(uint32_t ir, int bits);           // returns the captured IR
uint32_t jtag_shift_dr32(uint32_t tdi);                  // 32-bit DR shift, returns TDO
void jtag_run_test(uint32_t clocks);                     // TMS=0 clocks in RTI

// Number of devices in the chain (BYPASS method). 0 = no TDO activity / broken chain.
int jtag_chain_length(void);

// Streamed DR shift of configuration data, each byte MSB first (Xilinx bitstream order).
// dr_begin: RTI -> Shift-DR. Pass final=true on the last chunk to exit through Update-DR to RTI.
void jtag_dr_begin(void);
void jtag_dr_bytes_msb(const uint8_t *buf, size_t n, bool final);
