"""
pgkit.panel_core — flashStatus, log mirroring, and _installScrubInput,
as an ES module.

`flashStatus` and `_installScrubInput` are exported.  `_installScrubInput`
calls a `draw` callback on every value change; the callback is injected
at boot via `setPanelDraw(draw)`.  The previous inline `draw()` reference
relied on `draw` being a global, which is not true inside a module.

LOG MIRRORING
=============
Every playground is a terminal process.  When a browser-side error or
warning happens in a playground tab, the developer running the server
needs to see it without alt-tabbing to DevTools.  This module is the
single point where that forwarding happens.

Four kinds of browser-side event reach the terminal through here:

  * Panel errors — `flashStatus(msg, "bad")` writes the DOM status bar
    (unchanged behaviour) and also mirrors `msg` to the server.

  * Panel warnings — `flashStatus(msg, "warn")` mirrors too.  "warn"
    is used in this codebase for actionable diagnostics (mask dimension
    mismatch, mirror-disabled-during-expansion, empty context bbox)
    that a headless operator needs to see.

  * `console.error(...)` / `console.warn(...)` — the module overrides
    these at load, forwarding to the server while still calling the
    original so DevTools sees the message exactly as before.  This is
    what makes "boot catch blocks call console.error" reach the
    terminal, and what makes any direct `console.error` in playground
    code visible in the server's stdout.

  * Uncaught errors and unhandled rejections — installed via `window.
    addEventListener` hooks at load.

BOOT LOG MIRRORING — THE UNIFIED BOOT STANDARD
==============================================
Boot modules come in two shapes across the playgrounds:

  * Dynamic-import boots (`board`, `cone`, `boxes`): an async IIFE calls
    `await import(path)` in a loop, logging each step via
    `console.log("[boot]", ...)` and each failure via
    `console.error("[boot]", "FAIL", ...)`.

  * Static-import boots (`inpaint`, `multimask`, `interceptor`,
    `segment`, `text_boxes`, `text_wall`): imports are static, the
    body wraps its setup in a try/catch, and it logs success via
    `console.log("[boot] message")` and failure via
    `console.error("[boot] FAILED:", e)`.

Both shapes agree on one thing: **the literal token `[boot]` at the
start of the first console argument identifies a boot log line.** This
module exploits that to give both shapes identical mirroring behaviour
without any per-playground change:

  * Any `console.log` call whose first argument is exactly `"[boot]"`,
    or is a string starting with `"[boot] "`, is mirrored with class
    `"boot-ok"`.  This covers both forms — `console.log("[boot]", ...)`
    and `console.log("[boot] message")`.  The `[boot] ` prefix is
    stripped from the mirrored text because the class already says
    "boot-ok"; the terminal output is `[inpaint] boot-ok: starting`,
    not `[inpaint] boot-ok: [boot] starting`.

  * Any `console.error` call whose first argument is exactly `"[boot]"`,
    or is a string starting with `"[boot] "`, is mirrored with class
    `"boot-error"`, prefix stripped, same rationale.

  * Every other `console.log` / `console.error` call stays
    un-boot-classed: `console.log` is not mirrored at all (too noisy),
    `console.error` is mirrored as class `"console-error"` with its
    arguments passed through verbatim.

Why mirror boot *success* at all: a headless developer (Playwright,
Selenium, a `run_all.py` session with no browser tab) has no other
signal that boot completed.  The `[boot] BOOT COMPLETE` line, mirrored,
gives them the same per-step confirmation a browser developer reads in
DevTools.  Because the mirror is opt-in by tag, no per-playground
boot module needs to change — the mechanism is uniform across both
shapes.

Why separate `boot-ok` and `boot-error` classes rather than reusing
`console-error` for boot failures: a developer watching the terminal
gets a clean signal — anything tagged `boot-*` is a boot event, split
by success or failure.  Before this class existed, boot failures
appeared as `[prefix] console-error: [boot] FAIL import state → ...`
which was readable but forced the reader to parse the payload to know
it was a boot event.  Now success and failure are symmetric: `[prefix]
boot-ok: import state ok` / `[prefix] boot-error: FAIL import state
→ ...`.

The mirror target is `POST ./__log`, a built-in route installed
unconditionally by `pgkit/server.py::make_handler` on every playground.
The URL is *relative* to the page, not absolute — under the pgkit hub
the page lives at `/<name>/<name>.html`, so `./__log` resolves to
`/<name>/__log`, which the hub proxies back to that playground.  An
absolute `/__log` would hit the hub root where nothing handles it.

`document.title` supplies the prefix: `"Inpaint playground"` → `inpaint`,
`"Multimask"` → `multimask`, `"Remote panel"` → `remote`.  Matches each
server's own `_log("[...]")` convention, so a terminal running several
playgrounds under the hub stays readable.

The `console.error` / `console.warn` / `console.log` overrides are
*transparent*: the message still reaches DevTools in its original
form.  Only after the original call returns does the override POST the
message to the server.  This means an operator with a browser tab open
loses nothing, and an operator running headless gains the terminal
trace.

`console.log` is otherwise deliberately not mirrored.  It is used
pervasively for benign diagnostics (fetch timing, cache hits, dev
tracing); forwarding every `console.log` would flood the server's
stdout.  The `[boot]` tag is the only whitelisted case.

Opting out
==========
Three URL query parameters disable mirroring at three scopes.  The
broadest one wins — `pgkit_no_mirror=1` disables everything, including
the narrower two.

    ?pgkit_no_mirror=1         disable all mirroring: errors, warnings,
                               boot messages, console.error/warn, and
                               uncaught exceptions.

    ?pgkit_no_mirror_warn=1    silence warning-class mirroring only:
                               `flashStatus(..., "warn")` and
                               `console.warn(...)`.  Error mirroring,
                               boot mirroring, and uncaught-exception
                               mirroring stay on.

    ?pgkit_no_mirror_boot=1    silence boot-class mirroring only (the
                               `[boot]`-tagged `console.log` and
                               `console.error` paths).  Non-boot
                               console.error still mirrors; uncaught
                               exceptions still mirror via
                               `window.onerror`.

These use URL parameters rather than environment variables because
browser-side code cannot read the server's environment.  The knob is
per-page-load; closing the tab resets it.  Useful when debugging the
mirror itself, or when comparing page behaviour with the mirror on
versus off.

`reportPanelError(message, cls)` is the public explicit-report entry
point.  Used by `pgkit/remote_client.js` for remote-command failures,
and available to any playground that wants to mirror a message that
does not fit the status-bar lifecycle (e.g. a boot-time failure caught
before `flashStatus` is wired).

Recursion
=========
`_postLog` catches all errors internally and never throws, and its
fetch path never calls any overridden console method.  Each override's
mirror call is wrapped in try/catch.  Even if the mirror itself fails,
no further console method fires and no infinite loop is possible.

Multi-line messages
===================
A message that spans multiple lines — typically a stack trace — is
mirrored as one POST.  The server's `_handle_log` prints the first
line with a `[prefix] class:` tag and indents the rest by four spaces.
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
   LOG MIRRORING — panel errors, console.error/warn, boot logs,
   uncaught errors
   ==========================================================================
   See the module docstring for the full design.  Entry points:

     _postLog(message, cls)     the raw POST, fire-and-forget
     reportPanelError(msg, cls) exported alias for explicit reporting

   Transparent console overrides: original method still called first,
   DevTools still sees the message, then the mirror posts it.

   Three independent disable switches, read from URL query params at
   module load:

     _MIRROR_DISABLED       ?pgkit_no_mirror=1        — all off
     _MIRROR_WARN_DISABLED  ?pgkit_no_mirror_warn=1   — warn off only
     _MIRROR_BOOT_DISABLED  ?pgkit_no_mirror_boot=1   — boot off only

   Each of the narrower switches also respects _MIRROR_DISABLED: if
   the master kill switch is set, all three are true. */

function _readParam(name) {
  try {
    const u = new URL(window.location.href);
    return u.searchParams.get(name) === "1";
  } catch (_) { return false; }
}

const _MIRROR_DISABLED       = _readParam("pgkit_no_mirror");
const _MIRROR_WARN_DISABLED  = _MIRROR_DISABLED || _readParam("pgkit_no_mirror_warn");
const _MIRROR_BOOT_DISABLED  = _MIRROR_DISABLED || _readParam("pgkit_no_mirror_boot");

function _panelLogPrefix() {
  const t = (typeof document !== "undefined" && document.title) || "";
  const first = String(t).trim().toLowerCase().split(/\s+/)[0] || "";
  return first || "panel";
}

function _postLog(message, cls) {
  if (_MIRROR_DISABLED) return;
  const text = String(message == null ? "" : message);
  if (!text) return;
  try {
    fetch("./__log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        class:   String(cls || ""),
        prefix:  _panelLogPrefix(),
      }),
    }).catch(() => {});
  } catch (_) {}
}

export function reportPanelError(message, cls) {
  _postLog(message, cls);
}

/* ---- console overrides ---- */
const _origConsoleError = console.error.bind(console);
const _origConsoleWarn  = console.warn.bind(console);
const _origConsoleLog   = console.log.bind(console);

function _argsToString(args) {
  const out = [];
  for (const a of args) {
    if (a instanceof Error) {
      out.push(a.stack || String(a));
    } else if (typeof a === "object" && a !== null) {
      try {
        out.push(JSON.stringify(a));
      } catch (_) {
        out.push(String(a));
      }
    } else {
      out.push(String(a));
    }
  }
  return out.join(" ");
}

/* Boot log detector.  Two forms are recognized:
     console.x("[boot]", ...rest)           → first arg exactly "[boot]"
     console.x("[boot] message", ...rest)   → first arg starts "[boot] "
   Anything else is not a boot log. */

function _isBootTagged(args) {
  if (args.length === 0) return false;
  const first = args[0];
  if (typeof first !== "string") return false;
  return first === "[boot]" || first.startsWith("[boot] ");
}

function _stripBootPrefix(text) {
  if (text === "[boot]") return "";
  if (text.startsWith("[boot] ")) return text.slice("[boot] ".length);
  return text;
}

console.error = function (...args) {
  _origConsoleError(...args);
  if (_isBootTagged(args)) {
    if (_MIRROR_BOOT_DISABLED) return;
    const text = _stripBootPrefix(_argsToString(args));
    if (!text) return;
    try { _postLog(text, "boot-error"); } catch (_) {}
    return;
  }
  try { _postLog(_argsToString(args), "console-error"); } catch (_) {}
};

console.warn = function (...args) {
  _origConsoleWarn(...args);
  if (_MIRROR_WARN_DISABLED) return;
  try { _postLog(_argsToString(args), "console-warn"); } catch (_) {}
};

console.log = function (...args) {
  _origConsoleLog(...args);
  if (_MIRROR_BOOT_DISABLED) return;
  if (!_isBootTagged(args)) return;

  /* Strip the leading "[boot] " because the class "boot-ok" already
     carries the tag; the terminal line is `[inpaint] boot-ok: msg`. */
  const text = _stripBootPrefix(_argsToString(args));
  if (!text) return;
  try { _postLog(text, "boot-ok"); } catch (_) {}
};

/* ---- uncaught hooks ---- */
(function _installErrorHooks() {
  if (typeof window === "undefined") return;
  window.addEventListener("error", (ev) => {
    const err = ev && ev.error;
    const msg = (err && err.stack) ? err.stack : (ev && ev.message);
    if (msg) _postLog(msg, "js-error");
  });
  window.addEventListener("unhandledrejection", (ev) => {
    const r = ev && ev.reason;
    const msg = (r && r.stack) ? r.stack : String(r);
    _postLog(msg, "js-rejection");
  });
})();

/* ==========================================================================
   STATUS FLASH
   ==========================================================================
   DOM behaviour unchanged from before.  "bad" always mirrors.  "warn"
   mirrors unless the page opted out via ?pgkit_no_mirror_warn=1.
   "ok" and the null class do not mirror. */

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

  if (cls === "bad") {
    _postLog(msg, cls);
  } else if (cls === "warn") {
    if (!_MIRROR_WARN_DISABLED) _postLog(msg, cls);
  }
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
