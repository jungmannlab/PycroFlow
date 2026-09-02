"""Live schematic of the fluid path: ibidi multiplexer, pumps, sample.

A custom-painted :class:`QWidget` that draws the fluid wiring described by a
setup config (:meth:`SystemService.fluid_topology`) and overlays the live
valve and syringe state (:meth:`SystemService.fluid_state`). It repaints from a
cached snapshot only, so the owning tab can poll it on a timer without ever
touching the serial bus — the picture stays live during a run.

Layout, left to right::

    [ ibidi multiplexer 6x4 grid ]  ->  pump_a  ->  sample  ->  pump_out -> waste

The multiplexer ports are numbered left-to-right, bottom-to-top on their
physical grid (``grid_cols`` wide): port 1 lower left, port 6 lower right,
port 7 directly above port 1 (see :meth:`_grid_cell`). The *tubing* between
them meanders — that shape shows up as the edges traced from each reservoir's
route, not in the port numbering. The port wired to pump_a (``pump_channel``,
port 1 by default) is drawn with a stub to the pump.

Each port is shaded by its live state — green open (flowing), grey closed,
hatched when unknown. pump_a's valve position lights the active leg (syringe
to the multiplexer for ``in``, to the sample for ``out``); the syringe barrels
fill to each pump's commanded volume.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QBrush,
    QPolygonF,
    QFontMetrics,
)
from PyQt6.QtWidgets import QWidget, QSizePolicy

# Palette (explicit so it reads the same under any Qt theme).
_OPEN = QColor(46, 160, 67)  # flowing
_CLOSED = QColor(70, 74, 82)  # shut
_UNKNOWN = QColor(120, 120, 120)  # state not known
_PORT_EDGE = QColor(30, 32, 36)
_PUMP_PORT = QColor(214, 158, 46)  # the port wired to pump_a
_BODY = QColor(58, 62, 70)
_BODY_EDGE = QColor(150, 155, 165)
_FILL = QColor(64, 132, 214)  # liquid in a syringe
_ACTIVE = QColor(46, 160, 67)  # an energised flow leg
_IDLE = QColor(96, 100, 108)  # an idle leg
_TEXT = QColor(228, 230, 234)
_MUTED = QColor(150, 154, 162)
_HILITE = QColor(90, 200, 235)  # hovered / selected reservoir path


class FluidSchematic(QWidget):
    """Draws the fluid topology with a live valve/syringe overlay."""

    #: Emitted with the reservoir id under the cursor (or ``None`` when the
    #: cursor leaves the ports), so a host can echo the hovered route.
    reservoir_hovered = pyqtSignal(object)
    #: Emitted with an ibidi channel number when its port is clicked (a raw
    #: open/close toggle, ignoring reservoir routing).
    channel_clicked = pyqtSignal(int)
    #: Emitted with a pump name ('pump_a' / 'pump_out') when it is clicked (a
    #: raw in<->out valve toggle).
    pump_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._topo = None
        self._state = None
        # {reservoir_id: {'name': str|None, 'used': bool}} from the connected
        # design; drives port names and the dimmed "unused" look. Empty ->
        # every reservoir drawn neutral (no design connected yet).
        self._res_labels = {}
        # Route highlighting: a persistent "selected" reservoir (e.g. the tab's
        # dropdown) and a transient "hovered" one; hover wins while present.
        self._selected_res = None
        self._hover_res = None
        # channel -> QRectF and pump-name -> QRectF from the last paint, for
        # cursor hit-testing (clicks toggle the thing under the cursor).
        self._port_rects = {}
        self._pump_rects = {}
        self.setMinimumSize(520, 300)
        self.setMouseTracking(True)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setToolTip(
            "Live fluid wiring. Ports: green = open, dark = closed, hatched "
            "= unknown. R<n> marks the reservoir tapped at a port. Hover a "
            "port (or pick a reservoir) to highlight its path to the pump."
        )

    # -- data ---------------------------------------------------------------
    def set_topology(self, topo):
        """Set the static wiring (from ``SystemService.fluid_topology``)."""
        self._topo = topo
        self._port_rects = {}
        self._pump_rects = {}
        self.update()

    def set_state(self, state):
        """Set the live valve/syringe snapshot (``fluid_state``); repaint."""
        self._state = state
        self.update()

    def set_reservoir_labels(self, labels):
        """Set ``{id: {name, used}}`` for port names + unused dimming."""
        self._res_labels = labels or {}
        self.update()

    def highlight_reservoir(self, reservoir_id):
        """Persistently highlight one reservoir's path (``None`` clears it)."""
        if reservoir_id != self._selected_res:
            self._selected_res = reservoir_id
            self.update()

    # -- highlight helpers --------------------------------------------------
    def _routes(self):
        mux = (self._topo or {}).get("multiplexer") or {}
        return mux.get("routes") or {}

    def _active_highlight(self):
        """The reservoir whose path is highlighted (hover beats selection)."""
        rid = (
            self._hover_res
            if self._hover_res is not None
            else self._selected_res
        )
        if rid is None:
            return None, set()
        return rid, set(self._routes().get(rid, []))

    # -- geometry -----------------------------------------------------------
    @staticmethod
    def _grid_cell(channel, cols, rows):
        """Grid (col, row_from_top) of a 1-based port on the layout.

        Ports are numbered left-to-right, bottom-to-top: row 0 is the bottom
        row (port 1 lower left, port ``cols`` lower right), row 1 starts again
        at the left (port ``cols + 1`` directly above port 1). Returns the row
        counted *from the top* for painting.
        """
        idx = channel - 1
        row = idx // cols
        col = idx % cols
        return col, rows - 1 - row

    # -- mouse interaction --------------------------------------------------
    def _channel_at(self, pos):
        """Channel whose port rect contains ``pos``, or ``None``."""
        for channel, r in self._port_rects.items():
            if r.contains(QPointF(pos)):
                return channel
        return None

    def _pump_at(self, pos):
        """Pump name whose station rect contains ``pos``, or ``None``."""
        for name, r in self._pump_rects.items():
            if r.contains(QPointF(pos)):
                return name
        return None

    def mouseMoveEvent(self, event):  # noqa: N802 (Qt override)
        channel = self._channel_at(event.position())
        ports = ((self._topo or {}).get("multiplexer") or {}).get("ports", {})
        # Highlight the reservoir tapped at the hovered port; fall back to a
        # reservoir merely routed through it (a shared bridge port).
        rid = None
        if channel is not None:
            info = ports.get(channel, {})
            rid = info.get("reservoir")
            if rid is None and info.get("used_by"):
                rid = info["used_by"][0]
        if rid != self._hover_res:
            self._hover_res = rid
            self._update_hover_tooltip(channel, rid)
            self.reservoir_hovered.emit(rid)
            self.update()
        # A pointing-hand cursor advertises the clickable ports / pumps.
        clickable = channel is not None or self._pump_at(event.position())
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if clickable
            else Qt.CursorShape.ArrowCursor
        )
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        channel = self._channel_at(event.position())
        if channel is not None:
            self.channel_clicked.emit(channel)
            return
        pump = self._pump_at(event.position())
        if pump is not None:
            self.pump_clicked.emit(pump)
            return
        super().mousePressEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt override)
        if self._hover_res is not None:
            self._hover_res = None
            self.reservoir_hovered.emit(None)
            self.update()
        super().leaveEvent(event)

    def _update_hover_tooltip(self, channel, rid):
        if rid is None:
            self.setToolTip(
                "Live fluid wiring. Hover a port (or pick a reservoir) to "
                "highlight its path to the pump."
            )
            return
        route = self._routes().get(rid, [])
        label_info = self._res_labels.get(rid) or {}
        name = label_info.get("name")
        who = "R{} ({})".format(rid, name) if name else "R{}".format(rid)
        suffix = ""
        if (
            self._res_labels
            and label_info
            and not label_info.get("used", True)
        ):
            suffix = " — not used by the loaded design"
        self.setToolTip(
            "Reservoir {}: opens ibidi channels {} (all others closed); "
            "port {} is its tap.{}".format(
                who,
                ", ".join(str(c) for c in route),
                route[-1] if route else channel,
                suffix,
            )
        )

    # -- painting -----------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor(37, 39, 44))

        if self._topo is None:
            self._draw_centered(
                painter, rect, "Load a setup to see the fluid wiring."
            )
            painter.end()
            return

        mux = self._topo.get("multiplexer")
        w, h = rect.width(), rect.height()
        margin = 12
        # Left half: multiplexer grid. Right half: pumps + sample.
        mux_w = int(w * 0.52) if mux else 0
        mux_rect = QRectF(
            margin, margin, max(0, mux_w - margin), h - 2 * margin
        )
        flow_rect = QRectF(
            mux_w + margin, margin, w - mux_w - 2 * margin, h - 2 * margin
        )

        pump_port_center = None
        if mux:
            pump_port_center = self._draw_multiplexer(painter, mux_rect, mux)
        else:
            self._draw_centered(
                painter,
                mux_rect if mux_w else rect,
                "This setup has no ibidi multiplexer.",
            )

        self._draw_flow(painter, flow_rect, pump_port_center)
        painter.end()

    # -- multiplexer --------------------------------------------------------
    def _draw_multiplexer(self, painter, area, mux):
        """Draw the 6x4 port grid; return the pump-wired port's centre."""
        cols, rows = mux["cols"], mux["rows"]
        ports = mux["ports"]
        pump_channel = mux.get("pump_channel")
        open_states = self._open_states()

        self._label(
            painter,
            area.left(),
            area.top() - 2,
            "ibidi multiplexer",
            _MUTED,
            bold=True,
        )
        grid = QRectF(
            area.left(), area.top() + 14, area.width(), area.height() - 14
        )
        cw = grid.width() / cols
        ch_ = grid.height() / rows
        cell = min(cw, ch_)
        pad = cell * 0.12
        # Centre the grid in its area.
        ox = grid.left() + (grid.width() - cell * cols) / 2
        oy = grid.top() + (grid.height() - cell * rows) / 2

        # Pre-compute every port's rect so the manifold edges can be drawn
        # beneath the ports (and so the cursor can hit-test ports).
        rects = {}
        for channel in range(1, mux["channels"] + 1):
            col, row_top = self._grid_cell(channel, cols, rows)
            x = ox + col * cell
            y = oy + row_top * cell
            rects[channel] = QRectF(
                x + pad, y + pad, cell - 2 * pad, cell - 2 * pad
            )
        self._port_rects = rects

        _, hilite = self._active_highlight()

        # Manifold wiring tree (from the config): a leg is lit when both of
        # its ports are open — i.e. it is part of a live flow path. A leg on
        # the highlighted reservoir's route is drawn in the highlight colour.
        for a, b in mux.get("edges", []):
            if a not in rects or b not in rects:
                continue
            on_route = a in hilite and b in hilite
            active = bool(open_states) and (
                open_states.get(a) is True and open_states.get(b) is True
            )
            if on_route:
                pen = QPen(_HILITE, 3.4)
            else:
                pen = QPen(
                    _ACTIVE if active else QColor(88, 92, 100),
                    3 if active else 1.6,
                )
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(rects[a].center(), rects[b].center())

        for channel in range(1, mux["channels"] + 1):
            state = open_states.get(channel) if open_states else None
            self._draw_port(
                painter,
                rects[channel],
                channel,
                ports.get(channel, {}),
                state,
                channel == pump_channel,
            )

        # Highlight ring on the route's ports, on top of everything.
        if hilite:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(_HILITE, 2.6))
            for channel in hilite:
                r = rects.get(channel)
                if r is not None:
                    painter.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 5, 5)

        pc = rects.get(pump_channel)
        return QPointF(pc.center().x(), pc.center().y()) if pc else None

    def _draw_port(self, painter, r, channel, info, state, is_pump):
        if state is True:
            fill = _OPEN
        elif state is False:
            fill = _CLOSED
        else:
            fill = _UNKNOWN
        rid = info.get("reservoir")
        label_info = self._res_labels.get(rid) if rid is not None else None
        # A reservoir the connected design does not use is drawn dimmed.
        unused = (
            bool(self._res_labels)
            and label_info is not None
            and not label_info.get("used", True)
        )
        painter.setBrush(QBrush(fill))
        pen = QPen(
            _PUMP_PORT if is_pump else _PORT_EDGE, 2.4 if is_pump else 1.0
        )
        painter.setPen(pen)
        painter.drawRoundedRect(r, 4, 4)
        if unused:
            # Darken the cell to read as "not part of this experiment".
            painter.setBrush(QBrush(QColor(20, 21, 24, 150)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(r, 4, 4)
        if state is None:
            painter.setPen(QPen(QColor(90, 90, 90), 1, Qt.PenStyle.DotLine))
            painter.drawLine(r.topLeft(), r.bottomRight())

        # Primary label: the reservoir's name if the design gave one, else its
        # id; channel number small in the corner. Names use a smaller font and
        # are elided to the cell width so they never spill over the port.
        name = (label_info or {}).get("name")
        f = painter.font()
        f.setBold(True)
        if name:
            primary = str(name)
            f.setPointSizeF(max(6.0, min(8.5, r.height() * 0.24)))
        elif rid is not None:
            primary = "R{}".format(rid)
            f.setPointSizeF(max(6.5, min(11.0, r.height() * 0.30)))
        else:
            primary = ""
            f.setPointSizeF(max(6.5, min(11.0, r.height() * 0.30)))
        painter.setFont(f)
        primary = QFontMetrics(f).elidedText(
            primary, Qt.TextElideMode.ElideRight, int(r.width() - 4)
        )
        painter.setPen(QPen(_MUTED if unused else _TEXT))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, primary)
        f.setBold(False)
        f.setPointSizeF(max(5.5, min(8.0, r.height() * 0.22)))
        painter.setFont(f)
        painter.setPen(
            QPen(QColor(210, 214, 220) if not is_pump else _PUMP_PORT)
        )
        painter.drawText(
            QRectF(r.left(), r.top() + 1, r.width() - 2, r.height() * 0.4),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            "{}".format(channel),
        )
        if is_pump:
            painter.setPen(QPen(_PUMP_PORT))
            painter.drawText(
                QRectF(
                    r.left(),
                    r.bottom() - r.height() * 0.42,
                    r.width(),
                    r.height() * 0.4,
                ),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                "→ pump_a",
            )

    # -- pumps / sample / flow ---------------------------------------------
    def _draw_flow(self, painter, area, pump_port_center):
        """Draw pump_a, the sample, and pump_out with the active legs lit."""
        st = self._state or {}
        pa = st.get("pump_a")
        po = st.get("pump_out")

        x = area.left()
        w = area.width()
        # Three stacked stations sharing the column width.
        col_w = min(w, 210)
        cx = x + (w - col_w) / 2 if w > col_w else x
        top = area.top() + 12

        # Vertical rhythm: pump_a, sample, pump_out.
        h = area.height() - 24
        station_h = h / 3.0
        pa_rect = QRectF(cx, top, col_w, station_h * 0.9)
        sample_rect = QRectF(
            cx + col_w * 0.18, top + station_h, col_w * 0.64, station_h * 0.6
        )
        po_rect = QRectF(cx, top + 2 * station_h, col_w, station_h * 0.9)
        self._pump_rects = {"pump_a": pa_rect, "pump_out": po_rect}

        a_valve = pa.get("valve") if pa else None
        # pump_a: 'in' draws syringe<->multiplexer, 'out' syringe<->sample.
        leg_mux_active = a_valve == "in"
        leg_sample_active = a_valve == "out"

        # Connector: multiplexer pump-port (port 1) -> pump_a. Routed out to
        # the LEFT of the multiplexer and over its top, so it never crosses
        # the other reservoir ports (port 1 is the grid's bottom-left cell,
        # with nothing to its left).
        if pump_port_center is not None:
            self._draw_feed_line(
                painter,
                pump_port_center,
                QPointF(pa_rect.left(), pa_rect.center().y()),
                leg_mux_active,
            )
        # pump_a -> sample.
        self._draw_link(
            painter,
            QPointF(pa_rect.center().x(), pa_rect.bottom()),
            QPointF(sample_rect.center().x(), sample_rect.top()),
            leg_sample_active,
        )
        # sample -> pump_out (extraction leg; lit when pump_out draws 'in').
        po_valve = po.get("valve") if po else None
        self._draw_link(
            painter,
            QPointF(sample_rect.center().x(), sample_rect.bottom()),
            QPointF(po_rect.center().x(), po_rect.top()),
            po_valve == "in",
        )

        self._draw_pump(
            painter, pa_rect, "pump_a", pa, in_label="mux", out_label="sample"
        )
        self._draw_sample(painter, sample_rect)
        self._draw_pump(
            painter,
            po_rect,
            "pump_out",
            po,
            in_label="sample",
            out_label="waste",
        )

    def _draw_link(self, painter, p1, p2, active):
        pen = QPen(_ACTIVE if active else _IDLE, 3 if active else 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(p1, p2)

    def _draw_feed_line(self, painter, p_from, p_to, active):
        """Route the port-1 -> pump_a feed left of the grid and over its top.

        A polyline that exits ``p_from`` (port 1) to the left, climbs the
        far-left margin, runs along the top above the whole grid, then drops
        into ``p_to`` (pump_a's left side) — keeping clear of every port.
        """
        pen = QPen(_ACTIVE if active else _IDLE, 3 if active else 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        x_left = 6.0
        y_top = 6.0
        pts = [
            p_from,
            QPointF(x_left, p_from.y()),
            QPointF(x_left, y_top),
            QPointF(p_to.x(), y_top),
            p_to,
        ]
        painter.drawPolyline(QPolygonF(pts))

    def _draw_pump(self, painter, r, name, snap, in_label, out_label):
        """Draw a syringe pump: barrel with a fill level + valve state."""
        painter.setBrush(QBrush(_BODY))
        painter.setPen(QPen(_BODY_EDGE, 1.4))
        painter.drawRoundedRect(r, 6, 6)

        # Syringe barrel on the left third; fill from the bottom.
        barrel = QRectF(
            r.left() + 8, r.top() + 8, r.width() * 0.28, r.height() - 16
        )
        painter.setBrush(QBrush(QColor(28, 30, 34)))
        painter.setPen(QPen(_BODY_EDGE, 1.2))
        painter.drawRoundedRect(barrel, 3, 3)
        cap = snap.get("capacity") if snap else 0
        vol = snap.get("volume") if snap else 0
        frac = (vol / cap) if snap and cap else 0.0
        frac = max(0.0, min(1.0, frac))
        if frac > 0:
            fh = (barrel.height() - 4) * frac
            fill_rect = QRectF(
                barrel.left() + 2,
                barrel.bottom() - 2 - fh,
                barrel.width() - 4,
                fh,
            )
            painter.setBrush(QBrush(_FILL))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(fill_rect, 2, 2)

        # Labels: name, valve position, volume.
        tx = barrel.right() + 8
        tw = r.right() - tx - 6
        f = painter.font()
        f.setBold(True)
        f.setPointSizeF(9)
        painter.setFont(f)
        painter.setPen(QPen(_TEXT))
        painter.drawText(
            QRectF(tx, r.top() + 6, tw, 16), Qt.AlignmentFlag.AlignLeft, name
        )
        f.setBold(False)
        f.setPointSizeF(8)
        painter.setFont(f)
        valve = snap.get("valve") if snap else None
        if valve == "in":
            vtxt = "valve: IN → {}".format(in_label)
        elif valve == "out":
            vtxt = "valve: OUT → {}".format(out_label)
        else:
            vtxt = "valve: —"
        painter.setPen(QPen(_ACTIVE if valve else _MUTED))
        painter.drawText(
            QRectF(tx, r.top() + 24, tw, 16), Qt.AlignmentFlag.AlignLeft, vtxt
        )
        painter.setPen(QPen(_MUTED))
        if snap and cap:
            voltxt = "{:.0f} / {:.0f} µL".format(vol, cap)
        else:
            voltxt = "— µL"
        painter.drawText(
            QRectF(tx, r.top() + 40, tw, 16),
            Qt.AlignmentFlag.AlignLeft,
            voltxt,
        )

    def _draw_sample(self, painter, r):
        painter.setBrush(QBrush(QColor(52, 74, 96)))
        painter.setPen(QPen(_BODY_EDGE, 1.4))
        painter.drawRoundedRect(r, 5, 5)
        painter.setPen(QPen(_TEXT))
        f = painter.font()
        f.setBold(True)
        f.setPointSizeF(9)
        painter.setFont(f)
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "sample")

    # -- helpers ------------------------------------------------------------
    def _open_states(self):
        """Map channel -> open bool/None from the live snapshot, or None."""
        if not self._state:
            return None
        mux = self._state.get("multiplexer")
        if not mux:
            return None
        opened = mux.get("open") or []
        return {i + 1: opened[i] for i in range(len(opened))}

    def _label(self, painter, x, y, text, color, bold=False):
        f = painter.font()
        f.setBold(bold)
        f.setPointSizeF(9)
        painter.setFont(f)
        painter.setPen(QPen(color))
        painter.drawText(QPointF(x, y + 12), text)

    def _draw_centered(self, painter, rect, text):
        painter.setPen(QPen(_MUTED))
        f = painter.font()
        f.setPointSizeF(10)
        painter.setFont(f)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
