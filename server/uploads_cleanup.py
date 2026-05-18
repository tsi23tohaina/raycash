"""Nettoyage périodique du dossier uploads/.

L'endpoint /predict écrit une image à chaque classification ; sans rotation,
le disque finit par saturer. Ce module garde uniquement les N fichiers les
plus récents et supprime ceux plus vieux que `max_age_seconds`.

Le job tourne dans un thread démon, séparé du request handler — il n'a aucun
impact sur la latence de /predict.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Iterable

log = logging.getLogger("raycash.cleanup")


def _iter_files(folder: str) -> Iterable[os.DirEntry]:
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.is_file():
                    yield entry
    except FileNotFoundError:
        return


def cleanup_once(folder: str, *, max_files: int, max_age_seconds: int) -> int:
    """Une passe de nettoyage. Retourne le nombre de fichiers supprimés."""
    now = time.time()
    entries = sorted(
        _iter_files(folder),
        key=lambda e: e.stat().st_mtime,
        reverse=True,  # plus récent d'abord
    )

    deleted = 0
    for idx, entry in enumerate(entries):
        too_old = (now - entry.stat().st_mtime) > max_age_seconds
        over_quota = idx >= max_files
        if too_old or over_quota:
            try:
                os.remove(entry.path)
                deleted += 1
            except OSError as e:
                log.warning("Impossible de supprimer %s : %s", entry.path, e)

    if deleted:
        log.info("Cleanup uploads/ : %d fichier(s) supprimé(s)", deleted)
    return deleted


def start_background_cleanup(
    folder: str,
    *,
    max_files: int = 500,
    max_age_seconds: int = 7 * 24 * 3600,
    interval_seconds: int = 3600,
) -> threading.Thread:
    """Lance un thread démon qui tourne en boucle.

    Defaults : on garde au plus 500 fichiers et rien de plus vieux que 7 jours,
    avec une passe toutes les heures. Tous les paramètres sont configurables
    via Settings pour adapter à un automate de production.
    """

    def _loop() -> None:
        # Première passe immédiate au démarrage : utile si le serveur est
        # redémarré après un long uptime sans cleanup.
        try:
            cleanup_once(folder, max_files=max_files, max_age_seconds=max_age_seconds)
        except Exception:
            log.exception("Erreur cleanup uploads (passe initiale)")

        while True:
            time.sleep(interval_seconds)
            try:
                cleanup_once(folder, max_files=max_files, max_age_seconds=max_age_seconds)
            except Exception:
                log.exception("Erreur cleanup uploads")

    t = threading.Thread(target=_loop, name="uploads-cleanup", daemon=True)
    t.start()
    return t
