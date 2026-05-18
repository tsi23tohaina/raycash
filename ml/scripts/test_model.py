"""Smoke test rapide d'un model.tflite sur une image locale.

Utile pour valider qu'un modèle (nouveau ou ancien) classifie bien une image
sans avoir à lancer tout le serveur Flask.

Usage:
    python ml/scripts/test_model.py path/to/image.jpg
    python ml/scripts/test_model.py img.jpg --model server/models/model.tflite
    python ml/scripts/test_model.py img.jpg --top 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import tensorflow as tf
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent.parent


def load_labels(label_path: Path) -> list[str]:
    labels = []
    for line in label_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            labels.append(parts[1].strip())
        else:
            labels.append(line)
    return labels


def main() -> int:
    parser = argparse.ArgumentParser(description="Test rapide d'un modèle TFLite RayCash.")
    parser.add_argument("image", help="Chemin vers une image (jpg/png/webp).")
    parser.add_argument(
        "--model",
        default=str(ROOT / "server" / "models" / "model.tflite"),
        help="Chemin du modèle TFLite (défaut: server/models/model.tflite).",
    )
    parser.add_argument(
        "--labels",
        default=str(ROOT / "server" / "models" / "labels.txt"),
        help="Chemin labels.txt (défaut: server/models/labels.txt).",
    )
    parser.add_argument("--top", type=int, default=5, help="Top-K prédictions à afficher.")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"ERREUR : image introuvable : {image_path}", file=sys.stderr)
        return 1

    labels = load_labels(Path(args.labels))
    print(f"Modèle : {args.model}")
    print(f"Labels : {labels}")

    interpreter = tf.lite.Interpreter(model_path=args.model)
    interpreter.allocate_tensors()
    in_det = interpreter.get_input_details()[0]
    out_det = interpreter.get_output_details()[0]

    h, w = in_det["shape"][1], in_det["shape"][2]
    print(f"Input  : {in_det['shape']} {in_det['dtype']}")
    print(f"Output : {out_det['shape']} {out_det['dtype']}")

    img = Image.open(image_path).convert("RGB").resize((w, h))
    arr = np.array(img, dtype=in_det["dtype"])
    arr = np.expand_dims(arr, axis=0)

    interpreter.set_tensor(in_det["index"], arr)
    interpreter.invoke()
    out = interpreter.get_tensor(out_det["index"])[0]

    if out.dtype == np.uint8:
        out = out.astype(np.float32) / 255.0

    # Top-K
    top_n = min(args.top, len(out))
    order = np.argsort(out)[::-1][:top_n]
    print(f"\n=== Top-{top_n} prédictions ===")
    for rank, idx in enumerate(order, 1):
        label = labels[idx] if idx < len(labels) else f"<class {idx}>"
        print(f"  {rank}. {label:12} {out[idx] * 100:6.2f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
