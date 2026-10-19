"""
pgkit.panel_core — flashStatus and _installScrubInput, as an ES module.

Both are exported.  `_installScrubInput` calls a `draw` callback on
every value change; the callback is injected at boot via
`setPanelDraw(draw)`.  The previous inline `draw()` reference relied
on `draw` being a global, which is not true inside a module.

`flashStatus` needs no injection — it touches `#status`, which is
part of the shell.
"""

PANEL_CORE_JS = r"""
/* ==========================================================================
   MODULE STATE
   ========================================================================== */

let _drawFn = null;

export function setPanelDraw(fn) {
  _drawFn = fn;
}

/* ==========================================================================
   STATUS FLASH
   ========================================================================== */

let _statusTimer = null;

export function flashStatus(msg, cls) {
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
                              write. */

const SCRUB_DRAG_THRESHOLD = 3;

export function _installScrubInput(el, opts) {
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
    if (_drawFn) _drawFn();
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
      if (_drawFn) _drawFn();
    } else if (e.key === "Escape") {
      e.preventDefault();
      el.blur();
      if (_drawFn) _drawFn();
    }
  });

  el.addEventListener("blur", () => {
    const v = parseFloat(el.value);
    if (isFinite(v)) opts.write(v);
    if (_drawFn) _drawFn();
  });
}
"""
