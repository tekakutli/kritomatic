#!/usr/bin/env sh

source /home/tekakutli/code/kritomatic-auxiliary/bin/activate && \
cd /home/tekakutli/files/org/dotfiles/input_controller/krita_plugin/kritomatic/kritomatic_xremap/scripts/overlay_pipeline && \
mkdir -p /tmp/output/ && \
python flag_downloader.py && \
python bulk_text_renderer.py && \
python overlay.py && \
python output_grid_pdf.py
