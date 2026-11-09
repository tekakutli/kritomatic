CLIENT_JS = r"""
async function _post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error("HTTP " + res.status);
  return await res.json();
}

export async function interceptList() {
  try { return await _post("/intercept/list", {}); }
  catch (e) { return { success: false, tasks: [], message: e.message }; }
}

export async function interceptItem(taskId) {
  try { return await _post("/intercept/item", { task_id: taskId }); }
  catch (e) { return { success: false, message: e.message }; }
}

/* Full-body variant, used by the panel so it can send augmented
   canvas/selection/context along with expansion metadata.  The
   server ignores any augmented_* fields that are absent. */
export async function interceptRunBody(body) {
  try { return await _post("/intercept/run", body); }
  catch (e) { return { success: false, message: e.message }; }
}

export async function interceptDiscard(taskId) {
  try { return await _post("/intercept/discard", { task_id: taskId }); }
  catch (e) { return { success: false, message: e.message }; }
}

export async function interceptKritaOpen(canvasB64) {
  try {
    return await _post("/intercept/krita_open",
                       { canvas_b64: canvasB64 });
  } catch (e) {
    return { success: false, message: e.message };
  }
}

export async function interceptKritaRefresh(documentName) {
  try {
    return await _post("/intercept/krita_refresh",
                       { document_name: documentName });
  } catch (e) {
    return { success: false, message: e.message };
  }
}
"""