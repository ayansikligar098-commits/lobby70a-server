"""
FF_70A Top Up server (FastAPI + SQLite).

Flow (manual verification — there is no payment gateway yet):
  1. Player taps BUY/JOIN in the game, scans the owner's UPI QR,
     pays in any UPI app, enters the 12-digit UTR and submits.
  2. The claim is stored here as PENDING. NO diamonds are given yet.
  3. The owner opens /admin, checks the payment in GPay/bank
     (match the UTR + amount), and taps APPROVE or REJECT.
  4. The player's app polls /api/claims/mine — an APPROVED claim
     releases the diamonds into the mailbox (packs) or activates
     the membership (7-day daily rewards).

Run locally:  uvicorn main:app --reload --port 8000
Deploy: Render free web service (render.yaml in this folder).
NOTE: Render Free has no persistent disk — claims survive
spin-downs but are wiped on redeploy. Move to Postgres later
if claims must never be lost.
"""
import os
import sqlite3
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

ADMIN_KEY = os.environ.get("ADMIN_KEY", "70a-7K2m-9Qx4")
DB_PATH = os.environ.get("DB_PATH", "claims.db")

app = FastAPI(title="FF_70A Top Up Server")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            rupees INTEGER NOT NULL,
            diamonds INTEGER NOT NULL,
            utr TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL)"""
    )
    conn.commit()
    return conn


class ClaimIn(BaseModel):
    uid: str
    name: str
    kind: str  # "pack" or "membership"
    rupees: int
    diamonds: int
    utr: str


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/claims")
def create_claim(c: ClaimIn):
    if c.kind not in ("pack", "membership"):
        raise HTTPException(status_code=400, detail="bad kind")
    utr = c.utr.strip()
    if len(utr) < 6:
        raise HTTPException(status_code=400, detail="UTR chahiye")
    conn = db()
    cur = conn.execute(
        "INSERT INTO claims (uid, name, kind, rupees, diamonds, utr, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (c.uid, c.name, c.kind, c.rupees, c.diamonds, utr, int(time.time())),
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return {"ok": True, "id": cid, "status": "pending"}


@app.get("/api/claims/mine")
def my_claims(uid: str):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM claims WHERE uid=? ORDER BY id DESC LIMIT 50", (uid,)
    ).fetchall()
    conn.close()
    return {"claims": [dict(r) for r in rows]}


def _require_admin(key: str):
    if key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="wrong key")


@app.get("/api/claims/pending")
def pending(key: str = Query("")):
    _require_admin(key)
    conn = db()
    rows = conn.execute(
        "SELECT * FROM claims WHERE status='pending' ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return {"claims": [dict(r) for r in rows]}


@app.post("/api/claims/{cid}/approve")
def approve(cid: int, key: str = Query("")):
    _require_admin(key)
    conn = db()
    conn.execute("UPDATE claims SET status='approved' WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/claims/{cid}/reject")
def reject(cid: int, key: str = Query("")):
    _require_admin(key)
    conn = db()
    conn.execute("UPDATE claims SET status='rejected' WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return {"ok": True}


ADMIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FF_70A — Top Up Approvals</title>
<style>
body{background:#10101a;color:#fff;font-family:sans-serif;margin:0;padding:16px}
h1{color:#ffc93c;font-size:22px}
input,button{font-size:16px;padding:10px 14px;border-radius:10px;border:1px solid #c9a227}
input{background:#1e1e30;color:#fff;width:220px}
button{background:#ffc93c;color:#4a3200;font-weight:bold;border:none;cursor:pointer;margin-left:6px}
.card{background:#1e1e30;border:1px solid #c9a227;border-radius:12px;padding:14px;margin:12px 0}
.card b{color:#ffc93c}
.small{color:#aaa;font-size:13px}
</style></head><body>
<h1>FF_70A — Top Up Approvals</h1>
<p class="small">Pehle GPay/bank me UTR + amount check karo, phir APPROVE dabao. Approve karte hi player ke mailbox me diamonds aa jayenge.</p>
<label>Admin key: <input id="key" type="password" placeholder="admin key"></label>
<button onclick="load()">Load pending</button>
<div id="list"></div>
<script>
const keyInput = document.getElementById('key');
keyInput.value = localStorage.getItem('adminKey') || '';
async function load(){
  const key = keyInput.value;
  localStorage.setItem('adminKey', key);
  const r = await fetch('/api/claims/pending?key=' + encodeURIComponent(key));
  if(!r.ok){ document.getElementById('list').innerHTML = '<p>Wrong key ya server error.</p>'; return; }
  const data = await r.json();
  const list = document.getElementById('list');
  if(!data.claims.length){ list.innerHTML = '<p>No pending claims.</p>'; return; }
  list.innerHTML = data.claims.map(c =>
    `<div class="card"><b>${c.name}</b> (${c.uid})<br>
     ${c.kind === 'membership' ? 'Membership' : 'Pack'} — Rs ${c.rupees} — ◆ ${c.diamonds}<br>
     UTR: <b>${c.utr}</b><br>
     <span class="small">${new Date(c.created_at*1000).toLocaleString()}</span><br><br>
     <button onclick="decide(${c.id},'approve')">APPROVE</button>
     <button onclick="decide(${c.id},'reject')">REJECT</button></div>`).join('');
}
async function decide(id, action){
  const key = keyInput.value;
  const r = await fetch(`/api/claims/${id}/${action}?key=` + encodeURIComponent(key), {method:'POST'});
  if(r.ok) load(); else alert('Failed');
}
load();
</script></body></html>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return ADMIN_HTML
