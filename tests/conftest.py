"""Shared fixtures for CRUD API tests."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------- Fake Supabase helpers ----------

class FakeExecute:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Chainable mock that mimics supabase-py query builder."""

    def __init__(self, data=None):
        self._data = data if data is not None else []
        self._is_single = isinstance(data, dict)

    def select(self, *a, **kw): return self
    def insert(self, row, **kw):
        self._is_single = False
        if isinstance(row, list):
            for r in row:
                r.setdefault("id", str(uuid.uuid4()))
            self._data = row
        else:
            row.setdefault("id", str(uuid.uuid4()))
            self._data = [row]
        return self
    def update(self, vals, **kw):
        if self._is_single and isinstance(self._data, dict):
            self._data.update(vals)
            self._data = [self._data]
            self._is_single = False
        elif isinstance(self._data, list) and self._data:
            for row in self._data:
                row.update(vals)
        else:
            self._data = [vals]
        return self
    def upsert(self, rows, **kw):
        for r in rows:
            r.setdefault("id", str(uuid.uuid4()))
        self._data = rows
        return self
    def delete(self, **kw): return self
    def eq(self, *a, **kw): return self
    def or_(self, *a, **kw): return self
    def ilike(self, *a, **kw): return self
    def order(self, *a, **kw): return self
    def single(self, **kw):
        self._is_single = True
        return self
    def maybe_single(self, **kw):
        self._is_single = True
        return self

    def execute(self):
        if self._is_single:
            if isinstance(self._data, list):
                return FakeExecute(self._data[0] if self._data else None)
            return FakeExecute(self._data if self._data else None)
        return FakeExecute(self._data)


class FakeSupabase:
    """Minimal Supabase mock that returns configurable data per table."""

    def __init__(self):
        self._tables = {}

    def set_table_data(self, table: str, data):
        self._tables[table] = data

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self._tables.get(name, []))


# ---------- JWT fixture ----------

FAKE_USER_ID = str(uuid.uuid4())
FAKE_USER = {"id": FAKE_USER_ID, "email": "test@travyl.com", "role": "authenticated"}


@pytest.fixture()
def fake_sb():
    return FakeSupabase()


@pytest.fixture()
def client(fake_sb):
    """TestClient with auth and supabase mocked out."""
    from app.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: FAKE_USER

    with patch("app.routers.crud_trips.get_supabase", return_value=fake_sb), \
         patch("app.routers.activities.get_supabase", return_value=fake_sb), \
         patch("app.routers.trip_notes.get_supabase", return_value=fake_sb), \
         patch("app.routers.collaborators.get_supabase", return_value=fake_sb), \
         patch("app.routers.profiles.get_supabase", return_value=fake_sb), \
         patch("app.routers.favorites.get_supabase", return_value=fake_sb):
        yield TestClient(app)

    app.dependency_overrides.clear()
