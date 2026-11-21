import pytest

@pytest.fixture(autouse=True)
def isolated_scan_cache(tmp_path, monkeypatch):
    monkeypatch.setattr('core.scan_cache.DATA', tmp_path / 'appdata')

    monkeypatch.setattr('core.scan_cache.ROOT', tmp_path / 'program')
