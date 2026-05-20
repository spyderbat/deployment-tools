#!/usr/bin/env python3
"""Generate per-core-count DaemonSet variants from a base manifest and a
`kubectl get nodes -o yaml` file. See docs/design.md for the full spec."""


class CustomizerError(Exception):
    """Base class for all expected, user-facing errors."""


class InputValidationError(CustomizerError):
    """Raised when input files or arguments are malformed or unsupported."""


class InteractiveCancelled(CustomizerError):
    """Raised when the user cancels an interactive prompt (Ctrl-C / EOF)."""


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
