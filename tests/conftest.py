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


@pytest.fixture(autouse=True)
def _no_fail_verifier():
    """The fail verifier is on by default, which would add a second call to every FAIL a fake
    client grades. Tests that exercise it inject a FailVerifier or switch it back on."""
    old = settings.qa_fail_verifier
    object.__setattr__(settings, "qa_fail_verifier", "off")
    yield
    object.__setattr__(settings, "qa_fail_verifier", old)


@pytest.fixture(autouse=True)
def _offline_kb():
    """Graders must not sync the Intercom Help Center from tests. Tests that exercise the
    knowledge base inject one with Grader(knowledge=...)."""
    old = settings.kb_enabled
    object.__setattr__(settings, "kb_enabled", False)
    yield
    object.__setattr__(settings, "kb_enabled", old)


@pytest.fixture(autouse=True)
def _offline_history():
    """Graders must not open the history index or call the labeller from tests. Tests that
    exercise the historical context inject a provider with Grader(history=...)."""
    old = settings.history_context, settings.history_refresh_days
    object.__setattr__(settings, "history_context", "off")
    object.__setattr__(settings, "history_refresh_days", 0)   # no periodic refresh under TestClient
    yield
    object.__setattr__(settings, "history_context", old[0])
    object.__setattr__(settings, "history_refresh_days", old[1])
