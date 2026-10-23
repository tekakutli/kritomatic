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
  if (opts.layerName) body.layer_name = opts.layerName;
  if (opts.invert)    body.invert = true;
  if (opts.selMode)   body.sel_mode = opts.selMode;

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
