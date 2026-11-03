"""Region-constrained composite: stitch the new mask into the old one
only inside the painted region.

Pure async function; no state, no draw hook.  The caller (`runRefineAction`
in `pg_panel_bindings.js`) awaits the result and then replaces the mask."""

COMPOSITE_JS = r"""
import { board } from "./pg_state.js";

function _loadImageB64(b64) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = "data:image/png;base64," + b64;
  });
}

export async function compositeRefineWithRegion(oldB64, newB64, regionCanvas) {
  if (!regionCanvas) return newB64;
  const W = regionCanvas.width, H = regionCanvas.height;
  if (W !== board.imageWidth || H !== board.imageHeight) return newB64;

  const [oldImg, newImg] = await Promise.all([
    _loadImageB64(oldB64), _loadImageB64(newB64),
  ]);

  const oldCv = document.createElement("canvas");
  oldCv.width = W; oldCv.height = H;
  const oldCx = oldCv.getContext("2d");
  oldCx.drawImage(oldImg, 0, 0, W, H);
  const oldPx = oldCx.getImageData(0, 0, W, H).data;

  const newCv = document.createElement("canvas");
  newCv.width = W; newCv.height = H;
  const newCx = newCv.getContext("2d");
  newCx.drawImage(newImg, 0, 0, W, H);
  const newPx = newCx.getImageData(0, 0, W, H).data;

  const regionCv = document.createElement("canvas");
  regionCv.width = W; regionCv.height = H;
  const regionCx = regionCv.getContext("2d");
  regionCx.drawImage(regionCanvas, 0, 0, W, H);
  const regionPx = regionCx.getImageData(0, 0, W, H).data;

  let anyInRegion = false;
  const out = new Uint8ClampedArray(oldPx.length);
  for (let i = 0; i < out.length; i += 4) {
    if (regionPx[i + 3] > 128) {
      out[i]     = newPx[i];
      out[i + 1] = newPx[i + 1];
      out[i + 2] = newPx[i + 2];
      out[i + 3] = newPx[i + 3];
      anyInRegion = true;
    } else {
      out[i]     = oldPx[i];
      out[i + 1] = oldPx[i + 1];
      out[i + 2] = oldPx[i + 2];
      out[i + 3] = oldPx[i + 3];
    }
  }

  if (!anyInRegion) return oldB64;

  const comp = document.createElement("canvas");
  comp.width = W; comp.height = H;
  const compCx = comp.getContext("2d");
  const imgData = compCx.createImageData(W, H);
  imgData.data.set(out);
  compCx.putImageData(imgData, 0, 0);
  return comp.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}
"""
