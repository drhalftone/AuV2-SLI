/*********************************************************************************
 *                                                                               *
 * Copyright (c) 2026, Dr. Daniel L. Lau                                         *
 * All rights reserved.                                                          *
 *                                                                               *
 * Redistribution and use in source and binary forms, with or without            *
 * modification, is strictly forbidden.                                          *
 *                                                                               *
 * THIS SOFTWARE IS PROVIDED BY DR. DANIEL L. LAU ''AS IS'' AND ANY              *
 * EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED     *
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE        *
 * DISCLAIMED. IN NO EVENT SHALL DR. DANIEL L. LAU BE LIABLE FOR ANY             *
 * DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES    *
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;  *
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND   *
 * ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT    *
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS *
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.                  *
 *                                                                               *
 *********************************************************************************/

#ifndef LAUJTAG_H
#define LAUJTAG_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// BIT-BANGED JTAG MASTER FOR ONE XILINX 7-SERIES PART BEHIND U2 (SN74LVC125A)
// EVERY FUNCTION EXCEPT INIT AND ENABLE STARTS AND ENDS IN RUN-TEST/IDLE

// 7-SERIES JTAG INSTRUCTIONS, THE XC7A100T HAS A 6-BIT INSTRUCTION REGISTER
#define XC7IRLENGTH                 6
#define XC7CFGIN                    0x05
#define XC7IDCODE                   0x09
#define XC7JPROGRAM                 0x0B
#define XC7JSTART                   0x0C
#define XC7ISCNOOP                  0x14
#define XC7BYPASS                   0x3F

// INSTRUCTION CAPTURE BITS FROM UG470, THE ONLY VIEW OF INIT SINCE INIT_B IS NOT ON J3
#define XC7IRCAPTUREINITCOMPLETE    (1u << 4)
#define XC7IRCAPTUREDONE            (1u << 5)

void lauJtagInit(void);

// TRUE TURNS ON U2 AND DRIVES THE FPGA, FALSE TRI-STATES U2 SO THE PT'S FT2232H OWNS THE BUS
// THE CARD MUST LEAVE THIS FALSE WHENEVER IT IS NOT ACTIVELY SHIFTING (README SECTION 4.2)
void lauJtagEnable(bool enableFlag);

void lauJtagReset(void);
uint32_t lauJtagShiftIR(uint32_t instruction, int bits);
uint32_t lauJtagShiftDR32(uint32_t value);
void lauJtagRunTest(uint32_t clocks);

// NUMBER OF DEVICES IN THE CHAIN USING THE BYPASS METHOD, ZERO MEANS A BROKEN CHAIN
int lauJtagChainLength(void);

// STREAMED DATA REGISTER SHIFT OF CONFIGURATION DATA WITH EACH BYTE SENT MSB FIRST, PASS
// FINALFLAG ON THE LAST CHUNK TO EXIT THROUGH UPDATE-DR BACK TO RUN-TEST/IDLE
void lauJtagShiftDRBegin(void);
void lauJtagShiftDRBytes(const uint8_t *buffer, size_t length, bool finalFlag);

#endif // LAUJTAG_H
