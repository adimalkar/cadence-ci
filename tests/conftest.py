"""Suite-wide fixtures."""

from __future__ import annotations

import pytest
import structlog


@pytest.fixture(autouse=True)
def _restore_structlog():
    """A command under test may reroute structlog to stderr; never let that leak."""
    yield
    structlog.reset_defaults()
