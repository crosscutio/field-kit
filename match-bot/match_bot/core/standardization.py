"""
Text normalization utilities for Match-Bot.

Provides case normalization, accent removal, whitespace cleanup, and
name-mapping application for standardizing place names before matching.
"""

import re
import unicodedata
from typing import Dict, Optional

import pandas as pd


def normalize_text(
    text: str,
    case: Optional[str] = 'upper',
    strip_whitespace: bool = True,
    collapse_whitespace: bool = True,
    strip_punctuation: bool = False,
) -> str:
    """Apply standard text normalization transformations."""
    if pd.isna(text):
        return text

    result = str(text)

    if strip_whitespace:
        result = result.strip()

    if collapse_whitespace:
        result = re.sub(r'\s+', ' ', result)

    if strip_punctuation:
        result = re.sub(r"['\-,.]", '', result)

    if case == 'upper':
        result = result.upper()
    elif case == 'lower':
        result = result.lower()
    elif case == 'title':
        result = result.title()

    return result


def remove_accents(text: str) -> str:
    """Remove diacritical marks (accents) from characters."""
    if pd.isna(text):
        return text
    normalized = unicodedata.normalize('NFD', str(text))
    return ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')


def normalize_series(
    series: pd.Series,
    case: Optional[str] = 'upper',
    remove_accent: bool = False,
    strip_whitespace: bool = True,
    collapse_whitespace: bool = True,
) -> pd.Series:
    """Apply normalization to a pandas Series of strings."""
    result = series.copy()
    result = result.astype('string')

    if strip_whitespace:
        result = result.str.strip()

    if collapse_whitespace:
        result = result.str.replace(r'\s+', ' ', regex=True)

    if remove_accent:
        result = result.apply(lambda x: remove_accents(x) if pd.notna(x) else x)

    if case == 'upper':
        result = result.str.upper()
    elif case == 'lower':
        result = result.str.lower()
    elif case == 'title':
        result = result.str.title()

    return result


def apply_name_mappings(
    df: pd.DataFrame,
    column: str,
    mappings: Dict[str, str],
    filter_column: Optional[str] = None,
    filter_value: Optional[str] = None,
) -> pd.DataFrame:
    """Apply a dictionary of name corrections to a DataFrame column."""
    result = df.copy()

    for old_value, new_value in mappings.items():
        if filter_column is not None and filter_value is not None:
            mask = (result[column] == old_value) & (result[filter_column] == filter_value)
        else:
            mask = result[column] == old_value
        result.loc[mask, column] = new_value

    return result
