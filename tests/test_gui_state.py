from __future__ import annotations

import shlex

import pytest

from ccprune import settings
from ccprune.cli import build_parser
from ccprune.gui.state import PanelSettings, load_settings, save_settings

REPO = "/repo"


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "GUI_FILE", str(tmp_path / "gui.json"))


def argv(cmd: str) -> list[str]:
    """The cc_prune.py arguments of a generated command, with $F filled in."""
    tail = cmd.split('cc_prune.py" ', 1)[1].split("  #")[0]
    return [a.replace("$F", "/tmp/x.jsonl") for a in shlex.split(tail)]


def test_settings_round_trip_and_ignore_unknown_keys(tmp_path):
    save_settings(PanelSettings(drop=0.8, keep_tail=0.2, thinking="all"))
    got = load_settings()
    assert (got.drop, got.keep_tail, got.thinking) == (0.8, 0.2, "all")
    (tmp_path / "gui.json").write_text('{"panel": {"drop": 0.7, "bogus": 1, "method": "nope"}}')
    got = load_settings()
    assert got.drop == 0.7 and got.method == "fraction"
    (tmp_path / "gui.json").write_text("{broken")
    assert load_settings() == PanelSettings()


def test_default_command_is_a_clearcut_with_the_panel_values():
    s = PanelSettings(drop=0.8, keep_head=1, keep_tail=0.2)
    cmd = s.command("/p/a b.jsonl", REPO, note="My Session")
    assert cmd.startswith("PRUNE_PATH=/repo && F='/p/a b.jsonl' && python3 ")
    assert cmd.endswith("  # My Session")
    assert argv(cmd) == ["clearcut", "/tmp/x.jsonl", "--keep-head", "1", "--drop", "0.8",
                         "--keep-tail", "0.2", "--results", "1", "--inputs", "1", "--yes"]


@pytest.mark.parametrize("panel", [
    PanelSettings(),
    PanelSettings(drop=0.85, keep_head=3, keep_tail=0.05, images=False, purge=False,
                  note=False, reset_usage=False, commands_on=True, commands=300,
                  attachments_on=True, attachments=200, transformations=True),
    PanelSettings(method="turns", from_turn=2, to_turn=18),
    PanelSettings(cut_on=False),
    PanelSettings(cut_on=False, results_on=False, inputs_on=False, images=False,
                  thinking="failing"),
    PanelSettings(results_on=False, inputs_on=False, images=False),
    PanelSettings(thinking="all", keep_tail=0),
])
def test_every_generated_command_parses_and_matches_the_options(panel):
    cmd = panel.command("/tmp/x.jsonl", REPO, turns=19)
    args = build_parser().parse_args(argv(cmd))
    clear, cut = panel.clear_options(), panel.cut_on
    assert args.cmd == ("clearcut" if cut and clear else "cut" if cut else "clear")
    if cut:
        opts = panel.cut_options(19)
        assert (args.from_turn, args.to_turn) == (opts.from_turn, opts.to_turn)
        if panel.method == "fraction":
            assert (args.drop, args.keep_head) == (opts.drop, opts.keep_head)
        assert args.no_note == (not panel.note)
        purged = not args.branch if args.cmd == "clearcut" else args.purge
        assert purged == panel.purge
    if clear:
        assert args.keep_tail == pytest.approx(panel.keep_tail)
        assert args.results == clear.results
        assert args.inputs == clear.inputs
        assert args.commands == clear.commands and args.attachments == clear.attachments
        assert args.transformations == clear.transformations
        assert args.drop_failing_thinking == clear.drop_failing_thinking
        assert args.drop_all_thinking == clear.drop_all_thinking
        assert (not args.no_images if args.cmd == "clearcut" else args.images) == clear.images
    assert args.no_reset_usage == (not panel.reset_usage)


def test_nothing_selected_has_no_command():
    assert PanelSettings(cut_on=False, results_on=False, inputs_on=False,
                         images=False).command("/x", REPO) == ""


def test_turn_range_defaults_to_the_whole_middle_and_clamps():
    s = PanelSettings(method="turns")
    assert s.turn_range(19) == (2, 18)
    assert PanelSettings(from_turn=5, to_turn=9).turn_range(19) == (5, 9)
    assert PanelSettings(from_turn=5, to_turn=40).turn_range(19) == (5, 18)
    assert PanelSettings(from_turn=9, to_turn=3).turn_range(19) == (9, 9)
