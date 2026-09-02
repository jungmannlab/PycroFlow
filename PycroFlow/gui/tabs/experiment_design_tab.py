"""Experiment Design tab: author the high-level design + compile it.

Structured (schema-driven) editor for the high-level experiment design.
Load / Save a design YAML, edit it field-by-field (incl. the nested SPH-RESI
target / RESI rounds), and **Translate** it into the Run Sequence tab via
:meth:`PycroFlow.services.ExperimentService.translate`.
"""

import os

import yaml
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QTextEdit,
    QScrollArea,
    QFileDialog,
    QMessageBox,
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QAbstractSpinBox,
)

from PycroFlow.schemas.experiment_design import ExperimentDesign
from PycroFlow.gui.widgets.schema_form import SchemaForm
from PycroFlow.gui.widgets.dnd import YamlDropMixin


class ExperimentDesignTab(YamlDropMixin, QWidget):
    def __init__(
        self,
        experiment_service,
        on_translated=None,
        on_design_loaded=None,
        reservoir_ids_provider=None,
        laser_options_provider=None,
        parent=None,
    ):
        super().__init__(parent)
        self._svc = experiment_service
        self._on_translated = on_translated
        self._on_design_loaded = on_design_loaded
        # Callable returning the current setup's valid reservoir ids, used to
        # restrict the reservoir-id dropdowns. None -> free-text ids.
        self._reservoir_ids_provider = reservoir_ids_provider
        # Callable returning the setup's monet laser lines, for the laser
        # dropdown. None -> only the current value is offered.
        self._laser_options_provider = laser_options_provider
        self._form = None
        self._build_ui()
        self.enable_yaml_drop()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        self.load_btn = QPushButton("Load…")
        self.save_btn = QPushButton("Save…")
        self.clear_btn = QPushButton("Clear")
        self.translate_btn = QPushButton("Translate → Run Sequence")
        for b in (
            self.load_btn,
            self.save_btn,
            self.clear_btn,
            self.translate_btn,
        ):
            controls.addWidget(b)
        # Estimated run time + total reagent volume, recomputed live from the
        # current (unsaved) design whenever a field changes — see
        # _connect_estimate_signals.
        self.estimate_label = QLabel("")
        self.estimate_label.setStyleSheet("color: gray;")
        controls.addWidget(self.estimate_label)
        controls.addStretch()
        layout.addLayout(controls)

        # Foldable preview of the compiled sequence of events + per-reservoir
        # volumes. Collapsed by default so it costs no space until wanted.
        self.preview_toggle = QToolButton()
        self.preview_toggle.setText("Sequence & volumes")
        self.preview_toggle.setCheckable(True)
        self.preview_toggle.setChecked(False)
        self.preview_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.preview_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.preview_toggle.setStyleSheet("QToolButton { border: none; }")
        self.preview_toggle.toggled.connect(self._on_preview_toggled)
        layout.addWidget(self.preview_toggle)

        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setVisible(False)
        self.preview_text.setMaximumHeight(220)
        layout.addWidget(self.preview_text)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll)

        # Debounce: edits arrive in bursts (typing), so coalesce them into one
        # recompute shortly after the last change rather than per keystroke.
        self._estimate_timer = QTimer(self)
        self._estimate_timer.setSingleShot(True)
        self._estimate_timer.setInterval(300)
        self._estimate_timer.timeout.connect(self._recompute_estimate)

        self.load_btn.clicked.connect(self._on_load)
        self.save_btn.clicked.connect(self._on_save)
        self.clear_btn.clicked.connect(self._on_clear)
        self.translate_btn.clicked.connect(self._on_translate)

        # Start from an empty form (scalar defaults filled in by the schema).
        self._set_form({})

    def _set_form(self, data):
        self._form = SchemaForm(
            ExperimentDesign, data, context=self._editor_context(data)
        )
        self.scroll.setWidget(self._form)
        self._wire_save_dir_hint()
        self._connect_estimate_signals()
        self._schedule_estimate()

    def _editor_context(self, data):
        """Dynamic dropdown options for the schema form.

        ``reservoir_names`` (the names defined in this design) feed the
        imager/buffer dropdowns and update live as the reservoir table is
        edited; ``reservoir_ids`` (from the loaded setup) restrict the
        reservoir-id inputs, and ``lasers`` (from the setup's monet config)
        the laser dropdown (both snapshot at form-build time).
        """
        settings = ((data or {}).get("fluid", {}) or {}).get("settings", {})
        names = list((settings.get("reservoir_names") or {}).values())
        return {
            "reservoir_names": names,
            "reservoir_ids": self._call_provider(self._reservoir_ids_provider),
            "lasers": self._call_provider(self._laser_options_provider),
        }

    def refresh_setup_options(self):
        """Re-publish the setup-derived dropdown options into the live form.

        ``reservoir_ids`` and ``lasers`` come from the loaded microscope
        setup, which can change *after* a design was loaded (picking or
        switching a setup in the toolbar). Publishing them into the form
        context refreshes the subscribed dropdowns in place, so they never
        show a previous setup's options — or none at all.
        """
        if self._form is None:
            return
        ctx = self._form.context
        ctx.set_options(
            'reservoir_ids', self._call_provider(self._reservoir_ids_provider))
        ctx.set_options(
            'lasers', self._call_provider(self._laser_options_provider))

    @staticmethod
    def _call_provider(provider):
        if provider is None:
            return []
        try:
            return list(provider() or [])
        except Exception:
            return []

    def _wire_save_dir_hint(self):
        """Show the resolved absolute save_dir beside the edit box.

        Files created during the run land in ``save_dir``, resolved against
        the working directory (which loading a design from disk moves to the
        design's folder). When the entered path is relative (or '.'), the
        actual destination is non-obvious, so show it; hide it for an
        already-absolute path.
        """
        self._save_dir_hint = None
        editor = self._form.field_editor("save_dir")
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
                hint.setText("→ {}".format(os.path.abspath(text or ".")))

        line.textChanged.connect(update)
        update(line.text())

    # --- actions ------------------------------------------------------

    def _on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load experiment design", "", "YAML files (*.yaml *.yml)"
        )
        if path:
            self.load_design_path(path)

    def _on_clear(self):
        reply = QMessageBox.question(
            self,
            "Clear experiment design",
            "Discard the current experiment design and reset the editor to "
            "an empty design? Unsaved changes will be lost.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._svc.clear_design()
            self._set_form({})

    def load_design_path(self, path):
        """Load + validate a design YAML and show it in the editor."""
        try:
            self._svc.load_experiment_design(path)
        except Exception as exc:
            QMessageBox.critical(
                self, "Invalid experiment design", "{}".format(exc)
            )
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
                self, "Cannot save — invalid design", "{}".format(exc)
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save experiment design", "", "YAML files (*.yaml *.yml)"
        )
        if not path:
            return
        with open(path, "w") as f:
            yaml.safe_dump(model.model_dump(by_alias=True), f, sort_keys=False)

    def _on_translate(self):
        try:
            self._svc.load_experiment_design(self._form.to_dict())
            self._svc.translate()
        except Exception as exc:
            QMessageBox.critical(self, "Translation failed", "{}".format(exc))
            return
        self._schedule_estimate()
        if self._on_translated is not None:
            self._on_translated()

    # --- live duration estimate --------------------------------------

    def _schedule_estimate(self):
        """(Re)arm the debounce timer that recomputes the estimate."""
        self._estimate_timer.start()

    def _connect_estimate_signals(self):
        """Hook every input in the form so edits re-trigger the estimate.

        Walks the current form's widget tree and connects the usual editing
        signals to :meth:`_schedule_estimate`. Each widget is hooked once
        (guarded by a dynamic property); add/remove-row buttons are hooked too
        so structural edits recompute, and :meth:`_recompute_estimate` re-walks
        afterwards to pick up any freshly created rows.
        """
        if self._form is None:
            return
        for w in [self._form] + self._form.findChildren(QWidget):
            if w.property("_estimate_hooked"):
                continue
            hooked = True
            if isinstance(w, QAbstractSpinBox):
                w.valueChanged.connect(self._schedule_estimate)
            elif isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self._schedule_estimate)
                w.editTextChanged.connect(self._schedule_estimate)
            elif isinstance(w, QLineEdit):
                w.textChanged.connect(self._schedule_estimate)
            elif isinstance(w, QCheckBox):
                w.toggled.connect(self._schedule_estimate)
            elif isinstance(w, QAbstractButton):
                # Add/remove-row buttons change the design's structure.
                w.clicked.connect(self._schedule_estimate)
            else:
                hooked = False
            if hooked:
                w.setProperty("_estimate_hooked", True)

    def _on_preview_toggled(self, checked):
        self.preview_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        self.preview_text.setVisible(checked)

    def _recompute_estimate(self):
        """Compile the current design and show run time + volumes + preview.

        Builds the Run Sequence from the in-editor design without committing
        it (so it reflects unsaved edits). While the design is incomplete or
        invalid the build raises; that is expected mid-edit, so we just note
        it in the label instead of interrupting with a dialog.
        """
        from PycroFlow.protocols import ProtocolBuilder
        from PycroFlow.protocols.timing import (
            estimate_total_duration,
            estimate_volumes,
            format_duration,
            format_volume,
        )
        from PycroFlow.schemas import validate_experiment_design

        # New list/dict rows may have appeared since the last hook pass.
        self._connect_estimate_signals()
        try:
            design = validate_experiment_design(
                self._form.to_dict()
            ).model_dump(by_alias=True)
            protocol = ProtocolBuilder().build_protocol(design)
            total = estimate_total_duration(protocol)
            volumes = estimate_volumes(protocol)
        except Exception:
            self.estimate_label.setText(
                "Estimated: — (design incomplete)"
            )
            self.preview_text.setPlainText(
                "The sequence preview appears once the design compiles."
            )
            return
        self.estimate_label.setText(
            "Estimated: ~{}  ·  {} reagents".format(
                format_duration(total),
                format_volume(volumes["total_injected"]),
            )
        )
        names = (
            (design.get("fluid") or {}).get("settings") or {}
        ).get("reservoir_names") or {}
        self.preview_text.setPlainText(
            self._format_preview(protocol, volumes, names)
        )

    @staticmethod
    def _format_preview(protocol, volumes, reservoir_names):
        """Build the folded preview text: volumes then the event sequence."""
        from PycroFlow.protocols.timing import format_volume
        from PycroFlow.protocols.describe import describe_protocol

        lines = ["Volumes required:"]
        per = volumes["per_reservoir"]
        for rid in sorted(per, key=lambda r: (r is None, r)):
            name = reservoir_names.get(rid) or reservoir_names.get(
                str(rid)
            ) or "reservoir {}".format(rid)
            lines.append("  {}: {}".format(name, format_volume(per[rid])))
        lines.append(
            "  Total into sample: {}   ·   Waste extracted: {}".format(
                format_volume(volumes["total_injected"]),
                format_volume(volumes["total_waste"]),
            )
        )
        lines.append("")
        lines.append("Sequence of events:")
        steps = describe_protocol(protocol, reservoir_names)
        for i, step in enumerate(steps, 1):
            lines.append("  {:>2}. {}".format(i, step))
        return "\n".join(lines)
