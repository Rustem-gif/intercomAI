import pytest

from intercom_summary.settings import settings


@pytest.fixture(autouse=True)
def _offline_jev():
    """The suite is offline: .env's JEV_API_KEY must not make every v4.1 grade call Jev.
    Tests that exercise Jev inject a fake verifier explicitly."""
    old = settings.jev_mode
    object.__setattr__(settings, "jev_mode", "off")
    yield
    object.__setattr__(settings, "jev_mode", old)
