"""ViT-MAE crop embedder — the 768-d CLS extraction path Drawatoon needs.

This is the minimal subset of MAGI's `MagiModel.predict_crop_embeddings`
needed to feed the `ref_embedding_proj` in Drawatoon's ip branch:

    image + bboxes  →  crops  →  ViT-MAE  →  768-d CLS per crop

All three inputs — ViTMAEConfig, ViTImageProcessor, and the weight
tensor file — are loaded explicitly from `_DEFAULT_SOURCE`.  No
Hugging Face Hub download is ever attempted, and no filename
convention is relied on.

THREADING
=========
The singleton is created under a lock on first call and cached for
the process lifetime.  First call blocks for the duration of the
model load (~3-5s on CPU, ~10-15s if CUDA initialization is required
and we ever switch to GPU).  Concurrent first calls all wait on the
same lock.

DEVICE
======
Default is CPU.  ViT-MAE-base is ~86M params; on CPU, inference for
up to ~50 crops per call is under 500ms.  Keeping it off GPU avoids
competing for VRAM with Drawatoon + T5 + KSampler.  A GPU variant is
one parameter away if VRAM is not a constraint.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import List, Optional

import numpy as np
import torch
from PIL import Image
from safetensors.torch import load_file
from transformers import ViTMAEConfig, ViTImageProcessor, ViTMAEModel


# ---------------------------------------------------------------------------
# Local model directory.
#
# Expected contents (see prepare_magi_crop_embedder.py):
#   vit_mae_config.json                ViTMAEConfig JSON
#   vit_mae_preprocessor.json          ViTImageProcessor config
#   vit_mae_crop_embedder.safetensors  ViT-MAE weights extracted from MAGI
# ---------------------------------------------------------------------------
_DEFAULT_SOURCE = str(Path.home() / "ComfyUI/models/magi_crop_embedder")

_CONFIG_FILENAME       = "vit_mae_config.json"
_PREPROCESSOR_FILENAME = "vit_mae_preprocessor.json"
_WEIGHTS_FILENAME      = "vit_mae_crop_embedder.safetensors"

_EMBEDDING_DIM = 768
_BATCH_SIZE    = 64


def _log(msg: str) -> None:
    print(f"[magi:crop_embedder] {msg}", flush=True)


def _crop_image(image: np.ndarray, bboxes: List[List[float]]) -> List[np.ndarray]:
    """Crop each bbox from `image` (HxWx3 uint8), clamped to bounds.

    Verbatim port of `MagiProcessor.crop_image` — same clamping,
    same minimum-size enforcement, same tolerance for degenerate
    boxes.  Kept identical so crops match what Drawatoon was trained
    against.
    """
    h_img, w_img = image.shape[:2]
    crops = []
    for bbox in bboxes:
        x1, y1, x2, y2 = (float(v) for v in bbox)

        # Normalize corner order.
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)

        # Round to int, clamp to image bounds.
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w_img, x2), min(h_img, y2)
        x1, y1 = min(w_img, x1), min(h_img, y1)
        x2, y2 = max(0, x2), max(0, y2)

        # Minimum-size enforcement — if a crop is < 10 px in a
        # dimension, expand it, preferring the direction with room.
        if x2 - x1 < 10:
            if w_img - x1 > 10:
                x2 = x1 + 10
            else:
                x1 = x2 - 10
        if y2 - y1 < 10:
            if h_img - y1 > 10:
                y2 = y1 + 10
            else:
                y1 = y2 - 10

        # Final safety clamp in case the expansion went out of bounds.
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w_img, x2), min(h_img, y2)

        crops.append(image[y1:y2, x1:x2])
    return crops


def _load_processor(path: Path) -> ViTImageProcessor:
    """Build a ViTImageProcessor from an explicit JSON file.

    `from_dict` avoids Hugging Face's metadata lookup path entirely —
    no `_processor_class` resolution, no Hub probe.
    """
    cfg = json.loads(path.read_text())
    cfg.pop("_processor_class", None)   # not meaningful outside HF Hub
    return ViTImageProcessor.from_dict(cfg)


def _load_model(source_path: Path) -> ViTMAEModel:
    """Build a ViTMAEModel from explicit config + weight files.

    Returns a model whose weights are the ViT-MAE encoder+decoder
    extracted from MAGI.  `strict=False` because the MAGI extraction
    retains the pretraining decoder (`ViTMAEForPreTraining` has one;
    plain `ViTMAEModel` does not), so those keys are reported as
    unexpected and quietly dropped — exactly the behaviour
    `from_pretrained` would give.

    A missing *encoder* or *embeddings* key, however, is a hard error:
    it means the extraction prefix in `prepare_magi_crop_embedder.py`
    was wrong and the CLS vectors would be garbage.
    """
    config = ViTMAEConfig.from_dict(
        json.loads((source_path / _CONFIG_FILENAME).read_text())
    )
    model = ViTMAEModel(config)
    state = load_file(str(source_path / _WEIGHTS_FILENAME))
    missing, unexpected = model.load_state_dict(state, strict=False)

    encoder_missing = [
        k for k in missing
        if k.startswith(("encoder.", "embeddings."))
    ]
    if encoder_missing:
        raise RuntimeError(
            "ViT-MAE encoder weights are incomplete.  Missing keys:\n  "
            + "\n  ".join(encoder_missing)
        )

    _log(
        f"weights loaded ({len(state)} tensors; "
        f"{len(unexpected)} unexpected → dropped pretraining decoder)"
    )
    return model


class CropEmbedder:
    """ViT-MAE-base CLS extraction over a batch of crops."""

    def __init__(
        self,
        source: str = _DEFAULT_SOURCE,
        device: Optional[str] = None,
    ):
        self.source = source
        self.device = torch.device(
            device or ("cuda" if torch.cuda.is_available() else "cpu")
        ) if device else torch.device("cpu")

        source_path = Path(source)
        if not source_path.is_dir():
            raise FileNotFoundError(
                f"ViT-MAE model directory not found: {source_path}\n"
                f"Expected files:\n"
                f"  {source_path / _CONFIG_FILENAME}\n"
                f"  {source_path / _PREPROCESSOR_FILENAME}\n"
                f"  {source_path / _WEIGHTS_FILENAME}\n"
                f"Run prepare_magi_crop_embedder.py to produce them."
            )

        _log(f"loading ViT-MAE from {source_path} on {self.device}...")
        self.processor = _load_processor(source_path / _PREPROCESSOR_FILENAME)
        self.model     = _load_model(source_path)
        self.model.eval().to(self.device)

        # MAGI temporarily forces mask_ratio=0 during inference so the
        # encoder sees the full image; the decoder is never invoked
        # (we use ViTMAEModel, not ViTMAEForPreTraining).  The CLS
        # token at position 0 is the correct global feature.
        self.model.config.mask_ratio = 0.0
        if hasattr(self.model, "embeddings") and hasattr(
            self.model.embeddings, "config"
        ):
            self.model.embeddings.config.mask_ratio = 0.0

        _log("ViT-MAE loaded")

    @torch.inference_mode()
    def predict(
        self,
        image: np.ndarray,
        bboxes_px: List[List[float]],
        batch_size: int = _BATCH_SIZE,
    ) -> List[torch.Tensor]:
        """Return one 768-d CPU tensor per bbox, in bbox order.

        `image` is HxWx3 uint8 RGB.  `bboxes_px` are [x1,y1,x2,y2] in
        image-pixel coords.
        """
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError(
                f"expected HxWx3 RGB image, got shape {image.shape}"
            )

        crops = _crop_image(image, bboxes_px)
        if not crops:
            return []

        inputs = self.processor(crops, return_tensors="pt").pixel_values
        inputs = inputs.to(self.device)

        outs = []
        for i in range(0, inputs.shape[0], batch_size):
            batch = inputs[i:i+batch_size]
            hidden = self.model(batch).last_hidden_state  # [B, 1+n_patches, D]
            cls    = hidden[:, 0]                         # [B, 768]
            outs.append(cls.cpu())

        all_cls = torch.cat(outs, dim=0)                  # [N, 768]
        return [all_cls[i] for i in range(all_cls.shape[0])]

    def predict_as_lists(
        self,
        image: np.ndarray,
        bboxes_px: List[List[float]],
        batch_size: int = _BATCH_SIZE,
    ) -> List[List[float]]:
        """Same as `predict` but returns plain Python float lists,
        ready for `json.dumps` / HTTP responses."""
        return [t.tolist() for t in self.predict(image, bboxes_px, batch_size)]


# ---------------------------------------------------------------------------
#  Process-wide singleton
# ---------------------------------------------------------------------------

_singleton: Optional[CropEmbedder] = None
_singleton_source: Optional[str] = None
_singleton_lock = threading.Lock()


def get_or_create_embedder(
    source: str = _DEFAULT_SOURCE,
    device: Optional[str] = None,
) -> CropEmbedder:
    """Lazily build (once) and return the process-wide CropEmbedder.

    Called by the HTTP route on every request; the lock + cache mean
    only the first call pays the load cost.

    `source` is coerced to the configured local directory regardless
    of what the caller passes.  This is deliberate: it removes any
    code path by which a Hugging Face model name could sneak in and
    trigger a download.  A different *local* directory would still
    need an explicit edit here, not a runtime argument.
    """
    global _singleton, _singleton_source
    source = _DEFAULT_SOURCE

    with _singleton_lock:
        if _singleton is not None and _singleton_source == source:
            return _singleton
        if _singleton is not None:
            _log(
                f"replacing existing embedder "
                f"(source {_singleton_source!r} → {source!r})"
            )
            _singleton = None
        _singleton = CropEmbedder(source=source, device=device)
        _singleton_source = source
        return _singleton


def clear_embedder() -> None:
    """Drop the singleton. Next call reloads.  Useful for testing."""
    global _singleton, _singleton_source
    with _singleton_lock:
        _singleton = None
        _singleton_source = None
