"""A small synthetic transcript to run the engine against."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def write_transcript(path, turns: int = 8, result_chars: int = 3000,
                     input_chars: int = 800) -> None:
    """A linear conversation: each turn is a prompt, an assistant message with a
    thinking block and a tool call, the tool result and a closing text reply."""
    recs, state = [], {"parent": None, "n": 0, "tokens": 20000}

    def push(**rec):
        state["n"] += 1
        n = state["n"]
        rec["timestamp"] = f"2026-01-01T{n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}.000Z"
        rec["uuid"] = f"00000000-0000-4000-8000-{n:012d}"
        rec["parentUuid"] = state["parent"]
        state["parent"] = rec["uuid"]
        recs.append(rec)

    def usage():
        return {"input_tokens": 10, "cache_read_input_tokens": state["tokens"],
                "cache_creation_input_tokens": 100, "output_tokens": 50}

    for t in range(turns):
        push(type="user", message={"role": "user", "content": [
            {"type": "text", "text": f"prompt {t} " + "p" * 200}]})
        state["tokens"] += 3000
        push(type="assistant", message={
            "id": f"msg{t}a", "role": "assistant", "model": "m",
            "content": [{"type": "thinking", "thinking": "", "signature": "s" * 100},
                        {"type": "tool_use", "id": f"tu{t}", "name": "Read",
                         "input": {"file_path": f"/f{t}", "note": "n" * input_chars}}],
            "usage": usage()})
        push(type="user", message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"tu{t}", "content": "r" * result_chars}]})
        state["tokens"] += 4000
        push(type="assistant", message={
            "id": f"msg{t}b", "role": "assistant", "model": "m",
            "content": [{"type": "text", "text": f"done {t} " + "d" * 300}],
            "usage": usage()})

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in recs) + "\n")


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / "t.jsonl"
    write_transcript(path)
    return str(path)


def write_session(root, name: str, project: str = "-tmp-proj", **kw) -> str:
    """A transcript under root/project/<name>.jsonl that `sessions` can find."""
    folder = root / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl"
    write_transcript(path, **kw)
    old = __import__("time").time() - 3600
    os.utime(path, (old, old))
    return str(path)


