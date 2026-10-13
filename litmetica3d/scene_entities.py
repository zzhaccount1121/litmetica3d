"""Experimental static scene entities; deliberately isolated from block/print geometry."""
from __future__ import annotations

from collections import Counter
import hashlib
import io
import json
import math

import numpy as np

from .block_models import _cuboid
from .entity_assets import EntityAssets, face, identifier, rotate, scale, transform, translate, vector

# These vanilla renderers have no persistent surface. Their transient particles
# require a running world and are not fabricated into solid geometry.
INVISIBLE = {'marker', 'interaction', 'area_effect_cloud'}
SPRITES = {'egg': 'egg', 'ender_pearl': 'ender_pearl', 'experience_bottle': 'experience_bottle',
           'eye_of_ender': 'ender_eye', 'fireball': 'fire_charge', 'small_fireball': 'fire_charge',
           'snowball': 'snowball', 'firework_rocket': 'firework_rocket',
           'splash_potion': 'splash_potion', 'lingering_potion': 'lingering_potion'}
POSE_NAMES = {'Head': 'head', 'Body': 'body', 'LeftArm': 'left_arm', 'RightArm': 'right_arm',
              'LeftLeg': 'left_leg', 'RightLeg': 'right_leg'}


class _ObservedNBT(dict):
    """Track unconsumed fields; reading a field is NOT a fidelity guarantee."""
    def __init__(self, data):
        super().__init__(data)
        self.read = set()

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)


def _get(data, upper, lower, default=None):
    return data.get(upper, data.get(lower, default))


def _quad(texture, width=1, height=1, uv=None):
    pts = [(-width / 2, -height / 2, 0), (-width / 2, height / 2, 0),
           (width / 2, height / 2, 0), (width / 2, -height / 2, 0)]
    uv = uv or [(1, 0), (1, 1), (0, 1), (0, 0)]
    # OBJ/Blender surfaces are already visible from both sides. Coincident
    # opposite quads self-shadow in Cycles and turn an opaque item silhouette
    # black; do not duplicate the back side at exactly the same position.
    return [face(pts, uv, texture)]


def _facing(data):
    value = _get(data, 'Facing', 'facing', 2)
    if isinstance(value, str):
        value = {'down': 0, 'up': 1, 'north': 2, 'south': 3, 'west': 4, 'east': 5}.get(value, 2)
    return {0: rotate('x', -90), 1: rotate('x', 90), 2: np.eye(4),
            3: rotate('y', 180), 4: rotate('y', 90), 5: rotate('y', -90)}.get(value, np.eye(4))


def _frame_facing(data):
    """26.2 ItemFrameRenderer: Rx(vertical tilt) then Ry(180-yaw)."""
    value = _get(data, 'Facing', 'facing', 2)
    if value in (0, 'down'):
        return rotate('x', 90) @ rotate('y', 180)
    if value in (1, 'up'):
        return rotate('x', -90) @ rotate('y', 180)
    return _facing(data)


def _display_matrix(data):
    value = data.get('transformation', {})
    if isinstance(value, (list, tuple)):
        result = np.array(vector(value, 16)).reshape(4, 4).T
        if not np.allclose(result[3], [0, 0, 0, 1]):
            raise ValueError('展示实体矩阵不是仿射变换')
        return result
    if not isinstance(value, dict):
        raise ValueError('展示实体 transformation 格式错误')
    def quat(v):
        if isinstance(v, dict):
            angle = float(v.get('angle', 0))
            axis = np.array(vector(v.get('axis', [0, 1, 0])))
            norm = np.linalg.norm(axis)
            if norm == 0:
                raise ValueError('展示实体旋转轴为零')
            v = [*(axis / norm * math.sin(angle / 2)), math.cos(angle / 2)]
        x, y, z, w = vector(v, 4)
        norm = math.sqrt(x*x + y*y + z*z + w*w)
        if norm < 1e-12:
            raise ValueError('展示实体四元数为零')
        x, y, z, w = x/norm, y/norm, z/norm, w/norm
        out = np.eye(4)
        out[:3, :3] = [[1-2*y*y-2*z*z, 2*x*y-2*z*w, 2*x*z+2*y*w],
                      [2*x*y+2*z*w, 1-2*x*x-2*z*z, 2*y*z-2*x*w],
                      [2*x*z-2*y*w, 2*y*z+2*x*w, 1-2*x*x-2*y*y]]
        return out
    return (translate(*vector(value.get('translation', [0, 0, 0])))
            @ quat(value.get('left_rotation', [0, 0, 0, 1]))
            @ scale(*vector(value.get('scale', [1, 1, 1])))
            @ quat(value.get('right_rotation', [0, 0, 0, 1])))


class EntityRenderer:
    def __init__(self, loader):
        self.loader = loader
        self.assets = EntityAssets(loader)
        self.notes = []

    def block(self, state):
        if not isinstance(state, dict):
            raise ValueError('实体方块状态不是 compound')
        name = identifier(state.get('Name', state.get('name', 'minecraft:stone')))
        props = state.get('Properties', state.get('properties', {}))
        result = self.loader.resolve(name, props, (0, 0, 0), closed=False)
        if result.status == 'ok':
            return result.faces
        from .entity_models import get_entity_geometry
        special = get_entity_geometry(name, props, visual=True)
        if special:
            return special
        raise ValueError(f'实体引用的方块模型缺失：{name} ({result.status})')

    def item(self, stack, context='fixed'):
        if not stack or not isinstance(stack, dict):
            return []
        name = identifier(stack.get('id', 'minecraft:air'))
        if name == 'minecraft:air':
            return []
        ns, stem = name.split(':', 1)
        components = stack.get('components', {})
        if isinstance(components, dict) and components:
            self.notes.append(f'物品 {name} 的组件尚未完整参与静态渲染：' + ', '.join(sorted(components)))
        if stack.get('tag'):
            self.notes.append(f'物品 {name} 的旧版 tag 外观属性尚未完整参与静态渲染')
        model_ref = ns + ':item/' + stem
        definition_path = f'assets/{ns}/items/{stem}.json'
        if definition_path in self.loader._asset_names:
            definition = self.loader._read_json(definition_path).get('model', {})
            if definition.get('type') in ('model', 'minecraft:model'):
                model_ref = definition['model']
            else:
                self.notes.append(f'物品 {name} 使用动态/特殊模型定义，当前仅支持静态近似')
        model = self.loader._load_model(model_ref)
        faces = []
        if model and model.get('elements'):
            faces = self.loader._model_to_faces(model, name, closed=False)
            faces = transform(faces, translate(-.5, -.5, -.5))
        elif model and model.get('textures'):
            # Layered flat item sprites share exactly the same UVs; alpha blend
            # the layers once, not separate coincident surfaces.
            textures = model['textures']
            refs = [self.loader._resolve_texture(textures, textures[k]) for k in sorted(textures) if k.startswith('layer')]
            refs = [t for t in refs if t]
            if refs:
                texture = self.compose(refs, 'item_' + stem)
                faces = _quad(texture)
        if not faces:
            try:
                faces = transform(self.block({'Name': name}), translate(-.5, -.5, -.5))
            except ValueError:
                if self.assets.has_texture(ns + ':item/' + stem):
                    faces = _quad(ns + ':item/' + stem)
                else:
                    raise ValueError(f'物品模型尚未支持：{name}')
        display = (model or {}).get('display', {}).get(context, {})
        if display:
            rx, ry, rz = vector(display.get('rotation', [0, 0, 0]))
            matrix = (translate(*(v / 16 for v in vector(display.get('translation', [0, 0, 0]))))
                      @ rotate('x', rx) @ rotate('y', ry) @ rotate('z', rz)
                      @ scale(*vector(display.get('scale', [1, 1, 1]))))
            faces = transform(faces, matrix)
        return faces

    def compose(self, refs, label):
        if len(refs) == 1:
            return refs[0]
        from PIL import Image
        key = 'generated:entity_' + hashlib.sha256(json.dumps(refs).encode()).hexdigest()[:24]
        if key not in self.loader._generated_textures:
            images = [self.loader._texture_image(t) for t in refs]
            if any(i is None for i in images):
                raise ValueError(f'实体贴图层缺失：{label}')
            out = images[0].convert('RGBA').copy()
            for im in images[1:]:
                out.alpha_composite(im.convert('RGBA').resize(out.size, Image.Resampling.NEAREST))
            raw = io.BytesIO()
            out.save(raw, format='PNG')
            self.loader._generated_textures[key] = raw.getvalue()
        return key

    def render(self, data):
        self.notes = []
        observed = _ObservedNBT(data)
        result = self._render(observed)
        # These describe identity, placement or simulation, not a static mesh.
        metadata = {'Pos', 'pos', 'UUID', 'UUIDMost', 'UUIDLeast', 'Passengers', 'passengers',
                    'Motion', 'motion', 'Air', 'Fire', 'FallDistance', 'OnGround', 'PortalCooldown',
                    'Invulnerable', 'PersistenceRequired', 'Health', 'DeathTime', 'HurtTime',
                    'HurtByTimestamp', 'Brain', 'Tags', 'DataVersion'}
        unused = sorted(set(observed) - observed.read - metadata)
        if unused:
            self.notes.append('未参与静态模型渲染的实体字段：' + ', '.join(unused))
        kind = str(data.get('id', '')).split(':')[-1]
        if kind == 'tropical_fish':
            self.notes.append('热带鱼目前仅应用体型；花纹和两种染色尚未复现')
        if kind == 'horse' and isinstance(data.get('Variant', 0), int) and data.get('Variant', 0) >> 8:
            self.notes.append('马匹毛色已应用；Variant 高位斑纹尚未复现')
        variant = data.get('variant', data.get('Variant'))
        prefix = kind + '_variant/'
        variants = self.assets.catalog['variants']
        if variant is not None and any(key.startswith(prefix) for key in variants):
            if not isinstance(variant, str) or prefix + variant.split(':')[-1] not in variants:
                self.notes.append(f'实体变种 {variant!r} 未匹配内置资源，当前使用默认变种')
        return result

    def _render(self, data):
        entity_id = identifier(data.get('id', data.get('Id', '')))
        if entity_id not in self.assets.catalog['types'] and entity_id not in {'minecraft:boat', 'minecraft:chest_boat'}:
            raise ValueError('实体不在内置 Minecraft 26.2 名单中')
        kind = entity_id.split(':')[1]
        yaw, pitch = vector(_get(data, 'Rotation', 'rotation', [0, 0]), 2)
        rotation = rotate('y', -yaw)
        if kind in {'block_display', 'item_display', 'text_display'}:
            # DisplayRenderer FIXED uses Quaternionf.rotationYXZ(-yaw,pitch,0).
            rotation = rotation @ rotate('x', pitch)
            if data.get('billboard', 'fixed') != 'fixed':
                self.notes.append('展示实体动态 billboard 依赖摄像机，当前使用固定朝向')
        if kind in INVISIBLE:
            return [], 'invisible'
        invisible = bool(_get(data, 'Invisible', 'invisible', False))
        if invisible and kind not in {'item_frame', 'glow_item_frame'}:
            return [], 'invisible'
        if kind == 'shulker':
            from .shulker_visual import geometry
            from .conversion import DYE_ORDER
            color = int(data.get('Color', data.get('color', 16)))
            texture = 'minecraft:entity/shulker/shulker'
            if 0 <= color < 16:
                texture += '_' + DYE_ORDER[color]
            facing = ('down','up','north','south','west','east')[max(0,min(5,int(data.get('AttachFace',1))))]
            if data.get('Peek',0):
                self.notes.append('潜影贝使用关闭静态姿态；不重建 Peek 的运行时动画')
            return transform(geometry(texture,facing),translate(-.5,0,-.5)), 'rendered'
        if kind in {'item_frame', 'glow_item_frame'}:
            faces = []
            if not invisible:
                result = self.loader.resolve('minecraft:' + kind, {'map': 'false'}, (0, 0, 0))
                if result.status != 'ok':
                    raise ValueError('展示框模型缺失')
                faces = transform(result.faces, translate(-.5, -.5, -31 / 32))
            item = _get(data, 'Item', 'item', {})
            if item:
                angle = int(_get(data, 'ItemRotation', 'item_rotation', 0)) % 8 * 45
                try:
                    # Native .46875 outward anchor + .4375 local depth;
                    # local +Z points toward the supporting block.
                    depth = .03125 if invisible else -.03125
                    faces += transform(self.item(item), translate(0, 0, depth) @ rotate('z', angle) @ scale(.5))
                except ValueError as exc:
                    self.notes.append(str(exc))
            return transform(faces, _frame_facing(data)), 'rendered'
        if kind == 'painting':
            key = str(data.get('variant', data.get('Motive', 'minecraft:kebab'))).split(':')[-1]
            variant = self.assets.catalog['variants'].get('painting_variant/' + key)
            if variant is None:
                raise ValueError(f'未知画变体：{key}')
            w, h = variant['width'], variant['height']
            tex = variant.get('asset_id', 'minecraft:' + key).replace(':', ':painting/', 1)
            faces = _quad(tex, w, h)
            # A painting is a thin board, not a billboarding sprite.
            back = _cuboid(-w / 2, -h / 2, 0, w / 2, h / 2, 1 / 16, 'minecraft:painting')
            for f in back:
                f.texture = 'minecraft:painting/back'
                f.uvs = [(0, 0), (0, 1), (1, 1), (1, 0)]
            return transform(faces[:1] + [f for f in back if f.normal.z != -1], _facing(data)), 'rendered'
        if kind in {'block_display', 'falling_block', 'tnt'}:
            state = data.get('block_state', data.get('BlockState', {'Name': 'minecraft:tnt' if kind == 'tnt' else 'minecraft:sand'}))
            matrix = _display_matrix(data) if kind == 'block_display' else translate(-.5, 0, -.5)
            return transform(self.block(state), rotation @ matrix), 'rendered'
        if kind in {'item', 'item_display', 'ominous_item_spawner'}:
            matrix = _display_matrix(data) if kind == 'item_display' else translate(0, .125, 0) @ scale(.5)
            context = data.get('item_display', 'none') if kind == 'item_display' else 'ground'
            faces = self.item(_get(data, 'Item', 'item', {}), context)
            return transform(faces, rotation @ matrix), 'rendered' if faces else 'empty'
        if kind in SPRITES:
            faces = self.item(_get(data, 'Item', 'item', {'id': 'minecraft:' + SPRITES[kind]}))
            return transform(faces, rotation @ scale(.5)), 'rendered'
        if kind == 'dragon_fireball':
            return transform(_quad('minecraft:entity/enderdragon/dragon_fireball'), rotation), 'rendered'
        if kind == 'experience_orb':
            return transform(_quad('minecraft:entity/experience/experience_orb', .5, .5,
                [(0, .75), (0, 1), (.25, 1), (.25, .75)]), rotation), 'rendered'
        if kind == 'fishing_bobber':
            return transform(_quad('minecraft:entity/fishing/fishing_hook', .125, .125), rotation), 'rendered'
        if kind == 'text_display':
            return transform(self.text_display(data), rotation @ _display_matrix(data)), 'rendered'
        if kind == 'lightning_bolt':
            return self.lightning(data), 'rendered'
        if kind in {'boat', 'chest_boat'}:
            wood = str(data.get('Type', 'oak'))
            kind = wood + ('_chest_boat' if kind == 'chest_boat' else '_boat')
        return self.layer_entity(kind, data, yaw, pitch), 'rendered'

    def layer_entity(self, kind, data, yaw, pitch):
        layer, texture = self.assets.selection(kind, data)
        if layer not in self.assets.layers:
            raise ValueError(f'缺失实体模型层：{layer}')
        if not self.assets.has_texture(texture):
            raise ValueError(f'缺失实体贴图：{texture}')
        hidden = []
        pose = {}
        if kind == 'armor_stand':
            for key, value in data.get('Pose', data.get('pose', {})).items():
                if key in POSE_NAMES:
                    pose[POSE_NAMES[key]] = vector(value)
            if not data.get('ShowArms', False):
                hidden += ['left_arm', 'right_arm']
            if data.get('NoBasePlate', False):
                hidden += ['base_plate']
        elif pitch:
            pose['head'] = [pitch, 0, 0]
        if kind in {'llama', 'trader_llama', 'donkey', 'mule'} and not data.get('ChestedHorse', False):
            hidden += ['left_chest', 'right_chest']
        if kind in {'evoker', 'vindicator', 'illusioner'}:
            hidden += ['left_arm', 'right_arm']
        if kind == 'end_crystal' and not data.get('ShowBottom', True):
            hidden += ['base']
        if kind == 'goat':
            if not data.get('HasLeftHorn', True):
                hidden += ['left_horn']
            if not data.get('HasRightHorn', True):
                hidden += ['right_horn']
        if kind in {'villager', 'zombie_villager'}:
            villager = data.get('VillagerData', data.get('villager_data', {}))
            biome = str(villager.get('type', 'minecraft:plains')).split(':')[-1]
            profession = str(villager.get('profession', 'minecraft:none')).split(':')[-1]
            root = 'minecraft:entity/' + kind + '/'
            folder = 'baby/' if '_baby#' in layer else 'type/'
            overlays = [root + folder + biome, root + 'profession/' + profession]
            texture = self.compose([texture] + [t for t in overlays if self.assets.has_texture(t)], kind)
        faces = list(self.assets.baked(layer, texture, json.dumps(pose, sort_keys=True), tuple(hidden)))
        def extra(suffix, tex, tint=None):
            other = layer.split('#')[0] + '#' + suffix
            if other in self.assets.layers and self.assets.has_texture(tex):
                if tint:
                    tex = self.loader.tint_texture(tex, tint, label='entity')
                faces.extend(self.assets.baked(other, tex, json.dumps(pose, sort_keys=True), tuple(hidden)))
        if kind == 'sheep' and not data.get('Sheared', data.get('sheared', False)):
            from .conversion import DYE_COLORS, DYE_ORDER
            color = int(data.get('Color', data.get('color', 0))) % 16
            suffix = '_baby' if '_baby#' in layer else ''
            extra('wool', 'minecraft:entity/sheep/sheep_wool' + suffix, DYE_COLORS[DYE_ORDER[color]])
        if kind in {'slime', 'drowned', 'stray', 'bogged'}:
            tex = {'slime': 'slime/slime', 'drowned': 'zombie/drowned_outer_layer',
                   'stray': 'skeleton/stray_overlay', 'bogged': 'skeleton/bogged_overlay'}[kind]
            if '_baby#' in layer and self.assets.has_texture('minecraft:entity/' + tex + '_baby'):
                tex += '_baby'
            extra('outer', 'minecraft:entity/' + tex)
        if kind == 'sulfur_cube':
            extra('inner', 'minecraft:entity/sulfur_cube/sulfur_cube_inner' + ('_small' if 'small' in layer else ''))
        if data.get('Saddle', data.get('saddle', False)):
            extra('saddle', 'minecraft:entity/equipment/' + kind + '_saddle/saddle')
        if kind in {'cat', 'wolf'} and (data.get('Owner') or data.get('owner')):
            from .conversion import DYE_COLORS, DYE_ORDER
            color = DYE_COLORS[DYE_ORDER[int(data.get('CollarColor', 14)) % 16]]
            extra('collar', 'minecraft:entity/' + kind + '/' + kind + '_collar', color)

        # Native living-model convention is Y-down with feet at y=24 pixels.
        matrix = rotate('y', 180 - yaw) @ scale(-1, -1, 1) @ translate(0, -1.501, 0)
        if kind.endswith('minecart'):
            matrix = translate(0, .375, 0) @ rotate('y', 180 - yaw) @ rotate('z', -pitch) @ scale(-1, -1, 1)
        elif kind.endswith(('_boat', '_raft')):
            matrix = translate(0, .375, 0) @ rotate('y', 180 - yaw) @ scale(-1, -1, 1) @ rotate('y', 90)
        elif kind in {'arrow', 'spectral_arrow', 'trident'}:
            matrix = rotate('y', yaw - 90) @ rotate('z', pitch) @ rotate('x', 45) if kind != 'trident' else rotate('y', yaw - 90) @ rotate('z', pitch + 90)
        elif kind in {'wind_charge', 'breeze_wind_charge', 'shulker_bullet', 'leash_knot', 'llama_spit', 'wither_skull'}:
            matrix = rotate('y', 180-yaw) @ scale(-1, -1, 1)
        elif kind == 'end_crystal':
            matrix = scale(2) @ translate(0, -.5, 0)
        if kind == 'giant':
            matrix = scale(6) @ matrix
        elif kind in {'slime', 'magma_cube'}:
            matrix = scale(max(1, min(128, int(data.get('Size', data.get('size', 0))) + 1))) @ matrix
        if kind == 'armor_stand' and data.get('Small', False):
            matrix = scale(.5) @ matrix
        faces = transform(faces, matrix)
        if kind.endswith('minecart'):
            content = {'chest_minecart': 'chest', 'hopper_minecart': 'hopper', 'tnt_minecart': 'tnt',
                       'furnace_minecart': 'furnace', 'spawner_minecart': 'spawner',
                       'command_block_minecart': 'command_block'}.get(kind)
            state = data.get('DisplayState', {'Name': 'minecraft:' + content} if content else None)
            if state:
                try:
                    block = self.block(state)
                    offset = (float(data.get('DisplayOffset', 6)) - 8) / 16
                    # Contents share the cart's full pose before its model-only
                    # Y-down reflection. Native order: S(.75), T, Ry(90).
                    cart_pose = translate(0, .375, 0) @ rotate('y', 180-yaw) @ rotate('z', -pitch)
                    faces += transform(block, cart_pose @ scale(.75)
                                       @ translate(-.5, offset, .5) @ rotate('y', 90))
                except ValueError as exc:
                    self.notes.append(str(exc))
        if kind == 'mooshroom':
            mushroom = 'brown_mushroom' if data.get('Type') == 'brown' else 'red_mushroom'
            for x, y, z in ((0, 1.2, -.2), (0, 1.2, .35), (0, 1.5, -.65)):
                faces += transform(self.block({'Name': 'minecraft:' + mushroom}), rotate('y', -yaw) @ translate(x-.25, y, z-.25) @ scale(.5))
        if data.get('ArmorItems') or data.get('HandItems') or data.get('equipment'):
            self.notes.append('已渲染静态本体；装备/手持物的附着姿态尚未完整复现')
        return faces

    def text_display(self, data):
        from PIL import Image
        raw = data.get('text', '')
        def plain(value):
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                return ''.join(map(plain, value))
            if isinstance(value, dict):
                return str(value.get('text', value.get('translate', ''))) + plain(value.get('extra', []))
            return ''
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                pass
        text = plain(raw)
        if len(text) > 4096:
            raise ValueError('文字展示实体超过 4096 字符，未生成过大贴图')
        if not text:
            return []
        providers = self.loader._read_json('assets/minecraft/font/include/default.json')['providers']
        glyphs = {}
        for provider in providers:
            if provider.get('type') != 'bitmap':
                continue
            atlas = self.loader._texture_image(provider['file'].removesuffix('.png'))
            rows = provider['chars']
            cw, ch = atlas.width // len(rows[0]), atlas.height // len(rows)
            for y, row in enumerate(rows):
                for x, c in enumerate(row):
                    if c in text and c not in glyphs:
                        im = atlas.crop((x*cw, y*ch, (x+1)*cw, (y+1)*ch)).convert('RGBA')
                        bbox = im.getchannel('A').getbbox()
                        width = bbox[2] if bbox else 4
                        glyphs[c] = im.crop((0, 0, max(1, width), ch))
        missing = sorted(set(text) - set(glyphs) - {'\n'})
        if missing:
            self.notes.append('原版内置字体缺少字符：' + ''.join(missing)[:80])
        line_width = max(8, min(2048, int(data.get('line_width', 200))))
        rows = [[]]
        width = 0
        for c in text:
            im = glyphs.get(c, Image.new('RGBA', (4, 8), (255, 255, 255, 255)))
            if c == '\n' or width + im.width + 1 > line_width:
                rows.append([])
                width = 0
                if c == '\n':
                    continue
            rows[-1].append(im)
            width += im.width + 1
        w = max(1, max(sum(im.width + 1 for im in row) for row in rows))
        h = max(1, len(rows) * 9)
        bg = int(data.get('background', 0x40000000)) & 0xffffffff
        out = Image.new('RGBA', (w, h), ((bg >> 16)&255, (bg >> 8)&255, bg&255, bg>>24))
        for y, row in enumerate(rows):
            x = 0
            for im in row:
                out.alpha_composite(im, (x, y*9))
                x += im.width + 1
        buf = io.BytesIO(); out.save(buf, format='PNG')
        key = 'generated:entity_text_' + hashlib.sha256(buf.getvalue()).hexdigest()[:24]
        self.loader._generated_textures[key] = buf.getvalue()
        self.notes.append('文字展示为静态原版位图字形；富文本样式和动态朝向不参与世界模拟')
        return _quad(key, w / 40, h / 40)

    def lightning(self, data):
        import random
        seed = hashlib.sha256(json.dumps(data.get('UUID', data.get('Pos', []))).encode()).digest()
        rng = random.Random(seed)
        faces = []
        x = z = 0
        from PIL import Image
        key = 'generated:entity_lightning'
        buf = io.BytesIO(); Image.new('RGBA', (1, 1), (220, 225, 255, 210)).save(buf, format='PNG')
        self.loader._generated_textures[key] = buf.getvalue()
        for i in range(8):
            nx, nz = x + rng.uniform(-2, 2), z + rng.uniform(-2, 2)
            for axis in (0, 2):
                delta = np.zeros(3); delta[axis] = .15
                a, b = np.array([x, i*4, z]), np.array([nx, (i+1)*4, nz])
                pts = [a-delta, a+delta, b+delta, b-delta]
                uv = [(0, 0), (1, 0), (1, 1), (0, 1)]
                faces.append(face(pts, uv, key))
            x, z = nx, nz
        self.notes.append('闪电使用固定种子的静态折线；原版瞬时随机闪电无法由投影恢复')
        return faces


def append_scene_entities(schematic, selected, origin, mesh, loader, options, progress, cancelled):
    from .conversion import ConversionCancelled
    report = dict(enabled=True, source=0, rendered=0, invisible=0, empty=0, duplicates=0,
                  fallback_cubes=0, ignored=0, errors=0, types={}, events=[], mode='static-experimental')
    progress('entities', .75, '实验性：读取原版实体模型...')
    renderer = EntityRenderer(loader)
    seen = set()
    counts = Counter()
    # Litematica only rewrites the root Pos when serializing passengers in
    # older versions. Prefer separately saved top-level passengers with local
    # coordinates; do not mistake nested absolute world positions for local ones.
    roots = [(region, entity) for name, region in schematic.regions.items()
             if name in selected for entity in region.entities]
    top_uuids = set()
    def uid(data):
        value = data.get('UUID', data.get('uuid'))
        if value is None and 'UUIDMost' in data:
            value = [data['UUIDMost'], data.get('UUIDLeast', 0)]
        return json.dumps(value, sort_keys=True) if value is not None else None
    for _, raw in roots:
        if isinstance(raw, dict):
            data = raw.get('EntityData', raw)
            if isinstance(data, dict) and uid(data):
                top_uuids.add(uid(data))
    queue = [(r, raw, None, 0) for r, raw in reversed(roots)]
    try:
        while queue:
            if cancelled():
                raise ConversionCancelled()
            region, raw, parent_pos, depth = queue.pop()
            report['source'] += 1
            position = None
            entity_id = '<invalid>'
            try:
                if not isinstance(raw, dict):
                    raise ValueError('实体 NBT 不是 compound')
                data = raw.get('EntityData', raw)
                if not isinstance(data, dict):
                    raise ValueError('EntityData 不是 compound')
                entity_id = identifier(data.get('id', data.get('Id', '')))
                unique = uid(data)
                if unique and unique in seen:
                    report['duplicates'] += 1
                    continue
                if unique:
                    seen.add(unique)
                local = raw.get('Pos', data.get('Pos', data.get('pos')))
                if 'EntityData' in raw and local is None:
                    local = [raw.get(k) for k in ('x', 'y', 'z')]
                local = vector(local)
                position = tuple(local[i] + region.position[i] - origin[i] for i in range(3))
                notes = []
                if parent_pos is not None:
                    # No reliable schematic-world origin exists for nested old
                    # passenger NBT. Anchor to its vehicle and report this pose.
                    position = (parent_pos[0], parent_pos[1] + .75, parent_pos[2])
                    notes.append('嵌套乘客未独立保存：使用载具座位近似位置')
                passengers = data.get('Passengers', data.get('passengers', []))
                if depth < 16 and isinstance(passengers, list):
                    for passenger in reversed(passengers):
                        if isinstance(passenger, dict) and uid(passenger) not in top_uuids:
                            queue.append((region, passenger, position, depth + 1))
                elif passengers:
                    notes.append('乘客嵌套过深或格式异常，未继续展开')
                faces, status = renderer.render(data)
                for f in faces:
                    if f.texture and loader.texture_bytes(f.texture) is None:
                        raise ValueError(f'实体贴图缺失：{f.texture}')
                if faces:
                    mesh.add_faces(faces, offset=position)
                    report['rendered'] += 1
                else:
                    report['invisible' if status == 'invisible' else 'empty'] += 1
                counts[entity_id] += 1
                for note in notes + renderer.notes:
                    report['events'].append(dict(entity=entity_id, region=region.name, position=position,
                                                 action='warning', detail=note))
            except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
                report['errors'] += 1
                action = 'ignore'
                if position is not None and options.fallback == 'cube':
                    mesh.add_faces(_cuboid(-.5, 0, -.5, .5, 1, .5, entity_id), offset=position)
                    report['fallback_cubes'] += 1
                    action = 'cube'
                else:
                    report['ignored'] += 1
                report['events'].append(dict(entity=entity_id, region=region.name, position=position,
                                             action=action, detail=str(exc)))
            if report['source'] % 100 == 0 or not queue:
                progress('entities', .75 + .035 * report['source'] / max(report['source'] + len(queue), 1),
                         f"生成实体：{report['source']}/{report['source'] + len(queue)}；已渲染 {report['rendered']}，异常 {report['errors']}")
    finally:
        renderer.assets.close()
    report['types'] = dict(sorted(counts.items()))
    if not roots:
        progress('entities', .785, '投影内未保存实体；请在 Litematica 保存投影时包含实体。')
    return report
