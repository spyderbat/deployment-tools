from customize_daemonset import (
    NodeInfo,
    Taint,
    Toleration,
    distinct_taints,
    taint_matches_toleration,
)


def _n(name, *taints):
    return NodeInfo(hostname=name, cores=8, taints=tuple(taints))


def test_distinct_taints_dedupes():
    cp = Taint("node-role.kubernetes.io/control-plane", "", "NoSchedule")
    custom = Taint("dedicated", "gpu", "NoSchedule")
    nodes = [_n("a", cp), _n("b", cp), _n("c", cp, custom), _n("d")]
    result = distinct_taints(nodes)
    # Map from Taint -> sorted hostnames carrying it
    assert result[cp] == ["a", "b", "c"]
    assert result[custom] == ["c"]
    assert len(result) == 2


def test_distinct_taints_empty():
    assert distinct_taints([_n("a"), _n("b")]) == {}


def test_matches_equal_exact():
    t = Taint("k", "v", "NoSchedule")
    r = Toleration("k", "Equal", "v", "NoSchedule")
    assert taint_matches_toleration(t, r) is True


def test_matches_equal_wrong_value():
    t = Taint("k", "v", "NoSchedule")
    r = Toleration("k", "Equal", "other", "NoSchedule")
    assert taint_matches_toleration(t, r) is False


def test_matches_exists():
    t = Taint("k", "anything", "NoSchedule")
    r = Toleration("k", "Exists", "", "NoSchedule")
    assert taint_matches_toleration(t, r) is True


def test_matches_wrong_key():
    t = Taint("k1", "v", "NoSchedule")
    r = Toleration("k2", "Equal", "v", "NoSchedule")
    assert taint_matches_toleration(t, r) is False


def test_matches_wrong_effect():
    t = Taint("k", "v", "NoSchedule")
    r = Toleration("k", "Equal", "v", "NoExecute")
    assert taint_matches_toleration(t, r) is False


def test_matches_empty_effect_matches_all():
    t = Taint("k", "v", "NoExecute")
    r = Toleration("k", "Equal", "v", "")
    assert taint_matches_toleration(t, r) is True
