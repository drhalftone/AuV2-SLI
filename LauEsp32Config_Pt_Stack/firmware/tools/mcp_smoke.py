"""Bring-up smoke test for the card's MCP server: speaks the protocol the way Claude Code does.

    python mcp_smoke.py --host lau-esp32.local --token <token from the boot console>
    python mcp_smoke.py ... --fetch http://<pc>:8000/build_pt/Au2_SLI_pt.bit --load Au2_SLI_pt --restore

Read-only by default: initialize, tools/list, get_card_info, get_fpga_status, list_bitstreams.
--fetch / --load / --restore change the card or the FPGA and only run when asked.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

_id = 0


def rpc(url, token, method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if not notify:
        _id += 1
        msg["id"] = _id
    req = urllib.request.Request(url, data=json.dumps(msg).encode(), method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": "2025-06-18",
    })
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read().decode()
        dt = time.time() - t0
        if notify:
            assert r.status == 202, f"notification should get 202, got {r.status}"
            return None, dt
    out = json.loads(body)
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"], dt


def call_tool(url, token, name, args=None):
    res, dt = rpc(url, token, "tools/call", {"name": name, "arguments": args or {}})
    payload = json.loads(res["content"][0]["text"])
    flag = "ERROR" if res.get("isError") else "ok"
    print(f"\n== {name} [{flag}, {dt:.2f} s]\n{json.dumps(payload, indent=2)}")
    return payload, res.get("isError", False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="lau-esp32.local")
    ap.add_argument("--token", required=True)
    ap.add_argument("--fetch", metavar="URL", help="fetch_bitstream this URL onto the card")
    ap.add_argument("--load", metavar="NAME", help="load_bitstream NAME into the FPGA")
    ap.add_argument("--restore", action="store_true", help="restore_flash_image at the end")
    a = ap.parse_args()
    url = f"http://{a.host}/mcp"

    # Auth must be enforced before anything else is trusted.
    try:
        rpc(url, "wrong-token", "ping")
        sys.exit("FAIL: a wrong token was accepted")
    except urllib.error.HTTPError as e:
        assert e.code == 401, f"wrong token should get 401, got {e.code}"
        print("auth: wrong token refused (401)")

    init, dt = rpc(url, a.token, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "mcp_smoke", "version": "0"}})
    print(f"initialize ({dt:.2f} s): {init['serverInfo']} protocol {init['protocolVersion']}")
    rpc(url, a.token, "notifications/initialized", notify=True)
    tools, _ = rpc(url, a.token, "tools/list")
    print("tools:", ", ".join(t["name"] for t in tools["tools"]))

    call_tool(url, a.token, "get_card_info")
    call_tool(url, a.token, "get_fpga_status")
    if a.fetch:
        call_tool(url, a.token, "fetch_bitstream", {"url": a.fetch})
    call_tool(url, a.token, "list_bitstreams")
    if a.load:
        call_tool(url, a.token, "load_bitstream", {"name": a.load})
        call_tool(url, a.token, "get_fpga_status")
    if a.restore:
        call_tool(url, a.token, "restore_flash_image")
        call_tool(url, a.token, "get_fpga_status")


if __name__ == "__main__":
    main()
