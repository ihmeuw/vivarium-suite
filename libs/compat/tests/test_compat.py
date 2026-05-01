import sys

from vivarium._compat import _CompatFinder, install


def test_install_is_idempotent():
    install()
    install()
    finders = [f for f in sys.meta_path if isinstance(f, _CompatFinder)]
    assert len(finders) == 1


def test_finder_ignores_unknown_modules():
    finder = _CompatFinder()
    assert finder.find_spec("some_unknown_module", None) is None
