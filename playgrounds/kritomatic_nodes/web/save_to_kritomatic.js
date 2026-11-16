// kritomatic_nodes/web/save_to_kritomatic.js
//
// Frontend extension: adds “Save To Kritomatic” to the File menu.
// Serializes the current graph in UI format (the same object ComfyUI’s
// Export produces via `app.graph.serialize()`), prompts for a filename,
// and POSTs to `/kritomatic/save_workflow`. The backend route lives in
// `kritomatic_workflows.py` and writes to
// `<kritomatic repo>/data/comfy_panel/workflows/<filename>.json`.
//
// Loaded automatically because __init__.py exports WEB_DIRECTORY = "./web".

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";


// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

app.registerExtension({
  name: "kritomatic.saveToKritomatic",

  commands: [
    {
      id: "kritomatic.saveToKritomatic",
      label: "Save To Kritomatic",
      function: () => saveToKritomatic(),
    },
  ],

  menuCommands: [
    {
      path: ["File"],
      commands: ["kritomatic.saveToKritomatic"],
    },
  ],
});


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function defaultFilename() {
  // 1. Primary: the modern workflow store exposes `filename` on the active tab.
  try {
    const active = app?.extensionManager?.workflow?.activeWorkflow;
    if (active && active.filename) {
      return String(active.filename).replace(/\.json$/i, "") + ".json";
    }
  } catch (_) { /* ignore */ }

  // 2. Fallback: parse the last segment of the active workflow's `path`.
  try {
    const active = app?.extensionManager?.workflow?.activeWorkflow;
    if (active && active.path) {
      const base = String(active.path).split("/").pop();
      if (base) return base.replace(/\.json$/i, "") + ".json";
    }
  } catch (_) { /* ignore */ }

  // 3. Last resort: the generic name.
  return "workflow.json";
}


function notifySuccess(detail) {
  // Prefer the modern toast API; otherwise just log.
  try {
    const toast = app?.extensionManager?.toast;
    if (toast && typeof toast.add === "function") {
      toast.add({
        severity: "success",
        summary: "Save To Kritomatic",
        detail,
        life: 3000,
      });
      return;
    }
  } catch (_) { /* ignore */ }
  console.log("[Kritomatic] " + detail);
}


function notifyError(message) {
  console.error("[Kritomatic] " + message);
  try {
    alert("Save To Kritomatic failed:\n" + message);
  } catch (_) { /* ignore */ }
}


// ---------------------------------------------------------------------------
// Command implementation
// ---------------------------------------------------------------------------

async function saveToKritomatic() {
  // 1. Filename prompt (mirrors Export’s “ask the user for a name”).
  let filename;
  try {
    filename = prompt("Save workflow to kritomatic as:", defaultFilename());
  } catch (e) {
    notifyError("could not open filename prompt: " + e);
    return;
  }
  if (filename === null) return; // user cancelled
  filename = filename.trim();
  if (!filename) {
    notifyError("filename cannot be empty");
    return;
  }

  // 2. Serialize the graph in UI format — same object “Export” produces.
  let workflow;
  try {
    workflow = app.graph.serialize();
  } catch (e) {
    notifyError("could not serialize graph: " + e);
    return;
  }
  if (!workflow || typeof workflow !== "object" || !Array.isArray(workflow.nodes)) {
    notifyError("serialized workflow is not UI format (missing `nodes` array)");
    return;
  }

  // 3. POST to the ComfyUI route registered by kritomatic_workflows.py.
  let resp;
  try {
    resp = await api.fetchApi("/kritomatic/save_workflow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, workflow }),
    });
  } catch (e) {
    notifyError("network error: " + e);
    return;
  }

  let data = null;
  try {
    data = await resp.json();
  } catch (_) { /* non-JSON body */ }

  if (!resp.ok || !data || !data.success) {
    const msg = (data && data.message) || `HTTP ${resp.status}`;
    notifyError(msg);
    return;
  }

  notifySuccess(`saved ${data.filename} (${data.nodes} nodes, ${data.links} links)`);
}
