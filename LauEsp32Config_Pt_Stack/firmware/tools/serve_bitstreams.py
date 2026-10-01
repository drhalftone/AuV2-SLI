"""Serve FPGA build outputs to the ESP32 card's fetch_bitstream tool.

Writes a sidecar <stem>.json beside every .bit it serves (sha256, git commit, source folder), so
the card verifies the image before loading it, then serves the folder(s) over HTTP and prints the
URL to pass to fetch_bitstream.

    python serve_bitstreams.py ../../../build_pt ../../../build_merged --port 8000

Only .bit files are useful: the card refuses .bin (a flash image, not a JTAG configuration).
"""
import argparse
import functools
import hashlib
import http.server
import json
import os
import socket
import subprocess
import sys


def git_commit(path):
    try:
        out = subprocess.run(["git", "-C", path, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", path, "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True).stdout.strip()
        return out + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return ""


def write_sidecars(folder, description):
    bits = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".bit")]
    commit = git_commit(folder)
    for name in bits:
        path = os.path.join(folder, name)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        side = {
            "sha256": h.hexdigest(),
            "git_commit": commit,
            "source_script": os.path.basename(os.path.abspath(folder)),
        }
        if description:
            side["description"] = description
        with open(os.path.splitext(path)[0] + ".json", "w") as f:
            json.dump(side, f, indent=2)
    return bits


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packet is sent; this just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="+", help="build output folders containing .bit files")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--description", default="", help="sidecar description for every image")
    args = ap.parse_args()

    ip = lan_ip()
    roots = {}
    for folder in args.folders:
        bits = write_sidecars(folder, args.description)
        key = os.path.basename(os.path.abspath(folder))
        roots[key] = os.path.abspath(folder)
        for b in bits:
            print(f"fetch_bitstream url: http://{ip}:{args.port}/{key}/{b}")
    if not roots:
        sys.exit("no folders")

    class Handler(http.server.SimpleHTTPRequestHandler):
        # /<folder-name>/<file> -> that folder; nothing else is reachable
        def translate_path(self, path):
            parts = path.split("?", 1)[0].lstrip("/").split("/", 1)
            if len(parts) == 2 and parts[0] in roots and "/" not in parts[1] and ".." not in parts[1]:
                return os.path.join(roots[parts[0]], parts[1])
            return os.path.join(os.devnull, "nothing")

    print(f"serving on http://{ip}:{args.port}/  (Ctrl+C to stop)")
    http.server.ThreadingHTTPServer(("", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
