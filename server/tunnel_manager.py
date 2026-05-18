"""Lance `cloudflared tunnel --url ...` en sous-processus et expose l'URL HTTPS publique générée.

Pourquoi :
- Cloudflare Quick Tunnel donne une URL aléatoire (`https://xxx.trycloudflare.com`)
  qui change à chaque redémarrage de cloudflared.
- Sans automatisation, l'utilisateur doit copier-coller cette URL dans `.env`
  après chaque démarrage → frustrant et source d'erreur.
- Ce manager spawn cloudflared, parse son stdout/stderr pour extraire l'URL,
  et la rend accessible via `get_url()` aux routes Flask.

Le manager tourne dans un thread démon — il survit tant que le process Flask
vit, et se termine proprement quand Flask s'arrête.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Optional

log = logging.getLogger("raycash.tunnel")

# Regex qui matche l'URL générée par cloudflared dans son log :
#   "INF | https://beginning-thomson-donna-fuel.trycloudflare.com  |"
# On accepte les schemes http et https, et tout chemin compatible RFC-3986 simple.
_URL_RE = re.compile(r"https?://[a-zA-Z0-9-]+\.trycloudflare\.com")


class TunnelManager:
    """Wrapper autour d'un process `cloudflared tunnel --url <local>`.

    Thread-safe : `get_url()` peut être appelé depuis n'importe quelle requête
    Flask, le lock interne garantit qu'on lit une valeur cohérente.
    """

    def __init__(
        self,
        local_url: str = "http://localhost:5000",
        cloudflared_path: Optional[str] = None,
        startup_timeout: float = 30.0,
    ):
        self.local_url = local_url
        self.cloudflared_path = cloudflared_path or self._auto_detect_binary()
        self.startup_timeout = startup_timeout

        self._proc: Optional[subprocess.Popen] = None
        self._url: Optional[str] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None

    @staticmethod
    def _auto_detect_binary() -> Optional[str]:
        """Cherche cloudflared dans des chemins courants Windows + via $PATH."""
        # 1. $PATH (cas où l'utilisateur a installé via choco/scoop/winget)
        on_path = shutil.which("cloudflared")
        if on_path:
            return on_path
        # 2. Emplacement où on l'a téléchargé pendant le setup
        for candidate in (r"C:\Tools\cloudflared.exe", r"C:\Tools\cloudflared"):
            if os.path.isfile(candidate):
                return candidate
        return None

    @property
    def available(self) -> bool:
        return self.cloudflared_path is not None and os.path.isfile(self.cloudflared_path)

    def get_url(self) -> Optional[str]:
        with self._lock:
            return self._url

    def start(self) -> bool:
        """Lance cloudflared en sous-processus. Bloque jusqu'à ce que l'URL soit
        détectée OU `startup_timeout` expire. Retourne True si l'URL a été
        capturée avec succès."""
        if not self.available:
            log.warning("cloudflared introuvable (chemin testé : %s) — tunnel auto désactivé", self.cloudflared_path)
            return False

        log.info("Démarrage cloudflared (%s) pour %s", self.cloudflared_path, self.local_url)
        try:
            # Important : on combine stdout + stderr en un seul stream pour ne
            # rien rater (cloudflared écrit sur les deux selon la version).
            self._proc = subprocess.Popen(
                [self.cloudflared_path, "tunnel", "--url", self.local_url],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as e:
            log.error("Impossible de lancer cloudflared : %s", e)
            return False

        url_event = threading.Event()

        def _reader():
            assert self._proc is not None and self._proc.stdout is not None
            try:
                for line in self._proc.stdout:
                    if self._stop_event.is_set():
                        break
                    line = (line or "").strip()
                    if not line:
                        continue
                    # On garde une trace verbose en debug seulement pour ne pas spammer
                    log.debug("cloudflared: %s", line)
                    if self._url is None:
                        match = _URL_RE.search(line)
                        if match:
                            with self._lock:
                                self._url = match.group(0)
                            log.info("Tunnel HTTPS disponible : %s", self._url)
                            url_event.set()
            except Exception:
                log.exception("Erreur lecture stdout cloudflared")

        self._reader_thread = threading.Thread(target=_reader, name="cloudflared-reader", daemon=True)
        self._reader_thread.start()

        if not url_event.wait(timeout=self.startup_timeout):
            log.warning(
                "Tunnel cloudflared : URL non détectée après %.1fs — vérifie la connexion.",
                self.startup_timeout,
            )
            # On NE tue PAS le process : il peut encore réussir et logger
            # l'URL plus tard (le reader continue à écouter).
            return False

        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._proc and self._proc.poll() is None:
            log.info("Arrêt cloudflared")
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception:
                log.exception("Erreur lors de l'arrêt de cloudflared")
        self._proc = None


# Singleton global — utilisé par Flask via `tunnel_manager.get_url()`
_default_manager: Optional[TunnelManager] = None


def init_default(local_url: str = "http://localhost:5000",
                 cloudflared_path: Optional[str] = None,
                 startup_timeout: float = 30.0) -> TunnelManager:
    """À appeler une fois au démarrage du serveur. Idempotent."""
    global _default_manager
    if _default_manager is None:
        _default_manager = TunnelManager(
            local_url=local_url,
            cloudflared_path=cloudflared_path,
            startup_timeout=startup_timeout,
        )
    return _default_manager


def get_default() -> Optional[TunnelManager]:
    return _default_manager
