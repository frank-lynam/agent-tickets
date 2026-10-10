#!/usr/bin/env python3
"""agent-tickets: a small ticket tracker for AI agents and the human who
reviews their work. Python standard library only: a threaded HTTP server
and an SQLite database.

  python3 agent_tickets.py                     # serve on 127.0.0.1:43917, data in ./data
  python3 agent_tickets.py --port 8080 --data /srv/tickets
  python3 agent_tickets.py --add-user frank --kind human   # make a user, print their token
  python3 agent_tickets.py --export backups/   # back everything up (see "Backups")
  python3 agent_tickets.py --import FILE       # restore a backup into an empty data folder

Tickets move through five statuses:
  backlog      an idea, not cleared for work yet
  ready        cleared: agents may pick it up
  in_progress  an agent is on it (partial work counts)
  review       the agent is done or needs a decision: the human's turn
  done         approved by the human

Users have a name, a kind (human or agent) and their own token, which is
who they are. Humans can do anything, including adding users and issuing
or revoking tokens (the Users page). Agents use the JSON API and can only
move tickets between ready, in_progress and review, and can't change
backlog or done tickets (comments are always allowed). Only a hash of each
token is stored. API docs: GET /api.
"""
import argparse
import hashlib
import html
import io
import json
import mimetypes
import os
import random
import re
import secrets
import sqlite3
import sys
import tarfile
import tempfile
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

# Ticket ids are two words, "amber-otter": one from each of these hand-picked
# lists. Easy to say, hear and spell (no homophones, nothing spelled two ways,
# no word in both lists), nothing offensive, and 400 x 482 = 192,800 of them.
# Vet any word you add. Tickets made before this have four or five letters
# ("KAMI") and keep them; both kinds work everywhere.
ID_ADJECTIVES = tuple("""
able active agile airy alert alpine amber ample ancient arctic ardent artful astral atomic autumn avid azure balmy
bold bouncy brainy brassy brave breezy bright brisk bronze bubbly busy calm candid carefree casual catchy cheery
chief chipper choice chummy civil classic clean clear clever cloudy coastal cobalt comfy cool copper cordial cosmic
crafty crimson crisp cubic curious curly cyan dainty dandy dapper daring dazzling decent deep deft devoted dewy
direct distant docile dotted dreamy driven dusty dynamic eager early earnest earthy easy eastern elated electric
elegant eminent emerald endless epic equal even exact expert fabled famous fancy far fast fearless festive fierce
final fine first fleet floral fluent fluffy flying fond formal fresh friendly frosty frugal funky fuzzy gallant
gentle genuine giant gifted giddy glad gleaming gleeful glossy glowing golden good graceful grand grassy green
groovy grounded handy happy hardy hazel hearty helpful heroic hidden honest hopeful humble hushed icy ideal indigo
inner intent iron ivory jade jaunty jazzy jolly jovial joyful jumbo junior keen kind kindly kinetic large lasting
latest lavish lawful leafy lean level light lilac limber linear liquid little lively local lofty logical loyal lucid
lucky lunar lush lyric magic magnetic majestic major mammoth marine mellow mighty mild mindful minty misty mobile
modern modest molten mossy mystic narrow natural navy neat nifty nimble noble nordic normal northern notable novel
oaken olive open optimal orange orbital organic ornate outer pacific painted pastel patient peaceful pearly perky
placid playful pleasant plucky plush poetic polar polished polite popular precise premium pretty primal prime
pristine prompt proper proud prudent pure purple quaint quick quiet quirky radiant rainy rapid rare ready regal
relaxed reliable remote rich ripe robust rocky roomy rosy round royal rugged rural rustic safe sandy satin savvy
scarlet scenic secret secure serene shiny short silent silky silver simple sincere sleek sleepy slender smart
smiling smooth snowy snug social soft solar solid sonic southern sparkling speedy spirited splendid sporty spotted
spry stable stark starry steady steep stellar sterling still stoic stony stout striped strong sturdy sublime subtle
sunlit sunny super supreme sure swift tactful talented tall tame tangy tawny teal tender thankful thrifty tidal tidy
timely tiny tranquil trendy trim tropical true trusty twin ultra unique united upbeat upper urban useful valiant
valid vast velvet verdant vernal vintage violet visual vital vivid vocal warm wavy western wild windy winged wiry
witty wondrous wooden worthy young youthful zany zealous zesty zippy
""".split())
ID_NOUNS = tuple("""
acorn agate albatross alder almond alpaca anchor antelope anvil apple apricot arrow aspen atlas atoll aurora badge
bagel ballad bamboo banjo banner barrel basil basin basket bay beacon beagle beetle beryl bicycle birch biscuit
bison blanket blizzard bluff bobcat bonnet bottle bramble breeze brook bucket buffalo bugle bumblebee butter
butterfly button cabin cactus camel canary candle canoe canvas canyon cape cardinal caribou cashew castle cavern
cedar cello chalice chariot cheetah chestnut chimney chipmunk chorus cicada citrine clam cliff cloud clover coast
collie comet compass condor cookie corgi cosmos cove crab crane crater crayon cricket crown crow crystal cupcake
cushion custard cypress daisy delta diamond dolphin dragonfly drum dumpling dune dusk eagle easel eclipse egret
elephant elk elm ember engine ermine estuary falcon ferret fern fiddle fig finch firefly flag flamingo flounder
flute forest fountain fox frost gadget gale galaxy gannet garden gardenia garnet gazebo gazelle gecko gerbil geyser
giraffe glacier glade glen glove goblet gondola goose gopher gorge granola grape grotto grouse grove guava guitar
gulch gull guppy halibut halo hammer hammock hamster harmony harp hawk heather hedgehog helmet heron herring hill
hippo holly honey hornet horizon hut ibex ibis igloo iguana impala iris island ivy jacket jackal jaguar jasmine
jasper jay jellyfish jigsaw juniper kangaroo kayak kestrel kettle kingfisher kite kiwi koala krill ladder ladybug
lagoon lake lantern larch lark lasso laurel lavender lemon leopard lightning lily lime linden lion llama lobster
locket lotus macaw magnet magnolia magpie mallard mallet mango mantis maple marble marigold marlin marmot marsh
martin meadow meerkat melody merlin mesa meteor minnow mink mitten mongoose monsoon moon mosaic moss moth mountain
mural myrtle narwhal nebula nectar needle nettle noodle notebook nova nutmeg oak oasis oatmeal oboe ocean ocelot
octopus onyx opal orbit orca orchard orchid oriole osprey ostrich otter owl oyster paddle palm pancake panda panther
papaya parcel parrot pasta pasture pebble pecan pelican pencil penguin peony pepper perch pheasant piano pickle pie
pigeon pike pillow pine pinwheel pitcher planet plankton plateau plaza pony poplar popcorn poppy porcupine potato
prairie prawn pretzel prism pudding puffin pulsar puma pumpkin puzzle quail quartz quasar quill quilt quince quokka
rabbit radar radish raft rainbow raisin raspberry raven redwood reef reindeer rhino rhubarb ribbon ridge river robin
rocket rook rowan saddle saffron salmon sandpiper sapphire sardine satchel scallop scarf scone scroll seahorse seal
sequoia sesame shark sheep shore shovel shrimp sled slipper sloth sonata sorbet sorrel soup sparrow spinach spindle
spoon spring spruce squid stamp star starfish starling statue stoat stork storm stream strudel sturgeon sugar sumac
summit sunrise sunset swallow swamp swan sycamore tapir teapot telescope tent thimble thistle thrush thunder tiger
toast toffee tomato topaz tortoise toucan tower trolley trout truffle trumpet tuba tulip tuna tundra tunnel turnip
turtle twilight ukulele umbrella urchin valley vanilla vase violin volcano waffle wagon walnut walrus waltz wand
warbler waterfall whistle willow windmill wisteria wolf wombat wren yak yarrow zebra zenith zephyr zinnia
""".split())
OLD_ID = re.compile(r"[A-Za-z]{4,5}")
WORD_ID = re.compile(r"([A-Za-z]+)[-_ ]+([A-Za-z]+)")
ID_PATH = r"[A-Za-z]{4,5}|[A-Za-z]+-[A-Za-z]+"  # a ticket id in a URL


def norm_wiki_path(path):
    return "/".join(p for p in str(path or "").strip("/").lower().split("/") if p)


def norm_id(tid):
    """A ticket id as stored: "amber-otter" (from any case, with a hyphen or a space), or "KAMI"."""
    tid = str(tid or "").strip()
    if OLD_ID.fullmatch(tid):
        return tid.upper()
    m = WORD_ID.fullmatch(tid)
    return f"{m[1].lower()}-{m[2].lower()}" if m else tid


def new_id(taken, pick=random.choice):
    for _ in range(5000):
        tid = f"{pick(ID_ADJECTIVES)}-{pick(ID_NOUNS)}"
        if not taken(tid):
            return tid
    raise RuntimeError("no free ticket id")


# --------------------------------------------------------------------------- checklists

# An agent keeps its checklist in a markdown file on its own machine and
# uploads the whole file after each step: a report, replacing the last one.
# The file on the agent's machine is the only state; nobody edits it here.
CHECKLIST_MAX = 100_000


def parse_checklist(text):
    """Items ("- [ ] step" / "- [x] step") and note lines from checklist markdown."""
    out = {"items": [], "done": 0, "total": 0}
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


def checklist_summary(t, items=False):
    """Swap a ticket row's stored checklist columns for {"done", "total", "updated_by", "updated_at"}
    (plus "items" and "text" when asked), or None when nothing's been uploaded."""
    text = t.pop("checklist_md", None)
    t.pop("checklist_path", None)
    by, at = t.pop("checklist_by", None), t.pop("checklist_at", None)
    if not text:
        return None
    p = parse_checklist(text)
    out = {"done": p["done"], "total": p["total"], "updated_by": by, "updated_at": at}
    if items:
        out.update(items=p["items"], text=text)
    return out


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
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  kind TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, created_by TEXT, created_at REAL NOT NULL,
  disabled INTEGER NOT NULL DEFAULT 0, last_seen REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS deps (
  ticket_id TEXT NOT NULL REFERENCES tickets(id), depends_on TEXT NOT NULL REFERENCES tickets(id),
  PRIMARY KEY (ticket_id, depends_on));
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, created_by TEXT NOT NULL, created_at REAL NOT NULL,
  updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chat_members (
  conversation_id INTEGER NOT NULL REFERENCES conversations(id), username TEXT NOT NULL COLLATE NOCASE,
  last_read INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (conversation_id, username));
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL REFERENCES conversations(id),
  author TEXT NOT NULL, role TEXT NOT NULL, body TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chat_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL REFERENCES conversations(id),
  message_id INTEGER REFERENCES messages(id), filename TEXT NOT NULL, path TEXT NOT NULL, mime TEXT NOT NULL,
  size INTEGER NOT NULL, author TEXT NOT NULL, role TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS wiki_pages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER REFERENCES wiki_pages(id), slug TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at REAL NOT NULL,
  updated_by TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS wiki_sibling_slug ON wiki_pages(COALESCE(parent_id, 0), slug);
CREATE TABLE IF NOT EXISTS wiki_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, page_id INTEGER NOT NULL REFERENCES wiki_pages(id),
  title TEXT NOT NULL, body TEXT NOT NULL, edited_by TEXT NOT NULL, edited_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS wiki_revisions_page ON wiki_revisions(page_id, id);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS chat_members_user ON chat_members(username);
CREATE INDEX IF NOT EXISTS chat_files_message ON chat_files(message_id);
CREATE INDEX IF NOT EXISTS tickets_status ON tickets(status, priority, created_at);
CREATE INDEX IF NOT EXISTS comments_ticket ON comments(ticket_id, created_at);
CREATE INDEX IF NOT EXISTS attachments_ticket ON attachments(ticket_id);
CREATE INDEX IF NOT EXISTS events_ticket ON events(ticket_id, at);
"""


# Bumped (with a migration in Store.__init__) whenever the tables change.
SCHEMA_VERSION = 7
TABLES = ("users", "settings", "tickets", "deps", "comments", "attachments", "events",
          "conversations", "chat_members", "messages", "chat_files", "wiki_pages", "wiki_revisions")
KINDS = ("human", "agent")
USERNAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}")


# The house rules agents get with every ticket they claim and at the top of
# the /api briefing. Humans edit it on the Guidance page; this is the start.
GONE = ("checklist_path is gone: keep your checklist on your machine and upload it with "
        "PUT /api/tickets/ID/checklist after each step")

DEFAULT_GUIDANCE = """## How we work

0. Chat comes first. When a response carries a "chat" notice, read your unread messages and answer them before your next step on the ticket. Chat is for questions and planning that aren't about one ticket; anything about a ticket goes in a comment on that ticket.
1. When you take a ticket, first post a one-line comment: you're starting, and you'll write a checklist.
2. Write a markdown checklist of the steps ("- [ ] step") in a file on your machine, upload it (PUT /api/tickets/ID/checklist), and post a short plan: what you'll do, and how you'll know it's done.
3. After each step, tick it off in your file, upload it again, and comment what you did and what happened, including errors and surprises.
4. When something is visible (a page, a chart, a game), attach a screenshot or a short recording.
5. Hand back with a comment that starts "Done" or "Needs a decision", then set the status to review.

## How much to write

Progress comments: one to three sentences per step. Save the detail for the hand-off comment. Don't paste long logs; attach them as files.
"""


def new_token():
    return "at_" + secrets.token_urlsafe(24)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


class Store:
    """The database and the uploaded files. One SQLite connection per thread;
    writes go through one lock so ids and status changes never race."""

    def __init__(self, data_dir, checklist_roots=None):  # checklist_roots: no longer used
        self.dir = Path(data_dir).resolve()
        self.files = self.dir / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "tickets.db"
        self.local = threading.local()
        self.write_lock = threading.Lock()
        db = self.db()
        db.executescript(SCHEMA)
        # 1 -> 2 added the users table, 2 -> 3 the settings table, 3 -> 4 the
        # deps table, 4 -> 5 the chat tables, 5 -> 6 the wiki tables (all
        # created above); the columns are added here (6 -> 7: uploaded
        # checklists; tickets.checklist_path is no longer used).
        for table, col, decl in (("tickets", "assignee", "TEXT"), ("users", "guidance", "TEXT"),
                                 ("tickets", "kind", "TEXT NOT NULL DEFAULT 'task'"), ("tickets", "epic_id", "TEXT"),
                                 ("tickets", "proposed", "INTEGER NOT NULL DEFAULT 0"),
                                 ("tickets", "declined", "INTEGER NOT NULL DEFAULT 0"),
                                 ("tickets", "checklist_md", "TEXT"), ("tickets", "checklist_by", "TEXT"),
                                 ("tickets", "checklist_at", "REAL")):
            if col not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        db.execute("CREATE INDEX IF NOT EXISTS tickets_assignee ON tickets(assignee, status)")
        db.execute("CREATE INDEX IF NOT EXISTS tickets_epic ON tickets(epic_id)")
        db.execute("CREATE INDEX IF NOT EXISTS deps_on ON deps(depends_on)")
        if db.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def db(self):
        c = getattr(self.local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            self.local.conn = c
        return c

    # --- users
    def users(self):
        return [dict(r) for r in self.db().execute(
            "SELECT id, username, kind, created_by, created_at, disabled, last_seen, guidance FROM users ORDER BY kind, username")]

    def user(self, username):
        row = self.db().execute("SELECT id, username, kind, created_by, created_at, disabled, last_seen, guidance FROM users "
                                "WHERE username = ?", (username,)).fetchone()
        if not row:
            raise ApiError(404, f"no user {username}")
        return dict(row)

    def user_by_token(self, token):
        """The active user this token belongs to, or None."""
        row = self.db().execute("SELECT id, username, kind, disabled, last_seen FROM users WHERE token_hash = ?",
                                (token_hash(token),)).fetchone()
        if not row or row["disabled"]:
            return None
        now = time.time()
        if not row["last_seen"] or now - row["last_seen"] > 60:
            with self.write_lock, self.db() as db:
                db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now, row["id"]))
        return dict(row)

    def add_user(self, username, kind, created_by, token=None):
        """A new user and their token (returned once; only its hash is kept)."""
        username = str(username or "").strip()
        if not USERNAME_RE.fullmatch(username):
            raise ApiError(400, "usernames are 1-32 letters, digits, '.', '_' or '-', starting with a letter or digit")
        if kind not in KINDS:
            raise ApiError(400, f"kind is one of {list(KINDS)}")
        token = token or new_token()
        with self.write_lock:
            db = self.db()
            if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise ApiError(409, f"there's already a user called {username}")
            with db:
                db.execute("INSERT INTO users (username, kind, token_hash, created_by, created_at) VALUES (?,?,?,?,?)",
                           (username, kind, token_hash(token), created_by, time.time()))
        return self.user(username), token

    def new_user_token(self, username):
        """Replace a user's token; the old one stops working at once."""
        u = self.user(username)
        token = new_token()
        with self.write_lock, self.db() as db:
            db.execute("UPDATE users SET token_hash = ? WHERE id = ?", (token_hash(token), u["id"]))
        return token

    # --- guidance
    def setting(self, key, default=None):
        row = self.db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        with self.write_lock, self.db() as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       (key, value))

    def house_guidance(self):
        return self.setting("guidance", DEFAULT_GUIDANCE)

    def guidance_for(self, username):
        """The house rules, plus this user's own notes if they have any."""
        text = self.house_guidance().strip()
        row = self.db().execute("SELECT guidance FROM users WHERE username = ?", (username,)).fetchone()
        if row and (row["guidance"] or "").strip():
            text += f"\n\n## Notes for you ({username})\n\n" + row["guidance"].strip()
        return text

    def set_user_guidance(self, username, text):
        u = self.user(username)
        with self.write_lock, self.db() as db:
            db.execute("UPDATE users SET guidance = ? WHERE id = ?", ((text or "").strip() or None, u["id"]))
        return self.user(username)

    def set_disabled(self, username, disabled):
        u = self.user(username)
        with self.write_lock:
            db = self.db()
            if disabled and u["kind"] == "human" and db.execute(
                    "SELECT COUNT(*) FROM users WHERE kind = 'human' AND disabled = 0 AND id != ?", (u["id"],)).fetchone()[0] == 0:
                raise ApiError(409, "that's the last active human; add another before disabling this one")
            with db:
                db.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if disabled else 0, u["id"]))
        return self.user(username)

    # --- reads
    def ticket(self, tid):
        row = self.db().execute("SELECT * FROM tickets WHERE id = ?", (norm_id(tid),)).fetchone()
        if not row:
            raise ApiError(404, f"no ticket {tid}")
        return dict(row)

    def tickets(self, statuses=None, q=None, limit=500, recent_first=False, assignee=None, kind=None, epic=None,
                proposed=None):
        sql, args = "SELECT t.*, (SELECT COUNT(*) FROM comments c WHERE c.ticket_id = t.id) AS comment_count, " \
                    "(SELECT COUNT(*) FROM attachments a WHERE a.ticket_id = t.id) AS attachment_count FROM tickets t", []
        where = []
        if statuses:
            where.append("status IN (%s)" % ",".join("?" * len(statuses)))
            args += statuses
        if q:
            where.append("(t.id LIKE ? OR t.title LIKE ? OR t.description LIKE ?)")
            args += [f"%{q}%"] * 3
        if kind:
            where.append("t.kind = ?")
            args.append(kind)
        if epic:
            where.append("t.epic_id = ?")
            args.append(norm_id(epic))
        if proposed is not None:
            where.append("t.proposed = ?")
            args.append(1 if proposed else 0)
        if assignee == "":
            where.append("t.assignee IS NULL")
        elif assignee:
            where.append("t.assignee = ? COLLATE NOCASE")
            args.append(assignee)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?" if recent_first else " ORDER BY priority, created_at LIMIT ?"
        args.append(limit)
        out = [dict(r) for r in self.db().execute(sql, args)]
        for t in out:
            last = self.db().execute("SELECT author, role, body, created_at FROM comments WHERE ticket_id = ? "
                                     "ORDER BY created_at DESC, id DESC LIMIT 1", (t["id"],)).fetchone()
            t["last_comment"] = dict(last) if last else None
            self._links(t)
        return out

    def blockers(self, tid):
        """Ids of the tickets that must be done first and aren't yet."""
        return [r["id"] for r in self.db().execute(
            "SELECT p.id FROM deps d JOIN tickets p ON p.id = d.depends_on "
            "WHERE d.ticket_id = ? AND p.status != 'done' ORDER BY p.id", (tid,))]

    def _links(self, t):
        """Add what a list needs to show: blockers, the epic's title, an epic's progress, checklist progress."""
        if "checklist_md" in t:
            t["checklist"] = checklist_summary(t)
        t["blocked_by"] = self.blockers(t["id"])
        if t.get("epic_id"):
            row = self.db().execute("SELECT title FROM tickets WHERE id = ?", (t["epic_id"],)).fetchone()
            t["epic_title"] = row["title"] if row else None
        if t.get("kind") == "epic":
            t["progress"] = self.epic_progress(t["id"])
        return t

    def epic_progress(self, eid):
        counts = {r[0]: r[1] for r in self.db().execute(
            "SELECT status, COUNT(*) FROM tickets WHERE epic_id = ? AND declined = 0 GROUP BY status", (eid,))}
        return {"total": sum(counts.values()), "done": counts.get("done", 0), "by_status": counts}

    def detail(self, tid):
        t = self.ticket(tid)
        db = self.db()
        t["comments"] = [dict(r) for r in db.execute(
            "SELECT * FROM comments WHERE ticket_id = ? ORDER BY created_at, id", (t["id"],))]
        t["attachments"] = [self.attachment_json(dict(r)) for r in db.execute(
            "SELECT * FROM attachments WHERE ticket_id = ? ORDER BY created_at, id", (t["id"],))]
        t["events"] = [dict(r) for r in db.execute(
            "SELECT * FROM events WHERE ticket_id = ? ORDER BY at, id", (t["id"],))]
        t["checklist"] = checklist_summary(t, items=True)
        refs = wiki_refs(t["description"]) + [p for c in t["comments"] for p in wiki_refs(c["body"])]
        t["wiki_links"] = [self.wiki_link_info(p) for p in dict.fromkeys(refs)]
        t["depends_on"] = [dict(r) for r in db.execute(
            "SELECT p.id, p.title, p.status, p.declined FROM deps d JOIN tickets p ON p.id = d.depends_on "
            "WHERE d.ticket_id = ? ORDER BY p.id", (t["id"],))]
        t["unblocks"] = [dict(r) for r in db.execute(
            "SELECT n.id, n.title, n.status FROM deps d JOIN tickets n ON n.id = d.ticket_id "
            "WHERE d.depends_on = ? ORDER BY n.id", (t["id"],))]
        if t["kind"] == "epic":
            t["tickets"] = self.tickets(epic=t["id"], limit=1000)
        self._links(t)
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

    # --- writes
    def _event(self, db, tid, actor, role, field, old, new, now):
        db.execute("INSERT INTO events (ticket_id, actor, role, field, old, new, at) VALUES (?,?,?,?,?,?,?)",
                   (tid, actor, role, field, None if old is None else str(old), None if new is None else str(new), now))

    def _check_assignee(self, name):
        """A real user's exact name, or None for unassigned."""
        if name in (None, ""):
            return None
        return self.user(str(name))["username"]

    def _check_epic(self, eid):
        """An epic's id, or None."""
        if eid in (None, ""):
            return None
        e = self.ticket(str(eid))
        if e["kind"] != "epic":
            raise ApiError(400, f"{e['id']} isn't an epic")
        return e["id"]

    def _would_cycle(self, db, tid, dep):
        """Would "tid depends on dep" make a loop (dep already waits on tid)?"""
        seen, todo = set(), [dep]
        while todo:
            cur = todo.pop()
            if cur == tid:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            todo += [r[0] for r in db.execute("SELECT depends_on FROM deps WHERE ticket_id = ?", (cur,))]
        return False

    def _check_agent_may_change(self, t, actor):
        if t["kind"] == "epic":
            raise ApiError(403, f"{t['id']} is an epic: agents can only comment on it")
        if t["status"] in AGENT_LOCKED:
            raise ApiError(403, f"ticket {t['id']} is {t['status']}: agents can only comment on it")
        if t["assignee"] and t["assignee"].lower() != actor.lower():
            raise ApiError(403, f"ticket {t['id']} is assigned to {t['assignee']}: you can only comment on it")

    def create(self, data, actor, role):
        if data.get("checklist_path"):
            raise ApiError(400, GONE)
        title = str(data.get("title") or "").strip()
        if not title:
            raise ApiError(400, "a ticket needs a title")
        kind = data.get("kind") or "task"
        if kind not in ("task", "epic"):
            raise ApiError(400, 'kind is "task" or "epic"')
        epic_id = self._check_epic(data.get("epic_id"))
        if kind == "epic" and epic_id:
            raise ApiError(400, "epics can't belong to another epic")
        deps = data.get("depends_on") or []
        if isinstance(deps, str):
            # "amber-otter, quiet falcon" or "KAMI ROZU": commas, or spaces between ids
            # ("quiet falcon" is one id when that ticket exists).
            def ids_in(chunk):
                one = self.db().execute("SELECT 1 FROM tickets WHERE id = ?", (norm_id(chunk),)).fetchone()
                return [chunk] if one else chunk.split()
            deps = [d for chunk in deps.split(",") if chunk.strip() for d in ids_in(chunk.strip())]
        deps = [self.ticket(d)["id"] for d in deps]
        if kind == "epic" and deps:
            raise ApiError(400, "epics don't have predecessors; put them on the epic's tickets")
        status = data.get("status") or "backlog"
        if status not in STATUSES:
            raise ApiError(400, f"status must be one of {STATUSES}")
        if role == "agent" and status != "backlog":
            raise ApiError(403, "agents propose new tickets (they land in the backlog); a human accepts them")
        priority = int(data.get("priority") or 2)
        if priority not in PRIORITIES:
            raise ApiError(400, "priority is 1 (high), 2 (normal) or 3 (low)")
        if role == "agent" and data.get("assignee"):
            raise ApiError(403, "agents can't assign new tickets; a human assigns them")
        assignee = self._check_assignee(data.get("assignee"))
        now = time.time()
        with self.write_lock:
            db = self.db()
            tid = new_id(lambda x: db.execute("SELECT 1 FROM tickets WHERE id = ?", (x,)).fetchone())
            with db:
                # Tickets an agent creates are proposals until a human accepts them.
                db.execute("INSERT INTO tickets (id, title, description, status, priority, created_by, "
                           "created_at, updated_at, assignee, kind, epic_id, proposed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (tid, title, str(data.get("description") or ""), status, priority,
                            actor, now, now, assignee, kind, epic_id, 1 if role == "agent" else 0))
                self._event(db, tid, actor, role, "proposed" if role == "agent" else "created", None, status, now)
                for d in deps:
                    db.execute("INSERT OR IGNORE INTO deps (ticket_id, depends_on) VALUES (?, ?)", (tid, d))
                if assignee:
                    self._event(db, tid, actor, role, "assignee", None, assignee, now)
        return self._links(self.ticket(tid))

    def update(self, tid, data, actor, role):
        if "checklist_path" in data:
            raise ApiError(400, GONE)
        fields = {k: data[k] for k in ("title", "description", "status", "priority", "assignee", "epic_id") if k in data}
        if not fields:
            raise ApiError(400, "nothing to change (title, description, status, priority, assignee, epic_id)")
        if "assignee" in fields:
            fields["assignee"] = self._check_assignee(fields["assignee"])
        if "epic_id" in fields:
            fields["epic_id"] = self._check_epic(fields["epic_id"])
        with self.write_lock:
            db = self.db()
            t = self.ticket(tid)
            if t["kind"] == "epic" and fields.get("epic_id"):
                raise ApiError(400, "epics can't belong to another epic")
            # Waiting on unfinished predecessors: it can't start, be reviewed or be approved.
            if fields.get("status") in ("in_progress", "review", "done") and t["kind"] == "task":
                waiting = self.blockers(t["id"])
                if waiting:
                    raise ApiError(409, f"{t['id']} is blocked: {', '.join(waiting)} must be done first "
                                        f"(you can still comment on it)")
            if role == "agent":
                self._check_agent_may_change(t, actor)
                if "status" in fields and fields["status"] not in AGENT_TARGETS:
                    raise ApiError(403, "agents can set ready, in_progress or review; only humans move "
                                        "tickets to backlog or done")
                if "assignee" in fields and fields["assignee"] and fields["assignee"].lower() != actor.lower():
                    raise ApiError(403, "agents can assign a ticket to themselves or to no one; humans assign it to others")
                # Starting an unassigned ticket makes it yours.
                if fields.get("status") == "in_progress" and not t["assignee"] and "assignee" not in fields:
                    fields["assignee"] = actor
            if "status" in fields and fields["status"] not in STATUSES:
                raise ApiError(400, f"status must be one of {STATUSES}")
            if "title" in fields and not str(fields["title"]).strip():
                raise ApiError(400, "the title can't be empty")
            if "priority" in fields:
                fields["priority"] = int(fields["priority"])
                if fields["priority"] not in PRIORITIES:
                    raise ApiError(400, "priority is 1 (high), 2 (normal) or 3 (low)")
            # A human moving a proposal out of the backlog accepts it.
            if role == "human" and t["proposed"] and fields.get("status", "backlog") != "backlog":
                fields["proposed"] = 0
            now = time.time()
            with db:
                for k, v in fields.items():
                    if t[k] != v:
                        db.execute(f"UPDATE tickets SET {k} = ? WHERE id = ?", (v, t["id"]))
                        # Description edits are logged without their text.
                        self._event(db, t["id"], actor, role, k, None if k == "description" else t[k],
                                    None if k == "description" else v, now)
                db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, t["id"]))
        return self._links(self.ticket(t["id"]))

    def put_checklist(self, tid, text, actor, role):
        """The agent working a ticket reports its checklist: the whole file, replacing the last upload."""
        t = self.ticket(tid)
        if role != "agent":
            raise ApiError(403, "checklists are reported by the agent working the ticket; ask it in a comment")
        if not t["assignee"] or t["assignee"].lower() != actor.lower():
            raise ApiError(403, f"only the agent working {t['id']} ({t['assignee'] or 'no one yet'}) reports its checklist")
        if t["status"] != "in_progress":
            raise ApiError(409, f"checklists are reported while a ticket is in progress; {t['id']} is {t['status']}")
        if len(text) > CHECKLIST_MAX:
            raise ApiError(413, "checklist too long")
        text = text.strip()
        if not text:
            raise ApiError(400, "send the checklist's text as the body")
        old = checklist_summary(dict(t))
        new = parse_checklist(text)
        if text != (t["checklist_md"] or ""):
            now = time.time()
            with self.write_lock:
                db = self.db()
                with db:
                    db.execute("UPDATE tickets SET checklist_md = ?, checklist_by = ?, checklist_at = ?, updated_at = ? "
                               "WHERE id = ?", (text, actor, now, now, t["id"]))
                    self._event(db, t["id"], actor, role, "checklist", f"{old['done']}/{old['total']}" if old else None,
                                f"{new['done']}/{new['total']}", now)
        return {"checklist": self.detail(t["id"])["checklist"]}

    def review_proposal(self, tid, accept, ready, actor, role):
        """Accept (to the backlog, or ready) or decline (to done, marked
        declined) a proposal. On an epic, its proposed tickets go the same way."""
        if role != "human":
            raise ApiError(403, "only humans accept or decline proposals")
        with self.write_lock:
            db = self.db()
            t = self.ticket(tid)
            if not t["proposed"]:
                raise ApiError(409, f"{t['id']} isn't a proposal")
            ids = [t["id"]] + ([r[0] for r in db.execute(
                "SELECT id FROM tickets WHERE epic_id = ? AND proposed = 1", (t["id"],))] if t["kind"] == "epic" else [])
            now = time.time()
            with db:
                for i in ids:
                    cur = db.execute("SELECT status FROM tickets WHERE id = ?", (i,)).fetchone()["status"]
                    if accept:
                        # An epic itself isn't worked; "ready" applies to tickets only.
                        kind = db.execute("SELECT kind FROM tickets WHERE id = ?", (i,)).fetchone()[0]
                        to = "ready" if ready and kind == "task" else cur
                        db.execute("UPDATE tickets SET proposed = 0, status = ?, updated_at = ? WHERE id = ?", (to, now, i))
                        self._event(db, i, actor, role, "accepted", None, to, now)
                    else:
                        db.execute("UPDATE tickets SET proposed = 0, declined = 1, status = 'done', updated_at = ? "
                                   "WHERE id = ?", (now, i))
                        self._event(db, i, actor, role, "declined", cur, "done", now)
        return self._links(self.ticket(t["id"]))

    def add_dep(self, tid, dep, actor, role):
        with self.write_lock:
            db = self.db()
            t, d = self.ticket(tid), self.ticket(dep)
            if role == "agent":
                self._check_agent_may_change(t, actor)
            if "epic" in (t["kind"], d["kind"]):
                raise ApiError(400, "dependencies are between tickets, not epics")
            if t["id"] == d["id"] or self._would_cycle(db, t["id"], d["id"]):
                raise ApiError(409, f"that would make a loop: {d['id']} already waits on {t['id']}")
            with db:
                if db.execute("INSERT OR IGNORE INTO deps (ticket_id, depends_on) VALUES (?, ?)", (t["id"], d["id"])).rowcount:
                    self._event(db, t["id"], actor, role, "depends_on", None, d["id"], time.time())
        return self.detail(t["id"])

    def remove_dep(self, tid, dep, actor, role):
        with self.write_lock:
            db = self.db()
            t = self.ticket(tid)
            if role == "agent":
                self._check_agent_may_change(t, actor)
            with db:
                if db.execute("DELETE FROM deps WHERE ticket_id = ? AND depends_on = ?", (t["id"], norm_id(dep))).rowcount:
                    self._event(db, t["id"], actor, role, "depends_on", norm_id(dep), None, time.time())
        return self.detail(t["id"])

    def claim_next(self, actor, role, claim):
        """The next ready ticket for this user: assigned to them first, then
        unassigned ones (never someone else's), highest priority and oldest
        first. With claim, it moves to in_progress and is assigned to them in
        the same step, so two agents never take the same one."""
        with self.write_lock:
            db = self.db()
            # Only real work: not epics, not proposals, not blocked.
            row = db.execute("SELECT id, assignee FROM tickets t WHERE status = 'ready' AND kind = 'task' AND proposed = 0 "
                             "AND (assignee IS NULL OR assignee = ? COLLATE NOCASE) AND NOT EXISTS ("
                             "  SELECT 1 FROM deps d JOIN tickets p ON p.id = d.depends_on "
                             "  WHERE d.ticket_id = t.id AND p.status != 'done') "
                             "ORDER BY assignee IS NULL, priority, created_at LIMIT 1", (actor,)).fetchone()
            if not row:
                return None
            if claim:
                now = time.time()
                with db:
                    db.execute("UPDATE tickets SET status = 'in_progress', assignee = ?, updated_at = ? WHERE id = ?",
                               (actor, now, row["id"]))
                    self._event(db, row["id"], actor, role, "status", "ready", "in_progress", now)
                    if not row["assignee"]:
                        self._event(db, row["id"], actor, role, "assignee", None, actor, now)
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
        name, mime, dest = self._write_upload(self.files / t["id"], filename, mime, size, stream, max_bytes)
        now = time.time()
        with self.write_lock:
            db = self.db()
            with db:
                cur = db.execute("INSERT INTO attachments (ticket_id, comment_id, filename, path, mime, size, author, role, "
                                 "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (t["id"], comment_id, name, str(dest.relative_to(self.files)), mime, size, actor, role, now))
                db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, t["id"]))
        return self.attachment_json(self.attachment(cur.lastrowid))

    def _write_upload(self, folder, filename, mime, size, stream, max_bytes):
        """Save an upload under `folder`; (clean name, mime type, path on disk)."""
        if size > max_bytes:
            raise ApiError(413, f"file is larger than the {max_bytes // (1 << 20)} MB limit")
        name = re.sub(r"[^\w.\- ]+", "_", Path(filename or "upload").name).strip() or "upload"
        mime = (mime or "").split(";")[0].strip()
        # curl --data-binary (and urllib) send form-urlencoded when no type is given.
        if mime in ("", "application/octet-stream", "application/x-www-form-urlencoded"):
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        folder.mkdir(parents=True, exist_ok=True)
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
        return name, mime, dest

    # --- wiki: text pages in one tree. Every page has a place: a top-level
    # section, or under another page. Humans write; agents read (for now).
    def _wiki_human(self, role):
        if role != "human":
            raise ApiError(403, "the wiki is read-only for agents for now; if something is missing or wrong, "
                                "tell a human in chat")

    def _wiki_row(self, pid):
        row = self.db().execute("SELECT * FROM wiki_pages WHERE id = ?", (pid,)).fetchone()
        if not row:
            raise ApiError(404, "no such wiki page")
        return dict(row)

    def wiki_resolve(self, path):
        """The page at "section/page/subpage"."""
        parts = [p for p in str(path or "").strip("/").lower().split("/") if p]
        if not parts:
            raise ApiError(404, "no wiki page there")
        row, parent = None, None
        for part in parts:
            row = self.db().execute("SELECT * FROM wiki_pages WHERE COALESCE(parent_id, 0) = ? AND slug = ?",
                                    (parent or 0, part)).fetchone()
            if not row:
                raise ApiError(404, f"no wiki page {'/'.join(parts)}")
            parent = row["id"]
        return dict(row)

    def wiki_crumbs(self, page):
        """[(title, path), ...] from the top-level section down to `page`."""
        chain, cur = [], page
        while cur:
            chain.append(cur)
            cur = self._wiki_row(cur["parent_id"]) if cur["parent_id"] else None
        chain.reverse()
        out, path = [], ""
        for c in chain:
            path = f"{path}/{c['slug']}" if path else c["slug"]
            out.append((c["title"], path))
        return out

    def wiki_path(self, page):
        return self.wiki_crumbs(page)[-1][1]

    def wiki_children(self, pid):
        rows = self.db().execute("SELECT * FROM wiki_pages WHERE COALESCE(parent_id, 0) = ? ORDER BY title COLLATE NOCASE",
                                 (pid or 0,)).fetchall()
        return [dict(r) for r in rows]

    def wiki_page(self, path):
        p = self.wiki_resolve(path)
        p["path"] = self.wiki_path(p)
        p["breadcrumbs"] = [{"title": t, "path": x} for t, x in self.wiki_crumbs(p)]
        p["children"] = [{"title": c["title"], "path": f'{p["path"]}/{c["slug"]}'} for c in self.wiki_children(p["id"])]
        return p

    def wiki_tree(self):
        """Every page, as nested {"title", "path", "children"}."""
        rows = [dict(r) for r in self.db().execute("SELECT id, parent_id, slug, title FROM wiki_pages")]
        kids = {}
        for r in rows:
            kids.setdefault(r["parent_id"], []).append(r)

        def build(pid, prefix):
            out = []
            for r in sorted(kids.get(pid, []), key=lambda r: r["title"].lower()):
                path = f"{prefix}/{r['slug']}" if prefix else r["slug"]
                out.append({"title": r["title"], "path": path, "children": build(r["id"], path)})
            return out
        return build(None, "")

    def wiki_search(self, q):
        """Pages with every word of `q` (any order) in their title, their text, or the titles and
        addresses of the pages above them. Title matches rank first, then the path, then the text."""
        words = [w for w in re.split(r"\W+", q.lower()) if w]
        if not words:
            return []
        rows = [dict(r) for r in self.db().execute("SELECT id, parent_id, slug, title, body, updated_at FROM wiki_pages")]
        by_id = {r["id"]: r for r in rows}

        def place(r):  # this page's and its ancestors' titles and slugs
            parts, cur = [], r
            while cur:
                parts += [cur["title"], cur["slug"]]
                cur = by_id.get(cur["parent_id"])
            return " ".join(parts).lower()
        hits = []
        for r in rows:
            title, where, body = r["title"].lower(), place(r), r["body"].lower()
            if all(w in where or w in body for w in words):
                score = sum(3 * (w in title) + 2 * (w in where) + (w in body) for w in words)
                hits.append((-score, -r["updated_at"], r["id"], r))
        out = []
        for *_, r in sorted(hits)[:50]:
            low = r["body"].lower()
            at = min([low.find(w) for w in words if w in low] or [-1])
            snip = r["body"][max(0, at - 60): at + 120] if at >= 0 else r["body"][:180]
            out.append({"title": r["title"], "path": self.wiki_path(r), "snippet": snip.strip()})
        return out

    def wiki_link_info(self, path):
        """{"path", "title"} for a linked page, or {"path", "title": None, "missing": True}."""
        try:
            return {"path": path, "title": self.wiki_resolve(path)["title"]}
        except ApiError:
            return {"path": path, "title": None, "missing": True}

    @staticmethod
    def _wiki_slug(slug, title):
        slug = re.sub(r"[^a-z0-9]+", "-", str(slug or title or "").lower()).strip("-")[:60].strip("-")
        if not slug:
            raise ApiError(400, "a page needs a title (and a slug of letters, digits and hyphens)")
        return slug

    def _wiki_save(self, db, pid, title, body, actor, now):
        db.execute("INSERT INTO wiki_revisions (page_id, title, body, edited_by, edited_at) VALUES (?,?,?,?,?)",
                   (pid, title, body, actor, now))

    def wiki_create(self, data, actor, role):
        self._wiki_human(role)
        title = str(data.get("title") or "").strip()[:200]
        if not title:
            raise ApiError(400, "a page needs a title")
        parent = self.wiki_resolve(data["parent"]) if data.get("parent") else None
        slug = self._wiki_slug(data.get("slug"), title)
        body = str(data.get("body") or "")
        now = time.time()
        with self.write_lock:
            db = self.db()
            try:
                with db:
                    pid = db.execute("INSERT INTO wiki_pages (parent_id, slug, title, body, created_by, created_at, "
                                     "updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                                     (parent["id"] if parent else None, slug, title, body, actor, now, actor, now)).lastrowid
                    self._wiki_save(db, pid, title, body, actor, now)
            except sqlite3.IntegrityError:
                raise ApiError(409, f"there's already a page called {slug} there")
        return self.wiki_page(self.wiki_path(self._wiki_row(pid)))

    def wiki_update(self, path, data, actor, role):
        """Edit the title or text, rename (slug), or move it (parent: a path, or "" for a top-level section)."""
        self._wiki_human(role)
        p = self.wiki_resolve(path)
        title = str(data["title"]).strip()[:200] if "title" in data else p["title"]
        if not title:
            raise ApiError(400, "a page needs a title")
        body = str(data["body"]) if "body" in data else p["body"]
        slug = self._wiki_slug(data["slug"], title) if data.get("slug") else p["slug"]
        parent_id = p["parent_id"]
        if "parent" in data:
            parent_id = self.wiki_resolve(data["parent"])["id"] if data["parent"] else None
            cur = parent_id
            while cur:  # not under itself
                if cur == p["id"]:
                    raise ApiError(409, "a page can't go under itself or one of its own pages")
                cur = self._wiki_row(cur)["parent_id"]
        now = time.time()
        with self.write_lock:
            db = self.db()
            try:
                with db:
                    db.execute("UPDATE wiki_pages SET parent_id = ?, slug = ?, title = ?, body = ?, updated_by = ?, "
                               "updated_at = ? WHERE id = ?", (parent_id, slug, title, body, actor, now, p["id"]))
                    if (title, body) != (p["title"], p["body"]):
                        self._wiki_save(db, p["id"], title, body, actor, now)
            except sqlite3.IntegrityError:
                raise ApiError(409, f"there's already a page called {slug} there")
        return self.wiki_page(self.wiki_path(self._wiki_row(p["id"])))

    def wiki_delete(self, path, actor, role):
        self._wiki_human(role)
        p = self.wiki_resolve(path)
        if self.wiki_children(p["id"]):
            raise ApiError(409, "move or delete the pages under it first")
        with self.write_lock:
            db = self.db()
            with db:
                db.execute("DELETE FROM wiki_revisions WHERE page_id = ?", (p["id"],))
                db.execute("DELETE FROM wiki_pages WHERE id = ?", (p["id"],))
        return {"deleted": norm_wiki_path(path)}

    def wiki_revisions(self, pid):
        return [dict(r) for r in self.db().execute(
            "SELECT id, title, edited_by, edited_at, length(body) AS size FROM wiki_revisions WHERE page_id = ? "
            "ORDER BY id DESC", (pid,))]

    def wiki_revision(self, pid, rid):
        row = self.db().execute("SELECT * FROM wiki_revisions WHERE page_id = ? AND id = ?", (pid, rid)).fetchone()
        if not row:
            raise ApiError(404, "no such version")
        return dict(row)

    # --- chat: conversations anyone can read and post in; members are the
    # people who get told about new messages.
    def conversation(self, cid, viewer=None):
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            raise ApiError(404, "no such conversation")
        row = self.db().execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
        if not row:
            raise ApiError(404, f"no conversation {cid}")
        c = dict(row)
        c["members"] = [r[0] for r in self.db().execute(
            "SELECT username FROM chat_members WHERE conversation_id = ? ORDER BY username COLLATE NOCASE", (cid,))]
        others = [m for m in c["members"] if not viewer or m.lower() != viewer.lower()]
        c["name"] = c["title"] or ", ".join(others or c["members"]) or f"Conversation {cid}"
        if viewer:
            c["unread"] = self._unread_in(cid, viewer)
        return c

    def _unread_in(self, cid, user):
        return self.db().execute(
            "SELECT COUNT(*) FROM messages g JOIN chat_members m ON m.conversation_id = g.conversation_id "
            "AND m.username = ? WHERE g.conversation_id = ? AND g.id > m.last_read AND g.author != ? COLLATE NOCASE",
            (user, cid, user)).fetchone()[0]

    def unread_total(self, user):
        return self.db().execute(
            "SELECT COUNT(*) FROM messages g JOIN chat_members m ON m.conversation_id = g.conversation_id "
            "AND m.username = ? WHERE g.id > m.last_read AND g.author != ? COLLATE NOCASE", (user, user)).fetchone()[0]

    def conversations(self, viewer, limit=200):
        out = []
        for r in self.db().execute("SELECT id FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall():
            c = self.conversation(r[0], viewer)
            last = self.db().execute("SELECT author, role, body, created_at FROM messages WHERE conversation_id = ? "
                                     "ORDER BY id DESC LIMIT 1", (r[0],)).fetchone()
            c["last_message"] = dict(last) if last else None
            out.append(c)
        return out

    def chat_file_json(self, f, internal=False):
        f = dict(f)
        path = f.pop("path")
        f["url"] = f"/chat-files/{f['id']}/{quote(f['filename'])}"
        if internal:
            f["_path"] = path  # for previews on our own pages, never sent out
        return f

    def chat_file(self, fid):
        row = self.db().execute("SELECT * FROM chat_files WHERE id = ?", (fid,)).fetchone()
        if not row:
            raise ApiError(404, "no such file")
        return dict(row)

    def messages(self, cid, after=0, limit=200, internal=False):
        """A conversation's messages after id `after` (the latest `limit`), oldest first, with their files."""
        msgs = [dict(r) for r in self.db().execute(
            "SELECT * FROM (SELECT * FROM messages WHERE conversation_id = ? AND id > ? ORDER BY id DESC LIMIT ?) "
            "ORDER BY id", (cid, after, limit))]
        by_id = {m["id"]: m for m in msgs}
        for m in msgs:
            m["files"] = []
        if msgs:
            for f in self.db().execute(f"SELECT * FROM chat_files WHERE message_id IN ({','.join('?' * len(by_id))}) "
                                       "ORDER BY id", list(by_id)):
                by_id[f["message_id"]]["files"].append(self.chat_file_json(f, internal))
        return msgs

    def message(self, mid):
        row = self.db().execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
        if not row:
            raise ApiError(404, f"no message {mid}")
        m = dict(row)
        m["files"] = [self.chat_file_json(f) for f in self.db().execute(
            "SELECT * FROM chat_files WHERE message_id = ? ORDER BY id", (mid,))]
        return m

    def conversation_detail(self, cid, viewer, after=0, mark=True):
        c = self.conversation(cid, viewer)
        c["messages"] = self.messages(c["id"], after)
        if mark:
            self.mark_read(c["id"], viewer)
            c["unread"] = 0
        return c

    def unread(self, user):
        """The conversations `user` has unread messages in, each with those messages."""
        out = []
        for r in self.db().execute(
                "SELECT m.conversation_id, m.last_read FROM chat_members m JOIN conversations c "
                "ON c.id = m.conversation_id WHERE m.username = ? ORDER BY c.updated_at DESC", (user,)).fetchall():
            msgs = [x for x in self.messages(r[0], after=r[1]) if x["author"].lower() != user.lower()]
            if msgs:
                c = self.conversation(r[0], user)
                c["messages"] = msgs
                out.append(c)
        return out

    def chat_notice(self, user):
        """What goes with every ticket response to an agent that has unread chat."""
        total = self.unread_total(user)
        if not total:
            return None
        convs = [{"id": c["id"], "name": c["name"], "unread": c["unread"]} for c in self.unread(user)]
        where = "; ".join(f'{c["name"]} ({c["unread"]})' for c in convs)
        return {"unread": total, "conversations": convs,
                "notice": f"You have {total} unread chat message{'s' if total != 1 else ''} ({where}). Answer chat "
                          "before your next step on this ticket: GET /api/chat/unread, then reply with "
                          "POST /api/chat/ID/messages (or POST /api/chat/ID/read when no reply is needed). Then carry on."}

    def mark_read(self, cid, user, upto=None):
        db = self.db()
        if upto is None:
            upto = db.execute("SELECT COALESCE(MAX(id), 0) FROM messages WHERE conversation_id = ?", (cid,)).fetchone()[0]
        with self.write_lock, db:
            db.execute("UPDATE chat_members SET last_read = MAX(last_read, ?) WHERE conversation_id = ? AND username = ?",
                       (upto, cid, user))

    def start_conversation(self, data, actor, role):
        """A new conversation; with no title and one other person, the existing
        conversation between the two of you if there is one."""
        title = str(data.get("title") or "").strip()[:120] or None
        names = data.get("members") or []
        if isinstance(names, str):
            names = [n for n in re.split(r"[\s,]+", names) if n]
        members = {self.user(str(n))["username"] for n in names}
        members.add(actor)
        if len(members) < 2 and not title:
            raise ApiError(400, "say who it's with (members), or give it a title")
        cid = None
        with self.write_lock:
            db = self.db()
            if not title and len(members) == 2:
                want = {m.lower() for m in members}
                for r in db.execute("SELECT id FROM conversations WHERE title IS NULL").fetchall():
                    have = {x[0].lower() for x in db.execute(
                        "SELECT username FROM chat_members WHERE conversation_id = ?", (r[0],))}
                    if have == want:
                        cid = r[0]
                        break
            if cid is None:
                now = time.time()
                with db:
                    cid = db.execute("INSERT INTO conversations (title, created_by, created_at, updated_at) "
                                     "VALUES (?,?,?,?)", (title, actor, now, now)).lastrowid
                    for m in members:
                        db.execute("INSERT INTO chat_members (conversation_id, username) VALUES (?, ?)", (cid, m))
        body = str(data.get("body") or "").strip()
        if body:
            self.post_message(cid, body, actor, role)
        return cid

    def post_message(self, cid, body, actor, role):
        body = str(body or "").strip()
        if not body:
            raise ApiError(400, "a message needs a body")
        if len(body) > 100_000:
            raise ApiError(413, "message too long; attach it as a file")
        c = self.conversation(cid)
        now = time.time()
        with self.write_lock:
            db = self.db()
            with db:
                mid = db.execute("INSERT INTO messages (conversation_id, author, role, body, created_at) VALUES (?,?,?,?,?)",
                                 (c["id"], actor, role, body, now)).lastrowid
                # Posting joins you to the conversation, and means you've read it.
                db.execute("INSERT OR IGNORE INTO chat_members (conversation_id, username) VALUES (?, ?)", (c["id"], actor))
                db.execute("UPDATE chat_members SET last_read = ? WHERE conversation_id = ? AND username = ?",
                           (mid, c["id"], actor))
                db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, c["id"]))
        return self.message(mid)

    def add_member(self, cid, username, actor, role):
        c = self.conversation(cid)
        u = self.user(username)
        with self.write_lock:
            db = self.db()
            with db:
                added = db.execute("INSERT OR IGNORE INTO chat_members (conversation_id, username) VALUES (?, ?)",
                                   (c["id"], u["username"])).rowcount
                # They start with nothing unread except the note that they were added.
                db.execute("UPDATE chat_members SET last_read = (SELECT COALESCE(MAX(id), 0) FROM messages "
                           "WHERE conversation_id = ?) WHERE conversation_id = ? AND username = ? AND ?",
                           (c["id"], c["id"], u["username"], added))
        if added:
            self.post_message(c["id"], f"(added {u['username']})", actor, role)
        return self.conversation(c["id"], actor)

    def add_chat_file(self, cid, mid, filename, mime, size, stream, actor, role, max_bytes):
        c = self.conversation(cid)
        if mid is not None:
            row = self.db().execute("SELECT conversation_id FROM messages WHERE id = ?", (mid,)).fetchone()
            if not row or row[0] != c["id"]:
                raise ApiError(400, f"message {mid} isn't in conversation {c['id']}")
        name, mime, dest = self._write_upload(self.files / "chat" / str(c["id"]), filename, mime, size, stream, max_bytes)
        now = time.time()
        with self.write_lock:
            db = self.db()
            with db:
                fid = db.execute("INSERT INTO chat_files (conversation_id, message_id, filename, path, mime, size, author, "
                                 "role, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (c["id"], mid, name, str(dest.relative_to(self.files)), mime, size, actor, role,
                                  now)).lastrowid
                db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, c["id"]))
        return self.chat_file_json(self.chat_file(fid))


# --------------------------------------------------------------------------- keys

def migrate_keys(store, human_name="human"):
    """Before users existed there were two shared keys in <data>/keys.json.
    The first run after the upgrade turns them into users (a human named
    `human_name` and an agent named "agent", same tokens, so nothing stops
    working) and deletes the file."""
    path = store.dir / "keys.json"
    if not path.exists() or store.users():
        return []
    keys = json.loads(path.read_text())
    made = []
    for username, kind in ((human_name, "human"), ("agent", "agent")):
        if keys.get(kind):
            store.add_user(username, kind, "migrated from keys.json", token=keys[kind])
            made.append(username)
    path.unlink()
    return made


# --------------------------------------------------------------------------- backups
#
# A backup is a .tar.gz holding export.json and files/<ticket>/<file>.
# export.json is plain and versioned, one list of rows per table, so a
# backup stays readable (and convertible) across breaking changes:
#   {"format": "agent-tickets-export", "version": 1, "schema_version": 1,
#    "exported_at": "...", "statuses": [...], "missing_files": [...],
#    "tables": {"tickets": [...], "comments": [...], "attachments": [...], "events": [...]}}

EXPORT_FORMAT = "agent-tickets-export"
EXPORT_VERSION = 1


def export_archive(store, dest):
    """Write a backup of everything to `dest` (a .tar.gz path). Rows are read
    in one transaction, so the backup is consistent even while agents work."""
    db = sqlite3.connect(store.db_path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        tables = {t: [dict(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in TABLES}
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        db.execute("COMMIT")
    finally:
        db.close()
    paths = [a["path"] for a in tables["attachments"]] + [f["path"] for f in tables["chat_files"]]
    missing = [p for p in paths if not (store.files / p).is_file()]
    doc = {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "schema_version": schema,
           "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "statuses": STATUSES,
           "missing_files": missing, "tables": tables}
    data = json.dumps(doc, indent=1, ensure_ascii=False).encode()
    tmp = Path(str(dest) + ".part")
    with tarfile.open(tmp, "w:gz") as tar:
        info = tarfile.TarInfo("export.json")
        info.size, info.mtime = len(data), int(time.time())
        tar.addfile(info, io.BytesIO(data))
        gone = set(missing)
        for p in paths:
            if p not in gone:
                tar.add(store.files / p, arcname="files/" + p, recursive=False)
    os.replace(tmp, dest)
    return {t: len(rows) for t, rows in tables.items()}


def export_to(store, target, keep=30):
    """--export: to a file, or into a folder as agent-tickets-<time>.tar.gz,
    keeping the newest `keep` backups there."""
    as_folder = str(target).endswith(("/", os.sep))  # check before Path() drops the slash
    target = Path(target)
    if as_folder or target.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        dest = target / time.strftime("agent-tickets-%Y%m%d-%H%M%S.tar.gz")
        counts = export_archive(store, dest)
        old = sorted(target.glob("agent-tickets-*.tar.gz"))[:-keep] if keep > 0 else []
        for p in old:
            p.unlink()
        return dest, counts, len(old)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target, export_archive(store, target), 0


def parents_first(pages):
    """Wiki page rows ordered so every parent comes before its children."""
    by_parent = {}
    for p in pages:
        by_parent.setdefault(p.get("parent_id"), []).append(p)
    out, todo = [], list(by_parent.get(None, []))
    while todo:
        p = todo.pop(0)
        out.append(p)
        todo += by_parent.get(p["id"], [])
    return out


def import_archive(store, src, replace=False):
    """Restore a backup. Ids are kept, so links, ticket names and comment
    numbers stay the same. Refuses a data folder that already has tickets
    unless `replace`, which deletes them first. Columns this version doesn't
    know are skipped; ones it has that the backup lacks get their defaults."""
    with tarfile.open(src, "r:gz") as tar:
        doc = json.load(tar.extractfile("export.json"))
        if doc.get("format") != EXPORT_FORMAT:
            raise ValueError(f"{src} isn't an agent-tickets backup")
        if doc.get("version", 0) > EXPORT_VERSION:
            raise ValueError(f"{src} was made by a newer agent-tickets (backup version {doc['version']})")
        with store.write_lock:
            db = store.db()
            have = db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
            if have and not replace:
                raise ValueError(f"this data folder already has {have} tickets; restore into an empty one, or use --replace")
            # Users: a backup's users are added (by name, skipping names that
            # already exist here); a backup without any (older ones) leaves the
            # users here alone, so nobody gets locked out by a restore.
            backup_users = doc["tables"].get("users") or []
            with db:
                if replace:
                    for t in reversed(TABLES):
                        if t != "users" or backup_users:
                            db.execute(f"DELETE FROM {t}")
                for t in TABLES:
                    cols = {r[1] for r in db.execute(f"PRAGMA table_info({t})")}
                    rows = doc["tables"].get(t, [])
                    if t == "wiki_pages":
                        rows = parents_first(rows)
                    for row in rows:
                        keep = {k: v for k, v in row.items() if k in cols}
                        if t == "users":
                            keep.pop("id", None)
                            if db.execute("SELECT 1 FROM users WHERE username = ?", (keep.get("username"),)).fetchone():
                                continue
                        verb = "INSERT OR REPLACE" if t == "settings" else "INSERT"
                        db.execute(f"{verb} INTO {t} ({', '.join(keep)}) VALUES ({', '.join('?' * len(keep))})",
                                   list(keep.values()))
            # Files: only regular files under files/, never outside the data folder.
            root = store.files.resolve()
            restored = 0
            for m in tar.getmembers():
                if not m.isfile() or not m.name.startswith("files/"):
                    continue
                dest = (store.files / m.name[len("files/"):]).resolve()
                if root not in dest.parents:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as f, open(dest, "wb") as out:
                    while chunk := f.read(CHUNK):
                        out.write(chunk)
                restored += 1
    return {t: len(doc["tables"].get(t, [])) for t in TABLES} | {"files": restored, "missing_files": len(doc.get("missing_files", []))}


# --------------------------------------------------------------------------- HTML

CSS = """
:root{--bg:#f6f5f1;--panel:#fff;--ink:#1f2328;--dim:#646b73;--line:#e2dfd7;--accent:#3a5bd9;--accent-ink:#fff;
--review:#b5480f;--review-bg:#fdf0e7;--prog:#3a5bd9;--prog-bg:#edf1fd;--ready:#1d7a55;--ready-bg:#e8f6ef;
--back:#6b6f76;--back-bg:#efeeea;--done:#4b5563;--done-bg:#eceff2;--agent:#6d4bd1;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme]){--bg:#15171a;--panel:#1d2024;--ink:#e6e8eb;--dim:#9aa1aa;--line:#2c3036;
--accent:#7d97f5;--accent-ink:#0e1530;--review:#f29a63;--review-bg:#3a2417;--prog:#8ea6f7;--prog-bg:#1e2742;
--ready:#62d1a1;--ready-bg:#16302a;--back:#aab0b8;--back-bg:#26292e;--done:#b8c0ca;--done-bg:#262b31;--agent:#b39cf7;
color-scheme:dark}}
:root[data-theme=dark]{--bg:#15171a;--panel:#1d2024;--ink:#e6e8eb;--dim:#9aa1aa;--line:#2c3036;
--accent:#7d97f5;--accent-ink:#0e1530;--review:#f29a63;--review-bg:#3a2417;--prog:#8ea6f7;--prog-bg:#1e2742;
--ready:#62d1a1;--ready-bg:#16302a;--back:#aab0b8;--back-bg:#26292e;--done:#b8c0ca;--done-bg:#262b31;--agent:#b39cf7;
color-scheme:dark}
/* Goth rave: black-violet, hot magenta, UV cyan, acid green, a strobe's afterglow. */
:root[data-theme=goth]{--bg:#09060d;--panel:#130c1b;--ink:#f3e8ff;--dim:#a993c4;--line:#2f1d42;
--accent:#ff2bd6;--accent-ink:#14000f;--review:#ff3d7f;--review-bg:#3a0a22;--prog:#29e7ff;--prog-bg:#062a33;
--ready:#b4ff39;--ready-bg:#1d2e06;--back:#c3a9e3;--back-bg:#21142f;--done:#8f7aa8;--done-bg:#1a1124;--agent:#c77dff;
--glow:0 0 10px rgba(255,43,214,.55),0 0 26px rgba(255,43,214,.2);color-scheme:dark}
:root[data-theme=goth] body{background:radial-gradient(1200px 600px at 85% -10%,#2a0b3a 0%,transparent 60%),
radial-gradient(900px 500px at -10% 110%,#06283a 0%,transparent 55%),var(--bg);background-attachment:fixed}
:root[data-theme=goth] header.top{background:rgba(19,12,27,.92);border-bottom:1px solid transparent;
border-image:linear-gradient(90deg,#ff2bd6,#8a3dff,#29e7ff) 1}
:root[data-theme=goth] header.top b{color:#ff2bd6;text-shadow:var(--glow);letter-spacing:.08em;text-transform:uppercase}
:root[data-theme=goth] h1{text-shadow:0 0 14px rgba(199,125,255,.45)}
:root[data-theme=goth] h2{color:#29e7ff;text-shadow:0 0 8px rgba(41,231,255,.35)}
:root[data-theme=goth] nav a.on{background:#24113a;color:#ff2bd6;box-shadow:inset 0 -2px 0 #ff2bd6}
:root[data-theme=goth] nav .n{background:#ff2bd6;color:#14000f;box-shadow:var(--glow)}
:root[data-theme=goth] button.primary{box-shadow:var(--glow)}
:root[data-theme=goth] button.primary:hover{background:#ff5ce0}
:root[data-theme=goth] .card:hover,:root[data-theme=goth] .list .item:hover{border-color:#ff2bd6;box-shadow:var(--glow)}
:root[data-theme=goth] .chip,:root[data-theme=goth] .tag{box-shadow:0 0 8px -2px currentColor;text-shadow:0 0 6px currentColor}
:root[data-theme=goth] .bar{background:#24113a}
:root[data-theme=goth] .bar i{background:linear-gradient(90deg,#ff2bd6,#8a3dff,#29e7ff);box-shadow:0 0 10px #8a3dff}
:root[data-theme=goth] .c.agent{border-left-color:#c77dff;box-shadow:-4px 0 14px -6px #c77dff}
:root[data-theme=goth] .id{color:#29e7ff}
:root[data-theme=goth] ::selection{background:#ff2bd6;color:#14000f}
:root[data-theme=goth] *{scrollbar-color:#5b2a7a var(--bg)}
input[type=file]{color:var(--dim);font-size:13px;max-width:100%}
input[type=file]::file-selector-button{font:600 13px system-ui,sans-serif;padding:6px 12px;margin-right:8px;border-radius:7px;
border:1px solid var(--line);background:var(--panel);color:var(--ink);cursor:pointer}
.theme{margin-left:auto;width:auto;padding:4px 8px;font-size:13px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent)}
header.top{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
header.top b{margin-right:12px;font-size:15px}
nav a{padding:6px 10px;border-radius:6px;text-decoration:none;color:var(--ink);white-space:nowrap;display:inline-block}
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
.att.file{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.att .dl{font-weight:600;word-break:break-all}
.att details{margin-top:6px}.att summary{cursor:pointer;color:var(--dim);font-size:13px}
.pv{margin:6px 0 0;max-height:360px;overflow:auto;font-size:12px;line-height:1.45;background:var(--bg);padding:8px;border-radius:6px;white-space:pre}
.ck{list-style:none;padding:0;margin:8px 0 0}
.ck li{padding:2px 0}.ck li.note{color:var(--dim);font-size:13px}
.ck .x{color:var(--ready)}
.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;background:var(--ready)}
dl.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:0;font-size:14px}
dl.kv dt{color:var(--dim)}dl.kv dd{margin:0;overflow-wrap:anywhere}
.err{color:var(--review)}
a.wl.missing{color:var(--review);text-decoration-style:dashed}
.wlink{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 0 0}
.wlink code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--panel);border:1px solid var(--line);padding:3px 7px;border-radius:5px;overflow-wrap:anywhere}
.tag{display:inline-block;font-size:12px;padding:1px 7px;border-radius:5px;background:var(--back-bg);color:var(--back);font-weight:600}
.tag.blocked{background:var(--review-bg);color:var(--review)}
.tag.epic{background:var(--prog-bg);color:var(--agent)}
.banner{border-radius:10px;padding:10px 14px;margin-bottom:14px;background:var(--review-bg);color:var(--review)}
.banner.prop{background:var(--prog-bg);color:var(--prog)}
.chat{display:grid;gap:8px}
.msg{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:8px 12px;max-width:min(680px,94%);justify-self:start}
.msg.mine{justify-self:end;background:var(--prog-bg)}
.msg.agent{border-left:3px solid var(--agent)}
.msg .who{font-weight:600}.msg .who.agent{color:var(--agent)}
.msg .body{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:2px}
.msg .mk{margin-left:6px;text-decoration:none;font-size:12px;opacity:.75}
.composer{position:sticky;bottom:0;margin-top:14px;padding:10px 0 4px;background:var(--bg)}
.composer textarea{min-height:64px}
.clist .item.unread b{color:var(--accent)}
.clist .count{display:inline-block;min-width:20px;padding:0 6px;border-radius:10px;background:var(--accent);color:var(--accent-ink);font-size:12px;text-align:center;font-weight:700}
.who-pick{display:flex;flex-wrap:wrap;gap:6px 14px}.who-pick label{white-space:nowrap}
:root[data-theme=goth] .msg.mine{background:#24113a;border-color:#4a2370}
:root[data-theme=goth] .clist .count{box-shadow:var(--glow)}
.wk{line-height:1.6}
.wk h2,.wk h3,.wk h4{text-transform:none;letter-spacing:0;color:var(--ink);margin:18px 0 6px}
.wk>:first-child{margin-top:0}.wk h2{font-size:19px}.wk h3{font-size:16px}.wk h4{font-size:15px}
.wk p{white-space:pre-wrap;overflow-wrap:anywhere;margin:0 0 10px}
.wk pre{white-space:pre;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:8px 10px;font-size:13px}
.wk code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);padding:1px 4px;border-radius:4px}
.crumbs{font-size:13px;color:var(--dim);margin-bottom:4px}.crumbs a{text-decoration:none}
.tree,.tree ul{list-style:none;margin:0;padding:0}.tree ul{margin-left:16px;border-left:1px solid var(--line);padding-left:10px}
.tree li{margin:3px 0}.tree a{text-decoration:none}.tree>li>a{font-weight:600}
.list .item.prop{flex-wrap:wrap}.list .item.prop>a{flex:1 1 240px;min-width:0}.list .item.prop>.row{flex-wrap:wrap}
.sub{margin-left:18px;border-left:2px solid var(--line);padding-left:10px}
.deps li{margin:2px 0}
"""

JS = """
(function(){ try { const t = localStorage.getItem('at-theme'); if (t) document.documentElement.dataset.theme = t; } catch (e) {} })();
function setTheme(t){
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  try { t ? localStorage.setItem('at-theme', t) : localStorage.removeItem('at-theme'); } catch (e) {}
}
document.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('theme'); if (sel) sel.value = document.documentElement.dataset.theme || '';
});
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


# [[section/page]] or [[section/page|label]]: a link to a wiki page, from anywhere.
WIKI_LINK = re.compile(r"\[\[([A-Za-z0-9/-]+)(?:\|([^\]\n]+))?\]\]")
LINKABLE = re.compile(r"(https?://[^\s<]+|\[\[[A-Za-z0-9/-]+(?:\|[^\]\n]+)?\]\])")


def wiki_refs(text):
    """The wiki paths a text links to, in order."""
    return [norm_wiki_path(m[1]) for m in WIKI_LINK.finditer(text or "")]


def link_part(part):
    """One escaped URL or [[wiki link]] as HTML."""
    if part.startswith("[["):
        m = WIKI_LINK.fullmatch(part)
        return f'<a class="wl" href="/wiki/{norm_wiki_path(m[1])}">{m[2] or m[1]}</a>'
    return f'<a href="{part}" target="_blank" rel="noopener">{part}</a>'


def linkify(text):
    return "".join(link_part(p) if i % 2 else p for i, p in enumerate(LINKABLE.split(esc(text))))


CHAT_BADGE = "<!--chat-badge-->"


def page(title, body, active="", review_count=0):
    tabs = [("/", "Dashboard"), ("/chat", "Chat"), ("/wiki", "Wiki"), ("/backlog", "Backlog"), ("/done", "Done"), ("/new", "New ticket"),
            ("/users", "Users"), ("/guidance", "Guidance")]
    # The chat badge is per viewer: Handler.html() fills in CHAT_BADGE.
    nav = "".join(f'<a href="{h}" class="{"on" if h == active else ""}">{esc(n)}'
                  f'{f"<span class=n>{review_count}</span>" if h == "/" and review_count else ""}'
                  f'{CHAT_BADGE if h == "/chat" else ""}</a>' for h, n in tabs)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Agent tickets</title>'
            f'<style>{CSS}</style><script>{JS}</script></head><body>'
            f'<header class="top"><b>Agent tickets</b><nav>{nav}</nav>'
            f'<select id="theme" class="theme" aria-label="Theme" onchange="setTheme(this.value)">'
            f'<option value="">Auto</option><option value="light">Light</option><option value="dark">Dark</option>'
            f'<option value="goth">Goth rave</option></select></header><main>{body}</main></body></html>')


def card(t):
    last = t.get("last_comment")
    snip = (f'<div class="snip"><b>{esc(last["author"])}:</b> {esc(last["body"][:240])}</div>' if last
            else (f'<div class="snip">{esc(t["description"][:240])}</div>' if t["description"] else ""))
    extras = []
    if t.get("comment_count"):
        extras.append(f'{t["comment_count"]} comment{"s" if t["comment_count"] != 1 else ""}')
    if t.get("attachment_count"):
        extras.append(f'{t["attachment_count"]} file{"s" if t["attachment_count"] != 1 else ""}')
    if t.get("checklist") and t["checklist"]["total"]:
        extras.append(f'☑ {t["checklist"]["done"]}/{t["checklist"]["total"]}')
    pr = '<span class="p1">High · </span>' if t["priority"] == 1 else ""
    if t.get("assignee"):
        extras.insert(0, f'<b>{esc(t["assignee"])}</b>')
    tags = ""
    if t.get("blocked_by"):
        tags += f'<span class="tag blocked">blocked by {", ".join(t["blocked_by"])}</span> '
    if t.get("epic_title"):
        tags += f'<span class="tag epic">{esc(t["epic_title"])}</span>'
    if tags:
        snip = f'<div style="margin-top:6px">{tags}</div>' + snip
    return (f'<a class="card" href="/t/{t["id"]}"><div class="row"><span class="id">{t["id"]}</span>'
            f'<span class="t">{esc(t["title"])}</span></div>'
            f'<div class="meta">{pr}updated {ago(t["updated_at"])}{" · " + " · ".join(extras) if extras else ""}</div>{snip}</a>')


def column(title, tickets, empty):
    inner = "".join(card(t) for t in tickets) or f'<p class="empty">{esc(empty)}</p>'
    return f'<section><h2>{esc(title)} ({len(tickets)})</h2>{inner}</section>'


def render_dashboard(store):
    by = {s: store.tickets([s], kind="task", proposed=False) for s in ("review", "in_progress", "ready")}
    epics = [e for e in store.tickets(["backlog", "ready", "in_progress", "review"], kind="epic", proposed=False)]
    rows = []
    for e in epics:
        pr = e["progress"]
        pct = round(100 * pr["done"] / pr["total"]) if pr["total"] else 0
        doing = pr["by_status"].get("in_progress", 0)
        rows.append(f'<a class="card" href="/t/{e["id"]}"><div class="row"><span class="id">{e["id"]}</span>'
                    f'<span class="t">{esc(e["title"])}</span></div><div class="meta">{pr["done"]} of {pr["total"]} done'
                    f'{f" · {doing} in progress" if doing else ""}</div><div class="bar"><i style="width:{pct}%"></i></div></a>')
    proposals = len(store.tickets(["backlog"], proposed=True))
    body = ('<h1>In flight</h1><div class="cols">'
            + column("Needs your review", by["review"], "Nothing waiting on you.")
            + column("In progress", by["in_progress"], "No agent is working on anything.")
            + column("Ready to work", by["ready"], "Nothing cleared for agents. Promote something from the backlog.")
            + '</div>'
            + (f'<p class="banner prop">Agents proposed {proposals} ticket{"s" if proposals != 1 else ""}: '
               f'<a href="/backlog">review them in the backlog</a>.</p>' if proposals else "")
            + f'<h2>Epics ({len(epics)})</h2>' + ("".join(rows) or '<p class="empty">No open epics.</p>')
            + '<script>setInterval(() => { if (!document.hidden) location.reload(); }, 60000);</script>')
    return page("Dashboard", body, "/", len(by["review"]))


PROPOSAL_JS = """
async function review(tid, action, ready){
  try { await api('POST', '/api/tickets/' + tid + '/' + action, action === 'accept' ? {ready} : {}); location.reload(); }
  catch (e) { fail(e); }
}
"""


def proposal_row(t, children=()):
    kind = '<span class="tag epic">Epic</span> ' if t["kind"] == "epic" else ""
    deps = f' · after {", ".join(t["blocked_by"])}' if t.get("blocked_by") else ""
    more = f" (and its {len(children)} proposed ticket{'s' if len(children) != 1 else ''})" if children else ""
    tid = t["id"]
    head = (f'<div class="item prop"><a href="/t/{tid}"><span class="id">{tid}</span> {kind}<b>{esc(t["title"])}</b>'
            f'<div class="meta">proposed by {esc(t["created_by"])} {ago(t["created_at"])}{deps}</div></a><div class="row">'
            f'<button class="primary" onclick="review(\'{tid}\',\'accept\',false)" title="Accept{esc(more)}">Accept</button>'
            + ("" if t["kind"] == "epic" else
               f'<button onclick="review(\'{tid}\',\'accept\',true)">Accept &amp; make ready</button>')
            + f'<button onclick="review(\'{tid}\',\'decline\')" title="Decline{esc(more)}">Decline</button></div></div>')
    if children:
        head += '<div class="sub">' + "".join(proposal_row(c) for c in children) + "</div>"
    return head


def render_list(store, status, q):
    items = store.tickets([status], q=q or None, recent_first=status == "done",
                          proposed=False if status == "backlog" else None)
    props = ""
    if status == "backlog" and not q:
        proposed = store.tickets(["backlog"], proposed=True)
        ids = {t["id"] for t in proposed}
        epics = [t for t in proposed if t["kind"] == "epic"]
        loose = [t for t in proposed if t["kind"] != "epic" and t.get("epic_id") not in ids]
        blocks = [proposal_row(e, [t for t in proposed if t.get("epic_id") == e["id"]]) for e in epics]
        blocks += [proposal_row(t) for t in loose]
        if blocks:
            props = (f'<h2>Proposed by agents ({len(proposed)})</h2><p class="meta">Accepting or declining an epic does the same '
                     f'to its proposed tickets.</p><div class="list">{"".join(blocks)}</div><h2>Accepted</h2>'
                     f'<script>{PROPOSAL_JS}</script>')
    rows = []
    for t in items:
        btn = (f'<button class="primary" onclick="setStatus(\'{t["id"]}\',\'ready\')">Make ready</button>'
               if status == "backlog" else "")
        pr = '<span class="p1">High · </span>' if t["priority"] == 1 else ""
        if t["kind"] == "epic":
            pr = '<span class="tag epic">Epic</span> ' + pr
        if t.get("declined"):
            pr = '<span class="tag">Declined</span> ' + pr
        if t.get("blocked_by"):
            pr += f'<span class="tag blocked">after {", ".join(t["blocked_by"])}</span> '
        rows.append(f'<div class="item"><a href="/t/{t["id"]}"><span class="id">{t["id"]}</span> '
                    f'<b>{esc(t["title"])}</b><div class="meta">{pr}{"created" if status == "backlog" else "finished"} '
                    f'{ago(t["created_at"] if status == "backlog" else t["updated_at"])}</div></a>{btn}</div>')
    title = "Backlog" if status == "backlog" else "Done"
    search = (f'<form class="row" method="get" style="margin-bottom:12px"><input type="search" name="q" value="{esc(q)}" '
              f'placeholder="Search {title.lower()}" aria-label="Search"><button>Search</button></form>')
    hint = ('<p class="meta">Ideas waiting to be cleared. "Make ready" lets agents pick one up.</p>' if status == "backlog"
            else '<p class="meta">Work you approved. <a href="/api/export">Download a backup</a> of everything '
                 '(tickets, comments, history and files).</p>')
    body = f'<h1>{title}</h1>{hint}{search}{props}<div class="list">{"".join(rows) or "<p class=empty>Nothing here.</p>"}</div>'
    return page(title, body, "/" + status, len(store.tickets(["review"])))


def assignee_options(store, current=None):
    """<option>s for an assignee picker: no one, then active users."""
    opts = ['<option value="">No one</option>']
    for u in store.users():
        if u["disabled"] and u["username"] != current:
            continue
        sel = " selected" if current and u["username"].lower() == current.lower() else ""
        opts.append(f'<option value="{esc(u["username"])}"{sel}>{esc(u["username"])} ({u["kind"]})</option>')
    return "".join(opts)


def epic_options(store, current=None):
    opts = ['<option value="">None</option>']
    for e in store.tickets(["backlog", "ready", "in_progress", "review"], kind="epic"):
        sel = " selected" if current and e["id"] == norm_id(current) else ""
        opts.append(f'<option value="{e["id"]}"{sel}>{e["id"]} · {esc(e["title"])}</option>')
    return "".join(opts)


def render_new(store, epic=None, prefill=("", "")):
    opts = "".join(f'<option value="{s}"{" selected" if s == "backlog" else ""}>{LABELS[s]}</option>' for s in STATUSES)
    body = f"""<h1>New ticket</h1><form class="stack box" id="nf">
<label>Title<input type="text" id="nt-title" required value="{esc(prefill[0])}"></label>
<label>Description<textarea id="nt-desc" rows="8">{esc(prefill[1])}</textarea></label>
<p class="meta" style="margin:-4px 0 0">Link wiki pages with <code>[[section/page]]</code>: every wiki page has a Copy link button.</p>
<div class="row"><label>Status <select id="nt-status">{opts}</select></label>
<label>Priority <select id="nt-prio"><option value="1">High</option><option value="2" selected>Normal</option><option value="3">Low</option></select></label>
<label>Assign to <select id="nt-who">{assignee_options(store)}</select></label></div>
<div class="row"><label>Kind <select id="nt-kind"><option value="task">Ticket</option><option value="epic">Epic (a plan that groups tickets)</option></select></label>
<label>Epic <select id="nt-epic">{epic_options(store, epic)}</select></label></div>
<label>Must be done first (ticket ids, optional)<input type="text" id="nt-deps" placeholder="amber-otter, quiet-falcon"></label>
<div><button class="primary">Create</button></div></form>
<script>const $ = (id) => document.getElementById(id);
$('nf').onsubmit = async (e) => {{ e.preventDefault();
 try {{ const t = await api('POST', '/api/tickets', {{title: $('nt-title').value, description: $('nt-desc').value,
   status: $('nt-status').value, priority: +$('nt-prio').value,
   assignee: $('nt-who').value || null, kind: $('nt-kind').value, epic_id: $('nt-epic').value || null,
   depends_on: $('nt-deps').value}});
   location = '/t/' + t.id; }} catch (err) {{ fail(err); }} }};</script>"""
    return page("New ticket", body, "/new", len(store.tickets(["review"])))


def render_users(store, me):
    rows = []
    for u in store.users():
        state = '<span class="chip s-backlog">Disabled</span>' if u["disabled"] else ""
        you = ' <span class="meta">(you)</span>' if u["username"].lower() == me.lower() else ""
        seen = ago(u["last_seen"]) if u["last_seen"] else "never"
        toggle = "Enable" if u["disabled"] else "Disable"
        name = json.dumps(u["username"])
        rows.append(f'<div class="item"><div style="min-width:0;flex:1"><b>{esc(u["username"])}</b>{you} '
                    f'<span class="chip {"s-in_progress" if u["kind"] == "human" else "s-ready"}">{u["kind"]}</span> {state}'
                    f'<div class="meta">added {when(u["created_at"])}{" by " + esc(u["created_by"]) if u["created_by"] else ""}'
                    f' · last seen {seen}</div></div><div class="row">'
                    f"<button onclick='newToken({esc(name)})'>New token</button>"
                    f"<button onclick='toggleUser({esc(name)}, {str(not u['disabled']).lower()})'>{toggle}</button></div></div>")
    body = f"""<h1>Users</h1>
<p class="meta">Humans can do anything, including managing users. Agents use the API: they can move tickets between
Ready, In progress and Needs review, and comment. Each user has their own token; only a hash of it is stored, so a
token is shown once, when it's made. Give agents theirs as <code>Authorization: Bearer &lt;token&gt;</code>.</p>
<div id="tok" class="box" hidden style="margin-bottom:14px;border-color:var(--ready)"><b id="tokwho"></b>
<p class="meta">Copy it now: it won't be shown again.</p><div class="row"><input type="text" id="tokval" readonly>
<button class="primary" onclick="copyTok()">Copy</button></div></div>
<div class="list">{"".join(rows)}</div>
<h2>Add a user</h2><form class="row box" id="uf"><input type="text" id="uname" placeholder="name, e.g. agent-1"
 aria-label="Username" style="flex:1;min-width:160px" required><select id="ukind" style="width:auto" aria-label="Kind">
 <option value="agent">Agent</option><option value="human">Human</option></select><button class="primary">Add</button></form>
<script>
const $ = (id) => document.getElementById(id);
function showTok(who, token) {{ $('tok').hidden = false; $('tokwho').textContent = 'Token for ' + who; $('tokval').value = token;
  $('tokval').select(); window.scrollTo(0, 0); }}
async function copyTok() {{ try {{ await navigator.clipboard.writeText($('tokval').value); }} catch (e) {{ $('tokval').select(); }} }}
async function newToken(name) {{
  if (!confirm('Give ' + name + ' a new token? Their current one stops working immediately.')) return;
  try {{ const r = await api('POST', '/api/users/' + encodeURIComponent(name) + '/token'); showTok(name, r.token); }} catch (e) {{ fail(e); }}
}}
async function toggleUser(name, disable) {{
  try {{ await api('PATCH', '/api/users/' + encodeURIComponent(name), {{disabled: disable}}); location.reload(); }} catch (e) {{ fail(e); }}
}}
$('uf').onsubmit = async (e) => {{ e.preventDefault();
  try {{ const r = await api('POST', '/api/users', {{username: $('uname').value.trim(), kind: $('ukind').value}});
    showTok(r.user.username + ' (' + r.user.kind + ')', r.token); $('uname').value = '';
    const note = document.createElement('p'); note.className = 'meta'; note.textContent = 'Reload to see them in the list.';
    $('tok').appendChild(note); }} catch (err) {{ fail(err); }} }};
</script>"""
    return page("Users", body, "/users", len(store.tickets(["review"])))


def render_guidance(store):
    agents = [u for u in store.users() if u["kind"] == "agent" and not u["disabled"]]
    per = "".join(f'''<div class="box" style="margin-bottom:10px"><b>{esc(u["username"])}</b>
<textarea class="ug" data-user="{esc(u["username"])}" rows="3" aria-label="Notes for {esc(u["username"])}"
 placeholder="Extra notes just for {esc(u["username"])}, e.g. be extra terse; ask before deleting anything">{esc(u["guidance"] or "")}</textarea></div>'''
                  for u in agents) or '<p class="empty">No active agents yet. Add them on the Users page.</p>'
    body = f"""<h1>Guidance</h1>
<p class="meta">What agents are told about how to work here: how to start a ticket, how often and how much to comment,
how to hand back. They get it with every ticket they claim and at the top of <a href="/api">/api</a>, so a change
applies to the next ticket any agent picks up. Markdown is fine.</p>
<h2>House rules (every agent)</h2><form class="stack box" id="gf">
<textarea id="house" rows="18" aria-label="House rules">{esc(store.house_guidance())}</textarea>
<div class="row"><button class="primary">Save</button><button type="button" id="reset">Reset to the default</button>
<span id="gmsg" class="meta"></span></div></form>
<h2>Per agent</h2><p class="meta">Added after the house rules for that agent only.</p>{per}
<div class="row"><button class="primary" id="saveper">Save agent notes</button><span id="pmsg" class="meta"></span></div>
<script>
const $ = (id) => document.getElementById(id);
$('gf').onsubmit = async (e) => {{ e.preventDefault();
  try {{ await api('PUT', '/api/guidance', {{guidance: $('house').value}}); $('gmsg').textContent = 'Saved.'; }} catch (err) {{ fail(err); }} }};
$('reset').onclick = async () => {{ if (!confirm('Replace the house rules with the default text?')) return;
  try {{ await api('PUT', '/api/guidance', {{guidance: null}}); location.reload(); }} catch (err) {{ fail(err); }} }};
$('saveper').onclick = async () => {{
  try {{ for (const t of document.querySelectorAll('.ug'))
      await api('PATCH', '/api/users/' + encodeURIComponent(t.dataset.user), {{guidance: t.value}});
    $('pmsg').textContent = 'Saved.'; }} catch (err) {{ fail(err); }} }};
</script>"""
    return page("Guidance", body, "/guidance", len(store.tickets(["review"])))


TEXT_EXT = {".md", ".txt", ".log", ".py", ".sh", ".bash", ".js", ".mjs", ".ts", ".json", ".yaml", ".yml", ".toml",
            ".ini", ".cfg", ".conf", ".csv", ".tsv", ".diff", ".patch", ".sql", ".xml", ".html", ".css", ".rs", ".go",
            ".c", ".h", ".cpp", ".java", ".kt", ".rb", ".lua", ".ps1", ".bat", ".env.example", ".service", ".gradle"}
TEXT_MIME = {"application/json", "application/javascript", "application/xml", "application/x-sh", "application/toml",
             "application/x-yaml", "application/yaml", "application/sql"}
PREVIEW_BYTES = 256 * 1024
PREVIEW_LINES = 400


def text_preview(store, a):
    """The first lines of a small text file (script, markdown, log), or None."""
    ext = Path(a["filename"]).suffix.lower()
    if not (a["mime"].startswith("text/") or a["mime"] in TEXT_MIME or ext in TEXT_EXT) or a["size"] > PREVIEW_BYTES:
        return None
    try:
        rel = a.get("_path") or store.attachment(a["id"])["path"]
        raw = (store.files / rel).read_bytes()[:PREVIEW_BYTES]
        if b"\0" in raw:
            return None
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError, ApiError):
        return None
    lines = text.splitlines()
    more = f"\n… {len(lines) - PREVIEW_LINES} more lines in the download" if len(lines) > PREVIEW_LINES else ""
    return "\n".join(lines[:PREVIEW_LINES]) + more


def render_attachment(a, store=None):
    name = esc(a["filename"])
    link = f'<a href="{a["url"]}" target="_blank">{name}</a> <span class="meta">{size_text(a["size"])}</span>'
    media_type = a["mime"].split("/")[0]
    if media_type not in ("image", "video", "audio"):
        # Anything else (tarballs, scripts, docs) is a download; small text files get a preview.
        preview = text_preview(store, a) if store else None
        body = (f'<details><summary>Preview</summary><pre class="pv">{esc(preview)}</pre></details>'
                if preview is not None else "")
        return (f'<div class="att file"><a class="dl" href="{a["url"]}" download="{name}">⬇ {name}</a> '
                f'<span class="meta">{size_text(a["size"])}</span>{body}</div>')
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
        files = "".join(render_attachment(a, store) for a in by_comment.get(c["id"], []))
        role = "agent" if c["role"] == "agent" else ""
        items.append((c["created_at"], f'<div class="c {role}"><span class="who {role}">{esc(c["author"])}</span> '
                                       f'<span class="meta" title="{when(c["created_at"])}">{ago(c["created_at"])}</span>'
                                       f'<div class="body">{linkify(c["body"])}</div>{files}</div>'))
    for a in loose:
        items.append((a["created_at"], f'<div class="c {"agent" if a["role"] == "agent" else ""}"><span class="who">'
                                       f'{esc(a["author"])}</span> <span class="meta">attached a file · {ago(a["created_at"])}</span>'
                                       f'{render_attachment(a, store)}</div>'))
    for e in t["events"]:
        if e["field"] == "created":
            text = f'{esc(e["actor"])} created it in {LABELS.get(e["new"], e["new"])}'
        elif e["field"] == "proposed":
            text = f'{esc(e["actor"])} proposed it'
        elif e["field"] == "accepted":
            text = f'{esc(e["actor"])} accepted it into {LABELS.get(e["new"], e["new"])}'
        elif e["field"] == "declined":
            text = f'{esc(e["actor"])} <b>declined</b> it'
        elif e["field"] == "depends_on":
            text = (f'{esc(e["actor"])} said <a href="/t/{esc(e["new"])}">{esc(e["new"])}</a> must be done first' if e["new"]
                    else f'{esc(e["actor"])} removed the dependency on {esc(e["old"])}')
        elif e["field"] == "checklist":
            text = f'{esc(e["actor"])} reported the checklist: {esc(e["new"])} done'
        elif e["field"] == "epic_id":
            text = (f'{esc(e["actor"])} moved it into epic <a href="/t/{esc(e["new"])}">{esc(e["new"])}</a>' if e["new"]
                    else f'{esc(e["actor"])} took it out of its epic')
        elif e["field"] == "status":
            text = f'{esc(e["actor"])} moved it from {LABELS.get(e["old"], e["old"])} to <b>{LABELS.get(e["new"], e["new"])}</b>'
        elif e["field"] == "assignee":
            text = (f'{esc(e["actor"])} assigned it to <b>{esc(e["new"])}</b>' if e["new"]
                    else f'{esc(e["actor"])} unassigned it')
        elif e["field"] == "description":
            text = f'{esc(e["actor"])} edited the description'
        else:
            text = f'{esc(e["actor"])} changed {esc(e["field"]).replace("_", " ")} to {esc(e["new"] or "nothing")}'
        items.append((e["at"], f'<div class="ev">{text} · <span title="{when(e["at"])}">{ago(e["at"])}</span></div>'))
    items.sort(key=lambda x: x[0])
    timeline = "".join(h for _, h in items) or '<p class="empty">No activity yet.</p>'

    wiki_box = ""
    if t["wiki_links"]:
        lis = "".join(f'<li><a class="wl{" missing" if w.get("missing") else ""}" href="/wiki/{w["path"]}">'
                      f'{esc(w["title"] or w["path"])}</a>{" <span class=meta>(no such page yet)</span>" if w.get("missing") else ""}'
                      f'</li>' for w in t["wiki_links"])
        wiki_box = f'<h2 style="margin-top:0">Wiki pages</h2><div class="box"><ul class="ck">{lis}</ul></div>'
    ck = t["checklist"]
    if ck is None:
        ckhtml = '<p class="meta">None reported yet. The agent working the ticket uploads it as it goes.</p>'
    else:
        lis = "".join(f'<li class="note">{esc(i["text"])}</li>' if i.get("note") else
                      f'<li>{"<span class=x>☑</span>" if i["done"] else "☐"} {esc(i["text"])}</li>' for i in ck["items"])
        pct = round(100 * ck["done"] / ck["total"]) if ck["total"] else 0
        ckhtml = (f'<p class="meta">Reported by {esc(ck["updated_by"])} {ago(ck["updated_at"])}</p>'
                  + (f'<div class="meta">{ck["done"]} of {ck["total"]} done</div><div class="bar"><i style="width:{pct}%"></i></div>'
                     if ck["total"] else "") + f'<ul class="ck">{lis}</ul>')

    def chip(x):
        st = x["status"]
        label = "Declined" if x.get("declined") else LABELS[st]
        return f'<span class="chip s-{st}">{label}</span>'

    def link_list(items, removable=False):
        lis = "".join(f'<li><a href="/t/{x["id"]}"><span class="id">{x["id"]}</span> {esc(x["title"])}</a> {chip(x)}'
                      + (f' <button onclick="rmDep(\'{x["id"]}\')" aria-label="Remove {x["id"]}" '
                         f'style="padding:1px 7px">×</button>' if removable else "") + "</li>" for x in items)
        return f'<ul class="ck deps">{lis}</ul>' if lis else '<p class="meta">None.</p>'

    banners = ""
    if t["proposed"]:
        banners += (f'<div class="banner prop">Proposed by {esc(t["created_by"])}. '
                    f'<button class="primary" onclick="review(TID,\'accept\',false)">Accept</button> '
                    + ("" if t["kind"] == "epic" else '<button onclick="review(TID,\'accept\',true)">Accept &amp; make ready</button> ')
                    + '<button onclick="review(TID,\'decline\')">Decline</button>'
                    + (' <span class="meta">(its proposed tickets go the same way)</span>' if t["kind"] == "epic" else "")
                    + '</div>')
    if t["blocked_by"]:
        banners += (f'<div class="banner">Blocked: {", ".join(f"<a href=/t/{b}>{b}</a>" for b in t["blocked_by"])} '
                    f'must be done first. It can\'t start until then; comments are fine.</div>')
    epic_box = ""
    if t["kind"] == "epic":
        pr = t["progress"]
        pct = round(100 * pr["done"] / pr["total"]) if pr["total"] else 0
        epic_box = (f'<h2>Tickets in this epic ({pr["total"]})</h2><div class="box">'
                    f'<div class="meta">{pr["done"]} of {pr["total"]} done</div><div class="bar"><i style="width:{pct}%"></i></div>'
                    + link_list(t["tickets"]) + f'<p><a href="/new?epic={t["id"]}">Add a ticket to this epic</a></p></div>')
    if t["kind"] == "epic":
        side_links = ""
    else:
        side_links = (f'<h2>Epic</h2><div class="box"><select id="epic" aria-label="Epic" onchange="setEpic(this.value)">'
                      f'{epic_options(store, t["epic_id"])}</select></div>'
                      f'<h2>Must be done first</h2><div class="box">{link_list(t["depends_on"], True)}'
                      f'<form class="row" id="df"><input type="text" id="dep" placeholder="ticket id" aria-label="Add a predecessor" '
                      f'style="flex:1;min-width:90px"><button>Add</button></form></div>'
                      f'<h2>Unblocks</h2><div class="box">{link_list(t["unblocks"])}</div>')

    s = t["status"]
    actions = {
        "backlog": [("ready", "Make ready", True)],
        "ready": [("backlog", "Back to backlog", False)],
        "in_progress": [("review", "Needs review", False), ("ready", "Release to ready", False)],
        "review": [("done", "Approve", True), ("in_progress", "Send back", False)],
        "done": [("in_progress", "Reopen", False)],
    }[s]
    if t["kind"] == "epic":  # epics aren't worked, only closed when their plan is finished
        actions = [("backlog", "Reopen epic", False)] if s == "done" else [("done", "Close epic", False)]
    if t["proposed"]:  # the proposal banner has the buttons
        actions = []
    btns = "".join(f'<button class="{"primary" if p else ""}" onclick="setStatus(\'{t["id"]}\',\'{to}\')">{esc(lbl)}</button>'
                   for to, lbl, p in actions)
    move_opts = "".join(f'<option value="{x}">{LABELS[x]}</option>' for x in STATUSES if x != s)
    body = f"""<div class="row" style="margin-bottom:6px"><span class="id">{t['id']}</span>
<span class="chip s-{s}">{LABELS[s]}</span>{'<span class="p1">High priority</span>' if t['priority'] == 1 else ''}
<span class="meta">{'assigned to <b>' + esc(t['assignee']) + '</b>' if t['assignee'] else 'unassigned'}</span>
{'<span class="tag epic">Epic</span>' if t['kind'] == 'epic' else ''}
{f'<span class="meta">in epic <a href="/t/{t["epic_id"]}">{t["epic_id"]} · {esc(t.get("epic_title") or "")}</a></span>' if t['epic_id'] else ''}</div>
<h1>{esc(t['title'])}</h1>{banners}
<div class="row" style="margin-bottom:16px">{btns}</div>
<div class="detail"><div>
 <div class="box"><div class="desc">{linkify(t['description']) or '<span class="meta">No description.</span>'}</div></div>
 {epic_box}
 <h2>Activity</h2><div class="tl">{timeline}</div>
 <h2>Comment</h2><form class="stack box" id="cf">
  <textarea id="cbody" placeholder="Feedback, a decision, or a question for the agent" aria-label="Comment"></textarea>
  <div class="row"><input type="file" id="cfiles" multiple aria-label="Attach files">
  <label class="meta">then <select id="cmove" style="width:auto"><option value="">keep status</option>{move_opts}</select></label>
  <button class="primary" id="csend">Post</button></div></form>
</div><aside>
 <h2 style="margin-top:0">Checklist</h2><div class="box">{ckhtml}</div>
 {wiki_box}
 <h2>Assigned to</h2><div class="box"><select id="who" aria-label="Assigned to"
  onchange="assign(this.value)">{assignee_options(store, t['assignee'])}</select></div>
 {side_links}
 <h2>Details</h2><div class="box"><dl class="kv"><dt>Priority</dt><dd>{PRIORITIES[t['priority']]}</dd>
  <dt>Created</dt><dd>{when(t['created_at'])} by {esc(t['created_by'])}</dd><dt>Updated</dt><dd>{ago(t['updated_at'])}</dd></dl>
  <details style="margin-top:10px"><summary>Edit</summary><form class="stack" id="ef" style="margin-top:8px">
   <label>Title<input type="text" id="etitle" value="{esc(t['title'])}"></label>
   <label>Description<textarea id="edesc" rows="8">{esc(t['description'])}</textarea></label>
   <label>Priority<select id="eprio">{''.join(f'<option value="{k}"{" selected" if k == t["priority"] else ""}>{v}</option>' for k, v in PRIORITIES.items())}</select></label>
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
async function assign(name) {{ try {{ await api('PATCH', '/api/tickets/' + TID, {{assignee: name || null}}); location.reload(); }}
  catch (err) {{ fail(err); }} }}
async function setEpic(id) {{ try {{ await api('PATCH', '/api/tickets/' + TID, {{epic_id: id || null}}); location.reload(); }}
  catch (err) {{ fail(err); }} }}
async function rmDep(id) {{ try {{ await api('DELETE', '/api/tickets/' + TID + '/deps/' + id); location.reload(); }}
  catch (err) {{ fail(err); }} }}
const df = document.getElementById('df');
if (df) df.onsubmit = async (e) => {{ e.preventDefault();
  try {{ await api('POST', '/api/tickets/' + TID + '/deps', {{depends_on: $('dep').value.trim()}}); location.reload(); }}
  catch (err) {{ fail(err); }} }};
{PROPOSAL_JS}
document.getElementById('ef').onsubmit = async (e) => {{
  e.preventDefault();
  try {{ await api('PATCH', '/api/tickets/' + TID, {{title: $('etitle').value, description: $('edesc').value,
    priority: +$('eprio').value}}); location.reload(); }} catch (err) {{ fail(err); }}
}};
</script>"""
    return page(f"{t['id']} {t['title']}", body, "", len(store.tickets(["review"])))


TICKET_WORD = re.compile(r"\b(?:[A-Za-z]+-[A-Za-z]+|[A-Z]{4,5})\b")


def chat_text(store, body):
    """A message's text: escaped, with links, and ticket ids that exist linked to their tickets."""
    parts = LINKABLE.split(esc(body))
    ids = {norm_id(w) for w in TICKET_WORD.findall(" ".join(parts[::2]))}
    known = {r[0] for r in store.db().execute(
        f"SELECT id FROM tickets WHERE id IN ({','.join('?' * len(ids))})", list(ids))} if ids else set()
    out = []
    for i, p in enumerate(parts):
        if i % 2:
            out.append(link_part(p))
        else:
            out.append(TICKET_WORD.sub(
                lambda m: f'<a href="/t/{norm_id(m[0])}">{m[0]}</a>' if norm_id(m[0]) in known else m[0], p))
    return "".join(out)


def render_message(store, m, viewer):
    role = "agent" if m["role"] == "agent" else ""
    mine = " mine" if m["author"].lower() == viewer.lower() else ""
    files = "".join(render_attachment(f, store) for f in m["files"])
    return (f'<div class="msg {role}{mine}" id="m{m["id"]}"><div><span class="who {role}">{esc(m["author"])}</span> '
            f'<span class="meta" title="{when(m["created_at"])}">{ago(m["created_at"])}</span>'
            f'<a class="mk" href="/new?from_message={m["id"]}" title="Make a ticket from this message">→ ticket</a></div>'
            f'<div class="body">{chat_text(store, m["body"])}</div>{files}</div>')


def ticket_from_message(store, mid):
    """Title and description for a new ticket that quotes a chat message."""
    m = store.message(mid)
    c = store.conversation(m["conversation_id"])
    first = m["body"].strip().splitlines()[0] if m["body"].strip() else ""
    quoted = "\n".join("> " + line for line in m["body"].splitlines())
    return first[:100], f"From chat ({c['name']}), {m['author']} wrote:\n\n{quoted}\n"


CHAT_LIST_JS = """
const $ = (id) => document.getElementById(id);
document.getElementById('sf').onsubmit = async (e) => {
  e.preventDefault();
  const members = [...document.querySelectorAll('input[name=who]:checked')].map(x => x.value);
  try { const c = await api('POST', '/api/chat', {title: $('st').value, members, body: $('sb').value});
        location.href = '/chat/' + c.id; }
  catch (err) { fail(err); }
};
"""


def render_chat_list(store, viewer):
    rows = []
    for c in store.conversations(viewer):
        last = c["last_message"]
        snip = f'<b>{esc(last["author"])}:</b> {esc(last["body"][:160])}' if last else "No messages yet."
        badge = f' <span class="count">{c["unread"]}</span>' if c.get("unread") else ""
        group = f' <span class="meta">· {len(c["members"])} people</span>' if c["title"] else ""
        rows.append(f'<div class="item{" unread" if c.get("unread") else ""}"><a href="/chat/{c["id"]}"><b>{esc(c["name"])}</b>'
                    f'{badge}{group}<div class="snip">{snip}</div></a><span class="meta">{ago(c["updated_at"])}</span></div>')
    people = [u for u in store.users() if not u["disabled"] and u["username"].lower() != viewer.lower()]
    picks = "".join(f'<label><input type="checkbox" name="who" value="{esc(u["username"])}"> {esc(u["username"])}'
                    f'{" <span class=meta>(agent)</span>" if u["kind"] == "agent" else ""}</label>' for u in people)
    body = f"""<h1>Chat</h1>
<p class="meta">For questions and planning that aren't about one ticket. Anything about a ticket goes on the ticket.
Agents are told about new messages with every ticket update, and answer them first.</p>
<details class="box" style="margin-bottom:14px"{" open" if not rows else ""}><summary><b>Start a conversation</b></summary>
<form class="stack" id="sf" style="margin-top:10px">
<div class="who-pick" role="group" aria-label="With">{picks or '<span class="meta">No one else yet.</span>'}</div>
<label>Title <span class="meta">(optional; for a group or a topic)</span><input type="text" id="st"></label>
<label>Message<textarea id="sb" placeholder="What's on your mind?"></textarea></label>
<button class="primary">Start</button></form></details>
<div class="list clist">{"".join(rows) or '<p class="empty">No conversations yet.</p>'}</div>
<script>{CHAT_LIST_JS}</script>"""
    return page("Chat", body, "/chat", len(store.tickets(["review"])))


CHAT_JS = """
const room = document.getElementById('chatroom'), CID = room.dataset.cid, $ = (id) => document.getElementById(id);
const box = $('msgs');
let LAST = +room.dataset.last, busy = false;
function nearBottom(){ return window.innerHeight + window.scrollY >= document.body.scrollHeight - 160; }
async function poll(force){
  if (busy) { if (force) setTimeout(() => poll(true), 250); return; }
  if (document.hidden && !force) return;
  busy = true;
  try {
    const r = await fetch('/chat/' + CID + '/since/' + LAST, {credentials: 'same-origin'});
    if (r.ok) {
      const html = await r.text(), last = +r.headers.get('X-Last-Id');
      if (html.trim()) {
        const stick = force || nearBottom();
        document.getElementById('none')?.remove();
        box.insertAdjacentHTML('beforeend', html);
        if (stick) window.scrollTo(0, document.body.scrollHeight);
      }
      if (last) LAST = last;
    }
  } catch (e) {} finally { busy = false; }
}
setInterval(poll, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
window.scrollTo(0, document.body.scrollHeight);
async function chatUpload(file, mid){
  const q = new URLSearchParams({filename: file.name, message_id: mid});
  const r = await fetch('/api/chat/' + CID + '/files?' + q, {method: 'POST',
    headers: {'Content-Type': file.type || 'application/octet-stream', 'X-Tickets': '1'}, body: file, credentials: 'same-origin'});
  if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || ('HTTP ' + r.status)); }
}
$('mf').onsubmit = async (e) => {
  e.preventDefault();
  const btn = $('msend'), text = $('mbody').value.trim(), files = [...$('mfiles').files];
  if (!text && !files.length) return;
  btn.disabled = true; busy = true;  // no polling mid-send, so the message shows up with its files
  try {
    const m = await api('POST', '/api/chat/' + CID + '/messages', {body: text || '(attached files)'});
    for (const f of files) { btn.textContent = 'Uploading ' + f.name + '…'; await chatUpload(f, m.id); }
    $('mbody').value = ''; $('mfiles').value = '';
  } catch (err) { fail(err); }
  busy = false; btn.disabled = false; btn.textContent = 'Send';
  await poll(true);
};
$('mbody').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) $('mf').requestSubmit(); });
const am = $('am');
if (am) am.onsubmit = async (e) => { e.preventDefault();
  try { await api('POST', '/api/chat/' + CID + '/members', {username: $('amwho').value}); location.reload(); }
  catch (err) { fail(err); } };
"""


def render_chat(store, cid, viewer):
    c = store.conversation(cid, viewer)
    msgs = store.messages(c["id"], internal=True)
    store.mark_read(c["id"], viewer)
    last = msgs[-1]["id"] if msgs else 0
    others = [u for u in store.users() if not u["disabled"]
              and u["username"].lower() not in {m.lower() for m in c["members"]}]
    add = ""
    if others:
        opts = "".join(f'<option value="{esc(u["username"])}">{esc(u["username"])}</option>' for u in others)
        add = (f'<form class="row" id="am" style="margin-top:6px"><select id="amwho" aria-label="Add someone" '
               f'style="width:auto">{opts}</select><button>Add to conversation</button></form>')
    shown = "".join(render_message(store, m, viewer) for m in msgs)
    body = f"""<p class="meta" style="margin:0 0 4px"><a href="/chat">← Chat</a></p>
<h1 style="margin-bottom:4px">{esc(c["name"])}</h1>
<div class="meta">With {esc(", ".join(c["members"]) or "no one yet")}. Anyone can read and post here.</div>{add}
<div id="chatroom" data-cid="{c["id"]}" data-last="{last}" style="margin-top:14px">
<div class="chat" id="msgs">{shown or '<p class="empty" id="none">No messages yet. Say hello.</p>'}</div>
<form class="stack composer" id="mf"><textarea id="mbody" placeholder="Message (Ctrl+Enter sends)" aria-label="Message"></textarea>
<div class="row"><input type="file" id="mfiles" multiple aria-label="Attach files">
<button class="primary" id="msend" style="margin-left:auto">Send</button></div></form></div>
<script>{CHAT_JS}</script>"""
    return page(c["name"], body, "/chat", len(store.tickets(["review"])))


def wiki_inline(store, text):
    """One run of text: escaped, with `code`, links, ticket ids and [[wiki/links]]."""
    out = []
    for i, part in enumerate(re.split(r"(`[^`\n]+`)", text)):
        if i % 2:
            out.append(f"<code>{esc(part[1:-1])}</code>")
        else:
            out.append(chat_text(store, part))
    return "".join(out)


def wiki_html(store, text):
    """Plain text with a little structure: # headings, ``` code blocks; blank lines split paragraphs."""
    out, para, code = [], [], None

    def flush():
        if para:
            out.append(f"<p>{wiki_inline(store, chr(10).join(para))}</p>")
            para.clear()
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if code is None:
                flush()
                code = []
            else:
                out.append(f"<pre>{esc(chr(10).join(code))}</pre>")
                code = None
            continue
        if code is not None:
            code.append(line)
        elif m := re.match(r"(#{1,3})\s+(.*)", line):
            flush()
            n = len(m[1]) + 1
            out.append(f"<h{n}>{wiki_inline(store, m[2])}</h{n}>")
        elif not line.strip():
            flush()
        else:
            para.append(line)
    if code is not None:
        out.append(f"<pre>{esc(chr(10).join(code))}</pre>")
    flush()
    return "".join(out)


def wiki_tree_html(nodes):
    if not nodes:
        return ""
    return "<ul class=tree>" + "".join(
        f'<li><a href="/wiki/{n["path"]}">{esc(n["title"])}</a>{wiki_tree_html(n["children"])}</li>' for n in nodes) + "</ul>"


def wiki_parent_options(store, exclude=None, current=None):
    """Every page as a <select> option (indented), except `exclude` and what's under it."""
    opts = ['<option value="">(top level: a new section)</option>']

    def walk(nodes, depth):
        for n in nodes:
            if exclude and (n["path"] == exclude or n["path"].startswith(exclude + "/")):
                continue
            sel = " selected" if current and n["path"] == current else ""
            opts.append(f'<option value="{esc(n["path"])}"{sel}>{"— " * depth}{esc(n["title"])}</option>')
            walk(n["children"], depth + 1)
    walk(store.wiki_tree(), 0)
    return "".join(opts)


WIKI_INTRO = ("Shared knowledge for humans and agents: onboarding, repos, conventions, key details. "
              "Every page lives in a section or under another page. Plain text; <code># Heading</code>, "
              "<code>```</code> code blocks, <code>`code`</code>, and <code>[[section/page]]</code> links. "
              "Agents can read it but not change it.")


def render_wiki_index(store, role, q):
    if q:
        hits = store.wiki_search(q)
        rows = "".join(f'<div class="item"><a href="/wiki/{h["path"]}"><b>{esc(h["title"])}</b>'
                       f'<div class="meta">{esc(h["path"])}</div><div class="snip">{esc(h["snippet"])}</div></a></div>'
                       for h in hits)
        listing = f'<h2>Results for “{esc(q)}”</h2><div class="list">{rows or "<p class=empty>Nothing found.</p>"}</div>'
    else:
        tree = wiki_tree_html(store.wiki_tree())
        listing = f'<div class="box">{tree or "<p class=empty>No pages yet.</p>"}</div>'
    new = ('<p><a class="btn" href="/wiki-new" style="text-decoration:none;display:inline-block">New section</a></p>'
           if role == "human" else "")
    body = (f'<h1>Wiki</h1><p class="meta">{WIKI_INTRO}</p>'
            f'<form class="row" method="get" action="/wiki" style="margin-bottom:12px"><input type="search" name="q" '
            f'value="{esc(q)}" placeholder="Search the wiki" aria-label="Search the wiki" style="flex:1;min-width:0">'
            f'<button>Search</button></form>{new}{listing}')
    return page("Wiki", body, "/wiki", len(store.tickets(["review"])))


WIKI_EDIT_JS = """
const $ = (id) => document.getElementById(id);
const wf = $('wf');
if (wf) wf.onsubmit = async (e) => {
  e.preventDefault();
  const data = {title: $('wtitle').value, body: $('wbody').value};
  if ($('wslug')) data.slug = $('wslug').value;
  if ($('wparent')) data.parent = $('wparent').value;
  try {
    const p = WPATH ? await api('PUT', '/api/wiki/' + WPATH, data) : await api('POST', '/api/wiki', data);
    location.href = '/wiki/' + p.path;
  } catch (err) { fail(err); }
};
async function wdel(){
  if (!confirm('Delete this page?')) return;
  try { await api('DELETE', '/api/wiki/' + WPATH); location.href = '/wiki'; } catch (err) { fail(err); }
}
"""


def wiki_form(store, page_=None, parent=None):
    """The new-page or edit form (humans only)."""
    p = page_ or {}
    here = p.get("path")
    parent_path = here.rsplit("/", 1)[0] if here and "/" in here else (parent if not here else "")
    return f"""<form class="stack box" id="wf">
<label>Title<input type="text" id="wtitle" required value="{esc(p.get('title', ''))}"></label>
<div class="row"><label style="flex:1;min-width:180px">Under<select id="wparent">{wiki_parent_options(store, here, parent_path)}</select></label>
<label style="flex:1;min-width:140px">Address (optional)<input type="text" id="wslug" value="{esc(p.get('slug', ''))}"
 placeholder="from the title"></label></div>
<label>Text<textarea id="wbody" rows="18" style="font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace">{esc(p.get('body', ''))}</textarea></label>
<div class="row"><button class="primary">Save</button>{'<button type="button" onclick="wdel()">Delete page</button>' if here and not p.get('children') else ''}</div>
</form><script>const WPATH = {json.dumps(here or "")};{WIKI_EDIT_JS}</script>"""


def render_wiki_new(store, parent):
    parent = norm_wiki_path(parent)
    where = f" under {esc(store.wiki_page(parent)['title'])}" if parent else " (a new top-level section)"
    body = f'<p class="crumbs"><a href="/wiki">Wiki</a></p><h1>New page{where}</h1>{wiki_form(store, parent=parent)}'
    return page("New wiki page", body, "/wiki", len(store.tickets(["review"])))


COPY_JS = """
async function copyWikiLink(btn){
  const text = document.getElementById('wlink').textContent;
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; } catch (e) {
    // No clipboard API (plain http on a LAN address): the old way.
    const a = document.createElement('textarea'); a.value = text; document.body.appendChild(a); a.select();
    try { ok = document.execCommand('copy'); } catch (e2) {} a.remove();
  }
  if (!ok) { const r = document.createRange(); r.selectNodeContents(document.getElementById('wlink'));
    getSelection().removeAllRanges(); getSelection().addRange(r); }
  btn.textContent = ok ? 'Copied' : 'Selected: copy it'; setTimeout(() => { btn.textContent = 'Copy link'; }, 1600);
}
"""


def render_wiki_page(store, path, role, history=False, rev=None, edit=False):
    p = store.wiki_page(path)
    crumbs = " / ".join([f'<a href="/wiki">Wiki</a>'] + [f'<a href="/wiki/{c["path"]}">{esc(c["title"])}</a>'
                                                          for c in p["breadcrumbs"][:-1]])
    meta = f'Updated {ago(p["updated_at"])} by {esc(p["updated_by"])} · <a href="/wiki/{p["path"]}?history=1">History</a>'
    copy = (f'<div class="wlink"><code id="wlink">[[{p["path"]}]]</code>'
            f'<button type="button" onclick="copyWikiLink(this)">Copy link</button>'
            f'<span class="meta">Paste it into a ticket, comment or chat message.</span></div>'
            f'<script>{COPY_JS}</script>')
    if edit and role == "human":
        main = wiki_form(store, p)
    elif rev:
        r = store.wiki_revision(p["id"], int(rev))
        main = (f'<p class="banner prop">The version {esc(r["edited_by"])} saved {when(r["edited_at"])}. '
                f'<a href="/wiki/{p["path"]}">Current version</a></p><div class="box wk">{wiki_html(store, r["body"])}</div>')
    elif history:
        rows = "".join(f'<div class="item"><a href="/wiki/{p["path"]}?rev={r["id"]}"><b>{esc(r["title"])}</b>'
                       f'<div class="meta">{esc(r["edited_by"])} · {when(r["edited_at"])} · {r["size"]} characters</div></a></div>'
                       for r in store.wiki_revisions(p["id"]))
        main = f'<h2>History</h2><div class="list">{rows}</div>'
    else:
        main = f'<div class="box wk">{wiki_html(store, p["body"]) or "<p class=meta>No text yet.</p>"}</div>'
    kids = "".join(f'<li><a href="/wiki/{c["path"]}">{esc(c["title"])}</a></li>' for c in p["children"])
    actions = ""
    if role == "human" and not edit:
        actions = (f'<div class="row" style="margin:8px 0 14px"><a class="btn" href="/wiki/{p["path"]}?edit=1" '
                   f'style="text-decoration:none">Edit</a><a class="btn" href="/wiki-new?parent={p["path"]}" '
                   f'style="text-decoration:none">Add a page under this</a></div>')
    body = (f'<p class="crumbs">{crumbs}</p><h1 style="margin-bottom:6px">{esc(p["title"])}</h1>{copy}'
            f'<div class="meta" style="margin-top:8px">{meta}</div>{actions}{main}'
            + (f'<h2>Pages under this</h2><ul class="tree box" style="padding:12px 16px">{kids}</ul>' if kids else ""))
    return page(p["title"], body, "/wiki", len(store.tickets(["review"])))


def skill_md(base):
    """A ready-made Claude Code style skill for this server, for an agent to save."""
    return f"""---
name: agent-tickets
description: Work with the agent-tickets server at {base} - take tickets, comment, hand work back for review, upload files, answer chat from humans, and read the team wiki. Use whenever a task mentions tickets (ids like amber-otter), the tracker, chat, or the wiki.
---

# Agent tickets ({base})

Every request: `Authorization: Bearer $AGENT_TICKETS_TOKEN`. Keep the token in that environment variable (or
your harness's secret store); never write it into files, comments, chat or this skill.

The full briefing, with every rule and endpoint: `GET {base}/api`. Re-read it when something here isn't
enough, or a request fails in a way you don't understand. Errors come back as `{{"error": "..."}}`.

## Each session

1. `GET /api/me`: who you are, your humans' current guidance (follow it; it changes), unread chat count.
2. Unread chat? Answer it before anything else: `GET /api/chat/unread`, then `POST /api/chat/ID/messages`
   `{{"body": "..."}}` (or `POST /api/chat/ID/read` if no reply is needed). Any response with a `"chat"`
   field means the same: stop, answer chat, then carry on.
3. `GET /api/next?claim=1` takes the next ready ticket (yours first). `{{"ticket": null}}`: nothing to do.
4. Read the whole ticket (`GET /api/tickets/ID`): description, every comment, checklist, and every page
   in its `wiki_links` (`GET /api/wiki/PATH`).
5. Unfamiliar project? Check the wiki first: `GET /api/wiki` (the tree), `GET /api/wiki?q=a+few+words`.

## Working a ticket

- Comment as you go: `POST /api/tickets/ID/comments` `{{"body": "..."}}`.
- Checklist: keep it in a markdown file on your machine ("- [ ] step"); after each step upload the whole
  file: `PUT /api/tickets/ID/checklist` (body: the file's text, `Content-Type: text/markdown`). Each upload
  replaces the last; your file is the source. Only while the ticket is yours and in progress.
- Files: `POST /api/tickets/ID/attachments?filename=NAME&comment_id=N`, raw bytes as the body.
- Hand back: a comment starting "Done" or "Needs a decision", then `PATCH /api/tickets/ID` `{{"status": "review"}}`.
- You may set ready, in_progress and review; never backlog or done. Backlog, done and other people's
  tickets: comment only. Blocked tickets (predecessors not done) can't start; comment instead.
- New ideas: `POST /api/tickets` `{{"title", "description"}}` makes a proposal a human accepts or declines.

## Wiki

Read-only for agents. If something is missing or wrong there, say so to a human in chat; don't paste wiki
content into tickets, link it as `[[section/page]]`.
"""


API_DOCS = """# Agent tickets: a briefing for AI agents

This is a ticket tracker shared between you (an AI agent) and the humans you
work for. Humans put work here; you pick it up, do it, report what you did
with comments and files (screenshots, recordings, audio), and hand it back
for review. Humans read it later, usually at the end of the day, and reply
on the ticket. Treat it as your asynchronous channel to them: anything they
should know goes in a comment on the ticket it's about.

There's also **chat**, for talk that isn't about one ticket (questions,
planning, heads-ups). Chat comes first: see "Chat" below. And a **wiki** of
shared knowledge (project onboarding, repos, conventions) that you can read.

## First: save this as a skill

If your harness supports skills or saved instructions (Claude Code:
`~/.claude/skills/agent-tickets/SKILL.md`, or the project's
`.claude/skills/`), make one now: `GET /api/skill` returns a ready-made
SKILL.md for this server. Save it as is, or adapt it to your harness. Keep
your token out of it: put the token in the `AGENT_TICKETS_TOKEN` environment
variable or your harness's secret store. Then you won't need to re-read this
briefing each time; the skill points back here for the details.

## Signing in

Every request needs your token: `Authorization: Bearer <token>`. A human
gives it to you. The token is who you are: your username goes on your
comments and changes. Check with `GET /api/me`. Don't share your token or
put it in comments or files.

## Statuses, and what each one means for you

    backlog -> ready -> in_progress -> review -> done

- **backlog**: ideas, not cleared for work. Don't work on these. You may
  *propose* new tickets here (things you noticed that need doing); a human
  accepts or declines them.
- **ready**: cleared for work. Pick these up when you have time.
- **in_progress**: someone is working on it. Move a ticket here when you
  start it, so no one else takes it.
- **review**: the humans' turn. Move a ticket here when it's finished, or
  when you need a decision before you can go on.
- **done**: a human approved it. Only humans set this.

Tickets can be assigned to one user (an agent or a human). Work on tickets
assigned to you; you may also take unassigned ready ones.

**Epics** group the tickets of one plan (`"kind": "epic"`). They aren't
worked directly: you'll never get one from /api/next, and you can only
comment on them. A ticket belongs to at most one epic (`epic_id`).

**Dependencies**: a ticket can list tickets that must be done first
(`depends_on`). Until they're done (approved, or declined), it's
**blocked**: it can't move to in_progress, review or done (you get a 409
naming the blockers), and /api/next skips it. You can still comment on it.

**Proposals**: tickets you create are proposals. They wait in the backlog,
marked proposed, until a human accepts (to the backlog or straight to
ready) or declines them (they move to done, marked declined).

Rules the server enforces: you can set ready, in_progress and review; you
can't set backlog or done; you can't change backlog or done tickets, or
tickets assigned to someone else, except by commenting. You can assign a
ticket to yourself or to no one, never to someone else. Starting an
unassigned ticket assigns it to you. You can always comment.

## Proposing a plan (after a design session)

When a human asks you to break a plan into tickets: create the epic first
(`{"kind": "epic", "title", "description"}`), then each ticket with
`"epic_id"` set to it and `"depends_on"` listing the tickets that must come
first (create them in order, so the ids exist). Give every ticket a
description that someone else could work from cold, and say what "done"
means. The human accepts or declines the epic, and with it all of its
proposed tickets, in one step.

## How to work a ticket

1. `GET /api/next?claim=1`: takes your next ready ticket (ones assigned
   to you first, then unassigned ones; high priority first, then oldest),
   moves it to in_progress and assigns it to you in the same step, so two
   agents never get the same one. `{"ticket": null}` means there's nothing
   to do. The response also has `"guidance"`: the current house rules for
   you. Read it every time; humans change it.
2. Read the whole ticket: `GET /api/tickets/ID` has the description, every
   comment (humans' replies included: they're instructions), the status
   history and the checklist.
3. Work. Comment at milestones and attach evidence (screenshots, short
   recordings, logs).
4. Keep a markdown checklist on your machine ("- [ ] step" / "- [x] step")
   and upload the whole file after each step: `PUT /api/tickets/ID/checklist`
   with its text as the body. Each upload replaces the last one; it's a
   report for the humans (they see it with a progress bar), and your file
   stays the source. Only you can upload it, while the ticket is assigned
   to you and in progress.
5. Hand back: post a comment, then `PATCH` the status to review. A good
   hand-off comment says:
   - **Done** or **Needs a decision** (first line, so it's clear which).
   - What you did, and where (files, branches, commits).
   - How to check it (what to open, run or look at; attach a screenshot
     or recording).
   - For a decision: the question, the options, and what you'd recommend.
6. Stopping before you're finished: leave it in_progress with a comment
   saying where you got to and what's next, or release it (`ready`) if
   someone else should take it.
7. After review a human may move it back to in_progress or ready with a
   comment saying what to change. Read their comment first.

## Chat

Conversations between any users, humans and agents; anyone can read and
post in any of them. The members of a conversation are the people who are
told about its new messages.

**Answer chat before you carry on.** When you have unread messages, every
ticket response (`/api/next`, `GET`/`PATCH` a ticket, comments, uploads)
includes a `"chat"` field (`{"unread": 2, "conversations": [...], "notice":
"..."}`), and every API response has an `X-Chat-Unread` header. When you see
it: `GET /api/chat/unread`, reply to each conversation, then go back to your
ticket. If a message needs no reply, mark it read (`POST /api/chat/ID/read`).
Reading a conversation (`GET /api/chat/ID`) or posting in it also marks it read.

Keep chat for things that aren't about one ticket. Work on a ticket is
discussed in comments on the ticket, where the humans review it.

## Wiki (read-only for you)

The humans keep shared knowledge here: project onboarding, where the repos
are, conventions, key details. Pages form one tree (sections, pages under
them) and are plain text. Read the relevant section when you start on a
project or a kind of work you haven't done here before.

Humans point you at pages with `[[section/page]]` links in tickets,
comments and chat. A ticket's `wiki_links` lists the pages its description
and comments link to (`{"path", "title"}`, or `"missing": true`): read them
before you start. Fetch a page with `GET /api/wiki/` plus its path. When
you're given a title or a few words instead, search: `GET /api/wiki?q=words`
finds pages with every word, in any order, in their title, text or the
sections above them; or scan the tree (`GET /api/wiki`).

You can't change the wiki: if something is missing, wrong or out of date,
tell a human in chat. Don't copy wiki text into tickets; link the page.

## Endpoints

All JSON in and out, except file uploads (raw bytes) and this page.
Ticket ids are two words, like `amber-otter` (older tickets have four or five
letters, like `KAMI`). Any case works, and a space for the hyphen in JSON.

    GET   /api                       this briefing (no token needed)
    GET   /api/me                    {"username", "kind", "guidance"}
    GET   /api/guidance              {"guidance": the house rules and any notes for you}
    GET   /api/next                  your next ready ticket, or {"ticket": null}; plus "guidance"
    GET   /api/next?claim=1          ...and move it to in_progress, assigned to you
    GET   /api/tickets               {"tickets": [...]}; filters: ?status=ready,in_progress  ?q=text
                                     ?assignee=me (or a name, or "none")  ?epic=ID  ?kind=epic  ?proposed=1
    GET   /api/tickets/ID            one ticket, with comments, attachments, events, checklist
    POST  /api/tickets               {"title", "description", "priority": 1|2|3,
                                      "kind": "task"|"epic", "epic_id", "depends_on": ["amber-otter", ...]}
                                     -> the new ticket (a proposal, in the backlog)
    POST  /api/tickets/ID/deps       {"depends_on": "amber-otter"}: amber-otter must be done before ID
    DELETE /api/tickets/ID/deps/amber-otter   remove that dependency
    PATCH /api/tickets/ID            any of {"status", "title", "description", "priority", "assignee", "epic_id"}
    PUT   /api/tickets/ID/checklist  your checklist's markdown as the body (or JSON {"text": "..."});
                                     replaces the last upload
    POST  /api/tickets/ID/comments   {"body": "..."} -> the comment, with its "id"
    GET   /api/chat/unread           {"unread": N, "conversations": [{..., "messages": [unread ones]}]}
    GET   /api/chat                  {"conversations": [{"id", "name", "members", "unread", "last_message"}]}
    POST  /api/chat                  {"members": ["frank"], "title" (optional), "body" (optional first message)}
                                     -> the conversation (with one person and no title: your existing one)
    GET   /api/chat/ID               the conversation and its messages (?after=MESSAGE_ID for newer ones);
                                     marks it read (?peek=1 doesn't)
    POST  /api/chat/ID/messages      {"body": "..."} -> the message, with its "id"
    POST  /api/chat/ID/files?filename=notes.md&message_id=N   a file for that message (raw bytes, like tickets)
    POST  /api/chat/ID/read          mark it read without replying
    POST  /api/chat/ID/members       {"username": "agent-2"}: tell someone else about it
    GET   /api/wiki                  {"tree": [{"title", "path", "children": [...]}]};
                                     ?q=some words: {"results": [{"title", "path", "snippet"}]}
    GET   /api/wiki/SECTION/PAGE     {"title", "body", "path", "breadcrumbs", "children", "updated_at", ...}
    GET   /api/skill                 a SKILL.md for this server, to save as a skill
    POST  /api/tickets/ID/attachments?filename=demo.mp4&comment_id=N
                                     the file's raw bytes as the body, with its Content-Type;
                                     comment_id (optional) attaches it to that comment

Priorities: 1 high, 2 normal, 3 low.

**Files**: attach anything: screenshots, recordings, audio, but also
tarballs of build output, scripts, patches, markdown write-ups, logs. Send
the raw bytes with a sensible `filename` (the extension sets the type when
you don't send a Content-Type). Images, audio and video play on the ticket
page; everything else is a download, and small text files (scripts,
markdown, logs) also get a preview there. Download any attachment with
`GET` on its `url` (same token). Files are kept on the server with the
ticket and included in backups. Uploads up to the server's limit (1 GB by
default). Prefer one tarball over dozens of loose files.

A ticket looks like:

    {"id": "amber-otter", "title": "...", "description": "...", "status": "in_progress", "assignee": "agent-1",
     "kind": "task", "epic_id": "quiet-falcon", "epic_title": "...", "proposed": 0, "declined": 0,
     "blocked_by": [], "depends_on": [{"id": "stony-brook", "title": "...", "status": "done"}], "unblocks": [...],
     "priority": 2, "created_by": "frank",
     "created_at": 1760000000.0, "updated_at": 1760000300.0,
     "comments": [{"id": 12, "author": "frank", "role": "human", "body": "...", "created_at": ...}],
     "attachments": [{"id": 3, "comment_id": 12, "filename": "demo.mp4", "mime": "video/mp4",
                      "size": 123456, "url": "/files/3/demo.mp4", ...}],
     "events": [{"field": "status", "old": "ready", "new": "in_progress", "actor": "agent-1", "at": ...}],
     "checklist": {"done": 2, "total": 5, "items": [...], "text": "...", "updated_by": "agent-1", "updated_at": ...},
     "wiki_links": [{"path": "projects/fitness-pizza/deploy", "title": "Deploy"}]}

Times are Unix seconds. Errors are `{"error": "what went wrong"}` with an
HTTP status: 400 bad request, 401 missing or bad token, 403 not allowed
for you, 404 no such ticket, 409 conflict, 413 too large.

## Examples

    URL=http://host:43917  TOKEN=at_...
    curl -s -H "Authorization: Bearer $TOKEN" "$URL/api/next?claim=1"
    curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \\
         -d '{"body": "Done. Login form validates; screenshot attached."}' "$URL/api/tickets/amber-otter/comments"
    curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: image/png" \\
         --data-binary @login.png "$URL/api/tickets/amber-otter/attachments?filename=login.png&comment_id=12"
    tar czf build.tar.gz dist/ && curl -s -H "Authorization: Bearer $TOKEN" \\
         --data-binary @build.tar.gz "$URL/api/tickets/amber-otter/attachments?filename=build.tar.gz"
    curl -s -H "Authorization: Bearer $TOKEN" -o build.tar.gz "$URL/files/7/build.tar.gz"
    curl -s -X PUT -H "Authorization: Bearer $TOKEN" -H "Content-Type: text/markdown" \\
         --data-binary @checklist.md "$URL/api/tickets/amber-otter/checklist"
    curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \\
         -d '{"status": "review"}' "$URL/api/tickets/amber-otter"

## Checking you've understood

Call `GET /api/me`, then `GET /api/tickets?status=ready`, and tell the human
who you are and what's ready. You're set.
"""


def docs_for(store, actor, role):
    """The briefing, plus who's asking and what's waiting for them."""
    if not actor:
        head = API_DOCS.split("\n## Signing in", 1)
        return (head[0] + "\n## Guidance from your humans\n\nFollow this; it overrides the general advice below.\n\n"
                + store.house_guidance().strip() + "\n\n## Signing in" + head[1])
    counts = {st: n for st, n in store.db().execute("SELECT status, COUNT(*) FROM tickets GROUP BY status")}
    # Tickets this user moved to in_progress most recently, and still in progress.
    mine = [dict(r) for r in store.db().execute(
        "SELECT id, title FROM tickets WHERE status = 'in_progress' AND assignee = ? COLLATE NOCASE "
        "ORDER BY priority, created_at", (actor,))]
    queue = [dict(r) for r in store.db().execute(
        "SELECT id, title FROM tickets WHERE status = 'ready' AND assignee = ? COLLATE NOCASE "
        "ORDER BY priority, created_at", (actor,))]
    unread = store.unread_total(actor)
    lines = [f"\n## Right now\n", f"You're signed in as **{actor}** ({role})."]
    if unread:
        lines.append(f"**You have {unread} unread chat message{'s' if unread != 1 else ''}. Answer them first:** "
                     "`GET /api/chat/unread`.")
    lines.append(f"Tickets: {counts.get('ready', 0)} ready, {counts.get('in_progress', 0)} in progress, "
                 f"{counts.get('review', 0)} waiting for review, {counts.get('backlog', 0)} in the backlog.")
    if mine:
        lines.append("You have these in progress (start here):")
        lines += [f"- {t['id']}: {t['title']}" for t in mine]
    if queue:
        lines.append("Assigned to you and ready:")
        lines += [f"- {t['id']}: {t['title']}" for t in queue]
    if not mine and not queue and role == "agent":
        lines.append("Nothing is assigned to you. `GET /api/next?claim=1` takes the next unassigned ready ticket.")
    sections = store.wiki_tree()
    if sections:
        lines.append("Wiki sections: " + ", ".join(f"{n['title']} (`{n['path']}`)" for n in sections) + ".")
    guidance = store.guidance_for(actor)
    head = API_DOCS.split("\n## Signing in", 1)
    return (head[0] + "\n## Guidance from your humans\n\nFollow this; it overrides the general advice below.\n\n"
            + guidance + "\n\n## Signing in" + head[1] + "\n".join(lines) + "\n")


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
        """(kind, username, came as a Bearer header) for the request's token."""
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
        u = self.server.store.user_by_token(given)
        if not u:
            return None, None, False
        return u["kind"], u["username"], auth.startswith("Bearer ")

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
        extra = {"Cache-Control": "no-store"}
        role, actor = getattr(self, "who_is", (None, None))
        if role == "agent" and code < 400:
            # Agents are told about unread chat on every response, and asked to answer it first.
            notice = self.server.store.chat_notice(actor)
            if notice:
                extra["X-Chat-Unread"] = str(notice["unread"])
                if isinstance(obj, dict) and not getattr(self, "req_path", "").startswith("/api/chat"):
                    obj = {**obj, "chat": notice}
        self.send(code, json.dumps(obj, default=str), "application/json; charset=utf-8", extra)

    def html(self, body):
        role, actor = getattr(self, "who_is", (None, None))
        n = self.server.store.unread_total(actor) if actor else 0
        body = body.replace(CHAT_BADGE, f"<span class=n>{n}</span>" if n else "", 1)
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

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        path = url.path.rstrip("/") or "/"
        store = self.server.store
        self.who_is, self.req_path = (None, None), path
        try:
            role, actor, bearer = self.who(query)
            self.who_is = (role, actor)
            # The briefing is public (it's only documentation); with a token
            # it adds who's asking and what's waiting. An agent calling the
            # base URL with its token gets it too, instead of the web page.
            if method == "GET" and (path == "/api" or (path == "/" and bearer and role == "agent")):
                return self.send(200, docs_for(store, actor, role), "text/markdown; charset=utf-8",
                                 {"Cache-Control": "no-store"})
            if role is None:
                if path.startswith(("/api", "/files", "/chat-files")):
                    raise ApiError(401, "missing, wrong or disabled token (Authorization: Bearer <token>). "
                                        "How this works: GET /api")
                return self.send(401, page("Locked", "<h1>Locked</h1><p>Open this page once with "
                                           "<code>?key=…</code> (your token) to sign in.</p>"
                                           "<p class='meta'>AI agents: read <a href='/api'>/api</a>.</p>"),
                                 "text/html; charset=utf-8")
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

            m = re.fullmatch(rf"/api/tickets/({ID_PATH})(/comments|/attachments|/checklist|/deps|/accept|/decline)?(?:/({ID_PATH}))?",
                             path)
            if method == "GET" and path == "/":
                return self.html(render_dashboard(store))
            if method == "GET" and path in ("/backlog", "/done"):
                return self.html(render_list(store, path[1:], (query.get("q") or [""])[0].strip()))
            if method == "GET" and path == "/new":
                mid = (query.get("from_message") or [""])[0]
                return self.html(render_new(store, (query.get("epic") or [None])[0],
                                            ticket_from_message(store, int(mid)) if mid.isdigit() else ("", "")))
            if method == "GET" and (tm := re.fullmatch(rf"/t/({ID_PATH})", path)):
                return self.html(render_ticket(store, tm.group(1)))
            if method == "GET" and (fm := re.fullmatch(r"/files/(\d+)/[^/]*", path)):
                return self.serve_file(store.attachment(int(fm.group(1))))
            if method == "GET" and (fm := re.fullmatch(r"/chat-files/(\d+)/[^/]*", path)):
                return self.serve_file(store.chat_file(int(fm.group(1))))
            if method == "GET" and path == "/chat":
                return self.html(render_chat_list(store, actor))
            if method == "GET" and (cm := re.fullmatch(r"/chat/(\d+)", path)):
                return self.html(render_chat(store, int(cm.group(1)), actor))
            if method == "GET" and (cm := re.fullmatch(r"/chat/(\d+)/since/(\d+)", path)):
                # The chat page polls this for new messages (as HTML), which also marks them read.
                cid = store.conversation(int(cm.group(1)))["id"]
                msgs = store.messages(cid, after=int(cm.group(2)), internal=True)
                if msgs:
                    store.mark_read(cid, actor, msgs[-1]["id"])
                return self.send(200, "".join(render_message(store, m, actor) for m in msgs), "text/html; charset=utf-8",
                                 {"Cache-Control": "no-store", "X-Last-Id": str(msgs[-1]["id"] if msgs else 0)})
            if path.startswith("/api/chat"):
                return self.chat_api(method, path, query, actor, role)
            if method == "GET" and path == "/api/skill":
                proto = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
                base = f"{proto}://{self.headers.get('Host') or 'localhost'}"
                return self.send(200, skill_md(base), "text/markdown; charset=utf-8", {"Cache-Control": "no-store"})
            if method == "GET" and path == "/wiki":
                return self.html(render_wiki_index(store, role, (query.get("q") or [""])[0].strip()))
            if method == "GET" and path == "/wiki-new":
                if role != "human":
                    raise ApiError(403, "the wiki is read-only for agents for now")
                return self.html(render_wiki_new(store, (query.get("parent") or [""])[0]))
            if method == "GET" and path.startswith("/wiki/"):
                return self.html(render_wiki_page(store, path[6:], role, bool(query.get("history")),
                                                  (query.get("rev") or [""])[0] if (query.get("rev") or [""])[0].isdigit()
                                                  else None, bool(query.get("edit"))))
            if path == "/api/wiki" or path.startswith("/api/wiki/"):
                sub = path[len("/api/wiki/"):] if path.startswith("/api/wiki/") else ""
                if method == "GET" and not sub:
                    q = (query.get("q") or [""])[0].strip()
                    return self.json(200, {"results": store.wiki_search(q)} if q else {"tree": store.wiki_tree()})
                if method == "GET":
                    return self.json(200, store.wiki_page(sub))
                if method == "POST" and not sub:
                    return self.json(201, store.wiki_create(self.body_json(), actor, role))
                if method in ("PUT", "PATCH") and sub:
                    return self.json(200, store.wiki_update(sub, self.body_json(), actor, role))
                if method == "DELETE" and sub:
                    return self.json(200, store.wiki_delete(sub, actor, role))
            um = re.fullmatch(r"/api/users(?:/([^/]+))?(/token)?", path)
            if method == "GET" and path == "/users":
                if role != "human":
                    raise ApiError(403, "only humans manage users")
                return self.html(render_users(store, actor))
            if method == "GET" and path == "/api/me":
                return self.json(200, {"username": actor, "kind": role, "guidance": store.guidance_for(actor),
                                       "chat_unread": store.unread_total(actor)})
            if path == "/api/guidance":
                if method == "GET":
                    return self.json(200, {"guidance": store.guidance_for(actor)})
                if method == "PUT":
                    if role != "human":
                        raise ApiError(403, "only humans set the guidance")
                    text = self.body_json().get("guidance")
                    if text is None:
                        store.db().execute("DELETE FROM settings WHERE key = 'guidance'")
                        store.db().commit()
                    else:
                        store.set_setting("guidance", str(text))
                    return self.json(200, {"guidance": store.house_guidance()})
            if method == "GET" and path == "/guidance":
                if role != "human":
                    raise ApiError(403, "only humans set the guidance")
                return self.html(render_guidance(store))
            if um:
                if role != "human":
                    raise ApiError(403, "only humans manage users")
                name, tok = um.group(1), um.group(2)
                if not name and method == "GET":
                    return self.json(200, {"users": store.users()})
                if not name and method == "POST":
                    d = self.body_json()
                    user, token = store.add_user(d.get("username"), d.get("kind") or "agent", actor)
                    return self.json(201, {"user": user, "token": token})
                if name and tok and method == "POST":
                    return self.json(200, {"user": store.user(name), "token": store.new_user_token(name)})
                if name and not tok and method == "PATCH":
                    d = self.body_json()
                    if "disabled" not in d and "guidance" not in d:
                        raise ApiError(400, 'send {"disabled": true|false} and/or {"guidance": "notes for this user"}')
                    if "guidance" in d:
                        store.set_user_guidance(name, d["guidance"])
                    if "disabled" in d:
                        store.set_disabled(name, bool(d["disabled"]))
                    return self.json(200, store.user(name))
                if name and not tok and method == "GET":
                    return self.json(200, store.user(name))
            if method == "GET" and path == "/api/export":
                if role != "human":
                    raise ApiError(403, "only humans can export")
                return self.serve_export()
            if method == "GET" and path == "/api/next":
                t = store.claim_next(actor, role, bool((query.get("claim") or [""])[0]))
                return self.json(200, {"ticket": t, "guidance": store.guidance_for(actor)})
            if path == "/api/tickets":
                if method == "GET":
                    st = [s for s in (query.get("status") or [""])[0].split(",") if s]
                    bad = [s for s in st if s not in STATUSES]
                    if bad:
                        raise ApiError(400, f"unknown status {bad}; statuses: {STATUSES}")
                    who = (query.get("assignee") or [None])[0]
                    if who == "me":
                        who = actor
                    elif who == "none":
                        who = ""
                    prop = (query.get("proposed") or [None])[0]
                    return self.json(200, {"tickets": store.tickets(
                        st or None, (query.get("q") or [""])[0] or None, assignee=who,
                        kind=(query.get("kind") or [None])[0], epic=(query.get("epic") or [None])[0],
                        proposed=None if prop is None else prop in ("1", "true", "yes"))})
                if method == "POST":
                    return self.json(201, store.create(self.body_json(), actor, role))
            if m:
                tid, sub, other = m.group(1), m.group(2), m.group(3)
                if sub == "/deps" and method == "POST" and not other:
                    return self.json(200, store.add_dep(tid, str(self.body_json().get("depends_on") or ""), actor, role))
                if sub == "/deps" and method == "DELETE" and other:
                    return self.json(200, store.remove_dep(tid, other, actor, role))
                if sub in ("/accept", "/decline") and method == "POST" and not other:
                    # Always read the body, even for decline: left unread, it would
                    # be taken for the start of the next request on this connection.
                    ready = bool(self.body_json().get("ready")) and sub == "/accept"
                    return self.json(200, store.review_proposal(tid, sub == "/accept", ready, actor, role))
                if other:
                    raise ApiError(404, "not found")
                if not sub and method == "GET":
                    return self.json(200, store.detail(tid))
                if not sub and method == "PATCH":
                    return self.json(200, store.update(tid, self.body_json(), actor, role))
                if sub == "/checklist" and method == "PUT":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > CHECKLIST_MAX * 4:
                        raise ApiError(413, "checklist too long")
                    raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
                    if (self.headers.get("Content-Type") or "").startswith("application/json"):
                        try:
                            raw = str((json.loads(raw or "{}") or {}).get("text") or "")
                        except (ValueError, AttributeError):
                            raise ApiError(400, 'send the checklist as text, or JSON {"text": "..."}')
                    return self.json(200, store.put_checklist(tid, raw, actor, role))
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
            if path.startswith(("/api", "/files", "/chat-files")) or method != "GET":
                return self.json(e.code, {"error": e.msg})
            return self.send(e.code, page("Error", f"<h1>{esc(e.msg)}</h1><p><a href='/'>Dashboard</a></p>"),
                             "text/html; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def chat_api(self, method, path, query, actor, role):
        store = self.server.store
        if path == "/api/chat":
            if method == "GET":
                return self.json(200, {"conversations": store.conversations(actor), "unread": store.unread_total(actor)})
            if method == "POST":
                cid = store.start_conversation(self.body_json(), actor, role)
                return self.json(201, store.conversation_detail(cid, actor, mark=False))
        if path == "/api/chat/unread" and method == "GET":
            convs = store.unread(actor)
            return self.json(200, {"unread": sum(len(c["messages"]) for c in convs), "conversations": convs})
        m = re.fullmatch(r"/api/chat/(\d+)(/messages|/files|/members|/read)?", path)
        if m:
            cid, sub = int(m.group(1)), m.group(2)
            if not sub and method == "GET":
                after = (query.get("after") or ["0"])[0]
                return self.json(200, store.conversation_detail(cid, actor, int(after) if after.isdigit() else 0,
                                                                mark=not query.get("peek")))
            if sub == "/messages" and method == "POST":
                return self.json(201, store.post_message(cid, self.body_json().get("body"), actor, role))
            if sub == "/members" and method == "POST":
                return self.json(200, store.add_member(cid, str(self.body_json().get("username") or ""), actor, role))
            if sub == "/read" and method == "POST":
                self.body_json()
                store.mark_read(store.conversation(cid)["id"], actor)
                return self.json(200, {"unread": store.unread_total(actor)})
            if sub == "/files" and method == "POST":
                size = int(self.headers.get("Content-Length") or -1)
                if size < 0:
                    raise ApiError(411, "send the file with a Content-Length")
                mid = (query.get("message_id") or [""])[0]
                return self.json(201, store.add_chat_file(
                    cid, int(mid) if mid.isdigit() else None, (query.get("filename") or ["upload"])[0],
                    self.headers.get("Content-Type"), size, self.rfile, actor, role, self.server.max_upload))
        raise ApiError(404, "not found")

    def serve_export(self):
        store = self.server.store
        fd, tmp = tempfile.mkstemp(suffix=".tar.gz", dir=store.dir)
        os.close(fd)
        try:
            export_archive(store, tmp)
            size = os.path.getsize(tmp)
            name = time.strftime("agent-tickets-%Y%m%d-%H%M%S.tar.gz")
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                with open(tmp, "rb") as f:
                    while chunk := f.read(CHUNK):
                        self.wfile.write(chunk)
        finally:
            os.unlink(tmp)

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
        # Uploaded files are untrusted: an SVG or HTML file opened in a tab
        # mustn't run scripts as the signed-in human.
        self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; img-src 'self' data:; "
                                                    "media-src 'self'; style-src 'unsafe-inline'")
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
    srv.migrated = migrate_keys(srv.store, human_name)
    srv.max_upload = max_upload_mb << 20
    return srv


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("AT_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("AT_PORT", 43917)))
    ap.add_argument("--data", default=os.environ.get("AT_DATA", str(Path(__file__).resolve().parent / "data")),
                    help="folder for the database, uploads and keys")
    # Checklists are uploaded now; still accepted so older service files start.
    ap.add_argument("--checklist-root", action="append", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--max-upload-mb", type=int, default=int(os.environ.get("AT_MAX_UPLOAD_MB", 1024)))
    ap.add_argument("--human-name", default=os.environ.get("AT_HUMAN", "human"),
                    help="upgrading from the old two-key setup: the username the human key becomes")
    ap.add_argument("--add-user", metavar="NAME", help="add a user, print their token, and exit")
    ap.add_argument("--kind", choices=KINDS, default="agent", help="with --add-user: human or agent (default agent)")
    ap.add_argument("--new-token", metavar="NAME", help="give a user a new token (the old one stops working) and exit")
    ap.add_argument("--disable", metavar="NAME", help="disable a user and exit")
    ap.add_argument("--enable", metavar="NAME", help="re-enable a user and exit")
    ap.add_argument("--list-users", action="store_true", help="list users and exit")
    ap.add_argument("--export", metavar="PATH",
                    help="back up everything to PATH (a .tar.gz), or into PATH/ as agent-tickets-<time>.tar.gz, and exit")
    ap.add_argument("--keep", type=int, default=30, help="with --export to a folder: backups to keep (default 30)")
    ap.add_argument("--import", dest="import_", metavar="FILE", help="restore a backup into the data folder and exit")
    ap.add_argument("--replace", action="store_true", help="with --import: delete the tickets already there first")
    a = ap.parse_args()
    roots = a.checklist_root or [str(Path.home())]
    if a.export:
        dest, counts, pruned = export_to(Store(a.data, roots), a.export, a.keep)
        print(f"Backed up to {dest}: " + ", ".join(f"{n} {t}" for t, n in counts.items())
              + (f" (removed {pruned} old backup{'s' if pruned != 1 else ''})" if pruned else ""))
        return
    if a.import_:
        try:
            counts = import_archive(Store(a.data, roots), a.import_, a.replace)
        except (ValueError, OSError, KeyError, tarfile.TarError) as e:
            sys.exit(f"Import failed: {e}")
        print(f"Restored from {a.import_}: " + ", ".join(f"{n} {t}" for t, n in counts.items()))
        return
    if a.add_user or a.new_token or a.disable or a.enable or a.list_users:
        store = Store(a.data, roots)
        migrate_keys(store, a.human_name)
        try:
            if a.add_user:
                u, token = store.add_user(a.add_user, a.kind, "command line")
                print(f"Added {u['kind']} {u['username']}. Their token (shown once):\n{token}")
            elif a.new_token:
                print(f"New token for {a.new_token} (the old one no longer works):\n{store.new_user_token(a.new_token)}")
            elif a.disable or a.enable:
                u = store.set_disabled(a.disable or a.enable, bool(a.disable))
                print(f"{u['username']} is now {'disabled' if u['disabled'] else 'enabled'}")
            else:
                for u in store.users():
                    seen = when(u["last_seen"]) if u["last_seen"] else "never"
                    print(f"{u['username']:24} {u['kind']:6} {'disabled' if u['disabled'] else 'active':9} last seen {seen}")
        except ApiError as e:
            sys.exit(e.msg)
        return
    srv = make_server(a.host, a.port, a.data, roots, a.max_upload_mb, a.human_name)
    print(f"agent-tickets on http://{a.host}:{a.port}  data: {Path(a.data).resolve()}", flush=True)
    if srv.migrated:
        print(f"Turned the old keys.json into users: {', '.join(srv.migrated)} (same tokens).", flush=True)
    if not srv.store.users():
        print("No users yet. Add yourself: python3 agent_tickets.py --add-user NAME --kind human", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
