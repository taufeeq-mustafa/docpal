import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import main


def png() -> bytes:
    doc = pymupdf.open()
    doc.new_page(width=100, height=100)
    return doc[0].get_pixmap().tobytes("png")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "explain", lambda images, language: iter([]))  # no Gemma calls in tests
    main._calls.clear()
    return TestClient(main.app)


def analyze(client, ip: str):
    return client.post("/api/analyze", files={"file": ("f.png", png(), "image/png")}, headers={"X-Real-IP": ip})


def test_sixth_form_in_five_minutes_is_refused(client):
    assert [analyze(client, "1.1.1.1").status_code for _ in range(main.RATE_LIMIT)] == [200] * main.RATE_LIMIT
    refused = analyze(client, "1.1.1.1")
    assert refused.status_code == 429
    assert "limit of 5 forms" in refused.json()["detail"] and int(refused.headers["Retry-After"]) > 0
    assert analyze(client, "2.2.2.2").status_code == 200  # other visitors are unaffected


def test_window_slides(client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: now[0])
    for _ in range(main.RATE_LIMIT):
        analyze(client, "1.1.1.1")
    assert analyze(client, "1.1.1.1").status_code == 429
    now[0] += main.RATE_WINDOW + 1
    assert analyze(client, "1.1.1.1").status_code == 200
