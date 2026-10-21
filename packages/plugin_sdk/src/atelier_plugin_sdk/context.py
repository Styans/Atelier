"""What a component gets from the host for one call."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import overload

import numpy as np

from atelier_plugin_sdk.artifacts import (
    CompiledShow,
    CompiledShowRef,
    MeshData,
    MeshRef,
    PointSet,
    PointSetRef,
)
from atelier_plugin_sdk.errors import Cancelled

ProgressCallback = Callable[[float, str], None]
LogCallback = Callable[[str, str], None]


class RunContext:
    """Per-call services: seed and RNG, a scratch folder, progress, logging, cancellation.

    ``config`` carries host settings a plugin may need (e.g. ``blender_exe``).
    """

    def __init__(
        self,
        *,
        seed: int,
        workdir: Path,
        config: Mapping[str, str] | None = None,
        on_progress: ProgressCallback | None = None,
        on_log: LogCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.seed = seed
        self.workdir = workdir
        self.config: Mapping[str, str] = dict(config or {})
        self._on_progress = on_progress
        self._on_log = on_log
        self._cancel_event = cancel_event or threading.Event()

    def rng(self) -> np.random.Generator:
        return np.random.default_rng(self.seed)

    def output_path(self, name: str) -> Path:
        self.workdir.mkdir(parents=True, exist_ok=True)
        return self.workdir / name

    @overload
    def save(self, obj: PointSet, name: str) -> PointSetRef: ...
    @overload
    def save(self, obj: MeshData, name: str) -> MeshRef: ...
    @overload
    def save(self, obj: CompiledShow, name: str) -> CompiledShowRef: ...

    def save(
        self, obj: PointSet | MeshData | CompiledShow, name: str
    ) -> PointSetRef | MeshRef | CompiledShowRef:
        path = obj.save(self.output_path(name))
        if isinstance(obj, PointSet):
            return PointSetRef(path=path)
        if isinstance(obj, MeshData):
            return MeshRef(path=path)
        return CompiledShowRef(path=path)

    def progress(self, fraction: float, message: str = "") -> None:
        self.check_cancelled()
        if self._on_progress is not None:
            self._on_progress(min(max(fraction, 0.0), 1.0), message)

    def log(self, message: str, level: str = "info") -> None:
        if self._on_log is not None:
            self._on_log(level, message)

    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise Cancelled("cancelled by the host")
