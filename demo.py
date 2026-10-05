#!/usr/bin/env python3
"""
HONEY CHAIN — One-Command Automated Demo
=========================================
Start the backend in a separate terminal first:

    python main.py --seed --simulate

Then run:

    python demo.py

Flags:
    --base http://localhost:8000   target server (default shown)
    --fast                         auto-run, short pauses, no Enter presses
    --tamper                       include the ledger-tamper proof at the end
    --no-browser                   don't open web pages automatically
"""
import argparse, base64, os, sys, time, webbrowser
from datetime import datetime
import requests

# ---------- pretty console ----------
if sys.platform == "win32":
    os.system("")                                   # enables ANSI colours on Win10+
COL = sys.stdout.isatty()
if COL:
    OK, WR, ER, DIM, B, R = "\033[92m", "\033[93m", "\033[91m", "\033[2m", "\033[1m", "\033[0m"
else:
    OK = WR = ER = DIM = B = R = ""
LINE = "─" * 62

def head(title): print(f"\n{B}{LINE}\n  {title}\n{LINE}{R}")
def step(txt):   print(f"\n{B}▶ {txt}{R}")
def good(txt):   print(f"  {OK}✓ {txt}{R}")
def warn(txt):   print(f"  {WR}⚠ {txt}{R}")
def info(txt):   print(f"  {DIM}{txt}{R}")
def kv(l, v):    print(f"  {l:<24}: {B}{v}{R}")

def die(msg):
    print(f"\n{ER}✗ DEMO STOPPED{R}\n{msg}")
    sys.exit(1)

def pause():
    if FAST:
        time.sleep(1.0)
    else:
        try: input(f"  {DIM}⏎ Press Enter to continue…{R}")
        except (EOFError, KeyboardInterrupt): print(); sys.exit(0)

# ---------- API helper ----------
SESSION = requests.Session()

def api(method, path, token=None, expect=200, **kw):
    headers = kw.pop("headers", {})
    if token: headers["Authorization"] = f"Bearer {token}"
    try:
        r = SESSION.request(method, BASE + path, headers=headers, timeout=15, **kw)
    except requests.RequestException as e:
        die(f"Cannot reach {BASE}.\nStart the backend first:\n"
            f"    python main.py --seed --simulate\n({e.__class__.__name__}: {e})")
    if r.status_code != expect:
        try:    detail = r.json().get("detail", "")
        except Exception: detail = r.text[:200]
        die(f"{method} {path} → HTTP {r.status_code} — {detail}")
    return r.json() if r.content else {}

# ---------- run ----------
ap = argparse.ArgumentParser(description="Honey Chain automated demo")
ap.add_argument("--base", default="http://localhost:8000")
ap.add_argument("--fast", action="store_true", help="auto-run without Enter presses")
ap.add_argument("--tamper", action="store_true", help="include ledger tamper proof")
ap.add_argument("--no-browser", action="store_true")
args = ap.parse_args()

BASE = args.base.rstrip("/")
FAST = args.fast
T0 = time.time()

print(f"{B}🍯 HONEY CHAIN — AUTOMATED DEMO{R}   {datetime.now():%d %b %Y, %H:%M}")

# ── SCENE 0 · Preflight ──────────────────────────────────────────────
head("SCENE 0 · Preflight check")
h = api("GET", "/api/health")
good(f"backend online — {h['ledger_blocks']} ledger blocks, db {'ok' if h['db'] else 'DOWN'}")
if not h["db"]:
    die("Database unavailable.")
pause()

# ── SCENE 1 · Login ──────────────────────────────────────────────────
head("SCENE 1 · Login (JWT)")
tok = api("POST", "/api/login",
          json={"email": "admin@honeychain.in", "password": "demo1234"})
AUTH = tok["token"]
good(f"logged in as {tok['user']['name']} ({tok['user']['role']})")
info("token: " + AUTH[:30] + "…")
pause()

# ── SCENE 2 · Dashboard numbers ──────────────────────────────────────
head("SCENE 2 · Dashboard")
s = api("GET", "/api/stats")
kv("Beekeepers", s["total_beekeepers"]);  kv("Hives", s["total_hives"])
kv("Honey batches", s["total_batches"]);  kv("On ledger", s["verified_batches"])
kv("Pending quality tests", s["pending_quality"]); kv("Active alerts", s["active_alerts"])
pause()

# ── SCENE 3 · Live IoT ───────────────────────────────────────────────
head("SCENE 3 · Live IoT hive monitoring")
hives = api("GET", "/api/iot/summary")
print(f"  {'HIVE':<9}{'TEMP':>8}{'HUM':>7}{'WEIGHT':>10}   STATUS")
for hv in hives:
    t  = f"{hv['temperature']}°C" if hv["temperature"] is not None else "—"
    hu = f"{hv['humidity']}%"    if hv["humidity"]    is not None else "—"
    w  = f"{hv['weight']}kg"     if hv["weight"]      is not None else "—"
    print(f"  {hv['code']:<9}{t:>8}{hu:>7}{w:>10}   {hv['status']}")
info("readings arrive every 5 s from the built-in simulator (or a real ESP32)")
pause()

# ── SCENE 4 · Create batch → ledger ─────────────────────────────────
head("SCENE 4 · Create honey batch → write to ledger")
keepers = api("GET", "/api/beekeepers")
if not keepers:
    die("No demo data found. Run:  python main.py --seed")
keeper = keepers[0]
hive = next((x for x in hives if x.get("beekeeper") == keeper["farm"]), None)
if not hive:
    die(f"No hive registered for {keeper['farm']}.")
b = api("POST", "/api/batches", token=AUTH,
        json={"beekeeper_id": keeper["id"], "hive_id": hive["id"], "quantity": 25})
CODE = b["code"]
good(f"batch {CODE} created — 25 kg from {keeper['farm']} / {hive['code']}")
kv("Ledger block", f"#{b['block_index']}")
kv("Block hash", b["block_hash"][:36] + "…")
v = api("GET", "/api/ledger/verify")
good("chain re-verified: " + v["detail"])
pause()

# ── SCENE 5 · Processing step ────────────────────────────────────────
head("SCENE 5 · Processing step")
p = api("POST", f"/api/batches/{CODE}/processing", token=AUTH,
        json={"ptype": "Filtering", "operator": "Ramesh Kumar",
              "location": "Kaziranga, Assam"})
good(f"'{p['stage']}' recorded — block #{p['block_index']}")
pause()

# ── SCENE 6 · Quality report ─────────────────────────────────────────
head("SCENE 6 · Quality report (lab)")
q = api("POST", f"/api/batches/{CODE}/quality", token=AUTH,
        json={"moisture": 16.4, "purity": 94.2, "grade": "A", "status": "APPROVED"})
good(f"Grade {q['grade']} / {q['status']} — report hash {q['report_hash'][:16]}… "
     f"(block #{q['block_index']})")
pause()

# ── SCENE 7 · Ownership transfer ─────────────────────────────────────
head("SCENE 7 · Ownership transfer")
tr = api("POST", f"/api/batches/{CODE}/transfer", token=AUTH,
         json={"new_owner": "Guwahati Distribution Hub"})
good(f"ownership: {tr['from']} → {tr['to']}  (block #{tr['block_index']})")
pause()

# ── SCENE 8 · QR code → save a real PNG file ─────────────────────────
head("SCENE 8 · QR code for the jar")
qr = api("GET", f"/api/batches/{CODE}/qr")
png = base64.b64decode(qr["data_url"].split(",", 1)[1])
qr_file = f"QR_{CODE}.png"
with open(qr_file, "wb") as f:
    f.write(png)
good(f"QR image saved  →  {os.path.abspath(qr_file)}")
kv("Scans to", qr["url"])
pause()

# ── SCENE 9 · Consumer verification ──────────────────────────────────
head("SCENE 9 · Consumer verification (public page — no login)")
d = api("GET", f"/api/verify/{CODE}")
good(f"batch {CODE}: " + ("✓ VERIFIED — ledger intact" if d["verified"]
                          else "details found — confirmation pending"))
steps = d["trace"]
info(f"journey has {len(steps)} stages:")
for st in steps:
    info(f"   • {st['title']:<30} {st['actor'] or ''}")
if not args.no_browser:
    webbrowser.open(qr["url"])
    info("opened the consumer page in your browser — this is exactly what a phone shows")
pause()

# ── SCENE 10 · AI insights ───────────────────────────────────────────
head("SCENE 10 · AI insights")
hcode = "HIVE004" if any(x["code"] == "HIVE004" for x in hives) else hives[0]["code"]
api("POST", "/api/iot/readings",
    json={"hive_id": hcode, "temperature": 37.4, "humidity": 58.0, "weight": 25.1})
info(f"simulated a heatwave reading on {hcode} (37.4 °C)…")
a = api("GET", f"/api/ai/hive/{hcode}/analysis")
kv("AI status", a["status"])
for x in a.get("anomalies", []):
    info(f"   ⚠ [{x['source']}] {x['msg']}")
for rec in a.get("recommendations", [])[:2]:
    info(f"   👉 {rec}")
pred = api("GET", f"/api/ai/hive/{hive['code']}/prediction")
if pred.get("prediction_kg") is not None:
    extra = f", R²={pred['r2']}" if pred.get("r2") is not None else ""
    good(f"production forecast for {hive['code']}: {B}{pred['prediction_kg']} kg{R} "
         f"({pred['model']}{extra})")
else:
    info(pred.get("note", "no prediction available"))
pause()

# ── SCENE 11 · Ledger integrity ──────────────────────────────────────
head("SCENE 11 · Ledger integrity")
blocks = api("GET", "/api/ledger")
v = api("GET", "/api/ledger/verify")
good(f"{v['count']} blocks — {v['detail']}")
info("newest blocks:")
for blk in blocks[:5]:
    info(f"   #{blk['index']:<4} {blk['event']:<26} {blk['hash'][:22]}…")
pause()

# ── SCENE 12 · Save the demo report file ─────────────────────────────
head("SCENE 12 · Demo report file")
verify_url = qr["url"]
report = f"""# 🍯 Honey Chain — Demo Report

Generated: {datetime.now():%d %b %Y %H:%M} by `demo.py`  •  Server: {BASE}

## Batch created live
| Field | Value |
|---|---|
| Batch code | **{CODE}** |
| Beekeeper | {keeper['code']} — {keeper['farm']} ({keeper['location']}) |
| Hive | {hive['code']} |
| Quantity | 25 kg |

## Ledger proof
- Block: **#{b['block_index']}**
- Hash: `{b['block_hash']}`
- Chain verification: {'✓ ' + v['detail'] if v['valid'] else '⚠ ' + v['detail']}

## Events written during this demo
| Step | Event | Ledger block |
|---|---|---|
| Create | BATCH_CREATED | #{b['block_index']} |
| Process | Filtering | #{p['block_index']} |
| Quality | Grade {q['grade']} — {q['status']} | #{q['block_index']} |
| Transfer | → {tr['to']} | #{tr['block_index']} |

## Quality report
- Moisture {q.get('moisture', 16.4)}% · Purity {q.get('purity', 94.2)}% → {q['status']}, Grade {q['grade']}
- Report hash: `{q['report_hash']}`

## Consumer verification
- URL: {verify_url}
- QR image: `{os.path.abspath(qr_file)}`
- Result: {'✓ Verified' if d['verified'] else 'pending'}

## AI snapshot
- {hcode}: status **{a['status']}** — {a['anomalies'][0]['msg'] if a.get('anomalies') else 'all normal'}
- Forecast for {hive['code']}: {pred.get('prediction_kg', '—')} kg ({pred.get('model', 'n/a')})

---
*Prototype honesty note: IoT values are simulated (or from an ESP32); AI harvest-training
data is synthetic demo data; the ledger is a local SHA-256 hash chain with live tamper
detection — swap to a real EVM chain via the provided adapter.*
"""
rname = f"demo_report_{CODE}.md"
with open(rname, "w", encoding="utf-8") as f:
    f.write(report)
good(f"report saved  →  {os.path.abspath(rname)}")

# ── SCENE 13 · Tamper proof (optional) ───────────────────────────────
if args.tamper:
    head("SCENE 13 · Tamper demo (data-integrity proof)")
    warn("modifying a historical block behind the scenes…")
    t = api("POST", "/api/ledger/tamper")
    pause()
    v = api("GET", "/api/ledger/verify")
    warn(f"re-verify now says: {v['detail']}")
    pause()
    warn("resetting & reseeding a clean chain…")
    api("POST", "/api/ledger/reset")
    v = api("GET", "/api/ledger/verify")
    good(f"chain healthy again: {v['detail']}")
    info("note: reset restores seed data — rerun this demo for a fresh batch")

# ── DONE ─────────────────────────────────────────────────────────────
head("DEMO COMPLETE")
info(f"total time: {time.time() - T0:.0f} s")
info(f"dashboard:   {BASE}")
info(f"API console: {BASE}/docs")
print(f"\n{B}🍯 Honey Chain — trust you can scan.{R}\n")
if not args.no_browser:
    webbrowser.open(BASE)