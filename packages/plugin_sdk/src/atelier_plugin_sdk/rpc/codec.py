"""Encoding of call arguments and results; large data travels as files, never in the pipe."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel

from atelier_domain import FleetSpec
from atelier_plugin_sdk.artifacts import REF_TYPES, ArtifactRef
from atelier_plugin_sdk.errors import ERRORS_BY_CODE, PluginError
from atelier_plugin_sdk.plugin import Health
from atelier_plugin_sdk.results import ExportResult, RuleResult, TransitionPlan, Violation

MAX_MESSAGE_BYTES = 16 * 1024 * 1024

MODEL_TYPES: dict[str, type[BaseModel]] = {
    cls.__name__: cls for cls in (FleetSpec, Violation, RuleResult, ExportResult, Health)
}


def encode(value: Any, workdir: Path) -> Any:
    """Turn a Python value into JSON; arrays are written to ``workdir`` as ``.npy`` files."""
    if isinstance(value, np.ndarray):
        workdir.mkdir(parents=True, exist_ok=True)
        path = workdir / f"arr_{uuid.uuid4().hex}.npy"
        np.save(path, value, allow_pickle=False)
        return {"$ndarray": str(path)}
    if isinstance(value, ArtifactRef):
        return {"$ref": value.kind, "path": str(value.path), "meta": value.meta}
    if isinstance(value, TransitionPlan):
        data = {
            "assignment": encode(np.asarray(value.assignment), workdir),
            "positions": encode(np.asarray(value.positions), workdir),
            "duration_s": value.duration_s,
        }
        return {"$type": "TransitionPlan", "data": data}
    if isinstance(value, BaseModel) and type(value).__name__ in MODEL_TYPES:
        return {"$type": type(value).__name__, "data": value.model_dump(mode="json")}
    if isinstance(value, Path):
        return {"$path": str(value)}
    if isinstance(value, dict):
        return {str(k): encode(v, workdir) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [encode(v, workdir) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"cannot send {type(value).__name__} over RPC")


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "$ndarray" in value:
        return np.load(value["$ndarray"], allow_pickle=False)
    if "$ref" in value:
        return REF_TYPES[value["$ref"]](path=Path(value["path"]), meta=value.get("meta", {}))
    if "$path" in value:
        return Path(value["$path"])
    if "$type" in value:
        kind, data = value["$type"], value["data"]
        if kind == "TransitionPlan":
            return TransitionPlan(
                assignment=decode(data["assignment"]),
                positions=decode(data["positions"]),
                duration_s=float(data["duration_s"]),
            )
        return MODEL_TYPES[kind].model_validate(data)
    return {k: decode(v) for k, v in value.items()}


def dumps(message: dict[str, Any]) -> str:
    line = json.dumps(message, ensure_ascii=False, allow_nan=False)
    if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("RPC message too large; pass big data as files")
    return line


def error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, PluginError):
        return {"code": exc.code, "message": exc.message, "data": exc.data}
    return {"code": PluginError.code, "message": f"{type(exc).__name__}: {exc}", "data": None}


def error_from_payload(error: dict[str, Any]) -> PluginError:
    cls = ERRORS_BY_CODE.get(int(error.get("code", PluginError.code)), PluginError)
    return cls(str(error.get("message", "plugin error")), data=error.get("data"))
