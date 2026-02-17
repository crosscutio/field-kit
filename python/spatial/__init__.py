"""Spatial partitioning utilities for geospatial health campaign planning."""

from .contiguity import repair_noncontiguous_parts
from .geometry import fix_geometry, flatten_column

__all__ = ["repair_noncontiguous_parts", "fix_geometry", "flatten_column"]
