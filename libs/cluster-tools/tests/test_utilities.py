import pytest

from vivarium_cluster_tools.psimulate.utilities import exit_if_on_submit_host

@pytest.fixture
def hostname_init():
    return {
        'good': [
            'user@int-uge-archive-p006',
        ],
        'bad': [
            'user@gen-uge-submit-p01',
            '-submit-',
            'user@gen-uge-submit-p02',
        ]
    }
def test_exit_if_on_submit_host(hostname_init):
    for host in hostname_init['bad']:
        with pytest.raises(RuntimeError) as _:
            exit_if_on_submit_host(host)
    for host in hostname_init['good']:
        assert not exit_if_on_submit_host(host)
