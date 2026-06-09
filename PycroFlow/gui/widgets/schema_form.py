"""Schema-driven structured editor for pydantic models.

Renders an editable form from a pydantic v2 model (e.g.
:class:`PycroFlow.schemas.experiment_design.ExperimentDesign`) by introspecting
``model_fields`` — scalars become typed widgets, nested ``BaseModel`` fields
recurse, ``list[Model]`` / ``dict[str, Model]`` get add/remove controls, and a
union-of-models field (the ``experiment`` block) gets a type selector that
swaps the sub-form.

``to_dict()`` returns an alias-keyed dict (so hyphenated keys like
``target-rounds`` round-trip); ``to_model()`` validates it against the model
and raises the schema's validation error on bad input.
"""
import ast
import typing
from typing import Union, get_args, get_origin

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QCheckBox, QComboBox, QPushButton, QGridLayout,
)

from pydantic import BaseModel
from pydantic_core import PydanticUndefined


# --- type helpers --------------------------------------------------------

_NONE = type(None)


def _scalar_default(field_info):
    """Return a scalar field's default, or None when it is required."""
    default = field_info.get_default(call_default_factory=True)
    return None if default is PydanticUndefined else default


def _unwrap_optional(ann):
    """Return (inner_annotation, is_optional)."""
    if get_origin(ann) is Union:
        args = [a for a in get_args(ann) if a is not _NONE]
        is_opt = len(args) != len(get_args(ann))
        if len(args) == 1:
            return args[0], is_opt
        return Union[tuple(args)], is_opt
    return ann, False


def _is_model(ann):
    return isinstance(ann, type) and issubclass(ann, BaseModel)


def _is_list(ann):
    return get_origin(ann) in (list, typing.List)


def _is_dict(ann):
    return get_origin(ann) in (dict, typing.Dict)


def _union_models(ann):
    """If ``ann`` is a Union of BaseModels, return the list, else None."""
    if get_origin(ann) is Union:
        args = [a for a in get_args(ann) if a is not _NONE]
        if args and all(_is_model(a) for a in args):
            return args
    return None


def _coerce_scalar(text, ann):
    """Coerce a string to the field's scalar type (best effort)."""
    text = text.strip()
    if ann is int:
        return int(text)
    if ann is float:
        return float(text)
    if ann is str:
        return text
    # bool handled by checkbox; unknown -> try a python literal, else str
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def _variant_label(model_cls):
    """The discriminator literal of a model with a ``type`` field."""
    fi = model_cls.model_fields.get('type')
    if fi is not None:
        args = get_args(fi.annotation)
        if args:
            return args[0]
    return model_cls.__name__


# --- field editors -------------------------------------------------------

class _ScalarEditor(QWidget):
    def __init__(self, ann, optional, value, parent=None):
        super().__init__(parent)
        self.is_block = False
        self._ann = ann
        self._optional = optional
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if ann is bool:
            self._w = QCheckBox()
            self._w.setChecked(bool(value))
        else:
            self._w = QLineEdit()
            if value is not None:
                self._w.setText(str(value))
        lay.addWidget(self._w)

    def get_value(self):
        if isinstance(self._w, QCheckBox):
            return self._w.isChecked()
        text = self._w.text().strip()
        if text == '':
            # Empty -> None; to_dict drops it so pydantic applies the field
            # default (or raises a clear 'required' error).
            return None
        return _coerce_scalar(text, self._ann)


class _ListScalarEditor(QWidget):
    def __init__(self, item_ann, value, parent=None):
        super().__init__(parent)
        self.is_block = False
        self._item_ann = item_ann
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._w = QLineEdit()
        if value:
            self._w.setText(', '.join(str(v) for v in value))
        self._w.setPlaceholderText("comma-separated")
        lay.addWidget(self._w)

    def get_value(self):
        text = self._w.text().strip()
        if not text:
            return []
        items = [p.strip() for p in text.split(',') if p.strip()]
        return [_coerce_scalar(p, self._item_ann) for p in items]


class _MappingEditor(QGroupBox):
    """dict[scalar, scalar] as add/remove key/value rows."""

    def __init__(self, key_ann, val_ann, value, title, parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self._key_ann = key_ann
        self._val_ann = val_ann
        self._rows = []
        self._lay = QVBoxLayout(self)
        self._grid = QGridLayout()
        self._lay.addLayout(self._grid)
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add_row('', ''))
        self._lay.addWidget(add)
        for k, v in (value or {}).items():
            self._add_row(k, v)

    def _add_row(self, k, v):
        r = len(self._rows)
        key_edit = QLineEdit(str(k))
        val_edit = QLineEdit(str(v))
        rm = QPushButton("✕")
        rm.setFixedWidth(28)
        self._grid.addWidget(key_edit, r, 0)
        self._grid.addWidget(val_edit, r, 1)
        self._grid.addWidget(rm, r, 2)
        row = (key_edit, val_edit, rm)
        self._rows.append(row)
        rm.clicked.connect(lambda: self._remove(row))

    def _remove(self, row):
        for w in row:
            w.setParent(None)
        self._rows.remove(row)

    def get_value(self):
        out = {}
        for key_edit, val_edit, _ in self._rows:
            k = key_edit.text().strip()
            if k == '':
                continue
            out[_coerce_scalar(k, self._key_ann)] = _coerce_scalar(
                val_edit.text(), self._val_ann)
        return out


class _ListModelEditor(QGroupBox):
    """list[Model] as add/remove sub-forms."""

    def __init__(self, item_cls, value, title, parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self._item_cls = item_cls
        self._items = []
        self._lay = QVBoxLayout(self)
        self._items_lay = QVBoxLayout()
        self._lay.addLayout(self._items_lay)
        add = QPushButton("Add item")
        add.clicked.connect(lambda: self._add_item({}))
        self._lay.addWidget(add)
        for item in (value or []):
            self._add_item(item)

    def _add_item(self, data):
        row = QWidget()
        rlay = QHBoxLayout(row)
        rlay.setContentsMargins(0, 0, 0, 0)
        form = SchemaForm(self._item_cls, data)
        rlay.addWidget(form, 1)
        rm = QPushButton("✕")
        rm.setFixedWidth(28)
        rlay.addWidget(rm)
        self._items_lay.addWidget(row)
        entry = (row, form)
        self._items.append(entry)
        rm.clicked.connect(lambda: self._remove(entry))

    def _remove(self, entry):
        entry[0].setParent(None)
        self._items.remove(entry)

    def get_value(self):
        return [form.to_dict() for _, form in self._items]


class _DictModelEditor(QGroupBox):
    """dict[str, Model] as add/remove keyed sub-forms."""

    def __init__(self, item_cls, value, title, parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self._item_cls = item_cls
        self._items = []
        self._lay = QVBoxLayout(self)
        self._items_lay = QVBoxLayout()
        self._lay.addLayout(self._items_lay)
        add = QPushButton("Add entry")
        add.clicked.connect(lambda: self._add_item('', {}))
        self._lay.addWidget(add)
        for k, v in (value or {}).items():
            self._add_item(k, v)

    def _add_item(self, key, data):
        row = QWidget()
        rlay = QVBoxLayout(row)
        rlay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel("Key:"))
        key_edit = QLineEdit(str(key))
        head.addWidget(key_edit, 1)
        rm = QPushButton("✕")
        rm.setFixedWidth(28)
        head.addWidget(rm)
        rlay.addLayout(head)
        form = SchemaForm(self._item_cls, data)
        rlay.addWidget(form)
        self._items_lay.addWidget(row)
        entry = (row, key_edit, form)
        self._items.append(entry)
        rm.clicked.connect(lambda: self._remove(entry))

    def _remove(self, entry):
        entry[0].setParent(None)
        self._items.remove(entry)

    def get_value(self):
        out = {}
        for _, key_edit, form in self._items:
            k = key_edit.text().strip()
            if k:
                out[k] = form.to_dict()
        return out


class _UnionModelEditor(QGroupBox):
    """Union of models (e.g. experiment): a type selector + a sub-form."""

    def __init__(self, variants, value, title, parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self._by_label = {_variant_label(v): v for v in variants}
        self._lay = QVBoxLayout(self)
        head = QHBoxLayout()
        head.addWidget(QLabel("type:"))
        self._combo = QComboBox()
        self._combo.addItems(list(self._by_label))
        head.addWidget(self._combo, 1)
        self._lay.addLayout(head)
        self._holder = QWidget()
        self._holder_lay = QVBoxLayout(self._holder)
        self._holder_lay.setContentsMargins(0, 0, 0, 0)
        self._lay.addWidget(self._holder)
        self._form = None

        initial = (value or {}).get('type')
        if initial in self._by_label:
            self._combo.setCurrentText(initial)
        self._rebuild(value or {})
        self._combo.currentTextChanged.connect(lambda _: self._rebuild({}))

    def _rebuild(self, value):
        if self._form is not None:
            self._form.setParent(None)
        cls = self._by_label[self._combo.currentText()]
        self._form = SchemaForm(cls, value)
        self._holder_lay.addWidget(self._form)

    def get_value(self):
        data = self._form.to_dict()
        data['type'] = self._combo.currentText()
        return data


class _LiteralEditor(QWidget):
    """Fallback for unmodeled / union-of-scalar fields: a python literal."""

    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.is_block = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._w = QLineEdit()
        if value is not None:
            self._w.setText(repr(value) if not isinstance(value, str)
                            else value)
        lay.addWidget(self._w)

    def get_value(self):
        text = self._w.text().strip()
        if text == '':
            return None
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return text


def _make_editor(ann, optional, value, label):
    """Build the right editor widget for a field annotation."""
    union_ms = _union_models(ann)
    if union_ms is not None:
        return _UnionModelEditor(union_ms, value, label)
    if _is_model(ann):
        return _ModelEditor(ann, value, label)
    if _is_list(ann):
        (item_ann,) = get_args(ann) or (str,)
        if _is_model(item_ann):
            return _ListModelEditor(item_ann, value, label)
        return _ListScalarEditor(item_ann, value)
    if _is_dict(ann):
        kt, vt = (get_args(ann) + (str, str))[:2]
        if _is_model(vt):
            return _DictModelEditor(vt, value, label)
        return _MappingEditor(kt, vt, value, label)
    if ann in (int, float, str, bool):
        return _ScalarEditor(ann, optional, value)
    return _LiteralEditor(value)


class _ModelEditor(QGroupBox):
    """A nested BaseModel rendered as a titled sub-form."""

    def __init__(self, model_cls, value, title, parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self.is_optional = False
        lay = QVBoxLayout(self)
        self._form = SchemaForm(model_cls, value or {})
        lay.addWidget(self._form)

    def get_value(self):
        return self._form.to_dict()


class SchemaForm(QWidget):
    """Editable form generated from a pydantic model class."""

    def __init__(self, model_cls, data=None, parent=None):
        super().__init__(parent)
        self._model_cls = model_cls
        self._editors = {}   # alias -> editor
        data = data or {}
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        for name, fi in model_cls.model_fields.items():
            alias = fi.alias or name
            ann, optional = _unwrap_optional(fi.annotation)
            if alias in data or name in data:
                value = data.get(alias, data.get(name))
            elif ann in (int, float, str, bool):
                # Seed scalar fields with their default; nested models / lists
                # / dicts seed themselves recursively (so empty -> defaults).
                value = _scalar_default(fi)
            else:
                value = None
            editor = _make_editor(ann, optional, value, alias)
            self._editors[alias] = editor
            if getattr(editor, 'is_block', False):
                form.addRow(editor)
            else:
                form.addRow(alias, editor)

    def to_dict(self):
        """Return the edited values as an alias-keyed dict."""
        out = {}
        for alias, editor in self._editors.items():
            val = editor.get_value()
            if val is None:
                continue
            out[alias] = val
        return out

    def to_model(self):
        """Validate the form against the model, returning the parsed model."""
        return self._model_cls.model_validate(self.to_dict())
