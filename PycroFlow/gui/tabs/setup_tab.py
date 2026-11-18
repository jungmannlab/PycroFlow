"""Setup editor tab: a structured editor for a microscope **setup** YAML.

Loads a setup (shipped or user-local), renders its *structurable* hardware
fields (pump params, serial interface, flush positions, tubing, PFS tags,
illumination) as a schema-driven form, and keeps the parts a scalar form can't
safely edit — the reservoir **wiring** (``valve_pos``), the multiplexer, and
any extra blocks (``camera_info``, ``live_analysis``, ``monitoring``) — in a
raw-YAML panel that is preserved byte-for-byte on save (see
:mod:`PycroFlow.schemas.setup`). Saves back to the user setups dir via
:func:`PycroFlow.configs.save_setup`.

The same widget is embedded in the setup wizard (``embedded=True`` hides its
own load/save toolbar; the host dialog drives persistence).
"""

from __future__ import annotations

import yaml
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from PycroFlow import configs, setup_core
from PycroFlow.gui.widgets.schema_form import SchemaForm
from PycroFlow.schemas.setup import (
    SetupConfig,
    SetupValidationError,
    merge_setup,
    split_setup,
    validate_setup_config,
)


class SetupEditorTab(QWidget):
    """Structured editor for a microscope setup YAML.

    Parameters
    ----------
    on_saved : callable, optional
        Called with the saved setup name after a successful Save, so the host
        (the main window) can refresh its setup selector.
    embedded : bool, default False
        Hide the load/save toolbar (the host dialog provides OK/Cancel and
        reads :meth:`current_name` / :meth:`current_merged`).
    """

    def __init__(self, on_saved=None, embedded=False, parent=None):
        super().__init__(parent)
        self._on_saved = on_saved
        self._embedded = embedded
        self._name = None
        self._passthrough = {}
        self._form = None
        self._build_ui()
        if not embedded:
            self._refresh_list()

    # --- construction -------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        if not self._embedded:
            controls = QHBoxLayout()
            self.setup_combo = QComboBox()
            self.load_btn = QPushButton("Load")
            self.new_btn = QPushButton("New from template…")
            self.save_btn = QPushButton("Save")
            controls.addWidget(QLabel("Setup:"))
            controls.addWidget(self.setup_combo)
            controls.addWidget(self.load_btn)
            controls.addWidget(self.new_btn)
            controls.addStretch()
            controls.addWidget(self.save_btn)
            layout.addLayout(controls)
            self.load_btn.clicked.connect(self._on_load)
            self.new_btn.clicked.connect(self._on_new_from_template)
            self.save_btn.clicked.connect(self._on_save)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)

        # Foldable raw-YAML panel for the wiring + extras that are preserved
        # verbatim (collapsed by default — it is rarely edited).
        self.raw_toggle = QToolButton()
        self.raw_toggle.setText(
            "Wiring & extras (raw YAML) — reservoirs, multiplexer, camera_info…"
        )
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.setChecked(False)
        self.raw_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.raw_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.raw_toggle.setStyleSheet("QToolButton { border: none; }")
        self.raw_toggle.toggled.connect(self._on_raw_toggled)
        layout.addWidget(self.raw_toggle)

        self.raw_edit = QTextEdit()
        self.raw_edit.setFont(QFont("monospace"))
        self.raw_edit.setVisible(False)
        self.raw_edit.setToolTip(
            "The reservoir valve wiring and other blocks the structured form "
            "can't edit safely. Preserved exactly on save; edit as YAML."
        )
        layout.addWidget(self.raw_edit)

        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        layout.addWidget(self.status)

        # Start empty until a setup is loaded.
        self._set_form({})

    def _on_raw_toggled(self, checked):
        self.raw_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        self.raw_edit.setVisible(checked)

    def _refresh_list(self, select=None):
        current = select or self.setup_combo.currentText()
        self.setup_combo.blockSignals(True)
        self.setup_combo.clear()
        self.setup_combo.addItems(configs.list_setups())
        i = self.setup_combo.findText(current)
        if i >= 0:
            self.setup_combo.setCurrentIndex(i)
        self.setup_combo.blockSignals(False)

    # --- data in/out --------------------------------------------------

    def _set_form(self, structured, passthrough=None):
        self._passthrough = passthrough or {}
        self._form = SchemaForm(SetupConfig, structured)
        self.scroll.setWidget(self._form)
        self.raw_edit.setPlainText(
            yaml.safe_dump(self._passthrough, sort_keys=False)
            if self._passthrough
            else ""
        )

    def load_data(self, raw: dict, name: str | None = None):
        """Load a setup dict into the editor (splitting structured/raw)."""
        structured, passthrough = split_setup(raw or {})
        self._name = name or (raw or {}).get("setup")
        self._set_form(structured, passthrough)
        self.status.setText(
            "Editing {!r}".format(self._name) if self._name else ""
        )

    def load_setup(self, name: str):
        """Load an existing setup by name (shipped or user-local)."""
        try:
            raw = configs.load_setup_file(name)
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            QMessageBox.critical(
                self, "Could not load setup", "{}".format(exc)
            )
            return
        self.load_data(raw, name=name)

    def current_name(self) -> str:
        """The setup name from the form's ``setup`` field (authoritative)."""
        if self._form is None:
            return ""
        return str((self._form.to_dict() or {}).get("setup") or "").strip()

    def current_merged(self) -> dict:
        """Recombine the structured form + the raw-YAML panel into one dict.

        Raises
        ------
        ValueError
            If the raw-YAML panel does not parse, or is not a mapping.
        """
        text = self.raw_edit.toPlainText().strip()
        passthrough = {}
        if text:
            passthrough = yaml.safe_load(text)
            if not isinstance(passthrough, dict):
                raise ValueError(
                    "the 'Wiring & extras' panel must be a YAML mapping"
                )
        return merge_setup(self._form.to_dict(), passthrough)

    # --- actions ------------------------------------------------------

    def _on_load(self):
        name = self.setup_combo.currentText().strip()
        if name:
            self.load_setup(name)

    def _on_new_from_template(self):
        templates = setup_core.template_names()
        template, ok = QInputDialog.getItem(
            self,
            "New from template",
            "Clone which template?",
            templates,
            max(
                templates.index("Template") if "Template" in templates else 0,
                0,
            ),
            False,
        )
        if not ok:
            return
        name, ok = QInputDialog.getText(
            self, "New setup", "Name for the new setup:"
        )
        name = name.strip()
        if not ok or not name:
            return
        if name in configs.list_setups():
            QMessageBox.warning(
                self,
                "Name in use",
                "A setup named {!r} already exists.".format(name),
            )
            return
        raw = setup_core.clone_setup(template, name)
        # Not saved yet — load it into the form so the operator can fill in the
        # placeholder values, then Save.
        self.load_data(raw, name=name)
        self.status.setText(
            "New setup {!r} (cloned from {!r}) — not saved yet".format(
                name, template
            )
        )

    def _on_save(self):
        try:
            name, merged = self._validated_result()
        except (SetupValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot save setup", "{}".format(exc))
            return
        try:
            path = configs.save_setup(name, merged)
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            QMessageBox.critical(
                self, "Could not save setup", "{}".format(exc)
            )
            return
        self._name = name
        self._refresh_list(select=name)
        self.status.setText("Saved {!r} to {}".format(name, path))
        if self._on_saved is not None:
            self._on_saved(name)

    def _validated_result(self):
        """Return ``(name, merged_dict)`` or raise on an invalid setup.

        Shared by the Save button and the embedded (wizard dialog) path.
        """
        name = self.current_name()
        if not name:
            raise ValueError("the setup needs a name (the 'setup' field)")
        merged = self.current_merged()
        validate_setup_config(merged)
        return name, merged


class SetupEditorDialog(QDialog):
    """Modal wrapper around :class:`SetupEditorTab` for the setup wizard.

    Seeds the editor with ``data`` (a cloned/partially-filled setup dict); on
    OK the edited, validated dict is available via :meth:`result_data` — the
    wizard persists it on commit, so the dialog itself never writes.
    """

    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit hardware configuration")
        self.resize(640, 720)
        self._result = None
        self.editor = SetupEditorTab(embedded=True)
        self.editor.load_data(data)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.editor, 1)
        lay.addWidget(buttons)

    def _on_accept(self):
        try:
            _name, merged = self.editor._validated_result()
        except (SetupValidationError, ValueError) as exc:
            QMessageBox.warning(self, "Invalid setup", "{}".format(exc))
            return
        self._result = merged
        self.accept()

    def result_data(self) -> dict | None:
        """The edited, validated setup dict (``None`` if cancelled)."""
        return self._result
