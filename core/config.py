"""Configuration applicative.

CORRECTION PAR RAPPORT À LA V1
------------------------------
La v1 stockait ses réglages dans une table `Config` clé/valeur libre, et cette
table avait fini par contenir des **doublons** : `retraite_taxe` (30,0) et
`retraite_tax` (0,0), `retraite_apport_mensuel` et `retraite_app_mensuel`. Deux
clés, deux valeurs, un seul lecteur — le jour où l'on modifiait la mauvaise, la
projection changeait d'impôt sans raison visible.

Ici, la configuration est un **schéma typé**. Une clé inconnue est rejetée, une
clé manquante prend sa valeur par défaut déclarée. Pas de doublon possible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import POCHES, Perimetre


@dataclass
class Reglages:
    """Réglages du porteur. Une seule occurrence de chaque réglage."""

    # --- Identité fiscale ---
    statut_fiscal: str = "Marié(e) / Pacsé(e)"
    parts_fiscales: float = 3.0          # couple + 2 enfants = 2 + 0,5 + 0,5
    autres_revenus_imposables: float = 0.0

    # --- Projection retraite ---
    annee_depart_retraite: int = 2055
    apport_mensuel_eur: float = 250.0
    taux_imposition_plus_value: float = 0.308   # PFU : 12,8 % + 17,2 %

    # --- Inflation ---
    # Inflation officielle par défaut. Gave soutient qu'elle sous-estime l'érosion
    # réelle du pouvoir d'achat ; l'application calcule donc les deux.
    inflation_cible: float = 0.02
    inflation_reelle_estimee: float = 0.045     # à ajuster par le porteur

    # --- Rééquilibrage ---
    seuil_min_ordre_eur: float = 250.0     # en dessous, les frais mangent la correction
    devise_affichage: str = "EUR"

    # --- Sources ---
    # Les taux de change et les cours ne sont JAMAIS devinés. Ces valeurs ne sont
    # que des préférences d'affichage.
    afficher_bandeaux_erreurs: bool = True

    def valider(self) -> list[str]:
        """Retourne la liste des incohérences détectées."""
        erreurs = []
        if self.parts_fiscales < 0.5:
            erreurs.append("parts_fiscales doit être >= 0,5")
        if self.taux_imposition_plus_value > 0.60:
            erreurs.append("taux_imposition_plus_value supérieur à 60 % : vérifier la saisie")
        if self.apport_mensuel_eur < 0:
            erreurs.append("apport_mensuel_eur négatif")
        if self.annee_depart_retraite < 2026:
            erreurs.append("annee_depart_retraite dans le passé")
        return erreurs


# Clés autorisées : toute autre clé trouvée en base est signalée, pas absorbée.
CLES_AUTORISEES: set[str] = set(Reglages.__dataclass_fields__)


DEFAUTS = Reglages()


def depuis_dict(d: dict) -> tuple[Reglages, list[str]]:
    """Construit les réglages depuis un dict. Retourne `(reglages, cles_inconnues)`.

    Les clés inconnues sont renvoyées pour être signalées à l'utilisateur, jamais
    absorbées silencieusement comme dans la v1.
    """
    connues = set(Reglages.__dataclass_fields__)
    inconnues = [k for k in d if k not in connues]
    filtrees = {k: v for k, v in d.items() if k in connues}

    # Cohérence des types numériques.
    for cle, val in list(filtrees.items()):
        attendu = Reglages.__dataclass_fields__[cle].type
        if "float" in str(attendu) or "int" in str(attendu):
            try:
                filtrees[cle] = float(val) if "float" in str(attendu) else int(float(val))
            except (TypeError, ValueError):
                filtrees[cle] = getattr(DEFAUTS, cle)

    return Reglages(**filtrees), inconnues


def plan_allocation() -> list[dict]:
    """Le plan d'allocation, pour affichage et vérification."""
    return [
        {
            "cle": p.cle,
            "nom": p.nom,
            "cible": p.cible,
            "bande": p.bande,
            "perimetre": p.perimetre.value,
            "membres": p.membres,
            "description": p.description,
        }
        for p in POCHES
    ]


def verifier_plan() -> list[str]:
    """Vérifie que les poids cibles des poches investies totalisent 100 %."""
    total = sum(p.cible for p in POCHES if p.perimetre == Perimetre.INVESTI)
    if abs(total - 1.0) > 1e-9:
        return [f"Les poids cibles des poches investies totalisent {total:.1%} au lieu de 100 %."]
    return []
