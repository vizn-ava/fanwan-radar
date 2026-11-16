"""Exercise actual shipped adapter resources, not a mocked source tree."""
from pathlib import Path

from app.collector_worker import upstream_components


def test_shipped_mit_adapter_and_city_catalog_load_without_git_checkout():
    root = Path(__file__).resolve().parents[1]
    upstream = upstream_components()
    assert callable(upstream.map_api_job)
    assert callable(upstream.extract_detail_fields)
    assert isinstance(upstream.EXTRACT_DETAIL_JS, str) and upstream.EXTRACT_DETAIL_JS.strip()
    cities, _ = upstream.load_local_city_map()
    assert cities.get('郑州') == '101180100'
    assert (root / 'third_party/boss-zhipin-scraper/LICENSE').is_file()
