"""Live schematic of the fluid path: reservoir multiplexer, pumps, sample.

A custom-painted :class:`QWidget` that draws the fluid wiring described by a
setup config (:meth:`SystemService.fluid_topology`) and overlays the live
valve and syringe state (:meth:`SystemService.fluid_state`). It repaints from a
cached snapshot only, so the owning tab can poll it on a timer without ever
touching the serial bus — the picture stays live during a run.

Layout, left to right::

    [ reservoir multiplexer ]  ->  pump_a  ->  sample  ->  pump_out -> waste

Two reservoir-multiplexer styles are drawn, whichever the setup wires:

* **ibidi multiplexer** — a physical 6×4 grid of independently-actuated ports
  numbered left-to-right, bottom-to-top (port 1 lower left, port 7 above port
  1; see :meth:`_grid_cell`). The *tubing* between ports meanders — that shape
  shows up as the edges traced from each reservoir's route, not the numbering.
  Each port is shaded by its live open/closed state.

* **Hamilton MVP rotary valves** — one or more chained rotary valves, each a
  hub drawn on top with its reservoirs stacked in one or two short columns
  below it (see :meth:`_draw_valves`); the hub's tubing drops radially to a
  per-column rail and branches into each box. A rotary valve selects one port
  at a time, so the live path is lit and a reservoir box goes green only when
  its whole root→leaf path is live. Ports that chain to the next valve are
  drawn as hub-to-hub bridges.

pump_a's valve position lights the active leg (syringe to the multiplexer for
``in``, to the sample for ``out``); the syringe barrels fill to each pump's
commanded volume. Each reservoir box also carries a live volume gauge (a
draining reagent tank on the left, a filling waste column on the right).
"""

from __future__ import annotations

import math

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

from PycroFlow.protocols.timing import format_volume

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
_VOLBAR = QColor(64, 132, 214)  # remaining reagent (left tank, drains down)
_WASTEBAR = QColor(200, 96, 72)  # consumed/waste (right column, fills up)
_VOLBAR_TRACK = QColor(24, 25, 28)


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
        # {sink: {'used_vol', 'total_vol'}} for the waste-container fill gauges.
        self._waste_labels = {}
        # Route highlighting: a persistent "selected" reservoir (e.g. the tab's
        # dropdown) and a transient "hovered" one; hover wins while present.
        self._selected_res = None
        self._hover_res = None
        # channel -> QRectF and pump-name -> QRectF from the last paint, for
        # cursor hit-testing (clicks toggle the thing under the cursor).
        self._port_rects = {}
        self._pump_rects = {}
        # reservoir id -> QRectF for the MVP-valve layout (reservoir boxes are
        # keyed by id, not by a single channel as in the ibidi grid).
        self._res_rects = {}
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
        self._res_rects = {}
        self.update()

    def set_state(self, state):
        """Set the live valve/syringe snapshot (``fluid_state``); repaint."""
        self._state = state
        self.update()

    def set_reservoir_labels(self, labels):
        """Set ``{id: {name, used}}`` for port names + unused dimming."""
        self._res_labels = labels or {}
        self.update()

    def set_waste_labels(self, labels):
        """Set ``{sink: {used_vol, total_vol}}`` for the waste fill gauges."""
        self._waste_labels = labels or {}
        self.update()

    def highlight_reservoir(self, reservoir_id):
        """Persistently highlight one reservoir's path (``None`` clears it)."""
        if reservoir_id != self._selected_res:
            self._selected_res = reservoir_id
            self.update()

    # -- highlight helpers --------------------------------------------------
    def _routes(self):
        """Per-reservoir path map, whichever multiplexer this setup uses.

        For the ibidi grid a route is a list of channel numbers; for MVP
        valves it is a list of ``(valve_address, port)`` pairs. The highlight
        machinery only tests membership, so both work uniformly.
        """
        topo = self._topo or {}
        mux = topo.get("multiplexer") or {}
        if mux.get("routes"):
            return mux["routes"]
        valves = topo.get("valves") or {}
        return valves.get("routes") or {}

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

    def _reservoir_at(self, pos):
        """Reservoir under ``pos`` — an MVP box or an ibidi port's tap."""
        for rid, r in self._res_rects.items():
            if r.contains(QPointF(pos)):
                return rid
        channel = self._channel_at(pos)
        if channel is not None:
            ports = ((self._topo or {}).get("multiplexer") or {}).get(
                "ports", {}
            )
            info = ports.get(channel, {})
            # The tapped reservoir, else one merely routed through a bridge.
            rid = info.get("reservoir")
            if rid is None and info.get("used_by"):
                rid = info["used_by"][0]
            return rid
        return None

    def mouseMoveEvent(self, event):  # noqa: N802 (Qt override)
        rid = self._reservoir_at(event.position())
        if rid != self._hover_res:
            self._hover_res = rid
            self._update_hover_tooltip(rid)
            self.reservoir_hovered.emit(rid)
            self.update()
        # A pointing-hand cursor advertises the clickable ports / pumps. MVP
        # reservoir boxes carry no click action, so only ibidi channels and
        # the pumps light the cursor.
        clickable = (
            self._channel_at(event.position()) is not None
            or self._pump_at(event.position()) is not None
        )
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

    def _update_hover_tooltip(self, rid):
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
        # Planned vs pumped volume, when the design plans injects from here.
        total_vol = label_info.get("total_vol") or 0
        if total_vol > 0:
            used_vol = label_info.get("used_vol") or 0
            pct = int(round(100 * used_vol / total_vol))
            suffix += " Volume: {} used / {} needed ({}%).".format(
                format_volume(used_vol), format_volume(total_vol), pct
            )
        if (self._topo or {}).get("valves"):
            # MVP: route is [(valve_address, port), ...] from root to leaf.
            path = " → ".join(
                "valve {} → port {}".format(a, p) for a, p in route
            )
            self.setToolTip(
                "Reservoir {}: {}.{}".format(who, path or "—", suffix)
            )
            return
        self.setToolTip(
            "Reservoir {}: opens ibidi channels {} (all others closed); "
            "port {} is its tap.{}".format(
                who,
                ", ".join(str(c) for c in route),
                route[-1] if route else "?",
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
        valves = self._topo.get("valves")
        w, h = rect.width(), rect.height()
        margin = 12
        # Left region: reservoir multiplexer (ibidi grid or MVP valves).
        # Right region: pumps + sample.
        left_frac = 0.52 if (mux or valves) else 0.0
        left_w = int(w * left_frac)
        left_rect = QRectF(
            margin, margin, max(0, left_w - margin), h - 2 * margin
        )
        flow_rect = QRectF(
            left_w + margin, margin, w - left_w - 2 * margin, h - 2 * margin
        )

        pump_port_center = None
        feed = "overtop"
        if mux:
            pump_port_center = self._draw_multiplexer(painter, left_rect, mux)
        elif valves:
            pump_port_center = self._draw_valves(painter, left_rect, valves)
            feed = "direct"
        else:
            self._draw_centered(
                painter, rect, "This setup has no reservoir multiplexer."
            )

        self._draw_flow(painter, flow_rect, pump_port_center, feed=feed)
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
        # Live volume gauges, for a reservoir the design plans to inject from
        # (total_vol > 0): a "tank" on the left that starts full and drains as
        # the reagent is pumped out (fill height = remaining fraction), and a
        # waste column on the right that fills upward as it is consumed.
        total_vol = (label_info or {}).get("total_vol") or 0
        if total_vol > 0 and not unused:
            used_vol = (label_info or {}).get("used_vol") or 0
            used_frac = max(0.0, min(1.0, used_vol / total_vol))
            bar_w = 4.0
            top = r.top() + 3
            bottom = r.bottom() - 3
            painter.setPen(Qt.PenStyle.NoPen)
            # Left: remaining reagent, drains from full to empty.
            left = QRectF(r.left() + 2, top, bar_w, bottom - top)
            self._draw_vgauge(painter, left, 1.0 - used_frac, _VOLBAR)
            # Right: waste consumed, fills from empty to full.
            right = QRectF(r.right() - 2 - bar_w, top, bar_w, bottom - top)
            self._draw_vgauge(painter, right, used_frac, _WASTEBAR)
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

    @staticmethod
    def _draw_vgauge(painter, track, frac, color):
        """Draw a vertical gauge: dark track + bottom-anchored ``frac`` fill."""
        painter.setBrush(QBrush(_VOLBAR_TRACK))
        painter.drawRoundedRect(track, 2, 2)
        frac = max(0.0, min(1.0, frac))
        if frac > 0:
            fill_h = track.height() * frac
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(
                QRectF(
                    track.left(),
                    track.bottom() - fill_h,
                    track.width(),
                    fill_h,
                ),
                2, 2,
            )

    # -- MVP rotary valves --------------------------------------------------
    def _valve_positions(self):
        """Map ``{valve address: current position}`` from the live snapshot."""
        if not self._state:
            return {}
        return dict(self._state.get("valves") or {})

    @staticmethod
    def _active_reservoir(routes, positions):
        """The reservoir every valve on its route currently selects, or None."""
        if not positions:
            return None
        for rid, route in routes.items():
            if route and all(positions.get(a) == p for a, p in route):
                return rid
        return None

    def _draw_valves(self, painter, area, vt):
        """Draw chained MVP rotary valves; return the root hub's pump point.

        Each valve is a hub with its tubing fanning out radially to elbows,
        then rising vertically into a reservoir box at the top of the valve's
        band. The currently-selected port on each valve (and the whole path to
        the live reservoir) is lit; bridge ports link hub to hub.
        """
        valves = vt.get("valves") or []
        routes = vt.get("routes") or {}
        positions = self._valve_positions()
        active_rid = self._active_reservoir(routes, positions)
        _hl_rid, hl_set = self._active_highlight()

        self._label(
            painter,
            area.left(),
            area.top() - 2,
            "Hamilton MVP valves",
            _MUTED,
            bold=True,
        )
        self._res_rects = {}
        self._port_rects = {}
        n = max(len(valves), 1)
        band_w = area.width() / n
        hubs = {}  # address -> (QPointF center, radius)
        root_pt = None
        for vi, valve in enumerate(valves):
            # Lay the chain right-to-left so the root valve (index 0, wired to
            # the pump) sits in the rightmost band, nearest pump_a — its feed
            # line is then a short hop that never crosses the other valves.
            band = QRectF(
                area.left() + (n - 1 - vi) * band_w,
                area.top() + 14,
                band_w,
                area.height() - 14,
            )
            center, radius = self._draw_one_valve(
                painter, band, valve, positions, active_rid, hl_set
            )
            hubs[valve["address"]] = (center, radius)
            if vi == 0:
                root_pt = QPointF(center.x() + radius, center.y())
        # Bridge legs (hub -> downstream hub), on top of the spokes.
        for valve in valves:
            for port, down_addr in (valve.get("bridges") or {}).items():
                src = hubs.get(valve["address"])
                dst = hubs.get(down_addr)
                if not src or not dst:
                    continue
                active = positions.get(valve["address"]) == port
                on_route = (valve["address"], port) in hl_set
                self._draw_bridge(painter, src, dst, active, on_route)
        return root_pt

    def _draw_one_valve(
        self, painter, band, valve, positions, active_rid, hl_set
    ):
        """Draw one rotary valve on top with its reservoirs stacked below.

        The hub sits at the top of the band; its tubing drops radially to a
        vertical rail per column and branches horizontally into reservoir
        boxes. Stacking the boxes in one or two short columns (instead of one
        wide row) keeps the picture compact while every box stays wide enough
        to read. Returns the hub centre and radius.
        """
        addr = valve["address"]
        taps = valve.get("taps") or {}   # port -> reservoir id
        cur = positions.get(addr)

        hub_r = max(13.0, min(min(band.width(), band.height()) * 0.09, 26.0))
        hub = QPointF(band.center().x(), band.top() + hub_r + 8)
        tap_ports = sorted(taps)
        m = len(tap_ports)
        if m == 0:
            self._draw_hub(painter, hub, hub_r, addr, cur)
            return hub, hub_r

        # Up to two columns; boxes fill each column top-to-bottom.
        ncol = 1 if m <= 4 else 2
        nrow = -(-m // ncol)   # ceil
        col_w = band.width() / ncol
        grid_top = hub.y() + hub_r + 12
        gap_y = 6.0
        box_h = max(
            16.0,
            min((band.bottom() - grid_top - (nrow - 1) * gap_y) / nrow, 30.0),
        )

        # Geometry per tap; draw tubes first (idle, then active, then hover on
        # top) so the highlighted path reads cleanly over the shared rails.
        legs = []   # (priority, [points], active, on_route, box, port, rid)
        for idx, port in enumerate(tap_ports):
            rid = taps[port]
            col = 0 if idx < nrow else 1
            row = idx - col * nrow
            col_left = band.left() + col * col_w
            box_top = grid_top + row * (box_h + gap_y)
            # Both columns are wired toward the centre under the hub: the right
            # column keeps its rail on its left edge; the left column mirrors,
            # putting its rail on its right edge (its tubing faces centre).
            if ncol == 2 and col == 0:
                rail_x = col_left + col_w - 6
                box_left = col_left + 6
                box_entry_x = rail_x - 8   # branch into the box's right edge
            else:
                rail_x = col_left + 6
                box_left = rail_x + 8
                box_entry_x = box_left     # branch into the box's left edge
            box_w = (col_left + col_w - 6) - (col_left + 6) - 8
            box = QRectF(box_left, box_top, box_w, box_h)
            self._res_rects[rid] = box
            cy = box.center().y()
            # hub -> rail top (radial), down the rail, then into the box edge.
            pts = [
                hub,
                QPointF(rail_x, hub.y() + hub_r),
                QPointF(rail_x, cy),
                QPointF(box_entry_x, cy),
            ]
            active = cur == port
            on_route = (addr, port) in hl_set
            prio = 2 if on_route else (1 if active else 0)
            legs.append((prio, pts, active, on_route, box, port, rid))

        for prio, pts, active, on_route, *_ in sorted(
            legs, key=lambda leg_: leg_[0]
        ):
            self._draw_tube(painter, pts, active, on_route)
        for _prio, _pts, _active, _on_route, box, port, rid in legs:
            # The box lights green only when its *whole* route is live.
            state = (rid == active_rid) if positions else None
            self._draw_port(
                painter, box, port, {"reservoir": rid}, state, False
            )
        self._draw_hub(painter, hub, hub_r, addr, cur)
        return hub, hub_r

    def _draw_tube(self, painter, points, active, on_route):
        """Polyline tube, coloured by highlight / live-active / idle."""
        if on_route:
            pen = QPen(_HILITE, 3.4)
        elif active:
            pen = QPen(_ACTIVE, 3)
        else:
            pen = QPen(QColor(88, 92, 100), 1.8)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF(points))

    def _draw_bridge(self, painter, src, dst, active, on_route):
        """Draw the hub-to-hub link for a bridge port (edge to edge)."""
        (c1, r1), (c2, r2) = src, dst
        dx, dy = c2.x() - c1.x(), c2.y() - c1.y()
        dist = math.hypot(dx, dy) or 1.0
        ux, uy = dx / dist, dy / dist
        p1 = QPointF(c1.x() + ux * r1, c1.y() + uy * r1)
        p2 = QPointF(c2.x() - ux * r2, c2.y() - uy * r2)
        self._draw_tube(painter, [p1, p2], active, on_route)

    def _draw_hub(self, painter, center, r, addr, cur=None):
        """Draw a rotary valve hub: a labelled circle + selected-port note."""
        painter.setBrush(QBrush(_BODY))
        painter.setPen(QPen(_BODY_EDGE, 1.6))
        painter.drawEllipse(center, r, r)
        painter.setPen(QPen(_TEXT))
        f = painter.font()
        f.setBold(True)
        f.setPointSizeF(max(7.0, min(10.0, r * 0.55)))
        painter.setFont(f)
        painter.drawText(
            QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r),
            Qt.AlignmentFlag.AlignCenter,
            "V{}".format(addr),
        )
        # The rotary valve selects one port at a time; note it under the hub.
        f.setBold(False)
        f.setPointSizeF(7.5)
        painter.setFont(f)
        painter.setPen(QPen(_ACTIVE if cur is not None else _MUTED))
        painter.drawText(
            QRectF(center.x() - r * 2, center.y() + r + 1, r * 4, 12),
            Qt.AlignmentFlag.AlignHCenter,
            "port {}".format(cur) if cur is not None else "port —",
        )

    # -- pumps / sample / flow ---------------------------------------------
    def _draw_flow(self, painter, area, pump_port_center, feed="overtop"):
        """Draw pump_a, the sample, and pump_out with the active legs lit.

        ``feed`` selects how the multiplexer-to-pump_a connector is routed:
        ``'overtop'`` hugs the far-left margin and the top edge (for the ibidi
        grid, whose pump port sits bottom-left), ``'direct'`` draws a straight
        line (for the MVP valves, whose root hub already faces the pump).
        """
        st = self._state or {}
        pa = st.get("pump_a")
        po = st.get("pump_out")

        x = area.left()
        w = area.width()
        # Three stacked stations sharing the column width.
        col_w = min(w, 210)
        cx = x + (w - col_w) / 2 if w > col_w else x
        top = area.top() + 12

        # Vertical rhythm: pump_a, sample, pump_out. The waste container sits
        # beside the sample, fed by both the extraction and flush legs.
        h = area.height() - 24
        station_h = h / 3.0
        pa_rect = QRectF(cx, top, col_w, station_h * 0.9)
        sample_rect = QRectF(
            cx + col_w * 0.18, top + station_h, col_w * 0.64, station_h * 0.6
        )
        po_rect = QRectF(cx, top + 2 * station_h, col_w, station_h * 0.9)
        # Waste to the right of the sample (in the right margin), vertically
        # centred on it. Extraction (pump_out) and, when wired, flush (pump_a)
        # dispense into the same physical container.
        waste_w = min(col_w * 0.5, area.right() - (cx + col_w) - 12)
        waste_rect = None
        if waste_w > 34:
            waste_h = sample_rect.height() * 0.9
            waste_rect = QRectF(
                cx + col_w + 10,
                sample_rect.center().y() - waste_h / 2,
                waste_w,
                waste_h,
            )
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
            pa_in = QPointF(pa_rect.left(), pa_rect.center().y())
            if feed == "direct":
                self._draw_link(
                    painter, pump_port_center, pa_in, leg_mux_active
                )
            else:
                self._draw_feed_line(
                    painter, pump_port_center, pa_in, leg_mux_active
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
        # The single waste container is fed from the right: pump_out's
        # extraction leg into its bottom, and pump_a's flush leg (when wired)
        # into its top.
        flush_wired = bool((self._topo or {}).get("flush_waste"))
        if waste_rect is not None:
            self._draw_link(
                painter,
                QPointF(po_rect.right(), po_rect.center().y()),
                QPointF(waste_rect.center().x(), waste_rect.bottom()),
                po_valve == "out",
            )
            if flush_wired:
                self._draw_link(
                    painter,
                    QPointF(pa_rect.right(), pa_rect.center().y()),
                    QPointF(waste_rect.center().x(), waste_rect.top()),
                    False,
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
        if waste_rect is not None:
            # One container for both sinks: sum extraction + flush volumes.
            used = sum(
                float(v.get("used_vol") or 0)
                for v in self._waste_labels.values()
            )
            total = sum(
                float(v.get("total_vol") or 0)
                for v in self._waste_labels.values()
            )
            self._draw_waste(
                painter, waste_rect,
                {"used_vol": used, "total_vol": total}, "waste",
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

    def _draw_waste(self, painter, r, label, name):
        """Draw a waste container that fills (used/total) like a reservoir.

        ``label`` is ``{used_vol, total_vol}`` (or ``None``); when a planned
        total is known the bin fills bottom-up with the consumed fraction and
        the volumes are noted, so waste reads live and against expectation just
        like the reservoirs.
        """
        painter.setBrush(QBrush(QColor(44, 40, 40)))
        painter.setPen(QPen(_WASTEBAR, 1.4))
        painter.drawRoundedRect(r, 5, 5)
        total = float((label or {}).get("total_vol") or 0)
        used = float((label or {}).get("used_vol") or 0)
        if total > 0:
            frac = max(0.0, min(1.0, used / total))
            if frac > 0:
                fh = (r.height() - 4) * frac
                painter.setBrush(QBrush(QColor(200, 96, 72, 150)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(
                    QRectF(
                        r.left() + 2, r.bottom() - 2 - fh, r.width() - 4, fh
                    ),
                    3, 3,
                )
        painter.setPen(QPen(_TEXT))
        f = painter.font()
        f.setBold(True)
        f.setPointSizeF(8.5)
        painter.setFont(f)
        painter.drawText(
            QRectF(r.left(), r.top() + 2, r.width(), r.height() * 0.55),
            Qt.AlignmentFlag.AlignCenter,
            name,
        )
        if total > 0:
            f.setBold(False)
            f.setPointSizeF(7.5)
            painter.setFont(f)
            painter.setPen(QPen(_MUTED))
            painter.drawText(
                QRectF(
                    r.left(), r.center().y(), r.width(), r.height() * 0.45
                ),
                Qt.AlignmentFlag.AlignCenter,
                "{} / {}".format(format_volume(used), format_volume(total)),
            )

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
