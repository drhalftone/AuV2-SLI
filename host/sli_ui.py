#!/usr/bin/env python3
"""Projector-profiling app, document style.

    "C:\\Program Files\\Python312\\python.exe" host/sli_ui.py [profile.sliprof]

A DOCUMENT is one profiling session: the connection it was measured on (COM port, video
output, frame period, exposure limit), the WWWWW and WKKKK sweeps, and the chosen exposure
edges. It is saved as one JSON file, *.sliprof. The app starts with no document.

FILE MENU
  New...      the setup dialog (COM port + projector video output); OK runs the preflight
              -- display to 800x600 @ 120, open the port, genlock on over the Ft+, check the
              frame period -- and opens an empty document connected to the board. Cancel
              leaves no document.
  Open...     a saved .sliprof. It is NOT connected: the first Run asks for the setup.
  Save / Save As... / Close / Exit -- unsaved changes are asked about first.

THE VIEW. One plot of the 5-frame (41.7 ms) timeline: WWWWW in blue -- always white,
frame 0 at 255 and frames 1-4 at 254 so frame 0's tag still anchors the cycle
(lag_sweep.py --bg 254) -- and WKKKK in red, one white frame and four black (lag_sweep.py
with no --bg). Raw ROI mean.

Over it, the EXPOSURE EDGES: two vertical lines, each with a grab box near the top. The
LEADING edge (green) is when the exposure opens -- the genlock delay after frame 0's vsync
-- and the TRAILING edge (purple) when it closes. Drag a box, or type a value. Until you
move them they sit on the window of the longest allowed exposure that collects the most of
the white frame's light ("Best window" puts them back there at the current width). The
readout gives delay, exposure (in opcode 1's 0.375 us units), the share of the white
frame's light inside, and whether the exposure is over the limit.

MOUSE. Only the time axis zooms and pans; the ADU axis always spans every value the sweeps
have read, and grows as a sweep runs. The time axis never goes past frame 0's vsync or
the end of frame 4. Wheel zooms time around the cursor. Drag to pan;
let go while moving and it glides to a stop. Double-click (or "Whole view") shows all five
frames again. The grab boxes are a fixed size on screen at a fixed height in the plot, so
zoom never loses them; pan an edge out of view and its box goes with it.

ADJUST IRIS holds a 5 us exposure on the brightest delay of the WWWWW sweep and shows every
reading live, with the saturated-pixel count, while the lens iris is turned: aim for the
green band with nothing clipping. Done records the setting in the document and, if the
document already holds sweeps, says they were taken at the old setting and need re-running.

Run WWWWW / Run WKKKK replace that sweep; Stop ends it after the current delay and keeps
what it has. The Sweep menu imports a lag_sweep.py CSV (one written before the vblank tag
fix is corrected on import) and exports the sweeps as CSV and the window as JSON. While a
sweep runs, every reading is also written to a CSV in the temp folder, so a crash does
not lose it; the status line names the file.
"""
import csv
import json
import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import numpy as np                                                 # noqa: E402
import serial                                                      # noqa: E402
from serial.tools import list_ports                                # noqa: E402
from matplotlib.figure import Figure                               # noqa: E402
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg    # noqa: E402
from matplotlib.transforms import blended_transform_factory        # noqa: E402
import lag_core as lc                                              # noqa: E402
from align_roi import displays, set_mode                           # noqa: E402
from frame_sweep import rd, TICK_US                                # noqa: E402

MODE = lc.MODE
PAD = dict(padx=8, pady=4)
EXT = ".sliprof"
FORMAT = "auv2-sli-profile"

# the two sweeps a document holds: key -> (tab title, level of frame 0, level of frames 1-4)
SWEEPS = {"wwwww": ("WWWWW", 255, 254),
          "wkkkk": ("WKKKK", 255, 0)}
SETTINGS = dict(expo_us=5.0, step_us=5.0, frames=20, settle=4, active_us=lc.ACTIVE_US)


# ======================================================================================
# The document
# ======================================================================================
class Document:
    """One profiling session. Rows are lag_core.CSV_HEAD rows, already tag-corrected."""

    def __init__(self):
        self.path = None
        self.dirty = False
        self.port = self.display = None
        self.T = None                                # frame period, us
        self.mx = None                               # exposure limit, 0.375 us units
        self.sweeps = {}                             # key -> {"settings", "rows", "when", "complete"}
        self.edges = None                            # {"lead_us", "trail_us"}
        self.iris = None                             # last Adjust iris: {"delay_us", ...}
        self.created = time.strftime("%Y-%m-%d %H:%M:%S")
        self.ser = None                              # open serial port while connected

    @property
    def name(self):
        return os.path.basename(self.path) if self.path else "Untitled"

    def points(self, key):
        """[(t_us, mean)] sorted by t, or []."""
        sw = self.sweeps.get(key)
        if not sw:
            return []
        return sorted((float(r[4]), float(r[5])) for r in sw["rows"])

    # ---- file ------------------------------------------------------------------------
    def save(self, path):
        doc = {"format": FORMAT, "version": 1, "created": self.created,
               "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
               "port": self.port, "display": self.display, "T_us": self.T,
               "max_expo_units": self.mx, "edges": self.edges, "iris": self.iris,
               "csv_head": lc.CSV_HEAD, "sweeps": self.sweeps}
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh)
        os.replace(tmp, path)                        # never leave a half-written document
        self.path, self.dirty = path, False

    @classmethod
    def load(cls, path):
        with open(path) as fh:
            j = json.load(fh)
        if j.get("format") != FORMAT:
            raise ValueError("%s is not an AuV2-SLI profile" % os.path.basename(path))
        d = cls()
        d.path = path
        d.created = j.get("created", d.created)
        d.port, d.display = j.get("port"), j.get("display")
        d.T, d.mx = j.get("T_us"), j.get("max_expo_units")
        d.edges = j.get("edges")
        d.iris = j.get("iris")
        d.sweeps = j.get("sweeps", {})
        return d

    # ---- CSV -------------------------------------------------------------------------
    def import_csv(self, key, path):
        with open(path) as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            raise ValueError("%s has no readings" % os.path.basename(path))
        T = float(rows[0]["T_us"])
        if self.T and abs(T - self.T) > 1.0:
            raise ValueError("%s was measured at a %.1f us frame, this document is %.1f us"
                             % (os.path.basename(path), T, self.T))
        # A CSV written before lag_sweep corrected the vblank tag has no tag_fix_us: move
        # its readings before the first active pixel one position later, as lag_core does.
        fix = None if "tag_fix_us" in rows[0] else lc.ACTIVE_US
        out = []
        for r in rows:
            d, k = float(r["delay_us"]), int(r["position"])
            if fix is not None and d < fix:
                k = (k + 1) % 5
            out.append([float(r["T_us"]), float(r["expo_us"]), d, k, round(k * T + d, 2),
                        float(r["mean"]), float(r["sd"]), int(r["n"]), float(r["ref"]),
                        float(r.get("tag_fix_us") or lc.ACTIVE_US)])
        self.T = self.T or T
        self.sweeps[key] = {"settings": {"imported_from": os.path.abspath(path),
                                         "tag_fixed_on_import": fix is not None},
                            "rows": out, "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "complete": True}
        self.dirty = True

    def export_csv(self, key, path):
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(lc.CSV_HEAD)
            w.writerows(self.sweeps[key]["rows"])

    # ---- connection ------------------------------------------------------------------
    def disconnect(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None


# ======================================================================================
# Setup dialog: COM port + projector video output
# ======================================================================================
class SetupDialog(tk.Toplevel):
    """Modal. OK runs the preflight and sets self.result = (ser, T, mx, port, display);
    Cancel leaves it None."""

    def __init__(self, master, port=None, display=None):
        super().__init__(master)
        self.title("Connect to the board")
        self.resizable(False, False)
        self.transient(master)
        self.result = None
        self.protocol("WM_DELETE_WINDOW", self.cancel)

        # ---- COM port ----------------------------------------------------------------
        f = ttk.LabelFrame(self, text="Camera COM port")
        f.grid(row=0, column=0, sticky="ew", **PAD)
        self.port = tk.StringVar()
        self.port_box = ttk.Combobox(f, textvariable=self.port, state="readonly", width=48)
        self.port_box.grid(row=0, column=0, columnspan=2, sticky="ew", **PAD)
        ttk.Button(f, text="Refresh", command=self.refresh_ports).grid(row=1, column=0, **PAD)
        ttk.Button(f, text="Check", command=self.check_port).grid(row=1, column=1, **PAD)
        self.port_msg = ttk.Label(f, text="", width=56)
        self.port_msg.grid(row=2, column=0, columnspan=2, sticky="w", **PAD)

        # ---- video output ------------------------------------------------------------
        g = ttk.LabelFrame(self, text="Projector video output (HDMI)")
        g.grid(row=1, column=0, sticky="ew", **PAD)
        self.disp = tk.StringVar()
        self.disp_box = ttk.Combobox(g, textvariable=self.disp, state="readonly", width=48)
        self.disp_box.grid(row=0, column=0, columnspan=2, sticky="ew", **PAD)
        self.disp_box.bind("<<ComboboxSelected>>", lambda e: self.show_mode())
        ttk.Button(g, text="Refresh", command=self.refresh_displays).grid(row=1, column=0, **PAD)
        ttk.Button(g, text="Set %dx%d @ %d" % MODE, command=self.set_mode).grid(row=1, column=1,
                                                                             **PAD)
        self.disp_msg = ttk.Label(g, text="", width=56)
        self.disp_msg.grid(row=2, column=0, columnspan=2, sticky="w", **PAD)

        # ---- OK / Cancel -------------------------------------------------------------
        b = ttk.Frame(self)
        b.grid(row=2, column=0, sticky="ew", **PAD)
        self.msg = ttk.Label(b, text="", foreground="#b00020", wraplength=330)
        self.msg.pack(side="left", fill="x", expand=True)
        ttk.Button(b, text="Cancel", command=self.cancel).pack(side="right", padx=4)
        self.ok_btn = ttk.Button(b, text="OK", command=self.ok, default="active")
        self.ok_btn.pack(side="right", padx=4)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.cancel())

        self.ports, self.disps = [], []
        self.want_port, self.want_disp = port, display
        self.refresh_ports()
        self.refresh_displays()
        self.grab_set()
        self.focus_force()

    # ---- COM port --------------------------------------------------------------------
    def refresh_ports(self):
        self.ports = sorted(list_ports.comports(), key=lambda p: p.device)
        self.port_box["values"] = ["%s  -  %s" % (p.device, p.description) for p in self.ports]
        if self.ports:
            names = [p.device for p in self.ports]
            cur = self.port_device() or self.want_port
            self.port_box.current(names.index(cur) if cur in names else 0)
            self.port_msg["text"] = "%d port(s) found" % len(self.ports)
        else:
            self.port.set("")
            self.port_msg["text"] = "no serial ports -- is the board plugged in?"

    def port_device(self):
        return self.port.get().split()[0] if self.port.get() else None

    def check_port(self):
        dev = self.port_device()
        if not dev:
            return
        try:
            with serial.Serial(dev, 115200, timeout=0.05) as ser:
                time.sleep(0.3)
                v = [rd(ser, a) for a in (0x4A, 0x4B, 0x4C, 0x5D)]
        except serial.SerialException as e:
            self.port_msg["text"] = "%s: cannot open (%s)" % (dev, e.__class__.__name__)
            return
        if None in v:
            self.port_msg["text"] = "%s: no reply -- not the camera board?" % dev
            return
        T = (v[0] | (v[1] << 8) | (v[2] << 16)) * TICK_US
        gl = v[3]
        state = "live" if (gl & 3) == 3 else ("enabled, NOT live" if gl & 1 else "OFF")
        self.port_msg["text"] = "%s: frame %.1f us (%.2f Hz), genlock %s (0x%02X)" % (
            dev, T, 1e6 / T if T else 0, state, gl)

    # ---- video output ----------------------------------------------------------------
    def refresh_displays(self):
        self.disps = displays()
        self.disp_box["values"] = [
            "%s  %dx%d @ %d Hz  at (%d, %d)%s" % (d[0], d[3], d[4], d[5], d[1], d[2],
                                                 "  [primary]" if (d[1], d[2]) == (0, 0) else "")
            for d in self.disps]
        if not self.disps:
            self.disp.set("")
            self.disp_msg["text"] = "no displays found"
            return
        names = [d[0] for d in self.disps]
        cur = self.disp_device() or self.want_disp
        if cur in names:
            i = names.index(cur)
        else:                                        # default: the non-primary display
            proj = [k for k, d in enumerate(self.disps) if (d[1], d[2]) != (0, 0)]
            i = proj[0] if proj else 0
        self.disp_box.current(i)
        self.show_mode()

    def show_mode(self):
        d = self.disps[self.disp_box.current()]
        self.disp_msg["text"] = ("mode OK" if tuple(d[3:]) == MODE else
                                 "mode is %dx%d @ %d, sweeps need %dx%d @ %d (OK sets it)"
                                 % (*d[3:], *MODE))

    def disp_device(self):
        return self.disp.get().split()[0] if self.disp.get() else None

    def set_mode(self):
        dev = self.disp_device()
        if not dev:
            return
        ok, msg = set_mode(dev, *MODE)
        self.disp_msg["text"] = "%s: %s" % (dev, msg)
        if ok:
            self.after(3000, self.refresh_displays)  # let the mode settle before re-reading

    # ---- OK / Cancel -----------------------------------------------------------------
    def ok(self):
        port, disp = self.port_device(), self.disp_device()
        if not port or not disp:
            self.msg["text"] = "choose a COM port and a video output"
            return
        if [d for d in self.disps if d[0] == disp and (d[1], d[2]) == (0, 0)]:
            self.msg["text"] = "%s is the primary display -- choose the projector" % disp
            return
        self.msg["text"] = "checking..."
        self.ok_btn.state(["disabled"])
        self.update_idletasks()
        try:
            ser, T, mx = lc.preflight(port, disp, log=lambda s: None)
        except Exception as e:                       # serial, Ft+, display: show, stay
            self.msg["text"] = str(e)
            self.ok_btn.state(["!disabled"])
            self.refresh_displays()
            return
        self.result = (ser, T, mx, port, disp)
        self.grab_release()
        self.destroy()

    def cancel(self):
        self.result = None
        self.grab_release()
        self.destroy()


# ======================================================================================
# Shared plot pieces
# ======================================================================================
# Room around the axes in PIXELS. The tick labels, axis labels and "frame k" captions are
# a fixed size in points, so margins given as fractions of the figure clip them when the
# window gets small; these are converted to fractions on every resize (fit_margins).
MARGIN_PX = dict(left=72, right=14, top=30, bottom=52)


def fit_margins(fig):
    w, h = fig.get_size_inches() * fig.dpi
    m = MARGIN_PX
    # never let the margins eat the plot: keep at least 20 % of each direction for it
    left = min(m["left"] / w, 0.4)
    right = max(1 - m["right"] / w, left + 0.2)
    bottom = min(m["bottom"] / h, 0.4)
    top = max(1 - m["top"] / h, bottom + 0.2)
    fig.subplots_adjust(left=left, right=right, bottom=bottom, top=top)


def timeline_axes(fig, T):
    fit_margins(fig)
    ax = fig.add_subplot(111)
    for k in range(1, 5):
        ax.axvline(k * T, color="#888", lw=.8)
    for k in range(5):
        ax.text((k + .5) * T, 1.01, "frame %d" % k, transform=ax.get_xaxis_transform(),
                ha="center", va="bottom", fontsize=9, color="#555")
    ax.set_xlim(0, 5 * T)
    ax.set_xlabel("time after the vsync of frame 0 (us)")
    ax.set_ylabel("ROI mean (ADU)")
    ax.grid(alpha=.3)
    return ax


# ======================================================================================
# Running a sweep (no Tk in here except through the queue)
# ======================================================================================
class SweepRunner:
    """One sweep in a worker thread. The view drains `q` on the Tk thread."""

    def __init__(self, key):
        self.key = key
        self.title, self.level, self.bg = SWEEPS[key]
        self.worker = None
        self.stop_ev = threading.Event()
        self.q = queue.Queue()
        self.log_csv = None
        self.t0 = 0.0

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def start(self, doc):
        """Program the impulse cycle and start the worker. Raises on a bad setup."""
        e = lc.start_impulse(doc.ser, self.level, self.bg, SETTINGS["expo_us"], doc.mx)
        delays = lc.delays_for(doc.T, e, SETTINGS["step_us"])
        self.log_csv = os.path.join(tempfile.gettempdir(), "auv2sli_%s_%s.csv"
                                    % (self.key, time.strftime("%Y-%m-%d_%H%M%S")))
        doc.sweeps[self.key] = {"settings": dict(SETTINGS, level=self.level, bg=self.bg,
                                                 expo_real_us=e, port=doc.port,
                                                 display=doc.display),
                                "rows": [], "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "complete": False}
        self.stop_ev.clear()
        self.t0 = time.time()
        self.worker = threading.Thread(target=self.work, args=(doc.ser, doc.T, e, delays),
                                       daemon=True)
        self.worker.start()

    def work(self, ser, T, e, delays):
        try:
            for i, n, d, means, ref, rows in lc.sweep(
                    ser, T, e, delays, self.log_csv, self.level, self.bg,
                    SETTINGS["frames"], SETTINGS["settle"], SETTINGS["active_us"],
                    stop=self.stop_ev, log=lambda s: self.q.put(("log", s))):
                self.q.put(("rows", (i, n, rows)))
            self.q.put(("end", None))
        except Exception as ex:
            self.q.put(("error", ex))
        finally:
            try:
                lc.stop_impulse(ser)
            except Exception:
                pass

    def stop(self):
        self.stop_ev.set()

    def shutdown(self):
        """Stop and wait; the worker takes the camera back out of impulse mode."""
        if self.running:
            self.stop_ev.set()
            self.worker.join(timeout=15)


# ======================================================================================
# The profile view: both sweeps on one plot, the exposure edges on top
# ======================================================================================
class ProfileView(ttk.Frame):
    """WWWWW (blue) and WKKKK (red) on one timeline, with the exposure edges.

    MOUSE. Wheel zooms the time axis around the cursor; only time zooms and pans -- the
    ADU axis always spans every value the sweeps have read (fit_y), so a live sweep grows
    it. Dragging empty plot pans in time, and a flick keeps it gliding (momentum).
    Double-click returns to the whole 5-frame view. The edges are vertical lines with a
    grab box near the top; drag the box. The boxes sit at a fixed height in the plot and
    are a fixed size on screen, so zooming never loses them -- panning one out of the
    visible range clips it away like any other data.
    """

    COLORS = {"wwwww": "#1f5fd0", "wkkkk": "#d02a2a"}
    LEAD, TRAIL = "#2e9e4f", "#7b3fb5"
    BG = "#d97706"                                   # background exposure (trail -> next lead)
    BOX_Y = 0.93                                     # grab boxes, in axes fraction
    GRAB_PX = 10
    FRICTION = 4.0                                   # momentum decays as exp(-FRICTION t)
    TICK_MS = 16

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.runners = {k: SweepRunner(k) for k in SWEEPS}
        self.auto = True                             # view follows the data until touched
        self.drag = None                             # "lead" / "trail" / "pan"
        self.momentum = None
        self.sig_version = None
        self.iris_dlg = None
        self.sig = None

        # ---- controls ----------------------------------------------------------------
        bar = ttk.Frame(self)
        bar.pack(fill="x", **PAD)
        self.run_btns = {}
        for k in SWEEPS:
            b = ttk.Button(bar, text="Run %s" % SWEEPS[k][0], command=lambda k=k: self.run(k))
            b.pack(side="left", padx=(0, 4))
            self.run_btns[k] = b
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop)
        self.stop_btn.pack(side="left", padx=(0, 4))
        self.iris_btn = ttk.Button(bar, text="Adjust iris...", command=self.adjust_iris)
        self.iris_btn.pack(side="left", padx=(0, 16))
        ttk.Label(bar, text="leading edge (us)").pack(side="left")
        self.lead_v, self.trail_v = tk.StringVar(), tk.StringVar()
        le = ttk.Entry(bar, textvariable=self.lead_v, width=10)
        le.pack(side="left", padx=(4, 12))
        ttk.Label(bar, text="trailing edge (us)").pack(side="left")
        te = ttk.Entry(bar, textvariable=self.trail_v, width=10)
        te.pack(side="left", padx=(4, 12))
        for w in (le, te):
            w.bind("<Return>", lambda e: self.typed())
            w.bind("<FocusOut>", lambda e: self.typed())
        self.best_btn = ttk.Button(bar, text="Best window", command=self.best)
        self.best_btn.pack(side="left", padx=4)
        ttk.Button(bar, text="Whole view", command=self.reset_view).pack(side="right")
        self.status = ttk.Label(self, text="", font=("Consolas", 9))
        self.status.pack(fill="x", padx=8)
        self.readout = ttk.Label(self, text="", font=("Consolas", 9))
        self.readout.pack(fill="x", padx=8)
        self.cursor = ttk.Label(self, text="", font=("Consolas", 9))
        self.cursor.pack(fill="x", padx=8)

        # ---- plot --------------------------------------------------------------------
        self.fig = Figure(figsize=(13, 6))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        for ev, fn in (("button_press_event", self.press), ("motion_notify_event", self.motion),
                       ("button_release_event", self.release), ("scroll_event", self.scroll),
                       ("resize_event", lambda e: fit_margins(self.fig)),
                       ("axes_leave_event", lambda e: self.cursor.config(text=""))):
            self.canvas.mpl_connect(ev, fn)

        self.T = None
        self.build()

    # ---- building / drawing ----------------------------------------------------------
    def build(self):
        """(Re)create the axes for the document's frame period."""
        doc = self.app.doc
        self.T = doc.T or 8333.3
        # THE 120 FPS LIMIT, from the frame period exactly as the FPGA computes MAXEXP
        # (usb_link.v): T - 80 us sensor gap - 10 us margin, in whole 0.375 us units.
        # The board's own answer wins when connected; longer drops the camera to 60 fps.
        self.max_us = (doc.mx or lc.max_expo_units(self.T)) * lc.EXPO_UNIT_US
        self.fig.clear()
        self.bg_art = []                             # cleared with the figure
        ax = self.ax = timeline_axes(self.fig, self.T)
        self.lines = {k: ax.plot([], [], lw=.7, color=self.COLORS[k], label=SWEEPS[k][0])[0]
                      for k in SWEEPS}
        ax.legend(loc="upper right")
        bt = blended_transform_factory(ax.transData, ax.transAxes)
        self.span = ax.axvspan(0, 1, color=self.LEAD, alpha=.10, lw=0)
        self.l_lead = ax.axvline(0, color=self.LEAD, lw=1.4)
        self.l_trail = ax.axvline(0, color=self.TRAIL, lw=1.4)
        self.b_lead, = ax.plot([0], [self.BOX_Y], marker="s", ms=11, color=self.LEAD,
                               mec="white", mew=1.2, transform=bt, clip_on=True, zorder=5)
        self.b_trail, = ax.plot([0], [self.BOX_Y], marker="s", ms=11, color=self.TRAIL,
                                mec="white", mew=1.2, transform=bt, clip_on=True, zorder=5)
        e = doc.edges
        self.edges_auto = not e                      # follow the best window until touched
        self.lead, self.trail = (e["lead_us"], e["trail_us"]) if e else (0.0, 0.0)
        self.refresh()

    def refresh(self):
        """Redraw from the document: traces, edges, buttons, status."""
        doc = self.app.doc
        if (doc.T or 8333.3) != self.T:
            self.build()
            return
        for k, ln in self.lines.items():
            p = doc.points(k)
            ln.set_data([q[0] for q in p], [q[1] for q in p])
        self.update_sig()
        if self.edges_auto:
            self.lead = (self.best_start(self.max_us) if self.sig is not None
                         else lc.ACTIVE_US)
            self.trail = self.lead + self.max_us
        self.place_edges()
        if self.auto:
            self.ax.set_xlim(0, 5 * self.T)
        self.fit_y()
        self.buttons()
        if not self.busy_runner():
            self.status["text"] = "   ".join(
                "%s: %s" % (SWEEPS[k][0], self.summary(doc.sweeps[k]) if doc.sweeps.get(k)
                            else "none -- Run or Sweep > Import") for k in SWEEPS)
        self.canvas.draw_idle()

    def fit_y(self):
        """The ADU axis always spans every value the sweeps have read, whatever the zoom."""
        ys = [np.asarray(ln.get_ydata(), float) for ln in self.lines.values()]
        ys = np.concatenate([y for y in ys if y.size] or [np.array([0.0, 1023.0])])
        lo, hi = float(np.min(ys)), float(np.max(ys))
        pad = 0.05 * (hi - lo or 1.0)
        self.ax.set_ylim(lo - pad, hi + pad)

    def summary(self, sw):
        rows = sw["rows"]
        sat = sum(float(r[5]) >= 1000 for r in rows)
        src = sw["settings"].get("imported_from")
        return ("%s%d readings, %d saturated, %s %s"
                % ("INCOMPLETE " if not sw.get("complete", True) else "", len(rows), sat,
                   "imported" if src else "measured", sw.get("when", "")))

    def busy_runner(self):
        return next((r for r in self.runners.values() if r.running), None)

    def buttons(self):
        r = self.busy_runner()
        for b in list(self.run_btns.values()) + [self.iris_btn]:
            b.state(["disabled"] if r or self.iris_dlg else ["!disabled"])
        self.stop_btn.state(["!disabled"] if r and not r.stop_ev.is_set() else ["disabled"])
        self.best_btn.state(["!disabled"] if self.sig is not None else ["disabled"])

    # ---- edges -----------------------------------------------------------------------
    def update_sig(self):
        """The white frame's light on a uniform grid: WKKKK minus its dark level (the
        median of frames 2-4, which the white frame never reaches)."""
        imp = self.app.doc.points("wkkkk")
        if len(imp) == (self.sig_version or 0):
            return
        self.sig_version = len(imp)
        if len(imp) < 10:
            self.sig = None
            return
        T = self.T
        t = np.array([q[0] for q in imp])
        v = np.array([q[1] for q in imp])
        dark = float(np.median(v[t >= 2 * T])) if np.any(t >= 2 * T) else float(v.min())
        self.dt = float(np.median(np.diff(t))) or 5.0
        self.grid = np.arange(0.0, 5 * T, self.dt)
        self.sig = np.clip(np.interp(self.grid, t, v - dark, period=5 * T), 0, None)
        self.total = float(self.sig.sum())

    def captured(self, a, b):
        """Share of the white frame's light between a and b (circular over 5 frames)."""
        if self.sig is None or self.total <= 0:
            return None
        idx = np.arange(int(round(a / self.dt)), int(round(b / self.dt))) % len(self.grid)
        return float(self.sig[idx].sum() / self.total)

    def best_start(self, width):
        """Start of the `width`-wide window holding the most of the white frame's light."""
        n = max(1, int(round(width / self.dt)))
        ext = np.concatenate([self.sig, self.sig[:n]])
        box = np.convolve(ext, np.ones(n), mode="valid")[:len(self.sig)]
        return float(self.grid[int(np.argmax(box))])

    def set_edges(self, lead, trail, moving="trail"):
        """Edges, with the exposure held to the limit. Moving the lead (green)
        carries the trail (purple) with it at the same width; moving the trail keeps
        the lead anchored and stops the trail at the limit."""
        w = self.max_us
        if moving == "lead":
            width = min(max(self.trail - self.lead, lc.EXPO_UNIT_US), w)
            lead = min(max(lead, 0.0), 5 * self.T)
            trail = lead + width
        else:
            lead = min(max(lead, 0.0), 5 * self.T)
            trail = min(max(trail, lead + lc.EXPO_UNIT_US), lead + w)
        self.lead, self.trail = lead, trail
        self.edges_auto = False
        self.app.doc.edges = {"lead_us": round(lead, 2), "trail_us": round(trail, 2)}
        self.app.changed(redraw=False)
        self.place_edges()
        self.canvas.draw_idle()

    def place_edges(self):
        lead, trail = self.lead, self.trail
        self.lead_v.set("%.1f" % lead)
        self.trail_v.set("%.1f" % trail)
        self.l_lead.set_xdata([lead, lead])
        self.l_trail.set_xdata([trail, trail])
        self.b_lead.set_xdata([lead])
        self.b_trail.set_xdata([trail])
        self.span.remove()
        self.span = self.ax.axvspan(lead, trail, color=self.LEAD, alpha=.15, lw=0)
        for a_ in getattr(self, "bg_art", []):
            a_.remove()
        self.bg_art = []
        fa, fb = lead, trail
        # THE BACKGROUND EXPOSURE: the rest of the frame, from the trailing edge to the
        # next leading edge (trail -> lead + T), as it repeats in every frame. Wraps at 5T.
        T5, b0 = 5 * self.T, trail % self.T
        bw = max(0.0, lead + self.T - trail) if trail - lead < self.T else 0.0
        for f in range(5):
            s_, e_ = f * self.T + b0, f * self.T + b0 + bw
            for x0, x1 in ([(s_, e_)] if e_ <= T5 else [(s_, T5), (0.0, e_ - T5)]):
                self.bg_art.append(self.ax.axvspan(x0, x1, color=self.BG, alpha=.35, lw=0,
                                                   hatch="///", fill=False))
            for x in (s_, e_ % T5 if e_ > T5 else e_):
                self.bg_art.append(self.ax.axvline(x, color=self.BG, lw=1.0, ls="--"))
        units = int((fb - fa) / lc.EXPO_UNIT_US)
        expo = units * lc.EXPO_UNIT_US
        k = int(fa // self.T)
        over = expo > self.max_us
        cap = self.captured(fa, fb)
        bu = int(bw / lc.EXPO_UNIT_US)
        self.readout["text"] = (
            "foreground: delay %.2f us (frame %d + %.2f us)   %.3f us = %d units"
            "   white-frame light inside %s   limit %.2f us (T - 90 us, 120 fps)%s\n"
            "background (dashed, every frame): delay %.2f us into the frame   %.3f us = %d units"
            "   ends %.2f us into the frame"
            % (fa, k, fa - k * self.T, expo, units,
               "%.1f %%" % (100 * cap) if cap is not None else "- (needs WKKKK)",
               self.max_us, "   OVER THE LIMIT" if over else "",
               b0, bu * lc.EXPO_UNIT_US, bu, (b0 + bu * lc.EXPO_UNIT_US) % self.T))
        self.readout["foreground"] = "#b00020" if over else ""

    def typed(self):
        try:
            lead, trail = float(self.lead_v.get()), float(self.trail_v.get())
        except ValueError:
            lead, trail = self.lead, self.trail
        if (lead, trail) != (self.lead, self.trail) or trail - lead > self.max_us:
            self.set_edges(lead, trail, "lead" if lead != self.lead else "trail")
        else:
            self.place_edges()

    def best(self):
        """Keep the current exposure (capped at the limit), move it to the most light."""
        if self.sig is None:
            return
        w = min(self.trail - self.lead, self.max_us)
        s = self.best_start(w)
        self.set_edges(s, s + w)

    # ---- mouse: drag edges, pan with momentum, wheel zoom ----------------------------
    def box_px(self, box):
        return box.get_transform().transform((box.get_xdata()[0], self.BOX_Y))

    def data_x(self, px):
        return self.ax.transData.inverted().transform((px, 0))[0]

    def press(self, ev):
        if ev.button != 1 or ev.inaxes is not self.ax:
            return
        self.stop_momentum()
        if ev.dblclick:
            self.reset_view()
            return
        x0, x1 = self.ax.get_xlim()
        for name, box, x in (("lead", self.b_lead, self.lead), ("trail", self.b_trail, self.trail)):
            if x0 <= x <= x1:
                bx, by = self.box_px(box)
                if abs(bx - ev.x) <= self.GRAB_PX and abs(by - ev.y) <= self.GRAB_PX:
                    self.drag = name
                    self.canvas.get_tk_widget().config(cursor="sb_h_double_arrow")
                    return
        self.drag = "pan"
        self.pan0 = (ev.x, self.ax.get_xlim())
        self.samples = [(time.time(), ev.x)]
        self.canvas.get_tk_widget().config(cursor="fleur")

    def show_cursor(self, ev):
        """The mouse position in data units: time (and frame + offset) and ADU."""
        if ev.inaxes is not self.ax:
            return
        x, y = self.ax.transData.inverted().transform((ev.x, ev.y))
        k = int(x // self.T)
        self.cursor["text"] = ("cursor: t %.2f us (frame %d + %.2f us)   %.1f ADU"
                               % (x, k, x - k * self.T, y))

    def motion(self, ev):
        self.show_cursor(ev)
        if self.drag in ("lead", "trail"):
            x = self.data_x(ev.x)
            if self.drag == "lead":
                self.set_edges(x, self.trail, "lead")
            else:
                self.set_edges(self.lead, x)
        elif self.drag == "pan":
            x0, xl = self.pan0
            self.pan_to(xl, ev.x - x0)
            self.samples = (self.samples + [(time.time(), ev.x)])[-6:]
        else:                                        # hover: show which boxes grab
            over = False
            if ev.inaxes is self.ax:
                for box in (self.b_lead, self.b_trail):
                    bx, by = self.box_px(box)
                    if abs(bx - ev.x) <= self.GRAB_PX and abs(by - ev.y) <= self.GRAB_PX:
                        over = True
            self.canvas.get_tk_widget().config(cursor="sb_h_double_arrow" if over else "")

    def release(self, ev):
        if self.drag == "pan" and len(self.samples) >= 2:
            (t0, xa), (t1, xb) = self.samples[0], self.samples[-1]
            if time.time() - t1 < 0.08 and t1 > t0:  # still moving when released: glide
                self.start_momentum((xb - xa) / (t1 - t0))
        self.drag = None
        self.canvas.get_tk_widget().config(cursor="")

    def set_x(self, lo, hi):
        """Set the time axis, kept inside frame 0's vsync .. the end of frame 4: no wider
        than the five frames, and slid back in if it hangs over either end. Returns True
        if it had to be held back (so a glide can stop at the wall)."""
        end = 5 * self.T
        w = min(hi - lo, end)
        lo2 = min(max(lo, 0.0), end - w)
        self.ax.set_xlim(lo2, lo2 + w)
        return abs(lo2 - lo) > 1e-9 or abs(w - (hi - lo)) > 1e-9

    def pan_to(self, xl, dx_px):
        """Set the time axis to xl shifted by a pixel offset. The ADU axis does not pan.
        Returns True when the view hit the start or end of the five frames."""
        sx = (xl[1] - xl[0]) / self.ax.bbox.width
        hit = self.set_x(xl[0] - dx_px * sx, xl[1] - dx_px * sx)
        self.auto = False
        self.canvas.draw_idle()
        return hit

    def start_momentum(self, vx):
        if abs(vx) < 150:                            # px/s: a slow release just stops
            return
        self.momentum = [vx, time.time()]
        self.after(self.TICK_MS, self.glide)

    def glide(self):
        if self.momentum is None:
            return
        vx, t = self.momentum
        now = time.time()
        hit = self.pan_to(self.ax.get_xlim(), vx * (now - t))
        vx *= np.exp(-self.FRICTION * (now - t))
        if hit or abs(vx) < 20:                      # stop at the first / last frame
            self.momentum = None
            return
        self.momentum = [vx, now]
        self.after(self.TICK_MS, self.glide)

    def stop_momentum(self):
        self.momentum = None

    def scroll(self, ev):
        if ev.inaxes is not self.ax:
            return
        self.stop_momentum()
        f = 0.8 if ev.button == "up" else 1 / 0.8    # up = zoom in; time axis only
        x0, x1 = self.ax.get_xlim()
        c = ev.xdata
        if (x1 - x0) * f < 2.0 and f < 1:             # no closer than 2 us across
            return
        self.set_x(c - (c - x0) * f, c + (x1 - c) * f)
        self.auto = False
        self.canvas.draw_idle()

    def reset_view(self):
        self.stop_momentum()
        self.auto = True
        self.ax.set_xlim(0, 5 * self.T)
        self.canvas.draw_idle()

    # ---- running ---------------------------------------------------------------------
    def run(self, key):
        doc = self.app.doc
        title = SWEEPS[key][0]
        if doc.sweeps.get(key) and not messagebox.askokcancel(
                "Replace sweep", "Run a new %s sweep and replace the one in this document?"
                % title, parent=self):
            return
        if not self.app.ensure_connected():
            return
        r = self.runners[key]
        try:
            r.start(doc)
        except Exception as ex:
            messagebox.showerror("Cannot start", str(ex), parent=self)
            return
        self.app.changed()
        self.after(200, lambda: self.poll(r))

    def poll(self, r):
        sw = self.app.doc.sweeps.get(r.key)
        last = None
        while True:
            try:
                kind, v = r.q.get_nowait()
            except queue.Empty:
                break
            if kind == "rows":
                i, n, rows = v
                sw["rows"] += rows
                last = (i, n)
            elif kind == "log":
                print(v, flush=True)
            else:
                self.finish(r, sw, v if kind == "error" else None)
                return
        if last:
            self.app.changed()
            i, n = last
            el = time.time() - r.t0
            sat = sum(float(x[5]) >= 1000 for x in sw["rows"])
            self.status["text"] = (
                "%s running: delay %d/%d   %.0f s elapsed, ~%.0f s left   saturated %d/%d   "
                "log %s" % (r.title, i + 1, n, el, el / (i + 1) * (n - i - 1), sat,
                            len(sw["rows"]), r.log_csv))
        self.after(200, lambda: self.poll(r))

    def stop(self):
        r = self.busy_runner()
        if r:
            r.stop()
            self.buttons()
            self.status["text"] = "%s: stopping after this delay..." % r.title

    def finish(self, r, sw, err):
        r.worker.join(timeout=5)
        sw["complete"] = err is None and not r.stop_ev.is_set()
        self.app.changed()
        if err is not None:
            self.status["text"] = "%s ERROR: %s   (%d readings kept; log %s)" % (
                r.title, err, len(sw["rows"]), r.log_csv)

    def shutdown(self):
        self.stop_momentum()
        if self.iris_dlg is not None:
            self.iris_dlg.done()
        for r in self.runners.values():
            r.shutdown()

    @property
    def running(self):
        return self.busy_runner() is not None or self.iris_dlg is not None

    # ---- iris ------------------------------------------------------------------------
    def peak_delay(self):
        """Delay (us after vsync, within one frame) of the brightest WWWWW reading, or None.

        The five positions are all white, so the sweep is folded onto one frame and
        averaged per delay. When the brightest readings clip at 1023 there is no single
        maximum: the middle of the longest run of clipped delays is the one most surely
        inside a bright slot, so that is taken.
        """
        sw = self.app.doc.sweeps.get("wwwww")
        if not sw or not sw["rows"]:
            return None
        acc = {}
        for r in sw["rows"]:
            acc.setdefault(round(float(r[2]), 2), []).append(float(r[5]))
        ds = sorted(acc)
        avg = np.array([np.mean(acc[d]) for d in ds])
        top = avg.max()
        hot = avg >= top - 0.5
        best, i = None, 0
        while i < len(ds):
            if not hot[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(ds) and hot[j + 1]:
                j += 1
            if best is None or j - i > best[1] - best[0]:
                best = (i, j)
            i = j + 1
        return float(ds[(best[0] + best[1]) // 2]), float(top)

    def adjust_iris(self):
        if self.running or not self.app.ensure_connected():
            return
        pk = self.peak_delay()
        if pk is None:
            messagebox.showinfo("Adjust iris", "Run (or import) a WWWWW sweep first -- the "
                                "iris is set at its brightest delay.", parent=self)
            return
        self.iris_dlg = IrisDialog(self, self.app.doc, pk[0], pk[1])
        self.buttons()

    # ---- import / export (Sweep menu) ------------------------------------------------
    def import_csv(self, key):
        doc = self.app.doc
        title = SWEEPS[key][0]
        if self.running:
            return
        if doc.sweeps.get(key) and not messagebox.askokcancel(
                "Replace sweep", "Replace this document's %s sweep?" % title, parent=self):
            return
        path = filedialog.askopenfilename(parent=self, initialdir=ROOT,
                                          title="Import %s sweep" % title,
                                          filetypes=[("lag_sweep CSV", "*.csv"), ("All", "*")])
        if not path:
            return
        try:
            doc.import_csv(key, path)
        except Exception as e:
            messagebox.showerror("Import failed", str(e), parent=self)
            return
        self.app.changed()

    def export_csv(self, key):
        if not self.app.doc.sweeps.get(key):
            messagebox.showinfo("Nothing to export", "No %s sweep in this document."
                                % SWEEPS[key][0], parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, initialdir=ROOT, defaultextension=".csv",
            initialfile="lag_sweep_%s_%s.csv" % (key, time.strftime("%Y-%m-%d")),
            filetypes=[("CSV", "*.csv")])
        if path:
            self.app.doc.export_csv(key, path)

    def export_json(self):
        path = filedialog.asksaveasfilename(
            parent=self, initialdir=ROOT, defaultextension=".json",
            initialfile="exposure_window_%s.json" % time.strftime("%Y-%m-%d"),
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        units = int(round((self.trail - self.lead) / lc.EXPO_UNIT_US))
        cap = self.captured(self.lead, self.trail)
        with open(path, "w") as fh:
            json.dump({"T_us": self.T, "lead_us": round(self.lead, 2),
                       "trail_us": round(self.trail, 2),
                       "delay_ticks": int(round(self.lead / TICK_US)),
                       "expo_units": units, "expo_us": units * lc.EXPO_UNIT_US,
                       "white_frame_light_inside": None if cap is None else round(cap, 4),
                       "max_expo_us": self.max_us}, fh, indent=2)


# ======================================================================================
# Adjust iris: live reading at the WWWWW peak
# ======================================================================================
class IrisDialog(tk.Toplevel):
    """Hold a short exposure on the brightest part of the WWWWW sweep and show every
    reading live, so the lens iris can be turned until the peak no longer clips.

    The camera runs the same FPGA white cycle the WWWWW sweep used (255 / 254), at the
    peak's delay, so what is read here is the top of that sweep. The aim is the target
    band with NO saturated pixels: the mean can sit below 1023 while the brightest ROI
    pixels already clip, so the saturated count is the number that says "close it more".

    Done records the setting in the document. Sweeps measured before an iris change are
    no longer comparable, so it says so if the document already holds any.
    """

    N = 300                                          # readings on the chart
    TARGET = (750.0, 900.0)                          # ADU: bright, with headroom

    def __init__(self, view, doc, delay_us, sweep_peak):
        super().__init__(view)
        self.view, self.doc = view, doc
        self.title("Adjust iris")
        self.geometry("900x560+80+80")
        self.protocol("WM_DELETE_WINDOW", self.done)
        self.delay_us = delay_us
        self.q = queue.Queue()
        self.stop_ev = threading.Event()
        self.means, self.sats = [], []

        top = ttk.Frame(self)
        top.pack(fill="x", **PAD)
        ttk.Label(top, text="delay (us)").pack(side="left")
        self.delay_v = tk.StringVar(value="%.1f" % delay_us)
        de = ttk.Entry(top, textvariable=self.delay_v, width=9)
        de.pack(side="left", padx=(4, 12))
        de.bind("<Return>", lambda e: self.retarget())
        ttk.Label(top, text="exposure %.3f us   (WWWWW peak %.0f ADU at this delay when swept)"
                  % (13 * lc.EXPO_UNIT_US, sweep_peak), foreground="#555").pack(side="left")
        ttk.Button(top, text="Done", command=self.done).pack(side="right")
        self.big = ttk.Label(self, text="starting...", font=("Consolas", 22, "bold"))
        self.big.pack(fill="x", padx=8)
        self.hint = ttk.Label(self, text="", font=("Segoe UI", 11))
        self.hint.pack(fill="x", padx=8)

        self.fig = Figure(figsize=(9, 4.5))
        self.ax = self.fig.add_subplot(211)
        self.az = self.fig.add_subplot(212, sharex=self.ax)
        self.ax.axhspan(*self.TARGET, color="#2e9e4f", alpha=.12, lw=0)
        self.ax.axhline(1023, color="#d02a2a", ls="--", lw=1)
        self.ax.set_ylim(0, 1080)
        self.ax.set_xlim(0, self.N)
        self.ax.set_ylabel("ROI mean (ADU)")
        self.ax.grid(alpha=.3)
        self.az.set_ylim(-5, 261)
        self.az.set_ylabel("saturated px")
        self.az.set_xlabel("reading (newest on the right)")
        self.az.grid(alpha=.3)
        self.line, = self.ax.plot([], [], lw=1.2, color="#1f5fd0")
        self.sline, = self.az.plot([], [], lw=1.2, color="#d02a2a")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("resize_event", lambda e: fit_margins(self.fig))
        fit_margins(self.fig)

        try:
            lc.start_impulse(doc.ser, 255, 254, 5.0, doc.mx)   # the WWWWW cycle, 13 units
            self.set_delay(delay_us)
        except Exception as ex:
            messagebox.showerror("Adjust iris", str(ex), parent=self)
            self.after(10, self.done)
            return
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()
        self.after(100, self.poll)

    def set_delay(self, d):
        ticks = int(round(d / TICK_US))
        lc.set_delay(self.doc.ser, ticks)
        lc.wait_delay(self.doc.ser, ticks)
        self.delay_us = d

    def retarget(self):
        try:
            d = float(self.delay_v.get())
        except ValueError:
            return
        if not (0 <= d < (self.doc.T or 8333.3)):
            return
        self.stop_ev.set()
        self.worker.join(timeout=3)
        self.set_delay(d)
        self.means, self.sats = [], []
        self.stop_ev.clear()
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()

    def work(self):
        """Worker thread: read ROI lines until stopped."""
        ser = self.doc.ser
        try:
            ser.reset_input_buffer()
            while not self.stop_ev.is_set():
                rows = lc.collect(ser, 4, timeout=0.5, sat=True)
                for r in rows:
                    if r[1] == 256:
                        self.q.put(("r", (r[0], r[4])))
        except Exception as ex:
            self.q.put(("error", ex))

    def poll(self):
        if not self.winfo_exists():
            return
        new = False
        while True:
            try:
                kind, v = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "r":
                self.means.append(v[0])
                self.sats.append(v[1])
                new = True
            elif kind == "error":
                self.big["text"] = "ERROR: %s" % v
        if new:
            self.means, self.sats = self.means[-self.N:], self.sats[-self.N:]
            x = range(len(self.means))
            self.line.set_data(x, self.means)
            self.sline.set_data(x, [s if s is not None else np.nan for s in self.sats])
            m, s = self.means[-1], self.sats[-1]
            recent = self.means[-30:]
            self.big["text"] = "%6.1f ADU   %s saturated px   (last 30: %.1f .. %.1f)" % (
                m, "-" if s is None else "%3d" % s, min(recent), max(recent))
            clipped = m >= 1022 or (s is not None and s > 0)
            if clipped:
                self.hint["text"], self.hint["foreground"] = \
                    "SATURATED -- close the iris", "#b00020"
            elif m < self.TARGET[0]:
                self.hint["text"], self.hint["foreground"] = \
                    "below the target band -- open the iris a little", "#a06000"
            elif m > self.TARGET[1]:
                self.hint["text"], self.hint["foreground"] = \
                    "above the target band, not clipping -- close it slightly for headroom", \
                    "#a06000"
            else:
                self.hint["text"], self.hint["foreground"] = \
                    "in the target band, nothing clipping -- Done", "#2e7d32"
            self.canvas.draw_idle()
        self.after(100, self.poll)

    def done(self):
        self.stop_ev.set()
        w = getattr(self, "worker", None)
        if w is not None:
            w.join(timeout=3)
        try:
            lc.stop_impulse(self.doc.ser)
        except Exception:
            pass
        view, doc = self.view, self.doc
        if self.means:
            recent = self.means[-30:]
            sats = [s for s in self.sats[-30:] if s is not None]
            had = [k for k in SWEEPS if doc.sweeps.get(k)]
            doc.iris = {"delay_us": round(self.delay_us, 2), "expo_us": 13 * lc.EXPO_UNIT_US,
                        "peak_mean": round(float(np.median(recent)), 1),
                        "saturated_px": int(max(sats)) if sats else None,
                        "when": time.strftime("%Y-%m-%d %H:%M:%S")}
            view.iris_dlg = None
            self.destroy()
            view.app.changed()
            if had:
                messagebox.showinfo(
                    "Re-run the sweeps",
                    "The %s sweep%s in this document %s measured before this iris setting, "
                    "so %s no longer comparable. Run %s again."
                    % (" and ".join(SWEEPS[k][0] for k in had), "s" if len(had) > 1 else "",
                       "were" if len(had) > 1 else "was", "they are" if len(had) > 1 else "it is",
                       "them" if len(had) > 1 else "it"), parent=view)
        else:
            view.iris_dlg = None
            self.destroy()
            view.buttons()


# ======================================================================================
# The application window
# ======================================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.doc = None
        self.view = None
        self.geometry("1300x820+40+40")
        self.protocol("WM_DELETE_WINDOW", self.exit)

        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=False)
        f.add_command(label="New...", accelerator="Ctrl+N", command=self.new)
        f.add_command(label="Open...", accelerator="Ctrl+O", command=self.open)
        f.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        f.add_command(label="Save As...", accelerator="Ctrl+Shift+S", command=self.save_as)
        f.add_command(label="Close", accelerator="Ctrl+W", command=self.close)
        f.add_separator()
        f.add_command(label="Exit", command=self.exit)
        m.add_cascade(label="File", menu=f)
        s = tk.Menu(m, tearoff=False)
        for k in SWEEPS:
            s.add_command(label="Import %s CSV..." % SWEEPS[k][0],
                          command=lambda k=k: self.view and self.view.import_csv(k))
        s.add_separator()
        for k in SWEEPS:
            s.add_command(label="Export %s CSV..." % SWEEPS[k][0],
                          command=lambda k=k: self.view and self.view.export_csv(k))
        s.add_command(label="Export exposure window JSON...",
                      command=lambda: self.view and self.view.export_json())
        m.add_cascade(label="Sweep", menu=s)
        self.file_menu, self.sweep_menu, self.menubar = f, s, m
        self.config(menu=m)
        self.bind_all("<Control-n>", lambda e: self.new())
        self.bind_all("<Control-o>", lambda e: self.open())
        self.bind_all("<Control-s>", lambda e: self.save())
        self.bind_all("<Control-S>", lambda e: self.save_as())
        self.bind_all("<Control-w>", lambda e: self.close())

        self.empty = ttk.Label(self, text="No document.\n\nFile > New...   to connect and "
                               "measure\nFile > Open...  to load a saved profile",
                               justify="center", foreground="#666", font=("Segoe UI", 12))
        self.conn = ttk.Label(self, text="", font=("Consolas", 9), anchor="w")
        self.show_doc()

    # ---- state -----------------------------------------------------------------------
    def busy(self):
        return self.view is not None and self.view.running

    def changed(self, redraw=True):
        """Something in the document changed: title, and the view if asked."""
        if self.doc is not None:
            self.doc.dirty = True
        if redraw and self.view is not None:
            self.view.refresh()
        self.update_title()

    def update_title(self):
        has = self.doc is not None
        for item in ("Save", "Save As...", "Close"):
            self.file_menu.entryconfig(item, state="normal" if has else "disabled")
        self.menubar.entryconfig("Sweep", state="normal" if has else "disabled")
        if not has:
            self.title("AuV2-SLI projector profiling")
            return
        d = self.doc
        self.title("%s%s - AuV2-SLI projector profiling" % (d.name, " *" if d.dirty else ""))
        self.conn["text"] = ("  %s   %s   frame %s   exposure limit %s   %s" % (
            d.port or "-", d.display or "-", "%.1f us" % d.T if d.T else "-",
            "%.1f us" % (d.mx * lc.EXPO_UNIT_US) if d.mx else "-",
            "CONNECTED" if d.ser is not None else "not connected (Run connects)"))

    def show_doc(self):
        """Swap between the empty window and the document's view."""
        if self.view is not None:
            self.view.destroy()
            self.view = None
        self.empty.pack_forget()
        self.conn.pack_forget()
        if self.doc is None:
            self.empty.pack(expand=True)
        else:
            self.conn.pack(side="bottom", fill="x")
            self.view = ProfileView(self, self)
            self.view.pack(fill="both", expand=True)
        self.update_title()

    # ---- connection ------------------------------------------------------------------
    def ensure_connected(self):
        d = self.doc
        if d.ser is not None:
            return True
        dlg = SetupDialog(self, d.port, d.display)
        self.wait_window(dlg)
        if dlg.result is None:
            return False
        ser, T, mx, port, disp = dlg.result
        if d.T and abs(T - d.T) > 1.0 and any(d.sweeps.values()):
            ser.close()
            messagebox.showerror(
                "Different frame period",
                "The board runs a %.1f us frame; this document was measured at %.1f us.\n"
                "Start a new document for this mode." % (T, d.T), parent=self)
            return False
        d.ser, d.T, d.mx, d.port, d.display = ser, T, mx, port, disp
        self.update_title()
        return True

    # ---- file menu -------------------------------------------------------------------
    def new(self):
        if not self.close():
            return
        dlg = SetupDialog(self)
        self.wait_window(dlg)
        if dlg.result is None:                       # Cancel: stay with no document
            return
        d = Document()
        d.ser, d.T, d.mx, d.port, d.display = dlg.result
        self.doc = d
        self.show_doc()

    def open(self, path=None):
        if not self.close():
            return
        path = path or filedialog.askopenfilename(
            parent=self, initialdir=ROOT, title="Open profile",
            filetypes=[("AuV2-SLI profile", "*" + EXT), ("All", "*")])
        if not path:
            return
        try:
            self.doc = Document.load(path)
        except Exception as e:
            messagebox.showerror("Open failed", str(e), parent=self)
            return
        self.show_doc()

    def save(self):
        if self.doc is None:
            return False
        if self.doc.path is None:
            return self.save_as()
        return self._write(self.doc.path)

    def save_as(self):
        if self.doc is None:
            return False
        path = filedialog.asksaveasfilename(
            parent=self, initialdir=ROOT, defaultextension=EXT,
            initialfile=self.doc.name if self.doc.path else
            "profile_%s%s" % (time.strftime("%Y-%m-%d"), EXT),
            filetypes=[("AuV2-SLI profile", "*" + EXT)])
        return bool(path) and self._write(path)

    def _write(self, path):
        if self.busy():
            messagebox.showinfo("Sweep running", "Stop the sweep before saving.", parent=self)
            return False
        try:
            self.doc.save(path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self)
            return False
        self.update_title()
        return True

    def close(self):
        """Close the document. False if the user backed out."""
        if self.doc is None:
            return True
        if self.busy():
            if not messagebox.askokcancel("Sweep running",
                                          "A sweep is running. Stop it and close?",
                                          parent=self):
                return False
            self.view.shutdown()
        if self.doc.dirty:
            r = messagebox.askyesnocancel("Unsaved changes",
                                          "Save changes to %s?" % self.doc.name, parent=self)
            if r is None or (r and not self.save()):
                return False
        if self.view is not None:
            self.view.stop_momentum()
        self.doc.disconnect()
        self.doc = None
        self.show_doc()
        return True

    def exit(self):
        if self.close():
            self.destroy()


if __name__ == "__main__":
    app = App()
    if len(sys.argv) > 1:
        app.after(100, lambda: app.open(sys.argv[1]))
    app.mainloop()
