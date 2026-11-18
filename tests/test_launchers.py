from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from ccprune import cli

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_cc_prune_is_executable_with_a_uv_shebang_and_no_dependencies():
    path = os.path.join(ROOT, "cc-prune")
    assert os.access(path, os.X_OK)
    lines = open(path, encoding="utf-8").read().splitlines()
    assert lines[0] == "#!/usr/bin/env -S uv run --script"
    assert "# dependencies = []" in lines[:6]


def test_cc_prune_gui_is_a_shortcut_to_cc_prune_with_the_gui_flag():
    path = os.path.join(ROOT, "cc-prune-gui")
    assert os.access(path, os.X_OK)
    text = open(path, encoding="utf-8").read()
    assert text.startswith("#!/bin/sh\n") and '/cc-prune" --gui "$@"' in text


def test_the_old_script_is_gone():
    assert not os.path.exists(os.path.join(ROOT, "cc_prune.py"))


def test_cc_prune_runs_with_plain_python():
    out = subprocess.run([sys.executable, os.path.join(ROOT, "cc-prune"), "--version"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.startswith("cc-prune ")


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_cc_prune_runs_through_its_shebang():
    out = subprocess.run([os.path.join(ROOT, "cc-prune"), "--version"],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.startswith("cc-prune ")


def test_gui_flag_opens_the_window_and_takes_no_command(monkeypatch):
    opened = []
    monkeypatch.setattr(cli, "start_gui", lambda: opened.append(True))
    cli.main(["--gui"])
    assert opened == [True]
    with pytest.raises(SystemExit, match="takes no command"):
        cli.main(["--gui", "sessions"])


def test_without_pyside6_the_gui_runs_again_through_uv(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_pyside(name, *a, **k):
        if name.startswith("PySide6"):
            raise ImportError(name)
        return real(name, *a, **k)

    class Replaced(Exception):
        pass

    calls = []

    def execve(exe, argv, env):
        calls.append((exe, argv, env))
        raise Replaced

    monkeypatch.setattr(builtins, "__import__", no_pyside)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/uv")
    monkeypatch.setattr(cli.os, "execve", execve)
    monkeypatch.delenv("CC_PRUNE_GUI_REEXEC", raising=False)
    with pytest.raises(Replaced):
        cli.start_gui()
    exe, argv, env = calls[0]
    assert exe == "/usr/bin/uv" and argv[1:3] == ["run", "--no-project"]
    assert "--with" in argv and argv[argv.index("--with") + 1].startswith("pyside6")
    assert argv[-3:] == ["-m", "ccprune", "--gui"] and env["CC_PRUNE_GUI_REEXEC"] == "1"
    assert os.path.join(ROOT, "src") in env["PYTHONPATH"].split(os.pathsep)

    monkeypatch.setenv("CC_PRUNE_GUI_REEXEC", "1")
    with pytest.raises(SystemExit, match="needs PySide6"):
        cli.start_gui()
