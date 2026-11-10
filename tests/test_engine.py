from __future__ import annotations

import json
import os
import re
import shutil

import pytest

import ccprune
from ccprune import settings
from ccprune.clear import ClearOptions
from ccprune.cli import main
from ccprune.cut import CutOptions
from ccprune.errors import PruneError
from ccprune.guard import check_live
from ccprune.prune import apply_plan, plan_prune
from ccprune.snapshots import restore_snapshot, snapshot_paths
from ccprune.transcript import PLACEHOLDER, load

CLEAR = ClearOptions(results=1, inputs=1, images=True, keep_tail=0.25)
CUT = CutOptions(drop=0.5, keep_head=1, purge=True)


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def without_usage(path):
    """The file's records with recorded usage removed, for comparing runs that
    scale it in different steps."""
    out = []
    for r in load(path):
        r.pop("ccPruneEstimate", None)
        (r.get("message") or {}).pop("usage", None)
        out.append(r)
    return out


def test_version_matches_pyproject():
    toml = open(os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")).read()
    assert re.search(r'^version = "([^"]+)"', toml, re.M).group(1) == ccprune.__version__


def test_clear_counts_and_writes(transcript):
    plan = plan_prune(transcript, clear=ClearOptions(results=1, inputs=1, keep_tail=0))
    assert plan.clear.hits["results"] == 8
    assert plan.clear.hits["inputs"] == 8
    applied = apply_plan(plan)
    assert applied.snapshot == 0 and len(snapshot_paths(transcript)) == 1
    results = [b["content"] for r in load(transcript)
               for b in (r.get("message") or {}).get("content", []) if isinstance(b, dict)
               and b.get("type") == "tool_result"]
    assert results == [PLACEHOLDER] * 8


def test_plan_writes_nothing(transcript):
    before = read(transcript)
    plan_prune(transcript, cut=CUT, clear=CLEAR)
    assert read(transcript) == before
    assert snapshot_paths(transcript) == []


def test_clearcut_is_one_pass_with_one_snapshot(transcript, tmp_path):
    two_step = tmp_path / "two.jsonl"
    shutil.copy(transcript, two_step)
    two_step = str(two_step)

    apply_plan(plan_prune(two_step, cut=CUT))
    apply_plan(plan_prune(two_step, clear=CLEAR))
    apply_plan(plan_prune(transcript, cut=CUT, clear=CLEAR))

    assert len(snapshot_paths(two_step)) == 2
    assert len(snapshot_paths(transcript)) == 1
    assert without_usage(transcript) == without_usage(two_step)


def test_clearcut_unified_clear_runs_after_the_cut(transcript):
    plan = plan_prune(transcript, cut=CUT, clear=CLEAR)
    cut_records = len(plan.new)
    assert plan.clear.total == cut_records
    assert plan.clear.protected == cut_records - int(cut_records * (1 - CLEAR.keep_tail))


def test_validation_failure_keeps_the_file_and_takes_no_snapshot(transcript):
    before = read(transcript)
    plan = plan_prune(transcript, cut=CUT, clear=CLEAR)
    plan.check = lambda loaded: "forced problem"
    with pytest.raises(PruneError, match="forced problem"):
        apply_plan(plan)
    assert read(transcript) == before
    assert snapshot_paths(transcript) == []
    assert not os.path.exists(transcript + ".tmp")


def test_restore_returns_the_original(transcript):
    before = read(transcript)
    apply_plan(plan_prune(transcript, cut=CUT, clear=CLEAR))
    assert read(transcript) != before
    restore_snapshot(transcript, 0)
    assert read(transcript) == before


def test_live_guard(transcript, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "SESSIONS_DIR", str(tmp_path / "sessions"))
    with pytest.raises(PruneError, match="looks live"):
        check_live(transcript)
    check_live(transcript, force=True)
    os.utime(transcript, (1, 1))
    check_live(transcript)


def test_option_checks(transcript):
    with pytest.raises(PruneError, match="pick at least one bucket"):
        plan_prune(transcript, clear=ClearOptions())
    with pytest.raises(PruneError, match="pick --drop"):
        plan_prune(transcript, cut=CutOptions())
    with pytest.raises(PruneError, match="alternatives"):
        plan_prune(transcript, clear=ClearOptions(drop_all_thinking=True,
                                                  drop_failing_thinking=True))


def test_cli_clearcut_previews_then_applies_in_one_report(transcript, capsys):
    main(["clearcut", transcript, "--drop", "0.5", "--keep-tail", "0.25"])
    preview = capsys.readouterr().out
    assert "PREVIEW, nothing written" in preview and "snapshot ->" not in preview
    assert "clear: protecting the last" in preview

    os.utime(transcript, (1, 1))
    main(["clearcut", transcript, "--drop", "0.5", "--keep-tail", "0.25", "--yes"])
    out = capsys.readouterr().out
    assert out.count("snapshot ->") == 1
    assert out.count("size ") == 1
    assert out.count("rollback:") == 1
    assert json.loads(open(transcript).readline())


def test_cli_exits_with_the_message_on_an_error(transcript):
    with pytest.raises(SystemExit) as exc:
        main(["clear", transcript])
    assert "pick at least one bucket" in str(exc.value)
