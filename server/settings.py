"""Configuration centralisée du serveur, lue depuis l'environnement.

Tout est typé via une dataclass — terminé les `os.environ.get` éparpillés.
Permet aussi à pytest de fabriquer des `Settings(...)` à la volée pour tester
des combinaisons de config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Set


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    # --- Réseau ---
    host: str = "0.0.0.0"
    port: int = 5000
    # URL publique du serveur (tunnel HTTPS, reverse proxy, etc.).
    # Si renseigné, /pair génère le QR avec cette URL (et non avec request.host
    # qui peut être localhost selon où le navigateur ouvre /pair).
    # Exemple : https://beginning-thomson-donna-fuel.trycloudflare.com
    public_url: str = ""

    # Auto-tunnel : si activé, le serveur lance `cloudflared tunnel --url ...`
    # en sous-processus au démarrage et capture automatiquement l'URL HTTPS
    # générée. Plus besoin de mettre à jour PUBLIC_URL manuellement à chaque
    # redémarrage de cloudflared.
    auto_tunnel: bool = True
    cloudflared_path: str = ""  # Vide = auto-détection ($PATH puis C:\Tools)
    auto_tunnel_timeout: float = 30.0  # Délai max pour récupérer l'URL au boot

    # --- Auth ---
    api_key: str = ""
    hmac_secret: str = ""
    hmac_max_skew_seconds: int = 60

    # --- CORS ---
    allowed_origins: list[str] = field(default_factory=list)

    # --- Uploads ---
    max_upload_bytes: int = 5 * 1024 * 1024
    max_image_dimension: int = 4096
    allowed_mime_types: Set[str] = field(
        default_factory=lambda: {"image/jpeg", "image/png", "image/webp"}
    )
    # Magic bytes acceptés en sortie de PIL.Image.format
    allowed_image_formats: Set[str] = field(
        default_factory=lambda: {"JPEG", "PNG", "WEBP"}
    )

    # --- Inférence ---
    model_path: str = "models/model.tflite"
    label_path: str = "models/labels.txt"
    confidence_threshold: float = 0.45

    # Backend de classification :
    # - "local"   : TFLite seul (V3 single si ensemble disabled, sinon V4 ensemble)
    # - "gemini"  : Google Gemini Vision API (1500 req/jour gratuit)
    # - "hybrid"  : local en premier, fallback gemini si confidence < threshold
    #   (note : non implémenté encore, à coder ultérieurement)
    classifier_backend: str = "local"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # --- Ensemble (raycash-pro) ---
    # Si `ensemble_enabled` est True, le serveur charge plusieurs modèles
    # depuis `ensemble_dir` (1 fichier .tflite par modèle) et utilise un vote
    # moyen + uncertainty quantification au lieu d'un seul Classifier.
    ensemble_enabled: bool = True
    ensemble_dir: str = "models/ensemble"
    ensemble_label_path: str = "models/ensemble/labels.txt"
    # Seuils pour rejeter une prédiction comme "incertaine"
    ensemble_min_confidence: float = 0.70
    ensemble_max_entropy: float = 1.5
    ensemble_max_disagreement: float = 0.20
    # Nombre max de photos par requête /predict (multi-vue)
    ensemble_max_images_per_request: int = 5
    # Accuracy de référence de l'ensemble sur le test set (depuis notebook V4).
    # Affiché dans l'UI session pour donner du contexte. À mettre à jour quand
    # on re-train avec de nouvelles données.
    ensemble_test_accuracy: float = 0.9158

    # --- ESP32 ---
    esp32_ip: str = "http://192.168.1.42"
    esp32_timeout_seconds: float = 2.0

    # --- Rate limiting ---
    rate_limit_predict: str = "30 per minute"
    rate_limit_esp_signal: str = "60 per minute"
    rate_limit_default: str = "200 per minute"

    # --- Storage ---
    upload_folder: str = "uploads"
    db_path: str = "raycash.db"
    audit_log_path: str = "logs/audit.log"

    # --- Cleanup uploads ---
    cleanup_max_files: int = 500
    cleanup_max_age_seconds: int = 7 * 24 * 3600
    cleanup_interval_seconds: int = 3600

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "5000")),
            public_url=os.environ.get("PUBLIC_URL", "").rstrip("/"),
            auto_tunnel=os.environ.get("AUTO_TUNNEL", "true").lower() in ("1", "true", "yes"),
            cloudflared_path=os.environ.get("CLOUDFLARED_PATH", "").strip(),
            auto_tunnel_timeout=float(os.environ.get("AUTO_TUNNEL_TIMEOUT", "30")),
            api_key=os.environ.get("RAYCASH_API_KEY", ""),
            hmac_secret=os.environ.get("RAYCASH_HMAC_SECRET", ""),
            hmac_max_skew_seconds=int(os.environ.get("HMAC_MAX_SKEW_SECONDS", "60")),
            allowed_origins=_split_csv(os.environ.get("ALLOWED_ORIGINS", "")),
            max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024))),
            max_image_dimension=int(os.environ.get("MAX_IMAGE_DIMENSION", "4096")),
            confidence_threshold=float(os.environ.get("CONFIDENCE_THRESHOLD", "0.45")),
            esp32_ip=os.environ.get("ESP32_IP", "http://192.168.1.42"),
            esp32_timeout_seconds=float(os.environ.get("ESP32_TIMEOUT_SECONDS", "2")),
            rate_limit_predict=os.environ.get("RATE_LIMIT_PREDICT", "30 per minute"),
            rate_limit_esp_signal=os.environ.get("RATE_LIMIT_ESP_SIGNAL", "60 per minute"),
            rate_limit_default=os.environ.get("RATE_LIMIT_DEFAULT", "200 per minute"),
            upload_folder=os.environ.get("UPLOAD_FOLDER", "uploads"),
            db_path=os.environ.get("DB_PATH", "raycash.db"),
            audit_log_path=os.environ.get("AUDIT_LOG_PATH", "logs/audit.log"),
            cleanup_max_files=int(os.environ.get("CLEANUP_MAX_FILES", "500")),
            cleanup_max_age_seconds=int(os.environ.get("CLEANUP_MAX_AGE_SECONDS", str(7 * 24 * 3600))),
            cleanup_interval_seconds=int(os.environ.get("CLEANUP_INTERVAL_SECONDS", "3600")),
            ensemble_enabled=os.environ.get("ENSEMBLE_ENABLED", "true").lower() in ("1", "true", "yes"),
            ensemble_dir=os.environ.get("ENSEMBLE_DIR", "models/ensemble"),
            ensemble_label_path=os.environ.get("ENSEMBLE_LABEL_PATH", "models/ensemble/labels.txt"),
            ensemble_min_confidence=float(os.environ.get("ENSEMBLE_MIN_CONFIDENCE", "0.70")),
            ensemble_max_entropy=float(os.environ.get("ENSEMBLE_MAX_ENTROPY", "1.5")),
            ensemble_max_disagreement=float(os.environ.get("ENSEMBLE_MAX_DISAGREEMENT", "0.20")),
            ensemble_max_images_per_request=int(os.environ.get("ENSEMBLE_MAX_IMAGES", "5")),
            ensemble_test_accuracy=float(os.environ.get("ENSEMBLE_TEST_ACCURACY", "0.9158")),
            classifier_backend=os.environ.get("CLASSIFIER_BACKEND", "local").lower().strip(),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        )
