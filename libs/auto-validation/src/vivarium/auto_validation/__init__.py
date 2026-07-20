from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-auto-validation")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

from vivarium.auto_validation.interface import ValidationContext
