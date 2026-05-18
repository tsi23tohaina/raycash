"""Chargement des modèles TFLite + classification (single ou ensemble).

Isolé de main.py pour pouvoir tester l'inférence indépendamment de Flask
et garder le module HTTP plus court.

Deux niveaux d'abstraction :
- `Classifier` : un seul modèle TFLite, thread-safe.
- `EnsembleClassifier` : plusieurs `Classifier` votent, avec mesure d'incertitude.

Le serveur charge l'un OU l'autre selon `Settings.ensemble_enabled`.
"""

from __future__ import annotations

import io
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
import tensorflow as tf
from PIL import Image

log = logging.getLogger("raycash.inference")

# Barème de points — SOURCE UNIQUE. Si tu changes les valeurs, le client
# n'a rien à toucher car /predict renvoie déjà le champ "points".
POINTS_BY_LABEL: dict[str, int] = {
    "Aluminium": 50,
    "Plastique": 40,
    "Verre": 20,
    "Papier": 10,
    "Carton": 10,
}

RECYCLABLES = set(POINTS_BY_LABEL.keys())

DEFAULT_LABELS = [
    "Aluminium",
    "Plastique",
    "Verre",
    "Papier",
    "Carton",
    "Inconnu",
]


@dataclass
class Prediction:
    label: str
    confidence: float
    points: int
    tri_status: str  # "RECYCLABLE" | "NON_RECYCLABLE"
    # Champs d'incertitude (toujours présents, valent 0/False pour Classifier single)
    uncertain: bool = False
    entropy: float = 0.0
    disagreement: float = 0.0
    accepted: bool = True  # False si rejeté par le seuil de confiance
    per_model_top: list[str] = field(default_factory=list)  # debug : top-1 de chaque modèle
    # Détails complets par modèle : nom, top-1 label, top-1 score, distribution
    # complète (les 6 classes → score). Permet à l'UI d'afficher un breakdown
    # détaillé "qui pense quoi".
    per_model_details: list[dict] = field(default_factory=list)
    # Top-3 prédictions agrégées de l'ensemble {label, score}.
    top_predictions: list[dict] = field(default_factory=list)


def _normalize_label(raw: str) -> str:
    """Retire le préfixe numérique ("0 Aluminium" → "Aluminium").

    labels.txt suit la convention TensorFlow Lite (`<index> <nom>`). Sans
    normalisation, l'index parasite remonte jusqu'à l'UI utilisateur.
    """
    cleaned = raw.strip()
    parts = cleaned.split(None, 1)
    if len(parts) == 2 and parts[0].isdigit():
        return parts[1].strip()
    return cleaned


def _load_labels(label_path: Optional[str]) -> list[str]:
    if not label_path:
        return list(DEFAULT_LABELS)
    try:
        with open(label_path, "r") as f:
            return [_normalize_label(line) for line in f if line.strip()]
    except FileNotFoundError:
        return list(DEFAULT_LABELS)


class Classifier:
    """Wrapper thread-safe autour d'un interpréteur TFLite."""

    def __init__(self, model_path: str, label_path: Optional[str], confidence_threshold: float = 0.0):
        self.model_path = model_path
        self.label_path = label_path
        # Pour `Classifier` standalone, le threshold sert au filtrage label→Inconnu.
        # Pour `EnsembleClassifier`, on laisse à 0.0 et on filtre au niveau ensemble.
        self.confidence_threshold = confidence_threshold

        self._lock = threading.Lock()
        self._interpreter: Optional[tf.lite.Interpreter] = None
        self._input_details = None
        self._output_details = None
        self._input_shape = None
        self.labels: list[str] = []

        self._load()

    def _load(self) -> None:
        try:
            self.labels = _load_labels(self.label_path)
            self._interpreter = tf.lite.Interpreter(model_path=self.model_path)
            self._interpreter.allocate_tensors()
            self._input_details = self._interpreter.get_input_details()
            self._output_details = self._interpreter.get_output_details()
            self._input_shape = self._input_details[0]["shape"]
            log.info("Modèle IA chargé (%s)", self.model_path)
        except Exception as e:
            log.error("Échec de chargement du modèle IA %s : %s", self.model_path, e)
            self._interpreter = None

    @property
    def ready(self) -> bool:
        return self._interpreter is not None

    def points_for(self, label: str) -> int:
        return POINTS_BY_LABEL.get(label, 0)

    def raw_probs(self, img: Image.Image) -> np.ndarray:
        """Retourne le vecteur de probabilités softmax brut (float32, somme≈1)."""
        if not self.ready:
            raise RuntimeError("Le modèle IA n'est pas chargé")

        img_resized = img.resize((self._input_shape[1], self._input_shape[2]))
        arr = np.array(img_resized, dtype=np.uint8)
        arr = np.expand_dims(arr, axis=0)

        with self._lock:
            self._interpreter.set_tensor(self._input_details[0]["index"], arr)
            self._interpreter.invoke()
            output = self._interpreter.get_tensor(self._output_details[0]["index"])[0]

        if output.dtype == np.uint8:
            output = output.astype(np.float32) / 255.0
        # Garde-fou : si la somme ne fait pas 1, on renormalise (modèles quantizés parfois imparfaits)
        s = output.sum()
        if s > 0:
            output = output / s
        return output

    def classify(self, img: Image.Image) -> Prediction:
        probs = self.raw_probs(img)
        best_index = int(np.argmax(probs))
        confidence = float(probs[best_index])

        accepted = confidence >= self.confidence_threshold and best_index < len(self.labels)
        if accepted:
            label = self.labels[best_index]
        else:
            label = "Inconnu"

        is_recyclable = label in RECYCLABLES
        return Prediction(
            label=label,
            confidence=confidence,
            points=self.points_for(label) if is_recyclable else 0,
            tri_status="RECYCLABLE" if is_recyclable else "NON_RECYCLABLE",
            uncertain=not accepted,
            accepted=accepted,
        )


class EnsembleClassifier:
    """Ensemble de N `Classifier` votent par moyenne des probabilités softmax.

    En plus de la prédiction, calcule 3 signaux d'incertitude :
    - **max_prob** : confiance de la classe top-1 (le plus utilisé)
    - **entropy** : dispersion de la distribution (high entropy = doute)
    - **disagreement** : variance des prédictions entre modèles (high std = désaccord)

    Si max_prob < `confidence_threshold` OU entropy > `entropy_threshold` OU
    disagreement > `disagreement_threshold`, la prédiction est marquée
    `accepted=False` et le label rebascule en "Inconnu" — le client doit
    inviter l'utilisateur à rescanner.

    Le multi-vue (`classify_multi`) moyenne sur N images × N modèles =
    plus robuste qu'une seule photo.
    """

    # Mapping fichier → nom affichable (UI)
    DISPLAY_NAMES = {
        "efficientnet": "EfficientNetV2-B0",
        "mobilenet": "MobileNetV2",
        "resnet50": "ResNet50",
        "resnet": "ResNet50",
    }

    @classmethod
    def _display_name_from_path(cls, path: str) -> str:
        """Extrait un nom lisible du chemin du modèle.
        ex: 'models/ensemble/model_efficientnet.tflite' → 'EfficientNetV2-B0'.
        """
        import os
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        # On retire le préfixe 'model_' si présent
        if stem.startswith("model_"):
            stem = stem[len("model_"):]
        return cls.DISPLAY_NAMES.get(stem, stem)

    def __init__(
        self,
        model_paths: Sequence[str],
        label_path: Optional[str],
        confidence_threshold: float = 0.70,
        entropy_threshold: float = 1.5,
        disagreement_threshold: float = 0.20,
    ):
        if not model_paths:
            raise ValueError("EnsembleClassifier requires at least one model path")
        self.classifiers: list[Classifier] = [
            Classifier(p, label_path, confidence_threshold=0.0) for p in model_paths
        ]
        self.model_names: list[str] = [self._display_name_from_path(p) for p in model_paths]
        self.labels = self.classifiers[0].labels
        # Vérifie la cohérence inter-modèles
        for c in self.classifiers[1:]:
            if c.labels != self.labels:
                log.warning("Labels divergents entre modèles ensemble — peut causer des comportements incohérents.")
                break
        self.confidence_threshold = confidence_threshold
        self.entropy_threshold = entropy_threshold
        self.disagreement_threshold = disagreement_threshold
        log.info(
            "Ensemble chargé (%d modèles, threshold=%.2f, entropy_max=%.2f, disagreement_max=%.2f)",
            len(self.classifiers),
            confidence_threshold,
            entropy_threshold,
            disagreement_threshold,
        )

    @property
    def ready(self) -> bool:
        return all(c.ready for c in self.classifiers)

    @property
    def n_models(self) -> int:
        return len(self.classifiers)

    def points_for(self, label: str) -> int:
        return POINTS_BY_LABEL.get(label, 0)

    @staticmethod
    def _entropy(p: np.ndarray, eps: float = 1e-10) -> float:
        return float(-np.sum(p * np.log(p + eps)))

    def _collect_probs(self, imgs: Sequence[Image.Image]) -> tuple[np.ndarray, np.ndarray, list[str]]:
        """Pour chaque (image, modèle), retourne le softmax.

        Returns
        -------
        all_probs : ndarray of shape [n_imgs * n_models, n_classes]
            Tous les softmax individuels (utile pour calculer disagreement).
        per_model_avg : ndarray of shape [n_models, n_classes]
            Le softmax MOYEN de chaque modèle sur toutes les vues. Permet à l'UI
            d'afficher "modèle X dit Y avec score Z" indépendamment du nombre de vues.
        per_model_top : list[str]
            Top-1 label de chaque modèle (basé sur per_model_avg).
        """
        n_models = len(self.classifiers)
        n_views = len(imgs)
        # Récupération brute : on stocke par (model, view)
        probs_grid = np.zeros((n_models, n_views, len(self.labels)), dtype=np.float32)
        for view_idx, img in enumerate(imgs):
            for model_idx, c in enumerate(self.classifiers):
                probs_grid[model_idx, view_idx] = c.raw_probs(img)

        # Moyenne par modèle (sur les vues)
        per_model_avg = probs_grid.mean(axis=1)  # [n_models, n_classes]

        per_model_top: list[str] = []
        for m in range(n_models):
            idx = int(np.argmax(per_model_avg[m]))
            per_model_top.append(self.labels[idx] if idx < len(self.labels) else "?")

        # Aplati pour le calcul d'incertitude (disagreement)
        all_probs = probs_grid.reshape(n_models * n_views, -1)
        return all_probs, per_model_avg, per_model_top

    def classify(self, img: Image.Image) -> Prediction:
        return self.classify_multi([img])

    def classify_multi(self, imgs: Sequence[Image.Image]) -> Prediction:
        if not self.ready:
            raise RuntimeError("Ensemble pas prêt — au moins un modèle n'est pas chargé")
        if not imgs:
            raise ValueError("Au moins une image est requise")

        all_probs, per_model_avg, per_model_top = self._collect_probs(imgs)
        # Probas moyennes de l'ensemble (sur tous modèles et toutes vues)
        avg_probs = all_probs.mean(axis=0)
        s = avg_probs.sum()
        if s > 0:
            avg_probs = avg_probs / s

        best_index = int(np.argmax(avg_probs))
        confidence = float(avg_probs[best_index])
        entropy = self._entropy(avg_probs)
        # Désaccord : std max entre prédictions individuelles (modèles × vues)
        disagreement = float(np.std(all_probs, axis=0).max())

        confidence_ok = confidence >= self.confidence_threshold
        entropy_ok = entropy <= self.entropy_threshold
        disagreement_ok = disagreement <= self.disagreement_threshold
        accepted = confidence_ok and entropy_ok and disagreement_ok and best_index < len(self.labels)

        label = self.labels[best_index] if accepted else "Inconnu"
        is_recyclable = label in RECYCLABLES

        # --- Détails par modèle (pour UI breakdown) ---
        per_model_details = []
        for i, name in enumerate(self.model_names):
            probs_i = per_model_avg[i]
            top_idx = int(np.argmax(probs_i))
            per_model_details.append({
                "name": name,
                "label": self.labels[top_idx] if top_idx < len(self.labels) else "?",
                "score": round(float(probs_i[top_idx]), 4),
                # Distribution complète : utile pour les bars de score dans l'UI
                "distribution": [
                    {"label": self.labels[j] if j < len(self.labels) else "?",
                     "score": round(float(probs_i[j]), 4)}
                    for j in range(len(probs_i))
                ],
            })

        # --- Top-3 prédictions agrégées ---
        sorted_idx = np.argsort(avg_probs)[::-1][:3]
        top_predictions = [
            {"label": self.labels[int(j)] if int(j) < len(self.labels) else "?",
             "score": round(float(avg_probs[int(j)]), 4)}
            for j in sorted_idx
        ]

        prediction = Prediction(
            label=label,
            confidence=confidence,
            points=self.points_for(label) if is_recyclable else 0,
            tri_status="RECYCLABLE" if is_recyclable else "NON_RECYCLABLE",
            uncertain=not accepted,
            entropy=entropy,
            disagreement=disagreement,
            accepted=accepted,
            per_model_top=per_model_top,
            per_model_details=per_model_details,
            top_predictions=top_predictions,
        )

        if not accepted:
            log.info(
                "Rejet incertitude : conf=%.3f (min %.2f) entropy=%.3f (max %.2f) disagree=%.3f (max %.2f) top-models=%s",
                confidence,
                self.confidence_threshold,
                entropy,
                self.entropy_threshold,
                disagreement,
                self.disagreement_threshold,
                per_model_top,
            )

        return prediction


def load_image(image_bytes: bytes) -> tuple[Image.Image, str]:
    """Charge + valide une image depuis ses bytes.

    Retourne `(image_rgb, format_upper)`. Le format est lu **avant** le
    `.convert("RGB")` qui détache l'image de son format d'origine.
    Lève `UnidentifiedImageError` / `OSError` si invalide.
    """
    Image.open(io.BytesIO(image_bytes)).verify()
    raw = Image.open(io.BytesIO(image_bytes))
    fmt = (raw.format or "").upper()
    return raw.convert("RGB"), fmt
