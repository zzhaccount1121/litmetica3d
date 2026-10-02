"""Static printable geometry for blocks rendered by block-entity renderers."""

from __future__ import annotations

from .block_models import Face, Vec3, _cuboid
from .model_loader import _face_uvs, _rotate_face


def _box(values: tuple[float, float, float, float, float, float],
         material: str, texture: str | None = None,
         uv_origin: tuple[int, int] = (0, 0),
         texture_size: tuple[int, int] = (64, 64)) -> list[Face]:
    x1, y1, z1, x2, y2, z2 = (value / 16.0 for value in values)
    faces = _cuboid(x1, y1, z1, x2, y2, z2, material)
    if texture is None:
        return faces
    px = tuple(float(value) for value in values)
    width = max(1.0, px[3] - px[0])
    height = max(1.0, px[4] - px[1])
    depth = max(1.0, px[5] - px[2])
    u, v = uv_origin
    tw, th = texture_size
    rects = {
        0: (u + depth + width, v, u + depth + width * 2, v + depth),
        1: (u + depth, v, u + depth + width, v + depth),
        2: (u + depth, v + depth, u + depth + width, v + depth + height),
        3: (
            u + depth + width + depth, v + depth,
            u + depth + width + depth + width, v + depth + height,
        ),
        4: (u, v + depth, u + depth, v + depth + height),
        5: (
            u + depth + width, v + depth,
            u + depth + width + depth, v + depth + height,
        ),
    }
    directions = ("down", "up", "north", "south", "west", "east")
    for index, face in enumerate(faces):
        face.texture = texture
        face.material = f"texture:{texture}"
        face.uvs = _face_uvs(
            rects[index], 0, directions[index], (tw, th)
        )
    return faces


def _rotate(faces: list[Face], axis: str, angle: float) -> list[Face]:
    return [_rotate_face(face, axis, angle) for face in faces]


def _facing_y(faces: list[Face], facing: str, base: str = "south") -> list[Face]:
    order = ["south", "west", "north", "east"]
    if base not in order or facing not in order:
        return faces
    angle = (order.index(facing) - order.index(base)) * 90
    return _rotate(faces, "y", angle) if angle else faces


def _chest(
    name: str, props: dict[str, str], texture: str | None = None
) -> list[Face]:
    if texture is not None:
        return _visual_chest(name, props, texture)
    # Closed chest: body, lid and front latch.  Double-chest halves reach
    # the shared block edge while single chests retain the one-pixel margin.
    chest_type = props.get("type", "single")
    if name == "minecraft:ender_chest":
        chest_type = "single"
    x1, x2 = 1, 15
    lock_x1, lock_x2 = 7, 9
    if chest_type == "right":
        x2 = 16
        lock_x1, lock_x2 = 15, 16
    elif chest_type == "left":
        x1 = 0
        lock_x1, lock_x2 = 0, 1
    faces = []
    faces += _box((x1, 0, 1, x2, 10, 15), name, texture, (0, 19))
    faces += _box((x1, 10, 1, x2, 14, 15), name, texture, (0, 0))
    lock = ((7, 7, 14.75, 9, 12, 16) if chest_type == "single"
            else (lock_x1, 7, 15, lock_x2, 11, 16))
    faces += _box(lock, name, texture, (0, 0))
    angle = {"south": 0, "west": -90, "north": 180, "east": 90}.get(props.get("facing"), 0)
    return _rotate(faces, "y", angle) if angle else faces


def _chest_box(values, material, texture, uv_origin):
    """Chest ModelPart UV net in the renderer's positive-Y coordinates.

    This is intentionally separate from block JSON UVs and other entity models.
    The upper surface uses the SECOND horizontal island in the entity atlas.
    """
    faces = _box(values, material)
    x1, y1, z1, x2, y2, z2 = values
    width, depth = x2-x1, z2-z1
    u0, v0 = uv_origin
    for side, face in enumerate(faces):
        face.texture = texture
        face.material = f'texture:{texture}'
        face.uvs = []
        for vertex in face.vertices:
            x, y, z = vertex.x*16-x1, vertex.y*16-y1, vertex.z*16-z1
            if side == 0:
                u, v = depth+x, depth-z
            elif side == 1:
                u, v = depth+width+x, depth-z
            elif side == 2:
                u, v = depth+x, depth+y
            elif side == 3:
                u, v = depth+width+depth+width-x, depth+y
            elif side == 4:
                u, v = depth-z, depth+y
            else:
                u, v = depth+width+z, depth+y
            face.uvs.append(((u0+u)/64, 1-(v0+v)/64))
    return faces


def _visual_chest(name, props, texture):
    chest_type = props.get('type', 'single')
    if name == 'minecraft:ender_chest':
        chest_type = 'single'
    # Right and left are named from the chest's front. In south-facing local
    # coordinates the right half occupies the western block, touching x=16.
    if chest_type == 'right':
        x1, x2, lock_x1, lock_x2 = 1, 16, 15, 16
    elif chest_type == 'left':
        x1, x2, lock_x1, lock_x2 = 0, 15, 0, 1
    else:
        x1, x2, lock_x1, lock_x2 = 1, 15, 7, 9
    faces = []
    for box, origin in [
        ((x1, 0, 1, x2, 10, 15), (0, 19)),
        ((x1, 9, 1, x2, 14, 15), (0, 0)),
        ((lock_x1, 7, 15, lock_x2, 11, 16), (0, 0)),
    ]:
        faces.extend(_chest_box(box, name, texture, origin))
    angle = {'south': 0, 'west': -90, 'north': 180, 'east': 90}.get(props.get('facing'), 0)
    return _rotate(faces, 'y', angle) if angle else faces


def _shulker(
    name: str, props: dict[str, str], texture: str | None = None
) -> list[Face]:
    faces = _box((0, 0, 0, 16, 8, 16), name, texture, (0, 28))
    faces += _box((0, 8, 0, 16, 16, 16), name, texture, (0, 0))
    facing = props.get("facing", "up")
    if facing == "down":
        return _rotate(faces, "x", 180)
    if facing == "north":
        return _rotate(faces, "x", -90)
    if facing == "south":
        return _rotate(faces, "x", 90)
    if facing == "west":
        return _rotate(faces, "z", 90)
    if facing == "east":
        return _rotate(faces, "z", -90)
    return faces


def _banner(
    name: str, props: dict[str, str], wall: bool,
    texture: str | None = None,
) -> list[Face]:
    if wall:
        # Base orientation is attached to the north wall.
        faces = _box((3, 4, 0.5, 13, 15, 1.5), name, texture, (0, 0))
        faces += _box((2, 14, 0, 14, 15, 2), name, texture, (0, 42))
        return _facing_y(faces, props.get("facing", "north"), base="north")
    faces = _box((7.5, 0, 7.5, 8.5, 16, 8.5), name, texture, (0, 42))
    faces += _box((2, 14, 7, 14, 15, 9), name, texture, (0, 42))
    faces += _box((3, 4, 7.5, 13, 14, 8.5), name, texture, (0, 0))
    try:
        angle = int(props.get("rotation", "0")) * 22.5
    except ValueError:
        angle = 0
    return _rotate(faces, "y", angle) if angle else faces


def _head(
    name: str, props: dict[str, str], wall: bool,
    texture: str | None = None,
) -> list[Face]:
    if wall:
        faces = _box((4, 4, 0, 12, 12, 8), name, texture, (0, 0))
        return _facing_y(faces, props.get("facing", "north"), base="north")
    faces = _box((4, 0, 4, 12, 8, 12), name, texture, (0, 0))
    try:
        angle = int(props.get("rotation", "0")) * 22.5
    except ValueError:
        angle = 0
    return _rotate(faces, "y", angle) if angle else faces


def get_entity_geometry(
    block_name: str, properties: dict[str, str],
    visual: bool = False,
    texture_override: str | None = None,
) -> list[Face] | None:
    """Return geometry, [] for intentionally invisible, or None if unsupported."""
    base = block_name.split(":", 1)[-1]
    texture = (
        texture_override
        if visual and texture_override
        else (_entity_texture(base, properties) if visual else None)
    )
    if base in {"chest", "trapped_chest", "ender_chest", "copper_chest",
                "exposed_copper_chest", "weathered_copper_chest",
                "oxidized_copper_chest"}:
        return _chest(block_name, properties, texture)
    if base == "shulker_box" or base.endswith("_shulker_box"):
        return _shulker(block_name, properties, texture)
    if base == "banner" or (
        base.endswith("_banner") and not base.endswith("_wall_banner")
    ):
        return _banner(block_name, properties, False, texture)
    if base.endswith("_wall_banner"):
        return _banner(block_name, properties, True, texture)
    if base in {
        "skeleton_skull", "wither_skeleton_skull", "zombie_head",
        "player_head", "creeper_head", "dragon_head", "piglin_head",
    }:
        return _head(block_name, properties, False, texture)
    if base.endswith("_wall_skull") or base.endswith("_wall_head"):
        return _head(block_name, properties, True, texture)
    if base == "conduit":
        return _box(
            (3, 3, 3, 13, 13, 13), block_name,
            "minecraft:entity/conduit/base" if visual else None,
            texture_size=(32, 16),
        )
    if base in {"bubble_column", "barrier", "light", "structure_void"}:
        return []
    return None


def _entity_texture(
    base: str, properties: dict[str, str]
) -> str | None:
    if base in {
        "chest", "trapped_chest", "ender_chest", "copper_chest",
        "exposed_copper_chest", "weathered_copper_chest",
        "oxidized_copper_chest",
    }:
        stem = {
            "chest": "normal",
            "trapped_chest": "trapped",
            "ender_chest": "ender",
            "copper_chest": "copper",
            "exposed_copper_chest": "copper_exposed",
            "weathered_copper_chest": "copper_weathered",
            "oxidized_copper_chest": "copper_oxidized",
        }[base]
        chest_type = properties.get("type", "single")
        if chest_type in {"left", "right"} and base != "ender_chest":
            stem += f"_{chest_type}"
        return f"minecraft:entity/chest/{stem}"
    if base == "shulker_box":
        return "minecraft:entity/shulker/shulker"
    if base.endswith("_shulker_box"):
        color = base[:-len("_shulker_box")]
        return f"minecraft:entity/shulker/shulker_{color}"
    if base.endswith("_banner"):
        return "minecraft:entity/banner/banner_base"
    head_textures = {
        "skeleton_skull": "entity/skeleton/skeleton",
        "wither_skeleton_skull": "entity/skeleton/wither_skeleton",
        "zombie_head": "entity/zombie/zombie",
        "creeper_head": "entity/creeper/creeper",
        "dragon_head": "entity/enderdragon/dragon",
        "piglin_head": "entity/piglin/piglin",
        "player_head": "entity/player/wide/steve",
    }
    normalized = base.removeprefix("wall_")
    normalized = normalized.replace("_wall_skull", "_skull")
    normalized = normalized.replace("_wall_head", "_head")
    path = head_textures.get(normalized)
    return f"minecraft:{path}" if path else None
