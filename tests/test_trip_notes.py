"""Tests for trip notes CRUD endpoints (TRA-221)."""

from tests.conftest import FAKE_USER_ID


def test_list_notes(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    fake_sb.set_table_data("trip_notes", [])
    resp = client.get("/api/trips/t1/notes")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_create_note(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    resp = client.post("/api/trips/t1/notes", json={
        "day": "2026-04-01",
        "content": "Don't forget sunscreen",
        "color": "#ffd93d",
    })
    assert resp.status_code == 201
    assert "id" in resp.json()


def test_update_note(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    fake_sb.set_table_data("trip_notes", [{"id": "n1", "content": "Old"}])
    resp = client.patch("/api/trips/t1/notes/n1", json={"content": "Updated"})
    assert resp.status_code == 200


def test_update_note_no_fields(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    resp = client.patch("/api/trips/t1/notes/n1", json={})
    assert resp.status_code == 400


def test_delete_note(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    fake_sb.set_table_data("trip_notes", [{"id": "n1"}])
    resp = client.delete("/api/trips/t1/notes/n1")
    assert resp.status_code == 204
