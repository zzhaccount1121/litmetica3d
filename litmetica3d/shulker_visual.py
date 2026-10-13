"""Original ShulkerBox ModelPart vertices/UVs, independent of printable geometry."""
from functools import lru_cache
import json
from pathlib import Path

import numpy as np

from .entity_assets import face, rotate, scale, translate


@lru_cache(maxsize=1)
def _parts():
    data = json.loads((Path(__file__).parent / 'data/shulker_box.json').read_text(encoding='utf-8'))
    mask = np.array([[c == '1' for c in row] for row in data['alpha_mask']])
    for part in data['children'].values():
        part['cubes'] = [_shell(cube, mask) for cube in part['cubes']]
    return data['children']


def _shell(cube, mask):
    """One-pixel INWARD walls, exact native exterior, connected inner/rim faces.

    The baked game model uses alpha-cut planes and a painted one-pixel rim.
    Reconstruct that rim as real geometry for Blender's two-sided ray tracing.
    This is a local, exact integer-pixel shell, not a scene voxel approximation.
    """
    from collections import defaultdict
    from itertools import product
    sources, cells = [], defaultdict(list)
    for polygon in cube:
        raw = np.asarray(polygon, dtype=float)
        normal = np.cross(raw[1, :3]-raw[0, :3], raw[2, :3]-raw[0, :3])
        axis = int(np.argmax(np.abs(normal)))
        sign = int(np.sign(normal[axis]))
        mapping = np.linalg.pinv(np.stack((raw[1, :3]-raw[0, :3], raw[3, :3]-raw[0, :3]))) @ np.stack((raw[1, 3:]-raw[0, 3:], raw[3, 3:]-raw[0, 3:]))
        source = len(sources)
        sources.append((raw, axis, sign, mapping))
        for clipped in _clip_polygon(polygon, mask):
            points = np.asarray(clipped)[:, :3]
            lo, hi = np.rint(points.min(axis=0)).astype(int), np.rint(points.max(axis=0)).astype(int)
            if sign > 0:
                lo[axis] -= 1
            else:
                hi[axis] += 1
            for cell in product(*(range(lo[a], hi[a]) for a in range(3))):
                cells[cell].append(source)
    groups = defaultdict(set)
    for cell, owners in cells.items():
        for axis in range(3):
            tangents = [a for a in range(3) if a != axis]
            for sign in (-1, 1):
                neighbor = list(cell); neighbor[axis] += sign
                if tuple(neighbor) in cells:
                    continue
                plane = cell[axis] + (sign > 0)
                parallel = [s for s in owners if sources[s][1] == axis]
                source = next((s for s in parallel if sources[s][2] == sign), parallel[0] if parallel else owners[0])
                groups[(axis, sign, plane, source)].add(tuple(cell[a] for a in tangents))
    result = []
    for (axis, sign, plane, source), remaining in sorted(groups.items()):
        raw, source_axis, _, mapping = sources[source]
        a, b = [i for i in range(3) if i != axis]
        while remaining:
            x, y = min(remaining, key=lambda p: (p[1], p[0]))
            xe, ye = x+1, y+1
            while (xe, y) in remaining:
                xe += 1
            while all((xx, ye) in remaining for xx in range(x, xe)):
                ye += 1
            remaining.difference_update(product(range(x, xe), range(y, ye)))
            points = np.zeros((4, 3))
            points[:, axis] = plane
            points[:, a] = [x, xe, xe, x]
            points[:, b] = [y, y, ye, ye]
            sample = points.copy()
            if source_axis != axis:
                # The cut edge stretches the adjacent pixel into the wall.
                # Sample its centre, not the alpha-zero pixel across the cut.
                sample[:, axis] -= sign * .5
            uv = raw[0, 3:] + (sample-raw[0, :3]) @ mapping
            if np.cross(points[1]-points[0], points[2]-points[0])[axis] * sign < 0:
                points, uv = points[::-1], uv[::-1]
            result.append(np.column_stack((points, uv)).tolist())
    return result


def _clip_polygon(polygon, mask):
    """Native UV-aligned cutout rectangles, with no displacement or UV rewrite.

    Minecraft discards alpha-zero fragments before depth testing. A ray tracer
    can skip BOTH exactly coplanar faces after hitting one transparent fragment.
    Removing the discarded geometry makes complementary lid/base pixels visible.
    """
    raw = np.asarray(polygon, dtype=float)
    h, w = mask.shape
    uv = raw[:, 3:] * [w, h]
    x0, y0 = np.rint(uv.min(axis=0)).astype(int)
    x1, y1 = np.rint(uv.max(axis=0)).astype(int)
    remaining = mask[y0:y1, x0:x1].copy()
    uv_basis = np.stack((uv[1]-uv[0], uv[3]-uv[0]))
    xyz_basis = np.stack((raw[1, :3]-raw[0, :3], raw[3, :3]-raw[0, :3]))
    mapping = np.linalg.solve(uv_basis, xyz_basis)
    normal = np.cross(raw[1, :3]-raw[0, :3], raw[2, :3]-raw[0, :3])
    while remaining.any():
        y, x = np.argwhere(remaining)[0]
        xe = x + 1
        while xe < remaining.shape[1] and remaining[y, xe]:
            xe += 1
        ye = y + 1
        while ye < remaining.shape[0] and remaining[ye, x:xe].all():
            ye += 1
        remaining[y:ye, x:xe] = False
        corners = np.array([[x, y], [xe, y], [xe, ye], [x, ye]]) + [x0, y0]
        points = raw[0, :3] + (corners-uv[0]) @ mapping
        if np.dot(np.cross(points[1]-points[0], points[2]-points[0]), normal) < 0:
            points, corners = points[::-1], corners[::-1]
        yield np.column_stack((points, corners / [w, h])).tolist()


def geometry(texture, facing='up', openness=0.0):
    """openness is a preview/runtime pose (0..1), not a saved block property."""
    openness = float(openness)
    if not np.isfinite(openness) or not 0 <= openness <= 1:
        raise ValueError('潜影盒开合进度必须在 0..1')
    direction = {'up': np.eye(4), 'down': rotate('x', 180),
                 'north': rotate('x', -90), 'south': rotate('x', 90),
                 'west': rotate('z', 90), 'east': rotate('z', -90)}[facing]
    # Exact vanilla block renderer transform (including its anti-z-fighting scale).
    outer = translate(.5, .5, .5) @ scale(.9995) @ direction @ scale(1, -1, -1) @ translate(0, -1, 0)
    result = []
    for name, part in _parts().items():
        x, y, z = part['pose'][:3]
        turn = 0
        if name == 'lid':
            y -= openness * 8
            turn = 270 * openness
        matrix = outer @ translate(x/16, y/16, z/16) @ rotate('y', turn)
        for cube in part['cubes']:
            for polygon in cube:
                vertices = np.array(polygon)
                coords = np.column_stack((vertices[:, :3] / 16, np.ones(4))) @ matrix.T
                f = face(coords[:, :3], np.column_stack((vertices[:, 3], 1-vertices[:, 4])), texture)
                if f:
                    result.append(f)
    return result
