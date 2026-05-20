import pytest

from customize_daemonset import (
    InputValidationError,
    NodeInfo,
    Taint,
    Toleration,
    resolve_tolerations,
)


def _n(name, *taints):
    return NodeInfo(hostname=name, cores=8, taints=tuple(taints))


def test_explicit_tolerate_replaces_base():
    cp = Taint("control-plane", "", "NoSchedule")
    user_tol = Toleration("control-plane", "Exists", "", "NoSchedule")
    base = [{"key": "old", "operator": "Exists", "effect": "NoSchedule"}]
    final, usable, excluded = resolve_tolerations(
        nodes=[_n("a", cp), _n("b")],
        explicit_tolerations=[user_tol],
        base_tolerations=base,
        is_tty=False,
    )
    assert final == [{"key": "control-plane", "operator": "Exists", "effect": "NoSchedule"}]
    assert {n.hostname for n in usable} == {"a", "b"}
    assert excluded == []


def test_explicit_tolerate_excludes_unmatched_taints():
    cp = Taint("control-plane", "", "NoSchedule")
    gpu = Taint("dedicated", "gpu", "NoSchedule")
    user_tol = Toleration("control-plane", "Exists", "", "NoSchedule")
    final, usable, excluded = resolve_tolerations(
        nodes=[_n("a", cp), _n("b", cp, gpu), _n("c")],
        explicit_tolerations=[user_tol],
        base_tolerations=None,
        is_tty=False,
    )
    assert {n.hostname for n in usable} == {"a", "c"}
    assert excluded == ["b"]


def test_no_tolerate_no_taints_keeps_base():
    base = [{"key": "x", "operator": "Exists", "effect": "NoSchedule"}]
    final, usable, excluded = resolve_tolerations(
        nodes=[_n("a"), _n("b")],
        explicit_tolerations=[],
        base_tolerations=base,
        is_tty=False,
    )
    assert final == base
    assert {n.hostname for n in usable} == {"a", "b"}
    assert excluded == []


def test_no_tolerate_taints_non_tty_errors():
    cp = Taint("control-plane", "", "NoSchedule")
    with pytest.raises(InputValidationError) as exc_info:
        resolve_tolerations(
            nodes=[_n("a", cp)],
            explicit_tolerations=[],
            base_tolerations=None,
            is_tty=False,
        )
    assert "tainted nodes detected" in str(exc_info.value)
    assert "control-plane" in str(exc_info.value)
