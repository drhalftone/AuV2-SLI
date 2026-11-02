#!/usr/bin/env python3
"""Power-meter sweeps of the WWWWW and WKKKK cycles, matched against the camera's sweeps.

    py -3.12 host/pm_sweep.py                       # COM5, mode 0, 30 s per cycle, live view
    py -3.12 host/pm_sweep.py --load pm_sweep.csv   # re-analyse, no hardware

THE METER. A Thorlabs PM100USB with an S121C photodiode head, its head on the screen spot
the camera's ROI looks at. It has no fast-array mode and no trigger input, so it is
polled as fast as USB allows (~3 ms per reading, average count 1), each reading stamped
with the PC clock at the middle of the call. Before measuring, the 15 Hz input filter is
turned OFF (with it on, every frame's light averages into one level) and the range is
fixed from a short read of WWWWW, so autoranging never switches mid-run. Both are put
back on at exit. Power is in W at --wl; for the white LEDs that scale is nominal.

THE CYCLES. The board's impulse generator, as in sli_ui.py: WWWWW is frame 0 at 255 and
frames 1-4 at 254, WKKKK is 255 then four 0s, KKKKK is all 0 (the meter's dark level;
the camera has no KKKKK sweep). All three LEDs on.

FOLDING. Nothing ties a reading to the projector's vsync, but the cycle is 5T with T
measured by the FPGA. The PC and board clocks differ by tens of ppm, which over 30 s
moves the fold by milliseconds, so the period is searched within +-300 ppm of 5T for
the fold with the most contrast (variance of 200 bin means).

MATCHING. The camera sweep c(t) (profile .sliprof, 5 us exposure, 5 us steps) is the
light at time t of the cycle. The meter is modelled as

    p(t) = a + b * (c * box_w)(t - phi)

a box average of width w (the meter's effective sample window), shifted by phi (the
unknown vsync phase), scaled by b (W per ADU) over the offset a. Every phi on the 5 us
grid is fitted at once by FFT correlation, for each w in a log grid; the least-squares
best is reported with its R^2. Each cycle is fitted on its own, so phi is a fit, not a
measurement.

The live view shows the last second of readings and the running fold with the fitted
camera profile over it. Readings are written to --out at the end of each cycle; the
settings and fit results go to the .json beside it.
"""
import argparse
import csv
import json
import os
import sys
import threading
import time
from ctypes import byref, c_double, c_int16, c_uint32, create_string_buffer

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import matplotlib                                                   # noqa: E402
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt                                     # noqa: E402

TLPM_DLL_DIR = r"C:\Program Files\IVI Foundation\VISA\Win64\Bin"
TLPM_PY_DIR = r"C:\Program Files (x86)\IVI Foundation\VISA\WinNT\TLPM\Examples\Python"

# key -> (frame 0 level, frames 1-4 level, label, colour); colours as sli_ui.ProfileView
CYCLES = {"wwwww": (255, 254, "WWWWW", "#1f5fd0"),
          "wkkkk": (255, 0, "WKKKK", "#d02a2a"),
          "kkkkk": (0, 0, "KKKKK", "#555555")}
CSV_HEAD = ["cycle", "t_us", "call_us", "power_W"]

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("port", nargs="?", default="COM5", help="board serial port")
ap.add_argument("--mode", type=int, default=0,
                help="offline mode index (0 = 800x600@120 on the Optoma)")
ap.add_argument("--cycles", default="wwwww,wkkkk,kkkkk",
                help="comma list from %s, run in order" % ",".join(CYCLES))
ap.add_argument("--seconds", type=float, default=30.0, help="readings per cycle, s")
ap.add_argument("--settle", type=float, default=1.0,
                help="wait after switching cycle before reading, s")
ap.add_argument("--wl", type=float, default=550.0, help="meter wavelength, nm")
ap.add_argument("--resource", default=None, help="meter VISA resource (default: the first)")
ap.add_argument("--profile", default=os.path.join(ROOT, "profile_2026-10-16.sliprof"),
                help="camera sweeps to match against")
ap.add_argument("--out", default=os.path.join(ROOT, "pm_sweep.csv"))
ap.add_argument("--load", help="analyse this saved CSV instead of measuring")
args = ap.parse_args()


# ======================================================================================
# The meter
# ======================================================================================
def open_meter(resource=None):
    os.add_dll_directory(TLPM_DLL_DIR)
    sys.path.insert(0, TLPM_PY_DIR)
    from TLPM import TLPM
    if resource is None:
        probe = TLPM()
        n = c_uint32()
        probe.findRsrc(byref(n))
        if not n.value:
            raise RuntimeError("no Thorlabs power meter found")
        buf = create_string_buffer(1024)
        probe.getRsrcName(0, buf)
        probe.close()
        resource = buf.value
    elif isinstance(resource, str):
        resource = resource.encode()
    pm = TLPM()
    pm.open(resource, True, False)
    sn, ssn, msg = (create_string_buffer(1024) for _ in range(3))
    t, st, fl = c_int16(), c_int16(), c_int16()
    pm.getSensorInfo(sn, ssn, msg, byref(t), byref(st), byref(fl))
    return pm, resource.decode(), "%s %s" % (sn.value.decode(), ssn.value.decode())


def read_power(pm):
    p = c_double()
    pm.measPower(byref(p))
    return p.value


def fix_range(pm, seconds=1.0):
    """Autorange over `seconds` of the light now on the head, then pin a range 25 % above
    the largest reading. Returns that reading and the range, W."""
    pm.setPowerAutoRange(c_int16(1))
    t0, hi = time.perf_counter(), 0.0
    while time.perf_counter() - t0 < seconds:
        hi = max(hi, read_power(pm))
    pm.setPowerAutoRange(c_int16(0))
    pm.setPowerRange(c_double(hi * 1.25))
    rng = c_double()
    pm.getPowerRange(c_int16(0), byref(rng))
    return hi, rng.value


class Reader(threading.Thread):
    """Polls the meter flat out into `t_us`, `call_us`, `p` until stopped. The ctypes call
    releases the GIL, so the live view keeps drawing meanwhile."""

    def __init__(self, pm):
        super().__init__(daemon=True)
        self.pm = pm
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.t_us, self.call_us, self.p = [], [], []
        self.t0 = None

    def run(self):
        self.t0 = time.perf_counter()
        while not self.stop.is_set():
            a = time.perf_counter()
            v = read_power(self.pm)
            b = time.perf_counter()
            with self.lock:
                self.t_us.append(((a + b) / 2 - self.t0) * 1e6)
                self.call_us.append((b - a) * 1e6)
                self.p.append(v)

    def snapshot(self):
        with self.lock:
            return np.array(self.t_us), np.array(self.call_us), np.array(self.p)


# ======================================================================================
# Folding and matching
# ======================================================================================
def find_period(t_us, p, P0, ppm=300.0, step=1.0, nb=200):
    """The period within +-ppm of P0 whose fold has the largest variance of bin means."""
    best_v, best_P = -1.0, P0
    for e in np.arange(-ppm, ppm + step / 2, step):
        P = P0 * (1 + e * 1e-6)
        k = ((t_us % P) / P * nb).astype(int) % nb
        c = np.bincount(k, minlength=nb)
        s = np.bincount(k, p, nb)
        ok = c > 0
        v = np.var(s[ok] / c[ok])
        if v > best_v:
            best_v, best_P = v, P
    return best_P


def camera_cycle(profile, key, P):
    """The camera sweep `key` on a uniform ~5 us grid over one cycle of length P (its
    time axis stretched from the profile's 5T to P), plus the grid step."""
    with open(profile) as fh:
        j = json.load(fh)
    rows = j["sweeps"][key]["rows"]
    t = np.array([float(r[4]) for r in rows])
    v = np.array([float(r[5]) for r in rows])
    o = np.argsort(t)
    t, v = t[o] * P / (5 * j["T_us"]), v[o]
    N = int(round(P / 5.0))
    dt = P / N
    return np.interp(np.arange(N) * dt, t, v, period=P), dt


def box(x, n):
    """Circular moving average of x over n samples, centred."""
    if n <= 1:
        return x.copy()
    k = np.zeros(len(x))
    k[:n] = 1.0 / n
    k = np.roll(k, -(n // 2))
    return np.fft.irfft(np.fft.rfft(x) * np.fft.rfft(k), len(x))


def match(t_us, p, P, cam, dt):
    """Fit p(t) = a + b * box_w(cam)(t - phi) over every phi on the grid and w in a log
    grid. Returns the best fit as a dict (b > 0 only)."""
    N = len(cam)
    k = np.floor((t_us % P) / dt).astype(int) % N
    C = np.bincount(k, minlength=N).astype(float)
    S = np.bincount(k, p, N)
    n, Sy, Syy = C.sum(), S.sum(), float(np.dot(p, p))
    FC, FS = np.fft.rfft(C), np.fft.rfft(S)

    def corr(F, x):                                  # sum_k F[k] x[k - s], every s
        return np.fft.irfft(F * np.conj(np.fft.rfft(x)), N)

    best = None
    for w in np.unique(np.round(np.geomspace(dt, 4000.0, 48) / dt).astype(int)):
        x = box(cam, w)
        Sx, Sxx, Sxy = corr(FC, x), corr(FC, x * x), corr(FS, x)
        den = n * Sxx - Sx * Sx
        b = np.where(den > 0, (n * Sxy - Sx * Sy) / np.where(den > 0, den, 1), 0.0)
        a = (Sy - b * Sx) / n
        ssr = Syy - a * Sy - b * Sxy
        ssr[b <= 0] = np.inf
        s = int(np.argmin(ssr))
        if best is None or ssr[s] < best["ssr"]:
            best = dict(ssr=float(ssr[s]), w_us=float(w * dt), phi_us=float(s * dt),
                        a=float(a[s]), b=float(b[s]))
    sst = Syy - Sy * Sy / n
    best["r2"] = 1 - best["ssr"] / sst if sst > 0 else float("nan")
    best["rms_W"] = float(np.sqrt(max(best["ssr"], 0.0) / n))
    return best


def model(cam, dt, fit):
    """The fitted model on the camera's time axis (t, p)."""
    x = box(cam, int(round(fit["w_us"] / dt)))
    return np.arange(len(cam)) * dt, fit["a"] + fit["b"] * x


def fold_means(t_us, p, P, phi, nb=400):
    """Readings folded onto the camera's time axis (shifted by phi), nb bin means."""
    tc = (t_us - phi) % P
    k = (tc / P * nb).astype(int) % nb
    c = np.bincount(k, minlength=nb)
    s = np.bincount(k, p, nb)
    ok = c > 0
    return ((np.arange(nb) + 0.5) * P / nb)[ok], s[ok] / c[ok]


def analyse(key, t_us, p, P0, profile, log=print):
    """Period, mean power and, for a cycle the camera swept, the match."""
    res = dict(cycle=key, n=int(len(p)), mean_W=float(np.mean(p)), sd_W=float(np.std(p)),
               seconds=float((t_us[-1] - t_us[0]) / 1e6), P0_us=P0)
    log("%s: %d readings over %.1f s (%.2f ms each), mean %.4g uW, sd %.4g uW"
        % (CYCLES[key][2], len(p), res["seconds"], 1e-3 * np.median(np.diff(t_us)),
           1e6 * res["mean_W"], 1e6 * res["sd_W"]))
    if key == "kkkkk":
        return res
    P = find_period(t_us, p, P0)
    res.update(P_us=P, ppm=(P / P0 - 1) * 1e6)
    try:
        cam, dt = camera_cycle(profile, key, P)
    except (OSError, KeyError) as e:
        log("  no camera sweep %r to match (%s)" % (key, e))
        return res
    fit = match(t_us, p, P, cam, dt)
    res.update(fit=fit, cam_mean_adu=float(np.mean(cam)))
    log("  period %.3f us (%+.1f ppm vs 5T); match: w %.0f us, phi %.0f us, "
        "a %.4g uW, b %.4g nW/ADU, R^2 %.4f, rms %.3g uW"
        % (P, res["ppm"], fit["w_us"], fit["phi_us"], 1e6 * fit["a"], 1e9 * fit["b"],
           fit["r2"], 1e6 * fit["rms_W"]))
    return res


def compare(results, log=print):
    """Meter vs camera: the WKKKK/WWWWW light ratio over the dark level."""
    r = {x["cycle"]: x for x in results}
    if not {"wwwww", "wkkkk"} <= set(r):
        return {}
    out = {}
    dark = r["kkkkk"]["mean_W"] if "kkkkk" in r else None
    if dark is not None:
        out["meter_ratio"] = ((r["wkkkk"]["mean_W"] - dark)
                              / (r["wwwww"]["mean_W"] - dark))
        log("meter: (WKKKK - KKKKK) / (WWWWW - KKKKK) = %.4f" % out["meter_ratio"])
    if "cam_mean_adu" in r["wwwww"] and "cam_mean_adu" in r["wkkkk"]:
        # The camera's dark level: WKKKK frames 2-4, far from the white frame's light.
        cam, dt = camera_cycle(args.profile, "wkkkk", r["wkkkk"]["P_us"])
        cdark = float(np.median(cam[len(cam) * 2 // 5:]))
        out["camera_dark_adu"] = cdark
        out["camera_ratio"] = ((r["wkkkk"]["cam_mean_adu"] - cdark)
                               / (r["wwwww"]["cam_mean_adu"] - cdark))
        log("camera: (WKKKK - dark) / (WWWWW - dark) = %.4f  (dark %.1f ADU, WKKKK frames 2-4)"
            % (out["camera_ratio"], cdark))
        bw, bk = r["wwwww"]["fit"]["b"], r["wkkkk"]["fit"]["b"]
        log("scale b: WWWWW %.4g, WKKKK %.4g nW/ADU (ratio %.3f)"
            % (1e9 * bw, 1e9 * bk, bk / bw))
    return out


# ======================================================================================
# The live view
# ======================================================================================
class LiveView:
    def __init__(self):
        plt.ion()
        self.fig, (self.ax_raw, self.ax_fold) = plt.subplots(2, 1, figsize=(11, 7.5))
        self.fig.canvas.manager.set_window_title("PM100USB sweeps")
        self.raw, = self.ax_raw.plot([], [], lw=.7)
        self.ax_raw.set_xlabel("time, ms (last 1 s)")
        self.ax_raw.set_ylabel("power, uW")
        self.ax_fold.set_xlabel("time after the vsync of frame 0, ms (phase fitted)")
        self.ax_fold.set_ylabel("power, uW")
        self.closed = False
        self.fig.canvas.mpl_connect("close_event", lambda e: setattr(self, "closed", True))
        self.fig.tight_layout()

    def frames(self, P):
        ax = self.ax_fold
        for line in list(ax.lines) + list(ax.texts):
            line.remove()
        for k in range(6):
            ax.axvline(k * P / 5 / 1000, color="#888", lw=.8)
        ax.set_xlim(0, P / 1000)

    def update(self, key, t_us, p, P, cam=None, dt=None, fit=None, final=False):
        lab, col = CYCLES[key][2], CYCLES[key][3]
        m = t_us > t_us[-1] - 1e6
        self.raw.set_data((t_us[m] - t_us[m][0]) / 1000, 1e6 * p[m])
        self.raw.set_color(col)
        self.ax_raw.relim()
        self.ax_raw.autoscale_view()
        self.ax_raw.set_title("%s: %d readings, %.1f s, %.2f ms per reading, mean %.4g uW"
                              % (lab, len(p), (t_us[-1] - t_us[0]) / 1e6,
                                 1e-3 * np.median(np.diff(t_us)), 1e6 * np.mean(p)))
        self.frames(P)
        phi = fit["phi_us"] if fit else 0.0
        tf, pf = fold_means(t_us, p, P, phi)
        self.ax_fold.plot(tf / 1000, 1e6 * pf, ".", ms=3, color=col,
                          label="%s meter, folded" % lab)
        if fit:
            tm, pm_ = model(cam, dt, fit)
            self.ax_fold.plot(tm / 1000, 1e6 * pm_, color="k", lw=1,
                              label="camera %s, box %.0f us, R$^2$ %.3f"
                                    % (lab, fit["w_us"], fit["r2"]))
        self.ax_fold.legend(loc="upper right", fontsize=9)
        self.ax_fold.relim()
        self.ax_fold.autoscale_view(scalex=False)
        self.ax_fold.set_title("%s folded at %.3f us%s" % (lab, P, " (final)" if final else ""))
        self.fig.canvas.draw_idle()
        plt.pause(0.05)


# ======================================================================================
# Run
# ======================================================================================
def load_csv(path):
    by = {}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            by.setdefault(r["cycle"], []).append((float(r["t_us"]), float(r["power_W"])))
    return {k: (np.array([a for a, _ in v]), np.array([b for _, b in v]))
            for k, v in by.items()}


def preflight(port):
    """sli_ui.preflight without genlock: the meter needs no camera trigger, so the Ft+ may
    be unplugged. Checks the board generates its own video at a plausible frame period.
    Returns (open serial, frame period us, exposure limit in 0.375 us units)."""
    import serial
    from sli_ui import TICK_US, reg
    ser = serial.Serial(port, 115200, timeout=0.05)
    time.sleep(0.3)
    try:
        st, md = reg(ser, 0x02), reg(ser, 0x20)      # STATUS bit 4: 1 = pass-through
        if st & 0x10:
            raise RuntimeError("the board is in pass-through: unplug the PC's HDMI from "
                               "the board to run offline")
        if not md & 0x80:
            raise RuntimeError("the board has no valid offline mode (MODE 0x%02X)" % md)
        T = (reg(ser, 0x4A) | (reg(ser, 0x4B) << 8) | (reg(ser, 0x4C) << 16)) * TICK_US
        mx = reg(ser, 0x53) | (reg(ser, 0x54) << 8)
        if not 1000 < T < 60000:
            raise RuntimeError("frame %.1f us -- is video running?" % T)
    except Exception:
        ser.close()
        raise
    return ser, T, mx


def run_cycle(view, rd_, key, P0, fh_csv):
    """Read one cycle for args.seconds with the live view refitting every 3 s."""
    rd_.start()
    t_fit, cam, dt, fit, P = time.time(), None, None, None, P0
    while not view.closed:
        time.sleep(0.25)
        t, cu, p = rd_.snapshot()
        if len(p) < 10:
            continue
        done = t[-1] >= args.seconds * 1e6
        if key != "kkkkk" and (time.time() - t_fit > 3 or done) and t[-1] > 2e6:
            P = find_period(t, p, P0)
            try:
                cam, dt = camera_cycle(args.profile, key, P)
                fit = match(t, p, P, cam, dt)
            except (OSError, KeyError):
                cam = fit = None
            t_fit = time.time()
        view.update(key, t, p, P, cam, dt, fit)
        if done:
            break
    rd_.stop.set()
    rd_.join()
    t, cu, p = rd_.snapshot()
    w = csv.writer(fh_csv)
    w.writerows([key, round(a, 1), round(b, 1), "%.6e" % c] for a, b, c in zip(t, cu, p))
    fh_csv.flush()
    return t, p


def main():
    view = LiveView()
    results = []
    if args.load:
        meta_path = os.path.splitext(args.load)[0] + ".json"
        with open(meta_path) as fh:
            meta = json.load(fh)
        for key, (t, p) in load_csv(args.load).items():
            res = analyse(key, t, p, meta["P0_us"], args.profile)
            results.append(res)
            if "fit" in res:
                cam, dt = camera_cycle(args.profile, key, res["P_us"])
                view.update(key, t, p, res["P_us"], cam, dt, res["fit"], final=True)
                plt.pause(1.0)
        compare(results)
        plt.ioff()
        plt.show()
        return

    from sli_ui import set_board_mode, start_impulse, stop_impulse
    import serial
    keys = [k.strip().lower() for k in args.cycles.split(",") if k.strip()]
    bad = [k for k in keys if k not in CYCLES]
    if bad:
        sys.exit("unknown cycle(s) %s; choose from %s" % (bad, ", ".join(CYCLES)))

    with serial.Serial(args.port, 115200, timeout=0.05) as s:
        time.sleep(0.3)
        set_board_mode(s, args.mode)
    ser, T, mx = preflight(args.port)
    P0 = 5 * T
    print("board %s: frame %.2f us, cycle %.2f us" % (args.port, T, P0))

    pm, rsrc, sensor = open_meter(args.resource)
    filt = c_int16()
    pm.getInputFilterState(byref(filt))
    print("meter %s, sensor %s; input filter was %s" % (rsrc, sensor, "on" if filt.value else "off"))
    try:
        pm.setInputFilterState(c_int16(0))
        pm.setAvgCnt(c_int16(1))
        pm.setWavelength(c_double(args.wl))
        start_impulse(ser, *CYCLES["wwwww"][:2], 5.0, mx)
        time.sleep(args.settle)
        hi, rng = fix_range(pm)
        print("range fixed at %.4g uW (WWWWW peak reading %.4g uW), %.0f nm"
              % (1e6 * rng, 1e6 * hi, args.wl))
        meta = dict(when=time.strftime("%Y-%m-%d %H:%M:%S"), port=args.port, mode=args.mode,
                    T_us=T, P0_us=P0, meter=rsrc, sensor=sensor, wl_nm=args.wl,
                    range_W=rng, seconds=args.seconds, settle_s=args.settle,
                    profile=os.path.abspath(args.profile), cycles=keys)
        data = {}
        with open(args.out, "w", newline="") as fh:
            csv.writer(fh).writerow(CSV_HEAD)
            for key in keys:
                if view.closed:
                    break
                start_impulse(ser, *CYCLES[key][:2], 5.0, mx)
                time.sleep(args.settle)
                data[key] = run_cycle(view, Reader(pm), key, P0, fh)
        for key, (t, p) in data.items():
            if len(p) > 10:
                res = analyse(key, t, p, P0, args.profile)
                results.append(res)
                if "fit" in res:
                    cam, dt = camera_cycle(args.profile, key, res["P_us"])
                    view.update(key, t, p, res["P_us"], cam, dt, res["fit"], final=True)
        meta["results"] = results
        meta["compare"] = compare(results)
        with open(os.path.splitext(args.out)[0] + ".json", "w") as fh:
            json.dump(meta, fh, indent=1)
        print("wrote %s and its .json" % args.out)
    finally:
        try:
            stop_impulse(ser)
            ser.close()
        except Exception:
            pass
        pm.setPowerAutoRange(c_int16(1))
        pm.setInputFilterState(filt)
        pm.close()
    plt.ioff()
    plt.show()


if __name__ == "__main__":
    main()
