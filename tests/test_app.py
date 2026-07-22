from drawing_number_tool.app import _drag_bounds


def test_selection_requires_a_real_drag_in_both_directions() -> None:
    assert _drag_bounds((100, 100), (100, 100)) is None
    assert _drag_bounds((100, 100), (180, 103)) is None
    assert _drag_bounds((100, 100), (104, 180)) is None
    assert _drag_bounds((180, 140), (100, 100)) == (100, 100, 180, 140)
