"""Low-overhead exporters for NumPy-backed printable meshes."""

from __future__ import annotations

import pathlib
import struct

import numpy as np


def export_stl_arrays(
    vertices: np.ndarray,
    triangles: np.ndarray,
    path: pathlib.Path,
    *,
    binary: bool,
    batch_size: int = 100_000,
) -> None:
    if not binary:
        _export_ascii_stl(vertices, triangles, path, batch_size)
        return
    record_dtype = np.dtype([
        ("normal", "<f4", (3,)),
        ("vertices", "<f4", (3, 3)),
        ("attribute", "<u2"),
    ])
    with path.open("wb") as stream:
        header = b"Binary STL exported by litmetica3d" + b"\x00" * 46
        stream.write(header[:80])
        stream.write(struct.pack("<I", len(triangles)))
        for start in range(0, len(triangles), batch_size):
            points = vertices[triangles[start:start + batch_size]]
            normals = np.cross(
                points[:, 1] - points[:, 0],
                points[:, 2] - points[:, 0],
            )
            lengths = np.linalg.norm(normals, axis=1)
            nonzero = lengths > 0
            normals[nonzero] /= lengths[nonzero, None]
            records = np.zeros(len(points), dtype=record_dtype)
            records["normal"] = normals
            records["vertices"] = points
            stream.write(records.tobytes())


def _export_ascii_stl(vertices, triangles, path, batch_size):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("solid litmetica3d_export\n")
        for start in range(0, len(triangles), batch_size):
            points = vertices[triangles[start:start + batch_size]]
            normals = np.cross(
                points[:, 1] - points[:, 0],
                points[:, 2] - points[:, 0],
            )
            lengths = np.linalg.norm(normals, axis=1)
            nonzero = lengths > 0
            normals[nonzero] /= lengths[nonzero, None]
            lines = []
            for normal, triangle in zip(normals, points):
                lines.extend((
                    f"  facet normal {normal[0]:.6e} {normal[1]:.6e} "
                    f"{normal[2]:.6e}\n",
                    "    outer loop\n",
                    f"      vertex {triangle[0,0]:.9g} "
                    f"{triangle[0,1]:.9g} {triangle[0,2]:.9g}\n",
                    f"      vertex {triangle[1,0]:.9g} "
                    f"{triangle[1,1]:.9g} {triangle[1,2]:.9g}\n",
                    f"      vertex {triangle[2,0]:.9g} "
                    f"{triangle[2,1]:.9g} {triangle[2,2]:.9g}\n",
                    "    endloop\n",
                    "  endfacet\n",
                ))
            stream.writelines(lines)
        stream.write("endsolid litmetica3d_export\n")


def export_obj_arrays(
    vertices: np.ndarray,
    triangles: np.ndarray,
    path: pathlib.Path,
    *,
    batch_size: int = 100_000,
) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("# litmetica3d printable mesh\n")
        for start in range(0, len(vertices), batch_size):
            batch = vertices[start:start + batch_size]
            stream.writelines(
                f"v {x:.9g} {y:.9g} {z:.9g}\n" for x, y, z in batch
            )
        for start in range(0, len(triangles), batch_size):
            batch = triangles[start:start + batch_size] + 1
            stream.writelines(
                f"f {a} {b} {c}\n" for a, b, c in batch
            )
