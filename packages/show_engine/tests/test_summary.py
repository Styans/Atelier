from pathlib import Path

from atelier_show_engine.program import FigureDraft, SceneDraft
from atelier_show_engine.service import ShowService


def test_summary_lists_what_the_assistant_needs(
    service: ShowService, fixture_files: dict[str, Path]
) -> None:
    store, _ = service.create_show("Юбилей", drones=36, duration_s=120)
    service.update_brief(store, {"occasion": "35 лет компании", "must_have": ["логотип"]})
    _, torus = service.add_model(store, fixture_files["torus"], name="Torus")
    service.compute_points(store, torus)
    drafts = [
        SceneDraft(title="Кольцо", hold_s=10, model_id=torus),
        SceneDraft(title="Орёл", hold_s=12, figure=FigureDraft(name="Орёл", description="x" * 500)),
    ]
    service.apply_program(store, drafts)

    text = service.show_summary(store)

    assert "35 лет компании" in text and "обязательно: логотип" in text
    assert "m_torus «Torus» mesh3d: 3D есть, точки 36" in text
    assert "3D нет" in text and "x" * 200 not in text  # long descriptions are cut
    assert "s_1 «Кольцо» → m_torus: переход" in text
    assert "оценка" in text and "Проверка: не выполнялась" in text
    assert len(text) < 3000


def test_summary_marks_an_overrun(service: ShowService) -> None:
    store, _ = service.create_show("Короткое", drones=36, duration_s=30)
    service.apply_program(
        store,
        [SceneDraft(title=str(i), hold_s=10, figure=FigureDraft(name=f"F{i}")) for i in range(5)],
    )

    assert "длиннее бюджета" in service.show_summary(store)
