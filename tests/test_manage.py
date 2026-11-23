"""Renaming and deleting sessions."""
from __future__ import annotations

import json
import os
import stat
import time

import pytest

from ccprune import cli, favorites, settings
from ccprune.errors import PruneError
from ccprune.manage import custom_title, delete_session, rename_session, session_files
from ccprune.sessions import session_title
from ccprune.snapshots import take_snapshot
from conftest import write_session

SID = "aaaaaaaa-1111-4222-8333-444444444444"
TWIN = "aaaaaaaa-9999-4999-8999-999999999999"     # shares the first 8 characters
OTHER = "bbbbbbbb-1111-4222-8333-444444444444"


@pytest.fixture
def home(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    monkeypatch.setattr(settings, "CONFIG_DIR", str(claude))
    monkeypatch.setattr(settings, "PROJECTS_ROOT", str(claude / "projects"))
    monkeypatch.setattr(settings, "SESSIONS_DIR", str(claude / "sessions"))
    monkeypatch.setattr(settings, "FAVORITES_FILE", str(tmp_path / "cfg" / "fav.json"))
    monkeypatch.setattr(settings, "ESTIMATE_LOG", str(tmp_path / "data" / "est.jsonl"))
    return claude


def full_session(claude, sid=SID):
    path = write_session(claude / "projects", sid)
    take_snapshot(path, "one")
    take_snapshot(path, "two")
    folder = claude / "projects" / "-tmp-proj" / sid
    (folder / "subagents").mkdir(parents=True)
    (folder / "subagents" / "agent-x.jsonl").write_text("{}\n")
    (folder / "tool-results").mkdir()
    (folder / "tool-results" / "r.txt").write_text("result")
    for kind in ("file-history", "session-env"):
        (claude / kind / sid).mkdir(parents=True)
        (claude / kind / sid / "f").write_text("x")
    (claude / "teams" / f"session-{sid[:8]}").mkdir(parents=True)
    (claude / "teams" / f"session-{sid[:8]}" / "config.json").write_text(
        json.dumps({"leadSessionId": sid}))
    (claude / "tasks" / f"session-{sid[:8]}").mkdir(parents=True)
    (claude / "jobs" / sid[:8]).mkdir(parents=True)
    (claude / "jobs" / sid[:8] / "state.json").write_text(json.dumps({"sessionId": sid}))
    old = time.time() - 3600
    os.utime(path, (old, old))
    return path


def old(path):
    t = time.time() - 3600
    os.utime(path, (t, t))
    return path


def title_records(path):
    return [r for r in map(json.loads, open(path, encoding="utf-8")) if r.get("type") == "custom-title"]


def test_rename_writes_what_claude_codes_rename_writes(home):
    path = old(write_session(home / "projects", SID))
    before = open(path, "rb").read()
    mtime = os.stat(path).st_mtime_ns
    assert rename_session(path, "  New   name ") == "New name"
    after = open(path, "rb").read()
    assert after.startswith(before) and after.endswith(b"\n")
    assert after[len(before):] == (b'{"type":"custom-title","customTitle":"New name","sessionId":"'
                                   + SID.encode() + b'"}\n')
    assert os.stat(path).st_mtime_ns == mtime, "the transcript keeps its modification time"
    side = home / "projects" / "-tmp-proj" / SID / "custom-title.json"
    assert json.loads(side.read_text()) == {"customTitle": "New name"}
    assert stat.S_IMODE(os.stat(side).st_mode) == 0o600
    assert custom_title(path) == "New name" and session_title(path, [], title_records(path)) == "New name"


def test_renaming_twice_and_to_the_same_title(home):
    path = old(write_session(home / "projects", SID))
    rename_session(path, "First")
    rename_session(path, "Second")
    rename_session(path, "Second")
    assert [r["customTitle"] for r in title_records(path)] == ["First", "Second"]
    assert custom_title(path) == "Second"


def test_the_newest_record_wins_over_a_stale_side_file(home):
    """What a rename that only wrote the side file looked like to claude --resume."""
    path = old(write_session(home / "projects", SID))
    with open(path, "a") as fh:
        fh.write(json.dumps({"type": "custom-title", "customTitle": "Old", "sessionId": SID}) + "\n")
        fh.write(json.dumps({"type": "ai-title", "aiTitle": "Generated", "sessionId": SID}) + "\n")
    side = home / "projects" / "-tmp-proj" / SID
    side.mkdir()
    (side / "custom-title.json").write_text(json.dumps({"customTitle": "Side only"}))
    old(path)
    assert custom_title(path) == "Old"
    from ccprune.sessions import scan_session
    assert scan_session(path, False, False)["title"] == "Old"
    rename_session(path, "Fixed")
    assert scan_session(path, False, False)["title"] == "Fixed"
    assert json.loads((side / "custom-title.json").read_text()) == {"customTitle": "Fixed"}


def test_rename_refuses_empty_titles_and_running_sessions(home):
    path = write_session(home / "projects", SID)
    os.utime(path, None)                                   # written just now: looks live
    before = open(path, "rb").read()
    with pytest.raises(PruneError):
        rename_session(path, "While running")
    old(path)
    with pytest.raises(PruneError):
        rename_session(path, "   ")
    with pytest.raises(PruneError):
        rename_session(str(home / "nope.jsonl"), "x")
    assert open(path, "rb").read() == before


def test_rename_keeps_the_rest_of_the_session_folder(home):
    path = full_session(home)
    rename_session(path, "Kept")
    folder = home / "projects" / "-tmp-proj" / SID
    assert (folder / "subagents" / "agent-x.jsonl").exists()


def test_session_files_lists_everything_and_the_transcript_last(home):
    path = full_session(home)
    files = session_files(path)
    kinds = [a.kind for a in files.artifacts]
    assert kinds[-1] == "transcript"
    for kind in ("snapshot", "snapshot data", "session folder", "file-history", "session-env",
                 "teams", "tasks", "jobs"):
        assert kind in kinds, kind
    assert kinds.count("snapshot") == 2 and files.kept == [] and files.size > 0


def test_delete_removes_everything_and_only_this_session(home):
    path = full_session(home)
    other = full_session(home, OTHER)
    favorites.set_favorite(SID, True)
    favorites.set_favorite(OTHER, True)
    os.makedirs(os.path.dirname(settings.ESTIMATE_LOG))
    with open(settings.ESTIMATE_LOG, "w") as fh:
        fh.write(json.dumps({"session": SID}) + "\n" + json.dumps({"session": OTHER}) + "\n")
    done = delete_session(path)
    assert done.session_id == SID and done.size > 0
    leftovers = [p for p, _, fs in os.walk(home) for f in fs + [""] if SID in p or SID in f
                 or os.path.basename(p) in (f"session-{SID[:8]}", SID[:8])]
    assert leftovers == []
    assert os.path.exists(other) and (home / "file-history" / OTHER).exists()
    assert (home / "teams" / f"session-{OTHER[:8]}").exists()
    assert favorites.load_favorites() == {OTHER}
    assert [json.loads(line)["session"] for line in open(settings.ESTIMATE_LOG)] == [OTHER]


def test_prefix_folders_of_another_session_are_kept(home):
    path = full_session(home)
    (home / "teams" / f"session-{SID[:8]}" / "config.json").write_text(
        json.dumps({"leadSessionId": TWIN}))
    write_session(home / "projects", TWIN)            # a second transcript with the same prefix
    files = session_files(path)
    kinds = [a.kind for a in files.artifacts]
    assert "teams" not in kinds and "tasks" not in kinds      # tasks: no id inside, prefix shared
    assert "jobs" in kinds                                     # jobs: records this session's id
    assert any(k.endswith(f"session-{SID[:8]}") for k in files.kept)
    delete_session(path)
    assert (home / "teams" / f"session-{SID[:8]}").exists()
    assert (home / "tasks" / f"session-{SID[:8]}").exists()


def test_a_live_session_is_not_deleted(home):
    path = full_session(home)
    os.utime(path, None)                                       # edited just now
    with pytest.raises(PruneError):
        delete_session(path)
    assert os.path.exists(path) and session_files(path).artifacts


def test_symlinks_are_removed_not_followed(home, tmp_path):
    path = full_session(home)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    os.symlink(outside, home / "projects" / "-tmp-proj" / SID / "subagents" / "link")
    delete_session(path)
    assert (outside / "keep.txt").read_text() == "keep"


def test_cli_rename_and_delete(home, capsys):
    path = full_session(home)
    cli.main(["rename", SID[:12], "From the CLI"])
    assert custom_title(path) == "From the CLI"
    old(path)
    cli.main(["delete", SID])
    out = capsys.readouterr().out
    assert "PREVIEW, nothing deleted" in out and "session folder" in out and os.path.exists(path)
    cli.main(["delete", SID, "--yes"])
    assert not os.path.exists(path) and "deleted" in capsys.readouterr().out
