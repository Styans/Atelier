"""Errors a plugin may raise; each maps to a JSON-RPC error code."""

from __future__ import annotations

from typing import Any


class PluginError(Exception):
    """Generic plugin failure."""

    code = 1001

    def __init__(self, message: str, *, data: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.data = data


class InvalidParams(PluginError):
    """Parameters do not match the component's schema."""

    code = -32602


class ResourceExhausted(PluginError):
    """Out of GPU memory, RAM or disk; ``data['hint']`` says what to do."""

    code = 1002


class Cancelled(PluginError):
    code = 1003


class ContractViolation(PluginError):
    """The component returned something its role does not allow."""

    code = 1004


ERRORS_BY_CODE: dict[int, type[PluginError]] = {
    cls.code: cls
    for cls in (PluginError, InvalidParams, ResourceExhausted, Cancelled, ContractViolation)
}
