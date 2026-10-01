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

#ifndef LAUPINS_H
#define LAUPINS_H

// ESP32-S3-MINI-1U-N8 PIN MAP FOR THE LAUESP32CONFIG_PT_STACK CARD, READ FROM THE SCHEMATIC
// NETLIST (U1 PADS TO NETS) ON 2026-10-01. CHANGE THE SCHEMATIC FIRST, THEN THIS FILE.

// JTAG THROUGH U2 (SN74LVC125A) AND THE REV A STRAPS
#define PINJTAGTCK          4   // U1 PAD 8   ESP_TCK  -> U2 1A -> R10 0R -> J3 PIN 49
#define PINJTAGTDI          5   // U1 PAD 9   ESP_TDI  -> U2 3A -> R12 0R -> J3 PIN 43
#define PINJTAGTDO          6   // U1 PAD 10  ESP_TDO  <- U2 4Y <- R13 33R <- J3 PIN 45 (R3 10K PULL-DOWN)
#define PINJTAGTMS          7   // U1 PAD 11  ESP_TMS  -> U2 2A -> R11 0R -> J3 PIN 47
#define PINJTAGOEN          15  // U1 PAD 19  ALL FOUR /OE PINS, R2 10K PULL-UP KEEPS THE BUFFER OFF

// FPGA CONTROL THROUGH THE 2N7002 FETS, THE PT CARRIES THE PULL-UPS
#define PINFPGADONE         16  // U1 PAD 20  <- R6 1K <- J3 PIN 39, INPUT ONLY OR IT HOLDS OFF STARTUP
#define PINPROGRAMENABLE    17  // U1 PAD 21  Q1 GATE (R4 10K PULL-DOWN) PULLS PROGRAM_B (J3 PIN 41) LOW
#define PINRESETENABLE      18  // U1 PAD 22  Q2 GATE (R5 10K PULL-DOWN) PULLS FPGA_RESET (J3 PIN 37) LOW

// MICROSD CARD, SDMMC 4-BIT THROUGH THE GPIO MATRIX, 10K PULL-UPS R31-R35 ON CMD AND D0-D3
#define PINSDCLK            36  // U1 PAD 32  THROUGH R30 33R
#define PINSDCMD            35  // U1 PAD 31
#define PINSDDATA0          37  // U1 PAD 33
#define PINSDDATA1          38  // U1 PAD 34
#define PINSDDATA2          33  // U1 PAD 28
#define PINSDDATA3          34  // U1 PAD 29
#define PINSDCARDDETECT     47  // U1 PAD 27  R36 PULL-UP, POLARITY NOT YET VERIFIED ON HARDWARE

// STATUS LED: LED_STATUS -> R7 1K -> D1 -> GND SO IT IS ACTIVE HIGH
#define PINSTATUSLED        48  // U1 PAD 30

// FABRIC SIDE CHANNEL (README SECTION 3B), WIRED BUT NOT YET USED BY THIS FIRMWARE
#define PINFABRICIRQ        8
#define PINFABRICIO3        9
#define PINFABRICCS         10
#define PINFABRICMOSI       11
#define PINFABRICSCK        12
#define PINFABRICMISO       13
#define PINFABRICIO2        14
#define PINFABRICSPARE      21

#endif // LAUPINS_H
