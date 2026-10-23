import sqlite3
import time
from pathlib import Path

import pytest

from atelier_domain import Brief, Show, fleet_from_preset
from atelier_jobs import JobQueue, index_commits
from atelier_show_engine.config import DEFAULT_PIPELINE
from atelier_show_engine.store import COMMIT_LISTENERS, ShowStore


@pytest.fixture
def queue(tmp_path: Path) -> JobQueue:
    return JobQueue(tmp_path / "atelier.db")


def test_jobs_are_claimed_oldest_first_and_once(queue: JobQueue) -> None:
    first = queue.submit("show-a", "points", {"model_id": "m"})
    second = queue.submit("show-a", "compile")

    claimed = [queue.claim(pid=1), queue.claim(pid=2), queue.claim(pid=3)]

    assert [j.id if j else None for j in claimed] == [first.id, second.id, None]
    assert claimed[0] is not None and claimed[0].status == "running" and claimed[0].worker_pid == 1


def test_order_holds_when_jobs_share_a_timestamp(
    queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "time", lambda: 1_000.0)
    ids = [queue.submit("s", "compile").id for _ in range(20)]

    assert [queue.claim(pid=1).id for _ in ids] == ids  # type: ignore[union-attr]
    assert [j.id for j in queue.jobs(limit=20)] == ids[::-1]


def test_higher_priority_is_claimed_first_ties_stay_fifo(queue: JobQueue) -> None:
    running = queue.submit("s", "generate_images", {"prompt": "x"})
    assert queue.claim(pid=1).id == running.id  # type: ignore[union-attr]
    first = queue.submit("s", "compile")
    second = queue.submit("s", "validate")
    turn = queue.submit("s", "assistant_turn", priority=10)
    later_turn = queue.submit("s", "assistant_turn", priority=10)

    order = [queue.claim(pid=1) for _ in range(4)]

    assert [j.id for j in order if j] == [turn.id, later_turn.id, first.id, second.id]
    assert queue.get(running.id).status == "running"  # type: ignore[union-attr]
    assert not turn.gpu and turn.priority == 10 and first.priority == 0


def test_database_without_priority_gets_the_column(tmp_path: Path) -> None:
    path = tmp_path / "atelier.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE jobs (id VARCHAR PRIMARY KEY, show_id VARCHAR, kind VARCHAR, args VARCHAR, "
        "status VARCHAR, progress FLOAT, message VARCHAR, cancel_requested BOOLEAN, gpu BOOLEAN, "
        "worker_pid INTEGER, created_at FLOAT, started_at FLOAT, finished_at FLOAT, "
        "updated_at FLOAT, result VARCHAR, error VARCHAR, log_path VARCHAR)"
    )
    old.execute(
        "INSERT INTO jobs VALUES ('j_old', 's', 'compile', '{}', 'queued', 0, '', 0, 0, NULL, "
        "1, NULL, NULL, 1, NULL, NULL, NULL)"
    )
    old.commit()
    old.close()

    queue = JobQueue(path)
    JobQueue(path)  # a second start finds the column in place
    urgent = queue.submit("s", "assistant_turn", priority=10)

    assert queue.get("j_old").priority == 0  # type: ignore[union-attr]
    assert [queue.claim(pid=1).id for _ in range(2)] == [urgent.id, "j_old"]  # type: ignore[union-attr]


def test_gpu_kinds_are_flagged(queue: JobQueue) -> None:
    assert queue.submit("s", "generate_images", {"prompt": "x"}).gpu
    assert not queue.submit("s", "compile").gpu
    with pytest.raises(ValueError, match="unknown job kind"):
        queue.submit("s", "dance")


def test_cancel_queued_and_running(queue: JobQueue) -> None:
    waiting = queue.submit("s", "compile")
    running = queue.submit("s", "validate")
    queue.claim(pid=1)  # takes `waiting`
    queue.request_cancel(running.id)
    queue.request_cancel(waiting.id)

    assert queue.get(running.id).status == "cancelled"  # type: ignore[union-attr]
    assert queue.cancel_requested(waiting.id)
    assert queue.get(waiting.id).status == "running"  # type: ignore[union-attr]


def test_orphans_of_a_dead_worker_fail(queue: JobQueue) -> None:
    job = queue.submit("s", "compile")
    queue.claim(pid=999_999)

    assert queue.recover(alive=lambda pid: False) == [job.id]
    assert queue.get(job.id).status == "failed"  # type: ignore[union-attr]


def test_finish_stores_the_result(queue: JobQueue) -> None:
    job = queue.submit("s", "compile")
    queue.claim(pid=1)
    queue.set_progress(job.id, 0.5, "half")
    queue.finish(job.id, "done", result={"frames": 10})

    public = queue.get(job.id).public()  # type: ignore[union-attr]
    assert public["status"] == "done" and public["progress"] == 1.0
    assert public["result"] == {"frames": 10}


def test_changes_since_and_show_index(queue: JobQueue, tmp_path: Path) -> None:
    since = time.time() - 1
    listener = index_commits(queue)
    try:
        brief = Brief(title="Индекс", duration_s=60, fleet=fleet_from_preset("conservative", 9))
        ShowStore.create(
            tmp_path / "projects", Show(id="idx-1", brief=brief, pipeline=DEFAULT_PIPELINE)
        )
        queue.submit("idx-1", "compile")
    finally:
        COMMIT_LISTENERS.remove(listener)

    jobs, shows = queue.changes_since(since)
    assert len(jobs) == 1
    assert [(s.id, s.title, s.revision, s.drones) for s in shows] == [("idx-1", "Индекс", 1, 9)]
