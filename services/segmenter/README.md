# Segmenter service (SAM3)

Standalone HTTP service running SAM3 concept segmentation for the
`change_detect` job. It runs **natively on the Mac host**, not in compose —
MLX cannot run in Linux containers. The worker container reaches it via
`SAM3_URL` (default `http://host.docker.internal:8200` from the compose
network); nothing else in the system knows which backend is running: the HTTP
contract below is the compatibility seam.

## Install and run

```sh
cd services/segmenter
uv sync                                          # creates .venv, pulls mlx + pinned mlx_sam3
uv run uvicorn app:app --host 0.0.0.0 --port 8200
```

`python app.py` works too and respects `SAM3_PORT`. First `/segment` call
downloads the weights (~3.4 GB, cached in `~/.cache/huggingface`) and loads the
model; `/healthz` never triggers a load.

## Env vars

| var | default | meaning |
|-----|---------|---------|
| `SAM3_BACKEND` | `mlx` | `mlx` (Apple Silicon, mlx-community/sam3-image) or `transformers` (facebook/sam3) |
| `SAM3_PORT` | `8200` | listen port when started via `python app.py` |
| `HF_TOKEN` | — | transformers backend only: **facebook/sam3 is HF-gated** — request access on the model page first; the mlx-community mirror is not gated |

## HTTP contract

`GET /healthz` → `{"ok": true, "backend": "mlx"|"transformers", "model_loaded": bool}`
— never triggers a model load.

`POST /segment` with `{"image_png_b64": str, "concepts": [str, ...], "threshold": float}`
(threshold default 0.5) →

```json
{"width": 1008, "height": 1008,
 "model": {"backend": "mlx", "weights": "mlx-community/sam3-image", "resolution": 1008},
 "detections": [{"concept": "byggnad", "score": 0.91,
                 "box": [x1, y1, x2, y2],
                 "mask_png_b64": "..."}]}
```

Boxes are absolute pixels (floats); each mask is an 8-bit grayscale PNG with
values 0/255 at **exactly** the input image size. Errors: 422 on bad input,
503 with an actionable detail if the selected backend's deps are missing
(`/healthz` keeps working).

## Backend swap

The default venv is mlx-only; the transformers backend lives behind the `hf`
extra so neither backend's imports leak into the other (all backend imports are
lazy). For a future Linux/GPU deployment:

```sh
uv sync --extra hf
SAM3_BACKEND=transformers HF_TOKEN=hf_... uv run uvicorn app:app --host 0.0.0.0 --port 8200
```

Score calibration may differ between backends — the threshold stays a request
parameter, so callers tune it per backend rather than assuming parity.

## License

SAM3 weights (both mlx-community/sam3-image and facebook/sam3) are bound by the
Meta SAM License, not a plain open-source license. Per architecture doc §7 this
requires legal review before use in municipal contract deliverables.
