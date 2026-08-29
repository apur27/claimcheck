"""Tests for the claimcheck CLI entry point stub."""

import pytest

from claimcheck.cli import main


def test_main_raises_not_implemented() -> None:
    """The console-script entry point is wired but not yet implemented."""
    with pytest.raises(NotImplementedError):
        main()
