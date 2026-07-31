#!/usr/bin/env python3
# ============================================================================
# ft_bench_async.py -- find the FT601's REAL ceiling by removing the reader's
# own overhead, one source at a time.
#
# ft_video_grab.py measured 310 MB/s, but its read path does three
# size-proportional memory passes PER TRANSFER, none overlapped with the DMA:
#
#     buf = ctypes.create_string_buffer(size)   # allocate + zero-fill
#     n = self.dev.readPipe(pipe, buf, size)
#     return buf.raw[:n]                        # copy whole buffer, then slice
#
# At 16 MiB that is ~6 ms of memcpy against a ~54 ms transfer -- so "310 MB/s"
# is the link PLUS that churn, and the two cannot be separated by measurement
# alone. Hence three modes, each removing exactly one thing:
#
#   copy  : depth 1, fresh buffer + full copy per transfer   (the baseline cost)
#   zero  : depth 1, buffer preallocated once, no copies     (isolates memcpy)
#   async : depth N, preallocated, no copies                 (adds pipelining)
#
# `async` is the one that matters for a real camera: with several transfers
# queued, the driver keeps draining through a host stall (scheduler hiccup, AV
# scan, other USB traffic) that would starve a single-transfer reader instantly.
# Peak MB/s is only half the point; --stall measures the other half.
#
#--------------------------------------------------------------------------
# WHY EVERY MODE USES OVERLAPPED I/O -- learned the hard way, 2026-07-31
#--------------------------------------------------------------------------
# The first version of this script used genuinely synchronous reads (passing
# NULL for pOverlapped) for the two depth-1 modes. That combination --
# FT_SetStreamPipe PLUS a synchronous FT_ReadPipe -- is not supported: the pipe
# timeout does NOT apply, so if data stops the read blocks inside the driver
# forever. The process then cannot be killed (it sits in an uninterruptible
# kernel wait), it keeps the device handle, and every later FT_Create returns 3
# (FT_DEVICE_NOT_OPENED) until the USB cable is physically replugged.
#
# So depth-1 here is emulated with an overlapped transfer of depth 1 rather than
# a blocking call. Same measurement, no wedge -- overlapped I/O honours
# FT_SetPipeTimeout and completes with FT_TIMEOUT instead of hanging. Do not
# "simplify" this back to a blocking read.
#
#   python ft_bench_async.py                    # all three modes
#   python ft_bench_async.py --sweep            # size x depth matrix
#   python ft_bench_async.py --stall 50         # cost of a 50 ms consumer freeze
# ============================================================================
import argparse
import ctypes
import sys
import time

import ftd3xx._ftd3xx_win32 as _ft
from ftd3xx.defines import (FT_OK, FT_IO_PENDING, FT_IO_INCOMPLETE, FT_TIMEOUT,
                            FT_OPEN_BY_INDEX)

PIPE = 0x82

# Statuses that mean "not finished yet", not "failed".
PENDING = (FT_IO_PENDING, FT_IO_INCOMPLETE)


class NoData(RuntimeError):
    """Nothing is arriving on the pipe -- FPGA not streaming, or FT601 wedged."""


class Dev:
    """Thin ctypes handle -- deliberately bypasses the ftd3xx wrapper, whose
    read() is the very thing under test."""

    def __init__(self, index=0, pipe=PIPE, timeout_ms=1000):
        self.h = _ft.FT_HANDLE()
        st = _ft.FT_Create(ctypes.c_void_p(index), FT_OPEN_BY_INDEX, ctypes.byref(self.h))
        if st != FT_OK or not self.h:
            raise RuntimeError(
                f"FT_Create failed ({st}). 3 = FT_DEVICE_NOT_OPENED, which usually "
                f"means a previous process still holds the device -- replug the Ft+ "
                f"USB cable."
            )
        self.pipe = pipe
        _ft.FT_AbortPipe(self.h, pipe)
        _ft.FT_FlushPipe(self.h, pipe)
        _ft.FT_SetPipeTimeout(self.h, pipe, timeout_ms)

    def stream(self, size):
        _ft.FT_SetStreamPipe(self.h, False, False, self.pipe, size)

    def close(self):
        try:
            _ft.FT_AbortPipe(self.h, self.pipe)
            _ft.FT_ClearStreamPipe(self.h, False, False, self.pipe)
        except Exception:
            pass
        _ft.FT_Close(self.h)


def run(dev, size, secs, depth, copy=False, stall_ms=0, stall_at=0.5):
    """Queue `depth` overlapped reads, harvest and resubmit round-robin.

    copy=True reproduces ft_video_grab.py's per-transfer allocate+copy cost.
    Returns (bytes, seconds, short_reads). A short read means the pipe returned
    fewer bytes than asked; with a saturating source that only happens on a
    timeout, so it is exactly the signal that the host lost the race.
    """
    bufs = [ctypes.create_string_buffer(size) for _ in range(depth)]
    ovs = [_ft.OVERLAPPED() for _ in range(depth)]
    xfers = [ctypes.c_ulong(0) for _ in range(depth)]
    for i in range(depth):
        if _ft.FT_InitializeOverlapped(dev.h, ctypes.byref(ovs[i])) != FT_OK:
            raise RuntimeError("FT_InitializeOverlapped failed")

    def submit(i):
        st = _ft.FT_ReadPipe(dev.h, dev.pipe, bufs[i], size,
                             ctypes.byref(xfers[i]), ctypes.byref(ovs[i]))
        if st not in (FT_OK, FT_IO_PENDING):
            raise RuntimeError(f"FT_ReadPipe submit failed ({st})")

    # Hoist the ctypes attribute lookups out of the hot loop -- this is a
    # throughput benchmark, so per-iteration Python overhead is measurement error.
    _get_ovl = _ft.FT_GetOverlappedResult

    total = short = 0
    stalled = False
    i = 0
    for k in range(depth):
        submit(k)

    # NEVER call FT_GetOverlappedResult with bWait=TRUE. It ignores the pipe
    # timeout, so if the source stops the call blocks inside the driver
    # permanently: the process becomes unkillable, keeps the device handle, and
    # only a physical replug recovers it. Poll instead, against a hard deadline.
    t0 = time.perf_counter()
    deadline = t0 + secs
    idle_since = t0
    try:
        while True:
            now = time.perf_counter()
            if now >= deadline:
                break
            if stall_ms and not stalled and (now - t0) >= secs * stall_at:
                time.sleep(stall_ms / 1000.0)      # freeze the consumer on purpose
                stalled = True
                idle_since = time.perf_counter()
            st = _get_ovl(dev.h, ctypes.byref(ovs[i]),
                          ctypes.byref(xfers[i]), False)
            if st in PENDING:
                # not done yet; if NOTHING has completed for a while, the source
                # is dead -- bail out loudly rather than spinning to the deadline
                if time.perf_counter() - idle_since > 2.0:
                    raise NoData(
                        "no data on pipe 0x%02X for 2 s -- is the FPGA still "
                        "streaming? (reload the bitstream) or the FT601 needs a "
                        "replug" % dev.pipe)
                continue
            if st == FT_OK:
                n = xfers[i].value
                total += n
                idle_since = time.perf_counter()
                if n != size:
                    short += 1
                if copy:
                    # reproduce the baseline's churn: fresh buffer + full copy
                    tmp = ctypes.create_string_buffer(size)
                    ctypes.memmove(tmp, bufs[i], n)
                    _ = tmp.raw[:n]
            else:
                short += 1
                idle_since = time.perf_counter()
            submit(i)
            i = (i + 1) % depth
        dt = time.perf_counter() - t0
    finally:
        _ft.FT_AbortPipe(dev.h, dev.pipe)
        for k in range(depth):
            _ft.FT_GetOverlappedResult(dev.h, ctypes.byref(ovs[k]),
                                       ctypes.byref(xfers[k]), True)
            _ft.FT_ReleaseOverlapped(dev.h, ctypes.byref(ovs[k]))
    return total, dt, short


def bench(size, secs, depth, copy=False, stall_ms=0):
    dev = Dev()
    try:
        dev.stream(size)
        total, dt, short = run(dev, size, secs, depth, copy=copy, stall_ms=stall_ms)
    finally:
        dev.close()
    return total / dt / 1e6, short


def main():
    ap = argparse.ArgumentParser(description="FT601 zero-copy / async throughput bench.")
    ap.add_argument("--size", type=lambda x: int(x, 0), default=1 << 22)
    ap.add_argument("--depth", type=int, default=8)
    ap.add_argument("--secs", type=float, default=4.0)
    ap.add_argument("--sweep", action="store_true", help="size x depth matrix")
    ap.add_argument("--stall", type=float, default=0, help="freeze consumer N ms mid-run")
    args = ap.parse_args()

    if args.stall:
        print(f"stall tolerance -- consumer frozen {args.stall:.0f} ms mid-run, "
              f"size={args.size//1024}K")
        print(f"{'depth':>6} {'MB/s':>8} {'short reads':>12}")
        for d in (1, 2, 4, 8, 16):
            mbps, short = bench(args.size, args.secs, d, stall_ms=args.stall)
            print(f"{d:>6} {mbps:>8.1f} {short:>12}")
            time.sleep(0.4)
        return 0

    if args.sweep:
        sizes = [1 << 18, 1 << 20, 1 << 22, 1 << 24]
        depths = [1, 2, 4, 8, 16]
        print("async sweep -- MB/s (zero-copy)")
        print("  size " + "".join(f"{('d=%d' % d):>9}" for d in depths))
        for s in sizes:
            row = f"{s//1024:>5}K"
            for d in depths:
                mbps, _ = bench(s, args.secs, d)
                row += f"{mbps:>9.1f}"
                time.sleep(0.25)
            print(row, flush=True)
        return 0

    print(f"size={args.size//1024}K secs={args.secs}")
    print(f"{'mode':>7} {'depth':>6} {'MB/s':>8} {'Gbps':>7}   vs baseline")
    base = None
    for name, depth, copy in (("copy", 1, True), ("zero", 1, False),
                              ("async", args.depth, False)):
        mbps, short = bench(args.size, args.secs, depth, copy=copy)
        if base is None:
            base = mbps
        note = f"  ({short} short)" if short else ""
        print(f"{name:>7} {depth:>6} {mbps:>8.1f} {mbps*8/1000:>7.2f}   "
              f"{(mbps/base - 1)*100:+5.1f}%{note}", flush=True)
        time.sleep(0.4)
    return 0


if __name__ == "__main__":
    sys.exit(main())
