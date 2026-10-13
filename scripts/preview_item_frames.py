"""Export horizontal reference frames through the real OBJ pipeline."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from litmetica3d.scene_entities import EntityRenderer
from litmetica3d.model_loader import ModelLoader
from litmetica3d.conversion import bundled_asset_path
from litmetica3d.visual_mesh import CompactVisualMesh, export_compact_obj

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
loader = ModelLoader(bundled_asset_path(), visual_textures=True)
renderer = EntityRenderer(loader)
try:
    mesh = CompactVisualMesh()
    for row, turn in enumerate((0, 1, 2, 4)):
        for col, name in enumerate(('carrot', 'gold_block', 'golden_carrot')):
            faces, _ = renderer.render({'id':'minecraft:item_frame', 'Facing':1,
                'ItemRotation':turn, 'Item':{'id':'minecraft:'+name}})
            mesh.add_faces(faces, offset=(col*1.2, 0, row*1.2))
    export_compact_obj(mesh, out/'frames.obj', texture_provider=loader.texture_bytes,
                       alpha_provider=loader.texture_alpha_bytes)
finally:
    renderer.assets.close()
    loader.close()
