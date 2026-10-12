"""
pg_panel_core.py — panel primitives.

The two reusable building blocks the other panel files depend on:

    SCRUB_DRAG_THRESHOLD    the number of pixels of horizontal drag
                            before a scrub input counts as "moved".
                            Shared by every scrub binding.

    flashStatus             the transient one-line message at the
                            bottom of the panel.  Every other panel
                            file and every other JS file calls it.

    _installScrubInput      the generic horizontal-drag installer
                            every scrub field uses.  The installer
                            owns the drag loop, the snap step, and
                            the Enter / Escape / blur handling; each
                            field supplies rate, snapStep, read()
                            and write().

Nothing in this file references a specific field's semantics; if
you are adding a new field, the edit lives in
pg_panel_scrubs.py (and, if the field needs its own model
setter, pg_panel_fields.py), not here.
"""

PANEL_CORE_JS = r"""
/* ==========================================================================
   STATUS FLASH
   ========================================================================== */

let _statusTimer = null;
function flashStatus(msg, cls) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = msg;
  el.className = cls || "";
  if (_statusTimer) clearTimeout(_statusTimer);
  _statusTimer = setTimeout(() => {
    _statusTimer = null;
    el.textContent = "";
    el.className = "";
  }, 1800);
}

/* ==========================================================================
   SCRUB-INPUT INSTALLER
   ==========================================================================
   A scrub input is a text field the user drags horizontally to
   change.  The installer owns the drag loop and the read / write
   plumbing; each binding supplies four things:

       rate             units per pixel of horizontal drag
       snapStep         optional; Shift-held drags step by this
       read()           current value, in the binding's own units
       write(raw, drag) apply a value; `drag` is the drag-state
                        object (see below), or undefined when the
                        call came from Enter / blur rather than a
                        drag

   Two optional hooks extend the loop for bindings that need to
   coordinate more than one model field:

       onDragStart(drag)      called once at mousedown, after `drag`
                              has been created, so a binding can
                              snapshot whatever else it needs to
                              hold fixed for the duration of the
                              drag.

       symmetricOnShift       when true, a Shift-held drag routes to
       + writeSymmetric       writeSymmetric(raw, drag) instead of
                              write.  Any snapStep the binding
                              declared is skipped in this branch, so
                              a symmetric drag is continuous even if
                              the same field snaps in its other
                              modes.

   The `drag` object passed to the callbacks carries:

       startX       screen x at mousedown
       startValue   the field's value at mousedown (from read())
       moved        true once the drag has passed the threshold
       ... plus anything the binding's onDragStart added

   The installer never reads the model directly — every binding's
   `read` / `write` is the only place that knows what the field
   means. */

const SCRUB_DRAG_THRESHOLD = 3;

function _installScrubInput(el, opts) {
  let drag = null;

  function onMove(e) {
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    if (!drag.moved) {
      if (Math.abs(dx) < SCRUB_DRAG_THRESHOLD) return;
      drag.moved = true;
    }

    const symmetric = opts.symmetricOnShift && opts.writeSymmetric &&
                      e.shiftKey;

    let raw = drag.startValue + dx * opts.rate;
    if (!symmetric && opts.snapStep && e.shiftKey) {
      const delta = raw - drag.startValue;
      const snapped = Math.round(delta / opts.snapStep) * opts.snapStep;
      raw = drag.startValue + snapped;
    }

    if (symmetric) opts.writeSymmetric(raw, drag);
    else           opts.write(raw, drag);
    draw();
  }
  function onUp() {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    const wasMoved = drag && drag.moved;
    drag = null;
    if (!wasMoved) {
      el.focus();
      el.select();
    }
  }

  el.addEventListener("mousedown", (e) => {
    if (el.disabled) return;
    if (document.activeElement === el) return;
    e.preventDefault();
    drag = {
      startX: e.clientX,
      startValue: opts.read(),
      moved: false,
    };
    if (opts.onDragStart) opts.onDragStart(drag);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });

  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const v = parseFloat(el.value);
      if (isFinite(v)) opts.write(v);
      el.blur();
      draw();
    } else if (e.key === "Escape") {
      e.preventDefault();
      el.blur();
      _syncPairFieldInputs();
    }
  });

  el.addEventListener("blur", () => {
    const v = parseFloat(el.value);
    if (isFinite(v)) opts.write(v);
    _syncPairFieldInputs();
    draw();
  });
}
"""
