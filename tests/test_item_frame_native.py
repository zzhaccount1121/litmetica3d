"""Numeric contracts transcribed from Minecraft 26.2 ItemFrameRenderer."""
import numpy as np
import pytest
from litmetica3d.conversion import bundled_asset_path
from litmetica3d.model_loader import ModelLoader
from litmetica3d.scene_entities import EntityRenderer


@pytest.fixture(scope='module')
def renderer():
    loader = ModelLoader(bundled_asset_path(), visual_textures=True)
    r = EntityRenderer(loader)
    yield r
    r.assets.close()
    loader.close()


def vertices(faces):
    return np.array([[v.x, v.y, v.z] for f in faces for v in f.vertices])


def test_gold_block_fixed_scale_from_item_definition(renderer):
    pts = vertices(renderer.item({'id': 'minecraft:gold_block'}))
    np.testing.assert_allclose(np.ptp(pts, axis=0), [.5, .5, .5])
    faces, _ = renderer.render({'id': 'minecraft:item_frame', 'Facing': 2,
                               'Item': {'id': 'minecraft:gold_block'}})
    item = [f for f in faces if f.texture == 'minecraft:block/gold_block']
    np.testing.assert_allclose(np.ptp(vertices(item), axis=0), [.25, .25, .25])


def test_display_contexts_inherit_individually(renderer):
    model = renderer.loader._load_model('minecraft:item/handheld')
    assert model['display']['fixed']['rotation'] == [0, 180, 0]
    assert model['display']['firstperson_righthand']['rotation'] != [0, 180, 0]


@pytest.mark.parametrize('item', ['carrot', 'golden_carrot'])
@pytest.mark.parametrize('direction', range(6))
@pytest.mark.parametrize('rotation', range(8))
def test_frame_sprite_uv_world_axes(renderer, item, direction, rotation):
    faces, _ = renderer.render({'id': 'minecraft:item_frame', 'Facing': direction,
        'ItemRotation': rotation, 'Item': {'id': 'minecraft:' + item}})
    f = next(f for f in faces if f.texture == 'minecraft:item/' + item)
    # Native generated north face, then FIXED Ry180: U increases with X.
    # Columns below are transformed local X/Y/Z from original frame renderer.
    axes = [(( -1,0,0),(0,0,1),(0,1,0)),
            (( -1,0,0),(0,0,-1),(0,-1,0)),
            ((1,0,0),(0,1,0),(0,0,1)),
            ((-1,0,0),(0,1,0),(0,0,-1)),
            ((0,0,-1),(0,1,0),(1,0,0)),
            ((0,0,1),(0,1,0),(-1,0,0))]
    basis = np.array(axes[direction], dtype=float).T
    a = np.deg2rad(rotation*45)
    rz = np.array([[np.cos(a),-np.sin(a),0],[np.sin(a),np.cos(a),0],[0,0,1]])
    uv = np.array(f.uvs)
    local = np.column_stack(((uv[:,0]-.5)*.5, (uv[:,1]-.5)*.5, np.zeros(len(uv))))
    expected = (local @ rz.T + [0,0,-.03125]) @ basis.T
    np.testing.assert_allclose(vertices([f]), expected, atol=1e-12)


def test_unsupported_entity_fields_are_reported(renderer):
    renderer.render({'id':'minecraft:pig', 'CustomVisualProperty': 123})
    assert any('CustomVisualProperty' in note for note in renderer.notes)


def test_item_components_not_silently_claimed_as_supported(renderer):
    renderer.render({'id':'minecraft:item_frame', 'Item':{'id':'minecraft:carrot',
        'components':{'minecraft:custom_model_data':{'strings':['custom']}}}})
    assert any('minecraft:custom_model_data' in note for note in renderer.notes)


@pytest.mark.parametrize('kind,payload', [
    ('block_display', {'block_state':{'Name':'minecraft:stone'}}),
    ('item_display', {'item':{'id':'minecraft:carrot'}}),
    ('text_display', {'text':'A'}),
])
def test_display_pitch_is_not_discarded(renderer, kind, payload):
    original, _ = renderer.render({'id':'minecraft:'+kind, **payload})
    pitched, _ = renderer.render({'id':'minecraft:'+kind, **payload, 'Rotation':[0,90]})
    pts = vertices(original)
    expected = np.column_stack((pts[:,0], -pts[:,2], pts[:,1]))
    np.testing.assert_allclose(vertices(pitched), expected, atol=1e-12)


def test_unknown_variant_reports_default(renderer):
    renderer.render({'id':'minecraft:pig', 'variant':'minecraft:missing'})
    assert any('默认变种' in note for note in renderer.notes)
