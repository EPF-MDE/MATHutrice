"""
session_generator.py — Génère une session d'entraînement mixte (QCM / QRO / SBS)
à partir des scores réels de l'élève en base de données.

Flow :
  1. Vérifier si c'est la première fois (aucune progression en DB pour cette notion)
  2. Si première fois → positionnement : 5 QCM + 3 QRO + 2 SBS
  3. Sinon → entraînement : une question à la fois via generate_exercise_randomly
  4. Les scores sont persistés en DB après chaque réponse via /submit_answer
"""

import copy
import random
from decimal import Decimal
from datetime import datetime, UTC
import uuid
from uuid import UUID
from sqlmodel import Session, select

from mathutrice import models
from mathutrice.fonctions_python.base_generator import update_scores
from mathutrice.fonctions_python.main import (
    generate_mixed_test,
    generate_exercise_randomly,
)
from mathutrice.referentiel import REFERENTIEL




def get_competence_map_by_codes(codes: list[str], db: Session) -> dict[str, models.Competence]:
    """
    Convertit les codes du REFERENTIEL vers les lignes Competence en BDD.

    Exemple :
    tr01 -> Competence(competence_id=UUID(...), referentiel_code="tr01")
    """
    if not codes:
        return {}

    rows = db.exec(
        select(models.Competence).where(
            models.Competence.referentiel_code.in_(codes)
        )
    ).all()

    return {row.referentiel_code: row for row in rows}


def get_notion_by_referentiel_key(referentiel_key: str, db: Session) -> models.Notion | None:
    """
    Récupère la notion BDD à partir de la clé du REFERENTIEL.
    Exemple : trigonometrie -> notion_id UUID
    """
    return db.exec(
        select(models.Notion).where(
            models.Notion.referentiel_key == referentiel_key
        )
    ).first()




# ─── NIVEAU AUTO ──────────────────────────────────────────────────────────────


def deduire_niveau_eleve(notion_data: dict) -> str:
    """
    Déduit le niveau de l'élève pour une notion donnée d'après les scores.

    Règle :
      - Moyenne basique < 0.7           → basique
      - Moyenne basique >= 0.7
        ET moyenne solide < 0.7         → solide
      - Moyenne basique >= 0.7
        ET moyenne solide >= 0.7        → expert
    """

    def moyenne_niveau(niveau: str) -> float:
        comps = [c for c in notion_data["competences"] if c["niveau"] == niveau]
        if not comps:
            return 1.0  # niveau inexistant → considéré acquis
        return sum(c["score"] for c in comps) / len(comps)

    if moyenne_niveau("basique") < 0.7:
        return "basique"
    if moyenne_niveau("solide") < 0.7:
        return "solide"
    return "expert"


# ─── INJECTION SCORES DB ──────────────────────────────────────────────────────


def build_notion_data_with_scores(
    notion_key: str,
    sso_id: UUID,
    db: Session,
) -> dict:
    """
    Reconstruit le dict notion du REFERENTIEL en remplaçant les scores statiques
    par les vrais scores de l'élève issus de la table progression.

    Le REFERENTIEL travaille avec des codes métier : tr01, le01...
    La BDD travaille avec des UUID : competence.competence_id.
    """
    if notion_key not in REFERENTIEL:
        raise ValueError(f"Notion inconnue : {notion_key}")

    notion_data = copy.deepcopy(REFERENTIEL[notion_key])
    codes = [c["code"] for c in notion_data["competences"]]

    code_to_competence = get_competence_map_by_codes(codes, db)
    competence_ids = [comp.competence_id for comp in code_to_competence.values()]

    if not competence_ids:
        for comp in notion_data["competences"]:
            comp["score"] = 0.5
        return notion_data

    rows = db.exec(
        select(models.Progression).where(
            models.Progression.sso_id == sso_id,
            models.Progression.competence_id.in_(competence_ids),
        )
    ).all()

    id_to_code = {
        comp.competence_id: code
        for code, comp in code_to_competence.items()
    }

    scores_db = {
        id_to_code[row.competence_id]: float(row.score)
        for row in rows
        if row.competence_id in id_to_code
    }

    for comp in notion_data["competences"]:
        comp["score"] = scores_db.get(comp["code"], 0.5)

    return notion_data


# ─── VÉRIFICATION PREMIÈRE FOIS ───────────────────────────────────────────────


def is_first_session(notion_key: str, sso_id: UUID, db: Session) -> bool:
    if notion_key not in REFERENTIEL:
        raise ValueError(f"Notion inconnue : {notion_key}")

    codes = [c["code"] for c in REFERENTIEL[notion_key]["competences"]]

    code_to_competence = get_competence_map_by_codes(codes, db)
    competence_ids = [comp.competence_id for comp in code_to_competence.values()]

    if not competence_ids:
        return True

    attempted = db.exec(
        select(models.Progression).where(
            models.Progression.sso_id == sso_id,
            models.Progression.competence_id.in_(competence_ids),
            models.Progression.attempts_count > 0,
        )
    ).first()

    return attempted is None


# ─── INIT PROGRESSION ─────────────────────────────────────────────────────────


def init_progressions_for_user(sso_id: UUID, db: Session) -> None:
    """
    Initialise les entrées de progression pour toutes les compétences
    du REFERENTIEL pour un nouvel élève.

    Score initial = 0.50.
    level initial = moyen.
    updated_at = None car la compétence n'a pas encore été travaillée.
    """
    for notion_key, notion_data in REFERENTIEL.items():
        codes = [c["code"] for c in notion_data["competences"]]
        code_to_competence = get_competence_map_by_codes(codes, db)

        for comp in notion_data["competences"]:
            competence = code_to_competence.get(comp["code"])

            if not competence:
                print(f"[INIT PROGRESSION] Compétence introuvable en BDD : {comp['code']}")
                continue

            existing = db.exec(
                select(models.Progression).where(
                    models.Progression.sso_id == sso_id,
                    models.Progression.competence_id == competence.competence_id,
                )
            ).first()

            if existing:
                continue

            prog = models.Progression(
                progression_id=uuid.uuid4(),
                score=Decimal("0.50"),
                updated_at=None,
                level="moyen",
                attempts_count=0,
                competence_id=competence.competence_id,
                sso_id=sso_id,
            )
            db.add(prog)

    db.commit()


# ─── PERSISTANCE SCORE APRÈS RÉPONSE ─────────────────────────────────────────


def persist_score_update(
    sso_id: UUID,
    competences_dict: dict,  # { "tr01": True, "tr04": False }
    question_type: str,      # "QCM" | "QRO" | "SBS"
    question_niveau: str,    # "basique" | "solide" | "expert"
    db: Session,
) -> dict:
    """
    Applique les règles de scoring de update_scores() et persiste
    les nouveaux scores en base de données.

    Le REFERENTIEL utilise des codes comme "tr01".
    La BDD utilise des UUID comme competence_id.
    Donc on convertit toujours :
    referentiel_code -> competence_id UUID.
    """
    local_ref = copy.deepcopy(REFERENTIEL)

    # 1. Codes touchés par la question : ["tr01", "tr04", ...]
    codes = list(competences_dict.keys())

    # 2. Convertir les codes du REFERENTIEL en vraies compétences BDD
    code_to_competence = get_competence_map_by_codes(codes, db)

    competence_ids = [
        competence.competence_id
        for competence in code_to_competence.values()
    ]

    # 3. Récupérer les progressions existantes avec les UUID
    rows = db.exec(
        select(models.Progression).where(
            models.Progression.sso_id == sso_id,
            models.Progression.competence_id.in_(competence_ids),
        )
    ).all()

    # 4. Convertir competence_id UUID -> code référentiel
    id_to_code = {
        competence.competence_id: code
        for code, competence in code_to_competence.items()
    }

    scores_db = {
        id_to_code[row.competence_id]: float(row.score)
        for row in rows
        if row.competence_id in id_to_code
    }

    # 5. Injecter les scores actuels dans le référentiel local
    for notion_data in local_ref.values():
        for comp in notion_data["competences"]:
            if comp["code"] in scores_db:
                comp["score"] = scores_db[comp["code"]]

    # 6. Adapter le niveau pour update_scores()
    niveau_map = {
        "basique": "facile",
        "solide": "intermediaire",
        "expert": "difficile",
    }

    q_niveau = niveau_map.get(question_niveau, "intermediaire")

    question_format = {
        "type": question_type.upper(),
        "niveau": q_niveau,
    }

    # 7. Calculer les nouveaux scores
    updated_ref, _, nouveaux_scores = update_scores(
        local_ref,
        question_format,
        competences_dict,
    )

    # 8. Sauvegarder en BDD
    now = datetime.now(UTC).replace(tzinfo=None)

    for code, new_score in nouveaux_scores.items():
        competence = code_to_competence.get(code)

        if not competence:
            print(f"[SCORE UPDATE] Compétence introuvable en BDD : {code}")
            continue

        clamped = max(0.0, min(1.0, new_score))

        if clamped < 0.4:
            level = "faible"
        elif clamped < 0.75:
            level = "moyen"
        else:
            level = "avance"

        prog = db.exec(
            select(models.Progression).where(
                models.Progression.sso_id == sso_id,
                models.Progression.competence_id == competence.competence_id,
            )
        ).first()

        if prog:
            prog.score = Decimal(str(round(clamped, 2)))
            prog.level = level
            prog.updated_at = now
            prog.attempts_count += 1
            db.add(prog)
        else:
            # Sécurité : créer si absent
            prog = models.Progression(
                progression_id=uuid.uuid4(),
                score=Decimal(str(round(clamped, 2))),
                updated_at=now,
                level=level,
                attempts_count=1,
                competence_id=competence.competence_id,
                sso_id=sso_id,
            )
            db.add(prog)

    db.commit()

    return {
        code: max(0.0, min(1.0, score))
        for code, score in nouveaux_scores.items()
    }


# ─── GÉNÉRATION POSITIONNEMENT ────────────────────────────────────────────────


def generate_positioning_session(notion_key: str, sso_id: UUID, db: Session) -> dict:
    """
    Génère le test de positionnement sur les 3 niveaux.
    Distribution : 1 basique (3 questions) + 1 solide (6 questions) + 1 expert (2 questions)
    Adapté au nombre de compétences par niveau dans le REFERENTIEL.
    """
    notion_data = build_notion_data_with_scores(notion_key, sso_id, db)
    notion_nom = notion_data["notion_nom"]

    q_basique = generate_mixed_test(
        notion=notion_key,
        niveau="basique",
        n_qcm=1,
        n_qro=1,
        n_steps=1,
        notion_data_override=notion_data,
    )
    q_solide = generate_mixed_test(
        notion=notion_key,
        niveau="solide",
        n_qcm=3,
        n_qro=2,
        n_steps=1,
        notion_data_override=notion_data,
    )
    q_expert = generate_mixed_test(
        notion=notion_key,
        niveau="expert",
        n_qcm=1,
        n_qro=1,
        n_steps=0,
        notion_data_override=notion_data,
    )

    questions = q_basique + q_solide + q_expert

    # Dédupliquer par compétence
    seen = set()
    unique_questions = []
    for q in questions:
        comp_code = (q.get("competence_cible") or {}).get("code", "")
        key = comp_code + q.get("type", "")
        if key not in seen:
            seen.add(key)
            unique_questions.append(q)
    questions = unique_questions
    random.shuffle(questions)

    return {
        "session_type": "positionnement",
        "notion_nom": notion_nom,
        "notion_key": notion_key,
        "niveau_eleve": "mixte",
        "questions": questions,
    }


# ─── GÉNÉRATION QUESTION ENTRAÎNEMENT ─────────────────────────────────────────


def generate_next_question(notion_key: str, sso_id: UUID, db: Session) -> dict:
    """
    Génère une seule question pour la phase d'entraînement.
    Le type (qcm/qro/sbs) est choisi aléatoirement.
    Le niveau est déduit des scores actuels de l'élève.
    """
    notion_data = build_notion_data_with_scores(notion_key, sso_id, db)
    niveau_eleve = deduire_niveau_eleve(notion_data)

    # On reconstruit un mini-REFERENTIEL avec les scores injectés
    # pour que generate_exercise_randomly les utilise
    local_ref = copy.deepcopy(REFERENTIEL)
    local_ref[notion_key] = notion_data

    questions = generate_exercise_randomly(local_ref, niveau_eleve, notion_key)

    return {
        "session_type": "entrainement",
        "notion_nom": notion_data["notion_nom"],
        "notion_key": notion_key,
        "niveau_eleve": niveau_eleve,
        "questions": questions,
    }
