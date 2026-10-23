KRITA_CLIENT_JS = r"""
export async function checkKritaStatus() {
  try {
    const res = await fetch("/krita/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    return await res.json();
  } catch (e) {
    return { success: false, reachable: false, message: e.message };
  }
}

export async function applyToKrita(opts) {
  const body = {
    mask_b64: opts.maskB64,
    mode:     opts.mode,
  };
  if (opts.layerName)             body.layer_name = opts.layerName;
  if (opts.invert)                body.invert = true;
  if (opts.selMode)               body.sel_mode = opts.selMode;
  if (opts.expandPixels)          body.expand_pixels = opts.expandPixels;
  if (opts.contractPixels)        body.contract_pixels = opts.contractPixels;
  if (opts.smoothIterations)      body.smooth_iterations = opts.smoothIterations;
  if (opts.featherRadius)         body.feather_radius = opts.featherRadius;

  try {
    const res = await fetch("/krita/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await res.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}

export async function extractMaskToLayer(opts) {
  // Same endpoint as applyToKrita; the daemon server composes the
  // extract + blend + opacity + group chain. Any of blendMode,
  // opacity, and groupName may be empty, in which case the
  // corresponding post-apply step is skipped.
  const body = {
    mask_b64: opts.maskB64,
    mode:     opts.mode,
  };
  if (opts.layerName)             body.layer_name = opts.layerName;
  if (opts.newLayerName)          body.new_layer_name = opts.newLayerName;
  if (opts.blendMode)             body.blend_mode = opts.blendMode;
  if (opts.opacity !== undefined &&
      opts.opacity !== null &&
      opts.opacity !== "")        body.opacity = opts.opacity;
  if (opts.groupName)             body.group_name = opts.groupName;
  if (opts.invert)                body.invert = true;
  if (opts.expandPixels)          body.expand_pixels = opts.expandPixels;
  if (opts.contractPixels)        body.contract_pixels = opts.contractPixels;
  if (opts.smoothIterations)      body.smooth_iterations = opts.smoothIterations;
  if (opts.featherRadius)         body.feather_radius = opts.featherRadius;

  try {
    const res = await fetch("/krita/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await res.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}

export async function importFromKrita(opts) {
  const body = {};
  if (opts && opts.name) body.name = opts.name;

  try {
    const res = await fetch("/krita/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await res.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}
"""
