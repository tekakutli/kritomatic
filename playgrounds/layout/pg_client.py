"""HTTP bridge to the layout server.

The three daemon-facing functions are re-exported from pgruntime's
shared `makeKritaClient` factory — they're identical to magi's,
controlnets's, multimask's, and inpaint's, with no layout-specific
behaviour.  Layout's other client-side concerns live elsewhere:
`/generate` and `/generate_status` are called directly from
`pg_generate.js`, and `/state` / `/version` are called directly from
`pg_panel.js`.  Nothing layout-specific remains here.
"""

CLIENT_JS = r"""
import { makeKritaClient } from "./pgruntime/js/daemon_client.js";

const _krita = makeKritaClient();

export const checkKritaStatus   = _krita.checkKritaStatus;
export const fetchActiveState   = _krita.fetchActiveState;
export const fetchResourceImage = _krita.fetchResourceImage;
"""
