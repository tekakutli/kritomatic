"""HTTP bridge to the interceptor server.

Every action is a fetch to a Python route; the server talks to the
daemon over the kritomatic TCP socket and (when the workflow uses
multimask) to the multimask playground over HTTP.

`interceptRun` now carries the per-area prompt overrides alongside
the inpaint prompt override.  The server pushes them to multimask
before calling `comfy_resume_intercepted` on the daemon.  See
`interceptor_server.handle_run`'s module docstring for the ordering
and failure semantics.
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

   `prompt` is the inpaint prompt override (or `null` to keep the
   workflow's original).
   `areaPrompts` is `{area_id: prompt}` for the multimask areas (empty
   object when the workflow has no `MultiAreaConditioning` node or
   when the user made no edits — the server still pushes the snapshot
   to ensure the reviewed state runs).

   The handler blocks for the full generation duration (typically
   20-60s) — the browser's fetch is a long-poll against the pgkit
   server's socket, matching the original interceptor's blocking-Run
   UX. */
export async function interceptRun(taskId, prompt, areaPrompts) {
  try {
    return await _post("/intercept/run", {
      task_id: taskId,
      prompt: prompt === undefined ? null : prompt,
      area_prompts: areaPrompts || {},
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