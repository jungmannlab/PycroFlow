"""Clean-tubings options dialog.

A modal :class:`QDialog` shown when the user triggers "Clean tubings" from the
Fluid tab. It lets the operator pick, per cleaning run:

* **which reservoirs to clean** — a checkable row per wired reservoir (the ones
  used in the loaded run are pre-checked; unused ones are listed but off);
* **how much to pump out of each** before cleaning — *Default* (the design's
  single pre-empty volume), *Calculated* (``loaded − injected``, from an
  editable per-reservoir loaded volume), or a *Custom* value;
* **which cleaning liquids** to use (all are on by default).

The dialog is pure UI over the plan dict from
:meth:`SystemService.cleaning_plan`; :meth:`selections` returns the keyword
arguments for :meth:`SystemService.clean_tubings`.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_MODE_DEFAULT = "Default"
_MODE_CALCULATED = "Calculated"
_MODE_CUSTOM = "Custom"

# Reservoir-table columns.
_COL_CLEAN = 0
_COL_NAME = 1
_COL_INJECTED = 2
_COL_MODE = 3
_COL_INPUT = 4
_COL_PUMPOUT = 5


def _spin():
    box = QDoubleSpinBox()
    box.setRange(0, 1_000_000)
    box.setDecimals(0)
    box.setSuffix(" µl")
    return box


class CleaningDialog(QDialog):
    """Per-reservoir options for the tubing-cleaning procedure."""

    def __init__(self, plan, require_confirm=True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clean tubings")
        self._plan = plan or {}
        self._default_pre_empty = self._plan.get("default_pre_empty")
        self._default_load = self._plan.get("default_load")
        # Per-row widgets, keyed by table row: the mode combo and input spin.
        self._rows = []
        self._liquid_boxes = []
        self._build_ui(require_confirm)
        self._recompute()

    # -- construction ---------------------------------------------------

    def _build_ui(self, require_confirm):
        outer = QVBoxLayout(self)

        if require_confirm:
            note = QLabel(
                "Make sure the input and output needles sit in the same "
                "container (fluidly connected) and the cleaning reservoirs are "
                "connected to their tanks."
            )
            note.setWordWrap(True)
            note.setStyleSheet("color: gray;")
            outer.addWidget(note)

        outer.addWidget(QLabel("Reservoirs to clean:"))
        outer.addWidget(self._build_reservoir_table())
        outer.addWidget(
            self._hint(
                "Pump-out: Default uses the design's pre-empty volume; "
                "Calculated is loaded − injected (edit the loaded volume); "
                "Custom is the value you enter. 0 skips the pre-empty."
            )
        )

        liquids = self._build_liquids_group()
        if liquids is not None:
            outer.addWidget(liquids)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Clean"
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        outer.addWidget(self._buttons)

        # Connected only now the buttons exist: toggling a "Clean" checkbox
        # re-evaluates the OK state. (Done last so item edits during table
        # construction don't fire _recompute before the UI is complete.)
        self._table.itemChanged.connect(self._recompute)

    @staticmethod
    def _hint(text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: gray;")
        return label

    def _build_reservoir_table(self):
        reservoirs = self._plan.get("reservoirs", []) or []
        table = QTableWidget(len(reservoirs), 6)
        table.setHorizontalHeaderLabels(
            ["Clean", "Reservoir", "Injected", "Mode", "Value", "Pump-out"]
        )
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)

        # Pre-check the reservoirs the loaded run used; but if the run marks
        # none (e.g. no run sequence loaded yet), pre-check all so the dialog
        # never opens with an empty selection.
        any_used = any(res.get("used") for res in reservoirs)

        for row, res in enumerate(reservoirs):
            # Clean? checkbox — pre-checked for reservoirs used in the run.
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            )
            precheck = res.get("used") if any_used else True
            check.setCheckState(
                Qt.CheckState.Checked if precheck else Qt.CheckState.Unchecked
            )
            table.setItem(row, _COL_CLEAN, check)

            name = res.get("name")
            if name:
                label = "{} ({})".format(name, res.get("id"))
            else:
                label = "Reservoir {}".format(res.get("id"))
            table.setItem(row, _COL_NAME, self._readonly_item(label))

            injected = res.get("injected") or 0
            table.setItem(
                row,
                _COL_INJECTED,
                self._readonly_item("{:.0f} µl".format(injected)),
            )

            mode = QComboBox()
            mode.addItems([_MODE_DEFAULT, _MODE_CALCULATED, _MODE_CUSTOM])
            table.setCellWidget(row, _COL_MODE, mode)

            spin = _spin()
            table.setCellWidget(row, _COL_INPUT, self._center(spin))

            pumpout = self._readonly_item("")
            table.setItem(row, _COL_PUMPOUT, pumpout)

            mode.currentTextChanged.connect(self._recompute)
            spin.valueChanged.connect(self._recompute)
            self._rows.append(
                {
                    "res": res,
                    "check": check,
                    "mode": mode,
                    "spin": spin,
                    "pumpout": pumpout,
                }
            )

        table.resizeColumnsToContents()
        self._table = table
        return table

    def _build_liquids_group(self):
        rounds = self._plan.get("cleaning_rounds", []) or []
        if not rounds:
            return None
        box = QGroupBox("Cleaning liquids")
        lay = QVBoxLayout(box)
        for i, rnd in enumerate(rounds, start=1):
            name = rnd.get("name") or "Reservoir {}".format(rnd.get("id"))
            cb = QCheckBox("Round {}: {}".format(i, name))
            cb.setChecked(True)
            cb.stateChanged.connect(self._recompute)
            lay.addWidget(cb)
            self._liquid_boxes.append((cb, rnd))
        return box

    @staticmethod
    def _readonly_item(text):
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        return item

    @staticmethod
    def _center(widget):
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.addWidget(widget)
        return holder

    # -- live state -----------------------------------------------------

    def _pumpout_for(self, row):
        """Computed pump-out volume (µl) for a reservoir row."""
        mode = row["mode"].currentText()
        if mode == _MODE_DEFAULT:
            return float(self._default_pre_empty or 0)
        if mode == _MODE_CALCULATED:
            loaded = row["spin"].value()
            injected = row["res"].get("injected") or 0
            return max(0.0, loaded - injected)
        return float(row["spin"].value())

    def _recompute(self, *args):
        """Refresh the per-row input enablement, computed pump-out, and the
        OK button's enabled state."""
        for row in self._rows:
            mode = row["mode"].currentText()
            spin = row["spin"]
            if mode == _MODE_DEFAULT:
                spin.setEnabled(False)
            elif mode == _MODE_CALCULATED:
                spin.setEnabled(True)
                spin.setToolTip("Volume loaded into this reservoir")
                # Prefill the loaded volume from the design default once.
                if (
                    not spin.property("_touched")
                    and self._default_load is not None
                ):
                    spin.setValue(float(self._default_load))
                    spin.setProperty("_touched", True)
            else:  # custom
                spin.setEnabled(True)
                spin.setToolTip("Volume to pump out of this reservoir")
            row["pumpout"].setText("{:.0f}".format(self._pumpout_for(row)))

        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(self._is_valid())

    def _checked_rows(self):
        return [
            row
            for row in self._rows
            if row["check"].checkState() == Qt.CheckState.Checked
        ]

    def _checked_liquids(self):
        return [rnd for cb, rnd in self._liquid_boxes if cb.isChecked()]

    def _is_valid(self):
        rows = self._checked_rows()
        if not rows:
            return False
        # When cleaning liquids are configured, a "Clean" run must use at
        # least one — otherwise it would silently pre-empty only and clean
        # nothing. With no liquids configured at all, allow a pre-empty-only
        # run (the only possible action).
        if self._liquid_boxes:
            return bool(self._checked_liquids())
        return any(self._pumpout_for(r) > 0 for r in rows)

    # -- result ---------------------------------------------------------

    def selections(self):
        """Return the ``clean_tubings`` keyword arguments for the choices.

        Returns
        -------
        dict
            ``{'res_to_clean': [ids], 'cleaning_reservoirs': [entries],
            'pre_empty_volumes': {id: µl}}``.
        """
        rows = self._checked_rows()
        res_to_clean = [row["res"]["id"] for row in rows]
        pre_empty = {row["res"]["id"]: self._pumpout_for(row) for row in rows}
        cleaning_reservoirs = [rnd["entry"] for rnd in self._checked_liquids()]
        return {
            "res_to_clean": res_to_clean,
            "cleaning_reservoirs": cleaning_reservoirs,
            "pre_empty_volumes": pre_empty,
        }
