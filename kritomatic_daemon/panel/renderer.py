"""
Description → Qt widget tree.

Consumes the panel description and builds a widget tree in a container
layout. On a new description:

  * If structure_id is unchanged, values are synced in place. Fields
    that currently have focus are skipped (their spec is still updated
    so future syncs are consistent).
  * If structure_id changed, the tree is rebuilt.

Edits are routed to a commit callback. Actions are routed to an action
callback. The renderer knows nothing about comfyui; it knows field
types and command names.

Focus preservation across rebuilds
==================================
A structural change destroys and recreates every field widget. Without
intervention that would steal focus from a field the user is typing
into — visible on a Krita startup where the daemon publishes an empty
panel (no workflow), the user clicks into a run-option field, and the
auto-pull's subsequent state push arrives a beat later and rebuilds
the tree.

`_rebuild` therefore captures the focused field's key and (for text
inputs) its cursor position before the teardown, and restores both
after the rebuild if that field still exists in the new description.
A field that no longer exists is not restored; the user's focus is
allowed to fall wherever Qt puts it, which is what the user would
expect when the field they were editing has gone away.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPlainTextEdit, QCheckBox, QRadioButton,
    QButtonGroup, QComboBox, QSpinBox, QDoubleSpinBox, QPushButton,
    QFrame,
)


def _esc(text) -> str:
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


class _CommittableTextEdit(QPlainTextEdit):
    """QPlainTextEdit that commits on focus-out."""

    commitRequested = pyqtSignal(str)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.commitRequested.emit(self.toPlainText())


class PanelRenderer(QObject):
    """Renders a panel description into a container layout."""

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
        # Capture focus before teardown so it can be restored after the
        # new tree exists. See the module docstring, "Focus preservation
        # across rebuilds".
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
            # Insert before the trailing stretch if there is one.
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
        """Return (field_key, cursor) for the currently-focused field.

        `cursor` is a ("line", position) or ("plain", position) tuple
        when the focused widget is a text input, else None. If no field
        is focused, returns (None, None).
        """
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
        except Exception:
            pass

    @staticmethod
    def _find_focusable_leaf(widget):
        """Return the concrete input widget a field's outer container
        wraps, preferring the widget itself when it is a leaf type.

        A field's registered widget is sometimes a QLineEdit or
        QSpinBox directly, sometimes a QWidget wrapper holding radio
        buttons. Only the leaf types can receive keyboard focus and
        cursor positions, so those are what we return.
        """
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
        h.setSpacing(6)
        for item in section.get("items", []) or []:
            btn = QPushButton(item.get("label") or item.get("key", ""))
            style = item.get("style")
            if style == "primary":
                btn.setStyleSheet(
                    "background:#1a73e8;color:white;padding:6px 14px;"
                )
            elif style == "danger":
                btn.setStyleSheet(
                    "background:#c5221f;color:white;padding:6px 14px;"
                )
            key = item.get("key") or ""
            self._action_buttons[key] = btn
            btn.clicked.connect(
                lambda _checked=False, it=item: self._action(
                    it, self._read_all_values()
                )
            )
            h.addWidget(btn)
        return container

    # ---- commits ----

    def _on_text_commit(self, key: str, new_value: str) -> None:
        spec = self._field_specs.get(key)
        if spec is None:
            return
        old_value = spec.get("value")
        if str(new_value) == str(old_value if old_value is not None else ""):
            return
        self._on_field_commit(key, new_value)

    def _on_field_commit(self, key: str, value) -> None:
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

    def _read_widget_value(self, spec: dict, widget):
        ftype = spec.get("type", "text")
        try:
            if ftype == "text":
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
            if self._is_focused(widget):
                self._field_specs[key] = spec
                continue
            self._apply_value(spec, widget)
            self._field_specs[key] = spec

        # Refresh the section_specs from the new description too
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
