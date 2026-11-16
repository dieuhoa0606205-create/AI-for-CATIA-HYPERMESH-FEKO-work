from __future__ import annotations

import csv
import os
import platform
import math
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _tcl_quote(value: str) -> str:
    """Quote one path argument for a Tcl command without allowing substitution."""
    text = str(value).replace("\\", "/")
    for source, target in (
        ("[", "\\["),
        ("]", "\\]"),
        ("$", "\\$"),
        ('"', '\\"'),
    ):
        text = text.replace(source, target)
    return '"%s"' % text


BRIDGE_VERSION = "0.29.0"
MAX_NODES_PER_CALL = 5000
MAX_ELEMENTS_PER_CALL = 5000
MAX_ELEMENT_NODES = 20
MAX_GEOMETRY_IDS_PER_CALL = 10000
MAX_CAD_OPTIONS = 20
# Meshing and quality queries operate on element lists that scale with the mesh.
# The hard bound stays high; responses echo only a sample of IDs.
MAX_MESH_ELEMENT_IDS = _env_positive_int("HW_MCP_MAX_MESH_ELEMENT_IDS", 5_000_000)
ID_SAMPLE_LIMIT = _env_positive_int("HW_MCP_ID_SAMPLE_LIMIT", 2000)
PAIR_SAMPLE_LIMIT = _env_positive_int("HW_MCP_PAIR_SAMPLE_LIMIT", 200)
REVIEW_COLOR_A = _env_positive_int("HW_MCP_REVIEW_COLOR_A", 3)
REVIEW_COLOR_B = _env_positive_int("HW_MCP_REVIEW_COLOR_B", 4)
# One colour per surface inside an overlap group. Colours 1 and 2 are avoided
# because they render close to the viewport background on this build.
REVIEW_GROUP_COLORS = (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18)
BOUNDARY_SAMPLE_PARAMS = "0 0.125 0.25 0.375 0.5 0.625 0.75 0.875 1"
TCL_NUMBER_PATTERN = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")

CAD_TRANSLATORS = {
    ".step": "step_ct",
    ".stp": "step_ct",
    ".iges": "iges_altair",
    ".igs": "iges_altair",
    ".x_t": "parasolid_parasolid",
    ".x_b": "parasolid_parasolid",
    ".catpart": "catia_ct",
    ".catproduct": "catia_ct",
}
ALLOWED_CAD_TRANSLATORS = set(CAD_TRANSLATORS.values())
SURFACE_ELEMENT_TYPES = {
    "tria": 0,
    "quad": 1,
    "mixed": 2,
    "right_tria": 3,
    "quad_only": 4,
}
SURFACE_DENSITY_MODES = {
    "proximity_curvature": 34,
    "curvature": 35,
    "proximity_curvature_free_edge": 36,
    "curvature_free_edge": 37,
}
# Deprecated alias.  These are Density/size-control modes, not the GUI
# Mesh type (Free/Mapped); keep the name for older callers only.
SURFACE_MESH_TYPES = SURFACE_DENSITY_MODES



ENTITY_CLASS_NAMES = {
    "node": "Node",
    "element": "Element",
    "component": "Component",
    "material": "Material",
    "property": "Property",
    "loadcol": "Loadcol",
    "loadstep": "Loadstep",
    "set": "Set",
    "surface": "Surface",
    "solid": "Solid",
    "point": "Point",
    "line": "Line",
    "system": "System",
    "connector": "Connector",
    "assembly": "Assembly",
    "constraint": "Constraint",
    "contactbehavior": "Contactbehavior",
    "contactgroup": "Contactgroup",
    "contactsurf": "Contactsurf",
    "group": "Group",
}

# hm_getusermark is a Tcl query with no Python binding in 2025. The entity
# keyword is taken from this fixed table, never from caller input.
USER_MARK_ENTITY_TYPES = {
    "node": "nodes",
    "element": "elements",
    "component": "components",
    "material": "materials",
    "property": "properties",
    "loadcol": "loadcols",
    "loadstep": "loadsteps",
    "set": "sets",
    "surface": "surfaces",
    "solid": "solids",
    "point": "points",
    "line": "lines",
    "system": "systems",
    "connector": "connectors",
    "assembly": "assemblies",
    "constraint": "constraints",
    "contactbehavior": "contactbehaviors",
    "contactgroup": "contactgroups",
    "contactsurf": "contactsurfs",
    "group": "groups",
}

DEFAULT_ATTRIBUTES = {
    "node": ["id", "x", "y", "z"],
    "element": ["id", "config", "type"],
    "component": ["id", "name", "cardimage"],
    "material": ["id", "name", "cardimage"],
    "property": ["id", "name", "cardimage"],
    "loadcol": ["id", "name", "cardimage"],
    "loadstep": ["id", "name"],
    "set": ["id", "name", "cardimage"],
    "surface": ["id"],
    "solid": ["id"],
    "point": ["id", "x", "y", "z"],
    "line": ["id"],
    "system": ["id", "name"],
    "connector": ["id", "name"],
    "assembly": ["id", "name"],
    "constraint": ["id", "name", "cardimage"],
    "contactbehavior": ["id", "name", "cardimage"],
    "contactgroup": ["id", "name", "cardimage"],
    "contactsurf": ["id", "name", "cardimage"],
    "group": ["id", "name", "cardimage"],
}

SOLVER_CARD_ENTITY_TYPES = {
    "property",
    "loadcol",
    "loadstep",
    "set",
    "constraint",
    "contactbehavior",
    "contactgroup",
    "contactsurf",
    "group",
}

NODAL_LOAD_CONFIGS = {
    "force": 1,
    "moment": 2,
    "constraint": 3,
    "temperature": 5,
    "flux": 6,
    "velocity": 8,
    "acceleration": 9,
}


def _attribute_matches(actual: Any, expected: Any) -> bool:
    """Compare a stored HyperMesh attribute with the requested value."""
    if isinstance(expected, bool):
        return bool(actual) == expected
    if isinstance(expected, (int, float)):
        try:
            return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-12)
        except (TypeError, ValueError):
            return False
    if expected is None:
        return actual is None
    if isinstance(expected, str):
        return str(actual) == expected
    return actual == expected


def _jsonable(value: Any, depth: int = 0) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth >= 4:
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item, depth + 1) for item in value]
    keys = getattr(value, "keys", None)
    if keys is not None and not callable(keys):
        try:
            return {
                str(key): _jsonable(value[key], depth + 1)
                for key in list(keys)
            }
        except Exception:
            pass
    if value.__class__.__name__.endswith("QueryResultList"):
        try:
            return [_jsonable(item, depth + 1) for item in value]
        except Exception:
            pass
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return _jsonable(tolist(), depth + 1)
        except Exception:
            pass
    if hasattr(value, "id"):
        return {
            "entity_class": value.__class__.__name__,
            "id": _jsonable(getattr(value, "id"), depth + 1),
        }
    for method_name in ("as_dict", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return _jsonable(method(), depth + 1)
            except Exception:
                pass
    return str(value)


def _unwrap_hm_call(value: Any) -> Any:
    """Unwrap HyperMesh's ``(hwReturnStatus, HmQueryResult)`` convention."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return _jsonable(value)

    status, payload = value
    if not all(hasattr(status, name) for name in ("status", "message")):
        return _jsonable(value)

    try:
        succeeded = bool(status)
    except Exception:
        succeeded = int(getattr(status, "status", 1)) == 0
    if not succeeded:
        message = str(getattr(status, "message", "") or "HyperMesh query failed")
        raise RuntimeError(message)

    result = _jsonable(payload)
    if isinstance(result, dict) and len(result) == 1:
        key, item = next(iter(result.items()))
        if key.lower() in {"value", "values", "result"}:
            return item
    return result


def _vec_sub(first: tuple[float, ...], second: tuple[float, ...]) -> tuple[float, float, float]:
    return (
        float(first[0] - second[0]),
        float(first[1] - second[1]),
        float(first[2] - second[2]),
    )


def _vec_dot(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    return float(first[0] * second[0] + first[1] * second[1] + first[2] * second[2])


def _vec_cross(first: tuple[float, ...], second: tuple[float, ...]) -> tuple[float, float, float]:
    return (
        float(first[1] * second[2] - first[2] * second[1]),
        float(first[2] * second[0] - first[0] * second[2]),
        float(first[0] * second[1] - first[1] * second[0]),
    )


def _vec_norm(vector: tuple[float, ...]) -> float:
    return math.sqrt(_vec_dot(vector, vector))


def _vec_unit(vector: tuple[float, ...]) -> tuple[float, float, float] | None:
    length = _vec_norm(vector)
    if length <= 1.0e-15:
        return None
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _parse_point_list(value: Any) -> list[tuple[float, float, float]]:
    """Parse the nested Tcl point list returned by geometry queries."""
    numbers = [float(token) for token in TCL_NUMBER_PATTERN.findall(str(value or ""))]
    return [
        (numbers[index], numbers[index + 1], numbers[index + 2])
        for index in range(0, len(numbers) - 2, 3)
    ]


def _point_segment_distance(
    point: tuple[float, float, float],
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> float:
    """Shortest distance from a point to a line segment."""
    dx, dy, dz = end[0] - start[0], end[1] - start[1], end[2] - start[2]
    length_sq = dx * dx + dy * dy + dz * dz
    if length_sq <= 0.0:
        return math.dist(point, start)
    t = (
        (point[0] - start[0]) * dx
        + (point[1] - start[1]) * dy
        + (point[2] - start[2]) * dz
    ) / length_sq
    t = max(0.0, min(1.0, t))
    nearest = (
        start[0] + t * dx,
        start[1] + t * dy,
        start[2] + t * dz,
    )
    return math.dist(point, nearest)


def _polyline_distance(
    first: list[tuple[float, float, float]],
    second: list[tuple[float, float, float]],
) -> float:
    """Symmetric shortest distance between two sampled polylines."""
    best = float("inf")
    for point in first:
        for index in range(len(second) - 1):
            best = min(
                best,
                _point_segment_distance(point, second[index], second[index + 1]),
            )
    for point in second:
        for index in range(len(first) - 1):
            best = min(
                best,
                _point_segment_distance(point, first[index], first[index + 1]),
            )
    return best


def _fit_plane(
    points: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float], float, float] | None:
    """Fit a plane to sampled boundary points.

    Returns origin, unit normal, maximum point-plane residual, and the bounding
    box diagonal used to scale tolerances.
    """
    if len(points) < 3:
        return None
    origin = points[0]
    far = max(points, key=lambda point: _vec_dot(_vec_sub(point, origin), _vec_sub(point, origin)))
    first_axis = _vec_unit(_vec_sub(far, origin))
    if first_axis is None:
        return None
    second_vector = None
    second_length = 0.0
    for point in points:
        delta = _vec_sub(point, origin)
        perpendicular = _vec_sub(delta, tuple(first_axis[i] * _vec_dot(delta, first_axis) for i in range(3)))
        length = _vec_norm(perpendicular)
        if length > second_length:
            second_length = length
            second_vector = perpendicular
    if second_vector is None or second_length <= 1.0e-12:
        return None
    normal = _vec_unit(_vec_cross(first_axis, second_vector))
    if normal is None:
        return None
    residual = max(abs(_vec_dot(_vec_sub(point, origin), normal)) for point in points)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    zs = [point[2] for point in points]
    diagonal = math.sqrt(
        (max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2 + (max(zs) - min(zs)) ** 2
    )
    return origin, normal, residual, diagonal


def _project_edge_points(
    edge_points: list[list[tuple[float, float, float]]],
    origin: tuple[float, float, float],
    first_axis: tuple[float, float, float],
    second_axis: tuple[float, float, float],
) -> tuple[list[tuple[float, float, float, float]], list[tuple[float, float]]]:
    """Project sampled boundary points into a 2D frame anchored at origin."""
    segments: list[tuple[float, float, float, float]] = []
    boundary: list[tuple[float, float]] = []
    for points in edge_points:
        projected = [
            (
                _vec_dot(_vec_sub(point, origin), first_axis),
                _vec_dot(_vec_sub(point, origin), second_axis),
            )
            for point in points
        ]
        boundary.extend(projected)
        for index in range(len(projected) - 1):
            segments.append(
                (
                    projected[index][0],
                    projected[index][1],
                    projected[index + 1][0],
                    projected[index + 1][1],
                )
            )
    return segments, boundary


def _surface_boundary_sample(
    edge_points: list[list[tuple[float, float, float]]], tolerance: float
) -> dict[str, Any] | None:
    """Build a planar 2D representation of a surface boundary from edge samples."""
    all_points = [point for points in edge_points for point in points]
    fitted = _fit_plane(all_points)
    if fitted is None:
        return None
    origin, normal, residual, diagonal = fitted
    planarity_limit = max(float(tolerance), 1.0e-6 * diagonal)
    helper = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.9 else (0.0, 1.0, 0.0)
    first_axis = _vec_unit(_vec_cross(normal, helper))
    if first_axis is None:
        return None
    second_axis = _vec_cross(normal, first_axis)
    segments, boundary = _project_edge_points(
        edge_points, origin, first_axis, second_axis
    )
    perimeter = sum(
        math.hypot(segment[2] - segment[0], segment[3] - segment[1])
        for segment in segments
    )
    return {
        "origin": origin,
        "normal": normal,
        "axes": (first_axis, second_axis),
        "residual": residual,
        "diagonal": diagonal,
        "perimeter": perimeter,
        "planar": residual <= planarity_limit,
        "segments": segments,
        "boundary": boundary,
        "edge_points": edge_points,
    }


def _read_surface_boundary_geometry(
    hw: Any, edge_ids: set[int], tolerance: float
) -> dict[str, Any] | None:
    edge_points: list[list[tuple[float, float, float]]] = []
    for edge_id in sorted(edge_ids):
        try:
            value = hw.evalTcl(
                f"hm_getcoordinatesofpointsonline {edge_id} "
                f"{{{BOUNDARY_SAMPLE_PARAMS}}}"
            )
            points = _parse_point_list(value)
        except Exception:
            points = []
        if len(points) >= 2:
            edge_points.append(points)
    if not edge_points:
        return None
    try:
        return _surface_boundary_sample(edge_points, tolerance)
    except Exception:
        return None


def _planes_coplanar(
    first: dict[str, Any], second: dict[str, Any], tolerance: float
) -> bool:
    alignment = abs(_vec_dot(first["normal"], second["normal"]))
    if alignment < 0.999:
        return False
    offset = abs(_vec_dot(first["normal"], _vec_sub(second["origin"], first["origin"])))
    return offset <= max(float(tolerance), 1.0e-6 * first["diagonal"])


def _point_in_segments(x: float, y: float, segments: list[tuple[float, float, float, float]]) -> bool:
    """Even-odd ray cast against a set of closed boundary segments."""
    inside = False
    for x1, y1, x2, y2 in segments:
        if (y1 > y) != (y2 > y):
            intersection_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if intersection_x > x:
                inside = not inside
    return inside


def _distance_to_segments(
    x: float, y: float, segments: list[tuple[float, float, float, float]]
) -> float:
    best = math.inf
    for x1, y1, x2, y2 in segments:
        dx = x2 - x1
        dy = y2 - y1
        denominator = dx * dx + dy * dy
        if denominator <= 1.0e-18:
            distance = math.hypot(x - x1, y - y1)
        else:
            factor = ((x - x1) * dx + (y - y1) * dy) / denominator
            factor = max(0.0, min(1.0, factor))
            distance = math.hypot(x - (x1 + factor * dx), y - (y1 + factor * dy))
        if distance < best:
            best = distance
    return best


def _scanline_intervals(
    y: float, segments: list[tuple[float, float, float, float]]
) -> list[tuple[float, float]] | None:
    crossings: list[float] = []
    for x1, y1, x2, y2 in segments:
        if (y1 > y) != (y2 > y):
            crossings.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    crossings.sort()
    if len(crossings) < 2 or len(crossings) % 2:
        return None
    return [
        (crossings[index], crossings[index + 1])
        for index in range(0, len(crossings), 2)
    ]


def _intersect_intervals(
    first: list[tuple[float, float]], second: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    first_index = 0
    second_index = 0
    while first_index < len(first) and second_index < len(second):
        low = max(first[first_index][0], second[second_index][0])
        high = min(first[first_index][1], second[second_index][1])
        if high > low:
            result.append((low, high))
        if first[first_index][1] < second[second_index][1]:
            first_index += 1
        else:
            second_index += 1
    return result


def _scanline_overlap_area(
    first: dict[str, Any], second: dict[str, Any], rows: int = 200
) -> float:
    """Estimate the coplanar overlap area by integrating horizontal slices."""
    first_segments = first["segments"]
    second_segments = second["segments"]
    if not first_segments or not second_segments:
        return 0.0
    first_x = [value for segment in first_segments for value in (segment[0], segment[2])]
    first_y = [value for segment in first_segments for value in (segment[1], segment[3])]
    second_x = [value for segment in second_segments for value in (segment[0], segment[2])]
    second_y = [value for segment in second_segments for value in (segment[1], segment[3])]
    x_min = max(min(first_x), min(second_x))
    x_max = min(max(first_x), max(second_x))
    y_min = max(min(first_y), min(second_y))
    y_max = min(max(first_y), max(second_y))
    if x_max <= x_min or y_max <= y_min:
        return 0.0
    step = (y_max - y_min) / rows
    total = 0.0
    for index in range(rows):
        y = y_min + (index + 0.5) * step
        first_intervals = _scanline_intervals(y, first_segments)
        second_intervals = _scanline_intervals(y, second_segments)
        if first_intervals is None or second_intervals is None:
            continue
        total += sum(
            high - low
            for low, high in _intersect_intervals(first_intervals, second_intervals)
        )
    return total * step


def _classify_coplanar_pair(
    first: dict[str, Any] | None,
    second: dict[str, Any] | None,
    area_first: float | None,
    area_second: float | None,
    tolerance: float,
) -> tuple[str, dict[str, Any]]:
    """Classify a pair that produced no intersection curve."""
    if first is None or second is None:
        return "no_intersection_curve", {
            "unverified_reason": "missing_boundary_geometry"
        }
    if not first["planar"] or not second["planar"]:
        return "no_intersection_curve", {"unverified_reason": "non_planar_surface"}
    plane_offset = abs(
        _vec_dot(first["normal"], _vec_sub(second["origin"], first["origin"]))
    )
    if not _planes_coplanar(first, second, tolerance):
        return "contact", {
            "unverified_reason": "not_coplanar",
            "plane_offset": plane_offset,
        }
    # Both boundaries must be compared in one frame; reproject the second face
    # into the first face's plane basis.
    first_axis, second_axis = first["axes"]
    second_segments, second_boundary = _project_edge_points(
        second["edge_points"], first["origin"], first_axis, second_axis
    )
    first_segments = first["segments"]
    first_boundary = first["boundary"]
    margin = max(float(tolerance), 1.0e-6 * min(first["diagonal"], second["diagonal"]))
    inside_second = sum(
        1
        for x, y in second_boundary
        if _point_in_segments(x, y, first_segments)
        and _distance_to_segments(x, y, first_segments) > margin
    )
    inside_first = sum(
        1
        for x, y in first_boundary
        if _point_in_segments(x, y, second_segments)
        and _distance_to_segments(x, y, second_segments) > margin
    )
    overlap_area = _scanline_overlap_area(
        {"segments": first_segments}, {"segments": second_segments}
    )
    known_areas = [value for value in (area_first, area_second) if value]
    reference_area = min(known_areas) if known_areas else None
    reference_perimeter = min(first["perimeter"], second["perimeter"])
    # Ignore slivers whose average width is below the modelling tolerance: they
    # come from chord sampling on shared seams, not from a real area overlap.
    area_threshold = max(
        float(tolerance) ** 2,
        0.5 * float(tolerance) * reference_perimeter,
    )
    extra: dict[str, Any] = {
        "plane_offset": plane_offset,
        "inside_first_sample_count": inside_first,
        "inside_second_sample_count": inside_second,
        "overlap_area_estimate": overlap_area,
        "overlap_area_threshold": area_threshold,
        "overlap_width_estimate": (
            overlap_area / (0.5 * reference_perimeter)
            if reference_perimeter
            else None
        ),
        "overlap_ratio_of_smaller_area": (
            overlap_area / reference_area if reference_area else None
        ),
    }
    if overlap_area > area_threshold and (inside_first or inside_second):
        return "overlapping", extra
    if overlap_area > area_threshold:
        extra["unverified_reason"] = "overlap_area_without_interior_boundary_samples"
        return "overlapping", extra
    extra["unverified_reason"] = (
        "area_below_threshold" if (inside_first or inside_second) else "no_area_overlap"
    )
    return "contact", extra


def _parse_float_list(value: Any) -> list[float]:
    return [float(token) for token in TCL_NUMBER_PATTERN.findall(str(value or ""))]


def _surface_probe_samples(
    hw: Any,
    surface_id: int,
    edge_ids: set[int],
    target_count: int,
    budget: dict[str, int],
) -> list[dict[str, Any]]:
    """Sample a surface by projecting a boundary-frame grid onto it.

    The surface normal at each projected point is returned as well; both are
    needed for the coincidence test. Boundary edge samples of the surface are
    used only as a fallback when projection collapses to a few points.
    """
    boundary_points: list[tuple[float, float, float]] = []
    for edge_id in sorted(edge_ids):
        try:
            value = hw.evalTcl(
                f"hm_getcoordinatesofpointsonline {edge_id} "
                f"{{{BOUNDARY_SAMPLE_PARAMS}}}"
            )
            budget["queries"] += 1
            points = _parse_point_list(value)
        except Exception:
            continue
        if len(points) >= 2:
            boundary_points.extend(points)
    if not boundary_points:
        return []
    candidates: list[tuple[float, float, float]] = []
    fitted = _fit_plane(boundary_points)
    if fitted is not None:
        origin, normal, _residual, _diagonal = fitted
        helper = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.9 else (0.0, 1.0, 0.0)
        first_axis = _vec_unit(_vec_cross(normal, helper))
        second_axis = _vec_cross(normal, first_axis) if first_axis else None
        if first_axis is not None and second_axis is not None:
            projected = [
                (
                    _vec_dot(_vec_sub(point, origin), first_axis),
                    _vec_dot(_vec_sub(point, origin), second_axis),
                )
                for point in boundary_points
            ]
            u_values = [item[0] for item in projected]
            v_values = [item[1] for item in projected]
            span_u = max(u_values) - min(u_values)
            span_v = max(v_values) - min(v_values)
            if span_u > 0.0 and span_v > 0.0:
                side = max(2, int(math.ceil(math.sqrt(target_count))))
                for first_index in range(side):
                    u = min(u_values) + (first_index + 0.5) * span_u / side
                    for second_index in range(side):
                        v = min(v_values) + (second_index + 0.5) * span_v / side
                        candidates.append(
                            tuple(
                                origin[axis]
                                + u * first_axis[axis]
                                + v * second_axis[axis]
                                for axis in range(3)
                            )
                        )
    samples: list[dict[str, Any]] = []
    seen: set[tuple[float, float, float]] = set()

    def add_sample(
        point: tuple[float, float, float],
        direction: tuple[float, float, float] | None,
    ) -> bool:
        key = (round(point[0], 3), round(point[1], 3), round(point[2], 3))
        if key in seen:
            return False
        seen.add(key)
        samples.append({"point": point, "normal": direction})
        return True

    for candidate in candidates:
        if len(samples) >= target_count:
            break
        try:
            value = hw.evalTcl(
                "hm_getsurfacenormalatcoordinate "
                f"{surface_id} "
                f"{candidate[0]:.12g} {candidate[1]:.12g} {candidate[2]:.12g}"
            )
            budget["queries"] += 1
            numbers = _parse_float_list(value)
        except Exception:
            continue
        if len(numbers) < 3:
            continue
        direction = (
            _vec_unit((numbers[3], numbers[4], numbers[5]))
            if len(numbers) >= 6
            else None
        )
        add_sample((numbers[0], numbers[1], numbers[2]), direction)
    if len(samples) < 4:
        for point in boundary_points:
            if len(samples) >= target_count:
                break
            add_sample(point, None)
    return samples


def _closest_surface_point(
    hw: Any,
    surface_id: int,
    point: tuple[float, float, float],
    budget: dict[str, int],
) -> tuple[tuple[float, float, float], tuple[float, float, float] | None] | None:
    try:
        value = hw.evalTcl(
            "hm_getsurfacenormalatcoordinate "
            f"{surface_id} {point[0]:.12g} {point[1]:.12g} {point[2]:.12g}"
        )
        budget["queries"] += 1
        numbers = _parse_float_list(value)
    except Exception:
        return None
    if len(numbers) < 3:
        return None
    direction = (
        _vec_unit((numbers[3], numbers[4], numbers[5]))
        if len(numbers) >= 6
        else None
    )
    return (numbers[0], numbers[1], numbers[2]), direction


def _curved_side_metrics(
    hw: Any,
    source_samples: list[dict[str, Any]],
    target_surface: int,
    tolerance: float,
    budget: dict[str, int],
) -> dict[str, Any]:
    distances: list[float] = []
    covered_distances: list[float] = []
    covered_angles: list[float] = []
    for sample in source_samples:
        result = _closest_surface_point(
            hw, target_surface, sample["point"], budget
        )
        if result is None:
            continue
        closest, direction = result
        distance = _vec_norm(_vec_sub(sample["point"], closest))
        distances.append(distance)
        if distance <= tolerance:
            covered_distances.append(distance)
            if sample.get("normal") and direction:
                alignment = abs(_vec_dot(sample["normal"], direction))
                covered_angles.append(
                    math.degrees(math.acos(min(1.0, alignment)))
                )
    evaluated = len(distances)
    covered = len(covered_distances)
    return {
        "evaluated_sample_count": evaluated,
        "covered_sample_count": covered,
        "coverage": covered / evaluated if evaluated else 0.0,
        "min_distance": min(distances) if distances else None,
        "mean_covered_distance": (
            sum(covered_distances) / covered if covered else None
        ),
        "max_normal_angle_degrees": (
            max(covered_angles) if covered_angles else None
        ),
    }


def _classify_curved_pair(
    first_metrics: dict[str, Any],
    second_metrics: dict[str, Any],
    area_first: float | None,
    area_second: float | None,
    tolerance: float,
    coincident_coverage: float,
    overlap_coverage: float,
    normal_angle_limit: float,
) -> tuple[str, dict[str, Any]]:
    """Classify a non-planar surface pair by symmetric distance coverage."""
    coverage = max(first_metrics["coverage"], second_metrics["coverage"])
    angles = [
        value
        for value in (
            first_metrics["max_normal_angle_degrees"],
            second_metrics["max_normal_angle_degrees"],
        )
        if value is not None
    ]
    normal_angle = max(angles) if angles else None
    distances = [
        value
        for value in (
            first_metrics["min_distance"],
            second_metrics["min_distance"],
        )
        if value is not None
    ]
    min_distance = min(distances) if distances else None
    known_areas = [value for value in (area_first, area_second) if value]
    reference_area = min(known_areas) if known_areas else None
    extra: dict[str, Any] = {
        "coverage": coverage,
        "coverage_first": first_metrics["coverage"],
        "coverage_second": second_metrics["coverage"],
        "normal_angle_degrees": normal_angle,
        "min_distance": min_distance,
        "metrics_first": first_metrics,
        "metrics_second": second_metrics,
        "overlap_area_estimate": (
            coverage * reference_area if reference_area is not None else None
        ),
    }
    if min_distance is None:
        extra["unverified_reason"] = "distance_query_failed"
        return "failed", extra
    if min_distance > tolerance:
        # The pair passed hm_measureshortestdistance <= tolerance, so the
        # touching zone lies on the boundary or a corner that the interior
        # grid did not sample; it is contact, not an area overlap.
        extra["unverified_reason"] = "contact_outside_sampled_area"
        return "contact", extra
    if normal_angle is not None and normal_angle > normal_angle_limit:
        extra["unverified_reason"] = "normal_misalignment"
        return "contact", extra
    if coverage >= coincident_coverage:
        return "coincident", extra
    if coverage >= overlap_coverage:
        return "partial_overlap", extra
    extra["unverified_reason"] = "low_coverage"
    return "contact", extra


def _build_overlap_groups(
    pairs: list[dict[str, Any]], areas: dict[int, float | None]
) -> list[dict[str, Any]]:
    """Group overlap pairs into connected components (one component per group)."""
    nodes: set[int] = set()
    for pair in pairs:
        nodes.add(int(pair["surface_a"]))
        nodes.add(int(pair["surface_b"]))
    parent = {node: node for node in nodes}

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for pair in pairs:
        first = find(int(pair["surface_a"]))
        second = find(int(pair["surface_b"]))
        if first != second:
            parent[second] = first
    components: dict[int, list[int]] = {}
    for node in nodes:
        components.setdefault(find(node), []).append(node)
    groups: list[dict[str, Any]] = []
    for group_index, node_list in enumerate(
        sorted(components.values(), key=min), start=1
    ):
        members = set(node_list)
        related = [
            pair
            for pair in pairs
            if int(pair["surface_a"]) in members and int(pair["surface_b"]) in members
        ]
        related_sample, truncated = (
            related[:PAIR_SAMPLE_LIMIT],
            len(related) > PAIR_SAMPLE_LIMIT,
        )
        groups.append(
            {
                "group_id": group_index,
                "surface_ids": sorted(members),
                "surface_count": len(members),
                "surfaces": [
                    {"id": surface_id, "area": areas.get(surface_id)}
                    for surface_id in sorted(members)
                ],
                "pair_count": len(related),
                "pairs": related_sample,
                "pairs_truncated": truncated,
                "overlap_area_sum": sum(
                    float(pair.get("overlap_area_estimate") or 0.0)
                    for pair in related
                ),
            }
        )
    return groups


class HandlerRegistry:
    def __init__(self, allowed_roots: list[str]):
        self.allowed_roots = [Path(item).resolve() for item in allowed_roots]
        self._last_surface_pairs: dict[str, Any] | None = None
        self._last_overlap_groups: dict[str, Any] | None = None
        self._last_curved_overlaps: dict[str, Any] | None = None
        self._surface_review: dict[str, Any] | None = None
        self._overlap_review: dict[str, Any] | None = None
        self.methods = {
            "ping": self.ping,
            "get_capabilities": self.get_capabilities,
            "get_session_info": self.get_session_info,
            "get_model_summary": self.get_model_summary,
            "list_entities": self.list_entities,
            "get_entity": self.get_entity,
            "get_user_mark": self.get_user_mark,
            "interactive_select": self.interactive_select,
            "set_entity_attributes": self.set_entity_attributes,
            "create_nodes": self.create_nodes,
            "create_elements": self.create_elements,
            "organize_elements": self.organize_elements,
            "create_material": self.create_material,
            "create_component": self.create_component,
            "delete_entities": self.delete_entities,
            "clear_reference_mesh": self.clear_reference_mesh,
            "analyze_surface_repair": self.analyze_surface_repair,
            "analyze_surface_repair_all": self.analyze_surface_repair_all,
            "analyze_surface_intersections": self.analyze_surface_intersections,
            "analyze_curved_surface_overlaps": self.analyze_curved_surface_overlaps,
            "start_surface_review": self.start_surface_review,
            "show_surface_review_group": self.show_surface_review_group,
            "next_surface_review_group": self.next_surface_review_group,
            "stop_surface_review": self.stop_surface_review,
            "get_surface_review_status": self.get_surface_review_status,
            "start_overlap_review": self.start_overlap_review,
            "show_overlap_review_group": self.show_overlap_review_group,
            "next_overlap_review_group": self.next_overlap_review_group,
            "stop_overlap_review": self.stop_overlap_review,
            "get_overlap_review_status": self.get_overlap_review_status,
            "remove_duplicate_surfaces": self.remove_duplicate_surfaces,
            "stitch_free_edges": self.stitch_free_edges,
            "trim_free_edges": self.trim_free_edges,
            "create_solver_card_entity": self.create_solver_card_entity,
            "create_nodal_load": self.create_nodal_load,
            "create_pressure_load": self.create_pressure_load,
            "create_loadstep": self.create_loadstep,
            "create_solid_block": self.create_solid_block,
            "create_solid_cylinder": self.create_solid_cylinder,
            "import_cad": self.import_cad,
            "automesh_surfaces": self.automesh_surfaces,
            "solid_map_mesh": self.solid_map_mesh,
            "tetra_mesh_solids": self.tetra_mesh_solids,
            "repair_mesh_quality": self.repair_mesh_quality,
            "create_cylindrical_ogrid": self.create_cylindrical_ogrid,
            "get_mesh_quality": self.get_mesh_quality,
            "get_connection_capabilities": self.get_connection_capabilities,
            "get_safety_airbag_capabilities": self.get_safety_airbag_capabilities,
            "create_rigid_link": self.create_rigid_link,
            "create_rbe3": self.create_rbe3,
            "create_weld": self.create_weld,
            "create_spot_weld": self.create_spot_weld,
            "create_connector": self.create_connector,
            "create_checkpoint": self.create_checkpoint,
            "rollback_checkpoint": self.rollback_checkpoint,
            "load_model": self.load_model,
            "refresh_view": self.refresh_view,
            "get_model_metrics": self.get_model_metrics,
            "postprocess_hyperview_result": self.postprocess_hyperview_result,
            "extract_hypergraph_time_history": self.extract_hypergraph_time_history,
            "save_model": self.save_model,
            "export_feko_mesh": self.export_feko_mesh,
        }

    @property
    def allowed_methods(self) -> list[str]:
        return sorted(self.methods)

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        handler = self.methods.get(method)
        if handler is None:
            raise ValueError(f"Method is not allowlisted: {method}")
        return handler(**params)

    @staticmethod
    def _imports():
        import hm
        import hm.entities as ent

        return hm, ent

    def _model(self, model_name: str | None = None):
        hm, _ = self._imports()
        if model_name:
            session = hm.Session()
            if not session.model_exists(model_name):
                raise ValueError(f"HyperMesh model not found: {model_name}")
            return hm.Model(model_name)
        return hm.Model()

    def _entity_class(self, entity_type: str):
        _, ent = self._imports()
        key = entity_type.strip().lower()
        class_name = ENTITY_CLASS_NAMES.get(key)
        if not class_name:
            raise ValueError(
                "Unsupported entity_type. Allowed values: "
                + ", ".join(sorted(ENTITY_CLASS_NAMES))
            )
        entity_class = getattr(ent, class_name, None)
        if entity_class is None:
            raise RuntimeError(f"Entity class is unavailable in this client: {class_name}")
        return key, entity_class

    @staticmethod
    def _attributes(entity_type: str, attributes: list[str] | None) -> list[str]:
        requested = attributes or DEFAULT_ATTRIBUTES[entity_type]
        if len(requested) > 30:
            raise ValueError("At most 30 attributes may be requested")
        clean = []
        for name in requested:
            if not isinstance(name, str) or not name or name.startswith("_"):
                raise ValueError(f"Invalid attribute name: {name!r}")
            clean.append(name)
        if "id" not in clean:
            clean.insert(0, "id")
        return clean

    @staticmethod
    def _serialize_entity(entity: Any, attributes: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = {"entity_class": entity.__class__.__name__}
        unavailable = []
        for name in attributes:
            try:
                result[name] = _jsonable(getattr(entity, name))
            except Exception:
                unavailable.append(name)
        if unavailable:
            result["unavailable_attributes"] = unavailable
        return result

    def ping(self) -> dict[str, Any]:
        return {
            "bridge": "hyperworks-mcp-extension",
            "version": BRIDGE_VERSION,
            "python": sys.version,
            "platform": platform.platform(),
            "execution_thread": threading.current_thread().name,
        }

    def get_capabilities(self) -> dict[str, Any]:
        modules = {}
        for name in ("hm", "hw", "hw.hv", "hw.hg", "report", "hwx.gui"):
            try:
                __import__(name)
                modules[name] = True
            except Exception as exc:
                modules[name] = False
                modules[name + "_error"] = str(exc)
        return {
            "modules": modules,
            "methods": self.allowed_methods,
            "entity_types": sorted(ENTITY_CLASS_NAMES),
            "save_roots": [str(path) for path in self.allowed_roots],
            "modeling_limits": {
                "nodes_per_call": MAX_NODES_PER_CALL,
                "elements_per_call": MAX_ELEMENTS_PER_CALL,
                "element_nodes": MAX_ELEMENT_NODES,
                "load_extensions": [".hm"],
                "cad_import_extensions": sorted(CAD_TRANSLATORS),
                "cad_translators": sorted(ALLOWED_CAD_TRANSLATORS),
                "geometry_ids_per_call": MAX_GEOMETRY_IDS_PER_CALL,
                "automatic_checkpoints": True,
                "solid_map": "native HyperMesh multi-solid solid map",
                "tetra_mesh": "native HyperMesh structural tetmesh with bounded sizing",
                "quality_repair": "checkpointed element smoothing with before/after metrics",
                "cylindrical_ogrid": "radial Hex8 rings with a Penta6 core",
                "connections": "checkpointed rigid links, RBE3 spiders, node welds, property-backed spot welds, and unrealized connector intent",
                "solver_cards": sorted(SOLVER_CARD_ENTITY_TYPES),
                "nodal_loads": sorted(NODAL_LOAD_CONFIGS),
                "time_history": "HyperView frame query to CSV and HyperGraph XY/PNG",
                "feko_export": "FEKO .fhm mesh export through the installed feko.tpl template",
            },
        }

    def get_connection_capabilities(
        self, model_name: str | None = None
    ) -> dict[str, Any]:
        """Audit connection APIs exposed by the active embedded HyperMesh client."""
        model = self._model(model_name)
        solver = None
        solver_error = None
        try:
            solver = _unwrap_hm_call(model.hm_getsolver())
        except Exception as exc:
            solver_error = str(exc)

        method_names = (
            "rigidlink",
            "rbe3",
            "weld",
            "createspotweld",
            "CE_ConnectorCreateByList",
            "CE_ConnectorCreateByMark",
            "CE_FE_RealizeWithDetails",
            "fastenercreation",
        )
        methods = {
            name: callable(getattr(model, name, None)) for name in method_names
        }
        solver_name = solver.get("solver") if isinstance(solver, dict) else solver
        solver_text = str(solver_name or "").lower()
        return {
            "model_name": model_name,
            "solver": solver_name,
            "solver_error": solver_error,
            "methods": methods,
            "exposed_operations": {
                "rigid_link": methods["rigidlink"],
                "rbe3": methods["rbe3"],
                "node_weld": methods["weld"],
                "property_backed_spot_weld": methods["createspotweld"],
                "connector_intent": methods["CE_ConnectorCreateByList"],
            },
            "audited_not_exposed": {
                "generic_connector_realization": (
                    "Connector intent creation is exposed; FE realization still requires "
                    "explicit solver-profile config, FE type, property, and controls."
                ),
                "fastener": {
                    "api_available": methods["fastenercreation"],
                    "supported_profile": "Abaqus",
                    "active_solver_appears_supported": "abaqus" in solver_text,
                },
            },
        }

    def get_safety_airbag_capabilities(
        self, model_name: str | None = None
    ) -> dict[str, Any]:
        """Audit safety-domain APIs without entering GUI contexts or changing the model."""
        model = self._model(model_name)
        try:
            raw_solver = _unwrap_hm_call(model.hm_getsolver())
            solver = raw_solver.get("solver") if isinstance(raw_solver, dict) else raw_solver
            solver_error = None
        except Exception as exc:
            solver = None
            solver_error = str(exc)
        exact_signature_methods = (
            "positiondummy",
            "rotatedummy",
            "relativerotatedummyjoint",
            "absoluterotatedummyjoint",
            "createseatbeltmesh",
            "createseatbelt",
            "create2dseatbeltwithmeshelementsize",
            "createairbag",
            "updateairbag",
            "reviewairbag",
        )
        context_or_opaque_methods = (
            "seatbelttensioning",
            "createflattenfold",
            "createtuckfold",
            "createzigzagfold",
            "createrollfold",
            "createsimplefold",
            "createfoldingtable",
            "createinflatorinsertfold",
            "createtargetfold",
            "createairbagsequence",
            "realizeairbagsequence",
            "airbagfoldpreview",
        )
        exact = {
            name: callable(getattr(model, name, None)) for name in exact_signature_methods
        }
        context = {
            name: callable(getattr(model, name, None))
            for name in context_or_opaque_methods
        }
        return {
            "model_name": model_name,
            "solver": solver,
            "solver_error": solver_error,
            "exact_signature_methods": exact,
            "context_or_opaque_methods": context,
            "exposure_state": "audit_only",
            "required_validation_fixtures": {
                "dummy_positioning": (
                    "A supported dummy collector with joint metadata and target points."
                ),
                "seatbelt_creation": (
                    "Routing nodes, three plane definitions, mesh settings, and validated "
                    "1D/2D output collectors for the active solver profile."
                ),
                "airbag_folding": (
                    "A closed airbag shell model plus initialized hwctx selections/options; "
                    "folding Python wrappers expose opaque signatures in 2026."
                ),
                "airbag_stitching": (
                    "Ordered edge paths and an initialized crash GUI context; the installed "
                    "workflow calls hwctx GenerateSprings."
                ),
            },
            "safe_next_step": (
                "Validate one solver-specific fixture per workflow before opening any "
                "model-changing safety operation through MCP."
            ),
        }

    def get_session_info(self) -> dict[str, Any]:
        hm, _ = self._imports()
        session = hm.Session()
        return {
            "current_model": session.get_current_model(),
            "models": list(session.get_all_models()),
            "model_count": len(session.get_all_models()),
            "execution_thread": threading.current_thread().name,
        }

    def get_model_summary(
        self,
        model_name: str | None = None,
        entity_types: list[str] | None = None,
    ) -> dict[str, Any]:
        hm, _ = self._imports()
        model = self._model(model_name)
        requested = entity_types or [
            "node",
            "element",
            "component",
            "material",
            "property",
            "loadcol",
            "loadstep",
        ]
        counts = {}
        errors = {}
        for raw_type in requested:
            key, entity_class = self._entity_class(raw_type)
            try:
                counts[key] = len(hm.Collection(model, entity_class))
            except Exception as exc:
                errors[key] = str(exc)
        return {
            "model_name": model_name,
            "counts": counts,
            "count_errors": errors,
        }

    def list_entities(
        self,
        entity_type: str,
        model_name: str | None = None,
        offset: int = 0,
        limit: int = 100,
        attributes: list[str] | None = None,
    ) -> dict[str, Any]:
        hm, _ = self._imports()
        key, entity_class = self._entity_class(entity_type)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 1000))
        attrs = self._attributes(key, attributes)
        collection = hm.Collection(self._model(model_name), entity_class)
        total = len(collection)
        items = []
        for index, entity in enumerate(collection):
            if index < offset:
                continue
            if len(items) >= limit:
                break
            items.append(self._serialize_entity(entity, attrs))
        return {
            "entity_type": key,
            "model_name": model_name,
            "offset": offset,
            "limit": limit,
            "total": total,
            "entities": items,
        }

    def get_entity(
        self,
        entity_type: str,
        entity_id: int,
        model_name: str | None = None,
        attributes: list[str] | None = None,
    ) -> dict[str, Any]:
        key, entity_class = self._entity_class(entity_type)
        entity = entity_class(self._model(model_name), int(entity_id))
        return self._serialize_entity(entity, self._attributes(key, attributes))

    def get_user_mark(
        self, entity_type: str, model_name: str | None = None
    ) -> dict[str, Any]:
        key, _ = self._entity_class(entity_type)
        # There is no Python getter for the user mark in 2025; the Tcl query is
        # fixed except for a keyword drawn from USER_MARK_ENTITY_TYPES.
        import hw

        tcl_type = USER_MARK_ENTITY_TYPES[key]
        value = hw.evalTcl("hm_getusermark %s" % tcl_type)
        ids = []
        for token in str(value or "").split():
            try:
                ids.append(int(token))
            except ValueError:
                continue
        return {
            "entity_type": key,
            "ids": ids,
            "count": len(ids),
            "result": _jsonable(value),
        }

    def interactive_select(
        self,
        entity_type: str,
        message: str = "Select entities for Codex",
        highlight: bool = True,
    ) -> dict[str, Any]:
        hm, _ = self._imports()
        key, entity_class = self._entity_class(entity_type)
        model = self._model(None)
        collection = hm.CollectionByInteractiveSelection(
            model, entity_class, message=str(message)[:200], highlight=bool(highlight)
        )
        ids = [int(entity.id) for entity in collection]
        return {"entity_type": key, "ids": ids, "count": len(ids)}

    def set_entity_attributes(
        self,
        entity_type: str,
        entity_id: int,
        values: dict[str, Any],
        model_name: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(values, dict) or not values or len(values) > 20:
            raise ValueError("values must contain between 1 and 20 attributes")
        key, entity_class = self._entity_class(entity_type)
        entity = entity_class(self._model(model_name), int(entity_id))
        before = {}
        for name, value in values.items():
            if not isinstance(name, str) or name == "id" or name.startswith("_"):
                raise ValueError(f"Attribute cannot be modified: {name!r}")
            before[name] = _jsonable(getattr(entity, name))
            setattr(entity, name, value)
        after = {name: _jsonable(getattr(entity, name)) for name in values}
        return {
            "entity_type": key,
            "entity_id": int(entity_id),
            "before": before,
            "after": after,
        }

    def organize_elements(
        self,
        target_component_id: int,
        element_ids: list[int] | None = None,
        source_component_id: int | None = None,
        surface_ids: list[int] | None = None,
        bbox: list[float] | None = None,
        mode: str = "move",
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Move existing elements into another component without re-meshing.

        Elements are selected by explicit ID, by source component (optionally
        narrowed to an axis-aligned box on the element centroid), or by the
        surfaces they are attached to. Only the owning collector changes;
        connectivity, node IDs and element IDs stay exactly as they are.
        mode="copy" hands the marked elements to *copymark instead, which
        duplicates them.

        The owning collector is reassigned with the Organize Tcl operation,
        *movemark <type> <mark> <collector name>. The 2025 Python binding
        rejects a Component object on the collector and component setters, so
        the Tcl route is the only working one.
        """
        hm, ent = self._imports()
        import hw

        model = self._model(model_name)
        if mode not in ("move", "copy"):
            raise ValueError('mode must be "move" or "copy"')
        target_component_id = int(target_component_id)
        self._checked_collection(model, "component", [target_component_id])
        target = ent.Component(model, target_component_id)
        target_name = str(target.name)

        if element_ids is None and source_component_id is None and surface_ids is None:
            raise ValueError(
                "Either element_ids, source_component_id or surface_ids is required"
            )
        if surface_ids is not None and (
            element_ids is not None or source_component_id is not None or bbox is not None
        ):
            raise ValueError(
                "surface_ids cannot be combined with element_ids, "
                "source_component_id or bbox"
            )

        command = "copymark" if mode == "copy" else "movemark"

        if surface_ids is not None:
            ids = [int(value) for value in surface_ids]
            if not ids:
                raise ValueError("surface_ids is empty")
            self._checked_collection(model, "surface", ids)
            checkpoint = self.create_checkpoint("before_organize", model_name)
            hw.evalTcl(
                '*createmark elems 1 "by surface" ' + " ".join(str(value) for value in ids)
            )
            hw.evalTcl(f'*{command} elems 1 "{target_name}"')
            held = self._count_elements_in_component(hm, ent, model, target_component_id)
            if mode == "move" and not held:
                raise RuntimeError(
                    f"No element reached component {target_component_id} "
                    f"({target_name}) after *{command}"
                )
            return {
                "mode": mode,
                "selection": "surface_ids",
                "surface_ids": ids,
                "strategy": f"tcl_{command}",
                "elements_in_target": held,
                "target_component_id": target_component_id,
                "target_component_name": target_name,
                "checkpoint_file": checkpoint.get("checkpoint_file"),
            }

        box = None
        if bbox is not None:
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 6:
                raise ValueError("bbox must be [xmin, ymin, zmin, xmax, ymax, zmax]")
            try:
                box = [float(value) for value in bbox]
            except (TypeError, ValueError) as exc:
                raise ValueError("bbox contains a non-numeric value") from exc
            if any(box[index] > box[index + 3] for index in range(3)):
                raise ValueError("bbox minimum exceeds maximum")

        if element_ids is not None:
            self._checked_collection(model, "element", list(element_ids))
            candidates = [ent.Element(model, int(value)) for value in element_ids]
        else:
            candidates = list(hm.Collection(model, ent.Element))

        source_id = (
            int(source_component_id) if source_component_id is not None else None
        )
        selected: list[Any] = []
        for element in candidates:
            if source_id is not None:
                collector = element.collector
                if int(getattr(collector, "id", collector)) != source_id:
                    continue
            if box is not None:
                centre = [0.0, 0.0, 0.0]
                count = 0
                for node in element.nodes:
                    centre[0] += float(node.x)
                    centre[1] += float(node.y)
                    centre[2] += float(node.z)
                    count += 1
                if not count:
                    continue
                centre = [value / count for value in centre]
                if not all(
                    box[axis] <= centre[axis] <= box[axis + 3] for axis in range(3)
                ):
                    continue
            selected.append(element)

        if not selected:
            return {
                "mode": mode,
                "selection": "element_ids" if element_ids is not None else "source_component_id",
                "moved": 0,
                "elements_in_target": self._count_elements_in_component(
                    hm, ent, model, target_component_id
                ),
                "target_component_id": target_component_id,
                "target_component_name": target_name,
                "candidate_count": len(candidates),
                "checkpoint_file": None,
            }

        checkpoint = self.create_checkpoint("before_organize", model_name)
        if source_id is not None and box is None:
            hw.evalTcl(f'*createmark elems 1 "by collector id" {source_id}')
            hw.evalTcl(f'*{command} elems 1 "{target_name}"')
        else:
            batch = 20000
            for start in range(0, len(selected), batch):
                chunk = selected[start : start + batch]
                id_text = " ".join(str(int(element.id)) for element in chunk)
                hw.evalTcl(f"*createmark elems 1 {id_text}")
                hw.evalTcl(f'*{command} elems 1 "{target_name}"')

        held = self._count_elements_in_component(hm, ent, model, target_component_id)
        verified = (
            sum(
                1
                for element in selected
                if int(getattr(element.collector, "id", element.collector))
                == target_component_id
            )
            if source_id is not None and box is None
            else held
        )
        if mode == "move" and not held:
            raise RuntimeError(
                f"No element reached component {target_component_id} "
                f"({target_name}) after *{command}"
            )
        return {
            "mode": mode,
            "selection": "element_ids" if element_ids is not None else "source_component_id",
            "moved": verified,
            "verified_in_target": verified,
            "elements_in_target": held,
            "strategy": f"tcl_{command}",
            "target_component_id": target_component_id,
            "target_component_name": target_name,
            "candidate_count": len(candidates),
            "checkpoint_file": checkpoint.get("checkpoint_file"),
        }

    @staticmethod
    def _count_elements_in_component(hm, ent, model, component_id: int) -> int:
        """Count the elements whose owning collector is the given component."""
        total = 0
        for element in hm.Collection(model, ent.Element):
            collector = element.collector
            if int(getattr(collector, "id", collector)) == int(component_id):
                total += 1
        return total

    def create_nodes(
        self,
        coordinates: list[list[float]],
        model_name: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("coordinates must be a non-empty list of [x, y, z] triples")
        if len(coordinates) > MAX_NODES_PER_CALL:
            raise ValueError(f"At most {MAX_NODES_PER_CALL} nodes may be created per call")
        clean: list[tuple[float, float, float]] = []
        for index, point in enumerate(coordinates):
            if not isinstance(point, (list, tuple)) or len(point) != 3:
                raise ValueError(f"coordinates[{index}] must contain exactly three values")
            try:
                xyz = tuple(float(value) for value in point)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"coordinates[{index}] contains a non-numeric value") from exc
            if not all(math.isfinite(value) for value in xyz):
                raise ValueError(f"coordinates[{index}] must contain finite values")
            clean.append(xyz)

        _, ent = self._imports()
        model = self._model(model_name)
        created = [ent.Node(model, x=x, y=y, z=z) for x, y, z in clean]
        return {
            "created": True,
            "entity_type": "node",
            "model_name": model_name,
            "count": len(created),
            "ids": [int(node.id) for node in created],
            "nodes": [
                self._serialize_entity(node, ["id", "x", "y", "z"])
                for node in created
            ],
        }

    def create_elements(
        self,
        node_ids: list[list[int]],
        config: int,
        solver_type: int = 1,
        auto_order: bool = False,
        component_id: int | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(node_ids, list) or not node_ids:
            raise ValueError("node_ids must be a non-empty list of connectivity lists")
        if len(node_ids) > MAX_ELEMENTS_PER_CALL:
            raise ValueError(f"At most {MAX_ELEMENTS_PER_CALL} elements may be created per call")
        config = int(config)
        solver_type = int(solver_type)
        if config <= 0 or solver_type < 0:
            raise ValueError("config must be positive and solver_type must be non-negative")

        connectivities: list[list[int]] = []
        for index, connectivity in enumerate(node_ids):
            if not isinstance(connectivity, (list, tuple)):
                raise ValueError(f"node_ids[{index}] must be a list")
            if not 2 <= len(connectivity) <= MAX_ELEMENT_NODES:
                raise ValueError(
                    f"node_ids[{index}] must contain 2 to {MAX_ELEMENT_NODES} node IDs"
                )
            ids = [int(value) for value in connectivity]
            if any(value <= 0 for value in ids) or len(set(ids)) != len(ids):
                raise ValueError(f"node_ids[{index}] must contain unique positive node IDs")
            connectivities.append(ids)

        hm, ent = self._imports()
        model = self._model(model_name)
        component = None
        checkpoint = None
        if component_id is not None:
            component_id = int(component_id)
            self._checked_collection(model, "component", [component_id])
            component = ent.Component(model, component_id)
            checkpoint = self.create_checkpoint("before_elements", model_name)
            model.currentcollector(
                entity_type=ent.Component, name=str(component.name)
            )
        existing_nodes = {int(node.id) for node in hm.Collection(model, ent.Node)}
        missing = sorted({value for ids in connectivities for value in ids} - existing_nodes)
        if missing:
            preview = missing[:20]
            suffix = "..." if len(missing) > len(preview) else ""
            raise ValueError(f"Referenced node IDs do not exist: {preview}{suffix}")

        before = {int(element.id) for element in hm.Collection(model, ent.Element)}
        # createelement() expects a C++ EntityList and is not usable from the
        # embedded interpreter; the installed 2025 binding provides
        # createelements(), which accepts node IDs through an hwIntList.
        from hwcont import hwIntList

        call_results = []
        for connectivity in connectivities:
            node_id_list = hwIntList()
            for value in connectivity:
                node_id_list.push_back(value)
            call_results.append(
                _unwrap_hm_call(
                    model.createelements(
                        config=config,
                        type=solver_type,
                        integer_array=node_id_list,
                    )
                )
            )
        after = {int(element.id) for element in hm.Collection(model, ent.Element)}
        created_ids = sorted(after - before)
        if len(created_ids) != len(connectivities):
            raise RuntimeError(
                "HyperMesh completed element creation, but the created IDs could not be "
                f"identified reliably (expected {len(connectivities)}, found {len(created_ids)})"
            )
        component_info = None
        if component is not None:
            outside = [
                element_id
                for element_id in created_ids
                if int(ent.Element(model, element_id).component.id) != component_id
            ]
            if outside:
                self._rollback_after_failure(
                    checkpoint,
                    model_name,
                    RuntimeError(
                        "Elements were created outside the requested component: "
                        f"{outside[:20]}"
                    ),
                )
            component_info = {"id": component_id, "name": str(component.name)}
        return {
            "created": True,
            "entity_type": "element",
            "model_name": model_name,
            "count": len(created_ids),
            "ids": created_ids,
            "config": config,
            "solver_type": solver_type,
            "auto_order_requested": bool(auto_order),
            "auto_order_applied": False,
            "component": component_info,
            "call_results": call_results,
        }

    def create_material(
        self,
        name: str,
        cardimage: str | None = None,
        values: dict[str, Any] | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        name = str(name).strip()
        if not name or len(name) > 128:
            raise ValueError("name must contain between 1 and 128 characters")
        values = values or {}
        if not isinstance(values, dict) or len(values) > 30:
            raise ValueError("values must be an object containing at most 30 attributes")
        kwargs: dict[str, Any] = {"name": name}
        if cardimage is not None:
            cardimage = str(cardimage).strip()
            if not cardimage or len(cardimage) > 128:
                raise ValueError("cardimage must contain between 1 and 128 characters")
            kwargs["cardimage"] = cardimage
        for key, value in values.items():
            if (
                not isinstance(key, str)
                or not key
                or key.startswith("_")
                or key in {"id", "name", "cardimage"}
            ):
                raise ValueError(f"Material creation attribute is not allowed: {key!r}")
            kwargs[key] = value

        _, ent = self._imports()
        material = ent.Material(self._model(model_name), **kwargs)
        attributes = ["id", "name", "cardimage", *values.keys()]
        return {
            "created": True,
            "entity_type": "material",
            "model_name": model_name,
            "material": self._serialize_entity(material, attributes),
        }

    def create_component(
        self,
        name: str,
        property_id: int | None = None,
        material_id: int | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Create one component, optionally bound to an existing property or material."""
        clean_name = str(name).strip()
        if not clean_name or len(clean_name) > 128:
            raise ValueError("name must contain between 1 and 128 characters")
        hm, ent = self._imports()
        model = self._model(model_name)
        kwargs: dict[str, Any] = {"name": clean_name}
        if property_id is not None:
            property_id = int(property_id)
            self._checked_collection(model, "property", [property_id])
            # HyperMesh stores the component-to-property reference in the
            # propertyid data name; passing it as "property" is accepted by
            # the constructor and then silently dropped.
            kwargs["propertyid"] = ent.Property(model, property_id)
        if material_id is not None:
            material_id = int(material_id)
            self._checked_collection(model, "material", [material_id])
            kwargs["materialid"] = ent.Material(model, material_id)
        before = self._entity_ids(hm, model, ent.Component)
        checkpoint = self.create_checkpoint("before_component", model_name)
        try:
            component = ent.Component(model, **kwargs)
            created = sorted(self._entity_ids(hm, model, ent.Component) - before)
            if int(component.id) not in created:
                raise RuntimeError("Component creation did not expose the new entity")
            # Read the association back from a freshly constructed entity so a
            # transient constructor argument cannot be mistaken for a stored
            # reference.
            stored = ent.Component(model, int(component.id))
            if property_id is not None:
                stored_property = getattr(stored, "propertyid", None)
                stored_property_id = getattr(stored_property, "id", None)
                if stored_property_id is None or int(stored_property_id) != property_id:
                    raise RuntimeError("Component property reference did not persist")
            if material_id is not None:
                stored_material = getattr(stored, "materialid", None)
                stored_material_id = getattr(stored_material, "id", None)
                if stored_material_id is None or int(stored_material_id) != material_id:
                    raise RuntimeError("Component material reference did not persist")
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        return {
            "created": True,
            "entity_type": "component",
            "model_name": model_name,
            "component": self._serialize_entity(
                component,
                ["id", "name", "propertyid", "materialid", "property", "material"],
            ),
            "checkpoint": checkpoint,
        }

    def delete_entities(
        self,
        entity_type: str,
        entity_ids: list[int],
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Delete explicit solids, surfaces, elements, or empty components.

        Deleting solids must leave every existing surface in place; deleting
        surfaces must leave every existing solid in place; deleting elements
        must leave every existing node in place. HyperMesh deletes a
        component's contents together with the component, so only components
        holding no elements, surfaces, solids, lines, or points are accepted;
        any other component is refused before a checkpoint is written. A
        violated invariant restores the pre-delete checkpoint.
        """
        key = str(entity_type).strip().lower()
        if key not in {"solid", "surface", "element", "component"}:
            raise ValueError(
                "entity_type must be solid, surface, element, or component"
            )
        if not isinstance(entity_ids, list) or not entity_ids:
            raise ValueError("entity_ids must be a non-empty list")
        if len(entity_ids) > MAX_GEOMETRY_IDS_PER_CALL:
            raise ValueError(
                f"At most {MAX_GEOMETRY_IDS_PER_CALL} entities may be deleted per call"
            )
        clean_ids = [int(value) for value in entity_ids]
        if any(value <= 0 for value in clean_ids) or len(set(clean_ids)) != len(clean_ids):
            raise ValueError("entity_ids must contain unique positive IDs")

        hm, ent = self._imports()
        model = self._model(model_name)
        _, entity_class = self._entity_class(key)
        existing = self._entity_ids(hm, model, entity_class)
        missing = sorted(set(clean_ids) - existing)
        if missing:
            preview = missing[:20]
            suffix = "..." if len(missing) > len(preview) else ""
            raise ValueError(f"Entity IDs do not exist: {preview}{suffix}")

        if key == "component":
            by_id = {
                int(item.id): item
                for item in hm.Collection(model, entity_class)
            }
            occupied = []
            for value in clean_ids:
                component = by_id[value]
                held = {}
                for attribute in (
                    "elements",
                    "surfaces",
                    "solids",
                    "lines",
                    "points",
                ):
                    items = getattr(component, attribute)
                    try:
                        count = len(items)
                    except TypeError:
                        count = sum(1 for _ in items)
                    if count:
                        held[attribute] = count
                if held:
                    occupied.append({"id": value, "name": str(component.name), **held})
            if occupied:
                preview = occupied[:20]
                suffix = "..." if len(occupied) > len(preview) else ""
                raise ValueError(
                    "Components still hold entities and cannot be deleted: "
                    f"{preview}{suffix}"
                )

        solid_ids_before = self._entity_ids(hm, model, ent.Solid)
        surface_ids_before = self._entity_ids(hm, model, ent.Surface)
        element_ids_before = self._entity_ids(hm, model, ent.Element)
        node_ids_before = self._entity_ids(hm, model, ent.Node)
        component_ids_before = self._entity_ids(hm, model, ent.Component)

        checkpoint = self.create_checkpoint("before_delete", model_name)
        try:
            if key == "solid":
                # Collection.delete_items() deletes a solid together with its
                # boundary surfaces. The dedicated Tcl command exposes the
                # keep_shell option needed here: 1 keeps boundary surfaces and
                # 1 keeps elements attached to the solids.
                import hw

                id_text = " ".join(str(value) for value in clean_ids)
                hw.evalTcl(f"*createmark solids 1 {id_text}")
                hw.evalTcl("*deletesolidswithelems 1 1 1")
            else:
                hm.Collection(model, entity_class, clean_ids).delete_items()
            remaining = self._entity_ids(hm, model, entity_class)
            not_deleted = sorted(set(clean_ids) & remaining)
            if not_deleted:
                raise RuntimeError(
                    "Entity deletion did not remove IDs: " + str(not_deleted[:20])
                )
            if key == "solid":
                lost = sorted(
                    surface_ids_before - self._entity_ids(hm, model, ent.Surface)
                )
                if lost:
                    raise RuntimeError(
                        "Solid deletion also removed surfaces: " + str(lost[:20])
                    )
            elif key == "surface":
                lost = sorted(solid_ids_before - self._entity_ids(hm, model, ent.Solid))
                if lost:
                    raise RuntimeError(
                        "Surface deletion also removed solids: " + str(lost[:20])
                    )
            elif key == "component":
                for label, before, retained_class in (
                    ("solids", solid_ids_before, ent.Solid),
                    ("surfaces", surface_ids_before, ent.Surface),
                    ("elements", element_ids_before, ent.Element),
                    ("nodes", node_ids_before, ent.Node),
                ):
                    lost = sorted(
                        before - self._entity_ids(hm, model, retained_class)
                    )
                    if lost:
                        raise RuntimeError(
                            f"Component deletion also removed {label}: "
                            + str(lost[:20])
                        )
            else:
                lost = sorted(node_ids_before - self._entity_ids(hm, model, ent.Node))
                if lost:
                    raise RuntimeError(
                        "Element deletion also removed nodes: " + str(lost[:20])
                    )
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)

        return {
            "deleted": True,
            "entity_type": key,
            "deleted_ids": clean_ids,
            "deleted_count": len(clean_ids),
            "model_name": model_name,
            "counts_before": {
                "component": len(component_ids_before),
                "solid": len(solid_ids_before),
                "surface": len(surface_ids_before),
                "element": len(element_ids_before),
                "node": len(node_ids_before),
            },
            "counts_after": {
                "component": len(self._entity_ids(hm, model, ent.Component)),
                "solid": len(self._entity_ids(hm, model, ent.Solid)),
                "surface": len(self._entity_ids(hm, model, ent.Surface)),
                "element": len(self._entity_ids(hm, model, ent.Element)),
                "node": len(self._entity_ids(hm, model, ent.Node)),
            },
            "checkpoint": checkpoint,
        }

    def clear_reference_mesh(
        self,
        component_names: list[str],
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Delete named reference components together with the elements they hold.

        Deleting a component also deletes the elements it owns, which is how a
        reference mesh is discarded without reloading the model. The method
        expects those components to hold every element in the model and raises
        if any element survives, so a partially cleared model is never returned.
        """
        if not isinstance(component_names, list) or not component_names:
            raise ValueError("component_names must be a non-empty list")
        if len(component_names) > 500:
            raise ValueError("At most 500 component names may be cleared per call")
        names: list[str] = []
        for item in component_names:
            text = str(item).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", text):
                raise ValueError(f"Invalid component name: {item!r}")
            names.append(text)
        if len(set(names)) != len(names):
            raise ValueError("component_names must be unique")

        hm, ent = self._imports()
        model = self._model(model_name)
        by_name = {str(item.name): int(item.id) for item in hm.Collection(model, ent.Component)}
        missing = [name for name in names if name not in by_name]
        if missing:
            raise ValueError("Components do not exist: " + str(missing[:20]))
        ids = [by_name[name] for name in names]
        element_ids_before = self._entity_ids(hm, model, ent.Element)
        checkpoint = self.create_checkpoint("before_clear_reference", model_name)
        try:
            hm.Collection(model, ent.Component, ids).delete_items()
            remaining_names = {str(item.name) for item in hm.Collection(model, ent.Component)}
            not_deleted = [name for name in names if name in remaining_names]
            if not_deleted:
                raise RuntimeError(
                    "Component deletion did not remove names: " + str(not_deleted[:20])
                )
            element_ids_after = self._entity_ids(hm, model, ent.Element)
            orphaned = sorted(set(element_ids_before) & element_ids_after)
            if orphaned:
                raise RuntimeError(
                    "The reference components left elements behind: "
                    + str(orphaned[:20])
                )
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        return {
            "cleared": True,
            "component_names": names,
            "component_ids": ids,
            "deleted_element_count": len(element_ids_before) - len(element_ids_after),
            "element_count_after": len(element_ids_after),
            "model_name": model_name,
            "checkpoint": checkpoint,
        }

    @staticmethod
    def _tcl_int_ids(value: Any) -> list[int]:
        """Parse a space-separated Tcl list of integer IDs."""
        ids: list[int] = []
        for token in str(value or "").replace("{", " ").replace("}", " ").split():
            try:
                ids.append(int(token))
            except ValueError:
                continue
        return ids

    @staticmethod
    def _first_float(value: Any) -> float | None:
        """Return the first numeric token of a Tcl list, or None."""
        for token in str(value or "").replace("{", " ").replace("}", " ").split():
            try:
                number = float(token)
            except ValueError:
                continue
            if math.isfinite(number):
                return number
        return None

    @staticmethod
    def _tcl_free_edge_summary(value: Any) -> dict[str, Any]:
        """Summarise the loop lists returned by hm_getedgeloops."""
        loops: list[list[int]] = []
        for chunk in str(value or "").split(";"):
            tokens = HandlerRegistry._tcl_int_ids(chunk)
            if tokens:
                loops.append(tokens)
        edge_ids: set[int] = set()
        for loop in loops:
            edge_ids.update(loop[1:])
        loop_sample = [loop[:51] for loop in loops[:10]]
        return {
            "loop_count": len(loops),
            "edge_count": len(edge_ids),
            "loops_sample": loop_sample,
            "loops_truncated": len(loops) > len(loop_sample),
        }

    @staticmethod
    def _mark_surfaces(targets: list[int], use_all: bool = False) -> None:
        import hw

        if use_all:
            hw.evalTcl('*createmark surfs 1 "all"')
        else:
            id_text = " ".join(str(value) for value in targets)
            hw.evalTcl(f"*createmark surfs 1 {id_text}")

    def _surface_targets(
        self,
        hm: Any,
        model: Any,
        ent: Any,
        surface_ids: list[int] | None,
    ) -> tuple[list[int], bool]:
        all_ids = self._entity_ids(hm, model, ent.Surface)
        if surface_ids is None:
            targets = sorted(all_ids)
            use_all = True
        else:
            _, targets = self._checked_collection(model, "surface", surface_ids)
            use_all = False
        if not targets:
            raise ValueError("The model contains no surfaces to repair")
        return targets, use_all

    def _free_edge_summary(self, targets: list[int], use_all: bool) -> dict[str, Any]:
        import hw

        self._mark_surfaces(targets, use_all=use_all)
        value = hw.evalTcl(
            'join [hm_getedgeloops surfaces markid=1 looptype=3] ";"'
        )
        return self._tcl_free_edge_summary(value)

    def analyze_surface_repair(
        self,
        tolerance: float = 0.1,
        surface_ids: list[int] | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Report duplicate surfaces and free-edge loops without changing geometry.

        Duplicate detection uses the documented one-set mode of
        *surfacemark_duplicate_check (options=4 returns secondary duplicates).
        Free-edge loops are queried with looptype=3 (open and closed free edges).
        """
        tol = self._positive_float("tolerance", tolerance, maximum=10.0)
        hm, ent = self._imports()
        model = self._model(model_name)
        targets, use_all = self._surface_targets(hm, model, ent, surface_ids)

        import hw

        self._mark_surfaces(targets, use_all=use_all)
        hw.evalTcl(
            f"*surfacemark_duplicate_check 1 1 4 0 1 {tol:.12g}"
        )
        duplicate_ids = sorted(
            set(self._tcl_int_ids(hw.evalTcl("hm_getmark surfs 1"))) & set(targets)
        )
        duplicate_sample, duplicate_truncated = self._id_sample(duplicate_ids)
        free_edges = self._free_edge_summary(targets, use_all)

        return {
            "model_name": model_name,
            "tolerance": tol,
            "surface_count": len(targets),
            "duplicate_count": len(duplicate_ids),
            "duplicate_ids": duplicate_sample,
            "duplicate_ids_truncated": duplicate_truncated,
            "free_edge_loop_count": free_edges["loop_count"],
            "free_edge_count": free_edges["edge_count"],
            "free_edge_loops_sample": free_edges["loops_sample"],
            "free_edge_loops_truncated": free_edges["loops_truncated"],
        }

    def analyze_surface_intersections(
        self,
        tolerance: float = 0.1,
        surface_ids: list[int] | None = None,
        max_pairs: int = 2000,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Classify surface pairs as adjacent, identical, intersecting, or overlapping.

        Pairs that share boundary edge IDs are reported as adjacent without
        further tests. Remaining pairs are filtered with
        hm_measureshortestdistance. Pairs within tolerance are checked with the
        duplicate command and, when not identical, with a temporary
        surface-surface intersection line. The created lines are deleted before
        returning; a checkpoint restores the model if cleanup fails.
        """
        tol = self._positive_float("tolerance", tolerance, maximum=10.0)
        if isinstance(max_pairs, bool) or not isinstance(max_pairs, int):
            raise ValueError("max_pairs must be an integer")
        if not 1 <= max_pairs <= 20000:
            raise ValueError("max_pairs must be in the range [1, 20000]")
        hm, ent = self._imports()
        model = self._model(model_name)
        targets, _ = self._surface_targets(hm, model, ent, surface_ids)
        pair_count = len(targets) * (len(targets) - 1) // 2
        if pair_count > max_pairs:
            raise ValueError(
                f"Surface pair count {pair_count} exceeds max_pairs={max_pairs}; "
                "narrow surface_ids or raise max_pairs"
            )

        import hw

        edges: dict[int, set[int]] = {}
        areas: dict[int, float | None] = {}
        for surface_id in targets:
            edge_values: list[Any] = []
            area_value: float | None = None
            try:
                surface = next(
                    iter(hm.Collection(model, ent.Surface, [surface_id])), None
                )
                if surface is not None:
                    edge_values = getattr(surface, "edges", None) or []
                    raw_area = getattr(surface, "area", None)
                    if raw_area is not None:
                        area_value = float(raw_area)
            except Exception:
                pass
            edges[surface_id] = {
                int(edge.id) for edge in edge_values if getattr(edge, "id", None)
            }
            areas[surface_id] = area_value

        adjacent_pairs: list[dict[str, Any]] = []
        candidate_pairs: list[tuple[int, int, float]] = []
        distance_error_pairs: list[dict[str, Any]] = []
        separate_count = 0
        for index, first in enumerate(targets):
            for second in targets[index + 1 :]:
                shared = edges[first] & edges[second]
                if shared:
                    adjacent_pairs.append(
                        {
                            "surface_a": first,
                            "surface_b": second,
                            "area_a": areas.get(first),
                            "area_b": areas.get(second),
                            "shared_edge_count": len(shared),
                        }
                    )
                    continue
                hw.evalTcl(f"*createmark surfs 1 {first}")
                hw.evalTcl(f"*createmark surfs 2 {second}")
                try:
                    distance = self._first_float(
                        hw.evalTcl(
                            "hm_measureshortestdistance surfaces 1 0 surfaces 2 0 0"
                        )
                    )
                except Exception as exc:  # pragma: no cover - live fallback
                    distance = None
                    distance_error_pairs.append(
                        {
                            "surface_a": first,
                            "surface_b": second,
                            "area_a": areas.get(first),
                            "area_b": areas.get(second),
                            "error": str(exc),
                        }
                    )
                if distance is None or distance > tol:
                    if distance is not None:
                        separate_count += 1
                    continue
                candidate_pairs.append((first, second, distance))

        surface_ids_before = self._entity_ids(hm, model, ent.Surface)
        checkpoint = None
        identical_pairs: list[dict[str, Any]] = []
        intersecting_pairs: list[dict[str, Any]] = []
        no_curve_pairs: list[dict[str, Any]] = []
        overlapping_pairs: list[dict[str, Any]] = []
        contact_pairs: list[dict[str, Any]] = []
        unverified_pairs: list[dict[str, Any]] = []
        failed_pairs: list[dict[str, Any]] = []
        try:
            for first, second, distance in candidate_pairs:
                hw.evalTcl(f"*createmark surfs 1 {first}")
                hw.evalTcl(f"*createmark surfs 2 {second}")
                hw.evalTcl(
                    f"*surfacemark_duplicate_check 1 2 8 0 1 {tol:.12g}"
                )
                matched = set(
                    self._tcl_int_ids(hw.evalTcl("hm_getmark surfs 2"))
                )
                if second in matched:
                    identical_pairs.append(
                        {
                            "surface_a": first,
                            "surface_b": second,
                            "area_a": areas.get(first),
                            "area_b": areas.get(second),
                            "distance": distance,
                        }
                    )
                    continue

                if checkpoint is None:
                    checkpoint = self.create_checkpoint(
                        "before_intersection_detection", model_name
                    )
                hw.evalTcl(f"*createmark surfs 1 {first}")
                hw.evalTcl(f"*createmark surfs 2 {second}")
                line_ids_before = self._entity_ids(hm, model, ent.Line)
                error = None
                try:
                    hw.evalTcl("*surfaceintersectmark 1 0 0 2")
                except Exception as exc:  # pragma: no cover - live fallback
                    error = str(exc)
                line_ids_after = self._entity_ids(hm, model, ent.Line)
                created = sorted(line_ids_after - line_ids_before)
                if created:
                    hm.Collection(model, ent.Line, created).delete_items()
                    if self._entity_ids(hm, model, ent.Line) != line_ids_before:
                        raise RuntimeError(
                            "Intersection detection could not remove the temporary lines"
                        )
                record = {
                    "surface_a": first,
                    "surface_b": second,
                    "area_a": areas.get(first),
                    "area_b": areas.get(second),
                    "distance": distance,
                    "created_line_count": len(created),
                }
                if error:
                    if str(error).strip() == "0":
                        # The command reports a bare 0 when no curve exists;
                        # this is the same geometric case as an empty result.
                        no_curve_pairs.append(record)
                    else:
                        record["error"] = error
                        failed_pairs.append(record)
                elif created:
                    intersecting_pairs.append(record)
                else:
                    no_curve_pairs.append(record)

            boundary_cache: dict[int, dict[str, Any] | None] = {}

            def boundary_for(surface_id: int) -> dict[str, Any] | None:
                if surface_id not in boundary_cache:
                    boundary_cache[surface_id] = _read_surface_boundary_geometry(
                        hw, edges.get(surface_id, set()), tol
                    )
                return boundary_cache[surface_id]

            for pair in no_curve_pairs:
                first = int(pair["surface_a"])
                second = int(pair["surface_b"])
                try:
                    relation, extra = _classify_coplanar_pair(
                        boundary_for(first),
                        boundary_for(second),
                        pair.get("area_a"),
                        pair.get("area_b"),
                        tol,
                    )
                except Exception as exc:  # pragma: no cover - live fallback
                    relation = "no_intersection_curve"
                    extra = {
                        "unverified_reason": "classification_error",
                        "error": str(exc),
                    }
                record = {**pair, **extra}
                if relation == "overlapping":
                    overlapping_pairs.append(record)
                elif relation == "contact":
                    contact_pairs.append(record)
                else:
                    unverified_pairs.append(record)

            if self._entity_ids(hm, model, ent.Surface) != surface_ids_before:
                raise RuntimeError(
                    "Surface set changed during intersection detection"
                )
        except Exception as exc:
            if checkpoint is None:
                raise
            self._rollback_after_failure(checkpoint, model_name, exc)

        def sample(pairs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
            return pairs[:PAIR_SAMPLE_LIMIT], len(pairs) > PAIR_SAMPLE_LIMIT

        adjacent_sample, adjacent_truncated = sample(adjacent_pairs)
        identical_sample, identical_truncated = sample(identical_pairs)
        intersecting_sample, intersecting_truncated = sample(intersecting_pairs)
        overlapping_sample, overlapping_truncated = sample(overlapping_pairs)
        contact_sample, contact_truncated = sample(contact_pairs)
        unverified_sample, unverified_truncated = sample(unverified_pairs)
        failed_sample, failed_truncated = sample(failed_pairs)
        error_sample, error_truncated = sample(distance_error_pairs)
        overlap_groups = _build_overlap_groups(overlapping_pairs, areas)
        group_sample, group_truncated = sample(overlap_groups)

        reviewable_pairs: list[dict[str, Any]] = []
        for relation, pairs in (
            ("identical", identical_pairs),
            ("intersecting", intersecting_pairs),
            ("overlapping", overlapping_pairs),
            ("contact", contact_pairs),
            ("no_intersection_curve", unverified_pairs),
            ("failed", failed_pairs),
        ):
            for pair in pairs:
                reviewable_pairs.append({**pair, "relation": relation})
        self._last_surface_pairs = {
            "model_name": model_name,
            "tolerance": tol,
            "surface_ids": list(targets),
            "pairs": reviewable_pairs,
        }
        self._last_overlap_groups = {
            "model_name": model_name,
            "tolerance": tol,
            "surface_ids": list(targets),
            "groups": overlap_groups,
        }

        return {
            "model_name": model_name,
            "tolerance": tol,
            "surface_count": len(targets),
            "pair_count": pair_count,
            "max_pairs": max_pairs,
            "adjacent_pair_count": len(adjacent_pairs),
            "adjacent_pairs_sample": adjacent_sample,
            "adjacent_pairs_truncated": adjacent_truncated,
            "separate_pair_count": separate_count,
            "candidate_pair_count": len(candidate_pairs),
            "identical_pair_count": len(identical_pairs),
            "identical_pairs_sample": identical_sample,
            "identical_pairs_truncated": identical_truncated,
            "intersecting_pair_count": len(intersecting_pairs),
            "intersecting_pairs_sample": intersecting_sample,
            "intersecting_pairs_truncated": intersecting_truncated,
            "overlapping_pair_count": len(overlapping_pairs),
            "overlapping_pairs_sample": overlapping_sample,
            "overlapping_pairs_truncated": overlapping_truncated,
            "overlap_group_count": len(overlap_groups),
            "overlap_groups_sample": group_sample,
            "overlap_groups_truncated": group_truncated,
            "contact_pair_count": len(contact_pairs),
            "contact_pairs_sample": contact_sample,
            "contact_pairs_truncated": contact_truncated,
            "no_intersection_curve_pair_count": len(unverified_pairs),
            "no_intersection_curve_pairs_sample": unverified_sample,
            "no_intersection_curve_pairs_truncated": unverified_truncated,
            "failed_pair_count": len(failed_pairs),
            "failed_pairs_sample": failed_sample,
            "failed_pairs_truncated": failed_truncated,
            "distance_error_count": len(distance_error_pairs),
            "distance_error_pairs_sample": error_sample,
            "distance_error_pairs_truncated": error_truncated,
            "checkpoint": checkpoint,
        }

    def analyze_curved_surface_overlaps(
        self,
        tolerance: float | None = None,
        samples_per_surface: int = 64,
        max_pairs: int = 50,
        coincident_coverage: float = 0.95,
        overlap_coverage: float = 0.05,
        normal_angle_limit: float = 10.0,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Test non-planar pairs from the last intersection report for overlap.

        Each surface is sampled by projecting a grid built from its boundary
        frame with hm_getsurfacenormalatcoordinate. Every sample is then
        projected onto the other surface and the fraction of samples within
        tolerance, plus the largest normal angle over those samples, decides
        between coincident, partial overlap, and contact. The symmetric
        coverage c = max(f_a, f_b) is used so that a small face lying on a
        large one is still recognised as coincident.
        """
        source = self._last_surface_pairs
        if source is None:
            raise ValueError(
                "No surface intersection report is available; call "
                "analyze_surface_intersections first"
            )
        if model_name is not None and source.get("model_name") not in (
            None,
            model_name,
        ):
            raise ValueError(
                "The stored report belongs to a different model; run "
                "analyze_surface_intersections again"
            )
        tol = (
            self._positive_float("tolerance", tolerance, maximum=10.0)
            if tolerance is not None
            else float(source["tolerance"])
        )
        if isinstance(samples_per_surface, bool) or not isinstance(
            samples_per_surface, int
        ):
            raise ValueError("samples_per_surface must be an integer")
        if not 4 <= samples_per_surface <= 400:
            raise ValueError(
                "samples_per_surface must be in the range [4, 400]"
            )
        if isinstance(max_pairs, bool) or not isinstance(max_pairs, int):
            raise ValueError("max_pairs must be an integer")
        if not 1 <= max_pairs <= 500:
            raise ValueError("max_pairs must be in the range [1, 500]")
        coincident_limit = self._positive_float(
            "coincident_coverage", coincident_coverage, maximum=1.0
        )
        overlap_limit = self._positive_float(
            "overlap_coverage", overlap_coverage, maximum=1.0
        )
        if overlap_limit >= coincident_limit:
            raise ValueError(
                "overlap_coverage must be smaller than coincident_coverage"
            )
        angle_limit = self._positive_float(
            "normal_angle_limit", normal_angle_limit, maximum=90.0
        )

        candidates = [
            pair
            for pair in source["pairs"]
            if pair.get("relation") == "no_intersection_curve"
            and pair.get("unverified_reason") == "non_planar_surface"
        ]
        if len(candidates) > max_pairs:
            raise ValueError(
                f"Curved-surface candidate count {len(candidates)} exceeds "
                f"max_pairs={max_pairs}; narrow the model or raise max_pairs"
            )

        hm, ent = self._imports()
        model = self._model(model_name)
        present = self._entity_ids(hm, model, ent.Surface)
        needed: set[int] = set()
        for pair in candidates:
            needed.add(int(pair["surface_a"]))
            needed.add(int(pair["surface_b"]))
        edges: dict[int, set[int]] = {}
        areas: dict[int, float | None] = {}
        for surface_id in sorted(needed):
            if surface_id not in present:
                continue
            surface = next(
                iter(hm.Collection(model, ent.Surface, [surface_id])), None
            )
            if surface is None:
                continue
            edge_values = getattr(surface, "edges", None) or []
            edges[surface_id] = {
                int(edge.id) for edge in edge_values if getattr(edge, "id", None)
            }
            raw_area = getattr(surface, "area", None)
            areas[surface_id] = float(raw_area) if raw_area is not None else None

        active_pairs = [
            pair
            for pair in candidates
            if int(pair["surface_a"]) in edges
            and int(pair["surface_b"]) in edges
        ]
        skipped_pairs = [
            {
                "surface_a": int(pair["surface_a"]),
                "surface_b": int(pair["surface_b"]),
            }
            for pair in candidates
            if int(pair["surface_a"]) not in edges
            or int(pair["surface_b"]) not in edges
        ]
        estimated_queries = (
            len(edges) * samples_per_surface
            + 2 * len(active_pairs) * samples_per_surface
        )
        if estimated_queries > 200000:
            raise ValueError(
                f"Estimated query count {estimated_queries} exceeds the "
                "200000 budget; reduce samples_per_surface or max_pairs"
            )

        import hw

        budget = {"queries": 0}
        started = time.perf_counter()
        samples_by_surface: dict[int, list[dict[str, Any]]] = {}
        for surface_id in sorted(edges):
            samples_by_surface[surface_id] = _surface_probe_samples(
                hw,
                surface_id,
                edges[surface_id],
                samples_per_surface,
                budget,
            )

        records: list[dict[str, Any]] = []
        for pair in active_pairs:
            first = int(pair["surface_a"])
            second = int(pair["surface_b"])
            first_metrics = _curved_side_metrics(
                hw,
                samples_by_surface.get(first, []),
                second,
                tol,
                budget,
            )
            second_metrics = _curved_side_metrics(
                hw,
                samples_by_surface.get(second, []),
                first,
                tol,
                budget,
            )
            relation, extra = _classify_curved_pair(
                first_metrics,
                second_metrics,
                pair.get("area_a"),
                pair.get("area_b"),
                tol,
                coincident_limit,
                overlap_limit,
                angle_limit,
            )
            records.append(
                {
                    "surface_a": first,
                    "surface_b": second,
                    "area_a": pair.get("area_a"),
                    "area_b": pair.get("area_b"),
                    "relation": relation,
                    **extra,
                }
            )
        elapsed = time.perf_counter() - started

        def select(relation: str) -> list[dict[str, Any]]:
            return [
                record for record in records if record["relation"] == relation
            ]

        coincident_pairs = select("coincident")
        partial_pairs = select("partial_overlap")
        contact_pairs = select("contact")
        separate_pairs = select("separate")
        failed_pairs = select("failed")
        overlap_pairs = coincident_pairs + partial_pairs
        groups = _build_overlap_groups(overlap_pairs, areas)
        sample = overlap_pairs[:PAIR_SAMPLE_LIMIT]
        sample_truncated = len(overlap_pairs) > PAIR_SAMPLE_LIMIT
        group_sample = groups[:PAIR_SAMPLE_LIMIT]
        group_truncated = len(groups) > PAIR_SAMPLE_LIMIT
        self._last_curved_overlaps = {
            "model_name": model_name,
            "tolerance": tol,
            "pairs": records,
            "groups": groups,
        }
        query_count = budget["queries"]
        return {
            "model_name": model_name,
            "tolerance": tol,
            "samples_per_surface": samples_per_surface,
            "coincident_coverage_limit": coincident_limit,
            "overlap_coverage_limit": overlap_limit,
            "normal_angle_limit_degrees": angle_limit,
            "candidate_pair_count": len(candidates),
            "evaluated_pair_count": len(records),
            "skipped_missing_pair_count": len(skipped_pairs),
            "skipped_missing_pairs_sample": skipped_pairs[:PAIR_SAMPLE_LIMIT],
            "surface_sample_counts": {
                str(surface_id): len(samples)
                for surface_id, samples in samples_by_surface.items()
            },
            "coincident_pair_count": len(coincident_pairs),
            "partial_overlap_pair_count": len(partial_pairs),
            "contact_pair_count": len(contact_pairs),
            "separate_pair_count": len(separate_pairs),
            "failed_pair_count": len(failed_pairs),
            "overlap_pair_count": len(overlap_pairs),
            "overlap_pairs_sample": sample,
            "overlap_pairs_truncated": sample_truncated,
            "contact_pairs_sample": contact_pairs[:PAIR_SAMPLE_LIMIT],
            "contact_pairs_truncated": len(contact_pairs) > PAIR_SAMPLE_LIMIT,
            "separate_pairs_sample": separate_pairs[:PAIR_SAMPLE_LIMIT],
            "failed_pairs_sample": failed_pairs[:PAIR_SAMPLE_LIMIT],
            "overlap_group_count": len(groups),
            "overlap_groups_sample": group_sample,
            "overlap_groups_truncated": group_truncated,
            "query_count": query_count,
            "elapsed_seconds": round(elapsed, 3),
            "milliseconds_per_query": (
                round(1000.0 * elapsed / query_count, 3)
                if query_count
                else None
            ),
        }

    def analyze_surface_repair_all(
        self,
        tolerance: float = 0.1,
        surface_ids: list[int] | None = None,
        max_pairs: int = 2000,
        samples_per_surface: int = 64,
        max_curved_pairs: int = 50,
        coincident_coverage: float = 0.95,
        overlap_coverage: float = 0.05,
        normal_angle_limit: float = 10.0,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Run duplicate, coplanar-overlap and curved-overlap detection together.

        analyze_surface_repair supplies the duplicate and free-edge summary,
        analyze_surface_intersections supplies the coplanar groups, and
        analyze_curved_surface_overlaps supplies the curved groups. Coplanar
        and curved overlap pairs are regrouped into one combined list so a
        single review covers all findings.
        """
        duplicate_report = self.analyze_surface_repair(
            tolerance=tolerance,
            surface_ids=surface_ids,
            model_name=model_name,
        )
        coplanar_report = self.analyze_surface_intersections(
            tolerance=tolerance,
            surface_ids=surface_ids,
            max_pairs=max_pairs,
            model_name=model_name,
        )

        self._last_curved_overlaps = None
        curved_report: dict[str, Any] | None = None
        curved_error: str | None = None
        try:
            curved_report = self.analyze_curved_surface_overlaps(
                tolerance=tolerance,
                samples_per_surface=samples_per_surface,
                max_pairs=max_curved_pairs,
                coincident_coverage=coincident_coverage,
                overlap_coverage=overlap_coverage,
                normal_angle_limit=normal_angle_limit,
                model_name=model_name,
            )
        except ValueError as exc:
            message = str(exc)
            if not message.startswith(
                ("Curved-surface candidate count", "Estimated query count")
            ):
                raise
            curved_error = message

        coplanar_groups = list(
            (self._last_overlap_groups or {}).get("groups") or []
        )
        curved_groups = list(
            (self._last_curved_overlaps or {}).get("groups") or []
        )
        areas: dict[int, float | None] = {}
        for group in coplanar_groups + curved_groups:
            for surface in group.get("surfaces", []):
                areas.setdefault(int(surface["id"]), surface.get("area"))
        merged_pairs: list[dict[str, Any]] = [
            pair for group in coplanar_groups for pair in group.get("pairs", [])
        ]
        merged_pairs.extend(
            record
            for record in (self._last_curved_overlaps or {}).get("pairs", [])
            if record.get("relation") in ("coincident", "partial_overlap")
        )
        combined_groups = _build_overlap_groups(merged_pairs, areas)
        combined_sample = combined_groups[:PAIR_SAMPLE_LIMIT]

        return {
            "model_name": model_name,
            "tolerance": duplicate_report["tolerance"],
            "surface_count": duplicate_report["surface_count"],
            "duplicate_count": duplicate_report["duplicate_count"],
            "duplicate_ids": duplicate_report["duplicate_ids"],
            "duplicate_ids_truncated": duplicate_report["duplicate_ids_truncated"],
            "free_edge_loop_count": duplicate_report["free_edge_loop_count"],
            "free_edge_count": duplicate_report["free_edge_count"],
            "free_edge_loops_sample": duplicate_report["free_edge_loops_sample"],
            "free_edge_loops_truncated": duplicate_report["free_edge_loops_truncated"],
            "coplanar_overlap_group_count": coplanar_report["overlap_group_count"],
            "curved_overlap_group_count": (
                curved_report["overlap_group_count"]
                if curved_report is not None
                else None
            ),
            "curved_error": curved_error,
            "combined_overlap_group_count": len(combined_groups),
            "combined_overlap_groups_sample": combined_sample,
            "combined_overlap_groups_truncated": (
                len(combined_groups) > PAIR_SAMPLE_LIMIT
            ),
            "coplanar": coplanar_report,
            "curved": curved_report,
        }

    def _require_surface_review(self) -> dict[str, Any]:
        review = self._surface_review
        if review is None:
            raise ValueError(
                "No surface review is active; call start_surface_review first"
            )
        return review

    @staticmethod
    def _surface_review_pending_deletions(review: dict[str, Any]) -> list[int]:
        pending: set[int] = set()
        for decision in review["decisions"]:
            choice = decision.get("decision")
            if choice == "delete_a":
                pending.add(int(decision["surface_a"]))
            elif choice == "delete_b":
                pending.add(int(decision["surface_b"]))
            elif choice == "delete_both":
                pending.add(int(decision["surface_a"]))
                pending.add(int(decision["surface_b"]))
        return sorted(pending)

    def _clear_surface_review_display(self) -> None:
        import hw

        hw.evalTcl("*setreviewmode 0")
        hw.evalTcl("*reviewclearall")
        hw.evalTcl("*showall")
        hw.evalTcl("*unmaskall")
        hw.evalTcl("hm_viewshaded")
        hw.evalTcl("hm_viewfit")

    def _apply_surface_review_group(self) -> None:
        """Isolate the current pair and show it with review colors.

        Display-only side effect: all entities are hidden, the pair and its
        boundary lines are shown, and the view is fitted to them.
        stop_surface_review restores the full display.
        """
        import hw

        review = self._require_surface_review()
        pair = review["pairs"][review["index"]]
        first = int(pair["surface_a"])
        second = int(pair["surface_b"])
        hm, ent = self._imports()
        model = self._model(review.get("model_name"))
        edge_ids: set[int] = set()
        for surface_id in (first, second):
            surface = next(
                iter(hm.Collection(model, ent.Surface, [surface_id])), None
            )
            for edge in getattr(surface, "edges", None) or []:
                try:
                    edge_ids.add(int(edge.id))
                except (AttributeError, TypeError, ValueError):
                    continue
        hw.evalTcl("*setreviewmode 0")
        hw.evalTcl("*reviewclearall")
        hw.evalTcl(f"*createmark surfs 1 {first} {second}")
        if edge_ids:
            hw.evalTcl(
                "*createmark lines 2 "
                + " ".join(str(edge_id) for edge_id in sorted(edge_ids))
            )
        hw.evalTcl("*displaynone")
        hw.evalTcl("*showentitybymark 1")
        if edge_ids:
            hw.evalTcl("*showentitybymark 2")
        hw.evalTcl(f"*createmark surfs 1 {first}")
        hw.evalTcl(f"*setreviewbymark surfs 1 {REVIEW_COLOR_A}")
        hw.evalTcl(f"*createmark surfs 2 {second}")
        hw.evalTcl(f"*setreviewbymark surfs 2 {REVIEW_COLOR_B}")
        hw.evalTcl("*setreviewcolormode 0")
        hw.evalTcl("*setreviewtransparentmode 1")
        hw.evalTcl("*setreviewmode 1")
        hw.evalTcl("hm_viewfit")

    def _surface_review_status(self, include_group: bool = True) -> dict[str, Any]:
        review = self._surface_review
        if review is None:
            return {"active": False, "finished": False, "group_count": 0}
        total = len(review["pairs"])
        finished = bool(review["finished"])
        index = review["index"]
        payload: dict[str, Any] = {
            "active": not finished,
            "finished": finished,
            "group_count": total,
            "index": min(index, total - 1),
            "remaining": max(0, total - index - (0 if finished else 1)),
            "decisions": list(review["decisions"]),
            "pending_delete_ids": self._surface_review_pending_deletions(review),
            "skipped_missing": review["skipped_missing"],
            "model_name": review["model_name"],
        }
        if include_group and not finished and total:
            payload["group"] = review["pairs"][index]
            payload["group_number"] = index + 1
        return payload

    def start_surface_review(
        self,
        model_name: str | None = None,
        relations: list[str] | None = None,
        start_index: int = 0,
    ) -> dict[str, Any]:
        """Start a stepwise visual review of the last intersection report.

        Pairs are shown with review colors while the rest of the model is
        dimmed. Display only: no geometry is deleted or modified.
        """
        source = self._last_surface_pairs
        if source is None:
            raise ValueError(
                "No surface pair report is available; "
                "run analyze_surface_intersections first"
            )
        if model_name is not None and source["model_name"] not in (None, model_name):
            raise ValueError("model_name does not match the analyzed model")
        allowed_relations = {
            "identical",
            "intersecting",
            "overlapping",
            "contact",
            "no_intersection_curve",
            "failed",
        }
        # Boundary contact and T-junction contact are deliberately excluded from
        # the default review list; request them explicitly if needed.
        selected = (
            set(relations)
            if relations
            else allowed_relations - {"contact"}
        )
        if not selected <= allowed_relations:
            unknown = sorted(selected - allowed_relations)
            raise ValueError("Unknown relations: " + ", ".join(unknown))
        hm, ent = self._imports()
        model = self._model(model_name or source["model_name"])
        existing = self._entity_ids(hm, model, ent.Surface)
        pairs: list[dict[str, Any]] = []
        skipped = 0
        for pair in source["pairs"]:
            if pair.get("relation") not in selected:
                continue
            if int(pair["surface_a"]) in existing and int(pair["surface_b"]) in existing:
                pairs.append(dict(pair))
            else:
                skipped += 1
        if not pairs:
            return {
                "started": False,
                "group_count": 0,
                "skipped_missing": skipped,
                "reason": "No reported pairs match the requested relations",
            }
        if isinstance(start_index, bool) or not isinstance(start_index, int):
            raise ValueError("start_index must be an integer")
        if not 0 <= start_index < len(pairs):
            raise ValueError(f"start_index must be in the range [0, {len(pairs) - 1}]")
        self._surface_review = {
            "model_name": (
                model_name if model_name is not None else source["model_name"]
            ),
            "pairs": pairs,
            "index": start_index,
            "decisions": [],
            "finished": False,
            "skipped_missing": skipped,
        }
        self._apply_surface_review_group()
        return self._surface_review_status()

    def show_surface_review_group(
        self, index: int | None = None, model_name: str | None = None
    ) -> dict[str, Any]:
        """Show one review group again; display only."""
        review = self._require_surface_review()
        if model_name is not None and review["model_name"] not in (None, model_name):
            raise ValueError("model_name does not match the active review")
        if index is None:
            index = review["index"]
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("index must be an integer")
        if not 0 <= index < len(review["pairs"]):
            raise ValueError(
                f"index must be in the range [0, {len(review['pairs']) - 1}]"
            )
        review["index"] = index
        review["finished"] = False
        self._apply_surface_review_group()
        return self._surface_review_status()

    def next_surface_review_group(
        self,
        decision: str | None = None,
        note: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Advance one group after the user has looked at the current pair.

        The decision value is recorded for later reference only; no surface is
        deleted. Deletion remains an explicit separate call.
        """
        review = self._require_surface_review()
        if model_name is not None and review["model_name"] not in (None, model_name):
            raise ValueError("model_name does not match the active review")
        if not review["finished"] and decision is not None:
            allowed_decisions = {
                "delete_a",
                "delete_b",
                "delete_both",
                "keep_both",
                "trim",
                "skip",
            }
            if decision not in allowed_decisions:
                raise ValueError(
                    "decision must be one of: " + ", ".join(sorted(allowed_decisions))
                )
            pair = review["pairs"][review["index"]]
            review["decisions"].append(
                {
                    "index": review["index"],
                    "surface_a": pair["surface_a"],
                    "surface_b": pair["surface_b"],
                    "relation": pair.get("relation"),
                    "decision": decision,
                    "note": str(note)[:200] if note else None,
                }
            )
        if review["finished"]:
            return self._surface_review_status(include_group=False)
        review["index"] += 1
        if review["index"] >= len(review["pairs"]):
            review["finished"] = True
            self._clear_surface_review_display()
            payload = self._surface_review_status(include_group=False)
            payload["completed"] = True
            return payload
        self._apply_surface_review_group()
        return self._surface_review_status()

    def stop_surface_review(self, model_name: str | None = None) -> dict[str, Any]:
        """Stop the review and clear its display; no geometry changes."""
        review = self._surface_review
        if review is None:
            return {"active": False, "finished": False, "stopped": True}
        if model_name is not None and review["model_name"] not in (None, model_name):
            raise ValueError("model_name does not match the active review")
        self._clear_surface_review_display()
        review["finished"] = True
        payload = self._surface_review_status(include_group=False)
        payload["stopped"] = True
        return payload

    def get_surface_review_status(self) -> dict[str, Any]:
        """Return the active review position and recorded decisions."""
        return self._surface_review_status()

    def _require_overlap_review(self) -> dict[str, Any]:
        review = self._overlap_review
        if review is None:
            raise ValueError(
                "No overlap review is active; call start_overlap_review first"
            )
        return review

    @staticmethod
    def _overlap_review_pending_deletions(review: dict[str, Any]) -> list[int]:
        pending: set[int] = set()
        for decision in review["decisions"]:
            for surface_id in decision.get("delete_ids") or []:
                pending.add(int(surface_id))
        return sorted(pending)

    def _apply_overlap_review_group(self) -> None:
        """Isolate one overlap group and colour every surface in it.

        Each surface receives a different review colour so that a chain such as
        A-B plus A-C is visible as one group instead of two pairs. Display only.
        """
        import hw

        review = self._require_overlap_review()
        group = review["groups"][review["index"]]
        surface_ids = [int(value) for value in group["surface_ids"]]
        hm, ent = self._imports()
        model = self._model(review.get("model_name"))
        edge_ids: set[int] = set()
        surface_edges: dict[int, set[int]] = {}
        for surface_id in surface_ids:
            owned: set[int] = set()
            surface = next(
                iter(hm.Collection(model, ent.Surface, [surface_id])), None
            )
            for edge in getattr(surface, "edges", None) or []:
                try:
                    owned.add(int(edge.id))
                except (AttributeError, TypeError, ValueError):
                    continue
            surface_edges[surface_id] = owned
            edge_ids.update(owned)
        hw.evalTcl("*setreviewmode 0")
        hw.evalTcl("*reviewclearall")
        if edge_ids:
            hw.evalTcl(
                "*createmark lines 2 "
                + " ".join(str(edge_id) for edge_id in sorted(edge_ids))
            )
        else:
            hw.evalTcl(
                "*createmark surfs 1 "
                + " ".join(str(surface_id) for surface_id in surface_ids)
            )
        hw.evalTcl("*displaynone")
        # Exactly coplanar faces occlude each other when shaded. Showing the
        # boundary lines instead keeps every member of the group visible; the
        # surface fill is suppressed in this preview.
        hw.evalTcl("*showentitybymark 2" if edge_ids else "*showentitybymark 1")
        for index, surface_id in enumerate(surface_ids):
            color = REVIEW_GROUP_COLORS[index % len(REVIEW_GROUP_COLORS)]
            owned = surface_edges.get(surface_id) or set()
            if owned:
                hw.evalTcl(
                    "*createmark lines 1 "
                    + " ".join(str(edge_id) for edge_id in sorted(owned))
                )
                hw.evalTcl(f"*setreviewbymark lines 1 {color}")
            else:
                hw.evalTcl(f"*createmark surfs 1 {surface_id}")
                hw.evalTcl(f"*setreviewbymark surfs 1 {color}")
        hw.evalTcl("*setreviewcolormode 0")
        hw.evalTcl("*setreviewtransparentmode 1")
        hw.evalTcl("*setreviewmode 1")
        hw.evalTcl("hm_viewfit")

    def _overlap_review_status(self, include_group: bool = True) -> dict[str, Any]:
        review = self._overlap_review
        if review is None:
            return {"active": False, "finished": False, "group_count": 0}
        total = len(review["groups"])
        finished = bool(review["finished"])
        index = review["index"]
        payload: dict[str, Any] = {
            "active": not finished,
            "finished": finished,
            "group_count": total,
            "index": min(index, total - 1),
            "remaining": max(0, total - index - (0 if finished else 1)),
            "decisions": list(review["decisions"]),
            "pending_delete_ids": self._overlap_review_pending_deletions(review),
            "skipped_missing": review["skipped_missing"],
            "model_name": review["model_name"],
        }
        if include_group and not finished and total:
            group = review["groups"][index]
            payload["group"] = group
            payload["group_number"] = index + 1
            payload["colors"] = [
                {
                    "surface_id": int(surface_id),
                    "color": REVIEW_GROUP_COLORS[
                        position % len(REVIEW_GROUP_COLORS)
                    ],
                }
                for position, surface_id in enumerate(group["surface_ids"])
            ]
        return payload

    def start_overlap_review(
        self,
        model_name: str | None = None,
        start_index: int = 0,
        source: str = "coplanar",
    ) -> dict[str, Any]:
        """Start a stepwise review of overlap groups.

        source selects the coplanar groups, the curved-surface groups, or the
        union of both (regrouped over the combined pair set). Every surface in
        the shown group is recoloured; the rest of the model is hidden.
        Display only: no geometry is deleted or modified.
        """
        if source not in {"all", "coplanar", "curved"}:
            raise ValueError("source must be one of: all, coplanar, curved")
        coplanar = self._last_overlap_groups
        curved = self._last_curved_overlaps
        if source in {"coplanar", "all"} and coplanar is None:
            raise ValueError(
                "No overlap group report is available; run "
                "analyze_surface_intersections, then start_overlap_review"
            )
        if source == "curved" and curved is None:
            raise ValueError(
                "No curved-surface overlap report is available; run "
                "analyze_curved_surface_overlaps, then start_overlap_review"
            )
        if source == "all" and curved is None:
            raise ValueError(
                "No curved-surface overlap report is available; run "
                "analyze_curved_surface_overlaps before reviewing all groups"
            )
        if source == "coplanar":
            model_name_source = coplanar["model_name"]
            review_groups = coplanar["groups"]
        elif source == "curved":
            model_name_source = curved["model_name"]
            review_groups = curved["groups"]
        else:
            if (
                coplanar["model_name"] is not None
                and curved["model_name"] is not None
                and coplanar["model_name"] != curved["model_name"]
            ):
                raise ValueError(
                    "Coplanar and curved reports belong to different models"
                )
            model_name_source = coplanar["model_name"] or curved["model_name"]
            areas: dict[int, float | None] = {}
            for group in coplanar["groups"] + curved["groups"]:
                for surface in group["surfaces"]:
                    areas.setdefault(int(surface["id"]), surface.get("area"))
            merged_pairs = [
                pair for group in coplanar["groups"] for pair in group["pairs"]
            ]
            merged_pairs.extend(
                record
                for record in curved["pairs"]
                if record.get("relation") in ("coincident", "partial_overlap")
            )
            review_groups = _build_overlap_groups(merged_pairs, areas)
        if model_name is not None and model_name_source not in (None, model_name):
            raise ValueError("model_name does not match the analyzed model")
        hm, ent = self._imports()
        model = self._model(model_name or model_name_source)
        existing = self._entity_ids(hm, model, ent.Surface)
        groups: list[dict[str, Any]] = []
        skipped = 0
        for group in review_groups:
            surface_ids = [int(value) for value in group["surface_ids"]]
            if all(surface_id in existing for surface_id in surface_ids):
                groups.append({**group, "surface_ids": surface_ids})
            else:
                skipped += 1
        if not groups:
            return {
                "started": False,
                "group_count": 0,
                "skipped_missing": skipped,
                "reason": "No overlap groups match the current model",
            }
        if isinstance(start_index, bool) or not isinstance(start_index, int):
            raise ValueError("start_index must be an integer")
        if not 0 <= start_index < len(groups):
            raise ValueError(f"start_index must be in the range [0, {len(groups) - 1}]")
        self._overlap_review = {
            "model_name": (
                model_name if model_name is not None else model_name_source
            ),
            "groups": groups,
            "index": start_index,
            "decisions": [],
            "finished": False,
            "skipped_missing": skipped,
        }
        self._apply_overlap_review_group()
        return self._overlap_review_status()

    def show_overlap_review_group(
        self, index: int | None = None, model_name: str | None = None
    ) -> dict[str, Any]:
        """Show one overlap group again; display only."""
        review = self._require_overlap_review()
        if model_name is not None and review["model_name"] not in (None, model_name):
            raise ValueError("model_name does not match the active review")
        if index is None:
            index = review["index"]
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("index must be an integer")
        if not 0 <= index < len(review["groups"]):
            raise ValueError(
                f"index must be in the range [0, {len(review['groups']) - 1}]"
            )
        review["index"] = index
        review["finished"] = False
        self._apply_overlap_review_group()
        return self._overlap_review_status()

    def next_overlap_review_group(
        self,
        decision: str | None = None,
        note: str | None = None,
        delete_ids: list[int] | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Advance one overlap group after the user has looked at it.

        Decisions are recorded for later reference only; no surface is deleted.
        delete_ids selects the surfaces the user wants removed from this group.
        """
        review = self._require_overlap_review()
        if model_name is not None and review["model_name"] not in (None, model_name):
            raise ValueError("model_name does not match the active review")
        if not review["finished"] and decision is not None:
            allowed_decisions = {"keep_all", "delete_some", "trim", "skip"}
            if decision not in allowed_decisions:
                raise ValueError(
                    "decision must be one of: " + ", ".join(sorted(allowed_decisions))
                )
            group = review["groups"][review["index"]]
            group_surfaces = {int(value) for value in group["surface_ids"]}
            validated_ids: list[int] = []
            if delete_ids is not None:
                if not isinstance(delete_ids, list):
                    raise ValueError("delete_ids must be a list of surface IDs")
                for value in delete_ids:
                    if isinstance(value, bool) or not isinstance(value, int):
                        raise ValueError("delete_ids must be a list of surface IDs")
                    if value not in group_surfaces:
                        raise ValueError(
                            "delete_ids must belong to the current overlap group"
                        )
                    if value not in validated_ids:
                        validated_ids.append(value)
            if decision == "delete_some" and not validated_ids:
                raise ValueError("delete_some requires delete_ids")
            if decision != "delete_some" and validated_ids:
                raise ValueError(
                    "delete_ids is only valid with decision='delete_some'"
                )
            review["decisions"].append(
                {
                    "index": review["index"],
                    "surface_ids": sorted(group_surfaces),
                    "decision": decision,
                    "delete_ids": validated_ids,
                    "note": str(note)[:200] if note else None,
                }
            )
        if review["finished"]:
            return self._overlap_review_status(include_group=False)
        review["index"] += 1
        if review["index"] >= len(review["groups"]):
            review["finished"] = True
            self._clear_surface_review_display()
            payload = self._overlap_review_status(include_group=False)
            payload["completed"] = True
            return payload
        self._apply_overlap_review_group()
        return self._overlap_review_status()

    def stop_overlap_review(self, model_name: str | None = None) -> dict[str, Any]:
        """Stop the overlap review and clear its display; no geometry changes."""
        review = self._overlap_review
        if review is None:
            return {"active": False, "finished": False, "stopped": True}
        if model_name is not None and review["model_name"] not in (None, model_name):
            raise ValueError("model_name does not match the active review")
        self._clear_surface_review_display()
        review["finished"] = True
        payload = self._overlap_review_status(include_group=False)
        payload["stopped"] = True
        return payload

    def get_overlap_review_status(self) -> dict[str, Any]:
        """Return the active overlap review position and decisions."""
        return self._overlap_review_status()

    def remove_duplicate_surfaces(
        self,
        tolerance: float = 0.1,
        surface_ids: list[int] | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Delete redundant coincident surfaces while keeping one of each set.

        The duplicate mark is produced by *surfacemark_duplicate_check with
        options=4 and deleted through the Python collection API. The checkpoint
        restores the model if the deleted set differs from the reported
        duplicates or if any solid is lost.
        """
        tol = self._positive_float("tolerance", tolerance, maximum=10.0)
        hm, ent = self._imports()
        model = self._model(model_name)
        targets, use_all = self._surface_targets(hm, model, ent, surface_ids)

        import hw

        self._mark_surfaces(targets, use_all=use_all)
        hw.evalTcl(
            f"*surfacemark_duplicate_check 1 1 4 0 1 {tol:.12g}"
        )
        duplicate_ids = sorted(
            set(self._tcl_int_ids(hw.evalTcl("hm_getmark surfs 1"))) & set(targets)
        )
        surface_ids_before = self._entity_ids(hm, model, ent.Surface)
        solid_ids_before = self._entity_ids(hm, model, ent.Solid)
        duplicate_sample, duplicate_truncated = self._id_sample(duplicate_ids)

        if not duplicate_ids:
            return {
                "removed": False,
                "tolerance": tol,
                "duplicate_count": 0,
                "deleted_ids": [],
                "deleted_count": 0,
                "deleted_ids_truncated": False,
                "surface_count_before": len(surface_ids_before),
                "surface_count_after": len(surface_ids_before),
                "model_name": model_name,
                "checkpoint": None,
            }
        if len(duplicate_ids) >= len(targets):
            raise RuntimeError(
                "Duplicate detection marked every selected surface; "
                "reduce the tolerance before deleting"
            )

        checkpoint = self.create_checkpoint(
            "before_duplicate_surface_removal", model_name
        )
        try:
            # *deletemark returns a bare Tcl status on this build and can leave
            # the Python collection in an inconsistent state. The collection
            # API deletes free surfaces cleanly; the checkpoint still guards
            # against the solid-cascade behaviour seen with solids.
            hm.Collection(model, ent.Surface, duplicate_ids).delete_items()
            surface_ids_after = self._entity_ids(hm, model, ent.Surface)
            lost = sorted(surface_ids_before - surface_ids_after)
            if lost != duplicate_ids:
                raise RuntimeError(
                    "Duplicate removal deleted an unexpected surface set: "
                    + str(lost[:20])
                )
            lost_solids = sorted(
                solid_ids_before - self._entity_ids(hm, model, ent.Solid)
            )
            if lost_solids:
                raise RuntimeError(
                    "Duplicate removal also removed solids: "
                    + str(lost_solids[:20])
                )
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)

        return {
            "removed": True,
            "tolerance": tol,
            "duplicate_count": len(duplicate_ids),
            "deleted_ids": duplicate_sample,
            "deleted_count": len(duplicate_ids),
            "deleted_ids_truncated": duplicate_truncated,
            "surface_count_before": len(surface_ids_before),
            "surface_count_after": len(self._entity_ids(hm, model, ent.Surface)),
            "model_name": model_name,
            "checkpoint": checkpoint,
        }

    def stitch_free_edges(
        self,
        tolerance: float = 0.2,
        max_attempts: int = 5000,
        surface_ids: list[int] | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Stitch every free surface edge with *edgesmerge until none merge.

        Each pass reads the current free edges and all model lines, samples the
        line geometry, and attempts candidate pairs (free edge -> any other
        line) in ascending distance order. A free edge may therefore merge onto
        an existing shared edge, as in Stitch > Surface Edges. A successful
        merge restarts the pass because edge IDs change; the method stops when
        a full pass merges nothing or the attempt budget is spent. The surface
        set must stay unchanged and the free-edge count must not increase;
        otherwise the checkpoint restores the model.
        """
        tol = self._positive_float("tolerance", tolerance, maximum=10.0)
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise ValueError("max_attempts must be an integer")
        if not 1 <= max_attempts <= 20000:
            raise ValueError("max_attempts must be in the range [1, 20000]")
        hm, ent = self._imports()
        model = self._model(model_name)
        targets, use_all = self._surface_targets(hm, model, ent, surface_ids)

        import hw

        def free_edge_ids() -> list[int]:
            self._mark_surfaces(targets, use_all=use_all)
            value = hw.evalTcl(
                'join [hm_getedgeloops surfaces markid=1 looptype=3] ";"'
            )
            return sorted(
                {
                    edge_id
                    for chunk in str(value or "").split(";")
                    for edge_id in self._tcl_int_ids(chunk)[1:]
                }
            )

        def all_line_ids() -> list[int]:
            hw.evalTcl('*createmark lines 1 "all"')
            return sorted(self._tcl_int_ids(hw.evalTcl("hm_getmark lines 1")))

        def sampled_points(
            line_ids: list[int],
        ) -> dict[int, list[tuple[float, float, float]]]:
            sampled: dict[int, list[tuple[float, float, float]]] = {}
            for line_id in line_ids:
                try:
                    raw = hw.evalTcl(
                        f"hm_getcoordinatesofpointsonline {line_id} "
                        f"{{{BOUNDARY_SAMPLE_PARAMS}}}"
                    )
                    points = _parse_point_list(raw)
                except Exception:
                    points = []
                if len(points) >= 2:
                    sampled[line_id] = points
            return sampled

        def candidate_pairs(
            free: list[int], all_lines: list[int]
        ) -> list[tuple[float, int, int]]:
            points = sampled_points(all_lines)
            candidates: list[tuple[float, int, int]] = []
            for source in free:
                if source not in points:
                    continue
                for target in all_lines:
                    if target == source or target not in points:
                        continue
                    distance = _polyline_distance(points[source], points[target])
                    if distance <= tol:
                        candidates.append((distance, source, target))
            candidates.sort(key=lambda item: item[0])
            return candidates

        before = self._free_edge_summary(targets, use_all)
        if not free_edge_ids():
            return {
                "stitched": False,
                "tolerance": tol,
                "max_attempts": max_attempts,
                "surface_count": len(targets),
                "free_edge_loop_count_before": before["loop_count"],
                "free_edge_loop_count_after": before["loop_count"],
                "free_edge_count_before": before["edge_count"],
                "free_edge_count_after": before["edge_count"],
                "free_edges_reduced": 0,
                "merge_attempt_count": 0,
                "attempt_error_count": 0,
                "merged_pair_count": 0,
                "merged_pairs_sample": [],
                "merged_pairs_truncated": False,
                "model_name": model_name,
                "checkpoint": None,
            }

        surface_ids_before = self._entity_ids(hm, model, ent.Surface)
        checkpoint = self.create_checkpoint("before_free_edge_stitch", model_name)
        merged: list[dict[str, Any]] = []
        attempts = 0
        attempt_errors = 0
        try:
            while attempts < max_attempts:
                free = free_edge_ids()
                if not free:
                    break
                all_lines = all_line_ids()
                if not all_lines:
                    break
                candidates = candidate_pairs(free, all_lines)
                progress = False
                for _, source, target in candidates:
                    if attempts >= max_attempts:
                        break
                    attempts += 1
                    try:
                        hw.evalTcl(f"*edgesmerge {source} {target} {tol:.12g}")
                    except Exception:
                        attempt_errors += 1
                        continue
                    if len(free_edge_ids()) < len(free):
                        merged.append({"edge_a": source, "edge_b": target})
                        progress = True
                        break
                if not progress:
                    break
            if self._entity_ids(hm, model, ent.Surface) != surface_ids_before:
                raise RuntimeError("Free-edge stitching changed the surface set")
            after = self._free_edge_summary(targets, use_all)
            if after["edge_count"] > before["edge_count"]:
                raise RuntimeError(
                    "Free-edge stitching increased the free-edge count: "
                    f"{before['edge_count']} -> {after['edge_count']}"
                )
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)

        merged_sample = merged[:PAIR_SAMPLE_LIMIT]
        return {
            "stitched": bool(merged),
            "tolerance": tol,
            "max_attempts": max_attempts,
            "surface_count": len(targets),
            "free_edge_loop_count_before": before["loop_count"],
            "free_edge_loop_count_after": after["loop_count"],
            "free_edge_count_before": before["edge_count"],
            "free_edge_count_after": after["edge_count"],
            "free_edges_reduced": before["edge_count"] - after["edge_count"],
            "merge_attempt_count": attempts,
            "attempt_error_count": attempt_errors,
            "merged_pair_count": len(merged),
            "merged_pairs_sample": merged_sample,
            "merged_pairs_truncated": len(merged) > len(merged_sample),
            "model_name": model_name,
            "checkpoint": checkpoint,
        }

    @staticmethod
    def _entity_kwargs(
        values: dict[str, Any] | None,
        reserved: set[str],
        maximum: int = 40,
    ) -> dict[str, Any]:
        values = values or {}
        if not isinstance(values, dict) or len(values) > maximum:
            raise ValueError(f"values must be an object containing at most {maximum} attributes")
        clean: dict[str, Any] = {}
        for key, value in values.items():
            if (
                not isinstance(key, str)
                or not key
                or key.startswith("_")
                or key in reserved
            ):
                raise ValueError(f"Entity creation attribute is not allowed: {key!r}")
            clean[key] = value
        return clean

    def trim_free_edges(
        self,
        surface_ids: list[int] | None = None,
        line_ids: list[int] | None = None,
        vector: list[float] | None = None,
        distance: float = 0.0,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Trim surfaces along free-edge lines (Surface Edit > Trim with Lines).

        When line_ids is omitted, every line in the model's free-edge loops is
        used. Lines are swept through the whole surface along the surface normal
        (or along the supplied vector). The operation is checkpointed; the model
        is restored when nothing changes, when the element count drops, or when
        the free-edge count increases.
        """
        clean_distance = self._nonnegative_float(
            "distance", distance, maximum=1.0e6
        )
        components: list[float] | None = None
        if vector is not None:
            if not isinstance(vector, list) or len(vector) != 3:
                raise ValueError("vector must contain three numeric components")
            components = []
            for value in vector:
                try:
                    number = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "vector must contain three numeric components"
                    ) from exc
                if not math.isfinite(number):
                    raise ValueError("vector components must be finite")
                components.append(number)
            if all(abs(value) < 1.0e-12 for value in components):
                raise ValueError("vector must not be the zero vector")

        hm, ent = self._imports()
        model = self._model(model_name)
        targets, use_all = self._surface_targets(hm, model, ent, surface_ids)

        import hw

        if line_ids is not None:
            _, lines = self._checked_collection(model, "line", line_ids)
        else:
            self._mark_surfaces(targets, use_all=use_all)
            value = hw.evalTcl(
                'join [hm_getedgeloops surfaces markid=1 looptype=3] ";"'
            )
            lines = sorted(
                {
                    edge
                    for chunk in str(value or "").split(";")
                    for edge in self._tcl_int_ids(chunk)[1:]
                }
            )
            if len(lines) > MAX_GEOMETRY_IDS_PER_CALL:
                raise ValueError(
                    f"At most {MAX_GEOMETRY_IDS_PER_CALL} free-edge lines may be "
                    "trimmed per call; restrict surface_ids"
                )
        if not lines:
            raise RuntimeError(
                "No free-edge lines were found on the selected surfaces"
            )

        surface_ids_before = self._entity_ids(hm, model, ent.Surface)
        element_ids_before = self._entity_ids(hm, model, ent.Element)
        areas_before = self._surface_area_map(hm, model, ent)
        before = self._free_edge_summary(targets, use_all)

        checkpoint = self.create_checkpoint("before_free_edge_trim", model_name)
        try:
            self._mark_surfaces(targets, use_all=use_all)
            hw.evalTcl(
                "*createmark lines 1 "
                + " ".join(str(value) for value in lines)
            )
            if components is None:
                vector_id = 0
                trim_flag = 5
            else:
                hw.evalTcl(
                    "*createvector 1 "
                    + " ".join(f"{value:.12g}" for value in components)
                )
                vector_id = 1
                trim_flag = 1
            hw.evalTcl(
                f"*surfacemarksplitwithlines 1 1 {vector_id} {trim_flag} "
                f"{clean_distance:.12g}"
            )

            surface_ids_after = self._entity_ids(hm, model, ent.Surface)
            if len(self._entity_ids(hm, model, ent.Element)) < len(
                element_ids_before
            ):
                raise RuntimeError(
                    "Free-edge trimming reduced the element count: "
                    f"{len(element_ids_before)} -> "
                    f"{len(self._entity_ids(hm, model, ent.Element))}"
                )
            created_ids = sorted(surface_ids_after - surface_ids_before)
            lost_ids = sorted(surface_ids_before - surface_ids_after)
            areas_after = self._surface_area_map(hm, model, ent)
            changed_areas = [
                surface_id
                for surface_id in targets
                if areas_before.get(surface_id) is not None
                and areas_after.get(surface_id) is not None
                and abs(
                    areas_before[surface_id] - areas_after[surface_id]
                )
                > max(1.0e-9, 1.0e-6 * abs(areas_before[surface_id]))
            ]
            after = self._free_edge_summary(targets, use_all)
            if after["edge_count"] > before["edge_count"]:
                raise RuntimeError(
                    "Free-edge trimming increased the free-edge count: "
                    f"{before['edge_count']} -> {after['edge_count']}"
                )
            if not (
                created_ids
                or lost_ids
                or changed_areas
                or after["edge_count"] < before["edge_count"]
            ):
                raise RuntimeError(
                    "Free-edge trimming produced no measurable change; the "
                    "free-edge lines may not cross the selected surfaces"
                )
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)

        created_sample, created_truncated = self._id_sample(created_ids)
        lost_sample, lost_truncated = self._id_sample(lost_ids)
        risks: list[dict[str, Any]] = [
            {
                "code": "checkpoint_rollback_only",
                "severity": "info",
                "message": (
                    "Undo is available only by rolling back the checkpoint "
                    "created before this trim."
                ),
            },
            {
                "code": "downstream_reference_invalidation",
                "severity": "warning",
                "message": (
                    "Trimming can change surface IDs; meshes, loads, connectors, "
                    "and saved selections that reference the old surfaces may be "
                    "invalid."
                ),
            },
        ]
        if created_ids or lost_ids:
            risks.append(
                {
                    "code": "surface_topology_changed",
                    "severity": "warning",
                    "message": (
                        "The surface set changed during trimming: "
                        f"{len(created_ids)} created and {len(lost_ids)} removed."
                    ),
                }
            )
        if element_ids_before:
            risks.append(
                {
                    "code": "existing_mesh_present",
                    "severity": "warning",
                    "message": (
                        "The model already contains elements; trimming can "
                        "detach or invalidate mesh regions adjacent to the cut."
                    ),
                }
            )
        return {
            "trimmed": True,
            "operation": "free_edges",
            "projection": (
                "normal" if components is None else {"vector": components}
            ),
            "line_count": len(lines),
            "surface_count": len(targets),
            "surface_count_before": len(surface_ids_before),
            "surface_count_after": len(surface_ids_after),
            "created_surface_ids": created_sample,
            "created_surface_ids_truncated": created_truncated,
            "lost_surface_ids": lost_sample,
            "lost_surface_ids_truncated": lost_truncated,
            "changed_surface_ids": changed_areas[:ID_SAMPLE_LIMIT],
            "free_edge_count_before": before["edge_count"],
            "free_edge_count_after": after["edge_count"],
            "free_edges_reduced": before["edge_count"] - after["edge_count"],
            "risk_annotations": risks,
            "checkpoint": checkpoint,
            "model_name": model_name,
        }

    def create_solver_card_entity(
        self,
        entity_type: str,
        name: str,
        cardimage: str | None = None,
        values: dict[str, Any] | None = None,
        references: dict[str, dict[str, Any]] | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Create one typed solver-card entity with explicit entity references."""
        key, entity_class = self._entity_class(entity_type)
        if key not in SOLVER_CARD_ENTITY_TYPES:
            raise ValueError(
                "entity_type must be one of: " + ", ".join(sorted(SOLVER_CARD_ENTITY_TYPES))
            )
        clean_name = str(name).strip()
        if not clean_name or len(clean_name) > 128:
            raise ValueError("name must contain between 1 and 128 characters")
        clean_values = self._entity_kwargs(values, {"id", "name", "cardimage"})
        references = references or {}
        if not isinstance(references, dict) or len(references) > 20:
            raise ValueError("references must contain at most 20 entity attributes")
        if set(clean_values).intersection(references):
            raise ValueError("An attribute cannot appear in both values and references")

        kwargs: dict[str, Any] = {"name": clean_name, **clean_values}
        if cardimage is not None:
            clean_card = str(cardimage).strip()
            if not clean_card or len(clean_card) > 128:
                raise ValueError("cardimage must contain between 1 and 128 characters")
            kwargs["cardimage"] = clean_card
        model = self._model(model_name)
        for attribute, spec in references.items():
            if not isinstance(attribute, str) or not attribute or attribute.startswith("_"):
                raise ValueError(f"Invalid reference attribute: {attribute!r}")
            if not isinstance(spec, dict):
                raise ValueError(f"references[{attribute!r}] must be an object")
            ref_key, ref_class = self._entity_class(str(spec.get("entity_type", "")))
            ref_id = int(spec.get("entity_id", 0))
            if ref_id <= 0:
                raise ValueError(f"references[{attribute!r}].entity_id must be positive")
            self._checked_collection(model, ref_key, [ref_id])
            kwargs[attribute] = ref_class(model, ref_id)

        checkpoint = self.create_checkpoint(f"before_{key}", model_name)
        try:
            entity = entity_class(model, **kwargs)
            # Some attributes are accepted by the constructor without being
            # stored (observed for face_medium_thickness under the FEKO
            # profile). Read back from a fresh entity and repair in place.
            stored_entity = entity_class(model, int(entity.id))
            for attribute, expected in clean_values.items():
                if not _attribute_matches(getattr(stored_entity, attribute, None), expected):
                    setattr(stored_entity, attribute, expected)
                    if not _attribute_matches(getattr(stored_entity, attribute, None), expected):
                        raise RuntimeError(f"Attribute did not persist: {attribute}")
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        attributes = ["id", "name", "cardimage", *clean_values, *references]
        return {
            "created": True,
            "entity_type": key,
            "model_name": model_name,
            "entity": self._serialize_entity(entity, attributes),
            "checkpoint": checkpoint,
        }

    def _set_current_loadcol(self, model: Any, ent: Any, loadcol_id: int | None) -> dict[str, Any] | None:
        if loadcol_id is None:
            return None
        clean_id = int(loadcol_id)
        self._checked_collection(model, "loadcol", [clean_id])
        loadcol = ent.Loadcol(model, clean_id)
        model.currentcollector(entity_type=ent.Loadcol, name=str(loadcol.name))
        return {"id": clean_id, "name": str(loadcol.name)}

    def create_nodal_load(
        self,
        node_ids: list[int],
        load_kind: str,
        components: list[float],
        solver_type: int = 1,
        loadcol_id: int | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Create force, moment, SPC, temperature, flux, velocity, or acceleration loads."""
        kind = str(load_kind).strip().lower()
        if kind not in NODAL_LOAD_CONFIGS:
            raise ValueError("Unsupported load_kind: " + kind)
        expected = 6 if kind == "constraint" else 3
        if not isinstance(components, (list, tuple)) or len(components) != expected:
            raise ValueError(f"components must contain {expected} values for {kind}")
        clean_components = [float(value) for value in components]
        if not all(math.isfinite(value) for value in clean_components):
            raise ValueError("components must contain finite values")
        if kind == "constraint":
            # HyperMesh uses -999999.0 to mark an unconstrained DOF.
            clean_components = [
                -999999.0 if value == -999999.0 else value for value in clean_components
            ]
        else:
            clean_components.extend([0.0, 0.0, 0.0])
        hm, ent = self._imports()
        model = self._model(model_name)
        collection, clean_ids = self._checked_collection(model, "node", node_ids)
        current = self._set_current_loadcol(model, ent, loadcol_id)
        checkpoint = self.create_checkpoint(f"before_{kind}_load", model_name)
        load_class = getattr(ent, "Load", None)
        before = self._entity_ids(hm, model, load_class) if load_class is not None else set()
        try:
            result = _unwrap_hm_call(
                model.loadcreateonentity(
                    collection=collection,
                    config=NODAL_LOAD_CONFIGS[kind],
                    type=int(solver_type),
                    comp1=clean_components[0], comp2=clean_components[1],
                    comp3=clean_components[2], comp4=clean_components[3],
                    comp5=clean_components[4], comp6=clean_components[5],
                )
            )
            created_ids = (
                sorted(self._entity_ids(hm, model, load_class) - before)
                if load_class is not None else []
            )
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        return {
            "created": True,
            "load_kind": kind,
            "node_ids": clean_ids,
            "components": clean_components[:expected],
            "solver_type": int(solver_type),
            "loadcol": current,
            "load_ids": created_ids,
            "checkpoint": checkpoint,
            "call_result": result,
        }

    def create_pressure_load(
        self,
        entity_type: str,
        entity_ids: list[int],
        magnitude: float,
        direction: list[float] | None = None,
        face_node_ids: list[int] | None = None,
        break_angle: float = 30.0,
        loadcol_id: int | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        key = str(entity_type).strip().lower()
        if key not in {"surface", "element"}:
            raise ValueError("entity_type must be surface or element")
        pressure = self._nonnegative_float("magnitude", magnitude)
        vector = (0.0, 0.0, 0.0) if direction is None else self._finite_vector("direction", direction)
        angle = float(break_angle)
        if not math.isfinite(angle) or not 0.0 <= angle <= 180.0:
            raise ValueError("break_angle must be between 0 and 180 degrees")
        hm, ent = self._imports()
        model = self._model(model_name)
        collection, clean_ids = self._checked_collection(model, key, entity_ids)
        if face_node_ids:
            node_collection, clean_nodes = self._checked_collection(model, "node", face_node_ids)
        else:
            node_collection = hm.Collection(model, ent.Node, populate=False)
            clean_nodes = []
        current = self._set_current_loadcol(model, ent, loadcol_id)
        checkpoint = self.create_checkpoint("before_pressure_load", model_name)
        try:
            result = _unwrap_hm_call(
                model.pressuresonentity(
                    collection=collection,
                    nodes_collection=node_collection,
                    x=vector[0], y=vector[1], z=vector[2],
                    magnitude=pressure, breakangle=angle, onface=1,
                )
            )
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        return {
            "created": True,
            "load_kind": "pressure",
            "entity_type": key,
            "entity_ids": clean_ids,
            "face_node_ids": clean_nodes,
            "magnitude": pressure,
            "direction": list(vector),
            "loadcol": current,
            "checkpoint": checkpoint,
            "call_result": result,
        }

    def create_loadstep(
        self,
        name: str,
        analysis_type_attribute: str,
        analysis_type: int | str,
        load_attribute: str | None = None,
        loadcol_id: int | None = None,
        spc_attribute: str | None = None,
        spc_loadcol_id: int | None = None,
        cardimage: str | None = None,
        values: dict[str, Any] | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", str(analysis_type_attribute)):
            raise ValueError("Invalid analysis_type_attribute")
        clean_values = dict(values or {})
        clean_values[str(analysis_type_attribute)] = analysis_type
        references: dict[str, dict[str, Any]] = {}
        for attribute, entity_id in (
            (load_attribute, loadcol_id),
            (spc_attribute, spc_loadcol_id),
        ):
            if attribute is None and entity_id is None:
                continue
            if attribute is None or entity_id is None:
                raise ValueError("Load-step reference attribute and loadcol ID must be supplied together")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", str(attribute)):
                raise ValueError(f"Invalid load-step reference attribute: {attribute!r}")
            references[str(attribute)] = {"entity_type": "loadcol", "entity_id": int(entity_id)}
        return self.create_solver_card_entity(
            entity_type="loadstep",
            name=name,
            cardimage=cardimage,
            values=clean_values,
            references=references,
            model_name=model_name,
        )

    @staticmethod
    def _finite_vector(name: str, values: list[float], *, positive: bool = False) -> tuple[float, ...]:
        if not isinstance(values, (list, tuple)) or len(values) != 3:
            raise ValueError(f"{name} must contain exactly three numeric values")
        try:
            result = tuple(float(value) for value in values)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} contains a non-numeric value") from exc
        if not all(math.isfinite(value) for value in result):
            raise ValueError(f"{name} must contain finite values")
        if positive and not all(value > 0.0 for value in result):
            raise ValueError(f"{name} values must be positive")
        return result

    @staticmethod
    def _positive_float(name: str, value: float, maximum: float = 1.0e12) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if not math.isfinite(result) or not 0.0 < result <= maximum:
            raise ValueError(f"{name} must be finite and in the range (0, {maximum}]")
        return result

    @staticmethod
    def _unit_vector(values: list[float], name: str = "axis") -> tuple[float, float, float]:
        vector = HandlerRegistry._finite_vector(name, values)
        length = math.sqrt(sum(item * item for item in vector))
        if length <= 1.0e-12:
            raise ValueError(f"{name} must have non-zero length")
        return tuple(item / length for item in vector)

    @staticmethod
    def _cross(
        left: tuple[float, float, float], right: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        return (
            left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0],
        )

    @staticmethod
    def _entity_ids(hm: Any, model: Any, entity_class: Any) -> set[int]:
        return {int(entity.id) for entity in hm.Collection(model, entity_class)}

    @staticmethod
    def _surface_area_map(hm: Any, model: Any, ent: Any) -> dict[int, float | None]:
        """Return surface area by ID, leaving unavailable areas as None."""
        areas: dict[int, float | None] = {}
        for surface in hm.Collection(model, ent.Surface):
            try:
                raw_area = getattr(surface, "area", None)
                areas[int(surface.id)] = (
                    float(raw_area) if raw_area is not None else None
                )
            except Exception:
                areas[int(surface.id)] = None
        return areas

    @staticmethod
    def _id_sample(ids: list[int]) -> tuple[list[int], bool]:
        """Return a bounded ID sample and whether the original list was truncated."""
        if len(ids) <= ID_SAMPLE_LIMIT:
            return list(ids), False
        return list(ids[:ID_SAMPLE_LIMIT]), True

    def _checked_collection(
        self, model: Any, entity_type: str, ids: list[int]
    ) -> tuple[Any, list[int]]:
        hm, _ = self._imports()
        key, entity_class = self._entity_class(entity_type)
        if not isinstance(ids, list) or not ids:
            raise ValueError(f"{key}_ids must be a non-empty list")
        limit = MAX_MESH_ELEMENT_IDS if key == "element" else MAX_GEOMETRY_IDS_PER_CALL
        if len(ids) > limit:
            raise ValueError(f"At most {limit} {key} IDs may be used per call")
        clean = [int(value) for value in ids]
        if any(value <= 0 for value in clean) or len(set(clean)) != len(clean):
            raise ValueError(f"{key}_ids must contain unique positive IDs")
        existing = self._entity_ids(hm, model, entity_class)
        missing = sorted(set(clean) - existing)
        if missing:
            raise ValueError(f"Referenced {key} IDs do not exist: {missing[:20]}")
        return hm.Collection(model, entity_class, clean), clean

    @staticmethod
    def _dof_code(name: str, value: int) -> int:
        try:
            code = str(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer made from DOF digits 1 through 6") from exc
        if not code or any(char not in "123456" for char in code) or len(set(code)) != len(code):
            raise ValueError(
                f"{name} must contain unique DOF digits from 1 through 6, for example 123456"
            )
        return int(code)

    @staticmethod
    def _nonnegative_float(name: str, value: float, maximum: float = 1.0e12) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if not math.isfinite(result) or not 0.0 <= result <= maximum:
            raise ValueError(f"{name} must be finite and in the range [0, {maximum}]")
        return result

    def _checkpoint_root(self) -> Path:
        if not self.allowed_roots:
            raise RuntimeError("No save roots are configured for live-model checkpoints")
        root = self.allowed_roots[0] / ".hyperworks_mcp" / "checkpoints"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def create_checkpoint(
        self, label: str = "manual", model_name: str | None = None
    ) -> dict[str, Any]:
        clean_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label).strip())[:48]
        if not clean_label:
            clean_label = "checkpoint"
        path = self._checkpoint_root() / (
            time.strftime("%Y%m%d_%H%M%S")
            + "_"
            + clean_label
            + "_"
            + uuid.uuid4().hex[:8]
            + ".hm"
        )
        self._model(model_name).writefile(
            filename=path.as_posix(), do_not_write_facets=0
        )
        return {
            "created": True,
            "checkpoint_file": str(path),
            "model_name": model_name,
            "label": clean_label,
        }

    def rollback_checkpoint(
        self,
        checkpoint_file: str,
        confirm: bool = False,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        if not confirm:
            raise ValueError(
                "Rollback replaces the current model state; set confirm=true after approval"
            )
        path = self._safe_input(checkpoint_file)
        try:
            path.relative_to(self._checkpoint_root())
        except ValueError as exc:
            raise ValueError("checkpoint_file is not a bridge-created checkpoint") from exc
        if path.suffix.lower() != ".hm":
            raise ValueError("checkpoint_file must use the .hm extension")
        model = self._model(model_name)
        model.hm_answernext(answer="yes")
        result = _unwrap_hm_call(
            model.readfile(filename=path.as_posix(), load_cad_geometry_as_graphics=0)
        )
        return {
            "rolled_back": True,
            "checkpoint_file": str(path),
            "model_name": model_name,
            "read_result": result,
        }

    def _rollback_after_failure(
        self, checkpoint: dict[str, Any], model_name: str | None, exc: Exception
    ) -> None:
        rollback_error = None
        try:
            self.rollback_checkpoint(
                checkpoint["checkpoint_file"], confirm=True, model_name=model_name
            )
        except Exception as rollback_exc:  # pragma: no cover - defensive live fallback
            rollback_error = str(rollback_exc)
        message = (
            f"{exc}. The live model was restored from checkpoint "
            f"{checkpoint['checkpoint_file']}"
        )
        if rollback_error:
            message += f"; rollback also reported: {rollback_error}"
        raise RuntimeError(message) from exc

    def create_rigid_link(
        self,
        independent_node_id: int,
        dependent_node_ids: list[int],
        dofs: int = 123456,
        model_name: str | None = None,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Create one solver-profile rigid link from existing live nodes."""
        independent_id = int(independent_node_id)
        if independent_id <= 0:
            raise ValueError("independent_node_id must be positive")
        dof_code = self._dof_code("dofs", dofs)
        hm, ent = self._imports()
        model = self._model(model_name)
        dependent_collection, dependent_ids = self._checked_collection(
            model, "node", dependent_node_ids
        )
        self._checked_collection(model, "node", [independent_id])
        if independent_id in dependent_ids:
            raise ValueError("independent_node_id must not also be a dependent node")

        checkpoint = self.create_checkpoint("before_rigid_link", model_name)
        before = self._entity_ids(hm, model, ent.Element)
        try:
            result = _unwrap_hm_call(
                model.rigidlink(
                    independent=ent.Node(model, independent_id),
                    collection=dependent_collection,
                    dofs=dof_code,
                )
            )
            created_ids = sorted(self._entity_ids(hm, model, ent.Element) - before)
            if not created_ids:
                raise RuntimeError("HyperMesh did not expose a newly created rigid-link element")
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)

        refresh_result = None
        refresh_error = None
        if refresh:
            try:
                refresh_result = self.refresh_view(fit=False)
            except Exception as exc:  # pragma: no cover - graphics-only live failure
                refresh_error = str(exc)
        return {
            "created": True,
            "connection_type": "rigid_link",
            "model_name": model_name,
            "element_ids": created_ids,
            "independent_node_id": independent_id,
            "dependent_node_ids": dependent_ids,
            "dofs": dof_code,
            "checkpoint": checkpoint,
            "call_result": result,
            "refresh_result": refresh_result,
            "refresh_error": refresh_error,
        }

    def create_rbe3(
        self,
        independent_node_ids: list[int],
        dependent_node_id: int | None = None,
        independent_dofs: int = 123456,
        independent_weights: list[float] | None = None,
        dependent_dofs: list[bool] | None = None,
        dependent_weight: float = 1.0,
        model_name: str | None = None,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Create an RBE3 spider, optionally auto-creating its centroid dependent node."""
        dof_code = self._dof_code("independent_dofs", independent_dofs)
        hm, ent = self._imports()
        model = self._model(model_name)
        independent_collection, independent_ids = self._checked_collection(
            model, "node", independent_node_ids
        )
        if independent_weights is None:
            weights = [1.0] * len(independent_ids)
        else:
            if not isinstance(independent_weights, list) or len(independent_weights) != len(independent_ids):
                raise ValueError("independent_weights must contain one value per independent node")
            weights = [
                self._positive_float(f"independent_weights[{index}]", value)
                for index, value in enumerate(independent_weights)
            ]
        dof_flags = [True] * 6 if dependent_dofs is None else dependent_dofs
        if not isinstance(dof_flags, list) or len(dof_flags) != 6:
            raise ValueError("dependent_dofs must contain exactly six boolean values")
        if any(not isinstance(value, bool) for value in dof_flags) or not any(dof_flags):
            raise ValueError("dependent_dofs must contain booleans with at least one enabled DOF")
        dep_weight = self._positive_float("dependent_weight", dependent_weight)

        dependent_entity = None
        clean_dependent_id = None
        if dependent_node_id is not None:
            clean_dependent_id = int(dependent_node_id)
            if clean_dependent_id <= 0:
                raise ValueError("dependent_node_id must be positive")
            self._checked_collection(model, "node", [clean_dependent_id])
            if clean_dependent_id in independent_ids:
                raise ValueError("dependent_node_id must not also be an independent node")
            dependent_entity = ent.Node(model, clean_dependent_id)

        checkpoint = self.create_checkpoint("before_rbe3", model_name)
        before_elements = self._entity_ids(hm, model, ent.Element)
        before_nodes = self._entity_ids(hm, model, ent.Node)
        try:
            result = _unwrap_hm_call(
                model.rbe3(
                    collection=independent_collection,
                    independent_dofs=[dof_code] * len(independent_ids),
                    independent_weights=weights,
                    dependent_node=dependent_entity,
                    dof=list(dof_flags),
                    weight=dep_weight,
                )
            )
            created_element_ids = sorted(
                self._entity_ids(hm, model, ent.Element) - before_elements
            )
            created_node_ids = sorted(self._entity_ids(hm, model, ent.Node) - before_nodes)
            if not created_element_ids:
                raise RuntimeError("HyperMesh did not expose a newly created RBE3 element")
            if clean_dependent_id is None and not created_node_ids:
                raise RuntimeError("HyperMesh did not expose the auto-created RBE3 dependent node")
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)

        refresh_result = None
        refresh_error = None
        if refresh:
            try:
                refresh_result = self.refresh_view(fit=False)
            except Exception as exc:  # pragma: no cover - graphics-only live failure
                refresh_error = str(exc)
        return {
            "created": True,
            "connection_type": "rbe3",
            "model_name": model_name,
            "element_ids": created_element_ids,
            "independent_node_ids": independent_ids,
            "dependent_node_id": clean_dependent_id,
            "auto_created_dependent_node_ids": created_node_ids,
            "independent_dofs": dof_code,
            "independent_weights": weights,
            "dependent_dofs": list(dof_flags),
            "dependent_weight": dep_weight,
            "checkpoint": checkpoint,
            "call_result": result,
            "refresh_result": refresh_result,
            "refresh_error": refresh_error,
        }

    def create_weld(
        self,
        independent_node_id: int,
        dependent_node_id: int,
        length: float = 0.0,
        create_systems: bool = False,
        move_node: bool = False,
        model_name: str | None = None,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Create a solver-profile weld element between two existing nodes."""
        independent_id = int(independent_node_id)
        dependent_id = int(dependent_node_id)
        if independent_id <= 0 or dependent_id <= 0:
            raise ValueError("node IDs must be positive")
        if independent_id == dependent_id:
            raise ValueError("independent and dependent node IDs must be different")
        weld_length = self._nonnegative_float("length", length)
        hm, ent = self._imports()
        model = self._model(model_name)
        self._checked_collection(model, "node", [independent_id, dependent_id])

        checkpoint = self.create_checkpoint("before_weld", model_name)
        before = self._entity_ids(hm, model, ent.Element)
        try:
            result = _unwrap_hm_call(
                model.weld(
                    independent=ent.Node(model, independent_id),
                    dependent=ent.Node(model, dependent_id),
                    length=weld_length,
                    systems=1 if create_systems else 0,
                    movenode=1 if move_node else 0,
                )
            )
            created_ids = sorted(self._entity_ids(hm, model, ent.Element) - before)
            if not created_ids:
                raise RuntimeError("HyperMesh did not expose a newly created weld element")
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)

        refresh_result = None
        refresh_error = None
        if refresh:
            try:
                refresh_result = self.refresh_view(fit=False)
            except Exception as exc:  # pragma: no cover - graphics-only live failure
                refresh_error = str(exc)
        return {
            "created": True,
            "connection_type": "weld",
            "model_name": model_name,
            "element_ids": created_ids,
            "independent_node_id": independent_id,
            "dependent_node_id": dependent_id,
            "length": weld_length,
            "create_systems": bool(create_systems),
            "move_node": bool(move_node),
            "checkpoint": checkpoint,
            "call_result": result,
            "refresh_result": refresh_result,
            "refresh_error": refresh_error,
        }

    def create_spot_weld(
        self,
        independent_node_id: int,
        dependent_node_id: int,
        config: int,
        property_name: str,
        length: float = 0.0,
        create_system: bool = False,
        move_node: bool = False,
        remesh: bool = False,
        model_name: str | None = None,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Create a solver-profile spot weld with an explicit FE config and property."""
        independent_id = int(independent_node_id)
        dependent_id = int(dependent_node_id)
        config = int(config)
        prop_name = str(property_name).strip()
        if independent_id <= 0 or dependent_id <= 0 or independent_id == dependent_id:
            raise ValueError("Spot-weld node IDs must be different positive integers")
        if config <= 0:
            raise ValueError("config must be positive")
        if not prop_name or len(prop_name) > 128:
            raise ValueError("property_name must contain between 1 and 128 characters")
        weld_length = self._nonnegative_float("length", length)
        hm, ent = self._imports()
        model = self._model(model_name)
        self._checked_collection(model, "node", [independent_id, dependent_id])
        properties = [
            item for item in hm.Collection(model, ent.Property)
            if str(getattr(item, "name", "")) == prop_name
        ]
        if not properties:
            raise ValueError(f"Property does not exist: {prop_name}")
        checkpoint = self.create_checkpoint("before_spot_weld", model_name)
        before = self._entity_ids(hm, model, ent.Element)
        try:
            result = _unwrap_hm_call(
                model.createspotweld(
                    independent=ent.Node(model, independent_id),
                    dependent=ent.Node(model, dependent_id),
                    length_given=1 if weld_length > 0.0 else 0,
                    length=weld_length,
                    systems=1 if create_system else 0,
                    movenode=1 if move_node else 0,
                    remesh=1 if remesh else 0,
                    configval=config,
                    property=prop_name,
                )
            )
            created_ids = sorted(self._entity_ids(hm, model, ent.Element) - before)
            if not created_ids:
                raise RuntimeError("HyperMesh did not expose a newly created spot-weld element")
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        view = self.refresh_view(fit=False) if refresh else None
        return {
            "created": True,
            "connection_type": "spot_weld",
            "element_ids": created_ids,
            "independent_node_id": independent_id,
            "dependent_node_id": dependent_id,
            "config": config,
            "property_name": prop_name,
            "length": weld_length,
            "checkpoint": checkpoint,
            "call_result": result,
            "view": view,
        }

    def create_connector(
        self,
        location_entity_type: str,
        location_ids: list[int],
        style: str,
        link_entity_type: str,
        link_ids: list[int],
        num_links: int = 2,
        tolerance: float = 0.0,
        model_name: str | None = None,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Create connector intent without solver-specific FE realization.

        HyperMesh connectors preserve assembly intent separately from the realized
        weld/bolt/bar elements.  Realization is intentionally a separate future
        operation because its FE type, property, and control choices are solver
        profile dependent.
        """
        location_type = str(location_entity_type).strip().lower()
        link_type = str(link_entity_type).strip().lower()
        connector_style = str(style).strip().lower()
        if location_type not in {"node", "line"}:
            raise ValueError("location_entity_type must be 'node' or 'line'")
        if link_type not in {"component", "surface"}:
            raise ValueError("link_entity_type must be 'component' or 'surface'")
        if connector_style not in {"spot", "seam", "area", "bolt"}:
            raise ValueError("style must be spot, seam, area, or bolt")
        try:
            link_count = int(num_links)
        except (TypeError, ValueError) as exc:
            raise ValueError("num_links must be an integer") from exc
        if not 1 <= link_count <= 100:
            raise ValueError("num_links must be in the range [1, 100]")
        connector_tolerance = self._nonnegative_float("tolerance", tolerance)

        hm, ent = self._imports()
        model = self._model(model_name)
        location_collection, clean_location_ids = self._checked_collection(
            model, location_type, location_ids
        )
        link_collection, clean_link_ids = self._checked_collection(
            model, link_type, link_ids
        )
        checkpoint = self.create_checkpoint("before_connector", model_name)
        before = self._entity_ids(hm, model, ent.Connector)
        try:
            result = _unwrap_hm_call(
                model.CE_ConnectorCreateByList(
                    entitylist=list(location_collection),
                    ce_style=connector_style,
                    num_links=link_count,
                    link_collection=link_collection,
                    tol_flag=1 if connector_tolerance > 0.0 else 0,
                    tol=connector_tolerance,
                )
            )
            created_ids = sorted(self._entity_ids(hm, model, ent.Connector) - before)
            if not created_ids:
                raise RuntimeError("HyperMesh did not expose a newly created connector ID")
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        view = self.refresh_view(fit=False) if refresh else None
        return {
            "created": True,
            "connection_type": "connector_intent",
            "realized": False,
            "connector_ids": created_ids,
            "style": connector_style,
            "location_entity_type": location_type,
            "location_ids": clean_location_ids,
            "link_entity_type": link_type,
            "link_ids": clean_link_ids,
            "num_links": link_count,
            "tolerance": connector_tolerance,
            "checkpoint": checkpoint,
            "call_result": result,
            "view": view,
        }

    def create_solid_block(
        self,
        origin: list[float],
        dimensions: list[float],
        model_name: str | None = None,
    ) -> dict[str, Any]:
        base = self._finite_vector("origin", origin)
        size = self._finite_vector("dimensions", dimensions, positive=True)
        hm, ent = self._imports()
        model = self._model(model_name)
        checkpoint = self.create_checkpoint("before_solid_block", model_name)
        before = self._entity_ids(hm, model, ent.Solid)
        try:
            result = _unwrap_hm_call(
                model.solidblock(
                    base_x=base[0], base_y=base[1], base_z=base[2],
                    ivec_x=size[0], ivec_y=0.0, ivec_z=0.0,
                    jvec_x=0.0, jvec_y=size[1], jvec_z=0.0,
                    kvec_x=0.0, kvec_y=0.0, kvec_z=size[2],
                )
            )
            created = sorted(self._entity_ids(hm, model, ent.Solid) - before)
            if not created:
                raise RuntimeError("HyperMesh did not expose a newly created solid ID")
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        return {
            "created": True,
            "primitive": "block",
            "solid_ids": created,
            "origin": list(base),
            "dimensions": list(size),
            "checkpoint": checkpoint,
            "call_result": result,
        }

    def create_solid_cylinder(
        self,
        base_center: list[float],
        axis: list[float],
        radius: float,
        height: float,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        base = self._finite_vector("base_center", base_center)
        normal = self._unit_vector(axis)
        radius = self._positive_float("radius", radius)
        height = self._positive_float("height", height)
        reference = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
        major = self._unit_vector(list(self._cross(reference, normal)), "major_axis")
        hm, ent = self._imports()
        model = self._model(model_name)
        checkpoint = self.create_checkpoint("before_solid_cylinder", model_name)
        before = self._entity_ids(hm, model, ent.Solid)
        try:
            result = _unwrap_hm_call(
                model.solidcone(
                    base_x=base[0], base_y=base[1], base_z=base[2],
                    mvec_x=major[0], mvec_y=major[1], mvec_z=major[2],
                    nvec_x=normal[0], nvec_y=normal[1], nvec_z=normal[2],
                    base_radius=radius, top_radius=radius, aspect_ratio=1.0,
                    start_angle=0.0, end_angle=360.0, height=height,
                )
            )
            created = sorted(self._entity_ids(hm, model, ent.Solid) - before)
            if not created:
                raise RuntimeError("HyperMesh did not expose a newly created solid ID")
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        return {
            "created": True,
            "primitive": "cylinder",
            "solid_ids": created,
            "base_center": list(base),
            "axis": list(normal),
            "radius": radius,
            "height": height,
            "checkpoint": checkpoint,
            "call_result": result,
        }

    def import_cad(
        self,
        input_file: str,
        translator: str | None = None,
        options: list[str] | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        path = self._safe_input(input_file)
        default_translator = CAD_TRANSLATORS.get(path.suffix.lower())
        translator = str(translator or default_translator or "").strip().lower()
        if translator not in ALLOWED_CAD_TRANSLATORS:
            raise ValueError(
                "Unsupported CAD translator. Allowed values: "
                + ", ".join(sorted(ALLOWED_CAD_TRANSLATORS))
            )
        options = options or []
        if not isinstance(options, list) or len(options) > MAX_CAD_OPTIONS:
            raise ValueError(f"options must be a list with at most {MAX_CAD_OPTIONS} items")
        clean_options = []
        for item in options:
            value = str(item)
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*=[^\r\n;]{0,200}", value):
                raise ValueError(f"Invalid CAD import option: {value!r}")
            clean_options.append(value)

        hm, ent = self._imports()
        model = self._model(model_name)
        checkpoint = self.create_checkpoint("before_cad_import", model_name)
        tracked = (ent.Solid, ent.Surface, ent.Component)
        before = {entity_class: self._entity_ids(hm, model, entity_class) for entity_class in tracked}
        # options is a required hwStringList. Passing a Python list makes the
        # MDI argument converter rebuild it, and an empty list surfaces as
        # "argument not supported"; build the HyperMesh container directly.
        from hwcont import hwStringList

        import_options = hwStringList()
        for item in clean_options:
            import_options.push_back(item)
        try:
            result = _unwrap_hm_call(
                model.geomimport(
                    translator_type=translator,
                    input_file_name=path.as_posix(),
                    options=import_options,
                )
            )
            created = {
                entity_class.__name__.lower() + "_ids": sorted(
                    self._entity_ids(hm, model, entity_class) - before[entity_class]
                )
                for entity_class in tracked
            }
            if not created["solid_ids"] and not created["surface_ids"]:
                raise RuntimeError("CAD import completed without exposing new solids or surfaces")
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        return {
            "imported": True,
            "input_file": str(path),
            "translator": translator,
            "options": clean_options,
            **created,
            "checkpoint": checkpoint,
            "call_result": result,
        }

    def automesh_surfaces(
        self,
        surface_ids: list[int],
        element_size: float,
        element_type: str = "mixed",
        density_mode: str = "proximity_curvature",
        min_size: float | None = None,
        max_size: float | None = None,
        chordal_deviation: float | None = None,
        max_angle: float = 30.0,
        growth_rate: float = 1.2,
        keep_existing_mesh: bool = True,
        component_id: int | None = None,
        model_name: str | None = None,
        mesh_type: str | None = None,
        mesh_algorithm: str = "free",
    ) -> dict[str, Any]:
        element_size = self._positive_float("element_size", element_size)
        min_size = self._positive_float("min_size", min_size or element_size * 0.2)
        max_size = self._positive_float("max_size", max_size or element_size)
        if min_size > max_size:
            raise ValueError("min_size must not exceed max_size")
        chordal_deviation = float(
            element_size * 0.05 if chordal_deviation is None else chordal_deviation
        )
        if not math.isfinite(chordal_deviation) or chordal_deviation < 0.0:
            raise ValueError("chordal_deviation must be finite and non-negative")
        max_angle = float(max_angle)
        growth_rate = float(growth_rate)
        if not 1.0 <= max_angle <= 180.0:
            raise ValueError("max_angle must be between 1 and 180 degrees")
        if not 1.0 <= growth_rate <= 5.0:
            raise ValueError("growth_rate must be between 1 and 5")
        if element_type not in SURFACE_ELEMENT_TYPES:
            raise ValueError("Unsupported element_type")
        if mesh_type is not None:
            if mesh_type in SURFACE_DENSITY_MODES:
                if (
                    density_mode != "proximity_curvature"
                    and density_mode != mesh_type
                ):
                    raise ValueError(
                        "density_mode and mesh_type disagree; use density_mode"
                    )
                density_mode = mesh_type
            elif mesh_type in ("free", "mapped"):
                if mesh_algorithm != "free" and mesh_algorithm != mesh_type:
                    raise ValueError(
                        "mesh_algorithm and mesh_type disagree; use mesh_algorithm"
                    )
                mesh_algorithm = mesh_type
            else:
                raise ValueError(
                    "Unsupported mesh_type; use density_mode or mesh_algorithm"
                )
        if mesh_algorithm not in ("free", "mapped"):
            raise ValueError("mesh_algorithm must be 'free' or 'mapped'")
        if mesh_algorithm != "free":
            raise ValueError(
                "Mapped surface meshing is not implemented; use mesh_algorithm='free'"
            )
        if density_mode not in SURFACE_DENSITY_MODES:
            raise ValueError("Unsupported density_mode")

        hm, ent = self._imports()
        model = self._model(model_name)
        component = None
        if component_id is not None:
            component_id = int(component_id)
            self._checked_collection(model, "component", [component_id])
            component = ent.Component(model, component_id)
            model.currentcollector(
                entity_type=ent.Component, name=str(component.name)
            )
        collection, clean_ids = self._checked_collection(model, "surface", surface_ids)
        checkpoint = self.create_checkpoint("before_surface_automesh", model_name)
        before = self._entity_ids(hm, model, ent.Element)
        # The growth arrays start with three counts (points, edges, surfaces)
        # followed by the corresponding IDs. The MDI argument converter rejects
        # an empty sequence, so each meshed surface carries an explicit size
        # control equal to the requested element size.
        id_array = [0, 0, len(clean_ids)] + list(clean_ids)
        size_array = [element_size] * len(clean_ids)
        try:
            result = _unwrap_hm_call(
                model.defaultmeshsurf_growth(
                    collection=collection,
                    elem_size=element_size,
                    elem_type=SURFACE_ELEMENT_TYPES[element_type],
                    elem_type_2=SURFACE_ELEMENT_TYPES[element_type],
                    previous_settings=2,
                    comp_mode=0 if component is not None else 1,
                    size_control=1,
                    skew_control=1,
                    mesh_type=SURFACE_DENSITY_MODES[density_mode],
                    keep_mesh=1 if keep_existing_mesh else 0,
                    min_size=min_size,
                    max_size=max_size,
                    chordal_dev=chordal_deviation,
                    max_angle=max_angle,
                    growth_rate=growth_rate,
                    id_array=id_array,
                    size_array=size_array,
                )
            )
            created = sorted(self._entity_ids(hm, model, ent.Element) - before)
            if not created:
                raise RuntimeError("Surface automeshing did not expose newly created elements")
            if component is not None:
                outside = [
                    element_id
                    for element_id in created
                    if int(ent.Element(model, element_id).component.id) != component_id
                ]
                if outside:
                    raise RuntimeError(
                        "Surface automeshing placed elements outside the requested "
                        f"component: {outside[:20]}"
                    )
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        sample_ids, truncated = self._id_sample(created)
        return {
            "meshed": True,
            "surface_ids": clean_ids,
            "element_ids": sample_ids,
            "element_ids_truncated": truncated,
            "element_count": len(created),
            "element_size": element_size,
            "element_type": element_type,
            "density_mode": density_mode,
            "mesh_algorithm": mesh_algorithm,
            "mesh_type": density_mode,  # deprecated alias for density_mode
            "component": (
                {"id": component_id, "name": str(component.name)}
                if component is not None
                else None
            ),
            "checkpoint": checkpoint,
            "call_result": result,
        }

    def solid_map_mesh(
        self,
        solid_ids: list[int],
        element_size: float,
        element_type: str = "mixed",
        organize_to_current_component: bool = False,
        extra_smoothing: bool = True,
        remesh_shell_mesh: bool = False,
        continue_on_negative_jacobian: bool = False,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        element_size = self._positive_float("element_size", element_size)
        if element_type not in {"tria", "quad", "mixed"}:
            raise ValueError("element_type must be tria, quad, or mixed")
        hm, ent = self._imports()
        model = self._model(model_name)
        collection, clean_ids = self._checked_collection(model, "solid", solid_ids)
        checkpoint = self.create_checkpoint("before_solid_map", model_name)
        before = self._entity_ids(hm, model, ent.Element)
        options = {"tria": 0, "quad": 512, "mixed": 1024}[element_type]
        if organize_to_current_component:
            options |= 2
        if extra_smoothing:
            options |= 256
        if remesh_shell_mesh:
            options |= 8192
        if continue_on_negative_jacobian:
            options |= 262144
        try:
            # The installed 2025 binding exposes solidmap_solids_begin (and
            # begin2 for surface-driven mapping); begin3 is not available.
            model.solidmap_solids_begin(
                collection=collection, options=options, elem_size=element_size
            )
            result = _unwrap_hm_call(model.solidmap_solids_end())
            created = sorted(self._entity_ids(hm, model, ent.Element) - before)
            if not created:
                raise RuntimeError(
                    "Solid Map did not create elements; confirm the selected solids are mappable"
                )
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        sample_ids, truncated = self._id_sample(created)
        return {
            "meshed": True,
            "method": "native_solid_map",
            "solid_ids": clean_ids,
            "element_ids": sample_ids,
            "element_ids_truncated": truncated,
            "element_count": len(created),
            "element_size": element_size,
            "element_type": element_type,
            "options": options,
            "checkpoint": checkpoint,
            "call_result": result,
        }

    def tetra_mesh_solids(
        self,
        solid_ids: list[int],
        element_size: float,
        min_size: float | None = None,
        max_size: float | None = None,
        growth_rate: float = 1.3,
        element_order: int = 1,
        use_existing_surface_mesh: bool = True,
        model_name: str | None = None,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Create a bounded structural tetra mesh on explicit solid IDs."""
        size = self._positive_float("element_size", element_size)
        minimum = self._positive_float("min_size", min_size or size * 0.2)
        maximum = self._positive_float("max_size", max_size or size)
        growth = float(growth_rate)
        order = int(element_order)
        if minimum > maximum:
            raise ValueError("min_size must not exceed max_size")
        if not 1.0 <= growth <= 3.0:
            raise ValueError("growth_rate must be between 1 and 3")
        if order not in {1, 2}:
            raise ValueError("element_order must be 1 or 2")
        hm, ent = self._imports()
        model = self._model(model_name)
        solid_collection, clean_ids = self._checked_collection(model, "solid", solid_ids)
        empty_elements = hm.Collection(model, ent.Element, populate=False)
        checkpoint = self.create_checkpoint("before_tetra_mesh", model_name)
        before = self._entity_ids(hm, model, ent.Element)
        strings = [
            f"pars: upd_shell fix_comp_bdr post_cln elem_order={order} delaunay el2comp=3 fill_void=1",
            f"tet: 35 {growth:.12g} -1 {maximum:.12g} {minimum:.12g} 0 0 1",
            f"2d: {order} 0 4 {size:.12g} {minimum:.12g} 30 {1 if use_existing_surface_mesh else 0}",
        ]
        try:
            result = _unwrap_hm_call(
                model.tetmesh(
                    collection1=solid_collection,
                    mode1=1,
                    collection2=empty_elements,
                    mode2=-1,
                    string_array=strings,
                )
            )
            created_ids = sorted(self._entity_ids(hm, model, ent.Element) - before)
            if not created_ids:
                raise RuntimeError("Tetra meshing completed without exposing new elements")
            quality = self.get_mesh_quality(created_ids, model_name=model_name)
            if quality["non_positive_volume_element_ids"]:
                raise RuntimeError(
                    "Tetra mesh contains non-positive-volume elements: "
                    + str(quality["non_positive_volume_element_ids"][:20])
                )
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        view = self.refresh_view(fit=True) if refresh else None
        sample_ids, truncated = self._id_sample(created_ids)
        return {
            "meshed": True,
            "method": "native_tetmesh",
            "solid_ids": clean_ids,
            "element_ids": sample_ids,
            "element_ids_truncated": truncated,
            "element_count": len(created_ids),
            "parameters": {
                "element_size": size,
                "min_size": minimum,
                "max_size": maximum,
                "growth_rate": growth,
                "element_order": order,
                "use_existing_surface_mesh": bool(use_existing_surface_mesh),
            },
            "quality": quality,
            "checkpoint": checkpoint,
            "call_result": result,
            "view": view,
        }

    def repair_mesh_quality(
        self,
        element_ids: list[int],
        anchor_node_ids: list[int] | None = None,
        iterations: int = 5,
        method: str = "AutoDecideWithQI_Params_locked",
        anchor_free_edges: bool = True,
        model_name: str | None = None,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Smooth selected elements with an automatic checkpoint and before/after metrics."""
        allowed_methods = {
            "Angle", "AutoDecideWithoutQI", "AutoDecideWithQI",
            "AutoDecideWithQI_Params_locked", "QI", "Shape", "Size",
        }
        if method not in allowed_methods:
            raise ValueError("Unsupported smoothing method")
        iteration_count = int(iterations)
        if not 1 <= iteration_count <= 100:
            raise ValueError("iterations must be between 1 and 100")
        hm, ent = self._imports()
        model = self._model(model_name)
        element_collection, clean_ids = self._checked_collection(model, "element", element_ids)
        if anchor_node_ids:
            node_collection, clean_anchors = self._checked_collection(model, "node", anchor_node_ids)
        else:
            node_collection = hm.Collection(model, ent.Node, populate=False)
            clean_anchors = []
        before_quality = self.get_mesh_quality(clean_ids, model_name=model_name)
        checkpoint = self.create_checkpoint("before_mesh_quality_repair", model_name)
        try:
            result = _unwrap_hm_call(
                model.element_smooth_nodes(
                    elementsCollection=element_collection,
                    nodeCollection=node_collection,
                    anchorFreeEdgeNodes=1 if anchor_free_edges else 0,
                    iterations=iteration_count,
                    smoothmethod=method,
                    timelimit=0,
                )
            )
            shutdown = _unwrap_hm_call(model.elementqualityshutdown(dontsaveflag=0))
            after_quality = self.get_mesh_quality(clean_ids, model_name=model_name)
            if after_quality["non_positive_volume_element_ids"]:
                raise RuntimeError(
                    "Quality repair produced non-positive-volume elements: "
                    + str(after_quality["non_positive_volume_element_ids"][:20])
                )
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        view = self.refresh_view(fit=False) if refresh else None
        sample_ids, truncated = self._id_sample(clean_ids)
        anchor_sample, anchors_truncated = self._id_sample(clean_anchors)
        return {
            "repaired": True,
            "element_ids": sample_ids,
            "element_ids_truncated": truncated,
            "anchor_node_ids": anchor_sample,
            "anchor_node_ids_truncated": anchors_truncated,
            "method": method,
            "iterations": iteration_count,
            "quality_before": before_quality,
            "quality_after": after_quality,
            "checkpoint": checkpoint,
            "call_result": result,
            "shutdown_result": shutdown,
            "view": view,
        }

    def create_cylindrical_ogrid(
        self,
        base_center: list[float],
        axis: list[float],
        length: float,
        radius: float,
        axial_divisions: int = 4,
        circumferential_divisions: int = 12,
        radial_layers: int = 2,
        core_ratio: float = 0.4,
        model_name: str | None = None,
        refresh: bool = True,
    ) -> dict[str, Any]:
        base = self._finite_vector("base_center", base_center)
        normal = self._unit_vector(axis)
        length = self._positive_float("length", length)
        radius = self._positive_float("radius", radius)
        axial_divisions = int(axial_divisions)
        circumferential_divisions = int(circumferential_divisions)
        radial_layers = int(radial_layers)
        core_ratio = float(core_ratio)
        if not 1 <= axial_divisions <= 50:
            raise ValueError("axial_divisions must be between 1 and 50")
        if not 8 <= circumferential_divisions <= 128 or circumferential_divisions % 4:
            raise ValueError(
                "circumferential_divisions must be a multiple of 4 between 8 and 128"
            )
        if not 1 <= radial_layers <= 20:
            raise ValueError("radial_layers must be between 1 and 20")
        if not 0.1 <= core_ratio <= 0.8:
            raise ValueError("core_ratio must be between 0.1 and 0.8")
        plane_count = axial_divisions + 1
        ring_count = radial_layers + 1
        node_count = plane_count * (1 + ring_count * circumferential_divisions)
        penta_count = axial_divisions * circumferential_divisions
        hexa_count = axial_divisions * radial_layers * circumferential_divisions
        if node_count > MAX_NODES_PER_CALL:
            raise ValueError(f"Requested O-grid needs {node_count} nodes; limit is {MAX_NODES_PER_CALL}")
        if penta_count + hexa_count > MAX_ELEMENTS_PER_CALL:
            raise ValueError(
                f"Requested O-grid needs {penta_count + hexa_count} elements; "
                f"limit is {MAX_ELEMENTS_PER_CALL}"
            )

        reference = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
        radial_u = self._unit_vector(list(self._cross(reference, normal)), "radial_u")
        radial_v = self._cross(normal, radial_u)
        coordinates: list[list[float]] = []
        keys: list[tuple[int, int, int]] = []
        for plane in range(plane_count):
            axial_offset = length * plane / axial_divisions
            center = tuple(base[i] + normal[i] * axial_offset for i in range(3))
            coordinates.append(list(center))
            keys.append((plane, -1, 0))
            for ring in range(ring_count):
                ring_radius = radius * (
                    core_ratio + (1.0 - core_ratio) * ring / radial_layers
                )
                for sector in range(circumferential_divisions):
                    angle = 2.0 * math.pi * sector / circumferential_divisions
                    coordinates.append(
                        [
                            center[i]
                            + ring_radius
                            * (math.cos(angle) * radial_u[i] + math.sin(angle) * radial_v[i])
                            for i in range(3)
                        ]
                    )
                    keys.append((plane, ring, sector))

        checkpoint = self.create_checkpoint("before_cylindrical_ogrid", model_name)
        try:
            node_result = self.create_nodes(coordinates, model_name=model_name)
            node_map = dict(zip(keys, node_result["ids"]))

            def center_id(plane: int) -> int:
                return node_map[(plane, -1, 0)]

            def ring_id(plane: int, ring: int, sector: int) -> int:
                return node_map[(plane, ring, sector % circumferential_divisions)]

            penta_connectivity = []
            hexa_connectivity = []
            for plane in range(axial_divisions):
                for sector in range(circumferential_divisions):
                    penta_connectivity.append(
                        [
                            center_id(plane),
                            ring_id(plane, 0, sector),
                            ring_id(plane, 0, sector + 1),
                            center_id(plane + 1),
                            ring_id(plane + 1, 0, sector),
                            ring_id(plane + 1, 0, sector + 1),
                        ]
                    )
                    for ring in range(radial_layers):
                        hexa_connectivity.append(
                            [
                                ring_id(plane, ring, sector),
                                ring_id(plane, ring + 1, sector),
                                ring_id(plane, ring + 1, sector + 1),
                                ring_id(plane, ring, sector + 1),
                                ring_id(plane + 1, ring, sector),
                                ring_id(plane + 1, ring + 1, sector),
                                ring_id(plane + 1, ring + 1, sector + 1),
                                ring_id(plane + 1, ring, sector + 1),
                            ]
                        )
            penta_result = self.create_elements(
                penta_connectivity, config=206, solver_type=1,
                auto_order=True, model_name=model_name
            )
            hexa_result = self.create_elements(
                hexa_connectivity, config=208, solver_type=1,
                auto_order=True, model_name=model_name
            )
            element_ids = penta_result["ids"] + hexa_result["ids"]
            quality = self.get_mesh_quality(element_ids, model_name=model_name)
            if quality["non_positive_volume_element_ids"]:
                raise RuntimeError(
                    "O-grid contains non-positive-volume 3D elements: "
                    + str(quality["non_positive_volume_element_ids"][:20])
                )
            view = self.refresh_view(fit=True) if refresh else None
        except Exception as exc:
            self._rollback_after_failure(checkpoint, model_name, exc)
        return {
            "created": True,
            "method": "radial_ogrid_with_penta_core",
            "node_ids": node_result["ids"],
            "element_ids": element_ids,
            "node_count": node_result["count"],
            "penta6_count": penta_result["count"],
            "hex8_count": hexa_result["count"],
            "parameters": {
                "base_center": list(base), "axis": list(normal),
                "length": length, "radius": radius,
                "axial_divisions": axial_divisions,
                "circumferential_divisions": circumferential_divisions,
                "radial_layers": radial_layers, "core_ratio": core_ratio,
            },
            "quality": quality,
            "checkpoint": checkpoint,
            "view": view,
        }

    def get_mesh_quality(
        self,
        element_ids: list[int] | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        hm, ent = self._imports()
        model = self._model(model_name)
        all_elements = list(hm.Collection(model, ent.Element))
        if element_ids is None:
            if len(all_elements) > MAX_ELEMENTS_PER_CALL:
                raise ValueError(
                    f"Model has more than {MAX_ELEMENTS_PER_CALL} elements; pass a bounded element_ids list"
                )
            elements = all_elements
            clean_ids = [int(element.id) for element in elements]
        else:
            _, clean_ids = self._checked_collection(model, "element", element_ids)
            by_id = {int(element.id): element for element in all_elements}
            elements = [by_id[value] for value in clean_ids]

        attribute_values: dict[str, list[tuple[int, float]]] = {
            "volume": [], "jacobian": [], "aspect": [], "skew": []
        }
        unavailable: dict[str, list[int]] = {name: [] for name in attribute_values}
        non_positive_volume = []
        for element in elements:
            entity_id = int(element.id)
            try:
                config = int(getattr(element, "config"))
            except Exception:
                config = -1
            for name in attribute_values:
                try:
                    value = float(getattr(element, name))
                    if math.isfinite(value):
                        attribute_values[name].append((entity_id, value))
                    else:
                        unavailable[name].append(entity_id)
                except Exception:
                    unavailable[name].append(entity_id)
            if 200 <= config < 300:
                volume = next(
                    (value for item_id, value in attribute_values["volume"] if item_id == entity_id),
                    None,
                )
                if volume is not None and volume <= 0.0:
                    non_positive_volume.append(entity_id)
        statistics = {}
        for name, values in attribute_values.items():
            numeric = [value for _, value in values]
            statistics[name] = {
                "available_count": len(numeric),
                "min": min(numeric) if numeric else None,
                "max": max(numeric) if numeric else None,
                "mean": sum(numeric) / len(numeric) if numeric else None,
            }
        sample_ids, truncated = self._id_sample(clean_ids)
        return {
            "model_name": model_name,
            "element_ids": sample_ids,
            "element_ids_truncated": truncated,
            "element_count": len(clean_ids),
            "statistics": statistics,
            "unavailable_attribute_counts": {
                name: len(ids) for name, ids in unavailable.items()
            },
            "non_positive_volume_element_ids": non_positive_volume,
        }

    def _safe_input(self, raw_path: str) -> Path:
        path = self._safe_output(raw_path)
        if not path.is_file():
            raise ValueError(f"Input model does not exist: {path}")
        return path

    def load_model(
        self,
        input_file: str,
        replace_current: bool = False,
        load_cad_geometry_as_graphics: bool = False,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        if not replace_current:
            raise ValueError(
                "Loading replaces the current model state; set replace_current=true after approval"
            )
        path = self._safe_input(input_file)
        if path.suffix.lower() != ".hm":
            raise ValueError("The controlled live loader currently accepts only .hm files")
        model = self._model(model_name)
        model.hm_answernext(answer="yes")
        result = _unwrap_hm_call(
            model.readfile(
                filename=path.as_posix(),
                load_cad_geometry_as_graphics=1 if load_cad_geometry_as_graphics else 0,
            )
        )
        session = self.get_session_info()
        return {
            "loaded": True,
            "input_file": str(path),
            "model_name": model_name,
            "read_result": result,
            "session": session,
        }

    def refresh_view(self, fit: bool = True) -> dict[str, Any]:
        import hw

        commands = []
        if fit:
            hw.evalTcl("hm_viewfit")
            commands.append("hm_viewfit")
        hw.evalTcl("hm_redraw")
        commands.append("hm_redraw")
        return {"refreshed": True, "fit": bool(fit), "commands": commands}

    def get_model_metrics(self, model_name: str | None = None) -> dict[str, Any]:
        model = self._model(model_name)
        calls = {
            "solver": "hm_getsolver",
            "total_mass": "hm_gettotalmass",
            "center_of_gravity": "hm_gettotalcog",
            "current_view": "hm_getcurrentview",
            "existing_entity_types": "hm_getexistingentitytypes",
        }
        result = {"model_name": model_name, "values": {}, "errors": {}}
        for label, method_name in calls.items():
            try:
                result["values"][label] = _unwrap_hm_call(
                    getattr(model, method_name)()
                )
            except Exception as exc:
                result["errors"][label] = str(exc)
        return result

    def _safe_output(self, raw_path: str) -> Path:
        if not self.allowed_roots:
            raise RuntimeError("No save roots are configured for the live bridge")
        path = Path(raw_path).expanduser().resolve()
        for root in self.allowed_roots:
            try:
                path.relative_to(root)
                return path
            except ValueError:
                continue
        raise ValueError(f"Output path is outside configured save roots: {path}")

    def _safe_input(self, raw_path: str) -> Path:
        path = self._safe_output(raw_path)
        if not path.is_file():
            raise ValueError(f"Input file does not exist: {path}")
        return path

    def postprocess_hyperview_result(
        self,
        model_file: str,
        result_file: str,
        output_file: str,
        data_type: str = "Displacement",
        data_component: str = "Mag",
        entity_type: str = "node",
        page_title: str = "MCP HyperView Results",
        query_limit: int = 20,
        simulation: str | int = "last",
        average_mode: str = "none",
    ) -> dict[str, Any]:
        """Load one result on a new page, contour it, query values, and capture evidence."""
        import hw
        import hw.hv as hv

        model_path = self._safe_input(model_file)
        result_path = self._safe_input(result_file)
        image_path = self._safe_output(output_file)
        if result_path.suffix.lower() not in {".h3d", ".op2", ".d3plot", ".h5", ".hdf5"}:
            raise ValueError("result_file must be an allowlisted HyperView result format")
        if model_path.suffix.lower() not in {".fem", ".bdf", ".dat", ".h3d", ".rad", ".key"}:
            raise ValueError("model_file must be an allowlisted solver model format")
        if image_path.suffix.lower() != ".png":
            raise ValueError("output_file must use the .png extension")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _()/+&.-]{0,79}", data_type):
            raise ValueError("Invalid HyperView data_type")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _()/+.-]{0,79}", data_component):
            raise ValueError("Invalid HyperView data_component")
        entity_key = entity_type.strip().lower()
        if entity_key not in {"node", "element"}:
            raise ValueError("entity_type must be node or element")
        if not isinstance(page_title, str) or not page_title.strip() or len(page_title) > 100:
            raise ValueError("page_title must contain 1 to 100 characters")
        limit = max(1, min(int(query_limit), 100))
        normalized_average_mode = str(average_mode).strip().casefold()
        allowed_average_modes = {
            "none", "simple", "maximum", "minimum", "advanced", "difference"
        }
        if normalized_average_mode not in allowed_average_modes:
            raise ValueError(
                "average_mode must be none, simple, maximum, minimum, advanced, or difference"
            )

        image_path.parent.mkdir(parents=True, exist_ok=True)
        session = hw.Session()
        page = hw.Page(layout=1, title=page_title.strip())
        session.setActive(hw.Page, page=page)
        window = session.get(hw.Window, page=page)
        window.type = "animation"
        window.addModelAndResult(model=str(model_path), result=str(result_path))

        result = session.get(hv.Result, page=page, window=window)
        subcase_ids = _jsonable(result.getSubcaseIds())
        subcase_labels = _jsonable(result.getSubcaseLabels())
        if subcase_ids:
            result.subcase = int(subcase_ids[0])
        selected_subcase = int(subcase_ids[0]) if subcase_ids else 1
        data_types = _jsonable(result.getDataTypes(selected_subcase))
        resolved_data_type = data_type
        if isinstance(data_types, list):
            available_type_names = [str(item) for item in data_types]
            exact_type = next(
                (
                    item
                    for item in available_type_names
                    if item.casefold() == data_type.casefold()
                ),
                None,
            )
            if exact_type:
                resolved_data_type = exact_type
            elif data_type.casefold() == "stress":
                resolved_data_type = next(
                    (
                        item
                        for item in available_type_names
                        if "stress" in item.casefold()
                    ),
                    data_type,
                )
        data_components = None
        data_components_error = None
        try:
            data_components = _jsonable(
                result.getDataComponents(resolved_data_type, selected_subcase)
            )
        except Exception as exc:
            # Some HyperView readers accept the documented datatype/component
            # pair for plotting while rejecting getDataComponents() for that
            # same vector result.  Component enumeration is diagnostic only.
            data_components_error = str(exc)
        resolved_data_component = data_component
        if isinstance(data_components, list):
            normalized_component = re.sub(r"[^a-z0-9]", "", data_component.casefold())
            resolved_data_component = next(
                (
                    str(item)
                    for item in data_components
                    if re.sub(r"[^a-z0-9]", "", str(item).casefold())
                    == normalized_component
                ),
                data_component,
            )
        scalar = hv.ResultDefinitionScalar(
            dataType=resolved_data_type,
            dataComponent=resolved_data_component,
            page=page,
            window=window,
        )
        scalar.averageMode = normalized_average_mode
        result.plot(scalar, waitTillLoaded=True)
        simulation_ids = _jsonable(result.getSimulationIds(selected_subcase))
        if isinstance(simulation, bool):
            raise ValueError("simulation must be first, last, or an integer simulation ID")
        if isinstance(simulation, int):
            if isinstance(simulation_ids, list) and simulation_ids:
                normalized_ids = [int(value) for value in simulation_ids]
                if simulation not in normalized_ids:
                    raise ValueError(
                        f"simulation ID {simulation} is not available; choose from {normalized_ids}"
                    )
            selected_simulation: str | int = simulation
        elif isinstance(simulation, str) and simulation.strip().casefold() in {"first", "last"}:
            requested_simulation = simulation.strip().casefold()
            if requested_simulation == "first" and isinstance(simulation_ids, list) and simulation_ids:
                selected_simulation = int(simulation_ids[0])
            else:
                selected_simulation = "last"
        else:
            raise ValueError("simulation must be first, last, or an integer simulation ID")
        result.simulation = selected_simulation
        window.fit()
        window.draw()

        legend = session.get(hv.LegendScalar, page=page, window=window)
        legend.showMax = True
        legend.showMin = True
        window.draw()

        capture = hw.CaptureImageTool(type="png")
        capture.file = str(image_path)
        capture.width = 1600
        capture.height = 1000
        capture.area = "window"
        capture.capture()
        if not image_path.is_file():
            raise RuntimeError("HyperView capture did not create the requested image")

        query_rows = []
        numeric_rows = []
        query_error = None
        query_entity_count = 0
        query_data_source = None
        try:
            entity_class = hv.Node if entity_key == "node" else hv.Element
            collection = hv.Collection(
                entity_class,
                model=session.get(hv.Model, page=page, window=window),
            )
            # The 2026 constructor infers the default entity binding from the
            # active, fully-loaded contour.  Passing Page/Window explicitly can
            # bypass that inference and construct Collection(None).
            query_tool = hv.QueryResultsTool()
            query_tool.collection = collection
            query_tool.setDataSourceQuery(
                [[entity_key, "id"], ["contour", "value"]]
            )
            query_rows = _jsonable(query_tool.query())
            if not isinstance(query_rows, list):
                query_rows = [query_rows]
            query_entity_count = int(collection.getSize())
            query_data_source = _jsonable(query_tool.getDataSourceQuery())
            for row in query_rows:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    try:
                        numeric_value = float(row[-1])
                        if math.isfinite(numeric_value):
                            numeric_rows.append((numeric_value, list(row)))
                    except (TypeError, ValueError):
                        pass
            numeric_rows.sort(key=lambda item: item[0], reverse=True)
        except Exception as exc:
            query_error = str(exc)

        legend_min_raw = _jsonable(legend.minValue)
        legend_max_raw = _jsonable(legend.maxValue)
        try:
            legend_minimum = float(legend_min_raw)
        except (TypeError, ValueError):
            legend_minimum = None
        try:
            legend_maximum = float(legend_max_raw)
        except (TypeError, ValueError):
            legend_maximum = None
        return {
            "postprocessed": True,
            "bridge_version": BRIDGE_VERSION,
            "page_id": int(page.id),
            "page_title": str(page.title),
            "model_file": str(model_path),
            "result_file": str(result_path),
            "subcase_ids": subcase_ids,
            "subcase_labels": subcase_labels,
            "simulation_ids": simulation_ids,
            "selected_simulation": selected_simulation,
            "available_data_types": data_types,
            "available_components": data_components,
            "available_components_error": data_components_error,
            "contour": {
                "requested_data_type": data_type,
                "requested_data_component": data_component,
                "data_type": resolved_data_type,
                "data_component": resolved_data_component,
                "average_mode": normalized_average_mode,
            },
            "legend": {
                "minimum": legend_minimum,
                "maximum": legend_maximum,
                "minimum_raw": legend_min_raw,
                "maximum_raw": legend_max_raw,
            },
            "query": {
                "entity_type": entity_key,
                "entity_count": query_entity_count,
                "row_count": len(query_rows),
                "minimum_row": numeric_rows[-1][1] if numeric_rows else None,
                "maximum_row": numeric_rows[0][1] if numeric_rows else None,
                "top_rows": [row for _, row in numeric_rows[:limit]],
                "data_source": query_data_source,
                "error": query_error,
            },
            "screenshot": {
                "path": str(image_path),
                "size_bytes": image_path.stat().st_size,
            },
        }

    def extract_hypergraph_time_history(
        self,
        model_file: str,
        result_file: str,
        csv_file: str,
        image_file: str,
        data_type: str = "Displacement",
        data_component: str = "Mag",
        entity_type: str = "node",
        entity_ids: list[int] | None = None,
        statistic: str = "maximum",
        curve_label: str = "MCP time history",
    ) -> dict[str, Any]:
        """Extract a frame history from HyperView and render it as a HyperGraph XY curve."""
        import hw
        import hw.hv as hv
        import hw.hg as hg

        model_path = self._safe_input(model_file)
        result_path = self._safe_input(result_file)
        csv_path = self._safe_output(csv_file)
        image_path = self._safe_output(image_file)
        if csv_path.suffix.lower() != ".csv":
            raise ValueError("csv_file must use the .csv extension")
        if image_path.suffix.lower() != ".png":
            raise ValueError("image_file must use the .png extension")
        if entity_type not in {"node", "element"}:
            raise ValueError("entity_type must be node or element")
        if statistic not in {"maximum", "minimum", "mean"}:
            raise ValueError("statistic must be maximum, minimum, or mean")
        selected_ids = None
        if entity_ids is not None:
            selected_ids = {int(value) for value in entity_ids}
            if not selected_ids or any(value <= 0 for value in selected_ids):
                raise ValueError("entity_ids must contain positive IDs")
            if len(selected_ids) > 5000:
                raise ValueError("At most 5000 entity IDs may be requested")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _()/+&.-]{0,79}", data_type):
            raise ValueError("Invalid data_type")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _()/+.-]{0,79}", data_component):
            raise ValueError("Invalid data_component")
        if not isinstance(curve_label, str) or not curve_label.strip() or len(curve_label) > 100:
            raise ValueError("curve_label must contain 1 to 100 characters")

        csv_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        session = hw.Session()
        result_page = hw.Page(layout=1, title="MCP history source")
        session.setActive(hw.Page, page=result_page)
        result_window = session.get(hw.Window, page=result_page)
        result_window.type = "animation"
        result_window.addModelAndResult(model=str(model_path), result=str(result_path))
        result = session.get(hv.Result, page=result_page, window=result_window)
        subcases = _jsonable(result.getSubcaseIds())
        selected_subcase = int(subcases[0]) if subcases else 1
        result.subcase = selected_subcase
        scalar = hv.ResultDefinitionScalar(
            dataType=data_type,
            dataComponent=data_component,
            page=result_page,
            window=result_window,
        )
        result.plot(scalar, waitTillLoaded=True)
        simulation_ids = [int(value) for value in _jsonable(result.getSimulationIds(selected_subcase))]
        if not simulation_ids:
            raise RuntimeError("The result does not expose any simulation frames")
        entity_class = hv.Node if entity_type == "node" else hv.Element
        collection = hv.Collection(
            entity_class,
            model=session.get(hv.Model, page=result_page, window=result_window),
        )
        query_tool = hv.QueryResultsTool()
        query_tool.collection = collection
        query_tool.setDataSourceQuery([[entity_type, "id"], ["contour", "value"]])

        history = []
        for frame_index, simulation_id in enumerate(simulation_ids, start=1):
            result.simulation = simulation_id
            result_window.draw()
            rows = _jsonable(query_tool.query())
            values = []
            for row in rows if isinstance(rows, list) else [rows]:
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                try:
                    item_id = int(row[0])
                    value = float(row[-1])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value) and (selected_ids is None or item_id in selected_ids):
                    values.append(value)
            if not values:
                raise RuntimeError(f"Frame {simulation_id} returned no finite contour values")
            if statistic == "maximum":
                aggregate = max(values)
            elif statistic == "minimum":
                aggregate = min(values)
            else:
                aggregate = sum(values) / len(values)
            current_time = None
            try:
                current_time = float(hw.AnimationTool().currentTime)
                if not math.isfinite(current_time):
                    current_time = None
            except Exception:
                pass
            history.append(
                {
                    "frame_index": frame_index,
                    "simulation_id": simulation_id,
                    "time": current_time,
                    "value": aggregate,
                    "sample_count": len(values),
                }
            )

        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=["frame_index", "simulation_id", "time", "value", "sample_count"],
            )
            writer.writeheader()
            writer.writerows(history)

        graph_page = hw.Page(layout=1, title=curve_label.strip())
        session.setActive(hw.Page, page=graph_page)
        try:
            graph_window = session.get(hw.Window, page=graph_page)
            graph_window.type = "xy"
            curve = hg.CurveXY(label=curve_label.strip())
            x_values = [
                item["time"] if item["time"] is not None else float(item["simulation_id"])
                for item in history
            ]
            y_values = [float(item["value"]) for item in history]
            curve.xValues = x_values
            curve.yValues = y_values
            graph_window.update()
            graph_window.fit()
            capture = hw.CaptureImageTool(type="png")
            capture.file = str(image_path)
            capture.width = 1600
            capture.height = 1000
            capture.area = "window"
            capture.capture()
        finally:
            # The Extension is hosted by the HyperMesh/HyperView client. Leaving an
            # XY page active can trigger its unload hook when HyperWorks changes
            # clients after this request returns. Keep the HyperGraph page in the
            # session, but restore the result page so the localhost bridge remains
            # alive for the next MCP call.
            session.setActive(hw.Page, page=result_page)
        if not csv_path.is_file() or not image_path.is_file():
            raise RuntimeError("HyperGraph history export did not create all requested files")
        return {
            "extracted": True,
            "bridge_version": BRIDGE_VERSION,
            "model_file": str(model_path),
            "result_file": str(result_path),
            "data_type": data_type,
            "data_component": data_component,
            "entity_type": entity_type,
            "entity_ids": sorted(selected_ids) if selected_ids is not None else None,
            "statistic": statistic,
            "simulation_ids": simulation_ids,
            "history": history,
            "csv": {"path": str(csv_path), "size_bytes": csv_path.stat().st_size},
            "screenshot": {"path": str(image_path), "size_bytes": image_path.stat().st_size},
            "hypergraph_page_id": int(graph_page.id),
        }

    def save_model(
        self,
        output_file: str,
        model_name: str | None = None,
        overwrite: bool = False,
        do_not_write_facets: int = 0,
    ) -> dict[str, Any]:
        path = self._safe_output(output_file)
        if path.suffix.lower() != ".hm":
            raise ValueError("output_file must use the .hm extension")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not overwrite:
            raise ValueError("Output already exists; set overwrite=true after approval")
        model = self._model(model_name)
        if path.exists() and overwrite:
            model.hm_answernext(answer="yes")
        model.writefile(
            filename=path.as_posix(),
            do_not_write_facets=max(0, min(int(do_not_write_facets), 2)),
        )
        return {"saved": True, "output_file": str(path), "model_name": model_name}

    def export_feko_mesh(
        self,
        output_file: str,
        template_file: str,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Write the live mesh to a FEKO .fhm file through the installed export template."""
        import hw

        path = self._safe_output(output_file)
        if path.suffix.lower() != ".fhm":
            raise ValueError("output_file must use the .fhm extension")
        template = Path(template_file).expanduser().resolve()
        if template.suffix.lower() != ".tpl" or not template.is_file():
            raise ValueError("template_file must be an existing FEKO .tpl export template")
        hm, ent = self._imports()
        model = self._model(model_name)
        element_count = len(hm.Collection(model, ent.Element))
        if not element_count:
            raise RuntimeError("The model contains no elements to export")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        command = "*feoutputwithdata %s %s 0 0 1 1 0" % (
            _tcl_quote(str(template)),
            _tcl_quote(str(path)),
        )
        hw.evalTcl(command)
        if not path.is_file():
            raise RuntimeError("FEKO export did not create the output file")
        # CADFEKO imports .fhm as mesh-only and expects media/coating cards in
        # a same-basename .inc sidecar. HyperMesh writes every card into one
        # file, and DI cards inside .fhm are rejected with error 30410.
        material_lines: list[str] = []
        mesh_lines: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True):
            if line.lstrip().startswith(("DI:", "SK:", "MC:")):
                material_lines.append(line)
            else:
                mesh_lines.append(line)
        include_path = path.with_suffix(".inc")
        if material_lines:
            include_path.write_text("".join(material_lines), encoding="utf-8")
            path.write_text("".join(mesh_lines), encoding="utf-8")
        elif include_path.is_file():
            include_path.unlink()
        result = {
            "exported": True,
            "bridge_version": BRIDGE_VERSION,
            "model_name": model_name,
            "output_file": str(path),
            "template_file": str(template),
            "element_count": element_count,
            "size_bytes": path.stat().st_size,
            "command": command,
        }
        if include_path.is_file():
            result["include_file"] = str(include_path)
            result["include_size_bytes"] = include_path.stat().st_size
            result["material_card_count"] = len(material_lines)
        return result
