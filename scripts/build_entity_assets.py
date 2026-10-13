"""Package vanilla entity geometry extracted by ExtractModels.java (build time only)."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import zipfile
from io import BytesIO
from PIL import Image


def build(jar, extracted, destination, base_archive=None):
    layers = json.loads((extracted / 'entity_layers.json').read_text())
    # This is a depth-only boat water mask, with deliberately undefined UVs.
    layers.pop('minecraft:boat#water_patch', None)
    types = json.loads((extracted / 'entity_types.json').read_text())
    root = destination / 'assets/litmetica3d/entities'
    root.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(layers, separators=(',', ':'), allow_nan=False).encode()
    (root / 'layers.json.gz').write_bytes(gzip.compress(raw, mtime=0))
    with zipfile.ZipFile(jar) as archive:
        names = archive.namelist()
        variants = {
            n.removeprefix('data/minecraft/').removesuffix('.json'): json.loads(archive.read(n))
            for n in names if n.startswith('data/minecraft/') and '_variant/' in n
            and 'sound_variant/' not in n and '/tags/' not in n and n.endswith('.json')
        }
        textures = [n.removeprefix('assets/minecraft/textures/').removesuffix('.png')
                    for n in names if n.startswith('assets/minecraft/textures/') and n.endswith('.png')]
        for n in names:
            if n.endswith('/'):
                continue
            if n.startswith(('assets/minecraft/models/item/', 'assets/minecraft/items/',
                             'assets/minecraft/textures/item/', 'assets/minecraft/textures/painting/',
                             'assets/minecraft/font/', 'assets/minecraft/textures/font/')):
                target = destination / n
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(n))
        version = json.loads(archive.read('version.json'))['id']
        # All vanilla dye variants share this cutout shape. Keep the mask beside
        # the native geometry so coincident base/lid faces can be clipped before
        # ray tracing (transparent coplanar faces are not equivalent to discard).
        masks = []
        for n in names:
            if n.startswith('assets/minecraft/textures/entity/shulker/shulker') and n.endswith('.png'):
                im = Image.open(BytesIO(archive.read(n))).convert('RGBA')
                masks.append([''.join('1' if im.getpixel((x, y))[3] >= 26 else '0'
                                      for x in range(im.width)) for y in range(im.height)])
        if not masks or any(m != masks[0] for m in masks):
            raise ValueError('Shulker alpha masks differ; need per-texture geometry')
    catalog = dict(version=version, types=types, layers=sorted(layers), textures=sorted(textures),
                   variants=variants, source_sha256=hashlib.sha256(jar.read_bytes()).hexdigest(),
                   source='Minecraft client LayerDefinitions.createRoots / ModelPart.Cube.polygons',
                   pose='Static baked pose; no world simulation or animation timeline')
    (root / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
    data_dir = destination.parent.parent / 'data'
    data_dir.mkdir(exist_ok=True)
    shulker = dict(layers['minecraft:shulker_box#main'], alpha_mask=masks[0])
    (data_dir / 'shulker_box.json').write_text(json.dumps(shulker, separators=(',', ':')), encoding='utf-8')
    bundled = destination.with_name(destination.name + '.zip')
    if bundled.exists():
        # Preserve every original block asset byte-for-byte. Add only the new
        # entity/item resources to the copy in the feature worktree.
        temporary = bundled.with_name('26.2.entities-building.zip')
        with zipfile.ZipFile(base_archive or bundled) as original, zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as out:
            present = set()
            for info in original.infolist():
                name = info.filename.replace('\\', '/')
                if name.startswith('assets/litmetica3d/entities/'):
                    continue
                out.writestr(name, original.read(info))
                present.add(name)
            for file in destination.rglob('*'):
                if file.is_file():
                    name = file.relative_to(destination).as_posix()
                    if name not in present:
                        out.write(file, name)
        temporary.replace(bundled)
    print(f'Packaged {len(types)} entity IDs, {len(layers)} model layers from Minecraft {version}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('jar', type=Path)
    parser.add_argument('extracted', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--base-archive', type=Path)
    args = parser.parse_args()
    build(args.jar, args.extracted, args.destination, args.base_archive)
