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
    USB 3 SuperSpeed   ~196 MB/s   ~120 fps   (M7 soak: 117.8 fps, host-limited)
    USB 2 High Speed    ~49 MB/s    ~30 fps   (~4x slower; viewer visibly lags)

LIVE CAPTURE IS SENSOR-LIMITED, NOT LINK-LIMITED, AND THE PASS THRESHOLD HAS TO
SAY SO. The sensor reads out 120.000 fps; at 1.6384 MB that is 196.6 MB/s, and
that IS full rate. The link can do more -- 325 MB/s was measured replaying from
DDR faster than the camera fills it -- but no live stream will ever reach that,
because there is nothing to send.

The first version of this script judged "OK" on >200 MB/s and duly reported a
perfect 196.5 MB/s / 119.9 fps run as a shortfall, pointing at the FPGA. That is
this project's recurring failure in miniature: an instrument confidently wrong
about healthy hardware. Judge on FPS AGAINST THE SENSOR RATE, not on bytes
against the link ceiling.
"""
import ctypes, sys, time

import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX, FT_FLAGS_HISPEED, FT_FLAGS_SUPERSPEED

IN_PIPE = 0x82
CH = 1 << 22
FBYTES = 1280 * 1024 * 10 // 8      # packed-10 frame
FPS_SENSOR = 120.0                  # the sensor rate; the real ceiling
FPS_OK = 110.0                      # allow host-limited skid (soak saw 117.5)

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

    fps = (tot / FBYTES) / dt
    print()
    if ss and fps >= FPS_OK:
        print("OK -- USB 3 at %.1f fps. That is the SENSOR's rate (120.000 Hz),"
              " which is\nfull rate for live capture. The link has headroom above"
              " it and always will." % fps)
    elif ss and fps > 1.0:
        print("USB 3 negotiated but only %.1f fps (%.1f MB/s) against the sensor's"
              " 120.\nThe LINK is fine, so look at the FPGA end: is the camera"
              " streaming, and is\nthe ring reader running?" % (fps, mbs))
    elif ss:
        print("USB 3 negotiated but NOTHING is arriving. The FT601 enumerates from"
              " its own\nEEPROM whether or not the FPGA is configured, so this is"
              " what an UNCONFIGURED\nFPGA looks like -- most often a --ram load"
              " lost to a power cycle, and the board\nis USB-powered, so"
              " re-plugging it counts. Re-load the bitstream.")
    else:
        print("USB 2. Expect about a quarter of full rate, and a viewer that lags"
              "\nbecause the sensor still produces 120 fps the link cannot carry."
              "\n\nIn order of likelihood:"
              "\n  1. a USB 2 CABLE in a USB 3 port -- looks identical, very common"
              "\n  2. a USB 2 port, or a hub/dock in the path -- go direct, rear panel"
              "\n  3. marginal SuperSpeed signal integrity forcing a fallback"
              "\n\nRe-plug and run this again; you want flags 0x4.")
    return 0 if (ss and fps >= FPS_OK) else 1


if __name__ == "__main__":
    raise SystemExit(main())
