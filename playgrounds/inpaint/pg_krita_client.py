"""HTTP bridge to the inpaint server's Krita routes.

The eight daemon-facing functions are re-exported from pgruntime's
shared `makeKritaClient` factory.  Inpaint uses the `/krita/*` URL
prefix for its daemon routes and `/comfy/active_state` for the
daemon status pull, so four URLs are overridden.  The
`/krita/apply` family (`applyToKrita`, `extractMaskToLayer`,
`exportMaskAsLayer`) uses the factory's default URL — inpaint never
imports these (they are inherited from the segment template and
remain as re-exports for consistency with the six prior ports),
but if a future feature needs them they're already there.

`importKritaMasks`'s `preferSelectionMaskChildren` option — already
present in inpaint's local version — is also on the shared factory's
implementation, so nothing changes on that path.
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
