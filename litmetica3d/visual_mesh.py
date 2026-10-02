"""Compact chunked mesh and streaming exporters for visual geometry."""

from __future__ import annotations

import json
import pathlib
import re
import struct
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .block_models import Face
from .exporters.obj import _resolve_block_color


@dataclass
class _Chunk:
    vertices: np.ndarray
    uvs: np.ndarray
    materials: np.ndarray
    textures: np.ndarray
    emissions: np.ndarray
    emission_strengths: np.ndarray


@dataclass
class CompactVisualMesh:
    chunk_size: int = 8192
    chunks: list[_Chunk] = field(default_factory=list)
    material_names: list[str] = field(default_factory=list)
    texture_names: list[str | None] = field(default_factory=list)
    emission_names: list[str | None] = field(default_factory=list)
    _materials: dict[str, int] = field(default_factory=dict)
    _textures: dict[str | None, int] = field(default_factory=dict)
    _emissions: dict[str | None, int] = field(default_factory=dict)
    _vertices_buffer: list = field(default_factory=list)
    _uvs_buffer: list = field(default_factory=list)
    _material_buffer: list[int] = field(default_factory=list)
    _texture_buffer: list[int] = field(default_factory=list)
    _emission_buffer: list[int] = field(default_factory=list)
    _emission_strength_buffer: list[float] = field(default_factory=list)
    _pending_faces: int = 0
    bounds_min: np.ndarray = field(
        default_factory=lambda: np.full(3, np.inf, dtype=np.float64)
    )
    bounds_max: np.ndarray = field(
        default_factory=lambda: np.full(3, -np.inf, dtype=np.float64)
    )

    def _id(self, value, mapping, names):
        result = mapping.get(value)
        if result is None:
            result = len(names)
            mapping[value] = result
            names.append(value)
        return result

    def add_face(self, face: Face, offset=None) -> None:
        self.add_faces((face,), offset)

    def add_faces(self, faces, offset=None) -> None:
        """Batch work without increasing the existing chunk-size bound.

        Translation remains Python float addition followed by float32 casting,
        exactly as add_face; do not pre-cast local vertices before translation.
        """
        start = 0
        while start < len(faces):
            end = min(len(faces), start + max(1, self.chunk_size - self._pending_faces))
            batch = [f for f in faces[start:end] if len(f.vertices) == 4]
            start = end
            if not batch:
                continue
            if offset is None:
                vertices = np.asarray([[(v.x, v.y, v.z) for v in f.vertices]
                                       for f in batch], dtype=np.float32)
            else:
                x, y, z = offset
                vertices = np.asarray([[(v.x+x, v.y+y, v.z+z) for v in f.vertices]
                                       for f in batch], dtype=np.float32)
            valid = np.isfinite(vertices).all(axis=(1, 2))
            if not valid.all():
                batch = [f for f, keep in zip(batch, valid) if keep]
                vertices = vertices[valid]
            if not batch:
                continue
            uvs = np.asarray([f.uvs if f.uvs and len(f.uvs) == 4
                              else [(0.0, 0.0)] * 4 for f in batch], dtype=np.float32)
            self.bounds_min = np.minimum(self.bounds_min, vertices.min(axis=(0, 1)))
            self.bounds_max = np.maximum(self.bounds_max, vertices.max(axis=(0, 1)))
            self._vertices_buffer.append(vertices)
            self._uvs_buffer.append(uvs)
            for f in batch:
                self._material_buffer.append(self._id(f.material, self._materials, self.material_names))
                self._texture_buffer.append(self._id(f.texture, self._textures, self.texture_names))
                self._emission_buffer.append(self._id(f.emission_texture, self._emissions, self.emission_names))
                self._emission_strength_buffer.append(float(f.emission_strength))
            self._pending_faces += len(batch)
            if self._pending_faces >= self.chunk_size:
                self.flush()

    def flush(self) -> None:
        if not self._vertices_buffer:
            return
        self.chunks.append(_Chunk(
            self._vertices_buffer[0] if len(self._vertices_buffer) == 1 else np.concatenate(self._vertices_buffer),
            self._uvs_buffer[0] if len(self._uvs_buffer) == 1 else np.concatenate(self._uvs_buffer),
            np.asarray(self._material_buffer, dtype=np.int32),
            np.asarray(self._texture_buffer, dtype=np.int32),
            np.asarray(self._emission_buffer, dtype=np.int32),
            np.asarray(self._emission_strength_buffer, dtype=np.float32),
        ))
        self._vertices_buffer.clear()
        self._uvs_buffer.clear()
        self._material_buffer.clear()
        self._texture_buffer.clear()
        self._emission_buffer.clear()
        self._emission_strength_buffer.clear()
        self._pending_faces = 0

    @property
    def face_count(self) -> int:
        return sum(len(chunk.vertices) for chunk in self.chunks) + self._pending_faces

    @property
    def triangle_count(self) -> int:
        return self.face_count * 2

    @property
    def vertex_count(self) -> int:
        return self.face_count * 4

    def transform(self, scale: float, center: bool) -> None:
        self.flush()
        offset = np.zeros(3, dtype=np.float32)
        if center and np.isfinite(self.bounds_min).all():
            offset[0] = -float(self.bounds_min[0] + self.bounds_max[0]) / 2
            offset[2] = -float(self.bounds_min[2] + self.bounds_max[2]) / 2
        for chunk in self.chunks:
            chunk.vertices += offset
            if scale != 1:
                chunk.vertices *= np.float32(scale)
            if not np.isfinite(chunk.vertices).all():
                raise ValueError("缩放后的顶点超出输出格式范围，已停止导出")
        if np.isfinite(self.bounds_min).all():
            self.bounds_min = (self.bounds_min + offset) * scale
            self.bounds_max = (self.bounds_max + offset) * scale


def _triangles(vertices: np.ndarray) -> np.ndarray:
    result = np.empty((len(vertices) * 2, 3, 3), dtype=np.float32)
    result[0::2] = vertices[:, (0, 1, 2)]
    result[1::2] = vertices[:, (0, 2, 3)]
    return result


def export_compact_stl(
    mesh: CompactVisualMesh, path: pathlib.Path, binary: bool = True
) -> None:
    mesh.flush()
    if not binary:
        with path.open("w", encoding="ascii") as stream:
            stream.write("solid litmetica3d\n")
            for chunk in mesh.chunks:
                for tri in _triangles(chunk.vertices):
                    normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
                    length = float(np.linalg.norm(normal))
                    normal = normal / length if length else normal
                    stream.write(
                        f" facet normal {normal[0]} {normal[1]} {normal[2]}\n"
                        "  outer loop\n"
                    )
                    for vertex in tri:
                        stream.write(
                            f"   vertex {vertex[0]} {vertex[1]} {vertex[2]}\n"
                        )
                    stream.write("  endloop\n endfacet\n")
            stream.write("endsolid litmetica3d\n")
        return
    record = np.dtype([
        ("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)),
        ("attribute", "<u2"),
    ])
    with path.open("wb") as stream:
        stream.write(b"litmetica3d compact visual STL".ljust(80, b"\0"))
        stream.write(struct.pack("<I", mesh.triangle_count))
        for chunk in mesh.chunks:
            triangles = _triangles(chunk.vertices)
            normals = np.cross(
                triangles[:, 1] - triangles[:, 0],
                triangles[:, 2] - triangles[:, 0],
            )
            lengths = np.linalg.norm(normals, axis=1)
            valid = lengths > 0
            normals[valid] /= lengths[valid, None]
            records = np.zeros(len(triangles), dtype=record)
            records["normal"] = normals
            records["vertices"] = triangles
            stream.write(records.tobytes())


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def export_compact_obj(
    mesh: CompactVisualMesh,
    path: pathlib.Path,
    texture_provider: Callable[[str], bytes | None] | None = None,
    alpha_provider: Callable[[str], bytes | None] | None = None,
    emission_provider: Callable[[str], bytes | None] | None = None,
    light_sources: list[dict] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> dict:
    mesh.flush()
    mtl_path = path.with_suffix(".mtl")
    texture_dir = path.parent / f"{path.stem}_textures"
    combinations = {}
    for chunk in mesh.chunks:
        for material_id, texture_id, emission_id, strength in zip(
            chunk.materials.tolist(), chunk.textures.tolist(),
            chunk.emissions.tolist(), chunk.emission_strengths.tolist(),
        ):
            key = (material_id, texture_id, emission_id, round(float(strength), 5))
            combinations.setdefault(key, len(combinations))
    blender_materials = []
    # Store only small file-name records, not another image/PNG cache.
    texture_exports = {}
    emission_exports = {}
    # Legacy _safe can map distinct resource IDs onto the same filename.
    # In that unusual case preserve the original last-write-wins order.
    paths = {}
    deduplicate_textures = True
    for _, texture_id, emission_id, strength in combinations:
        texture, emission = mesh.texture_names[texture_id], mesh.emission_names[emission_id]
        candidates = []
        if texture:
            candidates.extend(((_safe(texture) + '.png', ('color', texture)),
                               (_safe(texture) + '_alpha.png', ('alpha', texture))))
        if emission and strength > 0:
            candidates.append((_safe(emission) + '.png', ('emission', emission)))
        for filename, resource in candidates:
            previous = paths.setdefault(filename, resource)
            if previous != resource:
                deduplicate_textures = False
    del paths
    with mtl_path.open("w", encoding="utf-8") as stream:
        stream.write("# MTL generated by litmetica3d compact visual mesh\n")
        for (
            material_id, texture_id, emission_id, emission_strength
        ), group_id in combinations.items():
            material = mesh.material_names[material_id]
            texture = mesh.texture_names[texture_id]
            emission = mesh.emission_names[emission_id]
            seamless = bool(texture and texture.startswith('generated:seamless_glass/'))
            opacity = None
            if seamless and texture_provider:
                from PIL import Image
                import io
                with Image.open(io.BytesIO(texture_provider(texture))) as glass_image:
                    opacity = glass_image.convert('RGBA').getpixel((0, 0))[3] / 255.0
            r, g, b = (1.0, 1.0, 1.0) if texture else _resolve_block_color(material)
            stream.write(
                f"\nnewmtl visual_{group_id}\nKd {r:.5f} {g:.5f} {b:.5f}\n"
                f"Ka {r:.5f} {g:.5f} {b:.5f}\nKs 0 0 0\nd {opacity if opacity is not None else 1.0:.6f}\n"
            )
            if texture and texture_provider:
                if texture not in texture_exports or not deduplicate_textures:
                    raw = texture_provider(texture)
                    filename = alpha_name = None
                    if raw:
                        texture_dir.mkdir(parents=True, exist_ok=True)
                        filename = _safe(texture) + ".png"
                        (texture_dir / filename).write_bytes(raw)
                        alpha = alpha_provider(texture) if alpha_provider else None
                        if alpha and not seamless:
                            alpha_name = _safe(texture) + "_alpha.png"
                            (texture_dir / alpha_name).write_bytes(alpha)
                    texture_exports[texture] = (filename, alpha_name)
                filename, alpha_name = texture_exports[texture]
                if filename:
                    stream.write(f"map_Kd {texture_dir.name}/{filename}\n")
                    if alpha_name:
                        stream.write(f"map_d {texture_dir.name}/{alpha_name}\n")
            emission_rel = None
            if emission and emission_provider and emission_strength > 0:
                if emission not in emission_exports or not deduplicate_textures:
                    raw = emission_provider(emission)
                    emitted = None
                    if raw:
                        texture_dir.mkdir(parents=True, exist_ok=True)
                        emission_name = _safe(emission) + ".png"
                        (texture_dir / emission_name).write_bytes(raw)
                        emitted = f"{texture_dir.name}/{emission_name}"
                    emission_exports[emission] = emitted
                emission_rel = emission_exports[emission]
                if emission_rel:
                    stream.write(f"Ke 1 1 1\nmap_Ke {emission_rel}\n")
            blender_materials.append({
                "material": f"visual_{group_id}",
                "emission_texture": emission_rel,
                "source_level": float(emission_strength),
                "glass_opacity": opacity,
            })
    with path.open("w", encoding="utf-8", buffering=1024 * 1024) as stream:
        stream.write(
            f"# OBJ generated by litmetica3d\nmtllib {mtl_path.name}\n"
        )
        from .visual_index import write_indexed_geometry
        optimization = write_indexed_geometry(mesh, stream, combinations, progress)

    manifest_data = {
        "format": 1,
        "renderer": "BLENDER_CYCLES",
        "materials": blender_materials,
        "lights": light_sources or [],
    }
    manifest_path = path.with_name(path.stem + ".blender_emission.json")
    manifest_path.write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_blender_script(
        path.with_name(path.stem + "_blender_setup.py"),
        manifest_data,
        path.parent,
    )
    return optimization


def _write_blender_script(
    path: pathlib.Path,
    manifest_data: dict,
    output_root: pathlib.Path,
) -> None:
    """Create editable Cycles lights plus pixel-level emission materials."""
    embedded_data = json.dumps(manifest_data, ensure_ascii=False)
    script = f'''# Generated by litmetica3d. Open in Blender and click Run Script.
import bpy
import json
from bpy_extras.io_utils import axis_conversion
from mathutils import Vector
from pathlib import Path

# Blender 5 can expose an opened Text block as "\\\\name.py" instead of its
# real path.  Keep the setup self-contained and locate textures from several
# reliable sources instead of depending on __file__.
DATA = json.loads({embedded_data!r})
OUTPUT_ROOT_HINT = Path({str(output_root)!r})
GLOBAL_STRENGTH = 1.0
CREATE_EDITABLE_LIGHTS = True
RESET_EXISTING_LIGHTS = False
ENABLE_GLARE = True
OBJ_FORWARD_AXIS = "-Z"
OBJ_UP_AXIS = "Y"

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.use_denoising = True
scene.cycles.samples = max(scene.cycles.samples, 128)
OBJ_TO_BLENDER = axis_conversion(
    from_forward=OBJ_FORWARD_AXIS,
    from_up=OBJ_UP_AXIS,
    to_forward="Y",
    to_up="Z",
).to_4x4()

def to_blender_position(position):
    converted = OBJ_TO_BLENDER @ Vector(tuple(position))
    return tuple(float(value) for value in converted)

def candidate_roots():
    roots = []
    text = getattr(getattr(bpy.context, "space_data", None), "text", None)
    text_path = getattr(text, "filepath", "") if text is not None else ""
    if text_path:
        try:
            roots.append(Path(bpy.path.abspath(text_path)).resolve().parent)
        except (OSError, ValueError):
            pass
    source_path = globals().get("__file__", "")
    if source_path:
        try:
            source = Path(source_path)
            if source.exists():
                roots.append(source.resolve().parent)
        except (OSError, ValueError):
            pass
    if OUTPUT_ROOT_HINT.exists():
        roots.append(OUTPUT_ROOT_HINT)
    # The imported OBJ's diffuse images normally live beside the emission
    # masks, so their paths remain useful even after the whole folder moves.
    for image in bpy.data.images:
        image_path = getattr(image, "filepath", "")
        if not image_path:
            continue
        try:
            resolved = Path(bpy.path.abspath(image_path)).resolve()
        except (OSError, ValueError):
            continue
        roots.extend((resolved.parent, resolved.parent.parent))
    roots.append(Path.cwd())
    unique = []
    seen = set()
    for root in roots:
        key = str(root).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique

def resolve_asset(relative_path):
    relative = Path(relative_path)
    if relative.is_absolute() and relative.exists():
        return relative
    searched = []
    for root in candidate_roots():
        candidate = root / relative
        searched.append(str(candidate))
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Cannot locate emission texture: " + str(relative)
        + "\\nSearched:\\n" + "\\n".join(searched)
    )

def socket(node, *names):
    for name in names:
        found = node.inputs.get(name)
        if found is not None:
            return found
    return None

# Pixel-accurate surface emission.  OBJ remains usable without this script,
# but Blender is authoritative for emitted light and indirect illumination.
for item in DATA["materials"]:
    rel = item.get("emission_texture")
    opacity = item.get("glass_opacity")
    if not rel and opacity is None:
        continue
    material = bpy.data.materials.get(item["material"])
    if material is None:
        continue
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    principled = next(
        (node for node in nodes if node.type == "BSDF_PRINCIPLED"), None
    )
    if principled is None:
        continue
    if opacity is not None:
        alpha_socket = socket(principled, "Alpha")
        if alpha_socket is not None:
            for link in list(alpha_socket.links):
                links.remove(link)
            alpha_socket.default_value = float(opacity)
        if hasattr(material, "surface_render_method"):
            material.surface_render_method = "DITHERED"
        elif hasattr(material, "blend_method"):
            material.blend_method = "HASHED"
        roughness = socket(principled, "Roughness")
        if roughness is not None:
            roughness.default_value = 0.15
    if not rel:
        continue
    image_node = nodes.get("Litematica Emission Mask")
    if image_node is None:
        image_node = nodes.new("ShaderNodeTexImage")
        image_node.name = image_node.label = "Litematica Emission Mask"
    image_node.image = bpy.data.images.load(
        str(resolve_asset(rel)), check_existing=True
    )
    emission_color = socket(principled, "Emission Color", "Emission")
    emission_strength = socket(principled, "Emission Strength")
    if emission_color is not None:
        for link in list(emission_color.links):
            links.remove(link)
        links.new(image_node.outputs["Color"], emission_color)
    level = max(0.0, float(item.get("source_level", 0.0)))
    strength = 0.0 if level <= 0 else 0.5 + 12.0 * (level / 15.0) ** 2
    if emission_strength is not None:
        emission_strength.default_value = strength * GLOBAL_STRENGTH

collection = bpy.data.collections.get("Minecraft Lights")
if collection is None:
    collection = bpy.data.collections.new("Minecraft Lights")
    scene.collection.children.link(collection)

if CREATE_EDITABLE_LIGHTS:
    for item in DATA.get("lights", []):
        position = item["position"]
        object_name = item["name"]
        obj = bpy.data.objects.get(object_name)
        if obj is not None and not RESET_EXISTING_LIGHTS:
            if int(obj.get("minecraft:coordinate_version", 0)) < 2:
                obj.location = to_blender_position(position)
                obj["minecraft:coordinate_version"] = 2
            continue
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        light_type = item.get("type", "POINT")
        light = bpy.data.lights.new(object_name, type=light_type)
        level = max(0.0, float(item.get("level", 0.0)))
        light.energy = (
            float(item.get("power", 80.0))
            * (level / 15.0) ** 2 * GLOBAL_STRENGTH
        )
        light.color = tuple(item.get("color", [1.0, 0.55, 0.25]))
        light.shadow_soft_size = float(item.get("radius", 0.125))
        obj = bpy.data.objects.new(object_name, light)
        obj.location = to_blender_position(position)
        obj["minecraft:block"] = item["block"]
        obj["minecraft:region"] = item.get("region", "")
        obj["minecraft:position"] = item.get("block_position", position)
        obj["minecraft:light_level"] = level
        obj["minecraft:coordinate_version"] = 2
        collection.objects.link(obj)

if ENABLE_GLARE:
    scene.use_nodes = True
    tree = getattr(scene, "node_tree", None)
    if tree is None:
        print(
            "Blender does not expose Scene.node_tree; "
            "skipping optional compositor glare."
        )
    else:
        render = next((n for n in tree.nodes if n.type == "R_LAYERS"), None)
        composite = next((n for n in tree.nodes if n.type == "COMPOSITE"), None)
        if render and composite:
            glare = tree.nodes.get("Litematica Glare")
            if glare is None:
                glare = tree.nodes.new("CompositorNodeGlare")
                glare.name = glare.label = "Litematica Glare"
                glare.glare_type = "FOG_GLOW"
                glare.quality = "HIGH"
                glare.threshold = 1.0
                glare.size = 7
            for socket in (glare.inputs["Image"], composite.inputs["Image"]):
                for link in list(socket.links):
                    tree.links.remove(link)
            tree.links.new(render.outputs["Image"], glare.inputs["Image"])
            tree.links.new(glare.outputs["Image"], composite.inputs["Image"])

print("Litematica emission and editable Cycles lights are ready.")
'''
    path.write_text(script, encoding="utf-8")
