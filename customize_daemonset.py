#!/usr/bin/env python3
"""Generate per-core-count DaemonSet variants from a base manifest and a
`kubectl get nodes -o yaml` file. See docs/design.md for the full spec."""

from dataclasses import dataclass


class CustomizerError(Exception):
    """Base class for all expected, user-facing errors."""


class InputValidationError(CustomizerError):
    """Raised when input files or arguments are malformed or unsupported."""


class InteractiveCancelled(CustomizerError):
    """Raised when the user cancels an interactive prompt (Ctrl-C / EOF)."""


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


@dataclass(frozen=True)
class NodeInfo:
    hostname: str
    cores: int
    taints: tuple  # tuple[Taint, ...]


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


def parse_cpu_per_core_arg(value: str) -> int:
    """Parse the --cpu-request-per-core flag value into integer millicores."""
    return parse_millicores(value)


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
