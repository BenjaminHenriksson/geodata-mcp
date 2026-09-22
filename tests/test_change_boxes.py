from connectors.change_boxes import reconcile


def window(tile, x=-100, y=-100, right=300, top=300):
    return {"tile_id": tile, "ulx": x, "lry": y, "lrx": right, "uly": top, "tile_px": 800}


def row(tile, bounds, change="new_building", confidence="high"):
    return (tile, "building", "appeared", change, confidence, "grass", "roof", "roof edges", *bounds)


def test_contained_edge_fragment_uses_complete_box_and_retains_evidence():
    rows = [row("full", (50, 50, 100, 100)), row("edge", (75, 50, 100, 100), confidence="low")]
    candidates = reconcile(rows, [window("full"), window("edge", x=75)])
    assert len(candidates) == 1
    assert candidates[0].row == rows[0]
    assert [o["confidence_label"] for o in candidates[0].observations] == ["high", "low"]
    assert candidates[0].observations[1]["clipped_edges"] == ["west"]


def test_complete_small_box_inside_large_box_is_not_assumed_to_be_same_building():
    assert len(reconcile([row("a", (0, 0, 100, 100)), row("b", (20, 20, 40, 40))],
                         [window("a"), window("b")])) == 2


def test_nearby_buildings_and_different_change_types_stay_separate():
    rows = [row("a", (0, 0, 10, 10)), row("b", (11, 0, 21, 10)),
            row("c", (0, 0, 10, 10), "demolition")]
    assert len(reconcile(rows, [window(t) for t in "abc"])) == 3


def test_overlap_chain_cannot_transitively_merge_distinct_objects():
    rows = [row(t, (x, 0, x + 100, 100)) for t, x in zip("abc", (0, 20, 40))]
    result = reconcile(rows, [window(t) for t in "abc"])
    assert sorted(len(c.observations) for c in result) == [1, 2]


def test_one_broad_box_cannot_swallow_two_objects_from_another_tile():
    rows = [row("a", (0, 0, 100, 100)), row("b", (40, 20, 60, 40)),
            row("b", (70, 60, 90, 80))]
    assert len(reconcile(rows, [window("a"), window("b", x=40, right=90)])) == 3


def test_result_is_order_independent_and_same_tile_boxes_are_not_merged():
    rows = [row("a", (0, 0, 100, 100)), row("b", (2, 2, 102, 102))]
    windows = [window("a"), window("b")]
    assert reconcile(rows, windows) == reconcile(list(reversed(rows)), windows)
    assert len(reconcile([rows[0], rows[0]], windows)) == 2


def test_unchanged_input_stays_empty():
    assert reconcile([], []) == []
