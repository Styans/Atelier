"""Artifact types exchanged between the core and plugins.

Heavy data lives in ``.npz`` files; plugins receive and return *references* (path + metadata),
so the same code works in-process and across a subprocess boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field

FloatArray = npt.NDArray[np.float64]
Float32Array = npt.NDArray[np.float32]
IntArray = npt.NDArray[np.int64]
UInt8Array = npt.NDArray[np.uint8]


# --- colour helpers -------------------------------------------------------------------------


def srgb_to_linear(srgb: npt.ArrayLike) -> Float32Array:
    """sRGB in [0, 1] -> linear RGB in [0, 1]."""
    c = np.clip(np.asarray(srgb, dtype=np.float64), 0.0, 1.0)
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return lin.astype(np.float32)


def linear_to_srgb8(linear: npt.ArrayLike) -> UInt8Array:
    """Linear RGB in [0, 1] -> sRGB bytes."""
    c = np.clip(np.asarray(linear, dtype=np.float64), 0.0, 1.0)
    srgb = np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)
    return np.round(srgb * 255.0).astype(np.uint8)


def hex_to_linear(color: str) -> Float32Array:
    value = color.lstrip("#")
    srgb = [int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    return srgb_to_linear(srgb)


def arrays_hash(arrays: dict[str, np.ndarray[Any, Any]], extra: Any = None) -> str:
    """sha256 over array contents, dtypes and shapes (npz bytes carry zip timestamps)."""
    digest = hashlib.sha256()
    for name in sorted(arrays):
        array = np.ascontiguousarray(arrays[name])
        digest.update(f"{name}:{array.dtype.str}:{array.shape};".encode())
        digest.update(array.tobytes())
    if extra is not None:
        digest.update(json.dumps(extra, sort_keys=True).encode())
    return digest.hexdigest()


def _save_npz(path: Path, arrays: dict[str, np.ndarray[Any, Any]], meta: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"__meta__": np.array(json.dumps(meta)), **arrays}
    with path.open("wb") as fh:
        np.savez_compressed(fh, **payload)
    return path


def _load_npz(path: Path) -> tuple[dict[str, np.ndarray[Any, Any]], dict[str, Any]]:
    with np.load(path, allow_pickle=False) as data:
        arrays = {k: data[k] for k in data.files if k != "__meta__"}
        meta = json.loads(str(data["__meta__"])) if "__meta__" in data.files else {}
    return arrays, meta


# --- data containers ------------------------------------------------------------------------


@dataclass
class MeshData:
    """An indexed triangle mesh in show coordinates (Z-up, meters).

    Colours: ``vertex_colors`` and ``base_color`` are linear RGB; ``texture`` is sRGB bytes,
    sampled through ``uv`` (v = 0 at the bottom row).
    """

    vertices: FloatArray
    faces: IntArray
    vertex_colors: Float32Array | None = None
    uv: Float32Array | None = None
    texture: UInt8Array | None = None
    base_color: Float32Array = field(default_factory=lambda: np.ones(4, dtype=np.float32))

    def __post_init__(self) -> None:
        self.vertices = np.asarray(self.vertices, dtype=np.float64).reshape(-1, 3)
        self.faces = np.asarray(self.faces, dtype=np.int64).reshape(-1, 3)
        if len(self.faces) == 0:
            raise ValueError("mesh has no faces")
        if self.faces.min() < 0 or self.faces.max() >= len(self.vertices):
            raise ValueError("face index out of range")

    def triangles(self) -> FloatArray:
        """Corner positions, shape (F, 3, 3)."""
        return self.vertices[self.faces]

    def face_areas(self) -> FloatArray:
        t = self.triangles()
        return 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)

    def face_normals(self) -> FloatArray:
        t = self.triangles()
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        length = np.linalg.norm(n, axis=1, keepdims=True)
        return np.divide(n, length, out=np.zeros_like(n), where=length > 0)

    def sample_colors(self, tri_index: npt.ArrayLike, barycentric: npt.ArrayLike) -> Float32Array:
        """Linear RGB at surface points: base colour x vertex colour x texture."""
        tri = np.asarray(tri_index, dtype=np.int64)
        bary = np.asarray(barycentric, dtype=np.float64)
        color = np.tile(self.base_color[:3].astype(np.float64), (len(tri), 1))
        corners = self.faces[tri]
        if self.vertex_colors is not None:
            color *= np.einsum("nk,nkc->nc", bary, self.vertex_colors[corners].astype(np.float64))
        if self.texture is not None and self.uv is not None:
            uv = np.einsum("nk,nkc->nc", bary, self.uv[corners].astype(np.float64))
            color *= _sample_texture(self.texture, uv)
        return np.clip(color, 0.0, 1.0).astype(np.float32)

    def save(self, path: Path) -> Path:
        arrays: dict[str, np.ndarray[Any, Any]] = {
            "vertices": self.vertices,
            "faces": self.faces,
            "base_color": self.base_color,
        }
        for name in ("vertex_colors", "uv", "texture"):
            value = getattr(self, name)
            if value is not None:
                arrays[name] = value
        return _save_npz(path, arrays, {"type": "mesh"})

    @classmethod
    def load(cls, path: Path) -> MeshData:
        arrays, _ = _load_npz(path)
        return cls(**arrays)

    def content_hash(self) -> str:
        arrays: dict[str, np.ndarray[Any, Any]] = {
            "vertices": self.vertices,
            "faces": self.faces,
            "base_color": self.base_color,
        }
        for name in ("vertex_colors", "uv", "texture"):
            value = getattr(self, name)
            if value is not None:
                arrays[name] = value
        return arrays_hash(arrays)


def _sample_texture(texture: UInt8Array, uv: FloatArray) -> FloatArray:
    """Bilinear lookup with repeat wrapping; returns linear RGB."""
    tex = srgb_to_linear(texture[..., :3].astype(np.float64) / 255.0).astype(np.float64)
    if tex.ndim == 2:
        tex = np.repeat(tex[..., None], 3, axis=2)
    h, w = tex.shape[:2]
    # Repeat outside [0, 1] only, so the edges u = 1 / v = 1 stay on their own side.
    u, v = (np.where((c < 0) | (c > 1), c % 1.0, c) for c in (uv[:, 0], uv[:, 1]))
    x = u * (w - 1)
    y = (1.0 - v) * (h - 1)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    top = tex[y0, x0] * (1 - fx) + tex[y0, x1] * fx
    bottom = tex[y1, x0] * (1 - fx) + tex[y1, x1] * fx
    result: FloatArray = top * (1 - fy) + bottom * fy
    return result


@dataclass
class PointSet:
    """Drone positions on a surface, bound to triangles so they can follow deformations."""

    positions: FloatArray
    normals: FloatArray
    colors: Float32Array
    tri_index: npt.NDArray[np.int32]
    barycentric: FloatArray
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.positions = np.asarray(self.positions, dtype=np.float64)
        self.normals = np.asarray(self.normals, dtype=np.float64)
        self.colors = np.asarray(self.colors, dtype=np.float32)
        self.tri_index = np.asarray(self.tri_index, dtype=np.int32)
        self.barycentric = np.asarray(self.barycentric, dtype=np.float64)
        n = len(self.positions)
        shapes = {
            "positions": (self.positions.shape, (n, 3)),
            "normals": (self.normals.shape, (n, 3)),
            "colors": (self.colors.shape, (n, 3)),
            "tri_index": (self.tri_index.shape, (n,)),
            "barycentric": (self.barycentric.shape, (n, 3)),
        }
        for name, (actual, expected) in shapes.items():
            if actual != expected:
                raise ValueError(f"PointSet.{name} has shape {actual}, expected {expected}")

    def __len__(self) -> int:
        return len(self.positions)

    def _arrays(self) -> dict[str, np.ndarray[Any, Any]]:
        return {
            "positions": self.positions,
            "normals": self.normals,
            "colors": self.colors,
            "tri_index": self.tri_index,
            "barycentric": self.barycentric,
        }

    def save(self, path: Path) -> Path:
        return _save_npz(path, self._arrays(), {"type": "point_set", "meta": self.meta})

    @classmethod
    def load(cls, path: Path) -> PointSet:
        arrays, meta = _load_npz(path)
        return cls(**arrays, meta=meta.get("meta", {}))

    def content_hash(self) -> str:
        return arrays_hash(self._arrays())


SegmentKind = Literal["ground", "takeoff", "transition", "hold", "landing"]


class SceneMark(BaseModel):
    """A segment of the compiled timeline; frames are inclusive."""

    kind: SegmentKind
    scene_id: str | None = None
    title: str = ""
    start_frame: int
    end_frame: int


class AudioInfo(BaseModel):
    path: Path
    offset_s: float
    duration_s: float


@dataclass
class CompiledShow:
    """Trajectories of the whole show: ``positions[frame, drone, xyz]``, sRGB LED colours."""

    positions: Float32Array
    colors: UInt8Array
    fps: int
    marks: list[SceneMark] = field(default_factory=list)
    audio: AudioInfo | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.positions = np.asarray(self.positions, dtype=np.float32)
        self.colors = np.asarray(self.colors, dtype=np.uint8)
        if self.positions.ndim != 3 or self.positions.shape[2] != 3:
            raise ValueError("CompiledShow.positions must have shape (frames, drones, 3)")
        if self.colors.shape != self.positions.shape:
            raise ValueError("CompiledShow.colors must match positions")

    @property
    def frame_count(self) -> int:
        return int(self.positions.shape[0])

    @property
    def drone_count(self) -> int:
        return int(self.positions.shape[1])

    @property
    def duration_s(self) -> float:
        return (self.frame_count - 1) / self.fps

    def mark_at(self, frame: int) -> SceneMark | None:
        for mark in self.marks:
            if mark.start_frame <= frame <= mark.end_frame:
                return mark
        return None

    def _meta(self) -> dict[str, Any]:
        return {
            "type": "compiled_show",
            "fps": self.fps,
            "marks": [m.model_dump(mode="json") for m in self.marks],
            "audio": self.audio.model_dump(mode="json") if self.audio else None,
            "meta": self.meta,
        }

    def save(self, path: Path) -> Path:
        return _save_npz(path, {"positions": self.positions, "colors": self.colors}, self._meta())

    @classmethod
    def load(cls, path: Path) -> CompiledShow:
        arrays, meta = _load_npz(path)
        return cls(
            positions=arrays["positions"],
            colors=arrays["colors"],
            fps=int(meta["fps"]),
            marks=[SceneMark.model_validate(m) for m in meta.get("marks", [])],
            audio=AudioInfo.model_validate(meta["audio"]) if meta.get("audio") else None,
            meta=meta.get("meta", {}),
        )

    def content_hash(self) -> str:
        meta = self._meta()
        meta.pop("audio")  # the audio path depends on where the project lives
        return arrays_hash({"positions": self.positions, "colors": self.colors}, meta)


# --- references -----------------------------------------------------------------------------


class ArtifactRef(BaseModel):
    """A file produced or consumed by a plugin."""

    model_config = ConfigDict(frozen=True)

    kind: ClassVar[str]
    path: Path
    meta: dict[str, Any] = Field(default_factory=dict)


class MeshRef(ArtifactRef):
    kind: ClassVar[str] = "mesh"

    def load(self) -> MeshData:
        return MeshData.load(self.path)


class PointSetRef(ArtifactRef):
    kind: ClassVar[str] = "point_set"

    def load(self) -> PointSet:
        return PointSet.load(self.path)


class CompiledShowRef(ArtifactRef):
    kind: ClassVar[str] = "compiled_show"

    def load(self) -> CompiledShow:
        return CompiledShow.load(self.path)


class ImageRef(ArtifactRef):
    kind: ClassVar[str] = "image"


class ImageSetRef(ArtifactRef):
    """Several images; ``path`` is a folder, ``meta['files']`` lists them in order."""

    kind: ClassVar[str] = "image_set"


class ContourRef(ArtifactRef):
    """A 2D outline (polylines) in an ``.npz`` file; produced by formation_source plugins."""

    kind: ClassVar[str] = "contour"


REF_TYPES: dict[str, type[ArtifactRef]] = {
    cls.kind: cls
    for cls in (MeshRef, PointSetRef, CompiledShowRef, ImageRef, ImageSetRef, ContourRef)
}
