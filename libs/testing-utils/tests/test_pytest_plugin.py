"""Regression tests for the pytest plugin."""

import pytest

pytest_plugins = ["pytester"]


def test_test_in_slow_directory_not_skipped(pytester: pytest.Pytester) -> None:
    """Regression test: a test located in a 'slow/' directory should NOT be
    skipped just because 'slow' appears in its keywords (path components).
    Only tests explicitly marked @pytest.mark.slow should be skipped."""
    pytester.mkdir("slow")
    pytester.makepyfile(
        **{
            "slow/test_example.py": """
def test_not_actually_slow(request):
    assert "slow" in request.keywords
"""
        }
    )
    result = pytester.runpytest("-v")
    result.stdout.fnmatch_lines(["*test_not_actually_slow PASSED*"])
    assert result.ret == 0
