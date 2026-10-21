import os
import time
from pathlib import Path

import pytest

from atelier_domain import Brief, Show, fleet_from_preset
from atelier_show_engine.config import DEFAULT_PIPELINE
from atelier_show_engine.store import ConflictError, ShowStore, StoreError


def make_show(show_id: str = "demo-0001") -> Show:
    brief = Brief(title="Demo", duration_s=300, fleet=fleet_from_preset("conservative", 10))
    return Show(id=show_id, brief=brief, pipeline=DEFAULT_PIPELINE)


def test_create_writes_the_first_revision(tmp_path: Path) -> None:
    store, show = ShowStore.create(tmp_path, make_show())

    assert show.revision == 1
    assert store.load() == show
    assert (store.root / "revisions" / "0001.json").is_file()
    assert [r.message for r in store.history()] == ["Create show"]


def test_create_refuses_an_existing_folder(tmp_path: Path) -> None:
    ShowStore.create(tmp_path, make_show())

    with pytest.raises(StoreError, match="exists"):
        ShowStore.create(tmp_path, make_show())


def test_commit_appends_revisions(tmp_path: Path) -> None:
    store, show = ShowStore.create(tmp_path, make_show())
    show.brief.title = "Renamed"

    new = store.commit(show, "Rename", "user")

    assert new.revision == 2
    assert store.load().brief.title == "Renamed"
    assert store.load_revision(1).brief.title == "Demo"
    assert [(r.revision, r.author) for r in store.history()] == [(1, "cli"), (2, "user")]


def test_commit_based_on_an_old_revision_is_a_conflict(tmp_path: Path) -> None:
    store, show = ShowStore.create(tmp_path, make_show())
    store.commit(show, "first edit")

    with pytest.raises(ConflictError, match="revision 2"):
        store.commit(show, "edit of a stale copy")


def test_rollback_is_a_new_revision(tmp_path: Path) -> None:
    store, show = ShowStore.create(tmp_path, make_show())
    show.brief.title = "Second"
    store.commit(show, "edit")

    restored = store.rollback(1)

    assert restored.revision == 3
    assert restored.brief.title == "Demo"
    assert store.load_revision(2).brief.title == "Second"


def test_new_file_never_reuses_a_path(tmp_path: Path) -> None:
    store, _ = ShowStore.create(tmp_path, make_show())
    first = store.new_file("models/m", "points_10", "abcdef1234", ".npz")
    first.write_bytes(b"x")

    second = store.new_file("models/m", "points_10", "abcdef1234", ".npz")

    assert first.name == "points_10_abcdef12.npz"
    assert second != first and not second.exists()


def test_a_stale_lock_is_cleared(tmp_path: Path) -> None:
    store, show = ShowStore.create(tmp_path, make_show())
    lock = store.root / ".lock"
    lock.write_text("12345")
    old = time.time() - 3600
    os.utime(lock, (old, old))

    assert store.commit(show, "after crash").revision == 2
    assert not lock.exists()
