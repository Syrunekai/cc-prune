from __future__ import annotations

import os

import pytest

from ccprune import cli, config, settings
from conftest import write_session

SID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    path = write_session(root, SID)
    monkeypatch.setattr(settings, "PROJECTS_ROOT", str(root))
    env = tmp_path / ".env"
    monkeypatch.setenv("CC_PRUNE_ENV", str(env))
    for key in (config.DIRS, config.DEFAULT, config.USER_NAME, config.ASSISTANT_NAME, config.TIMEZONE):
        monkeypatch.delenv(key, raising=False)
    return path, env, tmp_path


def test_export_to_a_dest_directory_and_by_session_id(world, capsys):
    path, _, tmp = world
    out = tmp / "out"
    out.mkdir()
    cli.main(["export", "11111111", str(out), "--tail", "4", "--timezone", "utc"])
    files = os.listdir(out)
    assert len(files) == 1 and files[0].endswith("-11111111.md")
    text = (out / files[0]).read_text()
    assert "**Showing:** last 4 of 16 messages" in text
    assert "wrote 4 of 16 messages" in capsys.readouterr().out


def test_export_uses_the_default_and_named_folders(world, capsys):
    path, env, tmp = world
    env.write_text(f"""CC_PRUNE_EXPORT_DIRS='[{{"name": "a", "path": "{tmp}/a"}}, {{"name": "b", "path": "{tmp}/b"}}]'
CC_PRUNE_EXPORT_DEFAULT=a
CC_PRUNE_USER_NAME=Sam
""")
    cli.main(["export", path])
    (only,) = os.listdir(tmp / "a")
    assert "### Sam — " in (tmp / "a" / only).read_text()
    cli.main(["export", path, "--to", "b", "--user-name", "Lee"])
    assert "### Lee — " in (tmp / "b" / os.listdir(tmp / "b")[0]).read_text()
    with pytest.raises(SystemExit, match="already exists"):
        cli.main(["export", path])
    cli.main(["export", path, "--force"])
    cli.main(["export", "--folders"])
    listing = capsys.readouterr().out
    assert "a" in listing and "default" in listing and str(tmp / "b") in listing


def test_export_explains_a_missing_folder(world):
    path, env, _ = world
    with pytest.raises(SystemExit, match="no default export folder"):
        cli.main(["export", path])
    env.write_text("CC_PRUNE_EXPORT_DIRS='[]'\n")
    with pytest.raises(SystemExit, match="no export folder named 'x'"):
        cli.main(["export", path, "--to", "x"])
    with pytest.raises(SystemExit, match="no such transcript"):
        cli.main(["export", "/nope.jsonl", "/tmp"])


def test_history_flag(world, capsys):
    path, _, tmp = world
    cli.main(["export", path, str(tmp / "a.md"), "--history"])
    assert "wrote 16 of 16 messages" in capsys.readouterr().out
    args = cli.build_parser().parse_args(["export", path])
    assert args.history is False and args.summary is None
    assert cli.build_parser().parse_args(["export", path, "--no-summary"]).summary is False
    assert cli.build_parser().parse_args(["export", path, "--history", "--summary"]).summary is True
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["export", path, "--summary", "--no-summary"])
