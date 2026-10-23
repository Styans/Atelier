# ruff: noqa: RUF001 - the text is Russian on purpose: Cyrillic letters are not look-alikes here
"""A compact text picture of a show for a language model (ADR-040).

The assistant reads this instead of show.json: no points, no artifacts, only what it needs to
reason and to call tools (ids, figures, the timeline against the budget, what is stale). It
also has to stay small: the local model has a 16K context, so long texts are cut.
"""

from __future__ import annotations

from collections.abc import Sequence

from atelier_domain import Show
from atelier_show_engine.estimate import TimelineEstimate

MAX_TEXT = 90
MAX_SCENES = 16
MAX_MODELS = 16


def _cut(text: str | None, limit: int = MAX_TEXT) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _time(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def _join(items: Sequence[str]) -> str:
    return "; ".join(items) if items else "—"


def show_summary(
    show: Show,
    timeline: TimelineEstimate,
    stale: dict[str, str | None],
    validation: tuple[bool, int] | None = None,
) -> str:
    """``validation``: (ok, number of violations) of a current report, if there is one."""
    brief, fleet = show.brief, show.brief.fleet
    lines = [f"ШОУ «{brief.title}» (id {show.id}, ревизия {show.revision})"]
    status = "утверждён" if brief.status == "approved" else "черновик"
    lines.append(
        f"Бриф [{status}]: повод {_cut(brief.occasion) or '—'};"
        f" место {_cut(brief.location) or '—'};"
        f" дата {brief.date or '—'}; зритель со стороны {brief.viewer_direction};"
        f" длительность {_time(brief.duration_s)}; настроение {_cut(brief.mood) or '—'};"
        f" палитра {', '.join(brief.palette) or '—'}"
    )
    lines.append(f"  обязательно: {_join([_cut(x, 60) for x in brief.must_have])}")
    lines.append(f"  запрещено: {_join([_cut(x, 60) for x in brief.forbidden])}")
    if brief.notes:
        lines.append(f"  заметки: {_cut(brief.notes, 160)}")
    lines.append(
        f"Флот: {fleet.drone_count} дронов (пресет {fleet.preset or 'свой'}): дистанция ≥"
        f" {fleet.min_distance_m:g} м, скорость ≤ {fleet.max_speed_h_ms:g}"
        f"/{fleet.max_speed_up_ms:g}/{fleet.max_speed_down_ms:g} м/с (гориз./вверх/вниз),"
        " ускорение ≤"
        f" {fleet.max_accel_ms2:g} м/с², высота {fleet.altitude_min_m:g}–{fleet.altitude_max_m:g} м"
    )
    if show.audio is not None:
        start = timeline.music_start_s
        lines.append(
            f"Музыка: {show.audio.asset.original_name}, {_time(show.audio.duration_s)}"
            + (f", звучит с {_time(start)}" if start is not None else "")
        )
    else:
        lines.append("Музыка: нет")

    used = {s.model_id for s in show.program}
    lines.append(f"Модели ({len(show.models)}):")
    for card in list(show.models.values())[:MAX_MODELS]:
        has = ["3D есть" if card.assets.mesh else "3D нет"]
        if card.assets.point_sets:
            has.append("точки " + ",".join(str(n) for n in sorted(card.assets.point_sets)))
        size = f", высота {card.target_height_m:g} м" if card.target_height_m else ""
        unused = "" if card.id in used else ", не в программе"
        lines.append(
            f"  {card.id} «{_cut(card.name, 40)}» {card.kind}{size}: {', '.join(has)}{unused}"
            f" — {_cut(card.description) or 'без описания'}"
        )
    if len(show.models) > MAX_MODELS:
        lines.append(f"  … и ещё {len(show.models) - MAX_MODELS}")

    kind = "по компиляции" if timeline.compiled else "оценка"
    verdict = (
        f"на {_time(timeline.over_budget_s)} длиннее бюджета"
        if timeline.over_budget_s > 0
        else f"запас {_time(timeline.budget_s - timeline.total_s)}"
    )
    lines.append(
        f"Программа ({len(show.program)} сцен): шоу {_time(timeline.total_s)} из"
        f" {_time(timeline.budget_s)} ({kind}), {verdict}"
    )
    transitions = {s.scene_id: s for s in timeline.segments if s.kind == "transition"}
    for index, scene in enumerate(show.program[:MAX_SCENES], start=1):
        flight = transitions.get(scene.id)
        tracks = [t.primitive for t in scene.animation.tracks] if scene.animation else []
        extra = f", анимация {', '.join(tracks)}" if tracks else ""
        turn = (
            f", поворот {','.join(f'{v:g}' for v in scene.rotation_deg)}°"
            if any(scene.rotation_deg)
            else ""
        )
        into = f"переход {flight.duration_s:.0f} с" if flight else "переход ?"
        lines.append(
            f"  {index}. {scene.id} «{_cut(scene.title, 50)}» → {scene.model_id}: {into},"
            f" удержание {scene.hold_s:g} с{turn}{extra}"
        )
    if len(show.program) > MAX_SCENES:
        lines.append(f"  … и ещё {len(show.program) - MAX_SCENES}")
    tail = [s for s in timeline.segments if s.scene_id is None and s.kind != "takeoff"]
    if tail:
        lines.append(f"  возврат и посадка: {sum(s.duration_s for s in tail):.0f} с")

    outdated = [slot for slot, reason in stale.items() if reason is not None]
    lines.append(f"Устарело: {_join(outdated[:8])}")
    if validation is None:
        lines.append("Проверка: не выполнялась или устарела")
    else:
        ok, count = validation
        lines.append("Проверка: без нарушений" if ok else f"Проверка: {count} нарушений")
    approved = [stage for stage, state in show.stage_status.items() if state == "approved"]
    lines.append(f"Утверждено пользователем: {_join(approved)}")
    return "\n".join(lines)
