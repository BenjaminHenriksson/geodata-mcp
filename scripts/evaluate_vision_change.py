"""Read-only, file-based evaluation: public WMS imagery -> vision -> JSON evidence.

No database, MCP jobs or deployment access. Inference uses the configured generic
VISION_* environment and incurs its normal usage charges. Labels are never sent
to the model. Use --fetch-only to inspect inputs before purchasing inference.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
from dataclasses import asdict

import httpx
from PIL import Image, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "worker"))
from connectors import vision_change  # noqa: E402


def area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def intersection(a, b):
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def score(candidates, case):
    """Region recall and unsupported proposals; repeat matches counted separately.

    A match needs IoU >= .25 OR >=50% reference coverage, plus candidate area
    <=4x reference area. This evaluates rough review regions, not footprints.
    """
    refs = case["expected"]
    hits, spatial_hits, unsupported, ignored, supported, type_mismatches = set(), set(), [], [], [], []
    for i, candidate in enumerate(candidates):
        bounds = candidate["row"][-4:]
        if intersection(bounds, case["evaluation_bounds_3006"]) < .5 * area(bounds):
            ignored.append(i)
            continue
        matches = []
        wrong_types = []
        for j, ref in enumerate(refs):
            b = ref["bounds_3006"]
            overlap = intersection(bounds, b)
            iou = overlap / (area(bounds) + area(b) - overlap)
            if (iou >= .25 or overlap / area(b) >= .5) and area(bounds) <= 4 * area(b):
                spatial_hits.add(j)
                if candidate["row"][3] in ref["change_types"]:
                    matches.append(j)
                else:
                    wrong_types.append({"candidate": i, "reference": j, "actual": candidate["row"][3],
                                        "expected": ref["change_types"]})
        if matches:
            hits.update(matches)
            supported.append({"candidate": i, "references": matches})
        else:
            unsupported.append(i)
            type_mismatches.extend(wrong_types)
    return {"expected_regions": len(refs), "returned_candidates": len(candidates),
            "matched_regions": len(hits), "missed_regions": [j for j in range(len(refs)) if j not in hits],
            "spatially_matched_regions": len(spatial_hits), "type_mismatches": type_mismatches,
            "supported_candidates": supported, "unsupported_candidates": unsupported,
            "outside_evaluation_area": ignored,
            "note": "Unsupported proposals require manual review; annotations are approximate regions, not cadastral truth."}


def fetch(client, case, window, tag, root, base):
    vintage = case["vintages"][tag]
    path = root / f"{case['id']}-{window['tile_id']}-{vintage}.png"
    bbox = [window[k] for k in ("ulx", "lry", "lrx", "uly")]
    params = {"service": "WMS", "version": "1.1.1", "request": "GetMap",
              "layers": vintage, "styles": "", "srs": "EPSG:3006", "bbox": ",".join(map(str, bbox)),
              "width": window["tile_px"], "height": window["tile_px"], "format": "image/png"}
    if not path.exists():
        response = client.get(base, params=params)
        response.raise_for_status()
        img = Image.open(io.BytesIO(response.content)).convert("RGB")
        if img.size != (window["tile_px"], window["tile_px"]) or max(ImageStat.Stat(img).stddev) < 4:
            raise RuntimeError(f"Missing or nearly uniform imagery: {case['id']} {vintage}")
        path.write_bytes(response.content)
    data = path.read_bytes()
    return data, {"path": path.name, "sha256": hashlib.sha256(data).hexdigest(),
                  "source_url": str(httpx.URL(base, params=params))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("fixtures") / "change_cases.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--concurrency", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--fetch-only", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    selected = [c for c in manifest["cases"] if not args.cases or c["id"] in args.cases]
    if not selected or (args.cases and set(args.cases) - {c["id"] for c in selected}):
        parser.error("Unknown or empty case selection")
    failures = 0
    with httpx.Client(timeout=httpx.Timeout(600, connect=30)) as client:
        for case in selected:
            pairs, sources = [], []
            for index, bounds in enumerate(case["windows_3006"]):
                x1, y1, x2, y2 = bounds
                window = {"tile_id": f"tile{index:02d}", "ulx": x1, "lry": y1,
                          "lrx": x2, "uly": y2, "tile_px": 800}
                pngs = {}
                for tag in ("a", "b"):
                    pngs[tag], source = fetch(client, case, window, tag, args.output, manifest["wms_url"])
                    sources.append({"tile_id": window["tile_id"], "vintage": tag, **source})
                pairs.append((window, pngs))
            print(f"{case['id']}: {len(pairs)} image pairs ready", flush=True)
            record = {"case": case, "sources": sources, "concurrency": args.concurrency}
            if not args.fetch_only:
                statuses = {w["tile_id"]: None for w, _ in pairs}
                def progress(tile):
                    path = args.output / f"{case['id']}-{tile['tile_id']}.json"
                    path.write_text(json.dumps(tile, indent=2))
                    print(f"{case['id']} {tile['tile_id']}: "
                          f"{tile['failure'] or 'analyzed'} ({tile['seconds']}s)", flush=True)
                try:
                    candidates, info = vision_change.infer(
                        client, iter(pairs), ["building"], case["vintages"], statuses,
                        (vision_change.settings()[0], args.concurrency), progress=progress)
                except RuntimeError as error:
                    failures += 1
                    record.update(error=str(error), statuses=statuses, score=None)
                    (args.output / f"{case['id']}.json").write_text(json.dumps(record, indent=2))
                    print(f"{case['id']}: unavailable; not scored as unchanged", flush=True)
                    continue
                # Private runtime routing is intentionally absent from shareable evidence.
                for name in ("model", "base_url"):
                    info.pop(name, None)
                record.update(candidates=[asdict(c) for c in candidates], model=info, statuses=statuses)
                record["score"] = score(record["candidates"], case) if info["complete"] else None
                failures += int(not info["complete"])
                print(json.dumps({"case": case["id"], "seconds": info["elapsed_seconds"],
                                  "usage": info["usage_cumulative"], "score": record["score"]}), flush=True)
            (args.output / f"{case['id']}.json").write_text(json.dumps(record, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
