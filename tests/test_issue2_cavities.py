"""Geometric regressions for cavity ownership and component filtering."""

import unittest
from unittest.mock import patch

import manifold3d as m3d
from litmetica3d.solid import SolidReport, process_components_and_cavities


def hollow_cube(size, cavity_size, origin=(0, 0, 0)):
    margin = (size - cavity_size) / 2
    return (
        m3d.Manifold.cube((size,) * 3)
        - m3d.Manifold.cube((cavity_size,) * 3).translate((margin,) * 3)
    ).translate(origin)


class Issue2CavityTests(unittest.TestCase):
    def process(self, source, cavity, component, threshold=0):
        report = SolidReport()
        result, vertices, triangles = process_components_and_cavities(
            source, cavities=cavity, components=component,
            min_component_volume=threshold, report=report, return_mesh=True,
        )
        self.assertEqual(m3d.Error.NoError, result.status())
        self.assertTrue(vertices.flags.writeable)
        self.assertTrue(triangles.flags.writeable)
        rebuilt = m3d.Manifold(m3d.Mesh(vertices, triangles))
        self.assertEqual(m3d.Error.NoError, rebuilt.status())
        self.assertAlmostEqual(result.volume(), rebuilt.volume(), places=5)
        self.assertAlmostEqual(result.volume(), report.volume_after_cavity_fill)
        return result, report

    def test_nested_hollow_island_all_policies(self):
        source = hollow_cube(10, 8) + hollow_cube(4, 2, (3, 3, 3))
        for component in ("keep", "main", "remove-small"):
            for cavity in ("preserve", "fill"):
                with self.subTest(component=component, cavity=cavity):
                    result, report = self.process(source, cavity, component, 65)
                    kept = component == "keep"
                    expected = 1000 if cavity == "fill" else (544 if kept else 488)
                    self.assertAlmostEqual(expected, result.volume())
                    self.assertAlmostEqual(544, report.volume_before_cavity_fill)
                    self.assertEqual(2, report.component_count)
                    self.assertEqual(2, report.cavity_count)
                    self.assertEqual(2 if kept and cavity == "preserve" else 1,
                                     report.retained_component_count)
                    self.assertAlmostEqual(0 if kept else 56,
                                           report.removed_component_volume)
                    filled = (456 if kept else 512) if cavity == "fill" else 0
                    self.assertAlmostEqual(filled, report.filled_cavity_volume)

    def test_multiple_islands_are_absorbed_by_fill(self):
        source = hollow_cube(10, 8)
        for origin in ((2, 2, 2), (4, 4, 4), (7, 7, 7)):
            source += m3d.Manifold.cube().translate(origin)
        result, report = self.process(source, "fill", "keep")
        self.assertAlmostEqual(1000, result.volume())
        self.assertAlmostEqual(509, report.filled_cavity_volume)
        self.assertEqual(4, report.component_count)
        self.assertEqual(1, report.retained_component_count)

    def test_independent_hollow_components_stay_independent_after_fill(self):
        source = hollow_cube(10, 8) + hollow_cube(3, 1, (20, 0, 0))
        result, report = self.process(source, "fill", "keep")
        self.assertAlmostEqual(1027, result.volume())
        self.assertAlmostEqual(513, report.filled_cavity_volume)
        self.assertEqual(2, report.retained_component_count)

    def test_ownership_after_non_axis_aligned_transform(self):
        source = hollow_cube(10, 8) + hollow_cube(4, 2, (3, 3, 3))
        transformed = source.rotate((23.5, 11.25, -13)).translate((-64, 16, 32))
        # Normalize once to the production pipeline's float32 mesh precision.
        transformed = m3d.Manifold(transformed.to_mesh())
        for component in ("main", "remove-small"):
            for cavity in ("preserve", "fill"):
                with self.subTest(component=component, cavity=cavity):
                    result, report = self.process(transformed, cavity, component, 65)
                    self.assertAlmostEqual(1000 if cavity == "fill" else 488,
                                           result.volume(), delta=.002)
                    self.assertAlmostEqual(512 if cavity == "fill" else 0,
                                           report.filled_cavity_volume, delta=.002)
                    self.assertAlmostEqual(56, report.removed_component_volume,
                                           delta=.002)

    def test_rotated_far_from_origin_has_one_shell_and_no_fill(self):
        for origin in ((10000, 10000, 10000), (-10000, -10000, -10000)):
            with self.subTest(origin=origin):
                transformed = m3d.Manifold.cube((10, 10, 10)).rotate(
                    (23.5, 11.25, -13)
                ).translate(origin)
                source = m3d.Manifold(transformed.to_mesh())
                result, report = self.process(source, "fill", "keep")
                self.assertAlmostEqual(source.volume(), result.volume(), places=5)
                self.assertEqual(1, report.component_count)
                self.assertEqual(1, report.retained_component_count)
                self.assertEqual(0, report.cavity_count)
                self.assertEqual(0, report.filled_cavity_volume)

    def test_removed_hollow_volume_is_not_counted_as_fill(self):
        source = hollow_cube(10, 8) + hollow_cube(2, 1, (20, 0, 0))
        for component in ("main", "remove-small"):
            for cavity in ("preserve", "fill"):
                with self.subTest(component=component, cavity=cavity):
                    result, report = self.process(source, cavity, component, 10)
                    self.assertAlmostEqual(1000 if cavity == "fill" else 488,
                                           result.volume())
                    self.assertAlmostEqual(7, report.removed_component_volume)
                    self.assertAlmostEqual(512 if cavity == "fill" else 0,
                                           report.filled_cavity_volume)

    def test_concave_bbox_is_not_cavity_ownership(self):
        # The detached three-legged frame's AABB contains the main cavity and
        # is smaller than the main AABB, but its material does not enclose it.
        main = (
            m3d.Manifold.cube((4, 4, 4))
            + m3d.Manifold.cube((46, 1, 1)).translate((4, 0, 0))
        ) - m3d.Manifold.cube().translate((1, 1, 1))
        frame = (
            m3d.Manifold.cube((8, .5, .5)).translate((-2, -2, -2))
            + m3d.Manifold.cube((.5, 8, .5)).translate((-2, -2, -2))
            + m3d.Manifold.cube((.5, .5, 8)).translate((-2, -2, -2))
        )
        for source in (main + frame, frame + main):
            for component in ("main", "remove-small"):
                for cavity in ("preserve", "fill"):
                    with self.subTest(component=component, cavity=cavity):
                        result, report = self.process(source, cavity, component, 10)
                        self.assertAlmostEqual(110 if cavity == "fill" else 109,
                                               result.volume())
                        self.assertAlmostEqual(1 if cavity == "fill" else 0,
                                               report.filled_cavity_volume)
                        self.assertAlmostEqual(5.75, report.removed_component_volume)
                        self.assertEqual(1, report.retained_component_count)

    def test_all_components_removed_and_empty_input(self):
        for source in (hollow_cube(2, 1), m3d.Manifold()):
            for cavity in ("preserve", "fill"):
                with self.subTest(cavity=cavity, empty=source.is_empty()):
                    result, report = self.process(source, cavity, "remove-small", 10)
                    self.assertTrue(result.is_empty())
                    self.assertEqual(0, report.retained_component_count)
                    self.assertEqual(0, report.filled_cavity_volume)
                    self.assertAlmostEqual(source.volume(), report.removed_component_volume)

    def test_remove_small_without_removal_preserves_mesh(self):
        source = hollow_cube(3, 1)
        result, report = self.process(source, "preserve", "remove-small", .1)
        self.assertAlmostEqual(26, result.volume())
        self.assertEqual(0, report.removed_components)
        self.assertEqual(0, report.filled_cavity_volume)

    def test_fill_without_cavities_skips_union_and_materialization(self):
        source = (
            m3d.Manifold.cube((2, 2, 2))
            + m3d.Manifold.cube().translate((4, 0, 0))
        )
        for component in ("keep", "main", "remove-small"):
            with self.subTest(component=component), patch(
                "litmetica3d.solid.union_balanced",
                side_effect=AssertionError("no cavities: must not union shells"),
            ), patch(
                "litmetica3d.solid.materialize_manifold",
                side_effect=AssertionError("no cavities: must not materialize"),
            ):
                result, report = self.process(source, "fill", component, 2)
                self.assertAlmostEqual(9 if component == "keep" else 8,
                                       result.volume())
                if component == "keep":
                    self.assertIs(source, result)
                self.assertEqual(0, report.cavity_count)
                self.assertEqual(0, report.filled_cavity_volume)


if __name__ == "__main__":
    unittest.main()
