"""Moteur de rééquilibrage par poches.

CORRECTIONS PAR RAPPORT À LA V1
-------------------------------
1. **L'assiette excluait les « Cash réserve ».** Le code ne sommait que le type
   `💵 Cash` et ignorait `🏦 Cash réserve`. Résultat : 12 277 €, soit 13 % du
   patrimoine, étaient absents du dénominateur et toutes les dérives affichées
   étaient fausses.

   Le vrai défaut était plus profond : l'épargne de précaution n'a pas à entrer
   dans une assiette d'allocation, mais elle doit être *exclue explicitement et
   pour une raison énoncée*, pas oubliée par un filtre de type. Ici, le
   périmètre est porté par le modèle (`Perimetre.INVESTI` vs `PRECAUTION` /
   `COURANT`), pas par une chaîne de caractères.

2. **Le seuil était unique et mécanique** : `abs(écart) >= 2.0 and abs(montant) >= 1000`.
   Un plancher de 1 000 $ en valeur absolue dispensait de rééquilibrer une petite
   poche même très dérivée. On travaille désormais par poche, avec sa propre bande
   et son propre seuil de rentabilité.

3. **Aucune notion de régime.** Gave raisonne en quatre quadrants et la
   pondération cible en dépend. Le moteur est prêt à recevoir un régime ; la
   pondération statique reste le défaut assumé par le porteur.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .portfolio import EtatPoche


@dataclass
class Ordre:
    """Un ordre de rééquilibrage proposé."""

    ticker: str
    sens: str                 # "achat" ou "vente"
    montant_eur: float
    quantite: float
    poche: str
    motif: str = ""
    montant_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.montant_usd == 0.0 and self.montant_eur != 0.0:
            self.montant_usd = self.montant_eur


@dataclass
class EcartPoche:
    """Diagnostic d'une poche."""

    poche_cle: str
    poche_nom: str
    poids_reel: float
    poids_cible: float
    bande: float
    valeur_eur: float
    valeur_cible_eur: float
    actifs: list = field(default_factory=list)
    valeur_usd: float = 0.0
    valeur_cible_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.valeur_usd == 0.0 and self.valeur_eur != 0.0:
            self.valeur_usd = self.valeur_eur
        if self.valeur_cible_usd == 0.0 and self.valeur_cible_eur != 0.0:
            self.valeur_cible_usd = self.valeur_cible_eur

    @property
    def ecart_points(self) -> float:
        return (self.poids_reel - self.poids_cible) * 100.0

    @property
    def hors_bande(self) -> bool:
        return abs(self.poids_reel - self.poids_cible) > self.bande

    @property
    def ecart_eur(self) -> float:
        """Montant à injecter (positif) ou retirer (négatif) pour revenir à la cible."""
        return self.valeur_cible_eur - self.valeur_eur

    @property
    def ecart_usd(self) -> float:
        """Même écart exprimé en dollars ($)."""
        return self.valeur_cible_usd - self.valeur_usd

    @property
    def rang(self) -> float:
        """Priorité de traitement : plus l'écart relatif est grand, plus c'est urgent."""
        if self.poids_cible <= 0:
            return 0.0
        return abs(self.ecart_points) / (self.poids_cible * 100.0)


def diagnostiquer(
    etats,
    total_investi_eur: float,
    total_investi_usd: float | None = None,
) -> list[EcartPoche]:
    """Construit le diagnostic par poche, trié par urgence décroissante."""
    ecarts: list[EcartPoche] = []
    tot_usd = total_investi_usd if (total_investi_usd and total_investi_usd > 0) else total_investi_eur
    for cle, etat in etats.items():
        if not etat.poche.est_investi:
            continue
        valeur_cible = total_investi_eur * etat.poche.cible
        val_usd = getattr(etat, "valeur_usd", 0.0) or etat.valeur_eur
        valeur_cible_usd = tot_usd * etat.poche.cible
        ecarts.append(EcartPoche(
            poche_cle=cle,
            poche_nom=etat.poche.nom,
            poids_reel=etat.poids_reel,
            poids_cible=etat.poids_cible,
            bande=etat.poche.bande,
            valeur_eur=etat.valeur_eur,
            valeur_cible_eur=valeur_cible,
            actifs=list(etat.actifs),
            valeur_usd=val_usd,
            valeur_cible_usd=valeur_cible_usd,
        ))
    return sorted(ecarts, key=lambda e: -e.rang)


def generer_ordres(
    ecarts: list[EcartPoche],
    seuil_min_eur: float = 250.0,
) -> tuple[list[Ordre], list[EcartPoche]]:
    """Propose les ordres pour les seules poches hors bande.

    `seuil_min_eur` : en dessous, l'ordre coûte plus en frais de courtage qu'il ne
    corrige de dérive. On le signale plutôt que de l'exécuter.

    Retourne `(ordres, poches_a_surveiller)`.
    """
    ordres: list[Ordre] = []
    a_surveiller: list[EcartPoche] = []

    for e in ecarts:
        if not e.hors_bande:
            continue

        ecart = e.ecart_eur
        if abs(ecart) < seuil_min_eur:
            a_surveiller.append(e)
            continue

        if not e.actifs:
            a_surveiller.append(e)
            continue

        sens = "achat" if ecart > 0 else "vente"

        # Répartition de l'ordre entre les actifs de la poche, au prorata de leur
        # valeur. Simple, et évite de concentrer le rééquilibrage sur un seul ETF.
        total_poche = sum(a.valeur_eur for a in e.actifs) or 1.0
        ecart_u = abs(getattr(e, "ecart_usd", ecart))
        for a in e.actifs:
            part = a.valeur_eur / total_poche
            montant = abs(ecart) * part
            montant_u = ecart_u * part
            quantite = montant / (a.prix * (a.dernier_taux or 1.0)) if a.prix > 0 else 0.0
            ordres.append(Ordre(
                ticker=a.ticker,
                sens=sens,
                montant_eur=round(montant, 2),
                quantite=round(quantite, 6),
                poche=e.poche_cle,
                motif=f"{e.poche_nom} à {e.poids_reel*100:.1f} % vs cible "
                      f"{e.poids_cible*100:.1f} % (bande ±{e.bande*100:.1f} pts)",
                montant_usd=round(montant_u, 2),
            ))

    return ordres, a_surveiller


def resume(etats, total_investi_eur: float) -> dict:
    """Vue d'ensemble pour le tableau de bord."""
    ecarts = diagnostiquer(etats, total_investi_eur)
    hors = [e for e in ecarts if e.hors_bande]
    return {
        "nb_poches": len(ecarts),
        "nb_hors_bande": len(hors),
        "pire_ecart": max((abs(e.ecart_points) for e in ecarts), default=0.0),
        "ecarts": ecarts,
        "besoin_reequilibrage": bool(hors),
    }
