"""SQLite tables: ``jobs`` (the queue) and ``shows`` (an index; ``show.json`` stays the truth).

The API enqueues, the single worker claims jobs one at a time, both read progress. SQLite in
WAL mode lets the two processes share the file; claiming is one atomic UPDATE ... RETURNING.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event, literal_column, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlmodel import Field, Session, SQLModel, create_engine, select

from atelier_domain import Show
from atelier_show_engine.store import COMMIT_LISTENERS, RevisionInfo, ShowStore

JOB_KINDS = (
    "points",
    "compile",
    "validate",
    "export",
    "generate_images",
    "generate_mesh",
    "assistant_turn",
)
GPU_KINDS = frozenset({"generate_images", "generate_mesh"})
ACTIVE = ("queued", "running")


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: str = Field(primary_key=True)
    show_id: str = Field(index=True)
    kind: str
    args: str = "{}"  # JSON
    status: str = Field(default="queued", index=True)  # queued running done failed cancelled
    progress: float = 0.0
    message: str = ""
    cancel_requested: bool = False
    gpu: bool = False
    priority: int = 0  # higher is claimed first; a chat turn jumps the queue (ADR-036)
    worker_pid: int | None = None
    created_at: float = Field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    updated_at: float = Field(default_factory=time.time, index=True)
    result: str | None = None  # JSON
    error: str | None = None
    log_path: str | None = None

    def public(self) -> dict[str, Any]:
        data = self.model_dump()
        data["args"] = json.loads(self.args)
        data["result"] = json.loads(self.result) if self.result else None
        return data


class ShowEntry(SQLModel, table=True):
    __tablename__ = "shows"

    id: str = Field(primary_key=True)
    title: str
    path: str
    revision: int
    drones: int
    updated_at: float = Field(default_factory=time.time, index=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class JobQueue:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine: Engine = create_engine(
            f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False, "timeout": 30}
        )
        event.listen(self.engine, "connect", _sqlite_pragmas)
        SQLModel.metadata.create_all(self.engine)
        _add_priority_column(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as session:
            yield session

    # --- queue ---

    def submit(
        self, show_id: str, kind: str, args: dict[str, Any] | None = None, *, priority: int = 0
    ) -> Job:
        if kind not in JOB_KINDS:
            raise ValueError(f"unknown job kind {kind!r}; known: {', '.join(JOB_KINDS)}")
        job = Job(
            id=f"j_{uuid.uuid4().hex[:12]}",
            show_id=show_id,
            kind=kind,
            args=json.dumps(args or {}),
            gpu=kind in GPU_KINDS,
            priority=priority,
        )
        with self.session() as session:
            session.add(job)
            session.commit()
        return job

    def claim(self, pid: int) -> Job | None:
        """Take the queued job with the highest priority, oldest first; atomic, so two workers
        never get the same one. A running job is never preempted.

        Order within a priority is SQLite's rowid (insertion order): jobs submitted back to back
        can share a ``created_at``, and the ids are random.
        """
        now = time.time()
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "UPDATE jobs SET status='running', worker_pid=:pid, started_at=:now, "
                    "updated_at=:now, message='' WHERE id = (SELECT id FROM jobs "
                    "WHERE status='queued' ORDER BY priority DESC, rowid LIMIT 1) "
                    "AND status='queued' RETURNING id"
                ),
                {"pid": pid, "now": now},
            ).first()
        return self.get(row[0]) if row else None

    def get(self, job_id: str) -> Job | None:
        with self.session() as session:
            return session.get(Job, job_id)

    def jobs(self, show_id: str | None = None, limit: int = 50) -> list[Job]:
        with self.session() as session:
            query = select(Job).order_by(literal_column("rowid").desc()).limit(limit)
            if show_id:
                query = query.where(Job.show_id == show_id)
            return list(session.exec(query))

    def _update(self, job_id: str, **values: Any) -> None:
        with self.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            for key, value in values.items():
                setattr(job, key, value)
            job.updated_at = time.time()
            session.add(job)
            session.commit()

    def set_progress(self, job_id: str, fraction: float, message: str) -> None:
        self._update(job_id, progress=fraction, message=message)

    def set_log(self, job_id: str, path: Path) -> None:
        self._update(job_id, log_path=str(path))

    def finish(
        self, job_id: str, status: str, *, result: Any = None, error: str | None = None
    ) -> None:
        values: dict[str, Any] = {"status": status, "finished_at": time.time(), "error": error}
        if status == "done":
            values["progress"] = 1.0
        if result is not None:
            values["result"] = json.dumps(result, ensure_ascii=False, default=str)
        self._update(job_id, **values)

    def request_cancel(self, job_id: str) -> Job | None:
        """A queued job is cancelled at once; a running one is asked to stop."""
        job = self.get(job_id)
        if job is None:
            return None
        if job.status == "queued":
            self.finish(job_id, "cancelled", error="cancelled before it started")
        elif job.status == "running":
            self._update(job_id, cancel_requested=True, message="stopping...")
        return self.get(job_id)

    def cancel_requested(self, job_id: str) -> bool:
        job = self.get(job_id)
        return bool(job and job.cancel_requested)

    def recover(self, alive: Callable[[int], bool] = _pid_alive) -> list[str]:
        """Mark jobs left running by a worker that died as failed; returns their ids."""
        orphans = [
            job.id
            for job in self.jobs(limit=1000)
            if job.status == "running" and not alive(job.worker_pid or -1)
        ]
        for job_id in orphans:
            self.finish(job_id, "failed", error="the worker stopped while running this job")
        return orphans

    def changes_since(self, since: float) -> tuple[list[Job], list[ShowEntry]]:
        with self.session() as session:
            jobs = list(session.exec(select(Job).where(Job.updated_at > since)))
            shows = list(session.exec(select(ShowEntry).where(ShowEntry.updated_at > since)))
        return jobs, shows

    # --- shows index ---

    def index_show(self, store: ShowStore, show: Show) -> None:
        with self.session() as session:
            entry = session.get(ShowEntry, show.id) or ShowEntry(
                id=show.id, title="", path="", revision=0, drones=0
            )
            entry.title = show.brief.title
            entry.path = str(store.root)
            entry.revision = show.revision
            entry.drones = show.brief.fleet.drone_count
            entry.updated_at = time.time()
            session.add(entry)
            session.commit()


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def _add_priority_column(engine: Engine) -> None:
    """``create_all`` never alters a table, so a database made before ``priority`` gets it here."""
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
        if "priority" in columns:
            return
        try:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"))
        except OperationalError as error:
            # The API and the worker start together; the other one may have just added it.
            if "duplicate column" not in str(error):
                raise


def index_commits(queue: JobQueue) -> Callable[[ShowStore, Show, RevisionInfo], None]:
    """Keep the shows index current on every commit made in this process."""

    def listener(store: ShowStore, show: Show, _info: RevisionInfo) -> None:
        queue.index_show(store, show)

    COMMIT_LISTENERS.append(listener)
    return listener
