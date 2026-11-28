STATE_SYNC_JS = r"""
/* ==========================================================================
   makeStateSync — debounced, deduped state push with success-only commit.

   The discipline that's easy to get wrong: a failed POST must NOT
   advance the "last sent" key, or the client and server silently
   diverge.  Every existing sync implementation already got this
   right; the factory centralises it so a new playground doesn't
   have to rediscover the trap.

   Opts:
     buildPayload()        — return the POST body
     keyFn()               — optional; return a dedupe key.  If
                             omitted, the key is the JSON-stringified
                             payload.
     url                   — POST endpoint (default "/state")
     debounceMs            — debounce window (default 250)
     onSuccess(data)       — called after a successful POST
     onError(message)      — called on first failure of a streak
     gate                  — optional Promise to await before first sync

   Returns:
     schedule()            — debounce, then POST if key changed
     now()                 — bypass debounce, POST if key changed
     reset()               — force next sync regardless of key
     setBootGate(promise)  — install a boot gate
   ========================================================================== */

export function makeStateSync(opts) {
  const {
    buildPayload,
    keyFn = null,
    url = "/state",
    debounceMs = 250,
    onSuccess = null,
    onError = null,
  } = opts;

  let timer = null;
  let lastKey = null;
  let warned = false;
  let inFlight = false;
  let bootGate = null;

  function setBootGate(promise) {
    if (!promise || typeof promise.then !== "function") return;
    const gated = promise.finally(() => {
      if (bootGate === gated) bootGate = null;
    });
    gated.catch(() => {});
    bootGate = gated;
  }

  if (opts.gate) setBootGate(opts.gate);

  async function _doSync() {
    timer = null;

    if (bootGate) {
      try {
        await Promise.race([bootGate, new Promise(r => setTimeout(r, 3000))]);
      } catch (_) {}
    }

    if (inFlight) {
      // Re-fire after the current POST settles; the key we'd compute
      // now might change again before then, so this is the cheapest
      // way to avoid dropping an intervening change.
      timer = setTimeout(_doSync, debounceMs);
      return;
    }

    const payload = buildPayload();
    const key = keyFn ? keyFn() : JSON.stringify(payload);

    if (key === lastKey) return;

    inFlight = true;
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        lastKey = key;
        warned = false;
        if (onSuccess) onSuccess(data);
      } else {
        if (!warned) {
          warned = true;
          if (onError) onError(data.message || "sync failed");
        }
      }
    } catch (e) {
      if (!warned) {
        warned = true;
        if (onError) onError(e.message);
      }
    } finally {
      inFlight = false;
    }
  }

  function schedule() {
    if (timer) return;   // trailing-edge: first call wins
    timer = setTimeout(_doSync, debounceMs);
  }

  function now() {
    if (timer) { clearTimeout(timer); timer = null; }
    _doSync();
  }

  function reset() {
    lastKey = null;
    warned = false;
  }

  return { schedule, now, reset, setBootGate };
}
"""
