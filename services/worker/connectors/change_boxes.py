"""Conservative cross-crop reconciliation of approximate review boxes.

Retain a real observation as the geometry, not a union inventing unseen extent.
Every member must match every other member, so overlap chains cannot collapse a
row of buildings into one object. Ambiguous one-to-many matches stay separate.
"""

from dataclasses import dataclass, field


@dataclass
class Candidate:
    row: tuple
    observations: list
    merge_method: str = "single_observation"
    review_required: bool = False
    conflicting_observations: list = field(default_factory=list)


def _area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


def _overlap(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1]))
    aa, ab = _area(a), _area(b)
    return intersection / (aa + ab - intersection), intersection / min(aa, ab)


def _observation(row, windows):
    w = windows[row[0]]
    b = row[-4:]
    # Two source pixels plus a 0.5% allowance for coarse model coordinates.
    tolerance = max(2 / w.get("tile_px", 800), .005)
    tx, ty = (w["lrx"] - w["ulx"]) * tolerance, (w["uly"] - w["lry"]) * tolerance
    clipped = [name for name, gap, tol in (
        ("west", b[0] - w["ulx"], tx), ("south", b[1] - w["lry"], ty),
        ("east", w["lrx"] - b[2], tx), ("north", w["uly"] - b[3], ty)) if gap <= tol]
    return {"tile_id": row[0], "bounds_3006": list(b), "clipped_edges": clipped,
            "concept": row[1], "change_type": row[3], "confidence_label": row[4],
            "before": row[5], "after": row[6], "evidence": row[7]}


def _matches(a, b):
    if a["tile_id"] == b["tile_id"]:
        return False
    synonyms = {"appearance": "new_building", "disappearance": "demolition"}
    if (a["concept"], synonyms.get(a["change_type"], a["change_type"])) != (
            b["concept"], synonyms.get(b["change_type"], b["change_type"])):
        return False
    ba, bb = a["bounds_3006"], b["bounds_3006"]
    iou, containment = _overlap(ba, bb)
    if iou >= .60:
        return True
    smaller = a if _area(ba) < _area(bb) else b
    # A small complete box can be a different building inside an overbroad box.
    # Only tile-edge truncation supports absorbing a low-IoU partial proposal.
    return containment >= .90 and bool(smaller["clipped_edges"])


def reconcile(rows, windows):
    windows = {w["tile_id"]: w for w in windows}
    observations = [_observation(row, windows) for row in rows]
    # Complete observations first, then larger ones; all tie-breaks deterministic.
    order = sorted(range(len(rows)), key=lambda i: (
        bool(observations[i]["clipped_edges"]), -_area(rows[i][-4:]), rows[i]))
    assigned, result = set(), []
    for seed in order:
        if seed in assigned:
            continue
        members = [seed]
        matches = [i for i in order if i not in assigned and i != seed
                   and _matches(observations[seed], observations[i])]
        # A broad proposal overlapping several objects in one other tile is
        # ambiguous. Do not choose one arbitrarily or swallow all of them.
        by_tile = {}
        for i in matches:
            by_tile.setdefault(rows[i][0], []).append(i)
        for i in matches:
            if len(by_tile[rows[i][0]]) != 1:
                continue
            if all(_matches(observations[i], observations[j]) for j in members):
                members.append(i)
        assigned.update(members)
        result.append(Candidate(rows[seed], [observations[i] for i in members],
                                "cross_tile_consensus" if len(members) > 1 else "single_observation"))
    result.sort(key=lambda c: c.row)
    _flag_conflicts(result)
    return result


def _flag_conflicts(candidates):
    """Flag opposing observations without deciding whether either is wrong.

    Demolition followed by replacement can legitimately produce both directions.
    This is a review cue, never a rejection or a confidence adjustment.
    """
    directions = {"new_building": 1, "appearance": 1, "extension": 1,
                  "demolition": -1, "disappearance": -1}
    for index, candidate in enumerate(candidates):
        for other in candidates[index + 1:]:
            for a in candidate.observations:
                for b in other.observations:
                    if (a["tile_id"] == b["tile_id"] or a["concept"] != b["concept"]
                            or directions.get(a["change_type"], 0)
                            * directions.get(b["change_type"], 0) != -1):
                        continue
                    iou, containment = _overlap(a["bounds_3006"], b["bounds_3006"])
                    if iou < .60 and containment < .90:
                        continue
                    candidate.review_required = other.review_required = True
                    for target, observation in ((candidate, b), (other, a)):
                        if observation not in target.conflicting_observations:
                            target.conflicting_observations.append(observation)
