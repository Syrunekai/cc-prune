"""Command line front end: parses arguments, calls the engine and prints."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from . import __version__, settings
from .analysis import audit_lines, density, inspect_file
from .clear import DEFAULT_KEEP_TAIL, ClearOptions, preview_thinking
from .cut import CutOptions
from .errors import PruneError
from .estimate import ESTIMATE_MARGIN
from .guard import check_live
from .maintenance import apply_strip, plan_strip, reset_usage_file
from .prune import Applied, Plan, apply_plan, plan_prune
from .sessions import find_sessions, to_local
from .snapshots import list_snapshots, restore_snapshot
from .transcript import first_text, load
from .turns import Turns
from .usage import usage_rows


def _prog() -> str:
    name = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    return "cc_prune.py" if name == "cc_prune.py" else "cc-prune"


PROG = _prog()


def mb(n: float) -> str:
    return f"{n / 1e6:.2f} MB"


# --------------------------------------------------------------------------
# snapshots and restore
# --------------------------------------------------------------------------

def cmd_snapshots(args) -> None:
    snaps = list_snapshots(args.path)
    if not snaps:
        print("no snapshots yet -- the first `clear` creates snap0 "
              "from the pristine file")
        return
    for p, size in snaps:
        print(f"  {os.path.basename(p):<52} {mb(size)}")
    print(f"\n  current  {os.path.basename(args.path):<52} {mb(os.path.getsize(args.path))}")


def cmd_restore(args) -> None:
    done = restore_snapshot(args.path, args.n, args.force)
    if done.saved:
        print(f"current file saved -> {os.path.basename(done.saved)}")
    print(f"restored {os.path.basename(done.source)} -> {os.path.basename(done.target)}")


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------

def parse_tz(text: str) -> timezone:
    s = text.strip().lower()
    if s in ("utc", "0"):
        return timezone.utc
    try:
        return timezone(timedelta(hours=float(s)))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid timezone '{text}'; use 'utc', '+7', '-4', '+5.5'")


def collapse(text: str | None, n: int) -> str:
    t = " ".join(str(text or "").split())
    return t[: n - 1] + "…" if len(t) > n else t


def human_size(n: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}G"


def print_sessions(rows: list[dict], tz: timezone, want_count: bool) -> None:
    if not rows:
        print("No sessions matched.")
        return
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 120
    width = max(80, width)

    w_start, w_last, w_proj, w_ctx, w_size, w_id = 16, 16, 22, 6, 5, 13
    w_msgs = 5 if want_count else 0
    fixed = w_start + w_last + w_proj + w_ctx + w_size + w_id + w_msgs
    w_about = max(20, width - fixed - 6 - (1 if want_count else 0))

    header = (f"{'STARTED':<{w_start}} {'LAST':<{w_last}} "
              f"{'PROJECT':<{w_proj}} {'CTX':>{w_ctx}} ")
    if want_count:
        header += f"{'MSGS':>{w_msgs}} "
    header += f"{'ABOUT':<{w_about}} {'SIZE':>{w_size}} {'ID':<{w_id}}"
    print(header)
    print("-" * min(width, len(header)))

    home = os.path.expanduser("~")
    for r in rows:
        start, end = to_local(r["start"], tz), to_local(r["end"], tz)
        if end is None:
            last = "?"
        elif start and start.date() == end.date():
            last = end.strftime("%H:%M")
        else:
            last = end.strftime("%Y-%m-%d %H:%M")
        cwd = r["cwd"] or ""
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home):]
        ctx = r["context_tokens"]
        line = (f"{start.strftime('%Y-%m-%d %H:%M') if start else '?':<{w_start}} "
                f"{last:<{w_last}} {collapse(cwd, w_proj):<{w_proj}} "
                f"{f'{ctx / 1000:.0f}k' if ctx is not None else '?':>{w_ctx}} ")
        if want_count:
            line += f"{r['messages'] if r['messages'] is not None else '?':>{w_msgs}} "
        line += (f"{collapse(r['about'], w_about):<{w_about}} "
                 f"{human_size(r['size']):>{w_size}} "
                 f"{r['session_id'][:w_id]:<{w_id}}")
        print(line)


def cmd_sessions(args) -> None:
    def progress(done: int, total: int) -> None:
        print(f"  scanning... {done}/{total}", end="\r", file=sys.stderr)

    found = find_sessions(
        args.root, deep=args.subagents, last=args.last, count=args.count,
        project=args.project, grep=args.grep, since=args.since, until=args.until,
        tz=args.timezone, sort=args.sort, reverse=args.reverse, limit=args.limit,
        show_all=args.all, on_progress=progress)
    if found.scanned >= 100:
        print(" " * 40, end="\r", file=sys.stderr)

    rows, total = found.rows, found.total
    if args.paths:
        for r in rows:
            print(r["path"])
        return
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    print_sessions(rows, args.timezone, args.count)
    note = "" if len(rows) == total else f" (showing {len(rows)}; pass --all or --limit)"
    print(f"\n{total} session(s) matched{note}. "
          f"Timezone: {datetime.now(args.timezone).tzname()}")
    print("CTX = input tokens of the last API request found in the file tail.\n"
          "Pass --paths to print file paths for the other commands.")


# --------------------------------------------------------------------------
# usage, inspect, density
# --------------------------------------------------------------------------

def cmd_usage(args) -> None:
    rows = usage_rows(args.path)

    print(f"{'line':>7}{'context in':>13}{'fresh':>10}"
          f"{'cache_read':>12}{'cache_new':>11}{'out':>8}")
    print("-" * 61)
    for i, ctx, u in rows[-args.n:]:
        print(f"{i:>7}{ctx:>13,}{u.get('input_tokens', 0):>10,}"
              f"{u.get('cache_read_input_tokens', 0):>12,}"
              f"{u.get('cache_creation_input_tokens', 0):>11,}"
              f"{u.get('output_tokens', 0):>8,}")
    print("-" * 61)

    last_i, last, _ = rows[-1]
    peak_i, peak, _ = max(rows, key=lambda t: t[1])
    print(f"\nlast request : {last:>9,} tokens of input   (line {last_i})")
    print(f"peak request : {peak:>9,} tokens of input   (line {peak_i})")
    print("\nThis is what the API actually counted. It is the only trustworthy\n"
          "number. /context recomputes locally and goes stale after you edit\n"
          "the transcript -- do not use it to verify a prune.")


CLEARABLE = {"results": "--results N", "inputs": "--inputs N",
             "attachments": "--attachments N", "images": "--images",
             "transformations": "--transformations"}

NOTES = {
    "thinking": "LOCKED -- signed, cannot be edited. Only /compact touches this.",
    "text": "the conversation itself. No flag; clearing it would be amnesia.",
    "images": "billed by pixels, not characters. Usually ~0 tokens. Skip it.",
    "attachments": "mostly harmless, but prompt_snapshot / deferred_tools_record "
                   "feed the API request and are skipped. Verify before trusting.",
    "transformations": "Claude Code's own log of thinking blocks it dropped from "
                       "requests. Not conversation; may be rewritten on every turn.",
    "results": "tool output. Safest and usually largest win.",
    "inputs": "tool call params (Write/Edit bodies). Real semantic cost.",
}


def cmd_inspect(args) -> None:
    info = inspect_file(args.path)
    total = info.file_bytes

    print(f"file: {mb(total)}, {info.records} records\n")
    print("=== record types ===")
    for t, v in info.by_type.most_common():
        print(f"  {t:<26}{mb(v):>10}{v / total:>7.1%}   x{info.counts[t]}")

    print("\n=== buckets ===")
    for k, v in info.buckets.most_common():
        flag = CLEARABLE.get(k, "")
        print(f"  {k:<14}{mb(v):>10}{v / total:>7.1%}   {flag}")
    for k, v in info.buckets.most_common():
        print(f"\n  {k}: {NOTES.get(k, '')}")

    print("\n" + "-" * 68)
    print("NOTE: sizes are BYTES, not tokens, and deliberately so. Characters\n"
          "per token vary enormously *within one file* -- we measured 1.5 for\n"
          "numeric training logs and 4.0 for prose reasoning in the same\n"
          "session. Any single conversion constant will mislead you.\n"
          "\n"
          "To learn a bucket's real density: note `usage` before, clear that\n"
          "bucket, resume, send a real message, note the status bar, then run\n"
          f"`{PROG} density`.")


def cmd_density(args) -> None:
    freed, per_token = density(args.before, args.after, args.mb)
    print(f"freed {freed:,} tokens by clearing {args.mb:.2f} MB")
    print(f"=> {per_token:.2f} chars/token for that bucket")
    print("\nUse this to project the remaining buckets in THIS session only.\n"
          "It does not transfer to another session, or to another bucket.")


# --------------------------------------------------------------------------
# cut, clear and clearcut: one plan, one apply, one report
# --------------------------------------------------------------------------

RESUME_HINT = ("\nNow: resume the session, send a REAL message (not a slash command),\n"
               "and read the status bar. Do not use /context to verify.")


def print_turns(turns: Turns) -> None:
    recs = turns.recs
    print(f"{'TURN':>5} {'REC':>6} {'TIME':<16} {'CHARS':>9}  CUT  PROMPT")
    for t in range(len(turns)):
        rec = recs[turns.first(t)]
        text = " ".join(first_text(rec).split())
        print(f"{t + 1:>5} {turns.first(t):>6} {str(rec.get('timestamp', ''))[:16]:<16} "
              f"{turns.size[t]:>9,}  {' ok' if turns.clean[t] else '  -'}  {text[:70]}")
    print(f"\n{len(turns)} turns, {sum(turns.size):,} chars. CUT ok: a cut may "
          f"start or end at this turn.")


def print_cut(plan: Plan) -> None:
    cut = plan.cut
    n = len(cut.turns)
    print(f"conversation: {n} turns, {cut.total_chars:,} chars")
    print(f"  keep turns 1-{cut.a}")
    print(f"  cut  turns {cut.a + 1}-{cut.b}  {cut.cut_chars:,} chars "
          f"({cut.cut_chars / cut.total_chars:.0%} of all), {cut.cut_records} records")
    if cut.earlier:
        print(f"         (plus {cut.earlier} turns earlier cuts already removed; "
              f"the note will say {cut.b - cut.a + cut.earlier})")
    print(f"         first cut:  {cut.first_cut}")
    print(f"         first kept: {cut.first_kept}")
    if cut.moved_from:
        print(f"         (asked for {cut.moved_from[0]}-{cut.moved_from[1]}; moved to the "
              f"nearest turns marked ok in --list)")
    print(f"  keep turns {cut.b + 1}-{n}")
    if cut.carried:
        print("  carried over: " + ", ".join(f"{k} x{v}" for k, v in cut.carried.items()))
    print("  mode: " + ("purge (deleted from the file)" if cut.purge
                        else "branch (stays in the file, unreachable)"))


def print_clear(plan: Plan, indent: str = "") -> None:
    out = plan.clear
    if not out.freed:
        lead = indent + "  " if indent else ""
        print(f"{lead}nothing matched -- lower the threshold, or this bucket is empty")
    for k in out.freed:
        print(f"{indent}  {k:<14}{out.hits[k]:>6} hits   {mb(out.freed[k])} removed")


def print_context(plan: Plan) -> None:
    if plan.estimate:
        print(f"  context: last request {plan.last_context:,} -> ~{plan.estimate:,} tokens "
              f"(estimate, kept {ESTIMATE_MARGIN:.1%} under)")


def print_applied(plan: Plan, applied: Applied) -> None:
    print(f"snapshot -> {os.path.basename(applied.snapshot_path)}")
    if applied.reset:
        print(f"usage reset: last request {applied.reset.last:,} -> "
              f"~{applied.reset.estimate:,} tokens on {applied.reset.records} records "
              f"(the next real reply corrects it)")
    print(f"\nsize {mb(plan.size_before)} -> {mb(applied.size_after)}")
    print(f"rollback: {PROG} restore {os.path.basename(plan.path)} {applied.snapshot}")
    print(RESUME_HINT)


def cmd_cut(args) -> None:
    if args.list:
        print_turns(Turns(load(args.path)))
        return
    opts = CutOptions(drop=args.drop, keep_head=args.keep_head, from_turn=args.from_turn,
                      to_turn=args.to_turn, purge=args.purge, note=not args.no_note)
    plan = plan_prune(args.path, cut=opts, reset_usage=not args.no_reset_usage)
    print_cut(plan)
    print_context(plan)
    if not args.yes:
        print("\nPREVIEW, nothing written. Add --yes to apply.")
        return

    applied = apply_plan(plan, force=args.force)
    print()
    cut = plan.cut
    print(f"snapshot -> {os.path.basename(applied.snapshot_path)}")
    print(f"cut turns {cut.a + 1}-{cut.b} ({cut.b - cut.a} turns), "
          + ("purge (deleted from the file)" if cut.purge
             else "branch (stays in the file, unreachable)"))
    if cut.copies:
        print(f"carried over {len(cut.copies)} state attachments")
    if applied.reset:
        print(f"usage reset: last request {applied.reset.last:,} -> "
              f"~{applied.reset.estimate:,} tokens on {applied.reset.records} records "
              f"(the next real reply corrects it)")
    print(f"\nsize {mb(plan.size_before)} -> {mb(applied.size_after)}")
    print(f"rollback: {PROG} restore {os.path.basename(plan.path)} {applied.snapshot}")
    print(RESUME_HINT)


def clear_options(args) -> ClearOptions:
    return ClearOptions(
        results=args.results, inputs=args.inputs, commands=args.commands,
        attachments=args.attachments, images=args.images,
        transformations=args.transformations,
        drop_failing_thinking=args.drop_failing_thinking,
        drop_all_thinking=args.drop_all_thinking, keep_tail=args.keep_tail)


def cmd_clear(args) -> None:
    opts = clear_options(args)
    opts.check()

    if opts.drops_thinking and not args.yes:
        drop = preview_thinking(args.path, opts)
        if drop is None:
            raise PruneError("no thinking blocks to delete in this file")
        flag = "--drop-all-thinking" if args.drop_all_thinking else "--drop-failing-thinking"
        print(f"PREVIEW, nothing written. {flag} would delete "
              f"{drop.deleted} of {drop.total} thinking blocks\n"
              f"  records {drop.first_record}..{drop.last_record}, "
              f"models: {', '.join(drop.models)}\n"
              f"  {drop.total - drop.deleted} thinking blocks stay "
              f"({drop.newer} of them newer)\n"
              f"Add --yes to apply (every other flag you passed is also held back).")
        return

    check_live(args.path, args.force)
    plan = plan_prune(args.path, clear=opts, reset_usage=not args.no_reset_usage)
    applied = apply_plan(plan, force=args.force)

    print(f"snapshot -> {os.path.basename(applied.snapshot_path)}")
    out = plan.clear
    print(f"protected the last {out.protected} of {out.total} records "
          f"(--keep-tail {args.keep_tail})")
    print_clear(plan)
    if applied.reset:
        print(f"\nusage reset: last request {applied.reset.last:,} -> "
              f"~{applied.reset.estimate:,} tokens on {applied.reset.records} records "
              f"(estimate, kept {ESTIMATE_MARGIN:.1%} under; the next real reply corrects it)")
    print(f"\nsize {mb(plan.size_before)} -> {mb(applied.size_after)}")
    print(f"rollback: {PROG} restore {os.path.basename(plan.path)} {applied.snapshot}")
    print(RESUME_HINT)


def cmd_clearcut(args) -> None:
    clear = ClearOptions(
        results=args.results, inputs=args.inputs, commands=args.commands,
        attachments=args.attachments, images=not args.no_images,
        transformations=args.transformations,
        drop_failing_thinking=args.drop_failing_thinking,
        drop_all_thinking=args.drop_all_thinking,
        keep_tail=DEFAULT_KEEP_TAIL if args.keep_tail is None else args.keep_tail)
    cut = CutOptions(drop=args.drop, keep_head=args.keep_head, purge=not args.branch,
                     note=not args.no_note)
    plan = plan_prune(args.path, cut=cut, clear=clear, reset_usage=not args.no_reset_usage)

    print_cut(plan)
    out = plan.clear
    print(f"  clear: protecting the last {out.protected} of {out.total} records "
          f"(--keep-tail {clear.keep_tail})")
    print_clear(plan, indent="  ")
    print_context(plan)
    if not args.yes:
        print("\nPREVIEW, nothing written. Add --yes to apply.")
        return

    applied = apply_plan(plan, force=args.force)
    print()
    print_applied(plan, applied)


# --------------------------------------------------------------------------
# reset-usage, audit, strip-commands
# --------------------------------------------------------------------------

def cmd_reset_usage(args) -> None:
    done = reset_usage_file(args.path, args.tokens, args.force)
    print(f"snapshot -> {os.path.basename(done.snapshot_path)}")
    for i, old, new in done.changed:
        print(f"  line {i}: {old:,} -> {new:,}")
    print("\nOnly the recorded usage changed. The next real reply replaces it "
          "with the API's own count.")
    print(f"rollback: {PROG} restore {os.path.basename(args.path)} {done.snapshot}")


def cmd_audit(args) -> None:
    for e in audit_lines(args.path, args.lines):
        if e.status == "out_of_range":
            print(f"\n### line {e.line}: out of range ({e.total_records} records)")
            continue
        if e.status == "no_result":
            print(f"\n### line {e.line}: no tool_result here")
            continue

        print("=" * 70)
        print(f"### line {e.line} | {e.chars:,} chars | {e.tool}"
              + (" | IMAGE" if e.image else ""))
        print(f"    call at line {e.call_line}: "
              f"{json.dumps(e.params, ensure_ascii=False)[:240]}")

        if e.source:
            print(f"\n[disk]    {e.source}\n          exists: {e.exists}"
                  + (f"  ({e.disk_bytes:,} B)" if e.exists else ""))
            print(f"[reread]  read again at: {e.rereads or 'never'}")
            print(f"[mention] '{e.basename}' in {len(e.mentions)} later records"
                  + (f", e.g. {e.mentions[:8]}" if e.mentions else ""))
        else:
            print(f"\n[disk]    not a file read"
                  + (f"; command: {str(e.command)[:150]}" if e.command else ""))

        if e.said_line is not None:
            print(f"\n[after]   line {e.said_line}: {e.said}")
        print(f"\n[head]    {e.head!r}")
        print(f"\n>> {' | '.join(e.verdict)}")
    print("=" * 70)


def cmd_strip_commands(args) -> None:
    plan = plan_strip(args.path, args.commands, args.orphans)
    if not plan.hits:
        print("no local-command-stdout blocks matched.")
        print("try --orphans, or check: grep -c 'local-command-stdout' FILE")
        return

    if args.dry_run:
        for k, v in plan.chars.most_common():
            print(f"  /{k:<16}{v:>10,} chars")
        print(f"\n  {len(plan.hits)} blocks, {sum(plan.chars.values()):,} chars total")
        print("  (drop --dry-run to strip)")
        return

    done = apply_strip(plan, args.keep_last, args.force)
    print(f"snapshot -> {os.path.basename(done.snapshot_path)}")
    print(f"stripped {done.count} stdout blocks, {done.freed:,} chars")
    print(f"kept the last {args.keep_last} of each command")
    print(f"rollback: {PROG} restore {os.path.basename(args.path)} {done.snapshot}")


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        description="Surgical context recovery for Claude Code sessions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Always confirm `pgrep -af claude` is empty before mutating "
               "a transcript.")
    p.add_argument("--version", action="version", version=f"cc-prune {__version__}")
    sub = p.add_subparsers(dest="cmd")

    se = sub.add_parser("sessions", help="list and filter session transcripts")
    se.add_argument("--root", default=settings.PROJECTS_ROOT, metavar="PATH",
                    help=f"directory or file to scan (default {settings.PROJECTS_ROOT})")
    se.add_argument("--project", metavar="SUBSTR",
                    help="only sessions whose cwd contains SUBSTR (case-insensitive)")
    se.add_argument("--grep", metavar="SUBSTR",
                    help="only sessions whose about/title/cwd/id contains SUBSTR")
    se.add_argument("--since", metavar="YYYY-MM-DD")
    se.add_argument("--until", metavar="YYYY-MM-DD")
    se.add_argument("--last", action="store_true",
                    help="preview the last user message instead of the first")
    se.add_argument("--count", action="store_true",
                    help="true message counts (full scan of each file; slower)")
    se.add_argument("--subagents", action="store_true",
                    help="also scan nested files (subagent logs, backup dirs)")
    se.add_argument("--sort", choices=["date", "size", "project", "ctx"],
                    default="date")
    se.add_argument("--reverse", action="store_true")
    se.add_argument("--limit", type=int, default=50, metavar="N")
    se.add_argument("--all", action="store_true",
                    help="show all matches (overrides --limit)")
    se.add_argument("--timezone", type=parse_tz, default=timezone.utc,
                    metavar="OFFSET", help="'utc', '+7', '-4' (default utc)")
    se.add_argument("--json", action="store_true")
    se.add_argument("--paths", action="store_true",
                    help="print only file paths")

    u = sub.add_parser("usage", help="real API token counts (source of truth)")
    u.add_argument("path")
    u.add_argument("-n", type=int, default=12, help="rows to show")

    i = sub.add_parser("inspect", help="byte accounting by bucket")
    i.add_argument("path")

    c = sub.add_parser("clear", help="clear a bucket (snapshots first)")
    c.add_argument("path")
    c.add_argument("--results", type=int, default=0, metavar="N")
    c.add_argument("--inputs", type=int, default=0, metavar="N")
    c.add_argument("--commands", type=int, default=0, metavar="N",
                   help="clear Bash `command` values longer than N chars "
                        "(--inputs never touches them)")
    c.add_argument("--attachments", type=int, default=0, metavar="N")
    c.add_argument("--images", action="store_true")
    c.add_argument("--drop-failing-thinking", action="store_true",
                   help="EXPERIMENTAL: delete the thinking blocks the API "
                        "reported dropping (prefix_binding_mismatch). Previews "
                        "only unless --yes is also given; refuses layouts it "
                        "has not been checked against; ignores --keep-tail")
    c.add_argument("--drop-all-thinking", action="store_true",
                   help="EXPERIMENTAL: delete every thinking block, including "
                        "ones this prune is about to invalidate. Records stay "
                        "with empty content. Previews only unless --yes is "
                        "also given; ignores --keep-tail")
    c.add_argument("--yes", action="store_true",
                   help="apply --drop-failing-thinking / --drop-all-thinking "
                        "instead of previewing")
    c.add_argument("--force", action="store_true",
                   help="edit a transcript even if a running session has it open")
    c.add_argument("--transformations", action="store_true",
                   help="delete the input_transformations log Claude Code "
                        "writes on assistant records (experimental)")
    c.add_argument("--keep-tail", type=float, default=DEFAULT_KEEP_TAIL, metavar="F",
                   help="fraction of most recent records left untouched "
                        f"(default {DEFAULT_KEEP_TAIL})")
    c.add_argument("--no-reset-usage", action="store_true",
                   help="leave the recorded usage alone (a pruned session "
                        "near its limit may then refuse to resume)")

    ru = sub.add_parser("reset-usage", help="rewrite stale recorded usage so a "
                                            "pruned session can resume")
    ru.add_argument("path")
    ru.add_argument("--tokens", type=int, required=True, metavar="N",
                    help="context size to record for the last requests")
    ru.add_argument("--force", action="store_true",
                    help="edit a transcript even if a running session has it open")

    s = sub.add_parser("snapshots", help="list snapshots")
    s.add_argument("path")

    r = sub.add_parser("restore", help="restore a snapshot")
    r.add_argument("path")
    r.add_argument("n", type=int)
    r.add_argument("--force", action="store_true",
                   help="edit a transcript even if a running session has it open")

    a = sub.add_parser("audit", help="is a specific tool_result safe to clear?")
    a.add_argument("path")
    a.add_argument("lines", type=int, nargs="+")

    sc = sub.add_parser("strip-commands", help="clear /context, /cost ... output")
    sc.add_argument("path")
    sc.add_argument("commands", nargs="*", default=[])
    sc.add_argument("--orphans", action="store_true",
                    help="strip every stdout block regardless of command name")
    sc.add_argument("--keep-last", type=int, default=1)
    sc.add_argument("--dry-run", action="store_true")
    sc.add_argument("--force", action="store_true",
                    help="edit a transcript even if a running session has it open")

    ct = sub.add_parser("cut", help="cut a stretch of turns out of the middle "
                                    "(previews unless --yes)")
    ct.add_argument("path")
    ct.add_argument("--list", action="store_true",
                    help="list the turns of the active conversation and exit")
    ct.add_argument("--keep-head", type=int, default=5, metavar="N",
                    help="turns at the start that always stay (default 5)")
    ct.add_argument("--drop", type=float, default=0, metavar="F",
                    help="cut this fraction of the conversation after the head, "
                         "by size, starting right after it")
    ct.add_argument("--from", dest="from_turn", type=int, default=0, metavar="A",
                    help="first turn to cut (turn numbers from --list)")
    ct.add_argument("--to", dest="to_turn", type=int, default=0, metavar="B",
                    help="last turn to cut, inclusive")
    ct.add_argument("--purge", action="store_true",
                    help="delete the cut records from the file instead of "
                         "leaving them as an unreachable branch")
    ct.add_argument("--no-note", action="store_true",
                    help="do not mark the cut in the first kept prompt")
    ct.add_argument("--yes", action="store_true", help="apply instead of previewing")
    ct.add_argument("--force", action="store_true",
                    help="edit a transcript even if a running session has it open")
    ct.add_argument("--no-reset-usage", action="store_true",
                    help="leave the recorded usage alone")

    cc = sub.add_parser("clearcut", help="cut, then clear, in one pass with one "
                                         "snapshot (previews unless --yes)")
    cc.add_argument("path")
    cc.add_argument("--drop", type=float, required=True, metavar="F",
                    help="cut this fraction of the conversation after the head")
    cc.add_argument("--keep-head", type=int, default=1, metavar="N",
                    help="turns at the start that always stay (default 1)")
    cc.add_argument("--keep-tail", type=float, default=None, metavar="F",
                    help="fraction of most recent records the clear leaves "
                         f"untouched (default: clear's own, {DEFAULT_KEEP_TAIL})")
    cc.add_argument("--results", type=int, default=1, metavar="N",
                    help="clear tool results longer than N chars (default 1)")
    cc.add_argument("--inputs", type=int, default=1, metavar="N",
                    help="clear tool inputs longer than N chars (default 1)")
    cc.add_argument("--no-images", action="store_true",
                    help="leave images alone")
    cc.add_argument("--commands", type=int, default=0, metavar="N",
                    help="also clear Bash `command` values longer than N chars")
    cc.add_argument("--attachments", type=int, default=0, metavar="N")
    cc.add_argument("--transformations", action="store_true",
                    help="also delete the input_transformations log (experimental)")
    cc.add_argument("--drop-failing-thinking", action="store_true",
                    help="EXPERIMENTAL, see `clear -h`")
    cc.add_argument("--drop-all-thinking", action="store_true",
                    help="EXPERIMENTAL, see `clear -h`")
    cc.add_argument("--branch", action="store_true",
                    help="leave the cut records as an unreachable branch instead "
                         "of purging them")
    cc.add_argument("--no-note", action="store_true",
                    help="do not mark the cut in the first kept prompt")
    cc.add_argument("--no-reset-usage", action="store_true",
                    help="leave the recorded usage alone")
    cc.add_argument("--yes", action="store_true", help="apply instead of previewing")
    cc.add_argument("--force", action="store_true",
                    help="edit a transcript even if a running session has it open")

    d = sub.add_parser("density", help="derive chars/token from a measured cut")
    d.add_argument("--before", type=int, required=True)
    d.add_argument("--after", type=int, required=True)
    d.add_argument("--mb", type=float, required=True)
    return p


COMMANDS = {
    "sessions": cmd_sessions, "usage": cmd_usage, "inspect": cmd_inspect,
    "clear": cmd_clear, "reset-usage": cmd_reset_usage, "snapshots": cmd_snapshots,
    "restore": cmd_restore, "audit": cmd_audit, "strip-commands": cmd_strip_commands,
    "cut": cmd_cut, "clearcut": cmd_clearcut, "density": cmd_density,
}


def main(argv: list[str] | None = None) -> None:
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # allow `| head`
    except (ImportError, AttributeError, ValueError):
        pass                                            # not POSIX; ignore

    p = build_parser()
    args = p.parse_args(argv)
    if args.cmd is None:
        print(f"cc-prune {__version__}: surgical context recovery for "
              f"Claude Code sessions\n\n"
              "Start here:\n"
              f"  {PROG + ' sessions':<42}list your sessions, newest first\n"
              f"  {PROG + ' sessions --paths --limit 1':<42}print the newest transcript path\n"
              f"  {PROG + ' inspect FILE':<42}see where the bytes are\n"
              f"  {PROG + ' clear FILE --results 500':<42}clear tool output (snapshots first)\n"
              f"  {PROG + ' restore FILE 0':<42}roll back to snapshot 0\n\n"
              f"Run `{PROG} COMMAND -h` for the options of a command.\n")
        p.print_help()
        return
    try:
        COMMANDS[args.cmd](args)
    except PruneError as exc:
        sys.exit(str(exc))
