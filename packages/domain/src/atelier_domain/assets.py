"""User-provided files (models, music, references) stored in the project's ``assets/``."""

from pydantic import Field

from atelier_domain.base import DomainModel


class AssetRef(DomainModel):
    """A file inside the project folder."""

    path: str = Field(description="Path relative to the project folder, with forward slashes")
    original_name: str
    content_hash: str = Field(description="sha256 of the file bytes")


class AudioTrack(DomainModel):
    """The show's music (ADR-006)."""

    asset: AssetRef
    duration_s: float = Field(gt=0)
    sample_rate: int = Field(gt=0)
    offset_s: float | None = Field(
        None,
        description=(
            "Show time at which the music starts; None = when the first formation starts"
            " forming, right after the vertical takeoff"
        ),
    )
