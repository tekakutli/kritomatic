"""HTTP bridge to the controlnets server.

The three daemon-facing functions are re-exported from pgruntime's
shared `makeKritaClient` factory — identical to magi's, layout's,
multimask's, and inpaint's.  `pushControlnets` and `refreshControlnets`
are controlnets-specific: they hit `/krita/push_controlnets` and
`/krita/refresh_controlnets`, endpoints no other playground has, and
they talk to the daemon's scratch-document workflow that controlnets
owns exclusively.
"""

CLIENT_JS = r"""
import { makeKritaClient } from "./pgruntime/daemon_client.js";

const _krita = makeKritaClient();

export const checkKritaStatus   = _krita.checkKritaStatus;
export const fetchActiveState   = _krita.fetchActiveState;
export const fetchResourceImage = _krita.fetchResourceImage;

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
