"""First-run setup wizard (Qt) — a thin shell over :mod:`PycroFlow.setup_core`.

Guides a fresh install through the two things a committed setup file used to
provide: **cross-project connectivity** (the gitignored ``.env`` — registry
URL/token, monet config paths, spill port) and a **user-local microscope setup**
(cloned from a shipped template and named for this rig). All the logic lives in
:mod:`PycroFlow.setup_core`; the pages here only collect input and call
``commit()``.

Launched automatically on first run (``setup_core.first_run()``) and re-runnable
from the toolbar's "Setup Wizard…" action. The deeper hardware topology (the
full reservoir manifold + tubing) is cloned from the template as-is — edit it in
the saved YAML (a schema editor for it is future work); the page says so.
"""

from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from PycroFlow import configs, setup_core


class _IntroPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("PycroFlow setup")
        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(
                "This wizard configures this machine:\n\n"
                "  • cross-project connectivity (picasso-registry, monet, "
                "spill sensor) — written to a private .env;\n"
                "  • a microscope setup for this rig — saved as a local YAML "
                "(setups are no longer shipped with the package).\n\n"
                "Nothing is sent anywhere; you can re-run this from the "
                "toolbar's “Setup Wizard…” button at any time."
            )
        )
        lay.addWidget(
            QLabel(
                "Files will be written to:\n  .env:    {}\n  setups:  {}".format(
                    setup_core.env_path(), configs.user_setups_dir()
                )
            )
        )


class _ConnectivityPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Connectivity")
        self.setSubTitle(
            "Optional — leave blank to skip. Values are stored in a private, "
            "gitignored .env (never committed)."
        )
        form = QFormLayout(self)
        env = setup_core.read_env()
        self.registry_url = QLineEdit(env.get(setup_core.ENV_REGISTRY_URL, ""))
        self.registry_token = QLineEdit(
            env.get(setup_core.ENV_REGISTRY_TOKEN, "")
        )
        self.registry_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.monet_paths = QLineEdit(
            env.get(setup_core.ENV_MONET_CONFIG_PATHS, "")
        )
        self.monet_token = QLineEdit(env.get(setup_core.ENV_MONET_TOKEN, ""))
        self.monet_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.spill_port = QLineEdit(env.get(setup_core.ENV_SPILL_PORT, ""))
        # Directories — default to the per-user location, but editable to a
        # lab-shared network/pool path so setups + data live centrally.
        self.setups_dir = QLineEdit(
            env.get(setup_core.ENV_SETUPS_DIR, "")
            or str(configs.user_setups_dir())
        )
        self.save_dir = QLineEdit(
            env.get(setup_core.ENV_SAVE_DIR, "")
            or setup_core.default_save_dir()
        )
        form.addRow("picasso-registry URL", self.registry_url)
        form.addRow("registry bearer token", self.registry_token)
        form.addRow("monet config path(s)", self.monet_paths)
        form.addRow("monet token", self.monet_token)
        form.addRow("spill-sensor port", self.spill_port)
        form.addRow("setups folder", self.setups_dir)
        form.addRow("data (acquisition) folder", self.save_dir)

        row = QHBoxLayout()
        self._test_btn = QPushButton("Test registry")
        self._test_result = QLabel("")
        self._test_btn.clicked.connect(self._on_test)
        row.addWidget(self._test_btn)
        row.addWidget(self._test_result)
        form.addRow(row)

        monet_row = QHBoxLayout()
        self._monet_test_btn = QPushButton("Test monet")
        self._monet_test_result = QLabel("")
        self._monet_test_btn.clicked.connect(self._on_test_monet)
        monet_row.addWidget(self._monet_test_btn)
        monet_row.addWidget(self._monet_test_result)
        form.addRow(monet_row)

    def _on_test(self):
        v = setup_core.check_registry(
            self.registry_url.text().strip(),
            self.registry_token.text().strip() or None,
        )
        self._test_result.setText(("✓ " if v["ok"] else "✗ ") + v["detail"])

    def _on_test_monet(self):
        # The monet server URL lives in a monet config's `database` field, so
        # check_monet auto-discovers it from the loaded configs; we pass the
        # token being configured here.
        v = setup_core.check_monet(
            token=self.monet_token.text().strip() or None,
        )
        self._monet_test_result.setText(
            ("✓ " if v["ok"] else "✗ ") + v["detail"]
        )


class _SetupPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Microscope setup")
        self.setSubTitle(
            "Clone a template and name it for this rig. The reservoir manifold "
            "and tubing are copied from the template — review them in the "
            "saved YAML afterwards."
        )
        form = QFormLayout(self)
        self.create = QCheckBox("Create a setup for this machine")
        self.create.setChecked(True)
        form.addRow(self.create)

        self.template = QComboBox()
        self.template.addItems(setup_core.template_names())
        _select(self.template, setup_core.DEFAULT_TEMPLATE)
        form.addRow("Clone from template", self.template)

        self.name = QLineEdit("")
        form.addRow("New setup name", self.name)

        self.emulated = QCheckBox("Emulated (no hardware)")
        form.addRow(self.emulated)

        self.com = QComboBox()
        self.com.setEditable(True)
        self.com.addItems(setup_core.enumerate_serial_ports())
        form.addRow("Syringe-pump COM port", self.com)

        self.monet_config = QLineEdit("")
        form.addRow("monet config key (lasers)", self.monet_config)

    def validatePage(self) -> bool:
        if not self.create.isChecked():
            return True
        name = self.name.text().strip()
        if not name:
            self._warn("Enter a name for the new setup.")
            return False
        if name in configs.list_setups():
            self._warn(
                "A setup named {!r} already exists — pick another name.".format(
                    name
                )
            )
            return False
        problems = setup_core.validate_setup(self._build())
        if problems:
            self._warn("Fix:\n  • " + "\n  • ".join(problems))
            return False
        return True

    def _build(self) -> dict:
        overrides = {
            "emulated": self.emulated.isChecked(),
            "illumination.config": self.monet_config.text().strip() or None,
        }
        if not self.emulated.isChecked():
            com = self.com.currentText().strip()
            # The Hamilton driver prefixes "COM" itself, so store the bare
            # number ('3'), not 'COM3' (pyserial's device name / what an
            # operator types).
            overrides["fluid.pumps.interface.COM"] = (
                setup_core.normalize_com_port(com) if com else None
            )
        return setup_core.clone_setup(
            self.template.currentText(),
            self.name.text().strip(),
            overrides,
        )

    def _warn(self, msg):
        from PyQt6.QtWidgets import QMessageBox

        QMessageBox.warning(self, "Setup", msg)


class _SummaryPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Summary")
        self._label = QLabel("")
        self._label.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addWidget(self._label)

    def initializePage(self):
        # Persist everything when the user reaches the final page.
        self._label.setText(self.wizard().commit())


class SetupWizard(QWizard):
    """The first-run / re-runnable setup wizard.

    ``commit()`` is the single persistence seam (also the headless test entry
    point): it writes the non-empty connectivity variables to ``.env`` and, when
    the setup page asked to create one, saves the cloned setup to the user dir.
    Returns a human-readable summary for the final page.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PycroFlow Setup")
        self.page_intro = _IntroPage()
        self.page_conn = _ConnectivityPage()
        self.page_setup = _SetupPage()
        self.page_summary = _SummaryPage()
        for page in (
            self.page_intro,
            self.page_conn,
            self.page_setup,
            self.page_summary,
        ):
            self.addPage(page)
        #: Set by commit() — the basename of a setup just created (or None), so
        #: the caller can select it in the toolbar combo.
        self.created_setup = None

    def commit(self) -> str:
        lines = []
        env_updates = {
            setup_core.ENV_REGISTRY_URL: self.page_conn.registry_url.text(),
            setup_core.ENV_REGISTRY_TOKEN: self.page_conn.registry_token.text(),
            setup_core.ENV_MONET_CONFIG_PATHS: self.page_conn.monet_paths.text(),
            setup_core.ENV_MONET_TOKEN: self.page_conn.monet_token.text(),
            setup_core.ENV_SPILL_PORT: self.page_conn.spill_port.text(),
            setup_core.ENV_SETUPS_DIR: self.page_conn.setups_dir.text(),
            setup_core.ENV_SAVE_DIR: self.page_conn.save_dir.text(),
        }
        env_updates = {
            k: v.strip() for k, v in env_updates.items() if v.strip()
        }
        # Each persistence step is guarded: commit() is called from the Qt
        # summary page's initializePage, so a raise here would break the wizard
        # UI — report the failure in the summary instead. The two writes are
        # independent (connectivity .env vs the setup YAML); one failing does
        # not abort the other.
        if env_updates:
            try:
                path = setup_core.write_env(env_updates)
                lines.append(
                    "Wrote {} connectivity variable(s) to {}".format(
                        len(env_updates), path
                    )
                )
            except Exception as exc:  # noqa: BLE001 - surfaced, not raised
                lines.append("Could not write .env: {}".format(exc))
            # Apply the directory choices to THIS process so the setup saved
            # below lands in the chosen setups folder (write_env only persists
            # to .env, which is otherwise read on the next launch).
            for key in (setup_core.ENV_SETUPS_DIR, setup_core.ENV_SAVE_DIR):
                if env_updates.get(key):
                    os.environ[key] = env_updates[key]
        else:
            lines.append("No connectivity variables set.")

        if self.page_setup.create.isChecked():
            try:
                data = self.page_setup._build()
                path = setup_core.save_setup(data["setup"], data)
                self.created_setup = data["setup"]
                # Record it as the default microscope so the GUI pre-selects it
                # on the next launch (PycroFlow has no CLI microscope arg).
                setup_core.write_env(
                    {setup_core.ENV_DEFAULT_SETUP: data["setup"]}
                )
                lines.append(
                    "Saved setup {!r} to {}".format(data["setup"], path)
                )
                lines.append(
                    "Review the reservoir manifold / tubing in that file "
                    "before a real run."
                )
            except Exception as exc:  # noqa: BLE001 - surfaced, not raised
                lines.append("Could not save the setup: {}".format(exc))
        else:
            lines.append("No setup created.")
        return "\n\n".join(lines)


def _select(combo: QComboBox, text: str) -> None:
    i = combo.findText(text)
    if i >= 0:
        combo.setCurrentIndex(i)


def run_setup_wizard(parent=None):
    """Open the wizard modally; return the created setup name (or None)."""
    wiz = SetupWizard(parent)
    wiz.exec()
    return wiz.created_setup
