"""Compact binary payloads for the 3D viewer (ADR-028).

Layout: ``uint32 little-endian header length`` + UTF-8 JSON header, padded to 4 bytes,
then the arrays listed in ``header["arrays"]`` back to back, each 4-byte aligned.
"""

from __future__ import annotations

import io
import json
import struct
from typing import Any

import numpy as np
import numpy.typing as npt
import trimesh

from atelier_plugin_sdk import CompiledShow, MeshData, linear_to_srgb8


def pack(header: dict[str, Any], arrays: dict[str, np.ndarray[Any, Any]]) -> bytes:
    specs = []
    chunks = []
    for name, array in arrays.items():
        data = np.ascontiguousarray(array)
        specs.append({"name": name, "dtype": data.dtype.str, "shape": list(data.shape)})
        raw = data.tobytes()
        chunks.append(raw + b"\0" * (-len(raw) % 4))
    head = json.dumps({**header, "arrays": specs}, ensure_ascii=False).encode()
    head += b" " * (-len(head) % 4)
    return struct.pack("<I", len(head)) + head + b"".join(chunks)


def unpack(payload: bytes) -> tuple[dict[str, Any], dict[str, np.ndarray[Any, Any]]]:
    """The inverse of ``pack`` (used by tests; the browser has its own reader)."""
    (length,) = struct.unpack_from("<I", payload)
    header = json.loads(payload[4 : 4 + length])
    offset = 4 + length
    arrays = {}
    for spec in header["arrays"]:
        dtype = np.dtype(spec["dtype"])
        count = int(np.prod(spec["shape"]))
        arrays[spec["name"]] = np.frombuffer(payload, dtype, count, offset).reshape(spec["shape"])
        offset += count * dtype.itemsize
        offset += -offset % 4
    return header, arrays


def show_frames(compiled: CompiledShow, fps: float) -> bytes:
    """Frames decimated to ``fps`` (always including the last one) for playback."""
    step = max(1, round(compiled.fps / fps))
    index = np.arange(0, compiled.frame_count, step)
    if index[-1] != compiled.frame_count - 1:
        index = np.append(index, compiled.frame_count - 1)
    header = {
        "drones": compiled.drone_count,
        "show_fps": compiled.fps,
        "duration_s": compiled.duration_s,
        "marks": [m.model_dump(mode="json") for m in compiled.marks],
    }
    return pack(
        header,
        {
            "times": (index / compiled.fps).astype(np.float32),
            "positions": compiled.positions[index],
            "colors": compiled.colors[index],
        },
    )


def mesh_glb(mesh: MeshData) -> bytes:
    """The mesh as the core sees it (Z-up, metres of the source) for the viewer."""
    if mesh.texture is not None and mesh.uv is not None:
        from PIL import Image

        visual: Any = trimesh.visual.TextureVisuals(  # type: ignore[no-untyped-call]
            uv=mesh.uv, image=Image.fromarray(mesh.texture)
        )
    else:
        colors = (
            linear_to_srgb8(mesh.vertex_colors)
            if mesh.vertex_colors is not None
            else np.tile(linear_to_srgb8(mesh.base_color[:3]), (len(mesh.vertices), 1))
        )
        alpha = np.full((len(colors), 1), 255, dtype=np.uint8)
        visual = trimesh.visual.ColorVisuals(vertex_colors=np.hstack([colors, alpha]))
    tm = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, visual=visual, process=False)
    buffer = io.BytesIO()
    tm.export(buffer, file_type="glb")
    return buffer.getvalue()


def points_payload(
    positions: npt.NDArray[np.float32],
    colors: npt.NDArray[np.uint8],
    nearest: npt.NDArray[np.float32],
    header: dict[str, Any],
) -> bytes:
    return pack(header, {"positions": positions, "colors": colors, "nearest": nearest})
