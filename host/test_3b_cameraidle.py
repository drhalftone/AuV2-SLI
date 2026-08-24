"""M3/3b: the camera goes away; HDMI must not notice.

    python host/test_3b_cameraidle.py [COM6]

3b is the last of M3's four independence tests and the only one that could not
be RUN. MERGE_MILESTONES recorded it blocked on "either an RTL hook or physically
unstacking the camera": M2 moved sensor ownership into cam_frame_ft and left
uart_ctrl's old reg 0x37 reset mailbox wired to `open`, so nothing could idle the
camera any more. Opcode 6 is that hook.

THE CLAIM UNDER TEST is not "both run at once" -- it is that neither subsystem's
failure changes the other's behaviour. So the pass condition is about HDMI, and
the milestone table's camera column for 3b is deliberately empty:

    HDMI must   : keep VSYNC counting, mode unchanged
    camera must : -- (it is the thing being broken)

WHY THE WITNESS IS THE SERIAL PORT. The camera is being deliberately stopped, and
the frame stream is what the Ft+ mostly carries, so measuring HDMI over the link
whose traffic is being disrupted would confound the two. Port A is untouched by
any of this and reports HDMI directly. This is the carried-forward rule -- camera
status never lives only on Port B -- applied in the other direction.

WHAT WOULD FALSIFY INDEPENDENCE: VSYNC (`N`) stalling while the camera is held
down, or the offline mode (`M`) changing. Either would mean the two datapaths are
coupled through a shared reset, a shared MMCM, or memory-controller contention.

RECOVERY IS CHECKED, THOUGH 3b DOES NOT REQUIRE IT. A hook that needs an FPGA
reconfigure to undo would be nearly useless for the fault-injection work this is
meant to enable, so the test also confirms the camera comes back on its own --
the release re-runs the full boot ROM upload rather than freeing a blank sensor.
"""
import os, sys, time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ftlink import FtLink

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"

fails = []       # 3b's own pass condition -- HDMI only
findings = []    # real defects this run exposed that are NOT 3b criteria


def telem(ser, win=1.3):
    """One parsed status line from Port A: S=sel V=pol T=trig F=fps M=mode N=count..."""
    ser.reset_input_buffer()
    time.sleep(win)
    raw = ser.read(ser.in_waiting or 1).decode("latin1", "replace")
    lines = [l for l in raw.replace("\r", "").split("\n") if "N=" in l and "M=" in l]
    if not lines:
        return None
    out = {}
    for f in lines[-1].split():
        if "=" in f:
            k, v = f.split("=", 1)
            out[k] = v
    return out


def vsync_samples(ser, n=4):
    """N over several telemetry windows.

    N IS A RATE, NOT A CUMULATIVE COUNTER -- VSYNC edges per telemetry window. It
    sits at 0x33/0x34 (51-52) and dithers between them. The first version of this
    test asserted that N must CHANGE between two samples, which is exactly what a
    healthy, steady HDMI output does NOT do; it reported a perfectly good result
    as a failure. test_3c_camerawedge.py already had this right: collect several
    samples and require more than one distinct value. A stalled output pins N to a
    single value (or zero); a live one dithers. Same instrument as the tests that
    already pass, which also makes the results comparable.
    """
    out = []
    for _ in range(n):
        t = telem(ser, 1.2)
        if t is not None and "N" in t:
            out.append(t["N"])
    return out


def frames_in(link, secs):
    """How many frames actually arrive in `secs`, straight off the IN pipe."""
    n0, t0 = link.frames, time.time()
    while time.time() - t0 < secs:
        link.pump()
    return link.frames - n0


link = FtLink()
ser = serial.Serial(PORT, 115200, timeout=0.3)
time.sleep(0.4)
link.drain()

print("3b -- camera idled; HDMI must be undisturbed\n")

# ---- baseline: both subsystems healthy ------------------------------------
t0 = telem(ser, 1.5)
if t0 is None:
    sys.exit("no telemetry on %s -- Port A is the witness, it has to be alive" % PORT)
f_base = frames_in(link, 2.0) / 2.0
print("  baseline   HDMI N=%s M=%s S=%s   camera %.1f fps"
      % (t0.get("N"), t0.get("M"), t0.get("S"), f_base))
if f_base < 50:
    fails.append("camera was not streaming at baseline (%.1f fps) -- nothing to idle"
                 % f_base)

# ---- hold the camera down --------------------------------------------------
# Self-timed at 6 s so a crash in this script cannot strand the camera off; the
# FPGA releases itself regardless of what the host does next.
print("\n  -> opcode 6: hold camera in reset, 6000 ms (self-releasing)")
link.cam_idle(6000)
time.sleep(1.0)

t1 = telem(ser, 1.5)
f_idle = frames_in(link, 2.0) / 2.0
print("  during     HDMI N=%s M=%s S=%s   camera %.1f fps"
      % (t1.get("N") if t1 else "--", t1.get("M") if t1 else "--",
         t1.get("S") if t1 else "--", f_idle))

# The camera must actually have stopped, or the hook did nothing and every
# HDMI result below is vacuous -- a test that cannot fail proves nothing.
if f_idle > 5:
    fails.append("camera did NOT stop (%.1f fps) -- the idle hook had no effect,"
                 " so the HDMI result below proves nothing" % f_idle)

# THE ACTUAL PASS CONDITION -- and it is about HDMI only. The milestone table's
# camera column for 3b is deliberately empty: the camera is the thing being
# broken, so nothing is required of it here.
if t1 is None:
    fails.append("HDMI telemetry stopped while the camera was held down")
else:
    vs = vsync_samples(ser, 4)
    counting = len(vs) >= 2 and len(set(vs)) > 1
    print("     VSYNC counting through the outage : %s %s" % (counting, vs))
    if not counting:
        fails.append("HDMI VSYNC stopped counting while the camera was idle "
                     "(samples %s)" % vs)
    if t1.get("M") != t0.get("M"):
        fails.append("HDMI mode changed while the camera was idle (M %s -> %s)"
                     % (t0.get("M"), t1.get("M")))
    if t1.get("S") != t0.get("S"):
        fails.append("HDMI source select changed while the camera was idle (S %s -> %s)"
                     % (t0.get("S"), t1.get("S")))

# ---- the control plane must still answer with the camera stopped ----------
# Replies leave at frame boundaries, so "no frames" is exactly the case where a
# naive implementation goes silent. The reader checks for a pending reply BEFORE
# it tests for a new frame specifically to prevent that; this proves it.
idv = link.read_reg(0x00, timeout=4.0)
print("     control plane answers with no frames : %s"
      % (("yes, ID=0x%02X" % idv) if idv is not None else "NO -- TIMED OUT"))
if idv != 0x48:
    findings.append(
        "THE Ft+ REPLY PATH STOPS WHEN THE FRAME STREAM STOPS.\n"
        "     Commands still ARRIVE (verified by writing 0x13 over the Ft+ and\n"
        "     reading it back over serial), and the FPGA does emit the reply --\n"
        "     0x3B shows ufifo_EMPTY=0 and 0x3A shows rd_busy=1, so the reader\n"
        "     built the packet. But zero bytes reach the host at any read size.\n"
        "     This CONTRADICTS the design comment at R_IDLE, which states the\n"
        "     reply path is deliberately checked before the new-frame test so\n"
        "     control still answers when the camera is stopped. The FSM does that\n"
        "     correctly; the stall is below it, in the FT601 / USB3 IN path, which\n"
        "     appears to need continuing traffic to flush a partial packet.\n"
        "     NOT a 3b criterion, but it means M6's control plane has an\n"
        "     undocumented dependency on the camera running.")

# ---- release (self-timed) and confirm the camera returns ------------------
print("\n  -> waiting for the self-release to expire")
t_wait = time.time()
while time.time() - t_wait < 12.0:
    if frames_in(link, 1.0) > 5:
        break
back = frames_in(link, 2.0) / 2.0
print("  after      camera %.1f fps  (recovered in ~%.1f s)"
      % (back, time.time() - t_wait))
if back < 50:
    findings.append(
        "THE CAMERA DOES NOT RESUME ON RELEASE (%.1f fps).\n"
        "     0x3A reads calib=1 aligned=1 cap=1 but streaming=0, so the sensor is\n"
        "     out of reset and the datapath is ready -- it was simply never asked\n"
        "     to stream again. Prime suspect: stream_go is a ONE-CYCLE pulse and\n"
        "     `fired` is cleared the instant cam_idle drops, so it fires into\n"
        "     cam_boot_stage1 while that module is still inside its own 2FF reset\n"
        "     sync and the request is swallowed. Fix is a re-arm delay: clear\n"
        "     `fired` a few ms AFTER release, not on the release edge.\n"
        "     NOT a 3b criterion -- 3b requires nothing of the camera -- but the\n"
        "     hook is not much use for repeatable fault injection until it is\n"
        "     fixed." % back)

t2 = telem(ser, 1.5)
if t2 is not None and t0 is not None:
    if t2.get("M") != t0.get("M"):
        fails.append("HDMI mode changed across the whole cycle (M %s -> %s)"
                     % (t0.get("M"), t2.get("M")))

ser.close()
link.close()

print()
if fails:
    print("3b FAIL:")
    for f in fails:
        print("  - " + f)
else:
    print("3b PASS: the camera was held in reset on command and HDMI never noticed.")
    print("         That is the whole of 3b -- the milestone requires nothing of the")
    print("         camera, which is the subsystem deliberately being broken.")
if findings:
    print("\nSEPARATE DEFECTS THIS RUN EXPOSED (not 3b pass/fail):")
    for i, f in enumerate(findings, 1):
        print("  %d. %s" % (i, f))
raise SystemExit(1 if fails else 0)
