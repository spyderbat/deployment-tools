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


def _strip_header(text: str) -> str:
    """Drop the leading generated-by comment block, leaving the YAML body.

    The output header is a run of '#' comment lines at the very top; the body
    begins at the first document separator. Golden files store body only.
    """
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    return "".join(lines[i:])


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
    actual = _strip_header(out.read_text())
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


def test_cli_all_nodes_included_regardless_of_state(fixtures_dir, run_script, tmp_path):
    """All nodes are included regardless of cordoned/NotReady state."""
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/unschedulable.yaml"),
        "--out", str(out),
    )
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    assert "healthy-1" in text
    assert "cordoned-1" in text
    assert "notready-1" in text


def test_cli_empty_nodes_errors(fixtures_dir, run_script):
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/empty.yaml"),
    )
    assert r.returncode == 1
    assert "no schedulable" in r.stderr


def test_cli_max_total_cpu_scales(fixtures_dir, run_script, tmp_path):
    """CPU cap below the natural total triggers scaling and a stderr message."""
    out = tmp_path / "out.yaml"
    # 3 nodes × 8 cores × 50m = 1200m. Cap at 600m → f_cpu = 0.5.
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--max-total-cpu", "600m",
        "--out", str(out),
    )
    assert r.returncode == 0, r.stderr
    assert "scaled CPU per-core factor" in r.stderr
    text = out.read_text()
    # Scaled cpu request per pod: round(0.5 * 8 * 50) = 200m
    assert "cpu: 200m" in text


def test_cli_max_total_memory_scales(fixtures_dir, run_script, tmp_path):
    """Memory cap below the natural scalable total triggers scaling."""
    out = tmp_path / "out.yaml"
    # 3 pods × 1024 Mi base = 3072 Mi base + 3×8×10 = 240 Mi scalable = 3312 Mi natural total
    # Cap at 3192 Mi → f_mem = (3192-3072)/240 = 0.5
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--max-total-memory", "3192Mi",
        "--out", str(out),
    )
    assert r.returncode == 0, r.stderr
    assert "scaled memory per-core factor" in r.stderr
    text = out.read_text()
    # Per-pod mem request: 1024 + round(0.5 * 8 * 10) = 1064 Mi
    assert "memory: 1064Mi" in text


def test_cli_mem_cap_below_base_errors(fixtures_dir, run_script):
    """Memory cap below the 1Gi-per-pod base must error clearly."""
    # 3 pods × 1024 Mi base = 3072 Mi. Cap at 2Gi (=2048 Mi) → unsatisfiable.
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--max-total-memory", "2Gi",
    )
    assert r.returncode == 1
    assert "below" in r.stderr
    assert "per-pod base" in r.stderr


def test_cli_caps_unset_no_change(fixtures_dir, run_script, tmp_path):
    """Without --max-total-* flags, output is identical to the existing golden."""
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--out", str(out),
    )
    assert r.returncode == 0
    expected = (fixtures_dir / "expected/minimal_homogeneous.yaml").read_text()
    assert _strip_header(out.read_text()) == expected


def test_cli_tolerate_all_with_taints(fixtures_dir, run_script, tmp_path):
    """--tolerate-all auto-tolerates everything; no node should be excluded."""
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/with_taints.yaml"),
        "--tolerate-all",
        "--out", str(out),
    )
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    # cp-1 has 4 cores; worker-1/2 have 8 cores
    assert "spyderbat-nanoagent-4core" in text
    assert "spyderbat-nanoagent-8core" in text
    # The control-plane taint should appear as a toleration in every variant
    assert "node-role.kubernetes.io/control-plane" in text


def test_cli_tolerate_all_no_taints(fixtures_dir, run_script, tmp_path):
    """--tolerate-all is a no-op when no taints are present."""
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--tolerate-all",
        "--out", str(out),
    )
    assert r.returncode == 0, r.stderr
    # Output should be identical to the golden for this fixture
    expected = (fixtures_dir / "expected/minimal_homogeneous.yaml").read_text()
    assert _strip_header(out.read_text()) == expected


def test_cli_tolerate_and_tolerate_all_are_mutually_exclusive(fixtures_dir, run_script):
    """Passing both --tolerate and --tolerate-all is a usage error (exit code 2)."""
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/with_taints.yaml"),
        "--tolerate", "foo:NoSchedule",
        "--tolerate-all",
    )
    assert r.returncode == 2
    assert "not allowed" in r.stderr.lower() or "argument" in r.stderr.lower()


def test_cli_short_options_work(fixtures_dir, run_script, tmp_path):
    """Short -b/-n/-o flags are equivalent to their long forms."""
    out = tmp_path / "out.yaml"
    r = run_script(
        "-b", str(fixtures_dir / "base/minimal.yaml"),
        "-n", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "-o", str(out),
    )
    assert r.returncode == 0, r.stderr
    expected = (fixtures_dir / "expected/minimal_homogeneous.yaml").read_text()
    assert _strip_header(out.read_text()) == expected


def test_cli_print_cli_no_taints(fixtures_dir, run_script):
    """--print-cli with no taints prints the equivalent non-interactive command."""
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--out", "result.yaml",
        "--print-cli",
    )
    assert r.returncode == 0, r.stderr
    assert "customize-daemonset" in r.stdout
    assert "--base" in r.stdout
    assert "--nodes" in r.stdout
    assert "--out" in r.stdout
    assert "result.yaml" in r.stdout
    # Should NOT have actually written any output file
    import pathlib
    assert not pathlib.Path("result.yaml").exists()


def test_cli_rejects_base_with_existing_tolerations(fixtures_dir, run_script, tmp_path):
    """A base manifest whose DaemonSet already defines tolerations is rejected."""
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/with_tolerations.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--out", str(out),
    )
    assert r.returncode == 1
    assert "tolerations" in r.stderr.lower()
    assert not out.exists()


def test_cli_output_has_generated_header(fixtures_dir, run_script, tmp_path):
    """Output starts with a comment header carrying the marker, command, and totals."""
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--out", str(out),
    )
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    assert text.startswith("# Generated by customize-daemonset")
    assert "Command:" in text
    assert "Totals across" in text
    assert "cores" in text and "Gi" in text


def test_cli_rejects_prior_output_as_base(fixtures_dir, run_script, tmp_path):
    """Feeding a previous run's output back in as --base is rejected (the out.yml mistake)."""
    out = tmp_path / "out.yaml"
    r1 = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--out", str(out),
    )
    assert r1.returncode == 0, r1.stderr

    out2 = tmp_path / "out2.yaml"
    r2 = run_script(
        "--base", str(out),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--out", str(out2),
    )
    assert r2.returncode == 1
    assert "previous" in r2.stderr.lower() or "generated" in r2.stderr.lower()
    assert not out2.exists()


def test_cli_rejects_cpu_cap_that_zeroes_request(fixtures_dir, run_script, tmp_path):
    """A CPU cap so low it rounds a pod request to 0m is rejected, not silently emitted."""
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--max-total-cpu", "0",
        "--out", str(out),
    )
    assert r.returncode == 1
    assert "0m" in r.stderr
    assert not out.exists()


def test_cli_print_cli_with_tolerate_all(fixtures_dir, run_script):
    """--print-cli preserves an explicit --tolerate-all in the output."""
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/with_taints.yaml"),
        "--tolerate-all",
        "--print-cli",
    )
    assert r.returncode == 0, r.stderr
    assert "--tolerate-all" in r.stdout


def test_cli_sets_cluster_name_env(fixtures_dir, run_script, tmp_path):
    """CLUSTER_NAME is derived from the hostname prefix and set in every variant."""
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--out", str(out),
    )
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    assert "name: CLUSTER_NAME" in text
    assert "value: demo1" in text


def test_cli_cluster_name_no_match_errors(fixtures_dir, run_script, tmp_path):
    """Hostnames without a '<digits>rb' prefix are a hard error, not a guess."""
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/bad_hostnames.yaml"),
        "--out", str(out),
    )
    assert r.returncode == 1
    assert "CLUSTER_NAME" in r.stderr
    assert not out.exists()


def test_cli_proxy_sets_all_four_vars(fixtures_dir, run_script, tmp_path):
    """--proxy sets HTTP_PROXY/HTTPS_PROXY and their lowercase variants."""
    out = tmp_path / "out.yaml"
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "--proxy", "http://proxy.local:3128",
        "--out", str(out),
    )
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert f"name: {var}" in text
    assert text.count("http://proxy.local:3128") >= 4


def test_cli_print_cli_with_explicit_tolerate(fixtures_dir, run_script):
    """--print-cli preserves explicit --tolerate flags verbatim."""
    r = run_script(
        "--base", str(fixtures_dir / "base/minimal.yaml"),
        "--nodes", str(fixtures_dir / "nodes/with_taints.yaml"),
        "--tolerate", "node-role.kubernetes.io/control-plane:NoSchedule",
        "--print-cli",
    )
    assert r.returncode == 0, r.stderr
    assert "--tolerate" in r.stdout
    assert "node-role.kubernetes.io/control-plane:NoSchedule" in r.stdout


def test_cli_print_cli_short_flag(fixtures_dir, run_script):
    """-p is the short form of --print-cli."""
    r = run_script(
        "-b", str(fixtures_dir / "base/minimal.yaml"),
        "-n", str(fixtures_dir / "nodes/homogeneous_8core.yaml"),
        "-p",
    )
    assert r.returncode == 0, r.stderr
    assert "customize-daemonset" in r.stdout
