"""The assistant's tools: small typed arguments over ShowService methods (ADR-039).

A tool never writes show.json itself: it calls the same service methods the UI and the CLI
use, so every change is a revision (author "assistant") and can be rolled back. Arguments are
validated by pydantic before anything runs; an error goes back to the model as text so it can
fix the call. What is expensive or removes something is not done: it becomes a proposal the
user applies with a button. Schemas are kept short on purpose: the local model reads them on
every request.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from atelier_assistant.llm import ToolSpec
from atelier_domain import HexColor
from atelier_domain.model_card import ModelKind
from atelier_jobs import ChatStore
from atelier_show_engine.program import FigureDraft, FitResult, SceneDraft
from atelier_show_engine.service import ServiceError, ShowService
from atelier_show_engine.store import ShowStore

MIN_PROGRAM, MAX_PROGRAM = 6, 12
# Where the audience stands -> Brief.viewer_direction (Z-up, north is +Y).
AUDIENCE_SIDES = {"юг": "-Y", "север": "+Y", "запад": "-X", "восток": "+X"}


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BriefArgs(Args):
    title: str | None = Field(None, max_length=80)
    occasion: str | None = Field(None, max_length=200)
    date: dt.date | None = Field(None, description="YYYY-MM-DD")
    location: str | None = Field(None, max_length=200)
    # A word, not an axis: the code maps it (the model got "север" -> "-Y" wrong).
    audience_side: Literal["юг", "север", "запад", "восток"] | None = Field(
        None, description="с какой стороны стоят зрители"
    )
    duration_s: float | None = Field(None, gt=30, le=3600)
    drone_count: int | None = Field(None, gt=0, le=5000)
    mood: str | None = Field(None, max_length=200)
    palette: list[HexColor] | None = Field(None, max_length=8, description="#rrggbb")
    must_have: list[str] | None = Field(None, max_length=12)
    forbidden: list[str] | None = Field(None, max_length=12)
    notes: str | None = Field(None, max_length=600)


class FigureArgs(Args):
    name: str = Field(min_length=1, max_length=60)
    kind: ModelKind = "mesh3d"
    description: str = Field("", max_length=400, description="что видит зритель, по-русски")
    image_prompt: str = Field(
        "", max_length=400, description="English prompt for a concept picture, one object"
    )


class ProgramScene(Args):
    title: str = Field(min_length=1, max_length=80)
    hold_s: float = Field(15, gt=0, le=120, description="желаемое удержание; код подгонит")
    figure: FigureArgs | None = None
    model_id: str | None = Field(None, description="или существующая карточка")


class ProgramArgs(Args):
    scenes: list[ProgramScene] = Field(min_length=MIN_PROGRAM, max_length=MAX_PROGRAM)


class SceneArgs(Args):
    title: str = Field(min_length=1, max_length=80)
    hold_s: float = Field(15, gt=0, le=120)
    figure: FigureArgs | None = None
    model_id: str | None = None


class SceneChange(Args):
    # No transition length here: the code computes the safe one from the fleet's limits, and
    # a small model "saved time" by setting it to zero.
    scene_id: str
    title: str | None = Field(None, max_length=80)
    hold_s: float | None = Field(None, gt=0, le=120)
    model_id: str | None = Field(None, description="другая карточка для этой сцены")
    rotation_deg: tuple[float, float, float] | None = None


class OrderArgs(Args):
    order: list[str] = Field(min_length=1, description="все id сцен в новом порядке")


class CardChange(Args):
    model_id: str
    name: str | None = Field(None, max_length=60)
    kind: ModelKind | None = None
    description: str | None = Field(None, max_length=400)
    image_prompt: str | None = Field(None, max_length=400)
    style: str | None = Field(None, max_length=120)
    palette: list[HexColor] | None = Field(None, max_length=8)
    target_height_m: float | None = Field(None, gt=1, le=500)


class FitArgs(Args):
    duration_s: float | None = Field(
        None, gt=30, le=3600, description="новая длительность шоу, если пользователь её назвал"
    )


Action = Literal[
    "points",
    "compile",
    "validate",
    "export",
    "generate_images",
    "generate_mesh",
    "remove_scene",
    "remove_model",
]


class ActionArgs(Args):
    action: Action
    target_id: str | None = Field(None, description="id сцены или модели, если нужно")
    prompt: str | None = Field(None, max_length=400, description="для generate_images")
    reason: str = Field(min_length=1, max_length=200, description="зачем, одной фразой")


@dataclass
class ToolContext:
    service: ShowService
    store: ShowStore
    chat: ChatStore
    show_id: str
    # The program this turn created may be replaced without asking (the model is fitting it),
    # and the cards made for a replaced attempt go with it.
    program: list[tuple[str, str]] | None = None  # (scene id, model id) as this turn left it
    created_cards: set[str] = field(default_factory=set)
    proposals: list[str] = field(default_factory=list)
    request: str = ""  # the user's message this turn answers
    said: str = ""  # everything the user wrote in this chat, for facts the model may not invent
    # Fields people changed by hand since the previous answer: id → field → value now.
    hand_changed: dict[str, dict[str, Any]] = field(default_factory=dict)


class ToolFailure(Exception):
    """A call the model should fix; the text goes back to it."""


Handler = Callable[[ToolContext, Any], str]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[Args]
    run: Handler

    def spec(self) -> ToolSpec:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(self.name, self.description, schema)


def _figure(args: FigureArgs | None) -> FigureDraft | None:
    if args is None:
        return None
    return FigureDraft(
        name=args.name,
        kind=args.kind,
        description=args.description,
        image_prompt=args.image_prompt or None,
    )


def _minutes(seconds: float) -> str:
    whole = math.ceil(seconds / 30) * 30  # up to the next half minute
    return f"{whole // 60}:{whole % 60:02d}"


REMOVE_BY_PROGRAM = "вызовите propose_program снова, на {n} сцен(ы) меньше"
REMOVE_BY_PROPOSAL = (
    "предложите пользователю убрать {n} сцен(ы): propose_action с action=remove_scene и"
    " target_id сцены — выберите наименее важные; или спросите, можно ли увеличить длительность"
)


def _fit_text(fit: FitResult, remove_how: str = REMOVE_BY_PROGRAM) -> str:
    total = f"оценка шоу {fit.total_s:.0f} с из {fit.budget_s:.0f} с"
    if not fit.fits and len(fit.holds) - fit.remove_scenes < MIN_PROGRAM:
        # Fewer scenes than a program may have: re-proposing cannot help, and a small model
        # otherwise rewrites the same program until it runs out of steps.
        least = fit.shortest_s(MIN_PROGRAM)
        return (
            f"{total}: не помещается — даже {MIN_PROGRAM} сцен при минимальных удержаниях займут"
            f" около {least:.0f} с (подъём к первой фигуре, переходы, возврат и посадка флота)."
            " Программу больше не меняйте. Ответьте пользователю: длительность слишком мала для"
            f" этого флота, нужно не меньше {_minutes(least)}; спросите, увеличить ли её"
        )
    if not fit.fits:
        return (
            f"{total}: не помещается даже при минимальных удержаниях (лишних"
            f" {fit.excess_s:.0f} с: переходы между фигурами занимают десятки секунд). Чтобы"
            f" уложиться, {remove_how.format(n=fit.remove_scenes)}"
        )
    holds = ", ".join(f"{scene}: {hold:g} с" for scene, hold in fit.holds.items())
    slack = f"; короче бюджета на {fit.slack_s:.0f} с" if fit.slack_s > 0 else ""
    return f"{total}{slack}. Удержания: {holds}"


MONTHS = r"январ|феврал|март|апрел|\bма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр"


def _unsaid(said: str, changes: dict[str, Any]) -> list[str]:
    """Brief facts the user never gave anywhere in the chat: the model is not to make them up
    (it wrote a date, a city and 75 seconds for "a show for the year of the monkey")."""
    said = said.casefold()
    numbers = bool(re.search(r"\d", said))
    checks: dict[str, Callable[[Any], bool]] = {
        "date": lambda _: numbers or bool(re.search(MONTHS, said)),
        "duration_s": lambda _: numbers or bool(re.search(r"минут|секунд|час", said)),
        "drone_count": lambda _: numbers,
        "location": lambda value: any(
            word[:4] in said for word in re.findall(r"\w{4,}", str(value).casefold())
        ),
    }
    return [name for name, ok in checks.items() if changes.get(name) and not ok(changes[name])]


def update_brief(ctx: ToolContext, args: BriefArgs) -> str:
    changes = args.model_dump(exclude_none=True)
    unsaid = _unsaid(ctx.said, changes) if ctx.said else []
    for name in unsaid:
        del changes[name]
    side = changes.pop("audience_side", None)
    if side is not None:
        changes["viewer_direction"] = AUDIENCE_SIDES[side]
    drones = changes.pop("drone_count", None)
    if drones is not None:
        ctx.service.set_fleet(ctx.store, drone_count=drones)
    skipped = (
        f"; не записано, пользователь этого не говорил: {', '.join(unsaid)} — оставьте пустым"
        " и спросите в конце ответа"
        if unsaid
        else ""
    )
    if changes:
        ctx.service.update_brief(ctx.store, changes)
    if not changes and drones is None:
        if unsaid:
            return f"бриф не изменён{skipped}"
        raise ToolFailure("ничего не передано: укажите хотя бы одно поле брифа")
    done = sorted([*changes, *(["drone_count"] if drones is not None else [])])
    return f"бриф обновлён: {', '.join(done)}{skipped}"


def propose_program(ctx: ToolContext, args: ProgramArgs) -> str:
    for index, scene in enumerate(args.scenes, start=1):
        if scene.figure is None and scene.model_id is None:
            raise ToolFailure(f"сцена {index}: нужна новая figure или существующий model_id")
    drafts = [
        SceneDraft(title=s.title, hold_s=s.hold_s, figure=_figure(s.figure), model_id=s.model_id)
        for s in args.scenes
    ]
    show = ctx.store.load()
    ours = ctx.program == [(s.id, s.model_id) for s in show.program]
    if show.program and not ours:
        # Replacing the user's program removes scenes: only with their click.
        done = _propose(
            ctx,
            "replace_program",
            {"scenes": [d.model_dump(mode="json") for d in drafts]},
            f"Заменить программу ({len(show.program)} сцен) новой из {len(drafts)} сцен",
        )
        return f"в шоу уже есть программа из {len(show.program)} сцен; {done}"
    before = set(show.models)
    shown, fit = ctx.service.apply_program(
        ctx.store, drafts, replace=True, message=f"Assistant: program of {len(drafts)} scenes"
    )
    used = {s.model_id for s in shown.program}
    for stale_card in sorted(ctx.created_cards - used):
        ctx.service.remove_model(ctx.store, stale_card)  # made by this turn's earlier attempt
    ctx.created_cards = (ctx.created_cards & used) | (set(shown.models) - before)
    ctx.program = [(s.id, s.model_id) for s in shown.program]
    ids = ", ".join(f"{s.id}→{s.model_id}" for s in shown.program)
    text = f"программа применена ({len(drafts)} сцен: {ids}); {_fit_text(fit)}"
    repeats = len(shown.program) - len({s.model_id for s in shown.program})
    if repeats > len(shown.program) // 4:
        # Padding the program with the same figures is the usual failure of a small model.
        text += (
            f". Внимание: {repeats} сцен повторяют уже показанные фигуры; если это не задумано"
            " (логотип в начале и в конце — задумано), замените их новыми идеями"
        )
    return text


def add_scene(ctx: ToolContext, args: SceneArgs) -> str:
    model_id = args.model_id
    if model_id is None:
        figure = _figure(args.figure)
        if figure is None:
            raise ToolFailure("нужна новая figure или существующий model_id")
        # A figure shown again ("the logo, as at the start") is the same card, as in a program.
        name = figure.name.strip().casefold()
        same = [i for i, c in ctx.store.load().models.items() if c.name.strip().casefold() == name]
        if same:
            model_id = same[0]
        else:
            _, model_id = ctx.service.create_model(
                ctx.store, **figure.model_dump(exclude_none=True)
            )
    scene = ctx.service.add_scene(ctx.store, model_id, hold_s=args.hold_s, title=args.title)
    return f"сцена {scene.id} добавлена в конец (модель {model_id})"


def _keep_hand_edits(ctx: ToolContext, target_id: str, changes: dict[str, Any]) -> None:
    """The user's own edits since the previous answer stay unless this message asks again.

    A small model "restores" the names it gave in its earlier answer; the prompt alone does
    not stop it. Holds are free: fitting the budget rescales them anyway.
    """
    mine = ctx.hand_changed.get(target_id, {})
    request = ctx.request.casefold()
    undone = [
        f"{name} = {mine[name]!r}"
        for name, value in changes.items()
        if name in mine and name != "hold_s" and value != mine[name]
        if not (isinstance(value, str) and value.strip().casefold() in request)
    ]
    if undone:
        raise ToolFailure(
            f"пользователь сам поменял у {target_id} после вашего прошлого ответа: "
            f"{', '.join(undone)}. Это его решение — не меняйте эти поля, если он не просит"
            " этого в последнем сообщении; остальное сделайте без них"
        )


def update_scene(ctx: ToolContext, args: SceneChange) -> str:
    changes = args.model_dump(exclude_none=True, exclude={"scene_id"})
    if not changes:
        raise ToolFailure(
            "ничего не передано для изменения сцены: укажите title, hold_s, model_id или"
            " rotation_deg (длительность перехода считает код, её не задают)"
        )
    _keep_hand_edits(ctx, args.scene_id, changes)
    ctx.service.update_scene(ctx.store, args.scene_id, changes)
    return f"сцена {args.scene_id} изменена: {', '.join(sorted(changes))}"


def reorder_scenes(ctx: ToolContext, args: OrderArgs) -> str:
    ctx.service.reorder_scenes(ctx.store, args.order)
    return f"новый порядок: {', '.join(args.order)}"


def create_model_card(ctx: ToolContext, args: FigureArgs) -> str:
    figure = _figure(args)
    assert figure is not None
    _, model_id = ctx.service.create_model(ctx.store, **figure.model_dump(exclude_none=True))
    return f"карточка {model_id} создана"


def update_model_card(ctx: ToolContext, args: CardChange) -> str:
    changes = args.model_dump(exclude_none=True, exclude={"model_id"})
    if not changes:
        raise ToolFailure("ничего не передано для изменения карточки")
    _keep_hand_edits(ctx, args.model_id, changes)
    ctx.service.update_model(ctx.store, args.model_id, changes)
    return f"карточка {args.model_id} изменена: {', '.join(sorted(changes))}"


def fit_to_budget(ctx: ToolContext, args: FitArgs) -> str:
    # The duration rides along: a small model asked to "shorten to 3 minutes" tends to fit
    # the old budget and forget update_brief.
    if args.duration_s is not None:
        ctx.service.update_brief(ctx.store, {"duration_s": args.duration_s})
    show, fit = ctx.service.fit_program(ctx.store)
    ours = ctx.program == [(s.id, s.model_id) for s in show.program]
    return _fit_text(fit, REMOVE_BY_PROGRAM if ours else REMOVE_BY_PROPOSAL)


def propose_action(ctx: ToolContext, args: ActionArgs) -> str:
    show = ctx.store.load()
    if args.action in ("remove_scene",) and args.target_id not in {s.id for s in show.program}:
        raise ToolFailure(f"нет сцены {args.target_id!r}")
    if args.action in ("remove_model", "points", "generate_images", "generate_mesh") and (
        args.target_id not in show.models
    ):
        raise ToolFailure(f"нет модели {args.target_id!r}; для этого действия нужен её id")
    if args.action == "generate_images" and not args.prompt:
        raise ToolFailure("для generate_images нужен prompt (по-английски)")
    if args.action == "generate_mesh" and not show.models[str(args.target_id)].assets.concepts:
        raise ToolFailure(
            f"у {args.target_id} ещё нет концептов: сначала предложите generate_images"
        )
    return _propose(
        ctx, args.action, {"target_id": args.target_id, "prompt": args.prompt}, args.reason
    )


def _propose(ctx: ToolContext, action: str, args: dict[str, Any], summary: str) -> str:
    """A proposal card in the chat; the same pending proposal is never shown twice."""
    for pending in ctx.chat.proposals(ctx.show_id, status="pending"):
        if pending.action == action and pending.public()["args"] == args:
            # A small model otherwise "retries" the same proposal until the step limit.
            return (
                f"это предложение уже ждёт решения пользователя ({pending.id}); не повторяйте"
                " его — закончите ход ответом пользователю"
            )
    proposal = ctx.chat.add_proposal(ctx.show_id, action, args, summary=summary)
    ctx.proposals.append(proposal.id)
    return (
        "предложение показано пользователю; выполнится, только если он нажмёт «Применить»."
        " Больше его не вызывайте"
    )


TOOLS: list[Tool] = [
    Tool(
        "update_brief",
        "Заполнить или поправить бриф. Передавайте только то, что меняется.",
        BriefArgs,
        update_brief,
    ),
    Tool(
        "propose_program",
        f"Программа шоу целиком: {MIN_PROGRAM}–{MAX_PROGRAM} сцен по порядку, у каждой новая"
        " фигура или существующая model_id. Удержания подгоняет код под длительность брифа.",
        ProgramArgs,
        propose_program,
    ),
    Tool("add_scene", "Добавить одну сцену в конец программы.", SceneArgs, add_scene),
    Tool("update_scene", "Изменить сцену по её id.", SceneChange, update_scene),
    Tool("reorder_scenes", "Переставить сцены.", OrderArgs, reorder_scenes),
    Tool("create_model_card", "Новая карточка фигуры без сцены.", FigureArgs, create_model_card),
    Tool("update_model_card", "Изменить карточку фигуры по её id.", CardChange, update_model_card),
    Tool(
        "fit_to_budget",
        "Подогнать удержания текущей программы под длительность брифа; с duration_s — сначала"
        " поменять длительность.",
        FitArgs,
        fit_to_budget,
    ),
    Tool(
        "propose_action",
        "Предложить пользователю дорогое или необратимое действие (расчёт точек, компиляция,"
        " проверка, экспорт, генерация картинок, удаление). Само не выполняется.",
        ActionArgs,
        propose_action,
    ),
]
BY_NAME = {tool.name: tool for tool in TOOLS}


@dataclass(frozen=True)
class ToolOutcome:
    text: str
    is_error: bool


def _validation_text(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:6]:
        where = ".".join(str(p) for p in error["loc"]) or "аргументы"
        parts.append(f"{where}: {error['msg']}")
    return "неверные аргументы — " + "; ".join(parts)


def run_tool(ctx: ToolContext, name: str, raw: dict[str, Any]) -> ToolOutcome:
    """Validate and run one call; any problem comes back as text for the model to fix."""
    tool = BY_NAME.get(name)
    if tool is None:
        return ToolOutcome(f"нет инструмента {name!r}; есть: {', '.join(BY_NAME)}", True)
    if "__invalid_json__" in raw:
        return ToolOutcome("аргументы не являются корректным JSON; повторите вызов", True)
    try:
        args = tool.args.model_validate(raw)
        return ToolOutcome(tool.run(ctx, args), False)
    except ValidationError as exc:
        return ToolOutcome(_validation_text(exc), True)
    except (ToolFailure, ServiceError, ValueError) as exc:
        return ToolOutcome(str(exc), True)
