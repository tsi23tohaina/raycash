"""Tests unitaires du module inference (sans Flask, sans TFLite)."""
import os
import sys

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from inference import POINTS_BY_LABEL, RECYCLABLES, Classifier, Prediction  # noqa: E402


class TestPointsTable:
    def test_recyclables_contient_les_5_categories(self):
        assert RECYCLABLES == {"Aluminium", "Plastique", "Verre", "Papier", "Carton"}

    def test_points_aluminium_50(self):
        assert POINTS_BY_LABEL["Aluminium"] == 50

    def test_points_plastique_40(self):
        assert POINTS_BY_LABEL["Plastique"] == 40

    def test_points_verre_20(self):
        assert POINTS_BY_LABEL["Verre"] == 20

    def test_points_papier_et_carton_10(self):
        assert POINTS_BY_LABEL["Papier"] == 10
        assert POINTS_BY_LABEL["Carton"] == 10


class TestClassifierPointsFor:
    """Sans modèle chargé, on peut quand même tester la table de points."""

    def setup_method(self):
        self.clf = Classifier(
            model_path="/inexistant/model.tflite",
            label_path=None,
            confidence_threshold=0.45,
        )

    def test_modele_indisponible_si_chemin_invalide(self):
        assert self.clf.ready is False

    def test_points_for_known_label(self):
        assert self.clf.points_for("Aluminium") == 50

    def test_points_for_unknown_label(self):
        assert self.clf.points_for("Banane") == 0
        assert self.clf.points_for("Inconnu") == 0


class TestPrediction:
    def test_prediction_recyclable_a_des_points(self):
        p = Prediction(label="Plastique", confidence=0.9, points=40, tri_status="RECYCLABLE")
        assert p.points == 40
        assert p.tri_status == "RECYCLABLE"

    def test_prediction_inconnu_zero_points(self):
        p = Prediction(label="Inconnu", confidence=0.3, points=0, tri_status="INCONNU")
        assert p.points == 0
