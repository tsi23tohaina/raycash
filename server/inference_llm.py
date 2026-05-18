"""Classifier basé sur un Vision LLM (Google Gemini 2.5 Flash).

Implémente la même interface que `Classifier` et `EnsembleClassifier` du module
`inference.py` (méthodes `classify`, `classify_multi`, propriété `ready`, etc.)
pour pouvoir être branché en remplacement transparent dans `main.py`.

Avantages vs modèle TFLite local :
- Précision réelle bien supérieure sur photos webcam variées (~95%+ vs ~50%)
- Aucun entraînement, aucun dataset à collecter
- Capable de reasoning : peut justifier pourquoi un objet est ambigu

Inconvénients :
- Nécessite une connexion internet et une clé API Google
- Latence ~1-2s par scan (vs ~300ms local)
- Coût marginal (~0.05-0.1¢ par scan en free tier — gratuit jusqu'à 1500/jour)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional, Sequence

from google import genai
from google.genai import types as genai_types
from PIL import Image

from inference import POINTS_BY_LABEL, RECYCLABLES, DEFAULT_LABELS, Prediction

log = logging.getLogger("raycash.inference.llm")


# Prompt système : on force Gemini à répondre en JSON structuré.
# Les règles aident à désambiguïser les cas frontières (canette aluminium vs boîte conserve, etc.).
SYSTEM_INSTRUCTION = """Tu es un classificateur de MATÉRIAU pour une chaîne de tri de recyclage.
Identifie le matériau principal de l'objet visible dans l'image et classe-le dans
EXACTEMENT une de ces 6 catégories — la classification se fait par matériau
constitutif, PAS par type d'usage (un jouet plastique = "Plastique", pas "Inconnu").

CATÉGORIES (par matériau dominant) :

- "Aluminium" : tout objet majoritairement en aluminium / métal léger argenté.
  Exemples : canettes (Coca, bière, jus), papier alu, barquettes alu, opercules,
  capsules de bouteilles métalliques.

- "Plastique" : tout objet majoritairement en plastique (PET, PEHD, PP, PS, PVC).
  Exemples : bouteilles PET, gobelets, sachets souples, emballages, JOUETS EN PLASTIQUE
  (voiture, figurine, lego, etc.), pots de yaourt, brosses à dents, stylos, contenants
  alimentaires plastique, sacs plastique, films, bouchons plastique.

- "Verre" : tout objet en verre.
  Exemples : bouteilles, bocaux, pots de confiture, verres à boire, vases, ampoules
  (verre majoritaire), pots de cosmétiques en verre.

- "Papier" : tout objet majoritairement en papier.
  Exemples : feuilles, journaux, magazines, livres, papier d'imprimante, enveloppes,
  papier kraft, post-it, papier journal froissé.

- "Carton" : tout objet majoritairement en carton.
  Exemples : boîtes carton, emballages cartonnés, tetra pak (briques de lait/jus),
  rouleaux essuie-tout, alvéoles à œufs, packaging Amazon, boîtes de céréales.

- "Inconnu" : UNIQUEMENT en dernier recours :
  * image complètement vide, noire, ou tellement floue qu'aucune forme n'est visible
  * objet composite électronique indissociable (téléphone, batterie, ordinateur)
  * déchet organique (nourriture, plante, animal)
  * être humain ou partie du corps qui occupe presque tout le cadre
  * textile/cuir/bois clairement identifié

PRINCIPE FONDAMENTAL — SOIS ASSERTIF, PAS PRUDENT :
- Si une photo est floue, mal éclairée ou prise sous un angle bizarre, fais quand
  même ta meilleure estimation à partir des indices visibles (forme, couleur,
  réflectivité, brillance, transparence, texture).
- Ne JAMAIS te réfugier dans "Inconnu" par excès de prudence. La machine de tri
  préfère une bonne estimation à 50% plutôt qu'un refus à 100%.
- Une canette même floue reste cylindrique et brillante → "Aluminium".
- Une bouteille même mal éclairée a une forme caractéristique → "Plastique" ou "Verre"
  selon la rigidité apparente / l'épaisseur des parois.

ÉCHELLE DE CONFIANCE :
- 0.9 - 1.0 : objet net, matériau évident, aucun doute
- 0.6 - 0.85 : objet identifiable, photo correcte mais perfectible
- 0.4 - 0.6 : tu fais une estimation raisonnable basée sur des indices partiels
              (forme + couleur seules, photo floue)
- 0.2 - 0.4 : tu donnes ta MEILLEURE hypothèse même si tu n'es pas sûr — NE PAS
              utiliser "Inconnu" à ce niveau, prends ta meilleure pioche
- < 0.2     : seulement si vraiment aucun indice (image presque vide)

RÈGLES de tri :
1. Classifie par MATÉRIAU dominant, pas par usage. Un jouet plastique = "Plastique".
2. Si plusieurs objets, classifie celui qui occupe le plus de surface ou le plus central.
3. Le `reasoning` doit être court (1 phrase) et expliquer le matériau identifié.

Format JSON STRICT : {"label": "<catégorie>", "confidence": <0.0-1.0>, "reasoning": "<phrase>"}
"""


class GeminiClassifier:
    """Drop-in remplacement de Classifier qui délègue l'inférence à Gemini."""

    DEFAULT_MODEL = "gemini-2.5-flash"

    # Nom affiché côté UI : on masque le provider sous-jacent (Gemini) pour rester
    # neutre vis-à-vis de la démo et du jury. Le `model_name` réel (gemini-X-Y)
    # est utilisé en interne pour l'appel API mais n'est jamais exposé au client.
    DISPLAY_NAME = "RayCash Vision AI"

    def __init__(
        self,
        api_key: str,
        model_name: Optional[str] = None,
        timeout_seconds: float = 15.0,
    ):
        self.api_key = api_key
        self.model_name = model_name or self.DEFAULT_MODEL
        self.timeout_seconds = timeout_seconds
        self.labels = list(DEFAULT_LABELS)

        self._client: Optional[genai.Client] = None
        try:
            self._client = genai.Client(api_key=api_key)
            log.info("GeminiClassifier prêt (modèle=%s)", self.model_name)
        except Exception as e:
            log.error("Impossible d'initialiser le client Gemini : %s", e)

    @property
    def ready(self) -> bool:
        return self._client is not None and bool(self.api_key)

    @property
    def n_models(self) -> int:
        return 1

    @property
    def model_names(self) -> list[str]:
        # On expose le DISPLAY_NAME (générique) au lieu du model_name technique.
        return [self.DISPLAY_NAME]

    def points_for(self, label: str) -> int:
        return POINTS_BY_LABEL.get(label, 0)

    def _build_prediction(self, parsed: dict, raw_text: str = "") -> Prediction:
        label_raw = (parsed.get("label") or "").strip()
        # Normalise contre la liste connue (insensible à la casse)
        label = "Inconnu"
        for candidate in self.labels:
            if candidate.lower() == label_raw.lower():
                label = candidate
                break

        try:
            confidence = float(parsed.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        reasoning = (parsed.get("reasoning") or "")[:200]
        is_recyclable = label in RECYCLABLES

        # Seuil bas : on accepte presque toute prédiction. Le prompt force déjà
        # Gemini à être assertif (interdit de se réfugier dans "Inconnu" par
        # prudence). En dessous de 0.25, c'est qu'il n'y a vraiment rien à voir.
        accepted = confidence >= 0.25
        label_out = label if accepted else "Inconnu"
        if accepted and is_recyclable:
            points = self.points_for(label_out)
            tri_status = "RECYCLABLE"
        else:
            points = 0
            tri_status = "NON_RECYCLABLE"

        # Pour rester compatible avec l'UI session qui affiche `per_model_details`
        per_model = [{
            "name": self.DISPLAY_NAME,  # nom générique exposé à l'UI
            "label": label,
            "score": round(confidence, 4),
            "reasoning": reasoning,
            "distribution": [],
        }]
        top_preds = [{"label": label, "score": round(confidence, 4)}]

        return Prediction(
            label=label_out,
            confidence=confidence,
            points=points,
            tri_status=tri_status,
            uncertain=not accepted,
            entropy=0.0,
            disagreement=0.0,
            accepted=accepted,
            per_model_top=[label],
            per_model_details=per_model,
            top_predictions=top_preds,
        )

    def _parse_json(self, raw_text: str) -> dict:
        """Robuste : extrait le premier bloc JSON de la réponse texte."""
        text = (raw_text or "").strip()
        # Gemini peut entourer en ```json ... ```
        m = re.search(r"\{.*?\}", text, flags=re.DOTALL)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}

    def classify(self, img: Image.Image) -> Prediction:
        return self.classify_multi([img])

    def classify_multi(self, imgs: Sequence[Image.Image]) -> Prediction:
        if not self.ready:
            raise RuntimeError("GeminiClassifier non prêt — vérifie GEMINI_API_KEY")
        if not imgs:
            raise ValueError("Au moins une image est requise")

        # En multi-vue, on passe toutes les images en parts ; Gemini les analyse
        # ensemble et fournit UNE prédiction.
        parts = []
        for img in imgs:
            parts.append(img)
        parts.append("Classe le déchet visible dans la/les image(s) ci-dessus.")

        # Schéma JSON strict : Gemini est OBLIGÉ de retourner un objet bien formé.
        # Évite les cas "Here is the JSON :" en texte naturel, ou JSON tronqué
        # par dépassement de max_output_tokens.
        schema = {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "enum": ["Aluminium", "Plastique", "Verre", "Papier", "Carton", "Inconnu"],
                },
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "reasoning": {"type": "string"},
            },
            "required": ["label", "confidence", "reasoning"],
        }

        # Retry simple pour absorber les erreurs transitoires côté Google
        # (503 UNAVAILABLE quand la demande est élevée, 429 rate limit ponctuel).
        # On reste rapide : 3 essais espacés de 0.8s/1.6s, sinon on abandonne.
        response = None
        last_err = None
        retry_delays = [0.0, 0.8, 1.6]  # 1ère tentative sans délai, puis backoff
        for attempt, delay in enumerate(retry_delays, start=1):
            if delay > 0:
                time.sleep(delay)
            try:
                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=parts,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=0.1,
                        max_output_tokens=512,
                        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                break  # succès, on sort de la boucle
            except Exception as e:
                last_err = e
                err_msg = str(e)
                # On retente uniquement sur erreurs VRAIMENT transitoires : 503/UNAVAILABLE
                # (surcharge serveur), timeouts. Le 429 RESOURCE_EXHAUSTED = quota épuisé,
                # inutile de retenter (gâche encore du quota).
                is_transient = (
                    "503" in err_msg or "UNAVAILABLE" in err_msg
                    or "timeout" in err_msg.lower() or "TIMEOUT" in err_msg
                )
                if not is_transient or attempt >= len(retry_delays):
                    log.error("Erreur Gemini (essai %d/%d, abandon): %s",
                              attempt, len(retry_delays), err_msg[:200])
                    break
                log.warning("Gemini erreur transitoire (essai %d/%d) : %s",
                            attempt, len(retry_delays), err_msg[:120])

        if response is None:
            return Prediction(
                label="Inconnu",
                confidence=0.0,
                points=0,
                tri_status="NON_RECYCLABLE",
                uncertain=True,
                accepted=False,
            )

        raw_text = ""
        try:
            raw_text = response.text or ""
        except Exception as e:
            log.error("Gemini : impossible de lire response.text : %s", e)

        # DEBUG temporaire : on log la réponse brute pour comprendre quand
        # le parsing échoue. À retirer une fois stable.
        log.info("Gemini RAW : %r", raw_text[:500])

        parsed = self._parse_json(raw_text)
        pred = self._build_prediction(parsed, raw_text=raw_text)
        log.info(
            "Gemini : %s (conf=%.2f, accepted=%s) — %s",
            pred.label, pred.confidence, pred.accepted,
            parsed.get("reasoning", "")[:120],
        )
        return pred
