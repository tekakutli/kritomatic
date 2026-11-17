"""HTTP bridge to the controlnets server."""

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

export async function pushControlnets(payload) {
  try {
    const res = await fetch("/krita/push_controlnets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) return { success: false, message: "HTTP " + res.status };
    return await res.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}

export async function refreshControlnets(payload) {
  try {
    const res = await fetch("/krita/refresh_controlnets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) return { success: false, message: "HTTP " + res.status };
    return await res.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}
"""