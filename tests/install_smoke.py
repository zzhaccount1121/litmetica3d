"""Build/install a wheel and an editable checkout in separate clean environments.

Run with a Python 3.10+ interpreter with pip, for example:
    python tests/install_smoke.py --work-dir <directory-outside-the-checkout>
No build-isolation bypass or --no-deps install is used. GUI routing is mocked;
this is not an interactive Qt/WinUI or cross-platform GUI certification.
"""
import argparse
import ast
from email.parser import BytesParser
import gzip
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zipfile


PROBE = r'''
import json
from importlib import metadata
from pathlib import Path
import sys
from unittest.mock import patch
import numpy, PIL, manifold3d, PySide6
import litmetica3d
from litmetica3d import cli, desktop, gui_app
from litmetica3d.conversion import bundled_asset_path
from litmetica3d.model_loader import ModelLoader

source, expected, mode = Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]
package = Path(litmetica3d.__file__).resolve()
assert litmetica3d.__version__ == expected
assert metadata.version('litmetica3d') == expected
if mode == 'wheel':
    assert source not in package.parents, (source, package)
    assert Path(sys.prefix).resolve() in package.parents, package
else:
    assert package == source / 'litmetica3d' / '__init__.py', package
entries = {ep.name: ep for ep in metadata.distribution('litmetica3d').entry_points}
assert entries['litmetica3d'].value == 'litmetica3d.cli:main'
assert entries['litmetica3d-gui'].value == 'litmetica3d.desktop:launch_gui'
assert entries['litmetica3d'].load() is cli.main
assert entries['litmetica3d-gui'].load() is desktop.launch_gui
# An installed wheel has no native WinUI frontend. Simulate the fallback call
# only; importing the real GUI module also verifies the declared Qt dependency.
with patch('litmetica3d.desktop.Path.is_file', return_value=False):
    with patch.object(gui_app, 'launch_gui', return_value=27) as qt:
        assert entries['litmetica3d-gui'].load()() == 27
        assert cli.main(['--gui']) == 27
        assert qt.call_count == 2
asset_path = bundled_asset_path()
assert asset_path.exists()
assert package.parent in asset_path.resolve().parents
loader = ModelLoader(asset_path, visual_textures=True)
try:
    assert loader.block_count >= 1100, loader.block_count
    stone = loader.resolve('minecraft:stone', {}, (0, 0, 0))
    assert stone.status == 'ok' and stone.faces, stone
    count = loader.block_count
finally:
    loader.close()
print(json.dumps({'mode': mode, 'version': expected, 'package': str(package),
                  'assets': str(asset_path), 'blockstates': count,
                  'gui': 'Qt routing mocked; real Qt import passed'}, ensure_ascii=False))
'''


def package_version(source):
    tree = ast.parse((source / 'litmetica3d/__init__.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == '__version__'
                for target in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError('No literal package version found')


def write_fixture(path):
    """Minimal real gzip NBT Litematic with one stone, using two palette bits."""
    def string(value):
        data = value.encode('utf-8')
        return struct.pack('>H', len(data)) + data

    def tag(kind, name, data):
        return bytes([kind]) + string(name) + data

    def integer(name, value):
        return tag(3, name, struct.pack('>i', value))

    def compound(name, data):
        return tag(10, name, data + b'\0')

    region = compound('Position', b''.join(integer(axis, 0) for axis in 'xyz'))
    region += compound('Size', b''.join(integer(axis, 1) for axis in 'xyz'))
    palette = b''.join(tag(8, 'Name', string(block)) + b'\0'
                       for block in ('minecraft:air', 'minecraft:stone'))
    region += tag(9, 'BlockStatePalette', b'\x0a' + struct.pack('>i', 2) + palette)
    region += tag(12, 'BlockStates', struct.pack('>i', 1) + struct.pack('>q', 1))
    data = compound('', integer('Version', 6) + compound('Regions', compound('R', region)))
    path.write_bytes(gzip.compress(data))


def inspect_wheel(wheel, source, version):
    asset_root = source / 'litmetica3d/mc_assets/26.2'
    expected = {
        path.relative_to(source).as_posix() for path in asset_root.rglob('*')
        if path.is_file() and path.suffix in {'.json', '.png', '.mcmeta'}
    }
    if not expected:
        raise AssertionError('Source asset inventory is empty')
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = sorted(expected - names)
        if missing:
            raise AssertionError(f'Wheel missing {len(missing)} bundled assets: {missing[:10]}')
        metadata_names = [name for name in names if name.endswith('.dist-info/METADATA')]
        assert len(metadata_names) == 1, metadata_names
        info = BytesParser().parsebytes(archive.read(metadata_names[0]))
        assert info['Name'] == 'litmetica3d'
        assert info['Version'] == version
        assert info['Requires-Python'] == '>=3.10'
        assert any(dep.startswith('PySide6') for dep in info.get_all('Requires-Dist', []))
        forbidden = ('tests/', 'frontend/', 'scripts/', 'work/', 'build/')
        assert not any(name.startswith(forbidden) for name in names)
    return {'wheel': wheel.name, 'version': version, 'bundled_assets': len(expected)}


def check(source, work):
    source, work = source.resolve(), work.resolve()
    if source == work or source in work.parents:
        raise ValueError('--work-dir must be outside the source checkout')
    work.mkdir(parents=True, exist_ok=True)
    for name in ('wheel-venv', 'editable-venv', 'wheels'):
        if (work / name).exists():
            raise FileExistsError(f'Refusing to overwrite prior installation evidence: {work / name}')
    logs = work / 'logs'
    logs.mkdir(exist_ok=True)
    outside = work / 'outside-source'
    outside.mkdir(exist_ok=True)
    wheels = work / 'wheels'
    wheels.mkdir()
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               QT_QPA_PLATFORM='offscreen', PIP_DISABLE_PIP_VERSION_CHECK='1')

    def run(label, *args):
        shown = ['<Python probe>' if arg == PROBE else str(arg) for arg in args[:6]]
        print(f'[{label}] {" ".join(shown)}', flush=True)
        result = subprocess.run([str(arg) for arg in args], cwd=outside, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding='utf-8', errors='replace', timeout=1200)
        (logs / f'{label}.txt').write_text(result.stdout, encoding='utf-8')
        if result.returncode:
            print(result.stdout, flush=True)
            raise RuntimeError(f'{label} failed with exit code {result.returncode}; see {logs}')
        print(result.stdout, end='', flush=True)
        return result.stdout

    version = package_version(source)
    # pip's default PEP 517 isolation is intentional: this caught Issue #2's
    # invalid backend even when the development interpreter had setuptools.
    run('build-wheel', sys.executable, '-m', 'pip', 'wheel', '--no-deps',
        '--wheel-dir', wheels, source)
    matches = list(wheels.glob('litmetica3d-*.whl'))
    assert len(matches) == 1, matches
    wheel = matches[0]
    summary = {'host_python': sys.version, 'platform': sys.platform,
               'wheel': inspect_wheel(wheel, source, version), 'installations': []}
    (logs / 'wheel-inventory.json').write_text(
        json.dumps(summary['wheel'], indent=2), encoding='utf-8')
    fixture = outside / 'one-stone.litematic'
    write_fixture(fixture)

    for mode in ('wheel', 'editable'):
        environment = work / f'{mode}-venv'
        run(f'{mode}-venv', sys.executable, '-m', 'venv', environment)
        binaries = environment / ('Scripts' if os.name == 'nt' else 'bin')
        python = binaries / ('python.exe' if os.name == 'nt' else 'python')
        command = binaries / ('litmetica3d.exe' if os.name == 'nt' else 'litmetica3d')
        install_args = ['-m', 'pip', 'install']
        install_args.extend([str(wheel)] if mode == 'wheel' else ['-e', str(source)])
        run(f'{mode}-install', python, *install_args)
        run(f'{mode}-pip-check', python, '-m', 'pip', 'check')
        # -I suppresses user-site and environment imports. The working directory
        # is deliberately not the source checkout for every probe and CLI call.
        probe = run(f'{mode}-imports-resources-gui', python, '-I', '-c', PROBE,
                    source, version, mode)
        summary['installations'].append(json.loads(probe))
        expected_version = f'litmetica3d {version}'
        assert run(f'{mode}-module-version', python, '-I', '-m', 'litmetica3d',
                   '--version').strip() == expected_version
        assert run(f'{mode}-console-version', command, '--version').strip() == expected_version
        help_text = run(f'{mode}-console-help', command, '--help')
        assert '--gui' in help_text and '--scale' in help_text
        target = outside / f'{mode}-stone.stl'
        run(f'{mode}-console-conversion', command, fixture, target)
        outputs = list(outside.rglob(f'{mode}-stone.stl'))
        assert len(outputs) == 1 and outputs[0].stat().st_size > 84, outputs
    (logs / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                       encoding='utf-8')
    print(f'PASS: isolated wheel + clean wheel/editable installs; evidence: {logs}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--work-dir', type=Path,
                        default=None, help='Evidence directory outside the source checkout')
    args = parser.parse_args()
    directory = args.work_dir or Path(tempfile.mkdtemp(prefix='litmetica3d-install-'))
    check(args.source, directory)
