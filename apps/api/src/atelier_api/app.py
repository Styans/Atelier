"""The HTTP API: REST + SSE over ``ShowService`` and the job queue (phase 2, docs/ARCHITECTURE.md).

Cheap edits commit a new revision right away; expensive work (points, compile, validate,
export, generation) is queued for the worker. Every edit may carry ``base_revision``; an edit
of an outdated revision is refused with 409.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import anyio
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from atelier_api import __version__
from atelier_api.binary import mesh_glb, points_payload, show_frames
from atelier_domain import AnimTrack, Show, StageName
from atelier_jobs import JOB_KINDS, JobQueue, index_commits
from atelier_plugin_host import ComponentNotFound, LoadedPlugin
from atelier_plugin_sdk import ROLE_NAMES, CompiledShow, MeshData, PluginError
from atelier_plugin_sdk.roles import RoleName
from atelier_show_engine.compiler import CompileError
from atelier_show_engine.config import AtelierConfig
from atelier_show_engine.importing import AssetImportError, UpAxis
from atelier_show_engine.service import ServiceError, ShowService
from atelier_show_engine.store import ConflictError, ShowStore, StoreError

STAGES: tuple[StageName, ...] = (
    "brief",
    "program",
    "models",
    "points",
    "animation",
    "validation",
    "export",
)
EVENT_POLL_S = 0.3
HEARTBEAT_S = 15.0


# --- request bodies -----------------------------------------------------------------------


class Edit(BaseModel):
    base_revision: int | None = None
    changes: dict[str, Any] = Field(default_factory=dict)


class NewShow(BaseModel):
    title: str
    drones: int = Field(gt=0)
    duration_s: float = Field(600, gt=0)
    preset: str = "conservative"
    viewer: str = "-Y"
    seed: int = 0


class FleetChange(BaseModel):
    base_revision: int | None = None
    preset: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class NewScene(BaseModel):
    base_revision: int | None = None
    model_id: str
    hold_s: float = Field(gt=0)
    title: str | None = None
    transition_s: float | None = None
    offset_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    tracks: list[AnimTrack] = Field(default_factory=list)


class Order(BaseModel):
    base_revision: int | None = None
    order: list[str]


class Stage(BaseModel):
    base_revision: int | None = None
    component: str
    params: dict[str, Any] = Field(default_factory=dict)


class StageList(BaseModel):
    base_revision: int | None = None
    components: list[str]


class Approve(BaseModel):
    base_revision: int | None = None
    status: str = "approved"


class Rollback(BaseModel):
    base_revision: int | None = None


class NewJob(BaseModel):
    kind: str
    args: dict[str, Any] = Field(default_factory=dict)


class MusicOffset(BaseModel):
    base_revision: int | None = None
    offset_s: float | None = None


# --- application ----------------------------------------------------------------------------


def plugin_view(plugin: LoadedPlugin) -> dict[str, Any]:
    manifest = plugin.manifest
    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "runtime": manifest.runtime,
        "roles": list(manifest.roles),
        "requires_gpu": manifest.requires_gpu,
        "description": manifest.description,
        "settings": manifest.settings,
        "status": plugin.status,
        "error": plugin.error,
        "health": plugin.health.model_dump() if plugin.health else None,
        "components": [
            {
                "id": c.id,
                "role": c.role,
                "name": c.name,
                "version": c.version,
                "description": c.description,
                "params_schema": c.params_schema,
            }
            for c in plugin.components
        ],
    }


def create_app(
    config: AtelierConfig,
    *,
    service: ShowService | None = None,
    queue: JobQueue | None = None,
    web_dist: Path | None = None,
) -> FastAPI:
    svc = service or ShowService(config, author="ui")
    jobs = queue or JobQueue(config.cache_dir / "atelier.db")
    index_commits(jobs)
    for show in svc.list_shows():
        jobs.index_show(ShowStore(config.projects_dir / show.id), show)

    app = FastAPI(title="Atelier", version=__version__)
    app.state.service = svc
    app.state.queue = jobs

    # --- errors ---

    @app.exception_handler(ConflictError)
    async def conflict(_request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    for error in (ServiceError, CompileError, AssetImportError, ComponentNotFound, ValueError):

        @app.exception_handler(error)
        async def bad_request(_request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(PluginError)
    async def plugin_error(_request: Request, exc: PluginError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=400)

    def store_for(show_id: str) -> ShowStore:
        root = config.projects_dir / show_id
        if not (root / "show.json").is_file():
            raise HTTPException(404, f"no show {show_id!r}")
        return ShowStore(root)

    @contextmanager
    def editing(show_id: str, base_revision: int | None) -> Iterator[ShowStore]:
        store = store_for(show_id)
        svc.check_revision(store, base_revision)
        yield store

    def view(store: ShowStore, show: Show | None = None) -> dict[str, Any]:
        show = show or store.load()
        return {
            "show": show.model_dump(mode="json"),
            "stale": svc.stale_report(store),
            "jobs": [j.public() for j in jobs.jobs(show.id, limit=20)],
        }

    # --- meta ---

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "version": __version__,
            "presets": {k: v.model_dump() for k, v in config.presets.items()},
            "roles": list(ROLE_NAMES),
            "job_kinds": list(JOB_KINDS),
            "stages": list(STAGES),
        }

    # --- shows ---

    @app.get("/api/shows")
    def list_shows() -> list[dict[str, Any]]:
        return [
            {
                "id": s.id,
                "title": s.brief.title,
                "drones": s.brief.fleet.drone_count,
                "duration_s": s.brief.duration_s,
                "scenes": len(s.program),
                "models": len(s.models),
                "revision": s.revision,
            }
            for s in svc.list_shows()
        ]

    @app.post("/api/shows", status_code=201)
    def create_show(body: NewShow) -> dict[str, Any]:
        store, show = svc.create_show(
            body.title,
            drones=body.drones,
            duration_s=body.duration_s,
            preset=body.preset,
            viewer=cast(Any, body.viewer),
            seed=body.seed,
        )
        return view(store, show)

    @app.get("/api/shows/{show_id}")
    def get_show(show_id: str) -> dict[str, Any]:
        return view(store_for(show_id))

    @app.patch("/api/shows/{show_id}/brief")
    def patch_brief(show_id: str, body: Edit) -> dict[str, Any]:
        with editing(show_id, body.base_revision) as store:
            return view(store, svc.update_brief(store, body.changes))

    @app.patch("/api/shows/{show_id}/flight")
    def patch_flight(show_id: str, body: Edit) -> dict[str, Any]:
        with editing(show_id, body.base_revision) as store:
            return view(store, svc.update_flight(store, body.changes))

    @app.put("/api/shows/{show_id}/fleet")
    def put_fleet(show_id: str, body: FleetChange) -> dict[str, Any]:
        with editing(show_id, body.base_revision) as store:
            if body.preset:
                svc.use_fleet_preset(store, body.preset)
            if body.values:
                svc.set_fleet(store, **body.values)
            return view(store)

    async def _saved_upload(upload: UploadFile, folder: Path) -> Path:
        name = Path(upload.filename or "upload.bin").name
        target = folder / name
        with target.open("wb") as out:
            while chunk := await upload.read(1 << 20):
                out.write(chunk)
        return target

    @app.post("/api/shows/{show_id}/music")
    async def post_music(
        show_id: str,
        file: UploadFile = File(...),
        offset_s: float | None = Form(None),
        base_revision: int | None = Form(None),
    ) -> dict[str, Any]:
        store = store_for(show_id)
        svc.check_revision(store, base_revision)
        with tempfile.TemporaryDirectory() as tmp:
            path = await _saved_upload(file, Path(tmp))
            show = await anyio.to_thread.run_sync(svc.set_music, store, path, offset_s)
        return view(store, show)

    @app.patch("/api/shows/{show_id}/music")
    def patch_music(show_id: str, body: MusicOffset) -> dict[str, Any]:
        with editing(show_id, body.base_revision) as store:
            return view(store, svc.update_music(store, body.offset_s))

    # --- models ---

    @app.post("/api/shows/{show_id}/models", status_code=201)
    async def post_model(
        show_id: str,
        file: UploadFile = File(...),
        name: str | None = Form(None),
        target_height_m: float | None = Form(None),
        up: str = Form("y"),
        color_source: str = Form("texture"),
        base_revision: int | None = Form(None),
    ) -> dict[str, Any]:
        store = store_for(show_id)
        svc.check_revision(store, base_revision)
        with tempfile.TemporaryDirectory() as tmp:
            path = await _saved_upload(file, Path(tmp))

            def add() -> tuple[Show, str]:
                return svc.add_model(
                    store,
                    path,
                    name=name or None,
                    target_height_m=target_height_m,
                    up=cast(UpAxis, up),
                    color_source=color_source,
                )

            show, model_id = await anyio.to_thread.run_sync(add)
        return {**view(store, show), "model_id": model_id}

    @app.patch("/api/shows/{show_id}/models/{model_id}")
    def patch_model(show_id: str, model_id: str, body: Edit) -> dict[str, Any]:
        with editing(show_id, body.base_revision) as store:
            return view(store, svc.update_model(store, model_id, body.changes))

    @app.delete("/api/shows/{show_id}/models/{model_id}")
    def delete_model(
        show_id: str, model_id: str, base_revision: int | None = None
    ) -> dict[str, Any]:
        with editing(show_id, base_revision) as store:
            return view(store, svc.remove_model(store, model_id))

    @app.get("/api/shows/{show_id}/models/{model_id}/mesh.glb")
    def model_mesh(show_id: str, model_id: str) -> Response:
        store = store_for(show_id)
        show = store.load()
        card = show.models.get(model_id)
        if card is None or card.assets.mesh is None:
            raise HTTPException(404, f"model {model_id!r} has no mesh")
        mesh = MeshData.load(store.path(show.artifacts[card.assets.mesh].path))
        return Response(mesh_glb(mesh), media_type="model/gltf-binary")

    @app.get("/api/shows/{show_id}/models/{model_id}/points")
    def model_points(show_id: str, model_id: str, n: int | None = None) -> Response:
        preview = svc.points_preview(store_for(show_id), model_id, n)
        header = {
            "scale": preview.scale,
            "min_distance_m": preview.min_distance_m,
            "stale": preview.stale,
            "metrics": preview.metrics,
        }
        payload = points_payload(preview.positions, preview.colors, preview.nearest_m, header)
        return Response(payload, media_type="application/octet-stream")

    @app.get("/api/shows/{show_id}/models/{model_id}/concepts")
    def model_concepts(show_id: str, model_id: str) -> list[str]:
        return svc.concept_images(store_for(show_id), model_id)

    @app.get("/api/shows/{show_id}/files/{path:path}")
    def project_file(show_id: str, path: str) -> FileResponse:
        """A file of the project (concepts, exports, logs); never outside the project folder."""
        store = store_for(show_id)
        target = (store.root / path).resolve()
        if not target.is_relative_to(store.root) or not target.is_file():
            raise HTTPException(404, "no such file")
        return FileResponse(target, filename=target.name)

    # --- program ---

    @app.post("/api/shows/{show_id}/scenes", status_code=201)
    def post_scene(show_id: str, body: NewScene) -> dict[str, Any]:
        with editing(show_id, body.base_revision) as store:
            svc.add_scene(
                store,
                body.model_id,
                hold_s=body.hold_s,
                title=body.title,
                transition_s=body.transition_s,
                offset_m=body.offset_m,
                rotation_deg=body.rotation_deg,
                tracks=body.tracks,
            )
            return view(store)

    @app.patch("/api/shows/{show_id}/scenes/{scene_id}")
    def patch_scene(show_id: str, scene_id: str, body: Edit) -> dict[str, Any]:
        with editing(show_id, body.base_revision) as store:
            return view(store, svc.update_scene(store, scene_id, body.changes))

    @app.delete("/api/shows/{show_id}/scenes/{scene_id}")
    def delete_scene(
        show_id: str, scene_id: str, base_revision: int | None = None
    ) -> dict[str, Any]:
        with editing(show_id, base_revision) as store:
            return view(store, svc.remove_scene(store, scene_id))

    @app.post("/api/shows/{show_id}/scenes/reorder")
    def reorder(show_id: str, body: Order) -> dict[str, Any]:
        with editing(show_id, body.base_revision) as store:
            return view(store, svc.reorder_scenes(store, body.order))

    # --- pipeline and stages ---

    @app.put("/api/shows/{show_id}/pipeline/{role}")
    def put_stage(show_id: str, role: str, body: Stage) -> dict[str, Any]:
        with editing(show_id, body.base_revision) as store:
            if role in ("validators", "exporters"):
                raise HTTPException(400, f"use PUT …/pipeline/{role}/list")
            return view(
                store, svc.set_stage(store, cast(RoleName, role), body.component, body.params)
            )

    @app.put("/api/shows/{show_id}/pipeline/{which}/list")
    def put_stage_list(show_id: str, which: str, body: StageList) -> dict[str, Any]:
        if which not in ("validators", "exporters"):
            raise HTTPException(404, f"no stage list {which!r}")
        with editing(show_id, body.base_revision) as store:
            return view(store, svc.set_stage_list(store, which, body.components))

    @app.post("/api/shows/{show_id}/stages/{stage}/approve")
    def approve(show_id: str, stage: str, body: Approve) -> dict[str, Any]:
        if stage not in STAGES:
            raise HTTPException(404, f"no stage {stage!r}")
        with editing(show_id, body.base_revision) as store:
            return view(store, svc.approve_stage(store, stage, body.status))

    # --- results ---

    def compiled_for(store: ShowStore) -> CompiledShow:
        show = store.load()
        if show.outputs.compiled is None:
            raise HTTPException(404, "the show is not compiled yet")
        return CompiledShow.load(store.path(show.artifacts[show.outputs.compiled].path))

    @app.get("/api/shows/{show_id}/compiled")
    def compiled_summary(show_id: str) -> dict[str, Any]:
        compiled = compiled_for(store_for(show_id))
        return {
            "frames": compiled.frame_count,
            "drones": compiled.drone_count,
            "fps": compiled.fps,
            "duration_s": compiled.duration_s,
            "marks": [m.model_dump(mode="json") for m in compiled.marks],
            "audio": compiled.audio.model_dump(mode="json") if compiled.audio else None,
            "meta": compiled.meta,
        }

    @app.get("/api/shows/{show_id}/timeline")
    def timeline(show_id: str) -> dict[str, Any]:
        """Exact from the current compiled show, otherwise estimated (ADR-038)."""
        return svc.estimate_timeline(store_for(show_id)).as_dict()

    @app.get("/api/shows/{show_id}/compiled/frames")
    def compiled_frames(show_id: str, fps: float = Query(10.0, gt=0, le=60)) -> Response:
        payload = show_frames(compiled_for(store_for(show_id)), fps)
        return Response(payload, media_type="application/octet-stream")

    @app.get("/api/shows/{show_id}/validation")
    def validation(show_id: str) -> dict[str, Any]:
        store = store_for(show_id)
        show = store.load()
        if show.outputs.validation is None:
            raise HTTPException(404, "the show is not validated yet")
        report: dict[str, Any] = json.loads(
            store.path(show.artifacts[show.outputs.validation].path).read_text(encoding="utf-8")
        )
        return report

    @app.get("/api/shows/{show_id}/exports")
    def exports(show_id: str) -> list[dict[str, Any]]:
        store = store_for(show_id)
        show = store.load()
        result = []
        for component, artifact_id in sorted(show.outputs.exports.items()):
            record = show.artifacts[artifact_id]
            folder = store.path(record.path)
            files = (
                sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
            )
            result.append(
                {
                    "component": component,
                    "folder": record.path,
                    "files": files,
                    "created_at": record.created_at.isoformat(),
                }
            )
        return result

    # --- revisions ---

    @app.get("/api/shows/{show_id}/revisions")
    def revisions(show_id: str) -> list[dict[str, Any]]:
        return [r.__dict__ for r in store_for(show_id).history()]

    @app.get("/api/shows/{show_id}/revisions/{revision}/diff")
    def revision_diff(show_id: str, revision: int, to: int | None = None) -> list[dict[str, Any]]:
        return [d.__dict__ for d in svc.revision_diff(store_for(show_id), revision, to)]

    @app.post("/api/shows/{show_id}/rollback/{revision}")
    def rollback(show_id: str, revision: int, body: Rollback) -> dict[str, Any]:
        with editing(show_id, body.base_revision) as store:
            try:
                show = store.rollback(revision, author=svc.author)
            except StoreError as exc:
                raise HTTPException(404, str(exc)) from exc
            return view(store, show)

    # --- jobs ---

    @app.post("/api/shows/{show_id}/jobs", status_code=202)
    def post_job(show_id: str, body: NewJob) -> dict[str, Any]:
        store_for(show_id)
        return jobs.submit(show_id, body.kind, body.args).public()

    @app.get("/api/jobs")
    def list_jobs(show: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [j.public() for j in jobs.jobs(show, limit)]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id!r}")
        return job.public()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        job = jobs.request_cancel(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id!r}")
        return job.public()

    @app.get("/api/jobs/{job_id}/log")
    def job_log(job_id: str) -> Response:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id!r}")
        text = (
            Path(job.log_path).read_text(encoding="utf-8", errors="replace") if job.log_path else ""
        )
        return Response(text, media_type="text/plain; charset=utf-8")

    # --- plugins ---

    @app.get("/api/plugins")
    def plugins() -> list[dict[str, Any]]:
        return [plugin_view(p) for p in svc.registry.plugins]

    @app.post("/api/plugins/{plugin_id}/{action}")
    def plugin_action(plugin_id: str, action: str) -> dict[str, Any]:
        try:
            if action in ("enable", "disable"):
                svc.set_plugin_enabled(plugin_id, action == "enable")
                return plugin_view(svc.registry.plugin(plugin_id))
            if action == "health":
                return plugin_view(svc.registry.check_health(plugin_id))
        except ComponentNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(404, f"no action {action!r}")

    @app.get("/api/components/{role}")
    def components(role: str) -> list[dict[str, Any]]:
        if role not in ROLE_NAMES:
            raise HTTPException(404, f"no role {role!r}")
        return [
            {
                "id": c.id,
                "plugin_id": c.plugin_id,
                "name": c.name,
                "version": c.version,
                "description": c.description,
                "params_schema": c.params_schema,
            }
            for c in svc.registry.components(role)
        ]

    @app.get("/api/schemas/{role}")
    def union_schema(role: str, discriminator: str = "primitive") -> dict[str, Any]:
        if role not in ROLE_NAMES:
            raise HTTPException(404, f"no role {role!r}")
        return svc.registry.union_schema(role, discriminator)

    # --- events ---

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            since = time.time()
            last_beat = time.monotonic()
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                checked = time.time()
                changed_jobs, changed_shows = await anyio.to_thread.run_sync(
                    jobs.changes_since, since
                )
                since = checked - 0.05  # overlap a little: updates may land in the same instant
                for job in changed_jobs:
                    yield f"event: job\ndata: {json.dumps(job.public(), default=str)}\n\n"
                for entry in changed_shows:
                    yield f"event: show\ndata: {json.dumps(entry.model_dump(), default=str)}\n\n"
                if time.monotonic() - last_beat > HEARTBEAT_S:
                    last_beat = time.monotonic()
                    yield ": keep-alive\n\n"
                await asyncio.sleep(EVENT_POLL_S)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    # --- the web UI ---

    dist = (
        web_dist if web_dist is not None else Path(__file__).resolve().parents[3] / "web" / "dist"
    )
    if (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            target = (dist / path).resolve()
            if path and target.is_relative_to(dist.resolve()) and target.is_file():
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app
