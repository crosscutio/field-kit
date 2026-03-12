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
    return {
        'project_name': form_data.get('project_name', 'Untitled'),
        'reference': {
            'file': form_data.get('ref_file', ''),
            'columns': {
                'id': form_data.get('ref_id_column', ''),
                'name': form_data.get('ref_name_column', ''),
                'hierarchy': form_data.get('ref_hierarchy', []),
            },
        },
        'target': {
            'file': form_data.get('target_file', ''),
            'columns': {
                'id': form_data.get('target_id_column', ''),
                'name': form_data.get('target_name_column', ''),
                'hierarchy': form_data.get('target_hierarchy', []),
            },
        },
        'standardization': {
            'case': form_data.get('case', 'lower'),
            'remove_accents': form_data.get('remove_accents', True),
        },
        'matching': {
            'levenshtein_distance_threshold': form_data.get('levenshtein_distance_threshold', 1),
            'levenshtein_score_threshold': form_data.get('levenshtein_score_threshold', 0.25),
            'validate_numbers': form_data.get('validate_numbers', True),
        },
        'paths': {
            'lookups_dir': form_data.get('lookups_dir', 'output/lookups'),
            'output_dir': form_data.get('output_dir', 'output'),
        },
    }


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
        'case': std.get('case', 'lower'),
        'remove_accents': std.get('remove_accents', True),
        'levenshtein_distance_threshold': match.get('levenshtein_distance_threshold', 1),
        'levenshtein_score_threshold': match.get('levenshtein_score_threshold', 0.25),
        'validate_numbers': match.get('validate_numbers', True),
        'lookups_dir': paths.get('lookups_dir', 'output/lookups'),
        'output_dir': paths.get('output_dir', 'output'),
    }
