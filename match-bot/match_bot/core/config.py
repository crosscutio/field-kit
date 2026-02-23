"""
Configuration loading and validation for Match-Bot.

Defines the YAML config schema: reference/target datasets with N-level hierarchy,
standardization rules, matching thresholds, and output paths.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class HierarchyLevel:
    """A single level in the administrative hierarchy."""
    column: str
    label: str


@dataclass
class DatasetConfig:
    """Configuration for a reference or target dataset."""
    file: str
    columns: Dict[str, Any]  # id, name, hierarchy
    format: Optional[str] = None
    layer: Optional[str] = None

    @property
    def id_column(self) -> str:
        return self.columns.get('id', '')

    @property
    def name_column(self) -> str:
        return self.columns.get('name', '')

    @property
    def hierarchy(self) -> List[HierarchyLevel]:
        raw = self.columns.get('hierarchy', [])
        return [HierarchyLevel(column=h['column'], label=h['label']) for h in raw]

    @property
    def hierarchy_labels(self) -> List[str]:
        return [h.label for h in self.hierarchy]

    @property
    def hierarchy_columns(self) -> List[str]:
        return [h.column for h in self.hierarchy]


@dataclass
class StandardizationConfig:
    """Text standardization settings."""
    case: Optional[str] = 'lower'  # upper, lower, title, or None
    remove_accents: bool = True


@dataclass
class MatchingConfig:
    """Matching algorithm thresholds."""
    levenshtein_distance_threshold: int = 1
    levenshtein_score_threshold: float = 0.25
    validate_numbers: bool = True


@dataclass
class PathsConfig:
    """Output path configuration."""
    lookups_dir: str = 'output/lookups'
    output_dir: str = 'output'


@dataclass
class MatcherConfig:
    """Top-level configuration for a matching project."""
    project_name: str
    reference: DatasetConfig
    target: DatasetConfig
    standardization: StandardizationConfig = field(default_factory=StandardizationConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    _config_dir: Optional[Path] = field(default=None, repr=False)

    @property
    def hierarchy_labels(self) -> List[str]:
        """Ordered hierarchy labels (shared between reference and target)."""
        return self.reference.hierarchy_labels

    @property
    def lookups_dir(self) -> Path:
        """Resolved lookups directory path."""
        p = Path(self.paths.lookups_dir)
        if not p.is_absolute() and self._config_dir:
            p = self._config_dir / p
        return p

    @property
    def output_dir(self) -> Path:
        """Resolved output directory path."""
        p = Path(self.paths.output_dir)
        if not p.is_absolute() and self._config_dir:
            p = self._config_dir / p
        return p

    def resolve_file(self, file_path: str) -> Path:
        """Resolve a file path relative to the config directory."""
        p = Path(file_path)
        if not p.is_absolute() and self._config_dir:
            p = self._config_dir / p
        return p

    def validate(self):
        """Validate the configuration.

        Raises:
            ValueError: If configuration is invalid.
        """
        # Check hierarchy labels match between reference and target
        ref_labels = self.reference.hierarchy_labels
        target_labels = self.target.hierarchy_labels
        if ref_labels != target_labels:
            raise ValueError(
                f"Hierarchy labels must match between reference and target.\n"
                f"  Reference: {ref_labels}\n"
                f"  Target:    {target_labels}"
            )

        # Check required columns
        if not self.reference.id_column:
            raise ValueError("Reference dataset must specify columns.id")
        if not self.reference.name_column:
            raise ValueError("Reference dataset must specify columns.name")
        if not self.target.id_column:
            raise ValueError("Target dataset must specify columns.id")
        if not self.target.name_column:
            raise ValueError("Target dataset must specify columns.name")

        # Check files specified
        if not self.reference.file:
            raise ValueError("Reference dataset must specify a file")
        if not self.target.file:
            raise ValueError("Target dataset must specify a file")

    @classmethod
    def from_yaml(cls, path: str) -> 'MatcherConfig':
        """Load config from a YAML file.

        Args:
            path: Path to the YAML config file.

        Returns:
            Validated MatcherConfig instance.
        """
        import yaml

        config_path = Path(path)
        with open(config_path, 'r', encoding='utf-8') as f:
            raw = yaml.safe_load(f)

        config = cls._from_dict(raw)
        config._config_dir = config_path.parent
        config.validate()
        return config

    @classmethod
    def _from_dict(cls, raw: Dict[str, Any]) -> 'MatcherConfig':
        """Build config from a parsed dictionary."""
        ref_raw = raw.get('reference', {})
        target_raw = raw.get('target', {})
        std_raw = raw.get('standardization', {})
        match_raw = raw.get('matching', {})
        paths_raw = raw.get('paths', {})

        reference = DatasetConfig(
            file=ref_raw.get('file', ''),
            format=ref_raw.get('format'),
            layer=ref_raw.get('layer'),
            columns=ref_raw.get('columns', {}),
        )

        target = DatasetConfig(
            file=target_raw.get('file', ''),
            format=target_raw.get('format'),
            layer=target_raw.get('layer'),
            columns=target_raw.get('columns', {}),
        )

        standardization = StandardizationConfig(
            case=std_raw.get('case', 'lower'),
            remove_accents=std_raw.get('remove_accents', True),
        )

        matching = MatchingConfig(
            levenshtein_distance_threshold=match_raw.get('levenshtein_distance_threshold', 1),
            levenshtein_score_threshold=match_raw.get('levenshtein_score_threshold', 0.25),
            validate_numbers=match_raw.get('validate_numbers', True),
        )

        paths = PathsConfig(
            lookups_dir=paths_raw.get('lookups_dir', 'output/lookups'),
            output_dir=paths_raw.get('output_dir', 'output'),
        )

        return cls(
            project_name=raw.get('project_name', 'Untitled'),
            reference=reference,
            target=target,
            standardization=standardization,
            matching=matching,
            paths=paths,
        )
