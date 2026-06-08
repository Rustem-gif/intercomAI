import textwrap

from intercom_summary.slack.auth import RoleStore


def _write_roles(tmp_path):
    p = tmp_path / "roles.yaml"
    p.write_text(textwrap.dedent("""
        analyst:
          - U_ALLOWED
        admin:
          - U_ADMIN
    """))
    return p


def test_analyst_allowed(tmp_path):
    store = RoleStore(_write_roles(tmp_path))
    assert store.role_for("U_ALLOWED") == "analyst"
    assert store.can_use_data("U_ALLOWED") is True


def test_default_denied(tmp_path):
    store = RoleStore(_write_roles(tmp_path))
    assert store.role_for("U_RANDOM") == "default"
    assert store.can_use_data("U_RANDOM") is False


def test_admin_is_also_analyst(tmp_path):
    store = RoleStore(_write_roles(tmp_path))
    assert store.is_admin("U_ADMIN") is True
    assert store.can_use_data("U_ADMIN") is True
