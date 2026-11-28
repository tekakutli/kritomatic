"""HTTP bridge to the multimask server.

The four daemon-facing functions are re-exported from pgruntime's
shared `makeKritaClient` factory.  Multimask uses the flat URL
defaults (`/krita_status`, `/active_state`, `/resource_image`) with
one override: `import_masks` is served at `/import_masks`, not
`/krita/import_masks`.

`importKritaMasks` on the server side is
`pgruntime.daemon_routes.daemon_route_handlers()["import_masks"]`
mapped to `/import_masks` — the shared handler's `message` default
differs slightly from multimask's original (`"<N> mask(s)"` vs `""`),
but no caller reads it on the success path.
"""

CLIENT_JS = r"""
import { makeKritaClient } from "./pgruntime/js/daemon_client.js";

const _krita = makeKritaClient({
  import_masks: "/import_masks",
});

export const importKritaMasks  = _krita.importKritaMasks;
export const checkKritaStatus  = _krita.checkKritaStatus;
export const fetchActiveState  = _krita.fetchActiveState;
export const fetchResourceImage = _krita.fetchResourceImage;
"""
