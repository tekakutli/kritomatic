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
  try {
    return await _post("/intercept/list", {});
  } catch (e) {
    return { success: false, tasks: [], message: e.message };
  }
}

export async function interceptItem(taskId) {
  try {
    return await _post("/intercept/item", { task_id: taskId });
  } catch (e) {
    return { success: false, message: e.message };
  }
}

/* `prompt` is optional.  When undefined the `prompt` key is omitted
   from the POST body entirely, and the interceptor's `handle_run`
   falls back to the prompt that arrived with the payload.  When a
   string is given (including ""), it overrides. */
export async function interceptRun(taskId, prompt) {
  const body = { task_id: taskId };
  if (prompt !== undefined) body.prompt = prompt;
  try {
    return await _post("/intercept/run", body);
  } catch (e) {
    return { success: false, message: e.message };
  }
}

export async function interceptDiscard(taskId) {
  try {
    return await _post("/intercept/discard", { task_id: taskId });
  } catch (e) {
    return { success: false, message: e.message };
  }
}
"""