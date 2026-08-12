"""Bridge between web form data and MatcherConfig / YAML."""

from pathlib import Path
from typing import Any, Dict

import yaml

from match_bot.core.config import MatcherConfig


def build_config(form_data: Dict[str, Any], upload_dir: Path) -> MatcherConfig:
    """Construct a MatcherConfig from web form data.

    Args:
        form_data: Dictionary of form values from the web form.
        upload_dir: Directory where uploaded files are stored (used as config_dir).

    Returns:
        Validated MatcherConfig instance.
    """
    raw = form_data_to_yaml_dict(form_data)
    config = MatcherConfig._from_dict(raw)
    config._config_dir = upload_dir
    config.validate()
    return config


def form_data_to_yaml_dict(form_data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert web form data to a YAML-compatible dictionary."""
    target_columns = {
        'id': form_data.get('target_id_column', ''),
        'name': form_data.get('target_name_column', ''),
        'hierarchy': form_data.get('target_hierarchy', []),
    }
    # Geocoding workflow: point coordinate columns. Only written when set so
    # configs from the Matching tab stay byte-identical.
    if form_data.get('geo_lat_column'):
        target_columns['latitude'] = form_data['geo_lat_column']
    if form_data.get('geo_lon_column'):
        target_columns['longitude'] = form_data['geo_lon_column']
    target = {
        'file': form_data.get('target_file', ''),
        'columns': target_columns,
    }
    # Geocoding workflow: gazetteer geography selection, only written when set.
    if form_data.get('gaz_country'):
        target['gazetteer'] = {
            'country': form_data['gaz_country'],
            'admin1': form_data.get('gaz_admin1', []),
        }
    matching = {
        'levenshtein_distance_threshold': form_data.get('levenshtein_distance_threshold', 1),
        'levenshtein_score_threshold': form_data.get('levenshtein_score_threshold', 0.25),
        'validate_numbers': form_data.get('validate_numbers', True),
    }
    # Per-level fuzzy-suggest thresholds (0-100), keyed by level label with
    # 'leaf' for the name level. Only written when set so configs from the
    # Matching tab stay byte-identical.
    if form_data.get('level_thresholds'):
        matching['level_thresholds'] = form_data['level_thresholds']

    result = {
        'project_name': form_data.get('project_name', 'Untitled'),
        'reference': {
            'file': form_data.get('ref_file', ''),
            'columns': {
                'id': form_data.get('ref_id_column', ''),
                'name': form_data.get('ref_name_column', ''),
                'hierarchy': form_data.get('ref_hierarchy', []),
            },
        },
        'target': target,
        'standardization': {
            'case': form_data.get('case', 'lower'),
            'remove_accents': form_data.get('remove_accents', True),
        },
        'matching': matching,
        'paths': {
            'lookups_dir': form_data.get('lookups_dir', 'output/lookups'),
            'output_dir': form_data.get('output_dir', 'output'),
        },
    }
    # Hand-made crosswalk links ({level: [{target, ref, method}]}). Saved so
    # the next run over the same dataset starts with parent levels settled.
    if form_data.get('crosswalk'):
        result['crosswalk'] = form_data['crosswalk']
    return result


def form_data_to_yaml(form_data: Dict[str, Any]) -> str:
    """Serialize form state to a YAML string for download."""
    data = form_data_to_yaml_dict(form_data)
    return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


def yaml_to_form_data(yaml_str: str) -> Dict[str, Any]:
    """Parse a YAML string into a form-compatible dictionary."""
    raw = yaml.safe_load(yaml_str)
    if not isinstance(raw, dict):
        raise ValueError(f"Config must be a YAML mapping, got {type(raw).__name__}")

    ref = raw.get('reference', {})
    ref_cols = ref.get('columns', {})
    target = raw.get('target', {})
    target_cols = target.get('columns', {})
    std = raw.get('standardization', {})
    match = raw.get('matching', {})
    paths = raw.get('paths', {})

    return {
        'project_name': raw.get('project_name', 'Untitled'),
        'ref_file': ref.get('file', ''),
        'ref_id_column': ref_cols.get('id', ''),
        'ref_name_column': ref_cols.get('name', ''),
        'ref_hierarchy': ref_cols.get('hierarchy', []),
        'target_file': target.get('file', ''),
        'target_id_column': target_cols.get('id', ''),
        'target_name_column': target_cols.get('name', ''),
        'target_hierarchy': target_cols.get('hierarchy', []),
        'geo_lat_column': target_cols.get('latitude', ''),
        'geo_lon_column': target_cols.get('longitude', ''),
        'gaz_country': target.get('gazetteer', {}).get('country', ''),
        'gaz_admin1': target.get('gazetteer', {}).get('admin1', []),
        'case': std.get('case', 'lower'),
        'remove_accents': std.get('remove_accents', True),
        'levenshtein_distance_threshold': match.get('levenshtein_distance_threshold', 1),
        'levenshtein_score_threshold': match.get('levenshtein_score_threshold', 0.25),
        'validate_numbers': match.get('validate_numbers', True),
        'level_thresholds': match.get('level_thresholds', {}),
        'crosswalk': raw.get('crosswalk', {}),
        'lookups_dir': paths.get('lookups_dir', 'output/lookups'),
        'output_dir': paths.get('output_dir', 'output'),
    }
