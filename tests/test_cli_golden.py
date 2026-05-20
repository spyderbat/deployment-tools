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
