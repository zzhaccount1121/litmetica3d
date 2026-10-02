import pytest
from litmetica3d.entity_models import get_entity_geometry, _box, _facing_y


def signature(faces):
    return [([(v.x,v.y,v.z) for v in f.vertices],f.uvs,f.texture) for f in faces]


@pytest.mark.parametrize('kind,width', [('single',14),('left',15),('right',15)])
def test_lid_upper_island_and_native_v_direction(kind,width):
    faces=get_entity_geometry('minecraft:chest',{'type':kind,'facing':'south'},visual=True)
    top=faces[7]
    assert all(v.y == 14/16 for v in top.vertices)
    raw=[(u*64,(1-v)*64) for u,v in top.uvs]
    assert min(u for u,v in raw)==14+width
    assert max(u for u,v in raw)==14+2*width
    # north row samples bottom edge of this island, south row its top edge
    assert [v for u,v in raw]==[14,0,0,14]
    assert min(v.y for f in faces[6:12] for v in f.vertices)==9/16


@pytest.mark.parametrize('facing,offset', [('south',(1,0)),('north',(-1,0)),('east',(0,-1)),('west',(0,1))])
def test_double_halves_meet_and_latches_join(facing,offset):
    right=get_entity_geometry('minecraft:chest',{'type':'right','facing':facing},visual=True)
    left=get_entity_geometry('minecraft:chest',{'type':'left','facing':facing},visual=True)
    def bounds(faces,dx=0,dz=0):
        points=[(v.x+dx,v.z+dz) for f in faces for v in f.vertices]
        return [(min(p[i] for p in points),max(p[i] for p in points)) for i in (0,1)]
    axis=0 if offset[0] else 1
    a=bounds(right[:12])[axis]
    b=bounds(left[:12],*offset)[axis]
    a,b=sorted([a,b])
    assert a[1]==pytest.approx(b[0])
    assert b[1]-a[0]==pytest.approx(30/16)
    locks=bounds(right[12:])[axis],bounds(left[12:],*offset)[axis]
    a,b=sorted(locks)
    assert a[1]==pytest.approx(b[0])
    assert b[1]-a[0]==pytest.approx(2/16)


@pytest.mark.parametrize('facing,axis,positive', [('south',2,True),('north',2,False),('east',0,True),('west',0,False)])
def test_lock_faces_requested_direction(facing,axis,positive):
    faces=get_entity_geometry('minecraft:chest',{'type':'single','facing':facing},visual=True)
    coords=[(v.x,v.y,v.z)[axis] for f in faces[12:] for v in f.vertices]
    assert (min(coords)>0.9) if positive else (max(coords)<0.1)


@pytest.mark.parametrize('name',['chest','trapped_chest','ender_chest','copper_chest'])
def test_all_chest_textures_use_same_top_net(name):
    faces=get_entity_geometry('minecraft:'+name,{'type':'single','facing':'south'},visual=True)
    assert faces[7].uvs==[(28/64,1-14/64),(28/64,1),(42/64,1),(42/64,1-14/64)]
    assert all(0<=c<=1 for f in faces for uv in f.uvs for c in uv)


@pytest.mark.parametrize('kind',['single','left','right'])
@pytest.mark.parametrize('facing',['south','north','east','west'])
def test_print_chest_has_correct_half_and_lock(kind,facing):
    x1,x2=(1,16) if kind=='right' else (0,15) if kind=='left' else (1,15)
    lx1,lx2=(15,16) if kind=='right' else (0,1) if kind=='left' else (7,9)
    original=[]
    lock=(7,7,14.75,9,12,16) if kind=='single' else (lx1,7,15,lx2,11,16)
    for box,origin in [((x1,0,1,x2,10,15),(0,19)),((x1,10,1,x2,14,15),(0,0)),(lock,(0,0))]:
        original.extend(_box(box,'minecraft:chest',uv_origin=origin))
    from litmetica3d.entity_models import _rotate
    angle={'south':0,'north':180,'east':90,'west':-90}[facing]
    original=_rotate(original,'y',angle) if angle else original
    actual=get_entity_geometry('minecraft:chest',{'type':kind,'facing':facing},visual=False)
    assert signature(original)==signature(actual)
