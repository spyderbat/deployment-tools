# DaemonSet Customizer

Generates per-core-count Kubernetes DaemonSet variants from a single base
manifest, with resource requests/limits scaled to each node's CPU capacity,
hostname-based node affinity, an auto-derived `CLUSTER_NAME`, and optional
proxy settings. Designed for Spyderbat Nano Agent deployments; see
`docs/design.md` for the full spec.

## Quick start (standalone bundle — no install)

The intended way to ship this to a cluster operator. Build a self-contained
tarball, copy it to the target Linux machine, and run it directly — no `pip`,
no `pipx`, no network access required on the target. It only needs a
compatible Python (3.10+).

Build (on a machine with internet + pip):

```bash
make build          # equivalently: ./build-standalone.sh
# -> dist/customize-daemonset.tar.gz
```

Run on the target machine:

```bash
tar xzf customize-daemonset.tar.gz
cd customize-daemonset
./customize-daemonset \
    --base deployment_file.yaml \
    --nodes nodes.yaml \
    --out customized.yaml
```

All Python dependencies are vendored under `_vendor/`; the `./customize-daemonset`
launcher puts them on `PYTHONPATH` and invokes `python3`. See
[Building the standalone bundle](#building-the-standalone-bundle) for details.

## Usage

Non-interactive (recommended for CI):

```bash
customize-daemonset \
    --base deployment_file.yaml \
    --nodes <(kubectl get nodes -o yaml) \
    --out customized.yaml \
    --tolerate node-role.kubernetes.io/control-plane:NoSchedule
```

(In the standalone bundle, substitute `./customize-daemonset`; in a dev venv,
substitute `.venv/bin/python customize_daemonset.py`.)

### Tolerations

- `--tolerate KEY[=VALUE]:EFFECT` (short `-t`, repeatable) — set exactly these
  tolerations.
- `--tolerate-all` (short `-T`) — auto-tolerate every distinct taint detected
  on the input nodes. Works in interactive and non-interactive mode. Mutually
  exclusive with `--tolerate`.

The base manifest must **not** carry its own `spec.template.spec.tolerations`
(this tool manages tolerations) — a base with tolerations is rejected. See
[Limits](#limits).

### Proxy settings

`--proxy URL` sets all four proxy environment variables on the container to the
same value: `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, `https_proxy`.

```bash
customize-daemonset -b template.yaml -n nodes.yaml -o customized.yaml \
    --proxy http://proxy.corp.local:3128
```

### Capture interactive selections into a script

Use `--print-cli` (`-p`) to run the interactive prompts once, then print the
equivalent non-interactive command instead of generating output:

```bash
customize-daemonset -b template.yaml -n nodes.yaml -o customized.yaml --print-cli
# (prompts you for taint selections)
# prints:
# customize-daemonset \
#     --base template.yaml \
#     --nodes nodes.yaml \
#     --out customized.yaml \
#     --tolerate-all
```

The same command is also embedded as a comment at the top of every generated
output file (see [Provenance header](#provenance-header)).

### Interactive (TTY)

Omit `--base` / `--nodes` / `--tolerate` to be prompted:

```
customize-daemonset
# -> prompts for paths, then a checkbox of detected taints
```

## How it works

1. Reads the base manifest. It must contain **exactly one DaemonSet** with
   **exactly one container** and **no initContainers**. All other documents
   (ServiceAccounts, ClusterRoles, Deployments, etc.) are passed through
   unchanged.
2. Reads `kubectl get nodes -o yaml` and groups every node by integer CPU core
   count. No soft filtering — cordoned and NotReady nodes are both included
   (DaemonSet pods schedule on cordoned nodes by default; including NotReady
   nodes keeps the affinity list stable across transient health blips).
3. Derives `CLUSTER_NAME` from the common hostname prefix (see below).
4. For each group, emits one DaemonSet variant with:
   - Name suffix `-{cores}core`
   - `containers[0].resources` scaled to the group's core count
   - `nodeAffinity` matching `kubernetes.io/hostname In [<group hosts>]`
   - Env vars: `CLUSTER_NAME`, plus the four proxy vars if `--proxy` was given
     (added if absent, updated in place if already present)
   - Tolerations from `--tolerate` / `--tolerate-all` / interactive selection

Resource formula (CPU coefficient `C` from `--cpu-request-per-core`, default
`50m`):

```
cpu_request_millis = cores * C
cpu_limit_millis   = max(2000, cpu_request_millis)
mem_request_Mi     = 1024 + (cores * 10)
mem_limit_Mi       = mem_request_Mi * 2
```

### CLUSTER_NAME derivation

`CLUSTER_NAME` is set to the common hostname prefix, taken up to and including
the digits that immediately precede `rb`. The domain (anything after the first
`.`) is ignored. For example, nodes:

```
demo1rbms001.example.local
demo1rbms002.example.local
demo1rbwk004.example.local
```

share the prefix `demo1rb`, so `CLUSTER_NAME` is set to `demo1`. If the
hostnames have no `<digits>rb` prefix, the tool errors out rather than guessing.

### Provenance header

Every generated output file begins with a comment block recording the
equivalent CLI command and the total cluster resource requests/limits, e.g.:

```yaml
# Generated by customize-daemonset — re-using this file as --base will be rejected.
# Command:
#   customize-daemonset \
#       --base template.yaml \
#       --nodes nodes.yaml \
#       --out customized.yaml
# Totals across 7 pods (final, post-scaling):
#   CPU:    requests 3.50 cores, limits 14.00 cores
#   memory: requests 8.05Gi, limits 16.10Gi
```

This header doubles as a guard: feeding a previously generated file back in as
`--base` is detected via this marker and rejected, so the common mistake of
`--base out.yaml` (instead of the original template) fails fast.

### Cluster resource caps (optional)

`--max-total-cpu` and `--max-total-memory` cap the total cluster request:

- `--max-total-cpu 100` — total `cpu.requests` across all pods cannot exceed 100 CPUs
- `--max-total-memory 200Gi` — total `memory.requests` across all pods cannot exceed 200 GiB

If the natural total exceeds a cap, the per-core scaling factor for that
dimension is reduced uniformly across all variants. The 1Gi-per-pod memory base
is never scaled — if the cap is below the cumulative base, the tool errors out.
A CPU cap so tight that it would round any pod's request to `0m` is also
rejected rather than emitting a useless manifest.

## Building the standalone bundle

```bash
make build          # equivalently: ./build-standalone.sh
```

This vendors the runtime dependencies (`ruamel.yaml`, `InquirerPy`, and their
transitive deps) into `dist/customize-daemonset/_vendor/`, copies the script and
a launcher alongside them, and tars the directory into
`dist/customize-daemonset.tar.gz`. Compiled extensions (`*.so`) are stripped so
the bundle is pure-Python and not tied to one Python ABI version.

Set `PYTHON=/path/to/python` to build with a specific interpreter (it just needs
`pip`; the produced bundle runs on plain `python3`):

```bash
make build PYTHON=python3.11
```

## Make targets

```
make dev       Create .venv and install dev + runtime deps
make test      Run the test suite
make golden    Regenerate golden CLI test outputs
make build     Build the standalone tarball (alias: make dist)
make clean     Remove build artifacts (dist/, build/, caches)
```

## Development setup

Set up a local virtualenv (or just run `make dev`):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

During development invoke the script directly as
`.venv/bin/python customize_daemonset.py …`.

## Tests

```bash
make test
# or: .venv/bin/pytest -v
```

Refresh golden CLI test outputs after an intentional output change:

```bash
make golden
# or: UPDATE_GOLDEN=1 .venv/bin/pytest tests/test_cli_golden.py
```

### Manual interactive smoke test

The InquirerPy prompts aren't auto-tested. Before releasing, run these by hand
in a real terminal:

1. **Missing-args prompt:** `./customize_daemonset.py` -> prompts for `--base`
   then `--nodes`. Ctrl-C should exit with code 130.
2. **Taint checkbox:** Use a nodes file with a tainted node (e.g.,
   `tests/fixtures/nodes/with_taints.yaml`). Run with `--base` only and confirm
   the checkbox shows each distinct taint with host counts.
3. **Interactive override:** `--interactive` should prompt even when both
   `--base` and `--nodes` are supplied.
4. **Cancel mid-prompt:** Ctrl-C during the taint checkbox should exit cleanly
   with code 130 and no traceback.

## Limits

- Exactly one DaemonSet in the base. Multiple DaemonSets are a hard error.
- Exactly one container in the DaemonSet. Multi-container pods and init
  containers are hard errors.
- The base manifest must not define its own tolerations — rejected.
- A file previously produced by this tool is rejected as `--base`.
- Resources, node affinity, tolerations, and container env vars (`CLUSTER_NAME`
  and the proxy vars) are the only DaemonSet fields modified.
- See `docs/design.md` "Out of Scope" for details.
