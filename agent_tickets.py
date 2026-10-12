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
import email.message
import email.parser
import email.policy
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
# An epic's state follows its tickets (declined ones don't count), stored as
# one of these statuses: backlog (none made ready yet), active (some ready or
# under way, or some done and some not), complete (all done).
EPIC_STATES = {"backlog": "Backlog", "in_progress": "Active", "done": "Complete"}
EPIC_STATE_NAMES = {"backlog": "backlog", "in_progress": "active", "done": "complete"}


def epic_status_for(statuses):
    if statuses and all(x == "done" for x in statuses):
        return "done"
    return "in_progress" if any(x != "backlog" for x in statuses) else "backlog"


COMMENT_QUIET_MINUTES = 20  # an agent's in-progress ticket with no comment for this long gets a nudge

PAGE = 20  # comments or messages shown (and returned) at a time; older ones on request

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
SCHEMA_VERSION = 10
TABLES = ("users", "settings", "tickets", "deps", "comments", "attachments", "events",
          "conversations", "chat_members", "messages", "chat_files", "wiki_pages", "wiki_revisions")
KINDS = ("human", "agent", "guest")  # guests can read everything and change nothing
USERNAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}")


# The house rules agents get with every ticket they claim and at the top of
# the /api briefing. Humans edit it on the Guidance page; this is the start.
DEFAULT_GUIDANCE = """## How we work

0. Chat comes first. When a response carries a "chat" notice, reply to the messages before your next step on the ticket. A human's message stays until you reply, even a short "Got it". Chat is for questions and planning that aren't about one ticket; anything about a ticket goes in a comment on that ticket.
1. When you take a ticket, it's in progress and assigned to you: that means you are working on it now. Make its folder (~/work/tickets/TICKET-ID/, e.g. ~/work/tickets/amber-otter/) and, if it changes a repo, a branch in that repo named ticket/TICKET-ID. Then post a one-line comment: you're starting, where the folder and branch are, and that you'll write a checklist.
2. Write a markdown checklist of the steps ("- [ ] step") in that folder (checklist.md), upload it (PUT /api/tickets/ID/checklist), and post a short plan: what you'll do, and how you'll know it's done.
3. After each step, tick it off in your file, upload it again, and comment what you did and what happened, including errors and surprises.
4. When something is visible (a page, a chart, a game), attach a screenshot or a short recording.
5. Hand back with a comment that starts "Done" or "Needs a decision", then set the status to review.
6. After every action (a step, a hand-back, a chat reply), call GET /api/wait and do what it returns: chat, replies, then your tickets in the order it lists them (highest priority first, started and nearest-done first). Finish what you've started before taking new work. Never end a turn with nothing in hand: keep wait running in the background so it wakes you, and have a recurring check (cron, scheduled task) as a safety net. Don't leave tickets in progress that you aren't working on: if you're waiting on a decision, move it to review and say what you need.

## Keeping your files tidy

Keep each ticket's working files (checklist, notes, scratch files, downloads, logs, screenshots) in its own folder, ~/work/tickets/TICKET-ID/, not loose in your home folder, so you and anyone after you can find them. Changes to a project happen in that project's own repo, on the ticket's branch, not as copies in the ticket folder. Don't touch other tickets' folders. When a ticket is closed as done, clear out what isn't worth keeping.

## How much to write

Progress comments: one to three sentences per step. Save the detail for the hand-off comment. Don't paste long logs; attach them as files.
Make it easy to read on a phone: short paragraphs with blank lines between them, a heading per part of a long write-up, lists for steps, and code blocks for commands and output (see "Formatting" in the briefing). No walls of text.
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

    def __init__(self, data_dir):
        self.dir = Path(data_dir).resolve()
        self.files = self.dir / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "tickets.db"
        self.local = threading.local()
        self.write_lock = threading.Lock()
        self.changed = threading.Condition()  # notified after every change; /api/wait sleeps on it
        db = self.db()
        db.executescript(SCHEMA)
        # 1 -> 2 added the users table, 2 -> 3 the settings table, 3 -> 4 the
        # deps table, 4 -> 5 the chat tables, 5 -> 6 the wiki tables (all
        # created above); the columns are added here (6 -> 7: uploaded
        # checklists; tickets.checklist_path is no longer used; 7 -> 8: cancelled).
        for table, col, decl in (("tickets", "assignee", "TEXT"), ("users", "guidance", "TEXT"),
                                 ("tickets", "kind", "TEXT NOT NULL DEFAULT 'ticket'"), ("tickets", "epic_id", "TEXT"),
                                 ("tickets", "proposed", "INTEGER NOT NULL DEFAULT 0"),
                                 ("tickets", "declined", "INTEGER NOT NULL DEFAULT 0"),
                                 ("tickets", "checklist_md", "TEXT"), ("tickets", "checklist_by", "TEXT"),
                                 ("tickets", "checklist_at", "REAL"),
                                 ("tickets", "cancelled", "INTEGER NOT NULL DEFAULT 0"),
                                 ("users", "prefs", "TEXT")):
            if col not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        db.execute("CREATE INDEX IF NOT EXISTS tickets_assignee ON tickets(assignee, status)")
        db.execute("CREATE INDEX IF NOT EXISTS tickets_epic ON tickets(epic_id)")
        db.execute("CREATE INDEX IF NOT EXISTS deps_on ON deps(depends_on)")
        with db:
            self._sync_epics(db, [r[0] for r in db.execute("SELECT id FROM tickets WHERE kind = 'epic'")])
        with db:  # 9 -> 10: ordinary tickets are kind "ticket" (they were "task")
            db.execute("UPDATE tickets SET kind = 'ticket' WHERE kind = 'task'")
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

    PREFS = {"tts": False}  # each user's own settings, and their defaults

    def prefs(self, username):
        row = self.db().execute("SELECT prefs FROM users WHERE username = ?", (username,)).fetchone()
        try:
            saved = json.loads(row[0]) if row and row[0] else {}
        except ValueError:
            saved = {}
        return {k: saved.get(k, v) for k, v in self.PREFS.items()}

    def set_prefs(self, username, changes):
        bad = [k for k in changes if k not in self.PREFS]
        if bad:
            raise ApiError(400, f"unknown setting {bad[0]}; settings: {list(self.PREFS)}")
        merged = {**self.prefs(username), **{k: bool(v) for k, v in changes.items()}}
        u = self.user(username)
        with self.write_lock, self.db() as db:
            db.execute("UPDATE users SET prefs = ? WHERE id = ?", (json.dumps(merged), u["id"]))
        return merged

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
        t["blocked"] = bool(t["blocked_by"])
        if t.get("epic_id"):
            row = self.db().execute("SELECT title FROM tickets WHERE id = ?", (t["epic_id"],)).fetchone()
            t["epic_title"] = row["title"] if row else None
        if t.get("kind") == "epic":
            t["progress"] = self.epic_progress(t["id"])
            t["epic_state"] = ("declined" if t.get("declined") else "cancelled" if t.get("cancelled")
                               else EPIC_STATE_NAMES.get(t["status"], t["status"]))
        return t

    def epic_progress(self, eid):
        counts = {r[0]: r[1] for r in self.db().execute(
            "SELECT status, COUNT(*) FROM tickets WHERE epic_id = ? AND declined = 0 AND cancelled = 0 GROUP BY status",
            (eid,))}
        return {"total": sum(counts.values()), "done": counts.get("done", 0), "by_status": counts}

    def detail(self, tid, limit=None):
        """A ticket with its comments, files, history and the rest. With `limit`, only the latest `limit`
        comments (comments_more says there are older ones), and the files and history from the same span."""
        t = self.ticket(tid)
        db = self.db()
        every = [dict(r) for r in db.execute(
            "SELECT * FROM comments WHERE ticket_id = ? ORDER BY created_at, id", (t["id"],))]
        shown = every[-limit:] if limit else every
        start = shown[0]["created_at"] if len(shown) < len(every) else None
        t["comments"], t["comments_total"], t["comments_more"] = shown, len(every), start is not None
        ids = {c["id"] for c in shown}
        t["attachments"] = [self.attachment_json(dict(r)) for r in db.execute(
            "SELECT * FROM attachments WHERE ticket_id = ? ORDER BY created_at, id", (t["id"],))
            if start is None or r["comment_id"] in ids or (r["comment_id"] is None and r["created_at"] >= start)]
        t["events"] = [dict(r) for r in db.execute(
            "SELECT * FROM events WHERE ticket_id = ? ORDER BY at, id", (t["id"],)) if start is None or r["at"] >= start]
        t["checklist"] = checklist_summary(t, items=True)
        refs = wiki_refs(t["description"]) + [p for c in every for p in wiki_refs(c["body"])]
        t["wiki_links"] = [self.wiki_link_info(p) for p in dict.fromkeys(refs)]
        # Predecessors: the ones still in the way, and the finished ones (which no longer block anything).
        deps = [dict(r) for r in db.execute(
            "SELECT p.id, p.title, p.status, p.declined, p.cancelled FROM deps d JOIN tickets p ON p.id = d.depends_on "
            "WHERE d.ticket_id = ? ORDER BY p.id", (t["id"],))]
        t["depends_on"] = [d for d in deps if d["status"] != "done"]
        t["done_dependencies"] = [d for d in deps if d["status"] == "done"]
        t["unblocks"] = [dict(r) for r in db.execute(
            "SELECT n.id, n.title, n.status FROM deps d JOIN tickets n ON n.id = d.ticket_id "
            "WHERE d.depends_on = ? ORDER BY n.id", (t["id"],))]
        if t["kind"] == "epic":
            # Most complete first: done, review, in progress, ready, backlog; declined or cancelled last; oldest
            # first within each.
            rank = {"done": 0, "review": 1, "in_progress": 2, "ready": 3, "backlog": 4}
            t["tickets"] = sorted(self.tickets(epic=t["id"], limit=1000),
                                  key=lambda x: (5 if x["declined"] or x["cancelled"] else rank[x["status"]], x["created_at"]))
        self._links(t)
        return t

    def comment_page(self, tid, before, limit=PAGE):
        """The `limit` comments before comment `before` (oldest first), their files, and whether there are
        older ones still."""
        t = self.ticket(tid)
        db = self.db()
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM comments WHERE ticket_id = ? AND id < ? ORDER BY id DESC LIMIT ?", (t["id"], before, limit + 1))]
        more, page_ = len(rows) > limit, rows[:limit][::-1]
        ids = [c["id"] for c in page_]
        files = [self.attachment_json(dict(r)) for r in db.execute(
            f"SELECT * FROM attachments WHERE comment_id IN ({','.join('?' * len(ids))}) ORDER BY id", ids)] if ids else []
        return {"comments": page_, "attachments": files, "more": more}

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
        u = self.user(str(name))
        if u["kind"] == "guest":
            raise ApiError(400, f"{u['username']} is a guest: guests can't be assigned work")
        return u["username"]

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

    def _sync_epics(self, db, ids, actor=None, role=None, now=None):
        """Bring each epic in `ids` to the state its tickets imply (inside the caller's transaction)."""
        now = now or time.time()
        for eid in {i for i in ids if i}:
            e = db.execute("SELECT status, kind, proposed, declined, cancelled FROM tickets WHERE id = ?", (eid,)).fetchone()
            if not e or e["kind"] != "epic" or e["declined"] or e["cancelled"] or e["proposed"]:
                continue
            want = epic_status_for([r[0] for r in db.execute(
                "SELECT status FROM tickets WHERE epic_id = ? AND declined = 0 AND cancelled = 0", (eid,))])
            if want != e["status"]:
                db.execute("UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?", (want, now, eid))
                if actor:
                    self._event(db, eid, actor, role, "epic_status", e["status"], want, now)

    def _check_agent_may_change(self, t, actor):
        if t["kind"] == "epic":
            raise ApiError(403, f"{t['id']} is an epic: agents can only comment on it")
        if t["status"] in AGENT_LOCKED:
            raise ApiError(403, f"ticket {t['id']} is {t['status']}: agents can only comment on it")
        if t["assignee"] and t["assignee"].lower() != actor.lower():
            raise ApiError(403, f"ticket {t['id']} is assigned to {t['assignee']}: you can only comment on it")

    def create(self, data, actor, role):
        title = str(data.get("title") or "").strip()
        if not title:
            raise ApiError(400, "a ticket needs a title")
        kind = data.get("kind") or "ticket"
        if kind not in ("ticket", "epic"):
            raise ApiError(400, 'kind is "ticket" or "epic"')
        epic_id = self._check_epic(data.get("epic_id"))
        if kind == "epic":
            data = {**data, "status": "backlog", "assignee": None}
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
                self._sync_epics(db, [epic_id], actor, role, now)
                if assignee:
                    self._event(db, tid, actor, role, "assignee", None, assignee, now)
        return self._links(self.ticket(tid))

    def update(self, tid, data, actor, role):
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
            if fields.get("status") in ("in_progress", "review", "done") and t["kind"] == "ticket":
                waiting = self.blockers(t["id"])
                if waiting:
                    raise ApiError(409, f"{t['id']} is blocked: {', '.join(waiting)} must be done first "
                                        f"(you can still comment on it)")
            if role == "agent":
                self._check_agent_may_change(t, actor)
            if t["kind"] == "epic" and "status" in fields and fields["status"] != t["status"]:
                raise ApiError(400, "an epic's status follows its tickets (backlog, active, complete); it can't be set")
            if role == "agent":
                if (fields.get("status") in ("review", "ready") and t["status"] == "in_progress"
                        and self.comment_gap(t["id"], actor)[1] is None):
                    what = ("a hand-off comment: start it with \"Done\" or \"Needs a decision\", then say what you did, "
                            "where, and how to check it (or the question and your recommendation)"
                            if fields["status"] == "review" else "a comment saying where you got to and what's left")
                    raise ApiError(409, f"comment on {t['id']} first: you haven't since you started it. Post {what}; "
                                        "then change the status.")
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
            # Reopening a cancelled or declined ticket makes it an ordinary one again.
            if fields.get("status", "done") != "done" and (t["cancelled"] or t["declined"]):
                fields["cancelled"], fields["declined"] = 0, 0
            now = time.time()
            with db:
                for k, v in fields.items():
                    if t[k] != v:
                        db.execute(f"UPDATE tickets SET {k} = ? WHERE id = ?", (v, t["id"]))
                        if k in ("cancelled", "declined", "proposed"):
                            continue  # shown by the status change itself
                        # Description edits are logged without their text.
                        self._event(db, t["id"], actor, role, k, None if k == "description" else t[k],
                                    None if k == "description" else v, now)
                db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, t["id"]))
                if "status" in fields or "epic_id" in fields:
                    self._sync_epics(db, [t["epic_id"], fields.get("epic_id")], actor, role, now)
        return self._links(self.ticket(t["id"]))

    def comment_gap(self, tid, user):
        """(when the ticket last went into progress, when `user` last commented on it since then or None)."""
        db = self.db()
        started = db.execute("SELECT MAX(at) FROM events WHERE ticket_id = ? AND field = 'status' AND new = 'in_progress'",
                             (tid,)).fetchone()[0] or 0
        last = db.execute("SELECT MAX(created_at) FROM comments WHERE ticket_id = ? AND author = ? COLLATE NOCASE "
                          "AND created_at >= ?", (tid, user, started)).fetchone()[0]
        return started, last

    def comment_nudge(self, tid, user):
        """A reminder for an agent that's gone quiet on its in-progress ticket, or None."""
        started, last = self.comment_gap(tid, user)
        if last is None:
            return "you haven't commented since you started it: post a comment (what you're doing, your plan)"
        quiet = (time.time() - last) / 60
        if quiet >= COMMENT_QUIET_MINUTES:
            return f"your last comment here was {int(quiet)} minutes ago: comment what you've done since"
        return None

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
        # Ticking steps off isn't a report on its own: if there's been no comment since the last upload, say so.
        started, last = self.comment_gap(t["id"], actor)
        since = max(started, t["checklist_at"] or 0)
        reminder = (None if last is not None and last >= since else
                    "Now comment on the ticket: what you did in this step and what happened. The checklist shows "
                    "progress; comments are how the humans know what's going on.")
        if text != (t["checklist_md"] or ""):
            now = time.time()
            with self.write_lock:
                db = self.db()
                with db:
                    db.execute("UPDATE tickets SET checklist_md = ?, checklist_by = ?, checklist_at = ?, updated_at = ? "
                               "WHERE id = ?", (text, actor, now, now, t["id"]))
                    self._event(db, t["id"], actor, role, "checklist", f"{old['done']}/{old['total']}" if old else None,
                                f"{new['done']}/{new['total']}", now)
        out = {"checklist": self.detail(t["id"])["checklist"]}
        if reminder:
            out["reminder"] = reminder
        return out

    def cancel(self, tid, actor, role):
        """Close a ticket out of the normal cycle (overtaken by events, a duplicate...): it goes to done,
        marked cancelled. An epic takes its unfinished tickets with it. Humans only; say why in a comment."""
        if role != "human":
            raise ApiError(403, "only humans cancel tickets")
        with self.write_lock:
            db = self.db()
            t = self.ticket(tid)
            if t["proposed"]:
                raise ApiError(409, f"{t['id']} is a proposal: decline it instead")
            if t["status"] == "done":
                raise ApiError(409, f"{t['id']} is already done")
            ids = [t["id"]] + ([r[0] for r in db.execute(
                "SELECT id FROM tickets WHERE epic_id = ? AND status != 'done'", (t["id"],))] if t["kind"] == "epic" else [])
            now = time.time()
            with db:
                for i in ids:
                    cur = db.execute("SELECT status FROM tickets WHERE id = ?", (i,)).fetchone()[0]
                    db.execute("UPDATE tickets SET status = 'done', cancelled = 1, proposed = 0, updated_at = ? WHERE id = ?",
                               (now, i))
                    self._event(db, i, actor, role, "cancelled", cur, "done", now)
                self._sync_epics(db, [t["epic_id"]], actor, role, now)
        return self._links(self.ticket(t["id"]))

    def review_proposal(self, tid, accept, ready, actor, role, assignee=None, priority=None):
        """Accept (to the backlog, or ready) or decline (to done, marked
        declined) a proposal. On an epic, its proposed tickets go the same way.
        Accepting can assign it (an epic: its tickets) to someone, and set its priority (an epic: its
        tickets' too), at the same time."""
        if role != "human":
            raise ApiError(403, "only humans accept or decline proposals")
        assignee = self._check_assignee(assignee) if accept and assignee else None
        if accept and priority not in (None, ""):
            try:
                priority = int(priority)
            except (TypeError, ValueError):
                priority = 0
            if priority not in PRIORITIES:
                raise ApiError(400, "priority is 1 (high), 2 (normal) or 3 (low)")
        else:
            priority = None
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
                        to = "ready" if ready and kind == "ticket" else cur
                        db.execute("UPDATE tickets SET proposed = 0, status = ?, updated_at = ? WHERE id = ?", (to, now, i))
                        self._event(db, i, actor, role, "accepted", None, to, now)
                        old = db.execute("SELECT assignee FROM tickets WHERE id = ?", (i,)).fetchone()[0]
                        if assignee and kind == "ticket" and old != assignee:
                            db.execute("UPDATE tickets SET assignee = ? WHERE id = ?", (assignee, i))
                            self._event(db, i, actor, role, "assignee", old, assignee, now)
                        was = db.execute("SELECT priority FROM tickets WHERE id = ?", (i,)).fetchone()[0]
                        if priority and was != priority:
                            db.execute("UPDATE tickets SET priority = ? WHERE id = ?", (priority, i))
                            self._event(db, i, actor, role, "priority", was, priority, now)
                    else:
                        db.execute("UPDATE tickets SET proposed = 0, declined = 1, status = 'done', updated_at = ? "
                                   "WHERE id = ?", (now, i))
                        self._event(db, i, actor, role, "declined", cur, "done", now)
                self._sync_epics(db, [t["id"] if t["kind"] == "epic" else t["epic_id"]], actor, role, now)
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
            row = db.execute("SELECT id, assignee FROM tickets t WHERE status = 'ready' AND kind = 'ticket' AND proposed = 0 "
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
                    self._sync_epics(db, [db.execute("SELECT epic_id FROM tickets WHERE id = ?", (row["id"],)).fetchone()[0]],
                                     actor, role, now)
        return self.detail(row["id"], limit=PAGE)

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

    # --- waiting: an idle agent holds GET /api/wait open until there's something for it
    def bump(self):
        with self.changed:
            self.changed.notify_all()

    def _marks(self):
        db = self.db()
        return (db.execute("SELECT COALESCE(MAX(id), 0) FROM comments").fetchone()[0],
                db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])

    def news_for(self, user, marks, queue=False):
        """Tickets someone else commented on or changed since `marks` (that are now assigned to `user`,
        including ones just assigned to them), and with `queue`, unassigned tickets made ready since."""
        c0, e0 = marks
        db, out = self.db(), {}
        for r in db.execute(
                "SELECT t.id, t.title, t.status, c.author FROM comments c JOIN tickets t ON t.id = c.ticket_id "
                "WHERE c.id > ? AND t.assignee = ? COLLATE NOCASE AND c.author != ? COLLATE NOCASE ORDER BY c.id",
                (c0, user, user)):
            out[r["id"]] = {"id": r["id"], "title": r["title"], "status": r["status"], "why": f"{r['author']} commented"}
        for r in db.execute(
                "SELECT t.id, t.title, t.status, e.actor, e.field, e.new FROM events e JOIN tickets t ON t.id = e.ticket_id "
                "WHERE e.id > ? AND e.actor != ? COLLATE NOCASE AND (t.assignee = ? COLLATE NOCASE OR (? AND "
                "t.assignee IS NULL AND t.status = 'ready' AND t.kind = 'ticket' AND t.proposed = 0 AND e.new = 'ready')) "
                "ORDER BY e.id", (e0, user, user, 1 if queue else 0)):
            if r["id"] in out:
                continue
            if r["field"] == "assignee" and (r["new"] or "").lower() == user.lower():
                why = f"{r['actor']} assigned it to you"
            elif r["new"] == "ready" and r["field"] in ("status", "created", "accepted"):
                why = f"{r['actor']} made it ready"
            elif r["field"] == "cancelled":
                why = f"{r['actor']} cancelled it: stop work on it"
            else:
                why = f"{r['actor']} changed {r['field'].replace('_', ' ')}"
            out[r["id"]] = {"id": r["id"], "title": r["title"], "status": r["status"], "why": why}
        return list(out.values())

    def work_for(self, user):
        """The user's own unblocked in-progress and ready tickets, in the order to do them: highest
        priority first; at the same priority, started before not started, then nearest completion
        (checklist progress), then oldest."""
        rows = [dict(r) for r in self.db().execute(
            "SELECT t.id, t.title, t.status, t.priority, t.created_at, t.checklist_md FROM tickets t "
            "WHERE t.status IN ('in_progress', 'ready') AND t.kind = 'ticket' AND t.proposed = 0 "
            "AND t.assignee = ? COLLATE NOCASE AND NOT EXISTS (SELECT 1 FROM deps d JOIN tickets p "
            "ON p.id = d.depends_on WHERE d.ticket_id = t.id AND p.status != 'done')", (user,))]
        out = []
        for r in rows:
            ck = parse_checklist(r.pop("checklist_md")) if r.get("checklist_md") else None
            done, total = (ck["done"], ck["total"]) if ck else (0, 0)
            r["progress"] = f"{done}/{total}" if total else None
            r["_rank"] = (r["priority"], 0 if r["status"] == "in_progress" else 1, -(done / total if total else 0),
                          r["created_at"])
            r["why"] = ("yours and in progress: keep working on it" if r["status"] == "in_progress"
                        else "assigned to you and ready: start it")
            if r["status"] == "in_progress":
                nudge = self.comment_nudge(r["id"], user)
                if nudge:
                    r["why"] += f" ({nudge})"
            out.append(r)
        out.sort(key=lambda r: r["_rank"])
        for r in out:
            del r["_rank"], r["created_at"]
        return out

    def outstanding(self, user, status, assigned=True):
        """Unblocked tickets (not epics or proposals) in `status`: the user's own, or with assigned=False,
        nobody's. Highest priority first, then oldest."""
        who = "t.assignee = ? COLLATE NOCASE" if assigned else "t.assignee IS NULL"
        return [dict(r) for r in self.db().execute(
            f"SELECT t.id, t.title, t.status, t.priority FROM tickets t WHERE t.status = ? AND t.kind = 'ticket' "
            f"AND t.proposed = 0 AND {who} AND NOT EXISTS (SELECT 1 FROM deps d JOIN tickets p ON p.id = d.depends_on "
            f"WHERE d.ticket_id = t.id AND p.status != 'done') ORDER BY t.priority, t.created_at",
            (status, user) if assigned else (status,))]

    def comments_owed(self, user, tid=None):
        """The user's open tickets (or just `tid`) with comments from humans posted since the user last
        commented there: they stay until the user replies on the ticket. [{"id", "title", "status",
        "new_comments", "from", "why"}], newest activity first."""
        sql = ("SELECT t.id, t.title, t.status, COUNT(c.id) AS n, GROUP_CONCAT(DISTINCT c.author) AS who, "
               "MAX(c.id) AS last FROM tickets t JOIN comments c ON c.ticket_id = t.id "
               "WHERE t.assignee = ? COLLATE NOCASE AND t.status != 'done' AND t.kind = 'ticket' AND c.role = 'human' "
               "AND c.author != ? COLLATE NOCASE AND c.id > COALESCE((SELECT MAX(m.id) FROM comments m "
               "WHERE m.ticket_id = t.id AND m.author = ? COLLATE NOCASE), 0)")
        args = [user, user, user]
        if tid:
            sql += " AND t.id = ?"
            args.append(tid)
        out = []
        for r in self.db().execute(sql + " GROUP BY t.id ORDER BY last DESC", args):
            who = ", ".join(sorted(set((r["who"] or "").split(","))))
            out.append({"id": r["id"], "title": r["title"], "status": r["status"], "new_comments": r["n"],
                        "from": who.split(", "),
                        "why": f"{who} commented ({r['n']} new): read it and reply on the ticket"})
        return out

    def wait_for(self, user, timeout, queue=False):
        """What `user` should do next, as soon as there's anything: chat they owe; humans' comments on
        their tickets they haven't replied to; other changes to their tickets since the call began; their
        in-progress and ready tickets; with `queue`, anyone's ready tickets. Only when there's none of that
        does it wait (up to `timeout` seconds)."""
        marks, end = self._marks(), time.time() + timeout
        steps = [("queue", lambda: self.outstanding(user, "ready", assigned=False) if queue else [],
                  "ready for anyone: take it with POST /api/next")]
        while True:
            notice = self.chat_notice(user)
            if notice:
                return {"reason": "chat", "chat": notice, "tickets": []}
            owed = self.comments_owed(user)
            if owed:
                return {"reason": "comments", "chat": None, "tickets": owed}
            news = self.news_for(user, marks, queue)
            if news:
                return {"reason": "tickets", "chat": None, "tickets": news}
            work = self.work_for(user)
            if work:  # "in_progress" or "ready", after the first thing to do
                return {"reason": work[0]["status"], "chat": None, "tickets": work}
            for reason, find, why in steps:
                found = find()
                if found:
                    return {"reason": reason, "chat": None, "tickets": [{**t, "why": why} for t in found]}
            left = end - time.time()
            if left <= 0:
                return {"reason": "timeout", "chat": None, "tickets": []}
            with self.changed:
                self.changed.wait(min(left, 5))

    def read_up_to(self, cid):
        """Each member's last read message id in a conversation."""
        return {r[0]: r[1] for r in self.db().execute(
            "SELECT username, last_read FROM chat_members WHERE conversation_id = ? ORDER BY username COLLATE NOCASE", (cid,))}

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
        c["read_up_to"] = self.read_up_to(cid)
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

    def messages(self, cid, after=0, limit=200, internal=False, before=None):
        """A conversation's messages after id `after` (and before id `before`): the latest `limit` of them,
        oldest first, with their files."""
        msgs = [dict(r) for r in self.db().execute(
            "SELECT * FROM (SELECT * FROM messages WHERE conversation_id = ? AND id > ? AND id < ? ORDER BY id DESC "
            "LIMIT ?) ORDER BY id", (cid, after, before or 1 << 62, limit))]
        by_id = {m["id"]: m for m in msgs}
        for m in msgs:
            m["attachments"] = []
        if msgs:
            for f in self.db().execute(f"SELECT * FROM chat_files WHERE message_id IN ({','.join('?' * len(by_id))}) "
                                       "ORDER BY id", list(by_id)):
                by_id[f["message_id"]]["attachments"].append(self.chat_file_json(f, internal))
        return msgs

    def message(self, mid):
        row = self.db().execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
        if not row:
            raise ApiError(404, f"no message {mid}")
        m = dict(row)
        m["attachments"] = [self.chat_file_json(f) for f in self.db().execute(
            "SELECT * FROM chat_files WHERE message_id = ? ORDER BY id", (mid,))]
        return m

    def older_messages(self, cid, before):
        return bool(self.db().execute("SELECT 1 FROM messages WHERE conversation_id = ? AND id < ? LIMIT 1",
                                      (cid, before)).fetchone())

    def conversation_detail(self, cid, viewer, mark=True):
        """A conversation with its latest PAGE messages; messages_more says there are older ones."""
        c = self.conversation(cid, viewer)
        c["messages"] = self.messages(c["id"], limit=PAGE)
        c["messages_more"] = bool(c["messages"]) and self.older_messages(c["id"], c["messages"][0]["id"])
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

    def chat_attention(self, user):
        """The conversations an agent still owes something in: messages from humans posted since the agent
        last wrote there (they stay until it replies, read or not), and other messages it hasn't read
        (from agents, or notes like "(added ...)": reading them is enough, so two agents can't owe each
        other replies forever). Each has "messages" and "needs_reply"."""
        db, out = self.db(), []
        for cid, last_read in db.execute(
                "SELECT m.conversation_id, m.last_read FROM chat_members m JOIN conversations c ON c.id = m.conversation_id "
                "WHERE m.username = ? ORDER BY c.updated_at DESC", (user,)).fetchall():
            mine = db.execute("SELECT COALESCE(MAX(id), 0) FROM messages WHERE conversation_id = ? AND author = ? "
                              "COLLATE NOCASE", (cid, user)).fetchone()[0]
            owed = lambda m: m["role"] == "human" and m["id"] > mine
            msgs = [m for m in self.messages(cid, after=min(mine, last_read), limit=500)
                    if m["author"].lower() != user.lower() and (owed(m) or m["id"] > last_read)]
            if msgs:
                c = self.conversation(cid, user)
                c["messages"], c["count"], c["needs_reply"] = msgs[-PAGE:], len(msgs), any(owed(m) for m in msgs)
                out.append(c)
        return out

    def chat_notice(self, user):
        """What goes with every response to an agent that owes chat a reply (or has unread chat)."""
        convs = self.chat_attention(user)
        if not convs:
            return None
        total = sum(c["count"] for c in convs)
        summary = [{"id": c["id"], "name": c["name"], "messages": c["count"], "needs_reply": c["needs_reply"]}
                   for c in convs]
        where = "; ".join(f'{c["name"]} ({c["count"]}{", needs your reply" if c["needs_reply"] else ""})'
                          for c in convs)
        return {"count": total, "conversations": summary,
                "notice": f"You have {total} chat message{'s' if total != 1 else ''} to answer ({where}). Answer chat "
                          "before your next step: GET /api/chat/inbox, then reply in each conversation with "
                          "POST /api/chat/ID/messages. Messages from humans keep this notice up until you reply there, "
                          "even after you've read them; if nothing needs saying, reply briefly (\"Got it\"). "
                          "Messages from other agents only need reading (POST /api/chat/ID/read). Then carry on."}

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
        found = [self.user(str(n)) for n in names]
        if any(u["kind"] == "guest" for u in found):
            raise ApiError(400, "guests can read chats but can't be in them")
        members = {u["username"] for u in found}
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

    def delete_conversation(self, cid, role):
        """A whole conversation, its messages and its files, for good. Humans only."""
        if role != "human":
            raise ApiError(403, "only humans delete chats")
        c = self.conversation(cid)
        with self.write_lock:
            db = self.db()
            paths = [r[0] for r in db.execute("SELECT path FROM chat_files WHERE conversation_id = ?", (c["id"],))]
            with db:
                for table in ("chat_files", "messages", "chat_members"):
                    db.execute(f"DELETE FROM {table} WHERE conversation_id = ?", (c["id"],))
                db.execute("DELETE FROM conversations WHERE id = ?", (c["id"],))
        for p in paths:
            (self.files / p).unlink(missing_ok=True)
        return {"deleted": c["id"], "name": c["name"]}

    def add_member(self, cid, username, actor, role):
        c = self.conversation(cid)
        u = self.user(username)
        if u["kind"] == "guest":
            raise ApiError(400, "guests can read chats but can't be in them")
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
            self.post_message(c["id"], f"(added {u['username']})", actor, "system")  # a note, not a question
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

# Themes: name -> (label, palette, light or dark). Light is the default and
# Dark follows the device under Auto; Goth rave has extra rules in the CSS.
THEMES = {
    "light": ("Light", {"bg": "#f6f5f1", "panel": "#fff", "ink": "#1f2328", "line": "#e2dfd7", "accent": "#3a5bd9"}, "light"),
    "dark": ("Dark", {"bg": "#15171a", "panel": "#1d2024", "ink": "#e6e8eb", "line": "#2c3036", "accent": "#7d97f5"}, "dark"),
    "sepia": ("Sepia", {"bg": "#f4ecd8", "panel": "#fbf6e9", "ink": "#3b2f22", "dim": "#7a6a55", "line": "#e2d5b8",
                        "accent": "#9c4a1a", "accent-ink": "#fff8ee", "review": "#b0411e", "review-bg": "#f7e3d3",
                        "prog": "#2f5f8a", "prog-bg": "#e2ebf3", "ready": "#4d7a2c", "ready-bg": "#e6efd9",
                        "back": "#7a6a55", "back-bg": "#ece2cb", "done": "#5f564a", "done-bg": "#e9e1cf",
                        "agent": "#7b4fa0"}, "light"),
    "midnight": ("Midnight", {"bg": "#0d1321", "panel": "#151d2e", "ink": "#e3e9f5", "dim": "#8d9ab3", "line": "#24304a",
                              "accent": "#5aa9ff", "accent-ink": "#04121f", "review": "#ffab6e", "review-bg": "#3a2618",
                              "prog": "#7fb5ff", "prog-bg": "#172a47", "ready": "#5fd4a6", "ready-bg": "#12302a",
                              "back": "#9aa6bd", "back-bg": "#1d2638", "done": "#aab4c8", "done-bg": "#1c2435",
                              "agent": "#b49cff"}, "dark"),
    "forest": ("Forest", {"bg": "#10170f", "panel": "#182317", "ink": "#e6eedc", "dim": "#98a88c", "line": "#2a3a27",
                          "accent": "#9ccc65", "accent-ink": "#0d1a05", "review": "#f2a65a", "review-bg": "#3a2a14",
                          "prog": "#7cc4d9", "prog-bg": "#15303a", "ready": "#a8e07a", "ready-bg": "#1f3314",
                          "back": "#a3b096", "back-bg": "#202b1e", "done": "#b3bda8", "done-bg": "#1f281d",
                          "agent": "#d4a5ff"}, "dark"),
    "synthwave": ("Synthwave", {"bg": "#1a1033", "panel": "#241645", "ink": "#fbe9ff", "dim": "#b9a3d6", "line": "#3b2766",
                                "accent": "#ff7edb", "accent-ink": "#1a0626", "review": "#ff8b3d", "review-bg": "#40211a",
                                "prog": "#36f9f6", "prog-bg": "#0f3446", "ready": "#fede5d", "ready-bg": "#3a3214",
                                "back": "#c7b3e6", "back-bg": "#2c1d52", "done": "#a08cc2", "done-bg": "#281a49",
                                "agent": "#72f1b8"}, "dark"),
    "contrast": ("High contrast", {"bg": "#000", "panel": "#000", "ink": "#fff", "dim": "#d6d6d6", "line": "#9a9a9a",
                                   "accent": "#ffd400", "accent-ink": "#000", "review": "#ff7a7a", "review-bg": "#2a0000",
                                   "prog": "#66ccff", "prog-bg": "#001a2a", "ready": "#66ff66", "ready-bg": "#002200",
                                   "back": "#e6e6e6", "back-bg": "#1a1a1a", "done": "#e6e6e6", "done-bg": "#1a1a1a",
                                   "agent": "#ff99ff"}, "dark"),
    "goth": ("Goth rave", {"bg": "#09060d", "panel": "#130c1b", "ink": "#f3e8ff", "line": "#2f1d42", "accent": "#ff2bd6"},
             "dark"),
}
EXTRA_THEMES = ("sepia", "midnight", "forest", "synthwave", "contrast")


def theme_css():
    return "\n".join(":root[data-theme=%s]{%s;color-scheme:%s}" % (
        name, ";".join(f"--{k}:{v}" for k, v in THEMES[name][1].items()), THEMES[name][2]) for name in EXTRA_THEMES)


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
""" + theme_css() + """
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent)}
header.top{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:4px;padding:3px 8px;border-bottom:1px solid var(--line);background:var(--panel)}
header.top nav{display:flex;flex:1;min-width:0;overflow-x:auto;scrollbar-width:none}
header.top nav::-webkit-scrollbar{display:none}
nav a{padding:5px 9px;border-radius:6px;text-decoration:none;color:var(--ink);white-space:nowrap;display:inline-block;font-size:14px}
.navx{flex:none;padding:4px 9px;font-size:13px;line-height:1;background:transparent;border-color:transparent;color:var(--dim)}
.navx .min{display:none}
:root[data-nav=min] header.top{position:fixed;top:6px;right:6px;padding:0;border:0;background:transparent}
:root[data-nav=min] header.top nav{display:none}
:root[data-nav=min] .navx{background:var(--panel);border:1px solid var(--line);color:var(--ink);padding:7px 11px;box-shadow:0 1px 4px rgba(0,0,0,.18)}
:root[data-nav=min] .navx .open{display:none}:root[data-nav=min] .navx .min{display:inline}
:root[data-nav=min] header.top:has(nav .n) .navx::after{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--review);margin-left:5px;vertical-align:middle}
html[data-role=guest] :is(#cf,#mf,#sf,#ef,#df,#wf,#am,#nf,#uf,details:has(#ef),a.btn,.msg .mk,select.as,
nav a[href="/new"],button[onclick]:not(.navx):not([onclick^="copyWikiLink"]):not([onclick^="pickTheme"]):not([onclick="location.reload()"])){display:none!important}
html[data-role=guest] :is(.row:has(> #prio),h2:has(+ #cf),a[href^="/new"],a[href^="/wiki-new"]){display:none!important}
html[data-role=guest] select{pointer-events:none;opacity:.75}
.acw{position:relative}
.ac{position:absolute;z-index:40;top:100%;left:0;right:0;margin-top:2px;max-height:280px;overflow:auto;background:var(--panel);
border:1px solid var(--line);border-radius:8px;box-shadow:0 6px 18px rgba(0,0,0,.18);font-size:14px}
.ac-h{padding:6px 10px 2px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)}
.ac-i{padding:7px 10px;cursor:pointer}.ac-i.on,.ac-i:hover{background:var(--prog-bg)}
.ac-i .id{margin-right:4px}
.kinds{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.kinds button{display:grid;gap:2px;text-align:left;padding:10px 12px;border-width:2px;font-weight:400}
.kinds button b{font-size:16px}
.kinds button[aria-pressed=true]{border-color:var(--accent);background:var(--prog-bg)}
.dlinks{display:flex;gap:6px 16px;flex-wrap:wrap;margin:-6px 0 14px;font-size:14px}
.themes{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.themes button{display:flex;align-items:center;gap:8px;text-align:left;padding:10px 12px;border-width:2px}
.themes button[aria-pressed=true]{outline:3px solid var(--accent);outline-offset:2px}
.themes .sw{width:14px;height:14px;border-radius:50%;flex:none}
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
textarea{min-height:90px;resize:vertical;max-height:80vh}
form.stack{display:grid;gap:8px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.list .item{display:flex;gap:12px;align-items:center;justify-content:space-between;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:8px}
.list .item a{color:var(--ink);text-decoration:none;min-width:0;flex:1}
.detail{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:20px}
@media (max-width:800px){.detail{grid-template-columns:1fr}}
.box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.desc{overflow-wrap:anywhere}
.md p{white-space:pre-wrap;overflow-wrap:anywhere;margin:0 0 8px}.md>:last-child{margin-bottom:0}
.md h2,.md h3,.md h4{text-transform:none;letter-spacing:0;color:inherit;margin:12px 0 4px;font-weight:700}
.md h2{font-size:17px}.md h3{font-size:15.5px}.md h4{font-size:15px}.md>:first-child{margin-top:0}
.md pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:8px 10px;
font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0 0 8px}
.md code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);padding:1px 4px;border-radius:4px;overflow-wrap:anywhere}
.code{position:relative;margin:0 0 8px}.code pre{margin:0;padding-right:62px}.md>.code:last-child{margin-bottom:0}
.code .copy{position:absolute;top:5px;right:5px;padding:2px 9px;font-size:12px;font-weight:600;opacity:.85}
.code .copy:hover{opacity:1}
.tl{display:grid;gap:10px}
.c{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.c.agent{border-left:3px solid var(--agent)}
.c .who{font-weight:600}.c .who.agent{color:var(--agent)}
.c .body{overflow-wrap:anywhere;margin-top:4px}
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
.cbtns{gap:6px}
.unanswered{margin:8px 4px 0;color:var(--review)}
button.cancel{margin-left:auto;color:var(--review);border-color:transparent;background:transparent}
a.wl.missing{color:var(--review);text-decoration-style:dashed}
.wlink{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 0 0}
.wlink code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--panel);border:1px solid var(--line);padding:3px 7px;border-radius:5px;overflow-wrap:anywhere}
.tag{display:inline-block;font-size:12px;padding:1px 7px;border-radius:5px;background:var(--back-bg);color:var(--back);font-weight:600}
.tag.blocked{background:var(--review-bg);color:var(--review)}
.tag.epic{background:var(--prog-bg);color:var(--agent)}
.banner{border-radius:10px;padding:10px 14px;margin-bottom:14px;background:var(--review-bg);color:var(--review)}
.banner.prop{background:var(--prog-bg);color:var(--prog)}
.chat{display:grid;gap:8px;grid-template-columns:minmax(0,1fr)}
.msg{min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:8px 12px;max-width:min(680px,94%);justify-self:start}
.msg.mine{justify-self:end;background:var(--prog-bg)}
.msg.agent{border-left:3px solid var(--agent)}
.msg .who{font-weight:600}.msg .who.agent{color:var(--agent)}
.msg .body{overflow-wrap:anywhere;margin-top:2px}
.msg .mk{margin-left:6px;text-decoration:none;font-size:12px;opacity:.75}
html[data-tts="1"] .c,html[data-tts="1"] .msg,html[data-tts="1"] .desc{cursor:pointer}
.speaking{outline:3px solid var(--accent);outline-offset:2px}
button.latest{position:fixed;z-index:35;right:max(16px,env(safe-area-inset-right));padding:8px 14px;border-radius:999px;
background:var(--accent);color:var(--accent-ink);border-color:var(--accent);box-shadow:0 4px 14px rgba(0,0,0,.25);font-size:14px}
.earlier{text-align:center;margin:0 0 10px}.earlier .more{font-size:13px;padding:6px 14px}
.receipt{margin:6px 2px 0;text-align:right}.receipt .unseen{color:var(--review)}
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
.wk pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:8px 10px;font-size:13px}
.wk code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);padding:1px 4px;border-radius:4px}
.crumbs{font-size:13px;color:var(--dim);margin-bottom:4px}.crumbs a{text-decoration:none}
.tree,.tree ul{list-style:none;margin:0;padding:0}.tree ul{margin-left:16px;border-left:1px solid var(--line);padding-left:10px}
.tree li{margin:3px 0}.tree a{text-decoration:none}.tree>li>a{font-weight:600}
select.as{width:auto;max-width:12em;padding:7px 8px;font-size:14px}
.tl .new{animation:arrive 2.5s ease-out}
button.busy,select.busy{cursor:progress}
button.busy{position:relative;color:transparent!important}
button.busy::after{content:"";position:absolute;inset:0;margin:auto;width:14px;height:14px;border-radius:50%;
border:2px solid var(--ink);border-right-color:transparent;animation:spin .7s linear infinite}
button.primary.busy::after{border-color:var(--accent-ink);border-right-color:transparent}
select.busy{opacity:.55}
html.working,html.working *{cursor:progress}
html.working::after{content:"";position:fixed;z-index:99;top:0;left:-40%;height:3px;width:40%;background:var(--accent);
animation:slide 1.1s ease-in-out infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes slide{from{left:-40%}to{left:100%}}
@keyframes arrive{from{box-shadow:0 0 0 2px var(--accent)}to{box-shadow:0 0 0 2px transparent}}
@media (prefers-reduced-motion:reduce){.tl .new{animation:none}}
.list .item.prop{flex-wrap:wrap}.list .item.prop>a{flex:1 1 240px;min-width:0}.list .item.prop>.row{flex-wrap:wrap}
.sub{margin-left:18px;border-left:2px solid var(--line);padding-left:10px}
.deps li{margin:2px 0}
"""

JS = """
(function(){ try {
  const t = localStorage.getItem('at-theme'); if (t) document.documentElement.dataset.theme = t;
  if (localStorage.getItem('at-nav') === 'min') document.documentElement.dataset.nav = 'min';
} catch (e) {} })();
// The bar collapses to one small button (remembered in this browser).
function toggleNav(){
  const root = document.documentElement, min = root.dataset.nav !== 'min';
  if (min) root.dataset.nav = 'min'; else delete root.dataset.nav;
  try { min ? localStorage.setItem('at-nav', 'min') : localStorage.removeItem('at-nav'); } catch (e) {}
  const b = document.getElementById('navx'); b.setAttribute('aria-expanded', String(!min));
  b.setAttribute('aria-label', min ? 'Show menu' : 'Hide menu');
}
function setTheme(t){
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  try { t ? localStorage.setItem('at-theme', t) : localStorage.removeItem('at-theme'); } catch (e) {}
}
document.addEventListener('DOMContentLoaded', () => {
  const b = document.getElementById('navx'), min = document.documentElement.dataset.nav === 'min';
  if (b) { b.setAttribute('aria-expanded', String(!min)); b.setAttribute('aria-label', min ? 'Show menu' : 'Hide menu'); }
});
// Feedback for every action: the button (or dropdown) you just used is disabled
// and shows a spinner while its request runs, and a bar slides along the top.
let busyEl = null, busyAt = 0;
function noteTarget(el){ if (el && /^(BUTTON|SELECT)$/.test(el.tagName)) { busyEl = el; busyAt = Date.now(); } }
document.addEventListener('click', (e) => noteTarget(e.target.closest && e.target.closest('button')), true);
document.addEventListener('change', (e) => noteTarget(e.target), true);
document.addEventListener('submit', (e) => noteTarget(e.submitter || e.target.querySelector('button.primary, button')), true);
function markBusy(){
  document.documentElement.classList.add('working');
  const el = Date.now() - busyAt < 3000 ? busyEl : null;
  if (!el || el.classList.contains('busy')) return null;
  el.dataset.wasDisabled = el.disabled ? '1' : '';  // a form that disables its own button keeps control of it
  el.classList.add('busy'); el.disabled = true; el.setAttribute('aria-busy', 'true');
  return el;
}
function clearBusy(el){
  if (leaving) return;
  document.documentElement.classList.remove('working');
  if (!el) return;
  el.classList.remove('busy'); el.removeAttribute('aria-busy');
  if (!el.dataset.wasDisabled) el.disabled = false;
}
let leaving = false;
window.addEventListener('beforeunload', () => { leaving = true; });
// TTS mode (a per-user setting): tapping a comment, chat message or description
// reads just its text aloud with the device's speech; tap it again to stop.
let speaking = null;
function speakBox(box, text){
  const again = speaking === box;
  speechSynthesis.cancel();
  document.querySelectorAll('.speaking').forEach(x => x.classList.remove('speaking'));
  speaking = null;
  if (again || !text) return;
  const u = new SpeechSynthesisUtterance(text);
  u.onend = u.onerror = () => { box.classList.remove('speaking'); if (speaking === box) speaking = null; };
  box.classList.add('speaking'); speaking = box;
  speechSynthesis.speak(u);
}
document.addEventListener('click', (e) => {
  if (document.documentElement.dataset.tts !== '1' || !window.speechSynthesis) return;
  if (!e.target.closest || e.target.closest('a,button,input,textarea,select,summary,video,audio,img,.att')) return;
  if (String(window.getSelection ? getSelection() : '').length) return;  // selecting text, not tapping
  const box = e.target.closest('.c, .msg, .desc');
  const body = box && (box.classList.contains('desc') ? box : box.querySelector(':scope > .body'));
  if (body) speakBox(box, body.innerText.trim());
});
// Following a link to another page: show the bar until it arrives.
document.addEventListener('click', (e) => {
  const a = e.target.closest && e.target.closest('a[href]');
  if (a && !a.target && !a.hasAttribute('download') && a.origin === location.origin && !e.ctrlKey && !e.metaKey
      && !e.shiftKey && a.getAttribute('href')[0] !== '#') document.documentElement.classList.add('working');
});
window.addEventListener('pageshow', () => { leaving = false; document.documentElement.classList.remove('working'); });
// "Show earlier": load the page before the oldest shown, above it, without moving what you're reading.
document.addEventListener('click', async (e) => {
  const b = e.target.closest && e.target.closest('button.more'); if (!b) return;
  const list = document.getElementById(b.dataset.list), label = b.textContent;
  b.disabled = true; b.textContent = 'Loading…';
  try {
    const r = await fetch(b.dataset.url + b.dataset.before, {credentials: 'same-origin'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json(), h0 = document.documentElement.scrollHeight;
    list.querySelector(':scope > .empty')?.remove();
    list.insertAdjacentHTML('afterbegin', d.html);
    window.scrollBy(0, document.documentElement.scrollHeight - h0);
    if (d.before) { b.dataset.before = d.before; b.textContent = d.label; b.disabled = false; }
    else b.parentNode.remove();
  } catch (err) { b.disabled = false; b.textContent = label; }
});
// Long tickets and chats: a "Latest" button while the newest comment or message is off screen,
// counting what arrives while you're scrolled away.
document.addEventListener('DOMContentLoaded', () => {
  const list = document.getElementById('msgs') || document.getElementById('tl');
  if (!list) return;
  const composer = document.getElementById('mf');  // chat's message box, stuck to the bottom
  const btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'latest'; btn.hidden = true; btn.setAttribute('aria-label', 'Go to the latest');
  document.body.appendChild(btn);
  let fresh = 0;
  const bottomLimit = () => composer ? composer.getBoundingClientRect().top : innerHeight;
  function update(){
    const last = list.lastElementChild;
    const away = !!last && last.getBoundingClientRect().bottom > bottomLimit() + 4;
    if (!away) fresh = 0;
    btn.hidden = !away;
    btn.textContent = fresh ? '↓ ' + fresh + ' new' : '↓ Latest';
    btn.style.bottom = (composer ? Math.round(innerHeight - bottomLimit()) + 12 : 16) + 'px';
  }
  btn.addEventListener('click', () => {
    const smooth = !(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
    if (composer) window.scrollTo({top: document.body.scrollHeight, behavior: smooth ? 'smooth' : 'auto'});
    else list.lastElementChild.scrollIntoView({block: 'center', behavior: smooth ? 'smooth' : 'auto'});
    fresh = 0;
  });
  new MutationObserver((changes) => {
    // Only what arrives at the end is new (older pages load at the top).
    if (!btn.hidden) fresh += changes.filter(c => !c.nextSibling)
      .reduce((n, c) => n + [...c.addedNodes].filter(x => x.nodeType === 1).length, 0);
    update();
  }).observe(list, {childList: true});
  addEventListener('scroll', update, {passive: true});
  addEventListener('resize', update);
  update();
});
async function api(method, url, body){
  const el = markBusy();
  try {
    const r = await fetch(url, {method, headers: {'Content-Type':'application/json','X-Tickets':'1'},
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin'});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    // Most actions reload the page next (then it stays busy until the new page); otherwise give it back.
    setTimeout(() => clearBusy(el), 250);
    return j;
  } catch (e) { clearBusy(el); throw e; }
}
async function upload(tid, file, commentId){
  const q = new URLSearchParams({filename: file.name}); if (commentId) q.set('comment_id', commentId);
  const r = await fetch('/api/tickets/' + tid + '/attachments?' + q, {method: 'POST',
    headers: {'Content-Type': file.type || 'application/octet-stream', 'X-Tickets': '1'}, body: file, credentials: 'same-origin'});
  if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || ('HTTP ' + r.status)); }
}
function fail(e){ alert(e.message); }
// Copy buttons on code blocks: the original text, unwrapped.
async function copyText(text){
  try { await navigator.clipboard.writeText(text); return true; } catch (e) {
    const a = document.createElement('textarea'); a.value = text; document.body.appendChild(a); a.select();
    let ok = false; try { ok = document.execCommand('copy'); } catch (e2) {} a.remove(); return ok;
  }
}
document.addEventListener('click', async (e) => {
  const b = e.target.closest && e.target.closest('button.copy'); if (!b) return;
  const ok = await copyText(b.parentNode.querySelector('pre').textContent);
  b.textContent = ok ? 'Copied' : 'Copy failed'; setTimeout(() => { b.textContent = 'Copy'; }, 1500);
});
// Text boxes grow with what's typed, up to 80% of the screen, then scroll.
function fit(el){
  if (!el || el.tagName !== 'TEXTAREA') return;
  el.style.height = 'auto';
  const max = Math.round(window.innerHeight * 0.8), want = el.scrollHeight + 2;
  el.style.height = Math.min(want, max) + 'px';
  el.style.overflowY = want > max ? 'auto' : 'hidden';
}
document.addEventListener('input', (e) => fit(e.target));
document.addEventListener('DOMContentLoaded', () => document.querySelectorAll('textarea').forEach(fit));
window.addEventListener('resize', () => document.querySelectorAll('textarea').forEach(fit));
// Enter sends; Shift+Enter is a new line (and typing with an IME isn't interrupted).
function enterSends(boxId, formId){
  const box = document.getElementById(boxId); if (!box) return;
  box.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); document.getElementById(formId).requestSubmit(); }
  });
}
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
    tabs = [("/", "Dashboard"), ("/chat", "Chat"), ("/wiki", "Wiki"), ("/new", "+ New"), ("/settings", "Settings")]
    # Pages reached from the dashboard or settings light up those tabs.
    active = {"/backlog": "/", "/done": "/", "/users": "/settings", "/guidance": "/settings"}.get(active, active)
    # The chat badge is per viewer: Handler.html() fills in CHAT_BADGE.
    nav = "".join(f'<a href="{h}" class="{"on" if h == active else ""}">{esc(n)}'
                  f'{f"<span class=n>{review_count}</span>" if h == "/" and review_count else ""}'
                  f'{CHAT_BADGE if h == "/chat" else ""}</a>' for h, n in tabs)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Agent tickets</title>'
            f'<style>{CSS}</style><script>{JS}</script></head><body>'
            f'<header class="top"><nav aria-label="Main">{nav}</nav>'
            f'<button type="button" id="navx" class="navx" aria-expanded="true" aria-label="Hide menu" onclick="toggleNav()">'
            f'<span class="open">▴</span><span class="min">☰</span></button></header><main>{body}</main></body></html>')


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


DASH_JS = """
// Every 10 seconds, swap in the latest dashboard (and the nav's counts) without reloading.
setInterval(async () => {
  if (document.hidden) return;
  try {
    const r = await fetch(location.pathname, {credentials: 'same-origin', cache: 'no-store'});
    if (!r.ok) return;
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    for (const sel of ['#dash', 'header nav']) {
      const now = document.querySelector(sel), next = doc.querySelector(sel);
      if (now && next && now.innerHTML !== next.innerHTML) now.innerHTML = next.innerHTML;
    }
  } catch (e) {}
}, 10000);
"""


def render_dashboard(store):
    proposals = len(store.tickets(["backlog"], proposed=True))
    by = {s: store.tickets([s], kind="ticket", proposed=False) for s in ("review", "in_progress", "ready")}
    # Open epics, active ones first.
    epics = sorted(store.tickets(["backlog", "in_progress"], kind="epic", proposed=False),
                   key=lambda e: e["status"] != "in_progress")
    rows = []
    for e in epics:
        pr = e["progress"]
        pct = round(100 * pr["done"] / pr["total"]) if pr["total"] else 0
        doing = pr["by_status"].get("in_progress", 0)
        rows.append(f'<a class="card" href="/t/{e["id"]}"><div class="row"><span class="id">{e["id"]}</span>'
                    f'<span class="t">{esc(e["title"])}</span><span class="chip s-{e["status"]}">{EPIC_STATES[e["status"]]}</span>'
                    f'</div><div class="meta">{pr["done"]} of {pr["total"]} done'
                    f'{f" · {doing} in progress" if doing else ""}</div><div class="bar"><i style="width:{pct}%"></i></div></a>')
    backlog = len(store.tickets(["backlog"], proposed=False))
    body = ('<div id="dash"><h1>In flight</h1>'
            f'<p class="dlinks"><a href="/backlog">Backlog ({backlog})</a><a href="/done">Done</a>'
            + (f'<a href="/backlog">Proposals ({proposals})</a>' if proposals else "") + '</p><div class="cols">'
            + column("Needs your review", by["review"], "Nothing waiting on you.")
            + column("In progress", by["in_progress"], "No agent is working on anything.")
            + column("Ready to work", by["ready"], "Nothing cleared for agents. Promote something from the backlog.")
            + '</div>'
            + (f'<p class="banner prop">Agents proposed {proposals} ticket{"s" if proposals != 1 else ""}: '
               f'<a href="/backlog">review them in the backlog</a>.</p>' if proposals else "")
            + f'<h2>Epics ({len(epics)})</h2>' + ("".join(rows) or '<p class="empty">No open epics.</p>')
            + f'</div><script>{DASH_JS}</script>')
    return page("Dashboard", body, "/", len(by["review"]))


PROPOSAL_JS = """
function assignFor(tid){ const s = document.getElementById('as-' + tid); return s ? (s.value || null) : undefined; }
async function review(tid, action, ready){
  const pr = document.getElementById('pr-' + tid);
  const body = action === 'accept' ? {ready, assignee: assignFor(tid), priority: pr ? +pr.value : undefined} : {};
  try { await api('POST', '/api/tickets/' + tid + '/' + action, body); location.reload(); }
  catch (e) { fail(e); }
}
async function readyFor(tid){
  try { await api('PATCH', '/api/tickets/' + tid, {status: 'ready', assignee: assignFor(tid) ?? null}); location.reload(); }
  catch (e) { fail(e); }
}
"""


def proposal_row(store, t, children=()):
    kind = '<span class="tag epic">Epic</span> ' if t["kind"] == "epic" else ""
    deps = f' · after {", ".join(t["blocked_by"])}' if t.get("blocked_by") else ""
    more = f" (and its {len(children)} proposed ticket{'s' if len(children) != 1 else ''})" if children else ""
    tid = t["id"]
    head = (f'<div class="item prop"><a href="/t/{tid}"><span class="id">{tid}</span> {kind}<b>{esc(t["title"])}</b>'
            f'<div class="meta">proposed by {esc(t["created_by"])} {ago(t["created_at"])}{deps}</div></a><div class="row">'
            + assign_select(store, tid, t["assignee"], "Assign its tickets to" if t["kind"] == "epic" else "Assign to")
            + prio_select(tid, t["priority"])
            + f'<button class="primary" onclick="review(\'{tid}\',\'accept\',false)" title="Accept{esc(more)}">Accept</button>'
            + ("" if t["kind"] == "epic" else
               f'<button onclick="review(\'{tid}\',\'accept\',true)">Accept &amp; make ready</button>')
            + f'<button onclick="review(\'{tid}\',\'decline\')" title="Decline{esc(more)}">Decline</button></div></div>')
    if children:
        head += '<div class="sub">' + "".join(proposal_row(store, c) for c in children) + "</div>"
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
        blocks = [proposal_row(store, e, [t for t in proposed if t.get("epic_id") == e["id"]]) for e in epics]
        blocks += [proposal_row(store, t) for t in loose]
        if blocks:
            props = (f'<h2>Proposed by agents ({len(proposed)})</h2><p class="meta">Accepting or declining an epic does the same '
                     f'to its proposed tickets.</p><div class="list">{"".join(blocks)}</div><h2>Accepted</h2>'
                     f'<script>{PROPOSAL_JS}</script>')
    rows = []
    for t in items:
        btn = (f'<div class="row">{assign_select(store, t["id"], t["assignee"])}'
               f'<button class="primary" onclick="readyFor(\'{t["id"]}\')">Make ready</button></div>'
               if status == "backlog" and t["kind"] == "ticket" else "")
        pr = '<span class="p1">High · </span>' if t["priority"] == 1 else ""
        if t["kind"] == "epic":
            pr = '<span class="tag epic">Epic</span> ' + pr
        if t.get("declined"):
            pr = '<span class="tag">Declined</span> ' + pr
        if t.get("cancelled"):
            pr = '<span class="tag">Cancelled</span> ' + pr
        if t.get("blocked_by"):
            pr += f'<span class="tag blocked">after {", ".join(t["blocked_by"])}</span> '
        rows.append(f'<div class="item{" prop" if btn else ""}"><a href="/t/{t["id"]}"><span class="id">{t["id"]}</span> '
                    f'<b>{esc(t["title"])}</b><div class="meta">{pr}{"created" if status == "backlog" else "finished"} '
                    f'{ago(t["created_at"] if status == "backlog" else t["updated_at"])}</div></a>{btn}</div>')
    title = "Backlog" if status == "backlog" else "Done"
    search = (f'<form class="row" method="get" style="margin-bottom:12px"><input type="search" name="q" value="{esc(q)}" '
              f'placeholder="Search {title.lower()}" aria-label="Search"><button>Search</button></form>')
    hint = ('<p class="meta">Ideas waiting to be cleared. "Make ready" lets agents pick one up.</p>' if status == "backlog"
            else '<p class="meta">Tickets closed as done (and any cancelled or declined). <a href="/api/export">Download a backup</a> of everything '
                 '(tickets, comments, history and files).</p>')
    body = (f'<h1>{title}</h1>{hint}{search}{props}<div class="list">{"".join(rows) or "<p class=empty>Nothing here.</p>"}</div>'
            + (f'<script>{PROPOSAL_JS}</script>' if status == "backlog" and not props else ""))
    return page(title, body, "/" + status, len(store.tickets(["review"])))


def prio_select(tid, current=2, label="Priority"):
    """A priority picker that goes with Accept (and sits under a ticket's description)."""
    opts = "".join(f'<option value="{k}"{" selected" if k == current else ""}>{v}</option>' for k, v in PRIORITIES.items())
    return f'<select id="pr-{tid}" class="as" aria-label="{label}" title="{label}">{opts}</select>'


def assign_select(store, tid, current=None, label="Assign to"):
    """A small "for whom" picker that goes with Make ready / Accept: anyone, or a user."""
    opts = assignee_options(store, current).replace('<option value="">No one</option>', '<option value="">Anyone</option>', 1)
    return f'<select id="as-{tid}" class="as" aria-label="{label}" title="{label}">{opts}</select>'


def assignee_options(store, current=None):
    """<option>s for an assignee picker: no one, then active users."""
    opts = ['<option value="">No one</option>']
    for u in store.users():
        if (u["disabled"] or u["kind"] == "guest") and u["username"] != current:
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


def dep_candidates(store):
    """Open tickets that could be a predecessor, for the dependency pickers (as a <script> line)."""
    rows = [{"id": r["id"], "title": r["title"], "status": r["status"], "epic": r["epic_id"]}
            for r in store.db().execute(
                "SELECT id, title, status, epic_id FROM tickets WHERE kind = 'ticket' AND status != 'done' "
                "ORDER BY updated_at DESC LIMIT 600")]
    return "const DEP_CANDIDATES = " + json.dumps(rows).replace("</", "<\\/") + ";"


# A dropdown for the dependency fields: the open tickets in the same epic as soon
# as you're in the field, then any open ticket whose id or title matches what you
# type (same epic first). Arrows and Enter, or a click, choose one.
DEP_PICKER_JS = """
function depPicker(input, opts){
  const menu = document.createElement('div');
  menu.className = 'ac'; menu.hidden = true; menu.setAttribute('role', 'listbox');
  input.parentNode.insertBefore(menu, input.nextSibling);
  input.setAttribute('autocomplete', 'off'); input.setAttribute('aria-autocomplete', 'list');
  let items = [], at = -1;
  const word = () => opts.multi ? input.value.split(',').pop().trim() : input.value.trim();
  const chosen = () => opts.multi ? input.value.split(',').map(x => x.trim().toLowerCase()).filter(Boolean) : [];
  function show(){
    const q = word().toLowerCase(), epic = opts.epic(), skip = new Set([...opts.exclude(), ...chosen()]);
    const fit = DEP_CANDIDATES.filter(c => !skip.has(c.id.toLowerCase()) && (q
      ? c.id.toLowerCase().includes(q) || c.title.toLowerCase().includes(q) : epic && c.epic === epic));
    fit.sort((a, b) => (b.epic === epic && !!epic) - (a.epic === epic && !!epic));
    items = fit.slice(0, 12); at = items.length ? 0 : -1;
    if (!items.length) { menu.hidden = true; return; }
    let html = '', group = null;
    items.forEach((c, i) => {
      const g = epic && c.epic === epic ? 'In this epic' : 'Other open tickets';
      if (g !== group) { html += '<div class="ac-h">' + g + '</div>'; group = g; }
      html += '<div class="ac-i" role="option" data-i="' + i + '"><span class="id">' + c.id + '</span> ' +
        c.title.replace(/[&<>]/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[ch])) +
        ' <span class="meta">' + c.status.replace('_', ' ') + '</span></div>';
    });
    menu.innerHTML = html; menu.hidden = false; mark();
  }
  function mark(){ menu.querySelectorAll('.ac-i').forEach(el => el.classList.toggle('on', +el.dataset.i === at)); }
  function pick(i){
    const c = items[i]; if (!c) return;
    if (opts.multi) { const parts = input.value.split(','); parts.pop(); parts.push(' ' + c.id);
      input.value = parts.map(x => x.trim()).filter(Boolean).join(', ') + ', '; }
    else input.value = c.id;
    menu.hidden = true; input.focus();
    if (opts.onPick) opts.onPick(c); else show();
  }
  input.addEventListener('focus', show);
  input.addEventListener('input', show);
  input.addEventListener('blur', () => setTimeout(() => { menu.hidden = true; }, 150));
  menu.addEventListener('mousedown', (e) => { const el = e.target.closest('.ac-i'); if (el) { e.preventDefault(); pick(+el.dataset.i); } });
  input.addEventListener('keydown', (e) => {
    if (menu.hidden) return;
    if (e.key === 'ArrowDown') { at = Math.min(items.length - 1, at + 1); mark(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { at = Math.max(0, at - 1); mark(); e.preventDefault(); }
    else if (e.key === 'Enter' && at >= 0) { e.preventDefault(); pick(at); }
    else if (e.key === 'Escape') { menu.hidden = true; }
  });
}
"""


NEW_JS = """
const $ = (id) => document.getElementById(id);
// Ticket or epic: an epic is a plan that groups tickets, so the ticket-only fields go away.
function setKind(kind){
  const epic = kind === 'epic';
  document.querySelectorAll('.kinds button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.kind === kind)));
  document.querySelectorAll('.task-only').forEach(el => { el.hidden = epic; });
  $('nt-h').textContent = epic ? 'New epic' : 'New ticket';
  $('nt-go').textContent = epic ? 'Create epic' : 'Create ticket';
  $('nt-title').placeholder = epic ? 'The plan, e.g. Offline sync' : '';
  $('nf').dataset.kind = kind;
}
$('nf').onsubmit = async (e) => {
  e.preventDefault();
  const epic = $('nf').dataset.kind === 'epic';
  const data = {title: $('nt-title').value, description: $('nt-desc').value, kind: epic ? 'epic' : 'ticket'};
  if (!epic) Object.assign(data, {status: $('nt-status').value, priority: +$('nt-prio').value,
    assignee: $('nt-who').value || null, epic_id: $('nt-epic').value || null, depends_on: $('nt-deps').value});
  try { const t = await api('POST', '/api/tickets', data); location = '/t/' + t.id; } catch (err) { fail(err); }
};
setKind('ticket');
depPicker($('nt-deps'), {multi: true, epic: () => $('nt-epic').value, exclude: () => []});
"""


def render_new(store, epic=None, prefill=("", "")):
    opts = "".join(f'<option value="{s}"{" selected" if s == "backlog" else ""}>{LABELS[s]}</option>' for s in STATUSES)
    body = f"""<h1 id="nt-h">New ticket</h1><form class="stack box" id="nf" data-kind="ticket">
<div class="kinds" role="group" aria-label="What are you creating?">
 <button type="button" data-kind="ticket" aria-pressed="true" onclick="setKind('ticket')"><b>Ticket</b>
  <span class="meta">A piece of work for an agent or a human</span></button>
 <button type="button" data-kind="epic" aria-pressed="false" onclick="setKind('epic')"><b>Epic</b>
  <span class="meta">A plan that groups tickets; its state follows them</span></button>
</div>
<label>Title<input type="text" id="nt-title" required value="{esc(prefill[0])}"></label>
<label>Description<textarea id="nt-desc" rows="8">{esc(prefill[1])}</textarea></label>
<p class="meta" style="margin:-4px 0 0">Link wiki pages with <code>[[section/page]]</code>: every wiki page has a Copy link button.</p>
<div class="row task-only"><label>Status <select id="nt-status">{opts}</select></label>
<label>Priority <select id="nt-prio"><option value="1">High</option><option value="2" selected>Normal</option><option value="3">Low</option></select></label>
<label>Assign to <select id="nt-who">{assignee_options(store)}</select></label></div>
<div class="row task-only"><label>Epic <select id="nt-epic">{epic_options(store, epic)}</select></label></div>
<label class="task-only acw">Must be done first (optional: pick from the list, or type ids or words)
<input type="text" id="nt-deps" placeholder="amber-otter, quiet-falcon"></label>
<div><button class="primary" id="nt-go">Create ticket</button></div></form>
<script>{dep_candidates(store)}{DEP_PICKER_JS}{NEW_JS}</script>"""
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
 <option value="agent">Agent</option><option value="human">Human</option><option value="guest">Guest (read-only)</option></select><button class="primary">Add</button></form>
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


SETTINGS_JS = """
async function setTts(on){
  try { await api('PATCH', '/api/me/prefs', {tts: on}); document.documentElement.dataset.tts = on ? '1' : ''; }
  catch (e) { fail(e); document.getElementById('tts').checked = !on; }
}
document.addEventListener('DOMContentLoaded', () => { if (!window.speechSynthesis) document.getElementById('tts-none').hidden = false; });
function pickTheme(t){
  setTheme(t);
  document.querySelectorAll('.themes button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.t === t)));
}
document.addEventListener('DOMContentLoaded', () => pickTheme(document.documentElement.dataset.theme || ''));
"""


def render_settings(role, prefs):
    def opt(name, label, pal):
        style = f"background:{pal['bg']};color:{pal['ink']};border-color:{pal['line']}"
        return (f'<button type="button" data-t="{name}" onclick="pickTheme(\'{name}\')" aria-pressed="false" style="{style}">'
                f'<span class="sw" style="background:{pal["accent"]}"></span>{esc(label)}</button>')
    themes = (opt("", "Auto (follows your device)", THEMES["light"][1])
              + "".join(opt(n, label, pal) for n, (label, pal, _) in THEMES.items()))
    team = ""
    if role == "human":
        team = ('<h2>Team</h2><div class="list">'
                '<div class="item"><a href="/users"><b>Users</b><div class="meta">Add humans and agents, new tokens, '
                'disable accounts.</div></a></div>'
                '<div class="item"><a href="/guidance"><b>Guidance</b><div class="meta">What agents are told about how to '
                'work here, for all of them and each one.</div></a></div>'
                '<div class="item"><a href="/api/export"><b>Download a backup</b><div class="meta">Everything: tickets, '
                'comments, chat, wiki, history and files.</div></a></div></div>')
    tts = (f'<h2>TTS mode</h2><div class="box"><label class="row" style="gap:10px;cursor:pointer">'
           f'<input type="checkbox" id="tts" style="width:22px;height:22px"{" checked" if prefs["tts"] else ""} '
           f'onchange="setTts(this.checked)"><span><b>Read aloud on tap</b><br><span class="meta">Tap a comment, chat '
           f'message or ticket description to hear its text; tap again to stop. Saved for your account, on every '
           f'device.</span></span></label><p class="meta" id="tts-none" hidden>This browser can\'t read aloud.</p></div>')
    body = (f'<h1>Settings</h1>{tts}<h2>Theme</h2><p class="meta">Saved in this browser.</p><div class="themes">{themes}</div>'
            f'{team}<h2>For agents</h2><div class="list">'
            '<div class="item"><a href="/api"><b>The agent briefing</b><div class="meta">What agents read to learn this '
            'system (GET /api).</div></a></div></div><script>' + SETTINGS_JS + '</script>')
    return body


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


def status_phrase(old, new, role):
    """A status change as what someone did ("sent it back for more work"), not a move between columns."""
    if new == "done":
        return "<b>closed it as done</b>"
    if old == "done":
        return f"<b>reopened it</b>{' into the backlog' if new == 'backlog' else ''}"
    if old == "review" and new == "in_progress":
        return "<b>sent it back for more work</b>"
    if new == "review":
        return "<b>handed it back for review</b>" if role == "agent" else "<b>moved it to review</b>"
    if new == "in_progress":
        return "<b>started it</b>"
    if new == "ready":
        return "<b>released it</b> back to Ready" if old == "in_progress" else "<b>made it ready</b>"
    if new == "backlog":
        return "<b>moved it back to the backlog</b>"
    return f"moved it from {LABELS.get(old, old)} to <b>{LABELS.get(new, new)}</b>"


def ticket_sig(t):
    """Changes to these mean the page's header and buttons are stale: "refresh to see it"."""
    key = [t["status"], t["assignee"], t["title"], t["priority"], t["epic_id"], t["proposed"], t["declined"], t["cancelled"],
           t["description"], t["kind"], t["blocked_by"], [d["id"] for d in t["depends_on"]]]
    return hashlib.sha256(json.dumps(key, default=str).encode()).hexdigest()[:16]


def ticket_timeline(store, t, empty='<p class="empty">No activity yet.</p>'):
    """The activity list: comments (with their files), loose files and events, oldest first. Every
    entry has an id, and a comment a data-v (its file count), so the live page can add and update them."""
    by_comment = {}
    loose = []
    for a in t["attachments"]:
        (by_comment.setdefault(a["comment_id"], []) if a["comment_id"] else loose).append(a)
    items = []
    for c in t["comments"]:
        mine = by_comment.get(c["id"], [])
        files = "".join(render_attachment(a, store) for a in mine)
        role = "agent" if c["role"] == "agent" else ""
        items.append((c["created_at"], f'<div class="c {role}" id="c{c["id"]}" data-v="{len(mine)}">'
                                       f'<span class="who {role}">{esc(c["author"])}</span> '
                                       f'<span class="meta" title="{when(c["created_at"])}">{ago(c["created_at"])}</span>'
                                       f'<div class="body md">{wiki_html(store, c["body"])}</div>{files}</div>'))
    for a in loose:
        items.append((a["created_at"], f'<div class="c {"agent" if a["role"] == "agent" else ""}" id="a{a["id"]}">'
                                       f'<span class="who">{esc(a["author"])}</span> <span class="meta">attached a file · '
                                       f'{ago(a["created_at"])}</span>{render_attachment(a, store)}</div>'))
    for e in t["events"]:
        if e["field"] == "created":
            text = f'{esc(e["actor"])} created it in {LABELS.get(e["new"], e["new"])}'
        elif e["field"] == "proposed":
            text = f'{esc(e["actor"])} proposed it'
        elif e["field"] == "accepted":
            text = f'{esc(e["actor"])} accepted it into {LABELS.get(e["new"], e["new"])}'
        elif e["field"] == "cancelled":
            text = f'{esc(e["actor"])} <b>cancelled</b> it'
        elif e["field"] == "declined":
            text = f'{esc(e["actor"])} <b>declined</b> it'
        elif e["field"] == "depends_on":
            text = (f'{esc(e["actor"])} said <a href="/t/{esc(e["new"])}">{esc(e["new"])}</a> must be done first' if e["new"]
                    else f'{esc(e["actor"])} removed the dependency on {esc(e["old"])}')
        elif e["field"] == "priority":
            label = PRIORITIES.get(int(e["new"]), e["new"]) if str(e["new"]).isdigit() else e["new"]
            text = f'{esc(e["actor"])} set the priority to <b>{esc(label)}</b>'
        elif e["field"] == "epic_status":
            text = f'the epic is now <b>{EPIC_STATES.get(e["new"], e["new"])}</b> (after a change by {esc(e["actor"])})'
        elif e["field"] == "checklist":
            text = f'{esc(e["actor"])} reported the checklist: {esc(e["new"])} done'
        elif e["field"] == "epic_id":
            text = (f'{esc(e["actor"])} moved it into epic <a href="/t/{esc(e["new"])}">{esc(e["new"])}</a>' if e["new"]
                    else f'{esc(e["actor"])} took it out of its epic')
        elif e["field"] == "status":
            text = f'{esc(e["actor"])} {status_phrase(e["old"], e["new"], e["role"])}'
        elif e["field"] == "assignee":
            text = (f'{esc(e["actor"])} assigned it to <b>{esc(e["new"])}</b>' if e["new"]
                    else f'{esc(e["actor"])} unassigned it')
        elif e["field"] == "description":
            text = f'{esc(e["actor"])} edited the description'
        else:
            text = f'{esc(e["actor"])} changed {esc(e["field"]).replace("_", " ")} to {esc(e["new"] or "nothing")}'
        items.append((e["at"], f'<div class="ev" id="e{e["id"]}">{text} · <span title="{when(e["at"])}">{ago(e["at"])}</span></div>'))
    items.sort(key=lambda x: x[0])
    return "".join(h for _, h in items) or empty


def ticket_wiki_box(t):
    if not t["wiki_links"]:
        return ""
    lis = "".join(f'<li><a class="wl{" missing" if w.get("missing") else ""}" href="/wiki/{w["path"]}">'
                  f'{esc(w["title"] or w["path"])}</a>{" <span class=meta>(no such page yet)</span>" if w.get("missing") else ""}'
                  f'</li>' for w in t["wiki_links"])
    return f'<h2>Wiki pages</h2><div class="box"><ul class="ck">{lis}</ul></div>'


def ticket_checklist(t):
    ck = t["checklist"]
    if ck is None:
        return '<p class="meta">None reported yet. The agent working the ticket uploads it as it goes.</p>'
    lis = "".join(f'<li class="note">{esc(i["text"])}</li>' if i.get("note") else
                  f'<li>{"<span class=x>☑</span>" if i["done"] else "☐"} {esc(i["text"])}</li>' for i in ck["items"])
    pct = round(100 * ck["done"] / ck["total"]) if ck["total"] else 0
    return (f'<p class="meta">Reported by {esc(ck["updated_by"])} {ago(ck["updated_at"])}</p>'
            + (f'<div class="meta">{ck["done"]} of {ck["total"]} done</div><div class="bar"><i style="width:{pct}%"></i></div>'
               if ck["total"] else "") + f'<ul class="ck">{lis}</ul>')


def ticket_span(t, start, end=None):
    """A copy of a (full) ticket with only the comments, files and history from `start` to before `end`."""
    inside = lambda at: at >= start and (end is None or at < end)
    out = dict(t)
    out["comments"] = [c for c in t["comments"] if inside(c["created_at"])]
    ids = {c["id"] for c in out["comments"]}
    out["attachments"] = [a for a in t["attachments"] if a["comment_id"] in ids or (a["comment_id"] is None and inside(a["created_at"]))]
    out["events"] = [e for e in t["events"] if inside(e["at"])]
    return out


def ticket_earlier(store, tid, before):
    """The page of activity before comment `before`, for the ticket page's "Show earlier" button."""
    t = store.detail(tid)
    page_ = store.comment_page(t["id"], before)
    if not page_["comments"]:
        return {"html": "", "before": None, "label": ""}
    end = next(c["created_at"] for c in t["comments"] if c["id"] == before)
    start = page_["comments"][0]["created_at"] if page_["more"] else 0
    html = ticket_timeline(store, ticket_span(t, start, end), empty="")
    left = sum(1 for c in t["comments"] if c["created_at"] < start)
    return {"html": html, "before": page_["comments"][0]["id"] if page_["more"] else None,
            "label": f"Show earlier activity ({left} more comment{'s' if left != 1 else ''})"}


def ticket_live(store, tid, start=0):
    """What the open ticket page polls for: the activity from `start` (where the page's view begins) on."""
    t = ticket_span(store.detail(tid), start)
    ck = t["checklist"]
    return {"sig": ticket_sig(t), "timeline": ticket_timeline(store, t),
            "checklist": ticket_checklist(t), "checklist_v": str(ck["updated_at"]) if ck else "",
            "wiki": ticket_wiki_box(t), "wiki_v": json.dumps(t["wiki_links"])}


LIVE_JS = """
// New comments, files and events appear as they happen; the checklist and
// linked pages update in place. Nothing here touches the comment box. Changes
// to the ticket itself (status, assignee, title...) only show a banner.
(function(){
  let busy = false;
  async function live(){
    if (busy || document.hidden) return;
    busy = true;
    try {
      const r = await fetch('/t/' + TID + '/live?from=' + document.getElementById('tl').dataset.from,
                            {credentials: 'same-origin', cache: 'no-store'});
      if (!r.ok) return;
      const d = await r.json(), tl = document.getElementById('tl'), tmp = document.createElement('div');
      tmp.innerHTML = d.timeline;
      for (const el of [...tmp.children]) {
        if (!el.id) continue;
        const cur = document.getElementById(el.id);
        if (!cur) { tl.querySelector(':scope > .empty')?.remove(); el.classList.add('new'); tl.appendChild(el); }
        else if (cur.dataset.v !== el.dataset.v) cur.replaceWith(el);
      }
      const ck = document.getElementById('ckbox');
      if (ck.dataset.v !== d.checklist_v) { ck.innerHTML = d.checklist; ck.dataset.v = d.checklist_v; }
      const wb = document.getElementById('wikibox');
      if (wb.dataset.v !== d.wiki_v) { wb.innerHTML = d.wiki; wb.dataset.v = d.wiki_v; }
      if (d.sig !== SIG) document.getElementById('changed').hidden = false;
    } catch (e) {} finally { busy = false; }
  }
  setInterval(live, 5000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) live(); });
  // Your unsent comment is kept in this browser until you post it.
  const box = document.getElementById('cbody'), key = 'at-draft:' + TID;
  try { const saved = localStorage.getItem(key); if (saved && !box.value) { box.value = saved; fit(box); } } catch (e) {}
  box.addEventListener('input', () => {
    try { box.value ? localStorage.setItem(key, box.value) : localStorage.removeItem(key); } catch (e) {}
  });
})();
function clearDraft(){ try { localStorage.removeItem('at-draft:' + TID); } catch (e) {} }
"""


def render_ticket(store, tid):
    t = store.detail(tid, limit=PAGE)
    timeline = ticket_timeline(store, t)
    view_from = t["comments"][0]["created_at"] if t["comments_more"] else 0
    earlier = (f'<div class="earlier"><button type="button" class="more" data-list="tl" '
               f'data-url="/t/{t["id"]}/earlier?before=" data-before="{t["comments"][0]["id"]}">Show earlier activity '
               f'({t["comments_total"] - len(t["comments"])} more comment{"s" if t["comments_total"] - len(t["comments"]) != 1 else ""})'
               f'</button></div>' if t["comments_more"] else "")
    wiki_box = ticket_wiki_box(t)
    ckhtml = ticket_checklist(t)
    ck_v = str(t["checklist"]["updated_at"]) if t["checklist"] else ""

    def chip(x):
        st = x["status"]
        label = "Declined" if x.get("declined") else "Cancelled" if x.get("cancelled") else LABELS[st]
        return f'<span class="chip s-{st}">{label}</span>'

    def link_list(items, removable=False):
        lis = "".join(f'<li><a href="/t/{x["id"]}"><span class="id">{x["id"]}</span> {esc(x["title"])}</a> {chip(x)}'
                      + (f' <button onclick="rmDep(\'{x["id"]}\')" aria-label="Remove {x["id"]}" '
                         f'style="padding:1px 7px">×</button>' if removable else "") + "</li>" for x in items)
        return f'<ul class="ck deps">{lis}</ul>' if lis else '<p class="meta">None.</p>'

    banners = ""
    if t["proposed"]:
        banners += (f'<div class="banner prop">Proposed by {esc(t["created_by"])}. '
                    + assign_select(store, t["id"], t["assignee"], "Assign its tickets to" if t["kind"] == "epic" else "Assign to")
                    + " " + prio_select(t["id"], t["priority"])
                    + f' <button class="primary" onclick="review(TID,\'accept\',false)">Accept</button> '
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
                      f'<h2>Must be done first</h2><div class="box">{link_list(t["depends_on"] + t["done_dependencies"], True)}'
                      f'<form class="row" id="df"><span class="acw" style="flex:1;min-width:120px"><input type="text" id="dep" '
                      f'placeholder="pick or type an id or words" aria-label="Add a predecessor"></span><button>Add</button></form>'
                      f'</div>'
                      f'<h2>Unblocks</h2><div class="box">{link_list(t["unblocks"])}</div>')

    s = t["status"]
    # Each button says what happens to the ticket, not what you think of the work: agreeing with an
    # agent's idea that still needs building is a "Send back for more work", not a "Close as done".
    actions = {
        "backlog": [("ready", "Make ready", True)],
        "ready": [("backlog", "Move back to backlog", False)],
        "in_progress": [("review", "Move to review", False), ("ready", "Release to ready", False)],
        "review": [("done", "Close as done", True), ("in_progress", "Send back for more work", False)],
        "done": [("in_progress", "Reopen", False)],
    }[s]
    if t["kind"] == "epic":  # an epic's state follows its tickets; nothing to set by hand
        actions = []
    if t["proposed"]:  # the proposal banner has the buttons
        actions = []
    btns = "".join(f'<button class="{"primary" if p else ""}" onclick="setStatus(\'{t["id"]}\',\'{to}\')">{esc(lbl)}</button>'
                   for to, lbl, p in actions)
    if s == "done" and (t["cancelled"] or t["declined"]):  # reopening goes back to the backlog
        btns = btns.replace(f"setStatus('{t['id']}','in_progress')", f"setStatus('{t['id']}','backlog')")
    if s != "done" and not t["proposed"]:  # out of the normal cycle: overtaken by events, a duplicate...
        btns += (f'<button class="cancel" onclick="cancelTicket()">Cancel {"epic" if t["kind"] == "epic" else "ticket"}</button>')
    if s == "backlog" and t["kind"] == "ticket" and not t["proposed"]:  # Make ready, for someone
        btns = (assign_select(store, t["id"], t["assignee"])
                + btns.replace(f"setStatus('{t['id']}','ready')", f"readyFor('{t['id']}')", 1))
    # What posting can do, besides commenting. Enter in the box only ever comments.
    if t["kind"] == "epic" or t["proposed"]:
        comment_buttons = '<button class="primary" id="csend">Comment</button>'
    elif s == "review":
        comment_buttons = ('<button class="primary" id="csend" data-move="in_progress">Send back with this comment</button>'
                           '<button data-move="done">Close as done</button><button data-move="">Just comment</button>')
    elif s == "done":
        comment_buttons = ('<button class="primary" id="csend">Comment</button>'
                           f'<button data-move="{"backlog" if t["cancelled"] or t["declined"] else "in_progress"}">Comment &amp; reopen</button>')
    else:
        comment_buttons = '<button class="primary" id="csend">Comment</button>'
    owed = store.comments_owed(t["assignee"], t["id"]) if t["assignee"] and s != "done" else []
    unanswered = (f'<p class="meta unanswered">{esc(t["assignee"])} hasn\'t answered '
                  f'{"your latest comment" if owed[0]["new_comments"] == 1 else str(owed[0]["new_comments"]) + " comments"} '
                  f'yet.</p>' if owed and store.user(t["assignee"])["kind"] == "agent" else "")
    if s == "review" and t["kind"] == "ticket" and not t["proposed"]:
        handoff = next((c["body"] for c in reversed(t["comments"]) if c["role"] == "agent"), "")
        first = handoff.strip().lower()
        if first.startswith("needs a decision"):
            # The likely next step is answering and sending it back, so that's the main button.
            tid_ = t["id"]
            btns = (btns.replace(f'class="primary" onclick="setStatus(\'{tid_}\',\'done\')"', f'class="" onclick="setStatus(\'{tid_}\',\'done\')"')
                        .replace(f'class="" onclick="setStatus(\'{tid_}\',\'in_progress\')"',
                                 f'class="primary" onclick="setStatus(\'{tid_}\',\'in_progress\')"'))
            say = ("<b>The agent needs a decision from you.</b> Answer in a comment, then <b>Send back with this "
                   "comment</b> so it carries on (or <b>Close as done</b> if nothing's left to do).")
        elif first.startswith("done"):
            say = ("<b>The agent says it's done.</b> Check it, then <b>Close as done</b>, or <b>Send back for more "
                   "work</b> with what to change.")
        else:
            say = "<b>Waiting on you.</b> <b>Close as done</b> if it's finished, or <b>Send back for more work</b> with what's next."
        banners += (f'<div class="banner prop">{say} <span class="meta">Agreeing with an idea that still needs '
                    f'building is a send-back: the button is about the ticket, not your verdict.</span></div>')
    body = f"""<div class="row" style="margin-bottom:6px"><span class="id">{t['id']}</span>
<span class="chip s-{s}">{"Cancelled" if t["cancelled"] else "Declined" if t["declined"] else (EPIC_STATES if t['kind'] == 'epic' else LABELS)[s]}</span>{'<span class="p1">High priority</span>' if t['priority'] == 1 else ''}
<span class="meta">{'assigned to <b>' + esc(t['assignee']) + '</b>' if t['assignee'] else 'unassigned'}</span>
{'<span class="tag epic">Epic</span>' if t['kind'] == 'epic' else ''}
{f'<span class="meta">in epic <a href="/t/{t["epic_id"]}">{t["epic_id"]} · {esc(t.get("epic_title") or "")}</a></span>' if t['epic_id'] else ''}</div>
<h1>{esc(t['title'])}</h1>
<div class="banner prop" id="changed" hidden>This ticket has changed (status, assignee or details).
 <button onclick="location.reload()">Refresh</button> <span class="meta">Your unsent comment is kept.</span></div>{banners}
<div class="row" style="margin-bottom:16px">{btns}</div>
<div class="detail"><div>
 <div class="box"><div class="desc md">{wiki_html(store, t['description']) or '<span class="meta">No description.</span>'}</div></div>
 <div class="row" style="margin-top:8px"><label class="meta" for="prio">Priority</label>
  <select id="prio" class="as" onchange="setPrio(this.value)">{''.join(f'<option value="{k}"{" selected" if k == t["priority"] else ""}>{v}</option>' for k, v in PRIORITIES.items())}</select></div>
 {epic_box}
 <h2>Activity</h2>{earlier}<div class="tl" id="tl" data-from="{view_from}">{timeline}</div>
 {unanswered}<h2>Comment</h2><form class="stack box" id="cf">
  <textarea id="cbody" placeholder="Feedback, a decision, or a question for the agent (Enter posts it as a comment only; Shift+Enter for a new line)" aria-label="Comment"></textarea>
  <div class="row"><input type="file" id="cfiles" multiple aria-label="Attach files"></div>
  <div class="row cbtns">{comment_buttons}</div></form>
</div><aside>
 <h2 style="margin-top:0">Checklist</h2><div class="box" id="ckbox" data-v="{ck_v}">{ckhtml}</div>
 <div id="wikibox" data-v="{esc(json.dumps(t['wiki_links']))}">{wiki_box}</div>
 <h2>Assigned to</h2><div class="box"><select id="who" aria-label="Assigned to"
  onchange="assign(this.value)">{assignee_options(store, t['assignee'])}</select></div>
 {side_links}
 <h2>Details</h2><div class="box"><dl class="kv"><dt>Created</dt><dd>{when(t['created_at'])} by {esc(t['created_by'])}</dd><dt>Updated</dt><dd>{ago(t['updated_at'])}</dd></dl>
  <details style="margin-top:10px"><summary>Edit</summary><form class="stack" id="ef" style="margin-top:8px">
   <label>Title<input type="text" id="etitle" value="{esc(t['title'])}"></label>
   <label>Description<textarea id="edesc" rows="8">{esc(t['description'])}</textarea></label>
   <button class="primary">Save</button></form></details></div>
</aside></div>
<script>
const TID = {json.dumps(t['id'])}, SIG = {json.dumps(ticket_sig(t))}, $ = (id) => document.getElementById(id);
document.getElementById('cf').onsubmit = async (e) => {{
  e.preventDefault();
  // Which button: its data-move is the status change, if any. Enter (no button) only comments.
  const btn = e.submitter || document.getElementById('csend'), move = (e.submitter && e.submitter.dataset.move) || '';
  if (btn.disabled || (!$('cbody').value.trim() && !$('cfiles').files.length && !move)) return;
  const label = btn.textContent; btn.disabled = true; btn.textContent = 'Posting…';
  try {{
    const files = [...$('cfiles').files], text = $('cbody').value.trim();
    let cid = null;
    if (text || files.length) cid = (await api('POST', '/api/tickets/' + TID + '/comments', {{body: text || '(attached files)'}})).id;
    for (const f of files) {{ btn.textContent = 'Uploading ' + f.name + '…'; await upload(TID, f, cid); }}
    if (move) await api('PATCH', '/api/tickets/' + TID, {{status: move}});
    clearDraft();
    location.reload();
  }} catch (err) {{ fail(err); btn.disabled = false; btn.textContent = label; }}
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
{dep_candidates(store) if t["kind"] == "ticket" else ""}
if (df) {{ {DEP_PICKER_JS}
  depPicker($('dep'), {{epic: () => {json.dumps(t["epic_id"] or "")},
    exclude: () => {json.dumps([t["id"].lower()] + [d["id"].lower() for d in t["depends_on"] + t["done_dependencies"]])},
    onPick: () => df.requestSubmit()}}); }}
{PROPOSAL_JS}
{LIVE_JS}
enterSends('cbody', 'cf');
document.getElementById('ef').onsubmit = async (e) => {{
  e.preventDefault();
  try {{ await api('PATCH', '/api/tickets/' + TID, {{title: $('etitle').value, description: $('edesc').value}});
    location.reload(); }} catch (err) {{ fail(err); }}
}};
async function cancelTicket() {{
  const epic = {json.dumps(t["kind"] == "epic")};
  if (!confirm(epic ? 'Cancel this epic and its unfinished tickets?' : 'Cancel this ticket? It moves to Done, marked cancelled.')) return;
  try {{
    if ($('cbody').value.trim()) {{ await api('POST', '/api/tickets/' + TID + '/comments', {{body: $('cbody').value.trim()}}); clearDraft(); }}
    await api('POST', '/api/tickets/' + TID + '/cancel', {{}}); location.reload();
  }} catch (err) {{ fail(err); }}
}}
async function setPrio(v) {{ try {{ await api('PATCH', '/api/tickets/' + TID, {{priority: +v}}); location.reload(); }}
  catch (err) {{ fail(err); }} }}
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
    files = "".join(render_attachment(f, store) for f in m["attachments"])
    return (f'<div class="msg {role}{mine}" id="m{m["id"]}"><div><span class="who {role}">{esc(m["author"])}</span> '
            f'<span class="meta" title="{when(m["created_at"])}">{ago(m["created_at"])}</span>'
            f'<a class="mk" href="/new?from_message={m["id"]}" title="Make a ticket from this message">→ ticket</a></div>'
            f'<div class="body md">{wiki_html(store, m["body"])}</div>{files}</div>')


def ticket_from_message(store, mid):
    """Title and description for a new ticket that quotes a chat message."""
    m = store.message(mid)
    c = store.conversation(m["conversation_id"])
    first = m["body"].strip().splitlines()[0] if m["body"].strip() else ""
    quoted = "\n".join("> " + line for line in m["body"].splitlines())
    return first[:100], f"From chat ({c['name']}), {m['author']} wrote:\n\n{quoted}\n"


CHAT_LIST_JS = """
const $ = (id) => document.getElementById(id);
enterSends('sb', 'sf');
document.getElementById('sf').onsubmit = async (e) => {
  e.preventDefault();
  const members = [...document.querySelectorAll('input[name=who]:checked')].map(x => x.value);
  try { const c = await api('POST', '/api/chat', {title: $('st').value, members, body: $('sb').value});
        location.href = '/chat/' + c.id; }
  catch (err) { fail(err); }
};
"""


def receipt_html(store, cid):
    """Under the latest message: who has read it, and who hasn't yet (and for how long)."""
    last = store.messages(cid, limit=1)
    if not last:
        return ""
    m, reads = last[-1], store.read_up_to(cid)
    others = [u for u in reads if u.lower() != m["author"].lower()]
    seen = [u for u in others if reads[u] >= m["id"]]
    unseen = [u for u in others if reads[u] < m["id"]]
    parts = []
    if seen:
        parts.append("Read by " + esc(", ".join(seen)))
    if unseen:
        parts.append(f'<span class="unseen">Not read yet by {esc(", ".join(unseen))}</span> (sent {ago(m["created_at"])})')
    return " · ".join(parts)


def render_chat_list(store, viewer):
    rows = []
    for c in store.conversations(viewer):
        last = c["last_message"]
        snip = f'<b>{esc(last["author"])}:</b> {esc(last["body"][:160])}' if last else "No messages yet."
        badge = f' <span class="count">{c["unread"]}</span>' if c.get("unread") else ""
        group = f' <span class="meta">· {len(c["members"])} people</span>' if c["title"] else ""
        rows.append(f'<div class="item{" unread" if c.get("unread") else ""}"><a href="/chat/{c["id"]}"><b>{esc(c["name"])}</b>'
                    f'{badge}{group}<div class="snip">{snip}</div></a><span class="meta">{ago(c["updated_at"])}</span></div>')
    people = [u for u in store.users() if not u["disabled"] and u["kind"] != "guest"
              and u["username"].lower() != viewer.lower()]
    picks = "".join(f'<label><input type="checkbox" name="who" value="{esc(u["username"])}"> {esc(u["username"])}'
                    f'{" <span class=meta>(agent)</span>" if u["kind"] == "agent" else ""}</label>' for u in people)
    body = f"""<h1>Chat</h1>
<p class="meta">For questions and planning that aren't about one ticket. Anything about a ticket goes on the ticket.
Agents are told about new messages with every ticket update, and answer them first.</p>
<details class="box" style="margin-bottom:14px"{" open" if not rows else ""}><summary><b>Start a conversation</b></summary>
<form class="stack" id="sf" style="margin-top:10px">
<div class="who-pick" role="group" aria-label="With">{picks or '<span class="meta">No one else yet.</span>'}</div>
<label>Title <span class="meta">(optional; for a group or a topic)</span><input type="text" id="st"></label>
<label>Message<textarea id="sb" placeholder="What's on your mind? (Enter starts it, Shift+Enter for a new line)"></textarea></label>
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
      const d = await r.json(), html = d.html, last = d.last;
      document.getElementById('receipt').innerHTML = d.receipt;
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
  const r = await fetch('/api/chat/' + CID + '/attachments?' + q, {method: 'POST',
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
    $('mbody').value = ''; $('mfiles').value = ''; fit($('mbody'));
  } catch (err) { fail(err); }
  busy = false; btn.disabled = false; btn.textContent = 'Send';
  await poll(true);
};
enterSends('mbody', 'mf');
async function deleteChat(){
  if (!confirm("Delete this whole chat, with all its messages and files? This can't be undone.")) return;
  try { await api('DELETE', '/api/chat/' + CID); location.href = '/chat'; } catch (err) { fail(err); }
}
const am = $('am');
if (am) am.onsubmit = async (e) => { e.preventDefault();
  try { await api('POST', '/api/chat/' + CID + '/members', {username: $('amwho').value}); location.reload(); }
  catch (err) { fail(err); } };
"""


def render_chat(store, cid, viewer):
    c = store.conversation(cid, viewer)
    msgs = store.messages(c["id"], limit=PAGE, internal=True)
    store.mark_read(c["id"], viewer)
    earlier = (f'<div class="earlier"><button type="button" class="more" data-list="msgs" data-url="/chat/{c["id"]}/before/" '
               f'data-before="{msgs[0]["id"]}">Show earlier messages</button></div>'
               if msgs and store.older_messages(c["id"], msgs[0]["id"]) else "")
    last = msgs[-1]["id"] if msgs else 0
    others = [u for u in store.users() if not u["disabled"] and u["kind"] != "guest"
              and u["username"].lower() not in {m.lower() for m in c["members"]}]
    add = ""
    if others:
        opts = "".join(f'<option value="{esc(u["username"])}">{esc(u["username"])}</option>' for u in others)
        add = (f'<form class="row" id="am" style="margin-top:6px"><select id="amwho" aria-label="Add someone" '
               f'style="width:auto">{opts}</select><button>Add to conversation</button></form>')
    shown = "".join(render_message(store, m, viewer) for m in msgs)
    body = f"""<p class="meta" style="margin:0 0 4px"><a href="/chat">← Chat</a></p>
<div class="row" style="justify-content:space-between;align-items:baseline"><h1 style="margin-bottom:4px">{esc(c["name"])}</h1>
<button type="button" class="cancel" onclick="deleteChat()">Delete chat</button></div>
<div class="meta">With {esc(", ".join(c["members"]) or "no one yet")}. Anyone can read and post here.</div>{add}
<div id="chatroom" data-cid="{c["id"]}" data-last="{last}" style="margin-top:14px">{earlier}
<div class="chat" id="msgs">{shown or '<p class="empty" id="none">No messages yet. Say hello.</p>'}</div>
<p class="meta receipt" id="receipt">{receipt_html(store, c["id"])}</p>
<form class="stack composer" id="mf"><textarea id="mbody" placeholder="Message (Enter sends, Shift+Enter for a new line)" aria-label="Message"></textarea>
<div class="row"><input type="file" id="mfiles" multiple aria-label="Attach files">
<button class="primary" id="msend" style="margin-left:auto">Send</button></div></form></div>
<script>{CHAT_JS}</script>"""
    return page(c["name"], body, "/chat", len(store.tickets(["review"])))


BOLD = re.compile(r"\*\*([^*\n]+?)\*\*")


def wiki_inline(store, text):
    """One run of text: escaped, with `code`, **bold**, links, ticket ids and [[wiki/links]]."""
    out = []
    for i, part in enumerate(re.split(r"(`[^`\n]+`)", text)):
        if i % 2:
            out.append(f"<code>{esc(part[1:-1])}</code>")
        else:
            out.append(BOLD.sub(r"<b>\1</b>", chat_text(store, part)))
    return "".join(out)


def code_block(lines):
    """A code block: monospace, wrapped, with a Copy button that copies the original text."""
    return f'<div class="code"><button type="button" class="copy" aria-label="Copy">Copy</button><pre>{esc(chr(10).join(lines))}</pre></div>'


def wiki_html(store, text):
    """Text as people and agents write it: line breaks and indentation kept, plus a little markdown:
    # headings, ``` code blocks (monospace, spacing exact), `code`, **bold**, links. Used for the wiki,
    ticket descriptions, comments and chat messages."""
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
                out.append(code_block(code))
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
        out.append(code_block(code))
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
    const p = WPATH ? await api('PATCH', '/api/wiki/' + WPATH, data) : await api('POST', '/api/wiki', data);
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

1. `GET /api/me`: who you are, your humans' current guidance (follow it; it changes), your chat inbox count.
2. Chat to answer? Do it before anything else: `GET /api/chat/inbox`, then reply in each conversation:
   `POST /api/chat/ID/messages` `{{"body": "..."}}`. A human's message keeps notifying you until you reply
   (reading isn't enough; if nothing needs saying, reply briefly). Other agents' messages only need reading
   (`POST /api/chat/ID/read`). Any response with a `"chat"` field means the same: stop, answer chat, carry on.
3. `POST /api/next` takes the next ready ticket (yours first). `{{"ticket": null}}`: nothing to do. Only take new
   work when you've none in progress (its reply lists any under `in_progress`).
   The ticket you get is now in progress and assigned to you: that means you work on it, now.
4. Read the whole ticket (`GET /api/tickets/ID`): description, every comment, checklist, and every page
   in its `wiki_links` (`GET /api/wiki/PATH`).
5. Unfamiliar project? Check the wiki first: `GET /api/wiki` (the tree), `GET /api/wiki?q=a+few+words`.

## Working a ticket

- A ticket that's in progress and assigned to you is yours to work: do it, and don't stop because it's
  "already in progress" (you put it there). Only other people's in-progress tickets are comment-only. Each
  ticket from the API has a `what_now` line saying what it means for you; follow it.
- Comment as you go: `POST /api/tickets/ID/comments` `{{"body": "..."}}`.
- A human's comment on your ticket waits for your reply: `/api/wait` returns it (reason `comments`) and the
  ticket shows `new_comments`, until you comment on that ticket. Read it, act on it, reply (even briefly).
- Files: one folder per ticket, `~/work/tickets/TICKET-ID/` (checklist, notes, scratch, logs, screenshots), never
  loose in your home folder; code changes in the project's repo on a `ticket/TICKET-ID` branch. Name both in your
  start comment; leave other tickets' folders alone; tidy up once a ticket is closed as done.
- Checklist: keep it as `checklist.md` in the ticket's folder ("- [ ] step"); after each step upload the whole
  file: `PUT /api/tickets/ID/checklist` (body: the file's text, `Content-Type: text/markdown`). Each upload
  replaces the last; your file is the source. Only while the ticket is yours and in progress.
- Files: `curl -F file=@notes.md -F comment_id=N "$URL/api/tickets/ID/attachments"`, or the raw bytes with the
  name in the URL: `curl --data-binary @notes.md "$URL/api/tickets/ID/attachments?filename=notes.md&comment_id=N"`.
  Always give the real file name (no name: refused). Chat attachments: the same, at `/api/chat/ID/attachments`
  with `message_id`.
- Hand back: a comment starting "Done" or "Needs a decision", then `PATCH /api/tickets/ID` `{{"status": "review"}}`.
- You may set ready, in_progress and review; never backlog or done. Backlog, done and other people's
  tickets: comment only. Blocked tickets (predecessors not done) can't start; comment instead. A ticket's
  `"blocked"` says whether it is; `depends_on` lists only predecessors that still block (done ones are in
  `done_dependencies` and don't block).
- New ideas: `POST /api/tickets` `{{"title", "description"}}` makes a proposal a human accepts or declines.

## When you're idle

After every action (a step, a hand-back, a chat reply), call `GET /api/wait?timeout=50` and do what it
returns. Never end a turn with nothing in hand: keep it running in the background so it wakes you (a
background command your harness reports when it finishes, a hook before you end a turn), plus a recurring
check as a safety net (cron, a scheduled task, your harness's scheduler) that calls
`GET /api/wait?timeout=1` every few minutes and wakes you if the reason isn't `timeout`. It tells you what to do next, at once if there's anything: unread chat, replies on your tickets,
then your tickets in the order to do them (highest priority first; started and nearest-done first within a
priority). Work that list top down, finishing what you've started before new work. Do it, then call it again. Don't park work in
in progress: if you're stuck on a decision, move it to review with what you need. Write down here how you set
up the listener for your harness.

## Writing

Descriptions, comments and chat show your line breaks plus a little markdown: `#` headings, ``` code
blocks (monospace), `code`, `**bold**`, links, ticket ids and `[[wiki/page]]` links. Write to be scanned on a
phone: short paragraphs with blank lines, headings for long write-ups, lists for steps, code blocks for
commands and output. No walls of text.

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

(Users are humans, agents, or guests: guests can read everything and change
nothing.)

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
- **in_progress**: its assignee is working on it right now. **If it's
  assigned to you, that's you: do the work, and comment on the ticket as
  you go.** Claiming a ticket (`POST /api/next`) or starting it puts it
  here, assigned to you; that's the signal to begin, not a sign that it's
  taken. Only someone *else's* in-progress ticket is hands-off (comment
  only). Check `assignee` against your username (`GET /api/me`).
- **review**: the humans' turn. Move a ticket here when it's finished, or
  when you need a decision before you can go on.
- **done**: a human closed it as done (the work is finished). Only humans
  set this. A human agreeing with your idea or answering your question
  sends it back to in_progress instead: read their comment and carry on.

Tickets can be assigned to one user (an agent or a human). Work on tickets
assigned to you; you may also take unassigned ready ones.

**Never stop because a ticket is "already in progress".** If it's assigned
to you, you put it there (or a human gave it to you): it's yours to work,
now. Every ticket the API returns to you also has `"what_now"`, one line
saying what it means for you (for example "This ticket is yours and in
progress: you are working on it now...", or "agent-2 is working on this:
you can only comment"). When in doubt, follow it.

**Epics** group the tickets of one plan (`"kind": "epic"`). They aren't
worked directly: you'll never get one from /api/next, and you can only
comment on them. A ticket belongs to at most one epic (`epic_id`). An
epic's state follows its tickets and is set automatically (`"epic_state"`):
backlog (none made ready yet), active (some ready or under way), complete
(all done; declined tickets don't count).

**Dependencies**: a ticket can list tickets that must be done first.
Until they're done (approved, declined or cancelled), it's **blocked**: it
can't move to in_progress, review or done (you get a 409 naming the
blockers), and /api/next skips it. You can still comment on it. **A
predecessor that's done no longer blocks anything.** In what the API gives
you: `"blocked"` is true or false; `depends_on` and `blocked_by` list only
the predecessors that still block; finished ones are moved to
`done_dependencies`. If `blocked` is false, nothing is in the way.

**Proposals**: tickets you create are proposals. They wait in the backlog,
marked proposed, until a human accepts (to the backlog or straight to
ready) or declines them (they move to done, marked declined).

**Cancelled**: a human can close any ticket out of the normal cycle
(overtaken by events, a duplicate...). It moves to done with
`"cancelled": 1`, usually with a comment saying why. If it was yours, stop
working on it; `/api/wait` tells you ("cancelled it: stop work on it").

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

1. `POST /api/next`: takes your next ready ticket (ones assigned
   to you first, then unassigned ones; high priority first, then oldest),
   moves it to in_progress and assigns it to you in the same step, so two
   agents never get the same one. **The ticket you get back is yours, in
   progress: you're working on it from this moment.** Start the work; don't
   look for another ticket or wait for permission. `{"ticket": null}`
   means there's nothing to do. The response also has `"guidance"`: the current house rules for
   you. Read it every time; humans change it.
2. Read the ticket: `GET /api/tickets/ID` has the description, the latest
   comments (humans' replies included: they're instructions; page back if
   you need older ones, see "Long threads"), the recent history and the
   checklist.
3. Work, and **comment on the ticket as you go**. Comments are the only way
   the humans know what happened; a ticket without them looks abandoned.
   The minimum: a comment when you start (what you're about to do), one
   after each step (what you did, what happened, including errors and
   surprises), and a hand-off. Attach evidence (screenshots, short
   recordings, logs). The server holds you to it: you can't hand a ticket
   back (review) or release it (ready) without having commented since you
   started it, your own in-progress tickets carry a note in `what_now` (and
   in `/api/wait`) once you've gone 20 minutes without a comment, and a
   checklist upload with no comment since the last one comes back with a
   `"reminder"`.
4. **Keep your files tidy: one folder per ticket.** Put everything you make
   for a ticket (checklist, notes, scratch files, downloads, logs,
   screenshots) in `~/work/tickets/TICKET-ID/` (e.g.
   `~/work/tickets/amber-otter/`), never loose in your home folder. Code
   changes go in the project's own repo, on a branch named
   `ticket/TICKET-ID`, not as copies in the ticket folder. Name the folder
   and branch in your start comment, so you (or whoever comes next) can
   find them. Leave other tickets' folders alone; once a ticket is closed
   as done, clear out what isn't worth keeping.
5. Keep a markdown checklist in that folder (`checklist.md`; "- [ ] step" / "- [x] step")
   and upload the whole file after each step: `PUT /api/tickets/ID/checklist`
   with its text as the body. Each upload replaces the last one; it's a
   report for the humans (they see it with a progress bar), and your file
   stays the source. Only you can upload it, while the ticket is assigned
   to you and in progress.
6. Hand back: post a comment, then `PATCH` the status to review. A good
   hand-off comment says:
   - **Done** or **Needs a decision** (first line, so it's clear which).
   - What you did, and where (files, branches, commits).
   - How to check it (what to open, run or look at; attach a screenshot
     or recording).
   - For a decision: the question, the options, and what you'd recommend.
7. Stopping before you're finished: comment where you got to and what's
   next. If you're blocked on a decision or on someone else, move it to
   review saying what you need; if someone else should take it, release
   it (`ready`). Leave it in_progress only if you're coming back to it:
   `/api/wait` keeps handing your in-progress tickets back to you.
8. After review a human may move it back to in_progress or ready with a
   comment saying what to change. Read their comment first.
9. **A human's comment on your ticket waits for your reply.** Whenever a
   human comments on a ticket assigned to you (not done), it stays on your
   list until you comment on that ticket: `/api/wait` returns it (reason
   `comments`) right after chat, the ticket shows `"new_comments": N`, and
   its `what_now` starts with who left them. Read them, act on them, and
   reply, even briefly ("Got it, doing that next"). Other agents' comments
   don't need a reply.

## Chat

Conversations between any users, humans and agents; anyone can read and
post in any of them. The members of a conversation are the people who are
told about its new messages.

**Answer chat before you carry on.** While you owe chat an answer, every
ticket response (`/api/next`, `GET`/`PATCH` a ticket, comments, uploads)
includes a `"chat"` field (`{"count": 2, "conversations": [...], "notice":
"..."}`), every API response has an `X-Chat-Inbox` header, and
`/api/wait` returns `"reason": "chat"`. When you see it:
`GET /api/chat/inbox`, reply in each conversation, then go back to your
ticket.

**A message from a human stays on that list until you reply in its
conversation.** Reading it isn't enough, and marking it read doesn't
clear it. If nothing needs saying, reply briefly anyway ("Got it",
"Will do after this ticket"), so they know you saw it. Messages from other
agents, and notes like "(added agent-2)", only need reading: mark them
read (`POST /api/chat/ID/read`) or reply if you have something to say.
That way two agents never owe each other replies forever.

Keep chat for things that aren't about one ticket. Work on a ticket is
discussed in comments on the ticket, where the humans review it.

## Staying reachable when you're idle

Humans expect an answer to chat, and to their replies on your tickets,
even when you're between tasks. This server can't reach into your
session: **you set up your own listener, once, with whatever your harness
offers, and write down how in your skill.**

- `GET /api/wait?timeout=50` tells you what to do next. If there's
  anything, it returns at once, the first of these that applies:
  1. `{"reason": "chat", "chat": {...}}`: there's chat in your inbox. Answer it
     (or mark it read).
  2. `{"reason": "comments", "tickets": [{"id", "title", "status", "new_comments", "from", "why"}]}`:
     a human commented on one of your tickets and you haven't replied there
     yet. Read the new comments (`GET /api/tickets/ID`) and reply on the
     ticket. They stay on this list until you do, even a short reply.
  3. `{"reason": "tickets", "tickets": [{"id", "title", "status", "why"}]}`:
     while you were waiting, someone else commented on or changed a ticket
     assigned to you, or assigned you one. Read it and act on it.
  4. `{"reason": "in_progress" or "ready", "tickets": [...]}`: your work,
     in the order to do it: your in-progress and ready tickets, **highest
     priority first; at the same priority, ones you've started before ones
     you haven't, then the nearest to done** (by checklist, `"progress":
     "3/5"`), then the oldest. The reason is the status of the first.
     Work the list top down: continue the first if it's in progress, or
     start it if it's ready. Finish what you've started before taking new
     work.
  5. `{"reason": "queue", "tickets": [...]}`: only with `&queue=1`:
     unassigned ready tickets. Take one with `POST /api/next`.
  Only when none of these applies does it wait, and it returns the moment
  one does, or `{"reason": "timeout"}` when time is up (then call it
  again). Blocked tickets never count. The longest timeout is 300 seconds;
  keep it under 100 if a proxy sits in front of this server (many cut long
  requests), and loop.
- Because of 1, 2 and 4, it returns straight away until you've dealt with
  them: answer chat, and finish your in-progress tickets or hand them
  back. **Don't park work in in_progress:** if you're stuck waiting on a
  decision or on someone else, move the ticket to review with a comment
  saying what you need, or release it to ready. Otherwise wait will keep
  handing it back to you (which is the point).
**The loop: after every action, call it.** Whenever you finish something
(a step of a ticket, a hand-back, a chat reply, a ticket you proposed),
your next move is `GET /api/wait`, and you do what it returns. Never end
a turn or a session with nothing in hand: either you're working on what
wait gave you, or wait is running in the background so that its
returning wakes you.

**Set up two things once, and write down how in your skill:**

1. *A listener:* run wait in the background whenever you'd otherwise sit
   idle, so its returning wakes you: for example a background command your
   harness tells you about when it finishes, or a hook that runs before you
   end a turn and calls it.
2. *A recurring check, as a safety net:* something on a timer (a cron job,
   a scheduled task, your harness's scheduler or loop feature) that every
   few minutes calls `GET /api/wait?timeout=1` and, if the reason isn't
   `timeout`, wakes you or starts a session to deal with it. That way a
   listener that died, or a session that ended, can't leave you deaf.

Use whatever your harness and machine support; there's no one way. If you
can do neither, tell a human in chat.
- When it returns: do what it says (chat first, then your tickets), then
  call it again. You can also call it any time as "what should I do next?".
- If your harness can't run anything while you're idle, tell a human in
  chat, so they know to nudge you when they write.

Humans see read receipts in chat ("Not read yet by agent-1"), so they can
tell when you're not listening.

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

## Long threads

To keep your context small, a ticket comes with its **latest 20
comments** (and the files and history from that span), and a conversation
with its latest 20 messages; `comments_total` / `comments_more` and
`messages_more` say whether there's more. That's usually all you need: the
description, the latest comments, the checklist and `what_now`. Page back
(`GET /api/tickets/ID/comments?before=ID`, `GET /api/chat/ID/messages?before=ID`)
only when you need something older, a page at a time, and stop when you
have it.

## Formatting what you write

Ticket descriptions, comments, chat messages and wiki pages are shown
with your line breaks and indentation kept, plus a little markdown:

- `# Heading`, `## Heading`, `### Heading` on a line of their own
- code blocks between lines of three backticks, shown in a monospace
  font with spacing exact (commands, output, tables, diffs)
- `` `inline code` `` and `**bold**`
- links: web addresses, ticket ids (`amber-otter`) and wiki pages
  (`[[section/page]]`, or `[[section/page|your words]]`)

Lists, tables in pipes and other markdown show as you typed them (which
reads fine). Humans often read on a phone, so write to be scanned: short
paragraphs separated by blank lines, a heading for each part of a long
write-up, a list for steps, and code blocks for anything that should line
up. Don't send a wall of text.

## Endpoints

All JSON in and out, except file uploads (raw bytes) and this page.
Ticket ids are two words, like `amber-otter` (older tickets have four or five
letters, like `KAMI`). Any case works, and a space for the hyphen in JSON.

Conventions: `GET` never changes anything. `POST` creates something or
does an action. `PATCH` changes the fields you send. `PUT` replaces a
whole thing (your checklist, the house guidance). Fields a request doesn't
take are refused (400, listing the ones it does), so a typo never passes
silently. Errors are `{"error": "..."}`.

Start here

    GET    /api                        this briefing (no token needed); GET /api/skill: a SKILL.md for it
    GET    /api/me                     {"username", "kind", "guidance", "chat_inbox", "comments_to_answer", "prefs"}
    GET    /api/wait?timeout=50        what to do now: chat, replies, your work in order (see above)
    POST   /api/next                   take a new ticket: {"ticket": ... or null, "guidance", "in_progress"
                                       (any you've already started: finish those first), "note"}

Tickets

    GET    /api/tickets                {"tickets": [...]}; filters ?status=ready,in_progress ?q=text
                                       ?assignee=me|NAME|none ?epic=ID ?kind=ticket|epic ?proposed=1
    GET    /api/tickets/ID             one ticket: the latest 20 comments (comments_total, comments_more),
                                       the attachments and events from the same span, checklist, wiki_links,
                                       blocked, depends_on, done_dependencies, new_comments, what_now
    GET    /api/tickets/ID/comments    ?before=COMMENT_ID[&limit=N]: the comments before that one (20 by
                                       default), with their attachments: {"comments", "attachments", "more"}
    POST   /api/tickets                {"title", "description", "priority": 1|2|3, "kind": "ticket"|"epic",
                                        "epic_id", "depends_on": ["amber-otter", ...]} -> a proposal
    PATCH  /api/tickets/ID             any of {"status", "title", "description", "priority", "assignee", "epic_id"}
    POST   /api/tickets/ID/comments    {"body": "..."} -> the comment, with its "id"
    POST   /api/tickets/ID/attachments a file (see Files below); comment_id attaches it to a comment
    PUT    /api/tickets/ID/checklist   your checklist's markdown as the body; replaces the last upload
    POST   /api/tickets/ID/deps        {"depends_on": "amber-otter"}: that one must be done first
    DELETE /api/tickets/ID/deps/OTHER  drop that dependency

Chat

    GET    /api/chat/inbox             what's waiting for you: {"count": N, "conversations": [{...,
                                       "needs_reply", "messages": [...]}]}
    GET    /api/chat                   {"conversations": [{"id", "name", "members", "unread", "last_message"}]}
    POST   /api/chat                   {"members": ["frank"], "title" (optional), "body" (optional first message)}
                                       -> the conversation (one person and no title: your existing one)
    GET    /api/chat/ID                the conversation, its latest 20 messages (messages_more) and "read_up_to"
    GET    /api/chat/ID/messages       ?before=MESSAGE_ID[&limit=N]: the page before that one; ?after=ID: all newer
                                       ones: {"messages", "more"}
    POST   /api/chat/ID/messages       {"body": "..."} -> the message, with its "id"
    POST   /api/chat/ID/attachments    a file for a message (as for tickets, with message_id)
    POST   /api/chat/ID/read           mark it read (enough for agents' messages; humans' need a reply)
    POST   /api/chat/ID/members        {"username": "agent-2"}: bring someone in

Wiki (read-only for agents)

    GET    /api/wiki                   {"tree": [{"title", "path", "children": [...]}]};
                                       ?q=some words: {"results": [{"title", "path", "snippet"}]}
    GET    /api/wiki/SECTION/PAGE      {"title", "body", "path", "breadcrumbs", "children", "updated_at", ...}

You personally

    GET    /api/guidance               the house rules plus any notes for you
    GET    /api/me/prefs, PATCH ...    your own settings, e.g. {"tts": true}

Humans only (for completeness): `POST /api/tickets/ID/accept` (`{"ready",
"assignee", "priority"}`) and `/decline` for proposals, `POST
/api/tickets/ID/cancel`, `DELETE /api/chat/ID`, writing the wiki
(`POST /api/wiki`, `PATCH`/`DELETE /api/wiki/PATH`), users
(`/api/users`), `PUT /api/guidance` and `GET /api/export`.

Priorities: 1 high, 2 normal, 3 low.

**Files**: attach anything: screenshots, recordings, audio, but also
tarballs of build output, scripts, patches, markdown write-ups, logs.
Every file needs a real name (its extension sets the type). Two ways to
send one, both fine:

- raw bytes, named in the URL:
  `curl --data-binary @notes.md "$URL/api/tickets/ID/attachments?filename=notes.md&comment_id=12"`
  (or name it with an `X-Filename: notes.md` header);
- a form, the way browsers upload:
  `curl -F file=@notes.md -F comment_id=12 "$URL/api/tickets/ID/attachments"`.

A raw upload without a name is refused (400) with these instructions.
The response has the stored `filename` and its `url`; check it. Images, audio and video play on the ticket
page; everything else is a download, and small text files (scripts,
markdown, logs) also get a preview there. Download any attachment with
`GET` on its `url` (same token). Files are kept on the server with the
ticket and included in backups. Uploads up to the server's limit (1 GB by
default). Prefer one tarball over dozens of loose files.

A ticket looks like:

    {"id": "amber-otter", "title": "...", "description": "...", "status": "in_progress", "assignee": "agent-1",
     "what_now": "This ticket is yours and in progress: you are working on it now. ...",
     "kind": "ticket", "epic_id": "quiet-falcon", "epic_title": "...", "proposed": 0, "declined": 0, "cancelled": 0,
     "blocked": false, "blocked_by": [], "depends_on": [],
     "done_dependencies": [{"id": "stony-brook", "title": "...", "status": "done"}], "unblocks": [...],
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
    curl -s -X POST -H "Authorization: Bearer $TOKEN" "$URL/api/next"
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
    unread = (sum(c["count"] for c in store.chat_attention(actor)) if role == "agent"
              else store.unread_total(actor))
    lines = [f"\n## Right now\n", f"You're signed in as **{actor}** ({role})."]
    if unread:
        lines.append(f"**You have {unread} chat message{'s' if unread != 1 else ''} to answer. Reply first:** "
                     "`GET /api/chat/inbox`.")
    owed = store.comments_owed(actor)
    if owed:
        lines.append("**Comments from humans waiting for your reply** (reply on each ticket):")
        lines += [f"- {o['id']}: {o['title']} ({o['why'].split(':')[0]})" for o in owed]
    lines.append(f"Tickets: {counts.get('ready', 0)} ready, {counts.get('in_progress', 0)} in progress, "
                 f"{counts.get('review', 0)} waiting for review, {counts.get('backlog', 0)} in the backlog.")
    if mine:
        lines.append("You have these in progress (start here):")
        lines += [f"- {t['id']}: {t['title']}" for t in mine]
    if queue:
        lines.append("Assigned to you and ready:")
        lines += [f"- {t['id']}: {t['title']}" for t in queue]
    if not mine and not queue and role == "agent":
        lines.append("Nothing is assigned to you. `POST /api/next` takes the next unassigned ready ticket.")
    sections = store.wiki_tree()
    if sections:
        lines.append("Wiki sections: " + ", ".join(f"{n['title']} (`{n['path']}`)" for n in sections) + ".")
    guidance = store.guidance_for(actor)
    head = API_DOCS.split("\n## Signing in", 1)
    return (head[0] + "\n## Guidance from your humans\n\nFollow this; it overrides the general advice below.\n\n"
            + guidance + "\n\n## Signing in" + head[1] + "\n".join(lines) + "\n")


# --------------------------------------------------------------------------- HTTP

def what_now(t, me):
    """One line telling an agent what a ticket means for it, right now."""
    mine = bool(t.get("assignee")) and t["assignee"].lower() == me.lower()
    who, st = t.get("assignee"), t["status"]
    blocked = t.get("blocked_by") or []
    if t.get("kind") == "epic":
        return "An epic (a plan that groups tickets): it isn't worked directly. Work its tickets; you can comment here."
    if t.get("proposed"):
        return "A proposal waiting for a human to accept or decline it: comment only for now."
    if st == "done":
        if t.get("cancelled"):
            return "Cancelled by a human: stop any work on it. You can still comment."
        return "Closed as done (or declined) by a human: nothing to do. You can still comment."
    if st == "review":
        return ("You handed this back; it's waiting for a human. Don't keep working on it unless they move it back "
                "to in progress." if mine else "Waiting for a human's review: comment only.")
    if st == "in_progress":
        if mine:
            return ("This ticket is yours and in progress: you are working on it now. Do the work, comment on the "
                    "ticket as you go, upload your checklist after each step, and hand back with a comment and "
                    "status review (or release it to ready if someone else should take it).")
        if who:
            return f"{who} is working on this: you can only comment."
        return "In progress with no one assigned: you can take it by assigning it to yourself, then work on it."
    if blocked:
        return f"Blocked until {', '.join(blocked)} {'is' if len(blocked) == 1 else 'are'} done: it can't start yet. Comment if you have ideas."
    if st == "ready":
        if mine:
            return "Assigned to you and ready: start it (PATCH status in_progress, or POST /api/next), then work on it."
        if who:
            return f"Ready, but assigned to {who}: leave it for them; you can comment."
        return "Ready for anyone: start it (PATCH status in_progress) to take it, then work on it."
    return "In the backlog: not cleared for work yet. Don't work on it; you can comment."


# What each request body may contain.
TICKET_NEW = ("title", "description", "status", "priority", "assignee", "kind", "epic_id", "depends_on")
TICKET_EDIT = ("title", "description", "status", "priority", "assignee", "epic_id")
WIKI_FIELDS = ("title", "body", "parent", "slug")


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
        if role == "agent" and code < 400 and isinstance(obj, dict):
            # Every ticket an agent gets says, in one line, what it means for that agent.
            for t in [obj, obj.get("ticket")] + list(obj.get("tickets") or []) + list(obj.get("in_progress") or []):
                if isinstance(t, dict) and "status" in t and "id" in t and "kind" in t:
                    t["what_now"] = what_now(t, actor)
                    mine = (t.get("assignee") or "").lower() == actor.lower()
                    if mine and t["status"] != "done" and t["kind"] == "ticket":
                        owed = self.server.store.comments_owed(actor, t["id"])
                        t["new_comments"] = owed[0]["new_comments"] if owed else 0
                        if owed:
                            o = owed[0]
                            t["what_now"] = (f"{', '.join(o['from'])} left {o['new_comments']} new comment"
                                             f"{'s' if o['new_comments'] != 1 else ''} here: read "
                                             f"{'them' if o['new_comments'] != 1 else 'it'} and reply on this ticket "
                                             f"first. Then: " + t["what_now"])
                    if mine and t["status"] == "in_progress" and t["kind"] == "ticket":
                        nudge = self.server.store.comment_nudge(t["id"], actor)
                        if nudge:
                            t["what_now"] += " Note: " + nudge + "."
        if role == "agent" and code < 400:
            # Agents are told about unread chat on every response, and asked to answer it first.
            notice = self.server.store.chat_notice(actor)
            if notice:
                extra["X-Chat-Inbox"] = str(notice["count"])
                if isinstance(obj, dict) and not getattr(self, "req_path", "").startswith("/api/chat"):
                    obj = {**obj, "chat": notice}
        self.send(code, json.dumps(obj, default=str), "application/json; charset=utf-8", extra)
        if self.command not in ("GET", "HEAD") and code < 400:
            self.server.store.bump()  # wake anyone waiting in /api/wait

    def html(self, body):
        role, actor = getattr(self, "who_is", (None, None))
        n = self.server.store.unread_total(actor) if actor else 0
        body = body.replace(CHAT_BADGE, f"<span class=n>{n}</span>" if n else "", 1)
        if actor and self.server.store.prefs(actor)["tts"]:
            body = body.replace('<html lang="en">', '<html lang="en" data-tts="1">', 1)
        if role == "guest":
            body = body.replace('<html lang="en"', '<html lang="en" data-role="guest"', 1).replace(
                "<main>", '<main><p class="banner prop guest-note">Read-only guest: you can look at everything, but not '
                          'change anything.</p>', 1)
        self.send(200, body, "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    UPLOAD_HELP = ('send the raw bytes as the body with the name in the URL, e.g. '
                   'curl --data-binary @report.pdf "URL?filename=report.pdf" (or an X-Filename header), '
                   'or send it as a form: curl -F file=@report.pdf URL')
    FORM_MAX = 100 << 20

    def read_upload(self, query):
        """(filename, mime, size, stream, form fields) for an upload. Either the raw bytes, named by
        ?filename=, an X-Filename header or a Content-Disposition header; or a multipart form (curl -F)
        with one file in it, plus optional fields such as comment_id or message_id."""
        size = int(self.headers.get("Content-Length") or -1)
        if size < 0:
            raise ApiError(411, "send the file with a Content-Length")
        ctype = self.headers.get("Content-Type") or ""
        if ctype.lower().startswith("multipart/form-data"):
            if size > self.FORM_MAX:
                raise ApiError(413, "forms are limited to 100 MB; send bigger files as raw bytes: "
                                    "curl --data-binary @big.tar.gz \"URL?filename=big.tar.gz\"")
            if size > self.server.max_upload:
                raise ApiError(413, f"file is larger than the {self.server.max_upload // (1 << 20)} MB limit")
            data = self.rfile.read(size)
            msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                b"Content-Type: " + ctype.encode("latin-1", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n" + data)
            found, fields = None, {}
            for part in (msg.iter_parts() if msg.is_multipart() else []):
                name, blob = part.get_filename(), part.get_payload(decode=True) or b""
                if name and found is None:
                    found = (name, part.get_content_type(), blob)
                elif part.get_param("name", header="content-disposition"):
                    fields[part.get_param("name", header="content-disposition")] = blob.decode("utf-8", "replace").strip()
            if not found:
                raise ApiError(400, "that form has no file in it: " + self.UPLOAD_HELP)
            name, mime, blob = found
            return name, mime, len(blob), io.BytesIO(blob), fields
        name = (query.get("filename") or [""])[0] or self.headers.get("X-Filename") or ""
        if not name and self.headers.get("Content-Disposition"):
            m = email.message.Message()
            m["Content-Disposition"] = self.headers["Content-Disposition"]
            name = m.get_filename() or ""
        if not name.strip():
            raise ApiError(400, "no file name: " + self.UPLOAD_HELP)
        return name, ctype, size, self.rfile, {}

    def body_json(self, allowed=None):
        """The request's JSON object. With `allowed`, any other field is refused (a typo shouldn't be ignored)."""
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
        if allowed is not None:
            extra = sorted(set(data) - set(allowed))
            if extra:
                raise ApiError(400, f"unknown field{'s' if len(extra) > 1 else ''} {', '.join(extra)}; "
                                    + (f"this takes: {', '.join(allowed)}" if allowed else "this takes no fields"))
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
            # Guests read; the only thing they can change is their own display settings.
            if role == "guest" and (method not in ("GET", "HEAD") and path != "/api/me/prefs"
                                    or path in ("/new", "/wiki-new")):
                raise ApiError(403, "guests can read but not change anything")
            # Changes made with the cookie must come from our own pages.
            if method != "GET" and not bearer and self.headers.get("X-Tickets") != "1":
                raise ApiError(403, "missing X-Tickets header")

            m = re.fullmatch(rf"/api/tickets/({ID_PATH})(/comments|/attachments|/checklist|/deps|/accept|/decline|/cancel)?(?:/({ID_PATH}))?",
                             path)
            if method == "GET" and path == "/":
                return self.html(render_dashboard(store))
            if method == "GET" and path in ("/backlog", "/done"):
                return self.html(render_list(store, path[1:], (query.get("q") or [""])[0].strip()))
            if method == "GET" and path == "/new":
                mid = (query.get("from_message") or [""])[0]
                return self.html(render_new(store, (query.get("epic") or [None])[0],
                                            ticket_from_message(store, int(mid)) if mid.isdigit() else ("", "")))
            if method == "GET" and (tm := re.fullmatch(rf"/t/({ID_PATH})/live", path)):
                start = (query.get("from") or ["0"])[0]
                return self.json(200, ticket_live(store, tm.group(1), float(start) if re.fullmatch(r"[0-9.]+", start) else 0))
            if method == "GET" and (tm := re.fullmatch(rf"/t/({ID_PATH})/earlier", path)):
                before = (query.get("before") or [""])[0]
                return self.json(200, ticket_earlier(store, tm.group(1), int(before) if before.isdigit() else 0))
            if method == "GET" and (cm := re.fullmatch(r"/chat/(\d+)/before/(\d+)", path)):
                cid = store.conversation(int(cm.group(1)))["id"]
                msgs = store.messages(cid, limit=PAGE, before=int(cm.group(2)), internal=True)
                more = bool(msgs) and store.older_messages(cid, msgs[0]["id"])
                return self.json(200, {"html": "".join(render_message(store, m, actor) for m in msgs),
                                       "before": msgs[0]["id"] if more else None, "label": "Show earlier messages"})
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
                # The chat page polls this for new messages (as HTML) and the read receipt; it marks them read.
                cid = store.conversation(int(cm.group(1)))["id"]
                msgs = store.messages(cid, after=int(cm.group(2)), internal=True)
                if msgs:
                    store.mark_read(cid, actor, msgs[-1]["id"])
                return self.json(200, {"html": "".join(render_message(store, m, actor) for m in msgs),
                                       "last": msgs[-1]["id"] if msgs else 0, "receipt": receipt_html(store, cid)})
            if method == "GET" and path == "/api/wait":
                t = (query.get("timeout") or ["50"])[0]
                timeout = max(1, min(300, int(t) if t.isdigit() else 50))
                queue = (query.get("queue") or [""])[0] in ("1", "true", "yes")
                return self.json(200, store.wait_for(actor, timeout, queue))
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
                    return self.json(201, store.wiki_create(self.body_json(WIKI_FIELDS), actor, role))
                if method == "PATCH" and sub:
                    return self.json(200, store.wiki_update(sub, self.body_json(WIKI_FIELDS), actor, role))
                if method == "DELETE" and sub:
                    return self.json(200, store.wiki_delete(sub, actor, role))
            um = re.fullmatch(r"/api/users(?:/([^/]+))?(/token)?", path)
            if method == "GET" and path == "/users":
                if role != "human":
                    raise ApiError(403, "only humans manage users")
                return self.html(render_users(store, actor))
            if method == "GET" and path == "/api/me":
                return self.json(200, {"username": actor, "kind": role, "guidance": store.guidance_for(actor),
                                       "chat_inbox": (sum(c["count"] for c in store.chat_attention(actor))
                                                       if role == "agent" else store.unread_total(actor)),
                                       "comments_to_answer": sum(o["new_comments"] for o in store.comments_owed(actor)),
                                       "prefs": store.prefs(actor)})
            if path == "/api/me/prefs":
                if method == "GET":
                    return self.json(200, store.prefs(actor))
                if method == "PATCH":
                    return self.json(200, store.set_prefs(actor, self.body_json()))
            if path == "/api/guidance":
                if method == "GET":
                    return self.json(200, {"guidance": store.guidance_for(actor)})
                if method == "PUT":
                    if role != "human":
                        raise ApiError(403, "only humans set the guidance")
                    text = self.body_json(("guidance",)).get("guidance")
                    if text is None:
                        store.db().execute("DELETE FROM settings WHERE key = 'guidance'")
                        store.db().commit()
                    else:
                        store.set_setting("guidance", str(text))
                    return self.json(200, {"guidance": store.house_guidance()})
            if method == "GET" and path == "/settings":
                return self.html(page("Settings", render_settings(role, store.prefs(actor)), "/settings",
                                      len(store.tickets(["review"]))))
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
                    d = self.body_json(("username", "kind"))
                    user, token = store.add_user(d.get("username"), d.get("kind") or "agent", actor)
                    return self.json(201, {"user": user, "token": token})
                if name and tok and method == "POST":
                    return self.json(200, {"user": store.user(name), "token": store.new_user_token(name)})
                if name and not tok and method == "PATCH":
                    d = self.body_json(("disabled", "guidance"))
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
            if path == "/api/next" and method == "GET":
                raise ApiError(405, "take a ticket with POST /api/next; to see what to do, GET /api/wait")
            if path == "/api/next" and method == "POST":
                self.body_json(())
                t = store.claim_next(actor, role, True)
                out = {"ticket": t, "guidance": store.guidance_for(actor)}
                started = [w for w in store.work_for(actor) if w["status"] == "in_progress" and (not t or w["id"] != t["id"])]
                if started:
                    out["in_progress"] = started
                    out["note"] = (f"You also have {len(started)} ticket{'s' if len(started) != 1 else ''} in progress "
                                   "(in \"in_progress\"): finish those first. GET /api/wait lists your work in order.")
                return self.json(200, out)
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
                    return self.json(201, store.create(self.body_json(TICKET_NEW), actor, role))
            if m:
                tid, sub, other = m.group(1), m.group(2), m.group(3)
                if sub == "/deps" and method == "POST" and not other:
                    return self.json(200, store.add_dep(tid, str(self.body_json(("depends_on",)).get("depends_on") or ""), actor, role))
                if sub == "/deps" and method == "DELETE" and other:
                    return self.json(200, store.remove_dep(tid, other, actor, role))
                if sub == "/cancel" and method == "POST" and not other:
                    self.body_json(())
                    return self.json(200, store.cancel(tid, actor, role))
                if sub in ("/accept", "/decline") and method == "POST" and not other:
                    # Always read the body, even for decline: left unread, it would
                    # be taken for the start of the next request on this connection.
                    d = self.body_json(("ready", "assignee", "priority") if sub == "/accept" else ())
                    return self.json(200, store.review_proposal(tid, sub == "/accept", bool(d.get("ready")) and sub == "/accept",
                                                                actor, role, d.get("assignee"), d.get("priority")))
                if other:
                    raise ApiError(404, "not found")
                if not sub and method == "GET":
                    return self.json(200, store.detail(tid, limit=PAGE))
                if not sub and method == "PATCH":
                    return self.json(200, store.update(tid, self.body_json(TICKET_EDIT), actor, role))
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
                if sub == "/comments" and method == "GET":
                    before = (query.get("before") or [""])[0]
                    limit = (query.get("limit") or [""])[0]
                    if not before.isdigit():
                        raise ApiError(400, "say where to page back from: ?before=COMMENT_ID (the oldest you have)")
                    return self.json(200, store.comment_page(tid, int(before),
                                                             max(1, min(200, int(limit) if limit.isdigit() else PAGE))))
                if sub == "/comments" and method == "POST":
                    return self.json(201, store.comment(tid, self.body_json(("body",)).get("body"), actor, role))
                if sub == "/attachments" and method == "POST":
                    name, mime, size, stream, fields = self.read_upload(query)
                    cid = (query.get("comment_id") or [fields.get("comment_id") or ""])[0]
                    if cid and not cid.isdigit():
                        raise ApiError(400, "comment_id is a comment's number")
                    a = store.add_attachment(tid, name, mime, size, stream, int(cid) if cid else None, actor, role,
                                             self.server.max_upload)
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
                cid = store.start_conversation(self.body_json(("members", "title", "body")), actor, role)
                return self.json(201, store.conversation_detail(cid, actor, mark=False))
        if path == "/api/chat/inbox" and method == "GET":
            # Agents: everything they still owe (human messages until they reply). Humans: unread.
            convs = store.chat_attention(actor) if role == "agent" else store.unread(actor)
            for c in convs:  # humans' unread list: same shape
                c.setdefault("count", len(c["messages"]))
                c["messages"] = c["messages"][-PAGE:]
            return self.json(200, {"count": sum(c["count"] for c in convs), "conversations": convs})
        m = re.fullmatch(r"/api/chat/(\d+)(/messages|/attachments|/members|/read)?", path)
        if m:
            cid, sub = int(m.group(1)), m.group(2)
            if not sub and method == "DELETE":
                return self.json(200, store.delete_conversation(cid, role))
            if not sub and method == "GET":
                return self.json(200, store.conversation_detail(cid, actor, mark=False))
            if sub == "/messages" and method == "GET":
                # ?before=ID: the page of older messages; ?after=ID: everything newer.
                num = lambda k: int(v) if (v := (query.get(k) or [""])[0]).isdigit() else None
                limit = max(1, min(200, num("limit") or PAGE))
                c = store.conversation(cid)
                if num("after") is not None:
                    return self.json(200, {"messages": store.messages(c["id"], after=num("after")), "more": False})
                msgs = store.messages(c["id"], limit=limit, before=num("before"))
                return self.json(200, {"messages": msgs, "more": bool(msgs) and store.older_messages(c["id"], msgs[0]["id"])})
            if sub == "/messages" and method == "POST":
                return self.json(201, store.post_message(cid, self.body_json(("body",)).get("body"), actor, role))
            if sub == "/members" and method == "POST":
                return self.json(200, store.add_member(cid, str(self.body_json(("username",)).get("username") or ""), actor, role))
            if sub == "/read" and method == "POST":
                self.body_json(())
                store.mark_read(store.conversation(cid)["id"], actor)
                return self.json(200, {"unread": store.unread_total(actor)})
            if sub == "/attachments" and method == "POST":
                name, mime, size, stream, fields = self.read_upload(query)
                mid = (query.get("message_id") or [fields.get("message_id") or ""])[0]
                return self.json(201, store.add_chat_file(
                    cid, int(mid) if mid.isdigit() else None, name, mime, size, stream, actor, role,
                    self.server.max_upload))
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


def make_server(host, port, data_dir, max_upload_mb=1024):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.store = Store(data_dir)
    srv.max_upload = max_upload_mb << 20
    return srv


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("AT_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("AT_PORT", 43917)))
    ap.add_argument("--data", default=os.environ.get("AT_DATA", str(Path(__file__).resolve().parent / "data")),
                    help="folder for the database, uploads and keys")
    ap.add_argument("--max-upload-mb", type=int, default=int(os.environ.get("AT_MAX_UPLOAD_MB", 1024)))
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
    if a.export:
        dest, counts, pruned = export_to(Store(a.data), a.export, a.keep)
        print(f"Backed up to {dest}: " + ", ".join(f"{n} {t}" for t, n in counts.items())
              + (f" (removed {pruned} old backup{'s' if pruned != 1 else ''})" if pruned else ""))
        return
    if a.import_:
        try:
            counts = import_archive(Store(a.data), a.import_, a.replace)
        except (ValueError, OSError, KeyError, tarfile.TarError) as e:
            sys.exit(f"Import failed: {e}")
        print(f"Restored from {a.import_}: " + ", ".join(f"{n} {t}" for t, n in counts.items()))
        return
    if a.add_user or a.new_token or a.disable or a.enable or a.list_users:
        store = Store(a.data)
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
    srv = make_server(a.host, a.port, a.data, a.max_upload_mb)
    print(f"agent-tickets on http://{a.host}:{a.port}  data: {Path(a.data).resolve()}", flush=True)
    if not srv.store.users():
        print("No users yet. Add yourself: python3 agent_tickets.py --add-user NAME --kind human", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
