import unittest
import struct
import tempfile
from pathlib import Path

import manifold3d as m3d
import numpy as np

from litmetica3d.block_models import Face, Vec3, _cuboid
from litmetica3d.optimize import _rect
from litmetica3d.exporters.array_mesh import export_stl_arrays
from litmetica3d.solid import (
    BooleanCancelled, SolidReport, cube_solid, manifold_from_closed_faces,
    process_components_and_cavities,
    union_balanced,
    validate_manifold,
)


class SolidPipelineTests(unittest.TestCase):
    def test_balanced_union_reports_progress_and_can_cancel(self):
        parts = [cube_solid((1, 1, 1), (i, 0, 0)) for i in range(40)]
        updates = []
        result = union_balanced(
            parts, batch_size=8,
            progress=lambda *values: updates.append(values),
        )
        self.assertFalse(result.is_empty())
        self.assertTrue(updates)
        with self.assertRaises(BooleanCancelled):
            union_balanced(parts, cancelled=lambda: True)

    def test_overlapping_cuboids_become_one_manifold(self):
        faces = _cuboid(0, 0, 0, 1, 1, 0.1, "plant")
        second = _cuboid(0.45, 0, -0.4, 0.55, 1, 0.5, "plant")
        solid = manifold_from_closed_faces(faces + second)
        self.assertEqual(m3d.Error.NoError, solid.status())
        self.assertEqual(1, len([x for x in solid.decompose() if x.volume() > 0]))

    def test_cavity_preserve_and_fill(self):
        hollow = (
            m3d.Manifold.cube((3, 3, 3))
            - m3d.Manifold.cube((1, 1, 1)).translate((1, 1, 1))
        )
        preserve_report = SolidReport()
        preserved = process_components_and_cavities(
            hollow, cavities="preserve", components="keep",
            min_component_volume=0, report=preserve_report,
        )
        fill_report = SolidReport()
        filled = process_components_and_cavities(
            hollow, cavities="fill", components="keep",
            min_component_volume=0, report=fill_report,
        )
        self.assertAlmostEqual(26, preserved.volume())
        self.assertAlmostEqual(27, filled.volume())
        self.assertEqual(1, fill_report.cavity_count)
        self.assertAlmostEqual(1, fill_report.filled_cavity_volume)

    def test_remove_small_component(self):
        large = m3d.Manifold.cube()
        small = m3d.Manifold.cube((0.05, 0.05, 0.05)).translate((2, 0, 0))
        report = SolidReport()
        result = process_components_and_cavities(
            m3d.Manifold.compose((large, small)),
            cavities="preserve", components="remove-small",
            min_component_volume=0.001, report=report,
        )
        self.assertEqual(1, report.removed_components)
        self.assertAlmostEqual(1, result.volume(), places=6)

    def test_main_component_keeps_largest_and_its_cavity(self):
        hollow = (
            m3d.Manifold.cube((3, 3, 3))
            - m3d.Manifold.cube((1, 1, 1)).translate((1, 1, 1))
        )
        small = m3d.Manifold.cube((1, 1, 1)).translate((10, 0, 0))
        source = hollow + small
        preserve_report = SolidReport()
        preserved = process_components_and_cavities(
            source, cavities="preserve", components="main",
            min_component_volume=0, report=preserve_report,
        )
        self.assertAlmostEqual(26.0, preserved.volume(), places=5)
        self.assertEqual(1, preserve_report.retained_component_count)
        self.assertEqual(1, preserve_report.removed_components)
        self.assertAlmostEqual(26.0, preserve_report.main_component_volume)

        fill_report = SolidReport()
        filled = process_components_and_cavities(
            source, cavities="fill", components="main",
            min_component_volume=0, report=fill_report,
        )
        self.assertAlmostEqual(27.0, filled.volume(), places=5)

    def test_main_component_tie_is_deterministic(self):
        left = m3d.Manifold.cube((1, 1, 1)).translate((-3, 0, 0))
        right = m3d.Manifold.cube((1, 1, 1)).translate((3, 0, 0))
        chosen_bounds = []
        for source in (left + right, right + left):
            report = SolidReport()
            process_components_and_cavities(
                source, cavities="preserve", components="main",
                min_component_volume=0, report=report,
            )
            chosen_bounds.append(report.main_component_bounds)
        self.assertEqual(chosen_bounds[0], chosen_bounds[1])
        self.assertEqual(-3.0, chosen_bounds[0][0])

    def test_export_validation_rejects_edge_and_point_contacts(self):
        for origin, edges, vertices in [((1, 1, 0), 1, None), ((1, 1, 1), 0, 1)]:
            with self.subTest(origin=origin):
                solid = m3d.Manifold.cube() + m3d.Manifold.cube().translate(origin)
                mesh = solid.to_mesh()
                report = SolidReport()
                validate_manifold(solid, report, mesh.vert_properties[:, :3], mesh.tri_verts)
                self.assertEqual(m3d.Error.NoError, solid.status())
                self.assertEqual(edges, report.nonmanifold_edges)
                self.assertEqual(vertices, report.nonmanifold_vertices)
                self.assertIsNone(report.self_intersections)
                self.assertFalse(report.printable)

    def test_export_validation_rejects_output_precision_collapse(self):
        solid = m3d.Manifold.cube()
        mesh = solid.to_mesh()
        vertices = mesh.vert_properties[:, :3].copy()
        vertices[:, 0] = 600 + vertices[:, 0] * 1e-6
        report = SolidReport()
        validate_manifold(solid, report, vertices, mesh.tri_verts)
        self.assertGreater(report.degenerate_triangles, 0)
        self.assertFalse(report.printable)

    def test_numpy_binary_stl_layout(self):
        vertices = np.asarray(
            ((0, 0, 0), (1, 0, 0), (0, 1, 0)), dtype=np.float32
        )
        triangles = np.asarray(((0, 1, 2),), dtype=np.uint32)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "triangle.stl"
            export_stl_arrays(vertices, triangles, target, binary=True)
            data = target.read_bytes()
        self.assertEqual(134, len(data))
        self.assertEqual(1, struct.unpack_from("<I", data, 80)[0])
        normal = struct.unpack_from("<fff", data, 84)
        self.assertAlmostEqual(1.0, normal[2])

    def test_rotated_horizontal_quad_is_not_axis_aligned_rect(self):
        face = Face(
            [Vec3(0.5, 1, 0), Vec3(1, 1, 0.5),
             Vec3(0.5, 1, 1), Vec3(0, 1, 0.5)],
            Vec3(0, 1, 0), "grass",
        )
        self.assertIsNone(_rect(face))


if __name__ == "__main__":
    unittest.main()
