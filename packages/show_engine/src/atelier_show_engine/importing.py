"""Bringing user files into a show: meshes (GLB/OBJ/STL/...) and music."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Literal

import numpy as np
import trimesh

from atelier_domain import AssetRef
from atelier_plugin_sdk import MeshData, srgb_to_linear

UpAxis = Literal["y", "z"]

# glTF is Y-up with the model's face towards +Z; shows are Z-up with the face towards -Y.
Y_UP_TO_Z_UP = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])


class AssetImportError(Exception):
    """A user file could not be imported."""


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_asset(project_root: Path, source: Path) -> AssetRef:
    """Copy ``source`` into ``assets/`` under a content-addressed name."""
    digest = file_hash(source)
    target_dir = project_root / "assets"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / f"{digest[:8]}_{source.name}"
    if not target.exists():
        shutil.copyfile(source, target)
    return AssetRef(
        path=target.relative_to(project_root).as_posix(),
        original_name=source.name,
        content_hash=digest,
    )


def _texture_of(visual: Any) -> np.ndarray[Any, Any] | None:
    material = getattr(visual, "material", None)
    image = getattr(material, "baseColorTexture", None) or getattr(material, "image", None)
    if image is None:
        return None
    return np.asarray(image.convert("RGB"), dtype=np.uint8)


def _base_color(visual: Any) -> np.ndarray[Any, Any]:
    factor = getattr(getattr(visual, "material", None), "baseColorFactor", None)
    if factor is None:
        return np.ones(4, dtype=np.float32)
    color = np.asarray(factor, dtype=np.float32).ravel()
    return color / 255.0 if color.max() > 1.0 else color


def _vertex_colors(mesh: Any) -> np.ndarray[Any, Any] | None:
    """Linear RGB per vertex, or None when the mesh carries no colour at all."""
    visual: Any = mesh.visual
    if visual.kind == "texture":
        texture = _texture_of(visual)
        if texture is not None and getattr(visual, "uv", None) is not None:
            colors = visual.to_color().vertex_colors[:, :3] / 255.0  # sampled sRGB texels
            linear: np.ndarray[Any, Any] = srgb_to_linear(colors) * _base_color(visual)[:3]
            return linear
        return np.tile(_base_color(visual)[:3], (len(mesh.vertices), 1)).astype(np.float32)
    if visual.kind == "vertex":
        # glTF COLOR_0 is linear; trimesh hands it over as bytes.
        return (np.asarray(visual.vertex_colors)[:, :3] / 255.0).astype(np.float32)
    return None


def load_mesh_file(path: Path, up: UpAxis = "y") -> MeshData:
    """Read a mesh file and return it in show coordinates (Z-up), unscaled and uncentred.

    A single textured mesh keeps its texture and UVs; several parts are merged with their
    colours baked to vertices.
    """
    try:
        scene: Any = trimesh.load(path, force="scene")
    except Exception as exc:
        raise AssetImportError(f"cannot read {path.name}: {exc}") from exc
    parts: list[Any] = [
        g for g in scene.dump(concatenate=False) if isinstance(g, trimesh.Trimesh) and len(g.faces)
    ]
    if not parts:
        raise AssetImportError(f"{path.name} contains no triangles")

    rotation = Y_UP_TO_Z_UP if up == "y" else np.eye(3)
    single = parts[0] if len(parts) == 1 else None
    if (
        single is not None
        and single.visual.kind == "texture"
        and _texture_of(single.visual) is not None
    ):
        visual: Any = single.visual
        return MeshData(
            vertices=np.asarray(single.vertices) @ rotation.T,
            faces=np.asarray(single.faces),
            uv=np.asarray(visual.uv, dtype=np.float32),
            texture=_texture_of(visual),
            base_color=_base_color(visual),
        )

    vertices, faces, colors = [], [], []
    offset = 0
    any_color = False
    for part in parts:
        part_colors = _vertex_colors(part)
        any_color |= part_colors is not None
        colors.append(
            part_colors
            if part_colors is not None
            else np.ones((len(part.vertices), 3), dtype=np.float32)
        )
        vertices.append(np.asarray(part.vertices))
        faces.append(np.asarray(part.faces) + offset)
        offset += len(part.vertices)
    return MeshData(
        vertices=np.concatenate(vertices) @ rotation.T,
        faces=np.concatenate(faces),
        vertex_colors=np.concatenate(colors).astype(np.float32) if any_color else None,
    )


def probe_audio(path: Path) -> tuple[float, int]:
    """Duration in seconds and sample rate of a music file (WAV, FLAC, OGG, MP3)."""
    import soundfile

    try:
        info = soundfile.info(str(path))
    except Exception as exc:
        raise AssetImportError(f"cannot read audio {path.name}: {exc}") from exc
    return float(info.duration), int(info.samplerate)
