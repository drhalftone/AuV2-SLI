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

#ifndef LAUBITFILE_H
#define LAUBITFILE_H

#include <stddef.h>
#include <stdint.h>

// XILINX .BIT HEADER PARSER, PLAIN C WITH NO ESP-IDF DEPENDENCY SO IT CAN BE TESTED ON A HOST

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
typedef struct {
    char designName[160];     // FIELD 'a', OFTEN WITH ";UserID=...;Version=..."
    char partName[48];        // FIELD 'b', SUCH AS "7a100tfgg484"
    char buildDate[24];       // FIELD 'c'
    char buildTime[24];       // FIELD 'd'
    uint32_t dataOffset;      // BYTE OFFSET OF THE CONFIGURATION DATA IN THE FILE
    uint32_t dataLength;      // FIELD 'e', LENGTH OF THE CONFIGURATION DATA IN BYTES
} LAUBitFileInfo;

// PARSE THE FIRST LENGTH BYTES OF A .BIT FILE, 512 IS PLENTY FOR REAL HEADERS
// RETURNS 0 ON SUCCESS AND A NEGATIVE NUMBER FOR A MALFORMED OR TRUNCATED HEADER
int lauBitFileParse(const uint8_t *buffer, size_t length, LAUBitFileInfo *info);

#endif // LAUBITFILE_H
