"""Decide which two materials a labelled surface separates.

The input is a FEKO ``.fhm`` mesh in which every label groups the triangles of
one CAD surface, plus a mapping from label to material.  Each material is
treated as a closed reference shell built from all of its own triangles; a
query point is inside a material when that material's winding number is close
to one.  Probing a short distance off both sides of a surface therefore names
the two materials without any distance threshold, which is what makes the
method independent of element size and of how well the model was stitched.

A surface may also carry simulation assignments that have no volume - an ideal
conductor, an impedance sheet, a symmetry plane.  Probing cannot see those, so
they are supplied per label and appended to the group name in a fixed order.

Nothing here assumes a particular material set, surface count, label naming
scheme, attribute vocabulary or probe distance: the number of shells follows
from the mapping, the probe distance from each label's own triangle size, and
the labels and attributes are opaque strings.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

OUTSIDE = "air"

# Probe distances are multiples of the label's median triangle edge length, so
# the same settings work on a 1 mm gasket and a 200 mm panel.  The clamps keep
# pathological meshes from producing absurd offsets.
OFFSET_MULTIPLIERS = (0.35, 0.7, 1.4)
MIN_OFFSET_MM = 0.05
MAX_OFFSET_MM = 50.0
DEFAULT_THRESHOLD = 0.55


def group_name(minus: str, plus: str, tags: Sequence[str] = ()) -> str:
    """Name a group from its ordered material pair and its face attributes.

    The minus side comes first, then the plus side, then every attribute that
    was assigned to the face.  Attributes are simulation assignments that live
    on the surface and have no volume - a perfect-conductor sheet, an impedance
    boundary, a symmetry plane - so probing can never see them and they are
    carried alongside instead.
    """
    return f"{minus}-{plus}" + "".join(f"-{tag}" for tag in tags)


@dataclass
class Shell:
    """One material treated as a closed reference volume."""

    name: str
    tris: np.ndarray
    low: np.ndarray
    high: np.ndarray

    @property
    def triangle_count(self) -> int:
        return int(len(self.tris))

    def contains(self, point: np.ndarray) -> bool:
        """True when the point lies inside the shell's bounding box."""
        return bool(np.all(point >= self.low) and np.all(point <= self.high))


@dataclass
class LabelResult:
    label: str
    material: str | None
    plus: str
    minus: str
    votes: dict[str, int]
    agreement: float
    median_edge: float
    offset: float
    margin: float
    tags: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "material": self.material,
            "plus": self.plus,
            "minus": self.minus,
            "pair": group_name(self.minus, self.plus, self.tags),
            "votes": self.votes,
            "agreement": self.agreement,
            "median_edge_mm": round(self.median_edge, 4),
            "offset_mm": round(self.offset, 4),
            "margin": round(self.margin, 4),
            "tags": list(self.tags),
        }


@dataclass
class Report:
    labels: list[LabelResult] = field(default_factory=list)
    shells: dict[str, Shell] = field(default_factory=dict)
    far_field: dict[str, float] = field(default_factory=dict)
    unknown_labels: list[str] = field(default_factory=list)

    def pairs(self) -> list[dict]:
        buckets: dict[tuple[str, str, tuple[str, ...]], list[str]] = {}
        for item in self.labels:
            key = (item.plus, item.minus, item.tags)
            buckets.setdefault(key, []).append(item.label)
        rows = [
            {
                "plus": plus,
                "minus": minus,
                "tags": list(tags),
                "name": group_name(minus, plus, tags),
                "labels": sorted(labels),
                "count": len(labels),
            }
            for (plus, minus, tags), labels in buckets.items()
        ]
        rows.sort(key=lambda row: (-row["count"], row["name"]))
        return rows

    def as_dict(self) -> dict:
        return {
            "label_count": len(self.labels),
            "shells": {
                name: {
                    "triangles": shell.triangle_count,
                    "far_field_winding": round(self.far_field.get(name, 0.0), 6),
                }
                for name, shell in sorted(self.shells.items())
            },
            "labels": [item.as_dict() for item in self.labels],
            "pairs": self.pairs(),
            "unknown_labels": sorted(self.unknown_labels),
        }


def parse_fhm(path: str | Path) -> tuple[list[str], np.ndarray]:
    """Read triangle geometry and its label from a FEKO .fhm file.

    Quadrilaterals, if present, are split into two triangles so the geometry is
    always triangular.  Labels are returned per triangle, in file order.
    """
    labels: list[str] = []
    triangles: list[list[tuple[float, float, float]]] = []
    label = ""
    nodes: list[list[str]] | None = None
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            if raw.startswith("LA:"):
                label = raw[3:].strip()
                continue
            if raw.startswith("DR:"):
                nodes = [raw.split(":")]
                continue
            if nodes is None or not raw.startswith("  :"):
                continue
            nodes.append(raw.split(":"))
            if len(nodes) not in (3, 4):
                continue
            corners = [_coordinates(fields) for fields in nodes]
            if len(corners) == 3:
                triangles.append(corners)
                labels.append(label)
            else:
                triangles.append([corners[0], corners[1], corners[2]])
                labels.append(label)
                triangles.append([corners[0], corners[2], corners[3]])
                labels.append(label)
            nodes = None
    if not triangles:
        raise ValueError(f"no triangle records found in {path}")
    return labels, np.asarray(triangles, dtype=np.float64)


def _coordinates(fields: Sequence[str]) -> tuple[float, float, float]:
    """Pick the x, y, z fields from one FEKO node record."""
    if len(fields) >= 9:
        try:
            return float(fields[6]), float(fields[7]), float(fields[8])
        except ValueError:
            pass
    numeric = []
    for item in reversed(fields):
        try:
            numeric.append(float(item))
        except ValueError:
            continue
        if len(numeric) == 3:
            return numeric[2], numeric[1], numeric[0]
    raise ValueError(f"cannot read coordinates from node record {fields!r}")


def build_shells(
    labels: Sequence[str],
    tris: np.ndarray,
    material_map: Mapping[str, str],
) -> tuple[dict[str, Shell], list[str]]:
    """Group labelled triangles into one closed shell per material.

    Every label is resolved to a material once, then the triangle indices are
    merged per material, so the cost grows with the number of triangles rather
    than with labels times materials.
    """
    label_array = np.asarray(labels)
    unique, inverse = np.unique(label_array, return_inverse=True)
    unknown = sorted(set(unique.tolist()) - set(material_map))

    per_material: dict[str, list[int]] = {}
    for position, name in enumerate(unique.tolist()):
        material = material_map.get(name)
        if material is None:
            continue
        per_material.setdefault(material, []).extend(np.flatnonzero(inverse == position).tolist())

    shells: dict[str, Shell] = {}
    for material, indices in per_material.items():
        if not indices:
            continue
        subset = tris[np.asarray(indices, dtype=int)]
        shells[material] = Shell(
            name=material,
            tris=subset,
            low=subset.reshape(-1, 3).min(axis=0),
            high=subset.reshape(-1, 3).max(axis=0),
        )
    if not shells:
        raise ValueError("the material map does not match any label in the mesh")
    return shells, unknown


def winding(point: np.ndarray, tris: np.ndarray) -> float:
    """Generalised winding number of a closed triangle set at one point."""
    a = tris[:, 0] - point
    b = tris[:, 1] - point
    c = tris[:, 2] - point
    la = np.linalg.norm(a, axis=1)
    lb = np.linalg.norm(b, axis=1)
    lc = np.linalg.norm(c, axis=1)
    numerator = (a * np.cross(b, c)).sum(axis=1)
    denominator = (
        la * lb * lc
        + (a * b).sum(axis=1) * lc
        + (b * c).sum(axis=1) * la
        + (c * a).sum(axis=1) * lb
    )
    return float(2.0 * np.arctan2(numerator, denominator).sum() / (4.0 * math.pi))


def field(
    point: np.ndarray,
    shells: Mapping[str, Shell],
    prune: bool = True,
) -> dict[str, float]:
    """Winding number of every shell at one point.

    A closed shell has winding number exactly zero outside its bounding box, so
    shells that cannot contain the point are skipped without any loss.  The
    shortcut is disabled for the closure diagnostic: skipping there would
    return zero for an open shell as well and hide exactly what is being
    measured.
    """
    values = {}
    for name, shell in shells.items():
        if prune and not shell.contains(point):
            values[name] = 0.0
        else:
            values[name] = winding(point, shell.tris)
    return values


def enclosing(
    point: np.ndarray,
    shells: Mapping[str, Shell],
    threshold: float,
) -> tuple[str | None, dict[str, float]]:
    """Material whose winding number at the point is largest and above threshold.

    Returns ``None`` when no shell encloses the point.  The caller maps that to
    the outside name, which may itself be a material name; comparing names here
    would confuse "inside the air shell" with "outside every shell".
    """
    values = field(point, shells)
    if not values:
        return None, {}
    name = max(values, key=values.get)
    return (name if values[name] > threshold else None), values


def median_edge_length(tris: np.ndarray) -> float:
    edges = np.stack(
        (
            np.linalg.norm(tris[:, 1] - tris[:, 0], axis=1),
            np.linalg.norm(tris[:, 2] - tris[:, 1], axis=1),
            np.linalg.norm(tris[:, 0] - tris[:, 2], axis=1),
        ),
        axis=1,
    ).reshape(-1)
    return float(np.median(edges))


def sample_triangles(tris: np.ndarray, samples: int) -> list[int]:
    """Spread sample indices over area, so large regions are represented too."""
    if len(tris) <= samples:
        return list(range(len(tris)))
    raw = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area = 0.5 * np.linalg.norm(raw, axis=1)
    cumulative = np.cumsum(area)
    total = cumulative[-1]
    if total <= 0.0:
        return list(range(0, len(tris), max(1, len(tris) // samples)))[:samples]
    picks = []
    for index in range(samples):
        target = (index + 0.5) * total / samples
        picks.append(int(min(np.searchsorted(cumulative, target), len(tris) - 1)))
    return sorted(set(picks))


def triangle_normal(tri: np.ndarray) -> np.ndarray | None:
    normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
    length = float(np.linalg.norm(normal))
    if length <= 0.0:
        return None
    return normal / length


def _probe_side(
    centroid: np.ndarray,
    direction: np.ndarray,
    offsets: Iterable[float],
    shells: Mapping[str, Shell],
    threshold: float,
    outside: str = OUTSIDE,
) -> tuple[str, float]:
    """Walk outwards until some material encloses the point, else report the outside name."""
    best: tuple[str, float] = (outside, 0.0)
    for distance in offsets:
        name, values = enclosing(centroid + distance * direction, shells, threshold)
        if name is not None:
            return name, values.get(name, 0.0)
        best = (outside, 0.0)
    return best


def classify(
    path: str | Path,
    material_map: Mapping[str, str],
    targets: Sequence[str] | None = None,
    face_tags: Mapping[str, Sequence[str]] | None = None,
    samples: int = 10,
    threshold: float = DEFAULT_THRESHOLD,
    offset_multipliers: Sequence[float] = OFFSET_MULTIPLIERS,
    outside: str = OUTSIDE,
) -> Report:
    """Classify every requested label into an ordered pair of materials.

    ``face_tags`` carries the simulation attributes of each label - ideal
    conductor, impedance sheet, symmetry plane, or whatever else the model
    assigns to a surface.  Such assignments have no volume and probing cannot
    see them, so they are supplied here and appended to the group name.

    ``outside`` names the region that no shell encloses.  It may also be the
    name of a material: a model whose enclosed cavity holds the same medium as
    the surroundings (air on both sides of a wall) is physically one medium,
    and both the shell and the open region then report that same name.  The
    per-point winding numbers stay in the report, so the two origins remain
    distinguishable where that matters.
    """
    labels, tris = parse_fhm(path)
    shells, unknown = build_shells(labels, tris, material_map)
    label_array = np.asarray(labels)
    attributes = {label: tuple(sorted(tags)) for label, tags in (face_tags or {}).items()}

    wanted = list(dict.fromkeys(targets)) if targets else list(dict.fromkeys(material_map))
    report = Report(shells=shells, unknown_labels=unknown)

    low = np.array([shell.low for shell in shells.values()]).min(axis=0)
    high = np.array([shell.high for shell in shells.values()]).max(axis=0)
    # Ten bounding-box diagonals away, so the distance follows the model's own
    # units and scale rather than a fixed number of millimetres.
    scale = float(np.linalg.norm(high - low)) or 1.0
    report.far_field = field(high + 10.0 * scale, shells, prune=False)

    for label in wanted:
        selected = np.where(label_array == label)[0]
        if not len(selected):
            continue
        subset = tris[selected]
        edge = median_edge_length(subset)
        offsets = [
            min(max(edge * factor, MIN_OFFSET_MM), MAX_OFFSET_MM)
            for factor in offset_multipliers
        ]
        votes: dict[tuple[str, str], int] = {}
        margins: list[float] = []
        for index in sample_triangles(subset, samples):
            tri = subset[index]
            normal = triangle_normal(tri)
            if normal is None:
                continue
            centroid = tri.mean(axis=0)
            plus, plus_margin = _probe_side(centroid, normal, offsets, shells, threshold, outside)
            minus, minus_margin = _probe_side(centroid, -normal, offsets, shells, threshold, outside)
            votes[(plus, minus)] = votes.get((plus, minus), 0) + 1
            margins.append(max(plus_margin, minus_margin))
        if not votes:
            continue
        (plus, minus), count = max(votes.items(), key=lambda item: item[1])
        total = sum(votes.values())
        report.labels.append(
            LabelResult(
                label=label,
                material=material_map.get(label),
                plus=plus,
                minus=minus,
                votes={f"{key[0]}/{key[1]}": value for key, value in votes.items()},
                agreement=count / total,
                median_edge=edge,
                offset=offsets[0],
                margin=float(np.mean(margins)),
                tags=attributes.get(label, ()),
            )
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point: python -m hyperworks_mcp.material_pairs."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fhm", help="FEKO .fhm file with one label per CAD surface")
    parser.add_argument("mapping", help="JSON file mapping label to material")
    parser.add_argument("--targets", nargs="*", help="labels to classify (default: all)")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="LABEL=TAG",
        help="face attribute for one label, repeatable (for example S13=PEC)",
    )
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument(
        "--outside",
        default=OUTSIDE,
        help="name for the region no material encloses (default: air)",
    )
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args(argv)

    material_map = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
    face_tags: dict[str, list[str]] = {}
    for item in args.tag:
        label, separator, tag = item.partition("=")
        if not separator or not label or not tag:
            parser.error(f"--tag expects LABEL=TAG, got {item!r}")
        face_tags.setdefault(label, []).append(tag)
    report = classify(
        args.fhm,
        material_map,
        targets=args.targets,
        face_tags=face_tags,
        samples=args.samples,
        threshold=args.threshold,
        outside=args.outside,
    )
    print(json.dumps(report.as_dict(), indent=args.indent, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
