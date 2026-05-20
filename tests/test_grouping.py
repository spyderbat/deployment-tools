import pytest

from customize_daemonset import (
    InputValidationError,
    NodeInfo,
    group_by_cores,
)


def _n(name, cores):
    return NodeInfo(hostname=name, cores=cores, taints=())


def test_group_single_bucket():
    out = group_by_cores([_n("a", 8), _n("b", 8)])
    assert out == {8: ["a", "b"]}


def test_group_multiple_buckets_sorted():
    out = group_by_cores([_n("c", 16), _n("a", 8), _n("b", 8), _n("d", 32)])
    assert list(out.keys()) == [8, 16, 32]
    assert out[8] == ["a", "b"]
    assert out[16] == ["c"]
    assert out[32] == ["d"]


def test_group_hostnames_sorted_within_bucket():
    out = group_by_cores([_n("zeta", 8), _n("alpha", 8), _n("mu", 8)])
    assert out[8] == ["alpha", "mu", "zeta"]


def test_group_empty_rejected():
    with pytest.raises(InputValidationError):
        group_by_cores([])
