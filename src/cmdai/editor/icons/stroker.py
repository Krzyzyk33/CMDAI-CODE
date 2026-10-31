import math
import xml.etree.ElementTree as ET
from typing import List, Tuple
from fontTools.svgLib.path import parse_path
from fontTools.pens.recordingPen import RecordingPen

def extract_path_strings(svg_path: str) -> List[str]:
    tree = ET.parse(svg_path)
    root = tree.getroot()
    paths = []

    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        if tag == "path":
            d = elem.get("d", "")
            stroke = elem.get("stroke", "")
            if d and stroke != "none":
                paths.append(d)
        elif tag == "circle":
            cx = float(elem.get("cx", 0))
            cy = float(elem.get("cy", 0))
            r = float(elem.get("r", 0))
            if r > 0:
                d = f"M {cx-r} {cy} a {r} {r} 0 1 0 {r*2} 0 a {r} {r} 0 1 0 {-r*2} 0"
                paths.append(d)
    return paths

def flatten_pen_commands(commands: List[Tuple], num_steps: int = 8) -> List[List[Tuple[float, float]]]:
    subpaths = []
    current_subpath = []
    curr_pt = (0.0, 0.0)

    def cubic_bezier(p0, p1, p2, p3, t):
        u = 1.0 - t
        tt = t * t
        uu = u * u
        uuu = uu * u
        ttt = tt * t
        x = uuu * p0[0] + 3 * uu * t * p1[0] + 3 * u * tt * p2[0] + ttt * p3[0]
        y = uuu * p0[1] + 3 * uu * t * p1[1] + 3 * u * tt * p2[1] + ttt * p3[1]
        return (x, y)

    def quad_bezier(p0, p1, p2, t):
        u = 1.0 - t
        x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
        y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
        return (x, y)

    for cmd, pts in commands:
        if cmd == "moveTo":
            if current_subpath:
                subpaths.append(current_subpath)
            curr_pt = pts[0]
            current_subpath = [curr_pt]
        elif cmd == "lineTo":
            curr_pt = pts[0]
            current_subpath.append(curr_pt)
        elif cmd == "curveTo":
            p1, p2, p3 = pts[0], pts[1], pts[2]
            for step in range(1, num_steps + 1):
                t = step / float(num_steps)
                interp = cubic_bezier(curr_pt, p1, p2, p3, t)
                current_subpath.append(interp)
            curr_pt = p3
        elif cmd == "qCurveTo":
            p1, p2 = pts[0], pts[1]
            for step in range(1, num_steps + 1):
                t = step / float(num_steps)
                interp = quad_bezier(curr_pt, p1, p2, t)
                current_subpath.append(interp)
            curr_pt = p2
        elif cmd in ("closePath", "endPath"):
            if current_subpath:
                subpaths.append(current_subpath)
                current_subpath = []

    if current_subpath:
        subpaths.append(current_subpath)

    return subpaths

def make_circle_contour(cx: float, cy: float, r: float, num_pts: int = 12) -> List[Tuple[float, float]]:
    pts = []
    for i in range(num_pts):
        angle = (2.0 * math.pi * i) / float(num_pts)
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts

def make_segment_quad(p1: Tuple[float, float], p2: Tuple[float, float], r: float) -> List[Tuple[float, float]]:
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return []
    nx = -dy / length * r
    ny = dx / length * r
    return [
        (p1[0] + nx, p1[1] + ny),
        (p2[0] + nx, p2[1] + ny),
        (p2[0] - nx, p2[1] - ny),
        (p1[0] - nx, p1[1] - ny),
    ]

def stroke_svg_file(svg_path: str, stroke_width: float = 2.0) -> List[List[Tuple[float, float]]]:
    path_strings = extract_path_strings(svg_path)
    r = stroke_width / 2.0
    all_contours = []

    for d in path_strings:
        rec_pen = RecordingPen()
        try:
            parse_path(d, rec_pen)
        except Exception:
            continue

        subpaths = flatten_pen_commands(rec_pen.value)
        for sp in subpaths:
            if not sp:
                continue
            if len(sp) == 1:
                all_contours.append(make_circle_contour(sp[0][0], sp[0][1], r))
                continue

            for pt in sp:
                all_contours.append(make_circle_contour(pt[0], pt[1], r))

            for i in range(len(sp) - 1):
                quad = make_segment_quad(sp[i], sp[i + 1], r)
                if quad:
                    all_contours.append(quad)

    return all_contours
