"""Remplace le model.tflite et labels.txt côté serveur par les artefacts entraînés.

Usage:
    python ml/scripts/swap_model.py
    python ml/scripts/swap_model.py --source ~/Downloads --dry-run
    python ml/scripts/swap_model.py --source ml/models --no-backup

Les fichiers attendus dans le dossier source :
    - model.tflite
    - labels.txt

Par défaut on backupe l'ancien modèle dans `server/models/backup-<timestamp>/`.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SERVER_MODELS = ROOT / "server" / "models"


def main() -> int:
    parser = argparse.ArgumentParser(description="Swap RayCash TFLite model.")
    parser.add_argument(
        "--source",
        default=str(ROOT / "ml" / "models"),
        help="Dossier contenant model.tflite et labels.txt (défaut: ml/models).",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Ne pas sauvegarder l'ancien modèle avant le remplacement.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche ce qui serait fait, ne touche pas aux fichiers.",
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    src_model = source / "model.tflite"
    src_labels = source / "labels.txt"

    if not src_model.exists() or not src_labels.exists():
        print(f"ERREUR : {src_model} et {src_labels} doivent exister.", file=sys.stderr)
        print(f"Contenu de {source} :", file=sys.stderr)
        for p in source.glob("*"):
            print(f"  - {p.name}", file=sys.stderr)
        return 1

    dst_model = SERVER_MODELS / "model.tflite"
    dst_labels = SERVER_MODELS / "labels.txt"

    print(f"Source  : {source}")
    print(f"Dest    : {SERVER_MODELS}")
    print(f"  - model.tflite  ({src_model.stat().st_size / 1024:.0f} KB)")
    print(f"  - labels.txt    ({len(src_labels.read_text().splitlines())} lignes)")

    if not args.no_backup and dst_model.exists():
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup = SERVER_MODELS / f"backup-{ts}"
        print(f"Backup  : {backup}")
        if not args.dry_run:
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy(dst_model, backup / "model.tflite")
            if dst_labels.exists():
                shutil.copy(dst_labels, backup / "labels.txt")

    if args.dry_run:
        print("\n[dry-run] Aucune modification effectuée.")
        return 0

    SERVER_MODELS.mkdir(parents=True, exist_ok=True)
    shutil.copy(src_model, dst_model)
    shutil.copy(src_labels, dst_labels)
    print("\n[OK] Swap termine. Redemarre le serveur Flask pour recharger le modele.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
