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

## Assignment

A ticket can be assigned to one user, an agent or a human, or to no one.
Agents work what's assigned to them first: `GET /api/next?claim=1` hands an
agent its own ready tickets, then unassigned ones, and never someone else's;
claiming assigns the ticket to them. Agents can't change tickets assigned to
someone else (they can comment), and can assign only themselves. Humans
assign and reassign from the ticket page or the new-ticket form.

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
| `--checklist-root` | your home folder | folders checklist files may be read from (repeatable) |
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

- **Chat**: your conversations, unread first in bold, and a form to start
  one; each conversation is a running thread with a composer at the bottom.
- **Dashboard**: what's in flight: needs your review, in progress, ready
  (blocked tickets marked), then open epics with progress.
- **Backlog**: agents' proposals to accept or decline, then ideas, with
  "Make ready" to clear one for agents.
- **Done**: approved (or declined) work, newest first, searchable.
- **Ticket**: description, the full history (comments, status changes,
  images, video and audio inline; any other file, like a tarball, script
  or markdown write-up, as a download, with a preview for small text
  files), the agent's checklist file with
  progress, its epic, what must be done first and what it unblocks, and
  buttons to approve, send back, or move it anywhere. An epic's page lists
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
