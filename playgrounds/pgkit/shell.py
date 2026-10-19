"""
pgkit.shell — installShellControls as an ES module.

The panel's collapse/restore logic used to live inline in the boot
script emitted by `html_shell.page()`.  With ES modules, it lives
here as an exported function; the boot module imports it and calls
it.

The function attaches listeners to `#collapseBtn` and `#restoreBtn`
and installs the H keyboard shortcut.  Both elements are part of
the shell emitted by `page()`, so they exist by the time this runs.
"""

SHELL_JS = r"""
export function installShellControls() {
  const ui          = document.getElementById("ui");
  const restoreBtn  = document.getElementById("restoreBtn");
  const collapseBtn = document.getElementById("collapseBtn");

  if (!ui || !restoreBtn || !collapseBtn) return;

  const collapse = () => {
    ui.classList.add("collapsed");
    restoreBtn.classList.add("visible");
  };
  const restore = () => {
    ui.classList.remove("collapsed");
    restoreBtn.classList.remove("visible");
  };

  collapseBtn.addEventListener("click", collapse);
  restoreBtn.addEventListener("click", restore);

  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
    if (e.key === "h" || e.key === "H") {
      if (ui.classList.contains("collapsed")) restore();
      else                                     collapse();
    }
  });
}
"""
