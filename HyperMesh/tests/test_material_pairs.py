from __future__ import annotations

import json

import numpy as np
import pytest

from hyperworks_mcp import material_pairs as mp


FACES = {
    "x0": [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)],
    "x1": [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)],
    "y0": [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)],
    "y1": [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)],
    "z0": [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)],
    "z1": [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
}


def box_records(origin, size, prefix):
    """One label per face, wound anticlockwise seen from outside the box."""
    ox, oy, oz = origin
    records = []
    for name, corners in FACES.items():
        points = [(ox + x * size, oy + y * size, oz + z * size) for x, y, z in corners]
        records.append((f"{prefix}_{name}", [points[0], points[1], points[2]]))
        records.append((f"{prefix}_{name}", [points[0], points[2], points[3]]))
    return records


def write_fhm(path, records):
    lines = ["** synthetic", "VV:2"]
    for label, tri in records:
        if not lines or lines[-1] != f"LA:{label}":
            lines.append(f"LA:{label}")
        head, *tail = tri
        lines.append("DR:-:-:-:0::{0}:{1}:{2}:".format(*head))
        for point in tail:
            lines.append("  ::::::{0}:{1}:{2}".format(*point))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture()
def two_boxes(tmp_path):
    """Box A on [-1,0] and box B on [0,1], sharing the plane x = 0."""
    records = box_records((-1, 0, 0), 1.0, "A") + box_records((0, 0, 0), 1.0, "B")
    path = tmp_path / "pair.fhm"
    write_fhm(path, records)
    material_map = {
        **{f"A_{name}": "A" for name in FACES},
        **{f"B_{name}": "B" for name in FACES},
    }
    return path, material_map


def test_parse_reads_labels_and_geometry(two_boxes):
    path, _ = two_boxes
    labels, tris = mp.parse_fhm(path)
    assert len(labels) == 24
    assert tris.shape == (24, 3, 3)
    assert set(labels) == {f"{box}_{face}" for box in "AB" for face in FACES}


def test_winding_inside_and_outside_box(two_boxes):
    path, _ = two_boxes
    _, tris = mp.parse_fhm(path)
    box_a = tris[np.array([label.startswith("A_") for label in mp.parse_fhm(path)[0]])]
    assert mp.winding(np.array([-0.5, 0.5, 0.5]), box_a) == pytest.approx(1.0, abs=1e-6)
    assert mp.winding(np.array([0.5, 0.5, 0.5]), box_a) == pytest.approx(0.0, abs=1e-6)
    assert mp.winding(np.array([-5.0, -5.0, -5.0]), box_a) == pytest.approx(0.0, abs=1e-6)


def test_ordered_pairs_across_a_shared_face(two_boxes):
    path, material_map = two_boxes
    report = mp.classify(path, material_map, targets=["A_x1", "B_x0", "A_x0", "B_x1"])
    result = {item.label: item for item in report.labels}

    assert (result["A_x1"].plus, result["A_x1"].minus) == ("B", "A")
    assert (result["B_x0"].plus, result["B_x0"].minus) == ("A", "B")
    assert (result["A_x0"].plus, result["A_x0"].minus) == (mp.OUTSIDE, "A")
    assert (result["B_x1"].plus, result["B_x1"].minus) == (mp.OUTSIDE, "B")
    assert all(item.agreement == 1.0 for item in result.values())


def test_group_names_put_the_minus_side_first_and_carry_face_tags(two_boxes):
    path, material_map = two_boxes
    report = mp.classify(path, material_map, targets=["A_x1"], face_tags={"A_x1": ["PEC"]})
    item = report.labels[0]
    assert item.as_dict()["pair"] == "A-B-PEC"
    assert item.as_dict()["tags"] == ["PEC"]


def test_several_face_tags_are_ordered_and_reach_the_group_row(two_boxes):
    path, material_map = two_boxes
    # Supplied in one order and expected back in a stable, sorted one.
    report = mp.classify(
        path,
        material_map,
        targets=["A_x1"],
        face_tags={"A_x1": ["symmetry", "PEC"]},
    )
    assert report.labels[0].as_dict()["pair"] == "A-B-PEC-symmetry"
    assert report.pairs()[0]["name"] == "A-B-PEC-symmetry"
    assert report.pairs()[0]["tags"] == ["PEC", "symmetry"]


def test_a_face_tag_that_is_not_a_standard_boundary_is_passed_through(two_boxes):
    path, material_map = two_boxes
    report = mp.classify(path, material_map, targets=["A_x1"], face_tags={"A_x1": ["coating-2"]})
    assert report.labels[0].as_dict()["pair"] == "A-B-coating-2"


def test_unknown_labels_are_reported(two_boxes):
    path, material_map = two_boxes
    report = mp.classify(path, material_map, targets=["A_x1"])
    assert report.unknown_labels == []


def test_the_outside_name_can_be_renamed(two_boxes):
    path, material_map = two_boxes
    report = mp.classify(path, material_map, targets=["A_x0"], outside="void")
    assert report.labels[0].as_dict()["pair"] == "A-void"


def test_the_outside_may_share_a_material_name(two_boxes):
    # An enclosed cavity of the same medium as the surroundings is one medium;
    # both the shell and the open region report that name.
    path, material_map = two_boxes
    report = mp.classify(path, material_map, targets=["A_x0"], outside="A")
    item = report.labels[0].as_dict()
    assert item["pair"] == "A-A"
    assert item["minus"] == "A" and item["plus"] == "A"


def test_empty_material_map_is_rejected(two_boxes):
    path, _ = two_boxes
    with pytest.raises(ValueError):
        mp.classify(path, {})


def test_sampling_spreads_over_equal_triangles_and_favours_large_ones():
    equal = np.zeros((10, 3, 3))
    equal[:, 1] = (1, 0, 0)
    equal[:, 2] = (0, 1, 0)
    picks = mp.sample_triangles(equal, 3)
    assert len(picks) == 3
    assert picks[0] < picks[1] < picks[2]

    skewed = np.zeros((10, 3, 3))
    skewed[:9, 1] = (0.1, 0, 0)
    skewed[:9, 2] = (0, 0.1, 0)
    skewed[9:, 1] = (10, 0, 0)
    skewed[9:, 2] = (0, 10, 0)
    assert mp.sample_triangles(skewed, 3) == [9]


def face_grid(corners, n):
    """Split one anticlockwise face into n x n quads, two triangles each."""
    p00, p10, p11, p01 = (np.asarray(corner, dtype=float) for corner in corners)

    def point(u, v):
        return (1 - u) * (1 - v) * p00 + u * (1 - v) * p10 + u * v * p11 + (1 - u) * v * p01

    tris = []
    for i in range(n):
        for j in range(n):
            u0, u1 = i / n, (i + 1) / n
            v0, v1 = j / n, (j + 1) / n
            a, b, c, d = point(u0, v0), point(u1, v0), point(u1, v1), point(u0, v1)
            tris.append([a, b, c])
            tris.append([a, c, d])
    return tris


def grid_box(origin, size, divisions):
    ox, oy, oz = origin
    labels, tris = [], []
    for name, corners in FACES.items():
        points = [(ox + x * size, oy + y * size, oz + z * size) for x, y, z in corners]
        for tri in face_grid(points, divisions):
            labels.append(name)
            tris.append(tri)
    return labels, np.asarray(tris, dtype=float)


def test_nested_shells_report_both_containing_materials():
    outer = box_records((0, 0, 0), 2.0, "O")
    inner = box_records((0.5, 0.5, 0.5), 1.0, "I")
    records = outer + inner
    labels = [label for label, _ in records]
    tris = np.asarray([tri for _, tri in records], dtype=float)
    material_map = {**{f"O_{f}": "outer" for f in FACES}, **{f"I_{f}": "inner" for f in FACES}}

    shells, unknown = mp.build_shells(labels, tris, material_map)
    assert unknown == []

    centre = mp.field(np.array([1.0, 1.0, 1.0]), shells)
    assert centre["outer"] == pytest.approx(1.0, abs=1e-6)
    assert centre["inner"] == pytest.approx(1.0, abs=1e-6)

    gap = mp.field(np.array([0.25, 0.25, 0.25]), shells)
    assert gap["outer"] == pytest.approx(1.0, abs=1e-6)
    assert abs(gap["inner"]) < 1e-6


def test_one_material_may_occupy_several_bodies():
    first = box_records((0, 0, 0), 1.0, "first")
    second = box_records((5, 0, 0), 1.0, "second")
    records = first + second
    labels = [label for label, _ in records]
    tris = np.asarray([tri for _, tri in records], dtype=float)
    material_map = {label: "solid" for label in set(labels)}

    shells, _ = mp.build_shells(labels, tris, material_map)
    assert mp.field(np.array([0.5, 0.5, 0.5]), shells)["solid"] == pytest.approx(1.0, abs=1e-6)
    assert mp.field(np.array([5.5, 0.5, 0.5]), shells)["solid"] == pytest.approx(1.0, abs=1e-6)
    assert mp.field(np.array([3.0, 0.5, 0.5]), shells)["solid"] == pytest.approx(0.0, abs=1e-6)


def test_open_shell_shows_up_in_the_far_field():
    records = box_records((0, 0, 0), 1.0, "A")
    torn = [item for item in records if item[0] != "A_x1"]
    far = np.array([10.0, 10.0, 10.0])

    closed_labels = [label for label, _ in records]
    closed_tris = np.asarray([tri for _, tri in records], dtype=float)
    closed_shells, _ = mp.build_shells(
        closed_labels, closed_tris, {label: "A" for label in set(closed_labels)}
    )
    assert abs(mp.field(far, closed_shells, prune=False)["A"]) < 1e-9

    open_labels = [label for label, _ in torn]
    open_tris = np.asarray([tri for _, tri in torn], dtype=float)
    open_shells, _ = mp.build_shells(
        open_labels, open_tris, {label: "A" for label in set(open_labels)}
    )
    assert abs(mp.field(far, open_shells, prune=False)["A"]) > 1e-6


def test_probe_offset_follows_the_local_triangle_size(tmp_path):
    coarse_labels, coarse_tris = grid_box((0, 0, 0), 1.0, 1)
    fine_labels, fine_tris = grid_box((5, 0, 0), 1.0, 4)
    path = tmp_path / "scaled.fhm"
    write_fhm(
        path,
        [(f"coarse_{label}", tri.tolist()) for label, tri in zip(coarse_labels, coarse_tris)]
        + [(f"fine_{label}", tri.tolist()) for label, tri in zip(fine_labels, fine_tris)],
    )
    material_map = {f"coarse_{f}": "coarse" for f in FACES}
    material_map.update({f"fine_{f}": "fine" for f in FACES})

    report = mp.classify(path, material_map, targets=["coarse_x1", "fine_x1"])
    by_label = {item.label: item for item in report.labels}
    ratio = by_label["coarse_x1"].offset / by_label["fine_x1"].offset
    assert 3.5 < ratio < 4.5
