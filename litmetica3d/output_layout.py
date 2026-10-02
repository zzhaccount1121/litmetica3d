"""GUI output layout: one root and one directory per schematic."""

from __future__ import annotations

from pathlib import Path


OUTPUT_ROOT_NAME = "L3D_output"


def normalize_output_root(selected: str | Path) -> Path:
    """Treat an existing L3D_output as the root; otherwise append it once."""
    path = Path(selected)
    if path.name.casefold() == OUTPUT_ROOT_NAME.casefold():
        return path
    return path / OUTPUT_ROOT_NAME


def next_model_path(root: Path, source: Path, output_format: str, reserved: set[str] | None = None) -> Path:
    """Choose a schematic-named folder without overwriting an earlier result."""
    stem = source.stem
    occupied = {p.name.casefold() for p in root.iterdir()} if root.exists() else set()
    if reserved:
        occupied.update(reserved)
    index = 1
    while True:
        folder = root / (stem if index == 1 else f"{stem} ({index})")
        if folder.name.casefold() not in occupied:
            if reserved is not None:
                reserved.add(folder.name.casefold())
            return folder / f"{stem}.{output_format}"
        index += 1
