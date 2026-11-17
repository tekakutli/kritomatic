"""HTTP bridge to the multimask server."""

CLIENT_JS = r"""
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
  }
  try {
    const res = await fetch("/import_masks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await res.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}

export async function checkKritaStatus() {
  try {
    const res = await fetch("/krita_status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    return await res.json();
  } catch (e) {
    return { success: false, reachable: false, message: e.message };
  }
}

/* ------------------------------------------------------------------
   @thecanvas auto-sync helpers */

export async function fetchActiveState() {
  try {
    const res = await fetch("/active_state", {
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
    const res = await fetch("/resource_image", {
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