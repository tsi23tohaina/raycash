"""Fixtures pytest partagées pour les tests du serveur."""
import io
import os
import sys

import pytest
from PIL import Image

# Configure l'environnement AVANT d'importer main.py (qui lit les env vars
# à l'import).
os.environ.setdefault("RAYCASH_API_KEY", "test-api-key")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost")
# Bumpe le rate limit pour éviter les 429 en série de tests.
os.environ.setdefault("RATE_LIMIT_PREDICT", "10000 per minute")
os.environ.setdefault("RATE_LIMIT_ESP_SIGNAL", "10000 per minute")

# Ajoute server/ au sys.path pour pouvoir importer main et inference.
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from inference import Prediction  # noqa: E402


class StubClassifier:
    """Faux Classifier — évite de charger TensorFlow Lite en test."""

    def __init__(self, ready: bool = True, prediction: Prediction | None = None):
        self._ready = ready
        self._prediction = prediction or Prediction(
            label="Plastique",
            confidence=0.87,
            points=40,
            tri_status="RECYCLABLE",
        )

    @property
    def ready(self) -> bool:
        return self._ready

    def classify(self, _img):
        return self._prediction


@pytest.fixture
def api_key():
    return os.environ["RAYCASH_API_KEY"]


@pytest.fixture
def stub_classifier():
    return StubClassifier()


@pytest.fixture
def client(monkeypatch, stub_classifier):
    """Flask test client avec un classifier mocké."""
    import main

    monkeypatch.setattr(main, "classifier", stub_classifier)
    # Désactive le call sortant vers l'ESP32 pendant les tests.
    monkeypatch.setattr(main.requests, "post", lambda *a, **kw: None)

    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


@pytest.fixture
def auth_header(api_key):
    return {"X-API-Key": api_key}


def make_jpeg_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    """Génère un JPEG en mémoire pour les tests d'upload."""
    buf = io.BytesIO()
    Image.new("RGB", size, color="red").save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def jpeg_bytes():
    return make_jpeg_bytes()
