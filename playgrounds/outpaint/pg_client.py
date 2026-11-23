"""HTTP bridge to the layout server."""

CLIENT_JS = r"""
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

// Trigger generation through the daemon with no_paste.  Returns
// {success, b64, count} on success — b64 is the base64-encoded PNG
// payload (no data: prefix).  The server does not touch Krita here;
// the caller is expected to display the returned PNG in the viewer.
// See outpaint_server.py's /generate.
export async function generate() {
  try {
    const res = await fetch("/generate", {
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

// Materialize the current PNG as a brand-new Krita document.  The
// server chains create_new_with_dimensions + comfy_paste_image_b64
// so the user's original document is untouched.
// See outpaint_server.py's /send_to_krita.
export async function sendToKrita(b64, width, height, name) {
  try {
    const res = await fetch("/send_to_krita", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ b64, width, height, name: name || "" }),
    });
    if (!res.ok) return { success: false, message: "HTTP " + res.status };
    return await res.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}
"""