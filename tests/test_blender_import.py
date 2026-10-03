import pathlib
from litmetica3d.visual_mesh import _write_blender_script

def test_generated_import_is_batched_and_cancellable(tmp_path):
    target=tmp_path/'setup.py'
    _write_blender_script(target,{'materials':[],'lights':[]},tmp_path)
    script=target.read_text(encoding='utf-8')
    compile(script,str(target),'exec')
    assert 'def setup_steps():' in script
    assert 'range(32)' in script
    assert 'time.perf_counter() + 0.02' in script
    assert 'modal_handler_add' in script
    assert 'event.type == "ESC"' in script
    assert 'event.timer' not in script
    assert 'progress_update(completed)' in script
    assert 'attach_pending_collection()' in script
    assert 'shading.type = previous' in script
    assert 'flush=True' in script
    assert '@lru_cache(maxsize=1)' in script
    assert 'for input_socket in (glare.inputs' in script

