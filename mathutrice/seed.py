"""seed.py — Données de référence et comptes de démonstration.

Appelé au démarrage, une fois les tables créées. Chaque ligne est insérée
uniquement si elle manque : sur une base neuve, tout est créé ; sur une base
déjà remplie, rien n'est écrit.

Les notions et les compétences viennent du REFERENTIEL, qui est la source de
vérité : `referentiel_key` et `referentiel_code` sont les clés par lesquelles
le reste de l'application retrouve ces lignes.
"""

import uuid
from datetime import datetime

from sqlmodel import Session, select

from mathutrice import models
from mathutrice.referentiel import REFERENTIEL

# Le REFERENTIEL porte le nom de chaque notion, pas sa description. Ajouter une
# notion là-bas oblige donc à l'ajouter ici : _description_de() le rappelle
# plutôt que de laisser passer une description vide.
NOTION_DESCRIPTIONS = {
    "trigonometrie": (
        "Étude des fonctions trigonométriques, des angles et du cercle "
        "trigonométrique."
    ),
    "fractions_puissances_radicaux": (
        "Manipulation des fractions, puissances et radicaux."
    ),
    "logarithme_exponentielle": (
        "Étude des fonctions logarithme et exponentielle."
    ),
    "manipulation_expressions_litterales": (
        "Isolement et manipulation de variables dans des expressions "
        "algébriques."
    ),
    "equations_inequations": (
        "Résolution d'équations et d'inéquations du premier et second degré."
    ),
    "polynomes_factorisation": (
        "Étude des polynômes, factorisation et identités remarquables."
    ),
    "analyse_dimensionnelle": (
        "Dimensions, unités et homogénéité des formules physiques."
    ),
}

# Un compte par rôle, pour que la connexion de développement ait de quoi
# choisir sur une base neuve. Aucun secret : ces comptes ne portent pas de
# mot de passe, seul AUTH_MODE=dev permet de s'y connecter.
DEMO_USERS = [
    ("eleve@epf.fr", "Démo Élève", "Student"),
    ("enseignant@epf.fr", "Démo Enseignant", "Teacher"),
    ("admin@epf.fr", "Démo Admin", "Admin"),
]


def _description_de(referentiel_key: str) -> str:
    """La description d'une notion, ou une erreur claire si elle manque."""
    try:
        return NOTION_DESCRIPTIONS[referentiel_key]
    except KeyError:
        raise KeyError(
            f"Notion {referentiel_key!r} sans description : "
            "ajoutez-la à NOTION_DESCRIPTIONS dans mathutrice/seed.py."
        ) from None


def _existe(session: Session, model, champ, valeur) -> bool:
    """Vrai si une ligne de `model` porte déjà cette valeur sur ce champ."""
    return session.exec(select(model).where(champ == valeur)).first() is not None


def _seed_referentiel(session: Session) -> int:
    """Insère les notions et compétences manquantes. Renvoie le nombre de lignes."""
    inserted = 0

    for referentiel_key, notion_data in REFERENTIEL.items():
        notion = session.exec(
            select(models.Notion).where(
                models.Notion.referentiel_key == referentiel_key
            )
        ).first()

        if not notion:
            notion = models.Notion(
                notion_id=uuid.uuid4(),
                referentiel_key=referentiel_key,
                title=notion_data["notion_nom"],
                description=_description_de(referentiel_key),
            )
            session.add(notion)
            inserted += 1

        for comp in notion_data["competences"]:
            if _existe(
                session, models.Competence, models.Competence.referentiel_code, comp["code"]
            ):
                continue

            session.add(
                models.Competence(
                    competence_id=uuid.uuid4(),
                    referentiel_code=comp["code"],
                    title=comp["nom"],
                    level=comp["niveau"],
                    notion_id=notion.notion_id,
                )
            )
            inserted += 1

    return inserted


def _seed_users(session: Session) -> int:
    """Insère les comptes de démonstration manquants. Renvoie le nombre de lignes."""
    inserted = 0
    now = datetime.utcnow()

    for email, name, role in DEMO_USERS:
        if _existe(session, models.User, models.User.email, email):
            continue

        session.add(
            models.User(
                sso_id=uuid.uuid4(),
                name=name,
                email=email,
                role=role,
                created_at=now,
            )
        )
        inserted += 1

    return inserted


def seed_missing(engine) -> None:
    """Insère les lignes de référence absentes.

    Sur une base neuve, tout est créé. Sur une base déjà remplie, aucune
    écriture n'a lieu : c'est ce qui rend l'appel au démarrage sans effet.
    """
    with Session(engine) as session:
        inserted = _seed_referentiel(session) + _seed_users(session)

        if not inserted:
            return

        session.commit()
        print(f"Base initialisée : {inserted} lignes insérées.")
