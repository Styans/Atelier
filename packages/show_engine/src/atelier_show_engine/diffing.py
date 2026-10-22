"""Human-readable differences between two revisions of a show."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from atelier_domain import Show

MAX_VALUE_CHARS = 120


@dataclass(frozen=True)
class DiffEntry:
    path: str
    old: str | None  # None: added
    new: str | None  # None: removed


def _text(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= MAX_VALUE_CHARS else text[: MAX_VALUE_CHARS - 1] + "…"


def _walk(path: str, old: Any, new: Any, out: list[DiffEntry]) -> None:
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(old.keys() | new.keys(), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in old:
                out.append(DiffEntry(child, None, _text(new[key])))
            elif key not in new:
                out.append(DiffEntry(child, _text(old[key]), None))
            else:
                _walk(child, old[key], new[key], out)
        return
    if (
        isinstance(old, list)
        and isinstance(new, list)
        and old
        and new
        and all(isinstance(x, dict) and "id" in x for x in [*old, *new])
    ):
        # Lists of things with ids (scenes): compare by id, report order separately.
        old_by, new_by = {x["id"]: x for x in old}, {x["id"]: x for x in new}
        _walk(path, old_by, new_by, out)
        common_old = [x for x in old_by if x in new_by]
        common_new = [x for x in new_by if x in old_by]
        if common_old != common_new:
            out.append(DiffEntry(f"{path} order", _text(common_old), _text(common_new)))
        return
    out.append(DiffEntry(path, _text(old), _text(new)))


def diff_shows(before: Show, after: Show) -> list[DiffEntry]:
    old = before.model_dump(mode="json", exclude={"revision", "artifacts"})
    new = after.model_dump(mode="json", exclude={"revision", "artifacts"})
    out: list[DiffEntry] = []
    _walk("", old, new, out)
    for artifact_id in sorted(after.artifacts.keys() - before.artifacts.keys()):
        record = after.artifacts[artifact_id]
        out.append(DiffEntry(f"artifacts.{artifact_id}", None, f"{record.kind} {record.slot}"))
    return out
