"""
FF_70A Redeem Code server (FastAPI + SQLite).

Flow (no payments — redeem codes only):
  1. The owner creates a redeem code on /admin (code + diamonds).
     A code works exactly ONCE, for one player.
  2. A player enters the code in the game's Redeem screen.
  3. POST /api/redeem validates the code: unknown code -> 404,
     already-used code -> 409. A valid unused code is marked used
     (by that player's UID) and the diamonds are returned — the app
     adds them to the balance straight away.

Run locally:  uvicorn main:app --reload --port 8000
Deploy: Render free web service (render.yaml in this folder).
NOTE: Render Free has no persistent disk — codes survive
spin-downs but are wiped on redeploy. Move to Postgres later
if codes must never be lost.
"""
import os
import sqlite3
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

ADMIN_KEY = os.environ.get("ADMIN_KEY", "70a-7K2m-9Qx4")
DB_PATH = os.environ.get("DB_PATH", "redeem.db")

app = FastAPI(title="FF_70A Redeem Server")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS redeem_codes (
            code TEXT PRIMARY KEY,
            diamonds INTEGER NOT NULL,
            used_by TEXT,
            used_at INTEGER,
            created_at INTEGER NOT NULL)"""
    )
    conn.commit()
    return conn


def norm_code(code: str) -> str:
    return code.strip().upper()


class RedeemIn(BaseModel):
    uid: str
    code: str


class CodeIn(BaseModel):
    code: str
    diamonds: int


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/redeem")
def redeem(r: RedeemIn):
    code = norm_code(r.code)
    uid = r.uid.strip()
    if len(code) < 4:
        raise HTTPException(status_code=400, detail="Galat code")
    if not uid:
        raise HTTPException(status_code=400, detail="UID chahiye")
    conn = db()
    row = conn.execute(
        "SELECT * FROM redeem_codes WHERE code=?", (code,)
    ).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Galat code")
    if row["used_by"]:
        conn.close()
        raise HTTPException(status_code=409, detail="Code pehle se use ho chuka hai")
    # Atomic claim: only one player can win the race for this code.
    cur = conn.execute(
        "UPDATE redeem_codes SET used_by=?, used_at=? "
        "WHERE code=? AND used_by IS NULL",
        (uid, int(time.time()), code),
    )
    conn.commit()
    conn.close()
    if cur.rowcount != 1:
        raise HTTPException(status_code=409, detail="Code pehle se use ho chuka hai")
    return {"ok": True, "diamonds": row["diamonds"]}


def _require_admin(key: str):
    if key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="wrong key")


@app.get("/api/codes")
def list_codes(key: str = Query("")):
    _require_admin(key)
    conn = db()
    rows = conn.execute(
        "SELECT * FROM redeem_codes ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return {"codes": [dict(r) for r in rows]}


@app.post("/api/codes")
def create_code(c: CodeIn, key: str = Query("")):
    _require_admin(key)
    code = norm_code(c.code)
    if len(code) < 4 or len(code) > 32 or c.diamonds <= 0 or c.diamonds > 100000:
        raise HTTPException(
            status_code=400,
            detail="Code 4-32 characters, diamonds 1-100000",
        )
    conn = db()
    try:
        conn.execute(
            "INSERT INTO redeem_codes (code, diamonds, used_by, used_at, created_at)"
            " VALUES (?, ?, NULL, NULL, ?)",
            (code, c.diamonds, int(time.time())),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Ye code pehle se hai")
    conn.close()
    return {"ok": True, "code": code, "diamonds": c.diamonds}


@app.post("/api/codes/{code}/delete")
def delete_code(code: str, key: str = Query("")):
    _require_admin(key)
    conn = db()
    conn.execute("DELETE FROM redeem_codes WHERE code=?", (norm_code(code),))
    conn.commit()
    conn.close()
    return {"ok": True}


ADMIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FF_70A — Redeem Codes</title>
<style>
body{background:#10101a;color:#fff;font-family:sans-serif;margin:0;padding:16px}
h1{color:#ffc93c;font-size:22px}
h2{color:#ffc93c;font-size:18px;margin-top:20px}
input,button{font-size:16px;padding:10px 14px;border-radius:10px;border:1px solid #c9a227}
input{background:#1e1e30;color:#fff;width:200px}
button{background:#ffc93c;color:#4a3200;font-weight:bold;border:none;cursor:pointer;margin-left:6px}
.card{background:#1e1e30;border:1px solid #c9a227;border-radius:12px;padding:14px;margin:12px 0}
.card b{color:#ffc93c}
.small{color:#aaa;font-size:13px}
.used{color:#ff7777}
.free{color:#77ff99}
</style></head><body>
<h1>FF_70A — Redeem Codes</h1>
<p class="small">Code banao aur player ko do — player game me code daalega aur diamonds mil jayenge. Ek code ek baar hi chalega.</p>
<label>Admin key: <input id="key" type="password" placeholder="admin key"></label>
<button onclick="load()">Load codes</button>
<h2>Naya code banao</h2>
<input id="newCode" placeholder="CODE likho, jaise FF70A-100">
<input id="newDiamonds" type="number" placeholder="Diamonds" style="width:120px">
<button onclick="createCode()">CREATE</button>
<div id="list"></div>
<script>
const keyInput = document.getElementById('key');
keyInput.value = localStorage.getItem('adminKey') || '';
async function load(){
  const key = keyInput.value;
  localStorage.setItem('adminKey', key);
  const r = await fetch('/api/codes?key=' + encodeURIComponent(key));
  if(!r.ok){ document.getElementById('list').innerHTML = '<p>Wrong key ya server error.</p>'; return; }
  const data = await r.json();
  const list = document.getElementById('list');
  if(!data.codes.length){ list.innerHTML = '<p>Abhi koi code nahi hai.</p>'; return; }
  list.innerHTML = data.codes.map(c =>
    `<div class="card"><b>${c.code}</b> — ◆ ${c.diamonds}<br>
     ${c.used_by ? `<span class="used">USED by ${c.used_by}</span>` : '<span class="free">Unused</span>'}<br>
     <span class="small">${new Date(c.created_at*1000).toLocaleString()}</span><br><br>
     <button onclick="delCode('${c.code}')">DELETE</button></div>`).join('');
}
async function createCode(){
  const key = keyInput.value;
  const code = document.getElementById('newCode').value;
  const diamonds = parseInt(document.getElementById('newDiamonds').value, 10);
  const r = await fetch('/api/codes?key=' + encodeURIComponent(key), {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({code, diamonds})});
  if(r.ok){ document.getElementById('newCode').value=''; document.getElementById('newDiamonds').value=''; load(); }
  else { const d = await r.json().catch(()=>({})); alert(d.detail || 'Failed'); }
}
async function delCode(code){
  const key = keyInput.value;
  if(!confirm('Delete ' + code + '?')) return;
  const r = await fetch('/api/codes/' + encodeURIComponent(code) + '/delete?key=' + encodeURIComponent(key), {method:'POST'});
  if(r.ok) load(); else alert('Failed');
}
load();
</script></body></html>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return ADMIN_HTML
