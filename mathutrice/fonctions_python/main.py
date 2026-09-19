"""
main.py — Point d'entrée principal

Lance un test en choisissant :
  - Le format  : qcm | qro | trous
  - La notion  : trigonométrie | fractions | dérivées | nombres complexes | ...
  - Le niveau  : débutant | intermédiaire | avancé
  - Le nombre  : n questions (ou exercices pour les trous)

Usage :
  python -m mathutrice.fonctions_python.main                        # paramètres par défaut
  python -m mathutrice.fonctions_python.main --format qro           # format QRO
  python -m mathutrice.fonctions_python.main --format trous --n 2   # 2 exercices phrases à trous
  python -m mathutrice.fonctions_python.main --format qcm --notion "fractions" --niveau "débutant" --n 5
"""

import argparse

from mathutrice.fonctions_python.type_questions.qcm_generator import (
    generate_qcm_test,
    run_test as run_qcm,
    ask_question as ask_qcm_question,
)
from mathutrice.fonctions_python.type_questions.qro_generator import (
    generate_qro_test,
    run_test as run_qro,
    ask_question as ask_qro_question,
)
from mathutrice.fonctions_python.type_questions.steps_generator import (
    generate_steps_test,
    run_test as run_sbs,
    ask_exercice as ask_sbs_exercice,
)
from mathutrice.fonctions_python.base_generator import choisir_competence, update_scores
from mathutrice.lacune_evaluation.LLM_as_Evaluator import (
    diagnostiquer_depuis_competence,
    afficher_resultat,
)
from mathutrice.referentiel import REFERENTIEL

# ─── NOTIONS DISPONIBLES ──────────────────────────────────────────────────────

# NOTIONS = [
#     "trigonométrie",
#     "fractions",
#     "dérivées",
#     "nombres complexes",
# ]

# NIVEAUX = ["débutant", "intermédiaire", "avancé"]

FORMATS = {
    "qcm": (generate_qcm_test, run_qcm),
    "qro": (generate_qro_test, run_qro),
    "sbs": (generate_steps_test, run_sbs),
}

# NOTIONS = [
#     notion_data["notion_nom"]
#     for notion_data in REFERENTIEL.values()
# ]

# NIVEAUX = sorted({
#     competence["niveau"]
#     for notion_data in REFERENTIEL.values()
#     for competence in notion_data["competences"]
# })
NOTIONS = list(REFERENTIEL.keys())
NIVEAUX = ["basique", "solide", "expert"]
# ─── MENU INTERACTIF ──────────────────────────────────────────────────────────


def choose_from(label: str, options: list[str]) -> str:
    """Affiche un menu numéroté et retourne le choix de l'utilisateur."""
    print(f"\n{label}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")

    while True:
        raw = input(f"Votre choix (1-{len(options)}) : ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print(f"  ⚠ Entrez un chiffre entre 1 et {len(options)}.")


def interactive_menu() -> tuple[str, str, str, int]:
    """Lance le menu interactif et retourne (notion, niveau, n)."""
    print("\n" + "═" * 50)
    print("  MATHTUTORIA — Générateur de tests")
    print("═" * 50)

    notion = choose_from("Notion :", NOTIONS)
    niveau = choose_from("Niveau :", NIVEAUX)

    while True:
        raw = input("\nNombre de questions (1-10) : ").strip()
        if raw.isdigit() and 1 <= int(raw) <= 10:
            n = int(raw)
            break
        print("  ⚠ Entrez un nombre entre 1 et 10.")

    return notion, niveau, n


# ─── LANCEMENT DU TEST ────────────────────────────────────────────────────────


def run(fmt: str, notion: str, niveau: str, n: int) -> None:
    """Génère et lance le test selon le format choisi."""
    if fmt not in FORMATS:
        print(f"Format inconnu : '{fmt}'. Choisir parmi : {list(FORMATS.keys())}")
        return

    generate_fn, run_fn = FORMATS[fmt]
    questions = generate_fn(notion=notion, niveau=niveau, n=n)
    run_fn(questions)


# def generate_mixed_test(
#     notion: str,
#     niveau: str,
#     n_qcm: int = 0,
#     n_qro: int = 0,
#     n_steps: int = 0,
# ) -> list[dict]:
#     """Génère un test mixte avec QCM, QRO et step by step."""
#     test = []

#     if n_qcm > 0:
#         qcms = generate_qcm_test(notion, niveau, n_qcm)
#         print
#         for q in qcms:
#             q["type"] = "qcm"
#         test.extend(qcms)

#     if n_qro > 0:
#         qros = generate_qro_test(notion, niveau, n_qro)
#         for q in qros:
#             q["type"] = "qro"
#         test.extend(qros)

#     if n_steps > 0:
#         steps = generate_steps_test(notion, niveau, n_steps)
#         for q in steps:
#             q["type"] = "sbs"
#         test.extend(steps)

#     return test


def generate_exercise_randomly(
    REFERENTIEL: dict, niveau: str, notion: str
) -> list[dict]:
    import random

    fmt = random.choice(list(FORMATS.keys()))

    notion_data = REFERENTIEL[notion]
    notion_nom = notion_data["notion_nom"]

    competence = choisir_competence(notion_data, type_exercice=fmt, niveau_eleve=niveau)

    if not competence:
        return []

    if fmt == "qcm":
        questions = generate_qcm_test(notion_nom, [competence])

    elif fmt == "qro":
        questions = generate_qro_test(notion_nom, [competence])

    elif fmt == "sbs":
        questions = generate_steps_test(notion_nom, [competence])

    for q in questions:
        q["type"] = fmt
        q["notion_nom"] = notion_nom
        q["niveau"] = niveau

    return questions


def generate_mixed_test(
    notion: str,
    niveau: str,
    n_qcm: int = 0,
    n_qro: int = 0,
    n_steps: int = 0,
    notion_data_override: dict = None,
) -> list[dict]:

    notion_data = notion_data_override or REFERENTIEL[notion]
    notion_nom = notion_data["notion_nom"]
    test = []
    used_codes = set()  # ← garder trace des compétences déjà utilisées

    def choisir_sans_repetition(type_ex):
        """Choisit une compétence pas encore utilisée si possible."""
        # Filtrer temporairement les compétences déjà vues
        notion_data_filtered = {
            **notion_data,
            "competences": [
                c for c in notion_data["competences"] if c["code"] not in used_codes
            ]
            or notion_data["competences"],  # fallback si toutes utilisées
        }
        comp = choisir_competence(notion_data_filtered, type_ex, niveau)
        if comp and isinstance(comp, dict):
            used_codes.add(comp["code"])
        elif comp and isinstance(comp, list):
            for c in comp:
                used_codes.add(c["code"])
        return comp

    if n_qcm > 0:
        competences_qcm = [choisir_sans_repetition("qcm") for _ in range(n_qcm)]
        competences_qcm = [c for c in competences_qcm if c is not None]
        qcms = generate_qcm_test(notion_nom, competences_qcm)
        for q in qcms:
            q["type"] = "qcm"
            q["notion_nom"] = notion_nom
            q["niveau"] = niveau
        test.extend(qcms)

    if n_qro > 0:
        competences_qro = [choisir_sans_repetition("qro") for _ in range(n_qro)]
        competences_qro = [c for c in competences_qro if c is not None]
        qros = generate_qro_test(notion_nom, competences_qro)
        for q in qros:
            q["type"] = "qro"
            q["notion_nom"] = notion_nom
            q["niveau"] = niveau
        test.extend(qros)

    if n_steps > 0:
        competences_groupes_sbs = []
        for _ in range(n_steps):
            competences = choisir_sans_repetition("sbs")
            if competences:
                competences_groupes_sbs.append(competences)
        steps = generate_steps_test(notion_nom, competences_groupes_sbs)
        for q in steps:
            q["type"] = "sbs"
            q["notion_nom"] = notion_nom
            q["niveau"] = niveau
        test.extend(steps)

    return test


def afficher_competences_dict(competences_dict: dict) -> None:
    SEP = "-" * 60
    print(f"\n{SEP}")
    print("  COMPÉTENCES DICT  (→ update_scores)")
    print(SEP)
    print(f"  {competences_dict}")
    print(SEP)


def afficher_delta_scores(anciens: dict, nouveaux: dict) -> None:
    SEP = "-" * 60
    print(f"\n{SEP}")
    print("  SCORES MIS À JOUR")
    print(SEP)
    for code in nouveaux:
        ancien = anciens.get(code, nouveaux[code])
        nouveau = nouveaux[code]
        delta = round(nouveau - ancien, 3)
        fleche = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
        print(f"  [{code}] : {ancien:.2f} → {nouveau:.2f}  {fleche}")
    print(SEP)


def afficher_bilan(referentiel: dict, scores_initiaux: dict) -> None:
    comp_noms = {}
    for notion in referentiel.values():
        for comp in notion["competences"]:
            if comp["code"] in scores_initiaux:
                comp_noms[comp["code"]] = comp["nom"]
    scores_actuels = {}
    for notion in referentiel.values():
        for comp in notion["competences"]:
            if comp["code"] in scores_initiaux:
                scores_actuels[comp["code"]] = round(comp["score"], 3)
    SEP = "=" * 60
    print(f"\n{SEP}")
    print("  BILAN DE LA SESSION")
    print(SEP)
    for code, score_initial in scores_initiaux.items():
        score_final = scores_actuels.get(code, score_initial)
        delta = round(score_final - score_initial, 3)
        fleche = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
        nom = comp_noms.get(code, code)
        print(
            f"  [{code}] {nom[:42]:<42} : {score_initial:.2f} → {score_final:.2f}  {fleche}"
        )
    print(SEP)


def run_training(REFERENTIEL: dict, niveau: str, notion: str) -> None:
    import time

    print(
        f"\nLancement de la session de formation pour la notion '{notion}' au niveau '{niveau}'..."
    )

    while True:
        questions = generate_exercise_randomly(REFERENTIEL, niveau, notion)

        if questions:
            run_test(questions)

        time.sleep(1)


def run_test(questions: list[dict]) -> None:
    """Lance un test mixte en exécutant chaque question selon son type."""
    NIVEAU_MAP = {"basique": "facile", "solide": "intermediaire", "expert": "difficile"}

    def _update(q_format, competences_dict):
        _, anciens, nouveaux = update_scores(REFERENTIEL, q_format, competences_dict)
        afficher_delta_scores(anciens, nouveaux)
        for code, ancien in anciens.items():
            if code not in scores_initiaux:
                scores_initiaux[code] = ancien

    scores_initiaux = {}
    total = len(questions)

    for i, question in enumerate(questions, start=1):
        q_type = question.get("type")

        print(f"\n--- Question {i}/{total} ---")

        if q_type == "qcm":
            correct, chosen = ask_qcm_question(i, total, question)
            comp = question.get("competence_cible")
            q_format = {
                "type": "QCM",
                "niveau": NIVEAU_MAP.get(question.get("niveau", "basique"), "facile"),
            }
            if comp:
                if correct:
                    competences_dict = {comp["code"]: True}
                    afficher_competences_dict(competences_dict)
                    _update(q_format, competences_dict)
                elif chosen:
                    try:
                        resultat = diagnostiquer_depuis_competence(
                            notion=question.get("notion_nom", ""),
                            niveau=question.get("niveau", ""),
                            enonce=question["question"],
                            reponse_correcte=question["answer"],
                            reponse_etudiant=chosen,
                            competence_cible=comp,
                        )
                        afficher_resultat(resultat)
                        _update(q_format, resultat["competences_dict"])
                    except Exception as e:
                        print(f"  [Diagnostic indisponible : {e}]")
                        _update(q_format, {comp["code"]: False})

        elif q_type == "qro":
            correct, user_answer = ask_qro_question(i, total, question)
            comp = question.get("competence_cible")
            q_format = {
                "type": "QRO",
                "niveau": NIVEAU_MAP.get(question.get("niveau", "basique"), "facile"),
            }
            if comp:
                if correct:
                    competences_dict = {comp["code"]: True}
                    afficher_competences_dict(competences_dict)
                    _update(q_format, competences_dict)
                elif user_answer:
                    try:
                        resultat = diagnostiquer_depuis_competence(
                            notion=question.get("notion_nom", ""),
                            niveau=question.get("niveau", ""),
                            enonce=question["question"],
                            reponse_correcte=question["correct_answer"],
                            reponse_etudiant=user_answer,
                            competence_cible=comp,
                        )
                        afficher_resultat(resultat)
                        _update(q_format, resultat["competences_dict"])
                    except Exception as e:
                        print(f"  [Diagnostic indisponible : {e}]")
                        _update(q_format, {comp["code"]: False})

        elif q_type == "sbs":
            score_ex, total_ex, student_answers = ask_sbs_exercice(i, total, question)
            comps = question.get("competences_cibles", [])
            q_format = {
                "type": "SBS",
                "niveau": NIVEAU_MAP.get(question.get("niveau", "basique"), "facile"),
            }
            if score_ex == total_ex:
                competences_dict = {c["code"]: True for c in comps}
                afficher_competences_dict(competences_dict)
                _update(q_format, competences_dict)
            elif student_answers and comps:
                reponse_correcte = "\n".join(
                    f"Étape {j}: {a}"
                    for j, a in enumerate(question["correct_answers"], 1)
                )
                reponse_etudiant = "\n".join(
                    f"Étape {j}: {a}" for j, a in enumerate(student_answers, 1)
                )
                competences_dict = {}
                try:
                    for comp in comps:
                        res = diagnostiquer_depuis_competence(
                            notion=question.get("notion_nom", ""),
                            niveau=question.get("niveau", ""),
                            enonce=question["enonce"],
                            reponse_correcte=reponse_correcte,
                            reponse_etudiant=reponse_etudiant,
                            competence_cible=comp,
                        )
                        competences_dict.update(res["competences_dict"])
                    afficher_resultat(res)
                    _update(q_format, competences_dict)
                except Exception as e:
                    print(f"  [Diagnostic indisponible : {e}]")
                    _update(q_format, {c["code"]: False for c in comps})

        else:
            print(f"Type de question inconnu : {q_type}")

    if scores_initiaux:
        afficher_bilan(REFERENTIEL, scores_initiaux)


# ─── ARGPARSE ─────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description="Générateur de tests de mathématiques via Mistral AI"
    )
    parser.add_argument(
        "--format",
        choices=list(FORMATS.keys()),
        default=None,
        help="Format du test : qcm | qro | trous",
    )
    parser.add_argument(
        "--notion",
        type=str,
        default=None,
        help=f"Notion mathématique. Exemples : {', '.join(NOTIONS)}",
    )
    parser.add_argument(
        "--niveau",
        choices=NIVEAUX,
        default=None,
        help="Niveau de difficulté : débutant | intermédiaire | avancé",
    )
    parser.add_argument(
        "--n", type=int, default=None, help="Nombre de questions à générer (1-10)"
    )
    return parser.parse_args()


# ─── MAIN ─────────────────────────────────────────────────────────────────────


def main():
    args = parse_args()

    # Si notion, niveau et n sont fournis en ligne de commande → mode direct
    if all([args.notion, args.niveau, args.n]):
        test = generate_mixed_test(args.notion, args.niveau, args.n, args.n, args.n)
        run_test(test)
        run_training(REFERENTIEL, args.niveau, args.notion)

    # Sinon → menu interactif
    else:
        notion, niveau, n = interactive_menu()
        test = generate_mixed_test(notion, niveau, n, n, n)
        run_test(test)
        run_training(REFERENTIEL, niveau, notion)


if __name__ == "__main__":
    main()
