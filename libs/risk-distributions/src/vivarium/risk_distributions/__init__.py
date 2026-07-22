"""vivarium.risk_distributions

Risk distributions for use with the ``vivarium`` simulation framework.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-risk-distributions")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

from vivarium.risk_distributions.risk_distributions import (
    EnsembleDistribution,
    LogNormal,
    Normal,
)
