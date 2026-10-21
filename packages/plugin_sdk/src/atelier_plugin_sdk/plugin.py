"""The object a plugin's ``entry`` points to."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel

from atelier_plugin_sdk.roles import Component, RoleName


class Health(BaseModel):
    status: Literal["ok", "degraded", "error"]
    message: str = ""

    @classmethod
    def ok(cls, message: str = "") -> Health:
        return cls(status="ok", message=message)

    @classmethod
    def error(cls, message: str) -> Health:
        return cls(status="error", message=message)


class Plugin:
    """A set of components plus a health check."""

    def __init__(
        self,
        components: Sequence[Component[Any]],
        health_check: Callable[[], Health] = Health.ok,
    ) -> None:
        keys = [(c.role, c.name) for c in components]
        if len(keys) != len(set(keys)):
            raise ValueError("component (role, name) pairs must be unique within a plugin")
        self.components = list(components)
        self.health_check = health_check

    def component(self, role: RoleName, name: str) -> Component[Any]:
        for component in self.components:
            if component.role == role and component.name == name:
                return component
        raise KeyError(f"no {role} component named {name!r}")
