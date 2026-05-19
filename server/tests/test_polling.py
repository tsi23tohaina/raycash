"""Tests de robustesse pour l'architecture polling /esp_verdict.

Scenarios couverts :
- happy path : POST /esp_signal -> set_verdict -> GET /esp_verdict recupere
- file vide : GET /esp_verdict avant tout scan -> 204
- consommation unique : un seul GET recupere le verdict, suivants -> 204
- expiration TTL : verdict drop apres _VERDICT_TTL_S
- ecrasement : un nouveau verdict ecrase l'ancien non reclame
- concurrence : N polls paralleles, un seul gagne
- auth : pas de X-API-Key -> 401
"""

from __future__ import annotations

import threading
import time

import pytest

import main as srv  # le module Flask (server/main.py)


@pytest.fixture
def client(monkeypatch):
    """Flask test client + reset de l'etat de la file de verdicts."""
    # Reset etat global entre tests pour eviter les fuites
    with srv._verdict_lock:
        srv._pending_verdict = None
    srv.app.config["TESTING"] = True
    return srv.app.test_client()


@pytest.fixture(autouse=True)
def reset_verdict():
    """Reset la file de verdicts avant ET apres chaque test."""
    with srv._verdict_lock:
        srv._pending_verdict = None
    yield
    with srv._verdict_lock:
        srv._pending_verdict = None


KEY = None  # rempli depuis srv.SETTINGS au setup


@pytest.fixture
def api_key():
    return srv.SETTINGS.api_key or "test-api-key"


# ---------- TESTS UNITAIRES (queue thread-safe) ----------

def test_set_then_consume_returns_verdict():
    srv.set_verdict("RECYCLABLE:40")
    assert srv.consume_verdict() == "RECYCLABLE:40"


def test_consume_twice_returns_none_second_time():
    srv.set_verdict("RECYCLABLE:40")
    assert srv.consume_verdict() == "RECYCLABLE:40"
    assert srv.consume_verdict() is None


def test_empty_queue_returns_none():
    assert srv.consume_verdict() is None


def test_overwrite_keeps_latest():
    srv.set_verdict("RECYCLABLE:40")
    srv.set_verdict("NON_RECYCLABLE:0")
    assert srv.consume_verdict() == "NON_RECYCLABLE:0"


def test_ttl_expires_old_verdict(monkeypatch):
    """Un verdict pose il y a > TTL est drop, retourne None."""
    monkeypatch.setattr(srv, "_VERDICT_TTL_S", 0.1)
    srv.set_verdict("RECYCLABLE:40")
    time.sleep(0.2)
    assert srv.consume_verdict() is None


def test_concurrent_consumers_only_one_wins():
    """Si 10 polls partent en parallele, 1 seul recupere le verdict."""
    srv.set_verdict("RECYCLABLE:40")
    results = []
    lock = threading.Lock()

    def poller():
        v = srv.consume_verdict()
        with lock:
            results.append(v)

    threads = [threading.Thread(target=poller) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r == "RECYCLABLE:40"]
    losers = [r for r in results if r is None]
    assert len(winners) == 1, f"Expected 1 winner, got {len(winners)}"
    assert len(losers) == 9, f"Expected 9 losers, got {len(losers)}"


# ---------- TESTS HTTP (endpoint GET /esp_verdict) ----------

def test_get_esp_verdict_empty_returns_204(client, api_key):
    r = client.get("/esp_verdict", headers={"X-API-Key": api_key})
    assert r.status_code == 204
    assert r.data == b""


def test_get_esp_verdict_with_pending_returns_200(client, api_key):
    srv.set_verdict("RECYCLABLE:40")
    r = client.get("/esp_verdict", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.data.decode() == "RECYCLABLE:40"


def test_get_esp_verdict_consumes_queue(client, api_key):
    srv.set_verdict("NON_RECYCLABLE:0")
    r1 = client.get("/esp_verdict", headers={"X-API-Key": api_key})
    r2 = client.get("/esp_verdict", headers={"X-API-Key": api_key})
    assert r1.status_code == 200
    assert r2.status_code == 204


def test_get_esp_verdict_no_api_key_returns_401(client):
    srv.set_verdict("RECYCLABLE:40")
    r = client.get("/esp_verdict")
    assert r.status_code == 401


def test_get_esp_verdict_wrong_api_key_returns_401(client):
    srv.set_verdict("RECYCLABLE:40")
    r = client.get("/esp_verdict", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 401


# ---------- SCENARIOS DE ROBUSTESSE ----------

def test_scenario_happy_path(client, api_key):
    """ESP32 attend, scan arrive, verdict recupere au premier poll."""
    # Etat initial : ESP32 polle, rien dispo
    r = client.get("/esp_verdict", headers={"X-API-Key": api_key})
    assert r.status_code == 204

    # /predict simule -> set_verdict
    srv.set_verdict("RECYCLABLE:40")

    # Prochain poll : verdict
    r = client.get("/esp_verdict", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.data.decode() == "RECYCLABLE:40"


def test_scenario_gemini_slow_many_polls_then_verdict(client, api_key):
    """Gemini prend 6s, ESP32 polle 12 fois (toutes les 500ms), puis recoit."""
    polls_204 = 0
    for _ in range(12):
        r = client.get("/esp_verdict", headers={"X-API-Key": api_key})
        if r.status_code == 204:
            polls_204 += 1
    assert polls_204 == 12

    # Gemini termine, verdict dispo
    srv.set_verdict("NON_RECYCLABLE:0")
    r = client.get("/esp_verdict", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.data.decode() == "NON_RECYCLABLE:0"


def test_scenario_double_scan_rapide(client, api_key):
    """Deux scans rapprochees : le 2e ecrase le 1er si pas encore consomme."""
    srv.set_verdict("RECYCLABLE:40")
    srv.set_verdict("NON_RECYCLABLE:0")   # ecrase

    r = client.get("/esp_verdict", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.data.decode() == "NON_RECYCLABLE:0"


def test_scenario_esp32_reboot_pendant_attente(client, api_key, monkeypatch):
    """ESP32 reboot apres /esp_signal, verdict reste cote serveur jusqu'a
    expiration. Quand l'ESP32 revient, il poll et le drop est silencieux."""
    monkeypatch.setattr(srv, "_VERDICT_TTL_S", 0.5)
    srv.set_verdict("RECYCLABLE:40")

    # ESP32 reboote, attendons l'expiration
    time.sleep(0.6)

    # ESP32 revient et polle : doit voir 204, pas un vieux verdict
    r = client.get("/esp_verdict", headers={"X-API-Key": api_key})
    assert r.status_code == 204


def test_scenario_polling_a_haute_frequence_pas_de_leak(client, api_key):
    """500 polls en serie : aucun leak memoire / etat residuel."""
    for _ in range(500):
        r = client.get("/esp_verdict", headers={"X-API-Key": api_key})
        assert r.status_code == 204

    # Apres 500 polls, la file est toujours vide
    assert srv.consume_verdict() is None
