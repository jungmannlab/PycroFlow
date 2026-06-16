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

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QCheckBox, QComboBox, QPushButton, QGridLayout,
)

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from PycroFlow.schemas.experiment_design import field_meta


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


# --- editor context (shared, observable dropdown options) ---------------

class FormContext:
    """Named dropdown option lists shared down a form tree, with live updates.

    Editors read options by key (``get``); option-providing editors push new
    values (``set_options``), which notifies subscribed dropdowns so they
    refresh in place. Quacks like a dict's ``get`` so a plain dict can be
    passed in (static, no live updates) and is wrapped transparently.
    """

    def __init__(self, options=None):
        self._options = {k: list(v) for k, v in (options or {}).items()}
        self._subs = {}   # key -> [callback]

    def get(self, key, default=None):
        return list(self._options.get(key, default if default is not None
                                      else []))

    def subscribe(self, key, callback):
        self._subs.setdefault(key, []).append(callback)

    def set_options(self, key, values):
        self._options[key] = list(values)
        alive = []
        for cb in self._subs.get(key, []):
            try:
                cb(list(values))
                alive.append(cb)
            except RuntimeError:
                # The subscribed widget was destroyed (e.g. a swapped union
                # variant); drop its stale subscription.
                pass
        self._subs[key] = alive


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
        self._lay = lay

    def line_edit(self):
        """The inner ``QLineEdit`` (None for a bool/checkbox field)."""
        return self._w if isinstance(self._w, QLineEdit) else None

    def add_suffix(self, widget):
        """Append a trailing widget (e.g. a hint label) after the editor."""
        self._lay.addWidget(widget)

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


class _ChoiceEditor(QWidget):
    """A dropdown for a string field with a fixed/contextual option set."""

    def __init__(self, options, value, allow_none, ann=str, parent=None):
        super().__init__(parent)
        self.is_block = False
        self._allow_none = allow_none
        self._ann = ann
        opts = [str(o) for o in options]
        # Always keep the current value selectable, even if not in the set.
        if value is not None and str(value) not in opts:
            opts.append(str(value))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._combo = QComboBox()
        if allow_none:
            self._combo.addItem('')   # blank -> None
        self._combo.addItems(opts)
        cur = '' if value is None else str(value)
        idx = self._combo.findText(cur)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        lay.addWidget(self._combo)
        self._lay = lay

    def add_suffix(self, widget):
        self._lay.addWidget(widget)

    def set_options(self, options):
        """Repopulate the dropdown (keeping the current selection).

        Subscribed to a :class:`FormContext` key for live updates (e.g. when
        the reservoir names change).
        """
        cur = self._combo.currentText()
        opts = [str(o) for o in options]
        if cur not in ('', *opts):
            opts.append(cur)
        self._combo.blockSignals(True)
        self._combo.clear()
        if self._allow_none:
            self._combo.addItem('')
        self._combo.addItems(opts)
        idx = self._combo.findText(cur)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)

    def get_value(self):
        text = self._combo.currentText()
        if text == '':
            return None   # to_dict drops it -> field default / required error
        return _coerce_scalar(text, self._ann)


class _ListChoiceEditor(QGroupBox):
    """list[scalar] as add/remove dropdown rows (each item a choice).

    Used for fields like Exchange ``imagers`` — one dropdown per round,
    chosen from the design's reservoir names, addable/removable like the
    SPH-RESI RESI-rounds.
    """

    def __init__(self, item_ann, value, title, choices_key=None,
                 static_options=None, allow_none=False, context=None,
                 row_label=None, parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self._item_ann = item_ann
        self._choices_key = choices_key
        self._static = static_options
        self._allow_none = allow_none
        self._ctx = context
        # Optional per-row label template, numbered 1..n (e.g.
        # 'imager round {}'); the labels renumber on add/remove.
        self._row_label = row_label
        self._items = []   # [(row_widget, _ChoiceEditor, label_or_None)]
        self._lay = QVBoxLayout(self)
        self._items_lay = QVBoxLayout()
        self._lay.addLayout(self._items_lay)
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add_item(None))
        self._lay.addWidget(add)
        for v in (value or []):
            self._add_item(v)

    def _options(self):
        if self._static is not None:
            return self._static
        if self._ctx is not None:
            return self._ctx.get(self._choices_key, [])
        return []

    def _add_item(self, val):
        row = QWidget()
        rlay = QHBoxLayout(row)
        rlay.setContentsMargins(0, 0, 0, 0)
        label = None
        if self._row_label:
            label = QLabel()
            rlay.addWidget(label)
        ed = _ChoiceEditor(
            self._options(), val, self._allow_none, self._item_ann)
        if (self._choices_key and self._ctx is not None
                and hasattr(self._ctx, 'subscribe')):
            self._ctx.subscribe(self._choices_key, ed.set_options)
        rlay.addWidget(ed, 1)
        rm = QPushButton("✕")
        rm.setFixedWidth(28)
        rlay.addWidget(rm)
        self._items_lay.addWidget(row)
        entry = (row, ed, label)
        self._items.append(entry)
        rm.clicked.connect(lambda: self._remove(entry))
        self._renumber()

    def _renumber(self):
        if not self._row_label:
            return
        for i, (_, _, label) in enumerate(self._items):
            if label is not None:
                label.setText(self._row_label.format(i + 1))

    def _remove(self, entry):
        entry[0].setParent(None)
        self._items.remove(entry)
        self._renumber()

    def get_value(self):
        out = []
        for _, ed, _ in self._items:
            v = ed.get_value()
            if v is not None and v != '':
                out.append(v)
        return out


class _MappingEditor(QGroupBox):
    """dict[scalar, scalar] as add/remove rows, with optional headers/combos.

    ``columns`` gives the two column headers in display order. By default the
    key is shown in column 0 and the value in column 1; ``display_value_first``
    swaps that (used for ``special_names``, stored name->id but shown id,
    name). ``key_choices`` / ``value_choices``, when given, render that side as
    a dropdown restricted to those options (e.g. the setup's reservoir ids).
    """

    def __init__(self, key_ann, val_ann, value, title, *, columns=None,
                 display_value_first=False, key_choices=None,
                 value_choices=None, provides=None, context=None,
                 parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self._key_ann = key_ann
        self._val_ann = val_ann
        self._dvf = display_value_first
        self._key_choices = key_choices
        self._value_choices = value_choices
        # When set, push this mapping's values to context[provides] on edit,
        # so dropdowns fed by those values (e.g. imager names) update live.
        self._provides = provides
        self._ctx = context
        self._rows = []
        self._next_row = 0   # monotonic grid row, so removals never collide
        self._lay = QVBoxLayout(self)
        self._grid = QGridLayout()
        self._lay.addLayout(self._grid)
        if columns:
            for c, head in enumerate(columns):
                lbl = QLabel("<b>{}</b>".format(head))
                self._grid.addWidget(lbl, self._next_row, c)
            self._next_row += 1
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add_row('', ''))
        self._lay.addWidget(add)
        for k, v in (value or {}).items():
            self._add_row(k, v)

    @staticmethod
    def _make_cell(val, choices):
        if choices:   # non-empty -> dropdown; empty/None -> free text
            opts = [str(c) for c in choices]
            if val not in (None, '') and str(val) not in opts:
                opts.append(str(val))
            combo = QComboBox()
            combo.addItems(opts)
            idx = combo.findText('' if val in (None, '') else str(val))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            return combo
        return QLineEdit('' if val in (None, '') else str(val))

    @staticmethod
    def _cell_text(w):
        return (w.currentText() if isinstance(w, QComboBox)
                else w.text()).strip()

    def _add_row(self, k, v):
        r = self._next_row
        self._next_row += 1
        key_w = self._make_cell(k, self._key_choices)
        val_w = self._make_cell(v, self._value_choices)
        rm = QPushButton("✕")
        rm.setFixedWidth(28)
        # Column 0/1 placement honours display_value_first.
        left, right = (val_w, key_w) if self._dvf else (key_w, val_w)
        self._grid.addWidget(left, r, 0)
        self._grid.addWidget(right, r, 1)
        self._grid.addWidget(rm, r, 2)
        row = (key_w, val_w, rm)
        self._rows.append(row)
        rm.clicked.connect(lambda: self._remove(row))
        # Live-publish the values (e.g. reservoir names) as they change.
        if self._provides:
            self._connect_change(val_w)
            self._notify()

    def _connect_change(self, w):
        if isinstance(w, QComboBox):
            w.currentTextChanged.connect(lambda _=None: self._notify())
        else:
            w.textChanged.connect(lambda _=None: self._notify())

    def _notify(self):
        if self._provides and self._ctx is not None:
            vals = [self._cell_text(val_w) for _, val_w, _ in self._rows]
            self._ctx.set_options(
                self._provides, [v for v in vals if v])

    def _remove(self, row):
        for w in row:
            w.setParent(None)
        self._rows.remove(row)
        if self._provides:
            self._notify()

    def get_value(self):
        out = {}
        for key_w, val_w, _ in self._rows:
            k = self._cell_text(key_w)
            if k == '':
                continue
            out[_coerce_scalar(k, self._key_ann)] = _coerce_scalar(
                self._cell_text(val_w), self._val_ann)
        return out


class _ListModelEditor(QGroupBox):
    """list[Model] as add/remove sub-forms."""

    def __init__(self, item_cls, value, title, context=None, parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self._item_cls = item_cls
        self._context = context or {}
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
        form = SchemaForm(self._item_cls, data, context=self._context)
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

    def __init__(self, item_cls, value, title, context=None, parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self._item_cls = item_cls
        self._context = context or {}
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
        form = SchemaForm(self._item_cls, data, context=self._context)
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

    def __init__(self, variants, value, title, context=None, parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self._context = context or {}
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
        # The variant 'type' is the selector above, so skip it in the sub-form.
        self._form = SchemaForm(
            cls, value, context=self._context, skip_fields={'type'})
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


def _has_choices(meta):
    return 'choices' in meta or 'choices_from' in meta


def _make_editor(ann, optional, value, label, meta, context):
    """Build the right editor widget for a field annotation + metadata."""
    union_ms = _union_models(ann)
    if union_ms is not None:
        return _UnionModelEditor(union_ms, value, label, context)
    if _is_model(ann):
        return _ModelEditor(ann, value, label, context)
    if _is_list(ann):
        (item_ann,) = get_args(ann) or (str,)
        if _is_model(item_ann):
            return _ListModelEditor(item_ann, value, label, context)
        if _has_choices(meta):
            # list of dropdowns (add/remove rows), e.g. Exchange imagers.
            return _ListChoiceEditor(
                item_ann, value, meta.get('title', label),
                choices_key=meta.get('choices_from'),
                static_options=meta.get('choices'),
                allow_none=meta.get('allow_none', False), context=context,
                row_label=meta.get('row_label'))
        return _ListScalarEditor(item_ann, value)
    if _is_dict(ann):
        kt, vt = (get_args(ann) + (str, str))[:2]
        if _is_model(vt):
            return _DictModelEditor(vt, value, label, context)
        return _MappingEditor(
            kt, vt, value, label,
            columns=meta.get('columns'),
            display_value_first=meta.get('display_value_first', False),
            key_choices=(context.get(meta['key_choices_from'])
                         if 'key_choices_from' in meta else None),
            value_choices=(context.get(meta['value_choices_from'])
                           if 'value_choices_from' in meta else None),
            provides=meta.get('provides'), context=context)
    # A scalar with a declared option set -> a single dropdown.
    if _has_choices(meta):
        key = meta.get('choices_from')
        opts = meta.get('choices')
        if opts is None:
            opts = context.get(key, [])
        editor = _ChoiceEditor(opts, value, meta.get('allow_none', False), ann)
        if key is not None and hasattr(context, 'subscribe'):
            context.subscribe(key, editor.set_options)
        return editor
    if ann in (int, float, str, bool):
        return _ScalarEditor(ann, optional, value)
    return _LiteralEditor(value)


class _ModelEditor(QGroupBox):
    """A nested BaseModel rendered as a titled, collapsible sub-form.

    The group box is checkable purely as a collapse toggle: unchecking hides
    the body to save space. It never affects the data — :meth:`get_value`
    always reads the (still-populated) sub-form back.
    """

    def __init__(self, model_cls, value, title, context=None, parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self.is_optional = False
        self.setCheckable(True)
        self.setChecked(True)
        self.setToolTip("Click the section title's checkbox to collapse it.")
        lay = QVBoxLayout(self)
        self._form = SchemaForm(model_cls, value or {}, context=context)
        lay.addWidget(self._form)
        # Collapse/expand the body with the check state (data is unaffected).
        self.toggled.connect(self._form.setVisible)

    def get_value(self):
        return self._form.to_dict()


class SchemaForm(QWidget):
    """Editable form generated from a pydantic model class."""

    def __init__(self, model_cls, data=None, parent=None, *, context=None,
                 skip_fields=None):
        super().__init__(parent)
        self._model_cls = model_cls
        # context: dynamic dropdown options shared down the form tree, keyed by
        # name (e.g. 'reservoir_names', 'reservoir_ids'). A plain dict is
        # wrapped into a FormContext (the same instance is threaded into every
        # nested form, so live updates propagate). skip_fields: field
        # names/aliases to omit (e.g. a union's 'type', shown by the selector).
        self._context = (context if isinstance(context, FormContext)
                         else FormContext(context or {}))
        self._skip = set(skip_fields or ())
        self._editors = {}   # alias -> editor
        data = data or {}
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        # Left-align the field labels (Qt right-aligns them by default on
        # some platforms) so scalar rows line up with the left-aligned
        # group/list/table editors below.
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        for name, fi in model_cls.model_fields.items():
            alias = fi.alias or name
            if name in self._skip or alias in self._skip:
                continue
            ann, optional = _unwrap_optional(fi.annotation)
            meta = field_meta(fi)
            if alias in data or name in data:
                value = data.get(alias, data.get(name))
            elif ann in (int, float, str, bool):
                # Seed scalar fields with their default; nested models / lists
                # / dicts seed themselves recursively (so empty -> defaults).
                value = _scalar_default(fi)
            else:
                value = None
            editor = _make_editor(
                ann, optional, value, alias, meta, self._context)
            self._editors[alias] = editor
            # Show the field's physical unit (if declared) after the input.
            unit = meta.get('unit')
            if unit and hasattr(editor, 'add_suffix'):
                hint = QLabel(unit)
                hint.setStyleSheet("color: gray;")
                editor.add_suffix(hint)
            if meta.get('tooltip'):
                editor.setToolTip(meta['tooltip'])
            if getattr(editor, 'is_block', False):
                form.addRow(editor)
            else:
                form.addRow(alias, editor)

    def field_editor(self, alias):
        """Return the editor widget for a top-level field (None if absent)."""
        return self._editors.get(alias)

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
