# DaemonSet Customizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the single-file Python CLI specified in `docs/design.md` that generates per-core-count Spyderbat Nano Agent DaemonSet variants from a base manifest and a `kubectl get nodes -o yaml` file.

**Architecture:** One executable script `customize_daemonset.py` at the repository root. Tests live under `tests/` and import directly from the script. Pure functions (parsing, formula, grouping) are unit-tested; end-to-end behavior is golden-file tested.

**Tech Stack:** Python 3.10+, `ruamel.yaml` (round-trip YAML), `InquirerPy` (interactive prompts), `pytest`.

---

## File Structure

```
customize_daemonset.py        # the script (executable, shebang)
requirements.txt              # ruamel.yaml, InquirerPy
requirements-dev.txt          # pytest
pyproject.toml                # minimal: pytest config, pythonpath
README.md                     # usage + manual interactive smoke test checklist
docs/
  design.md                   # already exists
  plan.md                     # this file
tests/
  __init__.py                 # empty
  conftest.py                 # fixtures_dir fixture, run_script helper
  test_resources.py
  test_parsers.py             # parse_cpu_per_core, parse_tolerate_spec, parse_cores
  test_nodes.py
  test_taints.py
  test_grouping.py
  test_transform.py
  test_cli_golden.py
  fixtures/
    base/
      minimal.yaml
      with_rbac.yaml
      two_daemonsets.yaml
      multi_container.yaml
      init_containers.yaml
    nodes/
      homogeneous_8core.yaml
      mixed_cores.yaml
      with_taints.yaml
      unschedulable.yaml
      empty.yaml
    expected/
      minimal_homogeneous.yaml
      with_rbac_mixed.yaml
      minimal_with_taints_interactive.txt   # describes interactive behavior, not auto-checked
```

### Internal structure of `customize_daemonset.py`

Top-to-bottom organization (single file):

1. **Imports** — stdlib + ruamel.yaml + InquirerPy (deferred import)
2. **Exception hierarchy** — `CustomizerError` + subclasses
3. **Dataclasses** — `Taint`, `Toleration`, `NodeInfo`
4. **Quantity helpers** — `parse_millicores`, `format_cpu_millicores`, `format_memory_mi`
5. **Argument parsers** — `parse_cpu_per_core_arg`, `parse_tolerate_arg`, `parse_node_cores`
6. **Node extraction** — `extract_nodes`, `distinct_taints`, `taint_matches_toleration`
7. **Toleration resolution** — `resolve_tolerations` (delegates interactive prompt to a helper)
8. **Grouping** — `group_by_cores`
9. **Resource formula** — `compute_resources`
10. **Base manifest handling** — `split_daemonset`, `build_variant`
11. **YAML I/O** — `load_yaml_docs`, `load_yaml_doc`, `write_output`, shared `ruamel.yaml.YAML` instance
12. **Interactive prompts** — `prompt_for_missing_inputs`, `prompt_for_taints` (only imports InquirerPy lazily)
13. **CLI plumbing** — `build_argparser`, `main`
14. **`if __name__ == "__main__":`** — call `main()`, translate `CustomizerError` to stderr + exit 1

---

## Task 1: Project skeleton & pytest config

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `pyproject.toml`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `.gitignore`

- [ ] **Step 1.1: Create `requirements.txt`**

```
ruamel.yaml>=0.18
InquirerPy>=0.3.4
```

- [ ] **Step 1.2: Create `requirements-dev.txt`**

```
-r requirements.txt
pytest>=7.0
```

- [ ] **Step 1.3: Create `pyproject.toml`**

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 1.4: Create `tests/__init__.py`** (empty file)

- [ ] **Step 1.5: Create `tests/conftest.py`**

```python
import pathlib
import subprocess
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "customize_daemonset.py"


@pytest.fixture
def fixtures_dir():
    return pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def run_script():
    def _run(*args, input_text=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            input=input_text,
            capture_output=True,
            text=True,
        )
    return _run
```

- [ ] **Step 1.6: Create `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.venv/
venv/
*.egg-info/
```

- [ ] **Step 1.7: Verify pytest can run (no tests yet)**

Run: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest -q`
Expected: `no tests ran in 0.0Xs`

- [ ] **Step 1.8: Commit**

```bash
git add requirements.txt requirements-dev.txt pyproject.toml .gitignore tests/__init__.py tests/conftest.py
git commit -m "Add project skeleton: requirements, pytest config, gitignore"
```

---

## Task 2: Exception hierarchy + skeleton script

**Files:**
- Create: `customize_daemonset.py`
- Create: `tests/test_errors.py`

- [ ] **Step 2.1: Write failing test `tests/test_errors.py`**

```python
import pytest

from customize_daemonset import (
    CustomizerError,
    InputValidationError,
    InteractiveCancelled,
)


def test_all_subclass_customizer_error():
    assert issubclass(InputValidationError, CustomizerError)
    assert issubclass(InteractiveCancelled, CustomizerError)


def test_can_raise_and_catch():
    with pytest.raises(CustomizerError):
        raise InputValidationError("bad input")
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'customize_daemonset'`

- [ ] **Step 2.3: Create `customize_daemonset.py` skeleton**

```python
#!/usr/bin/env python3
"""Generate per-core-count DaemonSet variants from a base manifest and a
`kubectl get nodes -o yaml` file. See docs/design.md for the full spec."""


class CustomizerError(Exception):
    """Base class for all expected, user-facing errors."""


class InputValidationError(CustomizerError):
    """Raised when input files or arguments are malformed or unsupported."""


class InteractiveCancelled(CustomizerError):
    """Raised when the user cancels an interactive prompt (Ctrl-C / EOF)."""
```

- [ ] **Step 2.4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_errors.py -v`
Expected: PASS (2 tests)

- [ ] **Step 2.5: Commit**

```bash
git add customize_daemonset.py tests/test_errors.py
git commit -m "Add exception hierarchy"
```

---

## Task 3: Quantity helpers

**Files:**
- Modify: `customize_daemonset.py`
- Create: `tests/test_resources.py`

The formula from §4.2 of the spec:

```
cpu_request_millis = cores * parse_millicores(C)
cpu_limit_millis   = max(2000, cpu_request_millis)
mem_request_mi     = 1024 + (cores * 10)
mem_limit_mi       = mem_request_mi * 2
```

This task implements the quantity helpers only; the formula itself ships in Task 4.

- [ ] **Step 3.1: Write failing test `tests/test_resources.py`**

```python
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
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_resources.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_millicores'`

- [ ] **Step 3.3: Add the helpers to `customize_daemonset.py`** (after the exception classes)

```python
def parse_millicores(value: str) -> int:
    """Parse a Kubernetes CPU quantity string into an integer number of millicores.

    Accepts: bare integer ("1" -> 1000), decimal ("0.5" -> 500), or millicore
    suffix ("50m" -> 50). Rejects negatives, empty strings, fractional
    millicore values, and unparseable strings.
    """
    if not isinstance(value, str) or not value.strip():
        raise InputValidationError(f"invalid CPU quantity: {value!r}")
    s = value.strip()
    if s.endswith("m"):
        body = s[:-1]
        try:
            millis = int(body)
        except ValueError:
            raise InputValidationError(
                f"invalid CPU quantity {value!r}: millicore form requires integer body"
            )
        if millis < 0:
            raise InputValidationError(f"invalid CPU quantity {value!r}: negative")
        return millis
    try:
        as_float = float(s)
    except ValueError:
        raise InputValidationError(f"invalid CPU quantity: {value!r}")
    if as_float < 0:
        raise InputValidationError(f"invalid CPU quantity {value!r}: negative")
    millis = as_float * 1000
    if abs(millis - round(millis)) > 1e-9:
        raise InputValidationError(
            f"invalid CPU quantity {value!r}: does not reduce to integer millicores"
        )
    return int(round(millis))


def format_cpu_millicores(millis: int) -> str:
    return f"{millis}m"


def format_memory_mi(mi: int) -> str:
    return f"{mi}Mi"
```

- [ ] **Step 3.4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_resources.py -v`
Expected: PASS (all)

- [ ] **Step 3.5: Commit**

```bash
git add customize_daemonset.py tests/test_resources.py
git commit -m "Add CPU/memory quantity helpers"
```

---

## Task 4: Resource formula (`compute_resources`)

**Files:**
- Modify: `customize_daemonset.py`
- Modify: `tests/test_resources.py`

- [ ] **Step 4.1: Append failing tests to `tests/test_resources.py`**

```python
from customize_daemonset import compute_resources


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
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_resources.py -v -k compute_resources`
Expected: FAIL with `ImportError: cannot import name 'compute_resources'`

- [ ] **Step 4.3: Add `compute_resources` to `customize_daemonset.py`**

```python
def compute_resources(*, cores: int, cpu_per_core_millis: int) -> dict:
    """Compute resources.requests and resources.limits for a node group.

    Formula (from docs/design.md §4.2):
        cpu_request_millis = cores * cpu_per_core_millis
        cpu_limit_millis   = max(2000, cpu_request_millis)
        mem_request_mi     = 1024 + (cores * 10)
        mem_limit_mi       = mem_request_mi * 2
    """
    if cores <= 0:
        raise InputValidationError(f"cores must be positive, got {cores}")
    cpu_request_millis = cores * cpu_per_core_millis
    cpu_limit_millis = max(2000, cpu_request_millis)
    mem_request_mi = 1024 + (cores * 10)
    mem_limit_mi = mem_request_mi * 2
    return {
        "requests": {
            "cpu": format_cpu_millicores(cpu_request_millis),
            "memory": format_memory_mi(mem_request_mi),
        },
        "limits": {
            "cpu": format_cpu_millicores(cpu_limit_millis),
            "memory": format_memory_mi(mem_limit_mi),
        },
    }
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_resources.py -v`
Expected: PASS (all)

- [ ] **Step 4.5: Commit**

```bash
git add customize_daemonset.py tests/test_resources.py
git commit -m "Add compute_resources formula"
```

---

## Task 5: CLI argument parsers (`parse_cpu_per_core_arg`, `parse_tolerate_arg`)

**Files:**
- Modify: `customize_daemonset.py`
- Create: `tests/test_parsers.py`

- [ ] **Step 5.1: Write failing tests `tests/test_parsers.py`**

```python
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
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_parsers.py -v`
Expected: FAIL with import errors

- [ ] **Step 5.3: Add the dataclasses + parsers to `customize_daemonset.py`**

Add right after the exception classes:

```python
from dataclasses import dataclass

VALID_TAINT_EFFECTS = ("NoSchedule", "PreferNoSchedule", "NoExecute")


@dataclass(frozen=True)
class Taint:
    key: str
    value: str   # may be ""
    effect: str  # one of VALID_TAINT_EFFECTS


@dataclass(frozen=True)
class Toleration:
    key: str
    operator: str   # "Equal" or "Exists"
    value: str      # "" when operator == "Exists"
    effect: str     # one of VALID_TAINT_EFFECTS, or "" to match all
```

Then add the parsers (place them after the quantity helpers, before `compute_resources` is fine):

```python
def parse_cpu_per_core_arg(value: str) -> int:
    """Parse the --cpu-request-per-core flag value into integer millicores."""
    return parse_millicores(value)


def parse_tolerate_arg(value: str) -> Toleration:
    """Parse a --tolerate flag value into a Toleration.

    Accepted forms:
        key=value:effect   -> operator Equal
        key:effect         -> operator Exists, no value
    """
    if not isinstance(value, str) or not value.strip():
        raise InputValidationError(f"invalid --tolerate {value!r}: empty")
    s = value.strip()

    colons = s.count(":")
    if colons != 1:
        raise InputValidationError(
            f"invalid --tolerate {value!r}: expected exactly one ':' separator"
        )
    left, effect = s.split(":", 1)
    if effect not in VALID_TAINT_EFFECTS:
        raise InputValidationError(
            f"invalid --tolerate {value!r}: effect must be one of "
            f"{VALID_TAINT_EFFECTS}"
        )

    equals = left.count("=")
    if equals == 0:
        # Exists form
        if not left:
            raise InputValidationError(f"invalid --tolerate {value!r}: empty key")
        return Toleration(key=left, operator="Exists", value="", effect=effect)
    if equals == 1:
        key, val = left.split("=", 1)
        if not key:
            raise InputValidationError(f"invalid --tolerate {value!r}: empty key")
        if not val:
            raise InputValidationError(
                f"invalid --tolerate {value!r}: empty value (use 'key:effect' for Exists)"
            )
        return Toleration(key=key, operator="Equal", value=val, effect=effect)
    raise InputValidationError(
        f"invalid --tolerate {value!r}: too many '=' separators"
    )
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_parsers.py -v`
Expected: PASS (all)

- [ ] **Step 5.5: Commit**

```bash
git add customize_daemonset.py tests/test_parsers.py
git commit -m "Add CLI argument parsers and Taint/Toleration dataclasses"
```

---

## Task 6: Node CPU capacity parser (`parse_node_cores`)

**Files:**
- Modify: `customize_daemonset.py`
- Modify: `tests/test_parsers.py`

- [ ] **Step 6.1: Append failing tests to `tests/test_parsers.py`**

```python
from customize_daemonset import parse_node_cores


@pytest.mark.parametrize("value,expected", [
    ("8", 8),
    ("16", 16),
    ("8000m", 8),
    ("16000m", 16),
    ("8.0", 8),
])
def test_parse_node_cores_accepts(value, expected):
    assert parse_node_cores(value) == expected


@pytest.mark.parametrize("value", [
    "7.5", "7500m", "0", "-1", "abc", "", None,
])
def test_parse_node_cores_rejects(value):
    with pytest.raises(InputValidationError):
        parse_node_cores(value)
```

- [ ] **Step 6.2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_parsers.py -v -k parse_node_cores`
Expected: FAIL with `ImportError`

- [ ] **Step 6.3: Add `parse_node_cores` to `customize_daemonset.py`**

```python
def parse_node_cores(value) -> int:
    """Parse a node's `status.capacity.cpu` value into a positive integer core count.

    Accepts forms like "8", "8000m", "8.0". Rejects fractional values
    (e.g. "7.5", "7500m") and non-positive values.
    """
    if not isinstance(value, str) or not value.strip():
        raise InputValidationError(f"invalid node CPU capacity: {value!r}")
    millis = parse_millicores(value)
    if millis <= 0:
        raise InputValidationError(
            f"invalid node CPU capacity {value!r}: must be positive"
        )
    if millis % 1000 != 0:
        raise InputValidationError(
            f"invalid node CPU capacity {value!r}: not a whole number of cores"
        )
    return millis // 1000
```

- [ ] **Step 6.4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_parsers.py -v -k parse_node_cores`
Expected: PASS

- [ ] **Step 6.5: Commit**

```bash
git add customize_daemonset.py tests/test_parsers.py
git commit -m "Add parse_node_cores for kubectl node capacity strings"
```

---

## Task 7: Node extraction (`extract_nodes`, filtering)

**Files:**
- Modify: `customize_daemonset.py`
- Create: `tests/test_nodes.py`

- [ ] **Step 7.1: Write failing tests `tests/test_nodes.py`**

```python
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
```

- [ ] **Step 7.2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_nodes.py -v`
Expected: FAIL with `ImportError: cannot import name 'NodeInfo'`

- [ ] **Step 7.3: Add `NodeInfo` and `extract_nodes` to `customize_daemonset.py`**

Add `NodeInfo` next to the other dataclasses:

```python
@dataclass(frozen=True)
class NodeInfo:
    hostname: str
    cores: int
    taints: tuple  # tuple[Taint, ...]
```

Add `extract_nodes` after the parsers:

```python
def _node_is_ready(node: dict) -> bool:
    conditions = (node.get("status") or {}).get("conditions") or []
    for c in conditions:
        if c.get("type") == "Ready":
            return c.get("status") == "True"
    return False


def _node_taints(node: dict) -> tuple:
    raw = (node.get("spec") or {}).get("taints") or []
    out = []
    for t in raw:
        key = t.get("key")
        effect = t.get("effect")
        value = t.get("value", "") or ""
        if not key or effect not in VALID_TAINT_EFFECTS:
            raise InputValidationError(
                f"node {(node.get('metadata') or {}).get('name')!r} has malformed taint: {t!r}"
            )
        out.append(Taint(key=key, value=value, effect=effect))
    return tuple(out)


def extract_nodes(nodes_doc: dict):
    """Parse a kubectl-get-nodes-o-yaml doc into NodeInfo list, plus skipped names.

    Filters out nodes where `spec.unschedulable` is true or `Ready` is not True.
    Returns (nodes, skipped_hostnames).
    """
    if not isinstance(nodes_doc, dict):
        raise InputValidationError("nodes file root is not a YAML mapping")
    if nodes_doc.get("kind") != "List":
        raise InputValidationError(
            f"nodes file is not a kubectl get nodes -o yaml output "
            f"(kind={nodes_doc.get('kind')!r}, expected 'List')"
        )
    items = nodes_doc.get("items")
    if items is None:
        raise InputValidationError("nodes file has no 'items' list")
    if not isinstance(items, list):
        raise InputValidationError("nodes file 'items' is not a list")

    nodes = []
    skipped = []
    for idx, item in enumerate(items):
        meta = item.get("metadata") or {}
        name = meta.get("name")
        if not name:
            raise InputValidationError(
                f"node at index {idx}: missing metadata.name"
            )
        capacity = ((item.get("status") or {}).get("capacity") or {}).get("cpu")
        if capacity is None:
            raise InputValidationError(
                f"node {name!r}: missing status.capacity.cpu"
            )
        if (item.get("spec") or {}).get("unschedulable") is True:
            skipped.append(name)
            continue
        if not _node_is_ready(item):
            skipped.append(name)
            continue
        try:
            cores = parse_node_cores(capacity)
        except InputValidationError as e:
            raise InputValidationError(f"node {name!r}: {e}") from e
        taints = _node_taints(item)
        nodes.append(NodeInfo(hostname=name, cores=cores, taints=taints))
    return nodes, skipped
```

- [ ] **Step 7.4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_nodes.py -v`
Expected: PASS (all)

- [ ] **Step 7.5: Commit**

```bash
git add customize_daemonset.py tests/test_nodes.py
git commit -m "Add node extraction with schedulability/Ready filtering"
```

---

## Task 8: Taint helpers (`distinct_taints`, `taint_matches_toleration`)

**Files:**
- Modify: `customize_daemonset.py`
- Create: `tests/test_taints.py`

- [ ] **Step 8.1: Write failing tests `tests/test_taints.py`**

```python
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
```

- [ ] **Step 8.2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_taints.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 8.3: Add the helpers to `customize_daemonset.py`**

Place after `extract_nodes`:

```python
def distinct_taints(nodes):
    """Map each distinct taint (by key+value+effect) to the sorted list of
    hostnames carrying it."""
    by_taint = {}
    for n in nodes:
        for t in n.taints:
            by_taint.setdefault(t, []).append(n.hostname)
    return {t: sorted(hosts) for t, hosts in by_taint.items()}


def taint_matches_toleration(taint: Taint, tol: Toleration) -> bool:
    if tol.key != taint.key:
        return False
    if tol.effect and tol.effect != taint.effect:
        return False
    if tol.operator == "Exists":
        return True
    if tol.operator == "Equal":
        return tol.value == taint.value
    return False
```

- [ ] **Step 8.4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_taints.py -v`
Expected: PASS (all)

- [ ] **Step 8.5: Commit**

```bash
git add customize_daemonset.py tests/test_taints.py
git commit -m "Add taint deduplication and toleration matching"
```

---

## Task 9: Toleration resolution (non-interactive paths)

**Files:**
- Modify: `customize_daemonset.py`
- Create: `tests/test_resolve_tolerations.py`

This task covers the three **non-interactive** rows of the §4.4 table:
- `--tolerate` supplied (replaces base, filters unmatched nodes)
- No `--tolerate`, no taints (keep base unchanged)
- No `--tolerate`, taints, non-TTY (hard error)

The TTY-driven prompt path is added in Task 14.

- [ ] **Step 9.1: Write failing tests `tests/test_resolve_tolerations.py`**

```python
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
    assert final == [user_tol]
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
```

- [ ] **Step 9.2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_resolve_tolerations.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 9.3: Add `resolve_tolerations` to `customize_daemonset.py`**

```python
def _toleration_to_dict(t: Toleration) -> dict:
    out = {"key": t.key, "operator": t.operator}
    if t.operator == "Equal":
        out["value"] = t.value
    if t.effect:
        out["effect"] = t.effect
    return out


def _filter_nodes_by_tolerations(nodes, tolerations):
    """Return (usable, excluded_hostnames). A node is usable iff every taint
    it carries is matched by at least one toleration."""
    usable = []
    excluded = []
    for n in nodes:
        if all(any(taint_matches_toleration(t, r) for r in tolerations)
               for t in n.taints):
            usable.append(n)
        else:
            excluded.append(n.hostname)
    return usable, excluded


def resolve_tolerations(
    *,
    nodes,
    explicit_tolerations,
    base_tolerations,
    is_tty: bool,
    prompt_fn=None,
):
    """Determine final tolerations and usable nodes per §4.4 of the spec.

    Args:
        nodes: list[NodeInfo] (already filtered for schedulability).
        explicit_tolerations: list[Toleration] from --tolerate flags.
        base_tolerations: list[dict] from the base manifest's
            spec.template.spec.tolerations, or None.
        is_tty: whether stdin is a TTY (controls interactive prompt path).
        prompt_fn: callable(distinct_taints_dict) -> set[Taint] selected.
            Used only on the no-explicit + has-taints + TTY path.

    Returns:
        (final_tolerations_as_dicts, usable_nodes, excluded_hostnames)
    """
    taint_map = distinct_taints(nodes)

    if explicit_tolerations:
        final = list(explicit_tolerations)
        usable, excluded = _filter_nodes_by_tolerations(nodes, final)
        return [_toleration_to_dict(t) for t in final], usable, excluded

    if not taint_map:
        # No taints anywhere -- preserve base tolerations as-is.
        return (list(base_tolerations) if base_tolerations else None,
                list(nodes), [])

    # Taints exist but no explicit --tolerate.
    if not is_tty:
        taint_summary = ", ".join(
            f"{t.key}{('=' + t.value) if t.value else ''}:{t.effect} ({len(hosts)} nodes)"
            for t, hosts in taint_map.items()
        )
        raise InputValidationError(
            f"tainted nodes detected: {taint_summary}. "
            f"Rerun interactively or supply --tolerate."
        )

    if prompt_fn is None:
        raise InputValidationError(
            "interactive taint prompt requested but no prompt_fn provided"
        )
    selected = prompt_fn(taint_map)  # set[Taint]
    new_tols = [Toleration(key=t.key,
                            operator="Equal" if t.value else "Exists",
                            value=t.value,
                            effect=t.effect)
                for t in selected]
    final_dicts = list(base_tolerations) if base_tolerations else []
    final_dicts.extend(_toleration_to_dict(t) for t in new_tols)
    usable, excluded = _filter_nodes_by_tolerations(nodes, new_tols)
    return final_dicts, usable, excluded
```

- [ ] **Step 9.4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_resolve_tolerations.py -v`
Expected: PASS (all)

- [ ] **Step 9.5: Commit**

```bash
git add customize_daemonset.py tests/test_resolve_tolerations.py
git commit -m "Add resolve_tolerations for non-interactive paths"
```

---

## Task 10: Grouping (`group_by_cores`)

**Files:**
- Modify: `customize_daemonset.py`
- Create: `tests/test_grouping.py`

- [ ] **Step 10.1: Write failing tests `tests/test_grouping.py`**

```python
import pytest

from customize_daemonset import (
    InputValidationError,
    NodeInfo,
    group_by_cores,
)


def _n(name, cores):
    return NodeInfo(hostname=name, cores=cores, taints=())


def test_group_single_bucket():
    out = group_by_cores([_n("a", 8), _n("b", 8)])
    assert out == {8: ["a", "b"]}


def test_group_multiple_buckets_sorted():
    out = group_by_cores([_n("c", 16), _n("a", 8), _n("b", 8), _n("d", 32)])
    assert list(out.keys()) == [8, 16, 32]
    assert out[8] == ["a", "b"]
    assert out[16] == ["c"]
    assert out[32] == ["d"]


def test_group_hostnames_sorted_within_bucket():
    out = group_by_cores([_n("zeta", 8), _n("alpha", 8), _n("mu", 8)])
    assert out[8] == ["alpha", "mu", "zeta"]


def test_group_empty_rejected():
    with pytest.raises(InputValidationError):
        group_by_cores([])
```

- [ ] **Step 10.2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_grouping.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 10.3: Add `group_by_cores` to `customize_daemonset.py`**

```python
def group_by_cores(nodes):
    """Return an ordered dict[int, list[str]] mapping cores -> sorted hostnames.
    Keys are sorted ascending."""
    if not nodes:
        raise InputValidationError(
            "no schedulable, ready nodes available for grouping"
        )
    buckets: dict = {}
    for n in nodes:
        buckets.setdefault(n.cores, []).append(n.hostname)
    return {cores: sorted(hosts) for cores, hosts in sorted(buckets.items())}
```

- [ ] **Step 10.4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_grouping.py -v`
Expected: PASS (all)

- [ ] **Step 10.5: Commit**

```bash
git add customize_daemonset.py tests/test_grouping.py
git commit -m "Add group_by_cores"
```

---

## Task 11: Base manifest handling (`split_daemonset`, `build_variant`)

**Files:**
- Modify: `customize_daemonset.py`
- Create: `tests/test_transform.py`

- [ ] **Step 11.1: Write failing tests `tests/test_transform.py`**

```python
import copy

import pytest

from customize_daemonset import (
    InputValidationError,
    Toleration,
    build_variant,
    split_daemonset,
)


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
```

- [ ] **Step 11.2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_transform.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 11.3: Add `split_daemonset` and `build_variant` to `customize_daemonset.py`**

```python
import copy


def split_daemonset(docs):
    """Validate and split a list of YAML documents.

    Returns (the_one_daemonset, list_of_other_documents_in_input_order).
    Skips None entries (empty YAML documents).
    """
    daemonsets = []
    passthrough = []
    for doc in docs:
        if doc is None:
            continue
        if not isinstance(doc, dict):
            raise InputValidationError(
                f"base manifest contains a non-mapping document: {type(doc).__name__}"
            )
        if doc.get("kind") == "DaemonSet":
            daemonsets.append(doc)
        else:
            passthrough.append(doc)

    if len(daemonsets) == 0:
        raise InputValidationError("base manifest contains no DaemonSet")
    if len(daemonsets) > 1:
        names = [(d.get("metadata") or {}).get("name", "<unnamed>")
                 for d in daemonsets]
        raise InputValidationError(
            f"base manifest contains {len(daemonsets)} DaemonSets, expected 1: {names}"
        )

    ds = daemonsets[0]
    ds_name = (ds.get("metadata") or {}).get("name", "<unnamed>")
    pod_spec = (((ds.get("spec") or {}).get("template") or {}).get("spec") or {})
    containers = pod_spec.get("containers") or []
    if len(containers) != 1:
        raise InputValidationError(
            f"DaemonSet {ds_name!r} has {len(containers)} containers, expected 1"
        )
    if pod_spec.get("initContainers"):
        raise InputValidationError(
            f"DaemonSet {ds_name!r} has initContainers, not supported"
        )
    return ds, passthrough


def build_variant(*, base, cores, hostnames, cpu_per_core_millis, final_tolerations):
    """Build a single DaemonSet variant from the base DS.

    `final_tolerations` is either a list of dicts to apply (replacing any
    existing tolerations) or None (leave the base manifest's tolerations
    untouched).
    """
    v = copy.deepcopy(base)
    base_name = (v.get("metadata") or {}).get("name", "daemonset")
    v["metadata"]["name"] = f"{base_name}-{cores}core"

    container = v["spec"]["template"]["spec"]["containers"][0]
    container["resources"] = compute_resources(
        cores=cores, cpu_per_core_millis=cpu_per_core_millis
    )

    pod_spec = v["spec"]["template"]["spec"]
    affinity = pod_spec.setdefault("affinity", {})
    affinity["nodeAffinity"] = {
        "requiredDuringSchedulingIgnoredDuringExecution": {
            "nodeSelectorTerms": [
                {
                    "matchExpressions": [
                        {
                            "key": "kubernetes.io/hostname",
                            "operator": "In",
                            "values": sorted(hostnames),
                        }
                    ]
                }
            ]
        }
    }

    if final_tolerations is not None:
        pod_spec["tolerations"] = list(final_tolerations)

    return v
```

- [ ] **Step 11.4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_transform.py -v`
Expected: PASS (all)

- [ ] **Step 11.5: Commit**

```bash
git add customize_daemonset.py tests/test_transform.py
git commit -m "Add split_daemonset and build_variant transforms"
```

---

## Task 12: YAML I/O (`load_yaml_docs`, `load_yaml_doc`, `write_output`)

**Files:**
- Modify: `customize_daemonset.py`
- Create: `tests/test_yaml_io.py`

- [ ] **Step 12.1: Write failing tests `tests/test_yaml_io.py`**

```python
import io
import sys

import pytest

from customize_daemonset import (
    InputValidationError,
    load_yaml_doc,
    load_yaml_docs,
    write_output,
)


def test_load_yaml_docs_multi(tmp_path):
    p = tmp_path / "multi.yaml"
    p.write_text("---\nkind: A\nname: one\n---\nkind: B\nname: two\n")
    docs = load_yaml_docs(p)
    assert len(docs) == 2
    assert docs[0]["kind"] == "A"
    assert docs[1]["kind"] == "B"


def test_load_yaml_docs_missing_file(tmp_path):
    with pytest.raises(InputValidationError, match="not found"):
        load_yaml_docs(tmp_path / "missing.yaml")


def test_load_yaml_doc_single(tmp_path):
    p = tmp_path / "one.yaml"
    p.write_text("kind: List\nitems: []\n")
    d = load_yaml_doc(p)
    assert d["kind"] == "List"


def test_load_yaml_doc_rejects_multi(tmp_path):
    p = tmp_path / "multi.yaml"
    p.write_text("---\na: 1\n---\nb: 2\n")
    with pytest.raises(InputValidationError, match="multiple documents"):
        load_yaml_doc(p)


def test_write_output_stdout(capsys):
    write_output(None, [{"kind": "A"}, {"kind": "B"}])
    out = capsys.readouterr().out
    assert "kind: A" in out
    assert "kind: B" in out
    assert "---" in out


def test_write_output_file(tmp_path):
    p = tmp_path / "out.yaml"
    write_output(p, [{"kind": "A"}, {"kind": "B"}])
    text = p.read_text()
    assert "kind: A" in text
    assert "kind: B" in text


def test_write_output_missing_dir(tmp_path):
    p = tmp_path / "nope" / "out.yaml"
    with pytest.raises(InputValidationError, match="output directory"):
        write_output(p, [{"kind": "A"}])
```

- [ ] **Step 12.2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_yaml_io.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 12.3: Add YAML I/O helpers to `customize_daemonset.py`**

Add near the top imports:

```python
import pathlib
import sys

from ruamel.yaml import YAML
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError
```

Add the helpers (after `build_variant`):

```python
def _yaml():
    y = YAML(typ="rt")  # round-trip
    y.preserve_quotes = True
    y.width = 4096
    return y


def load_yaml_docs(path) -> list:
    path = pathlib.Path(path)
    if not path.exists():
        raise InputValidationError(f"base manifest not found: {path}")
    try:
        return list(_yaml().load_all(path.read_text()))
    except (ParserError, ScannerError) as e:
        raise InputValidationError(f"failed to parse {path}: {e}") from e


def load_yaml_doc(path) -> dict:
    path = pathlib.Path(path)
    if not path.exists():
        raise InputValidationError(f"nodes file not found: {path}")
    try:
        docs = list(_yaml().load_all(path.read_text()))
    except (ParserError, ScannerError) as e:
        raise InputValidationError(f"failed to parse {path}: {e}") from e
    if len(docs) != 1:
        raise InputValidationError(
            f"{path}: expected a single YAML document, found {len(docs)} (multiple documents)"
        )
    return docs[0]


def write_output(path, docs):
    y = _yaml()
    y.explicit_start = True
    if path is None:
        for doc in docs:
            y.dump(doc, sys.stdout)
        return
    path = pathlib.Path(path)
    if not path.parent.exists():
        raise InputValidationError(f"output directory does not exist: {path.parent}")
    with path.open("w") as f:
        for doc in docs:
            y.dump(doc, f)
```

- [ ] **Step 12.4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_yaml_io.py -v`
Expected: PASS (all)

- [ ] **Step 12.5: Commit**

```bash
git add customize_daemonset.py tests/test_yaml_io.py
git commit -m "Add ruamel-based YAML I/O helpers"
```

---

## Task 13: CLI plumbing — argparser + main (non-interactive only)

**Files:**
- Modify: `customize_daemonset.py`

- [ ] **Step 13.1: Add CLI plumbing to `customize_daemonset.py`**

Add at end of file (before any `__main__` block):

```python
import argparse


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="customize_daemonset",
        description="Generate per-core-count DaemonSet variants.",
    )
    p.add_argument("--base", help="path to base manifest YAML")
    p.add_argument("--nodes", help="path to `kubectl get nodes -o yaml` output")
    p.add_argument("--out", help="output file path (default: stdout)")
    p.add_argument("--cpu-request-per-core", default="50m",
                   help="CPU quantity per core for requests (default: 50m)")
    p.add_argument("--tolerate", action="append", default=[],
                   metavar="KEY[=VALUE]:EFFECT",
                   help="replace base manifest tolerations with these (repeatable)")
    p.add_argument("--interactive", action="store_true",
                   help="force interactive prompts for all inputs")
    return p


def _run(args, *, stdin_is_tty: bool) -> int:
    if args.interactive or (stdin_is_tty and (not args.base or not args.nodes)):
        # Interactive mode is wired in Task 14. For now, error if any
        # required arg is missing.
        prompt_for_missing_inputs(args, stdin_is_tty)
    if not args.base:
        raise InputValidationError("missing required argument: --base")
    if not args.nodes:
        raise InputValidationError("missing required argument: --nodes")

    cpu_per_core_millis = parse_cpu_per_core_arg(args.cpu_request_per_core)
    explicit_tolerations = [parse_tolerate_arg(s) for s in args.tolerate]

    base_docs = load_yaml_docs(args.base)
    nodes_doc = load_yaml_doc(args.nodes)

    ds_doc, passthrough_docs = split_daemonset(base_docs)
    base_tolerations = (((ds_doc.get("spec") or {}).get("template") or {})
                        .get("spec") or {}).get("tolerations")

    node_infos, skipped = extract_nodes(nodes_doc)
    if skipped:
        print(f"skipped {len(skipped)} nodes "
              f"(unschedulable or NotReady): {', '.join(skipped)}",
              file=sys.stderr)

    final_tolerations, usable_nodes, excluded = resolve_tolerations(
        nodes=node_infos,
        explicit_tolerations=explicit_tolerations,
        base_tolerations=base_tolerations,
        is_tty=stdin_is_tty,
        prompt_fn=prompt_for_taints if stdin_is_tty else None,
    )
    if excluded:
        print(f"excluded {len(excluded)} nodes due to unmatched taints: "
              f"{', '.join(excluded)}", file=sys.stderr)

    groups = group_by_cores(usable_nodes)

    variants = [
        build_variant(
            base=ds_doc,
            cores=cores,
            hostnames=hostnames,
            cpu_per_core_millis=cpu_per_core_millis,
            final_tolerations=final_tolerations,
        )
        for cores, hostnames in groups.items()
    ]

    write_output(args.out, passthrough_docs + variants)
    return 0


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)
    try:
        return _run(args, stdin_is_tty=sys.stdin.isatty())
    except InteractiveCancelled:
        return 130
    except CustomizerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


# Stubs for interactive helpers; implemented in Task 14.
def prompt_for_missing_inputs(args, is_tty):
    if not args.base or not args.nodes:
        missing = [n for n, v in
                   [("--base", args.base), ("--nodes", args.nodes)] if not v]
        raise InputValidationError(
            f"missing required argument(s): {', '.join(missing)}"
        )


def prompt_for_taints(taint_map):
    raise InputValidationError(
        "interactive taint selection not yet implemented"
    )


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 13.2: Smoke test that the script runs (no-arg → usage error 2)**

Run: `.venv/bin/python customize_daemonset.py < /dev/null; echo "exit=$?"`
Expected: stderr says `missing required argument(s): --base, --nodes`, exit=1

- [ ] **Step 13.3: Commit**

```bash
git add customize_daemonset.py
git commit -m "Wire up CLI argparser and main() pipeline (non-interactive)"
```

---

## Task 14: Interactive prompts (`prompt_for_missing_inputs`, `prompt_for_taints`)

**Files:**
- Modify: `customize_daemonset.py`

- [ ] **Step 14.1: Replace the stub `prompt_for_missing_inputs` and `prompt_for_taints`** in `customize_daemonset.py`

```python
def _import_inquirer():
    try:
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice
        from InquirerPy.validator import PathValidator
    except ImportError as e:
        raise InputValidationError(
            "InquirerPy is required for interactive mode "
            "(pip install InquirerPy)"
        ) from e
    return inquirer, Choice, PathValidator


def prompt_for_missing_inputs(args, is_tty):
    """In TTY mode, fill in any missing required args via InquirerPy.
    Non-TTY mode: error out for any missing required arg."""
    if not is_tty:
        missing = [n for n, v in
                   [("--base", args.base), ("--nodes", args.nodes)] if not v]
        if missing:
            raise InputValidationError(
                f"missing required argument(s): {', '.join(missing)}"
            )
        return

    inquirer, _Choice, PathValidator = _import_inquirer()
    try:
        if not args.base:
            args.base = inquirer.filepath(
                message="Path to base manifest YAML:",
                validate=PathValidator(is_file=True,
                                       message="file does not exist"),
            ).execute()
        if not args.nodes:
            args.nodes = inquirer.filepath(
                message="Path to `kubectl get nodes -o yaml` output:",
                validate=PathValidator(is_file=True,
                                       message="file does not exist"),
            ).execute()
    except KeyboardInterrupt as e:
        raise InteractiveCancelled("cancelled") from e


def prompt_for_taints(taint_map):
    """Show a checkbox prompt for each distinct taint; return set[Taint] selected."""
    inquirer, Choice, _PathValidator = _import_inquirer()
    choices = [
        Choice(value=t,
               name=f"{t.key}{('=' + t.value) if t.value else ''}:{t.effect}"
                    f"  ({len(hosts)} nodes)")
        for t, hosts in taint_map.items()
    ]
    try:
        selected = inquirer.checkbox(
            message="Select taints to tolerate "
                    "(nodes with unselected taints will be excluded):",
            choices=choices,
            instruction="(space to toggle, enter to confirm)",
        ).execute()
    except KeyboardInterrupt as e:
        raise InteractiveCancelled("cancelled") from e
    return set(selected)
```

- [ ] **Step 14.2: Smoke test (manual)**

Run interactively in a real terminal:
```
.venv/bin/python customize_daemonset.py --base deployment_file.yaml --nodes nodes.yaml
```
Expected: if `nodes.yaml` has tainted nodes, a checkbox prompt appears. Output streams to stdout when complete.

- [ ] **Step 14.3: Commit**

```bash
git add customize_daemonset.py
git commit -m "Add InquirerPy-driven interactive prompts"
```

---

## Task 15: CLI fixtures

**Files:**
- Create: `tests/fixtures/base/minimal.yaml`
- Create: `tests/fixtures/base/with_rbac.yaml`
- Create: `tests/fixtures/base/two_daemonsets.yaml`
- Create: `tests/fixtures/base/multi_container.yaml`
- Create: `tests/fixtures/base/init_containers.yaml`
- Create: `tests/fixtures/nodes/homogeneous_8core.yaml`
- Create: `tests/fixtures/nodes/mixed_cores.yaml`
- Create: `tests/fixtures/nodes/with_taints.yaml`
- Create: `tests/fixtures/nodes/unschedulable.yaml`
- Create: `tests/fixtures/nodes/empty.yaml`

- [ ] **Step 15.1: Create `tests/fixtures/base/minimal.yaml`**

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: spyderbat-nanoagent
spec:
  selector:
    matchLabels:
      app: nanoagent
  template:
    metadata:
      labels:
        app: nanoagent
    spec:
      containers:
      - name: nanoagent
        image: spyderbat/nano:v1
```

- [ ] **Step 15.2: Create `tests/fixtures/base/with_rbac.yaml`**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: nanoagent-sa
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: nanoagent-cr
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list", "watch"]
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: spyderbat-nanoagent
spec:
  selector:
    matchLabels:
      app: nanoagent
  template:
    metadata:
      labels:
        app: nanoagent
    spec:
      serviceAccountName: nanoagent-sa
      containers:
      - name: nanoagent
        image: spyderbat/nano:v1
```

- [ ] **Step 15.3: Create `tests/fixtures/base/two_daemonsets.yaml`**

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: a
spec:
  selector: {matchLabels: {app: a}}
  template:
    metadata: {labels: {app: a}}
    spec:
      containers: [{name: a, image: a:1}]
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: b
spec:
  selector: {matchLabels: {app: b}}
  template:
    metadata: {labels: {app: b}}
    spec:
      containers: [{name: b, image: b:1}]
```

- [ ] **Step 15.4: Create `tests/fixtures/base/multi_container.yaml`**

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: spyderbat-nanoagent
spec:
  selector: {matchLabels: {app: nano}}
  template:
    metadata: {labels: {app: nano}}
    spec:
      containers:
      - {name: a, image: a:1}
      - {name: b, image: b:1}
```

- [ ] **Step 15.5: Create `tests/fixtures/base/init_containers.yaml`**

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: spyderbat-nanoagent
spec:
  selector: {matchLabels: {app: nano}}
  template:
    metadata: {labels: {app: nano}}
    spec:
      initContainers: [{name: i, image: busybox}]
      containers: [{name: nano, image: nano:1}]
```

- [ ] **Step 15.6: Create `tests/fixtures/nodes/homogeneous_8core.yaml`**

```yaml
apiVersion: v1
kind: List
items:
- apiVersion: v1
  kind: Node
  metadata: {name: node-a}
  spec: {}
  status:
    capacity: {cpu: "8"}
    conditions:
    - {type: Ready, status: "True"}
- apiVersion: v1
  kind: Node
  metadata: {name: node-b}
  spec: {}
  status:
    capacity: {cpu: "8"}
    conditions:
    - {type: Ready, status: "True"}
- apiVersion: v1
  kind: Node
  metadata: {name: node-c}
  spec: {}
  status:
    capacity: {cpu: "8"}
    conditions:
    - {type: Ready, status: "True"}
```

- [ ] **Step 15.7: Create `tests/fixtures/nodes/mixed_cores.yaml`**

```yaml
apiVersion: v1
kind: List
items:
- apiVersion: v1
  kind: Node
  metadata: {name: small-1}
  spec: {}
  status:
    capacity: {cpu: "4"}
    conditions: [{type: Ready, status: "True"}]
- apiVersion: v1
  kind: Node
  metadata: {name: small-2}
  spec: {}
  status:
    capacity: {cpu: "4"}
    conditions: [{type: Ready, status: "True"}]
- apiVersion: v1
  kind: Node
  metadata: {name: med-1}
  spec: {}
  status:
    capacity: {cpu: "8"}
    conditions: [{type: Ready, status: "True"}]
- apiVersion: v1
  kind: Node
  metadata: {name: med-2}
  spec: {}
  status:
    capacity: {cpu: "8"}
    conditions: [{type: Ready, status: "True"}]
- apiVersion: v1
  kind: Node
  metadata: {name: large-1}
  spec: {}
  status:
    capacity: {cpu: "16"}
    conditions: [{type: Ready, status: "True"}]
```

- [ ] **Step 15.8: Create `tests/fixtures/nodes/with_taints.yaml`**

```yaml
apiVersion: v1
kind: List
items:
- apiVersion: v1
  kind: Node
  metadata: {name: cp-1}
  spec:
    taints:
    - {key: "node-role.kubernetes.io/control-plane", effect: NoSchedule}
  status:
    capacity: {cpu: "4"}
    conditions: [{type: Ready, status: "True"}]
- apiVersion: v1
  kind: Node
  metadata: {name: worker-1}
  spec: {}
  status:
    capacity: {cpu: "8"}
    conditions: [{type: Ready, status: "True"}]
- apiVersion: v1
  kind: Node
  metadata: {name: worker-2}
  spec: {}
  status:
    capacity: {cpu: "8"}
    conditions: [{type: Ready, status: "True"}]
```

- [ ] **Step 15.9: Create `tests/fixtures/nodes/unschedulable.yaml`**

```yaml
apiVersion: v1
kind: List
items:
- apiVersion: v1
  kind: Node
  metadata: {name: cordoned-1}
  spec: {unschedulable: true}
  status:
    capacity: {cpu: "8"}
    conditions: [{type: Ready, status: "True"}]
- apiVersion: v1
  kind: Node
  metadata: {name: notready-1}
  spec: {}
  status:
    capacity: {cpu: "8"}
    conditions: [{type: Ready, status: "False"}]
- apiVersion: v1
  kind: Node
  metadata: {name: healthy-1}
  spec: {}
  status:
    capacity: {cpu: "8"}
    conditions: [{type: Ready, status: "True"}]
```

- [ ] **Step 15.10: Create `tests/fixtures/nodes/empty.yaml`**

```yaml
apiVersion: v1
kind: List
items: []
```

- [ ] **Step 15.11: Commit**

```bash
git add tests/fixtures/
git commit -m "Add CLI test fixtures (base manifests and node lists)"
```

---

## Task 16: Golden-file CLI tests

**Files:**
- Create: `tests/test_cli_golden.py`
- Create: `tests/fixtures/expected/minimal_homogeneous.yaml` (generated)
- Create: `tests/fixtures/expected/with_rbac_mixed.yaml` (generated)

The strategy: run the script against fixtures with `--out`, then snapshot the result the **first time** (using `UPDATE_GOLDEN=1`), commit it, and assert equality on subsequent runs.

- [ ] **Step 16.1: Write `tests/test_cli_golden.py`**

```python
import os
import pathlib

import pytest


GOLDEN_PAIRS = [
    # (base, nodes, expected_filename, extra_args)
    ("base/minimal.yaml", "nodes/homogeneous_8core.yaml",
     "minimal_homogeneous.yaml", []),
    ("base/with_rbac.yaml", "nodes/mixed_cores.yaml",
     "with_rbac_mixed.yaml", []),
]


def _read(p: pathlib.Path) -> str:
    return p.read_text() if p.exists() else ""


@pytest.mark.parametrize("base,nodes,expected_name,extra", GOLDEN_PAIRS)
def test_cli_golden(fixtures_dir, run_script, tmp_path,
                    base, nodes, expected_name, extra):
    out = tmp_path / "out.yaml"
    result = run_script(
        "--base", str(fixtures_dir / base),
        "--nodes", str(fixtures_dir / nodes),
        "--out", str(out),
        *extra,
    )
    assert result.returncode == 0, result.stderr

    expected_path = fixtures_dir / "expected" / expected_name
    actual = out.read_text()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(actual)
        return
    assert actual == _read(expected_path), (
        f"output does not match golden file. "
        f"Rerun with UPDATE_GOLDEN=1 to refresh."
    )


def test_cli_error_two_daemonsets(fixtures_dir, run_script):
    r = run_script(
        "--base", str(fixtures_dir / "base/two_daemonsets.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
    )
    assert r.returncode == 1
    assert "contains 2 DaemonSets" in r.stderr


def test_cli_error_multi_container(fixtures_dir, run_script):
    r = run_script(
        "--base", str(fixtures_dir / "base/multi_container.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
    )
    assert r.returncode == 1
    assert "2 containers" in r.stderr


def test_cli_error_init_containers(fixtures_dir, run_script):
    r = run_script(
        "--base", str(fixtures_dir / "base/init_containers.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
    )
    assert r.returncode == 1
    assert "initContainers" in r.stderr


def test_cli_error_taints_non_interactive(fixtures_dir, run_script):
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/with_taints.yaml"),
    )
    assert r.returncode == 1
    assert "tainted nodes detected" in r.stderr


def test_cli_taints_with_explicit_tolerate(fixtures_dir, run_script, tmp_path):
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/with_taints.yaml"),
        "--tolerate", "node-role.kubernetes.io/control-plane:NoSchedule",
        "--out", str(out),
    )
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    # cp-1 has 4 cores; worker-1/2 have 8 cores
    assert "spyderbat-nanoagent-4core" in text
    assert "spyderbat-nanoagent-8core" in text


def test_cli_unschedulable_skipped(fixtures_dir, run_script, tmp_path):
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/unschedulable.yaml"),
        "--out", str(out),
    )
    assert r.returncode == 0, r.stderr
    assert "skipped 2 nodes" in r.stderr
    text = out.read_text()
    assert "healthy-1" in text
    assert "cordoned-1" not in text
    assert "notready-1" not in text


def test_cli_empty_nodes_errors(fixtures_dir, run_script):
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/empty.yaml"),
    )
    assert r.returncode == 1
    assert "no schedulable" in r.stderr
```

- [ ] **Step 16.2: Generate the golden files**

Run: `UPDATE_GOLDEN=1 .venv/bin/pytest tests/test_cli_golden.py -v`
Expected: PASS (golden files written for the first time)

- [ ] **Step 16.3: Manually inspect the generated golden files**

```bash
cat tests/fixtures/expected/minimal_homogeneous.yaml
cat tests/fixtures/expected/with_rbac_mixed.yaml
```

Verify by eye:
- `minimal_homogeneous.yaml`: one DS variant `spyderbat-nanoagent-8core`, all 3 hostnames, resources cpu_req=400m cpu_limit=2000m mem_req=1104Mi mem_limit=2208Mi.
- `with_rbac_mixed.yaml`: ServiceAccount + ClusterRole first, then 3 DS variants `-4core`, `-8core`, `-16core` in that order.

- [ ] **Step 16.4: Run tests again without UPDATE_GOLDEN to confirm parity**

Run: `.venv/bin/pytest tests/test_cli_golden.py -v`
Expected: PASS (all)

- [ ] **Step 16.5: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: PASS (all tests across all files)

- [ ] **Step 16.6: Commit**

```bash
git add tests/test_cli_golden.py tests/fixtures/expected/
git commit -m "Add CLI golden-file integration tests"
```

---

## Task 17: README + manual smoke test checklist

**Files:**
- Create: `README.md`

- [ ] **Step 17.1: Create `README.md`**

````markdown
# DaemonSet Customizer

Generates per-core-count Kubernetes DaemonSet variants from a single base
manifest, with resource limits scaled to each node's CPU capacity and
hostname-based node affinity. Designed for Spyderbat Nano Agent deployments;
see `docs/design.md` for the full spec.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
chmod +x customize_daemonset.py
```

## Usage

Non-interactive (recommended for CI):

```bash
./customize_daemonset.py \
    --base deployment_file.yaml \
    --nodes <(kubectl get nodes -o yaml) \
    --out customized.yaml \
    --tolerate node-role.kubernetes.io/control-plane:NoSchedule
```

Interactive (TTY): omit `--base` / `--nodes` / `--tolerate` to be prompted.

```
./customize_daemonset.py
# -> prompts for paths, then a checkbox of detected taints
```

## How it works

1. Reads the base manifest. It must contain **exactly one DaemonSet** with
   **exactly one container** and **no initContainers**. All other documents
   (ServiceAccounts, ClusterRoles, Deployments, etc.) are passed through
   unchanged.
2. Reads `kubectl get nodes -o yaml`, filters out unschedulable and NotReady
   nodes, and groups remaining nodes by integer CPU core count.
3. For each group, emits one DaemonSet variant with:
   - Name suffix `-{cores}core`
   - `containers[0].resources` scaled to the group's core count
   - `nodeAffinity` matching `kubernetes.io/hostname In [<group hosts>]`
   - Tolerations driven by `--tolerate` flags (replace) or interactive
     selection (augment base)

Formula (CPU coefficient `C` from `--cpu-request-per-core`, default `50m`):

```
cpu_request_millis = cores * C
cpu_limit_millis   = max(2000, cpu_request_millis)
mem_request_Mi     = 1024 + (cores * 10)
mem_limit_Mi       = mem_request_Mi * 2
```

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -v
```

Refresh golden CLI test outputs:

```bash
UPDATE_GOLDEN=1 .venv/bin/pytest tests/test_cli_golden.py
```

## Manual interactive smoke test

The InquirerPy prompts aren't auto-tested. Before releasing, run these by
hand in a real terminal:

1. **Missing-args prompt:** `./customize_daemonset.py` -> prompts for
   `--base` then `--nodes`. Hitting Ctrl-C should exit with code 130.
2. **Taint checkbox:** Use a nodes file that contains a tainted node (e.g.,
   `tests/fixtures/nodes/with_taints.yaml`). Run with `--base` only and
   confirm the checkbox shows each distinct taint with host counts. Toggle
   one selection on/off and confirm with Enter.
3. **Interactive override:** `--interactive` should prompt even when both
   `--base` and `--nodes` are supplied.
4. **Cancel mid-prompt:** Ctrl-C during the taint checkbox should produce a
   clean exit code 130 with no traceback.

## Limits

- Exactly one DaemonSet in the base. Multiple DaemonSets are a hard error.
- Exactly one container in the DaemonSet. Multi-container pods are a hard
  error. (Init containers also a hard error.)
- Resources, node affinity, and tolerations are the only DaemonSet fields
  modified.
- See `docs/design.md` "Out of Scope" for details.
````

- [ ] **Step 17.2: Commit**

```bash
git add README.md
git commit -m "Add README with usage, formula, and interactive smoke test checklist"
```

---

## Task 18: Final integration & smoke test against the user's real files

**Files:** none new

- [ ] **Step 18.1: Run the script against the user-provided `deployment_file.yaml` + `nodes.yaml`**

The repo root has these two files committed by the user. Run a dry inspection (non-interactive) first to surface any taint-related errors:

```bash
.venv/bin/python customize_daemonset.py --base deployment_file.yaml --nodes nodes.yaml --out /tmp/customized.yaml < /dev/null
echo "exit=$?"
```

Expected outcomes:
- If `nodes.yaml` has tainted nodes: exit 1 with "tainted nodes detected: ..." message. Then rerun in interactive mode (a real terminal) and walk the checkbox.
- If no taints: exit 0 with the customized manifest at `/tmp/customized.yaml`. Eyeball:
  - Same Deployment as in the input (pass-through).
  - One DaemonSet variant per distinct core count.
  - Each variant's `nodeAffinity` lists the right hostnames.

- [ ] **Step 18.2: Make the script executable**

```bash
chmod +x customize_daemonset.py
```

- [ ] **Step 18.3: Run the full test suite once more**

Run: `.venv/bin/pytest -v`
Expected: PASS, all tests green.

- [ ] **Step 18.4: Commit if anything changed (executable bit on script)**

```bash
git add customize_daemonset.py
git commit -m "Mark customize_daemonset.py executable" || echo "nothing to commit"
```

---

## Done

The completed deliverable:
- `customize_daemonset.py` (executable single-file script).
- `tests/` with pure-function unit tests + golden-file CLI tests.
- `README.md` with usage and manual interactive smoke test checklist.
- `docs/design.md` (existing) and `docs/plan.md` (this file).
