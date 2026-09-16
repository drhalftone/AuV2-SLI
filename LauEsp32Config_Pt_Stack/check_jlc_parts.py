"""Check every LCSC part number in the BOM against JLCPCB's live parts library.

    python check_jlc_parts.py [production/bom_full.csv] [--boards N]

For each line: the code must exist, its manufacturer part number must match the MPN
we asked for (a wrong code is worse than a missing one -- JLCPCB places whatever the
code names), and stock must cover the placements for N boards. Basic parts carry no
feeder fee; Extended parts carry one each unless JLCPCB marks them "preferred".

Lesson from LauPythonCamera_Pt_Stack README 15.3: a code that goes quietly out of
stock gets silently DROPPED from the order. Run this at order time, not just now.
"""
import csv
import json
import sys
import urllib.request

API = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"


def lookup(code):
    req = urllib.request.Request(
        API,
        data=json.dumps({"keyword": code, "currentPage": 1, "pageSize": 50}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=30))
    for c in ((data.get("data") or {}).get("componentPageInfo") or {}).get("list") or []:
        if c["componentCode"] == code:
            return c
    return None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else "production/bom_full.csv"
    boards = 5
    if "--boards" in sys.argv:
        boards = int(sys.argv[sys.argv.index("--boards") + 1])

    failures = 0
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    rows = [r for r in rows if r.get("Populate", "yes") != "DNP"]
    # stock must cover the TOTAL placements of a code, which can span several BOM lines
    per_code = {}
    for r in rows:
        per_code[r["LCSC Part #"].strip()] = per_code.get(r["LCSC Part #"].strip(), 0) + int(r["Qty"])
    print(f"{'LCSC':>10}  {'lib':8} {'qty/bd':>6} {'stock':>8}  {'MPN (BOM)':28} {'MPN (JLCPCB)':28} result")
    for r in rows:
        code = r["LCSC Part #"].strip()
        qty = int(r["Qty"])
        if not code:
            print(f"{'-':>10}  {'':8} {qty:>6} {'':>8}  {r['MPN']:28} {'':28} NOT ASSEMBLED ({r['Designator']})")
            continue
        c = lookup(code)
        if c is None:
            print(f"{code:>10}  {'?':8} {qty:>6} {'':>8}  {r['MPN']:28} {'':28} FAIL: code not found")
            failures += 1
            continue
        lib = "basic" if c["componentLibraryType"] == "base" else (
            "pref-ext" if c.get("preferredComponentFlag") else "extended")
        model = c["componentModelEn"]
        problems = []
        norm = lambda s: s.upper().replace(" ", "")
        if norm(r["MPN"]) not in norm(model) and norm(model) not in norm(r["MPN"]):
            problems.append("MPN MISMATCH")
        if c["stockCount"] < per_code[code] * boards:
            problems.append(f"STOCK < {per_code[code] * boards}")
        result = "ok" if not problems else "FAIL: " + ", ".join(problems)
        failures += bool(problems)
        print(f"{code:>10}  {lib:8} {qty:>6} {c['stockCount']:>8}  {r['MPN'][:28]:28} {model[:28]:28} {result}")
    print(f"\n{len(rows)} lines, {failures} failing, stock checked for {boards} boards")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
