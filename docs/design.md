# DaemonSet Customizer — Design Spec

**Date:** 2026-05-20
**Status:** Approved (pending review of this written spec)

## 1. Purpose

A single-file Python CLI that takes a base Kubernetes manifest (containing
exactly one DaemonSet plus any number of supporting non-DaemonSet objects)
together with the YAML output of `kubectl get nodes -o yaml`, and emits a
combined multi-document YAML stream containing:

- All non-DaemonSet objects from the base manifest, passed through unchanged.
- One DaemonSet variant per distinct CPU core count among the input nodes,
  with resource requests/limits scaled to that core count and node affinity
  scoped to the hostnames that share that core count.

The primary use case is generating per-cluster Spyderbat Nano Agent
deployments tuned to the cluster's actual node sizes.

## 2. Invocation

### CLI signature

```
customize_daemonset.py \
    --base <path>                          # base manifest YAML (required)
    --nodes <path>                         # kubectl get nodes -o yaml output (required)
    [--out <path>]                         # output file; default: stdout
    [--cpu-request-per-core <quantity>]    # default: 50m
    [--tolerate <key>=<value>:<effect>]    # repeatable; replaces base tolerations
    [--interactive]                        # force prompt mode even when all args present
```

### Mode selection

- If stdin is a TTY **and** any required input is missing, fall into
  `InquirerPy` prompts for the missing pieces. CLI-provided values skip their
  respective prompts.
- If stdin is **not** a TTY (e.g., piped, CI), missing required inputs cause a
  hard error and non-zero exit.
- `--interactive` forces prompts even when everything is supplied (useful for
  review-before-run).

### Exit codes

| Code | Meaning |
|------|---------|
| 0    | Success |
| 1    | Input validation error (missing file, malformed YAML, >1 DaemonSet, >1 container, no nodes, taints + non-interactive without `--tolerate`, etc.) |
| 2    | Argument/usage error (argparse default) |
| 130  | User cancellation during an interactive prompt (Ctrl-C / EOF) |

### Layout

- Single executable Python file: `customize_daemonset.py` with a
  `#!/usr/bin/env python3` shebang.
- Dependencies: `ruamel.yaml` (round-trip-friendly YAML), `InquirerPy`
  (interactive prompts). Tracked in `requirements.txt`. Standard library
  for everything else.
- Optional `pyproject.toml` exposing a `customize-daemonset` console-script
  entry point. Single-file layout is preserved regardless.

## 3. Data Flow

The script runs as a straight pipeline. `main()` wires together small helper
functions defined in the same file. Each helper is importable so tests can
exercise it directly.

```
1. parse_args()                       -> argparse.Namespace
2. resolve_inputs_interactively()     -> fills missing required args via InquirerPy
                                         (only if TTY; otherwise main() already errored out)
3. base_docs    = load_yaml_docs(args.base)         # list of parsed YAML documents
4. nodes_doc    = load_yaml_doc(args.nodes)         # the kubectl-get-nodes Node-list doc
5. ds_doc, passthrough_docs = split_daemonset(base_docs)
       # validates: exactly one DaemonSet, exactly one container, no initContainers
6. node_infos  = extract_nodes(nodes_doc)
       # NodeInfo = (hostname, cores, taints). Skips unschedulable / NotReady.
7. final_tolerations, usable_node_infos = resolve_tolerations(args, node_infos)
       # Determines the final toleration list (per Section 4.4 table) and
       # filters out any node carrying a taint not matched by the final
       # toleration set. See Section 4 (Transformations).
8. groups       = group_by_cores(usable_node_infos)
       # dict[int, list[str]] -- cores -> sorted hostnames
       # validates: at least one usable node
9. variants     = [build_variant(ds_doc, cores, hostnames, args, final_tolerations)
                   for cores, hostnames in sorted(groups.items())]
       # each variant is a deep-copied DaemonSet with name suffix, resources,
       # nodeAffinity, and tolerations applied
10. write_output(args.out, passthrough_docs + variants)
       # multi-doc YAML stream; passthrough docs first, then variants in
       # ascending core-count order
```

### Invariants

- The base manifest is read **once** and deep-copied before each variant
  transformation. The original document object is never mutated.
- Pass-through documents (non-DaemonSet objects) appear in the output in the
  **same order** they appeared in the input. Formatting and comments are
  preserved by `ruamel.yaml`.
- DaemonSet variants follow the pass-through documents, ordered by ascending
  core count. Hostnames within each variant's `matchExpressions` are sorted
  alphabetically.

## 4. Transformations

For each `(cores, hostnames)` group, `build_variant` operates on a deep copy
of the base DaemonSet document and applies the following changes.

### 4.1 Name suffix

- `metadata.name` -> `{base_name}-{cores}core` (e.g.,
  `spyderbat-nanoagent-8core`).
- `metadata.labels`, `spec.selector.matchLabels`, and
  `spec.template.metadata.labels` are **not** rewritten. The base manifest's
  label scheme is preserved so RBAC and Services in the pass-through documents
  still match.

### 4.2 Resources

Applied to `spec.template.spec.containers[0]`. The CPU coefficient `C` comes
from `--cpu-request-per-core` (default `50m`).

```
cpu_request_millis = cores * parse_millicores(C)         # int millicores
cpu_limit_millis   = max(2000, cpu_request_millis)
mem_request_mi     = 1024 + (cores * 10)                 # int Mi
mem_limit_mi       = mem_request_mi * 2
```

Rendered as Kubernetes quantity strings, e.g. `500m`, `2000m`, `1104Mi`,
`2208Mi`.

The container's `resources.requests` and `resources.limits` are **replaced
wholesale**; any pre-existing values in the base manifest are overwritten.

### 4.3 nodeAffinity

`spec.template.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution`
is **replaced** with:

```yaml
nodeSelectorTerms:
- matchExpressions:
  - key: kubernetes.io/hostname
    operator: In
    values: [<sorted hostnames for this group>]
```

- Any pre-existing `nodeAffinity` is overwritten.
- `preferredDuringSchedulingIgnoredDuringExecution`, `podAffinity`, and
  `podAntiAffinity` (if present) are left untouched.
- `nodeSelector` on the pod spec (if present in the base) is left untouched,
  because using it alongside hostname affinity is harmless and the base may
  rely on it for OS/arch selection.

### 4.4 Tolerations

Each rendered toleration takes the form:

```yaml
- key: <key>
  operator: Equal
  value: <value>
  effect: <effect>
```

When `--tolerate` is supplied with an empty `value` (`--tolerate key:effect`),
the operator becomes `Exists` and the `value` field is omitted.

Behavior depends on whether `--tolerate` was supplied and whether the nodes
file contains any taints:

| `--tolerate` supplied? | Taints in nodes? | TTY? | Behavior |
|---|---|---|---|
| Yes (>=1) | n/a | n/a | Final tolerations = the CLI values only. The base manifest's `tolerations` are **discarded**. Nodes whose taints are not **all** matched by some final toleration are excluded from grouping. |
| No | No | n/a | Final tolerations = the base manifest's `tolerations` unchanged. No exclusions. |
| No | Yes | Yes (TTY) | InquirerPy **checkbox** prompt lists each distinct `(key, value, effect)` taint with the count of nodes carrying it. Final tolerations = the base manifest's `tolerations` **plus** one toleration per selected taint (appended, no dedupe across the two sets). Nodes carrying any **unselected** taint are excluded from grouping. |
| No | Yes | No (piped) | **Hard error**, listing the distinct taints. Tells the user to rerun interactively or supply `--tolerate` flags explicitly. |

The rule is: an **explicit** `--tolerate` set replaces what's in the base manifest; an **interactive** selection augments it. This lets operators who care take full control, while interactive users don't accidentally lose tolerations they didn't know were there.

A taint `T` "matches" a toleration `R` when:
- `R.key == T.key`, AND
- `R.effect == T.effect` (or `R.effect` is empty, meaning match-all-effects), AND
- One of:
  - `R.operator == "Exists"`, OR
  - `R.operator == "Equal"` (default) AND `R.value == T.value`.

A node is **excluded from grouping** if it carries any taint not matched by
the final toleration set. Excluded nodes are reported on stderr (see Section 5,
Soft conditions).

## 5. Error Handling

The script defines a small set of named exceptions, all subclassing
`CustomizerError`. `main()` wraps the pipeline in
`try/except CustomizerError`, prints a clean message to stderr (no
traceback), and exits with code `1`. Unexpected exceptions still produce a
traceback so bugs are not silently swallowed.

### Hard errors (exit 1)

| Condition | Message shape |
|---|---|
| `--base` file missing/unreadable | `base manifest not found: <path>` |
| `--nodes` file missing/unreadable | `nodes file not found: <path>` |
| YAML parse error in either file | `failed to parse <path>: <ruamel error>` |
| Zero DaemonSets in base | `base manifest contains no DaemonSet` |
| Multiple DaemonSets in base | `base manifest contains N DaemonSets, expected 1: <names>` |
| DaemonSet has 0 or >1 containers | `DaemonSet <name> has N containers, expected 1` |
| DaemonSet has any initContainers | `DaemonSet <name> has initContainers, not supported` |
| Nodes file has no `items` / wrong Kind | `nodes file is not a kubectl get nodes -o yaml output` |
| Zero usable nodes after filtering | `no schedulable, ready nodes found in <path>` |
| Node missing hostname or `status.capacity.cpu` | `node <index>: missing required fields: <list>` |
| `status.capacity.cpu` not parseable as integer cores | `node <name>: cpu capacity "<value>" is not parseable` |
| `--cpu-request-per-core` not parseable as quantity | `invalid --cpu-request-per-core "<value>": expected e.g. 50m, 1, 250m` |
| `--tolerate` value malformed | `invalid --tolerate "<value>": expected key=value:effect or key:effect` |
| Non-interactive + taints + no `--tolerate` | `tainted nodes detected: <list with counts>. Rerun interactively or supply --tolerate.` |
| `--out` parent directory missing | `output directory does not exist: <path>` |

### Soft conditions (stderr warning, proceed)

- Nodes file contains nodes skipped due to unschedulable/NotReady:
  `skipped N nodes (unschedulable or NotReady): <names>`
- `--tolerate` supplied AND tainted nodes excluded because no supplied
  toleration matched: `excluded N nodes due to unmatched taints: <names>`

### Interactive-mode behavior

- InquirerPy prompts that fail validation (e.g., bad path) re-prompt with the
  previous answer pre-filled.
- Ctrl-C or EOF during a prompt exits cleanly with code `130` (standard SIGINT
  convention).

### No silent fallbacks

The tool never invents defaults for the base manifest path, nodes file path,
or namespace. Either it is supplied via flag, interactively prompted for, or
it errors.

## 6. Input Parsing Details

### 6.1 Nodes file

The nodes file is the YAML output of `kubectl get nodes -o yaml`. It is a
single document with `apiVersion: v1`, `kind: List`, containing `items[]`,
each of which is a `Node` object. For each node, the tool extracts:

- **Hostname**: `metadata.name` (which equals the `kubernetes.io/hostname`
  label value for normal clusters).
- **Core count**: `status.capacity.cpu`. Accepted forms: integer string
  (`"8"`), millicore string (`"8000m"`), or decimal string that reduces to
  a whole integer (`"8.0"`, `"8000m"` -> `8`). Values that do not reduce to
  a whole integer (e.g., `"7.5"`, `"7500m"`) are an error — DaemonSet
  pods are scheduled per-node and need integer core counts to drive the
  formula sensibly.
- **Taints**: `spec.taints[]` (each having `key`, `value`, `effect`). May be
  absent.
- **Schedulability**: `spec.unschedulable` (bool) and the `Ready` condition
  under `status.conditions[]`.

Nodes where `spec.unschedulable == true` or whose `Ready` condition is not
`"True"` are filtered out **before** taint analysis and grouping.

### 6.2 Base manifest

The base manifest is a multi-document YAML stream. Each document is examined:

- Documents with `kind: DaemonSet` are collected for variant generation.
  Exactly one is required (Section 5 error otherwise).
- All other documents are pass-through.

`ruamel.yaml` is configured for round-trip mode so pass-through documents
preserve comments, key ordering, and quoting style.

### 6.3 `--cpu-request-per-core` parsing

Accepted forms:
- Bare integer (`"1"`, `"2"`) — interpreted as whole cores.
- Millicore suffix (`"50m"`, `"100m"`) — interpreted as millicores.
- Decimal (`"0.5"`) — interpreted as fractional cores.

The parsed value is stored internally as integer millicores.

### 6.4 `--tolerate` parsing

Accepted forms:
- `key=value:effect` -> operator `Equal`.
- `key:effect` -> operator `Exists`, no value.

`effect` must be one of `NoSchedule`, `PreferNoSchedule`, `NoExecute`. Any
other value is a parse error.

## 7. Testing

Test framework: `pytest`. Test deps tracked in `requirements-dev.txt`.

### Directory layout

```
tests/
  test_resources.py       # the formula
  test_nodes.py           # node parsing + filtering
  test_taints.py          # taint extraction, matching, deduplication
  test_grouping.py        # group-by-cores, sorted output
  test_transform.py       # build_variant -- name, resources, affinity, tolerations
  test_cli_golden.py      # full pipeline via subprocess against fixture inputs
  fixtures/
    base/
      minimal.yaml             # one DS, no extras
      with_rbac.yaml           # DS + ServiceAccount + ClusterRole + ClusterRoleBinding
      two_daemonsets.yaml      # error case
      multi_container.yaml     # error case
    nodes/
      homogeneous_8core.yaml   # 3 nodes, all 8 cores, no taints
      mixed_cores.yaml         # 2x4-core, 3x8-core, 1x16-core
      with_taints.yaml         # control-plane + custom taints
      unschedulable.yaml       # some cordoned, some NotReady
    expected/
      <fixture-name>.yaml      # golden output for each scenario
```

### Unit test coverage

- **Formula:** 1 core, 40 cores (boundary where `cpu_request` crosses
  2000m), 0 cores (error), fractional `--cpu-request-per-core` like `25m`
  and `1`.
- **Node parsing:** `capacity.cpu` as `"8"`, `"8000m"`, `"8.0"`, missing
  field, bad value.
- **Filtering:** unschedulable, NotReady, both — each drops the node.
- **Taint extraction & matching:** nodes with no taints; mixed taints;
  identical taints across nodes (dedupe by `(key, value, effect)`);
  `Equal` vs `Exists` matching; empty-effect tolerations matching all effects.
- **Grouping:** empty input -> error; single group; multiple groups; sorted
  output by ascending cores; sorted hostnames within a group.
- **Toleration parsing:** `key=value:effect`, `key:effect` (Exists operator),
  malformed inputs (missing colon, bad effect, etc.).
- **Transform:** deep-copy isolation (mutating one variant does not affect
  another or the base); name suffix; nodeAffinity replacement preserves
  other affinity types; tolerations replaced wholesale.

### Golden-file tests

- Run the script via `subprocess.run` with `--base`, `--nodes`, `--out` for
  each fixture combination.
- Diff actual vs `fixtures/expected/*.yaml`.
- Error scenarios assert a specific exit code and a stderr substring.
- `UPDATE_GOLDEN=1` env var rewrites the expected files (standard pattern for
  golden tests).

### Not covered by automation

- Interactive prompts (`InquirerPy`). Manual smoke tests via a checklist in
  `README.md`.
- The `--interactive` flag is exercised manually.

## 8. Out of Scope

The following are explicitly **not** part of this tool:

- Templating customizations beyond resources, node affinity, and tolerations
  (e.g., image overrides, env vars, sidecar injection).
- Handling base manifests with more than one DaemonSet or with multi-container
  pods. Both are hard errors.
- Applying the generated manifest to a cluster. The tool's output is meant to
  be piped to `kubectl apply -f -` by the operator.
- Auto-detecting node taint changes between runs; the tool is a one-shot
  generator.
- Per-group resource formula overrides or per-group tolerations.
