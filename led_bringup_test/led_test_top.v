// ============================================================================
// led_test_top.v  --  Stack-board bring-up smoke test (Alchitry Au V2)
// ----------------------------------------------------------------------------
// Mirrors the 8 Bank-B signals coming up through the LauCameraTrigger_Alchitry_Stack
// board (DF40 -> Hd pass-through -> Au) onto the 8 user LEDs on the Au V2.
//
//   - Drive a line HIGH  -> its LED turns ON.
//   - Drive a line LOW   -> its LED turns OFF.
//
// Sources of "high/low":
//   * The 4 DIP switches (SW1, SPDT): one orientation = +3V3 (LED on),
//     the other = GND (LED off).
//   * The 4 camera GPIO lines through the MASTER JST. Note only CAM_MODE and
//     CAM_READY are camera-OUTPUTS (the camera can drive them). CAM_TRIG and
//     CAM_PATTERN are camera-INPUTS; jumper them or use the Alvium GPIO direction
//     setting to exercise their LEDs.
//
// Purely combinational -- no clock, no MMCM, no timing constraints. If an LED
// follows its input, that ball and the full DF40 -> Hd -> Au path are good.
//
// LED map (Au V2 silk led0..led7):
//   led[0] = CAM_READY    (Bank-B ball R11, DF40 pin 27, JST pin 5)
//   led[1] = CAM_TRIG     (Bank-B ball R16, DF40 pin 28, JST pin 2)
//   led[2] = CAM_PATTERN  (Bank-B ball R10, DF40 pin 29, JST pin 4)
//   led[3] = CAM_MODE     (Bank-B ball R15, DF40 pin 30, JST pin 3)
//   led[4] = SW_HVSV      (Bank-B ball K5 , DF40 pin 33, SW1-1)
//   led[5] = SW_BLUE      (Bank-B ball N16, DF40 pin 34, SW1-2)
//   led[6] = SW_GREEN     (Bank-B ball E6 , DF40 pin 35, SW1-3)
//   led[7] = SW_RED       (Bank-B ball M16, DF40 pin 36, SW1-4)
// ============================================================================

module led_test_top (
    // --- Camera GPIO lines (Bank B, via MASTER JST) ---
    input  wire cam_ready,    // R11
    input  wire cam_trig,     // R16
    input  wire cam_pattern,  // R10
    input  wire cam_mode,     // R15
    // --- DIP-switch config lines (Bank B, SW1) ---
    input  wire sw_hvsv,      // K5
    input  wire sw_blue,      // N16
    input  wire sw_green,     // E6
    input  wire sw_red,       // M16
    // --- 8 user LEDs (active high on the Au V2) ---
    output wire [7:0] led
);

    assign led[0] = cam_ready;
    assign led[1] = cam_trig;
    assign led[2] = cam_pattern;
    assign led[3] = cam_mode;
    assign led[4] = sw_hvsv;
    assign led[5] = sw_blue;
    assign led[6] = sw_green;
    assign led[7] = sw_red;

endmodule
