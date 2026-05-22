import argparse

from customize_daemonset import (
    Toleration,
    format_cli_command,
)


def _args(**overrides):
    base = dict(
        base="path/to/base.yaml",
        nodes="path/to/nodes.yaml",
        out=None,
        cpu_request_per_core="50m",
        max_total_cpu=None,
        max_total_memory=None,
        tolerate=[],
        tolerate_all=False,
        interactive=False,
        print_cli=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_format_minimal():
    cmd = format_cli_command(_args())
    assert "customize-daemonset" in cmd
    assert "--base path/to/base.yaml" in cmd
    assert "--nodes path/to/nodes.yaml" in cmd
    assert "--tolerate" not in cmd


def test_format_with_out():
    cmd = format_cli_command(_args(out="result.yaml"))
    assert "--out result.yaml" in cmd


def test_format_path_with_spaces_quoted():
    cmd = format_cli_command(_args(base="/tmp/has spaces/base.yaml"))
    assert "'/tmp/has spaces/base.yaml'" in cmd


def test_format_explicit_tolerate_verbatim():
    cmd = format_cli_command(_args(tolerate=["k=v:NoSchedule", "x:NoExecute"]))
    assert "--tolerate k=v:NoSchedule" in cmd
    assert "--tolerate x:NoExecute" in cmd


def test_format_tolerate_all_flag():
    cmd = format_cli_command(_args(tolerate_all=True))
    assert "--tolerate-all" in cmd


def test_format_prompt_all_selected_uses_tolerate_all():
    tols = [Toleration("k", "Exists", "", "NoSchedule")]
    cmd = format_cli_command(_args(),
                             final_tolerations_from_prompt=tols,
                             all_taints_selected=True)
    assert "--tolerate-all" in cmd
    assert "k:NoSchedule" not in cmd  # not enumerated


def test_format_prompt_partial_enumerates():
    tols = [
        Toleration("k1", "Exists", "", "NoSchedule"),
        Toleration("k2", "Equal", "v2", "NoExecute"),
    ]
    cmd = format_cli_command(_args(),
                             final_tolerations_from_prompt=tols,
                             all_taints_selected=False)
    assert "--tolerate k1:NoSchedule" in cmd
    assert "--tolerate k2=v2:NoExecute" in cmd


def test_format_max_total_flags():
    cmd = format_cli_command(_args(max_total_cpu="100", max_total_memory="200Gi"))
    assert "--max-total-cpu 100" in cmd
    assert "--max-total-memory 200Gi" in cmd


def test_format_non_default_cpu_per_core_included():
    cmd = format_cli_command(_args(cpu_request_per_core="100m"))
    assert "--cpu-request-per-core 100m" in cmd


def test_format_default_cpu_per_core_omitted():
    # default 50m should be omitted to keep the command minimal
    cmd = format_cli_command(_args(cpu_request_per_core="50m"))
    assert "--cpu-request-per-core" not in cmd
