"""Export real pipeline OBJ fixtures for closed/open shulker visual QA."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from litmetica3d.conversion import bundled_asset_path
from litmetica3d.model_loader import ModelLoader
from litmetica3d.shulker_visual import geometry
from litmetica3d.visual_mesh import CompactVisualMesh, export_compact_obj

parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
loader = ModelLoader(bundled_asset_path(), visual_textures=True)
try:
    for name, openness in [('closed', 0), ('open', 1)]:
        mesh = CompactVisualMesh()
        mesh.add_faces(geometry('minecraft:entity/shulker/shulker', openness=openness))
        export_compact_obj(mesh, args.output / (name + '.obj'),
                           texture_provider=loader.texture_bytes, alpha_provider=loader.texture_alpha_bytes)
        print(name, flush=True)
finally:
    loader.close()
