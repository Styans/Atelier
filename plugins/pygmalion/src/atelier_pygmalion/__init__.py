"""Pygmalion (DroneGen) as an Atelier plugin: image_generator + mesh_generator.

Runs inside Pygmalion's venv (see ``bootstrap.py``) and drives its ``JobManager`` directly,
without the HTTP server: one job at a time on the GPU, models unloaded after every call
unless ``keep_loaded`` is set. Pygmalion's own messages (in Russian) are passed on as they are.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from atelier_plugin_sdk import (
    Health,
    ImageGenerator,
    ImageRef,
    ImageSetRef,
    MeshGenerator,
    MeshRef,
    Plugin,
    PluginError,
    ResourceExhausted,
    RunContext,
)

POLL_S = 0.2
OOM_HINT = "not enough GPU memory or RAM: close other GPU programs or switch to a cloud provider"

_lock = threading.Lock()
_manager: Any = None


def _settings() -> dict[str, str]:
    data: dict[str, str] = json.loads(os.environ.get("ATELIER_PLUGIN_SETTINGS", "{}"))
    return data


def _home() -> Path:
    home = _settings().get("home")
    if not home:
        raise PluginError(
            "set plugin_settings.pygmalion.home in atelier.toml (Pygmalion's runtime folder)"
        )
    path = Path(home)
    if not path.is_dir():
        raise PluginError(f"Pygmalion runtime folder not found: {path}")
    return path


def manager() -> Any:
    """Pygmalion's job manager, started on first use (torch loads only when a job runs)."""
    global _manager
    with _lock:
        if _manager is None:
            from dronegen_server.backends import Registry
            from dronegen_server.config import Settings
            from dronegen_server.jobs import JobManager

            settings = Settings.load(_home(), idle_unload_s=0)
            settings.apply_env()  # before Hugging Face libraries are imported
            _manager = JobManager(settings, Registry(settings))
            _manager.start()
        return _manager


def run_job(
    ctx: RunContext,
    kind: str,
    backend: str,
    params: dict[str, Any],
    inputs: dict[str, Any] | None = None,
    span: tuple[float, float] = (0.0, 1.0),
) -> Any:
    """Submit a Pygmalion job and wait, forwarding progress and cancellation."""
    m = manager()
    try:
        job = m.submit(kind, backend, params, inputs)
    except Exception as exc:  # Pygmalion's UserError / BackendUnavailable carry readable text
        raise PluginError(str(exc)) from exc
    low, high = span
    last: tuple[float, str] | None = None
    while job.status not in ("done", "error", "cancelled"):
        if ctx.cancelled():
            m.cancel(job.id)
        state = (low + (high - low) * float(job.progress), str(job.stage))
        if state != last:  # polling is fast; tell the host only what changed
            last = state
            try:
                ctx.progress(*state)
            except PluginError:
                m.cancel(job.id)  # the host asked to stop
        time.sleep(POLL_S)
    if job.status == "cancelled":
        raise PluginError("cancelled")
    if job.status == "error":
        text = str(job.error or "Pygmalion job failed")
        if any(word in text.lower() for word in ("памят", "memory", "oom", "vram")):
            raise ResourceExhausted(text, data={"hint": OOM_HINT})
        raise PluginError(text)
    return job


def output_file(job: Any, role: str) -> Path:
    name = job.files.get(role)
    if not name:
        raise PluginError(f"Pygmalion job {job.id} produced no {role!r}")
    outputs = Path(manager().settings.outputs)
    return outputs / str(name)


def _release(keep_loaded: bool) -> None:
    if not keep_loaded and _manager is not None:
        _manager.unload_all()


def _backends(kind: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """Pygmalion's own backends of a kind (``txt2img``, ``img2mesh``), read when the plugin
    loads: a backend added to Pygmalion shows up here without a change to this plugin."""
    try:
        from dronegen_server.backends import Registry
        from dronegen_server.config import Settings

        found = tuple(
            backend.name
            for backend in Registry(Settings.load(_home())).all()
            if backend.kind == kind and backend.name not in ("parametric", "template")
        )
    except Exception:  # no Pygmalion here (contract tests, docs): the last known list
        return fallback
    return found or fallback


IMAGE_BACKENDS = _backends("txt2img", ("zimage_turbo", "sdxl", "mock"))
MESH_BACKENDS = _backends("img2mesh", ("hunyuan", "hunyuan_mv", "triposr", "silhouette", "mock"))


def _default(names: tuple[str, ...], preferred: str) -> str:
    return preferred if preferred in names else names[0]


class ImagesParams(BaseModel):
    backend: Literal[IMAGE_BACKENDS] = Field(  # type: ignore[valid-type]
        _default(IMAGE_BACKENDS, "zimage_turbo"),
        description="Pygmalion's image models; zimage_turbo ignores the negative prompt",
    )
    width: int = Field(768, ge=256, le=2048)
    height: int = Field(768, ge=256, le=2048)
    steps: int | None = Field(None, ge=1, le=150, description="None = the model's default")
    keep_loaded: bool = Field(False, description="Keep the model in VRAM after the call")


class Images(ImageGenerator[ImagesParams]):
    """Concept images from a prompt, one Pygmalion job per image (seed, seed + 1, ...)."""

    name = "images"
    version = "0.1.0"
    Params = ImagesParams

    def generate(
        self, ctx: RunContext, prompt: str, negative: str | None, n: int, params: ImagesParams
    ) -> ImageSetRef:
        folder = ctx.output_path("concepts")
        folder.mkdir(parents=True, exist_ok=True)
        names = []
        try:
            for i in range(n):
                job_params: dict[str, Any] = {
                    "prompt": prompt,
                    "negative": negative or "",
                    "width": params.width,
                    "height": params.height,
                    "seed": (ctx.seed + i) % 2**31,
                }
                if params.steps is not None:
                    job_params["steps"] = params.steps
                job = run_job(ctx, "txt2img", params.backend, job_params, span=(i / n, (i + 1) / n))
                name = f"concept_{i + 1:02d}.png"
                shutil.copyfile(output_file(job, "image"), folder / name)
                names.append(name)
        finally:
            _release(params.keep_loaded)
        return ImageSetRef(path=folder, meta={"files": names, "backend": params.backend})


class MeshParams(BaseModel):
    backend: Literal[MESH_BACKENDS] = Field(  # type: ignore[valid-type]
        _default(MESH_BACKENDS, "hunyuan"),
        description=(
            "hunyuan: Hunyuan3D-2mini from one picture; hunyuan_mv: from a sheet of four views"
            " (more precise shape); silhouette runs on the CPU"
        ),
    )
    remove_bg: bool = True
    mc_resolution: int | None = Field(None, ge=32, le=512, description="None = the model's default")
    target_faces: int = Field(0, ge=0, description="Decimate to this many faces (0 = keep)")
    keep_loaded: bool = Field(False, description="Keep the model in VRAM after the call")


class Mesh(MeshGenerator[MeshParams]):
    """A 3D model (GLB, vertex colours) from one concept image."""

    name = "mesh"
    version = "0.1.0"
    Params = MeshParams

    def generate(self, ctx: RunContext, source: ImageRef | str, params: MeshParams) -> MeshRef:
        if not isinstance(source, ImageRef):
            raise PluginError("Pygmalion builds meshes from an image, not from text")
        from PIL import Image

        job_params: dict[str, Any] = {
            "remove_bg": params.remove_bg,
            "target_faces": params.target_faces,
            "seed": ctx.seed % 2**31,  # Hunyuan would otherwise always use seed 1
        }
        if params.mc_resolution is not None:
            job_params["mc_resolution"] = params.mc_resolution
        with Image.open(source.path) as image:
            inputs = {"image": image.convert("RGBA")}
        try:
            job = run_job(ctx, "img2mesh", params.backend, job_params, inputs)
            target = ctx.output_path("mesh.glb")
            shutil.copyfile(output_file(job, "mesh"), target)
        finally:
            _release(params.keep_loaded)
        return MeshRef(path=target, meta={"backend": params.backend, "result": dict(job.result)})


def health() -> Health:
    try:
        from dronegen_server.backends import Registry
        from dronegen_server.config import Settings

        registry = Registry(Settings.load(_home()))
    except PluginError as exc:
        return Health.error(exc.message)
    except Exception as exc:
        return Health.error(f"cannot load Pygmalion: {type(exc).__name__}: {exc}")
    states = []
    for backend in registry.all():
        if backend.name in ("parametric", "template"):
            continue
        ok, reason = backend.check()
        states.append(f"{backend.kind}/{backend.name}: {'ok' if ok else reason}")
    usable = any(state.endswith(": ok") and "/mock" not in state for state in states)
    message = "; ".join(states)
    return Health.ok(message) if usable else Health(status="degraded", message=message)


plugin = Plugin(components=[Images(), Mesh()], health_check=health)
