"""Vérifie que toutes les routes sont verrouillées par X-API-Key."""
import io


def test_predict_sans_cle_renvoie_401(client, jpeg_bytes):
    response = client.post(
        "/predict",
        data={"image": (io.BytesIO(jpeg_bytes), "test.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 401
    assert response.get_json()["error"] == "unauthorized"


def test_predict_avec_mauvaise_cle_renvoie_401(client, jpeg_bytes):
    response = client.post(
        "/predict",
        headers={"X-API-Key": "wrong"},
        data={"image": (io.BytesIO(jpeg_bytes), "test.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 401


def test_esp_signal_sans_cle_renvoie_401(client):
    response = client.post("/esp_signal", json={"action": "START"})
    assert response.status_code == 401


def test_uploads_sans_cle_renvoie_401(client):
    response = client.get("/uploads/anything.jpg")
    assert response.status_code == 401


def test_predict_avec_bonne_cle_renvoie_200(client, auth_header, jpeg_bytes):
    response = client.post(
        "/predict",
        headers=auth_header,
        data={"image": (io.BytesIO(jpeg_bytes), "test.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["label"] == "Plastique"
    assert body["tri_status"] == "RECYCLABLE"
    assert body["points"] == 40
    assert "confidence" in body
    assert body["image_url"].startswith("/uploads/")
