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
  // export_layer mode only
  if (opts.name)                  body.name = opts.name;
  if (opts.aboveLayer)            body.above_layer = opts.aboveLayer;

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

export async function exportMaskAsLayer(opts) {
  // Thin wrapper over applyToKrita for the export_layer mode. Kept as
  // its own function so callers read naturally: "I have a mask, I want
  // it as a layer, at this position".
  //
  // aboveLayer accepts "", "@active", or an explicit layer name. The
  // daemon resolves "@active" itself (single round trip) rather than
  // the client having to look up the active layer's name first.
  return applyToKrita({
    maskB64: opts.maskB64,
    mode: "export_layer",
    name: opts.name,
    aboveLayer: opts.aboveLayer,
    invert: opts.invert,
    expandPixels: opts.expandPixels,
    contractPixels: opts.contractPixels,
    smoothIterations: opts.smoothIterations,
    featherRadius: opts.featherRadius,
  });
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

export async function importKritaMasks(opts) {
  // Ask the daemon to read the currently-selected layer(s) in Krita as
  // base64 masks. When `layerNames` is provided (array or string), those
  // specific layers are read instead of the selection. Response shape:
  //
  //   { success, masks: [{name, type, source, width, height, mask_b64}],
  //     skipped: [{name, reason}], selection_path, message }
  //
  // `selection_path` reports which multi-selection accessor succeeded
  // ("view.selectedNodes", "doc.selectedNodes", "node.isSelected",
  // or "active_only") so the caller can tell the user whether multiple
  // selected layers were imported or just the active one.
  const body = {};
  if (opts) {
    if (opts.layerNames) {
      body.layer_names = Array.isArray(opts.layerNames)
        ? opts.layerNames.join(",")
        : String(opts.layerNames);
    }
    if (typeof opts.includePaintAlpha === "boolean") {
      body.include_paint_alpha = opts.includePaintAlpha;
    }
  }

  try {
    const res = await fetch("/krita/import_masks", {
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
