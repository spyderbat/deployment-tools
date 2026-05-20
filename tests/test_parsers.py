import pytest

from customize_daemonset import (
    InputValidationError,
    Toleration,
    parse_cpu_per_core_arg,
    parse_tolerate_arg,
)


def test_parse_cpu_per_core_arg_defaults_supported():
    assert parse_cpu_per_core_arg("50m") == 50
    assert parse_cpu_per_core_arg("100m") == 100
    assert parse_cpu_per_core_arg("1") == 1000


def test_parse_cpu_per_core_arg_rejects_garbage():
    with pytest.raises(InputValidationError):
        parse_cpu_per_core_arg("abc")


def test_parse_tolerate_arg_equal():
    t = parse_tolerate_arg("dedicated=gpu:NoSchedule")
    assert t == Toleration(key="dedicated", operator="Equal", value="gpu", effect="NoSchedule")


def test_parse_tolerate_arg_exists():
    t = parse_tolerate_arg("node.kubernetes.io/disk-pressure:NoSchedule")
    assert t == Toleration(
        key="node.kubernetes.io/disk-pressure",
        operator="Exists",
        value="",
        effect="NoSchedule",
    )


@pytest.mark.parametrize("bad", [
    "",
    "key=value",                 # no effect
    "key=value:BadEffect",       # invalid effect
    ":NoSchedule",               # no key
    "key=:NoSchedule",           # empty value with = -> ambiguous, reject
    "key:value:NoSchedule",      # too many colons
    "key=value=other:NoSchedule",  # too many equals
])
def test_parse_tolerate_arg_rejects(bad):
    with pytest.raises(InputValidationError):
        parse_tolerate_arg(bad)
