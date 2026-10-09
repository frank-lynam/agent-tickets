"""Tests for agent_tickets.py: a real server on a temporary folder, driven
over HTTP. Standard library only:  python3 -m unittest -v"""
import json
import re
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
        cls.srv = at.make_server("127.0.0.1", 0, root / "data", [str(cls.allowed)], max_upload_mb=1, human_name="Frank")
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.keys = cls.srv.keys
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, role="agent", headers=None, raw=None):
        h = {"X-Agent": "tester"}
        if role:
            h["Authorization"] = "Bearer " + self.keys[role]
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
