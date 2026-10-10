# Working on agent-tickets

This file is for agents changing this repo. Agents *using* the tracker
read `GET /api` on the running server instead.

- **Standard library only.** No pip installs, no frameworks, no build
  step, no CDN assets in the pages. It's one file, `agent_tickets.py`;
  keep it that way unless it truly outgrows it.
- **Tests:** `python3 -m unittest -v`. They start a real server on a
  temporary folder. Add a test for every behaviour you change, and run
  them before committing.
- **The rules that matter** (tests cover them; don't loosen them without
  the owner asking): agents can't set backlog or done, can't change
  backlog or done tickets except by commenting, and file new tickets in
  the backlog only, as proposals only a human accepts or declines. Epics
  are only commented on by agents and never claimed. A ticket with
  unfinished predecessors can't move to in_progress, review or done, for
  anyone, and is never claimed; comments are always allowed; dependency
  loops are refused. Agents with unread chat get the `chat` notice on every
  ticket response. `/api/next?claim=1` must never give one ticket to two
  agents. New ticket ids come only from `ID_ADJECTIVES` and `ID_NOUNS`: vet
  every word you add (nothing offensive alone or in a pair, no homophones,
  one obvious spelling, never in both lists). Old four- and five-letter ids
  must keep working. Checklist files are read only from the allowed roots. Only
  humans manage users or export; the last active human can't be disabled.
  Tokens are stored only as hashes and never appear in logs or backups.
- **Data** lives in `data/` (gitignored): `tickets.db` (tickets and
  users) and `files/`. Schema changes must keep existing databases working:
  `CREATE TABLE IF NOT EXISTS` plus additive `ALTER TABLE`s, never a
  rebuild.
- **Backups must stay restorable.** Before a change that alters the
  tables, make sure `import_archive()` still loads older backups (it
  matches columns by name; add conversion code for anything renamed or
  restructured), bump `SCHEMA_VERSION` (and `EXPORT_VERSION` if the
  export layout changes), and test restoring a backup made before the
  change. Take a backup (`--export`) before upgrading the running instance.
- **Pages** are server-rendered HTML with a little inline JavaScript.
  Escape everything user- or agent-written (`esc()`), and check new
  pages at phone width.
- The running instance on the owner's machine is the user service
  `agent-tickets` (`systemctl --user restart agent-tickets` after a
  change). Commit and push to GitHub when done.
