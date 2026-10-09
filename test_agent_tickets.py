"""Tests for agent_tickets.py: a real server on a temporary folder, driven
over HTTP. Standard library only:  python3 -m unittest -v"""
import io
import json
import re
import tarfile
import tempfile
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
    def test_ids_are_sayable(self):
        t = self.new()
        self.assertRegex(t["id"], r"^([BDFGHKLMNPRSTVZ][AEIOU]){2}[BDFGHKLMNPRSTVZ]?$")
        self.assertEqual(self.j("GET", "/api/tickets/" + t["id"].lower())[1]["id"], t["id"])

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
        self.assertEqual(me, {"username": "builder-2", "kind": "agent"})
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
        self.assertIn(b"# Agent tickets API", self.call("GET", "/api")[1])


if __name__ == "__main__":
    unittest.main()
