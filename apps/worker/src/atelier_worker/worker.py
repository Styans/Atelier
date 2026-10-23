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

from atelier_assistant.llm import LLMError
from atelier_assistant.runtime import AssistantRuntime
from atelier_jobs import ChatStore, Job, JobQueue, index_commits
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
    def __init__(
        self, service: ShowService, queue: JobQueue, assistant: AssistantRuntime | None = None
    ) -> None:
        self.service = service
        self.queue = queue
        self.chat = ChatStore(queue)
        self.assistant = assistant or AssistantRuntime(service.config)
        self.pid = os.getpid()
        logging.getLogger("atelier").setLevel(logging.INFO)  # job logs need INFO

    def run_once(self) -> Job | None:
        """Claim and run one job; None if the queue is empty."""
        job = self.queue.claim(self.pid)
        if job is None:
            return None
        if job.gpu:
            self.assistant.release()  # the chat model and a GPU job do not fit on one card
        self.execute(job)
        return self.queue.get(job.id)

    def run_forever(self, stop: threading.Event, poll_s: float = 0.5) -> None:
        orphans = self.queue.recover()
        if orphans:
            log.warning("marked %d orphaned jobs as failed", len(orphans))
        while not stop.is_set():
            if self.run_once() is None:
                self.assistant.idle()
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
        if job.kind == "assistant_turn":
            return self._turn(store, job)
        raise ServiceError(f"unknown job kind {job.kind!r}")

    def _turn(self, store: ShowStore, job: Job) -> dict[str, Any]:
        """One chat turn; what goes wrong is told in the chat too, not only in the job."""
        last_check = [0.0]

        def emit(kind: str, payload: dict[str, Any]) -> None:
            self.chat.emit(job.id, kind, payload)
            now = time.monotonic()
            if now - last_check[0] >= PROGRESS_INTERVAL_S:
                last_check[0] = now
                if self.queue.cancel_requested(job.id):
                    raise JobCancelled("cancelled by the user")

        def notice(text: str, error: str) -> None:
            self.chat.append(
                job.show_id,
                "notice",
                [{"type": "text", "text": text}],
                turn_job_id=job.id,
                revision_before=store.current_revision(),
            )
            self.chat.emit(job.id, "error", {"message": error})

        try:
            if not self.assistant.running():
                self.chat.emit(job.id, "status", {"stage": "loading"})
            agent = self.assistant.agent(self.service, self.chat)
            result = agent.run_turn(store, job.show_id, job.id, emit)
        except JobCancelled:
            notice("Ответ остановлен.", "остановлено")
            raise
        except LLMError as exc:
            notice(f"Ассистент не смог ответить: {exc}", str(exc))
            raise ServiceError(str(exc)) from exc
        return {
            "revision_before": result.revision_before,
            "revision_after": result.revision_after,
            "steps": result.steps,
            "tool_calls": result.tool_calls,
            "failures": result.failures,
            "proposals": result.proposals,
            "stopped": result.stopped,
        }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = load_config()
    queue = JobQueue(queue_path(config))
    index_commits(queue)
    stop = threading.Event()
    with ShowService(config, author="worker") as service:
        log.info("worker %d ready (%s)", os.getpid(), queue.path)
        worker = Worker(service, queue)
        try:
            worker.run_forever(stop)
        except KeyboardInterrupt:
            stop.set()
        finally:
            worker.assistant.close()  # frees the chat model's video memory
    return 0


if __name__ == "__main__":
    sys.exit(main())
