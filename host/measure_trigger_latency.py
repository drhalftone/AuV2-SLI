"""How long after the trigger does the sensor actually start integrating -- and how much does it vary?

    python host/measure_trigger_latency.py [COM6]

WHAT IS BEING TESTED, AND THE PREDICTION IT IS TESTING.

The datasheet gives NO trigger-to-exposure delay and NO jitter figure for it. The
only `tj` in the electrical tables is INPUT CLOCK jitter (20 ps), which is a
different parameter entirely. But page 24 says:

    "The start of the exposure time is synchronized to the start of a new line
     (during ROT) if the exposure period starts during a frame readout."

In pipelined mode integration always begins during a readout, so this always
applies: the start SNAPS to the sensor's internal line boundary. Measured line
time is ~5.7 us (readout floor 5840 us / 1024 lines). And the trigger period is
not a whole number of lines --

    8333.3 us / 5.7 us = 1461.9 lines

-- so the phase walks and the snap lands somewhere different every frame.

    PREDICTION: 0 to ~5.7 us of JITTER on the trigger-to-integration delay,
    which a fixed delay register CANNOT absorb.

    FALSIFIED IF: max - min is far below one line time (the snap is not
    happening, or the trigger is already line-aligned), or far above it
    (something other than line quantisation dominates).

For comparison: AVT-class cameras quote up to ~30 us of trigger delay. That is a
CAMERA figure -- their FPGA and firmware sit between the trigger connector and
the sensor. Here the FPGA is the camera and drives the sensor pin directly, so
that path does not exist. Anything measured here is the sensor itself.

WHERE THE NUMBERS COME FROM. monitor0 carries INTEGRATION TIME (monitor_select
0x1 in sensor register 192[13:11], which the boot ROM already writes as part of
0x0801). The FPGA timestamps its edges against the trigger at 100 MHz and
publishes min/max over each ~10 Hz window:

    0x42/0x43/0x44   trigger -> integration start, MINIMUM, 10 ns units
    0x45/0x46/0x47   same, MAXIMUM
    0x48/0x49        last integration length, 160 ns units

Read over the SERIAL port on purpose: it is the independent witness, and it stays
up in exactly the cases where the frame stream does not.
"""
import os, sys, time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ftlink import FtLink

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
SYNC, OP_R = 0xA5, 0x52
TICK_NS = 10.0            # 100 MHz
DUR_NS = 160.0            # dur_p is in 16-tick units
LINE_US = 5.7             # measured: readout floor 5840 us / 1024 lines


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


def rd24(ser, a):
    lo, mid, hi = rd(ser, a), rd(ser, a + 1), rd(ser, a + 2)
    if None in (lo, mid, hi):
        return None
    return lo | (mid << 8) | (hi << 16)


def rd16(ser, a):
    lo, hi = rd(ser, a), rd(ser, a + 1)
    if None in (lo, hi):
        return None
    return lo | (hi << 8)


def main():
    link = FtLink()          # only to confirm the camera is actually streaming
    n0, t0 = link.frames, time.time()
    while time.time() - t0 < 1.5:
        link.pump()
    fps = (link.frames - n0) / (time.time() - t0)
    link.close()

    ser = serial.Serial(PORT, 115200, timeout=0.3)
    time.sleep(0.4)

    print("trigger -> integration latency, from monitor0\n")
    print("  camera streaming at %.1f fps%s\n"
          % (fps, "" if fps > 50 else "   <-- NOT STREAMING, results meaningless"))
    print("   sample   min_us    max_us   jitter_us   integration_us")
    print("   " + "-" * 56)

    jit = []
    for i in range(12):
        mn = rd24(ser, 0x42)
        mx = rd24(ser, 0x45)
        du = rd16(ser, 0x48)
        if None in (mn, mx, du):
            print("   %6d   (timeout reading the status registers)" % i)
            time.sleep(0.4)
            continue
        mn_us, mx_us = mn * TICK_NS / 1000.0, mx * TICK_NS / 1000.0
        du_us = du * DUR_NS / 1000.0
        jit.append(mx_us - mn_us)
        print("   %6d  %8.2f  %8.2f   %9.2f   %14.1f"
              % (i, mn_us, mx_us, mx_us - mn_us, du_us))
        time.sleep(0.35)

    ser.close()

    if jit:
        j = sum(jit) / len(jit)
        print("\n  mean jitter over %d windows: %.2f us   (one line = %.1f us)"
              % (len(jit), j, LINE_US))
        if j < 0.5:
            print("  -> BELOW quantisation. Either the snap is not happening, or the")
            print("     trigger already lands on a line boundary. A fixed delay")
            print("     register WOULD be able to absorb this.")
        elif j <= LINE_US * 1.3:
            print("  -> CONSISTENT with the predicted line-boundary snap. A fixed")
            print("     delay register cannot absorb it; it is jitter, not offset.")
        else:
            print("  -> LARGER than one line time. Something other than line")
            print("     quantisation dominates -- do not design around the line")
            print("     figure until that is understood.")


if __name__ == "__main__":
    main()
