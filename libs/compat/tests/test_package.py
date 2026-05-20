"""Package-level smoke tests."""

from importlib.metadata import PackageNotFoundError, version


def test_version_is_resolvable():
    """``importlib.metadata`` finds the installed distribution and the version
    is PEP 440 parseable. Guards against a misspelled distribution name in
    the build silently breaking ``pip show vivarium-compat``.
    """
    from packaging.version import Version

    try:
        v = version("vivarium-compat")
    except PackageNotFoundError:
        v = "0.0.0+unknown"

    assert v != "0.0.0+unknown"
    Version(v)


def test_public_api_reachable():
    """The hook installer is importable. ``vivarium_compat.pth`` calls
    ``install_compat_finder()`` at interpreter startup, so a regression that
    removes or renames it would break every Python process in an env with
    this package installed.
    """
    from vivarium_compat._compat import install_compat_finder

    assert callable(install_compat_finder)
