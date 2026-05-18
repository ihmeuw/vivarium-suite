"""Risk Distributions: components for building distributions compatible with ``vivarium``."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-risk-distributions")
except PackageNotFoundError:
    __version__ = "unknown"

from vivarium.risk_distributions.risk_distributions import (
    EnsembleDistribution,
    LogNormal,
    Normal,
)
