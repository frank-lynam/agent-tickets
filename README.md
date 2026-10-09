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

## Run it

```bash
python3 agent_tickets.py                 # http://127.0.0.1:43917, data in ./data
python3 agent_tickets.py --keys          # print the two access keys
```

Open `http://127.0.0.1:43917/?key=<human key>` once; a cookie keeps you
signed in. Give agents the **agent** key.

| Option | Default | |
|---|---|---|
| `--host` / `AT_HOST` | `127.0.0.1` | `0.0.0.0` to reach it from the LAN |
| `--port` / `AT_PORT` | `43917` | |
| `--data` / `AT_DATA` | `./data` | database, uploaded files, `keys.json` |
| `--checklist-root` | your home folder | folders checklist files may be read from (repeatable) |
| `--max-upload-mb` | `1024` | per file |
| `--human-name` / `AT_HUMAN` | `human` | your name on comments and changes |

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
curl -H "Authorization: Bearer $AGENT_KEY" -H "X-Agent: builder" "$URL/api/next?claim=1"   # take the next ready ticket
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
