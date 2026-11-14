"""HTTP bridge to the interceptor server.

Every action is a fetch to a Python route; the server talks to the
daemon over the kritomatic TCP socket.  The Krita round-trip
functions (`interceptKritaOpen`, `interceptKritaRefresh`) and the old
`interceptRunBody` shape are gone — the new resume flow is a single
`interceptRun(taskId, prompt)` that mirrors the daemon's
`comfy_resume_intercepted`.
"""

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

/* Resume generation for one intercepted task.

   `prompt` is the user's override, or `null` to keep the workflow's
   original.  The handler blocks for the full generation duration
   (typically 20-60s) — the browser's fetch is a long-poll against
   the pgkit server's socket, matching the original interceptor's
   blocking-Run UX. */
export async function interceptRun(taskId, prompt) {
  try {
    return await _post("/intercept/run", {
      task_id: taskId,
      prompt: prompt === undefined ? null : prompt,
    });
  } catch (e) {
    return { success: false, message: e.message };
  }
}

export async function interceptDiscard(taskId) {
  try { return await _post("/intercept/discard", { task_id: taskId }); }
  catch (e) { return { success: false, message: e.message }; }
}
"""