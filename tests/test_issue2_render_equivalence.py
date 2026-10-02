"""Independent numerical old/new rendering audit, without production edits.

Run as a script to write the task evidence; pytest performs the same audit.
Both packages are isolated by importlib and use a task-only snapshot of the
current default 26.2 directory. No source-tree ZIP is read or generated.
Known fixes are not used as a regression baseline. Non-whitelisted changes
remain findings in JSON even though the audit-completeness tests pass.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image
import pytest

NEW_ROOT = Path(__file__).resolve().parents[1]
OLD_ROOT = Path(os.environ.get("L3D_EQUIVALENCE_OLD_ROOT", str(
    NEW_ROOT.parent / "tasks/winui-startup-fix-20260927/git/repo")))
DEFAULT_RESOURCES = NEW_ROOT / "litmetica3d/mc_assets/26.2"
TASK_TESTS = Path(os.environ.get("L3D_EQUIVALENCE_WORK_DIR", str(
    NEW_ROOT / "build/render-equivalence")))
ASSET = None  # Scoped task/tests temporary snapshot; never a bundled ZIP.
EVIDENCE = NEW_ROOT.parent / "tasks/issue2-v063-20261003/evidence"
ATOL = 1e-12


def load_package(root, alias):
    """Load a full package, never accidentally using the new dependencies."""
    if alias not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            alias, root / "litmetica3d/__init__.py",
            submodule_search_locations=[str(root / "litmetica3d")])
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        spec.loader.exec_module(module)
    result = {}
    for name in ("model_loader", "entity_models", "conversion", "litematic", "solid"):
        full_name = alias + "." + name
        if full_name not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                full_name, root / "litmetica3d" / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[full_name] = module
            spec.loader.exec_module(module)
        result[name] = sys.modules[full_name]
    result["root"] = str(root)
    result["version"] = sys.modules[alias].__version__
    return result


def vec(value):
    return [float(value.x), float(value.y), float(value.z)]


def face_record(face):
    return {
        "vertices": [vec(v) for v in face.vertices],
        "normal": vec(face.normal),
        "uvs": None if face.uvs is None else [list(p) for p in face.uvs],
        "material": face.material, "texture": face.texture,
        "emission_texture": face.emission_texture,
        "emission_strength": float(face.emission_strength),
    }


def normal_quality(faces):
    angles, lengths, bad, degenerate = [], [], 0, 0
    for f in faces:
        p, n = np.asarray(f["vertices"]), np.asarray(f["normal"])
        if len(p) < 3:
            continue
        cross = np.cross(p[1] - p[0], p[2] - p[0])
        ln, lc = float(np.linalg.norm(n)), float(np.linalg.norm(cross))
        lengths.append(abs(ln - 1))
        if lc <= 1e-14:
            degenerate += 1
            continue
        if ln == 0 or float(np.dot(n, cross)) <= 0:
            bad += 1
        cosine = float(np.dot(n, cross)) / (ln * lc) if ln else 0
        angles.append(math.degrees(math.acos(min(1, max(-1, cosine)))))
    return {"max_normal_length_error": max(lengths, default=0),
            "max_normal_vs_winding_angle_degrees": max(angles, default=0),
            "opposite_or_zero_normals": bad, "degenerate_faces": degenerate}


def compare_faces(old_faces, new_faces):
    old = [face_record(f) for f in old_faces]
    new = [face_record(f) for f in new_faces]
    fields = ("vertices", "normal", "uvs", "material", "texture",
              "emission_texture", "emission_strength")
    stats = {key: {"changed_faces": 0, "max_abs_delta": 0.0,
                   "shape_mismatches": 0, "samples": []} for key in fields}
    for index, (a, b) in enumerate(zip(old, new)):
        for key in fields:
            if a[key] == b[key]:
                continue
            item = stats[key]
            item["changed_faces"] += 1
            if key in ("vertices", "normal", "uvs", "emission_strength"):
                if a[key] is None or b[key] is None:
                    item["shape_mismatches"] += 1
                else:
                    x, y = np.asarray(a[key], dtype=float), np.asarray(b[key], dtype=float)
                    if x.shape != y.shape:
                        item["shape_mismatches"] += 1
                    else:
                        item["max_abs_delta"] = max(item["max_abs_delta"], float(np.max(np.abs(x-y), initial=0)))
            if len(item["samples"]) < 2:
                item["samples"].append({"face": index, "old": a[key], "new": b[key]})
    changed = [key for key, s in stats.items() if s["changed_faces"]]
    significant = [key for key in changed if key in ("material", "texture", "emission_texture")
                   or stats[key]["shape_mismatches"] or stats[key]["max_abs_delta"] > ATOL]
    topology_changed = len(old) != len(new)
    if topology_changed:
        significant.insert(0, "face_count")
    def bounds(fs):
        p = np.asarray([v for f in fs for v in f["vertices"]])
        return [p.min(0).tolist(), p.max(0).tolist()] if len(p) else None
    def winding(f):
        p=np.asarray(f["vertices"])
        c=np.cross(p[1]-p[0],p[2]-p[0])
        length=float(np.linalg.norm(c))
        return c/length if length else c
    winding_delta=max((float(np.abs(winding(a)-winding(b)).max()) for a,b in zip(old,new)),default=0)
    return {"old_faces": len(old), "new_faces": len(new),
            "old_bounds": bounds(old), "new_bounds": bounds(new),
            "exact": not changed and not topology_changed,
            "equivalent_at_1e_12": not significant,
            "significant_fields": significant, "fields": stats,
            "old_normal_quality": normal_quality(old),
            "new_normal_quality": normal_quality(new),
            "old_material_counts": dict(Counter(f["material"] for f in old)),
            "new_material_counts": dict(Counter(f["material"] for f in new)),
            "index_comparison_valid": not topology_changed,
            "max_unit_winding_direction_delta":winding_delta}


def model_cases():
    cases = []
    def add(name, props=None, label=None, position=(0,0,0)):
        cases.append((label or name + repr(props or {}), "minecraft:"+name,
                      props or {}, position))
    for name in ("stone", "dirt", "grass_block", "oak_leaves", "short_grass",
                 "fern", "dandelion", "dead_bush", "glass", "white_stained_glass",
                 "glowstone", "sea_lantern", "shroomlight", "torch", "soul_torch"):
        props = {"snowy":"false"} if name == "grass_block" else {}
        if name == "oak_leaves": props = {"distance":"7","persistent":"false","waterlogged":"false"}
        add(name, props)
    for position in ((-123,17,9), (1234,-5,777), (1_000_001,7,-33)):
        add("stone", label="stone_weighted_"+str(position), position=position)
    for kind in ("bottom", "top", "double"):
        add("oak_slab", {"type":kind,"waterlogged":"false"})
    for facing in ("north", "east", "south", "west"):
        for shape in ("straight", "inner_left", "outer_right"):
            for half in ("bottom", "top"):
                add("oak_stairs", {"facing":facing,"half":half,"shape":shape,"waterlogged":"false"})
        for half in ("lower", "upper"):
            for hinge in ("left", "right"):
                for opened in ("false", "true"):
                    add("oak_door", {"facing":facing,"half":half,"hinge":hinge,"open":opened,"powered":"false"})
        add("wall_torch", {"facing":facing})
        add("soul_wall_torch", {"facing":facing})
    for connections in ((False,False,False,False), (True,False,True,False), (True,True,True,True)):
        props = dict(zip(("north","east","south","west"), (str(v).lower() for v in connections)))
        add("oak_fence", dict(props,waterlogged="false"))
        add("glass_pane", dict(props,waterlogged="false"))
    for hanging in ("false", "true"):
        add("lantern", {"hanging":hanging,"waterlogged":"false"})
        add("soul_lantern", {"hanging":hanging,"waterlogged":"false"})
    for lit in ("false", "true"):
        add("redstone_lamp", {"lit":lit})
        add("campfire", {"facing":"east","lit":lit,"signal_fire":"false","waterlogged":"false"})
    add("firefly_bush")
    return cases


@contextmanager
def resource_snapshot():
    """Targeted actual-resource snapshot, avoiding an SMB-wide tree scan.

    Include each tested blockstate and all of its alternatives, recursively
    inherited models, referenced textures/animation metadata, and entity maps.
    Missing builtin/entity models retain the normal entity-fallback behavior.
    """
    if not DEFAULT_RESOURCES.is_dir():
        raise FileNotFoundError("Default resources unavailable: " + str(DEFAULT_RESOURCES))
    files, visited, missing = {}, set(), []
    def include(relative, required=False):
        if ".." in Path(relative).parts or Path(relative).is_absolute():
            raise ValueError("Invalid asset reference: " + relative)
        if relative in files:
            return files[relative]
        source=DEFAULT_RESOURCES/relative
        if not source.is_file():
            if required: raise FileNotFoundError(source)
            return None
        raw=source.read_bytes(); files[relative]=raw
        return raw
    def texture(reference):
        if isinstance(reference,dict): reference=reference.get("sprite")
        if not isinstance(reference,str) or reference.startswith("#"): return
        ns,name=reference.split(":",1) if ":" in reference else ("minecraft",reference)
        rel=f"assets/{ns}/textures/{name}.png"
        include(rel); include(rel+".mcmeta")
    def model(reference):
        ns,name=reference.split(":",1) if ":" in reference else ("minecraft",reference)
        if not name.startswith(("block/","item/")): name="block/"+name
        rel=f"assets/{ns}/models/{name}.json"
        if rel in visited: return
        visited.add(rel)
        raw=include(rel)
        if raw is None:
            missing.append(rel); return
        data=json.loads(raw)
        if data.get("parent"): model(data["parent"])
        for value in data.get("textures",{}).values(): texture(value)
        for elem in data.get("elements",[]):
            for face in elem.get("faces",{}).values(): texture(face.get("texture"))
    def variants(entry):
        for item in entry if isinstance(entry,list) else [entry]:
            if item.get("model"): model(item["model"])
    for name in sorted({c[1] for c in model_cases()} | {"minecraft:chest","minecraft:white_shulker_box","minecraft:white_banner","minecraft:skeleton_skull"}):
        ns,base=name.split(":",1)
        rel=f"assets/{ns}/blockstates/{base}.json"
        raw=include(rel,required=True)
        data=json.loads(raw)
        for entry in data.get("variants",{}).values(): variants(entry)
        for part in data.get("multipart",[]): variants(part["apply"])
    for ref in ("entity/chest/normal","entity/chest/normal_left","entity/chest/normal_right",
                "entity/chest/trapped","entity/chest/trapped_left","entity/chest/trapped_right",
                "entity/chest/ender","entity/shulker/shulker_white","entity/banner/banner_base",
                "entity/skeleton/skeleton","entity/conduit/base"):
        texture("minecraft:"+ref)
    TASK_TESTS.mkdir(parents=True,exist_ok=True)
    manifest={"source":str(DEFAULT_RESOURCES),"snapshot_kind":"targeted dependency closure from current default directory",
              "included_files":len(files),"missing_model_refs":missing,
              "file_sha256":{rel:hashlib.sha256(raw).hexdigest() for rel,raw in sorted(files.items())}}
    with tempfile.TemporaryDirectory(prefix="render-equivalence-assets-",dir=TASK_TESTS) as temp:
        snapshot=Path(temp)/"26.2-audit.zip"
        with zipfile.ZipFile(snapshot,"w",compression=zipfile.ZIP_STORED) as archive:
            for relative,raw in sorted(files.items()): archive.writestr(relative,raw)
        yield snapshot,manifest


def allow_verified_old_normal_fix(row,allowed,tags):
    """Whitelist only the now-explicitly-approved erroneous-normal repair."""
    old,new=row["old_normal_quality"],row["new_normal_quality"]
    new_correct=(new["max_normal_length_error"] <= 1e-10 and
                 new["max_normal_vs_winding_angle_degrees"] <= 1e-5 and
                 new["opposite_or_zero_normals"] == 0)
    old_wrong=(old["max_normal_length_error"] > 1e-10 or
               old["max_normal_vs_winding_angle_degrees"] > 1e-5 or
               old["opposite_or_zero_normals"] > 0)
    others_exact=all(s["changed_faces"] == 0 for name,s in row["fields"].items() if name != "normal")
    if row["significant_fields"] == ["normal"] and others_exact and row["old_faces"] == row["new_faces"] and row["max_unit_winding_direction_delta"] == 0 and new_correct and old_wrong:
        allowed.add("normal"); tags.append("metadata_normal_correction")
        row["normal_fix_verified_against_vertex_cross_product"] = True
        row["normal_fix_all_other_fields_strictly_equal"] = True
    return row


def verify_single_chest_facing(old,new,faces,row):
    a,b=faces
    unchanged=("uvs","material","texture","emission_texture","emission_strength")
    vertices=max((abs(want-actual) for x,y in zip(a,b) for v,w in zip(x.vertices,y.vertices)
                  for want,actual in zip((1-v.x,v.y,1-v.z),vec(w))),default=0)
    normals=max((abs(want-actual) for x,y in zip(a,b)
                 for want,actual in zip((-x.normal.x,x.normal.y,-x.normal.z),vec(y.normal))),default=0)
    volumes=[p["solid"].manifold_from_closed_faces(f).volume() for p,f in zip((old,new),faces)]
    validation={"rotation_degrees":180,"rotation_origin":[0.5,0.5,0.5],
                "max_rigid_vertex_residual":vertices,"max_rigid_normal_residual":normals,
                "other_fields_strictly_equal":all(row["fields"][k]["changed_faces"] == 0 for k in unchanged),
                "old_volume":volumes[0],"new_volume":volumes[1],"volume_abs_delta":abs(volumes[0]-volumes[1]),
                "face_count_equal":len(a)==len(b)}
    validation["verified"]=bool(validation["face_count_equal"] and validation["other_fields_strictly_equal"]
                                and vertices <= ATOL and normals <= ATOL and validation["volume_abs_delta"] <= ATOL)
    row["chest_facing_validation"]=validation
    return validation["verified"]


def selected_model_metadata(loader, name, props, position):
    bs = loader._load_blockstate(name)
    if "variants" in bs:
        selection = loader._resolve_simple_variant(bs["variants"], props)
        entries = [loader._select_weighted(selection,name,props,position,0)]
    else:
        selection = loader._resolve_multipart(bs["multipart"], props) or []
        entries = [loader._select_weighted(v,name,props,position,i) for i,v in enumerate(selection)]
    result = {"entries": entries, "rescale": False, "uvlock_rotated": False,
              "slanted_element": False}
    for entry in entries:
        result["uvlock_rotated"] |= bool(entry.get("uvlock") and (entry.get("x") or entry.get("y")))
        model = loader._load_model(entry["model"])
        for elem in model.get("elements", []):
            r = elem.get("rotation", {})
            result["rescale"] |= bool(r.get("rescale") and r.get("angle"))
            result["slanted_element"] |= bool(float(r.get("angle",0)) % 90)
    return result


def categorize(comparison, allowed, tags):
    comparison["expected_fix_tags"] = tags
    fields = set(comparison["significant_fields"])
    comparison["unexpected_fields"] = sorted(fields - set(allowed))
    if comparison["equivalent_at_1e_12"]:
        comparison["classification"] = "exact" if comparison["exact"] else "roundoff_only"
    elif not comparison["unexpected_fields"] and "metadata_normal_correction" in tags:
        comparison["classification"] = "metadata_normal_correction"
    elif not comparison["unexpected_fields"]:
        comparison["classification"] = "expected_fix_difference"
    else:
        comparison["classification"] = "additional_difference_requires_review"
    return comparison


def pixel_compare(old_loader, new_loader, textures):
    rows = []
    for texture in sorted(t for t in textures if t is not None):
        a, b = old_loader.texture_bytes(texture), new_loader.texture_bytes(texture)
        if a is None or b is None:
            rows.append({"texture":texture,"available_old":a is not None,"available_new":b is not None,"equal":a==b})
            continue
        x = np.asarray(Image.open(io.BytesIO(a)).convert("RGBA"))
        y = np.asarray(Image.open(io.BytesIO(b)).convert("RGBA"))
        same_shape = x.shape == y.shape
        rows.append({"texture":texture,"shape_old":list(x.shape),"shape_new":list(y.shape),
                     "equal":same_shape and np.array_equal(x,y),
                     "changed_pixels":int(np.any(x!=y,axis=-1).sum()) if same_shape else None,
                     "max_channel_delta":int(np.abs(x.astype(int)-y.astype(int)).max()) if same_shape else None})
    return rows


def audit_models(old, new):
    rows, pixels = [], []
    for mode in ("visual_cutout", "visual_solid", "print_closed"):
        visual, solid = mode != "print_closed", mode == "visual_solid"
        a = old["model_loader"].ModelLoader(ASSET,visual_textures=visual,solid_textures=solid)
        b = new["model_loader"].ModelLoader(ASSET,visual_textures=visual,solid_textures=solid)
        textures = set()
        try:
            for label, name, props, position in model_cases():
                fa = a.resolve(name,props,position,closed=not visual)
                fb = b.resolve(name,props,position,closed=not visual)
                if fa.status != "ok" or fb.status != "ok":
                    raise AssertionError((label, mode, fa.status, fb.status))
                metadata = selected_model_metadata(b,name,props,position)
                allowed, tags = set(), []
                if metadata["rescale"]:
                    allowed.update(("vertices","normal")); tags.append("rescale")
                if visual and metadata["uvlock_rotated"]:
                    allowed.update(("uvs",)); tags.append("uvlock")
                    # Alpha resampling may change topology and face ordering.
                    # Field deltas still appear in the evidence, not suppressed.
                    if not solid and len(fa.faces) != len(fb.faces):
                        allowed.update(("vertices","normal","face_count","material","texture","emission_strength"))
                comparison=compare_faces(fa.faces,fb.faces)
                allow_verified_old_normal_fix(comparison,allowed,tags)
                row = categorize(comparison,allowed,tags)
                row.update(label=label,block=name,properties=props,position=list(position),mode=mode,source=metadata)
                rows.append(row)
                for faces in (fa.faces,fb.faces):
                    textures.update(f.texture for f in faces)
                    textures.update(f.emission_texture for f in faces)
            for p in pixel_compare(a,b,textures):
                p["mode"] = mode; pixels.append(p)
        finally:
            a.close(); b.close()
    entity_rows = []
    for visual in (False, True):
        for name in ("chest", "trapped_chest", "ender_chest"):
            for kind in ("single", "left", "right"):
                if name == "ender_chest" and kind != "single": continue
                for facing in ("south", "west", "north", "east"):
                    props = {"type":kind,"facing":facing,"waterlogged":"false"}
                    fs = [p["entity_models"].get_entity_geometry("minecraft:"+name,props,visual=visual) for p in (old,new)]
                    allowed = {"vertices","normal"} if kind != "single" and not visual else set()
                    tags = ["double_chest_orientation"] if allowed else []
                    comparison=compare_faces(*fs)
                    if kind == "single" and not visual and facing in {"east","west"}:
                        if verify_single_chest_facing(old,new,fs,comparison):
                            allowed.update(("vertices","normal")); tags.append("chest_facing")
                    row = categorize(comparison,allowed,tags)
                    row.update(label=name+repr(props),block="minecraft:"+name,properties=props,mode="entity_visual" if visual else "entity_print")
                    entity_rows.append(row)
        for name, props in (("white_banner",{"rotation":"1"}), ("white_banner",{"rotation":"4"}),
                            ("skeleton_skull",{"rotation":"1"}), ("skeleton_skull",{"rotation":"4"}),
                            ("white_shulker_box",{"facing":"east"}), ("conduit",{})):
            fs = [p["entity_models"].get_entity_geometry("minecraft:"+name,props,visual=visual) for p in (old,new)]
            allowed,tags=set(),[]
            comparison=compare_faces(*fs)
            allow_verified_old_normal_fix(comparison,allowed,tags)
            row = categorize(comparison,allowed,tags)
            row.update(label=name+repr(props),block="minecraft:"+name,properties=props,mode="entity_visual" if visual else "entity_print")
            entity_rows.append(row)
    return rows, entity_rows, pixels


def schematic(package, cases, origin=(0,0,0)):
    m = package["litematic"]
    palette = [m.BlockState(name,dict(props)) for _,name,props,_ in cases]
    # Spacing keeps geometry differences local and avoids accidental overlap.
    blocks = {(i*3,0,0):i for i in range(len(cases))}
    r = m.Region("Audit",origin,(len(cases)*3,1,1),palette,blocks)
    return m.Schematic(6,0,regions={"Audit":r})


def capture_convert(package, cases, directory, *, geometry="visual", fmt="obj", **kw):
    m = package["conversion"]
    captured = []
    original = m.CompactVisualMesh.add_faces
    def add_faces(self, faces, offset=None):
        for face in faces:
            record = face_record(face)
            if offset is not None:
                record["vertices"] = [[v[i]+offset[i] for i in range(3)] for v in record["vertices"]]
            captured.append(record)
        return original(self, faces, offset=offset)
    output = directory / ("model."+fmt)
    origin = kw.pop("origin",(0,0,0))
    options = m.ConversionOptions(Path("audit-memory.litematic"),output,
                                  asset_path=ASSET,geometry=geometry,output_format=fmt,**kw)
    with patch.object(m,"load_schematic",return_value=schematic(package,cases,origin)), \
         patch.object(m.CompactVisualMesh,"add_faces",add_faces):
        report = m.convert(options)
    return captured, asdict(report), output


def compare_records(a,b):
    # Conversion capture retains the native Face-field types numerically.
    def restore(records):
        from types import SimpleNamespace as NS
        result = []
        for f in records:
            fields = dict(f)
            fields["vertices"] = [NS(**dict(zip("xyz",v))) for v in f["vertices"]]
            fields["normal"] = NS(**dict(zip("xyz",f["normal"])))
            result.append(NS(**fields))
        return result
    return compare_faces(restore(a),restore(b))


def obj_record(path):
    vertices, uvs, normals, faces, material = [], [], [], [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts: continue
        if parts[0] == "v": vertices.append(list(map(float,parts[1:4])))
        elif parts[0] == "vt": uvs.append(list(map(float,parts[1:3])))
        elif parts[0] == "vn": normals.append(list(map(float,parts[1:4])))
        elif parts[0] == "usemtl": material = " ".join(parts[1:])
        elif parts[0] == "f":
            corners = []
            for part in parts[1:]:
                fields = part.split("/")
                vi = int(fields[0]); ti = int(fields[1]) if len(fields)>1 and fields[1] else 0
                ni = int(fields[2]) if len(fields)>2 and fields[2] else 0
                corners.append({"vertex":vertices[vi-1],"uv":uvs[ti-1] if ti else None,
                                "normal":normals[ni-1] if ni else None})
            faces.append({"corners":corners,"material":material})
    return {"vertices":vertices,"uvs":uvs,"normals":normals,"faces":faces}


def stl_record(path):
    raw = path.read_bytes()
    count = struct.unpack_from("<I",raw,80)[0]
    assert len(raw) == 84+50*count
    data = np.frombuffer(raw,offset=84,count=count,dtype=np.dtype([
        ("normal","<f4",(3,)),("vertices","<f4",(3,3)),("attribute","<u2")]))
    return {"normals":data["normal"].tolist(),"vertices":data["vertices"].tolist(),
            "attributes":data["attribute"].tolist()}


def export_compare(a,b):
    ra = obj_record(a) if a.suffix == ".obj" else stl_record(a)
    rb = obj_record(b) if b.suffix == ".obj" else stl_record(b)
    fields = {}
    for key in ra:
        equal = ra[key] == rb[key]
        item = {"equal":equal,"old_count":len(ra[key]),"new_count":len(rb[key])}
        if key not in ("faces",):
            x,y = np.asarray(ra[key]),np.asarray(rb[key])
            item["max_abs_delta"] = float(np.abs(x-y).max(initial=0)) if x.shape==y.shape else None
        fields[key] = item
    artifacts = []
    for x in sorted(a.parent.rglob("*")):
        if not x.is_file() or x == a: continue
        rel = x.relative_to(a.parent); y = b.parent/rel
        if x.suffix == ".png" and y.exists():
            p = np.asarray(Image.open(x).convert("RGBA")); q = np.asarray(Image.open(y).convert("RGBA"))
            artifacts.append({"file":rel.as_posix(),"equal":p.shape==q.shape and np.array_equal(p,q),"pixel_comparison":True})
        elif x.suffix in (".mtl", ".json"):
            p=x.read_text(encoding="utf-8"); q=y.read_text(encoding="utf-8") if y.exists() else ""
            artifacts.append({"file":rel.as_posix(),"equal":p==q})
    only_new = [p.relative_to(b.parent).as_posix() for p in b.parent.rglob("*")
                if p.is_file() and not (a.parent/p.relative_to(b.parent)).exists()]
    return {"equal":all(v["equal"] for v in fields.values()) and all(v["equal"] for v in artifacts) and not only_new,
            "numeric_fields":fields,"material_emission_texture_artifacts":artifacts,"only_new_files":only_new,
            "obj_normals_exported":bool(ra.get("normals"))}


def audit_conversion(old,new):
    rows = []
    names = {"stone","dirt","grass_block","oak_leaves","dandelion","glass","white_stained_glass",
             "glowstone","sea_lantern","shroomlight","torch","soul_torch","oak_slab","redstone_lamp"}
    cases = [c for c in model_cases() if c[1].split(":")[1] in names]
    cases += [("chest_visual","minecraft:chest",{"type":"single","facing":"south"},(0,0,0)),
              ("shulker","minecraft:white_shulker_box",{"facing":"east"},(0,0,0))]
    loader=new["model_loader"].ModelLoader(ASSET)
    try:
        # Export optimization controls must not mix in a known rescale fix.
        cases=[c for c in cases if c[1] in {"minecraft:chest","minecraft:white_shulker_box"}
               or not selected_model_metadata(loader,c[1],c[2],c[3])["rescale"]]
    finally:
        loader.close()
    print_cases = [c for c in cases if c[1].split(":")[1] in {"stone","dirt","grass_block","glass","glowstone","oak_slab","redstone_lamp"}]
    print_cases += [c for c in model_cases() if c[1]=="minecraft:oak_stairs"][:3]
    with tempfile.TemporaryDirectory(prefix="l3d-render-equivalence-") as tmp:
        root = Path(tmp)
        for geometry,fmt,kw in (("visual","obj",{}),("visual","stl",{}),
                ("visual","obj",{"scale":3.25,"center":True}),
                ("visual","obj",{"solid_textures":True}),
                ("visual","obj",{"blender_lights":"clustered"}),
                ("visual","obj",{"seamless_glass":True}),
                ("print","obj",{}),("print","stl",{}),
                ("print","stl",{"scale":3.25,"center":True})):
            label = geometry+"_"+fmt+"_"+repr(kw)
            base=root/str(len(rows)); a_dir=base/"old"; b_dir=base/"new"
            used = print_cases if geometry=="print" else cases
            fa,ra,pa = capture_convert(old,used,a_dir,geometry=geometry,fmt=fmt,**kw)
            fb,rb,pb = capture_convert(new,used,b_dir,geometry=geometry,fmt=fmt,**kw)
            row={"label":label,"cohort":"unaffected_optimization_control","states":len(used),"export":export_compare(pa,pb),
                 "face_capture":categorize(compare_records(fa,fb),set(),[]) if geometry=="visual" else None,
                 "report_old":ra,"report_new":rb}
            rows.append(row)
        # Non-axis normals are additional internal changes; determine separately
        # whether they affect actual exported face winding or vertex geometry.
        normals_cases=[c for c in model_cases() if c[1] in {"minecraft:wall_torch","minecraft:soul_wall_torch","minecraft:lantern","minecraft:soul_lantern"}]
        normals_cases += [("banner","minecraft:white_banner",{"rotation":"1"},(0,0,0)),
                         ("head","minecraft:skeleton_skull",{"rotation":"1"},(0,0,0))]
        for geometry,fmt,kw in (("visual","obj",{}),("visual","stl",{}),("print","stl",{}),
                               ("visual","obj",{"solid_textures":True}),("visual","stl",{"solid_textures":True}),("print","obj",{})):
            base=root/("normal_"+geometry+"_"+fmt+"_"+str(len(rows)))
            fa,ra,pa=capture_convert(old,normals_cases,base/"old",geometry=geometry,fmt=fmt,**kw)
            fb,rb,pb=capture_convert(new,normals_cases,base/"new",geometry=geometry,fmt=fmt,**kw)
            comparison=compare_records(fa,fb) if geometry=="visual" else None
            if comparison:
                allowed,tags=set(),[]
                allow_verified_old_normal_fix(comparison,allowed,tags)
                comparison=categorize(comparison,allowed,tags)
            rows.append({"label":"normal_changes_"+geometry+"_"+fmt+"_"+repr(kw),"cohort":"metadata_normal_export_control",
                         "states":len(normals_cases),"export":export_compare(pa,pb),
                         "covered_states":[{"block":c[1],"properties":c[2]} for c in normals_cases],"options":kw,
                         "face_capture":comparison,
                         "report_old":ra,"report_new":rb})
        rescale_cases=[("dandelion","minecraft:dandelion",{},(0,0,0))]
        for geometry,fmt in (("visual","obj"),("visual","stl"),("print","stl")):
            base=root/("rescale_"+geometry+"_"+fmt)
            fa,ra,pa=capture_convert(old,rescale_cases,base/"old",geometry=geometry,fmt=fmt)
            fb,rb,pb=capture_convert(new,rescale_cases,base/"new",geometry=geometry,fmt=fmt)
            rows.append({"label":"expected_rescale_"+geometry+"_"+fmt,"cohort":"expected_fix",
                         "expected_fix_tags":["rescale"],"export":export_compare(pa,pb),
                         "face_capture":categorize(compare_records(fa,fb),{"vertices","normal"},["rescale"]) if geometry=="visual" else None,
                         "report_old":ra,"report_new":rb})
        for facing in ("south","north","west","east"):
            used=[("single_chest","minecraft:chest",{"type":"single","facing":facing},(0,0,0))]
            base=root/("single_chest_"+facing)
            fa,ra,pa=capture_convert(old,used,base/"old",geometry="print",fmt="stl")
            fb,rb,pb=capture_convert(new,used,base/"new",geometry="print",fmt="stl")
            volume_delta=abs(ra["solid"]["volume_after_cavity_fill"]-rb["solid"]["volume_after_cavity_fill"])
            rows.append({"label":"single_chest_print_"+facing,"cohort":"chest_facing_control" if facing in {"west","east"} else "single_chest_exact_control",
                         "expected_fix_tags":["chest_facing"] if facing in {"west","east"} else [],
                         "volume_abs_delta":volume_delta,"export":export_compare(pa,pb),"report_old":ra,"report_new":rb})
        # This is deliberately an expected rule-coordinate change, not a baseline failure.
        rule=root/"rules.json"; rule.write_text(json.dumps({"rules":[{
            "region":"Audit","position":[10,20,30],"multiplier":0}]}),encoding="utf-8")
        used=[("glowstone","minecraft:glowstone",{},(0,0,0))]
        fa,ra,pa=capture_convert(old,used,root/"rules_old",origin=(10,20,30),emission_config=rule)
        fb,rb,pb=capture_convert(new,used,root/"rules_new",origin=(10,20,30),emission_config=rule)
        rows.append({"label":"expected_rule_schematic_coordinates","expected_fix_tags":["rule_coordinates"],
                     "face_capture":categorize(compare_records(fa,fb),{"emission_texture","emission_strength"},["rule_coordinates"]),
                     "old_emissive_blocks":ra["emissive_blocks"],"new_emissive_blocks":rb["emissive_blocks"],
                     "old_lights":ra["blender_lights"],"new_lights":rb["blender_lights"]})
    return rows


def audit_known_nonrender_fixes(old,new):
    palette = ["minecraft:air","minecraft:stone"]
    decoded=[]
    for p in (old,new):
        decoded.append(p["litematic"]._decode_block_states([0x55],
                       [p["litematic"].BlockState(n) for n in palette],(4,1,1)))
    import manifold3d as m3d
    def hollow(size, cavity, origin=(0,0,0)):
        margin=(size-cavity)/2
        return (m3d.Manifold.cube((size,)*3)-m3d.Manifold.cube((cavity,)*3).translate((margin,)*3)).translate(origin)
    source=hollow(10,8)+hollow(4,2,(3,3,3))
    cavity=[]
    for p in (old,new):
        report=p["solid"].SolidReport()
        try:
            solid=p["solid"].process_components_and_cavities(source,cavities="fill",components="keep",min_component_volume=0,report=report)
            if isinstance(solid,tuple): solid=solid[0]
            cavity.append({"volume":solid.volume(),"status":str(solid.status()),"report":asdict(report)})
        except Exception as exc:
            cavity.append({"error":str(exc),"report":asdict(report)})
    return {"parser_minimum_2_bits":{"expected_indices":[1,1,1,1],"old_nonair_positions":[list(k) for k in decoded[0]],
             "new_nonair_positions":[list(k) for k in decoded[1]],"expected_fix":True},
            "fill_internal_island":{"expected_volume":1000,"old":cavity[0],"new":cavity[1],"expected_fix":True}}


def _run_audit_snapshot(snapshot_manifest):
    start=time.perf_counter()
    old=load_package(OLD_ROOT,"_l3d_render_old")
    new=load_package(NEW_ROOT,"_l3d_render_new")
    models,entities,pixels=audit_models(old,new)
    conversions=audit_conversion(old,new)
    normal_exports=[r for r in conversions if r.get("cohort")=="metadata_normal_export_control"]
    normal_export_ok=bool(normal_exports) and all(r["export"]["equal"] for r in normal_exports)
    for row in models+entities:
        if row["classification"] == "metadata_normal_correction":
            row["normal_fix_actual_export_zero_difference"]=normal_export_ok
            if not normal_export_ok:
                row["classification"]="additional_difference_requires_review"
                row["unexpected_fields"]=["normal"]
    categories=Counter(r["classification"] for r in models+entities)
    findings=[{k:r[k] for k in ("label","block","properties","mode","unexpected_fields","fields","old_normal_quality","new_normal_quality")}
              for r in models+entities if r["unexpected_fields"]]
    fingerprints={}
    for side,root in (("old",OLD_ROOT),("new",NEW_ROOT)):
        fingerprints[side]={name:hashlib.sha256((root/"litmetica3d"/(name+".py")).read_bytes()).hexdigest()
                            for name in ("model_loader","entity_models","conversion","solid","visual_mesh","litematic")}
    return {"schema":1,"old_root":str(OLD_ROOT),"new_root":str(NEW_ROOT),"source_sha256":fingerprints,
            "old_version":old["version"],"new_version":new["version"],"asset":str(ASSET),
            "asset_bytes":ASSET.stat().st_size,"tolerance":ATOL,
            "method":"isolated importlib packages; native ordered Face vertices, normals, UVs, material, texture, emission; decoded RGBA pixels; captured conversion faces; parsed OBJ/STL",
            "resource_snapshot":snapshot_manifest,
            "scope_note":"representative states, not every 26.2 block; metadata_normal_correction requires only-normal strict field changes plus six actual zero-difference exports; single chest south/north exact, east/west only proven rigid 180-degree chest_facing correction",
            "model_case_count":len(model_cases()),"model_runs":models,"entity_runs":entities,"texture_pixel_checks":pixels,
            "conversion_runs":conversions,"known_nonrender_fixes":audit_known_nonrender_fixes(old,new),
            "summary":{"model_and_entity_runs":len(models)+len(entities),"categories":dict(categories),
                       "texture_checks":len(pixels),"texture_failures":sum(not p["equal"] for p in pixels),
                       "conversion_export_checks":sum(r.get("cohort")=="unaffected_optimization_control" for r in conversions),
                       "conversion_export_failures":sum(not r["export"]["equal"] for r in conversions if r.get("cohort")=="unaffected_optimization_control"),
                       "metadata_normals_export_checks":len(normal_exports),
                       "metadata_normals_export_failures":sum(not r["export"]["equal"] for r in normal_exports),
                       "expected_fix_export_differences":sum(not r["export"]["equal"] for r in conversions if r.get("cohort")=="expected_fix"),
                       "chest_facing_export_differences":sum(not r["export"]["equal"] for r in conversions if r.get("cohort")=="chest_facing_control"),
                       "additional_difference_count":len(findings),"strict_equivalence_gate_passed":not findings},
            "additional_differences":findings,"elapsed_seconds":time.perf_counter()-start}


def run_audit():
    global ASSET
    start=time.perf_counter()
    with resource_snapshot() as (snapshot,manifest):
        ASSET=snapshot
        result=_run_audit_snapshot(manifest)
    ASSET=None
    result["elapsed_seconds_with_asset_snapshot"]=time.perf_counter()-start
    result["temporary_asset_snapshot_removed"]=True
    return result


@pytest.fixture(scope="module")
def audit():
    if not OLD_ROOT.is_dir():
        if os.environ.get("L3D_EQUIVALENCE_OLD_ROOT"):
            pytest.fail("Explicit independent baseline is missing: " + str(OLD_ROOT))
        pytest.skip("No independent baseline checkout; set L3D_EQUIVALENCE_OLD_ROOT to enable")
    return run_audit()


def test_issue2_audit_has_actual_26_2_geometry(audit):
    assert audit["model_case_count"] >= 90
    assert all(r["old_faces"] and r["new_faces"] for r in audit["model_runs"])
    assert audit["summary"]["texture_failures"] == 0


def test_issue2_unaffected_conversion_outputs_are_equivalent(audit):
    assert audit["summary"]["conversion_export_checks"] >= 9
    assert audit["summary"]["conversion_export_failures"] == 0
    assert all(r["face_capture"]["equivalent_at_1e_12"] for r in audit["conversion_runs"]
               if r.get("face_capture") and r.get("cohort")=="unaffected_optimization_control")
    assert audit["summary"]["metadata_normals_export_failures"] == 0


def test_issue2_expected_fixes_not_old_bug_baselines(audit):
    fixes=audit["known_nonrender_fixes"]
    assert len(fixes["parser_minimum_2_bits"]["new_nonair_positions"]) == 4
    assert abs(fixes["fill_internal_island"]["new"]["volume"]-1000) < 1e-6
    rules=next(r for r in audit["conversion_runs"] if r["label"]=="expected_rule_schematic_coordinates")
    assert (rules["old_emissive_blocks"],rules["new_emissive_blocks"]) == (1,0)


def test_issue2_additional_differences_are_explicitly_reported(audit):
    for row in audit["model_runs"]+audit["entity_runs"]:
        if not row["equivalent_at_1e_12"]:
            assert row["classification"] in ("expected_fix_difference","metadata_normal_correction","additional_difference_requires_review")
            assert row["expected_fix_tags"] or row["unexpected_fields"]


def test_issue2_representative_original_correct_models_are_exact(audit):
    required={"minecraft:stone","minecraft:dirt","minecraft:grass_block",
              "minecraft:oak_leaves","minecraft:oak_slab","minecraft:oak_door",
              "minecraft:glass","minecraft:white_stained_glass","minecraft:glass_pane",
              "minecraft:glowstone","minecraft:sea_lantern","minecraft:shroomlight",
              "minecraft:torch","minecraft:soul_torch","minecraft:redstone_lamp"}
    rows=[r for r in audit["model_runs"] if r["block"] in required]
    assert {r["block"] for r in rows} == required
    assert all(r["exact"] for r in rows)
    assert all(r["max_unit_winding_direction_delta"] == 0 for r in rows)


def test_issue2_no_non_whitelisted_effect_changes(audit):
    # Do not silently convert additional changes to an accepted baseline.
    # This gate fails until production is restored or the user explicitly
    # approves a new fix category. Other tests verify audit completeness.
    findings=audit["additional_differences"]
    grouped=Counter((r["block"],r["mode"],tuple(r["unexpected_fields"])) for r in findings)
    assert not findings, "Unapproved model effect changes: " + repr(dict(grouped))


def test_issue2_metadata_normal_tag_cannot_hide_any_other_field_change(audit):
    tagged=[r for r in audit["model_runs"]+audit["entity_runs"] if r["classification"]=="metadata_normal_correction"]
    assert tagged
    exports=[r for r in audit["conversion_runs"] if r.get("cohort")=="metadata_normal_export_control"]
    assert len(exports) == 6 and all(r["export"]["equal"] for r in exports)
    covered={(c["block"],tuple(sorted(c["properties"].items()))) for r in exports for c in r["covered_states"]}
    for r in tagged:
        assert r["normal_fix_verified_against_vertex_cross_product"]
        assert r["normal_fix_all_other_fields_strictly_equal"]
        assert r["normal_fix_actual_export_zero_difference"]
        assert all(s["changed_faces"]==0 for k,s in r["fields"].items() if k != "normal")
        assert (r["block"],tuple(sorted(r["properties"].items()))) in covered


def test_issue2_single_chest_only_approved_rigid_facing_change(audit):
    rows=[r for r in audit["entity_runs"] if r["mode"]=="entity_print" and r["properties"].get("type")=="single"]
    assert len(rows) == 12
    for r in rows:
        if r["properties"]["facing"] in {"south","north"}:
            assert r["exact"]
        else:
            assert r["expected_fix_tags"]==["chest_facing"]
            assert r["chest_facing_validation"]["verified"]
            assert r["chest_facing_validation"]["volume_abs_delta"] <= ATOL
    for r in audit["conversion_runs"]:
        if r.get("cohort")=="single_chest_exact_control":
            assert r["export"]["equal"] and r["volume_abs_delta"] <= ATOL
        elif r.get("cohort")=="chest_facing_control":
            assert r["volume_abs_delta"] <= ATOL


def test_issue2_missing_optional_baseline_is_explicit_skip(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.modules[__name__], "OLD_ROOT", tmp_path / "missing")
    monkeypatch.delenv("L3D_EQUIVALENCE_OLD_ROOT", raising=False)
    with pytest.raises(pytest.skip.Exception, match="No independent baseline"):
        audit.__wrapped__()


def test_issue2_explicit_missing_baseline_is_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.modules[__name__], "OLD_ROOT", tmp_path / "missing")
    monkeypatch.setenv("L3D_EQUIVALENCE_OLD_ROOT", str(tmp_path / "missing"))
    with pytest.raises(pytest.fail.Exception, match="Explicit independent baseline"):
        audit.__wrapped__()


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=EVIDENCE/"render-equivalence.json")
    args=parser.parse_args()
    result=run_audit()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result["summary"],ensure_ascii=False,indent=2))
    print("Evidence:",args.output)
    print("Elapsed:",result["elapsed_seconds"])
