from scripts.evaluate_vision_change import score


def candidate(kind="new_building", bounds=(0, 0, 10, 10)):
    return {"row": ["tile", "building", "appeared", kind, "high", "before", "after", "evidence", *bounds]}


CASE = {"evaluation_bounds_3006": [-100, -100, 100, 100],
        "expected": [{"bounds_3006": [0, 0, 10, 10], "change_types": ["new_building", "appearance"]}]}


def test_reverse_direction_is_not_counted_as_a_correct_detection():
    result = score([candidate("demolition")], CASE)
    assert result["spatially_matched_regions"] == 1
    assert result["matched_regions"] == 0 and result["missed_regions"] == [0]
    assert result["unsupported_candidates"] == [0]
    assert result["type_mismatches"][0]["actual"] == "demolition"


def test_overbroad_box_cannot_claim_every_reference_inside_it():
    result = score([candidate(bounds=(-90, -90, 90, 90))], CASE)
    assert result["matched_regions"] == 0


def test_duplicate_proposals_are_visible_without_inflating_region_recall():
    result = score([candidate(), candidate()], CASE)
    assert result["returned_candidates"] == 2 and result["matched_regions"] == 1
    assert len(result["supported_candidates"]) == 2


def test_control_proposal_is_reported_as_unsupported():
    result = score([candidate()], {**CASE, "expected": []})
    assert result["unsupported_candidates"] == [0] and not result["supported_candidates"]
