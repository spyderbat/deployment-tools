import pytest

from customize_daemonset import (
    InputValidationError,
    NodeInfo,
    Taint,
    extract_nodes,
)


def _node(name, cpu="8", *, unschedulable=False, ready=True, taints=None):
    n = {
        "metadata": {"name": name},
        "spec": {},
        "status": {
            "capacity": {"cpu": cpu},
            "conditions": [
                {"type": "Ready", "status": "True" if ready else "False"},
            ],
        },
    }
    if unschedulable:
        n["spec"]["unschedulable"] = True
    if taints:
        n["spec"]["taints"] = taints
    return n


def _list_doc(*nodes):
    return {"apiVersion": "v1", "kind": "List", "items": list(nodes)}


def test_extract_nodes_basic():
    doc = _list_doc(_node("a", "8"), _node("b", "16"))
    nodes, skipped = extract_nodes(doc)
    assert len(nodes) == 2
    assert nodes[0].hostname == "a" and nodes[0].cores == 8
    assert nodes[1].hostname == "b" and nodes[1].cores == 16
    assert skipped == []


def test_extract_nodes_skips_unschedulable():
    doc = _list_doc(
        _node("a", "8"),
        _node("b", "8", unschedulable=True),
    )
    nodes, skipped = extract_nodes(doc)
    assert [n.hostname for n in nodes] == ["a"]
    assert skipped == ["b"]


def test_extract_nodes_skips_notready():
    doc = _list_doc(_node("a", "8"), _node("b", "8", ready=False))
    nodes, skipped = extract_nodes(doc)
    assert [n.hostname for n in nodes] == ["a"]
    assert skipped == ["b"]


def test_extract_nodes_extracts_taints():
    taint = {"key": "node-role.kubernetes.io/control-plane",
             "effect": "NoSchedule"}
    doc = _list_doc(_node("a", "8", taints=[taint]))
    nodes, _ = extract_nodes(doc)
    assert nodes[0].taints == (
        Taint(key="node-role.kubernetes.io/control-plane",
              value="", effect="NoSchedule"),
    )


def test_extract_nodes_rejects_non_list_kind():
    with pytest.raises(InputValidationError):
        extract_nodes({"apiVersion": "v1", "kind": "Pod"})


def test_extract_nodes_rejects_missing_items():
    with pytest.raises(InputValidationError):
        extract_nodes({"apiVersion": "v1", "kind": "List"})


def test_extract_nodes_rejects_node_missing_name():
    doc = _list_doc({"metadata": {}, "status": {"capacity": {"cpu": "8"}}})
    with pytest.raises(InputValidationError):
        extract_nodes(doc)


def test_extract_nodes_rejects_node_missing_capacity():
    doc = _list_doc({"metadata": {"name": "a"}, "status": {}})
    with pytest.raises(InputValidationError):
        extract_nodes(doc)
