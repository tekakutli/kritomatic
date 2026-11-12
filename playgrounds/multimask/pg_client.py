"""HTTP bridge to the multimask server.  Every action is a fetch to
the Python server; the server fans out to the kritomatic daemon for
the Krita import paths."""

CLIENT_JS = r"""
export async function uploadImageFile(file) {
  const res = await fetch("/upload_image", {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Filename": encodeURIComponent(file.name || "image"),
    },
    body: file,
  });
  if (!res.ok) throw new Error("HTTP " + res.status);
  const payload = await res.json();
  if (!payload.success) throw new Error(payload.message || "upload failed");
  return payload;
}

export async function importFromKrita(opts) {
  const body = {};
  if (opts && opts.name) body.name = opts.name;
  try {
    const res = await fetch("/import_image", {
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

export async function checkImageStatus() {
  try {
    const res = await fetch("/image_status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    return await res.json();
  } catch (e) {
    return { success: false, active: false, message: e.message };
  }
}

export async function fetchCachedImage(wantHash) {
  const body = wantHash ? { hash: wantHash } : {};
  try {
    const res = await fetch("/cached_image", {
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