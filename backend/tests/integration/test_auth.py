"""
Integration tests for auth endpoints.
Uses FastAPI's TestClient with a real (test) database or mocked DB.

Phase 1: We test against the running Docker DB.
In CI you'd spin up a separate test database via pytest fixtures.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_wrong_credentials():
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@nowhere.com", "password": "wrong"},
    )
    assert response.status_code == 401


def test_upload_requires_auth():
    """Uploading without a token should return 403 (no credentials)."""
    response = client.post("/api/v1/documents/upload")
    assert response.status_code in (401, 403, 422)


def test_list_documents_requires_auth():
    response = client.get("/api/v1/documents")
    assert response.status_code in (401, 403)
