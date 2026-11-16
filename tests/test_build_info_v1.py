from app.build_info import code_build_id


def test_build_tracks_loaded_code_and_catalog_not_user_data(tmp_path):
    (tmp_path / 'main.py').write_text('version = 1', encoding='utf-8')
    original = code_build_id(tmp_path)
    assert code_build_id(tmp_path) == original
    (tmp_path / 'local.sqlite3').write_bytes(b'private test fixture')
    (tmp_path / 'model-key.dpapi').write_bytes(b'encrypted test fixture')
    (tmp_path / 'settings.local.json').write_text('{"secret":"synthetic only"}', encoding='utf-8')
    assert code_build_id(tmp_path) == original
    (tmp_path / 'main.py').write_text('version = 2', encoding='utf-8')
    assert code_build_id(tmp_path) != original
    current = code_build_id(tmp_path)
    (tmp_path / 'ncss_regions.json').write_text('{"catalog":"public test fixture"}', encoding='utf-8')
    assert code_build_id(tmp_path) != current
