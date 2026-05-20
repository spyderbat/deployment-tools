import pytest

from customize_daemonset import (
    InputValidationError,
    compute_resources,
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


def test_compute_resources_small_node():
    # 8 cores, default 50m/core: req=400m, limit=max(2000, 400)=2000m
    # mem_req = 1024 + 80 = 1104Mi, mem_limit = 2208Mi
    r = compute_resources(cores=8, cpu_per_core_millis=50)
    assert r == {
        "requests": {"cpu": "400m", "memory": "1104Mi"},
        "limits": {"cpu": "2000m", "memory": "2208Mi"},
    }


def test_compute_resources_above_2000m_boundary():
    # 50 cores * 50m = 2500m, > 2000m so limit = req
    r = compute_resources(cores=50, cpu_per_core_millis=50)
    assert r["requests"]["cpu"] == "2500m"
    assert r["limits"]["cpu"] == "2500m"


def test_compute_resources_exactly_at_2000m_boundary():
    # 40 cores * 50m = 2000m
    r = compute_resources(cores=40, cpu_per_core_millis=50)
    assert r["requests"]["cpu"] == "2000m"
    assert r["limits"]["cpu"] == "2000m"


def test_compute_resources_custom_coefficient():
    # 8 cores * 100m = 800m, limit = 2000m
    r = compute_resources(cores=8, cpu_per_core_millis=100)
    assert r["requests"]["cpu"] == "800m"
    assert r["limits"]["cpu"] == "2000m"


def test_compute_resources_one_core():
    r = compute_resources(cores=1, cpu_per_core_millis=50)
    assert r == {
        "requests": {"cpu": "50m", "memory": "1034Mi"},
        "limits": {"cpu": "2000m", "memory": "2068Mi"},
    }


def test_compute_resources_zero_cores_rejected():
    with pytest.raises(InputValidationError):
        compute_resources(cores=0, cpu_per_core_millis=50)
