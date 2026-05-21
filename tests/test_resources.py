import pytest

from customize_daemonset import (
    InputValidationError,
    compute_resources,
    compute_scale_factors,
    format_cpu_millicores,
    format_memory_mi,
    parse_memory_mi,
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


# ---------------------------------------------------------------------------
# parse_memory_mi
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected_mi", [
    ("1024Mi", 1024),
    ("1Gi", 1024),
    ("2Gi", 2048),
    ("1Ti", 1024 * 1024),
    ("1024", 0),         # bare integer = bytes; 1024 bytes -> 0 Mi (rounded down)
    ("1048576", 1),      # 1 Mi in bytes
    ("1G", 953),         # 1 GB = 10^9 bytes = ~953 Mi (953.674...)
    ("1M", 0),           # 1 MB = 10^6 bytes = 0 Mi (rounded down to int)
    ("500Mi", 500),
])
def test_parse_memory_mi_accepts(value, expected_mi):
    assert parse_memory_mi(value) == expected_mi


@pytest.mark.parametrize("value", [
    "", "abc", "1Xi", "-1Mi", " ", None, "1.5Gi",   # fractional not allowed
])
def test_parse_memory_mi_rejects(value):
    with pytest.raises(InputValidationError):
        parse_memory_mi(value)


# ---------------------------------------------------------------------------
# compute_scale_factors
# ---------------------------------------------------------------------------

def test_compute_scale_factors_no_caps():
    groups = {8: ["a", "b"], 16: ["c"]}
    f_cpu, f_mem = compute_scale_factors(
        groups=groups, cpu_per_core_millis=50,
        max_total_cpu_millis=None, max_total_mem_mi=None,
    )
    assert f_cpu == 1.0
    assert f_mem == 1.0


def test_compute_scale_factors_cpu_within_cap():
    # 2 nodes × 8 cores × 50m = 800m + 1 node × 16 cores × 50m = 800m = 1600m total
    groups = {8: ["a", "b"], 16: ["c"]}
    f_cpu, f_mem = compute_scale_factors(
        groups=groups, cpu_per_core_millis=50,
        max_total_cpu_millis=2000, max_total_mem_mi=None,
    )
    assert f_cpu == 1.0  # under cap


def test_compute_scale_factors_cpu_exceeds_cap():
    # total = 1600m; cap = 800m → f_cpu = 0.5
    groups = {8: ["a", "b"], 16: ["c"]}
    f_cpu, f_mem = compute_scale_factors(
        groups=groups, cpu_per_core_millis=50,
        max_total_cpu_millis=800, max_total_mem_mi=None,
    )
    assert f_cpu == 0.5


def test_compute_scale_factors_mem_exceeds_cap():
    # 3 pods total; base = 3 × 1024 = 3072 Mi
    # scalable = 2 × 8 × 10 + 1 × 16 × 10 = 320 Mi
    # at scale 1.0, total mem = 3072 + 320 = 3392 Mi
    # cap = 3200 Mi → f_mem = (3200 - 3072) / 320 = 128/320 = 0.4
    groups = {8: ["a", "b"], 16: ["c"]}
    f_cpu, f_mem = compute_scale_factors(
        groups=groups, cpu_per_core_millis=50,
        max_total_cpu_millis=None, max_total_mem_mi=3200,
    )
    assert f_cpu == 1.0
    assert abs(f_mem - 0.4) < 1e-9


def test_compute_scale_factors_mem_cap_below_base_errors():
    # 3 pods × 1024 Mi base = 3072 Mi. cap = 3000 Mi → impossible
    groups = {8: ["a", "b"], 16: ["c"]}
    with pytest.raises(InputValidationError, match="below"):
        compute_scale_factors(
            groups=groups, cpu_per_core_millis=50,
            max_total_cpu_millis=None, max_total_mem_mi=3000,
        )


def test_compute_scale_factors_both_caps_independent():
    groups = {8: ["a", "b"]}
    # cpu_total = 2 × 8 × 50 = 800m; cap = 400m → f_cpu = 0.5
    # mem_total = (1024 + 8×10) × 2 = 2208 Mi; base = 2048; scalable = 160
    # cap = 2128 Mi → f_mem = (2128 - 2048) / 160 = 0.5
    f_cpu, f_mem = compute_scale_factors(
        groups=groups, cpu_per_core_millis=50,
        max_total_cpu_millis=400, max_total_mem_mi=2128,
    )
    assert f_cpu == 0.5
    assert abs(f_mem - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# compute_resources with scale factors
# ---------------------------------------------------------------------------

def test_compute_resources_with_scale_factors():
    # 8 cores, 50m/core, scaled CPU to 0.5 and mem-per-core to 0.5
    # cpu_request = round(0.5 * 8 * 50) = 200m, limit = max(2000, 200) = 2000m
    # mem_request = 1024 + round(0.5 * 8 * 10) = 1024 + 40 = 1064 Mi
    # mem_limit = 2128 Mi
    r = compute_resources(cores=8, cpu_per_core_millis=50,
                          cpu_scale=0.5, mem_per_core_scale=0.5)
    assert r["requests"]["cpu"] == "200m"
    assert r["limits"]["cpu"] == "2000m"
    assert r["requests"]["memory"] == "1064Mi"
    assert r["limits"]["memory"] == "2128Mi"
