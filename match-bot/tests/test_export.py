import pandas as pd

from match_bot.core import export, links


def test_geocoded_export_joins_coordinates_and_pins(project_with_lookups):
    cfg = project_with_lookups
    links.link(cfg, 'leaf', 'T5', 'R6', rationale='manual: picked score=75')
    pins = pd.DataFrame([{'target_id': 'T7', 'latitude': 9.9, 'longitude': 19.9}])
    df = export.write_geocoded(cfg, pins=pins)
    out = cfg.output_dir / 'geocoded.csv'
    assert out.exists()
    df = df.set_index('cid')
    assert list(df.columns[:3]) == ['community', 'region_name', 'district_name']
    assert df.loc['T1', 'linked_place_id'] == 'R1' and float(df.loc['T1', 'latitude']) == 1.0
    assert df.loc['T1', 'set_by'] == 'auto' and float(df.loc['T1', 'score']) == 100
    assert df.loc['T5', 'set_by'] == 'manual' and float(df.loc['T5', 'longitude']) == 12.0
    assert df.loc['T7', 'set_by'] == 'manual · pin' and float(df.loc['T7', 'latitude']) == 9.9
    assert df.loc['T7', 'linked_place_name'] == 'dropped pin'
    assert df.loc['T2', 'set_by'] == 'pending' and df.loc['T2', 'latitude'] == ''
    assert len(df) == 7
