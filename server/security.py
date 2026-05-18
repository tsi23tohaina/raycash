"""Authentification : clé API simple + HMAC signing pour l'ESP32.

Le HMAC empêche un attaquant qui aurait sniffé la clé API (peu probable mais
possible sur réseau non chiffré) de rejouer ou forger un signal /esp_signal :
chaque requête doit inclure un timestamp + une signature dérivée du secret
partagé. Le serveur rejette toute requête trop ancienne ou mal signée.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from functools import wraps

from flask import jsonify, request
from flask_limiter.util import get_remote_address

audit_log = logging.getLogger("raycash.audit")


class HmacVerifier:
    def __init__(self, secret: str, max_skew_seconds: int):
        self._secret = secret.encode("utf-8") if secret else b""
        self.max_skew = max_skew_seconds

    @property
    def enabled(self) -> bool:
        return bool(self._secret)

    def verify(self, *, timestamp: str, signature: str, body: bytes) -> bool:
        if not self.enabled:
            return False
        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            return False
        if abs(time.time() - ts) > self.max_skew:
            return False
        expected = hmac.new(
            self._secret,
            f"{ts}.".encode("utf-8") + body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature or "")


def make_require_api_key(api_key: str):
    """Crée le décorateur d'authentification API key.

    Factory plutôt que global : permet de fournir une clé différente par test
    via une `Settings(api_key=...)` injectée.
    """
    key_bytes = api_key.encode("utf-8") if api_key else b""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            provided = request.headers.get("X-API-Key", "").encode("utf-8")
            if not key_bytes or not hmac.compare_digest(provided, key_bytes):
                audit_log.warning(
                    "auth.api_key.rejected ip=%s path=%s",
                    get_remote_address(),
                    request.path,
                )
                return jsonify({"error": "unauthorized"}), 401
            return view(*args, **kwargs)

        return wrapper

    return decorator


def make_require_hmac(verifier: HmacVerifier):
    """Décorateur HMAC à empiler APRÈS require_api_key.

    Attend les headers `X-Signature-Timestamp` et `X-Signature` côté ESP32.
    Si le secret HMAC n'est pas configuré, le décorateur est un no-op pour
    rester rétro-compatible (l'opérateur peut activer HMAC progressivement).
    """

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if verifier.enabled:
                ts = request.headers.get("X-Signature-Timestamp", "")
                sig = request.headers.get("X-Signature", "")
                body = request.get_data(cache=True) or b""
                if not verifier.verify(timestamp=ts, signature=sig, body=body):
                    audit_log.warning(
                        "auth.hmac.rejected ip=%s ts=%s",
                        get_remote_address(),
                        ts,
                    )
                    return jsonify({"error": "invalid_signature"}), 401
            return view(*args, **kwargs)

        return wrapper

    return decorator
