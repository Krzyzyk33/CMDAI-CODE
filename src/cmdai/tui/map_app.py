"""Standalone Textual Mind Map & Dependency Graph app (launched in a new window via /map)."""

from __future__ import annotations

import math
import os
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static

try:
    from cmdai.indexer.repo_map import build_repo_graph
except ImportError:
    _repo_src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _repo_src not in sys.path:
        sys.path.insert(0, _repo_src)
    from cmdai.indexer.repo_map import build_repo_graph


_KIND_COLOR = {
    "root": "#e6edf3",
    "dir": "#d29922",
    "file": "#58a6ff",
}


def _compute_layout(nodes: List[Dict[str, Any]], edges: List[Tuple[str, str, str]]) -> Dict[str, Tuple[float, float]]:
    """Compute a spacious, collision-free 2D layout in virtual terminal units."""
    pos: Dict[str, Tuple[float, float]] = {}
    if not nodes:
        return pos

    pos["root"] = (0.0, 0.0)
    dirs = [n for n in nodes if n["kind"] == "dir"]
    files = [n for n in nodes if n["kind"] == "file"]

    parent_map: Dict[str, str] = {}
    for src, dst, kind in edges:
        if kind == "contains":
            parent_map[dst] = src

    by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for f in files:
        pid = parent_map.get(f["id"], "root")
        by_parent.setdefault(pid, []).append(f)

    # Place directories in a wide ring around root
    dir_count = max(1, len(dirs))
    for i, d in enumerate(dirs):
        angle = (2.0 * math.pi * i / dir_count) - math.pi / 2
        rx = 42.0
        ry = 14.0
        pos[d["id"]] = (rx * math.cos(angle), ry * math.sin(angle))

    # Arrange each directory's files in neat staggered sub-columns outward from root
    for pid, flist in by_parent.items():
        px, py = pos.get(pid, (0.0, 0.0))
        if pid == "root":
            # Place root-level files in a clean arc above/left
            for idx, f in enumerate(flist):
                ang = -math.pi + (math.pi * (idx + 1) / (len(flist) + 1))
                pos[f["id"]] = (34.0 * math.cos(ang), 12.0 * math.sin(ang) - 4.0)
            continue

        # Outward unit vector from root through directory
        mag = math.hypot(px, py) or 1.0
        ux, uy = px / mag, py / mag
        cols = 3 if len(flist) <= 12 else 4
        col_step = 28.0
        row_step = 4.0
        for idx, f in enumerate(flist):
            col = idx % cols
            row = idx // cols
            ox = px + ux * 20.0 + (col - (cols - 1) / 2.0) * col_step
            oy = py + uy * 6.0 + (row - (len(flist) / cols) / 2.0) * row_step
            pos[f["id"]] = (ox, oy)

    # Collision-avoidance pass: keep label boxes (width ~28, height ~4) separated
    ids = [n["id"] for n in nodes]
    for _ in range(80):
        moved = False
        for i in range(len(ids)):
            a = ids[i]
            ax, ay = pos[a]
            for j in range(i + 1, len(ids)):
                b = ids[j]
                bx, by = pos[b]
                dx = bx - ax
                dy = by - ay
                min_w = 28.0
                min_h = 4.0
                if abs(dx) < min_w and abs(dy) < min_h:
                    moved = True
                    push_x = (min_w - abs(dx)) * 0.4 * (1.0 if dx >= 0 else -1.0)
                    push_y = (min_h - abs(dy)) * 0.6 * (1.0 if dy >= 0 else -1.0)
                    if abs(dx) / min_w > abs(dy) / min_h:
                        push_y = 0.0
                    else:
                        push_x = 0.0
                    if a != "root":
                        pos[a] = (ax - push_x, ay - push_y)
                    if b != "root":
                        pos[b] = (bx + push_x, by + push_y)
                    ax, ay = pos[a]
        if not moved:
            break
    return pos


class GraphCanvas(Static):
    """Interactive 2D ASCII/Unicode node-and-edge canvas."""

    can_focus = True
    allow_select = False

    def __init__(self, graph: Dict[str, Any], **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self.nodes: List[Dict[str, Any]] = graph.get("nodes", [])
        self.edges: List[Tuple[str, str, str]] = graph.get("edges", [])
        self.positions: Dict[str, Tuple[float, float]] = _compute_layout(self.nodes, self.edges)
        self.selected_id: str = self.nodes[0]["id"] if self.nodes else ""
        self.pan_x: float = 0.0
        self.pan_y: float = 0.0
        self.zoom: float = 1.0
        self.imports_only: bool = False
        self._screen_coords: Dict[str, Tuple[int, int]] = {}
        self._drag_last: Optional[Tuple[int, int]] = None
        self._drag_moved: bool = False

    def on_mount(self) -> None:
        self.call_after_refresh(self.redraw)

    def on_resize(self, event: events.Resize) -> None:
        self.redraw()

    def _to_screen(self, nx: float, ny: float, w: int, h: int) -> Tuple[int, int]:
        cx = w / 2.0 + self.pan_x
        cy = h / 2.0 + self.pan_y
        sx = int(round(cx + nx * self.zoom))
        sy = int(round(cy + ny * self.zoom))
        return sx, sy

    # 4-bit box-drawing mask: UP=1, RIGHT=2, DOWN=4, LEFT=8
    _MASK_TO_CHAR = {
        0: " ",
        1: "│", 4: "│", 5: "│",
        2: "─", 8: "─", 10: "─",
        6: "┌", 12: "┐", 3: "└", 9: "┘",
        7: "├", 13: "┤", 14: "┬", 11: "┴",
        15: "┼",
    }

    def _add_bit(
        self,
        masks: List[List[int]],
        colors: List[List[str]],
        x: int,
        y: int,
        bit: int,
        color: str,
        priority: bool,
    ) -> None:
        h = len(masks)
        w = len(masks[0]) if h else 0
        if 0 <= x < w and 0 <= y < h:
            masks[y][x] |= bit
            if priority or not colors[y][x]:
                colors[y][x] = color

    def _draw_ortho_edge(
        self,
        masks: List[List[int]],
        colors: List[List[str]],
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: str,
        priority: bool,
        arrow: bool = False,
    ) -> Optional[Tuple[int, int, str]]:
        """Route an orthogonal box-drawing edge (Z-shaped) between (x0, y0) and (x1, y1)."""
        if x0 == x1 and y0 == y1:
            return None

        def h_seg(xa: int, xb: int, y: int) -> None:
            if xa == xb:
                return
            step = 1 if xb > xa else -1
            for x in range(xa, xb, step):
                self._add_bit(masks, colors, x, y, 2 if step > 0 else 8, color, priority)
                self._add_bit(masks, colors, x + step, y, 8 if step > 0 else 2, color, priority)

        def v_seg(x: int, ya: int, yb: int) -> None:
            if ya == yb:
                return
            step = 1 if yb > ya else -1
            for y in range(ya, yb, step):
                self._add_bit(masks, colors, x, y, 4 if step > 0 else 1, color, priority)
                self._add_bit(masks, colors, x, y + step, 1 if step > 0 else 4, color, priority)

        if y0 == y1:
            h_seg(x0, x1, y0)
            arr_ch = "▸" if x1 > x0 else "◂"
            arr_pos = (x1 - (1 if x1 > x0 else -1), y1, arr_ch)
        elif x0 == x1:
            v_seg(x0, y0, y1)
            arr_ch = "▾" if y1 > y0 else "▴"
            arr_pos = (x1, y1 - (1 if y1 > y0 else -1), arr_ch)
        else:
            mx = (x0 + x1) // 2
            h_seg(x0, mx, y0)
            v_seg(mx, y0, y1)
            h_seg(mx, x1, y1)
            if x1 != mx:
                arr_ch = "▸" if x1 > mx else "◂"
                arr_pos = (x1 - (1 if x1 > mx else -1), y1, arr_ch)
            else:
                arr_ch = "▾" if y1 > y0 else "▴"
                arr_pos = (x1, y1 - (1 if y1 > y0 else -1), arr_ch)

        return arr_pos if arrow else None

    def redraw(self) -> None:
        w = max(40, self.size.width or 80)
        h = max(18, self.size.height or 24)
        masks = [[0 for _ in range(w)] for _ in range(h)]
        grid = [[" " for _ in range(w)] for _ in range(h)]
        colors = [["" for _ in range(w)] for _ in range(h)]
        self._screen_coords = {}

        for n in self.nodes:
            nx, ny = self.positions.get(n["id"], (0.0, 0.0))
            self._screen_coords[n["id"]] = self._to_screen(nx, ny, w, h)

        arrows: List[Tuple[int, int, str, str]] = []
        active_edges: List[Tuple[str, str, str]] = []
        for src, dst, kind in self.edges:
            if self.imports_only and kind != "import":
                continue
            if src == self.selected_id or dst == self.selected_id:
                active_edges.append((src, dst, kind))
                continue
            if src in self._screen_coords and dst in self._screen_coords:
                x0, y0 = self._screen_coords[src]
                x1, y1 = self._screen_coords[dst]
                ec = "#30363d" if kind == "contains" else "#8957e5"
                ap = self._draw_ortho_edge(masks, colors, x0, y0, x1, y1, ec, priority=False, arrow=(kind == "import"))
                if ap:
                    arrows.append((ap[0], ap[1], ap[2], ec))

        for src, dst, kind in active_edges:
            if src in self._screen_coords and dst in self._screen_coords:
                x0, y0 = self._screen_coords[src]
                x1, y1 = self._screen_coords[dst]
                ec = "#f0883e" if kind == "import" else "#58a6ff"
                ap = self._draw_ortho_edge(masks, colors, x0, y0, x1, y1, ec, priority=True, arrow=(kind == "import"))
                if ap:
                    arrows.append((ap[0], ap[1], ap[2], ec))

        for y in range(h):
            for x in range(w):
                m = masks[y][x]
                if m:
                    grid[y][x] = self._MASK_TO_CHAR.get(m, "┼")

        for ax, ay, ach, acol in arrows:
            if 0 <= ax < w and 0 <= ay < h:
                grid[ay][ax] = ach
                colors[ay][ax] = acol

        # Draw nodes & labels on top so lines never slice through text
        for n in self.nodes:
            nid = n["id"]
            if nid not in self._screen_coords:
                continue
            sx, sy = self._screen_coords[nid]
            if not (0 <= sx < w and 0 <= sy < h):
                continue
            is_sel = nid == self.selected_id
            col = "#7ee787" if is_sel else _KIND_COLOR.get(n["kind"], "#58a6ff")
            grid[sy][sx] = "◉" if is_sel else "●"
            colors[sy][sx] = f"bold {col}" if is_sel else col

            label = n["label"][:20]
            ly = sy + 1 if sy + 1 < h else sy
            lx_start = max(0, min(w - len(label), sx - len(label) // 2))
            # Clear 1-char margin around label so lines never touch text
            for pad_x in range(max(0, lx_start - 1), min(w, lx_start + len(label) + 1)):
                if ly != sy or pad_x != sx:
                    grid[ly][pad_x] = " "
            for idx, ch in enumerate(label):
                lx = lx_start + idx
                if 0 <= lx < w and (ly != sy or lx != sx):
                    grid[ly][lx] = ch
                    colors[ly][lx] = "bold #ffffff" if is_sel else "#c9d1d9"

        text = Text()
        for y in range(h):
            row_chars = grid[y]
            row_cols = colors[y]
            i = 0
            while i < w:
                col = row_cols[i]
                j = i + 1
                while j < w and row_cols[j] == col:
                    j += 1
                seg = "".join(row_chars[i:j])
                text.append(seg, style=col if col else "#c9d1d9")
                i = j
            if y < h - 1:
                text.append("\n")
        self.update(text)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button in (1, 3):
            self._drag_last = (event.x, event.y)
            self._drag_moved = False
            self.capture_mouse()
            event.stop()
            event.prevent_default()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag_last is not None:
            dx = event.x - self._drag_last[0]
            dy = event.y - self._drag_last[1]
            if dx or dy:
                self._drag_moved = True
                self.pan_x += dx
                self.pan_y += dy
                self._drag_last = (event.x, event.y)
                self.redraw()
            event.stop()
            event.prevent_default()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag_last is not None:
            self._drag_last = None
            self.release_mouse()
            event.stop()
            event.prevent_default()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.zoom = min(2.5, self.zoom + 0.12)
        self.redraw()
        event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.zoom = max(0.35, self.zoom - 0.12)
        self.redraw()
        event.stop()

    def on_click(self, event: events.Click) -> None:
        if self._drag_moved:
            self._drag_moved = False
            return
        best_id = ""
        best_dist = 9999.0
        for nid, (sx, sy) in self._screen_coords.items():
            d = math.hypot(event.x - sx, (event.y - sy) * 1.8)
            if d < best_dist and d <= 6.0:
                best_dist = d
                best_id = nid
        if best_id:
            self.selected_id = best_id
            self.redraw()
            if hasattr(self.app, "update_inspector"):
                self.app.update_inspector(best_id)


class CMDAIMapApp(App):
    """Textual Mind Map & Dependency Graph viewer."""

    CSS = """
    Screen {
        background: #0d1117;
        color: #e6edf3;
    }
    #map-topbar {
        height: 1;
        background: #161b22;
        color: #e6edf3;
        padding: 0 2;
    }
    #map-body {
        height: 1fr;
    }
    #map-canvas-wrap {
        width: 1fr;
        height: 1fr;
        background: #0d1117;
        border: round #30363d;
    }
    #map-canvas {
        width: 1fr;
        height: 1fr;
    }
    #map-legend {
        height: 1;
        background: #161b22;
        padding: 0 2;
        color: #8b949e;
    }
    #map-inspector {
        width: 38;
        height: 1fr;
        background: #161b22;
        border-left: solid #30363d;
        padding: 1 2;
    }
    #map-footer {
        height: 1;
        background: #161b22;
        color: #8b949e;
        padding: 0 2;
    }
    """

    BINDINGS = [
        Binding("escape", "quit", "Close"),
        Binding("q", "quit", "Close"),
        Binding("n", "next_node", "Next node"),
        Binding("tab", "next_node", "Next node"),
        Binding("p", "prev_node", "Prev node"),
        Binding("up", "pan_up", "Pan up"),
        Binding("down", "pan_down", "Pan down"),
        Binding("left", "pan_left", "Pan left"),
        Binding("right", "pan_right", "Pan right"),
        Binding("plus", "zoom_in", "Zoom in"),
        Binding("equals", "zoom_in", "Zoom in"),
        Binding("minus", "zoom_out", "Zoom out"),
        Binding("r", "reset_view", "Reset"),
        Binding("f", "toggle_imports", "Imports filter"),
    ]

    def __init__(self, workdir: str = ".") -> None:
        super().__init__()
        self.workdir = os.path.abspath(workdir or ".")
        self.graph = build_repo_graph(self.workdir)

    def compose(self) -> ComposeResult:
        nc = len(self.graph.get("nodes", []))
        ec = len(self.graph.get("edges", []))
        yield Static(
            f"[bold #58a6ff]CMDAI CODE[/]  •  [bold #e6edf3]MIND MAP[/]  "
            f"[dim]{self.workdir}  ({nc} nodes, {ec} edges)[/]",
            id="map-topbar",
        )
        with Horizontal(id="map-body"):
            with Vertical(id="map-canvas-wrap"):
                yield GraphCanvas(self.graph, id="map-canvas")
                yield Static(
                    "[#e6edf3]●[/] Workspace  "
                    "[#d29922]●[/] Directory  "
                    "[#58a6ff]●[/] File  "
                    "[#7ee787]◉[/] Selected  "
                    "[#6e40c9]·[/] Import  "
                    "[#f0883e]─[/] Active edge",
                    id="map-legend",
                )
            with VerticalScroll(id="map-inspector"):
                yield Static("", id="map-inspector-text")
        yield Static(
            "[dim]Click node or n/p: select  •  Arrows: pan  •  Scroll or +/-: zoom  •  f: imports only  •  r: reset  •  Esc/q: close[/]",
            id="map-footer",
        )

    def on_mount(self) -> None:
        canvas = self.query_one("#map-canvas", GraphCanvas)
        canvas.focus()
        if canvas.selected_id:
            self.update_inspector(canvas.selected_id)

    def update_inspector(self, node_id: str) -> None:
        node = next((n for n in self.graph.get("nodes", []) if n["id"] == node_id), None)
        if not node:
            return
        edges = self.graph.get("edges", [])
        out_imps = [dst.replace("file:", "") for src, dst, k in edges if src == node_id and k == "import"]
        in_imps = [src.replace("file:", "") for src, dst, k in edges if dst == node_id and k == "import"]

        lines = [
            f"[bold #58a6ff]{node['label']}[/]",
            f"[dim]Kind:[/] {node['kind']}",
            f"[dim]Path:[/] {node['path']}",
            "",
        ]
        if out_imps:
            lines.append("[bold #f0883e]Imports:[/]")
            for x in out_imps[:10]:
                lines.append(f"  → {x}")
            lines.append("")
        if in_imps:
            lines.append("[bold #6e40c9]Imported by:[/]")
            for x in in_imps[:10]:
                lines.append(f"  ← {x}")
            lines.append("")
        syms = node.get("symbols") or []
        if syms:
            lines.append("[bold #7ee787]AST Symbols:[/]")
            for s in syms[:14]:
                lines.append(f"  • {s}")
        self.query_one("#map-inspector-text", Static).update("\n".join(lines))

    def _cycle(self, step: int) -> None:
        canvas = self.query_one("#map-canvas", GraphCanvas)
        nodes = canvas.nodes
        if not nodes:
            return
        ids = [n["id"] for n in nodes]
        idx = ids.index(canvas.selected_id) if canvas.selected_id in ids else 0
        canvas.selected_id = ids[(idx + step) % len(ids)]
        canvas.redraw()
        self.update_inspector(canvas.selected_id)

    def action_next_node(self) -> None:
        self._cycle(1)

    def action_prev_node(self) -> None:
        self._cycle(-1)

    def action_pan_up(self) -> None:
        c = self.query_one("#map-canvas", GraphCanvas)
        c.pan_y += 2.0
        c.redraw()

    def action_pan_down(self) -> None:
        c = self.query_one("#map-canvas", GraphCanvas)
        c.pan_y -= 2.0
        c.redraw()

    def action_pan_left(self) -> None:
        c = self.query_one("#map-canvas", GraphCanvas)
        c.pan_x += 4.0
        c.redraw()

    def action_pan_right(self) -> None:
        c = self.query_one("#map-canvas", GraphCanvas)
        c.pan_x -= 4.0
        c.redraw()

    def action_zoom_in(self) -> None:
        c = self.query_one("#map-canvas", GraphCanvas)
        c.zoom = min(2.4, c.zoom + 0.15)
        c.redraw()

    def action_zoom_out(self) -> None:
        c = self.query_one("#map-canvas", GraphCanvas)
        c.zoom = max(0.5, c.zoom - 0.15)
        c.redraw()

    def action_reset_view(self) -> None:
        c = self.query_one("#map-canvas", GraphCanvas)
        c.pan_x = 0.0
        c.pan_y = 0.0
        c.zoom = 1.0
        c.imports_only = False
        c.redraw()

    def action_toggle_imports(self) -> None:
        c = self.query_one("#map-canvas", GraphCanvas)
        c.imports_only = not c.imports_only
        c.redraw()


if __name__ == "__main__":
    target_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    CMDAIMapApp(workdir=target_dir).run()
