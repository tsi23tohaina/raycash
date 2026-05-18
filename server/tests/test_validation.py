"""Tests de validation des entrées."""
import io


class TestPredictValidation:
    def test_sans_fichier_renvoie_400(self, client, auth_header):
        response = client.post("/predict", headers=auth_header, data={})
        assert response.status_code == 400

    def test_mime_non_autorise_renvoie_415(self, client, auth_header):
        response = client.post(
            "/predict",
            headers=auth_header,
            data={"image": (io.BytesIO(b"hello"), "test.txt", "text/plain")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 415

    def test_fichier_vide_renvoie_400(self, client, auth_header):
        response = client.post(
            "/predict",
            headers=auth_header,
            data={"image": (io.BytesIO(b""), "empty.jpg", "image/jpeg")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 400

    def test_fichier_image_corrompu_renvoie_400(self, client, auth_header):
        response = client.post(
            "/predict",
            headers=auth_header,
            data={"image": (io.BytesIO(b"not-a-jpeg"), "bad.jpg", "image/jpeg")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 400

    def test_modele_indisponible_renvoie_503(
        self, client, auth_header, jpeg_bytes, monkeypatch
    ):
        # Le client par défaut a un classifier "ready" ; on simule l'indispo
        # en lui injectant un stub `ready=False` pour ce test précis.
        import main
        from tests.conftest import StubClassifier

        monkeypatch.setattr(main, "classifier", StubClassifier(ready=False))
        response = client.post(
            "/predict",
            headers=auth_header,
            data={"image": (io.BytesIO(jpeg_bytes), "test.jpg", "image/jpeg")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 503


class TestEspSignalValidation:
    def test_action_invalide_renvoie_400(self, client, auth_header):
        response = client.post("/esp_signal", headers=auth_header, json={"action": "BOOM"})
        assert response.status_code == 400

    def test_payload_vide_renvoie_400(self, client, auth_header):
        response = client.post("/esp_signal", headers=auth_header, json={})
        assert response.status_code == 400

    def test_action_start_ok(self, client, auth_header):
        response = client.post("/esp_signal", headers=auth_header, json={"action": "START"})
        assert response.status_code == 200
        assert response.get_json()["status"] == "capture_triggered"

    def test_action_stop_ok(self, client, auth_header):
        response = client.post("/esp_signal", headers=auth_header, json={"action": "STOP"})
        assert response.status_code == 200
        assert response.get_json()["status"] == "ignored"
