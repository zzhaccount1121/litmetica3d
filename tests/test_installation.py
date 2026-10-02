"""Fast distribution-contract checks; full install probes are in install_smoke.py."""
import ast
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

from litmetica3d import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_has_a_literal_single_source():
    tree = ast.parse((ROOT / 'litmetica3d/__init__.py').read_text(encoding='utf-8'))
    values = [
        ast.literal_eval(node.value)
        for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == '__version__'
                for target in node.targets)
    ]
    assert values == [__version__]
    config = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    assert 'build-backend = "setuptools.build_meta"' in config
    assert 'dynamic = ["version"]' in config
    assert 'version = {attr = "litmetica3d.__version__"}' in config


def test_windows_portable_name_uses_package_version():
    with patch.object(sys, 'path', list(sys.path)):
        module = runpy.run_path(str(ROOT / 'scripts/package_winui.py'))
    assert module['VERSION'] == __version__
    assert module['NAME'] == f'Litematica3D-WinUI-v{__version__}-win-x64'


def test_current_build_entrypoint_delegates_to_winui():
    script = (ROOT / 'build_exe.ps1').read_text(encoding='utf-8')
    assert "@('scripts/package_winui.py', '--output', $outputPath)" in script
    assert '& $Python -m pytest -q' in script
    assert 'PyInstaller --' not in script
    assert 'litmetica3d-v0.5.2' not in script
    assert 'Compress-Archive' not in script


def test_native_engine_requirements_do_not_pull_qt():
    requirements = (ROOT / 'requirements-winui.txt').read_text(encoding='utf-8')
    assert 'PySide6' not in requirements
    for dependency in ('numpy', 'manifold3d', 'Pillow'):
        assert dependency in requirements


def test_native_ci_runs_the_real_engine_smoke():
    workflow = (ROOT / '.github/workflows/installation.yml').read_text(encoding='utf-8')
    native_job = workflow.split('  winui:\n', 1)[1]
    assert 'actions/setup-python@' in native_job
    assert 'python -m pip install -e .' in native_job
    assert 'write_fixture' in native_job
    assert 'dotnet run --project tests/WinUI.EngineSmoke/WinUI.EngineSmoke.csproj' in native_job
