#!/usr/bin/env python3
"""agent-tickets: a small ticket tracker for AI agents and the human who
reviews their work. Python standard library only: a threaded HTTP server
and an SQLite database.

  python3 agent_tickets.py                     # serve on 127.0.0.1:43917, data in ./data
  python3 agent_tickets.py --port 8080 --data /srv/tickets
  python3 agent_tickets.py --keys              # print the access keys and exit

Tickets move through five statuses:
  backlog      an idea, not cleared for work yet
  ready        cleared: agents may pick it up
  in_progress  an agent is on it (partial work counts)
  review       the agent is done or needs a decision: the human's turn
  done         approved by the human

Two keys (made on first run, in <data>/keys.json): the human key opens the
web pages and can do anything; the agent key is for the JSON API and can
only move tickets between ready, in_progress and review, and can't change
backlog or done tickets (comments are always allowed). API docs: GET /api.
"""
import argparse
import hmac
import html
import json
import mimetypes
import os
import random
import re
import secrets
import sqlite3
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

STATUSES = ["backlog", "ready", "in_progress", "review", "done"]
LABELS = {"backlog": "Backlog", "ready": "Ready", "in_progress": "In progress", "review": "Needs review", "done": "Done"}
AGENT_TARGETS = {"ready", "in_progress", "review"}  # statuses an agent may set
AGENT_LOCKED = {"backlog", "done"}                  # tickets an agent may only comment on
PRIORITIES = {1: "High", 2: "Normal", 3: "Low"}
COOKIE = "at_key"
CHUNK = 1 << 16

# Ticket ids: easy to say out loud. Consonant-vowel pairs ("KAMI", "ROZU"),
# from letters that don't sound alike; five letters if four get crowded.
ID_CONSONANTS = "BDFGHKLMNPRSTVZ"
ID_VOWELS = "AEIOU"


def new_id(taken):
    for length in (4, 4, 4, 5):
        for _ in range(200):
            tid = "".join(random.choice(ID_CONSONANTS if i % 2 == 0 else ID_VOWELS) for i in range(length))
            if not taken(tid):
                return tid
    raise RuntimeError("no free ticket id")


# --------------------------------------------------------------------------- storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 2, checklist_path TEXT,
  created_by TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL REFERENCES tickets(id),
  author TEXT NOT NULL, role TEXT NOT NULL, body TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL REFERENCES tickets(id),
  comment_id INTEGER REFERENCES comments(id), filename TEXT NOT NULL, path TEXT NOT NULL,
  mime TEXT NOT NULL, size INTEGER NOT NULL, author TEXT NOT NULL, role TEXT NOT NULL,
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL REFERENCES tickets(id),
  actor TEXT NOT NULL, role TEXT NOT NULL, field TEXT NOT NULL, old TEXT, new TEXT, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS tickets_status ON tickets(status, priority, created_at);
CREATE INDEX IF NOT EXISTS comments_ticket ON comments(ticket_id, created_at);
CREATE INDEX IF NOT EXISTS attachments_ticket ON attachments(ticket_id);
CREATE INDEX IF NOT EXISTS events_ticket ON events(ticket_id, at);
"""


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


class Store:
    """The database and the uploaded files. One SQLite connection per thread;
    writes go through one lock so ids and status changes never race."""

    def __init__(self, data_dir, checklist_roots):
        self.dir = Path(data_dir).resolve()
        self.files = self.dir / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "tickets.db"
        self.roots = [Path(r).resolve() for r in checklist_roots]
        self.local = threading.local()
        self.write_lock = threading.Lock()
        self.db().executescript(SCHEMA)

    def db(self):
        c = getattr(self.local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            self.local.conn = c
        return c

    # --- reads
    def ticket(self, tid):
        row = self.db().execute("SELECT * FROM tickets WHERE id = ?", ((tid or "").upper(),)).fetchone()
        if not row:
            raise ApiError(404, f"no ticket {tid}")
        return dict(row)

    def tickets(self, statuses=None, q=None, limit=500, recent_first=False):
        sql, args = "SELECT t.*, (SELECT COUNT(*) FROM comments c WHERE c.ticket_id = t.id) AS comment_count, " \
                    "(SELECT COUNT(*) FROM attachments a WHERE a.ticket_id = t.id) AS attachment_count FROM tickets t", []
        where = []
        if statuses:
            where.append("status IN (%s)" % ",".join("?" * len(statuses)))
            args += statuses
        if q:
            where.append("(t.id LIKE ? OR t.title LIKE ? OR t.description LIKE ?)")
            args += [f"%{q}%"] * 3
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?" if recent_first else " ORDER BY priority, created_at LIMIT ?"
        args.append(limit)
        out = [dict(r) for r in self.db().execute(sql, args)]
        for t in out:
            last = self.db().execute("SELECT author, role, body, created_at FROM comments WHERE ticket_id = ? "
                                     "ORDER BY created_at DESC, id DESC LIMIT 1", (t["id"],)).fetchone()
            t["last_comment"] = dict(last) if last else None
        return out

    def detail(self, tid):
        t = self.ticket(tid)
        db = self.db()
        t["comments"] = [dict(r) for r in db.execute(
            "SELECT * FROM comments WHERE ticket_id = ? ORDER BY created_at, id", (t["id"],))]
        t["attachments"] = [self.attachment_json(dict(r)) for r in db.execute(
            "SELECT * FROM attachments WHERE ticket_id = ? ORDER BY created_at, id", (t["id"],))]
        t["events"] = [dict(r) for r in db.execute(
            "SELECT * FROM events WHERE ticket_id = ? ORDER BY at, id", (t["id"],))]
        t["checklist"] = self.checklist(t["checklist_path"])
        return t

    def attachment_json(self, a):
        a = {k: v for k, v in a.items() if k != "path"}
        a["url"] = f"/files/{a['id']}/{quote(a['filename'])}"
        return a

    def attachment(self, aid):
        row = self.db().execute("SELECT * FROM attachments WHERE id = ?", (aid,)).fetchone()
        if not row:
            raise ApiError(404, "no such file")
        return dict(row)

    def checklist(self, path):
        """The checklist file an agent pointed the ticket at: its items
        ("- [ ] ..." / "- [x] ...") and other lines, if it's readable and
        under one of the allowed roots."""
        if not path:
            return None
        out = {"path": path, "readable": False, "items": [], "done": 0, "total": 0}
        try:
            p = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            out["error"] = "not a valid path"
            return out
        if not any(p == r or r in p.parents for r in self.roots):
            out["error"] = "outside the allowed checklist folders"
            return out
        try:
            text = p.read_text(errors="replace")[:200_000]
        except OSError as e:
            out["error"] = e.strerror or "can't read it"
            return out
        out["readable"] = True
        for line in text.splitlines():
            m = re.match(r"\s*[-*]\s+\[([ xX])\]\s+(.*)", line)
            if m:
                done = m.group(1).lower() == "x"
                out["items"].append({"done": done, "text": m.group(2)})
                out["total"] += 1
                out["done"] += done
            elif line.strip():
                out["items"].append({"text": line.strip(), "note": True})
        return out

    # --- writes
    def _event(self, db, tid, actor, role, field, old, new, now):
        db.execute("INSERT INTO events (ticket_id, actor, role, field, old, new, at) VALUES (?,?,?,?,?,?,?)",
                   (tid, actor, role, field, None if old is None else str(old), None if new is None else str(new), now))

    def create(self, data, actor, role):
        title = str(data.get("title") or "").strip()
        if not title:
            raise ApiError(400, "a ticket needs a title")
        status = data.get("status") or "backlog"
        if status not in STATUSES:
            raise ApiError(400, f"status must be one of {STATUSES}")
        if role == "agent" and status != "backlog":
            raise ApiError(403, "agents file new tickets in the backlog; the human promotes them")
        priority = int(data.get("priority") or 2)
        if priority not in PRIORITIES:
            raise ApiError(400, "priority is 1 (high), 2 (normal) or 3 (low)")
        now = time.time()
        with self.write_lock:
            db = self.db()
            tid = new_id(lambda x: db.execute("SELECT 1 FROM tickets WHERE id = ?", (x,)).fetchone())
            with db:
                db.execute("INSERT INTO tickets (id, title, description, status, priority, checklist_path, created_by, "
                           "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (tid, title, str(data.get("description") or ""), status, priority,
                            data.get("checklist_path") or None, actor, now, now))
                self._event(db, tid, actor, role, "created", None, status, now)
        return self.ticket(tid)

    def update(self, tid, data, actor, role):
        fields = {k: data[k] for k in ("title", "description", "status", "priority", "checklist_path") if k in data}
        if not fields:
            raise ApiError(400, "nothing to change (title, description, status, priority, checklist_path)")
        with self.write_lock:
            db = self.db()
            t = self.ticket(tid)
            if role == "agent":
                if t["status"] in AGENT_LOCKED:
                    raise ApiError(403, f"ticket {t['id']} is {t['status']}: agents can only comment on it")
                if "status" in fields and fields["status"] not in AGENT_TARGETS:
                    raise ApiError(403, "agents can set ready, in_progress or review; only the human moves "
                                        "tickets to backlog or done")
            if "status" in fields and fields["status"] not in STATUSES:
                raise ApiError(400, f"status must be one of {STATUSES}")
            if "title" in fields and not str(fields["title"]).strip():
                raise ApiError(400, "the title can't be empty")
            if "priority" in fields:
                fields["priority"] = int(fields["priority"])
                if fields["priority"] not in PRIORITIES:
                    raise ApiError(400, "priority is 1 (high), 2 (normal) or 3 (low)")
            now = time.time()
            with db:
                for k, v in fields.items():
                    if v == "" and k == "checklist_path":
                        v = None
                    if t[k] != v:
                        db.execute(f"UPDATE tickets SET {k} = ? WHERE id = ?", (v, t["id"]))
                        # Description edits are logged without their text.
                        self._event(db, t["id"], actor, role, k, None if k == "description" else t[k],
                                    None if k == "description" else v, now)
                db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, t["id"]))
        return self.ticket(t["id"])

    def claim_next(self, actor, role, claim):
        """The highest-priority, oldest ready ticket; with claim, moved to
        in_progress in the same step so two agents never take the same one."""
        with self.write_lock:
            db = self.db()
            row = db.execute("SELECT id FROM tickets WHERE status = 'ready' ORDER BY priority, created_at LIMIT 1").fetchone()
            if not row:
                return None
            if claim:
                now = time.time()
                with db:
                    db.execute("UPDATE tickets SET status = 'in_progress', updated_at = ? WHERE id = ?", (now, row["id"]))
                    self._event(db, row["id"], actor, role, "status", "ready", "in_progress", now)
        return self.detail(row["id"])

    def comment(self, tid, body, actor, role):
        body = str(body or "").strip()
        if not body:
            raise ApiError(400, "an empty comment")
        t = self.ticket(tid)
        now = time.time()
        with self.write_lock:
            db = self.db()
            with db:
                cur = db.execute("INSERT INTO comments (ticket_id, author, role, body, created_at) VALUES (?,?,?,?,?)",
                                 (t["id"], actor, role, body, now))
                db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, t["id"]))
        return dict(self.db().execute("SELECT * FROM comments WHERE id = ?", (cur.lastrowid,)).fetchone())

    def add_attachment(self, tid, filename, mime, size, stream, comment_id, actor, role, max_bytes):
        t = self.ticket(tid)
        if comment_id is not None:
            c = self.db().execute("SELECT ticket_id FROM comments WHERE id = ?", (comment_id,)).fetchone()
            if not c or c["ticket_id"] != t["id"]:
                raise ApiError(400, f"comment {comment_id} isn't on ticket {t['id']}")
        if size > max_bytes:
            raise ApiError(413, f"file is larger than the {max_bytes // (1 << 20)} MB limit")
        name = re.sub(r"[^\w.\- ]+", "_", Path(filename or "upload").name).strip() or "upload"
        mime = (mime or "").split(";")[0].strip()
        if not mime or mime == "application/octet-stream":
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        folder = self.files / t["id"]
        folder.mkdir(exist_ok=True)
        dest = folder / f"{secrets.token_hex(6)}-{name}"
        left = size
        with open(dest, "wb") as f:
            while left > 0:
                chunk = stream.read(min(CHUNK, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            dest.unlink(missing_ok=True)
            raise ApiError(400, "the upload ended early")
        now = time.time()
        with self.write_lock:
            db = self.db()
            with db:
                cur = db.execute("INSERT INTO attachments (ticket_id, comment_id, filename, path, mime, size, author, role, "
                                 "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (t["id"], comment_id, name, str(dest.relative_to(self.files)), mime, size, actor, role, now))
                db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, t["id"]))
        return self.attachment_json(self.attachment(cur.lastrowid))


# --------------------------------------------------------------------------- keys

def load_keys(data_dir):
    path = Path(data_dir) / "keys.json"
    if path.exists():
        return json.loads(path.read_text())
    keys = {"human": secrets.token_urlsafe(24), "agent": secrets.token_urlsafe(24)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=2) + "\n")
    path.chmod(0o600)
    return keys


# --------------------------------------------------------------------------- HTML

CSS = """
:root{--bg:#f6f5f1;--panel:#fff;--ink:#1f2328;--dim:#646b73;--line:#e2dfd7;--accent:#3a5bd9;--accent-ink:#fff;
--review:#b5480f;--review-bg:#fdf0e7;--prog:#3a5bd9;--prog-bg:#edf1fd;--ready:#1d7a55;--ready-bg:#e8f6ef;
--back:#6b6f76;--back-bg:#efeeea;--done:#4b5563;--done-bg:#eceff2;--agent:#6d4bd1;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--panel:#1d2024;--ink:#e6e8eb;--dim:#9aa1aa;--line:#2c3036;
--accent:#7d97f5;--accent-ink:#0e1530;--review:#f29a63;--review-bg:#3a2417;--prog:#8ea6f7;--prog-bg:#1e2742;
--ready:#62d1a1;--ready-bg:#16302a;--back:#aab0b8;--back-bg:#26292e;--done:#b8c0ca;--done-bg:#262b31;--agent:#b39cf7;
color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent)}
header.top{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
header.top b{margin-right:12px;font-size:15px}
nav a{padding:6px 10px;border-radius:6px;text-decoration:none;color:var(--ink)}
nav a.on{background:var(--bg);font-weight:600}
nav .n{display:inline-block;min-width:18px;padding:0 5px;margin-left:4px;border-radius:9px;background:var(--review);color:#fff;font-size:12px;text-align:center}
main{max-width:1100px;margin:0 auto;padding:18px 16px 60px}
h1{font-size:22px;margin:0 0 14px}
h2{font-size:14px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin:22px 0 10px;font-weight:600}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.card{display:block;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px;text-decoration:none;color:var(--ink)}
.card:hover{border-color:var(--accent)}
.card .t{font-weight:600}
.meta{color:var(--dim);font-size:13px}
.id{font:600 13px ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.04em;color:var(--dim)}
.chip{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}
.s-review{background:var(--review-bg);color:var(--review)}.s-in_progress{background:var(--prog-bg);color:var(--prog)}
.s-ready{background:var(--ready-bg);color:var(--ready)}.s-backlog{background:var(--back-bg);color:var(--back)}
.s-done{background:var(--done-bg);color:var(--done)}
.p1{color:var(--review);font-weight:700}
.snip{margin-top:6px;font-size:13px;color:var(--dim);overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.empty{color:var(--dim);font-size:14px;padding:10px 0}
button,.btn{font:600 14px system-ui,sans-serif;padding:8px 14px;border-radius:7px;border:1px solid var(--line);background:var(--panel);color:var(--ink);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
button:focus-visible,a:focus-visible,textarea:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
input[type=text],input[type=search],textarea,select{width:100%;font:15px system-ui,sans-serif;padding:8px 10px;border-radius:7px;border:1px solid var(--line);background:var(--panel);color:var(--ink)}
textarea{min-height:90px;resize:vertical}
form.stack{display:grid;gap:8px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.list .item{display:flex;gap:12px;align-items:center;justify-content:space-between;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:8px}
.list .item a{color:var(--ink);text-decoration:none;min-width:0;flex:1}
.detail{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:20px}
@media (max-width:800px){.detail{grid-template-columns:1fr}}
.box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.desc{white-space:pre-wrap;overflow-wrap:anywhere}
.tl{display:grid;gap:10px}
.c{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.c.agent{border-left:3px solid var(--agent)}
.c .who{font-weight:600}.c .who.agent{color:var(--agent)}
.c .body{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:4px}
.ev{color:var(--dim);font-size:13px;padding:0 4px}
.att{margin-top:8px}
.att img,.att video{max-width:100%;max-height:420px;border-radius:8px;display:block}
.att audio{width:100%}
.ck{list-style:none;padding:0;margin:8px 0 0}
.ck li{padding:2px 0}.ck li.note{color:var(--dim);font-size:13px}
.ck .x{color:var(--ready)}
.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;background:var(--ready)}
dl.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:0;font-size:14px}
dl.kv dt{color:var(--dim)}dl.kv dd{margin:0;overflow-wrap:anywhere}
.err{color:var(--review)}
"""

JS = """
async function api(method, url, body){
  const r = await fetch(url, {method, headers: {'Content-Type':'application/json','X-Tickets':'1'},
    body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin'});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
async function upload(tid, file, commentId){
  const q = new URLSearchParams({filename: file.name}); if (commentId) q.set('comment_id', commentId);
  const r = await fetch('/api/tickets/' + tid + '/attachments?' + q, {method: 'POST',
    headers: {'Content-Type': file.type || 'application/octet-stream', 'X-Tickets': '1'}, body: file, credentials: 'same-origin'});
  if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || ('HTTP ' + r.status)); }
}
function fail(e){ alert(e.message); }
async function setStatus(tid, status){ try { await api('PATCH', '/api/tickets/' + tid, {status}); location.reload(); } catch (e) { fail(e); } }
"""


def size_text(n):
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, round(n / 1024))} KB"


def esc(s):
    return html.escape(str(s if s is not None else ""))


def ago(ts):
    d = max(0, time.time() - ts)
    if d < 60:
        return "just now"
    for n, unit in ((86400 * 30, "mo"), (86400, "d"), (3600, "h"), (60, "m")):
        if d >= n:
            return f"{int(d // n)}{unit} ago"
    return ""


def when(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def linkify(text):
    out = esc(text)
    return re.sub(r"(https?://[^\s<]+)", r'<a href="\1" target="_blank" rel="noopener">\1</a>', out)


def page(title, body, active="", review_count=0):
    tabs = [("/", "Dashboard"), ("/backlog", "Backlog"), ("/done", "Done"), ("/new", "New ticket")]
    nav = "".join(f'<a href="{h}" class="{"on" if h == active else ""}">{esc(n)}'
                  f'{f"<span class=n>{review_count}</span>" if h == "/" and review_count else ""}</a>' for h, n in tabs)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Agent tickets</title>'
            f'<style>{CSS}</style><script>{JS}</script></head><body>'
            f'<header class="top"><b>Agent tickets</b><nav>{nav}</nav></header><main>{body}</main></body></html>')


def card(t):
    last = t.get("last_comment")
    snip = (f'<div class="snip"><b>{esc(last["author"])}:</b> {esc(last["body"][:240])}</div>' if last
            else (f'<div class="snip">{esc(t["description"][:240])}</div>' if t["description"] else ""))
    extras = []
    if t.get("comment_count"):
        extras.append(f'{t["comment_count"]} comment{"s" if t["comment_count"] != 1 else ""}')
    if t.get("attachment_count"):
        extras.append(f'{t["attachment_count"]} file{"s" if t["attachment_count"] != 1 else ""}')
    pr = '<span class="p1">High · </span>' if t["priority"] == 1 else ""
    return (f'<a class="card" href="/t/{t["id"]}"><div class="row"><span class="id">{t["id"]}</span>'
            f'<span class="t">{esc(t["title"])}</span></div>'
            f'<div class="meta">{pr}updated {ago(t["updated_at"])}{" · " + " · ".join(extras) if extras else ""}</div>{snip}</a>')


def column(title, tickets, empty):
    inner = "".join(card(t) for t in tickets) or f'<p class="empty">{esc(empty)}</p>'
    return f'<section><h2>{esc(title)} ({len(tickets)})</h2>{inner}</section>'


def render_dashboard(store):
    by = {s: store.tickets([s]) for s in ("review", "in_progress", "ready")}
    body = ('<h1>In flight</h1><div class="cols">'
            + column("Needs your review", by["review"], "Nothing waiting on you.")
            + column("In progress", by["in_progress"], "No agent is working on anything.")
            + column("Ready to work", by["ready"], "Nothing cleared for agents. Promote something from the backlog.")
            + '</div><script>setInterval(() => { if (!document.hidden) location.reload(); }, 60000);</script>')
    return page("Dashboard", body, "/", len(by["review"]))


def render_list(store, status, q):
    items = store.tickets([status], q=q or None, recent_first=status == "done")
    rows = []
    for t in items:
        btn = (f'<button class="primary" onclick="setStatus(\'{t["id"]}\',\'ready\')">Make ready</button>'
               if status == "backlog" else "")
        pr = '<span class="p1">High · </span>' if t["priority"] == 1 else ""
        rows.append(f'<div class="item"><a href="/t/{t["id"]}"><span class="id">{t["id"]}</span> '
                    f'<b>{esc(t["title"])}</b><div class="meta">{pr}{"created" if status == "backlog" else "finished"} '
                    f'{ago(t["created_at"] if status == "backlog" else t["updated_at"])}</div></a>{btn}</div>')
    title = "Backlog" if status == "backlog" else "Done"
    search = (f'<form class="row" method="get" style="margin-bottom:12px"><input type="search" name="q" value="{esc(q)}" '
              f'placeholder="Search {title.lower()}" aria-label="Search"><button>Search</button></form>')
    hint = ('<p class="meta">Ideas waiting to be cleared. "Make ready" lets agents pick one up.</p>' if status == "backlog"
            else '<p class="meta">Work you approved.</p>')
    body = f'<h1>{title}</h1>{hint}{search}<div class="list">{"".join(rows) or "<p class=empty>Nothing here.</p>"}</div>'
    return page(title, body, "/" + status, len(store.tickets(["review"])))


def render_new(store):
    opts = "".join(f'<option value="{s}"{" selected" if s == "backlog" else ""}>{LABELS[s]}</option>' for s in STATUSES)
    body = f"""<h1>New ticket</h1><form class="stack box" id="nf">
<label>Title<input type="text" id="nt-title" required></label>
<label>Description<textarea id="nt-desc" rows="8"></textarea></label>
<div class="row"><label>Status <select id="nt-status">{opts}</select></label>
<label>Priority <select id="nt-prio"><option value="1">High</option><option value="2" selected>Normal</option><option value="3">Low</option></select></label></div>
<label>Checklist file (optional, a path on this machine)<input type="text" id="nt-ck" placeholder="/home/.../checklist.md"></label>
<div><button class="primary">Create</button></div></form>
<script>const $ = (id) => document.getElementById(id);
$('nf').onsubmit = async (e) => {{ e.preventDefault();
 try {{ const t = await api('POST', '/api/tickets', {{title: $('nt-title').value, description: $('nt-desc').value,
   status: $('nt-status').value, priority: +$('nt-prio').value, checklist_path: $('nt-ck').value || null}});
   location = '/t/' + t.id; }} catch (err) {{ fail(err); }} }};</script>"""
    return page("New ticket", body, "/new", len(store.tickets(["review"])))


def render_attachment(a):
    name = esc(a["filename"])
    link = f'<a href="{a["url"]}" target="_blank">{name}</a> <span class="meta">{size_text(a["size"])}</span>'
    if a["mime"].startswith("image/"):
        media = f'<a href="{a["url"]}" target="_blank"><img src="{a["url"]}" alt="{name}" loading="lazy"></a>'
    elif a["mime"].startswith("video/"):
        media = f'<video src="{a["url"]}" controls preload="metadata"></video>'
    elif a["mime"].startswith("audio/"):
        media = f'<audio src="{a["url"]}" controls preload="metadata"></audio>'
    else:
        media = ""
    return f'<div class="att">{media}<div class="meta">{link}</div></div>'


def render_ticket(store, tid):
    t = store.detail(tid)
    by_comment = {}
    loose = []
    for a in t["attachments"]:
        (by_comment.setdefault(a["comment_id"], []) if a["comment_id"] else loose).append(a)
    items = []
    for c in t["comments"]:
        files = "".join(render_attachment(a) for a in by_comment.get(c["id"], []))
        role = "agent" if c["role"] == "agent" else ""
        items.append((c["created_at"], f'<div class="c {role}"><span class="who {role}">{esc(c["author"])}</span> '
                                       f'<span class="meta" title="{when(c["created_at"])}">{ago(c["created_at"])}</span>'
                                       f'<div class="body">{linkify(c["body"])}</div>{files}</div>'))
    for a in loose:
        items.append((a["created_at"], f'<div class="c {"agent" if a["role"] == "agent" else ""}"><span class="who">'
                                       f'{esc(a["author"])}</span> <span class="meta">attached a file · {ago(a["created_at"])}</span>'
                                       f'{render_attachment(a)}</div>'))
    for e in t["events"]:
        if e["field"] == "created":
            text = f'{esc(e["actor"])} created it in {LABELS.get(e["new"], e["new"])}'
        elif e["field"] == "status":
            text = f'{esc(e["actor"])} moved it from {LABELS.get(e["old"], e["old"])} to <b>{LABELS.get(e["new"], e["new"])}</b>'
        elif e["field"] == "description":
            text = f'{esc(e["actor"])} edited the description'
        else:
            text = f'{esc(e["actor"])} changed {esc(e["field"]).replace("_", " ")} to {esc(e["new"] or "nothing")}'
        items.append((e["at"], f'<div class="ev">{text} · <span title="{when(e["at"])}">{ago(e["at"])}</span></div>'))
    items.sort(key=lambda x: x[0])
    timeline = "".join(h for _, h in items) or '<p class="empty">No activity yet.</p>'

    ck = t["checklist"]
    if ck is None:
        ckhtml = '<p class="meta">No checklist file.</p>'
    elif not ck["readable"]:
        ckhtml = f'<p class="meta">{esc(ck["path"])}</p><p class="err">{esc(ck.get("error", "unreadable"))}</p>'
    else:
        lis = "".join(f'<li class="note">{esc(i["text"])}</li>' if i.get("note") else
                      f'<li>{"<span class=x>☑</span>" if i["done"] else "☐"} {esc(i["text"])}</li>' for i in ck["items"])
        pct = round(100 * ck["done"] / ck["total"]) if ck["total"] else 0
        ckhtml = (f'<p class="meta" style="overflow-wrap:anywhere">{esc(ck["path"])}</p>'
                  + (f'<div class="meta">{ck["done"]} of {ck["total"]} done</div><div class="bar"><i style="width:{pct}%"></i></div>'
                     if ck["total"] else "") + f'<ul class="ck">{lis}</ul>')

    s = t["status"]
    actions = {
        "backlog": [("ready", "Make ready", True)],
        "ready": [("backlog", "Back to backlog", False)],
        "in_progress": [("review", "Needs review", False), ("ready", "Release to ready", False)],
        "review": [("done", "Approve", True), ("in_progress", "Send back", False)],
        "done": [("in_progress", "Reopen", False)],
    }[s]
    btns = "".join(f'<button class="{"primary" if p else ""}" onclick="setStatus(\'{t["id"]}\',\'{to}\')">{esc(lbl)}</button>'
                   for to, lbl, p in actions)
    move_opts = "".join(f'<option value="{x}">{LABELS[x]}</option>' for x in STATUSES if x != s)
    body = f"""<div class="row" style="margin-bottom:6px"><span class="id">{t['id']}</span>
<span class="chip s-{s}">{LABELS[s]}</span>{'<span class="p1">High priority</span>' if t['priority'] == 1 else ''}</div>
<h1>{esc(t['title'])}</h1>
<div class="row" style="margin-bottom:16px">{btns}</div>
<div class="detail"><div>
 <div class="box"><div class="desc">{linkify(t['description']) or '<span class="meta">No description.</span>'}</div></div>
 <h2>Activity</h2><div class="tl">{timeline}</div>
 <h2>Comment</h2><form class="stack box" id="cf">
  <textarea id="cbody" placeholder="Feedback, a decision, or a question for the agent" aria-label="Comment"></textarea>
  <div class="row"><input type="file" id="cfiles" multiple aria-label="Attach files">
  <label class="meta">then <select id="cmove" style="width:auto"><option value="">keep status</option>{move_opts}</select></label>
  <button class="primary" id="csend">Post</button></div></form>
</div><aside>
 <h2 style="margin-top:0">Checklist</h2><div class="box">{ckhtml}</div>
 <h2>Details</h2><div class="box"><dl class="kv"><dt>Priority</dt><dd>{PRIORITIES[t['priority']]}</dd>
  <dt>Created</dt><dd>{when(t['created_at'])} by {esc(t['created_by'])}</dd><dt>Updated</dt><dd>{ago(t['updated_at'])}</dd></dl>
  <details style="margin-top:10px"><summary>Edit</summary><form class="stack" id="ef" style="margin-top:8px">
   <label>Title<input type="text" id="etitle" value="{esc(t['title'])}"></label>
   <label>Description<textarea id="edesc" rows="8">{esc(t['description'])}</textarea></label>
   <label>Priority<select id="eprio">{''.join(f'<option value="{k}"{" selected" if k == t["priority"] else ""}>{v}</option>' for k, v in PRIORITIES.items())}</select></label>
   <label>Checklist file<input type="text" id="eck" value="{esc(t['checklist_path'] or '')}"></label>
   <button class="primary">Save</button></form></details></div>
</aside></div>
<script>
const TID = {json.dumps(t['id'])}, $ = (id) => document.getElementById(id);
document.getElementById('cf').onsubmit = async (e) => {{
  e.preventDefault(); const btn = document.getElementById('csend'); btn.disabled = true; btn.textContent = 'Posting…';
  try {{
    const files = [...$('cfiles').files], text = $('cbody').value.trim();
    let cid = null;
    if (text || files.length) cid = (await api('POST', '/api/tickets/' + TID + '/comments', {{body: text || '(attached files)'}})).id;
    for (const f of files) {{ btn.textContent = 'Uploading ' + f.name + '…'; await upload(TID, f, cid); }}
    if ($('cmove').value) await api('PATCH', '/api/tickets/' + TID, {{status: $('cmove').value}});
    location.reload();
  }} catch (err) {{ fail(err); btn.disabled = false; btn.textContent = 'Post'; }}
}};
document.getElementById('ef').onsubmit = async (e) => {{
  e.preventDefault();
  try {{ await api('PATCH', '/api/tickets/' + TID, {{title: $('etitle').value, description: $('edesc').value,
    priority: +$('eprio').value, checklist_path: $('eck').value}}); location.reload(); }} catch (err) {{ fail(err); }}
}};
</script>"""
    return page(f"{t['id']} {t['title']}", body, "", len(store.tickets(["review"])))


API_DOCS = """# Agent tickets API

Send your key with every request: `Authorization: Bearer <agent key>`.
Name yourself with `X-Agent: <name>` (shown on your comments and changes).
JSON in, JSON out. Ticket ids are four or five letters, like `KAMI`
(any case).

## Statuses
backlog -> ready -> in_progress -> review -> done

- ready: cleared for you to work on.
- in_progress: you're on it (partial progress counts; leave a comment).
- review: you're done, or you need a decision. Say which in a comment.
- backlog and done belong to the human: you can't set them, and you can
  only comment on tickets in them.

## Endpoints
GET   /api/next                 next ready ticket (high priority, oldest first), or {"ticket": null}
GET   /api/next?claim=1         ...and move it to in_progress in the same step
GET   /api/tickets?status=ready,in_progress&q=text    list
GET   /api/tickets/ID           ticket with comments, attachments, events, checklist
POST  /api/tickets              {"title", "description", "priority": 1|2|3, "checklist_path"}  (lands in backlog)
PATCH /api/tickets/ID           any of {"status", "title", "description", "priority", "checklist_path"}
POST  /api/tickets/ID/comments  {"body": "..."}  -> {"id": comment id, ...}
POST  /api/tickets/ID/attachments?filename=demo.mp4[&comment_id=N]
                                raw file bytes as the body, with its Content-Type
                                (images, audio and video play in the page)

## A work session
1. `GET /api/next?claim=1` (or pick a ready ticket and PATCH it to in_progress).
2. Read the description and the human's comments.
3. Comment as you go; attach screenshots, recordings, audio.
4. Point `checklist_path` at a markdown checklist ("- [ ] step") you keep
   up to date; the page shows it with progress.
5. Finished, or need a decision: comment what you did and what you need,
   then PATCH status to review. Stopping early: leave it in_progress with a
   comment saying where you got to, or release it back to ready.

## Example
curl -s -H "Authorization: Bearer $KEY" -H "X-Agent: builder" "$URL/api/next?claim=1"
curl -s -H "Authorization: Bearer $KEY" -H "X-Agent: builder" -H "Content-Type: application/json" \\
     -d '{"body": "Tests pass. Recording attached."}' $URL/api/tickets/KAMI/comments
curl -s -H "Authorization: Bearer $KEY" -H "X-Agent: builder" -H "Content-Type: video/mp4" \\
     --data-binary @demo.mp4 "$URL/api/tickets/KAMI/attachments?filename=demo.mp4&comment_id=12"
curl -s -X PATCH -H "Authorization: Bearer $KEY" -H "X-Agent: builder" -H "Content-Type: application/json" \\
     -d '{"status": "review"}' $URL/api/tickets/KAMI
"""


# --------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "agent-tickets"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Never log query strings: they can carry the key.
        sys.stderr.write("%s %s %s\n" % (self.command, urlsplit(self.path).path, args[1] if len(args) > 1 else ""))

    # --- auth
    def who(self, query):
        keys = self.server.keys
        given = None
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            given = auth[7:].strip()
        elif query.get("key"):
            given = query["key"][0]
        else:
            c = SimpleCookie(self.headers.get("Cookie", "")).get(COOKIE)
            given = c.value if c else None
        if not given:
            return None, None, False
        for role in ("human", "agent"):
            if hmac.compare_digest(given, keys[role]):
                name = self.server.human_name if role == "human" else (self.headers.get("X-Agent") or "agent").strip()[:40] or "agent"
                return role, name, auth.startswith("Bearer ")
        return None, None, False

    # --- responses
    def send(self, code, body, ctype, extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def json(self, code, obj):
        self.send(code, json.dumps(obj, default=str), "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def html(self, body):
        self.send(200, body, "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ApiError(413, "request too large")
        raw = self.rfile.read(n) if n else b""
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise ApiError(400, "body isn't JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "body should be a JSON object")
        return data

    # --- routing
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PATCH(self):
        self.route("PATCH")

    def route(self, method):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        path = url.path.rstrip("/") or "/"
        store = self.server.store
        try:
            role, actor, bearer = self.who(query)
            if role is None:
                if path.startswith("/api") or path.startswith("/files"):
                    raise ApiError(401, "missing or wrong key (Authorization: Bearer <key>)")
                return self.send(401, page("Locked", "<h1>Locked</h1><p>Open this page once with "
                                           "<code>?key=…</code> (the human key) to sign in.</p>"), "text/html; charset=utf-8")
            if "key" in query and method == "GET" and not path.startswith("/api"):
                # Trade the key in the URL for a cookie, then drop it from the address bar.
                secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
                self.send_response(303)
                self.send_header("Location", path)
                self.send_header("Set-Cookie", f"{COOKIE}={query['key'][0]}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            # Changes made with the cookie must come from our own pages.
            if method != "GET" and not bearer and self.headers.get("X-Tickets") != "1":
                raise ApiError(403, "missing X-Tickets header")

            m = re.fullmatch(r"/api/tickets/([A-Za-z]{4,5})(/comments|/attachments)?", path)
            if method == "GET" and path == "/":
                return self.html(render_dashboard(store))
            if method == "GET" and path in ("/backlog", "/done"):
                return self.html(render_list(store, path[1:], (query.get("q") or [""])[0].strip()))
            if method == "GET" and path == "/new":
                return self.html(render_new(store))
            if method == "GET" and (tm := re.fullmatch(r"/t/([A-Za-z]{4,5})", path)):
                return self.html(render_ticket(store, tm.group(1)))
            if method == "GET" and (fm := re.fullmatch(r"/files/(\d+)/[^/]*", path)):
                return self.serve_file(store.attachment(int(fm.group(1))))
            if method == "GET" and path == "/api":
                return self.send(200, API_DOCS, "text/markdown; charset=utf-8")
            if method == "GET" and path == "/api/next":
                t = store.claim_next(actor, role, bool((query.get("claim") or [""])[0]))
                return self.json(200, {"ticket": t})
            if path == "/api/tickets":
                if method == "GET":
                    st = [s for s in (query.get("status") or [""])[0].split(",") if s]
                    bad = [s for s in st if s not in STATUSES]
                    if bad:
                        raise ApiError(400, f"unknown status {bad}; statuses: {STATUSES}")
                    return self.json(200, {"tickets": store.tickets(st or None, (query.get("q") or [""])[0] or None)})
                if method == "POST":
                    return self.json(201, store.create(self.body_json(), actor, role))
            if m:
                tid, sub = m.group(1), m.group(2)
                if not sub and method == "GET":
                    return self.json(200, store.detail(tid))
                if not sub and method == "PATCH":
                    return self.json(200, store.update(tid, self.body_json(), actor, role))
                if sub == "/comments" and method == "POST":
                    return self.json(201, store.comment(tid, self.body_json().get("body"), actor, role))
                if sub == "/attachments" and method == "POST":
                    size = int(self.headers.get("Content-Length") or -1)
                    if size < 0:
                        raise ApiError(411, "send the file with a Content-Length")
                    cid = (query.get("comment_id") or [None])[0]
                    a = store.add_attachment(tid, (query.get("filename") or ["upload"])[0], self.headers.get("Content-Type"),
                                             size, self.rfile, int(cid) if cid else None, actor, role, self.server.max_upload)
                    return self.json(201, a)
            raise ApiError(404, "not found")
        except ApiError as e:
            if method != "GET":
                self.close_connection = True  # an unread body would confuse the next request
            if path.startswith("/api") or path.startswith("/files") or method != "GET":
                return self.json(e.code, {"error": e.msg})
            return self.send(e.code, page("Error", f"<h1>{esc(e.msg)}</h1><p><a href='/'>Dashboard</a></p>"),
                             "text/html; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve_file(self, a):
        p = self.server.store.files / a["path"]
        try:
            size = p.stat().st_size
        except OSError:
            raise ApiError(404, "the file is missing from disk")
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range", "")
        mr = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        if mr and size:
            if mr.group(1):
                start = int(mr.group(1))
                end = min(int(mr.group(2)), size - 1) if mr.group(2) else size - 1
            elif mr.group(2):
                start = max(0, size - int(mr.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", a["mime"])
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        inline = a["mime"].split("/")[0] in ("image", "audio", "video")
        self.send_header("Content-Disposition", f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(a['filename'])}")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(p, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


def make_server(host, port, data_dir, checklist_roots, max_upload_mb=1024, human_name="human"):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.store = Store(data_dir, checklist_roots)
    srv.keys = load_keys(data_dir)
    srv.max_upload = max_upload_mb << 20
    srv.human_name = human_name
    return srv


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("AT_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("AT_PORT", 43917)))
    ap.add_argument("--data", default=os.environ.get("AT_DATA", str(Path(__file__).resolve().parent / "data")),
                    help="folder for the database, uploads and keys")
    ap.add_argument("--checklist-root", action="append", default=None,
                    help="a folder checklist files may live in (repeatable; default: your home folder)")
    ap.add_argument("--max-upload-mb", type=int, default=int(os.environ.get("AT_MAX_UPLOAD_MB", 1024)))
    ap.add_argument("--human-name", default=os.environ.get("AT_HUMAN", "human"),
                    help="your name on comments and changes you make in the web pages")
    ap.add_argument("--keys", action="store_true", help="print the access keys and exit")
    a = ap.parse_args()
    if a.keys:
        k = load_keys(a.data)
        print(f"human (web pages, full control): {k['human']}\nagent (API):                    {k['agent']}")
        return
    roots = a.checklist_root or [str(Path.home())]
    srv = make_server(a.host, a.port, a.data, roots, a.max_upload_mb, a.human_name)
    print(f"agent-tickets on http://{a.host}:{a.port}  data: {Path(a.data).resolve()}  checklists under: {', '.join(roots)}",
          flush=True)
    print("keys: python3 agent_tickets.py --keys", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
