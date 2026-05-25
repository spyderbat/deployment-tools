import copy

import pytest

from customize_daemonset import (
    InputValidationError,
    Toleration,
    apply_env_overrides,
    build_variant,
    derive_cluster_name,
    split_daemonset,
)


# ---------------------------------------------------------------------------
# derive_cluster_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hostnames,expected", [
    # mixed roles (ms + wk) -> prefix diverges right after "rb"
    (["demo1rbms001.example.local", "demo1rbms002.example.local",
      "demo1rbwk004.example.local"], "demo1"),
    # single role -> prefix runs past "rb" but the digit-rb match still wins
    (["demo1rbms001.example.local", "demo1rbms002.example.local",
      "demo1rbms003.example.local"], "demo1"),
    # no domain, single host
    (["demo1rbms001"], "demo1"),
])
def test_derive_cluster_name(hostnames, expected):
    assert derive_cluster_name(hostnames) == expected


def test_derive_cluster_name_no_match_errors():
    with pytest.raises(InputValidationError, match="CLUSTER_NAME"):
        derive_cluster_name(["web-server-01.example.com",
                             "web-server-02.example.com"])


# ---------------------------------------------------------------------------
# apply_env_overrides
# ---------------------------------------------------------------------------

def test_apply_env_overrides_creates_env_list():
    c = {"name": "nano", "image": "x"}
    apply_env_overrides(c, [("CLUSTER_NAME", "demo1")])
    assert c["env"] == [{"name": "CLUSTER_NAME", "value": "demo1"}]


def test_apply_env_overrides_updates_existing_preserving_others():
    c = {"name": "nano",
         "env": [{"name": "CLUSTER_NAME", "value": "old"},
                 {"name": "FOO", "value": "bar"}]}
    apply_env_overrides(c, [("CLUSTER_NAME", "new")])
    assert c["env"][0] == {"name": "CLUSTER_NAME", "value": "new"}
    assert c["env"][1] == {"name": "FOO", "value": "bar"}


def test_apply_env_overrides_appends_new_in_given_order():
    c = {"name": "nano"}
    apply_env_overrides(c, [("HTTP_PROXY", "p"), ("HTTPS_PROXY", "p")])
    assert [e["name"] for e in c["env"]] == ["HTTP_PROXY", "HTTPS_PROXY"]


def _ds(name="nano", containers=1, init=False):
    spec_containers = [
        {"name": f"c{i}", "image": "spyderbat/nano:v1"} for i in range(containers)
    ]
    pod_spec = {"containers": spec_containers}
    if init:
        pod_spec["initContainers"] = [{"name": "init", "image": "busybox"}]
    return {
        "apiVersion": "apps/v1",
        "kind": "DaemonSet",
        "metadata": {"name": name},
        "spec": {
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": pod_spec,
            },
        },
    }


def test_split_daemonset_one_ds_passthrough_rest():
    sa = {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "sa"}}
    ds = _ds("nano")
    other = {"apiVersion": "apps/v1", "kind": "Deployment",
             "metadata": {"name": "cm"}}
    found, passthrough = split_daemonset([sa, ds, other])
    assert found is ds
    assert passthrough == [sa, other]


def test_split_daemonset_zero():
    with pytest.raises(InputValidationError, match="no DaemonSet"):
        split_daemonset([{"kind": "Deployment", "metadata": {"name": "x"}}])


def test_split_daemonset_two():
    docs = [_ds("a"), _ds("b")]
    with pytest.raises(InputValidationError, match="contains 2 DaemonSets"):
        split_daemonset(docs)


def test_split_daemonset_multi_container():
    with pytest.raises(InputValidationError, match="has 2 containers"):
        split_daemonset([_ds("nano", containers=2)])


def test_split_daemonset_init_containers():
    with pytest.raises(InputValidationError, match="initContainers"):
        split_daemonset([_ds("nano", init=True)])


def test_split_daemonset_skips_none_docs():
    # ruamel.yaml emits None for empty documents
    found, passthrough = split_daemonset([None, _ds("nano"), None])
    assert found is not None
    assert passthrough == []


def test_build_variant_name_suffix_and_resources():
    base = _ds("spyderbat-nanoagent")
    variant = build_variant(
        base=base,
        cores=8,
        hostnames=["host-b", "host-a"],  # input order shouldn't matter for ordering
        cpu_per_core_millis=50,
        final_tolerations=None,
    )
    assert variant["metadata"]["name"] == "spyderbat-nanoagent-8core"
    c = variant["spec"]["template"]["spec"]["containers"][0]
    assert c["resources"]["requests"]["cpu"] == "400m"
    assert c["resources"]["limits"]["cpu"] == "2000m"
    assert c["resources"]["requests"]["memory"] == "1104Mi"
    assert c["resources"]["limits"]["memory"] == "2208Mi"


def test_build_variant_node_affinity():
    base = _ds("nano")
    variant = build_variant(
        base=base,
        cores=8,
        hostnames=["host-b", "host-a"],
        cpu_per_core_millis=50,
        final_tolerations=None,
    )
    aff = variant["spec"]["template"]["spec"]["affinity"]["nodeAffinity"]
    terms = aff["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
    assert terms[0]["matchExpressions"][0] == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["host-a", "host-b"],   # sorted
    }


def test_build_variant_tolerations_applied_when_not_none():
    base = _ds("nano")
    base["spec"]["template"]["spec"]["tolerations"] = [
        {"key": "stale", "operator": "Exists", "effect": "NoSchedule"}
    ]
    variant = build_variant(
        base=base,
        cores=8,
        hostnames=["a"],
        cpu_per_core_millis=50,
        final_tolerations=[{"key": "new", "operator": "Exists",
                            "effect": "NoSchedule"}],
    )
    assert variant["spec"]["template"]["spec"]["tolerations"] == [
        {"key": "new", "operator": "Exists", "effect": "NoSchedule"}
    ]


def test_build_variant_tolerations_left_alone_when_none():
    base = _ds("nano")
    base["spec"]["template"]["spec"]["tolerations"] = [
        {"key": "stale", "operator": "Exists", "effect": "NoSchedule"}
    ]
    variant = build_variant(
        base=base,
        cores=8,
        hostnames=["a"],
        cpu_per_core_millis=50,
        final_tolerations=None,
    )
    assert variant["spec"]["template"]["spec"]["tolerations"] == [
        {"key": "stale", "operator": "Exists", "effect": "NoSchedule"}
    ]


def test_build_variant_does_not_mutate_base():
    base = _ds("nano")
    snapshot = copy.deepcopy(base)
    build_variant(base=base, cores=8, hostnames=["a"],
                  cpu_per_core_millis=50, final_tolerations=None)
    assert base == snapshot
