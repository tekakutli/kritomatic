"""Browser-side HTTP bridge to the shared daemon proxy routes.

The client half of `pgruntime/daemon_routes.py`.  Exported as a
`makeKritaClient(urls)` factory because URL prefixes differ across
playgrounds:

  · controlnets, layout, magi, multimask:
        /active_state, /krita_status, /resource_image, /import_image
        (makeKritaClient() with no args uses these defaults)

  · inpaint:
        /comfy/active_state, /krita/status, /krita/resource_image,
        /krita/import

  · segment:
        /krita/status, /krita/import, /krita/import_masks
        (uses the flat URLs for the other three)

The factory closes over the URLs and returns functions that take
normal option objects.  Each playground imports `makeKritaClient`,
calls it once with its own URLs, and destructures the returned
functions into its own `pg_krita_client.js` (or `pg_client.js`).

USAGE
=====
    // pg_krita_client.js
    import { makeKritaClient }
      from "./pgruntime/daemon_client.js";

    const _krita = makeKritaClient({
      krita_status:   "/krita/status",
      active_state:   "/comfy/active_state",
      resource_image: "/krita/resource_image",
      import_image:   "/krita/import",
      import_masks:   "/krita/import_masks",
    });

    export const checkKritaStatus = _krita.checkKritaStatus;
    export const fetchActiveState = _krita.fetchActiveState;
    export const fetchResourceImage = _krita.fetchResourceImage;
    export const importFromKrita = _krita.importFromKrita;
    export const importKritaMasks = _krita.importKritaMasks;
    export const applyToKrita = _krita.applyToKrita;
    export const extractMaskToLayer = _krita.extractMaskToLayer;
    export const exportMaskAsLayer = _krita.exportMaskAsLayer;

Union of six per-playground copies this replaces.  `applyToKrita` /
`extractMaskToLayer` / `exportMaskAsLayer` are segment-only in current
usage but were already duplicated in inpaint as dead code; keeping
them here removes the dead code from inpaint without changing any
live behavior.  Playgrounds that don't use them don't import them.

DEDUPLICATION
=============
`checkKritaStatus` is the odd one out because its failure path
returns `reachable: false` in addition to `success: false, message`.
The server's success path already returns `reachable` so the client
just passes through; only the network-failure path has to add the
field.  The shared body below does that with an inline try/catch
rather than going through the shared `_post` helper.

UPLOAD
======
`uploadImageFile` is not exported.  It's shared across five
playgrounds but the URL prefix varies (`/upload_image` vs
`/comfy/upload_image`) and it's a different category — it's a
playground-local image cache, not a daemon-proxy route.  Kept out of
this module for now; if a later pass decides to move it up, it
belongs in a `pgruntime/js_upload.py` module, not here.
"""


DAEMON_CLIENT_JS = r"""
/* ==========================================================================
   pgruntime/js/daemon_client.js — the browser-side HTTP bridge to
   the shared kritomatic-daemon proxy routes.

   See `pgruntime/js_daemon_client.py`'s module docstring (the Python
   side of this module) for the URL-prefix differences and the
   migration notes.

   Everything here is thin: build a body, POST to a URL, decode the
   JSON response.  No state, no retries, no caching.  Timeouts and
   retry policy are the caller's problem — they know whether a
   failure is transient (ComfyUI restarting) or terminal (daemon not
   running).
   ========================================================================== */

export function makeKritaClient(opts) {
  opts = opts || {};

  const urls = {
    activeState:   opts.active_state   || "/active_state",
    kritaStatus:   opts.krita_status   || "/krita_status",
    resourceImage: opts.resource_image || "/resource_image",
    importImage:   opts.import_image   || "/import_image",
    importMasks:   opts.import_masks   || "/krita/import_masks",
    apply:         opts.apply          || "/krita/apply",
  };

  /* Shared POST helper.  Returns the JSON body on 2xx, a
     `{success: false, message}` envelope on transport or HTTP
     errors.  Never throws — callers routinely chain `.then` /
     `await` on these results and a throw would surface as an
     unhandled rejection somewhere in the panel. */
  async function _post(url, body) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      if (!res.ok) {
        return {
          success: false,
          message: "HTTP " + res.status,
        };
      }
      return await res.json();
    } catch (e) {
      return { success: false, message: e.message };
    }
  }

  /* ---- daemon reachability ----------------------------------------
     Distinct failure shape from the other calls: on network failure
     the response includes `reachable: false` because the callers
     test that field, not `success`.  The server's success path
     already returns `{success: true, reachable: bool}` so we just
     pass it through. */
  async function checkKritaStatus() {
    try {
      const res = await fetch(urls.kritaStatus, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      return await res.json();
    } catch (e) {
      return {
        success: false,
        reachable: false,
        message: e.message,
      };
    }
  }

  /* ---- daemon state pull ------------------------------------------
     Returns `{success, daemon_reachable, data}` where `data` is the
     daemon's `comfy_status` payload.  `daemon_reachable === false`
     means the daemon itself is down; `success === false` with
     `daemon_reachable === true` is unusual (means the daemon
     answered but rejected the command). */
  async function fetchActiveState() {
    return _post(urls.activeState);
  }

  /* ---- resource image --------------------------------------------
     `spec` is `{source, layer_name}`.  `source="active"` returns
     the active document's composite; `source="layer"` returns that
     specific layer's pixels.  Response: `{success, hash, width,
     height, data_url, filename}`. */
  async function fetchResourceImage(spec) {
    return _post(urls.resourceImage, spec);
  }

  /* ---- import image ----------------------------------------------
     `opts.name` selects a specific document by name; blank uses
     the active document.  Response shape identical to
     `fetchResourceImage`. */
  async function importFromKrita(opts) {
    const body = {};
    if (opts && opts.name) body.name = opts.name;
    return _post(urls.importImage, body);
  }

  /* ---- import masks ----------------------------------------------
     `opts.layerNames`           explicit layer selection
     `opts.includePaintAlpha`    paint-layer alpha policy
     `opts.preferSelectionMaskChildren`
                                 selection-mask preference

     `layerNames` accepts either an array or a comma-joined string;
     the array form is normalized to the string form the daemon
     expects. */
  async function importKritaMasks(opts) {
    const body = {};
    if (opts) {
      if (opts.layerNames) {
        body.layer_names = Array.isArray(opts.layerNames)
          ? opts.layerNames.join(",")
          : String(opts.layerNames);
      }
      if (typeof opts.includePaintAlpha === "boolean") {
        body.include_paint_alpha = opts.includePaintAlpha;
      }
      if (typeof opts.preferSelectionMaskChildren === "boolean") {
        body.prefer_selection_mask_children =
          opts.preferSelectionMaskChildren;
      }
    }
    return _post(urls.importMasks, body);
  }

  /* ---- /krita/apply family ---------------------------------------
     Segment-only in current usage, but defined in segment's and
     inpaint's `pg_krita_client.py`.  Both build a POST body from a
     camelCase options object and POST to `/krita/apply`; the
     endpoint dispatches on `mode`.

     Options key translation:
       maskB64       → mask_b64       (required)
       mode          → mode           (required)
       layerName     → layer_name
       newLayerName  → new_layer_name
       blendMode     → blend_mode
       opacity       → opacity        (0-255 byte)
       groupName     → group_name
       selMode       → sel_mode
       name          → name
       aboveLayer    → above_layer
       invert        → invert
       expandPixels  → expand_pixels
       contractPixels→ contract_pixels
       smoothIterations → smooth_iterations
       featherRadius → feather_radius

     Optional fields are omitted when falsy so the daemon can apply
     its own defaults.  `opacity` is the one exception: it may be
     `0` and `0` is meaningful, so the check is strict-undefined
     rather than falsy. */

  async function applyToKrita(opts) {
    const body = {
      mask_b64: opts.maskB64,
      mode:     opts.mode,
    };
    if (opts.layerName)        body.layer_name = opts.layerName;
    if (opts.invert)           body.invert = true;
    if (opts.selMode)          body.sel_mode = opts.selMode;
    if (opts.expandPixels)     body.expand_pixels = opts.expandPixels;
    if (opts.contractPixels)   body.contract_pixels = opts.contractPixels;
    if (opts.smoothIterations) body.smooth_iterations = opts.smoothIterations;
    if (opts.featherRadius)    body.feather_radius = opts.featherRadius;
    if (opts.name)             body.name = opts.name;
    if (opts.aboveLayer)       body.above_layer = opts.aboveLayer;
    return _post(urls.apply, body);
  }

  async function extractMaskToLayer(opts) {
    const body = {
      mask_b64: opts.maskB64,
      mode:     opts.mode,
    };
    if (opts.layerName)        body.layer_name = opts.layerName;
    if (opts.newLayerName)     body.new_layer_name = opts.newLayerName;
    if (opts.blendMode)        body.blend_mode = opts.blendMode;
    if (opts.opacity !== undefined &&
        opts.opacity !== null &&
        opts.opacity !== "")   body.opacity = opts.opacity;
    if (opts.groupName)        body.group_name = opts.groupName;
    if (opts.invert)           body.invert = true;
    if (opts.expandPixels)     body.expand_pixels = opts.expandPixels;
    if (opts.contractPixels)   body.contract_pixels = opts.contractPixels;
    if (opts.smoothIterations) body.smooth_iterations = opts.smoothIterations;
    if (opts.featherRadius)    body.feather_radius = opts.featherRadius;
    return _post(urls.apply, body);
  }

  function exportMaskAsLayer(opts) {
    return applyToKrita({
      maskB64:          opts.maskB64,
      mode:             "export_layer",
      name:             opts.name,
      aboveLayer:       opts.aboveLayer,
      invert:           opts.invert,
      expandPixels:     opts.expandPixels,
      contractPixels:   opts.contractPixels,
      smoothIterations: opts.smoothIterations,
      featherRadius:    opts.featherRadius,
    });
  }

  return {
    checkKritaStatus,
    fetchActiveState,
    fetchResourceImage,
    importFromKrita,
    importKritaMasks,
    applyToKrita,
    extractMaskToLayer,
    exportMaskAsLayer,
  };
}
"""