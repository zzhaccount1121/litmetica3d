import pathlib
import json
import io
import tempfile
import unittest

from PIL import Image

from litmetica3d.exporters.obj import OBJExporter
from litmetica3d.entity_models import get_entity_geometry
from litmetica3d.mesh import Mesh
from litmetica3d.model_loader import ModelLoader, _face_uvs
from litmetica3d.visual_mesh import (
    CompactVisualMesh, export_compact_obj, export_compact_stl,
)


ASSETS = (
    pathlib.Path(__file__).parents[1]
    / "litmetica3d" / "mc_assets" / "26.2"
)


class VisualTextureTests(unittest.TestCase):
    def setUp(self):
        self.loader = ModelLoader(ASSETS, visual_textures=True)

    def tearDown(self):
        self.loader.close()

    def test_regular_block_has_uv_and_texture(self):
        result = self.loader.resolve("minecraft:stone", {})
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.faces), 6)
        self.assertTrue(all(face.texture for face in result.faces))
        self.assertTrue(all(len(face.uvs) == 4 for face in result.faces))

    def test_all_vertical_cube_faces_keep_texture_top_at_world_top(self):
        model = {
            "textures": {"all": "minecraft:block/stone"},
            "elements": [{
                "from": [0, 0, 0], "to": [16, 16, 16],
                "faces": {
                    direction: {"texture": "#all", "uv": [0, 0, 16, 16]}
                    for direction in (
                        "down", "up", "north", "south", "west", "east"
                    )
                },
            }],
        }
        faces = self.loader._model_to_faces(
            model, "minecraft:test", properties={}
        )
        side_faces = [
            face for face in faces if abs(face.normal.y) < 0.5
        ]
        self.assertEqual(4, len(side_faces))
        for face in side_faces:
            top_v = [
                uv[1] for vertex, uv in zip(face.vertices, face.uvs)
                if abs(vertex.y - max(v.y for v in face.vertices)) < 1e-8
            ]
            bottom_v = [
                uv[1] for vertex, uv in zip(face.vertices, face.uvs)
                if abs(vertex.y - min(v.y for v in face.vertices)) < 1e-8
            ]
            self.assertGreater(min(top_v), max(bottom_v))

    def test_cube_face_uv_order_matches_minecraft_faceinfo(self):
        rect = [0, 0, 16, 16]
        tl, bl, tr, br = (0, 1), (0, 0), (1, 1), (1, 0)
        self.assertEqual([tl, bl, br, tr], _face_uvs(rect, 0, "up"))
        self.assertEqual([bl, br, tr, tl], _face_uvs(rect, 0, "down"))
        self.assertEqual([br, tr, tl, bl], _face_uvs(rect, 0, "north"))
        self.assertEqual([bl, br, tr, tl], _face_uvs(rect, 0, "south"))
        self.assertEqual([bl, br, tr, tl], _face_uvs(rect, 0, "west"))
        self.assertEqual([br, tr, tl, bl], _face_uvs(rect, 0, "east"))

    def test_transparent_sheet_top_left_pixel_follows_each_face_direction(self):
        image = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        image.putpixel((0, 0), (255, 255, 255, 255))
        output = io.BytesIO()
        image.save(output, format="PNG")
        texture = "generated:test/top_left"
        self.loader._generated_textures[texture] = output.getvalue()
        cases = {
            "south": ([0, 0, 8], [16, 16, 8], (0.125, 0.875, 0.5)),
            "north": ([0, 0, 8], [16, 16, 8], (0.875, 0.875, 0.5)),
            "east": ([8, 0, 0], [8, 16, 16], (0.5, 0.875, 0.875)),
            "west": ([8, 0, 0], [8, 16, 16], (0.5, 0.875, 0.125)),
            "up": ([0, 8, 0], [16, 8, 16], (0.125, 0.5, 0.125)),
            "down": ([0, 8, 0], [16, 8, 16], (0.125, 0.5, 0.875)),
        }
        for direction, (start, end, expected) in cases.items():
            model = {
                "textures": {"sheet": texture},
                "elements": [{
                    "from": start, "to": end,
                    "faces": {
                        direction: {
                            "texture": "#sheet", "uv": [0, 0, 16, 16]
                        }
                    },
                }],
            }
            faces = self.loader._model_to_faces(
                model, "minecraft:test", properties={}
            )
            vertices = [vertex for face in faces for vertex in face.vertices]
            center = (
                (min(v.x for v in vertices) + max(v.x for v in vertices)) / 2,
                (min(v.y for v in vertices) + max(v.y for v in vertices)) / 2,
                (min(v.z for v in vertices) + max(v.z for v in vertices)) / 2,
            )
            for actual, wanted in zip(center, expected):
                self.assertAlmostEqual(actual, wanted, places=6, msg=direction)

    def test_animated_lantern_exports_only_first_frame(self):
        original_path = (
            ASSETS / "assets" / "minecraft" / "textures"
            / "block" / "lantern.png"
        )
        with Image.open(original_path) as original:
            self.assertEqual((16, 48), original.size)
        exported = self.loader.texture_bytes("minecraft:block/lantern")
        with Image.open(io.BytesIO(exported)) as image:
            self.assertEqual((16, 16), image.size)

    def test_every_animated_texture_exports_one_valid_frame(self):
        texture_root = ASSETS / "assets"
        metadata_files = list(texture_root.rglob("*.png.mcmeta"))
        checked = 0
        for metadata_path in metadata_files:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(data.get("animation"), dict):
                continue
            relative = metadata_path.relative_to(texture_root).as_posix()
            namespace, rest = relative.split("/", 1)
            texture_name = rest.removeprefix("textures/").removesuffix(
                ".png.mcmeta"
            )
            raw = self.loader.texture_bytes(f"{namespace}:{texture_name}")
            self.assertIsNotNone(raw, relative)
            with Image.open(io.BytesIO(raw)) as frame:
                animation = data["animation"]
                expected_width = int(animation.get("width", frame.width))
                expected_height = int(
                    animation.get("height", expected_width)
                )
                self.assertEqual(
                    (expected_width, expected_height), frame.size, relative
                )
            checked += 1
        self.assertEqual(54, checked)

    def test_transparent_plant_pixels_are_removed_and_closed(self):
        result = self.loader.resolve("minecraft:short_grass", {})
        self.assertEqual(result.status, "ok")
        self.assertGreater(len(result.faces), 0)
        # Two fully opaque 16x16 crossed sheets would have at least 1024
        # front/back faces. The vanilla alpha mask must remove pixels.
        self.assertLess(len(result.faces), 1024)
        self.assertTrue(all(face.texture for face in result.faces))

    def test_reversed_uv_cutouts_are_not_dropped(self):
        # Vanilla coral fans use horizontally and vertically reversed UV
        # rectangles.  These are texture flips, not empty rectangles.
        result = self.loader.resolve("minecraft:tube_coral_fan", {})
        self.assertEqual(result.status, "ok")
        self.assertGreater(len(result.faces), 0)
        quadrants = {
            (
                min(vertex.x for vertex in face.vertices) < 0.5,
                min(vertex.z for vertex in face.vertices) < 0.5,
            )
            for face in result.faces
        }
        self.assertEqual(len(quadrants), 4)

    def test_coral_fan_face_rotation_moves_alpha_mask_not_element(self):
        model_path = (
            ASSETS / "assets" / "minecraft" / "models"
            / "block" / "coral_fan.json"
        )
        model = json.loads(model_path.read_text(encoding="utf-8"))
        model["textures"]["fan"] = "minecraft:block/tube_coral_fan"
        first = dict(model)
        first["elements"] = [model["elements"][0]]
        faces = self.loader._model_to_faces(
            first, "minecraft:tube_coral_fan", properties={}
        )
        vertices = [vertex for face in faces for vertex in face.vertices]
        x_span = max(v.x for v in vertices) - min(v.x for v in vertices)
        z_span = max(v.z for v in vertices) - min(v.z for v in vertices)
        # rotation=90 is clockwise in Minecraft.  The source element extends
        # beyond +X so its transparent half can rotate while the visible fan
        # remains close to the owning block instead of being pushed outward.
        self.assertLess(x_span, 0.6)
        self.assertGreater(z_span, 0.9)
        self.assertLessEqual(max(v.x for v in vertices), 1.04)

    def test_reversed_uv_solid_face_keeps_lightning_rod_top_closed(self):
        result = self.loader.resolve("minecraft:lightning_rod", {
            "facing": "up", "powered": "false", "waterlogged": "false",
        })
        self.assertEqual(result.status, "ok")
        top_faces = [
            face for face in result.faces
            if all(abs(vertex.y - 1.0) < 1e-8 for vertex in face.vertices)
        ]
        self.assertTrue(top_faces)

    def test_alpha_shell_preserves_opaque_faces_on_same_element(self):
        model = {
            "textures": {
                "cutout": "minecraft:block/glass",
                "solid": "minecraft:block/stone",
            },
            "elements": [{
                "from": [0, 0, 0],
                "to": [16, 16, 16],
                "faces": {
                    "north": {"texture": "#cutout"},
                    "up": {"texture": "#solid"},
                },
            }],
        }
        faces = self.loader._model_to_faces(
            model, "minecraft:test", properties={}
        )
        self.assertTrue(any(
            all(abs(vertex.y - 1.0) < 1e-8 for vertex in face.vertices)
            and face.texture == "minecraft:block/stone"
            for face in faces
        ))

    def test_obj_writes_uv_mtl_and_png(self):
        result = self.loader.resolve("minecraft:stone", {})
        mesh = Mesh()
        for face in result.faces:
            mesh.add_face(face)
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "stone.obj"
            OBJExporter(
                texture_provider=self.loader.texture_bytes
            ).export(mesh, output)
            text = output.read_text(encoding="utf-8")
            self.assertIn("\nvt ", text)
            self.assertIn("/", next(
                line for line in text.splitlines() if line.startswith("f ")
            ))
            self.assertTrue(output.with_suffix(".mtl").exists())
            self.assertTrue(any(
                (pathlib.Path(tmp) / "stone_textures").glob("*.png")
            ))

    def test_redstone_power_uses_cutout_and_distinct_tints(self):
        textures = []
        for power in ("0", "7", "15"):
            result = self.loader.resolve("minecraft:redstone_wire", {
                "power": power, "north": "none", "south": "none",
                "east": "none", "west": "none",
            })
            self.assertEqual(result.status, "ok")
            self.assertLess(len(result.faces), 6 * 16 * 16)
            textures.append(next(iter({
                face.texture for face in result.faces if face.texture
            })))
        self.assertEqual(len(set(textures)), 3)

    def test_vine_is_tinted_not_raw_grayscale(self):
        result = self.loader.resolve("minecraft:vine", {
            "north": "true", "south": "false", "east": "false",
            "west": "false", "up": "false",
        })
        texture = next(face.texture for face in result.faces if face.texture)
        self.assertTrue(texture.startswith("generated:tint/"))

    def test_clear_glass_is_cutout_but_stained_glass_remains_cube(self):
        clear = self.loader.resolve("minecraft:glass", {})
        stained = self.loader.resolve("minecraft:white_stained_glass", {})
        self.assertEqual(clear.status, "ok")
        self.assertGreater(len(clear.faces), 6)
        self.assertEqual(len(stained.faces), 6)

    def test_block_entities_receive_entity_textures(self):
        cases = [
            ("minecraft:chest", {"type": "single", "facing": "north"}),
            ("minecraft:red_shulker_box", {"facing": "up"}),
            ("minecraft:skeleton_skull", {"rotation": "0"}),
        ]
        for name, properties in cases:
            faces = get_entity_geometry(name, properties, visual=True)
            self.assertTrue(faces)
            self.assertTrue(all(face.texture for face in faces))

    def test_block_entity_side_uvs_keep_atlas_top_at_world_top(self):
        cases = [
            # Chests use their renderer's positive-Y ModelPart UV net instead
            # of the generic inverted-Y entity net; covered in test_chest_uv.
            # Shulker inner/rim faces stretch edge pixels in either direction;
            # their original UV projection is checked in test_shulker_visual.
            ("minecraft:white_banner", {"rotation": "0"}),
            ("minecraft:skeleton_skull", {"rotation": "0"}),
        ]
        checked = 0
        for name, properties in cases:
            for face in get_entity_geometry(name, properties, visual=True):
                if not face.uvs or abs(face.normal.y) >= 0.5:
                    continue
                top_y = max(vertex.y for vertex in face.vertices)
                bottom_y = min(vertex.y for vertex in face.vertices)
                if abs(top_y - bottom_y) < 1e-8:
                    continue
                top_v = [
                    uv[1] for vertex, uv in zip(face.vertices, face.uvs)
                    if abs(vertex.y - top_y) < 1e-8
                ]
                bottom_v = [
                    uv[1] for vertex, uv in zip(face.vertices, face.uvs)
                    if abs(vertex.y - bottom_y) < 1e-8
                ]
                self.assertGreater(min(top_v), max(bottom_v), name)
                checked += 1
        self.assertEqual(checked, 16)  # 12 banner side faces + 4 skull side faces

    def test_translucent_obj_writes_alpha_map(self):
        result = self.loader.resolve("minecraft:white_stained_glass", {})
        mesh = Mesh()
        for face in result.faces:
            mesh.add_face(face)
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "glass.obj"
            OBJExporter(
                texture_provider=self.loader.texture_bytes,
                alpha_provider=self.loader.texture_alpha_bytes,
            ).export(mesh, output)
            mtl = output.with_suffix(".mtl").read_text(encoding="utf-8")
            self.assertIn("map_d ", mtl)

    def test_compact_visual_exporters_share_triangle_count(self):
        result = self.loader.resolve("minecraft:glass", {})
        mesh = CompactVisualMesh(chunk_size=16)
        for face in result.faces:
            mesh.add_face(face)
        mesh.transform(1.0, False)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            obj = root / "glass.obj"
            stl = root / "glass.stl"
            export_compact_obj(
                mesh, obj, self.loader.texture_bytes,
                self.loader.texture_alpha_bytes,
            )
            export_compact_stl(mesh, stl)
            with stl.open("rb") as stream:
                stream.seek(80)
                count = int.from_bytes(stream.read(4), "little")
            obj_faces = sum(
                len(line.split()) - 3
                for line in obj.read_text(encoding="utf-8").splitlines()
                if line.startswith("f ")
            )
            self.assertEqual(count, mesh.triangle_count)
            self.assertEqual(obj_faces, mesh.triangle_count)


if __name__ == "__main__":
    unittest.main()
