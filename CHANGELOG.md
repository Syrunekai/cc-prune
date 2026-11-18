# Changelog

## 0.5.1 (2026-11-18)

### Fixed

- Tooltips in the window were black text on dark grey on desktops whose GTK/Qt style assumes
  light tooltips (seen on XFCE). They now take the theme's colors, through the stylesheet and
  Qt's separate tooltip palette.
- The README's install step cloned the original repository, which holds only its first commit;
  it now clones this fork, and the License section credits the original author.

## 0.5.0 (2026-11-18)

### Added

- **`export`**: write a session as a markdown transcript, by path or session id, into a
  directory, a file, a named folder (`--to NAME`) or the default folder. `--raw` adds tool calls
  and results, thinking and images; `--head` / `--tail N`; `--timezone` (local by default);
  `--user-name`, `--assistant-name`, `--title`; `--force` to overwrite. The library
  (`ccprune.export`) started from a standalone transcript extractor, cut down to Claude Code;
  what it exports is new:
  - It follows the conversation chain instead of taking every record in the file, so prompts
    you rewound or edited, records a branch-mode cut left behind and sidechains are left out.
  - By default it starts at the latest compaction, with that compaction's summary first.
    `--history` follows every compaction back to the first message (through
    `logicalParentUuid`), marks each one and leaves the summaries out. `--summary` /
    `--no-summary` override either default; with both, each summary follows its marker. In the
    window the summary box follows the history box and is never saved.
  - In `--raw`, tool results are labeled Tool, not as your messages.
- **An Export tab in the window**, beside the prune settings (now the Prune tab): named folders,
  a one-off folder that can be saved by name, a default, the file name, the same options, a
  preview of the markdown, the equivalent command, and Open folder.
- **`.env`** (gitignored) for local settings, with `.env.example`: `CC_PRUNE_EXPORT_DIRS`, a JSON
  array of named folders, `CC_PRUNE_EXPORT_DEFAULT` (the only folder is the default), and the
  export's speaker labels and time zone. The window writes saved folders back without touching
  any other line. `CC_PRUNE_ENV` names another file; the environment wins over the file.
- **`cc-prune --gui`** opens the window. The command line still needs nothing; without PySide6,
  `--gui` runs again through `uv run --with pyside6`.

### Changed

- **One launcher**: `cc-prune` (executable, uv shebang, no dependencies) is the command line,
  and the window with `--gui`. `cc-prune-gui` is a shell shortcut for `cc-prune --gui`. The
  window's commands use `cc-prune`.

### Removed

- `cc_prune.py`. Use `cc-prune` (`python3 cc-prune ...` also works).

## 0.4.0 (2026-11-18)

### Added

- `cc_prune.py` and the new `cc-prune-gui` are executable and start with a `uv` shebang
  (`#!/usr/bin/env -S uv run --script`) with their dependencies declared in the script header, so
  `./cc-prune-gui` runs from a checkout with no install and no activated environment.
- **A window** (`cc-prune-gui`, `python3 -m ccprune.gui`; Linux, needs the `gui` extra, PySide6). A
  sessions list with favorites, an always-visible settings panel with stepped sliders and the exact
  command for the same edit, a live preview, and a bottom drawer (hidden by default) with the
  by-turn range slider and the snapshots. The turn range is only used when "By turn range" is
  selected. Apply checks the live-session guard and waits out the recent-edit check on its own. The session list draws its own hover and selection, so text stays readable and a
  selected live session stays red, and a Live column says “● live” in words.
- `clearcut` takes `--from A --to B`, as `cut` does.
- Snapshots carry a label and can be pinned, deleted and capped: `snapshots FILE --pin N`,
  `--unpin N`, `--delete N`, and `MAX_SNAPSHOTS` in the settings. Snapshot 0 counts as pinned.
- Favorite sessions, kept by id in `~/.config/ccprune/favorites.json`.
- The context each edit estimated is logged in `~/.local/share/ccprune/estimates.jsonl` and matched
  later with the real count the API recorded for the first request after it.
- An estimate says whether it is anchored on real counts or chained on an earlier estimate.
- A plan can be made from an already-read transcript (`load_transcript`, `plan_prune(loaded=...)`),
  and applying it is refused if the file changed after it was read.
- Tests (`uv run pytest`) on a synthetic transcript: clear counts, plan writes nothing,
  clearcut equals a cut then a clear, one snapshot, validation failure, restore, the guard.

### Changed

- `clearcut` is one pass over the records, not a `cut` followed by a `clear`. It plans the
  cut and the clear in memory, validates once, takes one snapshot and writes once, and it
  prints one report: the cut, the clear's hit counts and the estimated context, then the
  snapshot, the usage reset, the size and the rollback line. The preview shows the clear's
  real hit counts instead of the flags it would run with. On real transcripts the file is
  byte-identical to the old two-step result; where the clear frees enough to rescale the
  recorded usage a second time, the single pass rescales it once, which moved two usage
  numbers by 1 token in one test.
- The tool is a package (`src/ccprune/`) and a `uv` project. The engine (`prune`, `clear`,
  `cut`, `estimate`, `sessions`, `snapshots`, `guard`, `analysis`, `maintenance`) takes
  explicit options, returns plain data and raises `PruneError`; `cli.py` only parses
  arguments and prints. `cc_prune.py` stays as a shim, so `python3 cc_prune.py ...` runs
  from a checkout without installing anything. `uv run cc-prune ...` works too.
- A transcript is validated before the snapshot is taken, so a refused edit no longer
  leaves a snapshot behind.
- The settings (`PROJECTS_ROOT`, `SESSIONS_DIR`, `LIVE_IF_EDITED_WITHIN`) moved to
  `src/ccprune/settings.py`.
- Rollback lines name the command as it was run (`cc_prune.py` or `cc-prune`).

### Fixed

- **The estimator over-estimated after earlier cuts.** The first request in the file was treated as
  an untouched anchor even when counts had dropped since, which spread a stale count over the few
  records left. One real session came out at 520k against a real 210k. That anchor is now skipped
  after any drop, and a segment's rescaling is capped at 6x. On 36 real before/after pairs the
  error is now between -7% and +4% (median 2%); before, the worst case was +157%.
- A clear that matches nothing no longer rewrites the file or takes a snapshot.
- `restore` labels the snapshot it saves.

### Removed

- The hidden `clear --no-snapshot`, which only the old `clearcut` used.

## 0.3.0 (2026-10-30)

### Added

- `clearcut FILE --drop F [--keep-head N] [--keep-tail F] --yes`: a `cut` followed by a
  `clear` in one call. The cut uses `--purge` and keeps the first turn by default
  (`--keep-head 1`, `--branch` keeps the cut turns as an unreachable branch instead); the
  clear uses `--results 1 --inputs 1` and `--images` (`--no-images` leaves them). A single
  snapshot is taken before the cut, so one `restore` undoes both steps. Without `--yes` it
  previews the cut only and prints the flags the clear would run with. `--keep-tail`,
  `--commands`, `--attachments`, `--transformations`, `--drop-failing-thinking`,
  `--drop-all-thinking`, `--no-note`, `--no-reset-usage` and `--force` pass through to
  the two commands. It calls `cmd_cut` and `cmd_clear` through the real subparsers, so
  their defaults stay in one place, and the result is byte-identical to running the two
  by hand. The live-session guard applies to the cut; the clear step then runs with
  `--force`, because the cut has just rewritten the file.

### Changed

- The argument parser is built by `build_parser()`. `clear` has a hidden `--no-snapshot`,
  used by `clearcut`. The defaults of `cut` and `clear` are unchanged.

## 0.2.2 (2026-10-30)

### Changed

- The usage estimate `clear` and `cut` write is rebuilt. It splits the conversation at
  the requests made since the last edit, whose recorded counts are exact, and estimates
  only how each segment divides among its records (text by characters, thinking by
  signature length). Thinking the edit deletes or invalidates now counts as removed,
  carried attachments count as moved, and an edit right after another starts from the
  snapshot that still holds real counts. The result is lowered by 2.5%. The old estimate
  (removed characters at 1.5 chars/token) was up to 12% over the real count; on 27 real
  before/after pairs the new one was under on every pair, by 0.3% to 15%.
- README is back to the original's structure and length, plus the Quick start. The
  Troubleshooting section, the `cut` documentation and the usage-estimate explanation moved
  to `docs/troubleshooting.md`, `docs/cut.md` and `docs/estimate.md`, with short sections
  in the README linking to them.
- Records whose usage cc-prune rewrote carry `"ccPruneEstimate": true`.
- Character counts for tool results and attachments no longer include JSON keys and
  escaping, which changes the sizes `cut --list` shows and the `--drop` boundaries a little.

### Fixed

- `cut` on a session cut before: the note now carries the running total. A note inside
  the cut stretch is added to the new count (it used to be dropped with its turn), and a
  note already on the first kept prompt is merged instead of stacked. The preview shows
  the earlier total.
- `sessions` showed the auto-generated title for a renamed session. `custom-title` records
  inside the transcript are now read and win over `ai-title` and `summary`; the latest
  one counts.
- Sessions written by Claude Code 2.1.300 and up carry `api-request` and
  `api-request-blob` records, and the blobs hold copies of tool results. The tool_use /
  tool_result pairing check in `validate` and `validate_cut` counted those copies, so a
  `cut --purge` aborted on results whose tool call sat in the part being deleted. Both
  checks now look at conversation records only.

## 0.2.1 (2026-10-14)

### Added

- `cut`: remove a stretch of whole turns from the middle of the conversation and keep the
  start and the recent end. `--keep-head N --drop F` cuts a fraction of the conversation
  after the head by size; `--from A --to B` cuts exact turns; `--list` numbers the turns.
  Previews unless `--yes`. The first kept turn is re-linked onto the head; the cut turns
  stay as an unreachable branch, or are deleted with `--purge`. Both ends land where no
  tool call is waiting for a result. State attachments in the cut stretch (system prompt,
  tool schemas, tool/MCP/agent/skill listings) are copied over. The first kept prompt gets
  a note unless `--no-note`. Recorded usage is reset to an estimate. Same live-session
  guard and snapshot as `clear`, with its own validation.
- `clear --commands N`: clear `Bash` `command` values longer than `N` characters.
  `--inputs` keeps them (they are in `KEEP_PARAMS`), so long heredocs and inline scripts
  had no flag before.

### Fixed

- A second `clear --inputs` pass re-cleared values the first pass had already replaced
  with `[cleared] N chars` and counted them as freed, which inflated the hit count and
  lowered the usage estimate. Those values are now skipped.

### Changed

- README Quick start: the prune step now includes `--drop-all-thinking --yes`, so a first
  prune is one command. The `--transformations` pass is documented only for sessions pruned
  and resumed before without the thinking flag. Suspended (ctrl+z) sessions count as running.

## 0.2.0 (2026-10-08)

Builds on 0.1.0, the original release by Qihan Zhao.

### Added

- `sessions`: list and filter Claude Code transcripts without knowing the session id.
  Shows start, last activity, project, context (`CTX`), a one-line "about", size and id.
  Filters and options: `--project`, `--grep`, `--since`, `--until`, `--last`, `--count`,
  `--sort {date,size,project,ctx}`, `--reverse`, `--limit`, `--all`, `--timezone`, `--json`,
  `--paths`, `--subagents`, `--root`.
- Running `cc_prune.py` with no arguments prints a quick start and the command list.
- `reset-usage FILE --tokens N`: rewrite the recorded token usage on the last requests.
  A pruned session that was near its limit is refused on resume ("Context limit reached")
  until the recorded usage is lowered.
- `clear` now does that automatically after pruning, from an estimate of the freed
  characters. Pass `--no-reset-usage` to leave usage alone.
- Live-session guard on `clear`, `strip-commands`, `reset-usage` and `restore`. A transcript
  is refused when a running Claude Code process is registered for it (including subagent
  logs; the process start time is checked on Linux) or when it was edited within
  `LIVE_IF_EDITED_WITHIN` seconds (default 30). `--force` overrides. Honors
  `CLAUDE_CONFIG_DIR`.
- A settings block near the top of `cc_prune.py`.
- Experimental, opt-in `clear` flags for the `input_transformations` field the API returns
  and Claude Code stores on every assistant record, which can make a pruned transcript grow
  by tens of KB per record:
  - `--transformations` deletes the saved reports.
  - `--drop-failing-thinking` deletes the thinking blocks the API reported dropping.
  - `--drop-all-thinking` deletes every thinking block.
  - The two thinking flags preview unless `--yes` is given, refuse layouts they have not
    been checked against, and keep the records in place with empty content.
- `inspect` reports the `transformations` bucket.
- README: sessions workflow, Troubleshooting section (resume refused, invalid tool schema,
  transcript growth), live-session documentation.

### Changed

- `restore` validates the snapshot first, saves the current file as a new snapshot, and
  swaps atomically, so a restore can no longer silently discard work done since the prune.
- Rewritten transcripts keep the original file mode. The temporary file is created `0600`.
- Validation compares whole thinking blocks, not only their signatures.
- `--attachments` skips `prompt_snapshot` and `deferred_tools_record` attachments. They hold
  the system prompt and tool schemas the API request is rebuilt from, and clearing them made
  a resume fail with `tools.0.custom.input_schema: JSON schema is invalid`.

### Fixed

- `strip-commands` could rewrite the text of a thinking block that contained
  `<local-command-stdout>` tags.
- Snapshot listing crashed on a stray `*.snapshot` file and treated glob characters in the
  path as wildcards.

### Known limits

- The thinking flags have been run on throwaway copies and a few real sessions only.
- The live-session guard was tested on Linux. The macOS and Windows branches are simulated.
- The transcript format is undocumented and changes between Claude Code versions.
- Python 3.9 is the stated minimum but only 3.13 has been run; the code parses under the 3.9
  grammar.
- The tool rewrites JSON with a space after `:` and `,`, which grows a rewritten file by
  about 3% compared with Claude Code's compact form.

## 0.1.0

Original release by Qihan Zhao: `usage`, `inspect`, `clear`, `snapshots`, `restore`,
`audit`, `strip-commands` and `density`.
