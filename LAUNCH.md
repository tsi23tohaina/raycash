# 🚀 LAUNCH — Lancer RayCash en local (cheatsheet démo)

Ce document liste les étapes exactes pour lancer une démo end-to-end depuis ta machine.
**Toutes les commandes supposent que tu es dans `D:\Lucas\raycash`.**

---

## 🔑 Clé API actuelle (générée localement)

> Cette clé est dans `server/.env` (gitignored). Elle doit être identique partout.

```
RAYCASH_API_KEY = <RAYCASH_API_KEY>
```

Pour en générer une nouvelle :
```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 1️⃣ Démarrer le serveur Flask

```powershell
# Depuis D:\Lucas\raycash
cd server

# Activer venv (une seule fois par session terminal, optionnel si on passe par le chemin complet)
.\venv\Scripts\Activate.ps1

# Lancer (l'UTF-8 évite le crash console Windows sur les emojis)
$env:PYTHONIOENCODING="utf-8"
python main.py
```

✅ **Sortie attendue** :
```
2026-… [INFO] raycash.inference: Modèle IA chargé (models/model.tflite)
2026-… [INFO] raycash: Démarrage du serveur sur http://0.0.0.0:5000
 * Running on http://127.0.0.1:5000
 * Running on http://<ton-ip-LAN>:5000
```

Si tu vois un warning `RAYCASH_API_KEY non défini` → ton `.env` n'est pas chargé.
Si tu vois un warning `ALLOWED_ORIGINS non défini — CORS verrouillé` → idem, ou tu n'as pas inclus l'origine du client.

**Note** : le serveur est en HTTP en clair (pas TLS). C'est OK pour démo LAN. Pour prod réelle, mets nginx devant (voir section HTTPS du README).

### Vérifier que le serveur répond
```powershell
curl http://localhost:5000/health
# {"status":"ok"}

curl http://localhost:5000/ready
# {"status":"ready"}   ← le modèle TFLite est chargé

# Sans clé : 401 unauthorized
curl -X POST http://localhost:5000/predict
# {"error":"unauthorized"}

# Avec clé : 400 "Aucune image"
curl -X POST -H "X-API-Key: <RAYCASH_API_KEY>" http://localhost:5000/predict
# {"error":"Aucune image reçue"}
```

---

## 2️⃣ Lancer l'app Flutter (Chrome web)

Dans un **second terminal** :

```powershell
# Depuis D:\Lucas\raycash
$env:PATH += ";C:\dev\flutter\bin"   # à chaque nouveau terminal tant que PATH user pas pris en compte
flutter run -d chrome --web-port 8080 --dart-define=RAYCASH_API_KEY=<RAYCASH_API_KEY>
```

L'app s'ouvre sur `http://localhost:8080`. Chrome demande l'accès caméra → accepter.

**Tester le scan manuellement (sans ESP32 physique)** :
- Ouvre un second onglet : `http://localhost:8080`
- Long-press sur la bandeau noir "REYCASH : ATTENTE MATÉRIEL" → édite l'IP serveur si besoin (par défaut `127.0.0.1`)
- Pour déclencher une capture sans hardware, fais un curl signal :
  ```powershell
  curl -X POST -H "X-API-Key: <RAYCASH_API_KEY>" -H "Content-Type: application/json" -d '{\"action\":\"START\"}' http://localhost:5000/esp_signal
  ```
  L'app devrait recevoir le signal Socket.IO et prendre une photo automatiquement.

---

## 3️⃣ Flasher l'ESP32 (optionnel — pour démo hardware complète)

### Prérequis
1. Arduino IDE 2.x avec support ESP32 (`File → Preferences → Additional Boards Manager URLs` :
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`)
2. Lib `ESP32Servo` (Library Manager)

### Câblage minimal
| Composant | Pin ESP32 |
|---|---|
| Bouton poussoir (autre patte au GND) | GPIO 4 |
| Buzzer passif (autre patte au GND) | GPIO 18 |
| Signal servo SG90 | GPIO 19 |
| 5V servo | VIN (ou alim externe 5V) |
| GND servo | GND |

### Configuration `secrets.h`
```bash
cp arduino_code/raycash_apk_serveur_esp32/secrets.h.example \
   arduino_code/raycash_apk_serveur_esp32/secrets.h
```

Puis édite `secrets.h` :
```c
#define WIFI_SSID      "<ton-wifi>"
#define WIFI_PASSWORD  "<mot-de-passe-wifi>"
#define SERVER_URL     "http://<IP-LAN-de-ton-PC>:5000/esp_signal"
#define RAYCASH_API_KEY "<RAYCASH_API_KEY>"
```

Flash, puis ouvre le moniteur série (115200 baud) :
```
Connexion au Wi-Fi.....
Wi-Fi connecté !
IP ESP32 : 192.168.X.Y      ← note cette IP
Système RecyCash prêt.
```

### Mettre à jour `ESP32_IP` côté serveur
Édite `server/.env` :
```
ESP32_IP=http://192.168.X.Y
```
Puis redémarre le serveur (Ctrl+C + relance).

---

## 4️⃣ Tester end-to-end

1. ✅ Serveur Flask tourne (terminal 1)
2. ✅ Flutter sur Chrome (terminal 2)
3. ✅ ESP32 connecté au Wi-Fi (moniteur série OK)
4. **Appuie sur le bouton physique** :
   - Buzzer fait un BIP de 1000Hz / 500ms
   - L'ESP32 envoie `POST /esp_signal {action:"START"}` au serveur
   - Le serveur émet `command_from_esp` via Socket.IO
   - L'app Flutter prend une photo de la webcam
   - Envoie l'image à `/predict`
   - Le serveur classifie + appelle `POST /servo` sur l'ESP32
   - Le servo pivote vers `RECYCLABLE` (180°) ou `INCONNU` (0°), puis revient au repos (90°)
   - L'app affiche le label + points dans l'onglet "Session"

---

## 🛠️ Commandes utiles

### Tests
```powershell
# Tests Flutter (26 tests)
flutter test

# Tests Python (24 tests)
cd server
$env:RAYCASH_API_KEY="test-api-key"
$env:ALLOWED_ORIGINS="http://localhost"
.\venv\Scripts\pytest tests\ -v
```

### Lint
```powershell
flutter analyze
```

### Build APK release (nécessite Android SDK installé)
```powershell
flutter build apk --release --dart-define=RAYCASH_API_KEY=<RAYCASH_API_KEY>
```

### Voir l'historique des scans côté serveur
```powershell
curl -H "X-API-Key: <RAYCASH_API_KEY>" http://localhost:5000/scans
```

---

## 🩹 Troubleshooting

| Symptôme | Cause probable | Fix |
|---|---|---|
| `404` sur `/health` | Vieux serveur Python encore en cours sur le port 5000 | `Get-Process python \| Stop-Process -Force` puis relance |
| `401` sur toutes les routes Flutter | Mauvaise clé API ou `--dart-define` oublié | Vérifie que la clé Flutter == clé `.env` == clé `secrets.h` |
| `403` ou CORS error dans la console Chrome | Origine pas dans `ALLOWED_ORIGINS` | Ajoute `http://localhost:8080` dans `server/.env` |
| `Serveur injoignable` dans l'app | Mauvaise IP dans le long-press dialog | Renseigne `127.0.0.1` pour Chrome web local |
| Console serveur affiche `?` au lieu d'emojis | Encoding Windows (cp1252) | `$env:PYTHONIOENCODING="utf-8"` avant `python main.py` |
| `UNSUPPORTED MEDIA TYPE` sur upload | Image non JPEG/PNG/WEBP | Convertir l'image, ou ajouter le format dans `Settings.allowed_image_formats` |
| L'ESP32 ne se connecte pas au Wi-Fi | SSID ou mot de passe faux | Vérifie le moniteur série, et que ton routeur n'a pas un caractère spécial mal échappé |
| Servo ne pivote pas malgré ordre `/servo` 200 | Câblage 5V insuffisant | Alim externe 5V pour le servo, ne pas tirer sur le VIN de l'ESP32 |

---

## 🛑 Arrêter proprement

```powershell
# Terminal 1 (serveur) : Ctrl+C

# Terminal 2 (flutter) : taper "q" puis Entrée

# S'il reste des process Python en arrière-plan :
Get-Process python | Stop-Process -Force
```

---

## 📦 Ce qui marche / ce qui ne marche pas

✅ **Marche déjà** :
- Serveur Flask + TFLite + SQLite + Socket.IO
- App Flutter Web (Chrome / Edge)
- API key auth + rate limiting + CORS verrouillé
- HMAC anti-rejeu (activé via `RAYCASH_HMAC_SECRET`)
- Tests unitaires : 26 Flutter + 24 Python = 50 tests
- CI GitHub Actions (Android APK + pytest)

⚠️ **À tester avec hardware réel** :
- Code ESP32 (bouton/buzzer/servo) — écrit mais jamais flashé sur vrai ESP32
- Positions servo (0°/90°/180°) à calibrer physiquement

❌ **Pas implémenté** (pas demandé pour démo) :
- Build APK Android (Android SDK pas installé)
- Login utilisateur / paiement
- HTTPS / nginx reverse proxy
- Déploiement Docker / systemd
