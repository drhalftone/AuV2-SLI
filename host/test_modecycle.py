#!/usr/bin/env python3
"""Cycle this PC's HDMI output modes and prove the FPGA follows -- without
disturbing the camera.

    python host/test_modecycle.py                 # every mode the FPGA display offers
    python host/test_modecycle.py --dry-run       # list the modes, change nothing
    python host/test_modecycle.py --settle 4.0    # longer wait per mode
    python host/test_modecycle.py --modes 1280x720@60,1024x768@60

TWO CLAIMS, TESTED SEPARATELY, because they fail differently.

  1. PASS-THROUGH FOLLOWS THE SOURCE. For each mode the PC is set to, the FPGA's
     OWN measurement of the incoming raster (regs 0x60..0x67, video_meas) must
     match what Windows says it is sending. This is the claim that "the FPGA
     changed its output resolution" -- and note it is measured on the FPGA, not
     inferred from the PC. Asking Windows what it set and calling that a pass
     would prove nothing about the FPGA at all.

  2. THE CAMERA DOES NOT NOTICE. Across the whole sweep the camera must keep
     streaming with its frame period unchanged, ldrop static, and no FIFO
     overflow. This is M3d from MERGE_MILESTONES.md generalised from one mode
     change to every mode the source can produce.

WHY ldrop IS THE SHARP ONE. "Frames still arrive" is a weak check -- the camera
can keep streaming while silently dropping. ldrop counts padded frames and is
static when nothing is lost, so a single increment anywhere in the sweep is a
failure even if the rate looks perfect.

BASELINE FIRST, AND IT CAN FAIL. The script reads the camera and HDMI state
before touching anything. If the camera is not streaming, or the HDMI input is
not being measured, it says so and stops rather than running a sweep whose
result would be meaningless. A test that "passes" because neither subsystem was
doing anything is worse than no test.

THE DISPLAY MODE IS RESTORED on exit, including on Ctrl-C and on failure.
"""
import argparse
import ctypes
import sys
import time
from ctypes import wintypes

SYNC, OP_R = 0xA5, 0x52
u32 = ctypes.windll.user32

ENUM_CURRENT = -1
CDS_UPDATEREGISTRY = 0x01
DISP_CHANGE_SUCCESSFUL = 0


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


class DEVMODE(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
                ("dmPosX", ctypes.c_long), ("dmPosY", ctypes.c_long),
                ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
                ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
                ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


# ---------------------------------------------------------------------------
# Windows display side
# ---------------------------------------------------------------------------

def find_fpga_display(pnp_hint="CBCF20A"):
    """The output whose monitor is the FPGA. Located by EDID PnP ID, not by index.

    Display indices move when monitors are plugged, slept or re-ordered, so an
    index would silently point at the wrong screen -- and this script CHANGES
    RESOLUTION, which is not something to do to the wrong monitor.
    """
    i = 0
    while True:
        dd = DISPLAY_DEVICE()
        dd.cb = ctypes.sizeof(dd)
        if not u32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        if dd.StateFlags & 1:
            mon = DISPLAY_DEVICE()
            mon.cb = ctypes.sizeof(mon)
            if u32.EnumDisplayDevicesW(dd.DeviceName, 0, ctypes.byref(mon), 0):
                if pnp_hint.upper() in mon.DeviceID.upper():
                    return dd.DeviceName, mon.DeviceID
        i += 1
    return None, None


def current_mode(dev):
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(dm)
    if not u32.EnumDisplaySettingsW(dev, ENUM_CURRENT, ctypes.byref(dm)):
        return None
    return dm


def list_modes(dev, min_w=640):
    seen, out, i = set(), [], 0
    while True:
        dm = DEVMODE()
        dm.dmSize = ctypes.sizeof(dm)
        if not u32.EnumDisplaySettingsW(dev, i, ctypes.byref(dm)):
            break
        i += 1
        if dm.dmBitsPerPel < 32 or dm.dmPelsWidth < min_w:
            continue
        key = (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return sorted(out)


def set_mode(dev, w, h, hz):
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(dm)
    u32.EnumDisplaySettingsW(dev, ENUM_CURRENT, ctypes.byref(dm))
    dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency = w, h, hz
    dm.dmFields = 0x80000 | 0x100000 | 0x400000       # WIDTH | HEIGHT | FREQUENCY
    return u32.ChangeDisplaySettingsExW(dev, ctypes.byref(dm), None,
                                        CDS_UPDATEREGISTRY, None)


# ---------------------------------------------------------------------------
# FPGA side
# ---------------------------------------------------------------------------

def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


class Board:
    def __init__(self, port):
        import serial
        self.s = serial.Serial(port, 115200, timeout=0.2)
        time.sleep(0.25)

    def rd(self, a, window=0.6):
        self.s.reset_input_buffer()
        self.s.write(bytes([SYNC, OP_R, a, ck(OP_R + a)]))
        buf, dl = bytearray(), time.time() + window
        while time.time() < dl:
            buf += self.s.read(self.s.in_waiting or 1)
            for i in range(len(buf) - 2):
                if buf[i] == a and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                    return buf[i + 1]
        return None

    def rdn(self, a, n):
        v = 0
        for i in range(n):
            b = self.rd(a + i)
            if b is None:
                return None
            v |= b << (8 * i)
        return v

    def hdmi(self):
        """Measured INCOMING raster (video_meas, regs 0x60..0x67)."""
        st = self.rd(0x67)
        if st is None:
            return None
        return dict(vid_valid=(st >> 1) & 1, meas_ok=st & 1,
                    hact=self.rdn(0x60, 2), vact=self.rdn(0x62, 2),
                    per=self.rdn(0x64, 3))

    def camera(self):
        a = self.rd(0x3A)
        b = self.rd(0x3B)
        if a is None or b is None:
            return None
        return dict(aligned=(a >> 2) & 1, streaming=(a >> 1) & 1, cap=a & 1,
                    calib=(a >> 3) & 1,
                    cfifo_ovf=(b >> 7) & 1, ufifo_ovf=(b >> 6) & 1,
                    ldrop=self.rdn(0x3C, 2), period16=self.rdn(0x3E, 2))

    def close(self):
        self.s.close()


def fps_of(period16):
    return 72e6 / (period16 * 16.0) if period16 else 0.0


def hz_of(per):
    return 1e8 / per if per else 0.0


# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--settle", type=float, default=3.0,
                   help="seconds to wait after each mode change before measuring")
    p.add_argument("--dry-run", action="store_true", help="list modes, change nothing")
    p.add_argument("--modes", default=None, help="comma list like 1280x720@60,800x600@60")
    p.add_argument("--pnp", default="CBCF20A", help="EDID PnP id of the FPGA display")
    p.add_argument("--camera-only", action="store_true",
                   help="judge ONLY the camera half. Use when the pass-through "
                        "measurement is known broken: the question 'does the camera "
                        "survive mode changes' is still answerable, and is still worth "
                        "answering. HDMI is recorded but not scored.")
    a = p.parse_args()

    dev, devid = find_fpga_display(a.pnp)
    if not dev:
        sys.exit("No display with PnP id %r is attached. Is the board's HDMI plugged "
                 "into this PC and enumerated?" % a.pnp)
    print("FPGA display : %s  (%s)" % (dev, devid))

    modes = list_modes(dev)
    if a.modes:
        want = set()
        for tok in a.modes.split(","):
            wh, _, hz = tok.strip().partition("@")
            w, _, h = wh.partition("x")
            want.add((int(w), int(h), int(hz or 60)))
        modes = [m for m in modes if m in want]
    print("modes offered: %d" % len(modes))
    for w, h, hz in modes:
        print("   %4dx%-5d @ %3d Hz" % (w, h, hz))
    if a.dry_run:
        return 0

    board = Board(a.port)
    original = current_mode(dev)
    orig = (original.dmPelsWidth, original.dmPelsHeight, original.dmDisplayFrequency)
    print("\ncurrent mode : %dx%d @ %d Hz  (will be restored)" % orig)

    # ---- baseline, and it is allowed to refuse -------------------------------
    base_cam = board.camera()
    base_hdmi = board.hdmi()
    print("\n=== BASELINE ===")
    if base_cam is None or base_hdmi is None:
        board.close()
        sys.exit("board did not answer -- is a control bitstream loaded?")
    print("  camera : streaming=%d aligned=%d calib=%d ldrop=%d %.3f fps"
          % (base_cam["streaming"], base_cam["aligned"], base_cam["calib"],
             base_cam["ldrop"], fps_of(base_cam["period16"])))
    print("  hdmi   : vid_valid=%d meas_ok=%d  %sx%s @ %.2f Hz"
          % (base_hdmi["vid_valid"], base_hdmi["meas_ok"],
             base_hdmi["hact"], base_hdmi["vact"], hz_of(base_hdmi["per"])))
    problems = []
    if not base_cam["streaming"]:
        problems.append("camera is not streaming -- the camera half of this test "
                        "would pass trivially")
    if not base_hdmi["meas_ok"] and not a.camera_only:
        problems.append("HDMI input is not being measured (meas_ok=0) -- the "
                        "pass-through half cannot be judged. Pass --camera-only to "
                        "test just the camera, which is still a real question.")
    if problems:
        board.close()
        print("\nREFUSING TO RUN:")
        for s in problems:
            print("  * " + s)
        print("\nA sweep run in this state would report results that mean nothing.")
        return 2

    rows, failures = [], []
    try:
        for w, h, hz in modes:
            rc = set_mode(dev, w, h, hz)
            if rc != DISP_CHANGE_SUCCESSFUL:
                rows.append((w, h, hz, "SKIP", "ChangeDisplaySettings rc=%d" % rc, ""))
                continue
            time.sleep(a.settle)
            m = board.hdmi()
            c = board.camera()
            if m is None or c is None:
                rows.append((w, h, hz, "FAIL", "board stopped answering", ""))
                failures.append((w, h, hz, "no reply"))
                continue

            got = "%sx%s @ %.2f Hz" % (m["hact"], m["vact"], hz_of(m["per"]))
            hdmi_ok = (m["meas_ok"] and m["hact"] == w and m["vact"] == h
                       and abs(hz_of(m["per"]) - hz) <= max(1.0, hz * 0.02))
            cam_ok = (c["streaming"] and c["ldrop"] == base_cam["ldrop"]
                      and not c["cfifo_ovf"] and not c["ufifo_ovf"]
                      and abs(fps_of(c["period16"]) - fps_of(base_cam["period16"])) < 0.5)
            if a.camera_only:
                verdict = "PASS" if cam_ok else "FAIL"
            else:
                verdict = "PASS" if (hdmi_ok and cam_ok) else "FAIL"
            note = []
            if not hdmi_ok and not a.camera_only:
                note.append("hdmi")
            if not cam_ok:
                note.append("camera ldrop=%d fps=%.2f ovf=%d"
                            % (c["ldrop"], fps_of(c["period16"]), c["cfifo_ovf"]))
            rows.append((w, h, hz, verdict, got, " ".join(note)))
            if verdict == "FAIL":
                failures.append((w, h, hz, " ".join(note)))
            print("  %4dx%-5d@%-3d -> %-4s  fpga sees %-22s %s"
                  % (w, h, hz, verdict, got, " ".join(note)))
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        set_mode(dev, *orig)
        time.sleep(1.0)
        print("\nrestored %dx%d @ %d Hz" % orig)

    print("\n=== SUMMARY ===")
    print("  %-16s %-6s %s" % ("mode", "result", "FPGA measured"))
    for w, h, hz, v, got, note in rows:
        print("  %4dx%-5d@%-3d %-6s %-24s %s" % (w, h, hz, v, got, note))
    final = board.camera()
    if final:
        print("\n  camera after sweep: streaming=%d ldrop=%d (baseline %d) %.3f fps"
              % (final["streaming"], final["ldrop"], base_cam["ldrop"],
                 fps_of(final["period16"])))
    board.close()
    print("\n  %d mode(s), %d failure(s)" % (len(rows), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
