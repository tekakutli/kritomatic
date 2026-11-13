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
    if (typeof opts.preferSelectionMaskChildren === "boolean") {
      body.prefer_selection_mask_children =
        opts.preferSelectionMaskChildren;
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

/* ------------------------------------------------------------------
   @thecanvas auto-sync helpers
   ------------------------------------------------------------------
   `fetchActiveState` returns the daemon's `comfy_status` data so the
   autosync poller can read `config.resources["@thecanvas"]`.

   `fetchResourceImage` resolves that spec to a PNG data URL via
   `/krita/resource_image`. */

export async function fetchActiveState() {
  try {
    const res = await fetch("/comfy/active_state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!res.ok) return { success: false, message: "HTTP " + res.status };
    return await res.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}

export async function fetchResourceImage(spec) {
  try {
    const res = await fetch("/krita/resource_image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(spec),
    });
    if (!res.ok) return { success: false, message: "HTTP " + res.status };
    return await res.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}
"""