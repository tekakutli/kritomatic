"""Magi crop-embedder — ViT-MAE CLS vectors for character reference images.

Drawatoon's ip branch consumes a 768-d vector per character bbox,
projected through `ref_embedding_proj` (768 → 4×1152).  That vector
is the CLS token of `facebook/vit-mae-base` applied to the character
crop — exactly what MAGI's `MagiModel.predict_crop_embeddings`
produces.  TrOCR and Conditional DETR in the original MAGI model
serve auto-detection and transcription; the magi playground supplies
manual bboxes, so neither is needed here.

This package exposes:

  · `POST /magi/crop_embed`  — ComfyUI HTTP route, consumed by the
                                magi playground's `/analyze_characters`
  · `MagiLoader`             — a V3 node returning an embedder handle,
                                for future use when MAGI graduates from
                                "route" to "graph-visible model"
"""