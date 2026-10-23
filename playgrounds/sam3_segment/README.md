# SAM3 Segmentation Playground

A standalone browser playground that drives ComfyUI's native `SAM3_Detect`
node. Drop an image, type a text prompt (and/or click points), press Run,
and see the resulting masks.

## Requirements

- **ComfyUI** running on `127.0.0.1:8188`.
- **`comfyui-tooling-nodes`** installed in ComfyUI's `custom_nodes/`
  (provides `ETN_KritaCanvas`, `ETN_KritaOutput`, `ETN_LoadImageCache`).
- **A ComfyUI build with `SAM3_Detect`** (ComfyUI core 0.39.0 or later).
- **The SAM3 checkpoint** at ComfyUI's
  `models/checkpoints/segmentation/sam3.1_multiplex_fp16.safetensors`.
- Python 3.10+ for the playground itself. Venv recommended.

## Install

```bash
cd playgrounds/sam3_segment
python3 -m venv .venv && . .venv/bin/activate
pip install pillow websockets
