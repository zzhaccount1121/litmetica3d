"""
Minecraft asset model loader.

Reads blockstate definitions and block model JSON files from a
Minecraft version JAR file and converts them to Face geometry.

Minecraft model format reference:
  https://minecraft.wiki/w/Model_(resource_pack)

Usage:
    loader = ModelLoader("path/to/1.21.4.jar")
    faces = loader.get_faces("minecraft:oak_slab", {"type": "bottom"})
"""

import json
import hashlib
import io
import math
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .block_models import Face, Vec3

try:
    from PIL import Image
except ImportError:  # Texture support is optional outside visual mode.
    Image = None


def _rgba_pixels(image):
    getter = getattr(image, "get_flattened_data", None)
    return getter() if getter is not None else image.getdata()


@dataclass(frozen=True)
class ModelResult:
    """Result of resolving one complete block state."""
    faces: tuple[Face, ...]
    status: str = "ok"
    detail: str = ""


# ── Minecraft direction → face generator ───────────────────────────────────

def _element_face(
    x1: float, y1: float, z1: float,
    x2: float, y2: float, z2: float,
    direction: str, material: str,
) -> Face:
    """
    Generate a Face for a cuboid element in a given direction.

    Coordinates are in normalized space (0.0–1.0, where 1.0 = one block).
    Vertex winding is CCW viewed from outside (right-hand rule).
    """
    if direction == "down":
        return Face(
            [Vec3(x1, y1, z1), Vec3(x2, y1, z1),
             Vec3(x2, y1, z2), Vec3(x1, y1, z2)],
            Vec3(0, -1, 0), material)
    elif direction == "up":
        return Face(
            [Vec3(x1, y2, z1), Vec3(x1, y2, z2),
             Vec3(x2, y2, z2), Vec3(x2, y2, z1)],
            Vec3(0, 1, 0), material)
    elif direction == "north":
        return Face(
            [Vec3(x1, y1, z1), Vec3(x1, y2, z1),
             Vec3(x2, y2, z1), Vec3(x2, y1, z1)],
            Vec3(0, 0, -1), material)
    elif direction == "south":
        return Face(
            [Vec3(x1, y1, z2), Vec3(x2, y1, z2),
             Vec3(x2, y2, z2), Vec3(x1, y2, z2)],
            Vec3(0, 0, 1), material)
    elif direction == "west":
        return Face(
            [Vec3(x1, y1, z1), Vec3(x1, y1, z2),
             Vec3(x1, y2, z2), Vec3(x1, y2, z1)],
            Vec3(-1, 0, 0), material)
    elif direction == "east":
        return Face(
            [Vec3(x2, y1, z1), Vec3(x2, y2, z1),
             Vec3(x2, y2, z2), Vec3(x2, y1, z2)],
            Vec3(1, 0, 0), material)
    else:
        return Face([], Vec3(0, 0, 0), material)


def _default_uv(direction: str, bounds: tuple[float, float, float, float, float, float]):
    x1, y1, z1, x2, y2, z2 = bounds
    if direction in {"north", "south"}:
        return [x1, 16 - y2, x2, 16 - y1]
    if direction in {"west", "east"}:
        return [z1, 16 - y2, z2, 16 - y1]
    return [x1, z1, x2, z2]


def _face_uvs(rect, rotation=0, direction=None, texture_size=(16, 16)):
    u1, v1, u2, v2 = map(float, rect)
    texture_width, texture_height = map(float, texture_size)
    top_left = (u1 / texture_width, 1 - v1 / texture_height)
    bottom_left = (u1 / texture_width, 1 - v2 / texture_height)
    top_right = (u2 / texture_width, 1 - v1 / texture_height)
    bottom_right = (u2 / texture_width, 1 - v2 / texture_height)
    # Match Minecraft FaceInfo's vertex order to _element_face's outward-CCW
    # order. A single ordering rotates south/west faces by 90 degrees and is
    # especially visible on grass sides and directional wood textures.
    if direction == "up":
        result = [top_left, bottom_left, bottom_right, top_right]
    elif direction in {"north", "east"}:
        result = [bottom_right, top_right, top_left, bottom_left]
    elif direction in {"down", "south", "west"}:
        result = [bottom_left, bottom_right, top_right, top_left]
    else:
        # Backward-compatible default for callers without a face direction.
        result = [bottom_left, top_left, top_right, bottom_right]
    turns = (int(rotation) // 90) % 4
    return result[turns:] + result[:turns]


# ── Rotation helpers ───────────────────────────────────────────────────────

def _rotate_vertex(v: Vec3, axis: str, angle: float,
                   origin: Vec3 | None = None) -> Vec3:
    """
    Rotate a vertex around the given axis by `angle` degrees.

    Rotation is applied around `origin` (default: 0.5, 0.5, 0.5 = block center).
    """
    if origin is None:
        origin = Vec3(0.5, 0.5, 0.5)

    import math
    rad = math.radians(angle)
    c, s = math.cos(rad), math.sin(rad)

    # Translate to origin
    x, y, z = v.x - origin.x, v.y - origin.y, v.z - origin.z

    if axis == "x":
        ny = y * c - z * s
        nz = y * s + z * c
        rx, ry, rz = x, ny, nz
    elif axis == "y":
        nx = x * c + z * s
        nz = -x * s + z * c
        rx, ry, rz = nx, y, nz
    else:  # "z"
        nx = x * c - y * s
        ny = x * s + y * c
        rx, ry, rz = nx, ny, z

    return Vec3(rx + origin.x, ry + origin.y, rz + origin.z)


def _rotate_face(face: Face, axis: str, angle: float) -> Face:
    """Rotate all vertices of a face."""
    origin = Vec3(0.5, 0.5, 0.5)
    new_verts = [_rotate_vertex(v, axis, angle, origin) for v in face.vertices]
    new_normal = _rotate_vertex(face.normal, axis, angle, Vec3(0, 0, 0))
    # Preserve slanted normals; only remove trigonometric roundoff.
    nx = round(new_normal.x, 12)
    ny = round(new_normal.y, 12)
    nz = round(new_normal.z, 12)
    return Face(new_verts, Vec3(float(nx), float(ny), float(nz)),
                face.material, face.uvs, face.texture,
                face.emission_texture, face.emission_strength)


def _rotate_element_face(face: Face, rotation: dict) -> Face:
    origin = Vec3(*(float(v) / 16 for v in rotation.get("origin", [8, 8, 8])))
    axis, angle = rotation.get("axis", "y"), float(rotation.get("angle", 0))
    if not angle:
        return face
    factors = [1.0, 1.0, 1.0]
    if rotation.get("rescale", False):
        factor = 1.0 / math.cos(math.radians(angle))
        factors = [1.0 if name == axis else factor for name in "xyz"]
    vertices = []
    for vertex in face.vertices:
        scaled = Vec3(*[getattr(origin, name) + (getattr(vertex, name) - getattr(origin, name)) * factor
                       for name, factor in zip("xyz", factors)])
        vertices.append(_rotate_vertex(scaled, axis, angle, origin))
    normal = Vec3(*[getattr(face.normal, name) / factor for name, factor in zip("xyz", factors)])
    normal = _rotate_vertex(normal, axis, angle, Vec3())
    length = math.sqrt(normal.x**2 + normal.y**2 + normal.z**2)
    if length:
        normal = Vec3(normal.x / length, normal.y / length, normal.z / length)
    return Face(vertices, normal, face.material, face.uvs, face.texture,
                face.emission_texture, face.emission_strength)


_UV_BASES = {
    (0,-1,0): (Vec3(1,0,0), Vec3(0,0,1)),
    (0,1,0): (Vec3(1,0,0), Vec3(0,0,-1)),
    (0,0,-1): (Vec3(-1,0,0), Vec3(0,1,0)),
    (0,0,1): (Vec3(1,0,0), Vec3(0,1,0)),
    (-1,0,0): (Vec3(0,0,1), Vec3(0,1,0)),
    (1,0,0): (Vec3(0,0,-1), Vec3(0,1,0)),
}


def _lock_face_uv(face: Face, rot_x: int, rot_y: int) -> Face:
    """Apply the inverse model-frame change in the texture's centered frame."""
    if not face.uvs or not (rot_x or rot_y):
        return face
    source = tuple(round(getattr(face.normal, a)) for a in "xyz")
    if source not in _UV_BASES:
        return face
    def rotate(v):
        if rot_x: v = _rotate_vertex(v, "x", -float(rot_x), Vec3())
        if rot_y: v = _rotate_vertex(v, "y", -float(rot_y), Vec3())
        return v
    target = tuple(round(getattr(rotate(face.normal), a)) for a in "xyz")
    if target not in _UV_BASES:
        return face
    rotated = [rotate(v) for v in _UV_BASES[source]]
    def dot(a,b): return a.x*b.x + a.y*b.y + a.z*b.z
    matrix = [[round(dot(basis, v)) for v in rotated] for basis in _UV_BASES[target]]
    uvs = [tuple(.5 + row[0]*(u-.5) + row[1]*(v-.5) for row in matrix) for u,v in face.uvs]
    return Face(face.vertices, face.normal, face.material, uvs, face.texture,
                face.emission_texture, face.emission_strength)


# ── Model loader ───────────────────────────────────────────────────────────

class ModelLoader:
    """
    Loads Minecraft block models from a version JAR file.

    The JAR contains:
      assets/minecraft/blockstates/<name>.json  — variant → model mapping
      assets/minecraft/models/block/<path>.json — cuboid elements
    """

    # Block renames between major Minecraft versions
    # (schematics from older versions may use old names)
    _BLOCK_ALIASES: dict[str, str] = {
        "chain": "iron_chain",
        "grass": "short_grass",
    }

    def __init__(self, asset_path: str | Path, minimum_thickness: float = 1 / 16,
                 visual_textures: bool = False, solid_textures: bool = False):
        self.asset_path = Path(asset_path)
        self.minimum_thickness = max(0.0, float(minimum_thickness))
        self.visual_textures = bool(visual_textures)
        self.solid_textures = bool(solid_textures)
        self.jar = (
            zipfile.ZipFile(str(self.asset_path), "r")
            if self.asset_path.is_file() else None
        )
        self._model_cache: dict[str, dict | None] = {}
        self._blockstate_cache: dict[str, dict | None] = {}
        self._geometry_cache: dict[tuple, ModelResult] = {}
        self._image_cache: dict[str, object | None] = {}
        self._generated_textures: dict[str, bytes] = {}
        self._export_texture_cache: dict[str, bytes | None] = {}
        self._animation_cache: dict[str, dict | None] = {}
        self.transparent_pixels_removed = 0
        self.tinted_texture_count = 0

        if self.jar is not None:
            self._asset_names = set(self.jar.namelist())
        else:
            assets = self.asset_path / "assets"
            self._asset_names = {
                (Path(folder).relative_to(self.asset_path) / name).as_posix()
                for folder, _, files in os.walk(assets)
                for name in files
            } if assets.exists() else set()

    @property
    def block_count(self) -> int:
        return sum(
            1 for name in self._asset_names
            if "/blockstates/" in name and name.endswith(".json")
        )

    def _read_json(self, path: str) -> dict:
        if self.jar is not None:
            raw = self.jar.read(path)
        else:
            raw = (self.asset_path / Path(path)).read_bytes()
        return json.loads(raw.decode("utf-8"))

    def _read_bytes(self, path: str) -> bytes:
        if self.jar is not None:
            return self.jar.read(path)
        return (self.asset_path / Path(path)).read_bytes()

    @staticmethod
    def _resolve_texture(textures: dict, reference) -> str | None:
        if not reference:
            return None
        seen = set()
        value = reference
        if isinstance(value, dict):
            value = value.get("sprite")
        if not isinstance(value, str):
            return None
        while value.startswith("#"):
            key = value[1:]
            if key in seen:
                return None
            seen.add(key)
            value = textures.get(key)
            if isinstance(value, dict):
                value = value.get("sprite")
            if not isinstance(value, str):
                return None
        if ":" not in value:
            value = "minecraft:" + value
        return value

    def texture_asset_path(self, texture: str) -> str:
        if texture.startswith("generated:"):
            return texture
        namespace, name = texture.split(":", 1)
        return f"assets/{namespace}/textures/{name}.png"

    def texture_bytes(self, texture: str) -> bytes | None:
        if texture.startswith("generated:"):
            return self._generated_textures.get(texture)
        if texture in self._export_texture_cache:
            return self._export_texture_cache[texture]
        path = self.texture_asset_path(texture)
        try:
            raw = self._read_bytes(path) if path in self._asset_names else None
        except Exception:
            raw = None
        animation = self._animation_metadata(texture)
        if raw and animation is not None and Image is not None:
            image = Image.open(io.BytesIO(raw)).convert("RGBA")
            image = self._first_animation_frame(image, animation)
            output = io.BytesIO()
            image.save(output, format="PNG")
            raw = output.getvalue()
        self._export_texture_cache[texture] = raw
        return raw

    def _animation_metadata(self, texture: str) -> dict | None:
        if texture.startswith("generated:"):
            return None
        if texture in self._animation_cache:
            return self._animation_cache[texture]
        metadata_path = self.texture_asset_path(texture) + ".mcmeta"
        animation = None
        if metadata_path in self._asset_names:
            try:
                data = json.loads(
                    self._read_bytes(metadata_path).decode("utf-8")
                )
                candidate = data.get("animation")
                if isinstance(candidate, dict):
                    animation = candidate
            except Exception:
                animation = None
        self._animation_cache[texture] = animation
        return animation

    @staticmethod
    def _first_animation_frame(image, animation: dict):
        frame_width = max(1, int(animation.get("width", image.width)))
        frame_height = max(1, int(animation.get("height", frame_width)))
        frames = animation.get("frames")
        frame_index = 0
        if isinstance(frames, list) and frames:
            first = frames[0]
            if isinstance(first, dict):
                first = first.get("index", 0)
            try:
                frame_index = max(0, int(first))
            except (TypeError, ValueError):
                frame_index = 0
        columns = max(1, image.width // frame_width)
        x = (frame_index % columns) * frame_width
        y = (frame_index // columns) * frame_height
        if x + frame_width > image.width or y + frame_height > image.height:
            x = y = 0
        return image.crop((x, y, x + frame_width, y + frame_height))

    def _texture_image(self, texture: str):
        if texture in self._image_cache:
            return self._image_cache[texture]
        if Image is None:
            raise RuntimeError("视觉纹理模式需要 Pillow")
        raw = self.texture_bytes(texture)
        image = Image.open(io.BytesIO(raw)).convert("RGBA") if raw else None
        self._image_cache[texture] = image
        return image

    @staticmethod
    def _tint_color(
        material: str, properties: dict[str, str], tintindex
    ) -> tuple[int, int, int] | None:
        if tintindex is None:
            return None
        base = material.split(":", 1)[-1]
        if base == "redstone_wire":
            try:
                power = max(0, min(15, int(properties.get("power", "0"))))
            except ValueError:
                power = 0
            strength = power / 15.0
            red = 0.3 if power == 0 else strength * 0.6 + 0.4
            green = max(0.0, strength * strength * 0.7 - 0.5)
            blue = max(0.0, strength * strength * 0.6 - 0.7)
            return tuple(round(channel * 255) for channel in (red, green, blue))
        if any(token in base for token in (
            "vine", "leaves", "grass", "fern", "lily_pad",
        )):
            # Stable vanilla-plains visual tint. Litematic has no biome grid.
            if "leaves" in base or "vine" in base:
                return (72, 181, 24)
            return (145, 189, 89)
        return (255, 255, 255)

    def _visual_texture(
        self, texture: str, material: str,
        properties: dict[str, str], tintindex,
    ) -> str:
        tint = self._tint_color(material, properties, tintindex)
        if tint is None or tint == (255, 255, 255):
            return texture
        key = f"generated:tint/{texture.replace(':', '_').replace('/', '_')}_{tint[0]:02x}{tint[1]:02x}{tint[2]:02x}"
        if key not in self._generated_textures:
            image = self._texture_image(texture)
            if image is None:
                return texture
            pixels = []
            tr, tg, tb = tint
            for r, g, b, a in _rgba_pixels(image):
                pixels.append((r * tr // 255, g * tg // 255,
                               b * tb // 255, a))
            tinted = Image.new("RGBA", image.size)
            tinted.putdata(pixels)
            output = io.BytesIO()
            tinted.save(output, format="PNG")
            self._generated_textures[key] = output.getvalue()
            self._image_cache[key] = tinted
            self.tinted_texture_count += 1
        return key

    def tint_texture(
        self, texture: str, tint: tuple[int, int, int], label: str = "custom"
    ) -> str:
        key = (
            f"generated:{label}/"
            f"{texture.replace(':', '_').replace('/', '_')}_"
            f"{tint[0]:02x}{tint[1]:02x}{tint[2]:02x}"
        )
        if key in self._generated_textures:
            return key
        image = self._texture_image(texture)
        if image is None:
            return texture
        tr, tg, tb = tint
        tinted = Image.new("RGBA", image.size)
        tinted.putdata([
            (r * tr // 255, g * tg // 255, b * tb // 255, a)
            for r, g, b, a in _rgba_pixels(image)
        ])
        output = io.BytesIO()
        tinted.save(output, format="PNG")
        self._generated_textures[key] = output.getvalue()
        self._image_cache[key] = tinted
        self.tinted_texture_count += 1
        return key

    def compose_banner_texture(
        self,
        base_color: tuple[int, int, int],
        patterns: list[tuple[str, tuple[int, int, int]]],
        cache_label: str,
    ) -> str:
        key = "generated:banner/" + cache_label
        if key in self._generated_textures:
            return key
        source = self._texture_image("minecraft:entity/banner/banner_base")
        if source is None:
            return "minecraft:entity/banner/banner_base"

        def colored_layer(image, color):
            cr, cg, cb = color
            result = Image.new("RGBA", image.size)
            result.putdata([
                (cr, cg, cb, a * max(r, g, b) // 255)
                for r, g, b, a in _rgba_pixels(image)
            ])
            return result

        canvas = colored_layer(source, base_color)
        for pattern_name, color in patterns:
            layer = self._texture_image(
                f"minecraft:entity/banner/{pattern_name}"
            )
            if layer is None:
                continue
            if layer.size != canvas.size:
                layer = layer.resize(canvas.size, Image.Resampling.NEAREST)
            canvas.alpha_composite(colored_layer(layer, color))
        output = io.BytesIO()
        canvas.save(output, format="PNG")
        self._generated_textures[key] = output.getvalue()
        self._image_cache[key] = canvas
        self.tinted_texture_count += 1
        return key

    def seamless_glass_texture(self, block_name: str) -> str:
        """Uniform color/opacity sampled from the original texture interior."""
        from PIL import Image
        import io
        base = block_name.removesuffix('_pane').split(':', 1)[1]
        key = f'generated:seamless_glass/{base}'
        if key not in self._generated_textures:
            source = self._texture_image(f'minecraft:block/{base}')
            if source is None:
                raise ValueError(f'Missing glass texture: {base}')
            w, h = source.size
            pixels = list(_rgba_pixels(source.crop((w//4, h//4, 3*w//4, 3*h//4))))
            visible = [p for p in pixels if p[3] > 0]
            if base == 'glass':
                rgba = (220, 240, 245, 40)
            else:
                if not visible:
                    raise ValueError(f'No visible glass color: {base}')
                rgba = tuple(round(sum(p[i] for p in visible)/len(visible))
                             for i in range(4))
            output = io.BytesIO()
            Image.new('RGBA', (1, 1), rgba).save(output, format='PNG')
            self._generated_textures[key] = output.getvalue()
        return key

    def texture_alpha_bytes(self, texture: str) -> bytes | None:
        image = self._texture_image(texture)
        if image is None:
            return None
        alpha = image.getchannel("A")
        if alpha.getextrema() == (255, 255):
            return None
        output = io.BytesIO()
        # OBJ importers differ: some sample map_d RGB, Blender may sample
        # its Alpha socket. Store the mask in BOTH so either is correct.
        Image.merge("RGBA", (alpha, alpha, alpha, alpha)).save(output, format="PNG")
        return output.getvalue()

    def emission_texture(self, texture: str, profile: str) -> str | None:
        """Build a shader-pack-style RGB emission overlay for one texture."""
        key = (
            "generated:emission/"
            f"{profile}_{texture.replace(':', '_').replace('/', '_')}"
        )
        if key in self._generated_textures:
            return key
        image = self._texture_image(texture)
        if image is None:
            return None

        def keep(r: int, g: int, b: int, a: int) -> bool:
            if a == 0:
                return False
            value = max(r, g, b)
            if profile == "full":
                return value > 0
            if profile == "red":
                return r >= 45 and r > g * 1.18 and r > b * 1.12
            if profile == "soul":
                return max(g, b) >= 55 and max(g, b) > r * 1.08
            if profile == "purple":
                return max(r, b) >= 55 and b > g * 1.08
            if profile == "warm":
                return (
                    value >= 70 and r >= g * 0.88
                    and r > b * 1.18 and (r - b) >= 24
                )
            # Bright integrated-PBR style fallback.  It is only used after a
            # block/texture classification, never on arbitrary white blocks.
            return value >= 112 and (r + g + b) >= 300

        pixels = [
            (r, g, b, a) if keep(r, g, b, a) else (0, 0, 0, 255)
            for r, g, b, a in _rgba_pixels(image)
        ]
        if not any((r or g or b) for r, g, b, _ in pixels):
            return None
        overlay = Image.new("RGBA", image.size)
        overlay.putdata(pixels)
        output = io.BytesIO()
        overlay.save(output, format="PNG")
        self._generated_textures[key] = output.getvalue()
        self._image_cache[key] = overlay
        return key

    def _alpha_shell_faces(
        self, elem, material, textures, properties
    ) -> list[Face] | None:
        """Build closed pixel plates for non-zero elements with cutout faces."""
        source_from = list(map(float, elem["from"]))
        source_to = list(map(float, elem["to"]))
        if any(source_from[i] == source_to[i] for i in range(3)):
            return None
        cutout_found = False
        output: list[Face] = []
        bounds = tuple(value / 16 for value in (*source_from, *source_to))
        source_bounds = tuple((*source_from, *source_to))

        def append_regular_face(direction, face_data, texture):
            face = _element_face(*bounds, direction, material)
            if texture:
                face.texture = texture
                face.material = f"texture:{texture}"
                face.uvs = _face_uvs(
                    face_data.get(
                        "uv", _default_uv(direction, source_bounds)
                    ),
                    face_data.get("rotation", 0),
                    direction,
                )
            output.append(face)

        for direction, face_data in elem.get("faces", {}).items():
            raw_texture = self._resolve_texture(
                textures, face_data.get("texture")
            )
            if raw_texture is None:
                append_regular_face(direction, face_data, None)
                continue
            visual_texture = self._visual_texture(
                raw_texture, material, properties,
                face_data.get("tintindex"),
            )
            image = self._texture_image(visual_texture)
            if image is None or image.getchannel("A").getextrema()[0] != 0:
                append_regular_face(direction, face_data, visual_texture)
                continue
            cutout_found = True
            a, b = source_from[:], source_to[:]
            if direction == "north":
                a[2] = b[2] = source_from[2]
            elif direction == "south":
                a[2] = b[2] = source_to[2]
            elif direction == "west":
                a[0] = b[0] = source_from[0]
            elif direction == "east":
                a[0] = b[0] = source_to[0]
            elif direction == "down":
                a[1] = b[1] = source_from[1]
            elif direction == "up":
                a[1] = b[1] = source_to[1]
            else:
                continue
            fake = {
                "from": a,
                "to": b,
                "faces": {direction: face_data},
            }
            pixel_faces = self._pixel_extrusion_faces(
                fake, material, textures, properties
            )
            if pixel_faces:
                output.extend(pixel_faces)
        return output if cutout_found else None

    @staticmethod
    def _apply_rotations(
        faces: list[Face], elem_rot, rot_x: int, rot_y: int, uvlock: bool = False
    ) -> list[Face]:
        result = []
        for face in faces:
            if uvlock:
                face = _lock_face_uv(face, rot_x, rot_y)
            if elem_rot and elem_rot.get("angle", 0):
                face = _rotate_element_face(face, elem_rot)
            if rot_x:
                face = _rotate_face(face, "x", -float(rot_x))
            if rot_y:
                face = _rotate_face(face, "y", -float(rot_y))
            result.append(face)
        return result

    def _pixel_extrusion_faces(
        self, elem, material, textures, properties=None
    ):
        """Turn opaque pixels of a zero-depth element into closed prisms."""
        original_from = list(map(float, elem["from"]))
        original_to = list(map(float, elem["to"]))
        zero_axes = [i for i in range(3) if original_to[i] == original_from[i]]
        if len(zero_axes) != 1:
            return None
        axis = zero_axes[0]
        elem_faces = elem.get("faces", {})
        preferred = {
            0: ("east", "west"), 1: ("up", "down"), 2: ("south", "north")
        }[axis]
        face_dir = next((d for d in preferred if d in elem_faces), None)
        if face_dir is None and elem_faces:
            face_dir = next(iter(elem_faces))
        face_data = elem_faces.get(face_dir, {}) if face_dir else {}
        texture = self._resolve_texture(textures, face_data.get("texture"))
        if texture is None:
            return None
        texture = self._visual_texture(
            texture, material, properties or {}, face_data.get("tintindex")
        )
        image = self._texture_image(texture)
        if image is None:
            return None
        alpha = image.getchannel("A")
        width, height = image.size
        uv = face_data.get("uv", [0, 0, 16, 16])
        ux1, vy1, ux2, vy2 = map(float, uv)
        source_u0 = max(0, min(width, int(round(ux1 / 16 * width))))
        source_u1 = max(0, min(width, int(round(ux2 / 16 * width))))
        source_v0 = max(0, min(height, int(round(vy1 / 16 * height))))
        source_v1 = max(0, min(height, int(round(vy2 / 16 * height))))
        source_width = abs(source_u1 - source_u0)
        source_height = abs(source_v1 - source_v0)
        if source_width == 0 or source_height == 0:
            return []
        face_turns = (int(face_data.get("rotation", 0)) // 90) % 4
        if face_turns % 2:
            grid_width, grid_height = source_height, source_width
        else:
            grid_width, grid_height = source_width, source_height
        u_forward = source_u1 >= source_u0
        v_forward = source_v1 >= source_v0
        source_u_min = min(source_u0, source_u1)
        source_u_max = max(source_u0, source_u1)
        source_v_min = min(source_v0, source_v1)
        source_v_max = max(source_v0, source_v1)
        px1, py1, px2, py2 = 0, 0, grid_width, grid_height
        plane_mapping = {
            # direction: (U world axis, U sign, V world axis, V sign)
            "east": (2, -1, 1, -1),
            "west": (2, 1, 1, -1),
            "south": (0, 1, 1, -1),
            "north": (0, -1, 1, -1),
            "up": (0, 1, 2, 1),
            "down": (0, 1, 2, -1),
        }
        u_axis, u_sign, v_axis, v_sign = plane_mapping.get(
            face_dir, tuple([i for i in range(3) if i != axis]) + (1, 1)
        )
        # Minecraft V runs down while model Y runs up.
        out = []
        thickness_px = self.minimum_thickness * 16
        def rotated_source_pixel(gx, gy):
            if face_turns == 0:
                return gx, gy
            if face_turns == 1:
                return gy, source_height - 1 - gx
            if face_turns == 2:
                return source_width - 1 - gx, source_height - 1 - gy
            return source_width - 1 - gy, gx

        def source_pixel(gx, gy):
            su, sv = rotated_source_pixel(gx, gy)
            return (
                source_u_min + su if u_forward
                else source_u_max - 1 - su,
                source_v_min + sv if v_forward
                else source_v_max - 1 - sv,
            )

        opaque = {
            (gx, gy)
            for gy in range(grid_height)
            for gx in range(grid_width)
            if alpha.getpixel(source_pixel(gx, gy)) != 0
        }
        self.transparent_pixels_removed += (
            (px2 - px1) * (py2 - py1) - len(opaque)
        )
        axis_dirs = {
            0: ("west", "east"),
            1: ("down", "up"),
            2: ("north", "south"),
        }
        def geometry(px0, py0, px_end, py_end):
            a, b = original_from[:], original_to[:]
            a[axis] -= thickness_px / 2
            b[axis] += thickness_px / 2
            fu0 = (px0 - px1) / (px2 - px1)
            fu1 = (px_end - px1) / (px2 - px1)
            fv0 = (py0 - py1) / (py2 - py1)
            fv1 = (py_end - py1) / (py2 - py1)
            u0, u1 = (fu0, fu1) if u_sign > 0 else (1 - fu1, 1 - fu0)
            v0, v1 = (fv0, fv1) if v_sign > 0 else (1 - fv1, 1 - fv0)
            a[u_axis] = (
                original_from[u_axis]
                + (original_to[u_axis] - original_from[u_axis]) * u0
            )
            b[u_axis] = (
                original_from[u_axis]
                + (original_to[u_axis] - original_from[u_axis]) * u1
            )
            a[v_axis] = (
                original_from[v_axis]
                + (original_to[v_axis] - original_from[v_axis]) * v0
            )
            b[v_axis] = (
                original_from[v_axis]
                + (original_to[v_axis] - original_from[v_axis]) * v1
            )
            bounds = tuple(value / 16 for value in (*a, *b))
            def texture_point(px, py):
                qx = px / grid_width
                qy = py / grid_height
                if face_turns == 0:
                    su, sv = qx, qy
                elif face_turns == 1:
                    su, sv = qy, 1 - qx
                elif face_turns == 2:
                    su, sv = 1 - qx, 1 - qy
                else:
                    su, sv = 1 - qy, qx
                return (
                    source_u0 + (source_u1 - source_u0) * su,
                    source_v0 + (source_v1 - source_v0) * sv,
                )

            uv_bl = texture_point(px0, py_end)
            uv_tl = texture_point(px0, py0)
            uv_tr = texture_point(px_end, py0)
            uv_br = texture_point(px_end, py_end)
            uvs = [
                (uv_bl[0] / width, 1 - uv_bl[1] / height),
                (uv_tl[0] / width, 1 - uv_tl[1] / height),
                (uv_tr[0] / width, 1 - uv_tr[1] / height),
                (uv_br[0] / width, 1 - uv_br[1] / height),
            ]
            return bounds, uvs

        def add_rect(px0, py0, px_end, py_end, direction):
            bounds, uvs = geometry(px0, py0, px_end, py_end)
            face = _element_face(*bounds, direction, f"texture:{texture}")
            face.texture = texture
            face.uvs = uvs
            out.append(face)

        # Greedy rectangles cover the two broad surfaces without changing
        # the alpha silhouette or UV sampling.
        remaining = set(opaque)
        while remaining:
            start_x, start_y = min(remaining, key=lambda p: (p[1], p[0]))
            end_x = start_x + 1
            while (end_x, start_y) in remaining:
                end_x += 1
            end_y = start_y + 1
            while all(
                (x, end_y) in remaining for x in range(start_x, end_x)
            ):
                end_y += 1
            for y in range(start_y, end_y):
                for x in range(start_x, end_x):
                    remaining.remove((x, y))
            for direction in axis_dirs[axis]:
                add_rect(start_x, start_y, end_x, end_y, direction)

        def consecutive(values):
            values = sorted(values)
            if not values:
                return
            start = previous = values[0]
            for value in values[1:]:
                if value != previous + 1:
                    yield start, previous + 1
                    start = value
                previous = value
            yield start, previous + 1

        # Merge collinear outer alpha-boundary edges into long side quads.
        u_negative, u_positive = axis_dirs[u_axis]
        v_negative, v_positive = axis_dirs[v_axis]
        if u_sign < 0:
            u_negative, u_positive = u_positive, u_negative
        for x in range(px1, px2):
            left = [y for y in range(py1, py2)
                    if (x, y) in opaque and (x - 1, y) not in opaque]
            right = [y for y in range(py1, py2)
                     if (x, y) in opaque and (x + 1, y) not in opaque]
            for y0, y1 in consecutive(left):
                add_rect(x, y0, x + 1, y1, u_negative)
            for y0, y1 in consecutive(right):
                add_rect(x, y0, x + 1, y1, u_positive)
        top_direction, bottom_direction = (
            (v_negative, v_positive)
            if v_sign > 0 else (v_positive, v_negative)
        )
        for y in range(py1, py2):
            top = [x for x in range(px1, px2)
                   if (x, y) in opaque and (x, y - 1) not in opaque]
            bottom = [x for x in range(px1, px2)
                      if (x, y) in opaque and (x, y + 1) not in opaque]
            for x0, x1 in consecutive(top):
                add_rect(x0, y, x1, y + 1, top_direction)
            for x0, x1 in consecutive(bottom):
                add_rect(x0, y, x1, y + 1, bottom_direction)
        return out

    # ── Blockstate resolution ──────────────────────────────────────────

    def _load_blockstate(self, block_name: str) -> dict | None:
        """Load a blockstate JSON. Returns None if not found."""
        namespace, name = (
            block_name.split(":", 1)
            if ":" in block_name else ("minecraft", block_name)
        )
        path = f"assets/{namespace}/blockstates/{name}.json"

        if path in self._blockstate_cache:
            return self._blockstate_cache[path]

        if path in self._asset_names:
            data = self._read_json(path)
            self._blockstate_cache[path] = data
            return data

        # Try alias (e.g. "chain" → "iron_chain" for 1.21.5+)
        alias = self._BLOCK_ALIASES.get(name)
        if alias:
            alias_path = f"assets/minecraft/blockstates/{alias}.json"
            if alias_path in self._asset_names:
                data = self._read_json(alias_path)
                self._blockstate_cache[path] = data   # cache under original name
                return data

        self._blockstate_cache[path] = None
        return None

    def _resolve_variant(self, blockstate: dict, properties: dict[str, str]) -> dict | None:
        """
        Given a blockstate JSON and block properties, find the matching
        variant entry. Returns {"model": "...", "x": 0, "y": 0, ...} or None.
        """
        variants = blockstate.get("variants")
        if variants:
            return self._resolve_simple_variant(variants, properties)

        multipart = blockstate.get("multipart")
        if multipart:
            return self._resolve_multipart(multipart, properties)

        return None

    def _resolve_simple_variant(
        self, variants: dict, properties: dict[str, str]
    ) -> dict | None:
        """
        Match a 'variants' blockstate against block properties.

        Variant keys are comma-separated 'key=value' pairs, e.g.:
          "type=bottom,waterlogged=false"

        Variant values can be a single model entry or a list (random alternatives).
        """
        # Build the property key from the block's actual properties
        prop_entries = sorted(properties.items())
        target_key = ",".join(f"{k}={v}" for k, v in prop_entries)

        # Direct match
        if target_key in variants:
            return variants[target_key]

        # Partial match: some variants only list relevant properties.
        # Find the variant whose conditions all match the block's properties.
        for key, entry in variants.items():
            if not key:
                continue
            conditions = dict(pair.split("=", 1) for pair in key.split(","))
            if all(properties.get(k) == v for k, v in conditions.items()):
                return entry

        # If no match found but there's an empty key, use it as default
        if "" in variants:
            return variants[""]

        return None

    def _resolve_multipart(
        self, multipart: list[dict], properties: dict[str, str]
    ) -> list[dict] | None:
        """
        Resolve a 'multipart' blockstate.

        Returns a list of ALL matching part model entries (combined geometry).
        """
        results = []
        for part in multipart:
            apply_entry = part.get("apply")
            when = part.get("when")

            if apply_entry is None:
                continue

            if when is None:
                # Unconditional part — always include
                results.append(apply_entry)
            elif self._match_multipart_when(when, properties):
                results.append(apply_entry)

        return results if results else None

    def _match_multipart_when(self, when, properties: dict[str, str]) -> bool:
        """Check if a multipart 'when' condition matches the block properties."""
        if isinstance(when, list):
            return all(self._match_multipart_when(item, properties) for item in when)
        if not isinstance(when, dict):
            return False
        # OR clause
        if "OR" in when:
            return any(
                self._match_multipart_when(cond, properties)
                for cond in when["OR"]
            )
        if "AND" in when:
            return all(
                self._match_multipart_when(cond, properties)
                for cond in when["AND"]
            )

        # AND clause (implicit: all keys must match)
        for key, expected in when.items():
            actual = properties.get(key, "")
            # expected can be a string "side|up" or a list ["side", "up"]
            if isinstance(expected, str):
                alternatives = expected.split("|")
            elif isinstance(expected, list):
                alternatives = expected
            else:
                alternatives = [str(expected)]
            if actual not in alternatives:
                return False

        return True

    # ── Model loading ──────────────────────────────────────────────────

    def _load_model(self, model_path: str) -> dict | None:
        """
        Load a model JSON by path (e.g. 'minecraft:block/oak_slab' or 'block/oak_slab').

        Handles parent inheritance recursively.
        Returns the fully resolved model dict, or None if not found.
        """
        namespace, path = (
            model_path.split(":", 1)
            if ":" in model_path else ("minecraft", model_path)
        )
        if not path.startswith("block/") and not path.startswith("item/"):
            path = f"block/{path}"

        jar_path = f"assets/{namespace}/models/{path}.json"

        if jar_path in self._model_cache:
            return self._model_cache[jar_path]

        if jar_path not in self._asset_names:
            self._model_cache[jar_path] = None
            return None

        data = self._read_json(jar_path)

        # Resolve parent
        parent_ref = data.get("parent")
        if parent_ref:
            parent_data = self._load_model(parent_ref)
            if parent_data:
                # Merge: child textures override parent, child elements append
                merged = dict(parent_data)  # shallow copy top-level keys
                merged.update(data)  # child overrides parent
                merged["textures"] = {
                    **parent_data.get("textures", {}),
                    **data.get("textures", {}),
                }
                # elements: use child's if present, otherwise parent's
                if "elements" not in data and "elements" in parent_data:
                    merged["elements"] = parent_data["elements"]
                # Special: if both have elements, use child's (child replaces)
                data = merged

        self._model_cache[jar_path] = data
        return data

    # ── Model → Faces ──────────────────────────────────────────────────

    def _model_to_faces(
        self, model_data: dict, material: str,
        rot_x: int = 0, rot_y: int = 0,
        closed: bool = False,
        properties: dict[str, str] | None = None,
        uvlock: bool = False,
    ) -> list[Face]:
        """
        Convert a Minecraft model JSON to a list of Face objects.

        Args:
            model_data: The resolved model dict (with 'elements' key).
            material: Block name for the face material field.
            rot_x: Blockstate-level X rotation (0/90/180/270).
            rot_y: Blockstate-level Y rotation (0/90/180/270).
        """
        elements = model_data.get("elements")
        if not elements:
            return []

        faces: list[Face] = []

        for elem in elements:
            if self.visual_textures and uvlock:
                # Lock the source UVs before sampling alpha. Changing UVs only
                # after extrusion would leave cutout geometry in the old frame.
                elem = self._locked_element(elem, rot_x, rot_y)
            fx, fy, fz = elem["from"]   # Minecraft pixel coords (0–16)
            tx, ty, tz = elem["to"]
            source_bounds = tuple(map(float, (fx, fy, fz, tx, ty, tz)))

            # ── Add minimum thickness for zero-depth elements ──────────
            # Cross models (plants, kelp, etc.) have elements like
            # from[0,0,8] to[16,16,8] — zero depth in Z.
            # For 3D printing we extrude by 1 pixel (1/16 block).
            needs_thickening = (tx - fx == 0) or (ty - fy == 0) or (tz - fz == 0)
            try:
                element_emission = float(elem.get("light_emission", 0))
            except (TypeError, ValueError):
                element_emission = 0.0

            if self.visual_textures and not self.solid_textures and not needs_thickening:
                shell_faces = self._alpha_shell_faces(
                    elem, material, model_data.get("textures", {}),
                    properties or {},
                )
                if shell_faces is not None:
                    for face in shell_faces:
                        face.emission_strength = element_emission
                    faces.extend(self._apply_rotations(
                        shell_faces, elem.get("rotation"), rot_x, rot_y
                    ))
                    continue

            if needs_thickening and self.visual_textures and not self.solid_textures:
                pixel_faces = self._pixel_extrusion_faces(
                    elem, material, model_data.get("textures", {}),
                    properties or {},
                )
                if pixel_faces is not None:
                    for face in pixel_faces:
                        face.emission_strength = element_emission
                    faces.extend(self._apply_rotations(
                        pixel_faces, elem.get("rotation"), rot_x, rot_y
                    ))
                    continue

            if needs_thickening:
                T = self.minimum_thickness * 16.0
                if tx - fx == 0:
                    center = fx
                    fx = center - T / 2
                    tx = center + T / 2
                if ty - fy == 0:
                    center = fy
                    fy = center - T / 2
                    ty = center + T / 2
                if tz - fz == 0:
                    center = fz
                    fz = center - T / 2
                    tz = center + T / 2

            # Normalize to 0.0–1.0
            x1, y1, z1 = fx / 16.0, fy / 16.0, fz / 16.0
            x2, y2, z2 = tx / 16.0, ty / 16.0, tz / 16.0

            # Element-level rotation
            elem_rot = elem.get("rotation")
            elem_faces = elem.get("faces", {})

            # For zero-thickness elements, generate ALL 6 faces since
            # the extrusion creates new edge faces that didn't exist before.
            if needs_thickening or closed:
                directions = ["down", "up", "north", "south", "west", "east"]
            else:
                directions = list(elem_faces.keys())

            for direction in directions:
                face = _element_face(x1, y1, z1, x2, y2, z2, direction, material)
                face_data = elem_faces.get(direction, {})
                # New caps of a solid sprite reuse its original face texture.
                # UV projection stays in the source plane (constant at edges).
                uv_direction = direction
                if (self.solid_textures and needs_thickening
                        and not face_data and elem_faces):
                    uv_direction, face_data = next(iter(elem_faces.items()))
                if self.visual_textures:
                    texture = self._resolve_texture(
                        model_data.get("textures", {}),
                        face_data.get("texture"),
                    )
                    if texture:
                        texture = self._visual_texture(
                            texture, material, properties or {},
                            face_data.get("tintindex"),
                        )
                        face.texture = texture
                        face.material = f"texture:{texture}"
                        face.uvs = _face_uvs(
                            face_data.get(
                                "uv", _default_uv(uv_direction, source_bounds)
                            ),
                            face_data.get("rotation", 0),
                            direction,
                        )
                        if uv_direction != direction:
                            source_face = _element_face(
                                x1, y1, z1, x2, y2, z2, uv_direction, material
                            )
                            source_uvs = _face_uvs(
                                face_data.get("uv", _default_uv(uv_direction, source_bounds)),
                                face_data.get("rotation", 0), uv_direction,
                            )
                            origin, a, _, b = source_face.vertices
                            edge_a = tuple(getattr(a, k) - getattr(origin, k) for k in "xyz")
                            edge_b = tuple(getattr(b, k) - getattr(origin, k) for k in "xyz")
                            def project_uv(vertex):
                                delta = tuple(getattr(vertex, k) - getattr(origin, k) for k in "xyz")
                                u = sum(d * e for d, e in zip(delta, edge_a)) / sum(e * e for e in edge_a)
                                v = sum(d * e for d, e in zip(delta, edge_b)) / sum(e * e for e in edge_b)
                                return tuple(source_uvs[0][i] + u * (source_uvs[1][i] - source_uvs[0][i])
                                             + v * (source_uvs[3][i] - source_uvs[0][i]) for i in (0, 1))
                            face.uvs = [project_uv(vertex) for vertex in face.vertices]

                face = self._apply_rotations([face], elem_rot, rot_x, rot_y)[0]

                face.emission_strength = max(
                    face.emission_strength, element_emission
                )
                faces.append(face)

        return faces

    @staticmethod
    def _locked_element(elem, rot_x, rot_y):
        locked_faces = {}
        bounds = tuple(map(float, (*elem["from"], *elem["to"])))
        for direction, data in elem.get("faces", {}).items():
            face = _element_face(0, 0, 0, 1, 1, 1, direction, "")
            face.uvs = _face_uvs(data.get("uv", _default_uv(direction, bounds)),
                                  data.get("rotation", 0), direction)
            wanted = _lock_face_uv(face, rot_x, rot_y).uvs
            us, vs = zip(*wanted)
            found = None
            # Rect orientation and face rotation together encode every quarter
            # turn, including reversed rectangles used by resource packs.
            for u1, u2 in ((min(us), max(us)), (max(us), min(us))):
                for v1, v2 in ((min(vs), max(vs)), (max(vs), min(vs))):
                    rect = [u1*16, (1-v2)*16, u2*16, (1-v1)*16]
                    for turn in (0, 90, 180, 270):
                        candidate = _face_uvs(rect, turn, direction)
                        if all(abs(a-b) < 1e-10 for p,q in zip(candidate,wanted) for a,b in zip(p,q)):
                            found = dict(data, uv=rect, rotation=turn)
                            break
                    if found is not None: break
                if found is not None: break
            if found is None:
                raise ValueError("uvlock cannot represent this face UV rectangle")
            locked_faces[direction] = found
        return dict(elem, faces=locked_faces)

    # ── Public API ─────────────────────────────────────────────────────

    def has_blockstate(self, block_name: str) -> bool:
        """Check if a blockstate definition exists for this block."""
        namespace, name = (
            block_name.split(":", 1)
            if ":" in block_name else ("minecraft", block_name)
        )
        path = f"assets/{namespace}/blockstates/{name}.json"
        if path in self._asset_names:
            return True
        return namespace == "minecraft" and name in self._BLOCK_ALIASES

    def resolve(
        self,
        block_name: str,
        properties: dict[str, str],
        position: tuple[int, int, int] = (0, 0, 0),
        closed: bool = False,
    ) -> ModelResult:
        """
        Get geometry for a block from its Minecraft model.

        Args:
            block_name: e.g. "minecraft:oak_slab"
            properties: e.g. {"type": "bottom"}

        Returns:
            List of Face objects, or None if no model found.
        """
        try:
            bs = self._load_blockstate(block_name)
        except Exception as exc:
            return ModelResult((), "model_error", str(exc))
        if bs is None:
            return ModelResult((), "unknown_block", "blockstate not found")
        random_model = any(
            isinstance(value, list) for value in bs.get("variants", {}).values()
        ) or any(
            isinstance(part.get("apply"), list)
            for part in bs.get("multipart", [])
        )
        cache_key = (
            block_name, tuple(sorted(properties.items())),
            position if random_model else None, self.minimum_thickness, closed,
            self.visual_textures, self.solid_textures,
        )
        cached = None if random_model else self._geometry_cache.get(cache_key)
        if cached is not None:
            return cached

        if bs.get("variants") is not None:
            variant = self._resolve_simple_variant(bs["variants"], properties)
            entries = [] if variant is None else [
                self._select_weighted(
                    variant, block_name, properties, position, 0
                )
            ]
        elif bs.get("multipart") is not None:
            variant = self._resolve_multipart(bs["multipart"], properties)
            entries = [] if variant is None else [
                self._select_weighted(
                    entry, block_name, properties, position, i
                )
                for i, entry in enumerate(variant)
            ]
        else:
            entries = []
        if not entries:
            return ModelResult((), "unknown_state", "no matching variant")

        if random_model:
            # Coordinate hashing still chooses the exact same weighted variant.
            # Only the selected geometry is shared, never coordinate overrides.
            selection = json.dumps(entries, sort_keys=True, separators=(",", ":"))
            cache_key = cache_key[:2] + (selection,) + cache_key[3:]
            cached = self._geometry_cache.get(cache_key)
            if cached is not None:
                return cached

        try:
            all_faces: list[Face] = []
            for entry in entries:
                faces = self._load_one_variant(
                    entry, block_name, properties, closed=closed
                )
                if faces is None:
                    return ModelResult((), "missing_model", str(entry))
                all_faces.extend(faces)
            result = (
                ModelResult(tuple(all_faces))
                if all_faces else ModelResult((), "missing_model", "empty model")
            )
        except Exception as exc:
            result = ModelResult((), "model_error", str(exc))
        self._geometry_cache[cache_key] = result
        return result

    def get_faces(
        self, block_name: str, properties: dict[str, str]
    ) -> list[Face] | None:
        result = self.resolve(block_name, properties)
        return list(result.faces) if result.status == "ok" else None

    @staticmethod
    def _select_weighted(entry, block_name, properties, position, salt):
        if not isinstance(entry, list):
            return entry
        if not entry:
            return {}
        weights = [max(1, int(item.get("weight", 1))) for item in entry]
        seed_text = repr((block_name, sorted(properties.items()), position, salt))
        number = int.from_bytes(
            hashlib.sha256(seed_text.encode("utf-8")).digest()[:8], "big"
        ) % sum(weights)
        for item, weight in zip(entry, weights):
            if number < weight:
                return item
            number -= weight
        return entry[-1]

    def _load_one_variant(
        self, variant: dict, material: str,
        properties: dict[str, str] | None = None,
        closed: bool = False,
    ) -> list[Face] | None:
        """Load faces for a single variant entry."""
        model_path = variant.get("model")
        if not model_path:
            return None

        model_data = self._load_model(model_path)
        if model_data is None:
            return None

        rot_x = variant.get("x", 0)
        rot_y = variant.get("y", 0)

        return self._model_to_faces(
            model_data, material, rot_x, rot_y, closed=closed,
            properties=properties, uvlock=bool(variant.get("uvlock", False)),
        )

    def close(self):
        """Close the JAR file."""
        if self.jar is not None:
            self.jar.close()
