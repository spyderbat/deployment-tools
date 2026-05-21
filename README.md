# DaemonSet Customizer

Generates per-core-count Kubernetes DaemonSet variants from a single base
manifest, with resource limits scaled to each node's CPU capacity and
hostname-based node affinity. Designed for Spyderbat Nano Agent deployments;
see `docs/design.md` for the full spec.

## Install

Recommended — install as a user-level CLI via `pipx`:

```bash
pipx install .
```

This makes `customize-daemonset` available from anywhere, isolated in its own
virtualenv, with no shell activation required. If you don't have `pipx`,
install it first (`apt install pipx`, `brew install pipx`, or
`pip install --user pipx`).

For development (running the test suite), set up a local venv instead:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

## Usage

Non-interactive (recommended for CI):

```bash
customize-daemonset \
    --base deployment_file.yaml \
    --nodes <(kubectl get nodes -o yaml) \
    --out customized.yaml \
    --tolerate node-role.kubernetes.io/control-plane:NoSchedule
```

Interactive (TTY): omit `--base` / `--nodes` / `--tolerate` to be prompted.

```
customize-daemonset
# -> prompts for paths, then a checkbox of detected taints
```

If you installed via the development venv (not pipx), substitute
`.venv/bin/python customize_daemonset.py` for `customize-daemonset` in the
commands above.

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

### Cluster resource caps (optional)

`--max-total-cpu` and `--max-total-memory` cap the total cluster request:

- `--max-total-cpu 100` — total `cpu.requests` across all pods cannot exceed 100 CPUs
- `--max-total-memory 200Gi` — total `memory.requests` across all pods cannot exceed 200 GiB

If the natural total exceeds either cap, the per-core scaling factor for that
dimension is reduced uniformly across all variants. The 1Gi-per-pod memory
base is never scaled — if the cap is below the cumulative base, the tool
errors out.

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
