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
| Backlog | An idea, not cleared for work | You; agents can file new tickets here |
| Ready | Cleared: agents may pick it up | You (from the backlog), or an agent releasing it |
| In progress | An agent is on it; partial work counts | An agent |
| Needs review | Done, or stopped at a decision; the agent's comment says which | An agent |
| Done | Approved | You only |

Agents can't set Backlog or Done, and can only comment on tickets in them.

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
python3 agent_tickets.py --add-user builder                # an agent (the default kind)
python3 agent_tickets.py --list-users
python3 agent_tickets.py --new-token builder               # replace a token
python3 agent_tickets.py --disable builder                 # and --enable
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
| `--data` / `AT_DATA` | `./data` | database, uploaded files, `keys.json` |
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

- **Dashboard**: what's in flight: needs your review, in progress, ready.
- **Backlog**: ideas, with "Make ready" to clear one for agents.
- **Done**: approved work, newest first, searchable.
- **Ticket**: description, the full history (comments, status changes,
  images, video and audio inline), the agent's checklist file with
  progress, and buttons to approve, send back, or move it anywhere.
- **New ticket**.

## For agents

`GET /api` returns the API docs as Markdown: point an agent at it. In short:

```bash
curl -H "Authorization: Bearer $TOKEN" "$URL/api/next?claim=1"                           # take the next ready ticket
curl ... -d '{"body": "..."}' "$URL/api/tickets/KAMI/comments"                            # report progress
curl ... -H "Content-Type: video/mp4" --data-binary @demo.mp4 \
     "$URL/api/tickets/KAMI/attachments?filename=demo.mp4&comment_id=12"                   # attach a recording
curl -X PATCH ... -d '{"status": "review"}' "$URL/api/tickets/KAMI"                        # hand it back
```

Ticket ids are four letters you can say out loud (`KAMI`, `ROZU`).

## Tests

```bash
python3 -m unittest -v
```
