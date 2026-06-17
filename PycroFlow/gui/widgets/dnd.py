"""Drag & drop helper for loading YAML files onto a widget.

Mix into a ``QWidget`` and call :meth:`enable_yaml_drop` in ``__init__``;
implement :meth:`on_yaml_dropped` to handle the dropped path. Only a single
``.yaml`` / ``.yml`` file is accepted.
"""


class YamlDropMixin:
    def enable_yaml_drop(self):
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):  # noqa: N802 (Qt naming)
        if self._yaml_path(event) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802 (Qt naming)
        path = self._yaml_path(event)
        if path is not None:
            self.on_yaml_dropped(path)
            event.acceptProposedAction()

    @staticmethod
    def _yaml_path(event):
        md = event.mimeData()
        if not md.hasUrls():
            return None
        urls = md.urls()
        if len(urls) != 1:
            return None
        path = urls[0].toLocalFile()
        if path.lower().endswith(('.yaml', '.yml')):
            return path
        return None

    def on_yaml_dropped(self, path):
        raise NotImplementedError
