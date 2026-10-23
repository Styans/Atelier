"""Chat routes: messages, the stream of a turn, proposals and "roll back this answer".

A user message becomes a job of kind ``assistant_turn`` in the same queue as everything else,
so the single worker runs it and it never overlaps a GPU job (ADR-035). The worker streams
the turn into ``chat_events``; ``/chat/events`` relays them to the browser by polling the
table (ADR-036). Proposals are expensive actions the assistant only suggested: applying one
runs the same job or service method a button in the UI would (ADR-039).
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from atelier_domain import Show
from atelier_jobs import ChatStore, JobQueue
from atelier_jobs.queue import Job
from atelier_show_engine.program import SceneDraft
from atelier_show_engine.service import ServiceError, ShowService
from atelier_show_engine.store import ShowStore

TURN_PRIORITY = 10  # before other waiting jobs, never preempting the running one
CHAT_POLL_S = 0.15
ACTIVE = ("queued", "running")
FINISHED = ("done", "failed", "cancelled")
# Proposal actions that are jobs, and how their arguments map.
JOB_ACTIONS: dict[str, Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]] = {
    "points": lambda a: ("points", {"model_id": a.get("target_id")}),
    "compile": lambda a: ("compile", {}),
    "validate": lambda a: ("validate", {}),
    "export": lambda a: ("export", {}),
    "generate_images": lambda a: (
        "generate_images",
        {"model_id": a.get("target_id"), "prompt": a.get("prompt"), "n": 4},
    ),
}


class NewMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Base(BaseModel):
    base_revision: int | None = None


def active_turn(jobs: JobQueue, show_id: str) -> Job | None:
    return next(
        (
            j
            for j in jobs.jobs(show_id, limit=50)
            if j.kind == "assistant_turn" and j.status in ACTIVE
        ),
        None,
    )


def chat_router(
    svc: ShowService,
    jobs: JobQueue,
    chat: ChatStore,
    store_for: Callable[[str], ShowStore],
    view: Callable[[ShowStore, Show | None], dict[str, Any]],
) -> APIRouter:
    router = APIRouter()
    # One decision at a time: two quick clicks must not run an action twice. The API is one
    # process, so a lock is enough; the store's conditional update guards the rest.
    deciding = threading.Lock()

    @router.get("/api/shows/{show_id}/chat")
    def history(show_id: str) -> dict[str, Any]:
        store_for(show_id)
        turn = active_turn(jobs, show_id)
        return {
            "messages": [m.public() for m in chat.history(show_id)],
            "proposals": [p.public() for p in chat.proposals(show_id)],
            "active_turn": turn.public() if turn else None,
        }

    @router.post("/api/shows/{show_id}/chat", status_code=202)
    def post_message(show_id: str, body: NewMessage) -> dict[str, Any]:
        store_for(show_id)
        if active_turn(jobs, show_id) is not None:
            raise HTTPException(409, "the assistant is still answering the previous message")
        job = jobs.submit(show_id, "assistant_turn", {}, priority=TURN_PRIORITY)
        message = chat.append(
            show_id, "user", [{"type": "text", "text": body.text}], turn_job_id=job.id
        )
        return {"message": message.public(), "job": job.public()}

    @router.get("/api/shows/{show_id}/chat/events")
    async def turn_events(request: Request, show_id: str, turn: str, after: int = 0) -> Any:
        job = jobs.get(turn)
        if job is None or job.show_id != show_id:
            raise HTTPException(404, f"no turn {turn!r} in show {show_id!r}")

        async def stream() -> AsyncIterator[str]:
            cursor = after
            yield "retry: 1000\n\n"
            while not await request.is_disconnected():
                events = await anyio.to_thread.run_sync(chat.events_since, turn, cursor)
                for event in events:
                    cursor = event.id or cursor
                    data = json.dumps(event.public(), ensure_ascii=False)
                    yield f"id: {cursor}\nevent: {event.kind}\ndata: {data}\n\n"
                    if event.kind in ("done", "error"):
                        return
                current = await anyio.to_thread.run_sync(jobs.get, turn)
                if current is None or (current.status in FINISHED and not events):
                    status = current.status if current else "missing"
                    yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                    return
                await asyncio.sleep(CHAT_POLL_S)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @router.post("/api/shows/{show_id}/chat/{turn}/rollback")
    def rollback_turn(show_id: str, turn: str, body: Base) -> dict[str, Any]:
        store = store_for(show_id)
        svc.check_revision(store, body.base_revision)
        befores = [
            m.revision_before
            for m in chat.history(show_id)
            if m.turn_job_id == turn and m.revision_before is not None
        ]
        if not befores:
            raise HTTPException(404, f"turn {turn!r} changed nothing that can be rolled back")
        show = store.rollback(min(befores), author=svc.author)
        return view(store, show)

    @router.post("/api/shows/{show_id}/proposals/{proposal_id}/{decision}")
    def decide(show_id: str, proposal_id: str, decision: str, body: Base) -> dict[str, Any]:
        if decision not in ("apply", "dismiss"):
            raise HTTPException(404, f"no decision {decision!r}")
        store = store_for(show_id)
        proposal = chat.get_proposal(proposal_id)
        if proposal is None or proposal.show_id != show_id:
            raise HTTPException(404, f"no proposal {proposal_id!r}")
        with deciding:
            current = chat.get_proposal(proposal_id)
            if current is None or current.status != "pending":
                status = current.status if current else "gone"
                raise HTTPException(409, f"proposal {proposal_id!r} is already {status}")
            if decision == "dismiss":
                return {"proposal": chat.decide_proposal(proposal_id, "dismissed").public()}
            svc.check_revision(store, body.base_revision)
            args = current.public()["args"]
            job = None
            if current.action in JOB_ACTIONS:
                kind, job_args = JOB_ACTIONS[current.action](args)
                job = jobs.submit(show_id, kind, job_args)
            elif current.action == "remove_scene":
                svc.remove_scene(store, args["target_id"])
            elif current.action == "remove_model":
                svc.remove_model(store, args["target_id"])
            elif current.action == "replace_program":
                drafts = [SceneDraft.model_validate(s) for s in args["scenes"]]
                svc.apply_program(store, drafts, replace=True, message="Program by the assistant")
            else:
                raise ServiceError(f"unknown proposal action {current.action!r}")
            decided = chat.decide_proposal(proposal_id, "applied")
        return {
            "proposal": decided.public(),
            "job": job.public() if job else None,
            "view": view(store, None),
        }

    return router
