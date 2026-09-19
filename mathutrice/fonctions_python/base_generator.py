"""
base_generator.py — Logique commune à tous les formats de questions

Contient :
  - Configuration LLM (client, modèle, retries)
  - clean_json()       : nettoyage de la réponse brute
  - parse_json()       : parsing JSON avec message d'erreur clair
  - call_mistral()     : appel API avec retry automatique
  - run_test()         : boucle de test console + score final (générique)
  - display_score()    : affichage du score final

Chaque fichier de format (qcm, qro, trous) importe ce module
et n'ajoute que ce qui lui est spécifique :
  - build_prompt()
  - parse_and_validate()
  - generate_question()
  - ask_question()
"""

import re
import json
import logging

from mathutrice.llm_client import client, MODEL

# from generator_test.lacune_evaluation.LLM_as_Evaluator import competences_dict
# from main import REFERENTIEL


# ─── CONFIG ───────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

MAX_RETRIES = 3

# ─── UTILITAIRES JSON ─────────────────────────────────────────────────────────


def clean_json(raw: str) -> str:
    """
    Nettoie la réponse brute de Mistral.
    Supprime les balises markdown ```json / ``` que Mistral ajoute parfois
    malgré les instructions explicites dans le prompt.
    """
    return re.sub(r"```json|```", "", raw).strip()


def parse_json(raw: str) -> dict:
    """
    Nettoie puis parse la réponse brute en dict Python.
    Lève ValueError avec un message clair si le JSON est invalide.
    """
    cleaned = clean_json(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON invalide : {e} | Reçu : {cleaned[:200]}")

    if not isinstance(data, dict):
        raise ValueError("La réponse n'est pas un objet JSON.")

    return data


# ─── APPEL MISTRAL AVEC RETRY ─────────────────────────────────────────────────


def call_mistral(
    prompt: str, notion: str, parse_and_validate, post_process=None
) -> dict | None:
    """
    Appelle Mistral avec retry automatique (MAX_RETRIES tentatives).

    Paramètres :
      prompt           : le prompt complet à envoyer
      notion           : nom de la notion (pour les logs)
      parse_and_validate : fonction spécifique au format qui valide le dict
      post_process     : fonction optionnelle appliquée après validation
                         (ex: apply_verification pour QCM)

    Retourne le dict validé (et post-traité si besoin), ou None si échec.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": prompt}]
            )
            raw = response.choices[0].message.content
            question = parse_and_validate(raw)

            if post_process:
                question = post_process(question)

            logger.info(f"Question prête (tentative {attempt}/{MAX_RETRIES})")
            return question

        except ValueError as e:
            logger.warning(f"Tentative {attempt}/{MAX_RETRIES} échouée — {e}")
            if attempt == MAX_RETRIES:
                logger.error(
                    f"Abandon après {MAX_RETRIES} tentatives pour notion='{notion}'"
                )
                return None


# ─── GÉNÉRATION D'UN TEST (générique) ─────────────────────────────────────────


def generate_test(
    notion: str, niveau: str, n: int, fonction_generer_format_voulu
) -> list[dict]:
    """
    Génère un test de n questions en appelant fonction_generer_format_voulu à chaque itération.
    Les questions qui échouent après MAX_RETRIES sont ignorées (pas de plantage).

    Paramètres :
      fonction_generer_format_voulu : fonction (notion, niveau) -> dict | None
                                      fournie par chaque fichier de format
    """
    questions = []
    logger.info(f"Génération de {n} questions | notion='{notion}' | niveau='{niveau}'")

    for i in range(n):
        logger.info(f"Question {i + 1}/{n}...")
        question = fonction_generer_format_voulu(notion, niveau)
        if question:
            questions.append(question)
        else:
            logger.warning(
                f"Question {i + 1} ignorée (échec après {MAX_RETRIES} tentatives)."
            )

    logger.info(f"{len(questions)}/{n} questions générées avec succès.")
    return questions


# SCORE FINAL (générique)


def display_score(score: int, total: int, format_label: str) -> None:
    """Affiche le score final dans le terminal."""
    print(f"\n{'═' * 50}")
    print(f"  RÉSULTAT FINAL — {format_label}")
    print(f"  {score}/{total} correctes")

    if total == 0:
        print("  Aucune question n'a pu être générée.")
        print(f"{'═' * 50}\n")
        return

    percentage = round(score / total * 100)
    print(f"  Score : {percentage}%  ", end="")

    if percentage == 100:
        print("🏆 Parfait !")
    elif percentage >= 70:
        print("👍 Bien joué !")
    elif percentage >= 50:
        print("📚 Continue tes révisions.")
    else:
        print("💪 Il faut retravailler cette notion.")

    print(f"{'═' * 50}\n")


# ─── HEADER DE TEST (générique) ───────────────────────────────────────────────


def print_test_header(total: int, format_label: str) -> None:
    """Affiche l'en-tête du test dans le terminal."""
    print(f"\n{'═' * 50}")
    print(f"  TEST {format_label} — {total} question(s)")
    print(f"{'═' * 50}")


def print_question_header(index: int, total: int, extra: str = "") -> None:
    """Affiche l'en-tête d'une question individuelle."""
    print(f"\n{'─' * 50}")
    # label = f"  Question {index}/{total}"
    # if extra:
    #     label += f"  [{extra}]"
    # print(label)
    # print(f"{'─' * 50}")


import random


# def choisir_competence(notion: dict, type_exercice: str, niveau_eleve: str):
#     """
#     Choisit une ou plusieurs compétences à travailler selon :
#     - la notion étudiée
#     - le type d'exercice : qcm, qro ou sbs
#     - le niveau actuel de l'élève : basique, solide ou expert
#     """

#     # Ordre des niveaux pour savoir quel est le niveau supérieur
#     ordre_niveaux = ["basique", "solide", "expert"]

#     # Vérification du type d'exercice
#     if type_exercice not in ["qcm", "qro", "sbs"]:
#         raise ValueError("type_exercice doit être 'qcm', 'qro' ou 'sbs'.")

#     # Vérification du niveau de l'élève
#     if niveau_eleve not in ordre_niveaux:
#         raise ValueError("niveau_eleve doit être 'basique', 'solide' ou 'expert'.")

#     # Récupération de toutes les compétences de la notion
#     competences = notion["competences"]

#     # On garde seulement les compétences du niveau actuel de l'élève
#     competences_niveau = [c for c in competences if c["niveau"] == niveau_eleve]
#     print("les comp du niveau sont:", competences_niveau)

#     # Si aucune compétence ne correspond au niveau demandé
#     if not competences_niveau:
#         return [] if type_exercice == "sbs" else None

#     # Nombre de compétences du niveau actuel considérées comme maîtrisées
#     # Une compétence est maîtrisée si son score est strictement supérieur à 0.8
#     nb_acquises = sum(1 for c in competences_niveau if c["score"] > 0.8)

#     # Proportion de compétences maîtrisées dans le niveau actuel
#     proportion_acquises = nb_acquises / len(competences_niveau)

#     # Position du niveau actuel dans la liste des niveaux
#     niveau_index = ordre_niveaux.index(niveau_eleve)

#     # Si plus de 70 % des compétences du niveau actuel sont maîtrisées,
#     # on autorise aussi les compétences du niveau supérieur
#     if proportion_acquises > 0.7:
#         niveaux_autorises = [niveau_eleve]

#         # Ajout du niveau supérieur s'il existe
#         if niveau_index + 1 < len(ordre_niveaux):
#             niveaux_autorises.append(ordre_niveaux[niveau_index + 1])

#         # On sélectionne les compétences non maîtrisées
#         # dans le niveau actuel + le niveau supérieur
#         competences_candidates = [
#             c
#             for c in competences
#             if c["niveau"] in niveaux_autorises and c["score"] < 1.0
#         ]

#     else:
#         # Si l'élève ne maîtrise pas encore assez son niveau,
#         # on reste vraiment uniquement sur les compétences de son niveau actuel
#         competences_candidates = [
#             c for c in competences if c["niveau"] == niveau_eleve and c["score"] < 1.0
#         ]

#     # Pour un QCM ou une QRO, on renvoie une seule compétence au hasard
#     # if type_exercice in ["qcm", "qro"]:
#     #     print("qcm","qro",competences_candidates)

#     #     return random.choice(competences_candidates) if competences_candidates else None
#     if type_exercice in ["qcm", "qro"]:

#         if competences_candidates:
#             competence_choisie = [random.choice(competences_candidates)]
#             print("compétence choisie :", competence_choisie)
#             return competence_choisie
#         else:
#             return []
#     # Pour SBS, on renvoie toute la liste des compétences candidates
#     if type_exercice == "sbs":
#         print("sbs", competences_candidates)
#         return competences_candidates

import random


def choisir_competence(notion: dict, type_exercice: str, niveau_eleve: str):
    """
    Choisit une ou plusieurs compétences à travailler selon :
    - la notion étudiée
    - le type d'exercice : qcm, qro ou sbs
    - le niveau actuel de l'élève : basique, solide ou expert
    """

    ordre_niveaux = ["basique", "solide", "expert"]

    if type_exercice not in ["qcm", "qro", "sbs"]:
        raise ValueError("type_exercice doit être 'qcm', 'qro' ou 'sbs'.")

    if niveau_eleve not in ordre_niveaux:
        raise ValueError("niveau_eleve doit être 'basique', 'solide' ou 'expert'.")

    competences = notion["competences"]

    competences_niveau = [c for c in competences if c["niveau"] == niveau_eleve]

    if not competences_niveau:
        return [] if type_exercice == "sbs" else None

    nb_acquises = sum(1 for c in competences_niveau if c["score"] >= 0.8)

    proportion_acquises = nb_acquises / len(competences_niveau)

    niveau_index = ordre_niveaux.index(niveau_eleve)

    niveau_superieur = None
    if niveau_index + 1 < len(ordre_niveaux):
        niveau_superieur = ordre_niveaux[niveau_index + 1]

    niveau_inferieur = None
    if niveau_index - 1 >= 0:
        niveau_inferieur = ordre_niveaux[niveau_index - 1]

    niveaux_autorises = [niveau_eleve]

    if proportion_acquises >= 0.7:
        if niveau_superieur is not None:
            niveaux_autorises.append(niveau_superieur)

    else:
        if type_exercice == "sbs" and niveau_inferieur is not None:
            niveaux_autorises.append(niveau_inferieur)

    competences_candidates = [
        c for c in competences if c["niveau"] in niveaux_autorises and c["score"] < 1.0
    ]

    if type_exercice in ["qcm", "qro"]:
        return random.choice(competences_candidates) if competences_candidates else None

    if type_exercice == "sbs":
        return competences_candidates


# DICTIONNAIRE FICTIF TEMPORAIRE avec compétences evaluées dans une questions donnée et lesquelles sont bonnes ou fausses

question_format = {"type": "QCM", "niveau": "facile"}


# FONCTION POUR METTRE A JOUR LE REFERENTIEL APRES UN TEST
def update_scores(REFERENTIEL, question_format, competences_dict):
    """
    Met à jour les scores des compétences évaluées dans une question.

    Paramètres :
    - REFERENTIEL : dictionnaire contenant toutes les notions et compétences
    - question_format : dictionnaire décrivant le format de la question
        Exemple :
        {
            "type": "QCM",
            "niveau": "facile"
        }

    - competences_dict : dictionnaire contenant les compétences évaluées
      ainsi que le résultat de l'étudiant.

      Exemple :
      {
          "ad01": True,
          "ad03": False
      }

    Retour :
    - REFERENTIEL mis à jour
    """

    # RÈGLES DE BONUS / MALUS (à valider en groupe)

    scoring_rules = {
        "QCM": {
            "facile": (0.2, -0.4),
            "intermediaire": (0.3, -0.3),
            "difficile": (0.4, -0.2),
        },
        "QRO": {
            "facile": (0.3, -0.4),
            "intermediaire": (0.4, -0.3),
            "difficile": (0.5, -0.2),
        },
        "SBS": {
            "facile": (0.4, -0.4),
            "intermediaire": (0.5, -0.3),
            "difficile": (0.6, -0.2),
        },
    }

    type_question = question_format["type"]
    niveau_question = question_format["niveau"]
    bonus, malus = scoring_rules[type_question][niveau_question]

    anciens_scores = {}
    for notion in REFERENTIEL.values():
        for competence in notion["competences"]:
            if competence["code"] in competences_dict:
                anciens_scores[competence["code"]] = round(competence["score"], 3)

    for notion in REFERENTIEL.values():
        for competence in notion["competences"]:
            code_competence = competence["code"]
            if code_competence in competences_dict:
                if competences_dict[code_competence] is True:
                    competence["score"] += bonus
                else:
                    competence["score"] += malus

    nouveaux_scores = {}
    for notion in REFERENTIEL.values():
        for competence in notion["competences"]:
            if competence["code"] in competences_dict:
                nouveaux_scores[competence["code"]] = round(competence["score"], 3)

    return REFERENTIEL, anciens_scores, nouveaux_scores


# ################# TEST de la fonction update_scores #################### Test OK
# if __name__ == "__main__":
#     print("\n===== AVANT =====")

#     for notion in REFERENTIEL.values():
#         for competence in notion["competences"]:
#             if competence["code"] in competences_dict:
#                 print(competence["code"], "| score =", competence["score"])

#     # appel de la fonction
#     update_scores(REFERENTIEL, question_format, competences_dict)

#     print("\n===== APRES =====")

#     for notion in REFERENTIEL.values():
#         for competence in notion["competences"]:
#             if competence["code"] in competences_dict:
#                 print(competence["code"], "| score =", competence["score"])
