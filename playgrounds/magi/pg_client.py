"""HTTP bridge to the magi server.

The daemon-facing three functions are re-exported from pgruntime's
shared `makeKritaClient` factory — they're identical to layout's,
controlnets's, multimask's, and inpaint's, with no magi-specific
behaviour.  Only `analyzeCharacters` is local: it talks to magi's
own `/analyze_characters` route, which forwards to ComfyUI's
`/magi/crop_embed`.
"""

CLIENT_JS = r"""
import { makeKritaClient } from "./pgruntime/js/daemon_client.js";

const _krita = makeKritaClient();

export const checkKritaStatus   = _krita.checkKritaStatus;
export const fetchActiveState   = _krita.fetchActiveState;
export const fetchResourceImage = _krita.fetchResourceImage;

export async function analyzeCharacters(payload) {
  try {
    const res = await fetch("/analyze_characters", {
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
