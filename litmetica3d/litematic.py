"""
Litematica schematic file parser.

Parses .litematic files (GZip-compressed NBT) into typed Python objects.
Handles the bit-packed block storage format used by Litematica.

Reference: https://litemapy.readthedocs.io/en/latest/litematics.html
"""

from dataclasses import dataclass, field
from typing import Any

from .nbt import read_gzip_nbt


# ── Data types ──────────────────────────────────────────────────────────────

@dataclass
class BlockState:
    """A single entry in the block state palette."""
    name: str           # e.g. "minecraft:oak_log"
    properties: dict[str, str] = field(default_factory=dict)
    # e.g. {"axis": "y", "waterlogged": "false"}

    @property
    def block_id(self) -> str:
        """Full block identifier including properties (for dedup)."""
        if not self.properties:
            return self.name
        props = ",".join(f"{k}={v}" for k, v in sorted(self.properties.items()))
        return f"{self.name}[{props}]"

    @property
    def namespace(self) -> str:
        """Return the namespace part, e.g. 'minecraft'."""
        return self.name.split(":")[0] if ":" in self.name else "minecraft"

    @property
    def basename(self) -> str:
        """Return the block name without namespace, e.g. 'oak_log'."""
        return self.name.split(":")[1] if ":" in self.name else self.name


@dataclass
class Region:
    """A single region (sub-volume) within a Litematica schematic."""
    name: str
    position: tuple[int, int, int]   # offset from schematic origin
    size: tuple[int, int, int]        # dimensions (x, y, z)
    palette: list[BlockState]          # indexed by palette index
    blocks: dict[tuple[int, int, int], int]  # (x, y, z) → palette index
    tile_entities: list[dict] = field(default_factory=list)

    @property
    def total_blocks(self) -> int:
        return len(self.blocks)

    @property
    def non_air_blocks(self) -> int:
        return sum(1 for idx in self.blocks.values()
                   if self.palette[idx].name not in AIR_BLOCKS)


@dataclass
class Schematic:
    """A parsed Litematica schematic file."""
    version: int
    data_version: int          # MinecraftDataVersion
    name: str = ""
    author: str = ""
    description: str = ""
    time_created: int = 0
    time_modified: int = 0
    enclosing_size: tuple[int, int, int] = (0, 0, 0)
    total_blocks: int = 0
    total_volume: int = 0
    regions: dict[str, Region] = field(default_factory=dict)


# ── Air block identifiers ───────────────────────────────────────────────────

AIR_BLOCKS = {
    "minecraft:air",
    "minecraft:cave_air",
    "minecraft:void_air",
    "minecraft:structure_void",
}


# ── Public API ──────────────────────────────────────────────────────────────

def load_schematic(path: str) -> Schematic:
    """Load and parse a .litematic file."""
    nbt = read_gzip_nbt(path)
    return _parse_schematic(nbt)


def load_schematic_info(path: str) -> Schematic:
    """Load schematic metadata only (no block data)."""
    nbt = read_gzip_nbt(path, skip_names=frozenset({"BlockStates"}))
    return _parse_schematic(nbt, load_blocks=False)


# ── Parsing ─────────────────────────────────────────────────────────────────

def _parse_schematic(nbt: dict[str, Any], load_blocks: bool = True) -> Schematic:
    schem = Schematic(
        version=nbt.get("Version", 0),
        data_version=nbt.get("MinecraftDataVersion", 0),
    )

    # ── Metadata ────────────────────────────────────────────────────────────
    meta = nbt.get("Metadata", {})
    if meta:
        schem.name = meta.get("Name", "")
        schem.author = meta.get("Author", "")
        schem.description = meta.get("Description", "")
        schem.time_created = meta.get("TimeCreated", 0)
        schem.time_modified = meta.get("TimeModified", 0)
        schem.total_blocks = meta.get("TotalBlocks", 0)
        schem.total_volume = meta.get("TotalVolume", 0)
        enc = meta.get("EnclosingSize", {})
        if enc:
            schem.enclosing_size = (enc.get("x", 0), enc.get("y", 0), enc.get("z", 0))

    # ── Regions ─────────────────────────────────────────────────────────────
    regions_nbt = nbt.get("Regions", {})
    for region_name, region_data in regions_nbt.items():
        schem.regions[region_name] = _parse_region(region_name, region_data, load_blocks)

    return schem


def _parse_region(name: str, data: dict[str, Any], load_blocks: bool = True) -> Region:
    # Position and Size
    pos = data.get("Position", {})
    position = (pos.get("x", 0), pos.get("y", 0), pos.get("z", 0))
    sz = data.get("Size", {})
    size = (sz.get("x", 0), sz.get("y", 0), sz.get("z", 0))

    # ── Block palette ───────────────────────────────────────────────────────
    palette_nbt = data.get("BlockStatePalette", [])
    palette: list[BlockState] = []
    for entry in palette_nbt:
        block_name = entry.get("Name", "minecraft:air")
        props = entry.get("Properties", {})
        # Properties might be None in NBT
        if props is None:
            props = {}
        palette.append(BlockState(name=block_name, properties=props))

    # ── Block state bitstream ───────────────────────────────────────────────
    block_states_nbt = data.get("BlockStates", [])
    blocks = _decode_block_states(block_states_nbt, palette, size) if load_blocks else {}

    # ── Tile entities ───────────────────────────────────────────────────────
    tile_entities = data.get("TileEntities", [])

    return Region(
        name=name,
        position=position,
        size=size,
        palette=palette,
        blocks=blocks,
        tile_entities=list(tile_entities),
    )


# ── Bit-packed block state decoding ─────────────────────────────────────────

def _decode_block_states(
    long_array: list[int],
    palette: list[BlockState],
    size: tuple[int, int, int],
) -> dict[tuple[int, int, int], int]:
    """
    Decode the bit-packed block state array into (x, y, z) → palette_index.

    Bit packing: all 64-bit longs are concatenated into one contiguous
    bitstream. Each block uses `bits_per_block` bits.

    Iteration order: Y outer → Z middle → X inner.
    index = y * (sx * sz) + z * sx + x
    """
    sx, sy, sz = size
    total_blocks = abs(sx) * abs(sy) * abs(sz)
    if total_blocks == 0:
        return {}
    if not palette:
        raise ValueError("Litematic 方块调色板为空")
    # Litematica's packed storage has a minimum width of TWO bits.
    bits_per_block = max(2, (len(palette) - 1).bit_length())
    required_longs = (total_blocks * bits_per_block + 63) // 64
    if len(long_array) < required_longs:
        raise ValueError(f"Litematic 方块位流截断：需要 {required_longs} 个 long，实际 {len(long_array)}")

    blocks: dict[tuple[int, int, int], int] = {}

    block_index = 0
    for y in range(abs(sy)):
        for z in range(abs(sz)):
            for x in range(abs(sx)):
                palette_idx = _read_packed_index(
                    long_array, block_index, bits_per_block
                )
                if palette_idx >= len(palette):
                    raise ValueError(f"Litematic 调色板索引越界：方块 {block_index}，索引 {palette_idx}")

                # Normalized coordinates (handle negative sizes)
                nx = x if sx >= 0 else x + sx + 1
                ny = y if sy >= 0 else y + sy + 1
                nz = z if sz >= 0 else z + sz + 1

                if (
                    palette_idx < len(palette)
                    and palette[palette_idx].name not in {
                        "minecraft:air",
                        "minecraft:cave_air",
                        "minecraft:void_air",
                        "minecraft:structure_void",
                    }
                ):
                    blocks[(nx, ny, nz)] = palette_idx

                block_index += 1

    return blocks


def _read_packed_index(
    long_array: list[int], index: int, bits_per_block: int
) -> int:
    """Read one LSB-first packed value without expanding the LongArray."""
    bit_offset = index * bits_per_block
    long_index = bit_offset >> 6
    start_bit = bit_offset & 63
    if long_index >= len(long_array):
        return 0

    mask = (1 << bits_per_block) - 1
    current = long_array[long_index] & ((1 << 64) - 1)
    value = current >> start_bit
    bits_here = 64 - start_bit
    if bits_here < bits_per_block and long_index + 1 < len(long_array):
        following = long_array[long_index + 1] & ((1 << 64) - 1)
        value |= following << bits_here
    return value & mask


def _longs_to_bitstream(long_array: list[int]) -> list[int]:
    """
    Convert a long array to a bit-by-bit list.

    Litematica packs block indices from LSB to MSB within each long
    (bit 0 first, bit 63 last).  Verified against litemapy's
    LitematicaBitArray.__getitem__ which uses:
        start_offset = index * nbits
        start_bit_offset = start_offset & 0x3F   # = 0 for index 0
        self.array[0] >> 0 & mask                # reads from LSB side
    """
    bits = []
    for long_val in long_array:
        # Handle signed → unsigned conversion
        if long_val < 0:
            long_val += (1 << 64)
        for shift in range(64):  # LSB first (0 to 63)
            bits.append((long_val >> shift) & 1)
    return bits


def _read_bits(bitstream: list[int], offset: int, count: int) -> int:
    """Read `count` bits from `offset` in the bitstream as an integer (LSB first)."""
    value = 0
    for i in range(count):
        if offset + i < len(bitstream):
            value = value | (bitstream[offset + i] << i)
    return value
