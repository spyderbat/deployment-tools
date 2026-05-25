#!/usr/bin/env bash
#
# Build a fully self-contained tarball of customize-daemonset.
#
# Produces dist/customize-daemonset.tar.gz containing one directory with the
# script, a launcher, and every Python dependency vendored under _vendor/.
# On the target machine (any compatible Python 3.10+ on Linux), with no pip,
# pipx, or network access:
#
#     tar xzf customize-daemonset.tar.gz
#     cd customize-daemonset
#     ./customize-daemonset --base <manifest> --nodes <nodes> --out <out>
#
# Set PYTHON=/path/to/python to build with a specific interpreter (it only
# needs pip available; the produced bundle runs on plain `python3`).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-python3}"
NAME="customize-daemonset"
STAGE="dist/$NAME"

rm -rf "$STAGE"
mkdir -p "$STAGE/_vendor"

# Vendor runtime dependencies (and their transitive deps) into _vendor/.
"$PYTHON" -m pip install \
    --target "$STAGE/_vendor" \
    --no-compile \
    ruamel.yaml InquirerPy

# Strip compiled extensions and bytecode so the bundle is pure-Python and not
# tied to one Python ABI (ruamel falls back to its pure-Python implementation).
find "$STAGE/_vendor" -name '*.so' -delete
find "$STAGE/_vendor" -name '__pycache__' -type d -prune -exec rm -rf {} +

# The script itself.
cp customize_daemonset.py "$STAGE/customize_daemonset.py"

# Launcher: put the vendored packages on PYTHONPATH, then run the script.
cat > "$STAGE/$NAME" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec env PYTHONPATH="$HERE/_vendor${PYTHONPATH:+:$PYTHONPATH}" \
    python3 "$HERE/customize_daemonset.py" "$@"
LAUNCH
chmod +x "$STAGE/$NAME"

# Package the directory into a tarball.
tar -C dist -czf "dist/$NAME.tar.gz" "$NAME"
echo "built dist/$NAME.tar.gz"
