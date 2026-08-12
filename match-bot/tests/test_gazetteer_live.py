"""Live integration test against real gazetteer sources (Eswatini).

Network-dependent; skipped by default. Run with:
    pytest tests/test_gazetteer_live.py -m network
Uses a tmp cache dir so the shared gazetteer/data/ tree is untouched.
"""
import pytest

from match_bot import gazetteer as gz

pytestmark = pytest.mark.network


def test_swz_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv('MATCH_BOT_GAZETTEER_DIR', str(tmp_path))

    gz.ensure_boundaries('SWZ')
    names = gz.admin1_names('SWZ')
    assert len(names) == 4  # Hhohho, Lubombo, Manzini, Shiselweni

    counts = gz.count_geonames_in_selection('SWZ', [names[0]])
    assert counts['geonames_count'] > 10

    shapes = gz.admin1_shapes('SWZ', [names[0]])
    gz.fetch_overpass_admin('SWZ', names[0], shapes[names[0]])

    df = gz.assemble_gazetteer('SWZ', [names[0]])
    assert len(df) > 100
    assert set(df['source']) <= {'geonames', 'osm'}
    assert (df['admin1'] == names[0]).all()
