import base64
import io
import logging
import os
import uuid
from logging.handlers import RotatingFileHandler

import qrcode
import requests
from PIL import UnidentifiedImageError
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO
from flask_talisman import Talisman
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from inference import Classifier, EnsembleClassifier, load_image
from inference_llm import GeminiClassifier
from scan_repository import ScanRepository
from security import HmacVerifier, make_require_api_key, make_require_hmac
from settings import Settings
import tunnel_manager
from uploads_cleanup import start_background_cleanup

load_dotenv()
SETTINGS = Settings.from_env()

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("raycash")

# Audit log dans son propre fichier (rotation), niveau WARNING+
os.makedirs(os.path.dirname(SETTINGS.audit_log_path) or ".", exist_ok=True)
audit_handler = RotatingFileHandler(
    SETTINGS.audit_log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
)
audit_handler.setLevel(logging.WARNING)
audit_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.getLogger("raycash.audit").addHandler(audit_handler)
logging.getLogger("raycash.audit").setLevel(logging.WARNING)
logging.getLogger("raycash.audit").propagate = False

# --- AVERTISSEMENTS DE CONFIG ---
if not SETTINGS.api_key:
    log.warning("RAYCASH_API_KEY non défini — toutes les requêtes seront rejetées.")
if not SETTINGS.allowed_origins:
    log.warning("ALLOWED_ORIGINS non défini — CORS verrouillé.")
if not SETTINGS.hmac_secret:
    log.info("RAYCASH_HMAC_SECRET non défini — HMAC signing désactivé (mode rétro-compat).")

# --- APP & EXTENSIONS ---
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = SETTINGS.max_upload_bytes

CORS(app, resources={r"/*": {"origins": SETTINGS.allowed_origins or []}})
# Socket.IO accepte les connexions depuis n'importe quelle origine : le tunnel
# Cloudflare a une URL dynamique impossible à prédire à l'avance, et les pages
# /pair, /scanner, /session sont servies par le serveur lui-même (auth via cookie
# de session). Les endpoints sensibles (/predict, /scans, /esp_signal) restent
# protégés par X-API-Key indépendamment de l'origine Socket.IO.
socketio = SocketIO(app, cors_allowed_origins="*")
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[SETTINGS.rate_limit_default],
)

# Security headers via Talisman. force_https=False car la terminaison TLS est
# faite par le reverse proxy ou un tunnel HTTPS (cf. README).
#
# CSP relâchée pour permettre les pages /scanner et /pair :
# - script-src 'unsafe-inline' : le JS de la page scanner est inline (simplicité)
# - style-src 'unsafe-inline' : idem pour le CSS
# - media-src + img-src : pour la vidéo/image de la caméra et les QR base64
# - connect-src 'self' : pour fetch /predict depuis la même origine
Talisman(
    app,
    force_https=False,
    strict_transport_security=True,
    content_security_policy={
        "default-src": "'self'",
        "img-src": "'self' data: blob:",
        "media-src": "'self' blob:",
        # Socket.IO client depuis cdn.socket.io.
        # 'unsafe-inline' pour notre <script> inline dans les templates.
        "script-src": "'self' 'unsafe-inline' https://cdn.socket.io",
        "style-src": "'self' 'unsafe-inline'",
        # WebSocket Socket.IO sur 'self' (ws://) + CDN HTTP pour fallback long-poll.
        "connect-src": "'self' wss: ws: https://cdn.socket.io",
        "frame-ancestors": "'none'",
    },
    frame_options="DENY",
    referrer_policy="no-referrer",
)

os.makedirs(SETTINGS.upload_folder, exist_ok=True)

# --- CLASSIFIER (single, ensemble, ou Gemini selon Settings) ---
def _build_classifier():
    """Choisit le backend de classification selon `CLASSIFIER_BACKEND` :
    - "gemini" → GeminiClassifier (Vision API Google, qualité réelle ~95%+)
    - "local" (défaut) → EnsembleClassifier ou Classifier selon ENSEMBLE_ENABLED.

    En cas de mauvaise config Gemini (clé manquante / SDK absent), fallback
    automatique sur local pour ne pas casser le serveur.
    """
    backend = SETTINGS.classifier_backend
    if backend == "gemini":
        if not SETTINGS.gemini_api_key:
            log.warning("CLASSIFIER_BACKEND=gemini mais GEMINI_API_KEY vide — fallback local")
        else:
            try:
                clf = GeminiClassifier(
                    api_key=SETTINGS.gemini_api_key,
                    model_name=SETTINGS.gemini_model,
                )
                if clf.ready:
                    log.info("Backend = GEMINI (%s)", clf.model_name)
                    return clf
                log.warning("GeminiClassifier non prêt — fallback local")
            except Exception as e:
                log.exception("Erreur init Gemini, fallback local : %s", e)

    if SETTINGS.ensemble_enabled and os.path.isdir(SETTINGS.ensemble_dir):
        tflite_files = sorted(
            f for f in os.listdir(SETTINGS.ensemble_dir) if f.endswith(".tflite")
        )
        if tflite_files:
            model_paths = [os.path.join(SETTINGS.ensemble_dir, f) for f in tflite_files]
            log.info("Backend = ENSEMBLE local (%d modèles): %s", len(model_paths), tflite_files)
            return EnsembleClassifier(
                model_paths=model_paths,
                label_path=SETTINGS.ensemble_label_path,
                confidence_threshold=SETTINGS.ensemble_min_confidence,
                entropy_threshold=SETTINGS.ensemble_max_entropy,
                disagreement_threshold=SETTINGS.ensemble_max_disagreement,
            )
        log.warning("ENSEMBLE_ENABLED mais aucun .tflite dans %s — fallback Classifier simple", SETTINGS.ensemble_dir)

    log.info("Backend = SINGLE local (model_path=%s)", SETTINGS.model_path)
    return Classifier(
        model_path=SETTINGS.model_path,
        label_path=SETTINGS.label_path,
        confidence_threshold=SETTINGS.confidence_threshold,
    )


classifier = _build_classifier()

# --- PERSISTENCE ---
scan_repo = ScanRepository(db_path=SETTINGS.db_path)

# --- ESP32 HTTP CLIENT (connection pool partagé) ---
esp32_session = requests.Session()
esp32_session.headers.update({"X-API-Key": SETTINGS.api_key})

# IP de l'ESP32 capturee dynamiquement a chaque /esp_signal entrant. Permet
# au callback /servo de joindre l'ESP32 sans config statique dans .env :
# l'ESP32 "annonce" sa propre IP a chaque fois qu'il declenche un scan.
# Si vide (ex: au boot avant tout signal ESP32), on retombe sur SETTINGS.esp32_ip
# du .env comme fallback.
_esp32_runtime_ip: str | None = None

def _get_esp32_url() -> str:
    """Retourne l'URL ESP32 a utiliser pour le callback /servo. Priorite a
    l'IP capturee a runtime (auto-decouverte), fallback sur .env si vide."""
    if _esp32_runtime_ip:
        return f"http://{_esp32_runtime_ip}"
    return SETTINGS.esp32_ip

# --- SECURITY DECORATORS ---
require_api_key = make_require_api_key(SETTINGS.api_key)
hmac_verifier = HmacVerifier(SETTINGS.hmac_secret, SETTINGS.hmac_max_skew_seconds)
require_hmac = make_require_hmac(hmac_verifier)


# --- HANDLERS D'ERREUR ---
@app.errorhandler(RequestEntityTooLarge)
def handle_too_large(_e):
    return (
        jsonify({"error": "fichier trop volumineux", "max_bytes": SETTINGS.max_upload_bytes}),
        413,
    )


# --- ROUTES TECHNIQUES ---
@app.route("/health")
@limiter.exempt
def health():
    """Liveness probe — l'app répond, c'est tout."""
    return jsonify({"status": "ok"}), 200


@app.route("/ready")
@limiter.exempt
def ready():
    """Readiness probe — vrai si le modèle IA est chargé."""
    if classifier.ready:
        return jsonify({"status": "ready"}), 200
    return jsonify({"status": "not_ready", "reason": "model_unavailable"}), 503


# --- ROUTES MÉTIER ---
@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    # Accepte la cle via header X-API-Key (clients programmatiques) OU query
    # string ?key=... (img tags dans session.html, qui ne peuvent pas envoyer
    # de headers custom).
    provided = request.headers.get("X-API-Key") or request.args.get("key", "")
    if not SETTINGS.api_key or provided != SETTINGS.api_key:
        return jsonify({"error": "unauthorized"}), 401
    safe = secure_filename(filename)
    if safe != filename or not safe:
        return jsonify({"error": "filename invalide"}), 400
    return send_from_directory(SETTINGS.upload_folder, safe)


@app.route("/esp_signal", methods=["POST"])
@limiter.limit(lambda: SETTINGS.rate_limit_esp_signal)
@require_api_key
@require_hmac
def esp_signal():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in {"START", "STOP"}:
        return jsonify({"error": "action invalide"}), 400

    # Auto-decouverte de l'IP ESP32 : on memorise l'adresse source du POST
    # pour pouvoir lui renvoyer le verdict sur /servo sans ESP32_IP statique.
    global _esp32_runtime_ip
    src_ip = request.remote_addr
    if src_ip and src_ip != _esp32_runtime_ip:
        _esp32_runtime_ip = src_ip
        log.info("ESP32 IP auto-decouverte : %s", _esp32_runtime_ip)

    log.info("Signal matériel reçu de l'ESP32 : %s", action)
    if action == "START":
        socketio.emit("command_from_esp", {"action": "START"})
        return jsonify({"status": "capture_triggered"}), 200
    return jsonify({"status": "ignored"}), 200


def _validate_and_load_image(file):
    """Valide MIME + taille + décodage PIL. Retourne (img, fmt) ou (None, error_response)."""
    if file.mimetype not in SETTINGS.allowed_mime_types:
        return None, (jsonify({"error": f"type MIME non autorisé: {file.mimetype}"}), 415)

    image_bytes = file.read()
    if not image_bytes:
        return None, (jsonify({"error": "fichier vide"}), 400)
    if len(image_bytes) > SETTINGS.max_upload_bytes:
        return None, (jsonify({"error": "fichier trop volumineux"}), 413)

    try:
        img, fmt = load_image(image_bytes)
    except (UnidentifiedImageError, OSError):
        return None, (jsonify({"error": "fichier image invalide"}), 400)

    if fmt not in SETTINGS.allowed_image_formats:
        return None, (jsonify({"error": "format image non autorisé"}), 415)

    if img.width > SETTINGS.max_image_dimension or img.height > SETTINGS.max_image_dimension:
        return None, (jsonify({"error": "image trop grande"}), 413)

    return img, None


def _predict_rate_limit():
    """Rate limit dynamique pour /predict.

    En mode `record=true` (scans réels), on garde le rate limit standard
    (ex: 30 per minute) — empêche un spam de la DB.
    En mode `record=false` (live preview), on relève à 200/min car le phone
    appelle ~2-3 fois/sec en boucle pour le live scan.
    """
    record = request.args.get("record", "true").lower() not in ("0", "false", "no")
    if not record:
        return os.environ.get("RATE_LIMIT_PREDICT_LIVE", "200 per minute")
    return SETTINGS.rate_limit_predict


@app.route("/predict", methods=["POST"])
@limiter.limit(_predict_rate_limit)
@require_api_key
def predict():
    """Classifie une OU plusieurs images (multi-vue).

    Query params :
    - `record` (défaut `true`) : si `false`, ne sauvegarde PAS l'image sur disque,
      ne persiste PAS le scan en DB, et n'émet PAS l'event Socket.IO. Utilisé par
      le mode "live" du phone qui appelle /predict en boucle ~2 fois/sec :
      on ne veut pas polluer la DB ni l'historique avec chaque frame intermédiaire,
      seulement la frame finale "lockée" par le smoothing client-side.

    Le client envoie :
    - `image` : une seule image (compat rétro)
    - OU `image[]` / `images` : plusieurs images, le serveur fait un vote multi-vue
    """
    record = request.args.get("record", "true").lower() not in ("0", "false", "no")
    if not classifier.ready:
        return jsonify({"error": "modèle IA indisponible"}), 503

    # Récupère TOUTES les images uploadées sous "image", "image[]" ou "images"
    files = []
    for key in ("image", "image[]", "images"):
        files.extend(request.files.getlist(key))
    if not files:
        return jsonify({"error": "Aucune image reçue"}), 400

    max_imgs = SETTINGS.ensemble_max_images_per_request
    if len(files) > max_imgs:
        return jsonify({"error": f"trop d'images (max {max_imgs})", "received": len(files)}), 413

    imgs = []
    image_urls = []
    for file in files:
        img, err = _validate_and_load_image(file)
        if err is not None:
            return err
        imgs.append(img)

    try:
        # Persiste sur disque SEULEMENT si record=True (mode normal).
        # En mode live (record=False), on ne sauvegarde rien — l'image est juste
        # classifiée à la volée et l'URL n'est pas retournée.
        if record:
            for img in imgs:
                filename = f"capture_{uuid.uuid4().hex}.jpg"
                img.save(os.path.join(SETTINGS.upload_folder, filename))
                image_urls.append(f"/uploads/{filename}")

        # Classification : préfère classify_multi si dispo (ensemble + Gemini),
        # sinon mono-image pour le Classifier basique.
        if hasattr(classifier, "classify_multi"):
            pred = classifier.classify_multi(imgs)
        else:
            pred = classifier.classify(imgs[0])

        log_level = log.info if record else log.debug
        log_level(
            "Résultat%s : %s (conf=%.2f%%, accepted=%s, n_views=%d) → %d pts",
            "" if record else " [live]",
            pred.label, pred.confidence * 100, pred.accepted, len(imgs), pred.points,
        )

        # DB + ESP32 + emit Socket.IO : seulement en mode record (= scan "validé"
        # par le client après stabilité). En live, on ne fait rien de tout ça.
        if record and pred.accepted:
            try:
                scan_repo.insert(
                    label=pred.label,
                    confidence=pred.confidence,
                    points=pred.points,
                    tri_status=pred.tri_status,
                    image_path=image_urls[0] if image_urls else "",
                )
            except Exception:
                log.exception("Persistance du scan a échoué")

            # Pilotage du servo ESP32 — best-effort. Body format : "TRI_STATUS:points"
            # ex: "RECYCLABLE:40" ou "NON_RECYCLABLE:0". L'ESP32 parse pour afficher
            # les points sur le LCD.
            try:
                esp32_session.post(
                    f"{_get_esp32_url()}/servo",
                    data=f"{pred.tri_status}:{pred.points}",
                    timeout=SETTINGS.esp32_timeout_seconds,
                )
            except requests.RequestException as e:
                log.warning("Impossible de joindre l'ESP32 : %s", e)

        response = {
            "label": pred.label,
            "confidence": f"{pred.confidence * 100:.1f}%",
            "confidence_raw": round(pred.confidence, 4),
            "tri_status": pred.tri_status,
            "points": pred.points,
            "image_urls": image_urls,
            # Compat rétro : l'ancien client lit `image_url` au singulier.
            # En mode live (image_urls vide), on renvoie une string vide.
            "image_url": image_urls[0] if image_urls else "",
            # Champs ensemble (toujours présents pour simplicité)
            "accepted": pred.accepted,
            "uncertain": pred.uncertain,
            "entropy": round(pred.entropy, 3),
            "disagreement": round(pred.disagreement, 3),
            "per_model_top": pred.per_model_top,
            "per_model_details": pred.per_model_details,
            "top_predictions": pred.top_predictions,
            "n_views": len(imgs),
            # Métadonnées de l'ensemble (référence pour l'UI)
            "ensemble_metadata": {
                "n_models": len(pred.per_model_details) if pred.per_model_details else 1,
                "model_names": [d["name"] for d in pred.per_model_details],
                "global_accuracy": SETTINGS.ensemble_test_accuracy,
                "thresholds": {
                    "min_confidence": SETTINGS.ensemble_min_confidence,
                    "max_entropy": SETTINGS.ensemble_max_entropy,
                    "max_disagreement": SETTINGS.ensemble_max_disagreement,
                },
            },
        }
        if not pred.accepted:
            response["hint"] = "Repositionne l'objet sous une meilleure lumière et réessaie."

        # En mode record uniquement, on broadcast à la page /session du laptop.
        # En mode live, le phone affiche déjà localement — pas besoin d'inonder
        # le laptop avec chaque frame intermédiaire.
        if record:
            try:
                socketio.emit("new_scan", response)
            except Exception:
                log.exception("Impossible d'émettre new_scan")

        return jsonify(response)

    except Exception:
        log.exception("Erreur traitement image")
        return jsonify({"error": "erreur interne"}), 500


@app.route("/scans")
@require_api_key
def list_scans():
    """Historique paginé serveur. Source de vérité long terme."""
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return jsonify({"error": "paramètres invalides"}), 400
    scans = scan_repo.list_recent(limit=limit, offset=offset)
    return jsonify(
        {
            "total": scan_repo.count(),
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "id": s.id,
                    "label": s.label,
                    "confidence": s.confidence,
                    "points": s.points,
                    "tri_status": s.tri_status,
                    "image_url": s.image_path,
                    "created_at": s.created_at,
                }
                for s in scans
            ],
        }
    )


# --- PAGES WEB POUR LE PAIRING SMARTPHONE ---
def _generate_qr_base64(payload: str) -> str:
    """Génère un QR code PNG en base64, prêt à embarquer dans `<img src=...>`."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


@app.route("/pair")
@limiter.exempt
def pair():
    """Page affichée sur le laptop. Génère un QR code pointant vers /scanner.

    L'API key est intégrée dans l'URL pour que le téléphone puisse appeler
    /predict sans configuration manuelle. C'est acceptable en démo LAN car :
    1. La page est affichée brièvement
    2. Le réseau est privé
    3. La clé peut être tournée si compromise
    Pour la prod, utiliser un token court terme à la place.
    """
    # Ordre de résolution de l'URL publique (du + spécifique au + générique) :
    # 1. PUBLIC_URL configuré explicitement dans .env (override manuel)
    # 2. URL captée dynamiquement depuis le tunnel cloudflared auto-lancé
    # 3. Détection via headers de proxy (X-Forwarded-Host ou Host rewrité)
    # 4. LAN local (cas dev sans tunnel)
    auto_tunnel_url = None
    mgr = tunnel_manager.get_default()
    if mgr is not None:
        auto_tunnel_url = mgr.get_url()

    if SETTINGS.public_url:
        base = SETTINGS.public_url
        is_local = base.startswith("http://")
    elif auto_tunnel_url:
        base = auto_tunnel_url
        is_local = False
    else:
        host = request.headers.get("X-Forwarded-Host") or request.host or ""
        public_proxy_suffixes = (".trycloudflare.com", ".ngrok-free.app", ".ngrok.io", ".loca.lt")
        is_public_proxy = any(host.endswith(s) for s in public_proxy_suffixes)

        if is_public_proxy:
            base = f"https://{host}"
            is_local = False
        elif request.headers.get("X-Forwarded-Host"):
            # Reverse proxy "classique" (nginx, Caddy) : on respecte X-Forwarded-Proto
            proto = request.headers.get("X-Forwarded-Proto", "https")
            base = f"{proto}://{request.headers['X-Forwarded-Host']}"
            is_local = False
        else:
            # LAN pur, pas de proxy : on prend ce que voit Flask (ex: http://192.168.X.X:5000/)
            base = request.host_url.rstrip("/")
            is_local = base.startswith("http://")

    scanner_url = f"{base}/scanner?key={SETTINGS.api_key}"
    qr_b64 = _generate_qr_base64(scanner_url)
    return render_template(
        "pair.html",
        qr_b64=qr_b64,
        scanner_url=scanner_url,
        is_local=is_local,
    )


@app.route("/scanner")
@limiter.exempt
def scanner():
    """Page chargée sur le téléphone après scan du QR. Utilise getUserMedia."""
    # La clé API arrive en query string (depuis le QR) — sans validation
    # serveur ici (la validation se fait via X-API-Key sur /predict).
    api_key = request.args.get("key", "")
    return render_template("scanner.html", api_key=api_key)


@app.route("/session")
@limiter.exempt
def session_page():
    """Page laptop qui affiche en live les scans du téléphone via Socket.IO."""
    # api_key passe en template pour permettre aux img tags d'authentifier
    # leur requete /uploads/... via ?key=... (un img ne peut pas envoyer
    # X-API-Key header).
    return render_template("session.html", api_key=SETTINGS.api_key)


# --- Socket.IO event handlers ---
@socketio.on("phone_connected")
def _on_phone_connected(data):
    """Relais : le téléphone signale qu'il vient d'ouvrir /scanner. On rebroadcast
    l'event à tous les clients (notamment le laptop sur /pair, qui redirige
    vers /session)."""
    log.info("Phone connecté au scanner : %s", (data or {}).get("ua", "?"))
    socketio.emit("phone_connected", data or {})


@socketio.on("disconnect")
def _on_disconnect():
    # On n'a pas vraiment de moyen propre de distinguer phone/laptop ici,
    # mais on émet une notif générique au cas où la page session voudrait
    # afficher "scanner déconnecté".
    socketio.emit("phone_disconnected", {})


if __name__ == "__main__":
    start_background_cleanup(
        SETTINGS.upload_folder,
        max_files=SETTINGS.cleanup_max_files,
        max_age_seconds=SETTINGS.cleanup_max_age_seconds,
        interval_seconds=SETTINGS.cleanup_interval_seconds,
    )

    # Démarre cloudflared en sous-processus, capte l'URL HTTPS générée, et
    # l'expose aux requêtes /pair via tunnel_manager.get_default().get_url().
    # Si PUBLIC_URL est explicitement set, on suppose que l'utilisateur gère
    # son tunnel à part, on ne lance rien.
    if SETTINGS.auto_tunnel and not SETTINGS.public_url:
        mgr = tunnel_manager.init_default(
            local_url=f"http://localhost:{SETTINGS.port}",
            cloudflared_path=SETTINGS.cloudflared_path or None,
            startup_timeout=SETTINGS.auto_tunnel_timeout,
        )
        if mgr.available:
            if mgr.start():
                log.info("Auto-tunnel actif → URL HTTPS dynamique : %s", mgr.get_url())
            else:
                log.warning("Auto-tunnel lancé mais URL non capturée dans le délai imparti.")
        else:
            log.info("cloudflared introuvable — auto-tunnel désactivé (utilise PUBLIC_URL si besoin).")
    elif SETTINGS.public_url:
        log.info("PUBLIC_URL explicit : %s (auto-tunnel ignoré)", SETTINGS.public_url)

    log.info("Démarrage du serveur sur http://%s:%s", SETTINGS.host, SETTINGS.port)
    try:
        socketio.run(
            app,
            host=SETTINGS.host,
            port=SETTINGS.port,
            debug=False,
            use_reloader=False,
            allow_unsafe_werkzeug=True,
        )
    finally:
        # Nettoyage propre du sous-processus cloudflared en cas de Ctrl+C.
        mgr = tunnel_manager.get_default()
        if mgr is not None:
            mgr.stop()
