"""Surface coverage and native UV checks, not only serialized fingerprints."""
import json
from pathlib import Path
import numpy as np
import pytest
from litmetica3d.shulker_visual import _parts, _clip_polygon, geometry
from litmetica3d.entity_models import get_entity_geometry, _box, _rotate

DATA = json.loads((Path(__file__).parents[1]/'litmetica3d/data/shulker_box.json').read_text())
MASK = np.array([[c == '1' for c in row] for row in DATA['alpha_mask']])


def test_closed_exterior_has_exactly_one_surface_at_every_pixel():
    faces = geometry('minecraft:entity/shulker/shulker')
    for axis in range(3):
        other = [a for a in range(3) if a != axis]
        for sign in (-1, 1):
            plane = .00025 if sign < 0 else .99975
            coverage = np.zeros((16,16), dtype=int)
            for f in faces:
                p = np.array([[v.x,v.y,v.z] for v in f.vertices])
                n = np.array([f.normal.x,f.normal.y,f.normal.z])
                if n[axis]*sign < .999 or not np.allclose(p[:,axis], plane):
                    continue
                lo = np.rint((p[:,other].min(axis=0)-.00025)/.9995*16).astype(int)
                hi = np.rint((p[:,other].max(axis=0)-.00025)/.9995*16).astype(int)
                coverage[lo[0]:hi[0],lo[1]:hi[1]] += 1
            assert np.all(coverage == 1), (axis, sign, coverage)


def test_opaque_cutout_preserves_native_affine_uv_projection():
    for part in DATA['children'].values():
        for cube in part['cubes']:
            for poly in cube:
                raw = np.array(poly)
                mapping = np.linalg.pinv(np.stack((raw[1,:3]-raw[0,:3],raw[3,:3]-raw[0,:3]))) @ np.stack((raw[1,3:]-raw[0,3:],raw[3,3:]-raw[0,3:]))
                for piece in _clip_polygon(poly, MASK):
                    piece = np.array(piece)
                    np.testing.assert_allclose(piece[:,3:], raw[0,3:] + (piece[:,:3]-raw[0,:3]) @ mapping, atol=1e-12)


def test_real_inner_wall_and_rim_thickness():
    base = _parts()['base']['cubes'][0]
    # Native outer walls at x=+-8; real inner walls exactly one pixel inward.
    walls = []
    for poly in base:
        p = np.array(poly)[:,:3]
        normal = np.cross(p[1]-p[0],p[2]-p[0])
        if np.ptp(p[:,0]) == 0:
            walls.append((p[0,0], np.sign(normal[0])))
    for wall in [(-8,-1),(-7,1),(7,-1),(8,1)]:
        assert wall in walls
    # Every emitted patch samples opaque pixels, including new rim/inner faces.
    for part in _parts().values():
        for poly in part['cubes'][0]:
            uv = np.array(poly)[:,3:]
            for s in (.1,.5,.9):
                for t in (.1,.5,.9):
                    u,v = uv[0] + s*(uv[1]-uv[0]) + t*(uv[3]-uv[0])
                    assert MASK[min(63,int(v*64)),min(63,int(u*64))]


@pytest.mark.parametrize('facing',['up','down','north','south','west','east'])
def test_orientation_and_opening_remain_finite(facing):
    closed = geometry('minecraft:entity/shulker/shulker', facing)
    pts = np.array([[v.x,v.y,v.z] for f in closed for v in f.vertices])
    np.testing.assert_allclose(pts.min(axis=0), [.00025]*3, atol=1e-12)
    np.testing.assert_allclose(pts.max(axis=0), [.99975]*3, atol=1e-12)
    for openness in (0,.25,.5,.75,1):
        for f in geometry('minecraft:entity/shulker/shulker', facing, openness):
            p = np.array([[v.x,v.y,v.z] for v in f.vertices])
            assert np.isfinite(p).all()
            assert np.linalg.norm(np.cross(p[1]-p[0],p[2]-p[0])) > 1e-9


@pytest.mark.parametrize('facing,axis,angle',[('up','x',0),('down','x',180),('north','x',-90),('south','x',90),('west','z',90),('east','z',-90)])
def test_print_geometry_is_unchanged(facing,axis,angle):
    old = _box((0,0,0,16,8,16),'minecraft:shulker_box',None,(0,28))
    old += _box((0,8,0,16,16,16),'minecraft:shulker_box',None,(0,0))
    if angle:
        old = _rotate(old,axis,angle)
    assert get_entity_geometry('minecraft:shulker_box',{'facing':facing}) == old
