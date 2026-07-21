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
    QLineEdit, QCheckBox, QComboBox, QPushButton, QGridLayout, QToolButton,
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
    """A dropdown for a string field with a fixed/contextual option set.

    With ``allow_custom`` the combo is editable, so a value outside the
    option set can be typed. That matters when the options come from an
    external source that may not know them all (e.g. the laser lines a
    setup's monet config declares) — otherwise an empty option set would
    leave the field unchangeable.
    """

    def __init__(self, options, value, allow_none, ann=str, parent=None,
                 allow_custom=False):
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
        self._combo.setEditable(allow_custom)
        if allow_custom:
            # Typing a value must not silently add it to the option list.
            self._combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
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
        # Keep row-remove buttons out of the tab chain: tabbing out of an
        # input should reach the next row's input, not a destructive button
        # that would swallow a stray Space/Enter.
        rm.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
    name). ``key_choices_key`` / ``value_choices_key`` name a *context* key
    whose options render that side as a dropdown (e.g. the setup's reservoir
    ids); the editor subscribes, so rows re-offer the current options when the
    setup changes — a snapshot would leave the column as free text forever if
    the form was built before a setup was loaded.
    """

    def __init__(self, key_ann, val_ann, value, title, *, columns=None,
                 display_value_first=False, key_choices_key=None,
                 value_choices_key=None, provides=None, context=None,
                 parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self._key_ann = key_ann
        self._val_ann = val_ann
        self._dvf = display_value_first
        self._key_choices_key = key_choices_key
        self._value_choices_key = value_choices_key
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
        self._add_btn = QPushButton("Add")
        self._add_btn.clicked.connect(lambda: self._add_row('', ''))
        self._lay.addWidget(self._add_btn)
        for k, v in (value or {}).items():
            self._add_row(k, v)
        # Follow the option sources, so a setup loaded/switched after this
        # form was built still turns the column into a dropdown.
        for key, side in ((self._key_choices_key, 'key'),
                          (self._value_choices_key, 'value')):
            if key and hasattr(self._ctx, 'subscribe'):
                self._ctx.subscribe(
                    key, lambda opts, s=side: self._refresh_choices(s, opts))

    def _choices(self, side):
        key = (self._key_choices_key if side == 'key'
               else self._value_choices_key)
        if not key or self._ctx is None:
            return None
        return self._ctx.get(key, [])

    def _refresh_choices(self, side, options):
        """Rebuild one column's cells against a new option set, in place."""
        idx = 0 if side == 'key' else 1
        for row in list(self._rows):
            old = row[idx]
            new = self._make_cell(self._cell_text(old), options)
            self._grid.replaceWidget(old, new)
            old.setParent(None)
            replacement = list(row)
            replacement[idx] = new
            self._rows[self._rows.index(row)] = tuple(replacement)
            if self._provides and idx == 1:
                self._connect_change(new)
        self._retab()

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
        key_w = self._make_cell(k, self._choices('key'))
        val_w = self._make_cell(v, self._choices('value'))
        rm = QPushButton("✕")
        rm.setFixedWidth(28)
        # Keep row-remove buttons out of the tab chain: tabbing out of an
        # input should reach the next row's input, not a destructive button
        # that would swallow a stray Space/Enter.
        rm.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Column 0/1 placement honours display_value_first.
        left, right = (val_w, key_w) if self._dvf else (key_w, val_w)
        self._grid.addWidget(left, r, 0)
        self._grid.addWidget(right, r, 1)
        self._grid.addWidget(rm, r, 2)
        row = (key_w, val_w, rm)
        self._rows.append(row)
        rm.clicked.connect(lambda: self._remove(row))
        self._retab()
        # Live-publish the values (e.g. reservoir names) as they change.
        if self._provides:
            self._connect_change(val_w)
            self._notify()

    def _retab(self):
        """Chain the typed cells down the table, column by column.

        Qt's default chain follows widget *creation* order, which puts the
        ✕ button between a row's name field and the next row — so tabbing
        out of a name landed on 'remove'. Instead Tab runs straight down the
        typed column (name → next row's name), which is how the table is
        actually filled in: the other column is a dropdown, picked from its
        list rather than typed, so it is taken out of the tab chain (it stays
        click- and keyboard-operable once focused). A column that is *not* a
        dropdown — e.g. reservoir ids before a setup is loaded — must still be
        typeable, so it keeps its place in the chain.

        Rows are rebuilt (option refresh) and removed out of order, so the
        chain is re-derived from the current row list rather than assumed.
        """
        stops = []
        for key_w, val_w, _ in self._rows:
            left, right = (val_w, key_w) if self._dvf else (key_w, val_w)
            if isinstance(left, QComboBox):
                left.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            else:
                left.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                stops.append(left)
            stops.append(right)
        for first, second in zip(stops, stops[1:]):
            QWidget.setTabOrder(first, second)

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
        self._retab()
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
        # Keep row-remove buttons out of the tab chain: tabbing out of an
        # input should reach the next row's input, not a destructive button
        # that would swallow a stray Space/Enter.
        rm.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        # Keep row-remove buttons out of the tab chain: tabbing out of an
        # input should reach the next row's input, not a destructive button
        # that would swallow a stray Space/Enter.
        rm.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
            key_choices_key=meta.get('key_choices_from'),
            value_choices_key=meta.get('value_choices_from'),
            provides=meta.get('provides'), context=context)
    # A scalar with a declared option set -> a single dropdown.
    if _has_choices(meta):
        key = meta.get('choices_from')
        opts = meta.get('choices')
        if opts is None:
            opts = context.get(key, [])
        editor = _ChoiceEditor(
            opts, value, meta.get('allow_none', False), ann,
            allow_custom=meta.get('allow_custom', False))
        if key is not None and hasattr(context, 'subscribe'):
            context.subscribe(key, editor.set_options)
        return editor
    if ann in (int, float, str, bool):
        return _ScalarEditor(ann, optional, value)
    return _LiteralEditor(value)


class _ModelEditor(QGroupBox):
    """A nested BaseModel rendered as a titled, collapsible sub-form.

    A ▾/▸ arrow toggle in the section header expands/collapses the body to
    save space. Collapsing is purely visual — it never affects the data, as
    :meth:`get_value` always reads the (still-populated) sub-form back.
    """

    def __init__(self, model_cls, value, title, context=None, parent=None):
        super().__init__(title, parent)
        self.is_block = True
        self.is_optional = False
        lay = QVBoxLayout(self)
        # Header row with the collapse arrow; flat/auto-raised so it reads as
        # a disclosure triangle rather than a button.
        self._toggle = QToolButton()
        self._toggle.setAutoRaise(True)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        self._toggle.setArrowType(Qt.ArrowType.DownArrow)
        self._toggle.setToolTip("Collapse / expand this section.")
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._toggle)
        header.addStretch()
        lay.addLayout(header)
        self._form = SchemaForm(model_cls, value or {}, context=context)
        lay.addWidget(self._form)
        self._toggle.toggled.connect(self._set_expanded)

    def _set_expanded(self, expanded):
        self._toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._form.setVisible(expanded)

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

    @property
    def context(self):
        """The :class:`FormContext` shared down this form tree.

        Exposed so an owner can publish fresh option lists (e.g. after the
        microscope setup changes) without rebuilding the form.
        """
        return self._context

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
