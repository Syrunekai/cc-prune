"""The .env file: parsing, folders and writing back."""
from __future__ import annotations

import os
import stat

import pytest

from ccprune import config
from ccprune.errors import PruneError


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setenv("CC_PRUNE_ENV", str(path))
    for key in (config.DIRS, config.DEFAULT, config.USER_NAME, config.ASSISTANT_NAME, config.TIMEZONE):
        monkeypatch.delenv(key, raising=False)
    return path


def test_parse_handles_comments_quotes_exports_and_multiline_values():
    text = """# a comment
PLAIN=value   # trailing comment
export EXPORTED=1
SINGLE='it''s'
DOUBLE="a \\"b\\" \\\\ c\\nd"
MULTI='[
  {"name": "a", "path": "~/a"}
]'
   SPACED = x
not a line
"""
    got = {e.key: e.value for e in config.parse(text)}
    assert got["PLAIN"] == "value" and got["EXPORTED"] == "1" and got["SPACED"] == "x"
    assert got["SINGLE"] == "it"
    assert got["DOUBLE"] == 'a "b" \\ c\nd'
    assert got["MULTI"] == '[\n  {"name": "a", "path": "~/a"}\n]'


def test_an_unclosed_quote_is_an_error():
    with pytest.raises(PruneError, match="never closed"):
        config.parse("A='open\nB=2\n")


def test_folders_and_the_default(env):
    env.write_text("""CC_PRUNE_EXPORT_DIRS='[{"name": "a", "path": "~/a"}, {"name": "b", "path": "/b"}]'\n""")
    f = config.load_folders()
    assert [x.name for x in f.folders] == ["a", "b"] and f.default is None and f.get() is None
    assert f.get("a").expanded == os.path.expanduser("~/a")
    env.write_text(env.read_text() + "CC_PRUNE_EXPORT_DEFAULT=b\n")
    assert config.load_folders().get().path == "/b"


def test_a_single_folder_is_the_default(env):
    env.write_text("""CC_PRUNE_EXPORT_DIRS='[{"name": "only", "path": "/x"}]'\n""")
    assert config.load_folders().default == "only"


def test_no_file_means_no_folders(env):
    assert config.load_folders().folders == []


@pytest.mark.parametrize("raw, match", [
    ("'{}'", "must be a JSON array"),
    ("'[{\"name\": \"a\"}]'", "needs a \"name\" and a \"path\""),
    ("'[{\"name\": \"a\", \"path\": \"/1\"}, {\"name\": \"a\", \"path\": \"/2\"}]'", "used twice"),
    ("'[nope'", "not a JSON array"),
])
def test_bad_folder_lists_are_explained(env, raw, match):
    env.write_text(f"CC_PRUNE_EXPORT_DIRS={raw}\n")
    with pytest.raises(PruneError, match=match):
        config.load_folders()


def test_an_unknown_default_is_explained(env):
    env.write_text("CC_PRUNE_EXPORT_DIRS='[{\"name\": \"a\", \"path\": \"/a\"}]'\nCC_PRUNE_EXPORT_DEFAULT=zzz\n")
    with pytest.raises(PruneError, match="not one of the folders"):
        config.load_folders()


def test_the_environment_wins_over_the_file(env, monkeypatch):
    env.write_text("CC_PRUNE_USER_NAME=file\n")
    monkeypatch.setenv(config.USER_NAME, "environment")
    assert config.read_env()[config.USER_NAME] == "environment"


def test_saving_folders_keeps_every_other_line(env):
    env.write_text("# my settings\nOTHER=keep me\nCC_PRUNE_EXPORT_DIRS='[\n  {\"name\": \"a\", \"path\": \"/a\"}\n]'\n# end\n")
    folders = config.load_folders()
    folders.folders.append(config.Folder("it's", "~/b"))
    config.save_folders(config.Folders(folders.folders, "it's"))
    text = env.read_text()
    assert text.startswith("# my settings\nOTHER=keep me\n") and text.endswith("# end\n")
    assert text.count("CC_PRUNE_EXPORT_DIRS") == 1
    again = config.load_folders()
    assert [(f.name, f.path) for f in again.folders] == [("a", "/a"), ("it's", "~/b")]
    assert again.default == "it's"
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600
    config.save_folders(config.Folders([], None))
    assert "CC_PRUNE_EXPORT" not in env.read_text() and "OTHER=keep me" in env.read_text()


def test_the_checkout_env_is_gitignored_and_the_example_parses():
    top = config.checkout_dir()
    assert top and config.env_path().startswith(top) or os.environ.get("CC_PRUNE_ENV")
    ignore = open(os.path.join(top, ".gitignore")).read().split()
    assert ".env" in ignore
    with open(os.path.join(top, ".env.example")) as fh:
        values = {e.key: e.value for e in config.parse(fh.read())}
    f = config.load_folders(values)
    assert [x.name for x in f.folders] == ["transcripts", "archive"] and f.default == "transcripts"
