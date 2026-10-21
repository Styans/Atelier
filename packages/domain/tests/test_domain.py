import datetime as dt

import pytest
from pydantic import ValidationError

from atelier_domain import (
    AnimationSpec,
    AnimTrack,
    ArtifactRecord,
    Brief,
    ModelCard,
    PipelineConfig,
    Producer,
    RegionPlaneSplit,
    Scene,
    Show,
    StageConfig,
    derive_seed,
    fingerprint,
    fleet_from_preset,
)
from atelier_domain.ids import slugify, unique_id

PRODUCER = Producer(
    component="pointille",
    role="point_distributor",
    plugin_id="pointille",
    plugin_version="0.1.0",
    component_version="0.1.0",
    api_version="1",
)


def make_show() -> Show:
    fleet = fleet_from_preset("conservative", 150)
    card = ModelCard(id="m_torus", name="Torus", palette=["#ffcc00"])
    scene = Scene(
        id="s_1",
        title="Torus",
        model_id="m_torus",
        hold_s=10,
        animation=AnimationSpec(
            tracks=[
                AnimTrack(
                    primitive="builtin.rotate",
                    region=RegionPlaneSplit(normal=(1, 0, 0)),
                    easing="ease_in_out",
                    params={"angle_deg": 90},
                )
            ]
        ),
    )
    record = ArtifactRecord(
        id="a_1",
        kind="point_set",
        path="models/m_torus/points_150_abcd1234.npz",
        slot="models.m_torus.points.150",
        producer=PRODUCER,
        params={"oversample": 8.0},
        seed=7,
        fingerprint="f" * 64,
        content_hash="c" * 64,
        created_at=dt.datetime(2026, 10, 21, tzinfo=dt.UTC),
    )
    return Show(
        id="demo-1a2b",
        brief=Brief(title="Demo", duration_s=600, fleet=fleet, date=dt.date(2026, 12, 16)),
        models={"m_torus": card},
        program=[scene],
        pipeline=PipelineConfig(
            point_distributor=StageConfig(component="pointille"),
            transition_planner=StageConfig(component="builtin.hungarian_direct"),
            validators=[StageConfig(component="builtin.min_distance")],
        ),
        artifacts={"a_1": record},
    )


def test_fleet_presets_copy_limits_and_remember_the_preset() -> None:
    fleet = fleet_from_preset("skybrush_default", 500)

    assert fleet.drone_count == 500
    assert fleet.preset == "skybrush_default"
    assert fleet.min_distance_m == 3.0
    assert fleet.max_speed_h_ms == 10.0


def test_unknown_preset_lists_known_ones() -> None:
    with pytest.raises(KeyError, match="conservative"):
        fleet_from_preset("nope", 10)


def test_fleet_rejects_inverted_altitudes() -> None:
    data = fleet_from_preset("conservative", 10).model_dump()
    data["altitude_max_m"] = 1.0

    with pytest.raises(ValidationError, match="altitude_max_m"):
        type(fleet_from_preset("conservative", 10)).model_validate(data)


def test_show_round_trips_through_json() -> None:
    show = make_show()

    restored = Show.model_validate_json(show.model_dump_json())

    assert restored == show
    assert isinstance(restored.program[0].animation.tracks[0].region, RegionPlaneSplit)  # type: ignore[union-attr]


def test_point_set_keys_survive_json() -> None:
    show = make_show()
    show.models["m_torus"].assets.point_sets[150] = "a_1"

    restored = Show.model_validate_json(show.model_dump_json())

    assert restored.models["m_torus"].assets.point_sets == {150: "a_1"}


def test_unknown_fields_are_rejected() -> None:
    data = make_show().model_dump(mode="json")
    data["brief"]["colour"] = "red"

    with pytest.raises(ValidationError, match="colour"):
        Show.model_validate(data)


def test_scene_must_refer_to_an_existing_model() -> None:
    data = make_show().model_dump(mode="json")
    data["program"][0]["model_id"] = "m_missing"

    with pytest.raises(ValidationError, match="m_missing"):
        Show.model_validate(data)


def test_scene_ids_must_be_unique() -> None:
    data = make_show().model_dump(mode="json")
    data["program"].append(data["program"][0])

    with pytest.raises(ValidationError, match="unique"):
        Show.model_validate(data)


def test_palette_accepts_only_hex_colours() -> None:
    with pytest.raises(ValidationError):
        ModelCard(id="m", name="m", palette=["gold"])


def test_json_schema_is_exportable() -> None:
    schema = Show.model_json_schema()

    assert "Brief" in schema["$defs"]
    assert "AnimTrack" in schema["$defs"]


def test_fingerprint_is_stable_and_sensitive() -> None:
    base = fingerprint(PRODUCER, {"a": 1.0, "b": [1, 2]}, 7, ["x"])

    assert base == fingerprint(PRODUCER, {"b": [1, 2], "a": 1.0}, 7, ["x"])
    assert base != fingerprint(PRODUCER, {"a": 1.5, "b": [1, 2]}, 7, ["x"])
    assert base != fingerprint(PRODUCER, {"a": 1.0, "b": [1, 2]}, 8, ["x"])
    assert base != fingerprint(PRODUCER, {"a": 1.0, "b": [1, 2]}, 7, ["y"])
    newer = PRODUCER.model_copy(update={"plugin_version": "0.2.0"})
    assert base != fingerprint(newer, {"a": 1.0, "b": [1, 2]}, 7, ["x"])


def test_derived_seeds_depend_on_show_seed_and_slot() -> None:
    assert derive_seed(0, "models.m.points.150") == derive_seed(0, "models.m.points.150")
    assert derive_seed(0, "models.m.points.150") != derive_seed(1, "models.m.points.150")
    assert derive_seed(0, "models.m.points.150") != derive_seed(0, "models.m.points.151")


def test_slugify_keeps_kazakh_letters() -> None:
    assert slugify("35 ЖЫЛ — Қазақстан!") == "35-жыл-қазақстан"


def test_unique_id_adds_a_suffix() -> None:
    assert unique_id("m", "Torus", set()) == "m_torus"
    assert unique_id("m", "Torus", {"m_torus"}) == "m_torus_2"
    assert unique_id("m", "Torus", {"m_torus", "m_torus_2"}) == "m_torus_3"
