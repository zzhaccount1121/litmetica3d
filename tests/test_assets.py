import unittest
from collections import Counter
from pathlib import Path

from litmetica3d.conversion import bundled_asset_path
from litmetica3d.model_loader import ModelLoader, _rotate_vertex
from litmetica3d.solid import manifold_from_closed_faces
import manifold3d as m3d


class AssetSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = ModelLoader(bundled_asset_path())

    @classmethod
    def tearDownClass(cls):
        cls.loader.close()

    def test_asset_inventory(self):
        self.assertGreaterEqual(
            self.loader.block_count, 1100,
            f"assets={self.loader.asset_path!r}, "
            f"is_file={self.loader.asset_path.is_file()}, "
            f"archive={self.loader.jar is not None}, "
            f"entries={len(self.loader._asset_names)}, "
            f"sample={sorted(self.loader._asset_names)[:3]!r}",
        )

    def test_archive_and_directory_have_equivalent_assets(self):
        resources = Path(__file__).resolve().parents[1] / "litmetica3d/mc_assets"
        archive_path = resources / "26.2.zip"
        directory_path = resources / "26.2"
        self.assertTrue(archive_path.is_file(), repr(archive_path))
        self.assertTrue(directory_path.is_dir(), repr(directory_path))
        archive = ModelLoader(archive_path)
        directory = ModelLoader(directory_path)
        try:
            self.assertIsNotNone(archive.jar, repr(archive_path))
            self.assertGreaterEqual(archive.block_count, 1100,
                                    repr(sorted(archive._asset_names)[:5]))
            self.assertEqual(directory.block_count, archive.block_count)
            for name, props in (
                ("minecraft:stone", {}),
                ("minecraft:oak_stairs", {"facing": "east", "half": "top",
                 "shape": "outer_left", "waterlogged": "false"}),
            ):
                with self.subTest(block=name):
                    expected = directory.resolve(name, props, (1, 2, 3))
                    actual = archive.resolve(name, props, (1, 2, 3))
                    self.assertEqual("ok", expected.status, expected.detail)
                    self.assertEqual(expected, actual)
        finally:
            archive.close()
            directory.close()

    def test_representative_states(self):
        states = [
            ("minecraft:oak_stairs", {
                "facing": "east", "half": "top",
                "shape": "outer_left", "waterlogged": "false",
            }),
            ("minecraft:oak_fence_gate", {
                "facing": "south", "in_wall": "false",
                "open": "true", "powered": "false",
            }),
            ("minecraft:lever", {
                "face": "wall", "facing": "west", "powered": "true",
            }),
            ("minecraft:redstone_wire", {
                "east": "side", "north": "none", "power": "12",
                "south": "up", "west": "none",
            }),
        ]
        for name, props in states:
            with self.subTest(name=name, props=props):
                result = self.loader.resolve(name, props, (1, 2, 3))
                self.assertEqual("ok", result.status, result.detail)
                self.assertTrue(result.faces)

    def test_grass_and_coral_are_closed_printable_solids(self):
        states = [
            ("minecraft:short_grass", {}),
            ("minecraft:fern", {}),
            ("minecraft:tube_coral_fan", {}),
            ("minecraft:tube_coral_wall_fan", {"facing": "east"}),
        ]
        for name, props in states:
            with self.subTest(name=name):
                result = self.loader.resolve(name, props, closed=True)
                self.assertEqual("ok", result.status, result.detail)
                self.assertEqual(0, len(result.faces) % 6)
                solid = manifold_from_closed_faces(list(result.faces))
                self.assertEqual(m3d.Error.NoError, solid.status())
                self.assertGreater(solid.volume(), 0)

    def test_straight_stairs_follow_all_four_facings(self):
        expected_upper_bounds = {
            "east": (0.5, 1.0, 0.0, 1.0),
            "west": (0.0, 0.5, 0.0, 1.0),
            "south": (0.0, 1.0, 0.5, 1.0),
            "north": (0.0, 1.0, 0.0, 0.5),
        }
        for half in ("bottom", "top"):
            for facing, expected in expected_upper_bounds.items():
                with self.subTest(half=half, facing=facing):
                    result = self.loader.resolve(
                        "minecraft:oak_stairs",
                        {
                            "facing": facing,
                            "half": half,
                            "shape": "straight",
                            "waterlogged": "false",
                        },
                        closed=True,
                    )
                    self.assertEqual("ok", result.status, result.detail)
                    if half == "bottom":
                        vertices = [
                            vertex for face in result.faces
                            for vertex in face.vertices if vertex.y > 0.51
                        ]
                    else:
                        vertices = [
                            vertex for face in result.faces
                            for vertex in face.vertices if vertex.y < 0.49
                        ]
                    bounds = (
                        round(min(v.x for v in vertices), 6),
                        round(max(v.x for v in vertices), 6),
                        round(min(v.z for v in vertices), 6),
                        round(max(v.z for v in vertices), 6),
                    )
                    self.assertEqual(expected, bounds)

    def test_every_stair_shape_rotates_from_east_clockwise(self):
        rotations = {"east": 0, "south": -90, "west": -180, "north": -270}
        for half in ("bottom", "top"):
            for shape in (
                "straight", "inner_left", "inner_right",
                "outer_left", "outer_right",
            ):
                base = self.loader.resolve(
                    "minecraft:oak_stairs",
                    {
                        "facing": "east", "half": half, "shape": shape,
                        "waterlogged": "false",
                    },
                    closed=True,
                )
                for facing, angle in rotations.items():
                    with self.subTest(
                        half=half, shape=shape, facing=facing
                    ):
                        result = self.loader.resolve(
                            "minecraft:oak_stairs",
                            {
                                "facing": facing, "half": half,
                                "shape": shape, "waterlogged": "false",
                            },
                            closed=True,
                        )
                        expected = Counter(
                            (
                                round(vertex.x, 6),
                                round(vertex.y, 6),
                                round(vertex.z, 6),
                            )
                            for face in base.faces
                            for vertex in (
                                _rotate_vertex(v, "y", angle)
                                for v in face.vertices
                            )
                        )
                        actual = Counter(
                            (
                                round(vertex.x, 6),
                                round(vertex.y, 6),
                                round(vertex.z, 6),
                            )
                            for face in result.faces
                            for vertex in face.vertices
                        )
                        self.assertEqual(expected, actual)
