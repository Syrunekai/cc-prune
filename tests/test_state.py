"""Favorites, labeled and pinned snapshots, the snapshot limit, no-op clears,
the stale-file guard, and the estimate log."""
from __future__ import annotations

import json
import os
import time

import pytest

from ccprune import calibration, favorites, settings
from ccprune.clear import ClearOptions
from ccprune.cut import CutOptions
from ccprune.errors import PruneError
from ccprune.prune import apply_plan, load_transcript, plan_prune
from ccprune.snapshots import (delete_snapshot, enforce_limit, list_snapshots, set_pinned,
                               snapshots_over_limit, take_snapshot)
from ccprune.transcript import load

CUT = CutOptions(drop=0.5, keep_head=1, purge=True)
CLEAR = ClearOptions(results=1, inputs=1, images=True, keep_tail=0.25)


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FAVORITES_FILE", str(tmp_path / "cfg" / "favorites.json"))
    monkeypatch.setattr(settings, "ESTIMATE_LOG", str(tmp_path / "data" / "estimates.jsonl"))
    monkeypatch.setattr(settings, "MAX_SNAPSHOTS", 0)


def test_favorites_round_trip():
    assert favorites.load_favorites() == set()
    favorites.set_favorite("aaa", True)
    favorites.set_favorite("bbb", True)
    favorites.set_favorite("aaa", False)
    assert favorites.load_favorites() == {"bbb"}


def test_favorites_ignore_a_damaged_file(tmp_path):
    path = tmp_path / "cfg" / "favorites.json"
    path.parent.mkdir()
    path.write_text("{not json")
    assert favorites.load_favorites() == set()


def test_snapshot_labels_and_pins(transcript):
    take_snapshot(transcript, "first")
    take_snapshot(transcript, "second")
    snaps = list_snapshots(transcript)
    assert [(s.n, s.label, s.pinned) for s in snaps] == [(0, "first", True), (1, "second", False)]
    set_pinned(transcript, 1, True)
    set_pinned(transcript, 0, False)
    assert [(s.n, s.pinned) for s in list_snapshots(transcript)] == [(0, False), (1, True)]
    delete_snapshot(transcript, 0)
    assert [s.n for s in list_snapshots(transcript)] == [1]
    assert not os.path.exists(f"{transcript}.snap0")


def test_limit_deletes_the_oldest_unpinned_and_never_the_pristine(transcript):
    for _ in range(5):
        take_snapshot(transcript)
    assert [s.n for s in snapshots_over_limit(transcript, 3, adding=1)] == [1, 2]
    assert [s.n for s in enforce_limit(transcript, 3)] == [1]
    assert [s.n for s in list_snapshots(transcript)] == [0, 2, 3, 4]
    take_snapshot(transcript)
    assert [s.n for s in enforce_limit(transcript, 3)] == [2]
    assert [s.n for s in list_snapshots(transcript)] == [0, 3, 4, 5]
    assert snapshots_over_limit(transcript, 0, adding=1) == []


def test_apply_enforces_the_limit_after_the_edit(transcript, monkeypatch):
    monkeypatch.setattr(settings, "MAX_SNAPSHOTS", 2)
    for _ in range(4):
        take_snapshot(transcript)
    plan = plan_prune(transcript, cut=CUT, clear=CLEAR)
    assert [s.n for s in plan.will_drop] == [1, 2]
    applied = apply_plan(plan)
    assert [s.n for s in applied.dropped] == [1, 2]
    assert [s.n for s in list_snapshots(transcript)] == [0, 3, 4]
    assert list_snapshots(transcript)[-1].label == plan.label


def test_a_clear_that_changes_nothing_writes_and_snapshots_nothing(transcript):
    apply_plan(plan_prune(transcript, clear=ClearOptions(results=1, keep_tail=0)))
    before = open(transcript, "rb").read()
    plan = plan_prune(transcript, clear=ClearOptions(results=1, keep_tail=0))
    assert plan.is_noop
    applied = apply_plan(plan)
    assert applied.noop and applied.snapshot is None
    assert open(transcript, "rb").read() == before
    assert len(list_snapshots(transcript)) == 1


def test_plan_from_a_loaded_transcript_leaves_it_untouched(transcript):
    loaded = load_transcript(transcript)
    snapshot = json.dumps(loaded.records, sort_keys=True)
    first = plan_prune(transcript, cut=CUT, clear=CLEAR, loaded=loaded)
    second = plan_prune(transcript, cut=CUT, clear=CLEAR, loaded=loaded)
    assert json.dumps(loaded.records, sort_keys=True) == snapshot
    assert first.estimate == second.estimate
    assert [r.get("uuid") for r in first.new] == [r.get("uuid") for r in second.new]
    assert plan_prune(transcript, clear=CLEAR).clear.hits == \
        plan_prune(transcript, clear=CLEAR, loaded=loaded).clear.hits


def test_a_plan_is_refused_when_the_file_changed_after_it_was_read(transcript):
    loaded = load_transcript(transcript)
    plan = plan_prune(transcript, cut=CUT, clear=CLEAR, loaded=loaded)
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "cost-state", "sessionId": "x"}) + "\n")
    before = open(transcript, "rb").read()
    with pytest.raises(PruneError, match="changed after it was read"):
        apply_plan(plan)
    assert open(transcript, "rb").read() == before
    assert list_snapshots(transcript) == []


def test_estimates_are_logged_and_matched_with_the_next_real_request(transcript):
    plan = plan_prune(transcript, cut=CUT, clear=CLEAR)
    apply_plan(plan)
    entries = [json.loads(line) for line in open(settings.ESTIMATE_LOG)]
    assert len(entries) == 1 and entries[0]["estimate"] == plan.estimate
    assert entries[0]["real"] is None
    assert calibration.reconcile(transcript) == []

    recs = load(transcript)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 5))
    recs.append({"type": "assistant", "uuid": "z" * 8, "parentUuid": recs[-1]["uuid"],
                 "timestamp": stamp, "message": {"id": "after", "role": "assistant",
                 "content": [{"type": "text", "text": "hi"}],
                 "usage": {"input_tokens": 3, "cache_read_input_tokens": 100_000,
                           "cache_creation_input_tokens": 0}}})
    with open(transcript, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in recs) + "\n")

    done = calibration.reconcile(transcript)
    assert len(done) == 1 and done[0].real == 100_003
    assert done[0].error == pytest.approx((plan.estimate - 100_003) / 100_003)
    assert json.loads(open(settings.ESTIMATE_LOG).readline())["real"] == 100_003


def test_an_older_estimate_is_not_matched_with_a_request_after_a_later_edit(transcript):
    first = plan_prune(transcript, clear=ClearOptions(results=1, keep_tail=0))
    apply_plan(first)
    calibration.record_estimate(transcript, 111, 222, "later edit")
    entries = [json.loads(line) for line in open(settings.ESTIMATE_LOG)]
    entries[0]["time"] = time.time() - 100
    entries[1]["time"] = time.time() - 50
    with open(settings.ESTIMATE_LOG, "w") as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in entries))

    recs = load(transcript)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 10))
    recs.append({"type": "assistant", "uuid": "y" * 8, "parentUuid": recs[-1]["uuid"],
                 "timestamp": stamp, "message": {"id": "late", "role": "assistant",
                 "content": [{"type": "text", "text": "hi"}],
                 "usage": {"input_tokens": 1, "cache_read_input_tokens": 5_000,
                           "cache_creation_input_tokens": 0}}})
    with open(transcript, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in recs) + "\n")

    done = calibration.reconcile(transcript)
    assert [(o.label, o.real) for o in done] == [("later edit", 5_001)]
