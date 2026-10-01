#include "jtag.h"

#include "driver/gpio.h"
#include "esp_attr.h"
#include "pins.h"
#include "sdkconfig.h"
#include "soc/gpio_reg.h"

// All four JTAG pins are below GPIO32, so one W1TS/W1TC register pair drives them.
#define TCK_M (1u << PIN_JTAG_TCK)
#define TDI_M (1u << PIN_JTAG_TDI)
#define TMS_M (1u << PIN_JTAG_TMS)

static inline void spin(void)
{
    for (volatile int i = 0; i < CONFIG_LAU_JTAG_HALF_PERIOD_SPIN; i++) {
    }
}

// One TCK cycle. TCK falls together with the new TMS/TDI (the FPGA samples them on the rising
// edge), TDO is read after the half-period it needs to settle from that falling edge, then TCK
// rises. Returns the TDO bit that was valid before this rising edge.
static inline int clk(int tms, int tdi)
{
    uint32_t set = 0, clr = TCK_M;
    if (tms) set |= TMS_M; else clr |= TMS_M;
    if (tdi) set |= TDI_M; else clr |= TDI_M;
    REG_WRITE(GPIO_OUT_W1TC_REG, clr);
    REG_WRITE(GPIO_OUT_W1TS_REG, set);
    spin();
    int tdo = (REG_READ(GPIO_IN_REG) >> PIN_JTAG_TDO) & 1;
    REG_WRITE(GPIO_OUT_W1TS_REG, TCK_M);
    spin();
    return tdo;
}

void jtag_init(void)
{
    // Buffer OFF first: R2 already holds /OE high, make it explicit before anything else moves.
    gpio_set_level(PIN_JTAG_OE_N, 1);
    gpio_config_t oe = {.pin_bit_mask = 1ULL << PIN_JTAG_OE_N, .mode = GPIO_MODE_OUTPUT};
    gpio_config(&oe);
    gpio_set_level(PIN_JTAG_OE_N, 1);

    gpio_config_t out = {
        .pin_bit_mask = (1ULL << PIN_JTAG_TCK) | (1ULL << PIN_JTAG_TDI) | (1ULL << PIN_JTAG_TMS),
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&out);
    REG_WRITE(GPIO_OUT_W1TC_REG, TCK_M | TDI_M | TMS_M);

    gpio_config_t in = {.pin_bit_mask = 1ULL << PIN_JTAG_TDO, .mode = GPIO_MODE_INPUT};
    gpio_config(&in);   // R3 10k parks it low while U2 is off
}

void jtag_enable(bool on)
{
    if (on) {
        REG_WRITE(GPIO_OUT_W1TC_REG, TCK_M | TDI_M);
        REG_WRITE(GPIO_OUT_W1TS_REG, TMS_M);   // TMS high: any stray TCK edge walks toward TLR
    }
    gpio_set_level(PIN_JTAG_OE_N, on ? 0 : 1);
    if (!on) REG_WRITE(GPIO_OUT_W1TC_REG, TCK_M | TDI_M | TMS_M);
}

void jtag_reset(void)
{
    for (int i = 0; i < 6; i++) clk(1, 0);
    clk(0, 0);   // -> RTI
}

uint32_t jtag_shift_ir(uint32_t ir, int bits)
{
    clk(1, 0); clk(1, 0); clk(0, 0); clk(0, 0);   // RTI -> SelDR -> SelIR -> Capture -> Shift-IR
    uint32_t cap = 0;
    for (int i = 0; i < bits; i++) {
        cap |= (uint32_t)clk(i == bits - 1, (ir >> i) & 1) << i;
    }
    clk(1, 0); clk(0, 0);                          // Exit1 -> Update-IR -> RTI
    return cap;
}

uint32_t jtag_shift_dr32(uint32_t tdi)
{
    clk(1, 0); clk(0, 0); clk(0, 0);               // RTI -> SelDR -> Capture -> Shift-DR
    uint32_t out = 0;
    for (int i = 0; i < 32; i++) {
        out |= (uint32_t)clk(i == 31, (tdi >> i) & 1) << i;
    }
    clk(1, 0); clk(0, 0);
    return out;
}

void jtag_run_test(uint32_t clocks)
{
    while (clocks--) clk(0, 0);
}

int jtag_chain_length(void)
{
    jtag_reset();
    // Every device into BYPASS: shift far more ones than any plausible total IR length.
    clk(1, 0); clk(1, 0); clk(0, 0); clk(0, 0);
    for (int i = 0; i < 64; i++) clk(i == 63, 1);
    clk(1, 0); clk(0, 0);
    // Flush the 1-bit bypass registers with zeros, then count clocks until a one comes out.
    clk(1, 0); clk(0, 0); clk(0, 0);
    for (int i = 0; i < 32; i++) clk(0, 0);
    int n = -1;
    for (int i = 0; i < 32; i++) {
        if (clk(0, 1)) { n = i; break; }
    }
    clk(1, 1); clk(1, 0); clk(0, 0);               // Exit1 -> Update-DR -> RTI
    jtag_reset();
    return n < 0 ? 0 : n;
}

void jtag_dr_begin(void)
{
    clk(1, 0); clk(0, 0); clk(0, 0);
}

void IRAM_ATTR jtag_dr_bytes_msb(const uint8_t *buf, size_t n, bool final)
{
    for (size_t k = 0; k < n; k++) {
        uint8_t b = buf[k];
        bool last_byte = final && k == n - 1;
        for (int bit = 7; bit >= 0; bit--) {
            clk(last_byte && bit == 0, (b >> bit) & 1);
        }
    }
    if (final) {
        clk(1, 0); clk(0, 0);                      // Exit1 -> Update-DR -> RTI
    }
}
