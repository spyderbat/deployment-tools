#!/usr/bin/env python3
"""Generate per-core-count DaemonSet variants from a base manifest and a
`kubectl get nodes -o yaml` file. See docs/design.md for the full spec."""

import argparse
import copy
import pathlib
import re
import sys
from dataclasses import dataclass

from ruamel.yaml import YAML
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError


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


_MEM_SUFFIXES = {
    "Ki": 1 / 1024,            # 1 KiB = 1/1024 Mi
    "Mi": 1,
    "Gi": 1024,
    "Ti": 1024 * 1024,
    "Pi": 1024 * 1024 * 1024,
    "K":  1000 / (1024 * 1024),
    "M":  1000 * 1000 / (1024 * 1024),
    "G":  1000 * 1000 * 1000 / (1024 * 1024),
    "T":  1000 * 1000 * 1000 * 1000 / (1024 * 1024),
}


def parse_memory_mi(value) -> int:
    """Parse a Kubernetes memory quantity string into an integer number of Mi.

    Accepts: bare integer (bytes), or value with suffix Ki/Mi/Gi/Ti/Pi (binary)
    or K/M/G/T (decimal). Rejects negatives, empty strings, fractional values
    (e.g. "1.5Gi"), and unknown suffixes.

    Bare bytes are converted to Mi via integer division (1024 bytes -> 0 Mi).
    """
    if not isinstance(value, str) or not value.strip():
        raise InputValidationError(f"invalid memory quantity: {value!r}")
    s = value.strip()
    # Try two-letter suffixes first (Ki, Mi, etc.) before one-letter (K, M, ...)
    for suffix in sorted(_MEM_SUFFIXES.keys(), key=len, reverse=True):
        if s.endswith(suffix):
            body = s[:-len(suffix)]
            try:
                n = int(body)
            except ValueError:
                raise InputValidationError(
                    f"invalid memory quantity {value!r}: integer body required before {suffix!r}"
                )
            if n < 0:
                raise InputValidationError(
                    f"invalid memory quantity {value!r}: negative")
            return int(n * _MEM_SUFFIXES[suffix])
    # Bare integer (bytes)
    try:
        n = int(s)
    except ValueError:
        raise InputValidationError(f"invalid memory quantity: {value!r}")
    if n < 0:
        raise InputValidationError(f"invalid memory quantity {value!r}: negative")
    return n // (1024 * 1024)


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


def compute_resources(*, cores: int, cpu_per_core_millis: int,
                      cpu_scale: float = 1.0,
                      mem_per_core_scale: float = 1.0) -> dict:
    """Compute resources.requests and resources.limits for a node group.

    Formula (from docs/design.md §4.2 with optional cluster-cap scaling):
        cpu_request_millis = round(cpu_scale * cores * cpu_per_core_millis)
        cpu_limit_millis   = max(2000, cpu_request_millis)
        mem_request_mi     = 1024 + round(mem_per_core_scale * cores * 10)
        mem_limit_mi       = mem_request_mi * 2
    """
    if cores <= 0:
        raise InputValidationError(f"cores must be positive, got {cores}")
    cpu_request_millis = round(cpu_scale * cores * cpu_per_core_millis)
    cpu_limit_millis = max(2000, cpu_request_millis)
    mem_request_mi = 1024 + round(mem_per_core_scale * cores * 10)
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


def compute_scale_factors(*, groups, cpu_per_core_millis,
                          max_total_cpu_millis, max_total_mem_mi):
    """Determine CPU and memory scale factors needed to fit cluster caps.

    Args:
        groups: dict[int, list[str]] mapping cores -> hostnames.
        cpu_per_core_millis: int millicores per core (from --cpu-request-per-core).
        max_total_cpu_millis: int total CPU request cap in millicores, or None.
        max_total_mem_mi: int total memory request cap in Mi, or None.

    Returns:
        (cpu_scale: float, mem_per_core_scale: float) — each in (0, 1].

    Raises:
        InputValidationError if the memory cap is below the per-pod base floor.
    """
    total_cpu_at_1 = sum(cores * cpu_per_core_millis * len(hosts)
                         for cores, hosts in groups.items())
    base_mem_total = sum(1024 * len(hosts) for cores, hosts in groups.items())
    scalable_mem_total = sum(cores * 10 * len(hosts)
                             for cores, hosts in groups.items())

    if max_total_cpu_millis is not None and total_cpu_at_1 > max_total_cpu_millis:
        f_cpu = max_total_cpu_millis / total_cpu_at_1
    else:
        f_cpu = 1.0

    if max_total_mem_mi is not None:
        if base_mem_total > max_total_mem_mi:
            total_pods = sum(len(hosts) for hosts in groups.values())
            raise InputValidationError(
                f"memory cap of {max_total_mem_mi}Mi is below the "
                f"{base_mem_total}Mi required for the per-pod base "
                f"(1Gi/pod × {total_pods} pods)"
            )
        if (base_mem_total + scalable_mem_total) > max_total_mem_mi:
            f_mem = (max_total_mem_mi - base_mem_total) / scalable_mem_total
        else:
            f_mem = 1.0
    else:
        f_mem = 1.0

    return f_cpu, f_mem


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


def build_variant(*, base, cores, hostnames, cpu_per_core_millis, final_tolerations,
                  cpu_scale: float = 1.0,
                  mem_per_core_scale: float = 1.0):
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
        cores=cores, cpu_per_core_millis=cpu_per_core_millis,
        cpu_scale=cpu_scale, mem_per_core_scale=mem_per_core_scale,
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


_APIVERSION_AT_BOL = re.compile(r"^apiVersion:", re.MULTILINE)


_DOC_SEPARATOR = re.compile(r"^---[ \t]*$", re.MULTILINE)


def _inject_doc_separators(text: str) -> str:
    """Insert `---\n` before every top-level `apiVersion:` line that is
    not the first one and is not already preceded by a `---` separator,
    so multi-doc YAML files that lack `---` separators (e.g. `helm template`
    output) parse correctly under ruamel.yaml."""
    matches = list(_APIVERSION_AT_BOL.finditer(text))
    if len(matches) <= 1:
        return text
    # Walk backwards so earlier-position offsets remain valid.
    for m in reversed(matches[1:]):
        # Check if the text before this apiVersion: already ends with a --- line.
        preceding = text[:m.start()].rstrip(" \t\n")
        if preceding.endswith("---") or _DOC_SEPARATOR.search(text[:m.start()]):
            # Only inject if no --- is already directly before this apiVersion:.
            # More precisely: check if the last non-blank line before this match
            # is already a --- separator.
            lines_before = text[:m.start()].rstrip("\n").splitlines()
            last_nonempty = next(
                (l.rstrip() for l in reversed(lines_before) if l.strip()),
                ""
            )
            if last_nonempty == "---":
                continue
        text = text[:m.start()] + "---\n" + text[m.start():]
    return text


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
        text = _inject_doc_separators(path.read_text())
        return list(_yaml().load_all(text))
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
    p.add_argument("--max-total-cpu", default=None,
                   help="Cap on total cluster CPU requests (e.g., '100', '50000m'). "
                        "If totals exceed this, CPU per-core scale is reduced uniformly.")
    p.add_argument("--max-total-memory", default=None,
                   help="Cap on total cluster memory requests (e.g., '200Gi'). "
                        "If totals exceed this, the memory per-core term is reduced uniformly.")
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

    max_total_cpu_millis = (parse_millicores(args.max_total_cpu)
                            if args.max_total_cpu else None)
    max_total_mem_mi = (parse_memory_mi(args.max_total_memory)
                        if args.max_total_memory else None)

    f_cpu, f_mem = compute_scale_factors(
        groups=groups,
        cpu_per_core_millis=cpu_per_core_millis,
        max_total_cpu_millis=max_total_cpu_millis,
        max_total_mem_mi=max_total_mem_mi,
    )

    if f_cpu < 1.0:
        total_cpu = sum(c * cpu_per_core_millis * len(h)
                        for c, h in groups.items())
        scaled_per_core = round(f_cpu * cpu_per_core_millis)
        print(
            f"scaled CPU per-core factor: {cpu_per_core_millis}m × {f_cpu:.4f} "
            f"→ {scaled_per_core}m (total would have been {total_cpu/1000:.1f} CPUs; "
            f"cap {max_total_cpu_millis/1000:.1f} CPUs)",
            file=sys.stderr,
        )

    if f_mem < 1.0:
        scalable_total = sum(c * 10 * len(h) for c, h in groups.items())
        scaled_per_core = round(f_mem * 10)
        base_total = sum(1024 * len(h) for c, h in groups.items())
        total_at_1 = base_total + scalable_total
        print(
            f"scaled memory per-core factor: 10Mi × {f_mem:.4f} "
            f"→ {scaled_per_core}Mi (total would have been "
            f"{total_at_1/1024:.2f}Gi; cap {max_total_mem_mi/1024:.2f}Gi)",
            file=sys.stderr,
        )

    variants = [
        build_variant(
            base=ds_doc,
            cores=cores,
            hostnames=hostnames,
            cpu_per_core_millis=cpu_per_core_millis,
            final_tolerations=final_tolerations,
            cpu_scale=f_cpu,
            mem_per_core_scale=f_mem,
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
    """Show a checkbox prompt for each distinct taint; return set[Taint] selected.

    Uses string labels (not Taint dataclasses) as Choice values, because
    InquirerPy 0.3.4 returns the full Choice as a dict for non-primitive
    values, which would break `set(...)` aggregation. The label-to-Taint
    map is rebuilt locally for lookup.
    """
    inquirer, Choice, _PathValidator = _import_inquirer()
    taint_by_label = {}
    choices = []
    for t, hosts in taint_map.items():
        label = (f"{t.key}{('=' + t.value) if t.value else ''}:{t.effect}"
                 f"  ({len(hosts)} nodes)")
        taint_by_label[label] = t
        choices.append(Choice(value=label, name=label))
    try:
        selected = inquirer.checkbox(
            message="Select taints to tolerate "
                    "(nodes with unselected taints will be excluded):",
            choices=choices,
            instruction="(space to toggle, enter to confirm)",
        ).execute()
    except KeyboardInterrupt as e:
        raise InteractiveCancelled("cancelled") from e
    return {taint_by_label[label] for label in selected}


if __name__ == "__main__":
    sys.exit(main())
