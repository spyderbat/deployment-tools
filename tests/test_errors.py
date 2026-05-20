import pytest

from customize_daemonset import (
    CustomizerError,
    InputValidationError,
    InteractiveCancelled,
)


def test_all_subclass_customizer_error():
    assert issubclass(InputValidationError, CustomizerError)
    assert issubclass(InteractiveCancelled, CustomizerError)


def test_can_raise_and_catch():
    with pytest.raises(CustomizerError):
        raise InputValidationError("bad input")
