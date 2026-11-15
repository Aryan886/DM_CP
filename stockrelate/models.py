"""Small shared data contracts (Section 5.4 of the project plan)."""

from dataclasses import dataclass, field

Symbols = tuple[str, ...]
BoolMatrix = list[list[bool]]
FloatMatrix = list[list[float]]
Pair = tuple[str, str]


@dataclass(frozen=True)
class ValidationSummary:
    metadata_rows: int = 0
    price_rows: int = 0
    date_count: int = 0
    candidate_return_rows: int = 0
    retained_return_rows: int = 0
    removed_return_dates: tuple[str, ...] = ()
    removed_return_count: int = 0
    missing_price_counts: dict[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    normalization_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedData:
    data_id: str
    source_mode: str  # historical | synthetic_prices | teaching_matrix | upload
    symbols: Symbols
    correlation: FloatMatrix
    sample_count: int | None
    return_dates: tuple[str, ...]
    requested_start: str | None
    requested_end: str | None
    validation_summary: ValidationSummary
    provenance: dict
    names: dict[str, str] = field(default_factory=dict)
    sectors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RelationResult:
    threshold: float
    symbols: Symbols
    matrix: BoolMatrix
    ordered_pairs: list[Pair]
    edges: list[Pair]
    neighbors: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class PropertyResult:
    holds: bool
    witness: tuple[str, ...] | None


@dataclass(frozen=True)
class PropertyReport:
    reflexive: PropertyResult
    symmetric: PropertyResult
    transitive: PropertyResult

    @property
    def is_equivalence(self) -> bool:
        return self.reflexive.holds and self.symmetric.holds and self.transitive.holds


@dataclass(frozen=True)
class AnalysisResult:
    analysis_id: str
    data_id: str
    relation: RelationResult
    direct_properties: PropertyReport
    reachability_matrix: BoolMatrix
    reachability_properties: PropertyReport
    components: list[list[str]]
    metrics: dict
    closure_added_pairs: list[Pair]

    @property
    def symbols(self) -> Symbols:
        return self.relation.symbols

    def component_of(self, symbol: str) -> str:
        """Return the representative (smallest member) of the symbol's class."""
        for component in self.components:
            if symbol in component:
                return component[0]
        raise ValueError(f"Unknown stock identifier {symbol!r}.")
