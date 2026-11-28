"""HTTP bridge to the segment server's Krita routes.

The eight daemon-facing functions are re-exported from pgruntime's
shared `makeKritaClient` factory.  Segment uses the `/krita/*` URL
prefix for its daemon routes and `/comfy/active_state` for the daemon
status pull, so four URLs are overridden.  The `/krita/apply` family
(`applyToKrita`, `extractMaskToLayer`, `exportMaskAsLayer`) uses the
factory's default URLs — segment is their only user and the defaults
match segment's routes.

The shared implementation's optional `preferSelectionMaskChildren`
handling on `importKritaMasks` is additive over segment's previous
version; segment's server-side handler ignores the field, so no
behavior change.
"""

KRITA_CLIENT_JS = r"""
import { makeKritaClient } from "./pgruntime/js/daemon_client.js";

const _krita = makeKritaClient({
  krita_status:   "/krita/status",
  active_state:   "/comfy/active_state",
  resource_image: "/krita/resource_image",
  import_image:   "/krita/import",
});

export const checkKritaStatus   = _krita.checkKritaStatus;
export const applyToKrita       = _krita.applyToKrita;
export const extractMaskToLayer = _krita.extractMaskToLayer;
export const exportMaskAsLayer  = _krita.exportMaskAsLayer;
export const importFromKrita    = _krita.importFromKrita;
export const importKritaMasks   = _krita.importKritaMasks;
export const fetchActiveState   = _krita.fetchActiveState;
export const fetchResourceImage = _krita.fetchResourceImage;
"""
