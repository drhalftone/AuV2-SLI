#!/usr/bin/env python3
# ============================================================================
# ft_video_grab.py -- grab the FT601 SLI video stream and measure throughput.
#
# Pairs with the ft_usb_video FPGA design (Alchitry Pt V2 + Ft+). The FPGA
# streams 1280x1024 8-bit-mono SLI cosine fringes out the FT601 (USB 3). Each
# frame is an 8-word header + 1280*1024 pixel bytes:
#
#   word0 = 0x30494C53  == b"SLI0" on the wire   (frame magic)
#   word1 = frame_index (monotonic)
#   word2 = (height<<16)|width  == 0x04000500
#   word3 = phase/freq nibble
#   word4 = bytes_per_frame (pixels) = 0x00140000
#   word5 = pixel words/frame        = 0x00050000
#   word6 = format (1 = 8bpp mono, 4px/word)
#   word7 = ~magic 0xCFB6B3AC
#
# Modes:
#   (default)   PySide6 GUI: live grayscale + FPS / MB/s / dropped frames
#   --bench     headless: parse frames, print MB/s + FPS + drops each second
#   --raw       headless: pure read (no framing) -> the link's upper-bound MB/s
#
# Requires:  pip install ftd3xx numpy pyside6   (numpy/pyside6 only for parse/GUI)
# and FTDI's D3XX driver installed (the FT601 must enumerate as a D3XX device).
# ============================================================================
import argparse
import ctypes
import sys
import threading
import time

MAGIC = b"SLI0"                 # 0x30494C53 little-endian
HDR_BYTES = 32                  # 8 x uint32
DEFAULT_PIPE = 0x82             # FT601 245-sync-FIFO channel-0 IN pipe


# --------------------------------------------------------------------------
# D3XX device wrapper -- tolerant of the ftd3xx wrapper's version drift.
# --------------------------------------------------------------------------
class FtDevice:
    def __init__(self, index=0, pipe=DEFAULT_PIPE, timeout_ms=1000, stream_size=0):
        import ftd3xx
        self._ftd3xx = ftd3xx
        if sys.platform == "win32":
            import ftd3xx._ftd3xx_win32 as _ft
        else:
            import ftd3xx._ftd3xx_linux as _ft
        self._ft = _ft

        n = ftd3xx.createDeviceInfoList()
        if n == 0:
            raise RuntimeError(
                "No D3XX devices found. Check: FT601 powered, USB3 cable, D3XX "
                "driver installed, and the chip is in 245-Synchronous-FIFO mode."
            )
        self.dev = None
        for _ in range(10):                      # open can flake right after a prior close
            self.dev = ftd3xx.create(index, _ft.FT_OPEN_BY_INDEX)
            if self.dev is not None:
                break
            time.sleep(0.2)
        if self.dev is None:
            raise RuntimeError("ftd3xx.create() failed after retries (device busy or wrong driver).")
        self.pipe = pipe

        # Abort anything stale, then (optionally) put the pipe in streaming mode for
        # the highest sustained rate. setStreamPipe tells the driver every transfer
        # on this pipe is exactly stream_size bytes -> larger, fully-pipelined URBs.
        for fn in ("abortPipe", "flushPipe"):
            try:
                getattr(self.dev, fn)(pipe)
            except Exception:
                pass
        try:
            self.dev.setPipeTimeout(pipe, timeout_ms)
        except Exception:
            pass
        if stream_size:
            try:
                self.dev.setStreamPipe(pipe, stream_size)
                self.stream_size = stream_size
            except Exception:
                self.stream_size = 0
        else:
            self.stream_size = 0

    def read(self, size):
        """Return up to `size` bytes from the pipe (may be short); b'' on timeout."""
        buf = ctypes.create_string_buffer(size)
        # The win32 wrapper exposes readPipe(pipe, buffer, len) -> bytesTransferred.
        try:
            n = self.dev.readPipe(self.pipe, buf, size)
        except Exception:
            # Older/newer wrappers: readPipeEx(pipe, len) -> bytes or dict.
            r = self.dev.readPipeEx(self.pipe, size)
            if isinstance(r, dict):
                return bytes(r.get("bytes", b""))[: r.get("bytesTransferred", 0)]
            return bytes(r)
        return buf.raw[:n]

    def close(self):
        try:
            if self.stream_size:
                self.dev.clearStreamPipe(self.pipe)
        except Exception:
            pass
        try:
            self.dev.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Frame assembler: byte stream -> whole frames, with drop detection.
# --------------------------------------------------------------------------
class FrameAssembler:
    def __init__(self):
        import struct
        self._struct = struct
        self.buf = bytearray()
        self.width = 0
        self.height = 0
        self.frame_bytes = 0            # header + pixels
        self.pix_bytes = 0
        self.locked = False
        self.last_idx = None
        self.dropped = 0
        self.frames = 0

    def _try_header(self, off):
        """Parse the 8-word header at self.buf[off:]; set geometry. Return True if valid."""
        w = self._struct.unpack_from("<8I", self.buf, off)
        if w[0] != 0x30494C53:
            return False
        width = w[2] & 0xFFFF
        height = (w[2] >> 16) & 0xFFFF
        fmt = w[6]
        if not (256 <= width <= 8192 and 256 <= height <= 8192) or fmt != 1:
            return False
        self.width, self.height = width, height
        self.pix_bytes = width * height
        self.frame_bytes = HDR_BYTES + self.pix_bytes
        return True

    def feed(self, data):
        """Add bytes; yield (frame_index, pixels_bytes) for each complete frame."""
        self.buf += data
        while True:
            if not self.locked:
                i = self.buf.find(MAGIC)
                if i < 0:
                    # keep a tail in case MAGIC straddles the next chunk
                    if len(self.buf) > 3:
                        del self.buf[:-3]
                    return
                if len(self.buf) - i < HDR_BYTES:
                    del self.buf[:i]
                    return
                if not self._try_header(i):
                    del self.buf[: i + 1]   # false magic in pixel data; skip past it
                    continue
                del self.buf[:i]
                self.locked = True

            if len(self.buf) < self.frame_bytes:
                return
            # validate this frame still starts on magic (else we desynced)
            if bytes(self.buf[:4]) != MAGIC or not self._try_header(0):
                self.locked = False
                del self.buf[:1]
                continue
            idx = self._struct.unpack_from("<I", self.buf, 4)[0]
            pixels = bytes(self.buf[HDR_BYTES:self.frame_bytes])
            del self.buf[:self.frame_bytes]
            self.frames += 1
            if self.last_idx is not None:
                gap = (idx - self.last_idx) & 0xFFFFFFFF
                if gap != 1:
                    self.dropped += max(0, gap - 1)
            self.last_idx = idx
            yield idx, pixels


# --------------------------------------------------------------------------
# Shared latest-state holder (reader thread -> GUI/printer).
# --------------------------------------------------------------------------
class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.mbps = 0.0
        self.fps = 0.0
        self.dropped = 0
        self.width = 0
        self.height = 0
        self.frame = None       # latest pixels (bytes)
        self.running = True


def reader_loop(dev, stats, chunk, parse=True):
    asm = FrameAssembler() if parse else None
    t0 = time.perf_counter()
    bytes_win = 0
    frames_win = 0
    try:
        while stats.running:
            data = dev.read(chunk)
            if not data:
                continue
            bytes_win += len(data)
            if parse:
                for idx, pixels in asm.feed(data):
                    frames_win += 1
                    with stats.lock:
                        stats.frame = pixels
                        stats.width = asm.width
                        stats.height = asm.height
                        stats.dropped = asm.dropped
            now = time.perf_counter()
            dt = now - t0
            if dt >= 1.0:
                with stats.lock:
                    stats.mbps = bytes_win / dt / 1e6
                    stats.fps = frames_win / dt
                t0, bytes_win, frames_win = now, 0, 0
    finally:
        stats.running = False


# --------------------------------------------------------------------------
# Headless modes.
# --------------------------------------------------------------------------
def run_headless(dev, chunk, parse):
    stats = Stats()
    th = threading.Thread(target=reader_loop, args=(dev, stats, chunk, parse), daemon=True)
    th.start()
    hdr = "  MB/s     Gbps    FPS     res         dropped" if parse else "  MB/s     Gbps"
    print(hdr)
    try:
        while stats.running:
            time.sleep(1.0)
            with stats.lock:
                mbps, fps, drop, w, h = stats.mbps, stats.fps, stats.dropped, stats.width, stats.height
            if parse:
                print(f"  {mbps:7.1f}  {mbps*8/1000:5.2f}  {fps:6.1f}  {w}x{h}   {drop}")
            else:
                print(f"  {mbps:7.1f}  {mbps*8/1000:5.2f}")
    except KeyboardInterrupt:
        stats.running = False
        th.join(timeout=1.0)


# --------------------------------------------------------------------------
# GUI mode (PySide6).
# --------------------------------------------------------------------------
def run_gui(dev, chunk):
    import numpy as np
    from PySide6 import QtCore, QtGui, QtWidgets

    stats = Stats()
    th = threading.Thread(target=reader_loop, args=(dev, stats, chunk, True), daemon=True)
    th.start()

    app = QtWidgets.QApplication(sys.argv)
    win = QtWidgets.QWidget()
    win.setWindowTitle("FT601 SLI video")
    lay = QtWidgets.QVBoxLayout(win)
    info = QtWidgets.QLabel("waiting for frames...")
    info.setStyleSheet("font-family: monospace; font-size: 13px;")
    view = QtWidgets.QLabel()
    view.setMinimumSize(640, 512)
    view.setAlignment(QtCore.Qt.AlignCenter)
    view.setStyleSheet("background:#111;")
    lay.addWidget(info)
    lay.addWidget(view, 1)

    def tick():
        with stats.lock:
            mbps, fps, drop = stats.mbps, stats.fps, stats.dropped
            w, h, frame = stats.width, stats.height, stats.frame
        info.setText(
            f"{fps:6.1f} fps   {mbps:7.1f} MB/s ({mbps*8/1000:.2f} Gbps)   "
            f"{w}x{h}   dropped: {drop}"
        )
        if frame and w and h and len(frame) >= w * h:
            arr = np.frombuffer(frame, dtype=np.uint8, count=w * h).reshape(h, w)
            img = QtGui.QImage(arr.data, w, h, w, QtGui.QImage.Format_Grayscale8)
            pm = QtGui.QPixmap.fromImage(img).scaled(
                view.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.FastTransformation
            )
            view.setPixmap(pm)

    timer = QtCore.QTimer()
    timer.timeout.connect(tick)
    timer.start(33)                       # ~30 Hz display (capture runs far faster)

    win.resize(900, 800)
    win.show()
    try:
        app.exec()
    finally:
        stats.running = False
        th.join(timeout=1.0)


def main():
    ap = argparse.ArgumentParser(description="Grab/measure the FT601 SLI video stream.")
    ap.add_argument("--bench", action="store_true", help="headless: parse frames, print FPS + MB/s")
    ap.add_argument("--raw", action="store_true", help="headless: pure read, link upper-bound MB/s")
    ap.add_argument("--index", type=int, default=0, help="D3XX device index (default 0)")
    ap.add_argument("--pipe", type=lambda x: int(x, 0), default=DEFAULT_PIPE, help="read pipe id (default 0x82)")
    ap.add_argument("--chunk", type=lambda x: int(x, 0), default=1 << 20, help="read size in bytes (default 1 MiB)")
    ap.add_argument("--stream", action="store_true", help="use setStreamPipe(chunk) for max rate")
    args = ap.parse_args()

    dev = FtDevice(
        index=args.index,
        pipe=args.pipe,
        stream_size=args.chunk if args.stream else 0,
    )
    try:
        if args.raw:
            run_headless(dev, args.chunk, parse=False)
        elif args.bench:
            run_headless(dev, args.chunk, parse=True)
        else:
            run_gui(dev, args.chunk)
    finally:
        dev.close()


if __name__ == "__main__":
    main()
