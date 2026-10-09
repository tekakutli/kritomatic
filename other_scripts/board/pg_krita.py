"""
pg_krita.py — JS glue for the /krita endpoints.

Two outbound HTTP calls:

    refreshFromKrita()      GET  /krita/documents
    activateDocument(doc)   POST /krita/activate

The merge policy on a refresh is: matching documents (by name) keep
their current board position and assigned hue; the thumbnail and
metadata are updated in place.  Documents no longer open in Krita are
removed from the board.  Documents newly present get a fresh slot from
findFreeSlot() and a freshly-assigned hue.
"""

KRITA_JS = r"""
/* ==========================================================================
   REFRESH
   ========================================================================== */

let _refreshInFlight = false;

async function refreshFromKrita() {
  if (_refreshInFlight) return;
  _refreshInFlight = true;

  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = "pulling thumbnails from Krita…";
    statusEl.className   = "";
  }

  let payload;
  try {
    const res = await fetch("/krita/documents");
    payload = await res.json();
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = "Request failed: " + err.message;
      statusEl.className   = "bad";
    }
    _refreshInFlight = false;
    return;
  }

  _refreshInFlight = false;

  if (!payload || !payload.success) {
    if (statusEl) {
      statusEl.textContent =
        "Krita: " + (payload && payload.message ? payload.message : "unknown error");
      statusEl.className = "bad";
    }
    return;
  }

  mergeIncomingDocs(payload.documents || [], payload.active || null);

  if (payload.partial && payload.message && statusEl) {
    statusEl.textContent = payload.message;
    statusEl.className   = "warn";
    setTimeout(updateStatus, 1800);
  } else if (statusEl) {
    statusEl.textContent =
      "refreshed · " + board.docs.length + " doc" +
      (board.docs.length === 1 ? "" : "s");
    statusEl.className = "ok";
    setTimeout(updateStatus, 1200);
  }

  syncDocList();
  draw();
}

/* ==========================================================================
   MERGE POLICY
   ==========================================================================
   Incoming docs are matched to existing ones by name.  A match keeps
   the existing position and color; a mismatch is removed.  Everything
   in the incoming list ends up on the board after this call.

   The `active` field on each doc is set from the payload's own
   `active` marker for the currently-focused document in Krita, and
   from each doc's own `active` field otherwise (the daemon reports
   it, so it survives round-trips without the caller having to
   recompute it). */

function mergeIncomingDocs(incoming, activeName) {
  const byName = new Map();
  for (const d of board.docs) byName.set(d.name, d);

  const merged = [];
  for (const inc of incoming) {
    const name = inc.name || "(untitled)";
    const existing = byName.get(name);

    if (existing) {
      existing.thumb     = inc.data_url || existing.thumb;
      existing.modified  = !!inc.modified;
      existing.active    = (activeName === name) || !!inc.active;
      if (inc.width && inc.height) {
        existing.aspect = inc.height / inc.width;
        existing.h      = Math.round(existing.w * existing.aspect);
      }
      merged.push(existing);
      byName.delete(name);
    } else {
      const aspect = (inc.height && inc.width)
        ? (inc.height / inc.width)
        : 1.0;
      const doc = {
        id:        name,
        name:      name,
        thumb:     inc.data_url || null,
        aspect:    aspect,
        w:         board.defaultWidth,
        h:         Math.round(board.defaultWidth * aspect),
        x:         0,
        y:         0,
        modified:  !!inc.modified,
        active:    (activeName === name) || !!inc.active,
      };
      _assignColor(doc);
      const slot = findFreeSlot();
      doc.x = slot.x;
      doc.y = slot.y;
      merged.push(doc);
    }
  }

  board.docs = merged;
  if (board.selectedIdx >= board.docs.length) board.selectedIdx = -1;
}

/* ==========================================================================
   ACTIVATE
   ========================================================================== */

async function activateDocument(doc) {
  if (!doc) return;
  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = "activating " + doc.name + "…";
    statusEl.className   = "";
  }
  try {
    const res = await fetch("/krita/activate", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ name: doc.name }),
    });
    const payload = await res.json();
    if (payload && payload.success) {
      if (statusEl) {
        statusEl.textContent = payload.message || ("activated " + doc.name);
        statusEl.className   = "ok";
        setTimeout(updateStatus, 1400);
      }
    } else {
      if (statusEl) {
        statusEl.textContent =
          "activate failed: " + (payload && payload.message ? payload.message : "unknown");
        statusEl.className = "bad";
      }
    }
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = "activate request failed: " + err.message;
      statusEl.className   = "bad";
    }
  }
}
"""
