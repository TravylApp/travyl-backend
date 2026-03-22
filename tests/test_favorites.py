"""Tests for favorite places CRUD endpoints (TRA-224)."""

from tests.conftest import FAKE_USER_ID


def test_list_favorites(client, fake_sb):
    fake_sb.set_table_data("favorite_places", [])
    resp = client.get("/api/favorites")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_add_favorite(client, fake_sb):
    resp = client.post("/api/favorites", json={
        "activity_type": "food",
        "activity_data": {"name": "Pizzeria", "city": "Rome"},
    })
    assert resp.status_code == 201
    assert "id" in resp.json()


def test_remove_favorite(client, fake_sb):
    fake_sb.set_table_data("favorite_places", [{"id": "f1", "user_id": FAKE_USER_ID}])
    resp = client.delete("/api/favorites/f1")
    assert resp.status_code == 204


def test_remove_favorite_not_found(client, fake_sb):
    fake_sb.set_table_data("favorite_places", [])
    resp = client.delete("/api/favorites/nonexistent")
    assert resp.status_code == 404
