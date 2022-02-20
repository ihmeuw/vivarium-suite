import pytest

from vivarium_cluster_tools.psimulate.cluster import validate_cluster_environment
from vivarium_cluster_tools.psimulate.environment import ENV_VARIABLES


@pytest.fixture(params=["user@int-uge-archive-p006"])
def good_host(request):
    return request.param


@pytest.fixture(
    params=[
        "user@gen-uge-submit-p01",
        "-submit-",
        "user@gen-uge-submit-p02",
    ]
)
def bad_host(request):
    return request.param


def test_validate_cluster_environment_pass(monkeypatch, good_host):
    monkeypatch.setenv(ENV_VARIABLES.CLUSTER_NAME.name, "cluster")
    monkeypatch.setenv(ENV_VARIABLES.HOSTNAME.name, good_host)
    validate_cluster_environment()


def test_validate_cluster_environment_no_cluster(monkeypatch):
    with pytest.raises(RuntimeError, match="IHME cluster"):
        validate_cluster_environment()


def test_validate_cluster_environment_on_submit_host(monkeypatch, bad_host):
    monkeypatch.setenv(ENV_VARIABLES.CLUSTER_NAME.name, "cluster")
    monkeypatch.setenv(ENV_VARIABLES.HOSTNAME.name, bad_host)
    with pytest.raises(RuntimeError, match="submit host"):
        validate_cluster_environment()
