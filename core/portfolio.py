"""Construction du portefeuille depuis les transactions.

CORRECTION PAR RAPPORT À LA V1
------------------------------
La v1 tenait les quantités dans une table `Donnees` mise à jour à la main, en
parallèle d'une table `Transaction`. Les deux pouvaient diverger, et rien ne le
détectait. On a vu le cas sur XJSE.SW : 9 achats saisis en JPY, un saisi en USD,
et un PRU qui ne veut plus rien dire.

Ici, **la table `Donnees` n'existe plus**. Les positions sont un résultat, pas une
saisie : on les recalcule depuis l'historique des transactions. Une seule source
de vérité, donc plus rien à réconcilier.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import pandas as pd
from . import dates

from . import fx, prices
from .models import (
    ACTIF_VERS_POCHE,
    POCHES_PAR_CLE,
    Actif,
    Classe,
    Perimetre,
    Poche,
    poche_de,
)

log = logging.getLogger(__name__)


# Classification d'un ticker. À étendre si de nouveaux actifs entrent.
# La classe détermine le régime fiscal — c'est le seul endroit où elle est fixée.
CLASSES: dict[str, Classe] = {
    "IGLN.L": Classe.OR,                  # ETC or — régime valeurs mobilières
    "BTCUSDT": Classe.CRYPTO,             # notation de l'utilisateur, conservée telle quelle
    "BTC-USD": Classe.CRYPTO,             # symbole Yahoo, si un jour il le saisit
    "XDW0.L": Classe.ACTION_ETF,          # Xtrackers MSCI World Energy
    "FLXC.L": Classe.ACTION_ETF,          # Franklin FTSE China
    "RI.PA": Classe.ACTION_ETF,           # Ripcurl / action unitaire
    "XJSE.SW": Classe.OBLIGATION_ETF,     # Xtrackers II Japan Government Bond
}

DEVISES_COTATION: dict[str, str] = {
    "IGLN.L": "USD",
    "BTCUSDT": "USD",
    "BTC-USD": "USD",
    "XDW0.L": "USD",
    "FLXC.L": "USD",
    "RI.PA": "EUR",
    "XJSE.SW": "JPY",
    "CHF": "CHF",
    "CNY": "CNY",
    "EUR": "EUR",
    "USD": "USD",
}


def classe_de(ticker: str) -> Classe:
    t = str(ticker).upper().strip()
    if t in CLASSES:
        return CLASSES[t]
    # Devises et espèces : pas de plus-value mobilière.
    if t in ("EUR", "USD", "CHF", "CNY", "GBP", "JPY", "CAD", "AUD"):
        return Classe.ESPECE
    # Défaut prudent : tout ticker inconnu est traité comme une action/ETF.
    log.warning("Ticker %s non classifié : traité comme action/ETF", t)
    return Classe.ACTION_ETF


def devise_cotation_de(ticker: str) -> str | None:
    """Devise de cotation connue, ou `None` si le ticker est inconnu.

    CORRECTION : cette fonction renvoyait `"USD"` par défaut. Tout titre
    européen absent de la table — ASML, MC.PA, SAP.DE... — se retrouvait donc
    réétiqueté en dollars, son cours en euro étant ensuite lu comme un cours
    en dollar. La valorisation de la ligne était fausse, et l'allocation avec.

    Une devise inconnue doit se voir, pas se deviner. Les appelants traitent
    le `None` : soit ils demandent à Yahoo (`prices.devise_de`), soit ils
    signalent la ligne.
    """
    return DEVISES_COTATION.get(str(ticker).upper().strip())


# ---------------------------------------------------------------------------
# Noms de colonnes : la v1 et la v2 n'écrivent pas pareil
# ---------------------------------------------------------------------------
# La v1 stockait les transactions avec des intitulés français
# (`Ticker`, `Type`, `Quantité`, `Cours`...). La v2 utilise du snake_case
# (`ticker`, `sens`, `quantite`, `cours`...), conformément à
# `migrations/001_init.sql`. C'est le même contenu sous deux habillages.
#
# `charger_transactions()` accepte les deux, parce qu'il est appelé sur deux
# sources différentes : un CSV exporté de la v1, et la table `pf2_transactions`.
# Ne reconnaître qu'une écriture, c'est casser l'autre en silence.
ALIAS_COLONNES = {
    "ticker":      "Ticker",
    "sens":        "Type",
    "date":        "Date",
    "quantite":    "Quantité",
    "cours":       "Cours",
    "frais":       "Frais",
    "devise":      "Devise",
    "source":      "Source",
    "reference":   "Référence",
    "montant_net": "Montant net",
}


def _normaliser_colonnes(df: pd.DataFrame) -> pd.DataFrame:
    """Renomme les colonnes snake_case de la v2 vers les intitulés de la v1.

    Idempotent : un DataFrame déjà au format v1 repart inchangé. Les colonnes
    inconnues sont conservées telles quelles — on ne jette rien.
    """
    if df is None or df.empty:
        return df
    renommage = {
        v2: v1
        for v2, v1 in ALIAS_COLONNES.items()
        if v2 in df.columns and v1 not in df.columns
    }
    return df.rename(columns=renommage) if renommage else df


# ---------------------------------------------------------------------------
# Lecture des transactions
# ---------------------------------------------------------------------------

@dataclass
class Transaction:
    ticker: str
    type: str                  # "achat" ou "vente"
    date: dt.date
    quantite: float
    cours: float               # dans la devise de cotation
    frais: float
    devise: str                # devise de cotation du titre
    montant_net: float         # dans la devise de cotation, frais inclus

    @property
    def est_achat(self) -> bool:
        return "achat" in self.type.lower()

    @property
    def est_vente(self) -> bool:
        return "vente" in self.type.lower()


def charger_transactions(df: pd.DataFrame) -> list[Transaction]:
    """Convertit un DataFrame Supabase en objets Transaction.

    Lève `ValueError` sur une ligne illisible plutôt que de l'ignorer.
    """
    if df is None or df.empty:
        return []

    df = _normaliser_colonnes(df)

    requises = {"Ticker", "Type", "Date", "Quantité", "Cours", "Frais", "Devise"}
    manquantes = requises - set(df.columns)
    if manquantes:
        raise ValueError(f"Colonnes manquantes dans Transaction : {sorted(manquantes)}")

    sortie: list[Transaction] = []
    for i, row in df.iterrows():
        try:
            # `format="mixed"` (dans `dates.parser`) : la v1 écrit des dates
            # jj/mm/aaaa, la v2 écrit de l'ISO aaaa-mm-jj. Sans ce paramètre,
            # pandas émet un UserWarning à chaque ligne ISO — du bruit inutile
            # dans les logs des robots nocturnes.
            d = dates.parser(row["Date"])
            if pd.isna(d):
                raise ValueError(f"date illisible : {row['Date']!r}")
            ticker = str(row["Ticker"]).upper().strip()
            typ = str(row["Type"]).strip().lower()
            if not ticker:
                raise ValueError("ticker vide")
            if "achat" not in typ and "vente" not in typ:
                raise ValueError(f"type inconnu : {row['Type']!r}")

            quantite = float(row["Quantité"])
            cours = float(row["Cours"])
            frais = float(row["Frais"]) if pd.notna(row["Frais"]) else 0.0
            if quantite <= 0 or cours <= 0:
                raise ValueError(f"quantité ou cours non positif ({quantite}, {cours})")

            devise = str(row.get("Devise", "") or "").upper().strip()
            if not devise:
                devise = devise_cotation_de(ticker)
                if not devise:
                    # La colonne Devise est vide et le ticker est inconnu de la
                    # table de cotation. On refuse la ligne plutôt que de
                    # deviner : une transaction sans devise ne peut pas être
                    # valorisée, et une devise fausse la valorise de travers.
                    raise ValueError(
                        f"devise absente pour {ticker} et ticker inconnu de "
                        f"DEVISES_COTATION — renseignez la colonne Devise"
                    )

            net = quantite * cours
            net = net + frais if "achat" in typ else net - frais

            sortie.append(Transaction(
                ticker=ticker, type=typ, date=d.date(), quantite=quantite,
                cours=cours, frais=frais, devise=devise, montant_net=round(net, 6),
            ))
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"Transaction ligne {i} illisible : {exc}") from exc

    # Achats AVANT ventes à date égale.
    #
    # Le tri précédent était `(date, ticker)`. Pour un achat et une vente du
    # même titre le même jour, la clé était donc identique, et le tri stable de
    # Python conservait l'ordre de la table d'origine. Si la vente était rangée
    # avant son achat dans la v1 — ce qui arrive, rien ne l'interdit — le
    # calcul des positions échouait avec :
    #
    #     Vente de 22.0 FLXC.L le 2025-01-07 sans position détenue.
    #
    # La donnée était pourtant correcte : elle était simplement dans le
    # désordre. Traiter les achats d'abord est la convention usuelle, et c'est
    # la seule qui rende l'arithmétique du PRU possible.
    return sorted(sortie, key=lambda t: (t.date, 0 if t.est_achat else 1, t.ticker))


# ---------------------------------------------------------------------------
# Calcul des positions
# ---------------------------------------------------------------------------

@dataclass
class Position:
    """Une position par ticker, avec son PRU et sa valorisation."""

    ticker: str
    classe: Classe
    poche: str
    devise_cotation: str
    quantite: float = 0.0
    cout_total_eur: float = 0.0     # coût total en euros (fiscalité française)
    pru_eur: float = 0.0
    cout_total_usd: float = 0.0     # coût total en dollars (unité de compte)
    pru_usd: float = 0.0
    prix: float = 0.0               # dans la devise de cotation
    valeur_eur: float = 0.0
    pv_latente_eur: float = 0.0
    valeur_usd: float = 0.0
    pv_latente_usd: float = 0.0

    @property
    def perf_globale(self) -> float | None:
        if self.pru_eur <= 0:
            return None
        return (self.valeur_eur / (self.pru_eur * self.quantite)) - 1.0 if self.quantite else None

    @property
    def perf_globale_usd(self) -> float | None:
        if self.pru_usd <= 0:
            return self.perf_globale
        return (self.valeur_usd / (self.pru_usd * self.quantite)) - 1.0 if self.quantite else None

    @property
    def poche_obj(self) -> Poche | None:
        return POCHES_PAR_CLE.get(self.poche)


def calculer_positions(
    transactions: list[Transaction],
    anomalies: list[str] | None = None,
) -> dict[str, Position]:
    """PRU et quantités par ticker, en euros, FIFO sur la quantité.

    Le PRU est calculé en euros : chaque ligne est convertie à sa date avec le
    taux de change réel. Aucune conversion manuelle n'est acceptée en saisie —
    c'est ce qui avait empoisonné XJSE.SW dans la v1.

    `anomalies` : si vous passez une liste, une transaction incohérente (vente
    sans position détenue, ou vente supérieure au détenu) y est consignée et la
    ligne est ignorée, au lieu de faire échouer tout le calcul. L'application
    reste utilisable et vous dit ce qui ne va pas. Sans liste, on lève
    `ValueError` — comportement des robots, où mieux vaut s'arrêter.
    """
    positions: dict[str, Position] = {}

    for t in transactions:
        pos = positions.get(t.ticker)
        if pos is None:
            pos = Position(
                ticker=t.ticker,
                classe=classe_de(t.ticker),
                poche=(poche_de(t.ticker).cle if poche_de(t.ticker) else "inconnu"),
                devise_cotation=t.devise,
            )
            positions[t.ticker] = pos

        montant_eur = t.montant_net * fx.taux(t.devise, t.date.isoformat(), "EUR")
        if t.devise == "USD":
            montant_usd = t.montant_net
        else:
            try:
                montant_usd = t.montant_net * fx.taux(t.devise, t.date.isoformat(), "USD")
            except Exception:
                montant_usd = montant_eur

        if t.est_achat:
            pos.quantite += t.quantite
            pos.cout_total_eur += montant_eur
            pos.cout_total_usd += montant_usd
        else:  # vente
            if pos.quantite <= 1e-9:
                message = (
                    f"Vente de {t.quantite} {t.ticker} le {t.date} sans position "
                    f"détenue. Vérifiez l'achat correspondant dans la v1."
                )
                if anomalies is None:
                    raise ValueError(message)
                anomalies.append(message)
                continue
            if t.quantite > pos.quantite + 1e-6:
                message = (
                    f"Vente de {t.quantite} {t.ticker} le {t.date} supérieure à la "
                    f"quantité détenue ({pos.quantite})."
                )
                if anomalies is None:
                    raise ValueError(message)
                anomalies.append(message)
                continue
            pru_instant = pos.cout_total_eur / pos.quantite
            pru_instant_usd = pos.cout_total_usd / pos.quantite
            pos.cout_total_eur -= pru_instant * t.quantite
            pos.cout_total_usd -= pru_instant_usd * t.quantite
            pos.quantite -= t.quantite
            if pos.quantite <= 1e-9:
                pos.quantite = 0.0
                pos.cout_total_eur = 0.0
                pos.cout_total_usd = 0.0

    for pos in positions.values():
        pos.pru_eur = (pos.cout_total_eur / pos.quantite) if pos.quantite > 0 else 0.0
        pos.pru_usd = (pos.cout_total_usd / pos.quantite) if pos.quantite > 0 else 0.0

    return positions


def valoriser(positions: dict[str, Position], date: str | None = None) -> tuple[list[Actif], list[str]]:
    """Valorise les positions. Retourne `(actifs, echecs)`.

    Les échecs de cours sont renvoyés, pas masqués : l'appelant doit les afficher.
    """
    actifs: list[Actif] = []
    echecs: list[str] = []

    for pos in positions.values():
        if pos.quantite <= 0:
            continue
        try:
            jour = date or dt.date.today().isoformat()
            prix = prices.cours(pos.ticker, date)
            taux = fx.taux(pos.devise_cotation, jour, "EUR")
            if pos.devise_cotation == "USD":
                taux_usd = 1.0
            else:
                try:
                    taux_usd = fx.taux(pos.devise_cotation, jour, "USD")
                except Exception:
                    taux_usd = taux
            valeur_eur = pos.quantite * prix * taux
            valeur_usd = pos.quantite * prix * taux_usd
            pos.prix = prix
            pos.valeur_eur = valeur_eur
            pos.pv_latente_eur = valeur_eur - pos.cout_total_eur
            pos.valeur_usd = valeur_usd
            pos.pv_latente_usd = valeur_usd - pos.cout_total_usd
            actifs.append(Actif(
                ticker=pos.ticker, classe=pos.classe, devise_cotation=pos.devise_cotation,
                poche=pos.poche, quantite=pos.quantite, prix=prix,
                valeur_eur=valeur_eur, valeur_usd=valeur_usd,
                dernier_taux=taux, dernier_taux_usd=taux_usd,
            ))
        except (prices.CoursIndisponible, fx.FXIndisponible) as exc:
            log.warning("Valorisation impossible : %s", exc)
            echecs.append(pos.ticker)

    return actifs, echecs


# ---------------------------------------------------------------------------
# Agrégation par poche
# ---------------------------------------------------------------------------

@dataclass
class EtatPoche:
    """État d'une poche : valeur, poids, écart à la cible."""

    poche: Poche
    valeur_eur: float = 0.0
    valeur_usd: float = 0.0
    poids_reel: float = 0.0
    poids_cible: float = 0.0
    actifs: list[Actif] = field(default_factory=list)

    @property
    def ecart(self) -> float:
        """Écart en points de pourcentage (réel - cible)."""
        return self.poids_reel - self.poids_cible

    @property
    def ecart_points(self) -> float:
        """Écart en points de pourcentage, prêt à afficher."""
        return (self.poids_reel - self.poids_cible) * 100.0

    @property
    def hors_bande(self) -> bool:
        return abs(self.poids_reel - self.poids_cible) > self.poche.bande

    @property
    def en_dehors_bande(self) -> bool:
        return self.hors_bande

    @property
    def valeur_cible_eur(self) -> float:
        return self.poids_cible * (self.valeur_eur / self.poids_reel) if self.poids_reel else 0.0


def agreger_par_poche(
    actifs: list[Actif],
    total_investi_eur: float,
) -> dict[str, EtatPoche]:
    """Répartit les actifs par poche et calcule les poids.

    `total_investi_eur` est le dénominateur : **uniquement** le patrimoine investi.
    L'épargne de précaution et le compte courant n'y entrent pas — leur pondération
    n'a aucun sens. C'est exactement l'erreur de la v1, en sens inverse.
    """
    etats: dict[str, EtatPoche] = {}
    for p in POCHES_PAR_CLE.values():
        etats[p.cle] = EtatPoche(poche=p, poids_cible=p.cible)

    for a in actifs:
        etat = etats.get(a.poche)
        if etat is None:
            etat = etats.setdefault("inconnu", EtatPoche(poche=Poche(
                cle="inconnu", nom="Non classé", cible=0.0, bande=0.0,
                perimetre=Perimetre.INVESTI,
            )))
        etat.actifs.append(a)
        etat.valeur_eur += a.valeur_eur
        etat.valeur_usd += getattr(a, "valeur_usd", a.valeur_eur)

    if total_investi_eur > 0:
        for etat in etats.values():
            # Une poche hors portefeuille n'a pas de poids d'allocation : lui en
            # attribuer un serait revenir au bug de la v1, où l'épargne de
            # précaution entrait dans l'assiette de rééquilibrage.
            if etat.poche.perimetre == Perimetre.INVESTI:
                etat.poids_reel = etat.valeur_eur / total_investi_eur
            else:
                etat.poids_reel = 0.0

    return etats


def patrimoine_total(actifs: list[Actif]) -> dict[str, float]:
    """Ventilation du patrimoine : investi, précaution, courant."""
    totaux = {p.value: 0.0 for p in Perimetre}
    for a in actifs:
        p = POCHES_PAR_CLE.get(a.poche)
        cle = p.perimetre.value if p else Perimetre.INVESTI.value
        totaux[cle] += a.valeur_eur
    return totaux
