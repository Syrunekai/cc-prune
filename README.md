# cc-prune

Surgical context recovery for Claude Code sessions.

This is a fork of [QihanZhao/cc-prune](https://github.com/QihanZhao/cc-prune) (MIT). The original
tool is 0.1.0; see [CHANGELOG.md](CHANGELOG.md) for what this fork adds.

When a session approaches its context limit, the usual answer is `/compact` — which
summarizes everything, including the reasoning you may still want. `cc-prune` takes a
narrower cut: it clears **tool output that Claude has already digested** from the
session transcript, leaving the conversation, the reasoning, and the structure intact.

Nothing is summarized. No line is deleted. Thinking blocks are never touched, except by
the explicit, experimental `--drop-failing-thinking` and `--drop-all-thinking` flags (see
[Troubleshooting](#troubleshooting)). The one command that removes conversation is `cut`,
which drops a stretch of whole turns you pick (see [Cutting turns](#cutting-turns)).

```
86.8% full  ──[ clear --results ]──►  57% full
```

That is a real measurement from one session. Your mileage will vary a lot — see
[What to expect](#what-to-expect).

---

## Quick start

1. **Exit the Claude Code session** (`exit`, not `kill`). Commands that edit a transcript
   refuse one that looks live; `--force` overrides that. A session suspended with ctrl+z is
   still running: bring it back with `fg` and exit it.
2. **Find the transcript.** This lists your sessions, newest first, with their context size:

   ```bash
   python3 cc_prune.py sessions
   F=$(python3 cc_prune.py sessions --paths --limit 1)   # or the path of the one you want
   ```

3. **Prune.** This is the step that frees context. It clears old tool results and tool
   inputs, deletes the thinking blocks, takes a snapshot first, and rewrites the recorded
   usage so the session can be resumed:

   ```bash
   python3 cc_prune.py clear "$F" --results 1 --inputs 1 --images --drop-all-thinking --yes
   ```

   The thinking flag keeps the file from growing after the resume. A prune makes Claude
   Code's API drop the thinking blocks that came after the first edit and report it on every
   record, which can add tens of KB per record; with no thinking left there is nothing to
   drop. It changes the file's size, not the model's context. Leave it off (and `--yes`,
   which only it needs) to keep the reasoning in the transcript.

4. **Resume, send a real message, and read the context from the status bar.** The number
   the tool printed is an estimate; the first reply replaces it with the API's own count.
   To undo: `python3 cc_prune.py restore "$F" 0`.

That is the whole job. Everything below is detail.

**Pruned and resumed before without the thinking flag?** Then the file already holds the
saved reports, and step 3 stops new ones but leaves those. Wait 30 seconds after step 3 (the
live-session check counts the tool's own write) and remove them:

```bash
python3 cc_prune.py clear "$F" --transformations --keep-tail 0
```

On a session's first prune there is nothing to remove. The thinking flags are experimental;
see [Troubleshooting](#troubleshooting).

---

## Honest scope

**This is not lossless.** Cleared tool results are gone from the model's view. It works
because Claude has usually already written its conclusions into its response text
before you get here — but if you clear a result the model still needs, it has to re-run
the command or re-read the file. `cc-prune audit` exists to help you check before you cut.

**This is reverse-engineered.** The transcript format is undocumented and changes
between Claude Code versions. Everything here was derived by measurement, not from
a spec. It may break on your version. Keep the snapshot.

**This is not a substitute for `/compact`.** If your session is dominated by *thinking*
rather than tool output, `cc-prune` cannot help much — thinking blocks are
cryptographically signed and cannot be edited (editing one makes the API reject the turn or
drop the block).
`inspect` will tell you which situation you are in.

---

## Install

```bash
git clone https://github.com/Syrunekai/cc-prune
cd cc-prune
python3 cc_prune.py --version
```

Python 3.9+, no dependencies.

---

## The one rule

**Claude Code holds the conversation in memory and writes it back.** If a session is
running when you edit its transcript, your changes get overwritten — or worse, half
overwritten.

```bash
pgrep -af claude | grep -v grep     # must be empty
```

If it isn't, check `ls -l /proc/<pid>/cwd` to see which project it belongs to. Exit that
session normally (`exit`, not `kill`) before continuing.

---

## Workflow

### 1. Find the transcript

Transcripts live under `~/.claude/projects/<slugified-cwd>/<session-id>.jsonl`. The
directory is keyed on the working directory the session started in, not the project.
List them with `sessions`:

```bash
python3 cc_prune.py sessions                        # newest 50
python3 cc_prune.py sessions --project my-repo      # cwd contains 'my-repo'
python3 cc_prune.py sessions --grep vpn --count     # match title/prompt, true message counts
python3 cc_prune.py sessions --sort ctx --limit 10  # biggest context first: prune candidates
```

Columns: start, last activity, project, `CTX` (input tokens of the last API request
found in the file tail), a one-line "about" (custom title, then Claude's `aiTitle`, then
the first prompt; `--last` prefers the last prompt), size, and session id. It reads only
the head and tail of each file, so it is fast; `--count` does a full scan. Only
top-level session files are listed unless you pass `--subagents`. Other flags:
`--since` / `--until` (YYYY-MM-DD), `--sort {date,size,project,ctx}`, `--reverse`,
`--limit N` / `--all`, `--timezone`, `--json`, `--root PATH`.

Put the path in a variable:

```bash
F=$(python3 cc_prune.py sessions --paths --limit 1)   # newest session
F=$(python3 cc_prune.py sessions --grep <session-id> --paths | head -1)
```

Or find it by id without `sessions`:

```bash
F=$(find ~/.claude/projects -name '<session-id>.jsonl' | head -1)
```

Running `cc_prune.py` with no arguments prints a quick-start and the command list.

### 2. Get the real number

```bash
python3 cc_prune.py usage "$F"
```

`last request` is the input token count the API actually charged. This is the only
number you should trust. See [The /context trap](#the-context-trap).

### 3. See where the bytes are

```bash
python3 cc_prune.py inspect "$F"
```

Buckets, in rough order of how much they are worth clearing:

| bucket | flag | verdict |
|---|---|---|
| `results` | `--results N` | tool output. Safest, usually the biggest win. |
| `inputs` | `--inputs N` | `Write`/`Edit` bodies. Real semantic cost — the model forgets what it wrote. `file_path` is preserved. |
| `commands` | `--commands N` | `Bash` `command` values. `--inputs` never touches them. Long heredocs and inline scripts add up; the model forgets exactly what it ran, and the output stays unless `--results` clears it too. |
| `attachments` | `--attachments N` | mostly harmless, but `prompt_snapshot` and `deferred_tools_record` attachments carry the system prompt and tool schemas the API request is rebuilt from, so they are skipped. Clearing them made a resume fail with `tools.0.custom.input_schema: JSON schema is invalid`. Verify before trusting. |
| `images` | `--images` | billed by pixels, not characters. ~0 tokens. Skip. |
| `thinking` | `--drop-failing-thinking` (experimental) | **locked by default.** Signed, and each block is bound to everything before it, so editing earlier tool results makes the API drop later blocks anyway. The flag deletes only the blocks the API already reported dropping. |
| `transformations` | `--transformations` (experimental) | the API's `input_transformations` response field, saved on assistant records. Not conversation. Rewritten every turn while blocks are being dropped, which is what bloats the file. |
| `text` | — | the conversation. No flag, by design. |

`inspect` reports **bytes, not tokens** — deliberately. See [Density](#density-varies-within-one-file).

### 4. Cut

```bash
python3 cc_prune.py clear "$F" --results 500
```

`N` is a length threshold in characters: results *longer* than `N` are replaced whole
(not truncated to `N`). Use `--results 1` to clear the bucket entirely.

`--keep-tail` (default `0.15`) protects the most recent records so the model can still
see what it just did. Lowering it recovers a little more and costs a lot of continuity.

Every `clear` writes a numbered snapshot first, validates the result before replacing
the original, and refuses to write if any invariant broke.

`clear` also rewrites the recorded `usage` on the last 5 requests to an estimate of the
new size. Without this a session that was near its limit refuses to resume, see
[Troubleshooting](#troubleshooting). The next real reply replaces it with the API's own
count. Pass `--no-reset-usage` to leave usage alone.

How the estimate works (`clear` and `cut`): every request made since the last edit
recorded the exact token count of everything before it, so the conversation splits into
segments of known size. Only how a segment divides among its records is estimated: text
by characters, thinking by the length of its signature (Opus thinking shows little text
but counts in full; the signature carries it). Thinking the edit invalidates counts as
removed. When an edit follows another with no request in between, the estimate starts
from the snapshot that still holds real counts. The result is lowered by 2.5% so it lands
under the real count. Checked against 27 real before/after pairs from pruned sessions:
every estimate was under the first real count, by 0.3% to 15% (the larger gaps come from
what Claude Code adds on resume, which no estimate can see). Records whose usage cc-prune
rewrote carry `"ccPruneEstimate": true`, so a later estimate never treats them as real.

### 5. Verify

Resume the session and **send a real message** — not a slash command. Read the status
bar.

```bash
claude --resume <session-id>
```

### 6. Roll back if needed

```bash
python3 cc_prune.py snapshots "$F"
python3 cc_prune.py restore  "$F" 0     # 0 = pristine
```

---

## Troubleshooting

**`Context limit reached` or `Prompt is too long` right after resuming a pruned session.**
Claude Code refuses the message in a few milliseconds, before the API counts anything. The
recorded `usage` on the last requests still holds the pre-prune size (for example 957k),
and `clear` does not change the conversation's recorded usage by itself. Fix:

```bash
python3 cc_prune.py reset-usage "$F" --tokens 335000    # a figure below your window
```

Pick a number comfortably under your context window. The next real reply corrects it. A
`clear` run with the default settings already does this. Use `reset-usage` on files that
were pruned with `--no-reset-usage` or by an older version. `--autocompact` is not a fix:
it sets the compaction window, not this check.

**`tools.0.custom.input_schema: JSON schema is invalid` after resuming.**
`--attachments` replaced strings inside the `prompt_snapshot` or `deferred_tools_record`
attachments, which hold the system prompt and tool schemas, including each schema's
`$schema` URL. Current versions skip those two kinds. A file already damaged cannot be
repaired from itself: restore a snapshot taken before the clear, or the pristine
original, and clear again.

Clearing `--attachments` is otherwise untested for the other attachment kinds. If a
resume fails, leave `--attachments` out first.

**The transcript keeps growing fast after a prune (tens of KB per assistant turn), and
assistant records carry `input_transformations` entries.**
Each entry is `{"type": "thinking_dropped", "reason": "prefix_binding_mismatch", "path":
"messages.N.content.0"}`. This is the API's own report, and Claude Code stores it. A thinking
block's signature binds the system prompt, the tools, every earlier message and the model.
Clearing earlier tool results edits that history, so on models that enforce the check
(observed here: Opus 5.5 and Sonnet 5.5; Haiku 4.5 does not run it) the API drops the first
mismatched block and every thinking block after it, on every request, unbilled. The
reasoning is lost from the prune onward and the report is rewritten each turn, growing the
file by about 107 bytes per dropped block per turn. Measured: 385 entries and about 43 KB
per turn after one prune of a large session, 698 entries and about 77 KB per turn after a
second.

Two experimental flags, both opt-in:

```bash
python3 cc_prune.py clear "$F" --transformations         # delete the saved reports (shrinks history once)
python3 cc_prune.py clear "$F" --drop-failing-thinking   # delete the blocks the API reported dropping
```

`--transformations` alone does not stop the growth: the field is regenerated every turn
while blocks keep failing. `--drop-failing-thinking` removes the blocks the API reported
dropping and leaves their records in place with empty content. On a small test session the
entries stopped, the context count was unchanged, and new thinking blocks written afterwards
were accepted. The next prune invalidates the blocks after it again.

Safeguards on `--drop-failing-thinking`:

- **Preview by default.** Without `--yes` it prints what it would delete (counts, record
  range, models, how many blocks stay) and writes nothing, not even a snapshot. Every other
  flag in the same command is held back too.
- **It refuses layouts it has not been checked against.** The failing blocks are the ones
  that existed when the history was last edited (found from where the latest run of equal
  reports starts). It only acts if those are every earlier block (the oldest is removed too,
  so the removal is from the front) or the newest blocks in the file. A report with valid
  blocks on both sides is refused, and so is a report that lists more blocks than exist.
- **Exact validation.** The write is rejected unless precisely the planned blocks are gone
  and everything else, including the uuid chain, is unchanged.
- Blocks written after the last edit are never listed or deleted.

`--keep-tail` is respected by `--transformations` and ignored by the two thinking flags.

**`--drop-all-thinking` (experimental).** `--drop-failing-thinking` can only remove blocks
the API has already reported, so blocks written since the last prune are missed: the next
prune invalidates them and the reports come back, smaller. `--drop-all-thinking` deletes every
thinking block in the file, so nothing is left for the API to drop. It has the same
safeguards (preview unless `--yes`, live-session guard, exact validation, records kept with
empty content) and can be combined with a prune in one pass:

```bash
python3 cc_prune.py clear "$F" --results 1 --inputs 1 --images --drop-all-thinking --yes
# only if earlier resumes already saved reports: wait out the live-edit window
# (30 s by default), then remove them:
python3 cc_prune.py clear "$F" --transformations --keep-tail 0
```

What it costs: the reasoning is gone from the transcript, although with the default
`display: "omitted"` the thinking text is blank anyway and the API was already dropping the
blocks. Run it before your first message after a prune and the cache rewrite that message
causes is the only cost. Tested on throwaway copies and on two real sessions (about 7,000 and
3,900 records); on both, every record written after the resume reported nothing dropped. The
two thinking flags have not been run on many real sessions.

**Running sessions.** `clear`, `strip-commands`, `reset-usage` and `restore` refuse to edit a
transcript under the Claude config directory (`$CLAUDE_CONFIG_DIR`, default `~/.claude`)
that looks live, unless you pass `--force`. Two independent signals are used:

- **The process registry.** `<config>/sessions/<pid>.json` maps a running process to its
  session id. A transcript counts as live if its file name, or the session folder above it
  (subagent logs), matches a registered process that is still running. On Linux the process
  start time must match too, so a reused pid is not mistaken for the session. Without `/proc`
  (macOS) only existence is checked, and on Windows nothing can be, so a registered session
  is assumed running.
- **Recent edits.** A transcript modified within the last `LIVE_IF_EDITED_WITHIN` seconds
  (a setting near the top of `cc_prune.py`, default 30, `0` turns it off) is treated as live.
  This catches a stale registry. The tool's own writes count too, so a second edit of the
  same transcript within that window needs a short wait or `--force`.

Copies outside the config directory are never blocked. The registry is an undocumented
format, so the first signal only works while it stays readable; the second does not depend
on it. Neither replaces exiting the session first.

---

## What to expect

Three sessions from the same project, same week:

| session | thinking | results | thinking:results | recovered |
|---|---|---|---|---|
| A | 0.88 MB | 0.78 MB | 1.1× | 98% → 72% |
| B | 1.50 MB | 0.52 MB | 2.9× | 86.8% → 57% |
| C | 1.92 MB | 0.41 MB | 4.7× | 92.9% → 80% |

**The `thinking:results` ratio predicts almost everything.** Tool-heavy sessions recover
well. Reasoning-heavy sessions don't, because the one large bucket is the one you can't
touch. Run `inspect` first and decide accordingly.

---

## Things we measured

Three findings, all from direct measurement against the API's own token counts.

### The `/context` trap

`/context` recomputes usage **locally**. The status bar reflects the `usage` field from
the last real API response. They normally agree — and diverge completely once you edit
the transcript.

We nearly threw away a successful prune because of this: the status bar read 72%,
`/context` read 98%, and it looked like the context had ballooned back. It hadn't. The
next real message settled at 72%.

> After editing a transcript, verify with `cc_prune.py usage` or by sending a real
> message. Never with `/context`.

This also means **auto-compaction may fire on the stale estimate**. Consider
`claude --resume <id> --autocompact 1M` while working on a pruned session.

### Images are billed by pixels, not characters

Three PNGs occupied 795,360 characters of base64 in the transcript. Clearing them moved
real usage by roughly nothing — image tokens scale with `width × height / 750`, so those
three were about 3,200 tokens total, not the ~300,000 their byte count suggests.

> Transcript size and context pressure are only loosely related. `--images` is almost
> never worth a cache miss.

### Density varies within one file

Characters per token, measured in a single session:

- numeric training logs, tensor shapes, test output: **~1.5 chars/token**
- prose reasoning: **~4.0 chars/token**

A 2.7× spread. Any single conversion constant will mislead you — ours did, repeatedly,
until we stopped estimating and started measuring. That is why `inspect` reports bytes
and refuses to guess tokens.

To learn a bucket's real density in *your* session:

```bash
python3 cc_prune.py density --before 867650 --after 570000 --mb 0.45
# => 1.51 chars/token for that bucket
```

---

## Cutting turns

When most of what is left is conversation, clearing buckets stops paying. `cut` removes a
stretch of whole turns from the middle and keeps the start and the recent end verbatim. A
turn is one prompt and everything up to the next prompt.

```bash
python3 cc_prune.py cut "$F" --list                          # numbered turns, sizes, prompts
python3 cc_prune.py cut "$F" --keep-head 5 --drop 0.5        # preview
python3 cc_prune.py cut "$F" --keep-head 5 --drop 0.5 --yes  # apply
python3 cc_prune.py cut "$F" --from 6 --to 120 --yes         # exact turns, inclusive
```

- `--keep-head N` (default 5) keeps the first N turns. `--drop F` then cuts that fraction of
  the rest, by size, starting right after the head. The first and the last turn always stay.
- The first kept turn is re-linked onto the head. By default the cut turns stay in the
  file as an unreachable branch; `--purge` deletes them. The snapshot holds them either way.
- Both ends of a cut land where no tool call is waiting for its result. `--list` marks
  those turns `ok`.
- Attachments that carry harness state (system prompt and tool schemas, deferred tool,
  MCP, agent and skill listings) are copied over from the cut stretch, so the tail keeps the
  tools it had.
- A tool that ToolSearch loaded inside the cut stretch still works afterwards if the kept
  turns call it: Claude Code rebuilds the loaded set from the history it resumes.
- The first kept prompt gets a note, `[cc-prune: N earlier turns were cut from the
  conversation here]`, so the model knows why it cannot see them. `--no-note` skips it.
  Repeated cuts keep one note with the running total: a note inside the cut stretch is
  added to the new count, and a note already on the first kept prompt is merged.
- Recorded usage is reset to an estimate, as with `clear` ("How the estimate works" under
  Workflow step 4).
  The preview shows it before anything is written.

The model loses everything in the cut turns. Kept turns that refer to cut ones stay as
they are, so the model may notice it is missing something they depend on. Thinking blocks
after the cut no longer match their signatures, so the API drops them, the same as after
any prune; running `clear --drop-all-thinking` after a cut avoids that.

Tested on Claude Code 2.1.296, resuming with Sonnet 5.5 (thinking) and Haiku: synthetic
sessions and copies of real interactive sessions, both modes, a second cut on a cut file,
parallel tool results on either side of the cut and inside it, and a ToolSearch-loaded
tool called after its loading turn was cut. Every resume worked; the model recalled the
kept turns and none of the cut ones, and the next turn was appended to the kept tail.
Without the note the model concluded the cut turns had never happened, so leave it on.

---

## Preventing the problem

Surgery is triage. The durable fixes are upstream:

```bash
export BASH_MAX_OUTPUT_LENGTH=4000     # default is 30000
```

- Pipe long output through `head`/`tail`/`grep`; redirect to a file and read selectively.
- `Grep` to locate, then `Read` with `offset`/`limit`. Avoid whole-file reads.
- Delegate heavy work to subagents — their context is separate, and their thinking never
  enters the main thread. This is the only structural lever against thinking growth.

---

## Commands

```
sessions [--project X ...]     list and filter session transcripts
usage FILE                     real API token counts
inspect FILE                   byte accounting by bucket
clear FILE --results N ...     clear buckets (snapshots first, resets usage)
reset-usage FILE --tokens N    rewrite stale recorded usage so a pruned session resumes
snapshots FILE                 list snapshots
restore FILE N                 restore snapshot N
audit FILE LINE...             is this specific result safe to clear?
strip-commands FILE --orphans  clear /context, /cost output
cut FILE --drop F | --from A --to B   cut a stretch of turns (previews unless --yes)
density --before --after --mb  derive chars/token from a measured cut
```

### `audit`

Before clearing a large result, check whether it matters:

```bash
python3 cc_prune.py audit "$F" 2328 2355
```

Reports which call produced it, whether the source file still exists on disk, whether it
was read again later, how often it's referenced downstream, and what the model said
immediately after. A result whose file is still on disk and never referenced again is
safe to clear; one whose source is gone and is still an active topic is not.

---

## Invariants

Every mutating command validates before replacing the original, and aborts leaving the
file untouched if any of these fail:

- record count and `uuid` / `parentUuid` chains unchanged
- `thinking` / `redacted_thinking` blocks (text and signature) identical, apart from the
  blocks `--drop-failing-thinking` was asked to delete (the check then expects exactly
  those removed)
- every `tool_result.tool_use_id` still has a matching `tool_use`

`cut` changes the chain on purpose, so it checks its own set instead: the conversation a
resume loads is exactly the head, the carried attachments and the tail; no other
`parentUuid` and no thinking block changed; and every kept `tool_result` still has its
`tool_use`.

---

## Contributing

The transcript schema is undocumented. If `inspect` reports a large `unattributed`
share, or `usage` finds no fields, please open an issue with your Claude Code version
and the output of:

```bash
python3 cc_prune.py inspect "$F" | head -30
```

Please redact paths and project names first — transcripts contain your work.

---

## License

MIT