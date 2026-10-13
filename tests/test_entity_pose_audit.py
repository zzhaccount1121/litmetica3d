import numpy as np
import pytest
from test_item_frame_native import renderer, vertices


@pytest.mark.parametrize('yaw', [0, 45, 90, 180, 270])
@pytest.mark.parametrize('pitch', [-30, 0, 30])
def test_minecart_contents_follow_native_full_pose(renderer, yaw, pitch):
    faces, _ = renderer.render({'id':'minecraft:minecart','Rotation':[yaw,pitch],
        'DisplayState':{'Name':'minecraft:gold_block'},'DisplayOffset':6})
    item = vertices([f for f in faces if f.texture=='minecraft:block/gold_block'])
    raw = vertices(renderer.block({'Name':'minecraft:gold_block'}))
    # Independent arithmetic for native S(.75)*T(-.5,-.125,.5)*Ry(90).
    local = .75*np.column_stack((raw[:,2]-.5, raw[:,1]-.125, .5-raw[:,0]))
    p,y = np.deg2rad([-pitch,180-yaw])
    rz = np.array([[np.cos(p),-np.sin(p),0],[np.sin(p),np.cos(p),0],[0,0,1]])
    ry = np.array([[np.cos(y),0,np.sin(y)],[0,1,0],[-np.sin(y),0,np.cos(y)]])
    expected = local @ rz.T @ ry.T + [0,.375,0]
    np.testing.assert_allclose(item,expected,atol=1e-12)


@pytest.mark.parametrize('kind', ['pig','cow','sheep','chicken','wolf','cat'])
@pytest.mark.parametrize('yaw', [90,180,270])
def test_animals_saved_yaw_rotates_all_vertices(renderer,kind,yaw):
    base,_ = renderer.render({'id':'minecraft:'+kind})
    turned,_ = renderer.render({'id':'minecraft:'+kind,'Rotation':[yaw,0]})
    v=vertices(base);a=np.deg2rad(-yaw)
    expected=np.column_stack((v[:,0]*np.cos(a)+v[:,2]*np.sin(a),v[:,1],
                              -v[:,0]*np.sin(a)+v[:,2]*np.cos(a)))
    np.testing.assert_allclose(vertices(turned),expected,atol=1e-12)


@pytest.mark.parametrize('kind', ['pig','cow','sheep','chicken'])
def test_baby_uses_smaller_geometry(renderer,kind):
    adult,_=renderer.render({'id':'minecraft:'+kind})
    baby,_=renderer.render({'id':'minecraft:'+kind,'Age':-100})
    assert np.ptp(vertices(baby),axis=0)[1] < np.ptp(vertices(adult),axis=0)[1]


def test_pig_variants_change_native_texture(renderer):
    textures=[]
    for variant in ['temperate','warm','cold']:
        faces,_=renderer.render({'id':'minecraft:pig','variant':'minecraft:'+variant})
        textures.append(frozenset(f.texture for f in faces))
    assert len(set(textures))==3
