"""Shared conversion pipeline used by both CLI and GUI."""

from __future__ import annotations

import json
import math
import pathlib
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from .block_models import AIR_BLOCKS, Face, Vec3, _cuboid
from .exporters.obj import OBJExporter
from .exporters.stl import STLExporter
from .exporters.array_mesh import export_obj_arrays, export_stl_arrays
from .entity_models import get_entity_geometry
from .emission import (
    block_light_level, default_light_color, emission_profile,
    load_overrides, resolve_override,
)
from .litematic import BlockState, load_schematic
from .mesh import Mesh
from .visual_mesh import (
    CompactVisualMesh, export_compact_obj, export_compact_stl,
)
from .model_loader import ModelLoader, ModelResult
from .solid import (
    BooleanCancelled, SolidReport, cube_solid, face_geometry_key,
    is_unit_cube_faces,
    greedy_cube_boxes, split_box_by_chunks,
    manifold_from_closed_faces, manifold_to_mesh, materialize_manifold,
    process_components_and_cavities, union_balanced,
    validate_manifold, voxel32_fallback,
)

Progress = Callable[[str, float, str], None]
WATER_PLANTS = {
    "minecraft:kelp", "minecraft:kelp_plant",
    "minecraft:seagrass", "minecraft:tall_seagrass",
}
DYE_COLORS = {
    "white": (249, 255, 254), "orange": (249, 128, 29),
    "magenta": (199, 78, 189), "light_blue": (58, 179, 218),
    "yellow": (254, 216, 61), "lime": (128, 199, 31),
    "pink": (243, 139, 170), "gray": (71, 79, 82),
    "light_gray": (157, 157, 151), "cyan": (22, 156, 156),
    "purple": (137, 50, 184), "blue": (60, 68, 170),
    "brown": (131, 84, 50), "green": (94, 124, 22),
    "red": (176, 46, 38), "black": (29, 29, 33),
}


def _editable_light_position(
    block_name: str,
    position: tuple[int, int, int],
    occupied: set[tuple[int, int, int]],
) -> list[float]:
    """Place helper lights where closed luminous cubes cannot trap them."""
    base = block_name.split(":", 1)[-1]
    closed_sources = {
        "glowstone", "sea_lantern", "shroomlight", "redstone_lamp",
        "ochre_froglight", "pearlescent_froglight", "verdant_froglight",
        "magma_block", "crying_obsidian", "copper_bulb",
        "exposed_copper_bulb", "weathered_copper_bulb",
        "oxidized_copper_bulb",
    }
    center = [float(value) + 0.5 for value in position]
    if base not in closed_sources and "copper_bulb" not in base:
        return center
    for dx, dy, dz in (
        (0, 1, 0), (0, 0, -1), (0, 0, 1),
        (1, 0, 0), (-1, 0, 0), (0, -1, 0),
    ):
        neighbor = (
            position[0] + dx, position[1] + dy, position[2] + dz
        )
        if neighbor not in occupied:
            return [
                center[0] + dx * 0.56,
                center[1] + dy * 0.56,
                center[2] + dz * 0.56,
            ]
    return center


def _uses_editable_blender_lights(mode: str) -> bool:
    """Material mode emits through Cycles without creating Light objects."""
    return mode in {"exact", "clustered"}


def _emission_enabled(enabled: bool, mode: str) -> bool:
    """The explicit none mode always disables masks and helper lights."""
    return enabled and mode != "none"


def _cluster_editable_lights(sources: list[dict]) -> list[dict]:
    """Merge directly adjacent, equivalent sources for large Cycles scenes."""
    groups: dict[tuple, list[dict]] = {}
    for source in sources:
        key = (
            source["block"],
            round(float(source["level"]), 4),
            tuple(round(float(v), 4) for v in source["color"]),
        )
        groups.setdefault(key, []).append(source)
    result = []
    for members in groups.values():
        by_position = {
            tuple(item["block_position"]): item for item in members
        }
        remaining = set(by_position)
        for seed in sorted(by_position):
            if seed not in remaining:
                continue
            remaining.remove(seed)
            stack = [seed]
            component = []
            while stack:
                current = stack.pop()
                component.append(by_position[current])
                for axis in range(3):
                    for delta in (-1, 1):
                        neighbor = list(current)
                        neighbor[axis] += delta
                        neighbor = tuple(neighbor)
                        if neighbor in remaining:
                            remaining.remove(neighbor)
                            stack.append(neighbor)
            if len(component) == 1:
                result.append(component[0])
                continue
            merged = dict(component[0])
            merged["name"] = (
                f"MC Cluster {merged['block'].split(':', 1)[-1]} "
                f"[{len(component)} blocks]"
            )
            merged["position"] = [
                sum(item["position"][axis] for item in component)
                / len(component)
                for axis in range(3)
            ]
            merged["power"] = sum(item["power"] for item in component)
            merged["radius"] = max(
                merged["radius"], len(component) ** (1 / 3) * 0.2
            )
            merged["block_position"] = component[0]["block_position"]
            merged["block_count"] = len(component)
            result.append(merged)
    return result
DYE_ORDER = tuple(DYE_COLORS)
BANNER_PATTERNS = {
    "b": "base", "bs": "stripe_bottom", "ts": "stripe_top",
    "ls": "stripe_left", "rs": "stripe_right", "cs": "stripe_center",
    "ms": "stripe_middle", "drs": "stripe_downright",
    "dls": "stripe_downleft", "ss": "small_stripes",
    "cr": "straight_cross", "sc": "cross", "ld": "diagonal_left",
    "rud": "diagonal_up_right", "lud": "diagonal_up_left",
    "rd": "diagonal_right", "vh": "half_vertical",
    "vhr": "half_vertical_right", "hh": "half_horizontal",
    "hhb": "half_horizontal_bottom", "bl": "square_bottom_left",
    "br": "square_bottom_right", "tl": "square_top_left",
    "tr": "square_top_right", "bt": "triangle_bottom",
    "tt": "triangle_top", "bts": "triangles_bottom",
    "tts": "triangles_top", "mc": "circle", "mr": "rhombus",
    "bo": "border", "cbo": "curly_border", "bri": "bricks",
    "gra": "gradient", "gru": "gradient_up", "cre": "creeper",
    "sku": "skull", "flo": "flower", "moj": "mojang",
    "glb": "globe", "pig": "piglin", "flow": "flow",
    "guster": "guster",
}


def _banner_texture(
    loader: ModelLoader, block_name: str, tile_entity: dict | None = None
) -> str | None:
    base = block_name.split(":", 1)[-1]
    suffix = "_wall_banner" if base.endswith("_wall_banner") else "_banner"
    if not base.endswith(suffix):
        return None
    color_name = base[:-len(suffix)]
    color = DYE_COLORS.get(color_name)
    if color is None:
        return "minecraft:entity/banner/banner_base"
    pattern_layers = []
    for item in (tile_entity or {}).get("Patterns", []):
        pattern = BANNER_PATTERNS.get(str(item.get("Pattern", "")))
        try:
            dye = DYE_ORDER[int(item.get("Color", 0))]
        except (ValueError, TypeError, IndexError):
            continue
        if pattern:
            pattern_layers.append((pattern, DYE_COLORS[dye]))
    if not pattern_layers:
        return loader.tint_texture(
            "minecraft:entity/banner/banner_base", color,
            label=f"banner_{color_name}",
        )
    signature = "_".join(
        f"{name}-{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
        for name, rgb in pattern_layers
    )
    return loader.compose_banner_texture(
        color, pattern_layers, f"{color_name}_{signature}"
    )


class ConversionCancelled(Exception):
    pass


@dataclass
class ConversionOptions:
    input_path: pathlib.Path
    output_path: pathlib.Path
    asset_path: pathlib.Path | None = None
    output_format: str = "stl"
    stl_binary: bool = True
    scale: float = 1.0
    center: bool = False
    water: str = "cube"
    fallback: str = "cube"
    optimize: str = "safe"
    # Compatibility field for older callers. There are no user-selectable
    # levels: print uses solid union and visual exports indexed OBJ automatically.
    minimum_thickness: float = 1 / 16
    regions: tuple[str, ...] = ()
    color: bool = False
    textures: bool = True
    seamless_glass: bool = False
    solid_textures: bool = False
    geometry: str = "print"
    components: str = "keep"
    min_component_volume: float = 1 / 4096
    cavities: str = "preserve"
    boolean_fallback: str = "voxel32"
    emission: bool = True
    emission_strength: float = 1.0
    emission_config: pathlib.Path | None = None
    blender_lights: str = "exact"
    save_report: bool = False


def validate_options(options: ConversionOptions) -> None:
    """Shared CLI/API/desktop validation, before opening input or output."""
    for name in ("scale", "minimum_thickness", "min_component_volume", "emission_strength"):
        value = getattr(options, name)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} 必须是有限数值")
        if value < 0 or (name in {"scale", "minimum_thickness"} and value == 0):
            raise ValueError(f"{name} 必须{'大于' if name in {'scale', 'minimum_thickness'} else '不小于'} 0")
    choices = {
        "output_format": {"stl", "obj"}, "geometry": {"print", "visual"},
        "water": {"cube", "drop", "level"}, "fallback": {"cube", "ignore"},
        "components": {"keep", "remove-small", "main"}, "cavities": {"preserve", "fill"},
        "boolean_fallback": {"voxel32", "fail"},
        "blender_lights": {"none", "off", "material", "exact", "clustered"},
    }
    for name, allowed in choices.items():
        if getattr(options, name) not in allowed:
            raise ValueError(f"无效参数 {name}: {getattr(options, name)}")


@dataclass
class FallbackEvent:
    category: str
    block: str
    properties: dict[str, str]
    region: str
    position: tuple[int, int, int]
    action: str
    detail: str = ""


@dataclass
class ConversionReport:
    input_path: str
    output_path: str
    asset_version: str = "26.2"
    asset_block_count: int = 0
    water_mode: str = "cube"
    fallback_mode: str = "cube"
    optimize_mode: str = "safe"
    coordinate_origin: tuple[int, int, int] = (0, 0, 0)
    emission_rule_coordinates: str = "schematic"
    source_blocks: int = 0
    rendered_blocks: int = 0
    entity_model_blocks: int = 0
    intentionally_invisible: int = 0
    water_skipped: int = 0
    unknown_blocks: int = 0
    unknown_states: int = 0
    missing_models: int = 0
    model_errors: int = 0
    fallback_cubes: int = 0
    ignored: int = 0
    vertices: int = 0
    triangles: int = 0
    geometry_mode: str = "print"
    seamless_glass: bool = False
    seamless_glass_blocks: int = 0
    solid_textures: bool = False
    geometry_pipeline: str = "v0.2-print"
    component_mode: str = "keep"
    cavity_mode: str = "preserve"
    solid: SolidReport | None = None
    fallback_summary: dict[str, int] = field(default_factory=dict)
    events: list[FallbackEvent] = field(default_factory=list)
    transparent_pixels_removed: int = 0
    tinted_textures: int = 0
    entity_textured_blocks: int = 0
    entity_texture_fallbacks: int = 0
    emissive_blocks: int = 0
    emissive_materials: int = 0
    blender_lights: int = 0
    emission_mode: str = "none"
    visual_optimization: dict = field(default_factory=dict)

    def record(
        self, result: ModelResult, state: BlockState, region: str,
        position: tuple[int, int, int], action: str,
    ) -> None:
        field_name = {
            "unknown_block": "unknown_blocks",
            "unknown_state": "unknown_states",
            "missing_model": "missing_models",
            "model_error": "model_errors",
        }[result.status]
        setattr(self, field_name, getattr(self, field_name) + 1)
        if action == "cube":
            self.fallback_cubes += 1
        else:
            self.ignored += 1
        summary_key = f"{result.status}|{state.name}|{action}"
        self.fallback_summary[summary_key] = (
            self.fallback_summary.get(summary_key, 0) + 1
        )
        self.events.append(FallbackEvent(
            result.status, state.name, dict(state.properties),
            region, position, action, result.detail,
        ))

    def save(self, path: pathlib.Path) -> None:
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def bundled_asset_path() -> pathlib.Path:
    base = pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(__file__).parent))
    candidates = [
        base / "mc_assets" / "26.2.zip",
        base / "litmetica3d" / "mc_assets" / "26.2.zip",
        base / "mc_assets" / "26.2",
        base / "litmetica3d" / "mc_assets" / "26.2",
        pathlib.Path(__file__).parent / "mc_assets" / "26.2.zip",
        pathlib.Path(__file__).parent / "mc_assets" / "26.2",
        pathlib.Path(__file__).parent / "mc_assets" / "26.2-Fabric.jar",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("未找到内置 Minecraft 26.2 模型资源")


def _water_height(properties: dict[str, str]) -> float:
    try:
        level = int(properties.get("level", "0"))
    except ValueError:
        level = 0
    if level >= 8:
        return 1.0
    return 1.0 if level == 0 else max(1 / 8, (8 - level) / 8)


def _normal(vertices: list[Vec3]) -> Vec3:
    a, b, c = vertices[:3]
    ux, uy, uz = b.x - a.x, b.y - a.y, b.z - a.z
    vx, vy, vz = c.x - a.x, c.y - a.y, c.z - a.z
    x, y, z = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = (x*x + y*y + z*z) ** 0.5 or 1.0
    return Vec3(x / length, y / length, z / length)


def _water_faces(
    pos: tuple[int, int, int],
    heights: dict[tuple[int, int, int], float],
) -> list[Face]:
    x, y, z = pos
    current = heights[pos]
    corners = []
    for dx, dz in ((0, 0), (0, 1), (1, 1), (1, 0)):
        values = [current]
        for ox in (dx - 1, dx):
            for oz in (dz - 1, dz):
                value = heights.get((x + ox, y, z + oz))
                if value is not None:
                    values.append(value)
        corners.append(max(values))
    p = [
        Vec3(0, corners[0], 0), Vec3(0, corners[1], 1),
        Vec3(1, corners[2], 1), Vec3(1, corners[3], 0),
    ]
    b = [Vec3(0, 0, 0), Vec3(0, 0, 1), Vec3(1, 0, 1), Vec3(1, 0, 0)]
    material = "minecraft:water"
    return [
        Face(p, _normal(p), material),
        Face([b[0], b[3], b[2], b[1]], Vec3(0, -1, 0), material),
        Face([b[0], p[0], p[3], b[3]], Vec3(0, 0, -1), material),
        Face([b[1], b[2], p[2], p[1]], Vec3(0, 0, 1), material),
        Face([b[0], b[1], p[1], p[0]], Vec3(-1, 0, 0), material),
        Face([b[3], p[3], p[2], b[2]], Vec3(1, 0, 0), material),
    ]


def _transform(mesh: Mesh, scale: float, center: bool) -> None:
    if not mesh.vertices:
        return
    cx = cz = 0.0
    if center:
        xs = [v.x for v in mesh.vertices]
        zs = [v.z for v in mesh.vertices]
        cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    mesh.vertices = [
        Vec3((v.x - cx) * scale, v.y * scale, (v.z - cz) * scale)
        for v in mesh.vertices
    ]


def convert(
    options: ConversionOptions,
    progress: Progress | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ConversionReport:
    validate_options(options)
    progress = progress or (lambda stage, value, text: None)
    cancelled = cancelled or (lambda: False)
    report = ConversionReport(
        str(options.input_path), str(options.output_path),
        water_mode=options.water,
        fallback_mode=options.fallback,
        optimize_mode="automatic",
        geometry_mode=options.geometry,
        seamless_glass=options.geometry == "visual" and options.seamless_glass,
        solid_textures=options.solid_textures,
        geometry_pipeline=(
            "v0.4-visual-emission" if options.geometry == "visual"
            else "v0.2-print"
        ),
        component_mode=options.components,
        cavity_mode=options.cavities,
    )
    progress("load", 0.02, "读取 Litematic...")
    schematic = load_schematic(str(options.input_path))
    asset_path = options.asset_path or bundled_asset_path()
    visual_alpha_geometry = options.geometry == "visual"
    loader = ModelLoader(
        asset_path, options.minimum_thickness,
        visual_textures=visual_alpha_geometry,
        solid_textures=options.solid_textures,
    )
    emission_config = load_overrides(options.emission_config)
    light_sources: list[dict] = []
    emission_materials: set[tuple[str, float]] = set()
    emission_enabled = _emission_enabled(
        options.emission, options.blender_lights
    )
    if options.geometry == "visual" and emission_enabled:
        report.emission_mode = (
            options.blender_lights
            if options.blender_lights in {"exact", "clustered"}
            else "material"
        )
    report.asset_block_count = loader.block_count
    try:
        selected = set(options.regions) if options.regions else set(schematic.regions)
        entries = []
        tile_entities_world: dict[tuple[int, int, int], dict] = {}
        for region_name, region in schematic.regions.items():
            if region_name not in selected:
                continue
            for tile in region.tile_entities:
                try:
                    local_tile = (
                        int(tile["x"]), int(tile["y"]), int(tile["z"])
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                tile_world = tuple(
                    local_tile[i] + region.position[i] for i in range(3)
                )
                tile_entities_world[tile_world] = tile
            for local_pos, palette_index in region.blocks.items():
                state = region.palette[palette_index]
                world = tuple(
                    local_pos[i] + region.position[i] for i in range(3)
                )
                entries.append((region_name, world, state))
        report.source_blocks = len(entries)
        minimum = (0, 0, 0)
        if entries:
            minimum = tuple(min(item[1][i] for item in entries) for i in range(3))
            report.coordinate_origin = minimum
            entries = [
                (region, tuple(pos[i] - minimum[i] for i in range(3)), state)
                for region, pos, state in entries
            ]
            tile_entities = {
                tuple(pos[i] - minimum[i] for i in range(3)): tile
                for pos, tile in tile_entities_world.items()
            }
        else:
            tile_entities = {}
        occupied_positions = {
            pos for _, pos, state in entries if state.name not in AIR_BLOCKS
        } if options.geometry == "visual" and emission_enabled and _uses_editable_blender_lights(options.blender_lights) else set()

        water_heights = {
            pos: (
                1.0 if state.name == "minecraft:bubble_column"
                else _water_height(state.properties)
            )
            for _, pos, state in entries
            if state.name in {"minecraft:water", "minecraft:bubble_column"}
        } if options.water == "level" else {}
        from .glass import GLASS, glass_faces
        glass_neighbors = {}
        if options.geometry == 'visual' and options.seamless_glass:
            for _, pos, state in entries:
                if state.name not in GLASS:
                    continue
                if options.water == 'cube' and state.properties.get('waterlogged') == 'true':
                    continue
                glass_props = dict(state.properties)
                if glass_props.get('waterlogged') == 'true':
                    glass_props['waterlogged'] = 'false'
                if loader.resolve(state.name, glass_props, pos).status == 'ok':
                    glass_neighbors[pos] = state
            progress('geometry', 0.05, f'半透明无缝玻璃：已识别 {len(glass_neighbors)} 个玻璃方块/玻璃板')
        mesh = CompactVisualMesh() if options.geometry == "visual" else None
        solid_chunks: dict[tuple[int, int, int], list] = {}
        cube_positions: set[tuple[int, int, int]] = set()
        local_solid_cache = {}
        solid_report = SolidReport() if options.geometry == "print" else None
        total = max(1, len(entries))
        for index, (region, pos, state) in enumerate(entries):
            if cancelled():
                raise ConversionCancelled()
            if index % 1000 == 0:
                progress("geometry", 0.05 + 0.7 * index / total, "生成方块模型...")
            if state.name in AIR_BLOCKS:
                continue
            props = dict(state.properties)
            is_plant = state.name in WATER_PLANTS
            is_water = state.name in {"minecraft:water", "minecraft:bubble_column"}
            waterlogged = props.get("waterlogged") == "true"
            local_faces: list[Face] = []
            if options.water == "cube" and (
                is_water or waterlogged or is_plant
            ):
                local_faces = _cuboid(0, 0, 0, 1, 1, 1, state.name)
            elif is_water:
                if options.water == "drop":
                    report.water_skipped += 1
                    continue
                local_faces = _water_faces(pos, water_heights)
            else:
                if waterlogged:
                    props["waterlogged"] = "false"
                result = loader.resolve(
                    state.name, props, pos, closed=options.geometry == "print"
                )
                if result.status == "ok":
                    local_faces = result.faces
                else:
                    entity_texture_override = None
                    if options.geometry == "visual":
                        entity_texture_override = _banner_texture(
                            loader, state.name, tile_entities.get(pos)
                        )
                    entity_faces = get_entity_geometry(
                        state.name, props,
                        visual=options.geometry == "visual",
                        texture_override=entity_texture_override,
                    )
                    if entity_faces is not None:
                        local_faces = entity_faces
                        if entity_faces:
                            report.entity_model_blocks += 1
                            if (
                                options.geometry == "visual"
                                and any(face.texture for face in entity_faces)
                            ):
                                report.entity_textured_blocks += 1
                            elif options.geometry == "visual":
                                report.entity_texture_fallbacks += 1
                        else:
                            report.intentionally_invisible += 1
                    else:
                        report.record(result, state, region,
                                      tuple(pos[i] + minimum[i] for i in range(3)), options.fallback)
                        if options.fallback == "cube":
                            local_faces = _cuboid(
                                0, 0, 0, 1, 1, 1, state.name
                            )
                        else:
                            continue
            if (options.geometry == 'visual' and options.seamless_glass
                    and state.name in GLASS and pos in glass_neighbors):
                local_faces = glass_faces(
                    state.name, props, pos, glass_neighbors,
                    loader.seamless_glass_texture(state.name),
                )
                report.seamless_glass_blocks += 1
            report.rendered_blocks += 1
            if (options.geometry == "visual" and emission_enabled and local_faces
                    and (block_light_level(state.name, props) > 0 or any(
                        face.emission_strength != 0 or face.emission_texture is not None
                        for face in local_faces))):
                # Cached local models must remain immutable: coordinate-specific
                # overrides may give identical states different strengths.
                local_faces = list(local_faces)
                multiplier, override_color = resolve_override(
                    emission_config, state.name, props, region,
                    tuple(pos[i] + minimum[i] for i in range(3)),
                )
                multiplier *= max(0.0, float(options.emission_strength))
                block_peak = 0.0
                for face_index, face in enumerate(local_faces):
                    spec = emission_profile(
                        state.name, face.texture, props,
                        explicit_level=face.emission_strength,
                    )
                    if spec is None:
                        if face.emission_texture is None and face.emission_strength == 0:
                            continue
                    # Copy only the metadata that will change. Geometry and UV
                    # stay immutable and shared with the local model cache.
                    face = Face(face.vertices, face.normal, face.material,
                                face.uvs, face.texture,
                                face.emission_texture, face.emission_strength)
                    local_faces[face_index] = face
                    if spec is None:
                        face.emission_texture = None
                        face.emission_strength = 0.0
                        continue
                    level, profile = spec
                    mask = loader.emission_texture(face.texture, profile)
                    if mask is None or multiplier <= 0:
                        face.emission_texture = None
                        face.emission_strength = 0.0
                        continue
                    face.emission_texture = mask
                    face.emission_strength = level * multiplier
                    block_peak = max(block_peak, face.emission_strength)
                    emission_materials.add((mask, face.emission_strength))
                if block_peak > 0:
                    report.emissive_blocks += 1
                    if _uses_editable_blender_lights(options.blender_lights):
                        color = override_color or default_light_color(state.name)
                        base = state.name.split(":", 1)[-1]
                        light_sources.append({
                            "name": (
                                f"MC {base} [{pos[0]},{pos[1]},{pos[2]}]"
                            ),
                            "block": state.name,
                            "region": region,
                            "block_position": list(pos),
                            "position": _editable_light_position(
                                state.name, pos, occupied_positions
                            ),
                            "level": block_peak,
                            "color": list(color),
                            "type": "POINT",
                            "power": 120.0,
                            "radius": 0.14,
                        })
            if options.geometry == "print":
                if not local_faces:
                    continue
                if is_unit_cube_faces(local_faces):
                    cube_positions.add(pos)
                    continue
                geometry_key = face_geometry_key(local_faces)
                local_solid = local_solid_cache.get(geometry_key)
                if local_solid is None:
                    try:
                        local_solid = manifold_from_closed_faces(local_faces)
                    except Exception:
                        if options.boolean_fallback == "fail":
                            raise
                        local_solid = voxel32_fallback(local_faces)
                        solid_report.boolean_failures += 1
                        solid_report.voxel_fallbacks += 1
                    local_solid_cache[geometry_key] = local_solid
                local_solid = local_solid.translate(tuple(map(float, pos)))
                chunk = (pos[0] // 16, pos[1] // 16, pos[2] // 16)
                solid_chunks.setdefault(chunk, []).append(local_solid)
                solid_report.boolean_inputs += 1
            else:
                offset = tuple(map(float, pos))
                mesh.add_faces(local_faces, offset=offset)

        if options.geometry == "print":
            for box_origin, box_size in greedy_cube_boxes(cube_positions):
                for origin, size in split_box_by_chunks(box_origin, box_size):
                        run = cube_solid(size, origin)
                        chunk = (
                            origin[0] // 16, origin[1] // 16, origin[2] // 16
                        )
                        solid_chunks.setdefault(chunk, []).append(run)
                        solid_report.boolean_inputs += 1
            boolean_started = time.monotonic()
            chunk_items = list(solid_chunks.items())
            solid_chunks.clear()
            chunk_total = max(1, len(chunk_items))
            progress(
                "boolean", 0.72,
                f"布尔阶段开始：{len(chunk_items)} 个空间区块，"
                f"{solid_report.boolean_inputs} 个实体输入",
            )
            chunk_solids = []
            for chunk_index, (chunk_pos, parts) in enumerate(chunk_items, 1):
                if cancelled():
                    raise ConversionCancelled()
                input_count = len(parts)

                def chunk_progress(done, total_batches, round_index, remaining):
                    fraction = (
                        chunk_index - 1 + done / total_batches
                    ) / chunk_total
                    elapsed = time.monotonic() - boolean_started
                    progress(
                        "boolean",
                        0.72 + 0.12 * min(1.0, fraction),
                        f"区块布尔 {chunk_index}/{chunk_total} {chunk_pos}："
                        f"第{round_index}轮，批次 {done}/{total_batches}，"
                        f"当前层剩余约 {remaining} 个实体，"
                        f"耗时 {elapsed:.1f}秒",
                    )

                try:
                    chunk_union = union_balanced(
                        parts,
                        progress=chunk_progress,
                        cancelled=cancelled,
                    )
                except BooleanCancelled as exc:
                    raise ConversionCancelled() from exc
                progress(
                    "boolean",
                    0.72 + 0.12 * chunk_index / chunk_total,
                    f"正在实体化区块 {chunk_index}/{chunk_total} {chunk_pos}，"
                    f"切断延迟布尔运算树...",
                )
                chunk_solids.append(materialize_manifold(chunk_union))
                parts.clear()
                chunk_items[chunk_index - 1] = None
                elapsed = time.monotonic() - boolean_started
                progress(
                    "boolean",
                    0.72 + 0.12 * chunk_index / chunk_total,
                    f"区块布尔完成 {chunk_index}/{chunk_total}："
                    f"该区块 {input_count} 个输入，累计耗时 {elapsed:.1f}秒",
                )
            solid_report.chunk_solids = len(chunk_solids)
            progress(
                "boolean", 0.85,
                f"开始全局布尔并集：{len(chunk_solids)} 个区块实体",
            )

            def global_progress(done, total_batches, round_index, remaining):
                elapsed = time.monotonic() - boolean_started
                progress(
                    "boolean",
                    0.85 + 0.05 * min(1.0, done / total_batches),
                    f"全局布尔：第{round_index}轮，批次 "
                    f"{done}/{total_batches}，当前层剩余约 {remaining} 个实体，"
                    f"总耗时 {elapsed:.1f}秒",
                )

            try:
                lazy_solid = union_balanced(
                    chunk_solids,
                    progress=global_progress,
                    cancelled=cancelled,
                )
            except BooleanCancelled as exc:
                raise ConversionCancelled() from exc
            progress(
                "boolean", 0.90,
                "正在实体化全局并集，释放分块布尔运算树...",
            )
            solid = materialize_manifold(lazy_solid)
            del lazy_solid
            chunk_solids.clear()
            progress(
                "solid", 0.905,
                "布尔并集完成，正在分析独立壳体和封闭空腔...",
            )
            solid, print_vertices, print_triangles = process_components_and_cavities(
                solid,
                cavities=options.cavities,
                components=options.components,
                min_component_volume=options.min_component_volume,
                report=solid_report,
                return_mesh=True,
            )
            validate_manifold(solid, solid_report)
            if not solid_report.printable:
                raise ValueError("打印实体未通过流形检查，已停止导出")
            if options.center and len(print_vertices):
                low = print_vertices.min(axis=0)
                high = print_vertices.max(axis=0)
                print_vertices = print_vertices.copy()
                print_vertices[:, 0] -= (low[0] + high[0]) / 2
                print_vertices[:, 2] -= (low[2] + high[2]) / 2
            if options.scale != 1:
                print_vertices = print_vertices * options.scale
            import numpy as np
            if not np.isfinite(print_vertices).all():
                raise ValueError("缩放后的顶点超出输出格式范围，已停止导出")
            progress(
                "solid", 0.915,
                f"实体检查完成：{solid_report.component_count} 个独立壳体，"
                f"{solid_report.cavity_count} 个封闭空腔",
            )
            report.solid = solid_report
        else:
            progress(
                "visual_mesh", 0.88,
                f"视觉网格已增量完成：{mesh.triangle_count} 个三角形",
            )
            if options.blender_lights == "clustered":
                light_sources = _cluster_editable_lights(light_sources)
            light_offset_x = 0.0
            light_offset_z = 0.0
            if options.center and light_sources:
                light_offset_x = -float(
                    mesh.bounds_min[0] + mesh.bounds_max[0]
                ) / 2
                light_offset_z = -float(
                    mesh.bounds_min[2] + mesh.bounds_max[2]
                ) / 2
            for source in light_sources:
                x, y, z = source["position"]
                source["position"] = [
                    (x + light_offset_x) * options.scale,
                    y * options.scale,
                    (z + light_offset_z) * options.scale,
                ]
                source["radius"] *= options.scale
                source["power"] *= options.scale * options.scale
            mesh.transform(options.scale, options.center)
            report.emissive_materials = len(emission_materials)
            report.blender_lights = len(light_sources)
        if options.geometry == "print":
            report.vertices = len(print_vertices)
            report.triangles = len(print_triangles)
        else:
            report.vertices, report.triangles = (
                mesh.vertex_count, mesh.triangle_count
            )
        progress("export", 0.92, "导出模型...")
        options.output_path.parent.mkdir(parents=True, exist_ok=True)
        if options.geometry == "print" and options.output_format == "obj":
            export_obj_arrays(
                print_vertices, print_triangles, options.output_path
            )
        elif options.geometry == "print":
            export_stl_arrays(
                print_vertices,
                print_triangles,
                options.output_path,
                binary=options.stl_binary,
            )
        elif options.output_format == "obj":
            def visual_progress(fraction, message):
                if cancelled():
                    raise ConversionCancelled()
                progress("visual_optimize", 0.92 + 0.07 * fraction, message)

            report.visual_optimization = export_compact_obj(
                mesh, options.output_path,
                texture_provider=(
                    loader.texture_bytes if options.textures else None
                ),
                alpha_provider=(
                    loader.texture_alpha_bytes if options.textures else None
                ),
                emission_provider=(
                    loader.texture_bytes
                    if options.textures and emission_enabled else None
                ),
                light_sources=light_sources,
                progress=visual_progress,
            )
            report.vertices = report.visual_optimization['exported_vertices']
        else:
            export_compact_stl(
                mesh, options.output_path, binary=options.stl_binary
            )
        report.transparent_pixels_removed = loader.transparent_pixels_removed
        report.tinted_textures = loader.tinted_texture_count
        if options.save_report:
            report.save(options.output_path.with_suffix(
                options.output_path.suffix + ".report.json"
            ))
        progress("done", 1.0, "转换完成")
        return report
    finally:
        loader.close()
