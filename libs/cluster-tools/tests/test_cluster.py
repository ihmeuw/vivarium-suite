import pytest

from vivarium_cluster_tools.psimulate.cluster import exit_if_on_submit_host, ENV_VARIABLES


@pytest.fixture(params=["user@int-uge-archive-p006"])
def good_host(request):
    return request.param


@pytest.fixture(params=["user@gen-uge-submit-p01", "-submit-", "user@gen-uge-submit-p02",])
def bad_host(request):
    return request.param


def test_exit_if_on_submit_host_pass(monkeypatch, good_host):
    monkeypatch.setenv(ENV_VARIABLES.HOSTNAME.name, good_host)
    exit_if_on_submit_host()


def test_exit_if_on_submit_host_fail(monkeypatch, bad_host):
    monkeypatch.setenv(ENV_VARIABLES.HOSTNAME.name, bad_host)
    with pytest.raises(RuntimeError):
        exit_if_on_submit_host()
