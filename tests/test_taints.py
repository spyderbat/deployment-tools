import pytest
from unittest.mock import MagicMock

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


def test_prompt_for_taints_maps_labels_back_to_taints(monkeypatch):
    """The taint prompt uses string labels (not Taint dataclasses) as Choice
    values, then maps the user's selection back to Taint instances via a
    local lookup. This regression-guards a real bug where InquirerPy 0.3.4
    returned Choice dicts (unhashable) instead of values for non-primitive
    Choice.value types."""
    from customize_daemonset import prompt_for_taints

    cp = Taint("node-role.kubernetes.io/control-plane", "", "NoSchedule")
    gpu = Taint("dedicated", "gpu", "NoSchedule")
    other = Taint("node", "gpu", "NoSchedule")
    taint_map = {cp: ["a", "b", "c"], gpu: ["d", "e"], other: ["f"]}

    # The expected labels (matching the format used inside prompt_for_taints)
    label_cp = "node-role.kubernetes.io/control-plane:NoSchedule  (3 nodes)"
    label_other = "node=gpu:NoSchedule  (1 nodes)"

    # Fake the inquirer.checkbox().execute() chain
    fake_prompt = MagicMock()
    fake_prompt.execute.return_value = [label_cp, label_other]
    fake_inquirer = MagicMock()
    fake_inquirer.checkbox.return_value = fake_prompt

    # Fake the Choice constructor so we don't need a real InquirerPy import path
    class FakeChoice:
        def __init__(self, value, name=None, enabled=False):
            self.value = value
            self.name = name

    monkeypatch.setattr(
        "customize_daemonset._import_inquirer",
        lambda: (fake_inquirer, FakeChoice, None),
    )

    selected = prompt_for_taints(taint_map)
    assert selected == {cp, other}
    # And gpu was NOT selected
    assert gpu not in selected


def test_prompt_for_taints_keyboard_interrupt(monkeypatch):
    """KeyboardInterrupt during the checkbox prompt must be converted to
    InteractiveCancelled."""
    from customize_daemonset import (
        InteractiveCancelled,
        prompt_for_taints,
    )

    fake_prompt = MagicMock()
    fake_prompt.execute.side_effect = KeyboardInterrupt()
    fake_inquirer = MagicMock()
    fake_inquirer.checkbox.return_value = fake_prompt

    class FakeChoice:
        def __init__(self, value, name=None, enabled=False):
            self.value = value
            self.name = name

    monkeypatch.setattr(
        "customize_daemonset._import_inquirer",
        lambda: (fake_inquirer, FakeChoice, None),
    )

    cp = Taint("k", "", "NoSchedule")
    with pytest.raises(InteractiveCancelled):
        prompt_for_taints({cp: ["a"]})
