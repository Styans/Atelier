from atelier_show_engine.provenance import param_changes


def test_param_changes_name_the_leaf_and_both_values() -> None:
    old = {"component": {"relax_iterations": 20, "view_dir": [0, -1, 0]}, "n": 150}
    new = {"component": {"relax_iterations": 40, "view_dir": [0, -1, 0], "facade": True}, "n": 120}

    assert param_changes(old, new) == [
        "facade: none -> true",
        "relax_iterations: 20 -> 40",
        "n: 150 -> 120",
    ]


def test_param_changes_keep_paths_below_the_top_level() -> None:
    old = {"planners": {"s_1": {"speed": 1}}}
    new = {"planners": {"s_1": {"speed": 2}}}

    assert param_changes(old, new) == ["planners.s_1.speed: 1 -> 2"]
    assert param_changes(old, old) == []


def test_long_values_are_shortened() -> None:
    (change,) = param_changes({"tracks": list(range(50))}, {"tracks": []})

    assert change.startswith("tracks: [0,1,2") and change.endswith("... -> []")
