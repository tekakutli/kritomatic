"""
pgkit.panel_core — the panel's JavaScript primitives.

Two things every project's panel needs:

    flashStatus           the transient one-line message at the
                          bottom of the panel.

    _installScrubInput    the generic horizontal-drag installer
                          every scrub field uses.  The installer
                          owns the drag loop, the snap step, and
                          the Enter / Escape / blur handling.

The installer calls `draw()` after every change, matching what the
two existing playgrounds already do.  The project's `draw()` is
responsible for syncing any DOM elements the change affects —
through its own sync functions — so the kit does not need to know
about the project's model.

`_installScrubInput` takes an options object with `rate`,
`snapStep`, `read`, and `write`, exactly as the pair and cone
playgrounds' installers do today.  Two optional hooks exist for
bindings that need to coordinate more than one field:

    onDragStart(drag)      called once at mousedown, after `drag`
                           has been created.

    symmetricOnShift       when true, a Shift-held drag routes to
    + writeSymmetric       writeSymmetric(raw, drag) instead of
                           write.  Any snapStep the binding
                           declared is skipped in this branch.

Projects that want a field to also be published over the remote
API should call `pgkitNotifyLocal(name)` inside their `write`
callback — that is how the kit learns a change originated locally
and should be broadcast.  The schema's renderer wires this
automatically for schema-declared fields; hand-written scrub
fields must do it by hand.
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
   plumbing; each binding supplies:

       rate             units per pixel of horizontal drag
       snapStep         optional; Shift-held drags step by this
       read()           current value, in the binding's own units
       write(raw, drag) apply a value; `drag` is the drag-state
                        object, or undefined when the call came
                        from Enter / blur rather than a drag

   Two optional hooks:

       onDragStart(drag)      called once at mousedown, after `drag`
                              has been created.
       symmetricOnShift +     a Shift-held drag routes to
       writeSymmetric         writeSymmetric(raw, drag) instead of
                              write.

   The `drag` object carries `startX`, `startValue`, `moved`, plus
   anything `onDragStart` added. */

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
      draw();
    }
  });

  el.addEventListener("blur", () => {
    const v = parseFloat(el.value);
    if (isFinite(v)) opts.write(v);
    draw();
  });
}
"""
