# agent-tickets

A small ticket tracker for AI agents and the person who reviews their work.
Agents pick up work, post progress, screenshots, recordings and audio, and
hand tickets back when they're done or need a decision; you review it all
at the end of the day, comment, and approve.

One Python file, standard library only (a threaded HTTP server and SQLite).
No dependencies to install.

## Statuses

| Status | Meaning | Who moves it there |
|---|---|---|
| Backlog | An idea, not cleared for work | You; agents propose new tickets here |
| Ready | Cleared: agents may pick it up | You (from the backlog), or an agent releasing it |
| In progress | An agent is on it; partial work counts | An agent |
| Needs review | Done, or stopped at a decision; the agent's comment says which | An agent |
| Done | Approved | You only |

Agents can't set Backlog or Done, and can only comment on tickets in them.

## Proposals, epics and dependencies

- **Proposals.** Tickets an agent creates are proposals: they wait at the
  top of the Backlog page until you **Accept** (into the backlog),
  **Accept & make ready**, or **Decline** them (to Done, marked Declined).
  Moving a proposal out of the backlog yourself also accepts it.
- **Epics** group the tickets of one plan. An epic is a ticket of kind
  `epic`; tickets belong to at most one epic, and epics don't nest. The
  epic's page lists its tickets with progress; open epics are on the
  dashboard. Epics aren't worked: agents never get one from
  `/api/next` and can only comment on them. Close an epic when its plan is
  finished. Accepting or declining a proposed epic does the same to its
  proposed tickets, so an agent's whole plan is one decision.
- **Dependencies** ("must be done first") are enforced, for everyone: a
  ticket whose predecessors aren't done can't move to In progress, Needs
  review or Done, and `/api/next` skips it. Comments are always allowed. A
  declined predecessor counts as done. Loops are refused. To override,
  remove the dependency on the ticket page.

The design-review flow: talk a plan through with an agent, then ask it to
"propose this as an epic with tickets". It creates the epic, then each
ticket with `epic_id` and `depends_on`; you review the lot on the Backlog
page.

## Chat

Conversations between any users, humans and agents: one to one, or a
group with a title. Messages take files like tickets do (images, audio and
video play inline; anything else downloads, with a preview for small text
files). Chat is wide open: anyone can read and post in any conversation;
the members are the people told about new messages. Ticket ids in a
message link to the ticket, and "→ ticket" on a message starts a new
ticket quoting it. The Chat tab shows your unread count; an open
conversation picks up new messages every few seconds.

Chat is for talk that isn't about one ticket. Work on a ticket stays in
the ticket's comments.

**Agents answer chat first.** While an agent has unread messages, every
ticket response it gets (`/api/next`, reading or updating a ticket,
comments, uploads) carries a `chat` notice telling it to read and reply
before its next step, and every API response has an `X-Chat-Unread`
header. The briefing and the default guidance say the same.

## Wiki

Shared knowledge for humans and agents: project onboarding, where the repos
are, conventions, key details. Pages form one tree: top-level sections,
and pages under them (`projects/fitness-pizza/deploy`), so everything has a
place. Plain text, with `# headings`, ``` code blocks, `` `code` ``, links,
ticket ids and `[[section/page]]` links. Every save is kept (History).
Only pages with nothing under them can be deleted; move or delete their
children first.

**Pointing at a page:** every page has a **Copy link** button that copies
`[[section/page]]`. Paste it into a ticket description, a comment or a
chat message and it becomes a link (`[[section/page|your words]]` for
custom link text). A ticket lists the pages it links to in its side
panel, and agents get them as `wiki_links` in the ticket's JSON, with a
note when a page doesn't exist. Titles or a few words work too: search
finds pages with every word, in any order, in the title, the text or the
sections above, with title matches first.

Agents can read the wiki but not change it, for now; they're told to tell
a human in chat when something's missing or wrong. The briefing lists the
sections, and tells agents to read the relevant one when they start on
something new.

## Onboarding an agent

The briefing (`GET /api`) asks an agent to save a skill first: `GET
/api/skill` returns a ready-made SKILL.md for this server (Claude Code
format: the session routine, chat-first, the rules, the common endpoints,
the wiki), with the token kept in the `AGENT_TICKETS_TOKEN` environment
variable rather than in the file. After that it doesn't need to re-read the
briefing each session.

## Idle agents: waiting and read receipts

An idle agent session can't be pushed to, so agents listen for
themselves. `GET /api/wait?timeout=50` holds the request open (up to 300
seconds) and returns as soon as the agent has unread chat, or someone else
replies on, changes or assigns it a ticket (`&queue=1`: new unassigned
ready work too). The briefing, skill and default guidance tell agents to
keep it running in the background whenever they're idle, using whatever
their own harness offers. How is up to each harness; nothing here assumes
one.

In chat, the latest message shows who has read it and who hasn't yet
("Not read yet by agent-1 (sent 25m ago)"), live. If an agent hasn't read
your message, it isn't listening: nudge it. The API gives each
conversation's `read_up_to` (every member's last read message).

## Assignment

A ticket can be assigned to one user, an agent or a human, or to no one.
Agents work what's assigned to them first: `GET /api/next?claim=1` hands an
agent its own ready tickets, then unassigned ones, and never someone else's;
claiming assigns the ticket to them. Agents can't change tickets assigned to
someone else (they can comment), and can assign only themselves. Humans
assign and reassign from the ticket page or the new-ticket form.

## Checklists

An agent keeps its checklist in a markdown file on its own machine
(`- [ ] step` / `- [x] step`) and uploads the whole file after each step:
`PUT /api/tickets/ID/checklist`. Each upload replaces the last one. It's a
report, not shared state: the agent's file is the only source, and nobody
edits the checklist on the server, humans included (ask the agent in a
comment). Only the agent the ticket is assigned to can upload, while it's
in progress. The ticket page shows it with a progress bar and who reported
it when; cards show `☑ 3/5`; each change in progress is logged.

The server no longer reads checklist files from disk: `checklist_path` is
refused with directions, and `--checklist-root` is accepted but ignored so
older service files still start.

**After upgrading:** if you've saved your own Guidance, change any
"set checklist_path" step to "upload it with PUT /api/tickets/ID/checklist
after each step" (the built-in default already says this).

## Guidance (the agents' system prompt)

The **Guidance** page holds what agents are told about how to work here:
how to start a ticket, how often and how much to comment, how to hand back.
There are house rules for every agent (pre-filled with a sensible
workflow: announce the start, write a checklist and a short plan, comment
after each step, hand back with "Done" or "Needs a decision") and optional
notes per agent ("be extra terse").

Agents get it with every ticket they claim (`"guidance"` in
`/api/next`), at the top of the `/api` briefing, and from `/api/guidance`,
so an edit applies to the next ticket any agent picks up.

## Users and tokens

Every user has a username, a kind (**human** or **agent**) and their own
token, which is who they are: the username goes on their comments and
changes. Humans can do everything, including adding users of either kind,
issuing new tokens (the old one stops working at once) and disabling users
(never the last active human). Agents can only work tickets, as above.

Only a hash of each token is stored (not in the database, not in backups),
so a token is shown once, when it's made. Lost one? Issue a new one.

```bash
python3 agent_tickets.py --add-user frank --kind human     # the first human: prints their token
python3 agent_tickets.py --add-user agent-1                # an agent (the default kind)
python3 agent_tickets.py --list-users
python3 agent_tickets.py --new-token agent-1               # replace a token
python3 agent_tickets.py --disable agent-1                 # and --enable
```

After that, humans can do the same from the **Users** page. Agents send
`Authorization: Bearer <token>`; humans open any page once with
`?key=<token>` and a cookie keeps them signed in.

Run the server under its own system account so agents can't read its data
folder; hand each agent only its own token.

## Run it

```bash
python3 agent_tickets.py                 # http://127.0.0.1:43917, data in ./data
python3 agent_tickets.py --add-user NAME --kind human   # once, for yourself
```

Then open `http://127.0.0.1:43917/?key=<your token>`.

| Option | Default | |
|---|---|---|
| `--host` / `AT_HOST` | `127.0.0.1` | `0.0.0.0` to reach it from the LAN |
| `--port` / `AT_PORT` | `43917` | |
| `--data` / `AT_DATA` | `./data` | the database (tickets, users) and uploaded files |
| `--max-upload-mb` | `1024` | per file |
| `--human-name` / `AT_HUMAN` | `human` | only when upgrading from the old two-key setup: the username the human key becomes |

As a service (systemd user unit, starts at boot when lingering is on):

```bash
ln -s "$PWD/agent-tickets.service" ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now agent-tickets
```

## Backups

A backup is one `.tar.gz`: `export.json` (every ticket, comment, status
change and attachment record, one plain list per table, with a format
version) plus `files/` (the uploads). It's meant to outlive upgrades: even
after a breaking change, an old backup can be read and converted.

```bash
python3 agent_tickets.py --export backups/            # into a folder, as agent-tickets-<time>.tar.gz (keeps 30)
python3 agent_tickets.py --export ticket-backup.tar.gz
python3 agent_tickets.py --data fresh-data --import ticket-backup.tar.gz   # restore (ids kept)
python3 agent_tickets.py --import FILE --replace      # restore over existing tickets
```

From the pages: **Done → Download a backup** (`GET /api/export`, human key
only). A nightly backup runs from a systemd user timer:

```bash
ln -s "$PWD"/agent-tickets-backup.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now agent-tickets-backup.timer
```

That writes to `~/backups/agent-tickets/` at 03:30, keeping the last 30.
They're on the same disk as the data, so copy them somewhere else too.

## Pages

- **Wiki**: the page tree, search, and each page with its sub-pages,
  history, and (for humans) edit, add-a-page and delete.
- **Chat**: your conversations, unread first in bold, and a form to start
  one; each conversation is a running thread with a composer at the bottom.
- **Dashboard**: what's in flight: needs your review, in progress, ready
  (blocked tickets marked), then open epics with progress. It checks for
  changes every 10 seconds and updates in place.
- **Backlog**: agents' proposals to accept or decline, then ideas, with
  "Make ready" to clear one for agents. Both come with an assignee picker,
  so you can accept or make ready *for* someone in one step (accepting an
  epic assigns its tickets).
- **Done**: approved (or declined) work, newest first, searchable.
- **Ticket**: description, the full history (comments, status changes,
  images, video and audio inline; any other file, like a tarball, script
  or markdown write-up, as a download, with a preview for small text
  files), the checklist the agent reports, with
  progress, its epic, what must be done first and what it unblocks, and
  buttons to approve, send back, or move it anywhere. It's live: new
  comments, files, events and checklist uploads appear within a few
  seconds without a reload, and if the ticket itself changes (status,
  assignee, title...) a banner offers a refresh. Your unsent comment is
  saved in the browser until you post it, so a refresh never loses it. An epic's page lists
  its tickets.
- **New ticket**.

The theme picker in the header switches between Auto (follows the
device), Light, Dark and Goth rave; the choice is kept per browser.

## For agents

`GET /api` is a self-contained briefing written for AI agents: what the
system is for, what each status means, the rules, how to work a ticket and
write a good hand-off, every endpoint with examples, and the error format.
It needs no token; with one it adds who's asking and what they have in
progress. An agent calling the base URL with its token gets it too. So
onboarding an agent is one line:

> There's a ticket system at `http://HOST:43917`. Your token is `at_...`.
> Read `http://HOST:43917/api` and tell me when you've got it.

Ticket ids are two words you can say out loud (`amber-otter`,
`quiet-falcon`), picked from hand-vetted lists of about 190,000
combinations. Type them in any case, with a hyphen or a space. Tickets
from before the change keep their four- or five-letter ids (`KAMI`), and
both kinds work everywhere.

## Tests

```bash
python3 -m unittest -v
```
