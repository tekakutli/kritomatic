"""
Description → Qt widget tree.

Consumes the panel description and builds a widget tree in a container
layout. On a new description:

  * If structure_id is unchanged, values are synced in place. Fields
    that currently have focus are skipped (their spec is still updated
    so future syncs are consistent). Fields marked `no_sync: true`
    (the v2 config fields) are always skipped — the docker collects
    them at Run time from the user's in-progress edits, and a daemon
    re-publish must not overwrite those edits.
  * If structure_id changed, the tree is rebuilt.

Edits are routed to a commit callback. Actions are routed to an action
callback. The renderer knows nothing about comfyui; it knows field
types, command names, and config paths.

Focus preservation across rebuilds
==================================
A structural change destroys and recreates every field widget. Without
intervention that would steal focus from a field the user is typing
into — visible on a Krita startup where the daemon publishes an empty
panel (no workflow), the user clicks into a run-option field, and the
auto-pull's subsequent state push arrives a beat later and rebuilds the
tree.

`_rebuild` therefore captures the focused field's key and (for text
inputs) its cursor position before the teardown, and restores both
after the rebuild if that field still exists in the new description.

Two config models
=================
Command fields carry `set_command`; the renderer commits them on edit.
Config fields carry `config_path` and `no_sync: true`; the renderer
does not commit them, but includes them in `build_config_from_widgets`,
which the docker calls at Run time to assemble the v2 config JSON.

Layer alias combos
==================
The `_LayerComboBox` remains for image/mask-alias fields rendered by
providers that carry `kind_hint`. In the v2 panel, none of the built-in
providers emit such fields, but the class is kept for future use and
for the playground's reuse. Behaviour is unchanged.

Commit triggers
---------------
Command fields commit on `activated` (dropdown pick), `editingFinished`
(blur or Enter), or on the checkbox/radio `toggled` signal. Config
fields do nothing on edit beyond updating their local widget state and
re-evaluating `visible_when` for dependent fields.

Layout
------
A `QComboBox`'s default horizontal size policy is `Preferred`, while
`QLineEdit`'s is `Expanding`. The form uses
`QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow`, so line edits fill
their row and combos do not. `_LayerComboBox` is given an `Expanding`
policy at construction so it lines up with the other fields.

Action-button fusion
====================
An action item may carry `thin: true` and `attach_to: "<key>"`. The
renderer draws it as a small companion button directly beside the item
named by `attach_to`. See the current module docstring's full section
for the QSS pairing rationale; behaviour is unchanged from the previous
version.

Disabled state
==============
Every action button has its stylesheet written with a `QPushButton`
rule for the enabled state and a `QPushButton:disabled` rule for the
disabled state. `set_action_enabled(key, enabled)` toggles
`setEnabled()`; the visuals follow from the QSS pseudo-state.
"""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPlainTextEdit, QCheckBox, QRadioButton,
    QButtonGroup, QComboBox, QSpinBox, QDoubleSpinBox, QPushButton,
    QFrame, QSizePolicy,
)


def _esc(text) -> str:
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


# Shared QSS for the enabled/disabled pair. The enabled rule carries
# the palette (danger/primary background), the disabled rule paints a
# neutral gray so the button visibly reads as unavailable. The
# `QPushButton` selector prefix is required so the rules form a matched
# pair — mixing implicit-property syntax (`background: red;`) with an
# explicit `QPushButton:disabled` selector in the same stylesheet makes
# the disabled rule apply but the enabled rule ambiguous on some Qt
# versions.
_DISABLED_QSS = (
    "QPushButton:disabled { background:#c8c8c8;color:#f0f0f0; }"
)


def _qss_danger(padding: str) -> str:
    return (
        f"QPushButton {{ background:#c5221f;color:white;padding:{padding}; }}"
        + _DISABLED_QSS
    )


def _qss_primary(padding: str) -> str:
    return (
        f"QPushButton {{ background:#1a73e8;color:white;padding:{padding}; }}"
        + _DISABLED_QSS
    )


class _CommittableTextEdit(QPlainTextEdit):
    """QPlainTextEdit that commits on focus-out."""

    commitRequested = pyqtSignal(str)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.commitRequested.emit(self.toPlainText())


class _LayerComboBox(QComboBox):
    """Editable combo used for layer-alias fields.

    Properties by design:
      * The current text is the alias value — a free string, not a
        whitelist. Picking from the dropdown is a convenience; typing
        a name that is not in the list still commits.
      * The completer matches on substring (MatchContains) and
        case-insensitively.
      * `popupAboutToShow` fires before the popup opens so the docker
        can schedule a layer-list refresh.
      * `_popup_open` tracks whether the popup is currently visible so
        `set_layers` can skip this combo without disrupting the user's
        view.
      * Horizontal size policy is Expanding, matching QLineEdit, so
        the field fills its row under
        `QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow`.
    """

    popupAboutToShow = pyqtSignal()

    def __init__(self, kind_hint: str, parent=None):
        super().__init__(parent)
        self._kind_hint = kind_hint
        self._popup_open = False
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        completer = self.completer()
        if completer is not None:
            completer.setCaseSensitivity(
                Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)

    def showPopup(self):
        self._popup_open = True
        self.popupAboutToShow.emit()
        super().showPopup()

    def hidePopup(self):
        super().hidePopup()
        self._popup_open = False

    def is_popup_open(self) -> bool:
        return self._popup_open


class PanelRenderer(QObject):
    """Renders a panel description into a container layout."""

    layerRefreshRequested = pyqtSignal()

    def __init__(self, container_layout, commit_callback, action_callback):
        super().__init__()
        self._layout = container_layout
        self._commit = commit_callback
        self._action = action_callback
        self._structure_id = None
        self._section_widgets = []
        self._section_specs = []
        self._field_widgets = {}
        self._field_specs = {}
        self._action_buttons = {}

    def render(self, description: dict) -> None:
        struct_id = description.get("structure_id", "")
        if struct_id == self._structure_id:
            self._sync_values(description)
            return
        self._rebuild(description)
        self._structure_id = struct_id

    # ---- rebuild ----

    def _rebuild(self, description: dict) -> None:
        focused_key, focused_cursor = self._capture_focus()

        for w in self._section_widgets:
            w.setParent(None)
            w.deleteLater()
        self._section_widgets = []
        self._field_widgets = {}
        self._field_specs = {}
        self._action_buttons = {}
        self._section_specs = description.get("sections", []) or []

        for section in self._section_specs:
            widget = self._build_section(section)
            if widget is None:
                continue
            count = self._layout.count()
            insert_at = count
            for i in range(count):
                item = self._layout.itemAt(i)
                if item and item.spacerItem():
                    insert_at = i
                    break
            self._layout.insertWidget(insert_at, widget)
            self._section_widgets.append(widget)

        self._restore_focus(focused_key, focused_cursor)

    # ---- focus capture / restore ----

    def _capture_focus(self):
        for key, widget in self._field_widgets.items():
            if widget.hasFocus():
                return key, self._read_cursor(widget)
            for child in widget.findChildren(QWidget):
                if child.hasFocus():
                    return key, self._read_cursor(child)
        return None, None

    @staticmethod
    def _read_cursor(widget):
        try:
            if isinstance(widget, QLineEdit):
                return ("line", widget.cursorPosition())
            if isinstance(widget, QPlainTextEdit):
                return ("plain", widget.textCursor().position())
            if isinstance(widget, QComboBox) and widget.isEditable():
                le = widget.lineEdit()
                if le is not None:
                    return ("combo", le.cursorPosition())
        except Exception:
            pass
        return None

    def _restore_focus(self, key, cursor) -> None:
        if not key or key not in self._field_widgets:
            return
        widget = self._field_widgets[key]
        target = self._find_focusable_leaf(widget)
        if target is None:
            return
        try:
            target.setFocus()
        except Exception:
            return
        if cursor is None:
            return
        kind, pos = cursor
        try:
            if kind == "line" and isinstance(target, QLineEdit):
                target.setCursorPosition(pos)
            elif kind == "plain" and isinstance(target, QPlainTextEdit):
                cur = target.textCursor()
                cur.setPosition(pos)
                target.setTextCursor(cur)
            elif (kind == "combo"
                  and isinstance(target, QComboBox)
                  and target.isEditable()):
                le = target.lineEdit()
                if le is not None:
                    le.setCursorPosition(min(pos, len(le.text())))
        except Exception:
            pass

    @staticmethod
    def _find_focusable_leaf(widget):
        leaf_types = (
            QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
            QComboBox, QCheckBox,
        )
        if isinstance(widget, leaf_types):
            return widget
        for child in widget.findChildren(QWidget):
            if isinstance(child, leaf_types):
                return child
        return None

    def _build_section(self, section: dict):
        kind = section.get("kind")
        if kind == "metadata":
            return self._build_metadata(section)
        if kind == "form":
            return self._build_form(section)
        if kind == "actions":
            return self._build_actions(section)
        return None

    def _build_metadata(self, section: dict):
        fields = section.get("fields") or []
        if not fields:
            return None
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)
        for f in fields:
            label = f.get("label")
            value = f.get("value", "")
            emphasis = f.get("emphasis") or "normal"
            text = f"<b>{_esc(label)}:</b> {_esc(value)}" if label else _esc(value)
            if emphasis == "strong":
                text = f"<b>{text}</b>"
            elif emphasis == "dim":
                text = f"<span style='color:#888'>{text}</span>"
            elif emphasis == "danger":
                text = f"<span style='color:#c5221f'>{text}</span>"
            lbl = QLabel(text)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setWordWrap(True)
            layout.addWidget(lbl)
        return container

    def _build_form(self, section: dict):
        group = QGroupBox(section.get("title") or "")
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        for f in section.get("fields", []):
            key = f.get("key")
            if not key:
                continue
            widget = self._build_field_widget(f)
            if widget is None:
                continue
            self._field_widgets[key] = widget
            self._field_specs[key] = f
            label = f.get("label") or key
            if f.get("overridden"):
                label = f"{label} ●"
            form.addRow(label, widget)
        self._reevaluate_visibility()
        return group

    def _build_field_widget(self, f: dict):
        key = f["key"]
        ftype = f.get("type", "text")
        value = f.get("value")

        if ftype == "text":
            kind_hint = f.get("kind_hint")
            if kind_hint in ("image", "mask"):
                w = _LayerComboBox(kind_hint)
                w.setCurrentText("" if value is None else str(value))
                w.activated.connect(
                    lambda _i, k=key, w=w: self._on_text_commit(
                        k, w.currentText()
                    )
                )
                le = w.lineEdit()
                if le is not None:
                    le.editingFinished.connect(
                        lambda k=key, w=w: self._on_text_commit(
                            k, w.currentText()
                        )
                    )
                w.popupAboutToShow.connect(
                    self.layerRefreshRequested.emit
                )
                return w

            w = QLineEdit()
            w.setText("" if value is None else str(value))
            w.editingFinished.connect(
                lambda k=key, w=w: self._on_text_commit(k, w.text())
            )
            return w

        if ftype == "textarea":
            w = _CommittableTextEdit()
            w.setPlainText("" if value is None else str(value))
            w.setFixedHeight(72)
            w.commitRequested.connect(
                lambda text, k=key: self._on_text_commit(k, text)
            )
            return w

        if ftype == "integer":
            w = QSpinBox()
            try:
                w.setRange(int(f.get("min", -10 ** 9)),
                           int(f.get("max", 10 ** 9)))
            except (TypeError, ValueError):
                w.setRange(-10 ** 9, 10 ** 9)
            if f.get("step") is not None:
                try:
                    w.setSingleStep(int(f["step"]))
                except (TypeError, ValueError):
                    pass
            if value is not None:
                try:
                    w.setValue(int(value))
                except (TypeError, ValueError):
                    pass
            w.valueChanged.connect(
                lambda v, k=key: self._on_field_commit(k, int(v))
            )
            return w

        if ftype == "number":
            w = QDoubleSpinBox()
            w.setDecimals(6)
            try:
                w.setRange(float(f.get("min", -10 ** 12)),
                           float(f.get("max", 10 ** 12)))
            except (TypeError, ValueError):
                w.setRange(-10 ** 12, 10 ** 12)
            step = f.get("step")
            if step is not None and step != "any":
                try:
                    w.setSingleStep(float(step))
                except (TypeError, ValueError):
                    pass
            if value is not None:
                try:
                    w.setValue(float(value))
                except (TypeError, ValueError):
                    pass
            w.valueChanged.connect(
                lambda v, k=key: self._on_field_commit(k, float(v))
            )
            return w

        if ftype == "toggle":
            w = QCheckBox()
            w.setChecked(bool(value))
            w.toggled.connect(
                lambda v, k=key: self._on_field_commit(k, bool(v))
            )
            return w

        if ftype == "radio":
            container = QWidget()
            h = QHBoxLayout(container)
            h.setContentsMargins(0, 0, 0, 0)
            grp = QButtonGroup(container)
            for opt in f.get("options", []):
                rb = QRadioButton(str(opt))
                if str(value) == str(opt):
                    rb.setChecked(True)
                grp.addButton(rb)
                h.addWidget(rb)
                rb.toggled.connect(
                    lambda checked, k=key, o=str(opt): (
                        self._on_field_commit(k, o) if checked else None
                    )
                )
            return container

        if ftype == "select":
            w = QComboBox()
            for opt in f.get("options", []):
                w.addItem(str(opt))
            if value is not None:
                idx = w.findText(str(value))
                if idx >= 0:
                    w.setCurrentIndex(idx)
            w.currentTextChanged.connect(
                lambda v, k=key: self._on_field_commit(k, v)
            )
            return w

        return None

    def _build_actions(self, section: dict):
        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        items = section.get("items", []) or []
        added_any = False

        for item in items:
            key = item.get("key") or ""
            is_thin = bool(item.get("thin", False))
            attaches = is_thin and bool(item.get("attach_to"))

            if added_any:
                h.addSpacing(-2 if attaches else 6)

            btn = QPushButton(item.get("label") or key)
            style = item.get("style")

            if style == "primary":
                btn.setStyleSheet(_qss_primary("6px 14px"))
            elif style == "danger":
                if is_thin:
                    btn.setFixedWidth(40)
                    btn.setStyleSheet(_qss_danger("6px 4px"))
                else:
                    btn.setStyleSheet(_qss_danger("6px 14px"))

            self._action_buttons[key] = btn
            btn.clicked.connect(
                lambda _checked=False, it=item: self._action(
                    it, self._read_all_values()
                )
            )
            h.addWidget(btn)
            added_any = True

        return container

    # ---- commits ----

    def _on_text_commit(self, key: str, new_value: str) -> None:
        """Route a text-widget edit to the field's commit command."""
        spec = self._field_specs.get(key)
        if spec is None:
            return
        old_value = spec.get("value")
        if str(new_value) == str(old_value if old_value is not None else ""):
            return
        spec["value"] = new_value
        self._on_field_commit(key, new_value)

    def _on_field_commit(self, key: str, value) -> None:
        # Config fields (config_path, no set_command) do not need a
        # per-field command — the docker assembles the whole config
        # from its widgets and sends `comfy_apply_config`. The commit
        # callback receives a marker and decides what to do (the
        # docker's implementation debounces the apply). Fields with
        # `visible_when` dependencies get their visibility re-checked
        # here regardless of whether they triggered a commit, because
        # a resource's source change is what makes its layer_name
        # field become visible.
        self._reevaluate_visibility()

        spec = self._field_specs.get(key)
        if spec is None:
            return
        section = self._find_section(key)
        if section is None:
            return

        section_commit = section.get("commit")
        if section_commit:
            args = {}
            for fk, argname in (section_commit.get("arg_map") or {}).items():
                fspec = self._field_specs.get(fk)
                fw = self._field_widgets.get(fk)
                if fspec is None or fw is None:
                    continue
                args[argname] = self._read_widget_value(fspec, fw)
            command = {"type": section_commit["type"], **args}
            self._commit(key, command)
            return

        if spec.get("set_command"):
            arg_key = spec.get("arg_key", "value")
            command = dict(spec["set_command"])
            command[arg_key] = value
            self._commit(key, command)
            return

        if spec.get("config_path"):
            # Marker command: the docker turns this into a full
            # `comfy_apply_config` built from `build_config_from_
            # widgets()`, debounced across rapid edits. The daemon
            # never sees this literal command type.
            self._commit(key, {"type": "comfy_apply_config_from_widgets"})

    def _read_widget_value(self, spec: dict, widget):
        ftype = spec.get("type", "text")
        try:
            if ftype == "text":
                if isinstance(widget, QComboBox):
                    return widget.currentText()
                return widget.text()
            if ftype == "textarea":
                return widget.toPlainText()
            if ftype == "integer":
                return int(widget.value())
            if ftype == "number":
                return float(widget.value())
            if ftype == "toggle":
                return bool(widget.isChecked())
            if ftype == "select":
                return widget.currentText()
            if ftype == "radio":
                for child in widget.findChildren(QRadioButton):
                    if child.isChecked():
                        return child.text()
                return ""
        except Exception:
            return ""
        return None

    def _read_all_values(self) -> dict:
        out = {}
        for key, widget in self._field_widgets.items():
            spec = self._field_specs.get(key)
            if spec is None:
                continue
            out[key] = self._read_widget_value(spec, widget)
        return out

    def _find_section(self, key: str):
        for section in self._section_specs:
            for f in section.get("fields", []) or []:
                if f.get("key") == key:
                    return section
        return None

    # ---- v2 config collection ----

    def build_config_from_widgets(self) -> dict:
        """Assemble the v2 panel config from the current widget state.

        Every field that carries a `config_path` contributes one value
        to the config dict at that path. Fields without a `config_path`
        are ignored. Returns the standard four-section shape
        `{"resources": {}, "params": {}, "crop": {}, "paste": {}}`,
        matching what `comfy_apply_config` expects.

        Called by the docker at Run time, immediately before dispatching
        `comfy_panel_run`. The result is JSON-serialised into the
        command's `resources` / `params` / `crop` / `paste` args.
        """
        cfg: dict = {
            "resources": {},
            "params": {},
            "crop": {},
            "paste": {},
        }
        for key, spec in self._field_specs.items():
            path = spec.get("config_path")
            if not path:
                continue
            widget = self._field_widgets.get(key)
            if widget is None:
                continue
            value = self._read_widget_value(spec, widget)
            self._set_path(cfg, path, value)
        return cfg

    @staticmethod
    def _set_path(cfg: dict, path, value) -> None:
        """Set `cfg[path[0]][path[1]]...[path[-1]] = value`, creating
        intermediate dicts as needed."""
        d = cfg
        for key in path[:-1]:
            if key not in d:
                d[key] = {}
            d = d[key]
        d[path[-1]] = value

    # ---- sync ----

    def _sync_values(self, description: dict) -> None:
        new_specs = {}
        for section in description.get("sections", []) or []:
            for f in section.get("fields", []) or []:
                if f.get("key"):
                    new_specs[f["key"]] = f

        for key, spec in new_specs.items():
            widget = self._field_widgets.get(key)
            if widget is None:
                continue
            if spec.get("no_sync"):
                # Config fields used to set this to prevent panel
                # re-publishes from stomping their in-progress edits.
                # With auto-apply, the user's edit is pushed to the
                # daemon within a debounce window and reflected back
                # via the next re-publish; the focus check below
                # protects the actively-edited field. The `no_sync`
                # branch stays for any future provider that wants a
                # field the daemon cannot override, but no built-in
                # provider uses it.
                self._field_specs[key] = spec
                continue
            if self._is_focused(widget):
                self._field_specs[key] = spec
                continue
            self._apply_value(spec, widget)
            self._field_specs[key] = spec

        self._section_specs = description.get("sections", []) or []
        self._reevaluate_visibility()

    @staticmethod
    def _is_focused(widget) -> bool:
        try:
            if widget.hasFocus():
                return True
            for child in widget.findChildren(QWidget):
                if child.hasFocus():
                    return True
        except Exception:
            pass
        return False

    def _apply_value(self, spec: dict, widget) -> None:
        ftype = spec.get("type", "text")
        value = spec.get("value")
        try:
            if ftype == "text":
                target = "" if value is None else str(value)
                if isinstance(widget, QComboBox):
                    if widget.currentText() != target:
                        widget.blockSignals(True)
                        widget.setCurrentText(target)
                        widget.blockSignals(False)
                else:
                    if widget.text() != target:
                        widget.blockSignals(True)
                        widget.setText(target)
                        widget.blockSignals(False)
            elif ftype == "textarea":
                target = "" if value is None else str(value)
                if widget.toPlainText() != target:
                    widget.blockSignals(True)
                    widget.setPlainText(target)
                    widget.blockSignals(False)
            elif ftype == "integer":
                try:
                    target = int(value)
                except (TypeError, ValueError):
                    return
                if widget.value() != target:
                    widget.blockSignals(True)
                    widget.setValue(target)
                    widget.blockSignals(False)
            elif ftype == "number":
                try:
                    target = float(value)
                except (TypeError, ValueError):
                    return
                if abs(widget.value() - target) > 1e-9:
                    widget.blockSignals(True)
                    widget.setValue(target)
                    widget.blockSignals(False)
            elif ftype == "toggle":
                target = bool(value)
                if widget.isChecked() != target:
                    widget.blockSignals(True)
                    widget.setChecked(target)
                    widget.blockSignals(False)
            elif ftype == "select":
                target = "" if value is None else str(value)
                if widget.currentText() != target:
                    idx = widget.findText(target)
                    if idx >= 0:
                        widget.blockSignals(True)
                        widget.setCurrentIndex(idx)
                        widget.blockSignals(False)
            elif ftype == "radio":
                target = "" if value is None else str(value)
                for child in widget.findChildren(QRadioButton):
                    should = child.text() == target
                    if child.isChecked() != should:
                        child.blockSignals(True)
                        child.setChecked(should)
                        child.blockSignals(False)
        except Exception:
            pass

    def _reevaluate_visibility(self) -> None:
        for key, spec in self._field_specs.items():
            widget = self._field_widgets.get(key)
            if widget is None:
                continue
            rule = spec.get("visible_when")
            if not rule:
                widget.setVisible(True)
                continue
            visible = True
            for other_key, expected in rule.items():
                other_spec = self._field_specs.get(other_key)
                other_widget = self._field_widgets.get(other_key)
                if other_spec is None or other_widget is None:
                    continue
                val = self._read_widget_value(other_spec, other_widget)
                if str(val) != str(expected):
                    visible = False
                    break
            widget.setVisible(visible)

    def set_action_enabled(self, key: str, enabled: bool) -> None:
        btn = self._action_buttons.get(key)
        if btn is not None:
            btn.setEnabled(bool(enabled))

    # ---- layer list ----

    def set_layers(self, layers: dict) -> None:
        """Update every live layer-alias combo's item list.

        `layers` is {"image": [...], "mask": [...], "recent": [...]}
        as emitted by `DaemonConnection.request_layers`. Behaviour is
        unchanged from the previous version; in the v2 panel no built-
        in providers emit `_LayerComboBox` fields, but the class remains
        available for the playground and for future providers.
        """
        image_list = layers.get("image") or []
        mask_list = layers.get("mask") or []
        recent = layers.get("recent") or []

        for key, widget in self._field_widgets.items():
            if not isinstance(widget, _LayerComboBox):
                continue
            if widget.is_popup_open():
                continue
            spec = self._field_specs.get(key) or {}
            hint = spec.get("kind_hint")
            if hint == "image":
                bucket = image_list
            elif hint == "mask":
                bucket = mask_list
            else:
                continue
            bucket_set = set(bucket)
            recent_in_bucket = [a for a in recent if a in bucket_set]
            recent_set = set(recent_in_bucket)
            rest = [b for b in bucket if b not in recent_set]
            new_list = recent_in_bucket + rest

            current_text = widget.currentText()
            widget.blockSignals(True)
            le = widget.lineEdit()
            if le is not None:
                le.blockSignals(True)
            try:
                widget.clear()
                widget.addItems(new_list)
                widget.setCurrentText(current_text)
            finally:
                if le is not None:
                    le.blockSignals(False)
                widget.blockSignals(False)
