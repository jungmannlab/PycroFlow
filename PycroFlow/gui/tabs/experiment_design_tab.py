"""Experiment Design tab: author the high-level design + compile it.

Structured (schema-driven) editor for the high-level experiment design.
Load / Save a design YAML, edit it field-by-field (incl. the nested SPH-RESI
target / RESI rounds), and **Translate** it into the Run Sequence tab via
:meth:`PycroFlow.services.ExperimentService.translate`.
"""
import os

import yaml
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFileDialog, QMessageBox,
)

from PycroFlow.schemas.experiment_design import ExperimentDesign
from PycroFlow.gui.widgets.schema_form import SchemaForm
from PycroFlow.gui.widgets.dnd import YamlDropMixin


class ExperimentDesignTab(YamlDropMixin, QWidget):
    def __init__(self, experiment_service, on_translated=None,
                 on_design_loaded=None, parent=None):
        super().__init__(parent)
        self._svc = experiment_service
        self._on_translated = on_translated
        self._on_design_loaded = on_design_loaded
        self._form = None
        self._build_ui()
        self.enable_yaml_drop()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        self.load_btn = QPushButton("Load…")
        self.save_btn = QPushButton("Save…")
        self.translate_btn = QPushButton("Translate → Run Sequence")
        for b in (self.load_btn, self.save_btn, self.translate_btn):
            controls.addWidget(b)
        controls.addStretch()
        layout.addLayout(controls)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll)

        self.load_btn.clicked.connect(self._on_load)
        self.save_btn.clicked.connect(self._on_save)
        self.translate_btn.clicked.connect(self._on_translate)

        # Start from an empty form (scalar defaults filled in by the schema).
        self._set_form({})

    def _set_form(self, data):
        self._form = SchemaForm(ExperimentDesign, data)
        self.scroll.setWidget(self._form)
        self._wire_save_dir_hint()

    def _wire_save_dir_hint(self):
        """Show the resolved absolute save_dir beside the edit box.

        Files created during the run land in ``save_dir``, resolved against
        the working directory (which loading a design from disk moves to the
        design's folder). When the entered path is relative (or '.'), the
        actual destination is non-obvious, so show it; hide it for an
        already-absolute path.
        """
        self._save_dir_hint = None
        editor = self._form.field_editor('save_dir')
        line = editor.line_edit() if editor is not None else None
        if line is None:
            return
        hint = QLabel()
        hint.setStyleSheet("color: gray;")
        editor.add_suffix(hint)
        self._save_dir_hint = hint

        def update(text):
            if os.path.isabs(text):
                hint.setText("")
            else:
                hint.setText("→ {}".format(os.path.abspath(text or '.')))

        line.textChanged.connect(update)
        update(line.text())

    # --- actions ------------------------------------------------------

    def _on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load experiment design", "", "YAML files (*.yaml *.yml)")
        if path:
            self.load_design_path(path)

    def load_design_path(self, path):
        """Load + validate a design YAML and show it in the editor."""
        try:
            self._svc.load_experiment_design(path)
        except Exception as exc:
            QMessageBox.critical(
                self, "Invalid experiment design", "{}".format(exc))
            return
        self._set_form(self._svc.experiment_design)
        if self._on_design_loaded is not None:
            self._on_design_loaded()

    def on_yaml_dropped(self, path):
        self.load_design_path(path)

    def _on_save(self):
        try:
            model = self._form.to_model()
        except Exception as exc:
            QMessageBox.warning(
                self, "Cannot save — invalid design", "{}".format(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save experiment design", "", "YAML files (*.yaml *.yml)")
        if not path:
            return
        with open(path, "w") as f:
            yaml.safe_dump(
                model.model_dump(by_alias=True), f, sort_keys=False)

    def _on_translate(self):
        try:
            self._svc.load_experiment_design(self._form.to_dict())
            self._svc.translate()
        except Exception as exc:
            QMessageBox.critical(
                self, "Translation failed", "{}".format(exc))
            return
        if self._on_translated is not None:
            self._on_translated()
