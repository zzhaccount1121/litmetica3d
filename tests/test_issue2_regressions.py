"""Issue #2 regressions using real gzip NBT and geometric assertions."""
import gzip, json, math, struct, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import manifold3d as m3d
import numpy as np
from litmetica3d.conversion import ConversionOptions, convert
from litmetica3d.entity_models import get_entity_geometry
from litmetica3d.litematic import BlockState, Region, Schematic, _decode_block_states, load_schematic, load_schematic_info
from litmetica3d.model_loader import ModelLoader
from litmetica3d.solid import SolidReport, process_components_and_cavities
from litmetica3d.winui_bridge import run

def fixture(path, indices=(1,), palette=None, size=None, position=(0,0,0)):
    palette = palette or ["minecraft:air", "minecraft:stone"]
    size = size or (len(indices),1,1)
    def string(s):
        b=s.encode("utf-8"); return struct.pack(">H",len(b))+b
    def tag(t,n,b): return bytes([t])+string(n)+b
    def integer(n,v): return tag(3,n,struct.pack(">i",v))
    def compound(n,b): return tag(10,n,b+b"\0")
    bits=max(2,(len(palette)-1).bit_length())
    packed=sum(v << (i*bits) for i,v in enumerate(indices))
    count=(len(indices)*bits+63)//64
    longs=[]
    for i in range(count):
        v=(packed >> (i*64)) & ((1<<64)-1)
        longs.append(v if v<(1<<63) else v-(1<<64))
    reg=compound("Position",b"".join(integer(a,v) for a,v in zip("xyz",position)))
    reg+=compound("Size",b"".join(integer(a,v) for a,v in zip("xyz",size)))
    reg+=tag(9,"BlockStatePalette",b"\x0a"+struct.pack(">i",len(palette))+b"".join(tag(8,"Name",string(n))+b"\0" for n in palette))
    reg+=tag(12,"BlockStates",struct.pack(">i",count)+b"".join(struct.pack(">q",v) for v in longs))
    path.write_bytes(gzip.compress(compound("",integer("Version",6)+compound("Regions",compound("R",reg)))))

def points(faces): return np.asarray([(v.x,v.y,v.z) for f in faces for v in f.vertices])

class Issue2Tests(unittest.TestCase):
    def test_minimum_bits(self):
        p=[BlockState("minecraft:air"),BlockState("minecraft:stone")]
        self.assertEqual({(x,0,0):1 for x in range(4)},_decode_block_states([0x55],p,(4,1,1)))

    def test_real_file_boundary_negative_sizes_and_info(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"bits.litematic"; indices=[i%6 for i in range(48)]
            fixture(path,indices,["minecraft:air"]+[f"test:b{i}" for i in range(1,6)],(-4,3,-4))
            expected={}
            for i,v in enumerate(indices):
                y,zx=divmod(i,16); z,x=divmod(zx,4)
                if v: expected[(x-3,y,z-3)]=v
            self.assertEqual(expected,load_schematic(path).regions["R"].blocks)
            info=load_schematic_info(path)
            self.assertEqual((-4,3,-4),info.regions["R"].size)
            self.assertEqual({},info.regions["R"].blocks)

    def test_corrupt_packed_data(self):
        p=[BlockState("minecraft:air"),BlockState("minecraft:stone")]
        for data,size in (([1],(33,1,1)),([3],(1,1,1))):
            with self.subTest(size=size),self.assertRaises(ValueError): _decode_block_states(data,p,size)

    def test_cavity_island(self):
        source=(m3d.Manifold.cube((10,10,10))-m3d.Manifold.cube((8,8,8)).translate((1,1,1)))+m3d.Manifold.cube().translate((4,4,4))
        for mode,volume,count in (("preserve",489,2),("fill",1000,1)):
            report=SolidReport()
            solid,v,t=process_components_and_cavities(source,cavities=mode,components="keep",min_component_volume=0,report=report,return_mesh=True)
            self.assertAlmostEqual(volume,solid.volume())
            self.assertAlmostEqual(volume,report.volume_after_cavity_fill)
            self.assertEqual(count,report.retained_component_count)
            self.assertAlmostEqual(volume,m3d.Manifold(m3d.Mesh(np.array(v,dtype=np.float32,copy=True),np.array(t,dtype=np.uint32,copy=True))).volume())
        self.assertAlmostEqual(511,report.filled_cavity_volume)

    def test_chest_all_directions(self):
        for facing,offset in {"south":(1,0,0),"north":(-1,0,0),"east":(0,0,-1),"west":(0,0,1)}.items():
            models=[]
            for kind in ("right","left"):
                p={"facing":facing,"type":kind}
                a=get_entity_geometry("minecraft:chest",p); b=get_entity_geometry("minecraft:chest",p,visual=True)
                for part in (slice(0,6),slice(-6,None)):
                    np.testing.assert_allclose(points(a[part]).min(0),points(b[part]).min(0),atol=1e-10)
                    np.testing.assert_allclose(points(a[part]).max(0),points(b[part]).max(0),atol=1e-10)
                models.append(points(a[:6]))
            a,b=models[0],models[1]+offset; axis=0 if offset[0] else 2
            self.assertAlmostEqual(0,b.min(0)[axis]-a.max(0)[axis] if offset[axis]>0 else a.min(0)[axis]-b.max(0)[axis])

    def test_rescale_and_normals(self):
        with tempfile.TemporaryDirectory() as d:
            loader=ModelLoader(d)
            for axis in "xyz":
                for angle in (22.5,45,-45):
                    model={"elements":[{"from":[2,4,6],"to":[10,12,14],"rotation":{"origin":[8,8,8],"axis":axis,"angle":angle},"faces":{}}]}
                    a=loader._model_to_faces(model,"test",closed=True)
                    model["elements"][0]["rotation"]["rescale"]=True
                    b=loader._model_to_faces(model,"test",closed=True); expected=points(a)-.5
                    for i,label in enumerate("xyz"):
                        if label!=axis: expected[:,i]/=math.cos(math.radians(angle))
                    np.testing.assert_allclose(points(b),expected+.5,atol=1e-12)
                    for face in b:
                        p=points([face]); n=np.cross(p[1]-p[0],p[2]-p[0]); n/=np.linalg.norm(n)
                        np.testing.assert_allclose([face.normal.x,face.normal.y,face.normal.z],n,atol=1e-10)
            loader.close()

    def test_uvlock_world_orientation(self):
        dirs=("down","up","north","south","west","east")
        model={"textures":{"all":"minecraft:block/missing"},"elements":[{"from":[0,0,0],"to":[16,16,16],"faces":{d:{"texture":"#all"} for d in dirs}}]}
        with tempfile.TemporaryDirectory() as d:
            loader=ModelLoader(d,visual_textures=True); loader._load_model=lambda name:model
            for rx in (0,90,180,270):
                for ry in (0,90,180,270):
                    fs=loader._load_one_variant({"model":"test","x":rx,"y":ry,"uvlock":True},"test")
                    for f in fs:
                        n=tuple(round(v) for v in (f.normal.x,f.normal.y,f.normal.z))
                        for v,uv in zip(f.vertices,f.uvs):
                            x,y,z=v.x,v.y,v.z
                            expected={(0,-1,0):(x,z),(0,1,0):(x,1-z),(0,0,-1):(1-x,y),(0,0,1):(x,y),(-1,0,0):(z,y),(1,0,0):(1-z,y)}[n]
                            np.testing.assert_allclose(uv,expected,atol=1e-10)
            loader.close()

    def test_invalid_scale(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/"a.litematic"; fixture(source)
            for scale in (0,-1,math.nan,math.inf,-math.inf):
                target=root/str(scale)/"a.stl"
                with self.subTest(scale=scale),self.assertRaises(ValueError): convert(ConversionOptions(source,target,scale=scale))
                self.assertFalse(target.parent.exists())

    def test_uvlock_alpha_geometry_matches_world_texture(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); texture=root/"assets/minecraft/textures/block/marker.png"
            texture.parent.mkdir(parents=True)
            image=Image.new("RGBA",(4,4),(0,0,0,0)); image.putpixel((0,0),(255,0,0,255)); image.save(texture)
            loader=ModelLoader(root,visual_textures=True)
            model={"textures":{"all":"minecraft:block/marker"},"elements":[{"from":[0,0,0],"to":[16,16,16],"faces":{"up":{"texture":"#all"}}}]}
            for ry in (0,90,180,270):
                faces=loader._model_to_faces(model,"test",rot_y=ry,uvlock=True)
                p=points(faces)
                np.testing.assert_allclose(p[:,(0,2)].min(0),(0,0),atol=1e-10)
                np.testing.assert_allclose(p[:,(0,2)].max(0),(.25,.25),atol=1e-10)
                self.assertTrue(faces)
            loader.close()

    def test_two_entry_real_file_spans_multiple_longs(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"stone.litematic"
            fixture(path,(1,)*70)
            self.assertEqual({(x,0,0):1 for x in range(70)},load_schematic(path).regions["R"].blocks)

    def test_emission_original_coordinates(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/"a.litematic"; rules=root/"rules.json"
            for pos in ((10,20,30),(-10,-20,-30)):
                fixture(source,palette=["minecraft:air","minecraft:glowstone"],position=pos)
                rules.write_text(json.dumps({"rules":[{"region":"R","position":list(pos),"multiplier":0}]}),encoding="utf-8")
                report=convert(ConversionOptions(source,root/"a.obj",output_format="obj",geometry="visual",emission_config=rules))
                self.assertEqual(0,report.emissive_blocks); self.assertEqual(0,report.blender_lights)

    def test_batch_names(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); files=[root/"a.litematic",root/"a (2).litematic"]
            for f in files: fixture(f)
            existing=root/"L3D_output"/"a"; existing.mkdir(parents=True)
            sentinel=existing/"keep.txt"; sentinel.write_text("original"); events=[]
            run({"files":list(map(str,files)),"output_dir":str(root),"options":{}},lambda:False,lambda kind,**data:events.append((kind,data)))
            outputs=[Path(data["report"]["output_path"]) for kind,data in events if kind=="report"]
            self.assertEqual(2,len(outputs)); self.assertEqual(2,len({p.parent for p in outputs}))
            self.assertTrue(all(p.is_file() for p in outputs)); self.assertEqual("original",sentinel.read_text())

    def test_emission_rules_multiple_regions_filter_center_scale(self):
        state=BlockState("minecraft:glowstone")
        schematic=Schematic(6,0,regions={
            "Negative":Region("Negative",(-10,20,30),(-3,1,1),[state],{(-2,0,0):0}),
            "Positive":Region("Positive",(10,20,30),(1,1,1),[state],{(0,0,0):0}),
        })
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); rules=root/"rules.json"
            rules.write_text(json.dumps({"rules":[{"region":"Negative","position":[-12,20,30],"multiplier":0}]}),encoding="utf-8")
            for selected,count,origin,light_x in (((),1,(-12,20,30),22),(("Negative",),0,(-12,20,30),None),(("Positive",),1,(10,20,30),0)):
                target=root/("all.obj" if not selected else selected[0]+".obj")
                with self.subTest(regions=selected),patch("litmetica3d.conversion.load_schematic",return_value=schematic):
                    report=convert(ConversionOptions(root/"source.litematic",target,output_format="obj",geometry="visual",emission_config=rules,regions=selected,center=True,scale=2))
                    self.assertEqual(count,report.emissive_blocks)
                    self.assertEqual(count,report.blender_lights)
                    self.assertEqual(origin,report.coordinate_origin)
                    manifest=json.loads(target.with_suffix(".blender_emission.json").read_text(encoding="utf-8"))
                    self.assertEqual(count,len(manifest["lights"]))
                    if count:
                        # Existing helper-light behavior: 0.06 above the top
                        # of a luminous cube; centering changes only X/Z.
                        np.testing.assert_allclose(manifest["lights"][0]["position"],(light_x,(.5+.56)*2,0),atol=1e-12)
