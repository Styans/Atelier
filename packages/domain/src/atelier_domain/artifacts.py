"""Artifact provenance records and fingerprints (ADR-012)."""

import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from atelier_domain.base import DomainModel

ArtifactKind = Literal[
    "mesh",
    "point_set",
    "compiled_show",
    "validation_report",
    "export_bundle",
    "image",
    "image_set",
    "contour",
]


class Producer(DomainModel):
    """Who made an artifact: a plugin component, or the core itself (plugin_id 'core')."""

    component: str
    role: str
    plugin_id: str
    plugin_version: str
    component_version: str
    api_version: str


class ArtifactRecord(DomainModel):
    id: str
    kind: ArtifactKind
    path: str = Field(description="Relative to the project folder; the file is never overwritten")
    slot: str = Field(description="What it is for, e.g. 'models.m_torus.points.150' or 'compiled'")
    producer: Producer
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    inputs: list[str] = Field(default_factory=list, description="Artifact ids of the inputs")
    fingerprint: str
    content_hash: str
    created_at: dt.datetime
    status: Literal["ready", "failed"] = "ready"


def canonical_json(value: Any) -> str:
    """Deterministic JSON used for hashing."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def fingerprint(
    producer: Producer,
    params: dict[str, Any],
    seed: int | None,
    input_fingerprints: Sequence[str],
) -> str:
    """sha256 over everything that determines an artifact's content."""
    payload = {
        "producer": producer.model_dump(mode="json"),
        "params": params,
        "seed": seed,
        "inputs": list(input_fingerprints),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def derive_seed(show_seed: int, slot: str) -> int:
    """Per-stage seed: stable as long as the show seed and the slot name are."""
    digest = hashlib.sha256(f"{show_seed}:{slot}".encode()).digest()
    return int.from_bytes(digest[:4], "little")
