"""Export a small gallery through the real entity and OBJ pipeline."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from litmetica3d.scene_entities import EntityRenderer
from litmetica3d.model_loader import ModelLoader
from litmetica3d.conversion import bundled_asset_path
from litmetica3d.visual_mesh import CompactVisualMesh, export_compact_obj

out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
l=ModelLoader(bundled_asset_path(),visual_textures=True)
r=EntityRenderer(l)
try:
    for name,data in [('pig',{}),('minecart',{}),('chest_minecart',{}),
                      ('item_frame',{'Facing':3,'Item':{'id':'minecraft:diamond'},'ItemRotation':1}),
                      ('cow',{}),('sheep',{'Color':14})]:
        data={'id':'minecraft:'+name,**data}
        faces,status=r.render(data)
        mesh=CompactVisualMesh();mesh.add_faces(faces)
        export_compact_obj(mesh,out/(name+'.obj'),texture_provider=l.texture_bytes,alpha_provider=l.texture_alpha_bytes)
        print(name,len(faces),status,r.notes,flush=True)
finally:
    r.assets.close();l.close()
