DISPATCH_JS = r"""
import { drawView }         from "./pg_view.js";
import { syncPanelFields }  from "./pg_panel_fields.js";

export function draw() {
  drawView();
  syncPanelFields();
}
"""