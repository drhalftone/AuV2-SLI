"""Dump raw RMAGIC reply packets so the wire format can be read directly.

Written because the first M6b reply came back as 00 00 48 where 00 48 B8 was
expected -- exactly one extra 0x00 in front. That is a wire-format question, and
guessing at it from the symptom is how the last four measurement bugs happened.
"""
import os, struct, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ftlink
from ftlink import FtLink, SYNC, OP_R, ck, MAGIC, RMAGIC, HDR

link = FtLink()
link.send_bytes(bytes([SYNC, OP_R, 0x00, ck(OP_R + 0x00)]))

t0 = time.time()
shown = 0
fmag, rmag = struct.pack("<I", MAGIC), struct.pack("<I", RMAGIC)

while time.time() - t0 < 5.0 and shown < 3:
    try:
        n = link.d.readPipe(0x82, link._buf, link._chunk)
    except Exception:
        n = 0
    if n:
        link._acc += link._buf.raw[:n]

    i = 0
    while len(link._acc) - i >= HDR:
        h = struct.unpack_from("<8I", link._acc, i)
        if h[0] == MAGIC and h[7] == (~MAGIC & 0xFFFFFFFF):
            if len(link._acc) - i < HDR + h[4]:
                break
            i += HDR + h[4]
        elif h[0] == RMAGIC and h[7] == (~RMAGIC & 0xFFFFFFFF):
            if len(link._acc) - i < HDR + h[4]:
                break
            pay = bytes(link._acc[i + HDR: i + HDR + h[4]])
            print("REPLY packet  seq=%d  fmt=%d" % (h[1], h[6]))
            print("   w2 (true count) = %d      w4 (padded len) = %d   w5 = %d"
                  % (h[2], h[4], h[5]))
            print("   payload[%d] = %s" % (len(pay), pay[:32].hex(" ")))
            print()
            shown += 1
            i += HDR + h[4]
        else:
            nxt = [p for p in (link._acc.find(fmag, i + 4),
                               link._acc.find(rmag, i + 4)) if p >= 0]
            if not nxt:
                i = max(0, len(link._acc) - HDR)
                break
            i = min(nxt)
    del link._acc[:i]

if shown == 0:
    print("no reply packet seen in 5 s")
link.close()
