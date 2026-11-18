"""Markdown export of a session."""
from __future__ import annotations

import json
import os

import pytest

from ccprune.cut import CutOptions
from ccprune.errors import PruneError
from ccprune.export import (ExportOptions, conversation, default_filename, export_session,
                            read_transcript, render, resolve_destination, write_export)
from ccprune.prune import apply_plan, plan_prune
from ccprune.transcript import load

UTC = ExportOptions(timezone="utc")


def save(path, recs):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in recs) + "\n")


def test_exports_the_conversation_text_only_by_default(transcript):
    t = read_transcript(transcript, UTC)
    assert len(t.messages) == 16                       # 8 prompts and 8 closing replies
    assert [m.speaker for m in t.messages[:2]] == ["Operator", "Machine"]
    assert t.messages[0].text.startswith("prompt 0 ")
    md = render(t, UTC)
    assert md.startswith("# Session Transcript\n")
    assert "**Total messages:** 16" in md and "r" * 50 not in md and "[Tool" not in md
    assert "### Operator — 2026-01-01 00:00:01 UTC" in md


def test_raw_adds_tool_calls_and_results_under_their_own_speaker(transcript):
    t = read_transcript(transcript, ExportOptions(raw=True, timezone="utc"))
    speakers = [m.speaker for m in t.messages[:4]]
    assert speakers == ["Operator", "Machine", "Tool", "Machine"]
    assert t.messages[1].text.startswith("[Tool: Read]")
    assert t.messages[2].text.startswith("[Tool Result]") and len(t.messages[2].text) < 2100


def test_head_and_tail(transcript):
    t = read_transcript(transcript, ExportOptions(tail=3, timezone="utc"))
    assert len(t.shown) == 3 and t.shown[-1] is t.messages[-1]
    assert t.trim_note == "last 3 of 16 messages"
    assert "**Showing:** last 3 of 16 messages" in render(t)
    t = read_transcript(transcript, ExportOptions(head=2, timezone="utc"))
    assert t.trim_note == "first 2 of 16 messages"
    assert read_transcript(transcript, ExportOptions(tail=99)).trim_note == ""
    with pytest.raises(PruneError):
        ExportOptions(head=1, tail=1).check()


def test_records_a_branch_cut_left_behind_are_not_exported(transcript):
    before = len(read_transcript(transcript, UTC).messages)
    apply_plan(plan_prune(transcript, cut=CutOptions(drop=0.5, keep_head=1, purge=False, note=False)))
    recs = load(transcript)
    assert len(recs) > len(conversation(recs)[0])      # the cut records are still in the file
    t = read_transcript(transcript, UTC)
    assert len(t.messages) < before
    assert all(m.text != "prompt 2 " + "p" * 200 for m in t.messages)


def test_rewound_prompts_and_sidechains_are_not_exported(transcript):
    recs = load(transcript)
    recs.insert(4, dict(recs[4], uuid="rewound", parentUuid=recs[3]["uuid"],
                        message={"role": "user", "content": "a prompt that was rewound"}))
    recs.append(dict(recs[0], uuid="side", parentUuid=None, isSidechain=True,
                     message={"role": "user", "content": "subagent prompt"}))
    recs.insert(len(recs) - 1, dict(recs[-2]))        # keep a main-thread record last but one
    save(transcript, recs)
    texts = [m.text for m in read_transcript(transcript, UTC).messages]
    assert "a prompt that was rewound" not in texts and "subagent prompt" not in texts


def compacted(transcript, k: int = 8):
    """Compact the synthetic transcript before record k: a boundary, a summary,
    and record k re-parented onto the summary."""
    recs = load(transcript)
    boundary = {"type": "system", "subtype": "compact_boundary", "uuid": "boundary",
                "parentUuid": None, "logicalParentUuid": recs[k - 1]["uuid"],
                "timestamp": recs[k]["timestamp"]}
    summary = {"type": "user", "uuid": "summary", "parentUuid": "boundary", "isCompactSummary": True,
               "timestamp": recs[k]["timestamp"],
               "message": {"role": "user", "content": "This session is being continued..."}}
    recs[k]["parentUuid"] = "summary"
    recs[k:k] = [boundary, summary]
    save(transcript, recs)


def test_by_default_only_the_latest_compaction_with_its_summary(transcript):
    compacted(transcript)
    t = read_transcript(transcript, UTC)
    assert not t.history and t.compactions == 1
    assert len(t.messages) == 13                       # the summary and 12 messages after it
    assert t.messages[0].speaker == "Compaction summary"
    assert t.messages[0].text.startswith("This session is being continued")
    assert t.messages[1].text.startswith("prompt 2 ") and not t.messages[1].compacted_before
    md = render(t, UTC)
    assert "**Compactions:** 1, this transcript starts at the latest one, with its summary" in md
    assert "### Compaction summary — " in md and "compacted here" not in md


def test_history_follows_compactions_marks_them_and_leaves_the_summary_out(transcript):
    compacted(transcript)
    full = read_transcript(transcript, ExportOptions(before_compaction=True, timezone="utc"))
    assert full.history and full.compactions == 1 and len(full.messages) == 16
    assert not any("being continued" in m.text for m in full.messages)
    assert not any(m.speaker == "Compaction summary" for m in full.messages)
    marked = [m for m in full.messages if m.compacted_before]
    assert len(marked) == 1 and marked[0].text.startswith("prompt 2 ")
    md = render(full)
    assert "the conversation was compacted here" in md and "**Compactions:** 1, marked below" in md


def test_only_the_latest_of_several_compactions_is_kept(transcript):
    compacted(transcript, 8)
    recs = load(transcript)
    k = next(i for i, r in enumerate(recs)
             if isinstance((r.get("message") or {}).get("content"), list)
             and r["message"]["content"][0].get("text", "").startswith("prompt 5 "))
    recs[k:k] = [{"type": "system", "subtype": "compact_boundary", "uuid": "b2", "parentUuid": None,
                  "logicalParentUuid": recs[k - 1]["uuid"], "timestamp": recs[k]["timestamp"]},
                 {"type": "user", "uuid": "s2", "parentUuid": "b2", "isCompactSummary": True,
                  "timestamp": recs[k]["timestamp"],
                  "message": {"role": "user", "content": "second summary"}}]
    recs[k + 2]["parentUuid"] = "s2"
    save(transcript, recs)
    t = read_transcript(transcript, UTC)
    assert t.compactions == 2 and [m.text for m in t.messages[:1]] == ["second summary"]
    assert t.messages[1].text.startswith("prompt 5 ")
    full = read_transcript(transcript, ExportOptions(before_compaction=True))
    assert full.compactions == 2 and len(full.messages) == 16
    assert sum(m.compacted_before for m in full.messages) == 2


def test_without_compactions_both_modes_are_the_same(transcript):
    a = read_transcript(transcript, UTC)
    b = read_transcript(transcript, ExportOptions(before_compaction=True, timezone="utc"))
    assert [m.text for m in a.messages] == [m.text for m in b.messages]
    assert "**Compactions:**" not in render(a)


def test_file_name_destination_and_overwrite(transcript, tmp_path):
    t = read_transcript(transcript, UTC)
    assert default_filename(t) == "260101-0000-t.md"
    title = tmp_path / "t"
    title.mkdir()
    (title / "custom-title.json").write_text(json.dumps({"customTitle": "My Session: Notes!"}))
    t = read_transcript(transcript, UTC)
    assert t.title == "My Session: Notes!" and default_filename(t) == "260101-0000-my-session-notes.md"
    out = tmp_path / "out"
    assert resolve_destination(t, str(out) + os.sep) == str(out / "260101-0000-my-session-notes.md")
    assert resolve_destination(t, str(tmp_path / "x")) == str(tmp_path / "x.md")

    done = export_session(transcript, str(out) + os.sep, UTC)
    text = open(done.path, encoding="utf-8").read()
    assert text.startswith("# My Session: Notes!\n") and done.messages == 16
    with pytest.raises(PruneError, match="already exists"):
        export_session(transcript, str(out) + os.sep, UTC)
    assert export_session(transcript, str(out) + os.sep, UTC, overwrite=True).path == done.path
    assert not [p for p in os.listdir(out) if p.endswith(".tmp")]


def test_export_never_changes_the_transcript(transcript, tmp_path):
    before = open(transcript, "rb").read()
    write_export(read_transcript(transcript, ExportOptions(raw=True)), str(tmp_path / "o.md"))
    assert open(transcript, "rb").read() == before


def test_timezones_and_labels(transcript):
    t = read_transcript(transcript, ExportOptions(timezone="+2", user_name="Me",
                                                  assistant_name="Claude"))
    md = render(t, ExportOptions(title="Heading"))
    assert "### Me — 2026-01-01 02:00:01 UTC+2" in md and "### Claude — " in md
    assert md.startswith("# Heading\n")
    with pytest.raises(PruneError, match="unknown timezone"):
        read_transcript(transcript, ExportOptions(timezone="mars"))
    assert read_transcript(transcript, ExportOptions(timezone="-5.5")).messages[0].time.utcoffset() \
        .total_seconds() == -5.5 * 3600


def test_render_limit_cuts_the_preview_short(transcript):
    t = read_transcript(transcript, UTC)
    short = render(t, UTC, limit=600)
    assert len(short) < len(render(t, UTC)) and "more messages" in short


def test_the_summary_can_be_left_out(transcript):
    compacted(transcript)
    t = read_transcript(transcript, ExportOptions(summary=False, timezone="utc"))
    assert not t.summary and len(t.messages) == 12
    assert t.messages[0].text.startswith("prompt 2 ") and not t.messages[0].compacted_before
    md = render(t)
    assert "being continued" not in md and "### Compaction summary" not in md
    assert "**Compactions:** 1, this transcript starts at the latest one\n" in md
    full = read_transcript(transcript, ExportOptions(before_compaction=True))
    assert not full.summary and not any("being continued" in m.text for m in full.messages)


def test_history_can_include_each_summary_after_its_marker(transcript):
    compacted(transcript)
    t = read_transcript(transcript, ExportOptions(before_compaction=True, summary=True, timezone="utc"))
    assert t.summary and len(t.messages) == 17
    k = next(i for i, m in enumerate(t.messages) if m.speaker == "Compaction summary")
    assert t.messages[k].compacted_before and not t.messages[k + 1].compacted_before
    assert t.messages[k + 1].text.startswith("prompt 2 ")
    md = render(t)
    assert md.index("compacted here") < md.index("### Compaction summary")
    assert "**Compactions:** 1, marked below, each with its summary" in md
