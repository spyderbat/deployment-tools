import pytest

from customize_daemonset import (
    _iter_workload_containers,
    _normalize_env_name,
    apply_consistent_env,
)


# ---------------------------------------------------------------------------
# _normalize_env_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("CLUSTER_NAME", "clustername"),
    ("Cluster_Name", "clustername"),
    ("ClusterName", "clustername"),
    ("cluster-name", "clustername"),
    ("HTTP_PROXY", "httpproxy"),
    ("http_proxy", "httpproxy"),
    ("HTTPProxy", "httpproxy"),
    ("HTTPS_PROXY", "httpsproxy"),
    ("https_proxy", "httpsproxy"),
    ("NO_PROXY", "noproxy"),
])
def test_normalize_env_name(name, expected):
    assert _normalize_env_name(name) == expected


# ---------------------------------------------------------------------------
# apply_consistent_env  (update-only, normalized matching)
# ---------------------------------------------------------------------------

def test_updates_all_cluster_name_spellings():
    c = {"name": "cm", "env": [
        {"name": "ClusterName", "value": "old"},
        {"name": "CLUSTER_NAME", "value": "old"},
    ]}
    apply_consistent_env(c, cluster_name="demo1", proxy=None)
    assert [e["value"] for e in c["env"]] == ["demo1", "demo1"]


def test_updates_both_proxy_vars_to_same_value_when_given():
    c = {"name": "cm", "env": [
        {"name": "HTTP_PROXY", "value": "old"},
        {"name": "https_proxy", "value": "old"},
    ]}
    apply_consistent_env(c, cluster_name="x", proxy="http://p:3128")
    assert [e["value"] for e in c["env"]] == ["http://p:3128", "http://p:3128"]


def test_leaves_proxy_untouched_when_proxy_is_none():
    c = {"name": "cm", "env": [{"name": "HTTP_PROXY", "value": "keep"}]}
    apply_consistent_env(c, cluster_name="x", proxy=None)
    assert c["env"][0]["value"] == "keep"


def test_no_proxy_is_never_clobbered():
    c = {"name": "cm", "env": [{"name": "NO_PROXY", "value": "10.0.0.0/8"}]}
    apply_consistent_env(c, cluster_name="x", proxy="http://p:3128")
    assert c["env"][0]["value"] == "10.0.0.0/8"


def test_never_appends_missing_vars():
    c = {"name": "cm", "env": [{"name": "FOO", "value": "bar"}]}
    apply_consistent_env(c, cluster_name="x", proxy="http://p:3128")
    assert c["env"] == [{"name": "FOO", "value": "bar"}]


def test_container_without_env_is_a_noop():
    c = {"name": "cm"}
    apply_consistent_env(c, cluster_name="x", proxy="http://p:3128")
    assert "env" not in c


def test_skips_value_from_refs():
    ref = {"configMapKeyRef": {"name": "cfg", "key": "cluster"}}
    c = {"name": "cm", "env": [{"name": "CLUSTER_NAME", "valueFrom": ref}]}
    apply_consistent_env(c, cluster_name="demo1", proxy=None)
    assert "value" not in c["env"][0]
    assert c["env"][0]["valueFrom"] == ref


# ---------------------------------------------------------------------------
# _iter_workload_containers
# ---------------------------------------------------------------------------

def test_iter_containers_deployment_includes_init_containers():
    dep = {"kind": "Deployment", "spec": {"template": {"spec": {
        "containers": [{"name": "a"}, {"name": "b"}],
        "initContainers": [{"name": "init"}],
    }}}}
    assert [c["name"] for c in _iter_workload_containers(dep)] == ["a", "b", "init"]


def test_iter_containers_bare_pod():
    pod = {"kind": "Pod", "spec": {"containers": [{"name": "solo"}]}}
    assert [c["name"] for c in _iter_workload_containers(pod)] == ["solo"]


def test_iter_containers_non_workload_yields_nothing():
    svc = {"kind": "Service", "spec": {"ports": [{"port": 80}]}}
    assert list(_iter_workload_containers(svc)) == []


def test_iter_containers_doc_without_spec_yields_nothing():
    cm = {"kind": "ConfigMap", "data": {"k": "v"}}
    assert list(_iter_workload_containers(cm)) == []
