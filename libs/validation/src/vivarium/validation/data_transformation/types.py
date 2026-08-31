"""Shared type aliases for data transformation."""
from typing import Literal

RateConversionType = Literal["linear", "exponential"]
"""How the simulation converted an annual rate into a per-time-step probability."""
