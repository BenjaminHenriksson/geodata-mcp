# Change-detection evaluation — 22 September 2026

The staged implementation produces useful **review candidates**, but this small
evaluation does not establish reliable automatic building-change decisions. All
seven annotated major construction/removal regions received a compatible proposal;
there were also two confirmed false proposals, two unverified proposals for one
small object, ambiguous duplicates, and one unavailable control. A single large
box covered two waterfront blocks. Region recall is not object-count or footprint
accuracy, and a high model confidence label is not a calibrated probability.

This evaluation used isolated file-based inference and public imagery. It did not
submit production jobs, modify application data, or change deployment.

## Results

The versioned [case manifest](../scripts/fixtures/change_cases.json) contains source
URLs, EPSG:3006 crop coordinates, vintage labels, approximate reference regions,
evidence gaps and demo prompts. The [saved results](../scripts/fixtures/change_results_2026-09-22.json)
contain input hashes, source request URLs, candidates and observations, tile failures,
reported usage, scoring and manual adjudication. Candidate numbers there are zero-based.

| Case | Expected regions | Proposals | Compatible regions / missed | Additional findings | Completed tiles | Elapsed |
|---|---:|---:|---:|---|---:|---:|
| Baldershallen, 2019–2023, development | 2 | 4 | 2 / 0 | Two unverified proposals for one small structure/container | 4/4 | 96.8 s |
| Skönsmons school, 2010–2023, held out | 3 | 7 | 3 / 0 | One false new-building claim on the removed pavilion/current court; two proposals outside study bounds; replacement also proposed as extension | 4/4 | 357.1 s |
| Norra kajen, 2019–2023, held out | 2 | 1 | 2 / 0 | One broad proposal covers both housing blocks | 2/2 | 28.2 s |
| Residential roofs, 2019–2023, control | 0 | Unavailable | Not scored | Three attempts exhausted; final failure was stream deadline | 0/1 | 246.1 s |
| Sports surfaces, 2019–2023, control | 0 | 1 | 0 / 0 | False location: returned box is on vegetation beside the pitch | 1/1 | 81.5 s |
| Exact same 2023 image, control | 0 | 0 | 0 / 0 | No hallucinated change on this pair | 1/1 | 18.8 s |

The school false proposal is semantically reversed: it claims a new green-roofed
building where another crop correctly reports demolition. The replayed conflict
guard marks both observations for review; it does not silently choose one. The
sports proposal at `[619553, 6921902.8, 619559.6, 6921910.4]` is not at the bright
small structures visible elsewhere in the image. Both are false positives even
though the model called them high confidence. Residential control failure gives
no evidence either way about accuracy on that image pair.

Reference regions were drawn by visual comparison before inference. Baldershallen
was the development example; the school, waterfront and controls were held out
from tuning. No drone images were used. Independent review found overly broad
allowed change directions in the initial scoring rubric. Those labels were
tightened and saved outputs rescored, with spatial hits and semantic mismatches
reported separately; input bounds, prompt and inference outputs did not change.
The later conflict flag was tested on independent synthetic examples and replayed
on saved results. It is a review aid, not a claimed accuracy improvement.

A compatible region match needs IoU ≥ 0.25 **or** at least 50% reference coverage,
candidate area ≤ 4 times reference area, and an allowed change type. A proposal is
scored only when at least half its area lies inside the declared review bounds.
Several proposals may support one region, and one broad proposal may support two.
The fixtures preserve those relationships instead of presenting seven region hits
as seven accurately located buildings. Unverified proposals are not converted to
either confirmed positives or confirmed negatives. The suite is too small and
partly unavailable for a general precision/recall claim.

## Dated construction evidence and cases 1, 3 and 7

These numbers refer to procurement workflows, not three numbered construction sites.
The manifest includes Swedish demo prompts and `demo_analysis_params` for `analyze`
with processor `change_detect`, `backend="vision"`, concepts `["building"]`, 0.25 m/px
and the appropriate collections. Its area polygons are transformed to EPSG:3014.
Those parameters are prepared examples, not claims that production jobs ran.
Production uses its normal grid; the file runner reproduces the exact frozen crop
windows (including the waterfront's deliberate 50 m offset).

**Case 1 — construction candidates and supporting records.** Baldershallen is
bracketed by 2019 and 2023 imagery. The owner reported completion and a planned
1 July 2022 handover in its [14 June 2022 announcement](https://www.balder.se/om-balder/press/06142022-0836/peab-f-rdigst-ller-nya-baldershallen-och-balder-tar--ver-som-fastighets-gare).
The [tennis federation's 26 August 2022 report](https://www.tennis.se/tennis-se/dags-for-invigning-av-nya-baldershallen-i-sundsvall/)
describes inauguration after two years of construction. Use this as a known
construction check; an image change alone does not establish unauthorised work.

**Case 3 — compare with the correct permit drawing.** The municipal archive has
[BYGG2021-000790 for HAGA 4:51](https://sundsvallportal.ondemand.formpipe.com/Controllers/Aip/ViewMetadata?contentId=15&archiveId=20&aipId=6e932080-c91d-4154-81df-eb72d8fac973),
created 20 May 2021 and closed 17 October 2022, concerning Baldershov extension,
layout/facade and parking changes. Metadata lists a Situationsplan created
2 September 2021. The actual drawing download was blocked in browser verification,
so its approved revision, page, dimensions and decision conditions remain
unverified. The repeatable demo finds the imagery candidates and the real record,
then identifies that missing evidence. It does **not** claim a departure from a permit.

The school gives an independent construction/removal example. The
[municipal 17 May 2017 notice](https://news.cision.com/se/sundsvalls-kommun/r/dags-for-forsta-spadtaget-for-skonsmons-skola---en-skola-anpassad-for-framtiden,c2266980)
announces the next day's groundbreaking, replacement buildings and removal of
existing structures. The [29 May 2018 notice](https://news.cision.com/se/sundsvalls-kommun/r/invigning-av-den-nya-skolrestaurangen-pa-skonsmons-skola,c2534320)
reports completion of the first phase. The advertised 2016 imagery was missing
at this location, so the evaluation uses 2010–2023; that broad interval can include
other changes. No approved school permit drawing was verified.

**Case 7 — construction plus actual protection status.** The
[developer's 4 June 2020 announcement](https://news.cision.com/se/magnolia-bostad-ab/r/magnolia-bostad-saljer-360-bostader-i-sundsvall,c3128242)
places the first 360-home stage between planned autumn 2020 construction and 2023
occupancy. The [owner's 21 October 2021 announcement](https://www.mynewsdesk.com/se/heimstaden/pressreleases/invigning-av-heimstadens-foersta-nybyggnationsprojekt-i-sundsvall-3138300)
identifies Lastkajen and Pollaren phases. The 2019–2023 images show new blocks.

An exact geometric intersection of the approximate reference regions with the
municipal current-plan layer finds DP376 and DP486, approximately 14%/86% for
the northern region and 13%/87% for the southern. DP550 was returned by the bbox
search but intersects neither region. These percentages are annotation overlaps,
not surveyed property boundaries.

The [DP376 scanned file](https://karta.sundsvall.se/Detaljplan/SkannadHandling/2281K-DP-376.pdf),
PDF page 68 of 74, has a county opinion dated 18 April 2011, reference
402-1309-11, referring to a 12 April order revoking shore protection within the
plan. The [DP486 file](https://karta.sundsvall.se/Detaljplan/SkannadHandling/2281K-DP-486.pdf)
has an `a2` revocation legend on PDF page 5; page 22 (printed 16, dated
15 October 2018, Dnr 2014-01030) explains the revocation; page 50 (printed 3,
dated 11 October 2018, SBN 2014-01030) records county comments and reduced water
revocation extent. These pages were visually inspected.

This is a useful control against automatic legal escalation: proximity to water
or intersection with a coarse protection layer is insufficient when plan-specific
revocations exist. Exact current boundaries, the applicable adopted decision and
object-specific conditions still need checking. No violation or exemption need is
established by this evaluation.

## Runtime behavior and latency

Four concurrent detailed requests already existed. This change retains the
configurable limit (maximum eight), 800×800 high-detail images and the existing
16,384-token output allowance. There is **no measured sequential-versus-parallel
speedup claim**. Reported elapsed times describe these runs only; request retries
and endpoint variability matter. Baldershallen completed four attempts; the school
needed eight attempts for four successful tiles. An earlier batch stalled and
was stopped before the bounded diagnostic run; it is not included in accuracy or
timing totals.

Retries now share rate-limit cooldown, honour Retry-After, and stop after three
attempts per tile. Long requested cooldowns are deferred rather than shortened.
Malformed results are failures, not empty change lists. Authentication/configuration
errors cancel new requests. Partial runs retain successful evidence with explicit
failed/cancelled tile coverage and `complete=false`; the file evaluator does not
score an incomplete run as unchanged. Already running calls are drained within
their transport/deadline bounds; cancellation does not instantly abort every call.

`VISION_STREAM_DEADLINE_SECONDS` defaults to 600 seconds and limits response-body
stream duration, independently of output/context allowance. The isolated evaluation
used 120 seconds to bound diagnosis, without altering deployed configuration.
For HTTP/1.0 and HTTP/1.1, a request-dedicated connection and the documented network
stream socket extension allow a blocked read to be interrupted. `Connection: close`
prevents the timer from reaching a later pooled request, including natural EOF
without a final event. Connect/write/response-header phases retain the caller's
HTTP timeouts. HTTP/2 and custom transports are checked at byte boundaries and
remain subject to caller read timeouts; a strict blocked-read interruption is not
claimed for them. Local real-streaming tests cover heartbeats, partial lines,
silent reads, normal completion, EOF and an unaffected simultaneous request.

The configured transcription override disables reasoning; document question
answering and change comparison retain reasoning. A separate one-pair diagnostic
completed in 44.76 seconds with reasoning and 4.97 seconds without, but proposed
materially different boxes. That is not sufficient evidence to disable reasoning
for reliability. Both returned valid final JSON; stalled tiles establish endpoint
incompletion, not model detection accuracy. Detailed visual token counts remain
endpoint controlled despite `detail="high"`. Usage totals are reported usage only;
missing events and failed attempts can make them incomplete.

Cross-crop reconciliation compares common projected coordinates. It retains an
observed box, uses complete-link matching, keeps ambiguous one-to-many matches and
different categories separate, and accepts low-IoU containment only for a smaller
box clipped at a tile edge. Observations, source tiles and original confidence
labels survive. It does not union boxes into invented footprints or split a model's
already overbroad proposal. Opposite-direction observations from different tiles
with IoU ≥ 0.60 or smaller-box coverage ≥ 0.90 receive `review_required=true` and
`conflicting_observations`. Both remain visible: demolition followed by replacement
can legitimately produce opposing directions, so the flag requests review rather
than declaring either observation wrong.

## Reproduce without production jobs

Use `uv` and the repository `.venv`, install `requirements-test.txt` plus the worker
vision dependencies if absent, and supply the normal private `VISION_*` environment.
No credentials or routing identity are part of the fixtures. The exact request
prompt is versioned in `vision_change._request`; expected labels never enter it.

```sh
uv venv .venv
uv pip install -r requirements-test.txt
.venv/bin/python scripts/evaluate_vision_change.py --output .evaluation/recheck --fetch-only
VISION_STREAM_DEADLINE_SECONDS=120 .venv/bin/python scripts/evaluate_vision_change.py --output .evaluation/recheck --concurrency 4
.venv/bin/pytest -q tests/test_change_boxes.py tests/test_change_evaluation.py tests/test_vision_change.py tests/test_vision_deadline.py
```

The `--fetch-only` command only downloads public images. The following command purchases inference;
use `--case norra_kajen` (or another manifest ID) for a bounded subset. To compare
concurrency, repeat the *same* frozen cases and image hashes with `--concurrency 1`
and a separate output directory, record both retries and outcomes, and do not
present endpoint variability as a software speed improvement. Raw assets and
per-call captures stay under gitignored `.evaluation/`. Refreshing WMS content may
change bytes: compare the recorded hashes before treating a new run as identical.
