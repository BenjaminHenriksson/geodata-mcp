"""SAM3 backend adapters behind get_backend().

Both backends expose the same surface — segment(pil_image, concepts, threshold)
returning a list of {concept, score, box, mask} dicts (score float, box absolute
pixels [x1, y1, x2, y2] floats, mask a PIL 'L' image with values 0/255 at
exactly the input image size) plus model_info() — so app.py and everything
behind the HTTP seam stay backend-agnostic.

All heavy imports are lazy (inside functions) so the default mlx venv never
imports transformers and vice versa. Missing deps raise BackendUnavailable,
which app.py maps to HTTP 503.
"""

import logging
import os

log = logging.getLogger("segmenter.backends")

# Sam3Processor bakes its threshold at init; keep a low floor and filter by the
# per-request threshold at the seam instead.
CONFIDENCE_FLOOR = 0.05

_mask_case_logged = False


class BackendUnavailable(RuntimeError):
    pass


def get_backend():
    """Build (and load) the backend selected by SAM3_BACKEND. Heavy: downloads
    weights on first run. Callers serialize via app.py's lock."""
    name = os.environ.get("SAM3_BACKEND", "mlx").strip().lower()
    if name == "mlx":
        return MlxBackend()
    if name == "transformers":
        return TransformersBackend()
    raise BackendUnavailable(f"unknown SAM3_BACKEND {name!r} — use 'mlx' or 'transformers'")


def _mask_to_pil(mask_np, size):
    """Binary numpy mask (any truthy dtype/shape, possibly model-resolution)
    -> PIL 'L' image, values 0/255, exactly `size` (w, h)."""
    global _mask_case_logged
    import numpy as np
    from PIL import Image

    mask_np = np.squeeze(np.asarray(mask_np))
    img = Image.fromarray((mask_np > 0).astype(np.uint8) * 255, mode="L")
    if not _mask_case_logged:
        if img.size == size:
            log.info("masks arrive at original image size %s", size)
        else:
            log.info("masks arrive at model resolution %s; resizing nearest to %s", img.size, size)
        _mask_case_logged = True
    if img.size != size:
        img = img.resize(size, Image.NEAREST)
    return img


# The mlx_sam3 wheel omits its repo-level assets/ dir but resolves the CLIP BPE
# vocab relative to the installed package. The vocab's used content (the 48 894
# merge rules SimpleTokenizer slices out) is byte-identical to the canonical
# merges.txt in the ungated openai/clip-vit-large-patch14 HF repo — build the
# asset from that instead of fetching the port's GitHub copy.
_BPE_ASSET = "bpe_simple_vocab_16e6.txt.gz"
_BPE_SOURCE_REPO = "openai/clip-vit-large-patch14"


def _ensure_mlx_assets():
    import sam3

    path = os.path.normpath(
        os.path.join(os.path.dirname(sam3.__file__), "..", "assets", _BPE_ASSET)
    )
    if os.path.exists(path):
        return
    import gzip

    from huggingface_hub import hf_hub_download

    log.info("building tokenizer asset %s from %s", _BPE_ASSET, _BPE_SOURCE_REPO)
    merges = hf_hub_download(_BPE_SOURCE_REPO, "merges.txt")
    with open(merges, encoding="utf-8") as f:
        text = f.read()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)


class MlxBackend:
    """mlx-community/sam3-image via the mlx_sam3 port (import name `sam3`)."""

    name = "mlx"
    weights = "mlx-community/sam3-image"
    resolution = 1008

    def __init__(self):
        try:
            from sam3 import build_sam3_image_model
            from sam3.model.sam3_image_processor import Sam3Processor
        except ImportError as e:
            raise BackendUnavailable(
                f"mlx backend deps missing ({e}) — run `uv sync` in services/segmenter"
            ) from e
        _ensure_mlx_assets()
        log.info("loading %s (first run downloads ~3.4 GB)...", self.weights)
        model = build_sam3_image_model()
        self._proc = Sam3Processor(model, confidence_threshold=CONFIDENCE_FLOOR)
        log.info("mlx sam3 model loaded")

    def model_info(self) -> dict:
        return {"backend": self.name, "weights": self.weights, "resolution": self.resolution}

    def segment(self, pil_image, concepts, threshold) -> list[dict]:
        import numpy as np

        detections = []
        state = self._proc.set_image(pil_image)  # ONE set_image per image
        for concept in concepts:
            state = self._proc.set_text_prompt(concept, state)
            masks, boxes, scores = state["masks"], state["boxes"], state["scores"]
            if scores is not None:
                # mlx.core arrays -> numpy before any indexing/float() use
                scores_np = np.asarray(np.array(scores), dtype=np.float64).reshape(-1)
                boxes_np = np.asarray(np.array(boxes), dtype=np.float64).reshape(-1, 4)
                masks_np = np.array(masks)
                for i, score in enumerate(scores_np):
                    if float(score) < threshold:
                        continue
                    detections.append(
                        {
                            "concept": concept,
                            "score": float(score),
                            "box": [float(v) for v in boxes_np[i]],
                            "mask": _mask_to_pil(masks_np[i], pil_image.size),
                        }
                    )
            self._proc.reset_all_prompts(state)
        return detections


class TransformersBackend:
    """facebook/sam3 via transformers (HF-gated: needs HF_TOKEN). Same contract."""

    name = "transformers"
    weights = "facebook/sam3"
    resolution = 1008

    def __init__(self):
        try:
            import torch
            from transformers import Sam3Model, Sam3Processor
        except ImportError as e:
            raise BackendUnavailable(
                f"transformers backend deps missing ({e}) — install the hf extra "
                "(`uv sync --extra hf` in services/segmenter) and set HF_TOKEN "
                "(facebook/sam3 is gated)"
            ) from e
        self._torch = torch
        log.info("loading %s (gated — needs HF_TOKEN with granted access)...", self.weights)
        try:
            self._model = Sam3Model.from_pretrained(self.weights, device_map="auto")
            self._proc = Sam3Processor.from_pretrained(self.weights)
        except Exception as e:
            # gated-access refusals and missing accelerate must surface as the
            # contractual 503, not a bare 500
            raise BackendUnavailable(
                f"transformers backend failed to load {self.weights}: {e}"
            ) from e
        log.info("transformers sam3 model loaded on %s", self._model.device)

    def model_info(self) -> dict:
        return {"backend": self.name, "weights": self.weights, "resolution": self.resolution}

    def segment(self, pil_image, concepts, threshold) -> list[dict]:
        import numpy as np

        detections = []
        for concept in concepts:
            inputs = self._proc(images=pil_image, text=concept, return_tensors="pt").to(
                self._model.device
            )
            with self._torch.no_grad():
                outputs = self._model(**inputs)
            res = self._proc.post_process_instance_segmentation(
                outputs,
                threshold=threshold,
                mask_threshold=0.5,
                target_sizes=inputs.get("original_sizes").tolist(),
            )[0]
            for mask, box, score in zip(res["masks"], res["boxes"], res["scores"]):
                detections.append(
                    {
                        "concept": concept,
                        "score": float(score),
                        "box": [float(v) for v in np.asarray(box.cpu()).reshape(-1)],
                        "mask": _mask_to_pil(np.asarray(mask.cpu()), pil_image.size),
                    }
                )
        return detections
