"""
=================
Vivarium Artifact
=================

A data artifact is an archive on disk that packages all data relevant to a
particular simulation. This package provides the artifact model and the HDF5
storage backend.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-artifact")
except PackageNotFoundError:
    # Distinguishable from setuptools_scm's ``0.0.0+no-git-tag`` fallback so
    # the smoke tests can tell "dist not installed" from "no reachable tag".
    __version__ = "0.0.0+not-installed"

from vivarium.artifact.artifact import Artifact, ArtifactException
from vivarium.artifact.entity_key import EntityKey, is_entity_key
