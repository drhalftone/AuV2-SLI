"""Is the Ft+ actually on USB 3? Answer it in one command, two ways.

    python host/usb_speed.py [seconds]

WHY THIS EXISTS. A USB 2 cable mates with the FT601's connector and enumerates
happily in a SuperSpeed port -- so a USB2 link looks exactly like a USB3 one from
the outside, and the only symptom is that everything is about 4x slower. That
reads as "the FPGA got slow", which sends you looking in the wrong place. It cost
a bench session once; hence a command that just says which it is.

TWO CHECKS, because either alone can mislead:

  * ENUMERATED SPEED, from the D3XX device flags. Definitive about what the link
    negotiated -- but says nothing about whether the pipe actually delivers.
  * MEASURED THROUGHPUT, read flat out from the IN pipe. Definitive about what
    you will actually get -- but a slow number could be starvation at the FPGA
    end rather than the link, so it needs the flag to interpret it.

Together they separate "the link is USB2" from "the link is USB3 but the source
is not filling it".

REFERENCE NUMBERS on this design, 1280x1024 packed-10 (1.6384 MB/frame):
    USB 3 SuperSpeed   ~325 MB/s   ~120 fps   (M7 soak: 117.8 fps, host-limited)
    USB 2 High Speed    ~49 MB/s    ~30 fps   (~4x slower; viewer visibly lags)
"""
import ctypes, sys, time

import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX, FT_FLAGS_HISPEED, FT_FLAGS_SUPERSPEED

IN_PIPE = 0x82
CH = 1 << 22
FBYTES = 1280 * 1024 * 10 // 8      # packed-10 frame

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0


def main():
    if ftd3xx.createDeviceInfoList() < 1:
        sys.exit("no D3XX device -- is the Ft+ plugged in and the board configured?")
    nd = ftd3xx.getDeviceInfoList()[0]

    ss = bool(nd.Flags & FT_FLAGS_SUPERSPEED)
    hs = bool(nd.Flags & FT_FLAGS_HISPEED)
    speed = "USB 3 SuperSpeed" if ss else ("USB 2 High Speed" if hs else "unknown")
    print("device : %s" % nd.Description.decode(errors="replace"))
    print("flags  : 0x%X -> %s" % (nd.Flags, speed))

    d = ftd3xx.create(0, FT_OPEN_BY_INDEX)
    if d is None:
        sys.exit("device present but could not be opened -- is the viewer still"
                 " running? It holds the handle exclusively.")
    for fn in ("abortPipe", "flushPipe"):
        try: getattr(d, fn)(IN_PIPE)
        except Exception: pass
    d.setPipeTimeout(IN_PIPE, 500)

    buf = ctypes.create_string_buffer(CH)
    for _ in range(5):                      # warm up; the first reads are ragged
        d.readPipe(IN_PIPE, buf, CH)

    tot, t0 = 0, time.time()
    while time.time() - t0 < SECS:
        tot += d.readPipe(IN_PIPE, buf, CH)
    dt = time.time() - t0
    d.close()

    mbs = (tot / 1e6) / dt
    print("stream : %.1f MB in %.2f s = %.1f MB/s (%.1f fps at %d B/frame)"
          % (tot / 1e6, dt, mbs, (tot / FBYTES) / dt, FBYTES))

    print()
    if ss and mbs > 200:
        print("OK -- USB 3, full rate.")
    elif ss:
        print("USB 3 negotiated but only %.1f MB/s. The LINK is fine, so look at"
              " the\nFPGA end: is the camera streaming, and is the ring reader"
              " running?" % mbs)
    else:
        print("USB 2. Expect about a quarter of full rate, and a viewer that lags"
              "\nbecause the sensor still produces 120 fps the link cannot carry."
              "\n\nIn order of likelihood:"
              "\n  1. a USB 2 CABLE in a USB 3 port -- looks identical, very common"
              "\n  2. a USB 2 port, or a hub/dock in the path -- go direct, rear panel"
              "\n  3. marginal SuperSpeed signal integrity forcing a fallback"
              "\n\nRe-plug and run this again; you want flags 0x4.")
    return 0 if (ss and mbs > 200) else 1


if __name__ == "__main__":
    raise SystemExit(main())
