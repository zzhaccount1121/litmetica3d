"""Record old/new API behavior and deterministic light-cluster timings."""
import argparse
import ast
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--baseline-source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(args.source))
    sys.path.insert(1,str(Path(__file__).resolve().parents[1]/"tests"))
    from test_issue2_regressions import fixture
    from litmetica3d.conversion import ConversionOptions, convert, _cluster_editable_lights
    results={"source":str(args.source)}
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder); assets=root/"assets"
        base=assets/"assets/minecraft"
        for part in ("models/block","blockstates","textures/block"):
            (base/part).mkdir(parents=True)
        directions=("down","up","north","south","west","east")
        for block in ("stone","glowstone"):
            (base/"blockstates"/f"{block}.json").write_text(json.dumps({"variants":{"":{"model":f"minecraft:block/{block}"}}}))
            (base/"models/block"/f"{block}.json").write_text(json.dumps({"textures":{"all":f"minecraft:block/{block}"},"elements":[{"from":[0,0,0],"to":[16,16,16],"faces":{d:{"texture":"#all"} for d in directions}}]}))
        from PIL import Image
        Image.new("RGBA",(16,16),(255,240,180,255)).save(base/"textures/block/glowstone.png")
        source=root/"pair.litematic"
        fixture(source,(1,1),["minecraft:air","minecraft:stone","minecraft:dirt"])
        files=[]; levels={}
        for level in ("raw","safe","experimental"):
            target=root/f"{level}.obj"
            report=convert(ConversionOptions(source,target,asset_path=assets,output_format="obj",geometry="visual",blender_lights="none",optimize=level))
            text=target.read_text(encoding="utf-8")
            geometry=[line for line in text.splitlines() if line.startswith(("v ","vt ","f ","usemtl "))]
            files.append(geometry); levels[level]={"triangles":report.triangles,"report_mode":report.optimize_mode}
        results["optimization"]={"levels":levels,"all_geometry_uv_materials_equal":files[0]==files[1]==files[2]}
        fixture(source,palette=["minecraft:air","minecraft:glowstone"],position=(10,20,30))
        rules=root/"rules.json"; rules.write_text(json.dumps({"rules":[{"region":"R","position":[10,20,30],"multiplier":0}]}))
        report=convert(ConversionOptions(source,root/"glow.obj",asset_path=assets,output_format="obj",geometry="visual",emission_config=rules))
        results["coordinate_rule"]={"emissive_blocks":report.emissive_blocks,"lights":report.blender_lights}
    # An exact copy of the old function supplies the independent reference.
    baseline=args.baseline_source/"litmetica3d/conversion.py"
    tree=ast.parse(baseline.read_text(encoding="utf-8"))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_cluster_editable_lights")
    namespace={}; exec(compile(ast.Module(body=[node],type_ignores=[]),str(baseline),"exec"),namespace)
    old=namespace["_cluster_editable_lights"]
    benchmark=[]
    for count in (2000,4000,8000):
        sources=[{"block":"minecraft:glowstone","level":15,"color":[1,.5,.1],"block_position":[i*2,0,0],"position":[i*2+.5,.5,.5],"power":100,"radius":.1,"name":str(i)} for i in range(count)]
        equal=old(sources)==_cluster_editable_lights(sources)
        times=[]
        for function in (old,_cluster_editable_lights):
            samples=[]
            for _ in range(3):
                start=time.perf_counter(); function(sources); samples.append(time.perf_counter()-start)
            times.append(statistics.median(samples))
        benchmark.append({"count":count,"all_fields_equal":equal,"old_seconds":times[0],"current_seconds":times[1]})
    results["cluster_benchmark"]=benchmark
    args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
