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

#include "laubitfile.h"

#include <string.h>

// THE .BIT LAYOUT USES BIG-ENDIAN LENGTHS:
//   U16 N, N BYTES OF PREAMBLE (0F F0 0F F0 0F F0 0F F0 00), U16 = 1, THEN KEYED FIELDS
//   WHERE 'a' THROUGH 'd' ARE A U16 LENGTH PLUS A NUL TERMINATED STRING AND 'e' IS A
//   U32 LENGTH FOLLOWED BY THE CONFIGURATION DATA

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static int readUInt16(const uint8_t *buffer, size_t length, size_t *index, uint32_t *value)
{
    if (*index + 2 > length) {
        return (-1);
    }
    *value = ((uint32_t)buffer[*index] << 8) | buffer[*index + 1];
    *index += 2;
    return (0);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static int readUInt32(const uint8_t *buffer, size_t length, size_t *index, uint32_t *value)
{
    if (*index + 4 > length) {
        return (-1);
    }
    *value = ((uint32_t)buffer[*index] << 24) | ((uint32_t)buffer[*index + 1] << 16) | ((uint32_t)buffer[*index + 2] << 8) | buffer[*index + 3];
    *index += 4;
    return (0);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static void copyField(char *destination, size_t capacity, const uint8_t *source, uint32_t length)
{
    // COPY AS MUCH AS FITS AND ALWAYS TERMINATE, WHICH ALSO BOUNDS A MALFORMED FIELD
    size_t count = (length < capacity - 1) ? length : capacity - 1;
    memcpy(destination, source, count);
    destination[count] = 0;
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
int lauBitFileParse(const uint8_t *buffer, size_t length, LAUBitFileInfo *info)
{
    memset(info, 0, sizeof(*info));

    // SKIP OVER THE PREAMBLE AND CHECK FOR THE U16 THAT ALWAYS FOLLOWS IT
    size_t index = 0;
    uint32_t value;
    if (readUInt16(buffer, length, &index, &value) || value > 64 || index + value > length) {
        return (-1);
    }
    index += value;
    if (readUInt16(buffer, length, &index, &value) || value != 1) {
        return (-2);
    }

    // WALK THROUGH THE KEYED FIELDS UNTIL WE REACH THE CONFIGURATION DATA
    for (int field = 0; field < 8; field++) {
        if (index >= length) {
            return (-3);
        }
        uint8_t key = buffer[index++];
        if (key == 'e') {
            if (readUInt32(buffer, length, &index, &value)) {
                return (-4);
            }
            info->dataOffset = (uint32_t)index;
            info->dataLength = value;

            // A HEADER WITHOUT A PART NAME IS NOT ONE WE TRUST
            if (info->partName[0]) {
                return (0);
            } else {
                return (-5);
            }
        }
        if (key < 'a' || key > 'd') {
            return (-6);
        }
        if (readUInt16(buffer, length, &index, &value) || index + value > length) {
            return (-7);
        }
        switch (key) {
            case 'a':
                copyField(info->designName, sizeof(info->designName), buffer + index, value);
                break;
            case 'b':
                copyField(info->partName, sizeof(info->partName), buffer + index, value);
                break;
            case 'c':
                copyField(info->buildDate, sizeof(info->buildDate), buffer + index, value);
                break;
            case 'd':
                copyField(info->buildTime, sizeof(info->buildTime), buffer + index, value);
                break;
        }
        index += value;
    }
    return (-8);
}
