import json
import pathlib
import tempfile

from PIL import Image

from litmetica3d.block_models import Face, Vec3
from litmetica3d.emission import (
    block_light_level, emission_profile, resolve_override,
)
from litmetica3d.model_loader import ModelLoader
from litmetica3d.conversion import (
    _emission_enabled,
    _cluster_editable_lights,
    _uses_editable_blender_lights,
)
from litmetica3d.visual_mesh import CompactVisualMesh, export_compact_obj


ASSETS = (
    pathlib.Path(__file__).parents[1]
    / "litmetica3d" / "mc_assets" / "26.2"
)


def test_state_aware_light_levels():
    assert block_light_level("minecraft:redstone_lamp", {"lit": "false"}) == 0
    assert block_light_level("minecraft:redstone_lamp", {"lit": "true"}) == 15
    assert block_light_level("minecraft:redstone_wire", {"power": "0"}) == 0
    assert block_light_level("minecraft:redstone_wire", {"power": "15"}) == 7
    assert block_light_level(
        "minecraft:white_candle", {"lit": "true", "candles": "4"}
    ) == 12


def test_coordinate_override_wins():
    config = {
        "global_multiplier": 0.5,
        "rules": [
            {"block": "minecraft:redstone_lamp", "multiplier": 2},
            {"region": "R", "position": [1, 2, 3], "multiplier": 4},
        ],
    }
    multiplier, _ = resolve_override(
        config, "minecraft:redstone_lamp", {"lit": "true"}, "R", (1, 2, 3)
    )
    assert multiplier == 4


def test_lantern_mask_does_not_emit_every_pixel():
    loader = ModelLoader(ASSETS, visual_textures=True)
    try:
        texture = "minecraft:block/lantern"
        key = loader.emission_texture(texture, "warm")
        assert key is not None
        image = Image.open(
            __import__("io").BytesIO(loader.texture_bytes(key))
        ).convert("RGBA")
        from litmetica3d.model_loader import _rgba_pixels
        pixels = list(_rgba_pixels(image))
        lit = sum(1 for r, g, b, _ in pixels if r or g or b)
        assert 0 < lit < len(pixels)
    finally:
        loader.close()


def test_obj_exports_blender_emission_and_editable_light():
    loader = ModelLoader(ASSETS, visual_textures=True)
    try:
        mask = loader.emission_texture("minecraft:block/lantern", "warm")
        mesh = CompactVisualMesh()
        mesh.add_face(Face(
            [Vec3(), Vec3(1, 0, 0), Vec3(1, 1, 0), Vec3(0, 1, 0)],
            Vec3(0, 0, -1),
            "texture:minecraft:block/lantern",
            [(0, 0), (1, 0), (1, 1), (0, 1)],
            "minecraft:block/lantern",
            mask,
            15,
        ))
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp) / "lamp.obj"
            export_compact_obj(
                mesh, output,
                texture_provider=loader.texture_bytes,
                emission_provider=loader.texture_bytes,
                light_sources=[{
                    "name": "MC lantern [0,0,0]",
                    "block": "minecraft:lantern",
                    "position": [0.5, 0.5, 0.5],
                    "block_position": [0, 0, 0],
                    "level": 15,
                    "color": [1, 0.5, 0.2],
                }],
            )
            assert "map_Ke" in output.with_suffix(".mtl").read_text()
            manifest = json.loads(
                (pathlib.Path(temp) / "lamp.blender_emission.json").read_text()
            )
            assert manifest["materials"][0]["source_level"] == 15
            assert manifest["lights"][0]["block"] == "minecraft:lantern"
            script = pathlib.Path(temp) / "lamp_blender_setup.py"
            text = script.read_text(encoding="utf-8")
            assert 'collection = bpy.data.collections.get("Minecraft Lights")' in text
            assert "light.energy" in text
            assert "DATA = json.loads(" in text
            assert "resolve_asset(rel)" in text
            assert "read_text(encoding=" not in text
            assert "OUTPUT_ROOT_HINT" in text
            assert 'getattr(scene, "node_tree", None)' in text
            assert "axis_conversion(" in text
            assert "to_blender_position(position)" in text
            assert '"minecraft:coordinate_version"' in text
            compile(text, str(script), "exec")
    finally:
        loader.close()


def test_explicit_model_emission_profile():
    assert emission_profile(
        "minecraft:firefly_bush",
        "minecraft:block/firefly_bush_emissive",
        {},
        explicit_level=15,
    ) == (15, "full")


def test_adjacent_editable_lights_can_be_clustered():
    def source(x):
        return {
            "name": f"L{x}",
            "block": "minecraft:redstone_lamp",
            "block_position": [x, 0, 0],
            "position": [x + 0.5, 0.5, 0.5],
            "level": 15,
            "color": [1.0, 0.4, 0.1],
            "power": 120.0,
            "radius": 0.14,
        }

    clustered = _cluster_editable_lights([source(0), source(1), source(4)])
    assert len(clustered) == 2
    merged = next(item for item in clustered if item.get("block_count") == 2)
    assert merged["power"] == 240


def test_material_emission_does_not_create_blender_lights():
    assert not _uses_editable_blender_lights("material")
    assert not _uses_editable_blender_lights("off")
    assert _uses_editable_blender_lights("exact")
    assert _uses_editable_blender_lights("clustered")


def test_none_mode_disables_all_emission():
    assert not _emission_enabled(True, "none")
    assert not _emission_enabled(False, "exact")
    assert _emission_enabled(True, "material")
    assert _emission_enabled(True, "exact")
