"""Ask the camera how long it can expose at the current frame rate.

    python host/max_exposure.py [COM6]              -- just ask
    python host/max_exposure.py [COM6] --apply      -- ask, then set it

The FPGA answers from two things it MEASURED, not from anything hard-coded in
the host:

    max_exposure = vsync_period - 44.1 us (sensor gap) - 10 us (margin)

The 44.1 us came from sweeping the trigger period against exposure: min_period =
exposure + 44.1 us, slope 1.0015 over a 6.0-8.0 ms span, i.e. a constant. At
120 Hz the formula returns 8279 us against the 8280 us clamp that was previously
found by walking into the cliff -- reproducing a known-good number is what makes
it trustworthy rather than merely plausible.

TWO ANSWERS THAT ARE NOT A NUMBER, and both matter:

  valid = 0    The vsync period was not plausible -- no edges, counter
               saturated. There is NO safe default here. Commanding an exposure
               longer than the frame period wedges the sensor until the FPGA is
               reconfigured, which is the exact failure this feature exists to
               prevent, so it refuses rather than guesses.

  reg_limited  The 16-bit exposure register ran out before the frame period did.
               65535 x 375 ns = 24.576 ms, so below about 40.7 Hz the binding
               limit is the REGISTER, not the frame. The value returned is the
               register maximum and is genuinely the most you can ask for -- it
               is just not the frame's limit.

The value is in EXPOSURE REGISTER UNITS so it can be written straight back with
opcode 1: no conversion, and no rounding at the one boundary where a rounding
error costs you a reconfigure.
"""
import os, sys, time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = "COM6"
APPLY = False
for a in sys.argv[1:]:
    if a == "--apply":
        APPLY = True
    else:
        PORT = a

SYNC, OP_R = 0xA5, 0x52
EXPO_UNIT_US = 0.375


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def rd(ser, addr, window=0.7):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    t0, b = time.time(), b""
    while time.time() - t0 < window:
        b += ser.read(64)
        for i in range(len(b) - 2):
            if b[i] == addr and ((b[i] + b[i + 1] + b[i + 2]) & 0xFF) == 0:
                return b[i + 1]
    return None


def main():
    ser = serial.Serial(PORT, 115200, timeout=0.3)
    time.sleep(0.4)

    lo, hi = rd(ser, 0x53), rd(ser, 0x54)
    fl = rd(ser, 0x55)
    rlo, rhi = rd(ser, 0x56), rd(ser, 0x57)
    per = [rd(ser, 0x4A), rd(ser, 0x4B), rd(ser, 0x4C)]
    # The camera's OWN measured frame period, regs 0x3E/0x3F (period/16 in
    # 72 MHz wordclk cycles). See THE GENLOCK TRAP below -- this is not the same
    # thing as the vsync period until the camera is actually locked to it.
    clo, chi = rd(ser, 0x3E), rd(ser, 0x3F)
    if None in (lo, hi, fl, rlo, rhi, clo, chi) or None in per:
        ser.close()
        sys.exit("register read timed out -- is the board running this bitstream?")

    max_reg = lo | (hi << 8)
    valid = bool(fl & 0x80)
    rlim = bool(fl & 0x40)
    reserve_us = ((rlo | (rhi << 8)) * 10) / 1000.0
    period_us = (per[0] | (per[1] << 8) | (per[2] << 16)) * 10 / 1000.0

    print("max usable exposure, computed by the FPGA\n")
    print("  vsync period   : %.3f us  (%.3f Hz)"
          % (period_us, 1e6 / period_us if period_us else 0))
    print("  reserved       : %.1f us  (measured sensor gap + margin)" % reserve_us)

    if not valid:
        print("  max exposure   : INVALID")
        print("\n  The vsync period is not plausible, so no answer is given. That is")
        print("  deliberate: there is no safe default. An exposure longer than the")
        print("  frame period wedges the sensor until the FPGA is reconfigured.")
        print("  Check that the output is actually running before asking again.")
        ser.close()
        return 1

    print("  max exposure   : %d register units = %.1f us"
          % (max_reg, max_reg * EXPO_UNIT_US))
    if rlim:
        print("\n  NOTE: limited by the 16-BIT REGISTER, not by the frame period.")
        print("  65535 x 375 ns = 24.576 ms is all the register can express, and at")
        print("  %.1f Hz the frame would allow more. This is still the most you can"
              % (1e6 / period_us))
        print("  ask for -- it is just not the frame's limit.")
    else:
        headroom = period_us - max_reg * EXPO_UNIT_US
        print("  duty cycle     : %.2f %%   (%.1f us not integrating)"
              % (100.0 * max_reg * EXPO_UNIT_US / period_us, headroom))

    # ---- THE GENLOCK TRAP -------------------------------------------------
    # The FPGA answers for the HDMI FRAME RATE, which is what was asked for. But
    # until genlock exists the camera is NOT triggered by vsync -- it free-runs
    # on its own TRIG_CY. If the camera's period is SHORTER than the display's,
    # the answer is longer than the camera's own frame and writing it wedges the
    # sensor until the FPGA is reconfigured.
    #
    # Measured here rather than assumed: 0x3E/0x3F is the camera's own frame
    # period. On the bench today that is 8333 us against a 9718 us display, so
    # the honest answer is 1331 us shorter than the register reports.
    cam_us = ((clo | (chi << 8)) * 16) / 72.0
    cam_max_reg = int(max(0.0, (cam_us - reserve_us)) / EXPO_UNIT_US)
    locked = abs(cam_us - period_us) < 50.0
    safe_reg = max_reg if locked else min(max_reg, cam_max_reg)

    print("\n  camera period  : %.1f us  (%.2f Hz)  <- what the sensor is ACTUALLY"
          " triggered at" % (cam_us, 1e6 / cam_us if cam_us else 0))
    if not locked:
        print("\n  ** NOT GENLOCKED. The camera free-runs on TRIG_CY; it is not")
        print("     triggered by vsync. The %d above is correct FOR THE DISPLAY"
              % max_reg)
        print("     and %s than the camera's own frame allows."
              % ("LONGER" if max_reg > cam_max_reg else "shorter"))
        print("     Safe value for the camera as configured: %d = %.1f us"
              % (safe_reg, safe_reg * EXPO_UNIT_US))
        print("     Writing %d would command an exposure longer than the frame"
              % max_reg)
        print("     period, which wedges the sensor until reconfigure.")

    if APPLY:
        try:
            from ftlink import FtLink
        except Exception as e:
            ser.close()
            sys.exit("--apply needs the Ft+ link (opcode 1): %s" % e)
        link = FtLink()
        link.send_word((1 << 28) | (safe_reg & 0xFFFF))
        time.sleep(1.0)
        link.close()
        print("\n  applied: exposure set to %d over the Ft+ (opcode 1)%s"
              % (safe_reg,
                 "" if locked else "   [CLAMPED to the camera's own period]"))
        print("  NOTE: exposure changes take ONE FRAME to land unless")
        print("  reg_seq_exposure_sync_mode is set -- see the datasheet, p20.")

    ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
