# Working on hearthboard

This file is for agents changing this repo. Agents *using* the tracker
read `GET /api` on the running server instead.

- **Standard library only.** No pip installs, no frameworks, no build
  step, no CDN assets in the pages. It's one file, `hearthboard.py`;
  keep it that way unless it truly outgrows it.
- **Tests:** `python3 -m unittest -v`. They start a real server on a
  temporary folder. Add a test for every behaviour you change, and run
  them before committing.
- **The rules that matter** (tests cover them; don't loosen them without
  the owner asking): agents can't set backlog or done, can't change
  backlog or done tickets except by commenting, and file new tickets in
  the backlog only, as proposals only a human accepts or declines. Epics
  are only commented on by agents and never claimed. A ticket with
  unfinished predecessors can't be moved to in_progress, review or done by
  an agent (humans may move it), and is never claimed; comments are always allowed; dependency
  loops are refused. An agent hands a ticket to review only when its latest
  comment starts "Done" or "Needs a decision"; releasing to ready unassigns it. Agents with unread chat get the `chat` notice on every
  ticket response. The wiki is read-only for agents; every wiki page has
  a parent or is a top-level section, and only leaf pages can be deleted. Claiming (`POST /api/next`) must never give one ticket to two
  agents. New ticket ids come only from `ID_ADJECTIVES` and `ID_NOUNS`: vet
  every word you add (nothing offensive alone or in a pair, no homophones,
  one obvious spelling, never in both lists). Old four- and five-letter ids
  must keep working. Checklists are only ever uploaded (`PUT .../checklist`), by the agent
  assigned to the ticket while it's in progress, replacing the last upload;
  the server never reads files from disk paths, and nobody edits checklists
  on the server. Only
  humans manage users or export; the last active human can't be disabled.
  Tokens are stored only as hashes and never appear in logs or backups.
- **Data** lives in `$HEARTHBOARD_DATA`, else `~/.local/share/hearthboard`
  (`default_data_dir()`): `tickets.db` (tickets and
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
- **No auto-updates or migrations.** Never write code that detects an older
  install and changes its files, services or data, and no legacy-name shims or
  fallbacks. Assume a fresh install in an austere environment. Moving a board
  is the owner's call, through `--export` / `--import`; `service add` leaves an
  existing service alone unless given `--force`.
- **Packaging:** it's on PyPI as `hearthboard` (`pyproject.toml`, the
  `hearthboard` command is `cli()`). Releases: bump `__version__`, push,
  and the owner publishes a GitHub release `vX.Y.Z` (see README "Releasing").
- The owner runs the live instance under their own account: don't start,
  restart or touch it or its data. Test on a throwaway server
  (`--data` in a temp folder, another `--port`), then commit and push to
  GitHub; the owner pulls and restarts.
