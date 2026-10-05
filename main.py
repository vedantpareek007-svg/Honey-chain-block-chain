"""
HONEY CHAIN — Lite Working Prototype (FINAL consolidated version)
-----------------------------------------------------------------
Run:
    pip install -r requirements.txt
    python main.py --seed --simulate
    open http://localhost:8000        (login: admin@honeychain.in / demo1234)

Flags:
    --seed       load realistic demo data (also a dashboard button)
    --simulate   background IoT simulator thread (5 hives, every 5 s)
    --hot        simulator drives HIVE004 hot -> live AI alert demo
    --port 8000 / --host 0.0.0.0
"""
import argparse, base64, hashlib, io, json, os, random, secrets, statistics, threading, time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt as pyjwt
import qrcode
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import (create_engine, event as sa_event, Column, Integer, String,
                        Float, DateTime, Text, ForeignKey)
from sqlalchemy.orm import declarative_base, sessionmaker

# ---------------- config ----------------
DB_PATH = os.getenv("HC_DB", "honeychain.db")
SECRET  = os.getenv("HC_SECRET", "dev-only-secret-change-me")
def utcnow(): return datetime.now(timezone.utc)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

@sa_event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor(); cur.execute("PRAGMA journal_mode=WAL"); cur.close()

Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()
app = FastAPI(title="Honey Chain Lite")

MNAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

# ---------------- models ----------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String); email = Column(String, unique=True, index=True)
    pw = Column(String); role = Column(String, default="CONSUMER")

class Beekeeper(Base):
    __tablename__ = "beekeepers"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)             # BK001
    farm = Column(String); location = Column(String)
    user_id = Column(Integer, ForeignKey("users.id"))

class Hive(Base):
    __tablename__ = "hives"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)             # HIVE001
    beekeeper_id = Column(Integer, ForeignKey("beekeepers.id"))
    location = Column(String); lat = Column(Float); lon = Column(Float)
    species = Column(String, default="Apis cerana indica")

class Batch(Base):
    __tablename__ = "honey_batches"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)             # HC001
    beekeeper_id = Column(Integer, ForeignKey("beekeepers.id"))
    hive_id = Column(Integer, ForeignKey("hives.id"))
    collection_date = Column(DateTime); qty = Column(Float)
    quality_status = Column(String, default="PENDING")
    owner = Column(String)
    block_index = Column(Integer); block_hash = Column(String)
    qr = Column(Text)

class Processing(Base):
    __tablename__ = "processing_records"
    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("honey_batches.id"))
    ptype = Column(String); operator = Column(String); location = Column(String)
    notes = Column(Text); ts = Column(DateTime)
    block_index = Column(Integer); block_hash = Column(String)

class Quality(Base):
    __tablename__ = "quality_reports"
    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("honey_batches.id"))
    moisture = Column(Float); purity = Column(Float)
    grade = Column(String); status = Column(String); lab = Column(String)
    ts = Column(DateTime); report_hash = Column(String)
    block_index = Column(Integer); block_hash = Column(String)

class Reading(Base):
    __tablename__ = "iot_readings"
    id = Column(Integer, primary_key=True)
    hive_id = Column(Integer, ForeignKey("hives.id"))
    temp = Column(Float); hum = Column(Float); weight = Column(Float)
    lat = Column(Float); lon = Column(Float); ts = Column(DateTime)

class Ownership(Base):
    __tablename__ = "ownership_history"
    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("honey_batches.id"))
    prev = Column(String); new = Column(String); ts = Column(DateTime)
    block_index = Column(Integer); block_hash = Column(String)

class ProductionRecord(Base):
    """Synthetic demo harvest history for the prediction model (documented as demo data)."""
    __tablename__ = "production_records"
    id = Column(Integer, primary_key=True)
    hive_id = Column(Integer, ForeignKey("hives.id"))
    dt = Column(DateTime); season = Column(Integer)
    avg_t = Column(Float); avg_h = Column(Float); avg_w = Column(Float); kg = Column(Float)

class Block(Base):
    """Tamper-evident local ledger. Each block's hash covers its payload AND the
    previous block's hash — any edit to history breaks verification (try it in the UI)."""
    __tablename__ = "ledger_blocks"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime); event = Column(String)
    payload = Column(Text); prev_hash = Column(String); hash = Column(String)

Base.metadata.create_all(engine)

# ---------------- ledger (hash chain) ----------------
_ledger_lock = threading.Lock()

def _hash_str(idx, ts_str, event, blob, prev_hash):
    return hashlib.sha256(f"{idx}|{ts_str}|{event}|{blob}|{prev_hash}".encode()).hexdigest()

def _ts_candidates(dt):
    """SQLite stores naive datetimes; hashes may have been made with tz info.
    Try all canonical forms so old databases keep verifying."""
    if dt is None:
        return [""]
    base = dt.isoformat()
    if dt.tzinfo is None:
        return [base, base + "+00:00", base + "Z"]
    return [base, dt.replace(tzinfo=None).isoformat()]

def add_block(db, event: str, payload: dict) -> Block:
    with _ledger_lock:
        prev = db.query(Block).order_by(Block.id.desc()).first()
        idx = (prev.id + 1) if prev else 1
        prev_hash = prev.hash if prev else "0" * 64
        ts = utcnow()
        blob = json.dumps(payload, sort_keys=True, default=str)
        h = _hash_str(idx, ts.isoformat(), event, blob, prev_hash)
        b = Block(id=idx, ts=ts, event=event, payload=blob, prev_hash=prev_hash, hash=h)
        db.add(b); db.commit()
        return b

def verify_chain(db) -> dict:
    blocks = db.query(Block).order_by(Block.id).all()
    prev_hash = "0" * 64
    for b in blocks:
        ok = False
        for ts_str in _ts_candidates(b.ts):
            if b.prev_hash == prev_hash and \
               b.hash == _hash_str(b.id, ts_str, b.event, b.payload or "", prev_hash):
                ok = True
                break
        if not ok:
            return {"valid": False, "count": len(blocks), "broken_at": b.id,
                    "detail": f"Block #{b.id} does not match its recorded hash — "
                              "the data was modified after writing."}
        prev_hash = b.hash
    return {"valid": True, "count": len(blocks), "broken_at": None, "detail": "All blocks intact."}

# ---------------- auth ----------------
def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000)
    return f"{salt}${dk.hex()}"

def check_pw(pw: str, stored: str) -> bool:
    try:
        salt, dk = stored.split("$")
        return secrets.compare_digest(
            hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex(), dk)
    except Exception:
        return False

def get_db():
    db = Session()
    try: yield db
    finally: db.close()

def auth(request: Request, db=Depends(get_db)):
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        raise HTTPException(401, "Login required.")
    try:
        payload = pyjwt.decode(h[7:], SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(401, "Session expired — please log in again.")
    u = db.get(User, int(payload.get("sub", 0)))
    if not u:
        raise HTTPException(401, "User not found.")
    return u

# ---------------- QR ----------------
def qr_data_url(request: Request, code: str):
    base = str(request.base_url).rstrip("/")          # works on LAN too (phone scanning!)
    url = f"{base}/verify/{code}"
    img = qrcode.make(url, box_size=8, border=2)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return url, "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

# ---------------- AI ----------------
T_CRIT_HI, T_WARN_HI, T_WARN_LO = 38.5, 35.5, 18.0
H_CRIT_HI, H_WARN_HI, H_WARN_LO = 90.0, 75.0, 40.0
W_CRIT = 5.0

def quick_status(t, h, w):
    s = "NORMAL"
    if t is not None and (t > T_CRIT_HI or t < T_WARN_LO - 6): s = "CRITICAL"
    elif t is not None and (t > T_WARN_HI or t < T_WARN_LO): s = "WARNING"
    if h is not None and h > H_CRIT_HI: s = "CRITICAL"
    elif h is not None and (h > H_WARN_HI or h < H_WARN_LO): s = "WARNING" if s == "NORMAL" else s
    if w is not None and w < W_CRIT: s = "CRITICAL"
    return s

def analyze_hive(db, hive) -> dict:
    rows = (db.query(Reading).filter(Reading.hive_id == hive.id)
              .order_by(Reading.ts.desc()).limit(24).all())
    rows.reverse()
    if not rows:
        return {"hive": hive.code, "status": "NO_DATA", "anomalies": [],
                "recommendations": ["No sensor data yet — enable the simulator or connect an ESP32."]}
    last = rows[-1]
    t, h, w = last.temp, last.hum, last.weight
    anomalies, recs = [], []
    def flag(src, sev, msg, advice):
        anomalies.append({"source": src, "severity": sev, "msg": msg}); recs.append(advice)
    if t > T_CRIT_HI: flag("threshold", "CRITICAL", f"Temperature {t}°C is dangerously high.", "Check ventilation and shading now.")
    elif t > T_WARN_HI: flag("threshold", "WARNING", f"Temperature {t}°C is above the safe range.", "Monitor temperature and improve ventilation.")
    elif t < T_WARN_LO: flag("threshold", "WARNING", f"Temperature {t}°C is below the safe range.", "Consider wind protection / insulation.")
    if h > H_CRIT_HI: flag("threshold", "CRITICAL", f"Humidity {h}% is dangerously high.", "Improve airflow to reduce moisture.")
    elif h > H_WARN_HI: flag("threshold", "WARNING", f"Humidity {h}% is above the safe range.", "Check rain exposure and ventilation.")
    elif h < H_WARN_LO: flag("threshold", "WARNING", f"Humidity {h}% is low.", "Provide a nearby water source.")
    if w < W_CRIT: flag("threshold", "CRITICAL", f"Hive weight {w} kg is critically low.", "Inspect colony — possible swarm or stores shortage.")
    if len(rows) >= 2 and rows[-2].weight - w > 2.0:
        flag("threshold", "WARNING", f"Sudden weight drop of {rows[-2].weight - w:.1f} kg.", "Inspect for swarming, robbing or tampering.")
    if len(rows) >= 8:  # z-score vs this hive's own recent behaviour
        for name, vals, cur in (("Temperature", [r.temp for r in rows], t),
                                ("Humidity", [r.hum for r in rows], h)):
            sd = statistics.stdev(vals[:-1]) if len(vals) > 2 else 0
            if sd > 1e-6:
                z = (cur - statistics.mean(vals[:-1])) / sd
                if abs(z) > 2.5:
                    flag("zscore", "WARNING", f"{name} {cur} is unusual for this hive (z={z:.1f}).",
                         f"Compare with recent {name.lower()} readings and inspect the hive.")
    try:  # optional real ML model — auto-used only if scikit-learn is installed
        from sklearn.ensemble import IsolationForest
        import numpy as np
        if len(rows) >= 20:
            X = np.array([[r.temp, r.hum, r.weight] for r in rows])
            if IsolationForest(contamination=0.08, random_state=42).fit_predict(X)[-1] == -1:
                flag("isolation_forest", "WARNING",
                     "Latest reading flagged as unusual by the IsolationForest model.",
                     "Review the last hours of sensor data before inspecting.")
    except ImportError:
        pass
    status = "NORMAL"
    for a in anomalies:
        status = max(status, a["severity"], key=lambda s: {"NORMAL": 0, "WARNING": 1, "CRITICAL": 2}[s])
    if not anomalies:
        recs.append("All readings look normal. Continue routine monitoring.")
    return {"hive": hive.code, "status": status, "temperature": t, "humidity": h, "weight": w,
            "readings_used": len(rows), "anomalies": anomalies,
            "recommendations": list(dict.fromkeys(recs)),
            "note": "Prototype insights from configured thresholds + statistics (IsolationForest if installed). Not a validated diagnosis."}

def _linfit(xs, ys):
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    sxx = sum((x-mx)**2 for x in xs); sxy = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    slope = sxy/sxx if sxx else 0.0
    return slope, my - slope*mx

def predict_production(db, hive) -> dict:
    rows = (db.query(Reading).filter(Reading.hive_id == hive.id)
              .order_by(Reading.ts.desc()).limit(12).all())
    if not rows:
        return {"hive": hive.code, "status": "NO_DATA", "note": "No sensor data — start the simulator."}
    t = statistics.mean(r.temp for r in rows); h = statistics.mean(r.hum for r in rows)
    w = statistics.mean(r.weight for r in rows)
    season = {12:1,1:1,2:1,3:2,4:2,5:2,6:3,7:3,8:3,9:4,10:4,11:4}[utcnow().month]
    hist = db.query(ProductionRecord).filter(ProductionRecord.hive_id == hive.id).all()
    pool = hist or db.query(ProductionRecord).all()
    if len(pool) >= 5:   # real least-squares fit: kg ~ hive weight, plus spring bonus
        slope, intercept = _linfit([p.avg_w for p in pool], [p.kg for p in pool])
        kg = max(slope * w + intercept + (1.4 if season == 2 else 0.0), 0.0)
        ys = [p.kg for p in pool]
        preds = [slope * x + intercept for x in [p.avg_w for p in pool]]
        my = sum(ys)/len(ys)
        ss_res = sum((y-p)**2 for y, p in zip(ys, preds)); ss_tot = sum((y-my)**2 for y in ys)
        r2 = round(1 - ss_res/ss_tot, 3) if ss_tot else None
        return {"hive": hive.code, "prediction_kg": round(kg, 1),
                "model": "Least-squares regression (kg ~ hive weight) + spring adjustment"
                         + (f" trained on hive history ({len(hist)} harvests)" if hist else
                            f" trained on pooled demo history ({len(pool)} rows)"),
                "r2": r2,
                "inputs": {"temp": round(t,1), "humidity": round(h,1), "weight": round(w,1), "season": season},
                "note": "Prototype estimate from demo training data — not agronomic advice."}
    return {"hive": hive.code, "status": "NO_MODEL", "note": "Not enough harvest history yet."}

# ---------------- helpers ----------------
def get_hive(db, code):
    hv = db.query(Hive).filter(Hive.code == code.upper()).first()
    if not hv: raise HTTPException(404, f"Unknown hive '{code}'. Use codes like HIVE001.")
    return hv

def get_batch(db, ref):
    b = (db.query(Batch).filter(Batch.code == ref.upper()).first()
         if not ref.isdigit() else db.get(Batch, int(ref)))
    if not b: raise HTTPException(404, f"Batch '{ref}' not found. Check the code on the jar label.")
    return b

def _blk(b):
    return {"index": b.block_index, "hash": b.block_hash} if b and b.block_hash else None

def build_trace(db, b: Batch):
    bk = db.get(Beekeeper, b.beekeeper_id); hv = db.get(Hive, b.hive_id)
    steps = [{"stage": "HIVE", "title": "Hive & Beekeeper", "when": None,
              "actor": bk.farm, "location": hv.location,
              "details": f"{hv.code} • {hv.species} • keeper {bk.code}", "block": None},
             {"stage": "COLLECTION", "title": "Honey Collection",
              "when": b.collection_date.isoformat() if b.collection_date else None,
              "actor": bk.farm, "location": hv.location,
              "details": f"{b.qty} kg harvested from {hv.code}", "block": _blk(b)}]
    for p in db.query(Processing).filter(Processing.batch_id == b.id).order_by(Processing.ts):
        steps.append({"stage": p.ptype.upper(), "title": p.ptype,
                      "when": p.ts.isoformat() if p.ts else None, "actor": p.operator,
                      "location": p.location, "details": p.notes or "", "block": _blk(p)})
    for q in db.query(Quality).filter(Quality.batch_id == b.id).order_by(Quality.ts):
        steps.append({"stage": "QUALITY", "title": f"Quality Test — Grade {q.grade}",
                      "when": q.ts.isoformat() if q.ts else None, "actor": q.lab,
                      "location": None,
                      "details": f"Moisture {q.moisture}% • Purity {q.purity}% → {q.status}",
                      "block": _blk(q)})
    for o in db.query(Ownership).filter(Ownership.batch_id == b.id).order_by(Ownership.ts):
        if o.prev != "CREATED":
            steps.append({"stage": "TRANSFER", "title": "Ownership Transfer",
                          "when": o.ts.isoformat() if o.ts else None, "actor": o.new,
                          "location": None, "details": f"Handed over from {o.prev}",
                          "block": _blk(o)})
    steps.sort(key=lambda s: s["when"] or "")
    return steps

# ---------------- schemas ----------------
class LoginIn(BaseModel):
    email: str
    password: str

class BeekeeperIn(BaseModel):
    name: str
    email: str
    farm: str
    location: str
    password: str = "demo1234"

class HiveIn(BaseModel):
    beekeeper_id: int
    code: str
    location: str
    lat: Optional[float] = None
    lon: Optional[float] = None

class BatchIn(BaseModel):
    beekeeper_id: int
    hive_id: int
    quantity: float
    collection_date: Optional[str] = None

class QualityIn(BaseModel):
    moisture: float
    purity: float
    grade: str = "A"
    status: str = "APPROVED"
    lab: str = "Brahmaputra Honey Testing Lab"

class ProcessingIn(BaseModel):
    ptype: str
    operator: str
    location: str
    notes: str = ""

class TransferIn(BaseModel):
    new_owner: str

class ReadingIn(BaseModel):
    hive_id: str
    temperature: float
    humidity: float
    weight: float
    latitude: Optional[float] = None
    longitude: Optional[float] = None

# ---------------- API ----------------
@app.post("/api/login")
def login(body: LoginIn, db=Depends(get_db)):
    u = db.query(User).filter(User.email == body.email.strip().lower()).first()
    if not u or not check_pw(body.password, u.pw):
        raise HTTPException(401, "Wrong email or password. Demo: admin@honeychain.in / demo1234")
    tok = pyjwt.encode({"sub": str(u.id), "role": u.role,
                        "exp": utcnow().timestamp() + 12*3600}, SECRET, algorithm="HS256")
    return {"token": tok, "user": {"name": u.name, "email": u.email, "role": u.role}}

@app.get("/api/health")
def health(db=Depends(get_db)):
    try:
        n_blocks = db.query(Block).count()
        return {"status": "ok", "db": True, "ledger_blocks": n_blocks}
    except Exception as e:
        return {"status": "degraded", "db": False, "error": str(e)}

@app.get("/api/stats")
def stats(db=Depends(get_db)):
    batches = db.query(Batch).all()
    alerts = sum(1 for hv in db.query(Hive).all()
                 if analyze_hive(db, hv)["status"] in ("WARNING", "CRITICAL"))
    prod = {}
    for p in db.query(ProductionRecord).all():
        m = MNAMES[p.dt.month - 1]
        prod[m] = prod.get(m, 0) + p.kg
    months = sorted(prod, key=MNAMES.index) if prod else []
    qcount, bcount = {}, {}
    for q in db.query(Quality).all(): qcount[q.grade] = qcount.get(q.grade, 0) + 1
    for b in batches: bcount[b.quality_status] = bcount.get(b.quality_status, 0) + 1
    return {"total_beekeepers": db.query(Beekeeper).count(),
            "total_hives": db.query(Hive).count(),
            "total_batches": len(batches),
            "verified_batches": sum(1 for b in batches if b.block_hash),
            "pending_quality": sum(1 for b in batches if not db.query(Quality).filter(Quality.batch_id == b.id).first()),
            "active_alerts": alerts,
            "production": [{"label": m, "value": round(prod[m], 1)} for m in months],
            "quality_dist": [{"label": f"Grade {k}", "value": v} for k, v in sorted(qcount.items())],
            "batch_status": [{"label": k, "value": v} for k, v in bcount.items()]}

# --- beekeepers / hives ---
@app.get("/api/beekeepers")
def list_beekeepers(db=Depends(get_db)):
    return [{"id": b.id, "code": b.code, "farm": b.farm, "location": b.location,
             "hives": db.query(Hive).filter(Hive.beekeeper_id == b.id).count()}
            for b in db.query(Beekeeper).order_by(Beekeeper.id).all()]

@app.post("/api/beekeepers", status_code=201)
def add_beekeeper(body: BeekeeperIn, db=Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, "Email already registered.")
    n = db.query(Beekeeper).count() + 1
    code = f"BK{n:03d}"
    u = User(name=body.name, email=body.email, pw=hash_pw(body.password), role="BEEKEEPER")
    db.add(u); db.flush()
    bk = Beekeeper(code=code, farm=body.farm, location=body.location, user_id=u.id)
    db.add(bk); db.commit()
    add_block(db, "BEEKEEPER_REGISTERED", {"code": code, "farm": body.farm, "location": body.location})
    return {"id": bk.id, "code": code, "farm": bk.farm, "location": bk.location, "hives": 0}

@app.get("/api/hives")
def list_hives(db=Depends(get_db)):
    out = []
    for hv in db.query(Hive).order_by(Hive.id).all():
        r = (db.query(Reading).filter(Reading.hive_id == hv.id)
               .order_by(Reading.ts.desc()).first())
        bk = db.get(Beekeeper, hv.beekeeper_id)
        out.append({"id": hv.id, "code": hv.code, "location": hv.location,
                    "beekeeper": bk.farm if bk else None,
                    "temperature": r.temp if r else None, "humidity": r.hum if r else None,
                    "weight": r.weight if r else None,
                    "last_updated": r.ts.isoformat() if r else None,
                    "status": quick_status(r.temp, r.hum, r.weight) if r else "NO_DATA"})
    return out

@app.post("/api/hives", status_code=201)
def add_hive(body: HiveIn, db=Depends(get_db), u=Depends(auth)):
    if not db.get(Beekeeper, body.beekeeper_id):
        raise HTTPException(404, "Beekeeper not found — register one first.")
    if db.query(Hive).filter(Hive.code == body.code.upper()).first():
        raise HTTPException(400, f"Hive code {body.code.upper()} already exists.")
    hv = Hive(code=body.code.upper(), beekeeper_id=body.beekeeper_id, location=body.location,
              lat=body.lat, lon=body.lon)
    db.add(hv); db.commit()
    add_block(db, "HIVE_REGISTERED", {"code": hv.code, "location": body.location})
    return {"id": hv.id, "code": hv.code, "location": hv.location, "status": "NO_DATA"}

# --- batches ---
@app.get("/api/batches")
def list_batches(db=Depends(get_db)):
    out = []
    for b in db.query(Batch).order_by(Batch.id.desc()).all():
        bk = db.get(Beekeeper, b.beekeeper_id); hv = db.get(Hive, b.hive_id)
        out.append({"code": b.code, "hive": hv.code, "beekeeper": bk.farm, "qty": b.qty,
                    "quality_status": b.quality_status, "owner": b.owner,
                    "block_index": b.block_index,
                    "block_hash": (b.block_hash[:14] + "…") if b.block_hash else None,
                    "collection_date": b.collection_date.isoformat() if b.collection_date else None})
    return out

@app.post("/api/batches", status_code=201)
def create_batch(body: BatchIn, request: Request, db=Depends(get_db), u=Depends(auth)):
    bk = db.get(Beekeeper, body.beekeeper_id)
    hv = db.get(Hive, body.hive_id)
    if not bk: raise HTTPException(404, "Beekeeper not found.")
    if not hv: raise HTTPException(404, "Hive not found.")
    if hv.beekeeper_id != bk.id:
        raise HTTPException(400, f"Hive {hv.code} does not belong to {bk.farm}.")
    if body.quantity <= 0: raise HTTPException(400, "Quantity must be greater than zero.")
    n = db.query(Batch).count() + 1
    while db.query(Batch).filter(Batch.code == f"HC{n:03d}").first(): n += 1
    code = f"HC{n:03d}"
    coll = (datetime.fromisoformat(body.collection_date) if body.collection_date else utcnow())
    if coll.tzinfo is None: coll = coll.replace(tzinfo=timezone.utc)
    url, data = qr_data_url(request, code)
    b = Batch(code=code, beekeeper_id=bk.id, hive_id=hv.id, collection_date=coll,
              qty=body.quantity, owner=bk.farm, qr=url)
    db.add(b); db.flush()
    oh = Ownership(batch_id=b.id, prev="CREATED", new=bk.farm, ts=coll)
    db.add(oh); db.commit()
    blk = add_block(db, "BATCH_CREATED", {"code": code, "beekeeper": bk.code, "hive": hv.code,
                                          "quantity_kg": body.quantity,
                                          "collection_date": coll.isoformat(), "owner": bk.farm})
    b.block_index, b.block_hash = blk.id, blk.hash
    oh.block_index, oh.block_hash = blk.id, blk.hash
    db.commit()
    return {"code": b.code, "qty": b.qty, "owner": b.owner, "quality_status": b.quality_status,
            "block_index": b.block_index, "block_hash": b.block_hash,
            "qr_url": url, "message": f"Batch {code} created and written to ledger block #{blk.id}."}

@app.get("/api/batches/{code}/qr")
def batch_qr(code: str, request: Request, db=Depends(get_db)):
    b = get_batch(db, code)
    url, data = qr_data_url(request, b.code)
    return {"code": b.code, "url": url, "data_url": data}

@app.get("/api/batches/{code}")
def batch_detail(code: str, db=Depends(get_db)):
    b = get_batch(db, code)
    bk = db.get(Beekeeper, b.beekeeper_id); hv = db.get(Hive, b.hive_id)
    procs = db.query(Processing).filter(Processing.batch_id == b.id).order_by(Processing.ts).all()
    quals = db.query(Quality).filter(Quality.batch_id == b.id).order_by(Quality.ts).all()
    owns = db.query(Ownership).filter(Ownership.batch_id == b.id).order_by(Ownership.ts).all()
    return {"code": b.code, "beekeeper": bk.farm, "beekeeper_code": bk.code,
            "hive": hv.code, "hive_location": hv.location, "qty": b.qty,
            "collection_date": b.collection_date.isoformat() if b.collection_date else None,
            "quality_status": b.quality_status, "owner": b.owner,
            "block_index": b.block_index,
            "block_hash": b.block_hash,
            "processing": [{"ptype": p.ptype, "operator": p.operator, "location": p.location,
                            "ts": p.ts.isoformat() if p.ts else None,
                            "block_index": p.block_index} for p in procs],
            "quality": [{"moisture": q.moisture, "purity": q.purity, "grade": q.grade,
                         "status": q.status, "lab": q.lab,
                         "ts": q.ts.isoformat() if q.ts else None,
                         "block_index": q.block_index} for q in quals],
            "ownership": [{"prev": o.prev, "new": o.new,
                           "ts": o.ts.isoformat() if o.ts else None,
                           "block_index": o.block_index} for o in owns],
            "trace": build_trace(db, b)}

@app.post("/api/batches/{code}/quality", status_code=201)
def add_quality(code: str, body: QualityIn, db=Depends(get_db), u=Depends(auth)):
    b = get_batch(db, code)
    if not (0 <= body.moisture <= 100 and 0 <= body.purity <= 100):
        raise HTTPException(400, "Moisture and purity must be percentages (0–100).")
    q = Quality(batch_id=b.id, moisture=body.moisture, purity=body.purity, grade=body.grade,
                status=body.status, lab=body.lab, ts=utcnow())
    db.add(q); b.quality_status = body.status; db.commit()
    report_hash = hashlib.sha256(json.dumps({"code": b.code, "m": body.moisture,
        "p": body.purity, "g": body.grade, "s": body.status}, sort_keys=True).encode()).hexdigest()
    q.report_hash = report_hash
    blk = add_block(db, "QUALITY_UPDATED", {"code": b.code, "status": body.status,
                                            "grade": body.grade, "report_hash": report_hash})
    q.block_index, q.block_hash = blk.id, blk.hash
    db.commit()
    return {"code": b.code, "status": body.status, "grade": body.grade,
            "moisture": body.moisture, "purity": body.purity,
            "report_hash": report_hash, "block_index": blk.id}

@app.post("/api/batches/{code}/processing", status_code=201)
def add_processing(code: str, body: ProcessingIn, db=Depends(get_db), u=Depends(auth)):
    b = get_batch(db, code)
    allowed = {"Collection", "Filtering", "Processing", "Packaging", "Storage", "Distribution"}
    if body.ptype not in allowed:
        raise HTTPException(400, f"Invalid stage. Allowed: {', '.join(sorted(allowed))}")
    p = Processing(batch_id=b.id, ptype=body.ptype, operator=body.operator,
                   location=body.location, notes=body.notes, ts=utcnow())
    db.add(p); db.commit()
    blk = add_block(db, "PROCESSING_ADDED", {"code": b.code, "stage": body.ptype,
                                             "operator": body.operator})
    p.block_index, p.block_hash = blk.id, blk.hash
    db.commit()
    return {"code": b.code, "stage": body.ptype, "block_index": blk.id}

@app.post("/api/batches/{code}/transfer", status_code=201)
def transfer(code: str, body: TransferIn, db=Depends(get_db), u=Depends(auth)):
    b = get_batch(db, code)
    if not body.new_owner.strip():
        raise HTTPException(400, "New owner name is required.")
    prev = b.owner
    oh = Ownership(batch_id=b.id, prev=prev, new=body.new_owner.strip(), ts=utcnow())
    db.add(oh); b.owner = body.new_owner.strip(); db.commit()
    blk = add_block(db, "OWNERSHIP_TRANSFERRED", {"code": b.code, "from": prev, "to": b.owner})
    oh.block_index, oh.block_hash = blk.id, blk.hash
    db.commit()
    return {"code": b.code, "from": prev, "to": b.owner, "block_index": blk.id}

# --- public consumer verification (hardened: always returns JSON, never crashes) ---
@app.get("/api/verify/{code}")
def public_verify(code: str, db=Depends(get_db)):
    code = (code or "").strip().upper()
    if not code:
        raise HTTPException(400, "No batch code supplied.")
    try:
        b = db.query(Batch).filter(Batch.code == code).first()
    except Exception as e:
        raise HTTPException(503, f"Database unavailable: {e.__class__.__name__}: {e}")
    if not b:
        raise HTTPException(404, f"Batch '{code}' does not exist in Honey Chain. "
                                 "Check the code printed under the jar lid.")
    try:
        bk = db.get(Beekeeper, b.beekeeper_id)
        hv = db.get(Hive, b.hive_id)
        q = (db.query(Quality).filter(Quality.batch_id == b.id)
               .order_by(Quality.ts.desc()).first())
        chain_ok = verify_chain(db)["valid"]
        return {
            "verified": bool(b.block_hash) and chain_ok,
            "batch": {"code": b.code, "qty": b.qty, "owner": b.owner,
                      "collection_date": b.collection_date.isoformat() if b.collection_date else None,
                      "block_index": b.block_index, "block_hash": b.block_hash},
            "beekeeper": {"code": bk.code if bk else "?", "farm": bk.farm if bk else "?"},
            "hive": {"code": hv.code if hv else "?", "location": hv.location if hv else "?"},
            "quality": ({"grade": q.grade, "status": q.status, "lab": q.lab,
                         "moisture": q.moisture, "purity": q.purity} if q else None),
            "trace": build_trace(db, b),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Verification failed: {e.__class__.__name__}: {e}")

# --- IoT ---
@app.post("/api/iot/readings", status_code=201)
def add_reading(body: ReadingIn, db=Depends(get_db)):
    hv = get_hive(db, body.hive_id)
    if not (-20 <= body.temperature <= 60): raise HTTPException(400, "Temperature out of sensor range (-20…60 °C).")
    if not (0 <= body.humidity <= 100): raise HTTPException(400, "Humidity must be 0–100%.")
    if not (0 <= body.weight <= 500): raise HTTPException(400, "Weight out of range (0–500 kg).")
    r = Reading(hive_id=hv.id, temp=body.temperature, hum=body.humidity, weight=body.weight,
                lat=body.latitude or hv.lat, lon=body.longitude or hv.lon, ts=utcnow())
    db.add(r); db.commit()
    return {"status": "ok", "hive": hv.code, "quick_status": quick_status(r.temp, r.hum, r.weight)}

@app.get("/api/iot/history/{code}")
def hive_history(code: str, limit: int = 48, db=Depends(get_db)):
    hv = get_hive(db, code)
    rows = (db.query(Reading).filter(Reading.hive_id == hv.id)
              .order_by(Reading.ts.desc()).limit(min(limit, 300)).all())[::-1]
    return [{"ts": r.ts.isoformat(), "temp": r.temp, "humidity": r.hum, "weight": r.weight}
            for r in rows]

@app.get("/api/iot/summary")
def iot_summary(db=Depends(get_db)):
    return list_hives(db)

# --- AI ---
@app.get("/api/ai/hive/{code}/analysis")
def ai_analysis(code: str, db=Depends(get_db)):
    return analyze_hive(db, get_hive(db, code))

@app.get("/api/ai/hive/{code}/prediction")
def ai_prediction(code: str, db=Depends(get_db)):
    return predict_production(db, get_hive(db, code))

@app.get("/api/ai/alerts")
def ai_alerts(db=Depends(get_db)):
    out = []
    for hv in db.query(Hive).all():
        a = analyze_hive(db, hv)
        if a["status"] in ("WARNING", "CRITICAL"):
            out.append({"hive": hv.code, "status": a["status"],
                        "messages": [x["msg"] for x in a["anomalies"]],
                        "recommendation": " ".join(a["recommendations"][:2])})
    return out

# --- ledger explorer + tamper demo ---
@app.get("/api/ledger")
def ledger_list(db=Depends(get_db)):
    blocks = db.query(Block).order_by(Block.id.desc()).limit(100).all()
    return [{"index": b.id, "event": b.event, "payload": json.loads(b.payload or "{}"),
             "hash": b.hash, "prev_hash": b.prev_hash,
             "ts": b.ts.isoformat() if b.ts else None} for b in blocks]

@app.get("/api/ledger/verify")
def ledger_verify(db=Depends(get_db)):
    return verify_chain(db)

@app.post("/api/ledger/tamper")
def ledger_tamper(db=Depends(get_db)):
    n = db.query(Block).count()
    if n < 3: raise HTTPException(400, "Need at least 3 blocks — seed demo data first.")
    target = db.query(Block).order_by(Block.id).all()[n // 2]
    target.payload = target.payload + " TAMPERED"
    db.commit()
    return {"tampered_block": target.id,
            "note": "A historical block was modified. Verification will now FAIL — refresh the ledger check."}

@app.post("/api/ledger/reset")
def ledger_reset(db=Depends(get_db)):
    from sqlalchemy import text as _t
    for t in reversed(Base.metadata.sorted_tables):
        db.execute(_t(f"DELETE FROM {t.name}"))
    db.commit()
    seed_demo(db)
    return {"status": "reset", "note": "Database cleared and reseeded with an intact ledger."}

@app.post("/api/demo/seed")
def demo_seed(db=Depends(get_db)):
    if db.query(User).first():
        return {"status": "already_seeded"}
    seed_demo(db)
    return {"status": "seeded"}

# ---------------- seed data ----------------
def seed_demo(db):
    random.seed(42)
    now = utcnow()
    add_block(db, "GENESIS", {"network": "honey-chain-lite", "note": "local tamper-evident ledger"})
    users = [("Demo Admin", "admin@honeychain.in", "ADMIN"),
             ("Ramesh Kumar", "ramesh@honeychain.in", "BEEKEEPER"),
             ("Sunita Devi", "sunita@honeychain.in", "BEEKEEPER"),
             ("Pema Wangchuk", "pema@honeychain.in", "BEEKEEPER"),
             ("Brahmaputra Labs", "lab@honeychain.in", "LAB")]
    for name, email, role in users:
        db.add(User(name=name, email=email, pw=hash_pw("demo1234"), role=role))
    db.flush()
    keepers = [("BK001", "Kumar Honey Farm", "Kaziranga, Assam"),
               ("BK002", "Brahmaputra Apiaries", "Majuli, Assam"),
               ("BK003", "Khasi Hills Bee Collective", "Shillong, Meghalaya")]
    for i, (code, farm, loc) in enumerate(keepers):
        db.add(Beekeeper(code=code, farm=farm, location=loc, user_id=2 + i))
        add_block(db, "BEEKEEPER_REGISTERED", {"code": code, "farm": farm, "location": loc})
    db.flush()
    hives = [("HIVE001", 1, "Kaziranga apiary — row A", 26.5775, 93.1711),
             ("HIVE002", 1, "Kaziranga apiary — row B", 26.5781, 93.1720),
             ("HIVE003", 2, "Majuli riverbank site", 26.9520, 94.1660),
             ("HIVE004", 2, "Majuli orchard site", 26.9495, 94.1701),
             ("HIVE005", 3, "Khasi pine forest site", 25.5788, 91.8933)]
    for code, bidx, loc, lat, lon in hives:
        db.add(Hive(code=code, beekeeper_id=bidx, location=loc, lat=lat, lon=lon))
        add_block(db, "HIVE_REGISTERED", {"code": code, "location": loc})
    db.flush()
    # 24h of hourly readings per hive; HIVE004 gets a hot spell (demo AI alert)
    for hi, hv in enumerate(db.query(Hive).order_by(Hive.id).all()):
        base_t, base_w = 31.0 + hi * 0.3, 24.0 + hi * 0.4
        for hr in range(24, 0, -1):
            t = base_t + 1.6 * ((24 - hr) / 24) + random.uniform(-0.6, 0.6)
            h = 62 + random.uniform(-5, 5)
            w = base_w + 0.02 * (24 - hr) + random.uniform(-0.1, 0.1)
            if hv.code == "HIVE004" and hr <= 5:
                t = 36.5 + random.uniform(0, 1.6)
            db.add(Reading(hive_id=hv.id, temp=round(t, 1), hum=round(h, 1), weight=round(w, 1),
                           lat=hv.lat, lon=hv.lon, ts=now - timedelta(hours=hr)))
    # synthetic harvest history for the prediction model
    for hi, hv in enumerate(db.query(Hive).order_by(Hive.id).all()):
        for months_ago in (2, 4, 6, 8, 10, 12):
            dt = now - timedelta(days=30 * months_ago)
            season = {12:1,1:1,2:1,3:2,4:2,5:2,6:3,7:3,8:3,9:4,10:4,11:4}[dt.month]
            t, h, w = 30 + hi*0.3, 62 + hi, 24 + hi*0.4
            kg = max(8 + 0.45*(w-15) + (1.5 if season == 2 else 0) - 0.18*max(0.0, t-33)
                     + random.uniform(-1.0, 1.0), 1.0)
            db.add(ProductionRecord(hive_id=hv.id, dt=dt, season=season, avg_t=t,
                                    avg_h=h, avg_w=w, kg=round(kg, 1)))
    db.commit()
    # batches with real journey events
    journey = [
        ("HC001", 1, 1, 25, 6,
         [("Filtering", "Ramesh Kumar", "Kaziranga, Assam"),
          ("Packaging", "Ramesh Kumar", "Kaziranga, Assam")],
         [("Kaziranga Collection Center", 5), ("Brahmaputra Honey Processing Co.", 4),
          ("Guwahati Distribution Hub", 2)],
         (16.2, 94.5, "A", "APPROVED")),
        ("HC002", 1, 2, 18, 5, [("Filtering", "Ramesh Kumar", "Kaziranga, Assam")],
         [("Kaziranga Collection Center", 4)], (17.8, 92.1, "A", "APPROVED")),
        ("HC003", 2, 3, 30, 8,
         [("Filtering", "Sunita Devi", "Majuli, Assam"),
          ("Processing", "Brahmaputra Honey Processing Co.", "Guwahati, Assam"),
          ("Packaging", "Brahmaputra Honey Processing Co.", "Guwahati, Assam")],
         [("Kaziranga Collection Center", 7), ("Brahmaputra Honey Processing Co.", 6),
          ("Guwahati Distribution Hub", 3), ("BeePure Retail — Guwahati", 1)],
         (19.9, 88.4, "B", "APPROVED")),
        ("HC004", 2, 4, 12, 10, [], [], None),          # awaiting quality test
        ("HC005", 3, 5, 22, 4, [("Filtering", "Pema Wangchuk", "Shillong, Meghalaya")],
         [], (24.6, 71.0, "C", "REJECTED")),            # high moisture -> rejected
    ]
    for code, bidx, hidx, qty, days_ago, procs, transfers, quality in journey:
        bk = db.query(Beekeeper).filter(Beekeeper.id == bidx).first()
        hv = db.query(Hive).filter(Hive.id == hidx).first()
        coll = now - timedelta(days=days_ago)
        b = Batch(code=code, beekeeper_id=bk.id, hive_id=hv.id, collection_date=coll,
                  qty=float(qty), owner=bk.farm, qr=f"/verify/{code}")
        db.add(b); db.flush()
        oh = Ownership(batch_id=b.id, prev="CREATED", new=bk.farm, ts=coll)
        db.add(oh); db.commit()
        blk = add_block(db, "BATCH_CREATED", {"code": code, "beekeeper": bk.code,
                                              "hive": hv.code, "quantity_kg": qty,
                                              "collection_date": coll.isoformat(),
                                              "owner": bk.farm})
        b.block_index, b.block_hash = blk.id, blk.hash
        oh.block_index, oh.block_hash = blk.id, blk.hash
        for ptype, operator, loc in procs:
            p = Processing(batch_id=b.id, ptype=ptype, operator=operator, location=loc,
                           notes=f"Standard {ptype.lower()} step for {code}.",
                           ts=coll + timedelta(hours=6))
            db.add(p); db.commit()
            pb = add_block(db, "PROCESSING_ADDED", {"code": code, "stage": ptype, "operator": operator})
            p.block_index, p.block_hash = pb.id, pb.hash
        if quality:
            m, p_, g, s = quality
            q = Quality(batch_id=b.id, moisture=m, purity=p_, grade=g, status=s,
                        lab="Brahmaputra Honey Testing Lab", ts=coll + timedelta(days=1))
            db.add(q); b.quality_status = s; db.commit()
            rh = hashlib.sha256(json.dumps({"code": code, "m": m, "p": p_, "g": g, "s": s},
                                           sort_keys=True).encode()).hexdigest()
            q.report_hash = rh
            qb = add_block(db, "QUALITY_UPDATED", {"code": code, "status": s, "grade": g,
                                                   "report_hash": rh})
            q.block_index, q.block_hash = qb.id, qb.hash
        for j, (owner, td) in enumerate(transfers):
            prev = bk.farm if j == 0 else transfers[j-1][0]
            o = Ownership(batch_id=b.id, prev=prev, new=owner, ts=coll + timedelta(days=td))
            db.add(o)
            if j == len(transfers) - 1: b.owner = owner
            db.commit()
            tb = add_block(db, "OWNERSHIP_TRANSFERRED", {"code": code, "from": prev, "to": owner})
            o.block_index, o.block_hash = tb.id, tb.hash
        db.commit()

# ---------------- built-in IoT simulator thread ----------------
_sim_stop = threading.Event()
HIVE_SIM = [("HIVE001", 31.0, 61.0, 24.5), ("HIVE002", 31.5, 63.0, 25.2),
            ("HIVE003", 30.5, 65.0, 24.0), ("HIVE004", 32.0, 60.0, 26.0),
            ("HIVE005", 29.5, 68.0, 23.5)]

def _sim_loop(hot: bool):
    state = {c: {"t": t, "h": h, "w": w} for c, t, h, w in HIVE_SIM}
    while not _sim_stop.is_set():
        db = Session()
        try:
            for code, bt, bh, bw in HIVE_SIM:
                s = state[code]
                s["t"] = min(max(s["t"] + random.uniform(-0.4, 0.4), bt - 4), bt + 7)
                s["h"] = min(max(s["h"] + random.uniform(-1, 1), 35), 95)
                s["w"] = max(s["w"] + random.uniform(-0.05, 0.12), 0)
                if hot and code == "HIVE004":
                    s["t"] = max(s["t"], 36.0 + random.uniform(0, 1.5))
                hv = db.query(Hive).filter(Hive.code == code).first()
                if hv:
                    db.add(Reading(hive_id=hv.id, temp=round(s["t"], 1), hum=round(s["h"], 1),
                                   weight=round(s["w"], 1), lat=hv.lat, lon=hv.lon, ts=utcnow()))
            db.commit()
        except Exception:
            pass
        finally:
            db.close()
        _sim_stop.wait(5)

# ---------------- pages ----------------
@app.get("/", response_class=HTMLResponse)
def index():
    return open("static/index.html", encoding="utf-8").read()

@app.get("/verify", response_class=HTMLResponse)
@app.get("/verify/{code}", response_class=HTMLResponse)
def verify_page(code: str = ""):
    return open("static/verify.html", encoding="utf-8").read()

# ---------------- entrypoint ----------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Honey Chain Lite")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--hot", action="store_true", help="simulator drives HIVE004 hot (AI alert demo)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    db = Session()
    if args.seed and not db.query(User).first():
        print("🌱 Seeding demo data…")
        seed_demo(db)
    db.close()
    if args.simulate:
        threading.Thread(target=_sim_loop, args=(args.hot,), daemon=True,
                         name="iot-simulator").start()
        print("📡 Built-in IoT simulator started (5 hives, every 5 s)")
    import uvicorn
    print(f"🍯 Honey Chain Lite → http://{'localhost' if args.host=='127.0.0.1' else args.host}:{args.port}")
    print("   Login: admin@honeychain.in / demo1234")
    uvicorn.run(app, host=args.host, port=args.port)