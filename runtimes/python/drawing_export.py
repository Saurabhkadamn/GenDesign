"""One vector sheet layout shared by web, SVG, PDF and DXF exports."""

import math
from html import escape
from pathlib import Path

import numpy as np


def symbol_vectors(primitives, characteristic, x, y, size=4):
    """Drafting symbols are vector geometry, independent of font coverage."""

    def line(a, b):
        primitives.append(
            {
                "type": "line",
                "x1": x + a[0] * size,
                "y1": y + a[1] * size,
                "x2": x + b[0] * size,
                "y2": y + b[1] * size,
                "symbol": characteristic,
            }
        )

    def circle(cx, cy, r):
        primitives.append(
            {
                "type": "circle",
                "cx": x + cx * size,
                "cy": y + cy * size,
                "radius": r * size,
                "symbol": characteristic,
            }
        )

    def arc():
        points = [
            [
                x + size * (0.5 + 0.4 * math.cos(t)),
                y + size * (0.65 - 0.4 * math.sin(t)),
            ]
            for t in np.linspace(0, math.pi, 25)
        ]
        primitives.append(
            {"type": "polyline", "points": points, "symbol": characteristic}
        )

    if characteristic == "straightness":
        line((0.1, 0.5), (0.9, 0.5))
    elif characteristic == "flatness":
        for a, b in zip(
            [(0.1, 0.65), (0.65, 0.65), (0.9, 0.35), (0.35, 0.35)],
            [(0.65, 0.65), (0.9, 0.35), (0.35, 0.35), (0.1, 0.65)],
        ):
            line(a, b)
    elif characteristic == "circularity":
        circle(0.5, 0.5, 0.35)
    elif characteristic == "cylindricity":
        circle(0.5, 0.5, 0.28)
        line((0.05, 0.8), (0.35, 0.2))
        line((0.65, 0.8), (0.95, 0.2))
    elif characteristic == "parallelism":
        line((0.15, 0.85), (0.45, 0.15))
        line((0.55, 0.85), (0.85, 0.15))
    elif characteristic == "perpendicularity":
        line((0.1, 0.8), (0.9, 0.8))
        line((0.5, 0.8), (0.5, 0.15))
    elif characteristic == "angularity":
        line((0.1, 0.8), (0.9, 0.8))
        line((0.1, 0.8), (0.6, 0.15))
    elif characteristic == "position":
        circle(0.5, 0.5, 0.28)
        line((0.05, 0.5), (0.95, 0.5))
        line((0.5, 0.05), (0.5, 0.95))
    elif characteristic in {"profile_surface", "profile_line"}:
        arc()
        if characteristic == "profile_surface":
            line((0.1, 0.65), (0.9, 0.65))
    elif characteristic in {"circular_runout", "total_runout"}:
        for offset in [0] if characteristic == "circular_runout" else [-0.15, 0.15]:
            line((0.2 + offset, 0.85), (0.75 + offset, 0.15))
            line((0.75 + offset, 0.15), (0.42 + offset, 0.28))
            line((0.75 + offset, 0.15), (0.71 + offset, 0.49))


def layout_sheet(sheet):
    width, height = (420, 297) if sheet["paper"] == "A3" else (297, 210)
    sheet.update(widthMm=width, heightMm=height)
    primitives = []
    dimension_geometry = []
    active_annotation = None

    def line(x1, y1, x2, y2, dash=False):
        primitives.append(
            {
                "type": "line",
                "x1": float(x1),
                "y1": float(y1),
                "x2": float(x2),
                "y2": float(y2),
                "dash": dash,
                "annotationId": active_annotation,
            }
        )

    def text(x, y, value, size=3, anchor="start"):
        primitives.append(
            {
                "type": "text",
                "x": float(x),
                "y": float(y),
                "text": str(value),
                "fontSize": size,
                "anchor": anchor,
                "annotationId": active_annotation,
            }
        )

    def rect(x, y, w, h):
        primitives.append(
            {
                "type": "rect",
                "x": x,
                "y": y,
                "width": w,
                "height": h,
                "annotationId": active_annotation,
            }
        )

    def issue(identifier, message):
        sheet["issues"].append({"id": identifier, "kind": "layout", "message": message})
        sheet["status"] = "unresolved"
        sheet["releaseEligible"] = False

    def arrow(tip, other):
        delta = np.array(other) - np.array(tip)
        length = np.linalg.norm(delta)
        if length < 1e-8:
            return
        direction = delta / length
        side = np.array([-direction[1], direction[0]])
        for sign in (-1, 1):
            end = np.array(tip) + direction * 2 + side * 0.65 * sign
            line(*tip, *end)

    def label(x, y, dimension, anchor="middle"):
        value = dimension["text"]
        if dimension.get("basic"):
            value = value[1:-1]
            w = max(6, len(value) * 1.65)
            left = x - w / 2 if anchor == "middle" else x - w if anchor == "end" else x
            rect(left - 1, y - 3.4, w + 2, 4.5)
        text(x, y, value, 2.8, anchor)

    rect(10, 10, width - 20, height - 20)
    reserve = 75 if sheet.get("bom") else 0
    usable_width = width - 40 - reserve
    usable_height = height - 85
    columns = 1 if len(sheet["views"]) == 1 else 2 if len(sheet["views"]) <= 4 else 3
    rows = (
        1
        if len(sheet["views"]) == 1
        else max(2, math.ceil(len(sheet["views"]) / columns))
    )
    cell_width, cell_height = usable_width / columns, usable_height / rows
    scales = [
        min(
            (cell_width - 42) / max(v["bounds"][2] - v["bounds"][0], 1e-9),
            (cell_height - 38) / max(v["bounds"][3] - v["bounds"][1], 1e-9),
        )
        for v in sheet["views"]
    ]
    allowed = [
        0.001,
        0.002,
        0.005,
        0.01,
        0.02,
        0.05,
        0.1,
        0.2,
        0.5,
        1,
        2,
        5,
        10,
        20,
        50,
        100,
    ]
    fit = min(scales, default=1)
    scale = sheet.get("requestedScale") or max(
        (s for s in allowed if s <= fit), default=0.001
    )
    if scale > fit + 1e-8:
        sheet["issues"].append(
            {
                "id": sheet["id"],
                "kind": "layout",
                "message": "Requested scale does not fit the sheet; choose a smaller scale or larger paper.",
            }
        )
        sheet["status"] = "unresolved"
        sheet["releaseEligible"] = False
    sheet["scale"] = scale
    standard_slots = (
        {"front": (0, 1), "top": (0, 0), "right": (1, 1), "isometric": (1, 0)}
        if sheet["projection"] == "third_angle"
        else {"front": (1, 0), "top": (1, 1), "right": (0, 0), "isometric": (0, 1)}
    )
    occupied = set()
    placements = {}
    for index, view in enumerate(sheet["views"]):
        col, row = (
            standard_slots.get(view["kind"], (index % columns, index // columns))
            if columns >= 2
            else (0, 0)
        )
        if (col, row) in occupied:
            col, row = next(
                (c, r)
                for r in range(rows)
                for c in range(columns)
                if (c, r) not in occupied
            )
        occupied.add((col, row))
        bounds = view["bounds"]
        cx = 20 + (col + 0.5) * cell_width
        cy = 17 + (row + 0.5) * cell_height
        ox = cx - scale * (bounds[0] + bounds[2]) / 2
        oy = cy + scale * (bounds[1] + bounds[3]) / 2
        view["sheetTransform"] = {"scale": scale, "x": ox, "y": oy}
        placements[view["id"]] = (view, ox, oy)

        def mapped(p, ox=ox, oy=oy):
            return [ox + scale * p[0], oy - scale * p[1]]

        for start, end in view.get("hatchLines", []):
            line(*mapped(start), *mapped(end))
            primitives[-1]["hatch"] = True
        for curve in view["curves"]:
            if curve.get("closed") and curve["kind"] == "circle":
                center = mapped(curve["center"])
                primitives.append(
                    {
                        "type": "circle",
                        "cx": center[0],
                        "cy": center[1],
                        "radius": curve["radius"] * scale,
                        "dash": curve["hidden"],
                    }
                )
            else:
                primitives.append(
                    {
                        "type": "polyline",
                        "points": [mapped(p) for p in curve["points"]],
                        "dash": curve["hidden"],
                    }
                )
        view_label = view["kind"].upper()
        if view["kind"] == "section":
            view_label += (
                " "
                + str(view["sectionAxis"])
                + " = "
                + str(view["sectionOffsetMm"])
                + " mm"
            )
        text(cx, cy + cell_height / 2 - 3, view_label, 2.5, "middle")
    for dimension in sheet["dimensions"]:
        active_annotation = dimension["id"]
        view, ox, oy = placements[dimension["viewId"]]
        bounds = view["bounds"]
        horizontal = np.array(view["basis"]["horizontal"])
        vertical = np.array(view["basis"]["vertical"])
        offset = dimension.get("offsetMm", 10)
        if dimension["kind"] == "extent":
            axis = "XYZ".index(dimension.get("axis", "X"))
            if abs(horizontal[axis]) > 1 - 1e-8:
                if abs(bounds[2] - bounds[0] - dimension["value"]) > 1e-5:
                    issue(
                        dimension["id"],
                        "The projected extent does not match the full-part measurement. Choose a view showing the full extent.",
                    )
                    continue
                x1, x2 = ox + scale * bounds[0], ox + scale * bounds[2]
                y = oy - scale * bounds[1] + offset
                line(x1, oy - scale * bounds[1] + 2, x1, y + 2)
                line(x2, oy - scale * bounds[1] + 2, x2, y + 2)
                line(x1, y, x2, y)
                for x, sign in [(x1, 1), (x2, -1)]:
                    line(x, y, x + sign * 2, y - 0.8)
                    line(x, y, x + sign * 2, y + 0.8)
                label((x1 + x2) / 2, y - 1, dimension)
                dimension_geometry.append(
                    {
                        "id": dimension["id"],
                        "kind": "linear",
                        "p1": [x1, oy - scale * bounds[1]],
                        "p2": [x2, oy - scale * bounds[1]],
                        "base": [(x1 + x2) / 2, y],
                        "angle": 0,
                    }
                )
            elif abs(vertical[axis]) > 1 - 1e-8:
                if abs(bounds[3] - bounds[1] - dimension["value"]) > 1e-5:
                    issue(
                        dimension["id"],
                        "The projected extent does not match the full-part measurement. Choose a view showing the full extent.",
                    )
                    continue
                y1, y2 = oy - scale * bounds[1], oy - scale * bounds[3]
                x = ox + scale * bounds[0] - offset
                line(ox + scale * bounds[0] - 2, y1, x - 2, y1)
                line(ox + scale * bounds[0] - 2, y2, x - 2, y2)
                line(x, y1, x, y2)
                for y, sign in [(y1, -1), (y2, 1)]:
                    line(x, y, x - 0.8, y + sign * 2)
                    line(x, y, x + 0.8, y + sign * 2)
                label(x - 2, (y1 + y2) / 2, dimension, "end")
                dimension_geometry.append(
                    {
                        "id": dimension["id"],
                        "kind": "linear",
                        "p1": [ox + scale * bounds[0], y1],
                        "p2": [ox + scale * bounds[0], y2],
                        "base": [x, (y1 + y2) / 2],
                        "angle": 90,
                    }
                )
            else:
                sheet["issues"].append(
                    {
                        "id": dimension["id"],
                        "kind": "dimension",
                        "message": "Extent axis is not aligned with this drawing view.",
                    }
                )
                sheet["status"] = "unresolved"
                sheet["releaseEligible"] = False
        elif dimension["kind"] in {"diameter", "radius"}:
            feature = dimension["feature"]
            origin = np.array(feature["origin"])
            if (
                abs(
                    np.dot(
                        np.array(feature["direction"]),
                        np.array(view["basis"]["normal"]),
                    )
                )
                < 1 - 1e-8
            ):
                issue(
                    dimension["id"],
                    "Diameter/radius requires a view normal to the cylinder axis.",
                )
                continue
            x, y = (
                ox + scale * np.dot(origin, horizontal),
                oy - scale * np.dot(origin, vertical),
            )
            radius = feature.get("radiusMm", 0) * scale
            tx, ty = x + radius + offset, y - radius - offset
            line(x + radius * 0.707, y - radius * 0.707, tx, ty)
            line(tx, ty, tx + 8, ty)
            arrow([x + radius * 0.707, y - radius * 0.707], [tx, ty])
            label(tx + 1, ty - 1, dimension, "start")
            dimension_geometry.append(
                {
                    "id": dimension["id"],
                    "kind": dimension["kind"],
                    "center": [x, y],
                    "radius": radius,
                    "location": [tx, ty],
                }
            )
        elif dimension["kind"] == "distance":
            first, second = dimension["feature"], dimension["secondFeature"]
            axis = "XYZ".index(dimension.get("axis", "X"))
            if abs(horizontal[axis]) < 1 - 1e-8 and abs(vertical[axis]) < 1 - 1e-8:
                issue(dimension["id"], "Distance axis must lie in the drawing plane.")
                continue
            # Both endpoints share the non-measured coordinates so the visible
            # line represents the specified component-axis distance exactly.
            p1 = np.array(second["center"])
            p2 = p1.copy()
            p1[axis] = first["origin"][axis]
            p2[axis] = second["origin"][axis]
            p1 = np.array(
                [ox + scale * np.dot(p1, horizontal), oy - scale * np.dot(p1, vertical)]
            )
            p2 = np.array(
                [ox + scale * np.dot(p2, horizontal), oy - scale * np.dot(p2, vertical)]
            )
            if abs(horizontal[axis]) > 1 - 1e-8:
                a = [p1[0], oy - scale * bounds[3] - offset]
                b = [p2[0], a[1]]
                angle = 0
                label((a[0] + b[0]) / 2, a[1] - 1, dimension)
            else:
                a = [ox + scale * bounds[2] + offset, p1[1]]
                b = [a[0], p2[1]]
                angle = 90
                label(a[0] + 2, (a[1] + b[1]) / 2, dimension, "start")
            line(*p1, *a)
            line(*p2, *b)
            line(*a, *b)
            arrow(a, b)
            arrow(b, a)
            dimension_geometry.append(
                {
                    "id": dimension["id"],
                    "kind": "linear",
                    "p1": p1.tolist(),
                    "p2": p2.tolist(),
                    "base": a,
                    "angle": angle,
                }
            )
        elif dimension["kind"] == "angle":
            normals = [
                np.array(f["direction"])
                for f in (dimension["feature"], dimension["secondFeature"])
            ]
            normal = np.array(view["basis"]["normal"])
            if any(abs(np.dot(n, normal)) > 1e-8 for n in normals):
                issue(
                    dimension["id"],
                    "Angular dimensions require both planar features to be edge-on in the selected view.",
                )
                continue
            matrix = np.array(
                [[np.dot(n, horizontal), np.dot(n, vertical)] for n in normals]
            )
            support = np.array(
                [
                    np.dot(n, f["origin"])
                    for n, f in zip(
                        normals, (dimension["feature"], dimension["secondFeature"])
                    )
                ]
            )
            center = np.linalg.solve(matrix, support)
            cx, cy = ox + scale * center[0], oy - scale * center[1]
            directions = [np.array([-row[1], -row[0]]) for row in matrix]
            if np.dot(*directions) < 0:
                directions[1] *= -1
            a1, a2 = [math.atan2(d[1], d[0]) for d in directions]
            delta = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
            radius = offset
            points = [
                [cx + radius * math.cos(t), cy + radius * math.sin(t)]
                for t in np.linspace(a1, a1 + delta, 30)
            ]
            primitives.append(
                {
                    "type": "polyline",
                    "points": points,
                    "annotationId": active_annotation,
                }
            )
            for direction in directions:
                line(
                    cx,
                    cy,
                    cx + direction[0] * (radius + 3),
                    cy + direction[1] * (radius + 3),
                )
            arrow(points[0], points[1])
            arrow(points[-1], points[-2])
            middle = a1 + delta / 2
            label(
                cx + (radius + 4) * math.cos(middle),
                cy + (radius + 4) * math.sin(middle),
                dimension,
            )
            ends = [
                [cx + d[0] * (radius + 3), cy + d[1] * (radius + 3)] for d in directions
            ]
            if delta > 0:
                ends.reverse()  # DXF uses the opposite sheet Y direction.
            dimension_geometry.append(
                {
                    "id": dimension["id"],
                    "kind": "angle",
                    "center": [cx, cy],
                    "ends": ends,
                    "base": points[len(points) // 2],
                }
            )
    active_annotation = None
    # Datum boxes and feature-control frames have leaders to actual trimmed
    # face points. No detached text rail can silently stand in for attachment.
    annotation_counts = {}

    def attachment(annotation):
        view, ox, oy = placements[annotation["viewId"]]
        point = np.array(annotation["feature"]["surfacePoint"])
        return [
            ox + scale * np.dot(point, view["basis"]["horizontal"]),
            oy - scale * np.dot(point, view["basis"]["vertical"]),
        ], view

    for datum in sheet["datums"]:
        point, view = attachment(datum)
        count = annotation_counts.get(view["id"], 0)
        annotation_counts[view["id"]] = count + 1
        dx, dy = datum.get("offset", [-14, 12])
        bx = point[0] + dx
        by = point[1] + dy + count * 8
        line(*point, bx + 4, by)
        arrow(point, [bx + 4, by])
        rect(bx, by, 8, 6)
        text(bx + 4, by + 4.2, datum["label"], 3, "middle")
    for control in sheet["controls"]:
        point, view = attachment(control)
        count = annotation_counts.get(view["id"], 0)
        annotation_counts[view["id"]] = count + 1
        value = ("Ø" if control.get("zone") == "diameter" else "") + (
            f"{control['toleranceMm']:.3f}".rstrip("0").rstrip(".") or "0"
        )
        tolerance_width = max(15, len(value) * 1.9 + 4) + (
            6 if control.get("materialCondition") in {"MMC", "LMC"} else 0
        )
        widths = [8, tolerance_width, *[8 for _ in control.get("datums", [])]]
        box_width = sum(widths)
        dx, dy = control.get("offset", [12, -12])
        transform = view["sheetTransform"]
        bounds = view["bounds"]
        bx = transform["x"] + scale * bounds[2] + dx
        by = transform["y"] - scale * bounds[3] + dy - count * 8
        line(*point, bx, by + 3)
        arrow(point, [bx, by + 3])
        rect(bx, by, box_width, 6)
        symbol_vectors(primitives, control["characteristic"], bx + 2, by + 1, 4)
        cell = bx + 8
        line(cell, by, cell, by + 6)
        text(cell + 2, by + 4.2, value, 2.8)
        if control.get("materialCondition") in {"MMC", "LMC"}:
            cx = cell + tolerance_width - 4
            primitives.append({"type": "circle", "cx": cx, "cy": by + 3, "radius": 1.8})
            text(cx, by + 4, control["materialCondition"][0], 2.5, "middle")
        cell += tolerance_width
        for datum in control.get("datums", []):
            line(cell, by, cell, by + 6)
            text(cell + 4, by + 4.2, datum, 2.8, "middle")
            cell += 8
    bom = sheet.get("bom")
    if bom:
        bx = width - 85
        by = 20
        text(bx, by, "ITEM / PART NUMBER / QTY", 2.7)
        by += 8
        for index, row in enumerate(bom.get("flat", []), 1):
            if by > height - 82:
                sheet["issues"].append(
                    {
                        "id": sheet["id"],
                        "kind": "bom",
                        "message": "BOM exceeds sheet capacity; split it across sheets.",
                    }
                )
                sheet["status"] = "unresolved"
                sheet["releaseEligible"] = False
                break
            number = str(row.get("partNumber") or "Unassigned")
            if len(number) > 23:
                issue(
                    sheet["id"],
                    "A parts-list identity exceeds its column width; use a dedicated BOM sheet.",
                )
                break
            text(bx, by, str(index), 2.5)
            text(bx + 7, by, number, 2.5)
            text(width - 20, by, str(row["quantity"]), 2.5, "end")
            by += 5
        for index, balloon in enumerate(sheet.get("balloons", [])):
            view, ox, oy = placements[balloon["viewId"]]
            point = np.array(balloon["anchor"])
            tip = [
                ox + scale * np.dot(point, view["basis"]["horizontal"]),
                oy - scale * np.dot(point, view["basis"]["vertical"]),
            ]
            cx = bx - 9
            cy = 32 + index * 8
            line(*tip, cx - 3, cy)
            arrow(tip, [cx - 3, cy])
            primitives.append(
                {
                    "type": "circle",
                    "cx": cx,
                    "cy": cy,
                    "radius": 3,
                    "bomItem": balloon["item"],
                }
            )
            text(cx, cy + 1, balloon["item"], 2.5, "middle")
    note_y = height - 60
    for note in sheet.get("notes", []):
        # Wrap without dropping supplied notes. Overflow is a visible issue.
        max_chars = max(15, int((width - 40) / 1.4))
        words = note.split()
        rows = []
        row = ""
        for word in words:
            if len(word) > max_chars:
                issue(
                    sheet["id"],
                    "A drawing note contains an unbreakable word wider than the sheet.",
                )
                break
            if len(row) + len(word) + 1 > max_chars:
                rows.append(row)
                row = word
            else:
                row = (row + " " + word).strip()
        if row:
            rows.append(row)
        for row in rows:
            if note_y > height - 34:
                issue(
                    sheet["id"],
                    "Notes exceed sheet capacity; split them across sheets.",
                )
                break
            text(20, note_y, row, 2.5)
            note_y += 4
    line(10, height - 30, width - 10, height - 30)
    text(16, height - 21, sheet["title"], 4)
    metadata = sheet["partMetadata"]
    text(
        16,
        height - 14,
        "Part "
        + str(metadata.get("partNumber") or "Unassigned")
        + " · Rev "
        + str(metadata.get("revision") or "—"),
        2.7,
    )
    text(
        width - 16,
        height - 21,
        f"{sheet['standard']} · {sheet['projection'].replace('_', ' ')} · mm · scale {scale:g}:1",
        2.7,
        "end",
    )
    text(
        width - 16,
        height - 14,
        "UNRESOLVED — EXPORT BLOCKED"
        if sheet["issues"]
        else "DRAFT — ENGINEERING REVIEW REQUIRED",
        2.5,
        "end",
    )
    # Bound all vector geometry and text approximations before exports. A
    # crowded sheet must report overflow rather than clip engineering intent.
    for primitive in primitives:
        kind = primitive["type"]
        if kind == "line":
            points = [
                (primitive["x1"], primitive["y1"]),
                (primitive["x2"], primitive["y2"]),
            ]
        elif kind == "polyline":
            points = primitive["points"]
        elif kind == "circle":
            points = [
                (
                    primitive["cx"] - primitive["radius"],
                    primitive["cy"] - primitive["radius"],
                ),
                (
                    primitive["cx"] + primitive["radius"],
                    primitive["cy"] + primitive["radius"],
                ),
            ]
        elif kind == "rect":
            points = [
                (primitive["x"], primitive["y"]),
                (
                    primitive["x"] + primitive["width"],
                    primitive["y"] + primitive["height"],
                ),
            ]
        else:
            w = len(primitive["text"]) * primitive["fontSize"] * 0.65
            left = (
                primitive["x"]
                - ({"start": 0, "middle": 0.5, "end": 1}[primitive["anchor"]]) * w
            )
            points = [
                (left, primitive["y"] - primitive["fontSize"]),
                (left + w, primitive["y"]),
            ]
        if any(
            x < 9.5 or y < 9.5 or x > width - 9.5 or y > height - 9.5 for x, y in points
        ):
            issue(
                primitive.get("annotationId") or sheet["id"],
                "Annotation or geometry extends beyond the sheet border; adjust scale, view or annotation offsets.",
            )
            break
    sheet["primitives"] = primitives
    sheet["dimensionGeometry"] = dimension_geometry
    return sheet


def svg_export(sheet):
    width, height = sheet["widthMm"], sheet["heightMm"]
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<g stroke="#1a2522" stroke-width="0.25" fill="none">',
    ]
    for p in sheet["primitives"]:
        kind = p["type"]
        dash = ' stroke-dasharray="2 1"' if p.get("dash") else ""
        if kind == "text":
            elements.append(
                f'<text x="{p["x"]}" y="{p["y"]}" font-size="{p["fontSize"]}" font-family="DejaVu Sans,sans-serif" text-anchor="{p["anchor"]}" fill="#1a2522" stroke="none">{escape(p["text"])}</text>'
            )
        elif kind == "line":
            elements.append(
                "<line "
                + " ".join(f'{k}="{p[k]}"' for k in ["x1", "y1", "x2", "y2"])
                + dash
                + "/>"
            )
        elif kind == "rect":
            elements.append(
                "<rect "
                + " ".join(f'{k}="{p[k]}"' for k in ["x", "y", "width", "height"])
                + "/>"
            )
        elif kind == "circle":
            elements.append(
                "<circle "
                + " ".join(
                    f'{k}="{p[source]}"'
                    for k, source in [("cx", "cx"), ("cy", "cy"), ("r", "radius")]
                )
                + dash
                + "/>"
            )
        elif kind == "polyline":
            elements.append(
                '<polyline points="'
                + " ".join(f"{x},{y}" for x, y in p["points"])
                + '"'
                + dash
                + "/>"
            )
    return "\n".join([*elements, "</g></svg>"])


def pdf_export(sheet, path):
    import matplotlib
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font = Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf"
    pdfmetrics.registerFont(TTFont("FormaDrawing", str(font)))
    from fontTools.ttLib import TTFont as FontInspection

    cmap = FontInspection(font).getBestCmap()
    missing = {
        c
        for p in sheet["primitives"]
        if p["type"] == "text"
        for c in p["text"]
        if ord(c) not in cmap
    }
    if missing:
        raise ValueError(
            "Drawing text contains unsupported PDF glyphs: "
            + ", ".join(f"U+{ord(c):04X}" for c in sorted(missing))
        )
    mm = 72 / 25.4
    width, height = sheet["widthMm"], sheet["heightMm"]
    document = canvas.Canvas(str(path), pagesize=(width * mm, height * mm), invariant=1)
    document.setTitle(sheet["title"])
    document.setAuthor("Forma")
    document.scale(mm, mm)
    document.setLineWidth(0.25)
    for p in sheet["primitives"]:
        document.setDash([2, 1] if p.get("dash") else [])
        if p["type"] == "text":
            document.setFont("FormaDrawing", p["fontSize"])
            {
                "start": document.drawString,
                "middle": document.drawCentredString,
                "end": document.drawRightString,
            }[p["anchor"]](p["x"], height - p["y"], p["text"])
        elif p["type"] == "line":
            document.line(p["x1"], height - p["y1"], p["x2"], height - p["y2"])
        elif p["type"] == "rect":
            document.rect(
                p["x"], height - p["y"] - p["height"], p["width"], p["height"]
            )
        elif p["type"] == "circle":
            document.circle(p["cx"], height - p["cy"], p["radius"])
        elif p["type"] == "polyline":
            path_obj = document.beginPath()
            path_obj.moveTo(p["points"][0][0], height - p["points"][0][1])
            for x, y in p["points"][1:]:
                path_obj.lineTo(x, height - y)
            document.drawPath(path_obj)
    document.showPage()
    document.save()


def dxf_export(sheet, path):
    import ezdxf
    from ezdxf.enums import TextEntityAlignment

    document = ezdxf.new("R2018", setup=True)
    document.units = 4
    document.layers.new("HIDDEN", dxfattribs={"linetype": "DASHED", "color": 8})
    document.layers.new("DRAWING")
    document.layers.new("ANNOTATION")
    document.appids.new("FORMA")
    document.appids.new("FORMA_INTENT")
    document.header["$INSUNITS"] = 4
    model = document.modelspace()
    height = sheet["heightMm"]
    dimensions = {d["id"]: d for d in sheet["dimensions"]}
    native_ids = {d["id"] for d in sheet.get("dimensionGeometry", [])}
    for p in sheet["primitives"]:
        if p.get("annotationId") in native_ids:
            continue
        attr = {"layer": "HIDDEN" if p.get("dash") else "DRAWING"}
        if p["type"] == "text":
            entity = model.add_text(
                p["text"], dxfattribs={"height": p["fontSize"], "layer": "ANNOTATION"}
            )
            entity.set_placement(
                (p["x"], height - p["y"]),
                align={
                    "start": TextEntityAlignment.LEFT,
                    "middle": TextEntityAlignment.CENTER,
                    "end": TextEntityAlignment.RIGHT,
                }[p["anchor"]],
            )
        elif p["type"] == "line":
            model.add_line(
                (p["x1"], height - p["y1"]),
                (p["x2"], height - p["y2"]),
                dxfattribs=attr,
            )
        elif p["type"] == "circle":
            model.add_circle((p["cx"], height - p["cy"]), p["radius"], dxfattribs=attr)
        elif p["type"] == "rect":
            model.add_lwpolyline(
                [
                    (p["x"], height - p["y"]),
                    (p["x"] + p["width"], height - p["y"]),
                    (p["x"] + p["width"], height - p["y"] - p["height"]),
                    (p["x"], height - p["y"] - p["height"]),
                ],
                close=True,
                dxfattribs=attr,
            )
        elif p["type"] == "polyline":
            model.add_lwpolyline(
                [(x, height - y) for x, y in p["points"]], dxfattribs=attr
            )

    # Editable native DIMENSION entities, with explicit sheet-to-model scale.
    def xy(point):
        return (point[0], height - point[1])

    for geometry in sheet.get("dimensionGeometry", []):
        dimension = dimensions[geometry["id"]]
        override = {
            "dimlfac": 1 / sheet["scale"],
            "dimtxt": 2.8,
            "dimasz": 2,
            "dimexo": 1,
            "dimexe": 2,
            "dimdec": 3,
            "dimzin": 8,
            "dimtdec": 3,
            "dimtzin": 8,
        }
        if dimension.get('upperTolerance') is not None:
            override.update(dimtol=1,dimtp=dimension['upperTolerance'],dimtm=dimension['lowerTolerance'],dimtfac=.8,dimtolj=1)
        override['dimpost']={'diameter':'%%c<>','radius':'R<>'}.get(geometry['kind'],'<>')
        kwargs = {
            # Preserve the measurement placeholder so CAD endpoint edits do
            # not keep an obsolete literal dimension value.
            "text": '<>',
            "override": override,
            "dxfattribs": {"layer": "ANNOTATION"},
        }
        if geometry["kind"] == "linear":
            native = model.add_linear_dim(
                xy(geometry["base"]),
                xy(geometry["p1"]),
                xy(geometry["p2"]),
                angle=geometry["angle"],
                **kwargs,
            )
        elif geometry["kind"] == "angle":
            kwargs["override"] = {**override, "dimlfac": 1}
            native = model.add_angular_dim_2l(
                xy(geometry["base"]),
                (xy(geometry["center"]), xy(geometry["ends"][0])),
                (xy(geometry["center"]), xy(geometry["ends"][1])),
                **kwargs,
            )
        else:
            creator = (
                model.add_diameter_dim
                if geometry["kind"] == "diameter"
                else model.add_radius_dim
            )
            native = creator(
                xy(geometry["center"]),
                radius=geometry["radius"],
                location=xy(geometry["location"]),
                **kwargs,
            )
        native.render()
        entity = native.dimension
        if dimension.get('basic'):
            # DXF has no portable basic-dimension flag. Frame the rendered text
            # and retain its engineer-authored intent separately in XDATA.
            from ezdxf import bbox
            block=document.blocks[entity.dxf.geometry]
            bounds=bbox.extents(e for e in block if e.dxftype() in {'TEXT','MTEXT'})
            if bounds.has_data:
                low,high=bounds.extmin,bounds.extmax
                block.add_lwpolyline([(low.x-.7,low.y-.7),(high.x+.7,low.y-.7),
                                     (high.x+.7,high.y+.7),(low.x-.7,high.y+.7)],close=True)
        entity.set_xdata('FORMA_INTENT',[(1000,'basic' if dimension.get('basic') else 'size'),
            (1040,dimension.get('upperTolerance') or 0),(1040,dimension.get('lowerTolerance') or 0)])
        entity.set_xdata(
            "FORMA",
            [
                (1000, dimension["id"]),
                (1040, dimension["value"]),
                (1000, dimension["unit"]),
            ],
        )
    # Angular annotations retain explicit native plane-angle evidence; add
    # native angular DIMENSION geometry when its construction is available.
    for dimension in sheet["dimensions"]:
        if dimension["id"] in native_ids:
            continue
        entity = model.add_point(
            (0, 0), dxfattribs={"layer": "ANNOTATION", "invisible": 1}
        )
        entity.set_xdata(
            "FORMA",
            [
                (1000, dimension["id"]),
                (1040, dimension["value"]),
                (1000, dimension["unit"]),
            ],
        )
    document.saveas(path)


def export_drawings(document, output):
    artifacts = []
    for sheet in document["sheets"]:
        layout_sheet(sheet)
        if sheet["issues"]:
            continue
        stem = "drawing-" + sheet["id"]
        paths = [output / (stem + "." + ext) for ext in ("svg", "pdf", "dxf")]
        try:
            paths[0].write_text(svg_export(sheet), encoding="utf-8")
            pdf_export(sheet, paths[1])
            dxf_export(sheet, paths[2])
        except ValueError as error:
            for path in paths:
                path.unlink(missing_ok=True)
            sheet["issues"].append(
                {"id": sheet["id"], "kind": "export", "message": str(error)}
            )
            sheet["status"] = "unresolved"
            sheet["releaseEligible"] = False
            continue
        for ext in ["svg", "pdf", "dxf"]:
            path = output / (stem + "." + ext)
            artifacts.append(
                {
                    "name": path.name,
                    "kind": "drawing",
                    "componentId": sheet["componentId"],
                    "bytes": path.stat().st_size,
                }
            )
    document["status"] = (
        "unresolved" if any(s["issues"] for s in document["sheets"]) else "draft"
    )
    return artifacts
