# cc-prune

Surgical context recovery for Claude Code sessions.

When a session approaches its context limit, the usual answer is `/compact` — which
summarizes everything, including the reasoning you may still want. `cc-prune` takes a
narrower cut: it clears **tool output that Claude has already digested** from the
session transcript, leaving the conversation, the reasoning, and the structure intact.

Nothing is summarized. No line is deleted. Thinking blocks are never touched.

```
86.8% full  ──[ clear --results ]──►  57% full
```

That is a real measurement from one session. Your mileage will vary a lot — see
[What to expect](#what-to-expect).

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
cryptographically signed and cannot be edited without the API rejecting the turn.
`inspect` will tell you which situation you are in.

---

## Install

```bash
git clone https://github.com/QihanZhao/cc-prune
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
directory is keyed on the working directory the session started in, not the project:

```bash
F=$(find ~/.claude/projects -name '<session-id>.jsonl' | head -1)
```

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
| `attachments` | `--attachments N` | did not reach the API in our one test. Verify before trusting. |
| `images` | `--images` | billed by pixels, not characters. ~0 tokens. Skip. |
| `thinking` | — | **locked.** Signed. Only `/compact` touches this. |
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
usage FILE                     real API token counts
inspect FILE                   byte accounting by bucket
clear FILE --results N ...     clear buckets (snapshots first)
snapshots FILE                 list snapshots
restore FILE N                 restore snapshot N
audit FILE LINE...             is this specific result safe to clear?
strip-commands FILE --orphans  clear /context, /cost output
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
- `thinking` / `redacted_thinking` signatures byte-identical
- every `tool_result.tool_use_id` still has a matching `tool_use`

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