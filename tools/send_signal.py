"""Envoie un signal /esp_signal au serveur RayCash, signé HMAC.

Sert à simuler l'ESP32 pour les démos et le debug. Lit la clé API et le
secret HMAC depuis server/.env, calcule la signature, et POST le payload.

Usage:
    python tools/send_signal.py START
    python tools/send_signal.py STOP
    python tools/send_signal.py START --url http://192.168.1.42:5000

Le serveur doit tourner et accepter l'origine appelante.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from pathlib import Path


def _load_env_file(path: Path) -> dict[str, str]:
    """Mini parseur .env — pas de dépendance externe."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description="Envoie un signal ESP32 signé HMAC au serveur RayCash.")
    parser.add_argument("action", choices=["START", "STOP"], help="Action à simuler.")
    parser.add_argument(
        "--url",
        default="http://localhost:5000",
        help="URL du serveur (défaut: http://localhost:5000).",
    )
    parser.add_argument(
        "--env",
        default=str(Path(__file__).resolve().parent.parent / "server" / ".env"),
        help="Chemin vers le .env (défaut: server/.env).",
    )
    args = parser.parse_args()

    env = _load_env_file(Path(args.env))
    # On laisse les vars d'environnement réelles surcharger le fichier.
    api_key = os.environ.get("RAYCASH_API_KEY") or env.get("RAYCASH_API_KEY", "")
    hmac_secret = os.environ.get("RAYCASH_HMAC_SECRET") or env.get("RAYCASH_HMAC_SECRET", "")

    if not api_key:
        print("ERREUR : RAYCASH_API_KEY introuvable (.env ou env var).", file=sys.stderr)
        return 1

    payload = json.dumps({"action": args.action}, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }

    if hmac_secret:
        signature = hmac.new(
            hmac_secret.encode("utf-8"),
            f"{timestamp}.".encode("utf-8") + payload,
            hashlib.sha256,
        ).hexdigest()
        headers["X-Signature-Timestamp"] = timestamp
        headers["X-Signature"] = signature

    req = urllib.request.Request(
        f"{args.url.rstrip('/')}/esp_signal",
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"HTTP {resp.status}: {resp.read().decode('utf-8')}")
            return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Erreur réseau : {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
