"""Calling a component: the single code path shared by in-process and subprocess runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from atelier_plugin_sdk.context import RunContext
from atelier_plugin_sdk.contracts import check_output
from atelier_plugin_sdk.errors import InvalidParams
from atelier_plugin_sdk.roles import ROLE_SPECS, Component


def _parse(component: Component[Any], params: Mapping[str, Any]) -> BaseModel:
    try:
        return component.Params.model_validate(dict(params))
    except ValidationError as exc:
        raise InvalidParams(
            f"invalid parameters for {component.role} {component.name!r}: {exc}",
            data=exc.errors(include_url=False, include_context=False),
        ) from exc


def normalize_params(component: Component[Any], params: Mapping[str, Any]) -> dict[str, Any]:
    """Validate ``params`` and return them with defaults filled in (JSON-compatible)."""
    return _parse(component, params).model_dump(mode="json")


def invoke(
    component: Component[Any],
    args: Mapping[str, Any],
    params: Mapping[str, Any],
    ctx: RunContext,
) -> Any:
    """Run the role method of ``component`` and check its output against the contract."""
    spec = ROLE_SPECS[component.role]
    missing = [name for name in spec.args if name not in args]
    if missing:
        raise InvalidParams(f"missing arguments for {component.role}: {missing}")
    parsed = _parse(component, params)
    method = getattr(component, spec.method)
    positional = [args[name] for name in spec.args]
    result = method(ctx, *positional, parsed) if spec.takes_ctx else method(*positional, parsed)
    check_output(component.role, args, result)
    return result
