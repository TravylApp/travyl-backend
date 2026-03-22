"""Tests for trips CRUD endpoints (TRA-219)."""

from tests.conftest import FAKE_USER_ID


def test_list_trips(client):
    resp = client.get("/api/trips")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_create_trip(client, fake_sb):
    fake_sb.set_table_data("trips", [{"id": "new", "user_id": FAKE_USER_ID, "title": "Paris"}])
    resp = client.post("/api/trips", json={
        "title": "Paris Trip",
        "destination": "Paris",
    })
    assert resp.status_code == 201
    assert "id" in resp.json()


def test_get_trip_owner(client, fake_sb):
    fake_sb.set_table_data("trips", {"id": "t1", "user_id": FAKE_USER_ID, "title": "Rome"})
    resp = client.get("/api/trips/t1")
    assert resp.status_code == 200


def test_get_trip_not_found(client, fake_sb):
    fake_sb.set_table_data("trips", None)
    resp = client.get("/api/trips/nonexistent")
    assert resp.status_code == 404


def test_update_trip(client, fake_sb):
    fake_sb.set_table_data("trips", {"id": "t1", "user_id": FAKE_USER_ID})
    resp = client.patch("/api/trips/t1", json={"title": "Updated"})
    assert resp.status_code == 200


def test_update_trip_no_fields(client, fake_sb):
    fake_sb.set_table_data("trips", {"id": "t1", "user_id": FAKE_USER_ID})
    resp = client.patch("/api/trips/t1", json={})
    assert resp.status_code == 400


def test_delete_trip(client, fake_sb):
    fake_sb.set_table_data("trips", {"id": "t1", "user_id": FAKE_USER_ID})
    resp = client.delete("/api/trips/t1")
    assert resp.status_code == 204


def test_list_public_trips(client, fake_sb):
    fake_sb.set_table_data("trips", [
        {"id": "t1", "visibility": "public", "profiles": {"display_name": "Alice"}},
    ])
    resp = client.get("/api/trips/public")
    assert resp.status_code == 200


def test_get_trip_by_share_token(client, fake_sb):
    fake_sb.set_table_data("trips", {"id": "t1", "share_link_token": "abc123"})
    resp = client.get("/api/trips/share/abc123")
    assert resp.status_code == 200


def test_get_trip_by_share_token_not_found(client, fake_sb):
    fake_sb.set_table_data("trips", None)
    resp = client.get("/api/trips/share/bad-token")
    assert resp.status_code == 404


def test_generate_share_token(client, fake_sb):
    fake_sb.set_table_data("trips", {"id": "t1", "user_id": FAKE_USER_ID, "share_link_token": None})
    resp = client.post("/api/trips/t1/share-token")
    assert resp.status_code == 200
    assert "share_link_token" in resp.json()


def test_fork_trip(client, fake_sb):
    fake_sb.set_table_data("trips", {"id": "t1", "user_id": FAKE_USER_ID, "title": "Original"})
    fake_sb.set_table_data("activity", [])
    resp = client.post("/api/trips/t1/fork")
    assert resp.status_code == 201
    assert "Fork" in resp.json().get("title", "")
