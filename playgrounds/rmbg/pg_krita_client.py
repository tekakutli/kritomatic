"""HTTP bridge to the rmbg server's Krita routes.

The eight daemon-facing functions are re-exported from
pgruntime's shared `makeKritaClient` factory.  rmbg uses the
`/krita/*` URL prefix and `/comfy/active_state` for the daemon
status pull — identical to inpaint's URL shape so operators
already familiar with one can reason about the other."""

KRITA_CLIENT_JS = r"""
import { makeKritaClient } from "./pgruntime/js/daemon_client.js";

const _krita = makeKritaClient({
  krita_status:   "/krita/status",
  active_state:   "/comfy/active_state",
  resource_image: "/krita/resource_image",
  import_image:   "/krita/import",
});

export const checkKritaStatus   = _krita.checkKritaStatus;
export const importFromKrita    = _krita.importFromKrita;
export const importKritaMasks   = _krita.importKritaMasks;
export const fetchActiveState   = _krita.fetchActiveState;
export const fetchResourceImage = _krita.fetchResourceImage;
"""
