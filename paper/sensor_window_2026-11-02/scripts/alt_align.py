"""Average many full and window pictures from the CAM_ALT bitstream and find the
window's true offset against the full frame.

The window picture's rows are counted from its frame start (black rows included);
if it matched the full frame exactly, window (r, c) would be full-frame
(504 + r, 632 + c) in counted rows. This finds the (dy, dx) that best maps window
rows 0..SHOW-1 onto the full-frame average, for both kernel orders, and prints
what the profiling ROI (window rows 8..23, columns 8..23) really covers.

Usage: alt_align.py OUTPREFIX [--n 300] [--expo-us 100] [--delay-ms 3.35]
The viewer must be closed (it holds the Ft+).
"""
import argparse
import ctypes
import struct
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, r"C:\Users\dllau\Developer\AuV2-SLI-sensorwin\ft_usb_video\host")
import campack
import ftd3xx
from ftd3xx.defines import FT_OPEN_BY_INDEX

MAGIC, HDR = 0x30494C53, 32
NMAGIC = (~MAGIC) & 0xFFFFFFFF
WIN_R0, WIN_C0, WIN_W, SHOW = 504, 632, 32, 40
ROI_R0, ROI_C0 = 512, 640


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--expo-us", type=float, default=100.0)
    ap.add_argument("--delay-ms", type=float, default=3.35)
    a = ap.parse_args()

    dev = None
    for _ in range(10):
        dev = ftd3xx.create(0, FT_OPEN_BY_INDEX)
        if dev is not None:
            break
        time.sleep(0.3)
    if dev is None:
        sys.exit("no D3XX device -- close the viewer first")
    for fn in ("abortPipe", "flushPipe"):
        try:
            getattr(dev, fn)(0x82)
        except Exception:
            pass
    dev.setPipeTimeout(0x82, 1000)

    def cmd(op, payload):
        dev.writePipe(0x02, ctypes.create_string_buffer(
            struct.pack("<I", (op << 28) | (payload & 0x0FFFFFFF))), 4)
    cmd(1, max(1, int(a.expo_us / 0.375 + 1e-6)))
    cmd(7, (1 << 27) | int(round(a.delay_ms * 1e5)))
    time.sleep(0.5)

    buf = ctypes.create_string_buffer(1 << 22)
    acc = bytearray()
    magic = struct.pack("<I", MAGIC)
    full_sum, full_n = None, 0
    win_sum, win_n = np.zeros((SHOW, WIN_W)), 0
    nbytes_win = SHOW * WIN_W * 10 // 8
    t0 = time.time()
    while (full_n < a.n or win_n < a.n) and time.time() - t0 < 120:
        n = dev.readPipe(0x82, buf, len(buf))
        acc += bytes(memoryview(buf)[:n])
        end = 0
        pos = acc.find(magic)
        while pos >= 0 and pos + HDR <= len(acc):
            h = struct.unpack_from("<8I", acc, pos)
            ncol, nrow, fb = h[2] & 0xFFFF, h[2] >> 16, h[4]
            if (h[7] == NMAGIC and campack.geometry_ok(ncol, nrow)
                    and fb == campack.fbytes_for(ncol, nrow, h[6])):
                if pos + HDR + fb > len(acc):
                    break
                p = pos + HDR
                if (h[3] >> 31) & 1:
                    if win_n < a.n:
                        win_sum += campack.unpack10_flat(
                            bytes(acc[p:p + nbytes_win])).reshape(SHOW, WIN_W)
                        win_n += 1
                elif full_n < a.n:
                    f = campack.to_frame(bytes(acc[p:p + fb]), h[6], ncol, nrow).astype(np.float64)
                    full_sum = f if full_sum is None else full_sum + f
                    full_n += 1
                end = pos + HDR + fb
                pos = acc.find(magic, end)
            else:
                pos = acc.find(magic, pos + 4)
        if end:
            del acc[:end]
        elif len(acc) > 3 * len(buf):
            del acc[:len(acc) - len(buf)]
    dev.close()
    print("averaged %d full and %d window pictures in %.0f s" % (full_n, win_n, time.time() - t0))
    if not full_n or not win_n:
        sys.exit("missing one kind of picture")

    full = full_sum / full_n
    win = win_sum / win_n
    np.save(a.out + "_full.npy", full)
    np.save(a.out + "_win.npy", win)

    print("\nfull frame, first 8 counted rows (means of columns 8..1271):",
          " ".join("%.1f" % v for v in full[:8, 8:-8].mean(axis=1)))
    print("window picture, row means 0..39:")
    print("  " + " ".join("%.1f" % v for v in win.mean(axis=1)))
    print("full-frame footprint (counted rows 504..543), row means:")
    print("  " + " ".join("%.1f" % v for v in full[WIN_R0:WIN_R0 + SHOW, WIN_C0:WIN_C0 + WIN_W].mean(axis=1)))

    # Match only the window's REAL rows: find where its rows stop being real by
    # trying 24..SHOW rows is fragile; use rows 0..23 + up to 8 black -> take 0..27.
    variants = {"as received": win,
                "kernels un-mirrored": win.reshape(SHOW, 4, 8)[:, :, ::-1].reshape(SHOW, WIN_W)}
    best_all = None
    for name, w in variants.items():
        tpl = w[:28]
        res = []
        for dy in range(-16, 17):
            for dx in range(-16, 17):
                r0, c0 = WIN_R0 + dy, WIN_C0 + dx
                ref = full[r0:r0 + 28, c0:c0 + WIN_W]
                x0, y0 = tpl - tpl.mean(), ref - ref.mean()
                den = np.sqrt((x0 * x0).sum() * (y0 * y0).sum())
                c = float((x0 * y0).sum() / den) if den > 0 else 0.0
                res.append((c, dy, dx, float(np.abs(tpl - ref).mean())))
        res.sort(reverse=True)
        print("\n%s: best offsets (window row 0 = full counted row 504+dy, col 632+dx)" % name)
        for c, dy, dx, d in res[:4]:
            print("  dy %+3d dx %+3d   corr %.4f   mean|diff| %.2f" % (dy, dx, c, d))
        z = [r for r in res if r[1] == 0 and r[2] == 0][0]
        print("  at the design offset (0, 0): corr %.4f   mean|diff| %.2f" % (z[0], z[3]))
        if best_all is None or res[0][0] > best_all[0]:
            best_all = (res[0][0], res[0][1], res[0][2], name)

    c, dy, dx, name = best_all
    print("\nBEST: %s, dy %+d, dx %+d (corr %.4f)" % (name, dy, dx, c))
    print("=> the profiling ROI (window rows 8..23, cols 8..23) covers full-frame counted "
          "rows %d..%d, columns %d..%d; the green box is rows %d..%d, columns %d..%d"
          % (WIN_R0 + dy + 8, WIN_R0 + dy + 23, WIN_C0 + dx + 8, WIN_C0 + dx + 23,
             ROI_R0, ROI_R0 + 15, ROI_C0, ROI_C0 + 15))
    w = variants[name]
    print("box means: green (full avg) %.2f   yellow (window avg) %.2f"
          % (full[ROI_R0:ROI_R0 + 16, ROI_C0:ROI_C0 + 16].mean(), w[8:24, 8:24].mean()))

    def save(arr, path):
        lo, hi = np.percentile(arr, 1), np.percentile(arr, 99.5)
        im = np.clip((arr - lo) / max(hi - lo, 1e-9) * 255, 0, 255).astype(np.uint8)
        Image.fromarray(im).resize((arr.shape[1] * 10, arr.shape[0] * 10), Image.NEAREST).save(path)
    cut = full[WIN_R0:WIN_R0 + SHOW, WIN_C0:WIN_C0 + WIN_W]
    both = np.hstack([cut, np.full((SHOW, 2), np.nan), w])
    both = np.where(np.isnan(both), np.nanmin(both), both)
    save(both, a.out + "_side_by_side.png")
    print("saved", a.out + "_side_by_side.png (left: full-frame footprint, right: window)")


if __name__ == "__main__":
    main()
