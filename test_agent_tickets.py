"""Tests for agent_tickets.py: a real server on a temporary folder, driven
over HTTP. Standard library only:  python3 -m unittest -v"""
import io
import json
import re
import tarfile
import tempfile
import time
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import agent_tickets as at


class TicketsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.allowed = root / "work"
        cls.allowed.mkdir()
        cls.srv = at.make_server("127.0.0.1", 0, root / "data", [str(cls.allowed)], max_upload_mb=1)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.keys = {"human": cls.srv.store.add_user("Frank", "human", "test")[1],
                    "agent": cls.srv.store.add_user("tester", "agent", "test")[1]}
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, role="agent", headers=None, raw=None, token=None):
        h = {}
        if token or role:
            h["Authorization"] = "Bearer " + (token or self.keys[role])
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def j(self, *a, **k):
        code, body, _ = self.call(*a, **k)
        return code, json.loads(body or b"null")

    def new(self, status="ready", **kw):
        code, t = self.j("POST", "/api/tickets", {"title": kw.pop("title", "A task"), "status": status, **kw}, role="human")
        self.assertEqual(code, 201, t)
        return t

    # --- auth
    def test_needs_a_key(self):
        self.assertEqual(self.call("GET", "/api/tickets", role=None)[0], 401)
        self.assertEqual(self.call("GET", "/api/tickets", role=None, headers={"Authorization": "Bearer nope"})[0], 401)
        self.assertEqual(self.call("GET", "/", role=None)[0], 401)

    def test_key_in_url_becomes_a_cookie(self):
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
        req = urllib.request.Request(self.url + "/?key=" + self.keys["human"])
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        try:
            urllib.request.build_opener(NoRedirect).open(req)
            self.fail("expected a redirect")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 303)
            self.assertEqual(e.headers["Location"], "/")
            cookie = e.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        value = cookie.split(";")[0]
        code, body, _ = self.call("GET", "/", role=None, headers={"Cookie": value})
        self.assertEqual(code, 200)
        self.assertIn(b"In flight", body)
        # Changes made with the cookie need the page's own header.
        self.assertEqual(self.call("POST", "/api/tickets", {"title": "x"}, role=None, headers={"Cookie": value})[0], 403)
        self.assertEqual(self.call("POST", "/api/tickets", {"title": "x"}, role=None,
                                   headers={"Cookie": value, "X-Tickets": "1"})[0], 201)

    # --- tickets and the rules
    def test_ids_are_two_words(self):
        t = self.new()
        adj, noun = t["id"].split("-")
        self.assertIn(adj, at.ID_ADJECTIVES)
        self.assertIn(noun, at.ID_NOUNS)
        # Any case, a hyphen or a space.
        self.assertEqual(self.j("GET", "/api/tickets/" + t["id"].upper())[1]["id"], t["id"])
        self.assertEqual(at.norm_id(f" {adj.title()} {noun.upper()} "), t["id"])
        self.assertEqual(self.call("GET", f"/t/{t['id']}", role="human")[0], 200)
        # The lists: clean words, no repeats, none in both (so an id reads one way), and room for 100,000+ tickets.
        for ws in (at.ID_ADJECTIVES, at.ID_NOUNS):
            self.assertEqual(len(ws), len(set(ws)))
            for w in ws:
                self.assertRegex(w, r"^[a-z]{3,10}$")
        self.assertFalse(set(at.ID_ADJECTIVES) & set(at.ID_NOUNS))
        self.assertGreater(len(at.ID_ADJECTIVES) * len(at.ID_NOUNS), 150_000)
        # A taken id is skipped.
        picks = iter([adj, noun, "amber", "otter"])
        self.assertEqual(at.new_id(lambda tid: tid == t["id"], pick=lambda pool: next(picks)), "amber-otter")

    def test_old_ids_keep_working(self):
        store = self.srv.store
        now = time.time()
        with store.db() as db:
            db.execute("INSERT INTO tickets (id, title, status, created_at, updated_at) VALUES ('KAMO', 'old one', 'done', ?, ?)",
                       (now, now))
        self.assertEqual(self.j("GET", "/api/tickets/kamo")[1]["title"], "old one")
        self.assertEqual(self.call("GET", "/t/KAMO", role="human")[0], 200)
        # Old and new ids together, as dependencies, in a string with commas and spaces.
        a = self.new("backlog", title="new one")
        spaced = a["id"].replace("-", " ").title()
        b = self.new("backlog", title="after both", depends_on=f"kamo, {spaced}")
        self.assertEqual(sorted(d["id"] for d in self.j("GET", f"/api/tickets/{b['id']}")[1]["depends_on"]),
                         sorted(["KAMO", a["id"]]))
        self.assertEqual(self.j("DELETE", f"/api/tickets/{b['id']}/deps/{a['id']}", role="human")[0], 200)
        # Both kinds link from chat; look-alike hyphenated words don't.
        c = self.j("POST", "/api/chat", {"title": "ids", "body": f"see KAMO and {a['id']}, not well-known"},
                   role="human")[1]
        html = self.call("GET", f"/chat/{c['id']}", role="human")[1].decode()
        self.assertIn('<a href="/t/KAMO">KAMO</a>', html)
        self.assertIn(f'<a href="/t/{a["id"]}">{a["id"]}</a>', html)
        self.assertNotIn("/t/well-known", html)

    def test_agents_file_into_the_backlog_only(self):
        code, t = self.j("POST", "/api/tickets", {"title": "Idea from an agent"})
        self.assertEqual(code, 201)
        self.assertEqual(t["status"], "backlog")
        self.assertEqual(t["created_by"], "tester")
        self.assertEqual(self.j("POST", "/api/tickets", {"title": "x", "status": "ready"})[0], 403)

    def test_agent_status_rules(self):
        t = self.new("ready")
        tid = t["id"]
        self.assertEqual(self.j("PATCH", f"/api/tickets/{tid}", {"status": "in_progress"})[1]["status"], "in_progress")
        self.assertEqual(self.j("PATCH", f"/api/tickets/{tid}", {"status": "done"})[0], 403)
        self.assertEqual(self.j("PATCH", f"/api/tickets/{tid}", {"status": "review"})[1]["status"], "review")
        # The human approves; then the agent can only comment.
        self.assertEqual(self.j("PATCH", f"/api/tickets/{tid}", {"status": "done"}, role="human")[1]["status"], "done")
        self.assertEqual(self.j("PATCH", f"/api/tickets/{tid}", {"status": "in_progress"})[0], 403)
        self.assertEqual(self.j("POST", f"/api/tickets/{tid}/comments", {"body": "thanks"})[0], 201)
        # Backlog tickets are the human's too.
        b = self.new("backlog")
        self.assertEqual(self.j("PATCH", f"/api/tickets/{b['id']}", {"status": "ready"})[0], 403)
        self.assertEqual(self.j("PATCH", f"/api/tickets/{b['id']}", {"title": "renamed"})[0], 403)
        events = self.j("GET", f"/api/tickets/{tid}")[1]["events"]
        self.assertEqual([(e["old"], e["new"]) for e in events if e["field"] == "status"],
                         [("ready", "in_progress"), ("in_progress", "review"), ("review", "done")])
        self.assertEqual(events[-1]["actor"], "Frank")

    def test_validation(self):
        self.assertEqual(self.j("POST", "/api/tickets", {"title": " "}, role="human")[0], 400)
        self.assertEqual(self.j("POST", "/api/tickets", {"title": "x", "status": "nope"}, role="human")[0], 400)
        self.assertEqual(self.j("POST", "/api/tickets", {"title": "x", "priority": 9}, role="human")[0], 400)
        self.assertEqual(self.j("GET", "/api/tickets/ZZZZ")[0], 404)
        self.assertEqual(self.j("GET", "/api/tickets?status=bogus")[0], 400)
        t = self.new()
        self.assertEqual(self.j("PATCH", f"/api/tickets/{t['id']}", {})[0], 400)
        self.assertEqual(self.call("POST", f"/api/tickets/{t['id']}/comments", raw=b"not json",
                                   headers={"Content-Type": "application/json"})[0], 400)

    def test_list_filters(self):
        a = self.new("review", title="Filter me please")
        code, r = self.j("GET", "/api/tickets?status=review&q=filter%20me")
        self.assertEqual(code, 200)
        self.assertIn(a["id"], [t["id"] for t in r["tickets"]])
        self.assertTrue(all(t["status"] == "review" for t in r["tickets"]))

    def test_next_claims_each_ticket_once(self):
        ids = {self.new("ready", priority=1, title=f"race {i}")["id"] for i in range(6)}
        got, lock = [], threading.Lock()

        def worker():
            code, r = self.j("GET", "/api/next?claim=1")
            if r["ticket"] and r["ticket"]["id"] in ids:
                with lock:
                    got.append(r["ticket"]["id"])
        threads = [threading.Thread(target=worker) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(got), len(set(got)), "a ticket was claimed twice")
        for tid in got:
            self.assertEqual(self.j("GET", f"/api/tickets/{tid}")[1]["status"], "in_progress")

    # --- comments and files
    def test_comments_and_attachments(self):
        t = self.new("in_progress")
        tid = t["id"]
        code, c = self.j("POST", f"/api/tickets/{tid}/comments", {"body": "Here's a recording"})
        self.assertEqual(code, 201)
        video = bytes(range(256)) * 40
        code, a = self.j("POST", f"/api/tickets/{tid}/attachments?filename=../demo%20clip.mp4&comment_id={c['id']}",
                         raw=video, headers={"Content-Type": "video/mp4"})
        self.assertEqual(code, 201, a)
        self.assertEqual(a["filename"], "demo clip.mp4")
        self.assertEqual(a["mime"], "video/mp4")
        self.assertEqual(a["comment_id"], c["id"])
        # Whole file, then a range (how browsers seek in video).
        code, body, h = self.call("GET", a["url"])
        self.assertEqual((code, body), (200, video))
        self.assertEqual(h["Accept-Ranges"], "bytes")
        code, body, h = self.call("GET", a["url"], headers={"Range": "bytes=100-199"})
        self.assertEqual((code, body, h["Content-Range"]), (206, video[100:200], f"bytes 100-199/{len(video)}"))
        self.assertEqual(self.call("GET", a["url"], headers={"Range": "bytes=999999-"})[0], 416)
        # Type from the name when none is sent; a comment from another ticket is refused.
        code, img = self.j("POST", f"/api/tickets/{tid}/attachments?filename=shot.png", raw=b"\x89PNG....",
                           headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(img["mime"], "image/png")
        other = self.new()
        self.assertEqual(self.j("POST", f"/api/tickets/{other['id']}/attachments?filename=x.txt&comment_id={c['id']}",
                                raw=b"x")[0], 400)
        detail = self.j("GET", f"/api/tickets/{tid}")[1]
        self.assertEqual(len(detail["comments"]), 1)
        self.assertEqual(len(detail["attachments"]), 2)
        self.assertNotIn("path", detail["attachments"][0])
        # The page shows the video player and the image.
        html = self.call("GET", f"/t/{tid}", role="human")[1].decode()
        self.assertIn("<video", html)
        self.assertIn("<img", html)
        self.assertIn("Here&#x27;s a recording", html)

    def test_any_file_type(self):
        tid = self.new("in_progress")["id"]
        blob = bytes(range(256)) * 50
        up = lambda name, data, ctype=None: self.j(
            "POST", f"/api/tickets/{tid}/attachments?filename={name}", raw=data,
            headers={"Content-Type": ctype} if ctype else {})[1]
        tgz = up("build.tar.gz", blob, "application/gzip")
        script = up("fix.sh", b"#!/bin/sh\necho '<b>hi</b>'\n")
        notes = up("NOTES.md", b"# Findings\n- it works\n")
        svg = up("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
        self.assertEqual((tgz["mime"], svg["mime"]), ("application/gzip", "image/svg+xml"))
        # Downloads come back byte for byte, as attachments, and never run scripts in the tracker's origin.
        code, body, h = self.call("GET", tgz["url"])
        self.assertEqual((code, body), (200, blob))
        self.assertTrue(h["Content-Disposition"].startswith("attachment"))
        self.assertIn("sandbox", self.call("GET", svg["url"])[2]["Content-Security-Policy"])
        # The page: download links for all; text previews (escaped) for the script and the notes, not the tarball.
        html = self.call("GET", f"/t/{tid}", role="human")[1].decode()
        for a in (tgz, script, notes):
            self.assertIn(f'href="{a["url"]}" download', html)
        self.assertEqual(html.count('class="pv"'), 2)
        self.assertIn("echo &#x27;&lt;b&gt;hi&lt;/b&gt;&#x27;", html)
        self.assertIn("# Findings", html)

    def test_upload_limit(self):
        t = self.new()
        self.assertEqual(self.j("POST", f"/api/tickets/{t['id']}/attachments?filename=big.bin",
                                raw=b"x" * ((1 << 20) + 1))[0], 413)

    # --- checklists
    def test_checklist(self):
        ck = self.allowed / "plan.md"
        ck.write_text("# Plan\n- [x] scaffold\n- [ ] tests\n* [X] docs\n")
        t = self.new(checklist_path=str(ck))
        c = self.j("GET", f"/api/tickets/{t['id']}")[1]["checklist"]
        self.assertTrue(c["readable"])
        self.assertEqual((c["done"], c["total"]), (2, 3))
        outside = self.new(checklist_path="/etc/passwd")
        c = self.j("GET", f"/api/tickets/{outside['id']}")[1]["checklist"]
        self.assertFalse(c["readable"])
        self.assertNotIn("root", json.dumps(c))
        sneaky = self.new(checklist_path=str(self.allowed / ".." / ".." / "etc" / "passwd"))
        self.assertFalse(self.j("GET", f"/api/tickets/{sneaky['id']}")[1]["checklist"]["readable"])

    # --- backups
    def test_export_and_restore(self):
        t = self.new("in_progress", title="Back me up", checklist_path=str(self.allowed / "x.md"))
        c = self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "progress"})[1]
        clip = bytes(range(256)) * 20
        self.j("POST", f"/api/tickets/{t['id']}/attachments?filename=clip.mp4&comment_id={c['id']}", raw=clip,
               headers={"Content-Type": "video/mp4"})
        self.assertEqual(self.call("GET", "/api/export")[0], 403)  # agents can't export
        code, blob, h = self.call("GET", "/api/export", role="human")
        self.assertEqual(code, 200)
        self.assertIn("attachment", h["Content-Disposition"])
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / "b.tar.gz"
            backup.write_bytes(blob)
            with tarfile.open(backup) as tar:
                doc = json.load(tar.extractfile("export.json"))
                self.assertEqual((doc["format"], doc["version"]), ("agent-tickets-export", 1))
                self.assertIn("files/", " ".join(tar.getnames()))
            # Restore into a fresh data folder: every row and file comes back as it was.
            fresh = at.Store(Path(tmp) / "data", [str(self.allowed)])
            counts = at.import_archive(fresh, backup)
            self.assertEqual(counts["tickets"], len(doc["tables"]["tickets"]))
            src = self.srv.store
            for table in at.TABLES:
                rows = lambda st: [dict(r) for r in st.db().execute(f"SELECT * FROM {table} ORDER BY rowid")]
                self.assertEqual(rows(fresh), rows(src), table)
            a = fresh.detail(t["id"])["attachments"][0]
            self.assertEqual((fresh.files / fresh.attachment(a["id"])["path"]).read_bytes(), clip)
            # A second restore refuses to overwrite, unless told to.
            with self.assertRaises(ValueError):
                at.import_archive(fresh, backup)
            at.import_archive(fresh, backup, replace=True)
            self.assertEqual(fresh.db().execute("SELECT COUNT(*) FROM tickets").fetchone()[0], counts["tickets"])

    def test_export_folder_keeps_the_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(3):
                dest, counts, pruned = at.export_to(self.srv.store, tmp + "/", keep=2)
                (Path(tmp) / f"agent-tickets-0000000{i}-000000.tar.gz").write_bytes(b"old")
            names = sorted(p.name for p in Path(tmp).glob("agent-tickets-*.tar.gz"))
            self.assertEqual(len(names), 3)  # 2 kept by the last prune + the old one written after it
            self.assertTrue(any(n.startswith("agent-tickets-2") for n in names))
            # A folder that doesn't exist yet, given with a trailing slash, is created.
            dest, _, _ = at.export_to(self.srv.store, tmp + "/new/sub/", keep=5)
            self.assertTrue((Path(tmp) / "new" / "sub").is_dir())
            self.assertEqual(dest.parent, Path(tmp) / "new" / "sub")

    def test_bad_backups_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = at.Store(Path(tmp) / "data", [tmp])
            def archive(doc, extra=None):
                p = Path(tmp) / "x.tar.gz"
                with tarfile.open(p, "w:gz") as tar:
                    data = json.dumps(doc).encode()
                    info = tarfile.TarInfo("export.json"); info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
                    for name, body in (extra or {}).items():
                        info = tarfile.TarInfo(name); info.size = len(body)
                        tar.addfile(info, io.BytesIO(body))
                return p
            with self.assertRaises(ValueError):
                at.import_archive(store, archive({"format": "something-else"}))
            with self.assertRaises(ValueError):
                at.import_archive(store, archive({"format": "agent-tickets-export", "version": 99, "tables": {}}))
            # A file trying to escape the data folder is skipped.
            ok = {"format": "agent-tickets-export", "version": 1, "tables": {}}
            at.import_archive(store, archive(ok, {"files/../../escaped.txt": b"x", "files/T/ok.txt": b"y"}))
            self.assertFalse((Path(tmp) / "escaped.txt").exists())
            self.assertFalse((store.dir / "escaped.txt").exists())
            self.assertTrue((store.files / "T" / "ok.txt").exists())

    # --- users
    def test_humans_manage_users(self):
        code, r = self.j("POST", "/api/users", {"username": "builder-2", "kind": "agent"}, role="human")
        self.assertEqual(code, 201, r)
        tok = r["token"]
        self.assertTrue(tok.startswith("at_"))
        me = self.j("GET", "/api/me", role=None, token=tok)[1]
        self.assertEqual((me["username"], me["kind"]), ("builder-2", "agent"))
        self.assertIn("How we work", me["guidance"])
        # Identity comes from the token: the old X-Agent header changes nothing.
        t = self.j("POST", "/api/tickets", {"title": "from builder-2"}, role=None, token=tok,
                   headers={"X-Agent": "someone-else"})[1]
        self.assertEqual(t["created_by"], "builder-2")
        # A second human can manage users too.
        h2 = self.j("POST", "/api/users", {"username": "sam", "kind": "human"}, role="human")[1]["token"]
        self.assertEqual(self.j("GET", "/api/users", role=None, token=h2)[0], 200)
        # Agents can't manage users or see the Users page.
        self.assertEqual(self.j("POST", "/api/users", {"username": "sneaky"})[0], 403)
        self.assertEqual(self.j("GET", "/api/users")[0], 403)
        self.assertEqual(self.call("GET", "/users")[0], 403)
        # Names: unique (any case) and sane.
        self.assertEqual(self.j("POST", "/api/users", {"username": "BUILDER-2"}, role="human")[0], 409)
        self.assertEqual(self.j("POST", "/api/users", {"username": "bad name!"}, role="human")[0], 400)
        self.assertEqual(self.j("POST", "/api/users", {"username": "x", "kind": "robot"}, role="human")[0], 400)
        # A new token replaces the old one; disabling locks the user out until re-enabled.
        tok2 = self.j("POST", "/api/users/builder-2/token", role="human")[1]["token"]
        self.assertEqual(self.j("GET", "/api/me", role=None, token=tok)[0], 401)
        self.assertEqual(self.j("GET", "/api/me", role=None, token=tok2)[0], 200)
        self.assertTrue(self.j("PATCH", "/api/users/builder-2", {"disabled": True}, role="human")[1]["disabled"])
        self.assertEqual(self.j("GET", "/api/me", role=None, token=tok2)[0], 401)
        self.j("PATCH", "/api/users/builder-2", {"disabled": False}, role="human")
        self.assertEqual(self.j("GET", "/api/me", role=None, token=tok2)[0], 200)
        # Tokens are never stored or listed, only their hashes.
        users = self.j("GET", "/api/users", role="human")[1]["users"]
        self.assertNotIn("token_hash", json.dumps(users))
        raw = (Path(self.srv.store.db_path)).read_bytes() + Path(str(self.srv.store.db_path) + "-wal").read_bytes()
        self.assertNotIn(tok2.encode(), raw)
        html = self.call("GET", "/users", role="human")[1].decode()
        self.assertIn("builder-2", html)

    def test_last_human_cant_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = at.Store(Path(tmp) / "data", [tmp])
            store.add_user("only", "human", "test")
            with self.assertRaises(at.ApiError):
                store.set_disabled("only", True)
            store.add_user("second", "human", "test")
            self.assertTrue(store.set_disabled("only", True)["disabled"])

    def test_old_keys_become_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            (data / "keys.json").write_text(json.dumps({"human": "old-human-key", "agent": "old-agent-key"}))
            srv = at.make_server("127.0.0.1", 0, data, [tmp], human_name="Frank")
            try:
                self.assertEqual(srv.migrated, ["Frank", "agent"])
                self.assertFalse((data / "keys.json").exists())
                self.assertEqual(srv.store.user_by_token("old-human-key")["username"], "Frank")
                self.assertEqual(srv.store.user_by_token("old-agent-key")["kind"], "agent")
            finally:
                srv.server_close()

    def test_briefing(self):
        # Public: no token needed, and it explains the workflow.
        code, body, h = self.call("GET", "/api", role=None)
        self.assertEqual(code, 200)
        text = body.decode()
        self.assertIn("text/markdown", h["Content-Type"])
        for must in ("backlog -> ready -> in_progress -> review -> done", "/api/next?claim=1", "Needs a decision",
                     "Authorization: Bearer"):
            self.assertIn(must, text)
        self.assertNotIn("Right now", text)
        # With a token: who's asking and what's theirs.
        t = self.new("ready", priority=1, title="Briefing example")
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "in_progress"})
        mine = self.call("GET", "/api")[1].decode()
        self.assertIn("signed in as **tester** (agent)", mine)
        self.assertIn(f"{t['id']}: Briefing example", mine)
        # An agent calling the base URL gets the briefing, not the web page; a human gets the page.
        self.assertIn("# Agent tickets: a briefing", self.call("GET", "/")[1].decode())
        self.assertIn("<html", self.call("GET", "/", role="human")[1].decode())
        # Errors point at the briefing.
        self.assertIn("GET /api", self.j("GET", "/api/tickets", role=None)[1]["error"])

    # --- assignment
    def test_assignment(self):
        a1 = self.j("POST", "/api/users", {"username": "agent-a", "kind": "agent"}, role="human")[1]["token"]
        a2 = self.j("POST", "/api/users", {"username": "agent-b", "kind": "agent"}, role="human")[1]["token"]
        # Clear the queue so only this test's tickets are ready.
        for t in self.j("GET", "/api/tickets?status=ready", role="human")[1]["tickets"]:
            self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "backlog"}, role="human")
        for_b = self.new("ready", priority=1, title="for b", assignee="agent-b")
        loose = self.new("ready", priority=3, title="anyone")
        for_a = self.new("ready", priority=3, title="for a", assignee="AGENT-A")
        self.assertEqual(for_a["assignee"], "agent-a")  # stored with the user's own spelling
        self.assertEqual(self.j("POST", "/api/tickets", {"title": "x", "assignee": "nobody"}, role="human")[0], 404)
        # agent-a gets its own ticket first (despite lower priority), then the unassigned one, never b's.
        n1 = self.j("GET", "/api/next?claim=1", role=None, token=a1)[1]
        self.assertEqual(n1["ticket"]["id"], for_a["id"])
        self.assertIn("How we work", n1["guidance"])
        n2 = self.j("GET", "/api/next?claim=1", role=None, token=a1)[1]["ticket"]
        self.assertEqual((n2["id"], n2["assignee"]), (loose["id"], "agent-a"))
        self.assertIsNone(self.j("GET", "/api/next?claim=1", role=None, token=a1)[1]["ticket"])
        # Someone else's ticket: comment yes, change no.
        self.assertEqual(self.j("PATCH", f"/api/tickets/{for_b['id']}", {"status": "in_progress"}, role=None, token=a1)[0], 403)
        self.assertEqual(self.j("POST", f"/api/tickets/{for_b['id']}/comments", {"body": "fyi"}, role=None, token=a1)[0], 201)
        # Agents assign only themselves (or no one).
        self.assertEqual(self.j("PATCH", f"/api/tickets/{n2['id']}", {"assignee": "agent-b"}, role=None, token=a1)[0], 403)
        self.assertIsNone(self.j("PATCH", f"/api/tickets/{n2['id']}", {"assignee": None}, role=None, token=a1)[1]["assignee"])
        # Starting an unassigned ticket assigns it.
        self.assertEqual(self.j("PATCH", f"/api/tickets/{n2['id']}", {"status": "ready"}, role=None, token=a1)[0], 200)
        started = self.j("PATCH", f"/api/tickets/{n2['id']}", {"status": "in_progress"}, role=None, token=a2)[1]
        self.assertEqual(started["assignee"], "agent-b")
        # Filters, the timeline and the briefing.
        mine = self.j("GET", "/api/tickets?assignee=me", role=None, token=a2)[1]["tickets"]
        self.assertEqual({t["id"] for t in mine}, {for_b["id"], n2["id"]})
        self.assertIn("assigned it to <b>agent-b</b>", self.call("GET", f"/t/{for_b['id']}", role="human")[1].decode())
        brief = self.call("GET", "/api", role=None, token=a2)[1].decode()
        self.assertIn(f"{for_b['id']}: for b", brief)  # assigned and ready
        self.assertIn(f"{n2['id']}: anyone", brief)    # in progress

    def test_guidance(self):
        tok = self.j("POST", "/api/users", {"username": "agent-g", "kind": "agent"}, role="human")[1]["token"]
        self.assertEqual(self.j("PUT", "/api/guidance", {"guidance": "x"}, role=None, token=tok)[0], 403)
        self.assertEqual(self.j("PUT", "/api/guidance", {"guidance": "## House\nComment after every step."}, role="human")[0], 200)
        self.j("PATCH", "/api/users/agent-g", {"guidance": "Be extra terse."}, role="human")
        g = self.j("GET", "/api/guidance", role=None, token=tok)[1]["guidance"]
        self.assertIn("Comment after every step.", g)
        self.assertIn("Be extra terse.", g)
        # Other agents get the house rules without agent-g's notes.
        self.assertNotIn("Be extra terse.", self.j("GET", "/api/guidance")[1]["guidance"])
        brief = self.call("GET", "/api", role=None, token=tok)[1].decode()
        self.assertLess(brief.index("Be extra terse."), brief.index("## Signing in"))  # up top
        self.assertIn("Comment after every step.", self.call("GET", "/api", role=None)[1].decode())  # public too
        html = self.call("GET", "/guidance", role="human")[1].decode()
        self.assertIn("Be extra terse.", html)
        self.assertEqual(self.call("GET", "/guidance", role=None, token=tok)[0], 403)
        # Reset to the default.
        self.j("PUT", "/api/guidance", {"guidance": None}, role="human")
        self.assertIn("How we work", self.j("GET", "/api/guidance")[1]["guidance"])

    # --- proposals, epics, dependencies
    def test_proposals(self):
        tok = self.j("POST", "/api/users", {"username": "agent-p", "kind": "agent"}, role="human")[1]["token"]
        code, p = self.j("POST", "/api/tickets", {"title": "Idea"}, role=None, token=tok)
        self.assertEqual((code, p["status"], p["proposed"]), (201, "backlog", 1))
        self.assertEqual(self.new("backlog")["proposed"], 0)  # humans' tickets aren't proposals
        self.assertIn(p["id"], {t["id"] for t in self.j("GET", "/api/tickets?proposed=1", role="human")[1]["tickets"]})
        self.assertIn("Proposed by agents", self.call("GET", "/backlog", role="human")[1].decode())
        # Only humans decide.
        self.assertEqual(self.j("POST", f"/api/tickets/{p['id']}/accept", {}, role=None, token=tok)[0], 403)
        a = self.j("POST", f"/api/tickets/{p['id']}/accept", {"ready": True}, role="human")[1]
        self.assertEqual((a["status"], a["proposed"]), ("ready", 0))
        self.assertEqual(self.j("POST", f"/api/tickets/{p['id']}/accept", {}, role="human")[0], 409)  # not a proposal now
        # Declined: done, marked declined.
        q = self.j("POST", "/api/tickets", {"title": "Bad idea"}, role=None, token=tok)[1]
        d = self.j("POST", f"/api/tickets/{q['id']}/decline", {}, role="human")[1]
        self.assertEqual((d["status"], d["declined"], d["proposed"]), ("done", 1, 0))
        self.assertIn("Declined", self.call("GET", "/done", role="human")[1].decode())
        # Over one kept-alive connection, as a browser does: the body must be read.
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1])
        hdr = {"Authorization": "Bearer " + self.keys["human"], "Content-Type": "application/json"}
        q2 = self.j("POST", "/api/tickets", {"title": "Worse idea"}, role=None, token=tok)[1]
        conn.request("POST", f"/api/tickets/{q2['id']}/decline", body=b"{}", headers=hdr)
        self.assertEqual(conn.getresponse().read() and 200, 200)
        conn.request("GET", "/api/me", headers=hdr)
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()
        # A human promoting a proposal by hand accepts it too.
        r = self.j("POST", "/api/tickets", {"title": "Another"}, role=None, token=tok)[1]
        self.assertEqual(self.j("PATCH", f"/api/tickets/{r['id']}", {"status": "ready"}, role="human")[1]["proposed"], 0)

    def test_epics(self):
        tok = self.j("POST", "/api/users", {"username": "agent-e", "kind": "agent"}, role="human")[1]["token"]
        # An agent proposes a plan: the epic, then its stories in order.
        e = self.j("POST", "/api/tickets", {"title": "New login", "kind": "epic"}, role=None, token=tok)[1]
        s1 = self.j("POST", "/api/tickets", {"title": "Schema", "epic_id": e["id"].lower()}, role=None, token=tok)[1]
        s2 = self.j("POST", "/api/tickets", {"title": "Form", "epic_id": e["id"], "depends_on": [s1["id"]]},
                    role=None, token=tok)[1]
        self.assertEqual((s1["epic_id"], s2["blocked_by"]), (e["id"], [s1["id"]]))
        self.assertEqual(self.j("POST", "/api/tickets", {"title": "x", "epic_id": s1["id"]}, role="human")[0], 400)
        self.assertEqual(self.j("POST", "/api/tickets", {"title": "x", "kind": "epic", "epic_id": e["id"]},
                                role="human")[0], 400)  # no nesting
        self.assertEqual(self.j("POST", "/api/tickets", {"title": "x", "kind": "saga"}, role="human")[0], 400)
        # Accepting the epic accepts its proposed tickets; "ready" makes the tickets ready, not the epic.
        self.j("POST", f"/api/tickets/{e['id']}/accept", {"ready": True}, role="human")
        got = self.j("GET", f"/api/tickets/{e['id']}", role="human")[1]
        self.assertEqual((got["status"], got["proposed"]), ("backlog", 0))
        self.assertEqual({t["id"]: (t["status"], t["proposed"]) for t in got["tickets"]},
                         {s1["id"]: ("ready", 0), s2["id"]: ("ready", 0)})
        self.assertEqual(got["progress"]["total"], 2)
        # Epics aren't worked: agents only comment on them, and claims never return one.
        self.j("PATCH", f"/api/tickets/{e['id']}", {"status": "ready", "assignee": "agent-e"}, role="human")
        self.assertEqual(self.j("PATCH", f"/api/tickets/{e['id']}", {"status": "in_progress"}, role=None, token=tok)[0], 403)
        self.assertEqual(self.j("POST", f"/api/tickets/{e['id']}/comments", {"body": "plan ok"}, role=None, token=tok)[0], 201)
        self.j("PATCH", f"/api/tickets/{s1['id']}", {"assignee": "agent-e"}, role="human")
        n = self.j("GET", "/api/next?claim=1", role=None, token=tok)[1]["ticket"]
        self.assertEqual(n["id"], s1["id"])
        # Declining an epic declines its proposed tickets.
        e2 = self.j("POST", "/api/tickets", {"title": "Plan B", "kind": "epic"}, role=None, token=tok)[1]
        c = self.j("POST", "/api/tickets", {"title": "B1", "epic_id": e2["id"]}, role=None, token=tok)[1]
        self.j("POST", f"/api/tickets/{e2['id']}/decline", {}, role="human")
        self.assertEqual(self.j("GET", f"/api/tickets/{c['id']}", role="human")[1]["declined"], 1)
        # Pages.
        self.assertIn("Schema", self.call("GET", f"/t/{e['id']}", role="human")[1].decode())
        self.assertIn("New login", self.call("GET", "/", role="human")[1].decode())
        self.assertIn(f'value="{e["id"]}" selected', self.call("GET", f"/new?epic={e['id']}", role="human")[1].decode())

    def test_dependencies_block(self):
        tok = self.j("POST", "/api/users", {"username": "agent-d", "kind": "agent"}, role="human")[1]["token"]
        first = self.new("ready", title="first", assignee="agent-d", priority=3)
        then = self.new("ready", title="then", assignee="agent-d", priority=1, depends_on=first["id"])
        self.assertEqual(then["blocked_by"], [first["id"]])
        # Blocked for everyone, humans included; comments still fine.
        for role, tk in (("human", None), (None, tok)):
            code, err = self.j("PATCH", f"/api/tickets/{then['id']}", {"status": "in_progress"}, role=role, token=tk)
            self.assertEqual(code, 409)
            self.assertIn(first["id"], err["error"])
        self.assertEqual(self.j("POST", f"/api/tickets/{then['id']}/comments", {"body": "idea"}, role=None, token=tok)[0], 201)
        # /api/next skips it despite its higher priority.
        self.assertEqual(self.j("GET", "/api/next?claim=1", role=None, token=tok)[1]["ticket"]["id"], first["id"])
        self.assertNotEqual((self.j("GET", "/api/next", role=None, token=tok)[1]["ticket"] or {}).get("id"), then["id"])
        self.assertIn("Blocked", self.call("GET", f"/t/{then['id']}", role="human")[1].decode())
        # Loops are refused; so are dependencies on epics.
        self.assertEqual(self.j("POST", f"/api/tickets/{first['id']}/deps", {"depends_on": then["id"]}, role="human")[0], 409)
        self.assertEqual(self.j("POST", f"/api/tickets/{first['id']}/deps", {"depends_on": first["id"]}, role="human")[0], 409)
        third = self.new("backlog", title="third")
        self.j("POST", f"/api/tickets/{third['id']}/deps", {"depends_on": then["id"]}, role="human")
        self.assertEqual(self.j("POST", f"/api/tickets/{first['id']}/deps", {"depends_on": third["id"]}, role="human")[0], 409)
        epic = self.new("backlog", title="ep", kind="epic")
        self.assertEqual(self.j("POST", f"/api/tickets/{third['id']}/deps", {"depends_on": epic["id"]}, role="human")[0], 400)
        # Done predecessor: unblocked, and claimable.
        self.j("PATCH", f"/api/tickets/{first['id']}", {"status": "review"}, role=None, token=tok)
        self.j("PATCH", f"/api/tickets/{first['id']}", {"status": "done"}, role="human")
        self.assertEqual(self.j("GET", "/api/next?claim=1", role=None, token=tok)[1]["ticket"]["id"], then["id"])
        got = self.j("GET", f"/api/tickets/{first['id']}", role="human")[1]
        self.assertEqual([u["id"] for u in got["unblocks"]], [then["id"]])
        # Removing a dependency unblocks too (the human's override).
        self.assertEqual(self.j("GET", f"/api/tickets/{third['id']}", role="human")[1]["blocked_by"], [then["id"]])
        d = self.j("DELETE", f"/api/tickets/{third['id']}/deps/{then['id'].lower()}", role="human")[1]
        self.assertEqual((d["blocked_by"], d["depends_on"]), ([], []))
        # Dependencies survive a backup and restore.
        self.j("POST", f"/api/tickets/{third['id']}/deps", {"depends_on": first["id"]}, role="human")
        with tempfile.TemporaryDirectory() as tmp:
            at.export_archive(self.srv.store, Path(tmp) / "b.tar.gz")
            other = at.Store(Path(tmp) / "data", [tmp])
            at.import_archive(other, Path(tmp) / "b.tar.gz")
            self.assertEqual([x["id"] for x in other.detail(third["id"])["depends_on"]], [first["id"]])

    # --- chat
    def test_chat(self):
        tok = self.j("POST", "/api/users", {"username": "agent-c", "kind": "agent"}, role="human")[1]["token"]
        other = self.j("POST", "/api/users", {"username": "agent-o", "kind": "agent"}, role="human")[1]["token"]
        t = self.new("ready", title="chatty", assignee="agent-c")
        # Frank starts a conversation with the agent.
        code, c = self.j("POST", "/api/chat", {"members": ["AGENT-C"], "body": "Quick q: KAMI or ZZZZ? see https://x.io/KAMI"},
                         role="human")
        self.assertEqual((code, sorted(c["members"]), c["name"]), (201, ["Frank", "agent-c"], "agent-c"))
        # Any ticket response tells the agent, until it answers.
        code, got, h = self.call("PATCH", f"/api/tickets/{t['id']}", {"status": "in_progress"}, role=None, token=tok)
        got = json.loads(got)
        self.assertEqual((got["status"], got["chat"]["unread"], h["X-Chat-Unread"]), ("in_progress", 1, "1"))
        self.assertIn("Answer chat", got["chat"]["notice"])
        self.assertEqual(self.j("GET", "/api/next", role=None, token=tok)[1]["chat"]["conversations"][0]["id"], c["id"])
        self.assertIn("unread chat message", self.call("GET", "/api", role=None, token=tok)[1].decode())
        self.assertEqual(self.j("GET", "/api/me", role=None, token=tok)[1]["chat_unread"], 1)
        unread = self.j("GET", "/api/chat/unread", role=None, token=tok)[1]
        self.assertEqual((unread["unread"], unread["conversations"][0]["messages"][0]["author"]), (1, "Frank"))
        self.assertNotIn("chat", unread)  # chat endpoints don't nag about themselves
        # It answers, with a file; the notice is gone, and Frank now has one unread.
        m = self.j("POST", f"/api/chat/{c['id']}/messages", {"body": "ZZZZ. Notes attached."}, role=None, token=tok)[1]
        f = self.j("POST", f"/api/chat/{c['id']}/files?filename=notes.md&message_id={m['id']}", raw=b"# Notes\n- a\n",
                   role=None, token=tok)[1]
        self.assertEqual(f["mime"], "text/markdown")
        self.assertNotIn("chat", self.j("GET", f"/api/tickets/{t['id']}", role=None, token=tok)[1])
        self.assertEqual(self.call("GET", f["url"], role="human")[1], b"# Notes\n- a\n")
        self.assertIn("sandbox", self.call("GET", f["url"], role="human")[2]["Content-Security-Policy"])
        self.assertEqual(self.j("GET", "/api/chat", role="human")[1]["unread"], 1)
        # Humans aren't nagged in JSON; the page shows a badge, and opening the conversation reads it.
        self.assertNotIn("chat", self.j("GET", f"/api/tickets/{t['id']}", role="human")[1])
        self.assertIn('Chat<span class=n>1</span>', self.call("GET", "/", role="human")[1].decode())
        html = self.call("GET", f"/chat/{c['id']}", role="human")[1].decode()
        self.assertIn("notes.md", html)
        self.assertIn('class="pv"', html)  # markdown preview
        self.assertNotIn('<a href="/t/ZZZZ">', html)  # not a ticket
        self.assertIn('href="https://x.io/KAMI"', html)
        self.assertEqual(self.j("GET", "/api/chat", role="human")[1]["unread"], 0)
        # Ticket ids that exist are linked.
        self.j("POST", f"/api/chat/{c['id']}/messages", {"body": f"See {t['id']}"}, role="human")
        self.assertIn(f'<a href="/t/{t["id"]}">', self.call("GET", f"/chat/{c['id']}", role="human")[1].decode())
        # Polling fragment: only newer messages.
        _, frag, h = self.call("GET", f"/chat/{c['id']}/since/{m['id']}", role="human")
        self.assertIn(f'See <a href="/t/{t["id"]}">', frag.decode())
        self.assertNotIn("ZZZZ. Notes", frag.decode())
        # One conversation per pair; groups need a title or more people.
        self.assertEqual(self.j("POST", "/api/chat", {"members": ["Frank"]}, role=None, token=tok)[1]["id"], c["id"])
        self.assertEqual(self.j("POST", "/api/chat", {"members": []}, role=None, token=tok)[0], 400)
        self.assertEqual(self.j("POST", "/api/chat", {"members": ["nobody"]}, role="human")[0], 404)
        g = self.j("POST", "/api/chat", {"title": "Planning", "members": ["agent-c"]}, role="human")[1]
        self.assertNotEqual(g["id"], c["id"])
        # Wide open: anyone reads and posts; adding someone tells them (once).
        self.assertEqual(self.j("GET", f"/api/chat/{c['id']}", role=None, token=other)[0], 200)
        self.j("POST", f"/api/chat/{g['id']}/members", {"username": "agent-o"}, role="human")
        self.assertEqual(self.j("GET", "/api/chat/unread", role=None, token=other)[1]["unread"], 1)
        self.j("POST", f"/api/chat/{g['id']}/read", {}, role=None, token=other)
        self.assertEqual(self.j("GET", "/api/me", role=None, token=other)[1]["chat_unread"], 0)
        # A ticket from a message.
        new = self.call("GET", f"/new?from_message={m['id']}", role="human")[1].decode()
        self.assertIn("&gt; ZZZZ. Notes attached.", new)
        # Pages, and the cookie rule.
        self.assertEqual(self.call("GET", "/chat", role="human")[0], 200)
        self.assertEqual(self.call("GET", "/chat/99999", role="human")[0], 404)
        # Backups carry conversations, messages and their files.
        with tempfile.TemporaryDirectory() as tmp:
            at.export_archive(self.srv.store, Path(tmp) / "b.tar.gz")
            restored = at.Store(Path(tmp) / "data", [tmp])
            at.import_archive(restored, Path(tmp) / "b.tar.gz")
            msgs = restored.messages(c["id"], internal=True)
            self.assertEqual(msgs[1]["files"][0]["filename"], "notes.md")
            self.assertTrue((restored.files / msgs[1]["files"][0]["_path"]).is_file())

    def test_old_database_upgrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            import sqlite3
            db = sqlite3.connect(data / "tickets.db")
            db.executescript("""CREATE TABLE tickets (id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 2, checklist_path TEXT, created_by TEXT,
              created_at REAL NOT NULL, updated_at REAL NOT NULL);
              INSERT INTO tickets VALUES ('KAMI', 'old', '', 'ready', 2, NULL, 'frank', 1, 1);
              PRAGMA user_version = 1;""")
            db.close()
            store = at.Store(data, [tmp])
            self.assertIsNone(store.ticket("KAMI")["assignee"])
            self.assertEqual(store.db().execute("PRAGMA user_version").fetchone()[0], at.SCHEMA_VERSION)

    # --- pages
    def test_pages_render(self):
        t = self.new("review", title="Check <b>escaping</b>", description="See https://example.com")
        for path in ("/", "/backlog", "/done", "/new", f"/t/{t['id']}", "/backlog?q=x"):
            code, body, _ = self.call("GET", path, role="human")
            self.assertEqual(code, 200, path)
            self.assertNotIn(b"<b>escaping</b>", body)
        html = self.call("GET", f"/t/{t['id']}", role="human")[1].decode()
        self.assertIn('href="https://example.com"', html)
        self.assertIn("Approve", html)
        self.assertIn("Needs your review", self.call("GET", "/", role="human")[1].decode())
        self.assertEqual(self.call("GET", "/t/ZZZZ", role="human")[0], 404)


if __name__ == "__main__":
    unittest.main()
