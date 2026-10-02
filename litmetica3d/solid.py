"""Exact printable-solid pipeline backed by manifold3d."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np
import manifold3d as m3d

from .block_models import Face, Vec3
from .mesh import Mesh


@dataclass
class CavityInfo:
    volume: float
    bounds: tuple[float, float, float, float, float, float]


class BooleanCancelled(Exception):
    pass


@dataclass
class SolidReport:
    boolean_inputs: int = 0
    chunk_solids: int = 0
    boolean_failures: int = 0
    voxel_fallbacks: int = 0
    component_count: int = 0
    removed_components: int = 0
    removed_component_volume: float = 0.0
    retained_component_count: int = 0
    main_component_volume: float = 0.0
    main_component_bounds: tuple[float, float, float, float, float, float] | None = None
    cavity_count: int = 0
    cavities: list[CavityInfo] = field(default_factory=list)
    volume_before_cavity_fill: float = 0.0
    volume_after_cavity_fill: float = 0.0
    filled_cavity_volume: float = 0.0
    open_edges: int = 0
    nonmanifold_edges: int = 0
    orientation_conflicts: int = 0
    degenerate_triangles: int = 0
    self_intersections: int = 0
    printable: bool = False


def _faces_to_mesh(faces: list[Face]) -> m3d.Mesh:
    index: dict[tuple[float, float, float], int] = {}
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    for face in faces:
        ids = []
        for vertex in face.vertices:
            key = (round(vertex.x, 12), round(vertex.y, 12), round(vertex.z, 12))
            if key not in index:
                index[key] = len(vertices)
                vertices.append(key)
            ids.append(index[key])
        if len(ids) == 4:
            triangles.extend(((ids[0], ids[1], ids[2]),
                              (ids[0], ids[2], ids[3])))
    return m3d.Mesh(
        np.asarray(vertices, dtype=np.float32),
        np.asarray(triangles, dtype=np.uint32),
        tolerance=1e-7,
    )


def manifold_from_closed_faces(faces: list[Face]) -> m3d.Manifold:
    """Convert one or more independently closed six-face elements to a solid."""
    parts = []
    for start in range(0, len(faces), 6):
        group = faces[start:start + 6]
        if len(group) != 6:
            raise ValueError("closed solid face group is not a multiple of six")
        part = m3d.Manifold(_faces_to_mesh(group))
        if part.status() != m3d.Error.NoError or part.is_empty():
            raise ValueError(f"invalid closed element: {part.status()}")
        parts.append(part)
    return union_balanced(parts)


def cube_solid(
    size: tuple[float, float, float],
    origin: tuple[float, float, float],
) -> m3d.Manifold:
    return m3d.Manifold.cube(size).translate(origin)


def greedy_cube_boxes(positions: set[tuple[int, int, int]]):
    """Consume unit cubes into deterministic maximal axis-aligned boxes."""
    remaining = set(positions)
    ordered = sorted(positions, key=lambda p: (p[1], p[2], p[0]))
    for candidate in ordered:
        if candidate not in remaining:
            continue
        x0, y0, z0 = candidate
        x1 = x0
        while (x1 + 1, y0, z0) in remaining:
            x1 += 1
        z1 = z0
        while all(
            (x, y0, z1 + 1) in remaining for x in range(x0, x1 + 1)
        ):
            z1 += 1
        y1 = y0
        while all(
            (x, y1 + 1, z) in remaining
            for z in range(z0, z1 + 1)
            for x in range(x0, x1 + 1)
        ):
            y1 += 1
        for y in range(y0, y1 + 1):
            for z in range(z0, z1 + 1):
                for x in range(x0, x1 + 1):
                    remaining.remove((x, y, z))
        yield (x0, y0, z0), (x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1)


def split_box_by_chunks(origin, size, chunk_size: int = 16):
    x0, y0, z0 = origin
    sx, sy, sz = size
    x = x0
    while x < x0 + sx:
        ex = min(x0 + sx, (x // chunk_size + 1) * chunk_size)
        y = y0
        while y < y0 + sy:
            ey = min(y0 + sy, (y // chunk_size + 1) * chunk_size)
            z = z0
            while z < z0 + sz:
                ez = min(z0 + sz, (z // chunk_size + 1) * chunk_size)
                yield (x, y, z), (ex - x, ey - y, ez - z)
                z = ez
            y = ey
        x = ex


def is_unit_cube_faces(faces: list[Face]) -> bool:
    if len(faces) != 6:
        return False
    points = {
        (round(v.x, 9), round(v.y, 9), round(v.z, 9))
        for face in faces for v in face.vertices
    }
    return (
        len(points) == 8
        and all(value in {0.0, 1.0} for point in points for value in point)
    )


def face_geometry_key(faces: list[Face]) -> tuple:
    return tuple(
        tuple((round(v.x, 8), round(v.y, 8), round(v.z, 8))
              for v in face.vertices)
        for face in faces
    )


def voxel32_fallback(faces: list[Face]) -> m3d.Manifold:
    """Create a level-set union of transformed cuboids at 1/32-block detail."""
    boxes = []
    all_points = []
    for start in range(0, len(faces), 6):
        points = sorted({
            (float(v.x), float(v.y), float(v.z))
            for face in faces[start:start + 6] for v in face.vertices
        })
        if len(points) != 8:
            continue
        all_points.extend(points)
        box = _oriented_box(points)
        if box is not None:
            boxes.append(box)
    if not boxes:
        raise ValueError("voxel fallback could not recover any closed elements")
    pts = np.asarray(all_points)
    margin = 1 / 32
    lower = pts.min(axis=0) - margin
    upper = pts.max(axis=0) + margin

    def field(x: float, y: float, z: float) -> float:
        point = np.asarray((x, y, z), dtype=float)
        best = -1e30
        for center, axes, half in boxes:
            local = np.abs(axes.T @ (point - center)) - half
            outside = np.linalg.norm(np.maximum(local, 0.0))
            inside = min(float(np.max(local)), 0.0)
            # Standard SDF is negative inside; Manifold level_set expects positive.
            best = max(best, -(outside + inside))
        return best

    result = m3d.Manifold.level_set(
        field, (*lower.tolist(), *upper.tolist()), 1 / 32, 0.0, 1 / 128
    )
    if result.status() != m3d.Error.NoError or result.is_empty():
        raise ValueError(f"voxel32 fallback failed: {result.status()}")
    return result


def _oriented_box(points):
    for origin_tuple in points:
        origin = np.asarray(origin_tuple)
        vectors = [
            np.asarray(point) - origin for point in points if point != origin_tuple
        ]
        for i in range(len(vectors)):
            for j in range(i + 1, len(vectors)):
                for k in range(j + 1, len(vectors)):
                    basis = np.column_stack((vectors[i], vectors[j], vectors[k]))
                    if abs(np.linalg.det(basis)) < 1e-10:
                        continue
                    inv = np.linalg.inv(basis)
                    coeffs = [inv @ (np.asarray(point) - origin) for point in points]
                    if all(np.all(np.isclose(c, np.round(c), atol=1e-6))
                           and np.all((c >= -1e-6) & (c <= 1 + 1e-6))
                           for c in coeffs):
                        lengths = np.linalg.norm(basis, axis=0)
                        if np.any(lengths < 1e-10):
                            continue
                        axes = basis / lengths
                        if not np.allclose(axes.T @ axes, np.eye(3), atol=1e-5):
                            continue
                        center = origin + basis @ np.asarray((0.5, 0.5, 0.5))
                        return center, axes, lengths / 2
    return None


def union_balanced(
    parts: Iterable[m3d.Manifold],
    batch_size: int | None = None,
    *,
    progress: Callable[[int, int, int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> m3d.Manifold:
    current = [part for part in parts if not part.is_empty()]
    if not current:
        return m3d.Manifold()
    if batch_size is None:
        batch_size = 8 if len(current) <= 64 else (
            16 if len(current) <= 512 else 32
        )
    estimate = len(current)
    estimated_batches = 0
    while estimate > 1:
        estimate = (estimate + batch_size - 1) // batch_size
        estimated_batches += estimate
    completed_batches = 0
    round_index = 0
    while len(current) > 1:
        if cancelled is not None and cancelled():
            raise BooleanCancelled()
        round_index += 1
        following = []
        for start in range(0, len(current), batch_size):
            if cancelled is not None and cancelled():
                raise BooleanCancelled()
            if progress is not None and start == 0:
                # Report before entering the native boolean call. A difficult
                # first batch can take a while and must not look frozen.
                progress(
                    completed_batches,
                    max(1, estimated_batches),
                    round_index,
                    len(following) + len(current) - start,
                )
            following.append(m3d.Manifold.batch_boolean(
                current[start:start + batch_size], m3d.OpType.Add
            ))
            completed_batches += 1
            if progress is not None:
                progress(
                    completed_batches,
                    max(1, estimated_batches),
                    round_index,
                    len(following) + (
                        len(current) - min(start + batch_size, len(current))
                    ),
                )
        current = following
    result = current[0]
    if result.status() != m3d.Error.NoError:
        raise ValueError(f"boolean union failed: {result.status()}")
    return result


def materialize_manifold(solid: m3d.Manifold) -> m3d.Manifold:
    """Evaluate a lazy boolean tree and rebuild a compact independent solid."""
    mesh = solid.to_mesh()
    result = m3d.Manifold(mesh)
    if result.status() != m3d.Error.NoError:
        raise ValueError(f"materialized manifold is invalid: {result.status()}")
    return result


def process_components_and_cavities(
    solid: m3d.Manifold,
    *,
    cavities: str,
    components: str,
    min_component_volume: float,
    report: SolidReport,
    return_mesh: bool = False,
):
    source_mesh = solid.to_mesh()
    vertices = np.asarray(source_mesh.vert_properties[:, :3])
    triangles = np.asarray(source_mesh.tri_verts, dtype=np.uint32)
    shells, triangle_shell_ids = _split_mesh_shells(vertices, triangles)
    positives = [shell for shell in shells if shell.volume >= 0]
    negatives = [shell for shell in shells if shell.volume < 0]
    report.component_count = len(positives)
    report.cavity_count = len(negatives)
    report.cavities = [
        CavityInfo(abs(shell.volume), shell.bounds) for shell in negatives
    ]
    report.volume_before_cavity_fill = sum(shell.volume for shell in shells)

    all_positives = positives
    removed = []
    positive_solids: dict[int, m3d.Manifold] = {}
    if components == "main" and positives:
        selected_main = min(
            positives,
            key=lambda shell: (
                -shell.volume, shell.bounds, shell.min_triangle
            ),
        )
        removed = [shell for shell in positives if shell is not selected_main]
        positives = [selected_main]
        report.main_component_volume = selected_main.volume
        report.main_component_bounds = selected_main.bounds

    if components == "remove-small":
        kept = []
        for shell in positives:
            if shell.volume < min_component_volume:
                removed.append(shell)
            else:
                kept.append(shell)
        positives = kept

    if removed:
        removed_ids = {shell.shell_id for shell in removed}
        if positives and negatives:
            owners = _cavity_owners(
                positives=all_positives, cavities=negatives,
                vertices=vertices, triangles=triangles,
                triangle_shell_ids=triangle_shell_ids,
                positive_solids=positive_solids,
            )
            removed_cavities = [
                cavity for cavity in negatives
                if owners[cavity.shell_id] in removed_ids
            ]
        else:
            removed_cavities = negatives
        removed_cavity_ids = {shell.shell_id for shell in removed_cavities}
        negatives = [
            cavity for cavity in negatives
            if cavity.shell_id not in removed_cavity_ids
        ]
        report.removed_components += len(removed)
        # A removed hollow component contains less material than its outer
        # boundary's volume. Its owned (negative) boundaries travel with it.
        report.removed_component_volume += sum(
            shell.volume for shell in [*removed, *removed_cavities]
        )

    retained_material_volume = sum(s.volume for s in positives) + sum(
        s.volume for s in negatives
    )

    report.retained_component_count = len(positives)
    if cavities == "fill" and negatives:
        # Each positive boundary becomes a filled solid. Compose only joins
        # boundary lists; a real union is required to absorb nested islands.
        parts = []
        for shell in positives:
            if shell.shell_id not in positive_solids:
                mask = triangle_shell_ids == shell.shell_id
                positive_solids[shell.shell_id] = _manifold_from_selected_triangles(
                    vertices, triangles[mask]
                )
            parts.append(positive_solids[shell.shell_id])
        result = materialize_manifold(union_balanced(parts))
        final_mesh = result.to_mesh()
        vertices = np.asarray(final_mesh.vert_properties[:, :3])
        triangles = np.asarray(final_mesh.tri_verts, dtype=np.uint32)
        final_shells, _ = _split_mesh_shells(vertices, triangles)
        report.retained_component_count = sum(s.volume > 0 for s in final_shells)
        report.volume_after_cavity_fill = result.volume()
        # Filling adds only previously empty space in retained components,
        # not volume deleted by policy or already occupied by nested islands.
        report.filled_cavity_volume = max(
            0.0, report.volume_after_cavity_fill - retained_material_volume
        )
        if return_mesh:
            return result, vertices.copy(), triangles.copy()
        return result
    elif not removed:
        result = solid
        selected = None
    else:
        selected = [*positives, *negatives]
    if selected is not None:
        selected_ids = np.fromiter(
            (shell.shell_id for shell in selected),
            dtype=np.int64,
            count=len(selected),
        )
        mask = np.isin(triangle_shell_ids, selected_ids)
        result, vertices, triangles = _manifold_from_selected_triangles(
            vertices, triangles[mask], return_mesh=True
        )

    report.volume_after_cavity_fill = retained_material_volume
    report.filled_cavity_volume = 0.0
    if return_mesh:
        if selected is None:
            return result, vertices.copy(), triangles.copy()
        return result, vertices, triangles
    return result


@dataclass
class _MeshShell:
    shell_id: int
    volume: float
    bounds: tuple[float, float, float, float, float, float]
    min_triangle: int


def _split_mesh_shells(
    vertices: np.ndarray, triangles: np.ndarray
) -> tuple[list[_MeshShell], np.ndarray]:
    """Find closed boundary shells in near-linear time using shared edges."""
    count = len(triangles)
    if count == 0:
        return [], np.empty(0, dtype=np.int64)

    edges = np.stack((
        triangles[:, (0, 1)],
        triangles[:, (1, 2)],
        triangles[:, (2, 0)],
    ), axis=1).reshape((-1, 2))
    edges.sort(axis=1)
    triangle_ids = np.repeat(np.arange(count, dtype=np.int64), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    ordered_edges = edges[order]
    ordered_triangles = triangle_ids[order]
    duplicates = np.all(
        ordered_edges[1:] == ordered_edges[:-1], axis=1
    )
    left = ordered_triangles[:-1][duplicates]
    right = ordered_triangles[1:][duplicates]
    del edges, ordered_edges, order, ordered_triangles, triangle_ids

    labels = np.arange(count, dtype=np.int64)
    while True:
        previous = labels.copy()
        minimum = np.minimum(labels[left], labels[right])
        np.minimum.at(labels, left, minimum)
        np.minimum.at(labels, right, minimum)
        labels = labels[labels]
        if np.array_equal(labels, previous):
            break
    _, shell_ids = np.unique(labels, return_inverse=True)
    shell_count = int(shell_ids.max()) + 1

    points = vertices[triangles].astype(np.float64, copy=False)
    triangle_low = points.min(axis=1)
    triangle_high = points.max(axis=1)
    lows = np.full((shell_count, 3), np.inf)
    highs = np.full((shell_count, 3), -np.inf)
    np.minimum.at(lows, shell_ids, triangle_low)
    np.maximum.at(highs, shell_ids, triangle_high)
    min_triangles = np.full(shell_count, count, dtype=np.int64)
    np.minimum.at(min_triangles, shell_ids, np.arange(count))

    # Signed volume is translation invariant for a closed shell. Work around
    # a point on each shell to avoid cancellation at large world coordinates,
    # especially for float32 meshes returned by the native library.
    origins = points[min_triangles, 0]
    points -= origins[shell_ids, None, :]
    triangle_volumes = np.einsum(
        "ij,ij->i",
        points[:, 0],
        np.cross(points[:, 1], points[:, 2]),
    ) / 6.0
    volumes = np.bincount(
        shell_ids, weights=triangle_volumes, minlength=shell_count
    )

    shells = [
        _MeshShell(
            shell_id,
            float(volumes[shell_id]),
            (
                float(lows[shell_id, 0]), float(lows[shell_id, 1]),
                float(lows[shell_id, 2]), float(highs[shell_id, 0]),
                float(highs[shell_id, 1]), float(highs[shell_id, 2]),
            ),
            int(min_triangles[shell_id]),
        )
        for shell_id in range(shell_count)
    ]
    return shells, shell_ids


def _manifold_from_selected_triangles(
    vertices: np.ndarray, triangles: np.ndarray, *, return_mesh: bool = False
):
    if len(triangles) == 0:
        empty_vertices = np.empty((0, 3), dtype=np.float32)
        empty_triangles = np.empty((0, 3), dtype=np.uint32)
        result = m3d.Manifold()
        return (
            (result, empty_vertices, empty_triangles)
            if return_mesh else result
        )
    used, inverse = np.unique(triangles.reshape(-1), return_inverse=True)
    compact_triangles = inverse.reshape((-1, 3)).astype(np.uint32)
    compact_vertices = vertices[used].astype(np.float32)
    mesh = m3d.Mesh(compact_vertices, compact_triangles, tolerance=1e-7)
    result = m3d.Manifold(mesh)
    if result.status() != m3d.Error.NoError:
        raise ValueError(f"shell filtering produced invalid solid: {result.status()}")
    if return_mesh:
        return result, compact_vertices, compact_triangles
    return result


def _cavity_owners(
    *,
    positives: list[_MeshShell],
    cavities: list[_MeshShell],
    vertices: np.ndarray,
    triangles: np.ndarray,
    triangle_shell_ids: np.ndarray,
    positive_solids: dict[int, m3d.Manifold],
) -> dict[int, int]:
    """Assign cavities to the smallest geometrically containing boundary.

    AABBs are only a broad phase: a concave or toroidal component can have a
    containing box without enclosing the cavity. Reversed cavity boundaries
    form positive solids, allowing exact containment via Boolean difference.
    """
    if not cavities:
        return {}
    bounds = np.asarray([shell.bounds for shell in positives])
    volumes = np.asarray([shell.volume for shell in positives])
    min_triangles = np.asarray(
        [shell.min_triangle for shell in positives], dtype=np.int64
    )
    cell_size = 16.0
    spatial_index: dict[tuple[int, int, int], list[int]] = {}
    for shell_index, shell_bounds in enumerate(bounds):
        low = np.floor(shell_bounds[:3] / cell_size).astype(int)
        high = np.floor(
            (shell_bounds[3:] - 1e-9) / cell_size
        ).astype(int)
        for x in range(low[0], high[0] + 1):
            for y in range(low[1], high[1] + 1):
                for z in range(low[2], high[2] + 1):
                    spatial_index.setdefault((x, y, z), []).append(
                        shell_index
                    )
    owners = {}
    for cavity in cavities:
        cavity_bounds = np.asarray(cavity.bounds)
        center = (cavity_bounds[:3] + cavity_bounds[3:]) * 0.5
        cell = tuple(np.floor(center / cell_size).astype(int))
        local = np.asarray(
            spatial_index.get(cell, ()), dtype=np.int64
        )
        if len(local):
            local_bounds = bounds[local]
            contained = np.all(
                local_bounds[:, :3] <= cavity_bounds[:3] + 1e-8, axis=1
            ) & np.all(
                local_bounds[:, 3:] >= cavity_bounds[3:] - 1e-8, axis=1
            )
            candidates = local[contained]
        else:
            candidates = local
        owner = None
        if len(candidates):
            choices = np.lexsort((
                min_triangles[candidates],
                volumes[candidates],
            ))
            cavity_triangles = triangles[triangle_shell_ids == cavity.shell_id]
            cavity_solid = _manifold_from_selected_triangles(
                vertices, cavity_triangles[:, (0, 2, 1)]
            )
            for choice in choices:
                candidate = positives[int(candidates[choice])]
                if candidate.shell_id not in positive_solids:
                    mask = triangle_shell_ids == candidate.shell_id
                    positive_solids[candidate.shell_id] = (
                        _manifold_from_selected_triangles(vertices, triangles[mask])
                    )
                outside = cavity_solid - positive_solids[candidate.shell_id]
                if outside.status() != m3d.Error.NoError:
                    raise ValueError(f"cavity containment failed: {outside.status()}")
                if outside.is_empty():
                    owner = candidate.shell_id
                    break
        if owner is None:
            raise ValueError("closed cavity has no geometrically containing component")
        owners[cavity.shell_id] = owner
    return owners


def manifold_to_mesh(solid: m3d.Manifold) -> Mesh:
    source = solid.as_original().to_mesh()
    positions = source.vert_properties[:, :3]
    mesh = Mesh()
    remap = {}
    index = {}
    for old, (x, y, z) in enumerate(positions):
        key = (round(float(x), 9), round(float(y), 9), round(float(z), 9))
        if key not in index:
            index[key] = len(mesh.vertices)
            mesh.vertices.append(Vec3(*key))
        remap[old] = index[key]
    seen = set()
    for a, b, c in source.tri_verts:
        triangle = (remap[int(a)], remap[int(b)], remap[int(c)])
        if len(set(triangle)) < 3:
            continue
        canonical = tuple(sorted(triangle))
        if canonical in seen:
            continue
        seen.add(canonical)
        mesh.triangles.append(triangle)
    return mesh


def validate_manifold(solid: m3d.Manifold, report: SolidReport) -> None:
    # A successful Manifold is an oriented 2-manifold by construction.
    report.open_edges = 0
    report.nonmanifold_edges = 0
    report.orientation_conflicts = 0
    report.degenerate_triangles = 0
    report.self_intersections = 0
    report.printable = (
        solid.status() == m3d.Error.NoError
        and not solid.is_empty()
        and solid.num_tri() > 0
    )
