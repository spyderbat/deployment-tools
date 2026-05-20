import pytest

from customize_daemonset import (
    InputValidationError,
    format_cpu_millicores,
    format_memory_mi,
    parse_millicores,
)


@pytest.mark.parametrize("value,expected", [
    ("50m", 50),
    ("100m", 100),
    ("1", 1000),
    ("2", 2000),
    ("0.5", 500),
    ("0.25", 250),
])
def test_parse_millicores_accepts(value, expected):
    assert parse_millicores(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "50mm", "-1", "1.5m", " "])
def test_parse_millicores_rejects(value):
    with pytest.raises(InputValidationError):
        parse_millicores(value)


@pytest.mark.parametrize("millis,expected", [
    (50, "50m"),
    (1000, "1000m"),
    (2500, "2500m"),
])
def test_format_cpu_millicores(millis, expected):
    assert format_cpu_millicores(millis) == expected


@pytest.mark.parametrize("mi,expected", [
    (1024, "1024Mi"),
    (2048, "2048Mi"),
    (1104, "1104Mi"),
])
def test_format_memory_mi(mi, expected):
    assert format_memory_mi(mi) == expected
