"""The active conversation of a transcript, split into turns."""
from __future__ import annotations

from .errors import PruneError
from .transcript import API_ATTACHMENTS, blocks, is_prompt, rendered_chars

# attachments that carry harness state (system prompt, tool schemas, tool, MCP,
# agent and skill listings) forward from the turn they appear in; a cut keeps
# copies of the ones in the cut stretch
CARRIED_ATTACHMENTS = API_ATTACHMENTS | {"deferred_tools_delta", "mcp_instructions_delta",
                                         "skill_listing", "agent_listing_delta"}


def active_chain(recs: list[dict]) -> list[int]:
    """Record indexes of the conversation a resume loads, oldest first: the
    parentUuid walk from the newest main-thread record back to its root (the
    first record, or the latest compact boundary)."""
    by = {r["uuid"]: i for i, r in enumerate(recs) if r.get("uuid")}
    leaf = next((i for i in range(len(recs) - 1, -1, -1)
                 if recs[i].get("uuid") and not recs[i].get("isSidechain")), None)
    if leaf is None:
        raise PruneError("no conversation records in this file")
    chain, i = [], leaf
    while i is not None:
        if len(chain) > len(recs):
            raise PruneError("parentUuid loop in this file; refusing to cut")
        chain.append(i)
        parent = recs[i].get("parentUuid")
        if parent and parent not in by:
            raise PruneError(f"record {i} points to a missing parent; refusing to cut")
        i = by.get(parent) if parent else None
    return chain[::-1]


def tool_siblings(recs: list[dict], chain: list[int]) -> dict[int, list[int]]:
    """Tool results that hang off the chain instead of sitting on it, keyed by
    the chain record they hang from. Parallel tool calls put every result but
    the last one there, and a resume still loads them."""
    on, pos = set(chain), {recs[i]["uuid"]: i for i in chain}
    out = {}
    for j, r in enumerate(recs):
        if (j not in on and r.get("type") == "user" and r.get("parentUuid") in pos
                and not r.get("isSidechain") and any(b.get("type") == "tool_result" for b in blocks(r))):
            out.setdefault(pos[r["parentUuid"]], []).append(j)
    return out


def is_carried(rec: dict) -> bool:
    return (rec.get("type") == "attachment"
            and (rec.get("attachment") or {}).get("type") in CARRIED_ATTACHMENTS)


def tool_ids(recs: list[dict], idx) -> tuple[set, set]:
    uses, results = set(), set()
    for i in idx:
        for b in blocks(recs[i]):
            if b.get("type") == "tool_use" and b.get("id"):
                uses.add(b["id"])
            elif b.get("type") == "tool_result":
                results.add(b.get("tool_use_id"))
    return uses, results


class Turns:
    """The active conversation split into turns: a prompt and everything up to
    the next prompt. Records before the first prompt belong to turn 0."""

    def __init__(self, recs: list[dict]):
        self.recs = recs
        self.chain = active_chain(recs)
        self.siblings = tool_siblings(recs, self.chain)
        starts = [k for k, i in enumerate(self.chain) if is_prompt(recs[i])]
        self.bounds = sorted({0, *starts})
        self.ends = self.bounds[1:] + [len(self.chain)]
        # characters a cut of the turn removes; carried attachments stay
        self.size = [sum(rendered_chars(recs[i]) for i in self.members(t, t + 1)
                         if not is_carried(recs[i]))
                     for t in range(len(self))]
        # a cut may start or end at turn t only if no tool call before t is
        # still waiting for a result that comes after t
        answered = tool_ids(recs, self.members(0, len(self)))[1]
        self.clean, uses, results = [], set(), set()
        for t in range(len(self)):
            self.clean.append(not ((uses & answered) - results))
            u, r = tool_ids(recs, self.members(t, t + 1))
            uses |= u
            results |= r

    def __len__(self) -> int:
        return len(self.bounds)

    def members(self, a: int, b: int) -> list[int]:
        """Record indexes of turns a..b-1, chain records and their siblings."""
        if a >= b:
            return []
        out = []
        for k in range(self.bounds[a], self.ends[b - 1]):
            out.append(self.chain[k])
            out.extend(self.siblings.get(self.chain[k], []))
        return out

    def first(self, t: int) -> int:
        return self.chain[self.bounds[t]]


def descendants(recs: list[dict], roots: set[int], stop: set[int]) -> set[int]:
    """Indexes of `roots` and every record below them, not descending into `stop`."""
    by = {r["uuid"]: i for i, r in enumerate(recs) if r.get("uuid")}
    children = {}
    for i, r in enumerate(recs):
        if r.get("uuid") and r.get("parentUuid") in by:
            children.setdefault(by[r["parentUuid"]], []).append(i)
    out, todo = set(), list(roots)
    while todo:
        i = todo.pop()
        if i in out or i in stop:
            continue
        out.add(i)
        todo.extend(children.get(i, []))
    return out
