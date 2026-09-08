"""Shared fixtures: a tiny two-level project with deliberate typos.

Target (communities, need coordinates) vs reference (places, have lat/lon).
Regions: target "Centr" is a typo of reference "Centre"; districts: target
"Gama" is a typo of "Gamma". Leaf names carry one exact match, fuzzy
near-misses, and a number-only difference.
"""
import contextlib
import io

import pandas as pd
import pytest

from match_bot.core.config import MatcherConfig

TARGET_ROWS = [
    # id, name, region, district
    ('T1', 'Kola', 'Nord', 'Alpha'),
    ('T2', 'Kolo 2', 'Nord', 'Alpha'),
    ('T3', 'Zed', 'Nord', 'Alpha'),
    ('T4', 'Mira', 'Nord', 'Beta'),
    ('T5', 'Tavi', 'Sud', 'Gama'),
    ('T6', 'Foo', 'Centr', 'Eps'),
    ('T7', 'Nowhere', 'Sud', 'Gama'),
]

REF_ROWS = [
    # id, name, region, district, lat, lon
    ('R1', 'Kola', 'Nord', 'Alpha', 1.0, 10.0),
    ('R2', 'Kolo', 'Nord', 'Alpha', 1.1, 10.1),
    ('R3', 'Zedd', 'Nord', 'Alpha', 1.2, 10.2),
    ('R4', 'Mira', 'Nord', 'Beta', 2.0, 11.0),
    ('R5', 'Mirra', 'Nord', 'Beta', 2.1, 11.1),
    ('R6', 'Tavy', 'Sud', 'Gamma', 3.0, 12.0),
    ('R7', 'Foo', 'Centre', 'Eps', 4.0, 13.0),
    ('R8', 'Q', 'Sud', 'Delta', 3.5, 12.5),
    ('R9', 'Kola', 'Ouest', 'Omega', 5.0, 14.0),
]


def write_project(root):
    root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(TARGET_ROWS, columns=['cid', 'community', 'region_name', 'district_name']) \
        .to_csv(root / 'target.csv', index=False)
    pd.DataFrame(REF_ROWS, columns=['pid', 'place', 'adm1', 'adm2', 'lat', 'lon']) \
        .to_csv(root / 'ref.csv', index=False)
    raw = {
        'project_name': 'fixture',
        'reference': {'file': 'ref.csv', 'columns': {
            'id': 'pid', 'name': 'place', 'latitude': 'lat', 'longitude': 'lon',
            'hierarchy': [{'column': 'adm1', 'label': 'region'},
                          {'column': 'adm2', 'label': 'district'}]}},
        'target': {'file': 'target.csv', 'columns': {
            'id': 'cid', 'name': 'community',
            'hierarchy': [{'column': 'region_name', 'label': 'region'},
                          {'column': 'district_name', 'label': 'district'}]}},
        'standardization': {'case': 'lower', 'remove_accents': True},
        'matching': {'levenshtein_distance_threshold': 1,
                     'levenshtein_score_threshold': 0.25,
                     'validate_numbers': True},
        'paths': {'lookups_dir': 'output/lookups', 'output_dir': 'output'},
    }
    cfg = MatcherConfig._from_dict(raw)
    cfg._config_dir = root
    cfg.validate()
    return cfg


def run_lookups(config):
    from match_bot.scripts.generate_lookups import run
    with contextlib.redirect_stdout(io.StringIO()):
        run(config)


@pytest.fixture
def project(tmp_path):
    """Config for a fresh fixture project (no lookups generated yet)."""
    return write_project(tmp_path / 'proj')


@pytest.fixture
def project_with_lookups(project):
    run_lookups(project)
    return project
