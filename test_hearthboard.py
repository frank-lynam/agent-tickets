"""Tests for hearthboard.py: a real server on a temporary folder, driven
over HTTP. Standard library only:  python3 -m unittest -v"""
import io
import os
import subprocess
import sys
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

import hearthboard as at


class TicketsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.allowed = root / "work"
        cls.allowed.mkdir()
        cls.srv = at.make_server("127.0.0.1", 0, root / "data", max_upload_mb=1)
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
        got = self.j("GET", f"/api/tickets/{b['id']}", role="human")[1]
        self.assertEqual(sorted(d["id"] for d in got["depends_on"] + got["done_dependencies"]),
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
        code, err = self.j("PATCH", f"/api/tickets/{tid}", {"status": "review"})  # no comment yet: refused
        self.assertEqual(code, 409)
        self.assertIn('"Done" or "Needs a decision"', err["error"])
        self.j("POST", f"/api/tickets/{tid}/comments", {"body": "Done. All green."})
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
            code, r = self.j("POST", "/api/next", {})
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

    def test_upload_names(self):
        t = self.new("in_progress")
        c = self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "files"})[1]
        # A form upload (curl -F): the real name and bytes, and its fields.
        boundary = "XyZ123"
        form = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"comment_id\"\r\n\r\n{c['id']}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"notes.md\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n# Notes\n- a\n\r\n--{boundary}--\r\n").encode()
        code, a = self.j("POST", f"/api/tickets/{t['id']}/attachments", raw=form,
                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual((code, a["filename"], a["mime"], a["comment_id"]), (201, "notes.md", "text/markdown", c["id"]))
        self.assertEqual(self.call("GET", a["url"])[1], b"# Notes\n- a\n")
        # Raw bytes named by a header.
        self.assertEqual(self.j("POST", f"/api/tickets/{t['id']}/attachments", raw=b"x",
                                headers={"X-Filename": "x.log"})[1]["filename"], "x.log")
        # No name at all: refused, with directions (no more files called "upload").
        code, err = self.j("POST", f"/api/tickets/{t['id']}/attachments", raw=b"x")
        self.assertEqual(code, 400)
        self.assertIn("curl -F file=@", err["error"])
        self.assertIn("?filename=", err["error"])
        code, err = self.j("POST", f"/api/tickets/{t['id']}/attachments",
                           raw=f"--{boundary}\r\nContent-Disposition: form-data; name=\"x\"\r\n\r\n1\r\n--{boundary}--\r\n".encode(),
                           headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(code, 400)
        self.assertIn("no file in it", err["error"])

    def test_upload_limit(self):
        t = self.new()
        self.assertEqual(self.j("POST", f"/api/tickets/{t['id']}/attachments?filename=big.bin",
                                raw=b"x" * ((1 << 20) + 1))[0], 413)

    # --- checklists
    def test_checklist(self):
        tok = self.j("POST", "/api/users", {"username": "agent-k", "kind": "agent"}, role="human")[1]["token"]
        t = self.new("in_progress", assignee="agent-k")
        put = lambda text, token=tok, role=None, ctype="text/markdown": self.j(
            "PUT", f"/api/tickets/{t['id']}/checklist", role=role, token=token, raw=text.encode(),
            headers={"Content-Type": ctype})
        code, got = put("# Plan\n- [x] scaffold\n- [ ] tests\n* [X] docs\n")
        self.assertEqual((code, got["checklist"]["done"], got["checklist"]["total"]), (200, 2, 3), got)
        self.assertEqual(got["checklist"]["updated_by"], "agent-k")
        self.assertEqual(got["checklist"]["items"][0], {"text": "# Plan", "note": True})
        # Each upload replaces the last; JSON works too; the same text again changes nothing.
        put("- [x] scaffold\n- [x] tests")
        self.assertEqual(put(json.dumps({"text": "- [x] scaffold\n- [x] tests"}), ctype="application/json")[1]
                         ["checklist"]["total"], 2)
        events = [e for e in self.j("GET", f"/api/tickets/{t['id']}", role="human")[1]["events"] if e["field"] == "checklist"]
        self.assertEqual([(e["old"], e["new"]) for e in events], [(None, "2/3"), ("2/3", "2/2")])
        # Only the agent working it, while it's in progress.
        self.assertEqual(put("- [ ] x", token=None, role="human")[0], 403)  # humans don't edit it
        self.assertEqual(put("- [ ] x", token=self.keys["agent"])[0], 403)  # another agent
        self.assertEqual(put("   ")[0], 400)
        self.assertEqual(put("x" * (at.CHECKLIST_MAX + 1))[0], 413)
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Done."}, role=None, token=tok)
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "review"}, role=None, token=tok)
        self.assertEqual(put("- [ ] late")[0], 409)
        # Lists and pages show progress; the old path field is refused with directions.
        listed = [x for x in self.j("GET", "/api/tickets?status=review", role="human")[1]["tickets"] if x["id"] == t["id"]][0]
        self.assertEqual((listed["checklist"]["done"], "items" in listed["checklist"]), (2, False))
        html = self.call("GET", f"/t/{t['id']}", role="human")[1].decode()
        self.assertIn("2 of 2 done", html)
        self.assertIn("reported the checklist: 2/2 done", html)
        self.assertIn("☑ 2/2", self.call("GET", "/", role="human")[1].decode())
        for method, path, body in (("POST", "/api/tickets", {"title": "x", "checklist_path": "/etc/passwd"}),
                                   ("PATCH", f"/api/tickets/{t['id']}", {"checklist_path": "/etc/passwd"})):
            code, err = self.j(method, path, body, role="human")
            self.assertEqual(code, 400)
            self.assertIn("unknown field checklist_path", err["error"])
        self.assertIsNone(self.j("GET", f"/api/tickets/{self.new()['id']}")[1]["checklist"])

    # --- backups
    def test_export_and_restore(self):
        t = self.new("in_progress", title="Back me up")
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
                self.assertEqual((doc["format"], doc["version"]), ("hearthboard-export", 1))
                self.assertIn("files/", " ".join(tar.getnames()))
            # Restore into a fresh data folder: every row and file comes back as it was.
            fresh = at.Store(Path(tmp) / "data")
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

    def test_move_to_a_new_instance(self):
        """Export from one instance on the command line, import into a brand-new one: tickets, chats and users
        all come across, and everyone's existing token still signs them in there."""
        hb = str(Path(__file__).resolve().parent / "hearthboard.py")
        with tempfile.TemporaryDirectory() as tmp:
            def serve(data):                 # its own board (the shared test board's file is read raw elsewhere)
                srv = at.make_server("127.0.0.1", 0, Path(tmp) / data)
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                return srv, f"http://127.0.0.1:{srv.server_address[1]}"
            def call(url, method, path, key, body=None):
                req = urllib.request.Request(url + path, method=method, data=json.dumps(body).encode() if body else None,
                                             headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
                with urllib.request.urlopen(req) as r:
                    return json.loads(r.read())
            old, url = serve("old")
            try:
                human = old.store.add_user("Frank", "human", "test")[1]
                agent = old.store.add_user("tester", "agent", "test")[1]
                t = call(url, "POST", "/api/tickets", human, {"title": "Moving house", "status": "ready"})
                call(url, "POST", "/api/chat", human, {"members": ["tester"], "body": "see you on the new box"})
                out = subprocess.run([sys.executable, hb, "--data", tmp + "/old", "--export", tmp + "/out/"],
                                     capture_output=True, text=True, check=True).stdout
            finally:
                old.shutdown(); old.server_close()
            self.assertIn("users", out)
            backup = next(Path(tmp, "out").glob("hearthboard-*.tar.gz"))
            subprocess.run([sys.executable, hb, "--data", tmp + "/new", "--import", str(backup)],
                           capture_output=True, text=True, check=True)
            new, url = serve("new")
            try:
                self.assertEqual(call(url, "GET", "/api/me", agent)["username"], "tester")
                self.assertEqual(call(url, "GET", "/api/me", human)["username"], "Frank")
                self.assertEqual(call(url, "GET", f"/api/tickets/{t['id']}", human)["title"], "Moving house")
                self.assertIn("see you on the new box", json.dumps(call(url, "GET", "/api/chat", human)))
            finally:
                new.shutdown(); new.server_close()

    def test_export_folder_keeps_the_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(3):
                dest, counts, pruned = at.export_to(self.srv.store, tmp + "/", keep=2)
                (Path(tmp) / f"hearthboard-0000000{i}-000000.tar.gz").write_bytes(b"old")
            names = sorted(p.name for p in Path(tmp).glob("hearthboard-*.tar.gz"))
            self.assertEqual(len(names), 3)  # 2 kept by the last prune + the old one written after it
            self.assertTrue(any(n.startswith("hearthboard-2") for n in names))
            # A folder that doesn't exist yet, given with a trailing slash, is created.
            dest, _, _ = at.export_to(self.srv.store, tmp + "/new/sub/", keep=5)
            self.assertTrue((Path(tmp) / "new" / "sub").is_dir())
            self.assertEqual(dest.parent, Path(tmp) / "new" / "sub")

    def test_bad_backups_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = at.Store(Path(tmp) / "data")
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
                at.import_archive(store, archive({"format": "hearthboard-export", "version": 99, "tables": {}}))
            # A file trying to escape the data folder is skipped.
            ok = {"format": "hearthboard-export", "version": 1, "tables": {}}
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
            store = at.Store(Path(tmp) / "data")
            store.add_user("only", "human", "test")
            with self.assertRaises(at.ApiError):
                store.set_disabled("only", True)
            store.add_user("second", "human", "test")
            self.assertTrue(store.set_disabled("only", True)["disabled"])

    def test_briefing(self):
        # Public: no token needed, and it explains the workflow.
        code, body, h = self.call("GET", "/api", role=None)
        self.assertEqual(code, 200)
        text = body.decode()
        self.assertIn("text/markdown", h["Content-Type"])
        for must in ("backlog -> ready -> in_progress -> review -> done", "POST /api/next", "Needs a decision",
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
        self.assertIn("# Hearthboard: a briefing", self.call("GET", "/")[1].decode())
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
        n1 = self.j("POST", "/api/next", {}, role=None, token=a1)[1]
        self.assertEqual(n1["ticket"]["id"], for_a["id"])
        self.assertIn("How we work", n1["guidance"])
        n2 = self.j("POST", "/api/next", {}, role=None, token=a1)[1]["ticket"]
        self.assertEqual((n2["id"], n2["assignee"]), (loose["id"], "agent-a"))
        self.assertIsNone(self.j("POST", "/api/next", {}, role=None, token=a1)[1]["ticket"])
        # Someone else's ticket: comment yes, change no.
        self.assertEqual(self.j("PATCH", f"/api/tickets/{for_b['id']}", {"status": "in_progress"}, role=None, token=a1)[0], 403)
        self.assertEqual(self.j("POST", f"/api/tickets/{for_b['id']}/comments", {"body": "fyi"}, role=None, token=a1)[0], 201)
        # Agents assign only themselves (or no one).
        self.assertEqual(self.j("PATCH", f"/api/tickets/{n2['id']}", {"assignee": "agent-b"}, role=None, token=a1)[0], 403)
        self.assertIsNone(self.j("PATCH", f"/api/tickets/{n2['id']}", {"assignee": None}, role=None, token=a1)[1]["assignee"])
        # Starting an unassigned ticket assigns it. (Releasing one needs a word about where you got to.)
        self.j("POST", f"/api/tickets/{n2['id']}/comments", {"body": "Releasing: not started."}, role=None, token=a1)
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
        # Accepting the epic accepts its proposed tickets; "ready" makes the tickets ready, and so the epic active.
        self.j("POST", f"/api/tickets/{e['id']}/accept", {"ready": True}, role="human")
        got = self.j("GET", f"/api/tickets/{e['id']}", role="human")[1]
        self.assertEqual((got["status"], got["epic_state"], got["proposed"]), ("in_progress", "active", 0))
        self.assertEqual({t["id"]: (t["status"], t["proposed"]) for t in got["tickets"]},
                         {s1["id"]: ("ready", 0), s2["id"]: ("ready", 0)})
        self.assertEqual(got["progress"]["total"], 2)
        # Epics aren't worked: agents only comment on them, and claims never return one.
        self.j("PATCH", f"/api/tickets/{e['id']}", {"assignee": "agent-e"}, role="human")
        self.assertEqual(self.j("PATCH", f"/api/tickets/{e['id']}", {"status": "review"}, role=None, token=tok)[0], 403)
        self.assertEqual(self.j("PATCH", f"/api/tickets/{e['id']}", {"status": "done"}, role="human")[0], 400)
        self.assertEqual(self.j("POST", f"/api/tickets/{e['id']}/comments", {"body": "plan ok"}, role=None, token=tok)[0], 201)
        self.j("PATCH", f"/api/tickets/{s1['id']}", {"assignee": "agent-e"}, role="human")
        n = self.j("POST", "/api/next", {}, role=None, token=tok)[1]["ticket"]
        self.assertEqual(n["id"], s1["id"])
        # Declining an epic declines its proposed tickets.
        e2 = self.j("POST", "/api/tickets", {"title": "Plan B", "kind": "epic"}, role=None, token=tok)[1]
        c = self.j("POST", "/api/tickets", {"title": "B1", "epic_id": e2["id"]}, role=None, token=tok)[1]
        self.j("POST", f"/api/tickets/{e2['id']}/decline", {}, role="human")
        self.assertEqual(self.j("GET", f"/api/tickets/{c['id']}", role="human")[1]["declined"], 1)
        # Pages.
        self.assertIn("Schema", self.call("GET", f"/t/{e['id']}", role="human")[1].decode())
        self.assertIn("New login", self.call("GET", "/", role="human")[1].decode())
        self.assertIn(f'<input type="hidden" id="nt-epic" value="{e["id"]}">', self.call("GET", f"/new?epic={e['id']}", role="human")[1].decode())

    def test_dependencies_block(self):
        tok = self.j("POST", "/api/users", {"username": "agent-d", "kind": "agent"}, role="human")[1]["token"]
        first = self.new("ready", title="first", assignee="agent-d", priority=3)
        then = self.new("ready", title="then", assignee="agent-d", priority=1, depends_on=first["id"])
        self.assertEqual(then["blocked_by"], [first["id"]])
        # An agent can't start it; comments are still fine.
        code, err = self.j("PATCH", f"/api/tickets/{then['id']}", {"status": "in_progress"}, role=None, token=tok)
        self.assertEqual(code, 409)
        self.assertIn(first["id"], err["error"])
        self.assertEqual(self.j("POST", f"/api/tickets/{then['id']}/comments", {"body": "idea"}, role=None, token=tok)[0], 201)
        # /api/next skips it despite its higher priority.
        self.assertEqual(self.j("POST", "/api/next", {}, role=None, token=tok)[1]["ticket"]["id"], first["id"])
        self.assertNotIn(then["id"], [w["id"] for w in self.j("GET", "/api/wait?timeout=1", role=None, token=tok)[1]["tickets"]])
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
        self.assertEqual(self.j("POST", "/api/next", {}, role=None, token=tok)[1]["ticket"]["id"], then["id"])
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
            other = at.Store(Path(tmp) / "data")
            at.import_archive(other, Path(tmp) / "b.tar.gz")
            got = other.detail(third["id"])
            self.assertEqual([x["id"] for x in got["depends_on"] + got["done_dependencies"]], [first["id"]])

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
        self.assertEqual((got["status"], got["chat"]["count"], h["X-Chat-Inbox"]), ("in_progress", 1, "1"))
        self.assertIn("Answer chat", got["chat"]["notice"])
        self.assertEqual(self.j("GET", f"/api/tickets/{t['id']}", role=None, token=tok)[1]["chat"]["conversations"][0]["id"], c["id"])
        self.assertIn("chat message to answer", self.call("GET", "/api", role=None, token=tok)[1].decode())
        self.assertEqual(self.j("GET", "/api/me", role=None, token=tok)[1]["chat_inbox"], 1)
        unread = self.j("GET", "/api/chat/inbox", role=None, token=tok)[1]
        self.assertEqual((unread["count"], unread["conversations"][0]["messages"][0]["author"]), (1, "Frank"))
        self.assertNotIn("chat", unread)  # chat endpoints don't nag about themselves
        # It answers, with a file; the notice is gone, and Frank now has one unread.
        m = self.j("POST", f"/api/chat/{c['id']}/messages", {"body": "ZZZZ. Notes attached."}, role=None, token=tok)[1]
        f = self.j("POST", f"/api/chat/{c['id']}/attachments?filename=notes.md&message_id={m['id']}", raw=b"# Notes\n- a\n",
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
        poll = self.j("GET", f"/chat/{c['id']}/since/{m['id']}", role="human")[1]
        self.assertIn(f'See <a href="/t/{t["id"]}">', poll["html"])
        self.assertNotIn("ZZZZ. Notes", poll["html"])
        self.assertIn("Not read yet by agent-c", poll["receipt"])
        # One conversation per pair; groups need a title or more people.
        self.assertEqual(self.j("POST", "/api/chat", {"members": ["Frank"]}, role=None, token=tok)[1]["id"], c["id"])
        self.assertEqual(self.j("POST", "/api/chat", {"members": []}, role=None, token=tok)[0], 400)
        self.assertEqual(self.j("POST", "/api/chat", {"members": ["nobody"]}, role="human")[0], 404)
        g = self.j("POST", "/api/chat", {"title": "Planning", "members": ["agent-c"]}, role="human")[1]
        self.assertNotEqual(g["id"], c["id"])
        # Wide open: anyone reads and posts; adding someone tells them (once).
        self.assertEqual(self.j("GET", f"/api/chat/{c['id']}", role=None, token=other)[0], 200)
        self.j("POST", f"/api/chat/{g['id']}/members", {"username": "agent-o"}, role="human")
        self.assertEqual(self.j("GET", "/api/chat/inbox", role=None, token=other)[1]["count"], 1)
        self.j("POST", f"/api/chat/{g['id']}/read", {}, role=None, token=other)
        self.assertEqual(self.j("GET", "/api/me", role=None, token=other)[1]["chat_inbox"], 0)
        # A ticket from a message.
        new = self.call("GET", f"/new?from_message={m['id']}", role="human")[1].decode()
        self.assertIn("&gt; ZZZZ. Notes attached.", new)
        # Pages, and the cookie rule.
        self.assertEqual(self.call("GET", "/chat", role="human")[0], 200)
        self.assertEqual(self.call("GET", "/chat/99999", role="human")[0], 404)
        # Backups carry conversations, messages and their files.
        with tempfile.TemporaryDirectory() as tmp:
            at.export_archive(self.srv.store, Path(tmp) / "b.tar.gz")
            restored = at.Store(Path(tmp) / "data")
            at.import_archive(restored, Path(tmp) / "b.tar.gz")
            msgs = restored.messages(c["id"], internal=True)
            self.assertEqual(msgs[1]["attachments"][0]["filename"], "notes.md")
            self.assertTrue((restored.files / msgs[1]["attachments"][0]["_path"]).is_file())

    # --- wiki
    def test_wiki(self):
        tok = self.j("POST", "/api/users", {"username": "agent-w", "kind": "agent"}, role="human")[1]["token"]
        mk = lambda **d: self.j("POST", "/api/wiki", d, role="human")
        code, sec = mk(title="Projects", body="Everything per project.")
        self.assertEqual((code, sec["path"]), (201, "projects"))
        fp = mk(title="Fitness Pizza", parent="projects", body="# Repo\nSee `~/workspace/fitness-pizza` and [[projects/fitness-pizza/deploy|deploy notes]].\n\n<script>x</script>")[1]
        self.assertEqual(fp["path"], "projects/fitness-pizza")
        dep = mk(title="Deploy", parent="projects/fitness-pizza", body="```\n./deploy.sh <prod>\n```")[1]
        self.assertEqual([c["path"] for c in dep["breadcrumbs"]], ["projects", "projects/fitness-pizza", "projects/fitness-pizza/deploy"])
        # Structure: siblings need distinct names (the same name elsewhere is fine); a parent must exist.
        self.assertEqual(mk(title="Deploy", parent="projects/fitness-pizza")[0], 409)
        self.assertEqual(mk(title="Deploy", parent="projects")[0], 201)
        self.assertEqual(mk(title="Orphan", parent="nowhere")[0], 404)
        self.assertEqual(mk(title="  ")[0], 400)
        # Agents read, and can't write.
        got = self.j("GET", "/api/wiki/Projects/fitness-pizza", role=None, token=tok)[1]
        self.assertEqual((got["title"], [c["path"] for c in got["children"]]), ("Fitness Pizza", ["projects/fitness-pizza/deploy"]))
        tree = self.j("GET", "/api/wiki", role=None, token=tok)[1]["tree"]
        self.assertIn("projects/fitness-pizza/deploy", json.dumps(tree))
        self.assertEqual(self.j("GET", "/api/wiki?q=deploy.sh", role=None, token=tok)[1]["results"][0]["path"],
                         "projects/fitness-pizza/deploy")
        for method, path, body in (("POST", "/api/wiki", {"title": "x"}), ("PATCH", "/api/wiki/projects", {"body": "x"}),
                                   ("DELETE", "/api/wiki/projects/deploy", None)):
            self.assertEqual(self.j(method, path, body, role=None, token=tok)[0], 403, path)
        self.assertEqual(self.call("GET", "/wiki-new", role=None, token=tok)[0], 403)
        # Edits keep history; rename and move change the path; no moving under itself.
        self.j("PATCH", "/api/wiki/projects/fitness-pizza", {"body": fp["body"] + "\nMore."}, role="human")
        moved = self.j("PATCH", "/api/wiki/projects/deploy", {"parent": "", "slug": "deploying"}, role="human")[1]
        self.assertEqual(moved["path"], "deploying")
        self.assertEqual(self.j("PATCH", "/api/wiki/projects", {"parent": "projects/fitness-pizza"}, role="human")[0], 409)
        self.assertEqual(len(self.srv.store.wiki_revisions(fp["id"])), 2)
        # Only leaf pages can be deleted.
        self.assertEqual(self.j("DELETE", "/api/wiki/projects/fitness-pizza", role="human")[0], 409)
        self.assertEqual(self.j("DELETE", "/api/wiki/deploying", role="human")[0], 200)
        self.assertEqual(self.j("GET", "/api/wiki/deploying", role="human")[0], 404)
        # Pages: text with headings, code, links; escaped; agents see no edit buttons.
        html = self.call("GET", "/wiki/projects/fitness-pizza", role="human")[1].decode()
        self.assertIn("<h2>Repo</h2>", html)
        self.assertIn("<code>~/workspace/fitness-pizza</code>", html)
        self.assertIn('<a class="wl" href="/wiki/projects/fitness-pizza/deploy">deploy notes</a>', html)
        self.assertIn('<code id="wlink">[[projects/fitness-pizza]]</code>', html)  # with a Copy link button
        self.assertIn("copyWikiLink", html)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("Add a page under this", html)
        self.assertNotIn("?edit=1", self.call("GET", "/wiki/projects/fitness-pizza", role=None, token=tok)[1].decode())
        self.assertIn("&lt;prod&gt;", self.call("GET", "/wiki/projects/fitness-pizza/deploy", role="human")[1].decode())
        for path in ("/wiki", "/wiki?q=pizza", "/wiki-new", "/wiki-new?parent=projects", "/wiki/projects?history=1",
                     "/wiki/projects/fitness-pizza?edit=1"):
            self.assertEqual(self.call("GET", path, role="human")[0], 200, path)
        rev = self.srv.store.wiki_revisions(fp["id"])[-1]["id"]
        self.assertIn("The version", self.call("GET", f"/wiki/projects/fitness-pizza?rev={rev}", role="human")[1].decode())
        # The briefing names the sections; backups restore a page moved under a newer one.
        brief = self.call("GET", "/api", role=None, token=tok)[1].decode()
        sections = [line for line in brief.splitlines() if line.startswith("Wiki sections:")]
        self.assertIn("Projects (`projects`)", sections[0])
        late = mk(title="Later section")[1]
        self.j("PATCH", "/api/wiki/projects", {"parent": late["path"]}, role="human")
        with tempfile.TemporaryDirectory() as tmp:
            at.export_archive(self.srv.store, Path(tmp) / "b.tar.gz")
            restored = at.Store(Path(tmp) / "data")
            at.import_archive(restored, Path(tmp) / "b.tar.gz")
            self.assertEqual(restored.wiki_page("later-section/projects/fitness-pizza/deploy")["title"], "Deploy")

    def test_wiki_search_and_links(self):
        mk = lambda **d: self.j("POST", "/api/wiki", d, role="human")[1]
        mk(title="Teams", body="Who does what.")
        mk(title="Kitchen Sink", parent="teams", body="Release notes go out after every deploy.")
        mk(title="Deploy", parent="teams/kitchen-sink", body="Run the release script, then check the dashboard.")
        mk(title="Deployment glossary", parent="teams", body="Words we use.")
        q = lambda words: [r["path"] for r in self.j("GET", "/api/wiki?q=" + words.replace(" ", "+"))[1]["results"]]
        # Every word, any order, counting the sections above; title matches first.
        self.assertEqual(q("sink deploy")[0], "teams/kitchen-sink/deploy")
        self.assertEqual(q("deploy kitchen")[0], "teams/kitchen-sink/deploy")
        self.assertIn("teams/kitchen-sink", q("release deploy"))
        self.assertEqual(q("dashboard script"), ["teams/kitchen-sink/deploy"])
        self.assertEqual(q("deploy zebra"), [])
        ranked = q("deploy")  # titles with the word come before pages that only mention it
        self.assertLess(ranked.index("teams/kitchen-sink/deploy"), ranked.index("teams/kitchen-sink"))
        self.assertLess(ranked.index("teams/deployment-glossary"), ranked.index("teams/kitchen-sink"))
        # [[links]] work in descriptions, comments and chat; the ticket lists what it links to.
        t = self.new("ready", description="Ship it. Steps: [[teams/kitchen-sink/deploy|the deploy page]].")
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Also [[Teams/Kitchen-Sink]] and [[teams/nope]]"},
               role="human")
        links = self.j("GET", f"/api/tickets/{t['id']}")[1]["wiki_links"]
        self.assertEqual(links, [{"path": "teams/kitchen-sink/deploy", "title": "Deploy"},
                                 {"path": "teams/kitchen-sink", "title": "Kitchen Sink"},
                                 {"path": "teams/nope", "title": None, "missing": True}])
        html = self.call("GET", f"/t/{t['id']}", role="human")[1].decode()
        self.assertIn('<a class="wl" href="/wiki/teams/kitchen-sink/deploy">the deploy page</a>', html)
        self.assertIn('<a class="wl" href="/wiki/teams/kitchen-sink">Teams/Kitchen-Sink</a>', html)
        self.assertIn("(no such page yet)", html)
        c = self.j("POST", "/api/chat", {"title": "links", "body": "see [[teams]] and https://x.io/[[a]]"}, role="human")[1]
        chat = self.call("GET", f"/chat/{c['id']}", role="human")[1].decode()
        self.assertIn('<a class="wl" href="/wiki/teams">teams</a>', chat)
        t2 = self.new(description="[[teams|<b>x</b>]]")
        self.assertNotIn("<b>x</b>", self.call("GET", f"/t/{t2['id']}", role="human")[1].decode())

    def test_assign_while_accepting_or_making_ready(self):
        tok = self.j("POST", "/api/users", {"username": "agent-r", "kind": "agent"}, role="human")[1]["token"]
        # A proposal, accepted straight to ready for agent-r.
        p = self.j("POST", "/api/tickets", {"title": "Idea"}, role=None, token=tok)[1]
        got = self.j("POST", f"/api/tickets/{p['id']}/accept", {"ready": True, "assignee": "AGENT-R"}, role="human")[1]
        self.assertEqual((got["status"], got["assignee"]), ("ready", "agent-r"))
        self.assertEqual(self.j("POST", f"/api/tickets/{self.j('POST', '/api/tickets', {'title': 'x'}, role=None, token=tok)[1]['id']}/accept",
                                {"assignee": "nobody"}, role="human")[0], 404)
        # An epic: its tickets get the assignee, the epic doesn't.
        e = self.j("POST", "/api/tickets", {"title": "Plan", "kind": "epic"}, role=None, token=tok)[1]
        k = self.j("POST", "/api/tickets", {"title": "Step", "epic_id": e["id"]}, role=None, token=tok)[1]
        self.j("POST", f"/api/tickets/{e['id']}/accept", {"assignee": "agent-r"}, role="human")
        self.assertIsNone(self.j("GET", f"/api/tickets/{e['id']}", role="human")[1]["assignee"])
        self.assertEqual(self.j("GET", f"/api/tickets/{k['id']}", role="human")[1]["assignee"], "agent-r")
        # Declining ignores it.
        d = self.j("POST", "/api/tickets", {"title": "Nah"}, role=None, token=tok)[1]
        self.assertEqual(self.j("POST", f"/api/tickets/{d['id']}/decline", {"assignee": "agent-r"}, role="human")[0], 400)
        self.assertIsNone(self.j("POST", f"/api/tickets/{d['id']}/decline", {}, role="human")[1]["assignee"])
        # Make ready for someone, in one step; the pages offer the picker.
        b = self.new("backlog", title="Later")
        got = self.j("PATCH", f"/api/tickets/{b['id']}", {"status": "ready", "assignee": "agent-r"}, role="human")[1]
        self.assertEqual((got["status"], got["assignee"]), ("ready", "agent-r"))
        b2 = self.new("backlog", title="Later still")
        self.assertIn(f'id="as-{b2["id"]}"', self.call("GET", "/backlog", role="human")[1].decode())
        page = self.call("GET", f"/t/{b2['id']}", role="human")[1].decode()
        self.assertIn(f'id="as-{b2["id"]}"', page)
        self.assertIn(f"readyFor('{b2['id']}')", page)

    def test_priority_up_top_and_on_accept(self):
        tok = self.j("POST", "/api/users", {"username": "agent-p2", "kind": "agent"}, role="human")[1]["token"]
        p = self.j("POST", "/api/tickets", {"title": "Urgent idea"}, role=None, token=tok)[1]
        self.assertIn(f'id="pr-{p["id"]}"', self.call("GET", "/backlog", role="human")[1].decode())
        self.assertIn(f'id="pr-{p["id"]}"', self.call("GET", f"/t/{p['id']}", role="human")[1].decode())
        got = self.j("POST", f"/api/tickets/{p['id']}/accept", {"ready": True, "priority": 1}, role="human")[1]
        self.assertEqual((got["status"], got["priority"]), ("ready", 1))
        bad = self.j("POST", "/api/tickets", {"title": "x"}, role=None, token=tok)[1]
        self.assertEqual(self.j("POST", f"/api/tickets/{bad['id']}/accept", {"priority": 7}, role="human")[0], 400)
        # An epic's priority applies to its proposed tickets too.
        e = self.j("POST", "/api/tickets", {"title": "Low plan", "kind": "epic"}, role=None, token=tok)[1]
        k = self.j("POST", "/api/tickets", {"title": "Low step", "epic_id": e["id"]}, role=None, token=tok)[1]
        self.j("POST", f"/api/tickets/{e['id']}/accept", {"priority": 3}, role="human")
        self.assertEqual(self.j("GET", f"/api/tickets/{k['id']}", role="human")[1]["priority"], 3)
        # On the ticket page it sits right under the description, not in the Edit form.
        html = self.call("GET", f"/t/{p['id']}", role="human")[1].decode()
        self.assertLess(html.index('class="desc md"'), html.index('id="prio"'))
        self.assertLess(html.index('id="prio"'), html.index("<h2>Activity</h2>"))
        self.assertNotIn('id="eprio"', html)
        self.assertIn("set the priority to <b>High</b>", html)

    def test_cancel(self):
        tok = self.j("POST", "/api/users", {"username": "agent-x", "kind": "agent"}, role="human")[1]["token"]
        t = self.new("in_progress", assignee="agent-x", title="Obsolete")
        page = self.call("GET", f"/t/{t['id']}", role="human")[1].decode()
        self.assertIn("Cancel ticket", page)
        self.assertEqual(self.j("POST", f"/api/tickets/{t['id']}/cancel", {}, role=None, token=tok)[0], 403)
        # The agent (which has handed it back) is woken and told to stop.
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Needs a decision."}, role=None, token=tok)
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "review"}, role=None, token=tok)
        threading.Timer(0.4, lambda: self.j("POST", f"/api/tickets/{t['id']}/cancel", {}, role="human")).start()
        woke = self.j("GET", "/api/wait?timeout=20", role=None, token=tok)[1]
        self.assertEqual(woke["tickets"][0]["why"], "Frank cancelled it: stop work on it")
        got = self.j("GET", f"/api/tickets/{t['id']}", role="human")[1]
        self.assertEqual((got["status"], got["cancelled"]), ("done", 1))
        self.assertEqual(self.j("POST", f"/api/tickets/{t['id']}/cancel", {}, role="human")[0], 409)  # already done
        page = self.call("GET", f"/t/{t['id']}", role="human")[1].decode()
        self.assertIn(">Cancelled</span>", page)
        self.assertIn("<b>cancelled</b> it", page)
        self.assertNotIn("Cancel ticket", page)
        self.assertIn("Cancelled</span>", self.call("GET", "/done", role="human")[1].decode())
        # Reopening puts it back in the backlog as an ordinary ticket.
        self.assertIn(f"setStatus('{t['id']}','backlog')", page)
        back = self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "backlog"}, role="human")[1]
        self.assertEqual((back["status"], back["cancelled"]), ("backlog", 0))
        # Proposals are declined, not cancelled.
        prop = self.j("POST", "/api/tickets", {"title": "idea"}, role=None, token=tok)[1]
        self.assertEqual(self.j("POST", f"/api/tickets/{prop['id']}/cancel", {}, role="human")[0], 409)
        # An epic takes its unfinished tickets with it; finished ones stay done, and it doesn't count them.
        e = self.new("backlog", title="Dropped plan", kind="epic")
        done = self.new("ready", title="already shipped", epic_id=e["id"])
        self.j("PATCH", f"/api/tickets/{done['id']}", {"status": "done"}, role="human")
        open_ = self.new("ready", title="never mind", epic_id=e["id"])
        after = self.new("backlog", title="waits on it")
        self.j("POST", f"/api/tickets/{after['id']}/deps", {"depends_on": open_["id"]}, role="human")
        got = self.j("POST", f"/api/tickets/{e['id']}/cancel", {}, role="human")[1]
        self.assertEqual((got["epic_state"], got["cancelled"]), ("cancelled", 1))
        self.assertEqual(self.j("GET", f"/api/tickets/{open_['id']}", role="human")[1]["cancelled"], 1)
        self.assertEqual(self.j("GET", f"/api/tickets/{done['id']}", role="human")[1]["cancelled"], 0)
        self.assertEqual(self.j("GET", f"/api/tickets/{after['id']}", role="human")[1]["blocked_by"], [])  # unblocked

    def test_live_ticket_page(self):
        tok = self.j("POST", "/api/users", {"username": "agent-l", "kind": "agent"}, role="human")[1]["token"]
        t = self.new("in_progress", assignee="agent-l")
        first = self.j("GET", f"/t/{t['id']}/live", role="human")[1]
        page = self.call("GET", f"/t/{t['id']}", role="human")[1].decode()
        self.assertIn(f'SIG = "{first["sig"]}"', page)
        self.assertIn('id="changed" hidden', page)
        self.assertIn("at-draft:", page)  # unsent comments are kept
        self.assertIn("enterSends('cbody', 'cf')", page)  # Enter sends, Shift+Enter is a new line
        self.assertIn("window.innerHeight * 0.8", page)  # text boxes grow to fit, up to 80% of the screen
        self.assertIn("function markBusy()", page)  # buttons show they're working
        self.assertIn("button.latest", page)  # a "Latest" button on long tickets and chats
        self.assertIn("button.busy::after", page)
        # A comment and a file arrive: new entries with ids; the ticket's own state hasn't changed.
        c = self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "progress"}, role=None, token=tok)[1]
        self.j("POST", f"/api/tickets/{t['id']}/attachments?filename=x.txt&comment_id={c['id']}", raw=b"hi",
               role=None, token=tok)
        self.j("PUT", f"/api/tickets/{t['id']}/checklist", role=None, token=tok, raw=b"- [x] a",
               headers={"Content-Type": "text/markdown"})
        now = self.j("GET", f"/t/{t['id']}/live", role="human")[1]
        self.assertIn(f'id="c{c["id"]}" data-v="1"', now["timeline"])
        self.assertEqual(now["sig"], first["sig"])
        self.assertNotEqual(now["checklist_v"], first["checklist_v"])
        self.assertIn("1 of 1 done", now["checklist"])
        # A status change does change it (the page shows "refresh").
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Done."}, role=None, token=tok)
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "review"}, role=None, token=tok)
        self.assertNotEqual(self.j("GET", f"/t/{t['id']}/live", role="human")[1]["sig"], first["sig"])
        # The dashboard checks every 10 seconds, swapping in place.
        dash = self.call("GET", "/", role="human")[1].decode()
        self.assertIn('<div id="dash">', dash)
        self.assertIn("10000", dash)
        self.assertNotIn("location.reload(); }, 60000", dash)

    def test_wait(self):
        tok = self.j("POST", "/api/users", {"username": "agent-z", "kind": "agent"}, role="human")[1]["token"]
        W = lambda path="/api/wait?timeout=20": self.j("GET", path, role=None, token=tok)[1]

        def later(fn, delay=0.4):
            threading.Timer(delay, fn).start()

        def waited(path="/api/wait?timeout=20"):
            t0 = time.time()
            return W(path), time.time() - t0
        # Nothing for it: times out.
        started = time.time()
        self.assertEqual(W("/api/wait?timeout=1")["reason"], "timeout")
        self.assertLess(time.time() - started, 4)
        # Work it already has comes back at once, as one list in the order to do it: priority first; within a
        # priority, started before not started, then nearest done (checklist), then oldest.
        low = self.new("in_progress", assignee="agent-z", title="low", priority=3)
        high = self.new("in_progress", assignee="agent-z", title="high", priority=1)
        queued = self.new("ready", assignee="agent-z", title="next up")  # normal priority
        near = self.new("in_progress", assignee="agent-z", title="low but nearly done", priority=3)
        self.j("PUT", f"/api/tickets/{near['id']}/checklist", role=None, token=tok, raw=b"- [x] a\n- [x] b\n- [ ] c",
               headers={"Content-Type": "text/markdown"})
        self.j("PUT", f"/api/tickets/{low['id']}/checklist", role=None, token=tok, raw=b"- [x] a\n- [ ] b\n- [ ] c",
               headers={"Content-Type": "text/markdown"})
        got, took = waited()
        self.assertEqual((got["reason"], [t["id"] for t in got["tickets"]]),
                         ("in_progress", [high["id"], queued["id"], near["id"], low["id"]]))
        self.assertTrue(got["tickets"][0]["why"].startswith("yours and in progress: keep working on it"))
        self.assertIn("you haven't commented since you started it", got["tickets"][0]["why"])
        self.assertEqual([t["progress"] for t in got["tickets"]], [None, None, "2/3", "1/3"])
        self.assertLess(took, 2)
        self.j("POST", f"/api/tickets/{near['id']}/comments", {"body": "Done."}, role=None, token=tok)
        self.j("PATCH", f"/api/tickets/{near['id']}", {"status": "review"}, role=None, token=tok)
        for t in (high, low):  # handed back, with a hand-off comment
            self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Done."}, role=None, token=tok)
            self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "review"}, role=None, token=tok)
        got = W()
        self.assertEqual((got["reason"], got["tickets"][0]["id"]), ("ready", queued["id"]))
        self.j("PATCH", f"/api/tickets/{queued['id']}", {"assignee": None}, role="human")
        # Blocked work doesn't count; unassigned ready work only with queue=1.
        stuck = self.new("ready", assignee="agent-z", title="waits on high", depends_on=high["id"])
        self.assertEqual(W("/api/wait?timeout=1")["reason"], "timeout")
        self.j("PATCH", f"/api/tickets/{stuck['id']}", {"status": "backlog"}, role="human")
        got = W("/api/wait?timeout=1&queue=1")
        self.assertEqual(got["reason"], "queue")
        self.assertIn(queued["id"], [t["id"] for t in got["tickets"]])
        for t in self.j("GET", "/api/tickets?status=ready", role="human")[1]["tickets"]:  # clear the shared queue
            self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "backlog"}, role="human")
        # While waiting: a reply on its (in review) ticket wakes it at once.
        later(lambda: self.j("POST", f"/api/tickets/{high['id']}/comments", {"body": "use plan B"}, role="human"))
        got, took = waited()
        self.assertEqual((got["reason"], got["tickets"][0]["id"], got["tickets"][0]["why"]),
                         ("comments", high["id"], "Frank commented (1 new): read it and reply on the ticket"))
        self.assertLess(took, 5)
        # It replies (which clears that); its own changes don't wake it; being assigned a ticket does.
        self.j("POST", f"/api/tickets/{high['id']}/comments", {"body": "Plan B it is."}, role=None, token=tok)
        other = self.new("backlog", title="next one")
        later(lambda: self.j("POST", f"/api/tickets/{high['id']}/comments", {"body": "on it"}, role=None, token=tok))
        later(lambda: self.j("PATCH", f"/api/tickets/{other['id']}", {"assignee": "agent-z"}, role="human"), 1.2)
        got, took = waited()
        self.assertEqual(([x["id"] for x in got["tickets"]], got["tickets"][0]["why"]), ([other["id"]], "Frank assigned it to you"))
        self.assertGreater(took, 1)
        # Sent back to in progress: from then on it's work to do.
        self.j("PATCH", f"/api/tickets/{high['id']}", {"status": "in_progress"}, role="human")
        self.assertEqual(W()["reason"], "in_progress")
        self.j("POST", f"/api/tickets/{high['id']}/comments", {"body": "Done, with plan B."}, role=None, token=tok)
        self.j("PATCH", f"/api/tickets/{high['id']}", {"status": "review"}, role=None, token=tok)
        # Chat comes before everything, and keeps returning at once until it's read.
        later(lambda: self.j("POST", "/api/chat", {"members": ["agent-z"], "body": "got a sec?"}, role="human"))
        got, _ = waited()
        self.assertEqual((got["reason"], got["chat"]["count"]), ("chat", 1))
        self.assertEqual(W()["reason"], "chat")
        for c in self.j("GET", "/api/chat/inbox", role=None, token=tok)[1]["conversations"]:
            self.j("POST", f"/api/chat/{c['id']}/read", {}, role=None, token=tok)
        self.assertEqual(W("/api/wait?timeout=1")["reason"], "chat")  # read isn't enough: a human asked
        for c in self.j("GET", "/api/chat/inbox", role=None, token=tok)[1]["conversations"]:
            self.j("POST", f"/api/chat/{c['id']}/messages", {"body": "Got it"}, role=None, token=tok)
        self.assertEqual(W("/api/wait?timeout=1")["reason"], "timeout")
        # The briefing and skill teach it.
        brief = self.call("GET", "/api", role=None)[1].decode()
        self.assertIn("## Staying reachable when you're idle", brief)
        self.assertIn("never for parking", brief)
        self.assertIn("**The loop: after every action, call it.**", brief)
        self.assertIn("*A recurring check, as a safety net:*", brief)
        self.assertIn("/api/wait", self.call("GET", "/api/skill", role=None, token=tok)[1].decode())

    def test_read_receipts(self):
        tok = self.j("POST", "/api/users", {"username": "agent-rr", "kind": "agent"}, role="human")[1]["token"]
        c = self.j("POST", "/api/chat", {"members": ["agent-rr"], "body": "ping"}, role="human")[1]
        html = self.call("GET", f"/chat/{c['id']}", role="human")[1].decode()
        self.assertIn("Not read yet by agent-rr", html)
        conv = self.j("GET", f"/api/chat/{c['id']}", role=None, token=tok)[1]  # GET changes nothing...
        self.assertIn("Not read yet by agent-rr", self.call("GET", f"/chat/{c['id']}", role="human")[1].decode())
        self.j("POST", f"/api/chat/{c['id']}/read", {}, role=None, token=tok)  # ...marking it read does
        conv = self.j("GET", f"/api/chat/{c['id']}", role=None, token=tok)[1]
        self.assertEqual(conv["read_up_to"]["Frank"], conv["messages"][-1]["id"])
        html = self.call("GET", f"/chat/{c['id']}", role="human")[1].decode()
        self.assertIn("Read by agent-rr", html)
        self.assertNotIn("Not read yet", html)
        self.assertEqual(self.j("GET", f"/api/chat/{c['id']}", role="human")[1]["read_up_to"]["agent-rr"],
                         conv["messages"][-1]["id"])

    def test_formatted_text(self):
        body = "## Plan\nstep one\n   - indented\n\n```\nname   ms\nopen  420\n```\nRun `npm test`. **Don't** <script>x</script> **<b>y</b>**"
        t = self.new(description=body)
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": body}, role="human")
        c = self.j("POST", "/api/chat", {"title": "fmt", "body": body}, role="human")[1]
        for path in (f"/t/{t['id']}", f"/chat/{c['id']}"):
            html = self.call("GET", path, role="human")[1].decode()
            self.assertIn("<h3>Plan</h3>", html, path)
            self.assertIn("<p>step one</p><ul><li>indented</li></ul>", html, path)  # an indented "-" line is a list
            self.assertIn("<pre>name   ms\nopen  420</pre>", html, path)  # exact spacing, monospace
            self.assertIn('<button type="button" class="copy" aria-label="Copy">Copy</button><pre>name', html, path)
            self.assertIn("<code>npm test</code>", html, path)
            self.assertIn("<b>Don&#x27;t</b>", html, path)
            self.assertNotIn("<script>x</script>", html, path)
            self.assertNotIn("<b>y</b>", html, path)
        self.assertEqual(self.call("GET", f"/t/{t['id']}", role="human")[1].decode().count("<h3>Plan</h3>"), 2)
        # Agents are told about it, and to write legibly.
        self.assertIn("## Formatting what you write", self.call("GET", "/api", role=None)[1].decode())
        self.assertIn("No walls of text", self.call("GET", "/api/skill", role="agent")[1].decode())

    def test_epic_lifecycle(self):
        e = self.new("backlog", title="Lifecycle", kind="epic")
        a = self.new("backlog", title="part a", epic_id=e["id"])
        b = self.new("backlog", title="part b", epic_id=e["id"])
        state = lambda: self.j("GET", f"/api/tickets/{e['id']}", role="human")[1]["epic_state"]
        self.assertEqual(state(), "backlog")  # nothing made ready yet
        self.j("PATCH", f"/api/tickets/{a['id']}", {"status": "ready"}, role="human")
        self.assertEqual(state(), "active")
        self.assertIn("the epic is now <b>Active</b>", self.call("GET", f"/t/{e['id']}", role="human")[1].decode())
        for t in (a, b):
            self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "done"}, role="human")
        self.assertEqual(state(), "complete")
        self.assertIn(e["id"], [t["id"] for t in self.j("GET", "/api/tickets?status=done&kind=epic", role="human")[1]["tickets"]])
        # More work added: active again; declined tickets don't hold it open.
        c = self.new("ready", title="part c", epic_id=e["id"])
        self.assertEqual(state(), "active")
        self.j("PATCH", f"/api/tickets/{c['id']}", {"epic_id": None}, role="human")  # moved out
        self.assertEqual(state(), "complete")
        # Its page has no status buttons, and the backlog page no longer lists it.
        page = self.call("GET", f"/t/{e['id']}", role="human")[1].decode()
        self.assertNotIn("Close epic", page)
        self.assertIn(">Complete</span>", page)
        self.assertNotIn(f"/t/{e['id']}\"", self.call("GET", "/backlog", role="human")[1].decode())
        # Its page lists tickets most complete first, declined last.
        d = self.new("backlog", title="part d", epic_id=e["id"])
        r = self.new("review", title="part r", epic_id=e["id"])
        x = self.j("POST", "/api/tickets", {"title": "part x", "epic_id": e["id"]}, role="agent")[1]
        self.j("POST", f"/api/tickets/{x['id']}/decline", {}, role="human")
        order = [t["title"] for t in self.j("GET", f"/api/tickets/{e['id']}", role="human")[1]["tickets"]]
        self.assertEqual(order, ["part a", "part b", "part r", "part d", "part x"])
        html = self.call("GET", f"/t/{e['id']}", role="human")[1].decode()
        self.assertLess(html.index("part b"), html.index("part r"))
        self.assertLess(html.index("part r"), html.index("part d"))
        # Out-of-step epics (from before this rule) are fixed when the server starts.
        with self.srv.store.db() as db:
            db.execute("UPDATE tickets SET status = 'backlog' WHERE id = ?", (e["id"],))
        at.Store(self.srv.store.dir)
        self.assertEqual(state(), "active")  # parts d and r are still open

    def test_nav_and_settings(self):
        home = self.call("GET", "/", role="human")[1].decode()
        nav = home[home.index("<nav"):home.index("</nav>")]
        for link in ('href="/"', 'href="/chat"', 'href="/wiki"', 'href="/new"', 'href="/settings"'):
            self.assertIn(link, nav)
        for gone in ('href="/users"', 'href="/guidance"', 'href="/backlog"', 'href="/done"', 'id="theme"'):
            self.assertNotIn(gone, nav)
        self.assertIn('id="navx"', home)  # collapses
        self.assertIn('<a href="/backlog">Backlog (', home)
        self.assertIn('<a href="/done">Done</a>', home)
        self.assertIn('class="on">Dashboard', self.call("GET", "/backlog", role="human")[1].decode())
        settings = self.call("GET", "/settings", role="human")[1].decode()
        for t in ("light", "dark", "sepia", "midnight", "forest", "synthwave", "contrast", "goth"):
            self.assertIn(f'data-t="{t}"', settings)
        self.assertIn('href="/users"', settings)
        self.assertIn('href="/guidance"', settings)
        self.assertIn(":root[data-theme=midnight]{--bg:#0d1321", settings)
        agent = self.call("GET", "/settings", role="agent")[1].decode()
        self.assertEqual(self.call("GET", "/settings", role="agent")[0], 200)
        self.assertNotIn('href="/users"', agent)

    def test_tts_mode(self):
        self.assertEqual(self.j("GET", "/api/me/prefs", role="human")[1], {"tts": False})
        self.assertNotIn('<html lang="en" data-tts="1">', self.call("GET", "/", role="human")[1].decode())
        self.assertEqual(self.j("PATCH", "/api/me/prefs", {"tts": True}, role="human")[1], {"tts": True})
        self.assertEqual(self.j("PATCH", "/api/me/prefs", {"loud": True}, role="human")[0], 400)
        self.assertIn('<html lang="en" data-tts="1">', self.call("GET", "/", role="human")[1].decode())
        self.assertIn('id="tts" style="width:22px;height:22px" checked', self.call("GET", "/settings", role="human")[1].decode())
        self.assertEqual(self.j("GET", "/api/me", role="human")[1]["prefs"], {"tts": True})
        # It's per user: the agent's pages aren't affected.
        self.assertNotIn('<html lang="en" data-tts="1">', self.call("GET", "/settings", role="agent")[1].decode())
        self.j("PATCH", "/api/me/prefs", {"tts": False}, role="human")

    def test_guest(self):
        guest = self.j("POST", "/api/users", {"username": "visitor", "kind": "guest"}, role="human")[1]["token"]
        t = self.new("ready", title="visible to guests")
        c = self.j("POST", "/api/chat", {"title": "open room", "body": "hi"}, role="human")[1]
        self.j("POST", "/api/wiki", {"title": "Guest readable"}, role="human")
        g = lambda m, path, body=None: self.j(m, path, body, role=None, token=guest)
        # Reads work.
        for path in ("/api/tickets", f"/api/tickets/{t['id']}", "/api/chat", f"/api/chat/{c['id']}", "/api/wiki",
                     "/api/wiki/guest-readable", "/api/me", "/api/wait?timeout=1"):
            self.assertEqual(g("GET", path)[0], 200, path)
        for path in ("/", f"/t/{t['id']}", "/backlog", "/done", "/chat", f"/chat/{c['id']}", "/wiki", "/settings"):
            self.assertEqual(self.call("GET", path, role=None, token=guest)[0], 200, path)
        # Changes don't.
        for m, path, body in (("POST", "/api/tickets", {"title": "x"}), ("PATCH", f"/api/tickets/{t['id']}", {"status": "done"}),
                              ("POST", f"/api/tickets/{t['id']}/comments", {"body": "x"}),
                              ("POST", f"/api/tickets/{t['id']}/cancel", {}), ("POST", f"/api/chat/{c['id']}/messages", {"body": "x"}),
                              ("POST", "/api/chat", {"title": "x"}), ("POST", "/api/wiki", {"title": "x"}),
                              ("POST", "/api/next", {})):
            self.assertEqual(g(m, path, body)[0], 403, path)
        self.assertEqual(self.call("GET", "/new", role=None, token=guest)[0], 403)
        self.assertEqual(self.call("GET", "/users", role=None, token=guest)[0], 403)
        # Their own display settings are theirs.
        self.assertEqual(g("PATCH", "/api/me/prefs", {"tts": True})[0], 200)
        # Guests aren't assignable or chat members, and aren't offered as either.
        self.assertEqual(self.j("PATCH", f"/api/tickets/{t['id']}", {"assignee": "visitor"}, role="human")[0], 400)
        self.assertEqual(self.j("POST", "/api/chat", {"members": ["visitor"]}, role="human")[0], 400)
        self.assertNotIn('value="visitor"', self.call("GET", f"/t/{t['id']}", role="human")[1].decode())
        # Their pages say so and hide the forms.
        page = self.call("GET", f"/t/{t['id']}", role=None, token=guest)[1].decode()
        self.assertIn('data-role="guest"', page)
        self.assertIn("Read-only guest", page)
        self.assertIn('<option value="guest">Guest (read-only)</option>', self.call("GET", "/users", role="human")[1].decode())

    def test_new_page_kind_first(self):
        html = self.call("GET", "/new", role="human")[1].decode()
        self.assertLess(html.index('class="kinds"'), html.index('id="nt-title"'))  # the kind comes first
        self.assertEqual(len(re.findall(r'class="[^"]*task-only', html)), 3)
        # An epic created with ticket-only fields ignores them: backlog, no assignee.
        e = self.j("POST", "/api/tickets", {"title": "Plan", "kind": "epic", "status": "ready", "assignee": "tester"},
                   role="human")[1]
        self.assertEqual((e["status"], e["assignee"]), ("backlog", None))

    def test_what_now(self):
        a1 = self.j("POST", "/api/users", {"username": "agent-w1", "kind": "agent"}, role="human")[1]["token"]
        a2 = self.j("POST", "/api/users", {"username": "agent-w2", "kind": "agent"}, role="human")[1]["token"]
        t = self.new("ready", title="clear it up", assignee="agent-w1", priority=1)
        say = lambda tok: self.j("GET", f"/api/tickets/{t['id']}", role=None, token=tok)[1]["what_now"]
        self.assertTrue(say(a1).startswith("Assigned to you and ready: start it"))
        self.assertTrue(say(a2).startswith("Ready, but assigned to agent-w1"))
        # Claiming: the ticket that comes back says it's yours to work, now.
        got = self.j("POST", "/api/next", {}, role=None, token=a1)[1]["ticket"]
        self.assertEqual(got["id"], t["id"])
        self.assertTrue(got["what_now"].startswith("This ticket is yours and in progress: you are working on it now"))
        self.assertEqual(say(a2), "agent-w1 is working on this: you can only comment.")
        # Lists, updates and other states.
        listed = [x for x in self.j("GET", "/api/tickets?status=in_progress", role=None, token=a1)[1]["tickets"] if x["id"] == t["id"]]
        self.assertIn("yours and in progress", listed[0]["what_now"])
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Done."}, role=None, token=a1)
        self.assertIn("waiting for a human", self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "review"}, role=None, token=a1)[1]["what_now"])
        waiting = self.new("ready", title="after it", depends_on=t["id"])
        self.assertTrue(self.j("GET", f"/api/tickets/{waiting['id']}", role=None, token=a2)[1]["what_now"].startswith(f"Blocked until {t['id']} is done"))
        self.assertNotIn("what_now", self.j("GET", f"/api/tickets/{t['id']}", role="human")[1])  # only agents get it
        brief = self.call("GET", "/api", role=None)[1].decode()
        self.assertIn('**Never stop because a ticket is "already in progress".**', brief)
        self.assertIn("If it's\n  assigned to you, that's you", brief)

    def test_done_dependencies_dont_block(self):
        tok = self.j("POST", "/api/users", {"username": "agent-dd", "kind": "agent"}, role="human")[1]["token"]
        first = self.new("ready", title="first step")
        second = self.new("ready", title="second step")
        then = self.new("ready", title="after both", depends_on=f"{first['id']}, {second['id']}")
        got = self.j("GET", f"/api/tickets/{then['id']}", role=None, token=tok)[1]
        self.assertEqual((got["blocked"], sorted(d["id"] for d in got["depends_on"]), got["done_dependencies"]),
                         (True, sorted([first["id"], second["id"]]), []))
        self.j("PATCH", f"/api/tickets/{first['id']}", {"status": "done"}, role="human")
        got = self.j("GET", f"/api/tickets/{then['id']}", role=None, token=tok)[1]
        self.assertEqual((got["blocked"], [d["id"] for d in got["depends_on"]], [d["id"] for d in got["done_dependencies"]]),
                         (True, [second["id"]], [first["id"]]))
        self.j("PATCH", f"/api/tickets/{second['id']}", {"status": "done"}, role="human")
        got = self.j("GET", f"/api/tickets/{then['id']}", role=None, token=tok)[1]
        self.assertEqual((got["blocked"], got["depends_on"], len(got["done_dependencies"])), (False, [], 2))
        self.assertFalse(got["what_now"].startswith("Blocked"))
        # Everyone sees the same shape.
        human = self.j("GET", f"/api/tickets/{then['id']}", role="human")[1]
        self.assertEqual((human["depends_on"], len(human["done_dependencies"]), human["blocked"]), ([], 2, False))
        self.assertIn("**A\npredecessor that's done no longer blocks anything.**", self.call("GET", "/api", role=None)[1].decode())

    def test_chat_until_replied(self):
        tok = self.j("POST", "/api/users", {"username": "agent-rp", "kind": "agent"}, role="human")[1]["token"]
        other = self.j("POST", "/api/users", {"username": "agent-rq", "kind": "agent"}, role="human")[1]["token"]
        me = lambda: self.j("GET", "/api/me", role=None, token=tok)[1]["chat_inbox"]
        c = self.j("POST", "/api/chat", {"members": ["agent-rp"], "body": "Can you check the deploy?"}, role="human")[1]
        self.assertEqual(me(), 1)
        # Reading it (opening the conversation, or marking it read) doesn't clear it.
        self.j("GET", f"/api/chat/{c['id']}", role=None, token=tok)
        self.j("POST", f"/api/chat/{c['id']}/read", {}, role=None, token=tok)
        notice = self.j("GET", "/api/tickets", role=None, token=tok)[1]["chat"]
        self.assertEqual((notice["count"], notice["conversations"][0]["needs_reply"]), (1, True))
        self.assertIn("until you reply there", notice["notice"])
        self.assertEqual(self.j("GET", "/api/chat/inbox", role=None, token=tok)[1]["conversations"][0]["needs_reply"], True)
        # Humans still see it as read (the receipt), whatever the agent owes.
        self.assertIn("Read by agent-rp", self.call("GET", f"/chat/{c['id']}", role="human")[1].decode())
        # Replying clears it; a new human message brings it back.
        self.j("POST", f"/api/chat/{c['id']}/messages", {"body": "On it"}, role=None, token=tok)
        self.assertEqual(me(), 0)
        self.j("POST", f"/api/chat/{c['id']}/messages", {"body": "Thanks!"}, role="human")
        self.assertEqual(me(), 1)
        self.j("POST", f"/api/chat/{c['id']}/messages", {"body": "Got it"}, role=None, token=tok)
        # Another agent's message, and an "(added ...)" note, only need reading: no reply loops between agents.
        d = self.j("POST", "/api/chat", {"members": ["agent-rp"], "body": "fyi: tests are green"}, role=None, token=other)[1]
        self.assertEqual(me(), 1)
        self.j("POST", f"/api/chat/{d['id']}/read", {}, role=None, token=tok)
        self.assertEqual(me(), 0)
        g = self.j("POST", "/api/chat", {"title": "team"}, role="human")[1]
        self.j("POST", f"/api/chat/{g['id']}/members", {"username": "agent-rp"}, role="human")
        self.assertEqual(me(), 1)
        self.j("POST", f"/api/chat/{g['id']}/read", {}, role=None, token=tok)
        self.assertEqual(me(), 0)

    def test_dependency_suggestions(self):
        e = self.new("backlog", title="Suggest plan", kind="epic")
        a = self.new("ready", title="suggest me", epic_id=e["id"])
        done = self.new("ready", title="finished one", epic_id=e["id"])
        self.j("PATCH", f"/api/tickets/{done['id']}", {"status": "done"}, role="human")
        c = self.new("ready", title="the one with deps", epic_id=e["id"])
        page = self.call("GET", f"/t/{c['id']}", role="human")[1].decode()
        line = [l for l in page.splitlines() if l.startswith("const DEP_CANDIDATES = ")][0]
        cands = {x["id"]: x for x in json.loads(line[len("const DEP_CANDIDATES = "):-1])}
        self.assertEqual(cands[a["id"]]["epic"], e["id"])
        self.assertNotIn(done["id"], cands)  # finished tickets aren't offered
        self.assertNotIn(e["id"], cands)     # nor epics
        self.assertIn("depPicker($('dep')", page)
        self.assertIn("depPicker($('nt-deps')", self.call("GET", "/new", role="human")[1].decode())

    def test_claiming_and_commenting(self):
        tok = self.j("POST", "/api/users", {"username": "agent-cc", "kind": "agent"}, role="human")[1]["token"]
        first = self.new("ready", title="cc first", assignee="agent-cc", priority=1)
        second = self.new("ready", title="cc second", assignee="agent-cc", priority=1)
        # POST takes a ticket; GET is refused with directions.
        code, err = self.j("GET", "/api/next", role=None, token=tok)
        self.assertEqual(code, 405)
        self.assertIn("POST /api/next", err["error"])
        got = self.j("POST", "/api/next", {}, role=None, token=tok)[1]
        self.assertEqual((got["ticket"]["id"], got["ticket"]["status"]), (first["id"], "in_progress"))
        self.assertNotIn("in_progress", got)  # nothing else under way
        # Taking more while one is under way: it says so.
        more = self.j("POST", "/api/next", {}, role=None, token=tok)[1]
        self.assertEqual(more["ticket"]["id"], second["id"])
        self.assertEqual([t["id"] for t in more["in_progress"]], [first["id"]])
        self.assertIn("finish those first", more["note"])
        # Silent so far: the nudge is in what_now, and a hand-back is refused until it comments.
        self.assertIn("you haven't commented since you started it", self.j("GET", f"/api/tickets/{first['id']}", role=None, token=tok)[1]["what_now"])
        self.assertEqual(self.j("PATCH", f"/api/tickets/{first['id']}", {"status": "review"}, role=None, token=tok)[0], 409)
        # A checklist upload without a comment comes back with a reminder; after a comment, it doesn't.
        put = lambda text: self.j("PUT", f"/api/tickets/{first['id']}/checklist", role=None, token=tok, raw=text.encode(),
                                  headers={"Content-Type": "text/markdown"})[1]
        self.assertIn("reminder", put("- [x] one\n- [ ] two"))
        self.j("POST", f"/api/tickets/{first['id']}/comments", {"body": "Step one done."}, role=None, token=tok)
        self.assertNotIn("reminder", put("- [x] one\n- [x] two"))
        self.assertNotIn("Note:", self.j("GET", f"/api/tickets/{first['id']}", role=None, token=tok)[1]["what_now"])
        # 20 quiet minutes later, it's nudged again.
        with self.srv.store.db() as db:
            db.execute("UPDATE comments SET created_at = created_at - 1500 WHERE ticket_id = ?", (first["id"],))
            db.execute("UPDATE events SET at = at - 1500 WHERE ticket_id = ?", (first["id"],))
        self.assertIn("your last comment here was 25 minutes ago", self.j("GET", f"/api/tickets/{first['id']}", role=None, token=tok)[1]["what_now"])
        self.j("POST", f"/api/tickets/{first['id']}/comments", {"body": "Done. Both steps in."}, role=None, token=tok)
        self.assertEqual(self.j("PATCH", f"/api/tickets/{first['id']}", {"status": "review"}, role=None, token=tok)[0], 200)
        # Everyone gets "blocked", humans too.
        self.assertIs(self.j("GET", f"/api/tickets/{first['id']}", role="human")[1]["blocked"], False)

    def test_review_wording(self):
        tok = self.j("POST", "/api/users", {"username": "agent-wd", "kind": "agent"}, role="human")[1]["token"]
        t = self.new("in_progress", assignee="agent-wd", title="word test")
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Needs a decision: A or B? I'd pick A."}, role=None, token=tok)
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "review"}, role=None, token=tok)
        html = self.call("GET", f"/t/{t['id']}", role="human")[1].decode()
        for words in ("Close as done", "Send back for more work", "Send back with this comment", "Just comment",
                      "The agent needs a decision from you.", "handed it back for review"):
            self.assertIn(words, html)
        self.assertNotIn('id="cmove"', html)
        self.assertIn(f"class=\"primary\" onclick=\"setStatus('{t['id']}','in_progress')\">Send back for more work", html)
        # The comment box's buttons carry the status change; sending back reads as such.
        self.assertIn('data-move="in_progress">Send back with this comment', html)
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "A, go."}, role="human")
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "in_progress"}, role="human")
        self.assertIn("<b>sent it back for more work</b>", self.call("GET", f"/t/{t['id']}", role="human")[1].decode())
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Done. Built A."}, role=None, token=tok)
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "review"}, role=None, token=tok)
        self.assertIn("The agent says it's done.", self.call("GET", f"/t/{t['id']}", role="human")[1].decode())
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "done"}, role="human")
        html = self.call("GET", f"/t/{t['id']}", role="human")[1].decode()
        self.assertIn("<b>closed it as done</b>", html)
        self.assertIn("Comment &amp; reopen", html)

    def test_delete_chat(self):
        tok = self.j("POST", "/api/users", {"username": "agent-dc", "kind": "agent"}, role="human")[1]["token"]
        c = self.j("POST", "/api/chat", {"members": ["agent-dc"], "body": "temporary"}, role="human")[1]
        f = self.j("POST", f"/api/chat/{c['id']}/attachments?filename=x.txt", raw=b"bye", role="human")[1]
        path = self.srv.store.files / self.srv.store.chat_file(f["id"])["path"]
        self.assertTrue(path.is_file())
        self.assertIn("deleteChat()", self.call("GET", f"/chat/{c['id']}", role="human")[1].decode())
        self.assertEqual(self.j("DELETE", f"/api/chat/{c['id']}", role=None, token=tok)[0], 403)  # agents can't
        self.assertEqual(self.j("DELETE", f"/api/chat/{c['id']}", role="human")[1]["deleted"], c["id"])
        self.assertEqual(self.j("GET", f"/api/chat/{c['id']}", role="human")[0], 404)
        self.assertFalse(path.exists())
        self.assertEqual(self.j("GET", "/api/me", role=None, token=tok)[1]["chat_inbox"], 0)  # nothing owed any more

    def test_page_scripts_parse(self):
        """Every script the pages carry is valid JavaScript (a stray quote breaks a whole page). Needs node."""
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node isn't installed")
        for name in ("JS", "CHAT_JS", "CHAT_LIST_JS", "LIVE_JS", "SETTINGS_JS", "NEW_JS", "DEP_PICKER_JS",
                     "WIKI_EDIT_JS", "COPY_JS", "DASH_JS", "PROPOSAL_JS"):
            r = subprocess.run(["node", "--check", "-"], input=getattr(at, name), text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")
        # And the scripts written into pages (ticket, chat, new, wiki, settings).
        t = self.new("review", title="script check")
        c = self.j("POST", "/api/chat", {"title": "script check", "body": "x"}, role="human")[1]
        self.j("POST", "/api/wiki", {"title": "Script check"}, role="human")
        for path in (f"/t/{t['id']}", f"/chat/{c['id']}", "/chat", "/new", "/wiki/script-check?edit=1", "/settings", "/",
                     "/backlog", "/users", "/guidance"):
            html = self.call("GET", path, role="human")[1].decode()
            for i, js in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
                r = subprocess.run(["node", "--check", "-"], input=js, text=True, capture_output=True)
                self.assertEqual(r.returncode, 0, f"{path} script {i}: {r.stderr}")

    def test_api_conventions(self):
        tok = self.j("POST", "/api/users", {"username": "agent-cv", "kind": "agent"}, role="human")[1]["token"]
        t = self.new("ready", title="conventions")
        # Typos are refused, naming what's accepted.
        code, err = self.j("PATCH", f"/api/tickets/{t['id']}", {"titel": "oops"}, role="human")
        self.assertEqual(code, 400)
        self.assertIn("unknown field titel", err["error"])
        self.assertIn("title", err["error"])
        self.assertEqual(self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "x", "author": "me"}, role="human")[0], 400)
        self.assertEqual(self.j("POST", f"/api/tickets/{t['id']}/cancel", {"why": "dup"}, role="human")[0], 400)
        # Ordinary tickets are kind "ticket".
        self.assertEqual(t["kind"], "ticket")
        self.assertEqual(self.j("POST", "/api/tickets", {"title": "x", "kind": "task"}, role="human")[0], 400)
        # Chat attachments are named like ticket ones; GET never marks anything read.
        c = self.j("POST", "/api/chat", {"members": ["agent-cv"], "body": "see file"}, role="human")[1]
        mid = c["messages"][0]["id"]
        self.assertEqual(self.j("POST", f"/api/chat/{c['id']}/attachments?filename=a.txt&message_id={mid}", raw=b"a",
                                role="human")[0], 201)
        msg = self.j("GET", f"/api/chat/{c['id']}", role=None, token=tok)[1]["messages"][0]
        self.assertEqual(msg["attachments"][0]["filename"], "a.txt")
        self.assertEqual(self.j("GET", "/api/me", role=None, token=tok)[1]["chat_inbox"], 1)
        self.assertEqual(self.j("GET", "/api/chat/inbox", role=None, token=tok)[1]["count"], 1)
        # PATCH changes fields; the wiki has no PUT.
        self.j("POST", "/api/wiki", {"title": "Conv"}, role="human")
        self.assertEqual(self.j("PUT", "/api/wiki/conv", {"body": "x"}, role="human")[0], 404)
        self.assertEqual(self.j("PATCH", "/api/wiki/conv", {"body": "x"}, role="human")[0], 200)
        brief = self.call("GET", "/api", role=None)[1].decode()
        self.assertIn("Conventions: `GET` never changes anything.", brief)
        for gone in ("claim=1", "/api/chat/unread", '"task"', "?peek", "chat/ID/files"):
            self.assertNotIn(gone, brief)

    def test_comments_wait_for_a_reply(self):
        tok = self.j("POST", "/api/users", {"username": "agent-cm", "kind": "agent"}, role="human")[1]["token"]
        other = self.j("POST", "/api/users", {"username": "agent-cm2", "kind": "agent"}, role="human")[1]["token"]
        t = self.new("in_progress", assignee="agent-cm", title="comment me")
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Starting."}, role=None, token=tok)
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Done."}, role=None, token=tok)
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "review"}, role=None, token=tok)
        W = lambda: self.j("GET", "/api/wait?timeout=1", role=None, token=tok)[1]
        self.assertEqual(W()["reason"], "timeout")
        # Comments posted while the agent isn't waiting still reach it, next time it asks.
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Nice. One thing:"}, role="human")
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "rename the button."}, role="human")
        got = W()
        self.assertEqual((got["reason"], got["tickets"][0]["id"], got["tickets"][0]["new_comments"], got["tickets"][0]["from"]),
                         ("comments", t["id"], 2, ["Frank"]))
        self.assertEqual(W()["reason"], "comments")  # and keeps doing so...
        ticket = self.j("GET", f"/api/tickets/{t['id']}", role=None, token=tok)[1]
        self.assertEqual(ticket["new_comments"], 2)
        self.assertTrue(ticket["what_now"].startswith("Frank left 2 new comments here: read them and reply on this ticket first."))
        self.assertEqual(self.j("GET", "/api/me", role=None, token=tok)[1]["comments_to_answer"], 2)
        self.assertIn("Comments from humans waiting for your reply", self.call("GET", "/api", role=None, token=tok)[1].decode())
        self.assertIn("agent-cm hasn't answered 2 comments yet", self.call("GET", f"/t/{t['id']}", role="human")[1].decode())
        # ...until it replies on the ticket.
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Got it, renaming."}, role=None, token=tok)
        self.assertEqual(W()["reason"], "timeout")
        self.assertEqual(self.j("GET", f"/api/tickets/{t['id']}", role=None, token=tok)[1]["new_comments"], 0)
        self.assertNotIn("hasn't answered", self.call("GET", f"/t/{t['id']}", role="human")[1].decode())
        # Another agent's comment doesn't need a reply.
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "fyi"}, role=None, token=other)
        self.assertEqual(W()["reason"], "timeout")
        # A comment on a ticket a human closes is still a comment: owed until the agent replies.
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Shipping it. Next time, tests go in tests/."}, role="human")
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "done"}, role="human")
        got = W()
        self.assertEqual((got["reason"], got["tickets"][0]["id"], got["tickets"][0]["status"]), ("comments", t["id"], "done"))
        ticket = self.j("GET", f"/api/tickets/{t['id']}", role=None, token=tok)[1]
        self.assertTrue(ticket["what_now"].startswith("Frank left 1 new comment here: read it and reply"), ticket["what_now"])
        self.assertIn("agent-cm hasn't answered your latest comment", self.call("GET", f"/t/{t['id']}", role="human")[1].decode())
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Will do, thanks!"}, role=None, token=tok)
        self.assertEqual(W()["reason"], "timeout")
        self.assertEqual(self.j("GET", f"/api/tickets/{t['id']}", role="human")[1]["status"], "done")

    def test_long_threads_are_paged(self):
        tok = self.j("POST", "/api/users", {"username": "agent-pg", "kind": "agent"}, role="human")[1]["token"]
        t = self.new("ready", title="long thread", assignee="agent-pg")
        ids = [self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": f"note {i}"}, role="human")[1]["id"] for i in range(30)]
        # Agents get the latest 20 (also when claiming), and page back on request.
        got = self.j("GET", f"/api/tickets/{t['id']}", role=None, token=tok)[1]
        self.assertEqual(([c["body"] for c in got["comments"]][:2], len(got["comments"]), got["comments_total"], got["comments_more"]),
                         (["note 10", "note 11"], 20, 30, True))
        claimed = self.j("POST", "/api/next", {}, role=None, token=tok)[1]["ticket"]
        self.assertEqual((claimed["id"], len(claimed["comments"])), (t["id"], 20))
        older = self.j("GET", f"/api/tickets/{t['id']}/comments?before={got['comments'][0]['id']}", role=None, token=tok)[1]
        self.assertEqual(([c["body"] for c in older["comments"]][0], len(older["comments"]), older["more"]), ("note 0", 10, False))
        self.assertEqual(self.j("GET", f"/api/tickets/{t['id']}/comments?before={ids[5]}&limit=3", role="human")[1]["more"], True)
        self.assertEqual(self.j("GET", f"/api/tickets/{t['id']}/comments", role="human")[0], 400)
        # The page: the latest 20, and a button for the rest, which loads the earlier page.
        html = self.call("GET", f"/t/{t['id']}", role="human")[1].decode()
        self.assertEqual(html.count('class="c " id="c'), 20)
        self.assertIn("Show earlier activity (10 more comments)", html)
        earlier = self.j("GET", f"/t/{t['id']}/earlier?before={got['comments'][0]['id']}", role="human")[1]
        self.assertEqual((earlier["html"].count('class="c " id="c'), earlier["before"]), (10, None))
        self.assertIn("created it in", earlier["html"])  # the history from that span comes along
        # Chat: the same.
        c = self.j("POST", "/api/chat", {"members": ["agent-pg"], "body": "m 0"}, role="human")[1]
        for i in range(1, 45):
            self.j("POST", f"/api/chat/{c['id']}/messages", {"body": f"m {i}"}, role="human")
        conv = self.j("GET", f"/api/chat/{c['id']}", role=None, token=tok)[1]
        self.assertEqual((len(conv["messages"]), conv["messages"][0]["body"], conv["messages_more"]), (20, "m 25", True))
        page1 = self.j("GET", f"/api/chat/{c['id']}/messages?before={conv['messages'][0]['id']}", role=None, token=tok)[1]
        page2 = self.j("GET", f"/api/chat/{c['id']}/messages?before={page1['messages'][0]['id']}", role=None, token=tok)[1]
        self.assertEqual((len(page1["messages"]), page1["more"], len(page2["messages"]), page2["more"]), (20, True, 5, False))
        newer = self.j("GET", f"/api/chat/{c['id']}/messages?after={conv['messages'][-3]['id']}", role=None, token=tok)[1]
        self.assertEqual([m["body"] for m in newer["messages"]], ["m 43", "m 44"])
        inbox = self.j("GET", "/api/chat/inbox", role=None, token=tok)[1]["conversations"]
        mine = [x for x in inbox if x["id"] == c["id"]][0]
        self.assertEqual((mine["count"], len(mine["messages"])), (45, 20))  # all counted, the latest 20 shown
        page = self.call("GET", f"/chat/{c['id']}", role="human")[1].decode()
        self.assertEqual(page.count('class="msg'), 20)
        self.assertIn("Show earlier messages", page)
        more = self.j("GET", f"/chat/{c['id']}/before/{conv['messages'][0]['id']}", role="human")[1]
        self.assertEqual((more["html"].count('class="msg'), bool(more["before"])), (20, True))

    def test_files_guidance(self):
        brief = self.call("GET", "/api", role=None)[1].decode()
        self.assertIn("**Keep your files tidy: one folder per ticket.**", brief)
        self.assertIn("`~/work/tickets/TICKET-ID/`", brief)
        self.assertIn("ticket/TICKET-ID", at.DEFAULT_GUIDANCE)
        self.assertIn("## Keeping your files tidy", at.DEFAULT_GUIDANCE)
        self.assertIn("**Your machine is yours alone:**", brief)
        self.assertNotIn("other tickets' folders", brief + at.DEFAULT_GUIDANCE)
        self.assertIn("~/work/tickets/TICKET-ID/", self.call("GET", "/api/skill", role="agent")[1].decode())

    def test_humans_can_move_blocked_tickets(self):
        tok = self.j("POST", "/api/users", {"username": "agent-bk", "kind": "agent"}, role="human")[1]["token"]
        t = self.new("in_progress", assignee="agent-bk", title="in review soon")
        self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": "Done."}, role=None, token=tok)
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "review"}, role=None, token=tok)
        first = self.new("ready", title="needs to happen first")
        self.j("POST", f"/api/tickets/{t['id']}/deps", {"depends_on": first["id"]}, role="human")
        # The human sends it back anyway; the agent is told it's blocked, and isn't handed it.
        self.assertEqual(self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "in_progress"}, role="human")[1]["status"], "in_progress")
        got = self.j("GET", f"/api/tickets/{t['id']}", role=None, token=tok)[1]
        self.assertTrue(got["what_now"].startswith(f"Yours and in progress, but blocked until {first['id']} is done"))
        self.assertNotIn(t["id"], [w["id"] for w in self.j("GET", "/api/wait?timeout=1", role=None, token=tok)[1]["tickets"]])
        self.assertEqual(self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "review"}, role=None, token=tok)[0], 409)
        self.assertIn("you can still move it", self.call("GET", f"/t/{t['id']}", role="human")[1].decode())

    def test_complete_epics_take_new_tickets(self):
        e = self.new("backlog", title="Finished plan", kind="epic")
        only = self.new("ready", title="the only part", epic_id=e["id"])
        self.j("PATCH", f"/api/tickets/{only['id']}", {"status": "done"}, role="human")
        self.assertEqual(self.j("GET", f"/api/tickets/{e['id']}", role="human")[1]["epic_state"], "complete")
        # The pickers know about it (as complete), and adding a ticket brings it back to life.
        page = self.call("GET", "/new", role="human")[1].decode()
        cands = {x["id"]: x["state"] for x in json.loads(re.search(r"const EPIC_CANDIDATES = (\[.*?\]);", page).group(1))}
        self.assertEqual(cands[e["id"]], "complete")
        later = self.new("ready", title="one more for it")
        self.j("PATCH", f"/api/tickets/{later['id']}", {"epic_id": e["id"]}, role="human")
        self.assertEqual(self.j("GET", f"/api/tickets/{e['id']}", role="human")[1]["epic_state"], "active")
        self.assertEqual(self.new("backlog", title="and another", epic_id=e["id"])["epic_id"], e["id"])
        # Cancelled epics aren't offered and can't take tickets.
        dead = self.new("backlog", title="Dropped plan", kind="epic")
        self.j("POST", f"/api/tickets/{dead['id']}/cancel", {}, role="human")
        self.assertEqual(self.j("PATCH", f"/api/tickets/{later['id']}", {"epic_id": dead["id"]}, role="human")[0], 400)
        self.assertNotIn(dead["id"], re.search(r"const EPIC_CANDIDATES = (\[.*?\]);",
                                               self.call("GET", "/new", role="human")[1].decode()).group(1))
        # The ticket page shows the current epic in the picker, with a way to remove it.
        html = self.call("GET", f"/t/{later['id']}", role="human")[1].decode()
        self.assertIn(f'value="{e["id"]} · Finished plan"', html)
        self.assertIn(">Remove</button>", html)

    def test_follow_on_ticket(self):
        e = self.new("backlog", title="Follow plan", kind="epic")
        t = self.new("ready", title="step one", epic_id=e["id"])
        page = self.call("GET", f"/t/{t['id']}", role="human")[1].decode()
        self.assertIn(f'href="/new?after={t["id"]}"', page)
        new = self.call("GET", f"/new?after={t['id']}", role="human")[1].decode()
        self.assertIn(f'id="nt-deps" placeholder="amber-otter, quiet-falcon" value="{t["id"]}, "', new)
        self.assertIn(f'<input type="hidden" id="nt-epic" value="{e["id"]}">', new)
        self.assertIn("Follows on from", new)
        loose = self.new("ready", title="no epic")
        new = self.call("GET", f"/new?after={loose['id']}", role="human")[1].decode()
        self.assertIn('<input type="hidden" id="nt-epic" value="">', new)
        self.assertEqual(self.call("GET", "/new?after=no-such-ticket", role="human")[0], 404)
        self.assertNotIn("/new?after=", self.call("GET", f"/t/{e['id']}", role="human")[1].decode())  # epics: no

    def test_markdown(self):
        render = lambda text: at.wiki_html(self.srv.store, text)
        self.assertEqual(render("*it* and _it_ and **b** and __b__ and ~~s~~"),
                         "<p><i>it</i> and <i>it</i> and <b>b</b> and <b>b</b> and <s>s</s></p>")
        # Things that only look like markdown stay as typed.
        self.assertEqual(render("snake_case_name, 2 * 3 * 4, a*b, file_name.txt"),
                         "<p>snake_case_name, 2 * 3 * 4, a*b, file_name.txt</p>")
        url = "https://example.com/_under_/x_y_z"
        self.assertIn(f'<a href="{url}" target="_blank" rel="noopener">{url}</a>', render(f"see {url}"))
        self.assertEqual(render("[the docs](https://x.io/a_b) and [home](/wiki/projects)"),
                         '<p><a href="https://x.io/a_b" target="_blank" rel="noopener">the docs</a> and '
                         '<a href="/wiki/projects">home</a></p>')
        self.assertNotIn('href="javascript', render("[x](javascript:alert(1))"))  # only web and site links
        # Lists: bullets, numbers, nesting, tasks, continuation lines.
        self.assertEqual(render("- one\n- two\n  - two a\n  - two b\n- three"),
                         "<ul><li>one</li><li>two<ul><li>two a</li><li>two b</li></ul></li><li>three</li></ul>")
        self.assertEqual(render("1. first\n2. second"), "<ol><li>first</li><li>second</li></ol>")
        self.assertIn('<span class="task done">☑</span> shipped', render("- [x] shipped\n- [ ] tests"))
        self.assertIn('<span class="task">☐</span> tests', render("- [x] shipped\n- [ ] tests"))
        self.assertEqual(render("- item\n  more of it"), "<ul><li>item\nmore of it</li></ul>")
        # Quotes, rules, tables, code.
        self.assertEqual(render("> quoted *bit*\n> more"), "<blockquote><p>quoted <i>bit</i>\nmore</p></blockquote>")
        self.assertEqual(render("above\n\n---\n\nbelow"), "<p>above</p><hr><p>below</p>")
        table = render("| name | ms |\n|---|---:|\n| open | 420 |\n| *sort* | 35 |")
        self.assertIn("<th>name</th><th>ms</th>", table)
        self.assertIn("<tr><td><i>sort</i></td><td>35</td></tr>", table)
        self.assertIn("<pre>*not* **this**</pre>", render("```\n*not* **this**\n```"))
        self.assertIn("<code>*nor_this*</code>", render("`*nor_this*`"))
        # Still escaped everywhere.
        self.assertNotIn("<script>", render("- <script>x</script>\n> <b>y</b>\n\n| <i>t</i> | a |\n|---|---|"))

    def test_review_is_for_done_or_a_decision(self):
        tok = self.j("POST", "/api/users", {"username": "agent-rv", "kind": "agent"}, role="human")[1]["token"]
        t = self.new("in_progress", assignee="agent-rv", title="half done")
        patch = lambda status: self.j("PATCH", f"/api/tickets/{t['id']}", {"status": status}, role=None, token=tok)
        say = lambda body: self.j("POST", f"/api/tickets/{t['id']}/comments", {"body": body}, role=None, token=tok)
        # Paused, not finished: refused, with the right moves spelled out.
        say("Got halfway, switching to something urgent.")
        code, err = patch("review")
        self.assertEqual(code, 409)
        for hint in ('"Done" or "Needs a decision"', "Keep it in progress", "Add it as a dependency", "Release it"):
            self.assertIn(hint, err["error"])
        # Done, or a decision needed (formatting around the first word is fine): accepted.
        say("**Needs a decision:** A or B? I'd pick A.")
        self.assertEqual(patch("review")[0], 200)
        self.j("PATCH", f"/api/tickets/{t['id']}", {"status": "in_progress"}, role="human")
        say("Done. Built A.")
        self.assertEqual(patch("review")[0], 200)
        # Releasing to ready unassigns it, so someone else can take it.
        u = self.new("in_progress", assignee="agent-rv", title="hand it over")
        self.j("POST", f"/api/tickets/{u['id']}/comments", {"body": "Releasing: someone with DB access should do this."}, role=None, token=tok)
        got = self.j("PATCH", f"/api/tickets/{u['id']}", {"status": "ready"}, role=None, token=tok)[1]
        self.assertEqual((got["status"], got["assignee"]), ("ready", None))
        # Waiting on another ticket: add it as a dependency; it stays yours, and wait leaves it out.
        v = self.new("in_progress", assignee="agent-rv", title="needs the other one")
        other = self.new("ready", title="the other one")
        self.assertEqual(self.j("POST", f"/api/tickets/{v['id']}/deps", {"depends_on": other["id"]}, role=None, token=tok)[0], 200)
        got = self.j("GET", f"/api/tickets/{v['id']}", role=None, token=tok)[1]
        self.assertEqual((got["status"], got["assignee"], got["blocked"]), ("in_progress", "agent-rv", True))
        self.assertNotIn(v["id"], [w["id"] for w in self.j("GET", "/api/wait?timeout=1", role=None, token=tok)[1]["tickets"]])

    def test_onboarding_says_talk_here(self):
        """Agents are told the tracker's chat and comments are how they talk to people, not their own interface."""
        skill = self.call("GET", "/api/skill", role="agent")[1].decode()
        brief = self.call("GET", "/api", role="agent")[1].decode()
        me = json.loads(self.call("GET", "/api/me", role="agent")[1])
        self.assertIn("Talk to people here: the humans read this tracker", skill)
        self.assertIn("if someone talks to you directly in your own interface", skill)
        self.assertIn('POST /api/chat` `{"members": ["their-username"]', skill)
        self.assertIn("**This is where you talk to people.**", brief)
        self.assertIn("we read the tracker, not your terminal or session", me["guidance"])

    def test_users_page_offers_the_hobkin_command_for_agents(self):
        page = self.call("GET", "/users", role="human")[1].decode()
        self.assertIn("'hobkin --hearthboard ' + location.origin + ' ' + token + ' --id ' + agent", page)
        self.assertIn("newToken(&quot;", page)

    def test_skill(self):
        code, body, h = self.call("GET", "/api/skill", role="agent", headers={"Host": "tickets.example:43917"})
        text = body.decode()
        self.assertEqual(code, 200)
        self.assertTrue(text.startswith("---\nname: hearthboard\ndescription: "))
        self.assertIn("http://tickets.example:43917", text)
        self.assertIn("$HEARTHBOARD_TOKEN", text)
        self.assertNotIn(self.keys["agent"], text)
        brief = self.call("GET", "/api", role=None)[1].decode()
        self.assertLess(brief.index("GET /api/skill"), brief.index("## Signing in"))  # up front

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
            store = at.Store(data)
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
        self.assertIn("Close as done", html)
        self.assertNotIn("Approve", html)
        self.assertIn("Needs your review", self.call("GET", "/", role="human")[1].decode())
        self.assertEqual(self.call("GET", "/t/ZZZZ", role="human")[0], 404)



class DataFolderTest(unittest.TestCase):
    """Where data goes when --data isn't given: never inside an installed package."""

    def where(self, folder, **env):
        import shutil as _shutil
        _shutil.copy(Path(__file__).resolve().parent / "hearthboard.py", folder)
        clean = {k: v for k, v in os.environ.items() if k not in ("HEARTHBOARD_DATA", "XDG_DATA_HOME")}
        r = subprocess.run([sys.executable, "-c", "import hearthboard; print(hearthboard.default_data_dir())"],
                           cwd=folder, env={**clean, **env}, capture_output=True, text=True, check=True)
        return r.stdout.strip()

    def test_default_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            xdg = os.path.join(tmp, "xdg")
            self.assertEqual(self.where(tmp, XDG_DATA_HOME=xdg), os.path.join(xdg, "hearthboard"))
            os.mkdir(os.path.join(tmp, "data"))              # a data/ folder beside the file isn't picked up
            self.assertEqual(self.where(tmp, XDG_DATA_HOME=xdg), os.path.join(xdg, "hearthboard"))
            self.assertEqual(self.where(tmp, HEARTHBOARD_DATA="/srv/hb"), "/srv/hb")

    def test_add_agent_prints_the_hobkin_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "hearthboard.py"), "--data", tmp,
                                  "--add-user", "worker-1"], capture_output=True, text=True).stdout
            token = out.splitlines()[1]
            self.assertIn(f"hobkin --hearthboard http://THIS-SERVER:43917 {token} --id worker-1", out)

    def test_version(self):
        out = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "hearthboard.py"), "--version"],
                             capture_output=True, text=True).stdout
        self.assertRegex(out, r"^hearthboard \d+\.\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
