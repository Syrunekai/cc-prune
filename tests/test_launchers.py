from __future__ import annotations

import os
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
LAUNCHERS = ["cc_prune.py", "cc-prune-gui"]


@pytest.mark.parametrize("name", LAUNCHERS)
def test_launchers_are_executable_with_a_uv_shebang_and_declare_their_dependencies(name):
    path = os.path.join(ROOT, name)
    assert os.access(path, os.X_OK)
    lines = open(path, encoding="utf-8").read().splitlines()
    assert lines[0] == "#!/usr/bin/env -S uv run --script"
    assert "# /// script" in lines and "# dependencies = " in "\n".join(lines[:6])


def test_the_gui_launcher_declares_pyside6():
    assert 'dependencies = ["pyside6' in open(os.path.join(ROOT, "cc-prune-gui")).read()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_the_command_line_runs_directly_through_its_shebang():
    out = subprocess.run([os.path.join(ROOT, "cc_prune.py"), "--version"],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.startswith("cc-prune ")
