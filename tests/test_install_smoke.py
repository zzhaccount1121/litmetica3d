"""Regression coverage for release spelling versus PEP 440 wheel metadata."""
from pathlib import Path
import runpy
import zipfile

import pytest

SMOKE = runpy.run_path(str(Path(__file__).with_name('install_smoke.py')))


def make_wheel(tmp_path, metadata_version, *, omit_layers=False):
    source = tmp_path / 'source'
    asset = 'litmetica3d/mc_assets/26.2/assets/litmetica3d/entities/layers.json.gz'
    file = source / asset
    file.parent.mkdir(parents=True)
    file.write_bytes(b'fixture')
    wheel = tmp_path / 'fixture.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        if not omit_layers:
            archive.write(file, asset)
        archive.writestr('litmetica3d.dist-info/METADATA',
                         'Name: litmetica3d\n'
                         f'Version: {metadata_version}\n'
                         'Requires-Python: >=3.10\n'
                         'Requires-Dist: PySide6>=6.8,<6.9\n')
    return source, wheel


@pytest.mark.parametrize(('source_version', 'metadata_version'), [
    ('0.6.4', '0.6.4'),
    ('0.7.0-beta.1', '0.7.0b1'),
    ('0.7.0-alpha.2', '0.7.0a2'),
    ('0.7.0-rc.1', '0.7.0rc1'),
    ('0.7.0-dev.1', '0.7.0.dev1'),
    ('0.7.0-post.1', '0.7.0.post1'),
])
def test_wheel_accepts_normalized_release_versions(tmp_path, source_version, metadata_version):
    source, wheel = make_wheel(tmp_path, metadata_version)
    result = SMOKE['inspect_wheel'](wheel, source, source_version)
    assert result['version'] == source_version
    assert result['metadata_version'] == metadata_version
    assert result['bundled_assets'] == 1


@pytest.mark.parametrize('wrong_version', ['0.7.0b2', '0.7.0', '0.6.4'])
def test_wheel_rejects_a_different_release(tmp_path, wrong_version):
    source, wheel = make_wheel(tmp_path, wrong_version)
    with pytest.raises(AssertionError):
        SMOKE['inspect_wheel'](wheel, source, '0.7.0-beta.1')


def test_wheel_rejects_missing_compressed_entity_layers(tmp_path):
    source, wheel = make_wheel(tmp_path, '0.7.0b1', omit_layers=True)
    with pytest.raises(AssertionError, match='Wheel missing 1 bundled assets'):
        SMOKE['inspect_wheel'](wheel, source, '0.7.0-beta.1')
