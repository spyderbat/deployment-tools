# Development

Notes for working on `customize-daemonset` itself. For using the tool, see the
[README](../README.md). The full design is in [design.md](design.md).

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

