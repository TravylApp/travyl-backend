"""Tests for profiles CRUD endpoints (TRA-223)."""

from tests.conftest import FAKE_USER_ID


def test_get_my_profile(client, fake_sb):
    fake_sb.set_table_data("profiles", {"id": FAKE_USER_ID, "display_name": "Test User"})
    resp = client.get("/api/profiles/me")
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Test User"


def test_get_my_profile_not_found(client, fake_sb):
    fake_sb.set_table_data("profiles", None)
    resp = client.get("/api/profiles/me")
    assert resp.status_code == 404


def test_update_my_profile(client, fake_sb):
    fake_sb.set_table_data("profiles", [{"id": FAKE_USER_ID, "display_name": "Old Name"}])
    resp = client.patch("/api/profiles/me", json={"display_name": "New Name"})
    assert resp.status_code == 200


def test_update_my_profile_no_fields(client, fake_sb):
    resp = client.patch("/api/profiles/me", json={})
    assert resp.status_code == 400


def test_get_public_profile(client, fake_sb):
    fake_sb.set_table_data("profiles", {"id": "other", "display_name": "Alice", "avatar_url": None})
    resp = client.get("/api/profiles/Alice")
    assert resp.status_code == 200


def test_get_public_profile_not_found(client, fake_sb):
    fake_sb.set_table_data("profiles", None)
    resp = client.get("/api/profiles/nobody")
    assert resp.status_code == 404
