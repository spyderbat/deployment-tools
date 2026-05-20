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
