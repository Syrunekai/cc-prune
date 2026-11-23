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
    after = list_snapshots(transcript)
    assert [(s.n, s.label, s.pinned) for s in after] == [(0, "second", True)]
    assert not os.path.exists(f"{transcript}.snap1")


def test_limit_deletes_the_oldest_unpinned_and_never_the_pristine(transcript):
    for k in range(5):
        take_snapshot(transcript, f"s{k}")
    assert [s.n for s in snapshots_over_limit(transcript, 3, adding=1)] == [1, 2]
    assert [s.n for s in enforce_limit(transcript, 3)] == [1]
    assert [(s.n, s.label) for s in list_snapshots(transcript)] == \
        [(0, "s0"), (1, "s2"), (2, "s3"), (3, "s4")]
    take_snapshot(transcript, "s5")
    assert [s.label for s in enforce_limit(transcript, 3)] == ["s2"]
    assert [(s.n, s.label) for s in list_snapshots(transcript)] == \
        [(0, "s0"), (1, "s3"), (2, "s4"), (3, "s5")]
    assert snapshots_over_limit(transcript, 0, adding=1) == []


def test_apply_enforces_the_limit_after_the_edit(transcript, monkeypatch):
    monkeypatch.setattr(settings, "MAX_SNAPSHOTS", 2)
    for _ in range(4):
        take_snapshot(transcript)
    plan = plan_prune(transcript, cut=CUT, clear=CLEAR)
    assert [s.n for s in plan.will_drop] == [1, 2]
    applied = apply_plan(plan)
    assert [s.n for s in applied.dropped] == [1, 2]
    assert [s.n for s in list_snapshots(transcript)] == [0, 1, 2]
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


def test_deleting_renumbers_and_moves_labels_pins_and_times(transcript):
    from ccprune.snapshots import delete_snapshots, read_meta, snapshot_time
    for k in range(5):
        take_snapshot(transcript, f"s{k}")
    set_pinned(transcript, 3, True)
    times = {s.label: s.created for s in list_snapshots(transcript)}
    contents = {s.label: open(s.path, "rb").read() for s in list_snapshots(transcript)}
    freed = delete_snapshots(transcript, [0, 2])
    assert freed > 0
    after = list_snapshots(transcript)
    assert [(s.n, s.label, s.pinned) for s in after] == [(0, "s1", False), (1, "s3", True), (2, "s4", False)]
    assert all(s.created == times[s.label] for s in after)
    assert all(open(s.path, "rb").read() == contents[s.label] for s in after)
    assert not os.path.exists(f"{transcript}.snap3") and not os.path.exists(f"{transcript}.snap4")
    assert sorted(read_meta(transcript)) == ["0", "1", "2"]
    assert snapshot_time(transcript, 1) == times["s3"]


def test_the_estimator_reads_snapshot_times_from_the_metadata(transcript):
    from ccprune.estimate import last_edit_time
    from ccprune.snapshots import delete_snapshots
    for k in range(3):
        take_snapshot(transcript, f"s{k}")
    newest = list_snapshots(transcript)[-1].created
    time.sleep(0.05)
    delete_snapshots(transcript, [0])            # renames the others: their change times move
    assert last_edit_time(transcript) == newest
    assert os.stat(f"{transcript}.snap1").st_ctime > newest


def test_a_gapped_set_from_an_older_version_is_closed_before_a_new_snapshot(transcript):
    import shutil
    for k in range(3):
        take_snapshot(transcript, f"s{k}")
    os.remove(f"{transcript}.snap1")            # as an older version deleting one would leave it
    shutil.move(f"{transcript}.snap2", f"{transcript}.snap5")
    from ccprune.snapshots import read_meta, write_meta
    meta = read_meta(transcript)
    meta.pop("1")
    meta["5"] = meta.pop("2")
    write_meta(transcript, meta)
    n, _ = take_snapshot(transcript, "new")
    assert n == 2
    assert [(s.n, s.label) for s in list_snapshots(transcript)] == [(0, "s0"), (1, "s2"), (2, "new")]


def test_delete_all_snapshots(transcript):
    from ccprune.snapshots import delete_all_snapshots, meta_path
    for k in range(3):
        take_snapshot(transcript, f"s{k}")
    before = open(transcript, "rb").read()
    assert delete_all_snapshots(transcript) > 0
    assert list_snapshots(transcript) == [] and not os.path.exists(meta_path(transcript))
    assert open(transcript, "rb").read() == before
