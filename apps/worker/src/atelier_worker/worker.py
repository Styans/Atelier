"""Runs queued jobs one at a time, so at most one GPU job ever runs (SPEC §4.6, ADR-023)."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from atelier_jobs import Job, JobQueue, index_commits
from atelier_plugin_sdk import Cancelled
from atelier_show_engine.compiler import CompileError
from atelier_show_engine.config import AtelierConfig, load_config
from atelier_show_engine.importing import AssetImportError
from atelier_show_engine.service import ServiceError, ShowService
from atelier_show_engine.store import ShowStore, StoreError

PROGRESS_INTERVAL_S = 0.25
EXPECTED = (ServiceError, CompileError, StoreError, AssetImportError, ValueError, KeyError)
log = logging.getLogger("atelier.worker")


class JobCancelled(Cancelled):
    """Raised from the progress callback when the user asked to stop."""


def queue_path(config: AtelierConfig) -> Path:
    return config.cache_dir / "atelier.db"


class Worker:
    def __init__(self, service: ShowService, queue: JobQueue) -> None:
        self.service = service
        self.queue = queue
        self.pid = os.getpid()
        logging.getLogger("atelier").setLevel(logging.INFO)  # job logs need INFO

    def run_once(self) -> Job | None:
        """Claim and run one job; None if the queue is empty."""
        job = self.queue.claim(self.pid)
        if job is None:
            return None
        self.execute(job)
        return self.queue.get(job.id)

    def run_forever(self, stop: threading.Event, poll_s: float = 0.5) -> None:
        orphans = self.queue.recover()
        if orphans:
            log.warning("marked %d orphaned jobs as failed", len(orphans))
        while not stop.is_set():
            if self.run_once() is None:
                stop.wait(poll_s)

    def _progress(self, job: Job) -> Callable[[float, str], None]:
        last = [0.0]

        def report(fraction: float, message: str) -> None:
            now = time.monotonic()
            if now - last[0] >= PROGRESS_INTERVAL_S or fraction >= 1.0:
                last[0] = now
                self.queue.set_progress(job.id, fraction, message)
                if self.queue.cancel_requested(job.id):
                    raise JobCancelled("cancelled by the user")

        return report

    def execute(self, job: Job) -> None:
        args: dict[str, Any] = json.loads(job.args)
        handler: logging.Handler = logging.NullHandler()
        try:
            store = self.service.open(job.show_id)
            log_path = store.logs_dir / "jobs" / f"{job.id}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_path, encoding="utf-8")
            logging.getLogger("atelier").addHandler(handler)
            self.queue.set_log(job.id, log_path)
            log.info("job %s: %s %s", job.id, job.kind, args)
            result = self._dispatch(store, job, args, self._progress(job))
        except Exception as exc:
            if self.queue.cancel_requested(job.id):
                self.queue.finish(job.id, "cancelled", error="cancelled by the user")
            elif isinstance(exc, EXPECTED):
                log.info("job %s failed: %s", job.id, exc)
                self.queue.finish(job.id, "failed", error=str(exc))
            else:
                log.error("job %s crashed:\n%s", job.id, traceback.format_exc())
                self.queue.finish(job.id, "failed", error=f"{type(exc).__name__}: {exc}")
        else:
            log.info("job %s done", job.id)
            self.queue.finish(job.id, "done", result=result)
        finally:
            logging.getLogger("atelier").removeHandler(handler)
            handler.close()

    def _dispatch(
        self,
        store: ShowStore,
        job: Job,
        args: dict[str, Any],
        progress: Callable[[float, str], None],
    ) -> Any:
        svc = self.service
        if job.kind == "points":
            record = svc.compute_points(
                store, args["model_id"], args.get("n"), on_progress=progress
            )
            return {
                "slot": record.slot,
                "path": record.path,
                "component": record.producer.component,
            }
        if job.kind == "compile":
            record, report = svc.compile(
                store, allow_stale=bool(args.get("allow_stale")), on_progress=progress
            )
            return {
                "path": record.path,
                "frames": report.frames,
                "duration_s": report.duration_s,
                "warnings": report.warnings,
            }
        if job.kind == "validate":
            summary = svc.validate(store, allow_stale=bool(args.get("allow_stale")))
            return {"ok": summary.ok, "errors": summary.errors, "metrics": summary.metrics}
        if job.kind == "export":
            results = svc.export(store, args.get("exporters") or None, on_progress=progress)
            return [
                {
                    "component": r.component,
                    "folder": str(r.folder),
                    "files": [f.name for f in r.files],
                }
                for r in results
            ]
        if job.kind == "generate_images":
            record = svc.generate_concepts(
                store, args["model_id"], args["prompt"], int(args.get("n", 4)), on_progress=progress
            )
            return {"artifact": record.id, "path": record.path}
        if job.kind == "generate_mesh":
            record = svc.generate_mesh(
                store, args["model_id"], args.get("concept"), on_progress=progress
            )
            return {"artifact": record.id, "path": record.path}
        raise ServiceError(f"unknown job kind {job.kind!r}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = load_config()
    queue = JobQueue(queue_path(config))
    index_commits(queue)
    stop = threading.Event()
    with ShowService(config, author="worker") as service:
        log.info("worker %d ready (%s)", os.getpid(), queue.path)
        try:
            Worker(service, queue).run_forever(stop)
        except KeyboardInterrupt:
            stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
