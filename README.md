# RayCash Pro — Reverse Vending Machine + Vision AI

**Branche `buildcheck`** — snapshot V2 du projet RayCash. Ajoute Gemini Vision AI, le pairing par QR code, le tunnel HTTPS automatique (cloudflared) et l'effet de récompense visuelle « HOURRA » pour les déchets recyclables.

> Statut : **prototype** (démo / hackathon). Ne pas exposer en production sans durcissement — voir [Sécurité](#sécurité).

---

## Ce qui change par rapport au V1 (`main`)

| Composant | V1 (`main`) | V2 (`buildcheck` — ce code) |
|---|---|---|
| Classificateur | TFLite single-model V0 (~80 % offline) | Gemini 2.5 Flash via API (cloud) **+** ensemble V4 TFLite (91,58 % offline, fallback) |
| Capture mobile | Live scanner (boucle ~85 req/min) | Capture unique (tap → flash shutter → loading → résultat) |
| Réseau LAN | Saisie manuelle de l'IP serveur | **QR pairing** : laptop affiche un QR → téléphone scanne → URL HTTPS auto |
| Exposition publique | LAN uniquement | **Tunnel Cloudflare auto** (lancé par le serveur Flask) |
| UX résultat | Liste texte | Plein-écran caméra + bannière « HOURRA » + confettis si recyclable |
| Backend swappable | non | `CLASSIFIER_BACKEND=gemini` | `local` (TFLite) |

---

## Architecture

```
┌──────────────────────────┐         ┌──────────────────────────┐
│ Téléphone (Chrome/Edge)  │ HTTPS   │  cloudflared (auto)      │
│  /scanner?key=...        │◀───────▶│  trycloudflare.com tunnel │
│  - capture caméra        │         └────────────┬─────────────┘
│  - HOURRA + confettis    │                      │
└──────────────────────────┘                      ▼
                                        ┌──────────────────────┐
                                        │   Flask + Socket.IO  │
                                        │   /predict (Gemini)  │
┌──────────────────────────┐  X-API-Key │   /pair (QR code)    │
│ Laptop (admin)           │◀──────────▶│   /session (live)    │
│  /pair  ← QR code        │            │   /esp_signal (HMAC) │
│  /session  ← live result │            └──────────┬───────────┘
└──────────────────────────┘                       │ X-API-Key
                                                   ▼
                                        ┌──────────────────────┐
                                        │   ESP32 (Arduino)    │
                                        │   bouton + servo +   │
                                        │   buzzer (port 80)   │
                                        └──────────────────────┘
```

### Serveur Flask ([server/](server/))

- [main.py](server/main.py) — entrypoint Flask + Socket.IO. Routes : `/predict`, `/pair`, `/scanner`, `/session`, `/esp_signal`, `/health`, `/ready`, `/scans`.
- [inference_llm.py](server/inference_llm.py) — `GeminiClassifier` : appels structurés `response_schema={label, confidence, reasoning}`, `ThinkingConfig(thinking_budget=0)`, retry **uniquement** sur 503/timeout (jamais sur 429 pour ne pas gâcher le quota).
- [inference.py](server/inference.py) — ensemble V4 TFLite (EfficientNetV2-B0 + MobileNetV2 + ResNet50) avec uncertainty quantification (entropy, disagreement). Fallback si Gemini désactivé.
- [tunnel_manager.py](server/tunnel_manager.py) — lance `cloudflared tunnel --url http://localhost:5000`, capture l'URL HTTPS via regex `https?://[a-z0-9-]+\.trycloudflare\.com`.
- [settings.py](server/settings.py) — config typée depuis `.env`.

### Application Flutter ([lib/](lib/))

L'app Flutter reste fonctionnelle (`main.dart`, `app_state.dart`, `api_client.dart`, …) mais la démo V2 passe désormais par la **page web** [server/templates/scanner.html](server/templates/scanner.html), pour fonctionner sur n'importe quel téléphone sans installer d'APK.

### ESP32 ([arduino_code/](arduino_code/))

Inchangé par rapport au V1. Le firmware déclenche `/esp_signal` (HMAC) et reçoit `/servo` pour piloter la trappe.

### ML notebooks ([ml/notebooks/](ml/notebooks/))

| Notebook | Modèle | Test accuracy |
|---|---|---|
| `train_raycash.ipynb` | V1 — MobileNetV2 fine-tuned | ~80 % |
| `train_raycash_v2.ipynb` | V2 — EfficientNetV2-B0 | ~84 % |
| `train_raycash_v3.ipynb` | V3 — V2 + augmentations | 83,5 % |
| `train_raycash_v4_ensemble.ipynb` | V4 — vote 3 modèles + UQ | **91,58 %** |

Les `.tflite` exportés sont versionnés sous [server/models/](server/models/) (`V0`, `V1`, `V2`, `ensemble`).

---

## Quickstart

### 0. Prérequis

- Python 3.11+
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) installé et dans le `PATH` (ou laisse `tunnel_manager` le détecter via WinGet)
- Une clé **Gemini API** sur un projet Google Cloud avec **billing activé** (sinon `gemini-2.0-flash` répond 404 « no longer available to new users »)
- (Optionnel) Flutter 3.x si tu veux rebuilder l'APK

### 1. Configurer le serveur

```bash
cd server
cp .env.example .env
# Edit .env — au minimum :
#   RAYCASH_API_KEY=<32+ caractères random>
#   GEMINI_API_KEY=<ta clé>
#   GEMINI_MODEL=gemini-2.5-flash       # ⚠ gemini-2.0-flash est bloqué sur nouveaux projets
#   CLASSIFIER_BACKEND=gemini           # ou "local" pour utiliser l'ensemble TFLite

python -m venv venv
. venv/Scripts/activate    # PowerShell : venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Le serveur va :
1. Charger le backend de classification (`GeminiClassifier prêt` dans les logs).
2. Lancer cloudflared en sous-processus et capturer l'URL HTTPS (`Tunnel HTTPS disponible : https://xxx.trycloudflare.com`).
3. Servir Flask sur `http://0.0.0.0:5000`.

### 2. Pairer le téléphone

Sur le laptop, ouvre **http://localhost:5000/pair** dans un navigateur.

- Un **QR code** s'affiche pointant vers `https://xxx.trycloudflare.com/scanner?key=<RAYCASH_API_KEY>`.
- Scanne avec le téléphone — la page caméra s'ouvre en HTTPS (donc `getUserMedia` autorisé).
- Le laptop bascule automatiquement vers `/session` (Socket.IO `phone_connected`) et affiche les scans en temps réel.

### 3. Utiliser le scanner

1. Pointe la caméra du téléphone vers un déchet.
2. Tap sur l'écran → flash shutter → freeze image → loading spinner → résultat plein écran.
3. Si **recyclable** (Plastique / Aluminium / Verre / Papier) : bannière **« HOURRA ! »** + confettis + **+ X PTS**.
4. Si non recyclable : message d'incitation au tri.

---

## Variables d'environnement clés

| Variable | Défaut | Rôle |
|---|---|---|
| `RAYCASH_API_KEY` | — | Clé partagée par tous les clients (`X-API-Key`). À régénérer. |
| `CLASSIFIER_BACKEND` | `gemini` | `gemini` (cloud) ou `local` (ensemble TFLite). |
| `GEMINI_API_KEY` | — | Clé Google AI Studio (projet billing activé). |
| `GEMINI_MODEL` | `gemini-2.5-flash` | `gemini-2.0-flash` est désormais bloqué sur les nouveaux projets. |
| `AUTO_TUNNEL` | `true` | Lance cloudflared au boot et capture l'URL. |
| `PUBLIC_URL` | (vide) | Si fourni, écrase la détection auto (utile derrière nginx/ngrok). |
| `RAYCASH_HMAC_SECRET` | — | Signe les payloads `/esp_signal`. |
| `RATE_LIMIT_PREDICT` | `200 per minute` | flask-limiter sur `/predict`. |
| `ENSEMBLE_ENABLED` | `false` | Active l'ensemble V4 si `CLASSIFIER_BACKEND=local`. |
| `ENSEMBLE_MIN_CONFIDENCE` | `0.70` | Seuil de rejet UQ. |

Voir [server/.env.example](server/.env.example) pour la liste complète.

---

## Tests

```bash
cd server
RAYCASH_API_KEY=test-api-key ALLOWED_ORIGINS=http://localhost pytest -v
```

Couvre : auth API key, validation MIME/taille/dimensions, `/predict`, `/esp_signal`, classification mock.

```bash
flutter test
```

26 tests : `app_state`, `scan_result`, `scoring`, `scan_list_tile`, `status_badge`.

---

## Sécurité

⚠ **Ce prototype est conçu pour démo / hackathon, pas pour la production.**

### Ce qui est en place

- `X-API-Key` sur toutes les routes (sauf `/health`, `/pair`, `/scanner` qui valident via la query string).
- CORS verrouillé via `ALLOWED_ORIGINS` (sauf Socket.IO en `*` pour permettre le tunnel — accepté car `/predict` reste protégé).
- Rate limiting `flask-limiter` (`200 req/min` sur `/predict`, `60` sur `/esp_signal`).
- HMAC anti-rejeu sur `/esp_signal` (`HMAC_MAX_SKEW_SECONDS=60`).
- Validation uploads : MIME, taille (`MAX_UPLOAD_BYTES`), dimensions (`MAX_IMAGE_DIMENSION`).
- Secrets externalisés : `server/.env` + `arduino_code/**/secrets.h` (les deux gitignored).
- Cleanup auto `server/uploads/` (≤ 500 fichiers, < 7 jours).
- TLS via cloudflared (Cloudflare termine TLS, traffic LAN → tunnel reste local).

### Ce qui manque pour la production

- Pas d'authentification utilisateur réelle (la clé API est partagée entre tous les clients).
- La clé API circule dans la query string du QR code (visible si le QR est filmé / partagé).
- L'ESP32 sert encore en HTTP en clair sur le LAN (`WiFiClientSecure` non configuré).
- Pas de rotation automatique de `RAYCASH_API_KEY` / `RAYCASH_HMAC_SECRET`.
- Quick Tunnel Cloudflare = URL aléatoire qui change à chaque restart. Pour une URL stable : Cloudflare Tunnel avec compte (`cloudflared tunnel create`).
- Pas de monitoring / alerting.

### Pour durcir (LAN → prod)

1. Migrer vers **Cloudflare Tunnel managé** (URL stable, JWT validation) ou nginx + Let's Encrypt en frontal.
2. Remplacer la clé API partagée par un vrai mécanisme d'identité utilisateur (OIDC, magic link, etc.).
3. Auth des téléphones via token court par session de pairing (au lieu de partager `RAYCASH_API_KEY`).
4. Passer l'ESP32 en HTTPS (`WiFiClientSecure::setCACert`).
5. Externaliser le rate limiter (Redis) — actuellement en mémoire process, perdu au restart.

---

## Roadmap (idées non-implémentées)

- [ ] Combo **barcode + Vision AI** (vote ensemble + lookup OpenFoodFacts pour pré-classifier les bouteilles connues).
- [ ] Mode offline complet (ensemble V4 TFLite sur device).
- [ ] Auth utilisateur réelle + portefeuille persistant côté serveur (au lieu de SharedPreferences local).
- [ ] Webhook Mobile Money (MVola, Orange Money) pour le payout.
- [ ] Dashboard admin (stats par quartier, par matériau, par session).

---

## Licence

Voir [LICENSE](LICENSE). Code original `tsi23tohaina/raycash` ; cette branche est une réécriture / extension V2 réalisée dans le cadre d'une démo.
