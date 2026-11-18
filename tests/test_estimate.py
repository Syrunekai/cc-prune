from __future__ import annotations

import copy

import pytest

from ccprune import estimate
from ccprune.cut import CutOptions, plan_cut
from ccprune.estimate import estimate_context, iso_time

TEXT = "x" * 400


def conversation(counts: list[int]) -> list[dict]:
    """One prompt and one reply per turn; `counts` are the input tokens each
    reply's request recorded."""
    recs, parent = [], None

    def push(**rec):
        n = len(recs) + 1
        rec["uuid"] = f"00000000-0000-4000-8000-{n:012d}"
        rec["parentUuid"] = parent
        rec["timestamp"] = f"2026-01-01T00:{n // 60:02d}:{n % 60:02d}.000Z"
        recs.append(rec)
        return rec["uuid"]

    for t, tokens in enumerate(counts):
        parent = push(type="user", message={"role": "user", "content": [
            {"type": "text", "text": f"prompt {t} {TEXT}"}]})
        parent = push(type="assistant", message={
            "id": f"msg{t}", "role": "assistant", "model": "m",
            "content": [{"type": "text", "text": f"reply {t} {TEXT}"}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": tokens - 10,
                      "cache_creation_input_tokens": 0, "output_tokens": 50}})
    return recs


def cut_turns(recs: list[dict], first: int, last: int) -> list[dict]:
    return plan_cut(copy.deepcopy(recs), CutOptions(
        keep_head=1, from_turn=first, to_turn=last, purge=True)).new


# request counts after an earlier edit dropped the context: the first two are
# stale, made while content that has since been removed was still there
STALE_THEN_DROP = [400_000, 600_000, 700_000, 500_000, 505_000, 510_000, 515_000, 520_000]


@pytest.fixture
def uncapped(monkeypatch):
    monkeypatch.setattr(estimate, "MAX_SEGMENT_SCALE", None)


def test_stale_first_request_does_not_anchor_after_a_drop(uncapped):
    old = conversation(STALE_THEN_DROP)
    first, _ = estimate_context(old, cut_turns(old, 2, 5), 0.0)

    changed = copy.deepcopy(old)
    changed[1]["message"]["usage"]["cache_read_input_tokens"] = 290_000
    second, _ = estimate_context(changed, cut_turns(changed, 2, 5), 0.0)

    assert first == second


def test_first_request_still_anchors_without_a_drop(uncapped):
    old = conversation([40_000, 60_000, 80_000, 100_000, 120_000, 140_000, 160_000, 180_000])
    since = iso_time(old[2]["timestamp"])
    first, _ = estimate_context(old, cut_turns(old, 2, 5), since)

    changed = copy.deepcopy(old)
    changed[1]["message"]["usage"]["cache_read_input_tokens"] = 20_000
    second, _ = estimate_context(changed, cut_turns(changed, 2, 5), since)

    assert first != second


def test_segment_scale_is_capped(monkeypatch):
    old = conversation([20_000, 60_000, 61_000, 62_000, 63_000, 64_000])
    new = cut_turns(old, 2, 3)

    monkeypatch.setattr(estimate, "MAX_SEGMENT_SCALE", None)
    uncapped, _ = estimate_context(old, new, 0.0)
    monkeypatch.setattr(estimate, "MAX_SEGMENT_SCALE", 6.0)
    capped, _ = estimate_context(old, new, 0.0)

    assert capped > uncapped
