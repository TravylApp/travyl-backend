"""Tests for collaborators CRUD endpoints (TRA-222)."""

from tests.conftest import FAKE_USER_ID


def test_list_collaborators(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    fake_sb.set_table_data("trip_collaborators", [])
    resp = client.get("/api/trips/t1/collaborators")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_invite_collaborator(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    fake_sb.set_table_data("trip_collaborators", None)  # no existing invite
    resp = client.post("/api/trips/t1/collaborators/invite", json={
        "invited_email": "friend@example.com",
        "role_type": "editor",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data.get("invite_status") == "pending" or "invite_token" in data


def test_update_collaborator_role(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    fake_sb.set_table_data("trip_collaborators", [{"id": "c1", "role_type": "viewer"}])
    resp = client.patch("/api/trips/t1/collaborators/c1", json={"role_type": "editor"})
    assert resp.status_code == 200


def test_remove_collaborator(client, fake_sb):
    fake_sb.set_table_data("trips", {"user_id": FAKE_USER_ID})
    fake_sb.set_table_data("trip_collaborators", [{"id": "c1"}])
    resp = client.delete("/api/trips/t1/collaborators/c1")
    assert resp.status_code == 204


def test_join_via_link(client, fake_sb):
    # Trip owned by someone else, shared via link
    fake_sb.set_table_data("trips", {
        "user_id": "other-user-id",
        "visibility": "link",
        "link_permission": "edit",
    })
    fake_sb.set_table_data("trip_collaborators", None)  # not already a collab
    resp = client.post("/api/trips/t1/collaborators/join")
    assert resp.status_code == 201
    assert resp.json().get("role_type") == "editor"


def test_join_private_trip_rejected(client, fake_sb):
    fake_sb.set_table_data("trips", {
        "user_id": "other-user-id",
        "visibility": "private",
        "link_permission": "view",
    })
    resp = client.post("/api/trips/t1/collaborators/join")
    assert resp.status_code == 403


def test_accept_invite(client, fake_sb):
    fake_sb.set_table_data("trip_collaborators", {
        "id": "c1",
        "invite_token": "tok123",
        "invite_status": "pending",
    })
    resp = client.post("/api/collaborators/accept/tok123")
    assert resp.status_code == 200
    assert resp.json().get("invite_status") == "accepted"


def test_accept_invite_not_found(client, fake_sb):
    fake_sb.set_table_data("trip_collaborators", None)
    resp = client.post("/api/collaborators/accept/bad-token")
    assert resp.status_code == 404
