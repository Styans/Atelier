"""The project folder: ``show.json``, its revisions and the artifact files."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from atelier_domain import Show

SHOW_FILE = "show.json"
LOCK_FILE = ".lock"
STALE_LOCK_S = 120.0


class StoreError(Exception):
    pass


class ConflictError(StoreError):
    """``show.json`` changed since the caller loaded it."""


@dataclass(frozen=True)
class RevisionInfo:
    revision: int
    message: str
    author: str
    created_at: str


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    for attempt in range(10):  # Windows: a reader or an antivirus may hold the file briefly
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))


class ShowStore:
    """Every change goes through ``commit``: a new full revision, never an overwrite."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    # --- creation and reading ---

    @classmethod
    def create(cls, projects_dir: Path, show: Show) -> tuple[ShowStore, Show]:
        root = projects_dir / show.id
        if root.exists():
            raise StoreError(f"project folder already exists: {root}")
        root.mkdir(parents=True)
        store = cls(root)
        return store, store.commit(show.model_copy(update={"revision": 0}), "Create show", "cli")

    @classmethod
    def open(cls, path: Path) -> ShowStore:
        root = path.parent if path.name == SHOW_FILE else path
        if not (root / SHOW_FILE).is_file():
            raise StoreError(f"no {SHOW_FILE} in {root}")
        return cls(root)

    @property
    def show_path(self) -> Path:
        return self.root / SHOW_FILE

    @property
    def revisions_dir(self) -> Path:
        return self.root / "revisions"

    def load(self) -> Show:
        return Show.model_validate_json(self.show_path.read_text(encoding="utf-8"))

    def current_revision(self) -> int:
        if not self.show_path.is_file():
            return 0
        return int(json.loads(self.show_path.read_text(encoding="utf-8"))["revision"])

    def load_revision(self, revision: int) -> Show:
        path = self.revisions_dir / f"{revision:04d}.json"
        if not path.is_file():
            raise StoreError(f"no revision {revision}")
        return Show.model_validate_json(path.read_text(encoding="utf-8"))

    def history(self) -> list[RevisionInfo]:
        index = self.revisions_dir / "index.jsonl"
        if not index.is_file():
            return []
        lines = index.read_text(encoding="utf-8").splitlines()
        return [RevisionInfo(**json.loads(line)) for line in lines if line.strip()]

    # --- writing ---

    @contextlib.contextmanager
    def _lock(self, timeout_s: float = 10.0) -> Iterator[None]:
        lock = self.root / LOCK_FILE
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                with contextlib.suppress(FileNotFoundError):
                    if time.time() - lock.stat().st_mtime > STALE_LOCK_S:
                        lock.unlink()
                        continue
                if time.monotonic() > deadline:
                    raise StoreError(f"project is locked by another process ({lock})") from None
                time.sleep(0.05)
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            with contextlib.suppress(FileNotFoundError):
                lock.unlink()

    def commit(self, show: Show, message: str, author: str = "cli") -> Show:
        """Write ``show`` as the next revision. ``show.revision`` must be the current one."""
        with self._lock():
            current = self.current_revision()
            if show.revision != current:
                raise ConflictError(
                    f"show.json is at revision {current}, the change was based on {show.revision}"
                )
            new = Show.model_validate(show.model_dump(mode="json") | {"revision": current + 1})
            text = new.model_dump_json(indent=2)
            self.revisions_dir.mkdir(exist_ok=True)
            (self.revisions_dir / f"{new.revision:04d}.json").write_text(
                text, encoding="utf-8", newline="\n"
            )
            _atomic_write(self.show_path, text)
            info = RevisionInfo(
                revision=new.revision,
                message=message,
                author=author,
                created_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            )
            with (self.revisions_dir / "index.jsonl").open(
                "a", encoding="utf-8", newline="\n"
            ) as f:
                f.write(json.dumps(info.__dict__, ensure_ascii=False) + "\n")
            return new

    def rollback(self, revision: int, author: str = "cli") -> Show:
        """Make the content of ``revision`` current again, as a new revision."""
        old = self.load_revision(revision)
        current = self.current_revision()
        return self.commit(
            old.model_copy(update={"revision": current}), f"Rollback to {revision}", author
        )

    # --- files ---

    def path(self, relative: str) -> Path:
        return self.root / relative

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def new_file(self, folder: str, stem: str, fingerprint: str, suffix: str) -> Path:
        """A fresh path for an artifact; existing files are never overwritten."""
        target_dir = self.root / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        base = f"{stem}_{fingerprint[:8]}"
        candidate, n = target_dir / f"{base}{suffix}", 2
        while candidate.exists():
            candidate, n = target_dir / f"{base}_{n}{suffix}", n + 1
        return candidate

    def workdir(self) -> Path:
        path = self.root / ".work" / uuid.uuid4().hex[:12]
        path.mkdir(parents=True)
        return path

    @property
    def logs_dir(self) -> Path:
        path = self.root / "logs"
        path.mkdir(exist_ok=True)
        return path
