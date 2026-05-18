# 🧠 Entraînement de l'IA RayCash

Ce dossier contient tout le nécessaire pour **réentraîner le classifieur de déchets** et brancher le nouveau modèle dans le serveur Flask.

```
ml/
├── notebooks/
│   └── train_raycash.ipynb   # Notebook Colab tout-en-un (DL → train → export TFLite)
├── scripts/
│   ├── swap_model.py          # Remplace server/models/model.tflite par le nouveau
│   └── test_model.py          # Smoke test rapide : classifie une image locale
├── models/                    # Dépôt local des modèles entraînés (gitignored idéalement)
└── README.md                  # (ce fichier)
```

---

## 🚀 Workflow complet (30-40 min)

### 1. Ouvrir le notebook sur Google Colab

1. Va sur https://colab.research.google.com
2. **Fichier → Importer un notebook → Upload** → choisis `ml/notebooks/train_raycash.ipynb`
3. **Exécution → Modifier le type d'exécution → Type de matériel = T4 GPU** (gratuit)

> Pourquoi Colab ? GPU gratuit, pas d'install locale, isolation propre.

### 2. Lancer le notebook

Clique sur **Exécution → Tout exécuter** (ou Ctrl+F9).

Le notebook va :
1. Télécharger TrashNet (~50 MB, ~2500 images, 6 classes)
2. Remapper les classes vers les 5 cibles RayCash + Inconnu
3. Splitter en 80% train / 10% val / 10% test
4. Fine-tuner MobileNetV2 (transfer learning depuis ImageNet) en 2 phases
5. Évaluer sur le test set + afficher une matrice de confusion
6. Exporter un `model.tflite` quantizé int8 (~900 KB)
7. Proposer le téléchargement de `model.tflite` + `labels.txt`

**Durée** : ~15-20 min sur T4 GPU. Plus si la phase 2 (fine-tuning) ne déclenche pas l'early stopping rapidement.

### 3. Récupérer les artefacts

Le notebook te propose deux téléchargements à la fin. Mets les fichiers dans `ml/models/` du projet :

```
ml/models/
├── model.tflite
└── labels.txt
```

### 4. Swap dans le serveur

Depuis le repo :
```bash
python ml/scripts/swap_model.py
```

Ça :
- Backupe l'ancien modèle dans `server/models/backup-<timestamp>/`
- Copie `ml/models/model.tflite` → `server/models/model.tflite`
- Copie `ml/models/labels.txt` → `server/models/labels.txt`

### 5. Tester rapidement (sans relancer le serveur)

```bash
python ml/scripts/test_model.py path/to/canette.jpg
```

Affiche le top-5 des prédictions avec leurs scores. Si la canette n'est pas reconnue comme "Aluminium" → quelque chose cloche.

### 6. Redémarrer le serveur Flask

```powershell
cd server
$env:PYTHONIOENCODING="utf-8"
python main.py
```

Le serveur charge le nouveau modèle au boot. Tu peux maintenant retester depuis l'app Flutter ou avec `curl`.

---

## 🩹 Si les résultats ne sont pas bons

### Symptômes courants et fixes

| Symptôme | Cause probable | Fix |
|---|---|---|
| Tout classifié "Plastique" / une seule classe | Modèle pas convergé OU dataset déséquilibré | Vérifie la matrice de confusion + le `classification_report`. Augmente le nombre d'epochs ou le LR de la phase 2. |
| Bonnes perfs sur test, mauvaises sur webcam | Distribution de la webcam ≠ TrashNet (fond, lumière, angle) | Ajoute 30-50 photos de ta webcam dans `ml/datasets/<classe>/` et re-train (cf. section "Dataset perso" ci-dessous). |
| Modèle énorme (>5 MB) | Quantization int8 n'a pas marché | Vérifie que la cellule "Export TFLite" utilise bien `target_spec.supported_ops = [OpsSet.TFLITE_BUILTINS_INT8]` et le `representative_dataset`. |
| Confiance toujours faible (<50%) | Trop peu de données ou trop d'augmentation | Réduis l'augmentation ou ajoute des données. |

### Ajouter des photos perso (fine-tuning supplémentaire)

Si le modèle TrashNet ne marche pas bien sur ta webcam (lumière, fond, distance différents), tu peux augmenter le dataset avec tes propres photos :

1. Prends 30-50 photos par classe (alu, plastique, verre, papier, carton) **avec la même webcam, dans les mêmes conditions** que la démo réelle.
2. Crée un dossier `ml/datasets/custom/<classe>/` et range les photos.
3. Modifie le notebook pour fusionner `custom/` avec TrashNet dans le split.
4. Re-train.

Souvent, **30 photos perso par classe valent mieux que 500 photos TrashNet** quand le contexte de la démo est très différent.

---

## 📐 Architecture du modèle

- **Backbone** : MobileNetV2 (pré-entraîné sur ImageNet, ~3.5M params, ~14 MB)
  - Choisi pour être léger (mobile-ready) et précis sur des objets quotidiens
- **Tête** : `GlobalAveragePooling → Dropout(0.2) → Dense(N classes, softmax)`
- **Input** : 224×224×3 uint8 (après quantization)
- **Output** : N×uint8 (softmax quantizé, divisé par 255 côté serveur dans `inference.py`)
- **Taille TFLite final** : ~900 KB (vs 14 MB float)

---

## 🔁 Variantes possibles

| Variante | Backbone | Taille TFLite | Précision attendue |
|---|---|---|---|
| Léger (défaut) | MobileNetV2 | ~900 KB | ~85-92% |
| Très léger | MobileNetV3-Small | ~600 KB | ~80-88% |
| Plus précis | EfficientNet-Lite0 | ~1.5 MB | ~90-94% |
| Très précis | EfficientNet-Lite4 | ~5 MB | ~93-96% |

Pour changer, remplace la ligne `base = tf.keras.applications.MobileNetV2(...)` dans le notebook par le modèle voulu (et adapte `preprocess_input`).

---

## 📚 Datasets alternatifs

| Dataset | Taille | Classes | Note |
|---|---|---|---|
| **TrashNet** (utilisé ici) | 2.5k images | 6 | Petit, propre, mapping facile |
| **WaRP** | 28k images | 28 | Beaucoup plus gros, plus de variation |
| **TACO** | 1.5k images | 60 (regroupables) | Photos de déchets en contexte réel (sols, rues) |
| **Custom** | Variable | N | Le mieux pour la démo réelle si même contexte |
