import json
import re

from mathutrice.referentiel import REFERENTIEL
from mathutrice.llm_client import client, MODEL


def _build_flat_competences(referentiel):
    """Construit une liste plate de toutes les compétences de toutes les notions."""
    flat = []
    for notion_data in referentiel.values():
        for comp in notion_data["competences"]:
            flat.append(
                {
                    "code": comp["code"],
                    "nom": comp["nom"],
                    "niveau": comp["niveau"],
                    "notion": notion_data["notion_nom"],
                }
            )
    return flat


def _parse_json(reponse_llm):
    texte = reponse_llm.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", texte)
    if match:
        texte = match.group(1).strip()
    match = re.search(r"\{[\s\S]*\}", texte)
    if match:
        texte = match.group(0)
    return json.loads(texte)


# ─── PASSE 1 : Détection des compétences évaluées par l'exercice ─────────────

FORMAT_DETECTION = """{
    "competences_evaluees": [
        {
            "code": "code de la compétence",
            "nom": "nom de la compétence",
            "notion": "nom de la notion d appartenance",
            "justification": "pourquoi cet exercice évalue cette compétence"
        }
    ]
}"""


def prompt_detection(notion, enonce, reponse_correcte, toutes_competences):
    competences_str = "\n".join(
        f"  - [{c['code']}] ({c['notion']}) {c['nom']} (niveau : {c['niveau']})"
        for c in toutes_competences
    )
    return f"""
        Tu es un expert en didactique des mathématiques.

        Un exercice t'est soumis. Ta tâche est d'identifier toutes les compétences
        mathématiques que cet exercice évalue, parmi la liste du référentiel ci-dessous.

        Exercice :
        - Notion ciblée : {notion}
        - Énoncé : {enonce}
        - Réponse correcte avec étapes : {reponse_correcte}

        Référentiel complet des compétences (toutes notions confondues) :
{competences_str}

        Consignes :
        - Identifie TOUTES les compétences mobilisées pour résoudre cet exercice,
          y compris celles qui appartiennent à d'autres notions (prérequis implicites).
        - Utilise UNIQUEMENT les codes du référentiel fourni.
        - Pour chaque compétence retenue, justifie brièvement pourquoi l'exercice la mobilise.
        - Réponds uniquement avec un JSON valide, sans texte avant ou après, sans markdown.

        Format attendu :
        {FORMAT_DETECTION}
    """


def detecter_competences(notion, enonce, reponse_correcte):
    """Passe 1 : identifie toutes les compétences que l'exercice évalue."""
    toutes_competences = _build_flat_competences(REFERENTIEL)
    prompt = prompt_detection(notion, enonce, reponse_correcte, toutes_competences)
    response = client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": prompt}]
    )
    return _parse_json(response.choices[0].message.content)


# ─── PASSE 2 : Analyse des lacunes sur les compétences détectées ──────────────

FORMAT_DIAGNOSTIC = """{
    "correct": true ou false,
    "diagnostic": {
        "type_erreur": "aucune | conceptuelle | confusion_regle | prerequis_manquant | erreur_calcul | erreur_inversion | erreur_signe | incomplet | hors_sujet",
        "gravite": "bloquante | importante | mineure | null",
        "etape_echec": "description de l étape où l erreur apparaît ou null",
        "erreur_recurrente": true ou false,
        "lacune_precise": "description claire de ce qui manque ou null",
        "competences_lacunaires": [
            {
                "code": "code de la compétence non acquise",
                "nom": "nom de la compétence",
                "notion": "notion d appartenance",
                "source": "evaluee_passe1 | detectee_passe2",
                "explication": "pourquoi cette compétence n est pas maîtrisée"
            }
        ]
    },
    "feedback_etudiant": "explication bienveillante en tu, 2-3 phrases, sans donner la réponse",
    "recommandation": {
        "exercice_suivant": "notion_prerequis | reexpliquer | exercice_similaire | exercice_difficile | notion_suivante",
        "notion_cible": "notion sur laquelle générer le prochain exercice",
        "niveau_cible": "debutant | intermediaire | avance",
        "consigne_generation": "instruction précise pour générer le prochain exercice"
    },
    "confiance_diagnostic": nombre entre 0.0 et 1.0
}"""


def prompt_analyse(
    notion,
    niveau,
    enonce,
    reponse_correcte,
    reponse_etudiant,
    competences_evaluees,
    toutes_competences,
    nb_tentatives,
    dernieres_erreurs,
):
    competences_evaluees_str = "\n".join(
        f"  - [{c['code']}] ({c['notion']}) {c['nom']}" for c in competences_evaluees
    )
    toutes_competences_str = "\n".join(
        f"  - [{c['code']}] ({c['notion']}) {c['nom']} (niveau : {c['niveau']})"
        for c in toutes_competences
    )
    return f"""
        Tu es un expert en diagnostic pédagogique pour des étudiants
        de première année d'université en mathématiques.

        Ta tâche est d'analyser la réponse d'un étudiant et d'identifier
        précisément quelles compétences ne sont pas acquises.

        Exercice :
        - Notion ciblée : {notion}
        - Niveau : {niveau}
        - Énoncé : {enonce}
        - Réponse correcte avec étapes : {reponse_correcte}
        - Réponse de l'étudiant : {reponse_etudiant}

        Compétences identifiées comme évaluées par cet exercice (passe 1) :
{competences_evaluees_str}

        Référentiel complet (toutes notions) — à utiliser si l'erreur révèle
        un gap non détecté en passe 1 :
{toutes_competences_str}

        Historique de l'étudiant :
        - Nombre de tentatives sur cette notion : {nb_tentatives}
        - Dernières erreurs connues : {dernieres_erreurs}

        Consignes :
        - Compare le raisonnement étape par étape, pas uniquement le résultat final.
        - Dans competences_lacunaires, liste toutes les compétences non maîtrisées :
            * Celles de la liste passe 1 que l'étudiant rate → source = "evaluee_passe1"
            * Celles du référentiel complet que l'erreur révèle en plus → source = "detectee_passe2"
        - Utilise UNIQUEMENT des codes du référentiel complet fourni.
        - Si la réponse est correcte, competences_lacunaires doit être [].
        - Le feedback doit être en "tu", bienveillant, sans donner la réponse.
        - Réponds uniquement avec un JSON valide, sans texte avant ou après, sans markdown.

        Format attendu :
        {FORMAT_DIAGNOSTIC}
    """


def analyser_lacunes(
    notion,
    niveau,
    enonce,
    reponse_correcte,
    reponse_etudiant,
    competences_evaluees,
    nb_tentatives,
    dernieres_erreurs,
):
    """Passe 2 : identifie les compétences non acquises, y compris hors passe 1."""
    toutes_competences = _build_flat_competences(REFERENTIEL)
    prompt = prompt_analyse(
        notion,
        niveau,
        enonce,
        reponse_correcte,
        reponse_etudiant,
        competences_evaluees,
        toutes_competences,
        nb_tentatives,
        dernieres_erreurs,
    )
    response = client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": prompt}]
    )
    return _parse_json(response.choices[0].message.content)


def _build_competences_dict(competences_evaluees, diagnostic):
    """
    Construit {code: True/False} attendu par update_scores().
    True  = compétence maîtrisée
    False = compétence lacunaire (passe 1 ou passe 2)
    """
    lacunaires = {
        c["code"]
        for c in diagnostic.get("diagnostic", {}).get("competences_lacunaires", [])
    }
    competences_dict = {
        c["code"]: c["code"] not in lacunaires for c in competences_evaluees
    }
    for c in diagnostic.get("diagnostic", {}).get("competences_lacunaires", []):
        competences_dict[c["code"]] = False
    return competences_dict


# ─── FONCTION ALLÉGÉE (passe 2 seulement, compétence déjà connue) ────────────


def diagnostiquer_depuis_competence(
    notion,
    niveau,
    enonce,
    reponse_correcte,
    reponse_etudiant,
    competence_cible,
    nb_tentatives=1,
    dernieres_erreurs="aucune",
):
    """
    Version allégée : saute la passe 1 car la compétence ciblée est déjà connue
    (fournie par choisir_competence). Fait uniquement la passe 2 pour confirmer
    la lacune et détecter d'éventuels gaps supplémentaires critiques.
    """
    competence_avec_notion = {**competence_cible, "notion": notion}
    competences_evaluees = [competence_avec_notion]
    diagnostic = analyser_lacunes(
        notion,
        niveau,
        enonce,
        reponse_correcte,
        reponse_etudiant,
        competences_evaluees,
        nb_tentatives,
        dernieres_erreurs,
    )
    competences_dict = _build_competences_dict(competences_evaluees, diagnostic)
    return {
        "competences_evaluees": competences_evaluees,
        "diagnostic": diagnostic,
        "competences_dict": competences_dict,
    }


# ─── FONCTION PRINCIPALE ─────────────────────────────────────────────────────


def diagnostiquer(
    notion,
    niveau,
    enonce,
    reponse_correcte,
    reponse_etudiant,
    nb_tentatives=1,
    dernieres_erreurs="aucune",
):
    """
    Passe 1 : détecte les compétences évaluées par l'exercice (toutes notions).
    Passe 2 : identifie les lacunes parmi ces compétences.

    Retourne :
    - competences_evaluees : liste des compétences mobilisées
    - diagnostic           : diagnostic complet des lacunes
    - competences_dict     : {code: True/False} prêt pour update_scores()
    """
    detection = detecter_competences(notion, enonce, reponse_correcte)
    competences_evaluees = detection.get("competences_evaluees", [])

    diagnostic = analyser_lacunes(
        notion,
        niveau,
        enonce,
        reponse_correcte,
        reponse_etudiant,
        competences_evaluees,
        nb_tentatives,
        dernieres_erreurs,
    )

    competences_dict = _build_competences_dict(competences_evaluees, diagnostic)

    return {
        "competences_evaluees": competences_evaluees,
        "diagnostic": diagnostic,
        "competences_dict": competences_dict,
    }


# ─── AFFICHAGE ────────────────────────────────────────────────────────────────


def afficher_resultat(resultat):
    diag = resultat["diagnostic"].get("diagnostic", {})
    competences_lacunaires = diag.get("competences_lacunaires", [])
    SEP = "=" * 60
    SEP2 = "-" * 60

    print(f"\n{SEP}")
    print("  PASSE 1 — COMPÉTENCES ÉVALUÉES PAR L'EXERCICE")
    print(SEP)
    for c in resultat["competences_evaluees"]:
        print(f"  [{c['code']}] {c['nom']}")
        print(f"         Notion  : {c.get('notion', '-')}")
        if c.get("justification"):
            print(f"         Raison  : {c['justification']}")
        print()

    print(SEP)
    print("  PASSE 2 — DIAGNOSTIC DES LACUNES")
    print(SEP)
    correct = resultat["diagnostic"].get("correct", False)
    print(f"  Réponse correcte   : {'Oui' if correct else 'Non'}")
    print(f"  Type d'erreur      : {diag.get('type_erreur', '-')}")
    print(f"  Gravité            : {diag.get('gravite', '-')}")
    print(f"  Étape d'échec      : {diag.get('etape_echec', '-')}")
    print(f"  Erreur récurrente  : {'Oui' if diag.get('erreur_recurrente') else 'Non'}")
    print(f"  Lacune identifiée  : {diag.get('lacune_precise', '-')}")
    print(
        f"  Confiance          : {resultat['diagnostic'].get('confiance_diagnostic', '-')}"
    )

    print(f"\n{SEP2}")
    print("  COMPÉTENCES LACUNAIRES")
    print(SEP2)
    if not competences_lacunaires:
        print("  Aucune lacune détectée.")
    for c in competences_lacunaires:
        label = (
            "★ hors exercice"
            if c.get("source") == "detectee_passe2"
            else "  dans l'exercice"
        )
        print(f"  [{c['code']}] {c['nom']}  —  {label}")
        print(f"         Notion      : {c['notion']}")
        print(f"         Explication : {c['explication']}")
        print()

    print(SEP2)
    print("  COMPÉTENCES DICT  (→ update_scores)")
    print(SEP2)
    print(f"  {resultat['competences_dict']}")

    recommandation = resultat["diagnostic"].get("recommandation", {})
    print(f"\n{SEP2}")
    print("  RECOMMANDATION")
    print(SEP2)
    print(f"  Prochain exercice  : {recommandation.get('exercice_suivant', '-')}")
    print(f"  Notion cible       : {recommandation.get('notion_cible', '-')}")
    print(f"  Niveau cible       : {recommandation.get('niveau_cible', '-')}")
    print(f"  Consigne           : {recommandation.get('consigne_generation', '-')}")

    print(f"\n{SEP2}")
    print("  FEEDBACK ÉTUDIANT")
    print(SEP2)
    print(f"  {resultat['diagnostic'].get('feedback_etudiant', '-')}")
    print(SEP)


# ─── TESTS ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ============================================================
    # TEST 1 — Erreur de signe sur une multiplication de fractions
    # ============================================================
    # resultat = diagnostiquer(
    #     notion="Fractions – Puissances – Radicaux",
    #     niveau="debutant",
    #     enonce="Calculer : (-3/4) × (-8/9)",
    #     reponse_correcte="(-3) × (-8) = 24 et 4 × 9 = 36, donc 24/36 = 2/3",
    #     reponse_etudiant="(-3) × 8 = -24 et 4 × 9 = 36, donc -24/36 = -2/3",
    # )

    # ============================================================
    # TEST 2 — Lacune hors notion ciblée (√1 = 0)
    # ============================================================
    resultat = diagnostiquer(
        notion="Équations – Inéquations",
        niveau="solide",
        enonce="Résoudre x² - 7x + 12 = 0",
        reponse_correcte=(
            "Discriminant : Δ = (-7)² - 4×1×12 = 49 - 48 = 1. "
            "Racines : x₁ = (7 + √1) / 2 = (7 + 1) / 2 = 4, "
            "x₂ = (7 - √1) / 2 = (7 - 1) / 2 = 3."
        ),
        reponse_etudiant=(
            "Δ = 49 - 48 = 1. "
            "Comme √1 = 0, il n'y a qu'une seule racine : x = 7 / 2 = 3.5"
        ),
        nb_tentatives=1,
        dernieres_erreurs="aucune",
    )

    afficher_resultat(resultat)
