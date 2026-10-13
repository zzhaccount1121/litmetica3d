import json
from types import SimpleNamespace
import numpy as np
import pytest
from litmetica3d.conversion import ConversionOptions, validate_options, bundled_asset_path, ConversionCancelled
from litmetica3d.model_loader import ModelLoader
from litmetica3d.scene_entities import EntityRenderer, append_scene_entities, INVISIBLE
from litmetica3d.litematic import Region, Schematic, _parse_region
from litmetica3d.visual_mesh import CompactVisualMesh


def test_item_sprite_does_not_self_shadow_with_duplicate_back_face():
    from litmetica3d.scene_entities import _quad
    assert len(_quad('minecraft:item/diamond')) == 1


@pytest.fixture(scope='module')
def loader():
    l = ModelLoader(bundled_asset_path(), visual_textures=True)
    yield l
    l.close()


def test_default_off_and_core_rejects_incompatible_parameters(tmp_path):
    args = dict(input_path=tmp_path/'in.litematic',output_path=tmp_path/'out.obj')
    assert not ConversionOptions(**args).experimental_entities
    for fmt,mode in [('stl','print'),('stl','visual'),('obj','print')]:
        with pytest.raises(ValueError):
            validate_options(ConversionOptions(**args,output_format=fmt,geometry=mode,experimental_entities=True))
    validate_options(ConversionOptions(**args,output_format='obj',geometry='visual',experimental_entities=True))


def test_all_registered_entity_types_have_finite_geometry_and_existing_textures(loader):
    r = EntityRenderer(loader)
    try:
        assert len(r.assets.catalog['types']) == 158
        for entity in r.assets.catalog['types']:
            faces,status = r.render({'id':entity,'text':'Test','Item':{'id':'minecraft:diamond'}})
            assert faces or status in {'empty','invisible'}, entity
            for f in faces:
                assert np.isfinite([[v.x,v.y,v.z] for v in f.vertices]).all(),entity
                assert np.isfinite(f.uvs).all(),entity
                assert loader.texture_bytes(f.texture), (entity,f.texture)
    finally:
        r.assets.close()


def test_region_parser_retains_entity_nbt_without_changing_block_order():
    data = {'Position':{'x':8,'y':2,'z':-3},'Size':{'x':-1,'y':1,'z':1},
            'BlockStatePalette':[{'Name':'minecraft:stone'}],'BlockStates':[0],
            'Entities':[{'id':'minecraft:pig','Pos':[.5,0,.5]}]}
    result = _parse_region('region', data)
    assert result.entities == data['Entities']
    assert result.blocks == {(0,0,0):0}
    assert not _parse_region('region',data,load_blocks=False).entities


def test_coordinates_region_selection_duplicates_and_report(loader):
    e = {'id':'minecraft:pig','Pos':[.5,1.25,-.5],'UUID':[1,2,3,4]}
    region = Region('chosen',(12,3,-7),(-2,4,2),[],{},entities=[e,e])
    excluded = Region('excluded',(999,999,999),(1,1,1),[],{},entities=[e])
    schematic = Schematic(6,4903,regions={'chosen':region,'excluded':excluded})
    mesh = CompactVisualMesh()
    options = SimpleNamespace(fallback='ignore')
    progress = []
    report = append_scene_entities(schematic,{'chosen'},(10,0,-10),mesh,loader,options,lambda *v:progress.append(v),lambda:False)
    assert report['rendered']==1 and report['duplicates']==1 and report['errors']==0
    r=EntityRenderer(loader)
    try:
        local,_=r.render(e)
        expected=CompactVisualMesh(); expected.add_faces(local,offset=(2.5,4.25,2.5))
        mesh.flush();expected.flush()
        np.testing.assert_array_equal(mesh.chunks[0].vertices,expected.chunks[0].vertices)
    finally:
        r.assets.close()


@pytest.mark.parametrize('fallback',['cube','ignore'])
def test_unknown_entity_fallback_and_bad_nbt_reported(loader,fallback):
    region = Region('test',(0,0,0),(1,1,1),[],{},entities=[
        {'id':'mod:unknown','Pos':[1,2,3]}, {'id':'minecraft:pig','Pos':[float('nan'),0,0]},
        {'id':'minecraft:marker','Pos':[0,0,0]}])
    mesh=CompactVisualMesh()
    report=append_scene_entities(Schematic(6,4903,regions={'test':region}),{'test'},(0,0,0),mesh,loader,
        SimpleNamespace(fallback=fallback),lambda *a:None,lambda:False)
    assert report['errors']==2 and report['invisible']==1
    assert report['fallback_cubes']==(1 if fallback=='cube' else 0)
    assert report['ignored']==(1 if fallback=='cube' else 2)
    assert len(report['events'])==2


def test_cancellation(loader):
    r=Region('test',(0,0,0),(1,1,1),[],{},entities=[{'id':'minecraft:pig','Pos':[0,0,0]}])
    with pytest.raises(ConversionCancelled):
        append_scene_entities(Schematic(6,4903,regions={'test':r}),{'test'},(0,0,0),CompactVisualMesh(),loader,
            SimpleNamespace(fallback='ignore'),lambda *a:None,lambda:True)


@pytest.mark.parametrize('mode,fmt',[('print','stl'),('visual','obj')])
def test_disabled_entities_leave_block_output_byte_identical(tmp_path,monkeypatch,mode,fmt):
    from litmetica3d import conversion, entity_assets
    from litmetica3d.litematic import BlockState
    def forbidden(*args,**kwargs):
        raise AssertionError('Disabled feature must not load entity assets')
    monkeypatch.setattr(entity_assets,'EntityAssets',forbidden)
    region=Region('r',(12,3,-8),(1,1,1),[BlockState('minecraft:stone')],{(0,0,0):0})
    schematic=Schematic(6,4903,regions={'r':region})
    monkeypatch.setattr(conversion,'load_schematic',lambda path:schematic)
    options=ConversionOptions(tmp_path/'input.litematic',tmp_path/('output.'+fmt),geometry=mode,output_format=fmt,blender_lights='none')
    first=conversion.convert(options)
    raw=options.output_path.read_bytes()
    region.entities=[{'id':'minecraft:pig','Pos':[123,45,67]}]
    second=conversion.convert(options)
    assert options.output_path.read_bytes()==raw
    assert first.triangles==second.triangles and first.vertices==second.vertices
    assert not second.scene_entities
