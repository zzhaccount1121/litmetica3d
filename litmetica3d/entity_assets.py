"""Vanilla baked entity layers. Loaded lazily, only for experimental visual OBJ."""
from __future__ import annotations

from functools import lru_cache
import gzip
import json
import math
import re

import numpy as np

from .block_models import Face, Vec3

ROOT = 'assets/litmetica3d/entities/'


def identifier(value):
    value = str(value)
    if ':' not in value:
        value = 'minecraft:' + value
    if not re.fullmatch(r'[a-z0-9_.-]+:[a-z0-9_./-]+', value) or '..' in value:
        raise ValueError('无效实体/资源标识符')
    return value


def vector(value, count=3):
    if not isinstance(value, (tuple, list)) or len(value) != count:
        raise ValueError(f'坐标/旋转需要 {count} 个数值')
    result = tuple(float(v) for v in value)
    if not all(math.isfinite(v) for v in result):
        raise ValueError('实体包含非有限坐标/旋转')
    return result


def translate(x=0, y=0, z=0):
    out = np.eye(4)
    out[:3, 3] = x, y, z
    return out


def scale(x=1, y=None, z=None):
    return np.diag([x, x if y is None else y, x if z is None else z, 1.0])


def rotate(axis, degrees):
    angle = math.radians(float(degrees))
    c, s = math.cos(angle), math.sin(angle)
    out = np.eye(4)
    i, j = {'x': (1, 2), 'y': (2, 0), 'z': (0, 1)}[axis]
    out[i, i] = out[j, j] = c
    out[i, j], out[j, i] = -s, s
    return out


def face(points, uv, texture, material=None):
    points = np.asarray(points, dtype=float)
    n = np.cross(points[1] - points[0], points[2] - points[0])
    length = np.linalg.norm(n)
    if not np.isfinite(points).all() or not np.isfinite(uv).all():
        raise ValueError('实体模型包含无效顶点或 UV')
    if length < 1e-12:
        return None
    return Face([Vec3(*p) for p in points], Vec3(*(n / length)),
                material or f'texture:{texture}', list(map(tuple, uv)), texture)


def transform(faces, matrix):
    if not np.isfinite(matrix).all():
        raise ValueError('实体变换包含非有限值')
    reverse = np.linalg.det(matrix[:3, :3]) < 0
    result = []
    for f in faces:
        points = np.array([[v.x, v.y, v.z, 1] for v in f.vertices]) @ matrix.T
        uv = f.uvs or [(0, 0)] * len(points)
        if reverse:
            points, uv = points[::-1], uv[::-1]
        new = face(points[:, :3], uv, f.texture, f.material)
        if new:
            new.emission_texture, new.emission_strength = f.emission_texture, f.emission_strength
            result.append(new)
    return result


class EntityAssets:
    def __init__(self, loader):
        self.loader = loader
        self.catalog = loader._read_json(ROOT + 'catalog.json')
        self.layers = json.loads(gzip.decompress(loader._read_bytes(ROOT + 'layers.json.gz')))
        self.textures = set(self.catalog['textures'])

    def has_texture(self, texture):
        return texture.removeprefix('minecraft:') in self.textures

    @lru_cache(maxsize=64)
    def baked(self, layer, texture, pose_json='{}', hidden=()):
        root = self.layers[layer]
        overrides = json.loads(pose_json)
        out = []

        def visit(part, parent, name):
            if name in hidden:
                return
            x, y, z, rx, ry, rz, sx, sy, sz = part['pose']
            if name in overrides:
                rx, ry, rz = [math.radians(v) for v in vector(overrides[name])]
            matrix = (parent @ translate(x / 16, y / 16, z / 16)
                      @ rotate('z', math.degrees(rz)) @ rotate('y', math.degrees(ry))
                      @ rotate('x', math.degrees(rx)) @ scale(sx, sy, sz))
            for cube in part['cubes']:
                for polygon in cube:
                    raw = np.array(polygon, dtype=float)
                    pts = np.column_stack((raw[:, :3] / 16, np.ones(len(raw)))) @ matrix.T
                    uv = np.column_stack((raw[:, 3], 1 - raw[:, 4]))
                    if np.linalg.det(matrix[:3, :3]) < 0:
                        pts, uv = pts[::-1], uv[::-1]
                    f = face(pts[:, :3], uv, texture)
                    if f:
                        out.append(f)
            for child_name, child in part['children'].items():
                visit(child, matrix, child_name)
        visit(root, np.eye(4), '')
        return out

    def close(self):
        self.baked.cache_clear()
        self.layers.clear()

    def variant(self, kind, data):
        defaults = {'cat': 'tabby', 'wolf': 'pale', 'zombie_nautilus': 'temperate'}
        value = data.get('variant', data.get('Variant', defaults.get(kind, 'temperate')))
        if isinstance(value, str):
            key = value.split(':')[-1]
        else:
            key = defaults.get(kind, 'temperate')
        prefix = kind + '_variant/'
        return self.catalog['variants'].get(prefix + key,
            self.catalog['variants'].get(prefix + defaults.get(kind, 'temperate'), {}))

    def selection(self, kind, data):
        """Resolve a base mesh and texture; overlays are handled by the renderer."""
        baby = data.get('Age', data.get('age', 0)) < 0 or bool(data.get('IsBaby', data.get('baby', False)))
        layer = kind
        texture = None
        aliases = {'camel_husk': 'camel', 'spectral_arrow': 'arrow', 'breeze_wind_charge': 'wind_charge',
                   'mannequin': 'player', 'pufferfish': 'pufferfish_small', 'tropical_fish': 'tropical_fish_small'}
        layer = aliases.get(kind, layer)
        if kind.endswith(('_boat', '_raft')):
            chest = '_chest_' in kind
            wood = kind.replace('_chest', '').removesuffix('_boat').removesuffix('_raft')
            layer = ('chest_boat/' if chest else 'boat/') + wood
            texture = 'entity/' + layer
        variants = self.variant(kind, data)
        if variants:
            texture = variants.get('baby_asset_id' if baby else 'asset_id')
            if kind == 'wolf':
                mode = 'angry' if data.get('AngerTime', 0) > 0 else 'tame' if data.get('Owner') or data.get('owner') else 'wild'
                texture = variants.get('baby_assets' if baby else 'assets', {}).get(mode)
            model = variants.get('model')
            if model and model != 'normal' and 'minecraft:' + model + '_' + kind + '#main' in self.layers:
                layer = model + '_' + kind
        if kind == 'pufferfish':
            layer = ('pufferfish_small', 'pufferfish_medium', 'pufferfish_big')[max(0, min(2, int(data.get('PuffState', data.get('puff_state', 0)))))]
        if kind == 'salmon':
            size = data.get('type', data.get('Type', 'medium'))
            layer = 'salmon_' + size if size in {'small', 'large'} else 'salmon'
        if kind == 'sulfur_cube' and data.get('Size', data.get('size', 1)) == 0:
            layer = 'sulfur_cube_small'
        special = {
            'armor_stand': 'armorstand/armorstand', 'polar_bear': 'bear/polarbear',
            'ender_dragon': 'enderdragon/dragon', 'magma_cube': 'slime/magmacube',
            'elder_guardian': 'guardian/guardian_elder', 'giant': 'zombie/zombie',
            'player': 'player/wide/steve', 'mannequin': 'player/wide/steve',
            'leash_knot': 'lead_knot/lead_knot', 'arrow': 'projectiles/arrow',
            'spectral_arrow': 'projectiles/arrow_spectral', 'wind_charge': 'projectiles/wind_charge',
            'breeze_wind_charge': 'projectiles/wind_charge', 'wither_skull': 'wither/wither',
            'sulfur_cube': 'sulfur_cube/sulfur_cube_outer', 'pufferfish': 'fish/pufferfish',
            'tropical_fish': 'fish/tropical_a', 'horse': 'horse/horse_white',
            'skeleton_horse': 'horse/horse_skeleton', 'zombie_horse': 'horse/horse_zombie',
            'parrot': 'parrot/parrot_red_blue', 'rabbit': 'rabbit/brown',
            'llama': 'llama/llama_creamy', 'trader_llama': 'llama/llama_creamy',
            'mooshroom': 'cow/mooshroom_red', 'axolotl': 'axolotl/axolotl_lucy',
            'fox': 'fox/fox', 'shulker_bullet': 'shulker/spark',
        }
        if kind.endswith('minecart'):
            texture = 'entity/minecart/minecart'
        if texture is None:
            texture = 'entity/' + special[kind] if kind in special else None
        if texture is None:
            matches = sorted(t for t in self.textures if t.startswith('entity/') and t.endswith('/' + kind))
            texture = matches[0] if len(matches) == 1 else 'entity/' + kind + '/' + kind
        texture = identifier(texture)
        if baby:
            baby_layer = 'minecraft:' + layer + '_baby#main'
            if baby_layer in self.layers:
                layer += '_baby'
            if self.has_texture(texture + '_baby'):
                texture += '_baby'
        # Numeric variants used in older schematics.
        numeric = {
            'axolotl': ('lucy', 'wild', 'gold', 'cyan', 'blue'),
            'parrot': ('red_blue', 'blue', 'green', 'yellow_blue', 'gray'),
            'horse': ('white', 'creamy', 'chestnut', 'brown', 'black', 'gray', 'darkbrown'),
            'llama': ('creamy', 'white', 'brown', 'gray'),
            'trader_llama': ('creamy', 'white', 'brown', 'gray'),
            'rabbit': ('brown', 'white', 'black', 'white_splotched', 'gold', 'salt'),
        }
        value = data.get('Variant', data.get('variant', data.get('RabbitType', 0)))
        if kind in numeric and isinstance(value, int):
            values = numeric[kind]
            variant = values[(value & 255) % len(values)]
            folder = 'llama' if kind == 'trader_llama' else kind
            stem = folder + '_' + variant
            candidate = 'minecraft:entity/' + folder + '/' + stem + ('_baby' if baby else '')
            if self.has_texture(candidate):
                texture = candidate
        if kind in {'mooshroom', 'fox'}:
            variant = data.get('Type', data.get('type', 'red'))
            stem = ('cow/mooshroom_brown' if variant == 'brown' else 'cow/mooshroom_red') if kind == 'mooshroom' else ('fox/snow_fox' if variant == 'snow' else 'fox/fox')
            texture = 'minecraft:entity/' + stem + ('_baby' if baby else '')
        if kind == 'tropical_fish':
            layer = 'tropical_fish_large' if int(value or 0) & 255 else 'tropical_fish_small'
            texture = 'minecraft:entity/fish/tropical_' + ('b' if layer.endswith('large') else 'a')
        if kind == 'zombie_nautilus' and data.get('variant') == 'minecraft:warm':
            layer = 'zombie_nautilus_coral'
        return 'minecraft:' + layer + '#main', texture
