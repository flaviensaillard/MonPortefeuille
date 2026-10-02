"""Modèles de données — MonPortefeuille 2.

Deux notions structurantes, absentes de la v1 :

1. La **poche** (sleeve). Un actif appartient à une poche, et une poche a un poids
   cible et une bande de tolérance. C'est la poche qu'on rééquilibre, jamais l'actif
   isolément. L'or et le bitcoin partagent la poche « réserve de valeur » parce que
   le porteur les traite comme substituables.

2. Le **périmètre**. Un actif est soit « investi » (il compte dans l'allocation et se
   rééquilibre), soit « hors portefeuille » (épargne de précaution, compte courant :
   il fait partie du patrimoine mais sa pondération n'a aucun sens). La v1 confondait
   les deux, ce qui faussait toutes les dérives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Classe(str, Enum):
    """Classe économique d'un actif. Détermine le régime fiscal."""

    OR = "or"                        # ETC or (IGLN.L) — régime valeurs mobilières
    OR_PHYSIQUE = "or_physique"      # Lingots, pièces — article 150 VI
    CRYPTO = "crypto"                # Bitcoin etc. — article 150 VH bis
    ACTION_ETF = "action_etf"        # Actions et ETF actions — article 150-0 A
    OBLIGATION_ETF = "obligation_etf"  # ETF obligataires — article 150-0 A
    ESPECE = "espece"                   # Cash, devises — hors champ des plus-values


# Régime fiscal applicable selon la classe.
REGIMES_FISCAUX: dict[Classe, str] = {
    Classe.OR: "150-0 A",
    Classe.OR_PHYSIQUE: "150 VI",
    Classe.CRYPTO: "150 VH bis",
    Classe.ACTION_ETF: "150-0 A",
    Classe.OBLIGATION_ETF: "150-0 A",
    Classe.ESPECE: "hors_champ",
}


class Perimetre(str, Enum):
    """Un actif investi se rééquilibre. Un actif hors portefeuille se suit seulement."""

    INVESTI = "investi"
    PRECAUTION = "precaution"   # Livret CHF — disponible en 5 minutes
    COURANT = "courant"         # Compte courant Revolut


@dataclass
class Poche:
    """Une poche d'allocation : un poids cible, une bande, des actifs membres."""

    cle: str
    nom: str
    cible: float                 # part du patrimoine investi, ex. 0.20
    bande: float                 # tolérance en points de pourcentage, ex. 0.03
    membres: list[str] = field(default_factory=list)
    perimetre: Perimetre = Perimetre.INVESTI
    description: str = ""

    @property
    def cle_perimetre(self) -> str:
        return self.perimetre.value

    @property
    def est_investi(self) -> bool:
        """Une poche hors portefeuille n'entre pas dans l'allocation."""
        return self.perimetre == Perimetre.INVESTI


# ---------------------------------------------------------------------------
# Le plan d'allocation du porteur.
#
# Les poids sont ceux qu'il a validés : il surpondère volontairement les actions
# pour la croissance, et traite l'or et le bitcoin comme une seule poche
# « réserve de valeur » substituable.
#
# Les bandes sont plus serrées sur la réserve de valeur (±3 pts) parce que c'est
# la poche qui porte la thèse anti-monnaie-fiduciaire : une dérive y est plus
# coûteuse en doctrine qu'en performance.
# ---------------------------------------------------------------------------

POCHES: list[Poche] = [
    Poche(
        cle="rv",
        nom="Réserve de valeur",
        cible=0.20,
        bande=0.03,
        membres=["IGLN.L", "BTCUSDT"],
        description="Or (ETC) + Bitcoin. Substituts assumés face à la dépréciation monétaire.",
    ),
    Poche(
        cle="energie",
        nom="Énergie",
        cible=0.30,
        bande=0.05,
        membres=["XDW0.L"],
        description="ETF énergie. Surexposition volontaire à la croissance.",
    ),
    Poche(
        cle="asie",
        nom="Asie / Chine",
        cible=0.30,
        bande=0.05,
        membres=["FLXC.L"],
        description="ETF Chine (Franklin FTSE China). Surexposition volontaire à la croissance.",
    ),
    Poche(
        cle="jgb",
        nom="Obligations japonaises",
        cible=0.20,
        bande=0.05,
        membres=["XJSE.SW"],
        description="ETF dettes d'État japonaises. Poche de désinflation et de récession.",
    ),
    Poche(
        cle="precaution",
        nom="Épargne de précaution",
        cible=0.0,
        bande=0.0,
        membres=["CHF", "CNY"],
        perimetre=Perimetre.PRECAUTION,
        description="Livret CHF chez Swissquote. Disponible en 5 minutes. Jamais rééquilibré.",
    ),
    Poche(
        cle="courant",
        nom="Compte courant",
        cible=0.0,
        bande=0.0,
        membres=["EUR", "USD"],
        perimetre=Perimetre.COURANT,
        description="Revolut. Hors portefeuille d'investissement.",
    ),
]

POCHES_PAR_CLE: dict[str, Poche] = {p.cle: p for p in POCHES}
POCHES_INVESTIES: list[Poche] = [p for p in POCHES if p.perimetre == Perimetre.INVESTI]

# Actif -> poche, pour la résolution rapide.
ACTIF_VERS_POCHE: dict[str, str] = {
    ticker: p.cle for p in POCHES for ticker in p.membres
}


def poche_de(ticker: str) -> Poche | None:
    """Retourne la poche d'un ticker, ou None s'il est inconnu."""
    cle = ACTIF_VERS_POCHE.get(str(ticker).upper().strip())
    return POCHES_PAR_CLE.get(cle) if cle else None


@dataclass
class Actif:
    """Une ligne de portefeuille, valorisée."""

    ticker: str
    classe: Classe
    devise_cotation: str
    poche: str
    quantite: float = 0.0
    prix: float = 0.0                 # dans la devise de cotation
    valeur_eur: float = 0.0           # valorisation en euros (indication & fiscalité)
    valeur_usd: float = 0.0           # valorisation en dollars (unité de compte)
    dernier_taux: float | None = None # taux utilisé, pour traçabilité
    dernier_taux_usd: float | None = None

    def __post_init__(self) -> None:
        if self.valeur_usd == 0.0 and self.valeur_eur != 0.0:
            self.valeur_usd = self.valeur_eur

    @property
    def regime_fiscal(self) -> str:
        return REGIMES_FISCAUX[self.classe]

    @property
    def est_investi(self) -> bool:
        p = POCHES_PAR_CLE.get(self.poche)
        return bool(p and p.perimetre == Perimetre.INVESTI)
