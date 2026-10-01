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

#include "laujtag.h"
#include "laupins.h"

#include "driver/gpio.h"
#include "esp_attr.h"
#include "sdkconfig.h"
#include "soc/gpio_reg.h"

// ALL FOUR JTAG PINS ARE BELOW GPIO32 SO ONE PAIR OF SET/CLEAR REGISTERS DRIVES THEM
#define JTAGTCKMASK     (1u << PINJTAGTCK)
#define JTAGTDIMASK     (1u << PINJTAGTDI)
#define JTAGTMSMASK     (1u << PINJTAGTMS)

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static inline void halfPeriodDelay(void)
{
    for (volatile int count = 0; count < CONFIG_LAU_JTAG_HALF_PERIOD_SPIN; count++) {
        ;
    }
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static inline int clockTck(int tms, int tdi)
{
    // DROP TCK TOGETHER WITH THE NEW TMS AND TDI SINCE THE FPGA SAMPLES THEM ON THE RISING EDGE
    uint32_t setMask = 0;
    uint32_t clearMask = JTAGTCKMASK;
    if (tms) {
        setMask |= JTAGTMSMASK;
    } else {
        clearMask |= JTAGTMSMASK;
    }
    if (tdi) {
        setMask |= JTAGTDIMASK;
    } else {
        clearMask |= JTAGTDIMASK;
    }
    REG_WRITE(GPIO_OUT_W1TC_REG, clearMask);
    REG_WRITE(GPIO_OUT_W1TS_REG, setMask);
    halfPeriodDelay();

    // READ TDO AFTER IT HAS HAD HALF A PERIOD TO SETTLE FROM THE FALLING EDGE, THEN RAISE TCK
    int tdo = (REG_READ(GPIO_IN_REG) >> PINJTAGTDO) & 1;
    REG_WRITE(GPIO_OUT_W1TS_REG, JTAGTCKMASK);
    halfPeriodDelay();
    return (tdo);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
void lauJtagInit(void)
{
    // TURN THE BUFFER OFF FIRST, R2 ALREADY HOLDS /OE HIGH BUT MAKE IT EXPLICIT
    gpio_set_level(PINJTAGOEN, 1);
    gpio_config_t outputEnableConfig = { .pin_bit_mask = 1ULL << PINJTAGOEN, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&outputEnableConfig);
    gpio_set_level(PINJTAGOEN, 1);

    // CONFIGURE TCK, TDI, AND TMS AS OUTPUTS AND PARK THEM LOW
    gpio_config_t outputConfig = {
        .pin_bit_mask = (1ULL << PINJTAGTCK) | (1ULL << PINJTAGTDI) | (1ULL << PINJTAGTMS),
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&outputConfig);
    REG_WRITE(GPIO_OUT_W1TC_REG, JTAGTCKMASK | JTAGTDIMASK | JTAGTMSMASK);

    // TDO IS AN INPUT THAT R3 PARKS LOW WHILE U2 IS OFF
    gpio_config_t inputConfig = { .pin_bit_mask = 1ULL << PINJTAGTDO, .mode = GPIO_MODE_INPUT };
    gpio_config(&inputConfig);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
void lauJtagEnable(bool enableFlag)
{
    if (enableFlag) {
        // HOLD TMS HIGH SO ANY STRAY TCK EDGE WALKS THE TAP TOWARD TEST-LOGIC-RESET
        REG_WRITE(GPIO_OUT_W1TC_REG, JTAGTCKMASK | JTAGTDIMASK);
        REG_WRITE(GPIO_OUT_W1TS_REG, JTAGTMSMASK);
        gpio_set_level(PINJTAGOEN, 0);
    } else {
        gpio_set_level(PINJTAGOEN, 1);
        REG_WRITE(GPIO_OUT_W1TC_REG, JTAGTCKMASK | JTAGTDIMASK | JTAGTMSMASK);
    }
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
void lauJtagReset(void)
{
    // SIX CLOCKS WITH TMS HIGH REACH TEST-LOGIC-RESET FROM ANY STATE, ONE LOW GOES TO IDLE
    for (int count = 0; count < 6; count++) {
        clockTck(1, 0);
    }
    clockTck(0, 0);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
uint32_t lauJtagShiftIR(uint32_t instruction, int bits)
{
    // RUN-TEST/IDLE -> SELECT-DR -> SELECT-IR -> CAPTURE-IR -> SHIFT-IR
    clockTck(1, 0);
    clockTck(1, 0);
    clockTck(0, 0);
    clockTck(0, 0);

    // SHIFT THE INSTRUCTION LSB FIRST AND COLLECT THE CAPTURED VALUE, LEAVING ON THE LAST BIT
    uint32_t capture = 0;
    for (int bit = 0; bit < bits; bit++) {
        capture |= (uint32_t)clockTck(bit == bits - 1, (instruction >> bit) & 1) << bit;
    }

    // EXIT1-IR -> UPDATE-IR -> RUN-TEST/IDLE
    clockTck(1, 0);
    clockTck(0, 0);
    return (capture);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
uint32_t lauJtagShiftDR32(uint32_t value)
{
    // RUN-TEST/IDLE -> SELECT-DR -> CAPTURE-DR -> SHIFT-DR
    clockTck(1, 0);
    clockTck(0, 0);
    clockTck(0, 0);

    uint32_t result = 0;
    for (int bit = 0; bit < 32; bit++) {
        result |= (uint32_t)clockTck(bit == 31, (value >> bit) & 1) << bit;
    }

    // EXIT1-DR -> UPDATE-DR -> RUN-TEST/IDLE
    clockTck(1, 0);
    clockTck(0, 0);
    return (result);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
void lauJtagRunTest(uint32_t clocks)
{
    for (uint32_t count = 0; count < clocks; count++) {
        clockTck(0, 0);
    }
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
int lauJtagChainLength(void)
{
    lauJtagReset();

    // PUT EVERY DEVICE INTO BYPASS BY SHIFTING FAR MORE ONES THAN ANY TOTAL IR LENGTH
    clockTck(1, 0);
    clockTck(1, 0);
    clockTck(0, 0);
    clockTck(0, 0);
    for (int bit = 0; bit < 64; bit++) {
        clockTck(bit == 63, 1);
    }
    clockTck(1, 0);
    clockTck(0, 0);

    // FLUSH THE ONE-BIT BYPASS REGISTERS WITH ZEROS, THEN COUNT CLOCKS UNTIL A ONE COMES OUT
    clockTck(1, 0);
    clockTck(0, 0);
    clockTck(0, 0);
    for (int bit = 0; bit < 32; bit++) {
        clockTck(0, 0);
    }
    int chainLength = 0;
    for (int bit = 0; bit < 32; bit++) {
        if (clockTck(0, 1)) {
            chainLength = bit;
            break;
        }
    }

    // EXIT1-DR -> UPDATE-DR -> RUN-TEST/IDLE AND LEAVE THE CHAIN IN A KNOWN STATE
    clockTck(1, 1);
    clockTck(1, 0);
    clockTck(0, 0);
    lauJtagReset();
    return (chainLength);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
void lauJtagShiftDRBegin(void)
{
    // RUN-TEST/IDLE -> SELECT-DR -> CAPTURE-DR -> SHIFT-DR
    clockTck(1, 0);
    clockTck(0, 0);
    clockTck(0, 0);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
void IRAM_ATTR lauJtagShiftDRBytes(const uint8_t *buffer, size_t length, bool finalFlag)
{
    // SEND EACH BYTE MSB FIRST, WHICH IS THE ORDER THE CONFIGURATION LOGIC EXPECTS
    for (size_t index = 0; index < length; index++) {
        uint8_t byte = buffer[index];
        bool lastByteFlag = finalFlag && (index == length - 1);
        for (int bit = 7; bit >= 0; bit--) {
            clockTck(lastByteFlag && (bit == 0), (byte >> bit) & 1);
        }
    }

    // EXIT1-DR -> UPDATE-DR -> RUN-TEST/IDLE AFTER THE LAST CHUNK
    if (finalFlag) {
        clockTck(1, 0);
        clockTck(0, 0);
    }
}
