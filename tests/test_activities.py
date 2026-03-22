"""Tests for activities CRUD endpoints (TRA-220)."""

from tests.conftest import FAKE_USER_ID


def test_list_activities(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    fake_sb.set_table_data("activity", [])
    resp = client.get("/api/trips/t1/activities")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_activities_with_type_filter(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    fake_sb.set_table_data("activity", [])
    resp = client.get("/api/trips/t1/activities?type=hotel")
    assert resp.status_code == 200


def test_create_activity(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    resp = client.post("/api/trips/t1/activities", json={
        "activity_name": "Visit Colosseum",
        "starting_date": "2026-04-01",
        "ending_date": "2026-04-01",
        "starting_time": "09:00",
        "ending_time": "11:00",
        "activity_type": "other",
    })
    assert resp.status_code == 201
    assert "id" in resp.json()


def test_update_activity(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    fake_sb.set_table_data("activity", [{"id": "a1", "activity_name": "Old"}])
    resp = client.patch("/api/trips/t1/activities/a1", json={"activity_name": "Updated"})
    assert resp.status_code == 200


def test_update_activity_no_fields(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    resp = client.patch("/api/trips/t1/activities/a1", json={})
    assert resp.status_code == 400


def test_delete_activity(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    fake_sb.set_table_data("activity", [{"id": "a1"}])
    resp = client.delete("/api/trips/t1/activities/a1")
    assert resp.status_code == 204


def test_bulk_upsert_activities(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    resp = client.put("/api/trips/t1/activities/bulk", json=[
        {
            "activity_name": "Morning hike",
            "starting_date": "2026-04-01",
            "ending_date": "2026-04-01",
            "starting_time": "07:00",
            "ending_time": "09:00",
        },
        {
            "activity_name": "Lunch",
            "starting_date": "2026-04-01",
            "ending_date": "2026-04-01",
            "starting_time": "12:00",
            "ending_time": "13:00",
            "activity_type": "food",
        },
    ])
    assert resp.status_code == 200
    assert len(resp.json()) == 2
