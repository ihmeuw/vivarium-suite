from collections import defaultdict
from dataclasses import dataclass, field
from typing import DefaultDict

from .comparison import Comparison


@dataclass
class VerificationResults:
    """Stores passing and failing test results for Comparisons."""

    passing: DefaultDict[str, DefaultDict[str, Comparison]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Comparison))
    )
    failing: DefaultDict[str, DefaultDict[str, Comparison]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Comparison))
    )
