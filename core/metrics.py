"""Indicateurs de performance.

CORRECTIONS PAR RAPPORT À LA V1
-------------------------------
1. **Le TWR était un simple cumprod d'un ratio Dietz mensuel.** Acceptable en
   ordre de grandeur, mais il suppose que les apports arrivent en fin de période.
   On chaîne désormais les rendements de sous-période, ce qui est la définition
   standard du TWR.

2. **Aucune performance n'était mesurée en or.** Or Gave tient l'or pour l'étalon
   de valeur, pas pour un placement : « l'or montera tant que les monnaies ne
   redeviendront pas des réserves de valeur ». La v1 collectait pourtant une
   colonne `Montant Or` à chaque apport… et ne s'en servait jamais.

3. **Aucun rendement pondéré par les flux (IRR).** Le TWR répond à « qu'a fait la
   stratégie », l'IRR répond à « qu'ai-je gagné, moi, avec mon calendrier
   d'apports ». Les deux sont nécessaires et ne disent pas la même chose.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import numpy as np


# ---------------------------------------------------------------------------
# Rendements de sous-période et TWR
# ---------------------------------------------------------------------------

def rendements_periode(
    valeurs: list[float],
    flux: list[float] | None = None,
) -> list[float]:
    """Rendement de chaque sous-période, corrigé des flux externes.

    `valeurs[i]` = valeur du portefeuille à la fin de la période i.
    `flux[i]`    = flux externe sur la période i (apport positif, retrait négatif),
                   supposé survenir à la fin de la période.

    r_i = (V_i - V_{i-1} - F_i) / V_{i-1}
    """
    n = len(valeurs)
    if n < 2:
        return []
    flux = flux or [0.0] * n
    if len(flux) != n:
        raise ValueError("valeurs et flux doivent avoir la même longueur")

    sortie = []
    for i in range(1, n):
        v_prec = valeurs[i - 1]
        if v_prec <= 0:
            # Portefeuille vide ou négatif : pas de rendement calculable.
            sortie.append(0.0)
            continue
        r = (valeurs[i] - v_prec - flux[i]) / v_prec
        sortie.append(r)
    return sortie


def flux_par_periode(
    dates: list,
    flux_jour: dict,
    defaut: float = 0.0,
) -> list[float]:
    """Range chaque flux dans la période qui se termine à la date du snapshot.

    `dates`     : les dates des snapshots, TRIÉES, en `datetime.date`.
    `flux_jour` : `{date: montant signé}`, apport positif — exactement ce que
                  rend `session.flux_par_date`.
    `defaut`    : la valeur du premier élément, qui n'appartient à aucune période.

    POURQUOI PAS UNE CORRESPONDANCE EXACTE PAR DATE
    -----------------------------------------------
    `rendements_periode` calcule `r_i = (V_i - V_{i-1} - F_i) / V_{i-1}` : `F_i`
    est le flux de la période qui se TERMINE en i. Écrire
    `flux_jour.get(dates[i])` ne trouve donc que les flux tombés exactement un
    jour de snapshot.

    Tant que le robot écrivait un snapshot par nuit, les deux coïncidaient —
    sauf la nuit où le robot échouait. L'apport de ce jour-là disparaissait alors
    sans un mot, et gonflait le TWR du montant du versement. Le défaut existait
    déjà ; il était rare.

    Depuis que l'historique de la v1 alimente la série — mensuel jusqu'en avril
    2026, quotidien ensuite — le décalage devient la règle. Un apport du 13/03
    tombe entre deux snapshots et n'est vu par personne. On somme donc tous les
    flux de l'intervalle `(dates[i-1], dates[i]]`.

    Sur des données quotidiennes complètes, le résultat est identique à l'ancien
    calcul. Sur des données trouées, il est juste là où l'ancien se taisait.
    """
    n = len(dates)
    if n == 0:
        return []

    sortie = [float(defaut)] * n
    entrees = sorted(
        (d, float(m)) for d, m in (flux_jour or {}).items()
        if m is not None and float(m) != 0.0
    )
    if not entrees:
        return sortie

    # Le flux du premier jour est déjà dans la valeur de départ : il n'appartient
    # à aucune période et n'est donc rangé nulle part. On le garde en tête de
    # liste pour que la longueur reste celle des snapshots, sans l'utiliser.
    premier = float(flux_jour.get(dates[0], defaut))
    sortie[0] = premier

    k = 0
    for i in range(1, n):
        deb, fin = dates[i - 1], dates[i]
        # Tout ce qui est antérieur ou égal au début de la période a été consommé
        # par la période précédente. Le pointeur ne recule pas.
        while k < len(entrees) and not (entrees[k][0] > deb):
            k += 1
        j = k
        total = 0.0
        while j < len(entrees) and not (entrees[j][0] > fin):
            total += entrees[j][1]
            j += 1
        sortie[i] = total
        k = j
    return sortie


def twr(rendements: list[float]) -> float:
    """Time-Weighted Return : chaînage géométrique des rendements de sous-période."""
    if not rendements:
        return 0.0
    prod = 1.0
    for r in rendements:
        prod *= (1.0 + r)
    return prod - 1.0


def twr_depuis(valeurs: list[float], flux: list[float] | None = None) -> float:
    """TWR cumulé sur toute la série."""
    return twr(rendements_periode(valeurs, flux))


def annualiser(twr_total: float, jours: int) -> float:
    """Annualise un TWR cumulé. `jours` = nombre de jours de la période."""
    if jours <= 0:
        raise ValueError("La durée doit être positive")
    annees = jours / 365.25
    if annees <= 0:
        return 0.0
    return (1.0 + twr_total) ** (1.0 / annees) - 1.0


# ---------------------------------------------------------------------------
# Rendement réel et rendement en or
# ---------------------------------------------------------------------------

def rendement_reel(nominal: float, inflation: float) -> float:
    """Rendement réel par la relation de Fisher.

    (1 + réel) = (1 + nominal) / (1 + inflation)
    """
    return (1.0 + nominal) / (1.0 + inflation) - 1.0


def pouvoir_achat(montant_futur: float, inflation: float, annees: int) -> float:
    """Valeur aujourd'hui d'un montant futur, déflaté."""
    if annees < 0:
        raise ValueError("Le nombre d'années ne peut pas être négatif")
    return montant_futur / ((1.0 + inflation) ** annees)


def rendement_en_or(
    valeur_debut: float,
    valeur_fin: float,
    or_debut: float,
    or_fin: float,
) -> float:
    """Performance exprimée en onces d'or — l'étalon de Gave.

    On ne demande pas « combien d'euros ai-je gagnés » mais « combien d'onces
    d'or puis-je acheter en plus qu'au début ».

    Lève `ValueError` si un cours de l'or est nul ou négatif : mieux vaut une
    erreur qu'un rendement en or calculé sur un prix inventé.
    """
    if or_debut <= 0 or or_fin <= 0:
        raise ValueError(
            "Cours de l'or invalide. Le rendement en or exige un prix réel, "
            "pas une valeur de repli."
        )
    if valeur_debut < 0:
        raise ValueError("La valeur de départ ne peut pas être négative")
    onces_debut = valeur_debut / or_debut
    onces_fin = valeur_fin / or_fin
    if onces_debut <= 0:
        return 0.0
    return onces_fin / onces_debut - 1.0


# ---------------------------------------------------------------------------
# Rendement pondéré par les flux (IRR / MWR)
# ---------------------------------------------------------------------------

def irr(flux: list[tuple[dt.date, float]]) -> float | None:
    """Taux de rendement interne d'une série de flux datés.

    `flux` = liste de (date, montant), montant négatif pour un apport
    (sortie de poche) et positif pour un retrait (rentrée).

    Retourne le taux annualisé, ou None s'il n'y a pas de solution.
    """
    if len(flux) < 2:
        return None

    flux = sorted(flux, key=lambda x: x[0])
    t0 = flux[0][0]

    def van(taux: float) -> float:
        total = 0.0
        for date, montant in flux:
            annees = (date - t0).days / 365.25
            total += montant / ((1.0 + taux) ** annees)
        return total

    # Recherche par bissection sur une plage large mais réaliste.
    bas, haut = -0.95, 10.0
    try:
        f_bas, f_haut = van(bas), van(haut)
    except (OverflowError, ZeroDivisionError):
        return None

    if f_bas * f_haut > 0:
        return None  # pas de changement de signe : pas de solution unique

    for _ in range(200):
        milieu = (bas + haut) / 2.0
        try:
            f_milieu = van(milieu)
        except (OverflowError, ZeroDivisionError):
            return None
        if abs(f_milieu) < 1e-9:
            return milieu
        if f_bas * f_milieu <= 0:
            haut, f_haut = milieu, f_milieu
        else:
            bas, f_bas = milieu, f_milieu
    return (bas + haut) / 2.0


# ---------------------------------------------------------------------------
# Risque
# ---------------------------------------------------------------------------

def volatilite(rendements: list[float], periodicite: int = 252) -> float:
    """Volatilité annualisée à partir de rendements périodiques."""
    if len(rendements) < 2:
        return 0.0
    return float(np.std(rendements, ddof=1) * np.sqrt(periodicite))


def sharpe(rendements: list[float], taux_sans_risque: float = 0.0,
           periodicite: int = 252) -> float | None:
    """Ratio de Sharpe annualisé.

    Sans objet sur un portefeuille permanent : sa raison d'être est la corrélation
    négative entre poches, pas un couple rendement/risque favorable. On le
    calcule quand même, mais il ne doit jamais être le critère de décision.
    """
    vol = volatilite(rendements, periodicite)
    if vol == 0:
        return None
    moyenne = float(np.mean(rendements))
    rf_periode = (1.0 + taux_sans_risque) ** (1.0 / periodicite) - 1.0
    return (moyenne - rf_periode) / vol * np.sqrt(periodicite)


def correlation(a: list[float], b: list[float]) -> float | None:
    """Corrélation entre deux séries de rendements de même longueur."""
    if len(a) != len(b) or len(a) < 3:
        return None
    sa, sb = float(np.std(a)), float(np.std(b))
    if sa == 0 or sb == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


# ---------------------------------------------------------------------------
# Synthèse
# ---------------------------------------------------------------------------

@dataclass
class Bilan:
    """Synthèse d'une période de performance."""

    twr_cumule: float
    twr_annualise: float | None
    rendement_reel: float | None
    rendement_en_or: float | None
    irr: float | None
    volatilite: float
    jours: int

    @property
    def annees(self) -> float:
        return self.jours / 365.25


# ---------------------------------------------------------------------------
# Deux calculs qui vivaient dans la page et n'étaient testables nulle part
# ---------------------------------------------------------------------------
# Les deux défauts ci-dessous ont vécu des mois dans `pages/5_Performance.py`
# sans qu'aucun test ne les voie, parce qu'ils étaient enfouis dans du code
# Streamlit. On les remonte ici, où ils sont mesurables.

def inflation_cumulee(
    inflation: dict[int, float],
    d0: dt.date,
    d1: dt.date,
) -> float:
    """Facteur d'inflation cumulé sur `[d0, d1]`, pondéré par le TEMPS.

    `inflation[annee]` est un taux annuel **déjà en fraction** (0,049 pour 4,9 %).

    Le défaut que ceci remplace : le facteur était calculé en élevant chaque
    année à la puissance « nombre de snapshots dans l'année / 12 ». Exact pour
    des données mensuelles ; absurde dès que le robot quotidien tourne. Une
    année de 250 lignes donnait une inflation à la puissance 20 au lieu de 1 —
    sur trois ans, +70 % au lieu de +8 %.

    On pondère donc par la fraction de jours réellement passée dans chaque
    année. Une année sans donnée est **sautée**, pas remplacée par 0 % : la
    performance réelle sera alors incomplète, et l'appelant doit le dire.
    """
    if d1 <= d0:
        return 1.0
    facteur = 1.0
    for annee in sorted(set([d0.year, d1.year]) | set(inflation)):
        if annee not in inflation:
            continue
        debut = max(d0, dt.date(annee, 1, 1))
        fin = min(d1, dt.date(annee + 1, 1, 1))
        jours = (fin - debut).days
        if jours <= 0:
            continue
        # L'exposant est le nombre d'ANNEES passees dans `annee` (1 pour une
        # annee pleine, 0,5 pour une demi-annee), pas la fraction de la periode
        # totale. Confondre les deux donnerait la moyenne geometrique des taux
        # au lieu du facteur cumule : sur trois ans a 2 %, 1,02 au lieu de
        # 1,061 — et la performance reelle serait sur-estimee de 6 points.
        facteur *= (1.0 + inflation[annee]) ** (jours / 365.25)
    return facteur


def twr_par_annee(
    dates: list[dt.date],
    rendements: list[float],
) -> dict[int, float]:
    """TWR par année civile, en chaînant les rendements de sous-période.

    `dates[i]` est la date de **fin** de la sous-période dont le rendement est
    `rendements[i]`. Le rendement d'une année est le produit des sous-périodes
    qui se terminent dans cette année.

    Le défaut que ceci remplace : `dernière_valeur / première_valeur - 1`. Ce
    calcul compte les apports comme du rendement. Sur un portefeuille alimenté
    chaque mois, une stratégie à 9 % par an s'affichait à +66 %, +42 %, +32 %.
    """
    if len(dates) != len(rendements):
        raise ValueError("dates et rendements doivent avoir la même longueur")
    par_annee: dict[int, float] = {}
    for d, r in zip(dates, rendements):
        par_annee.setdefault(d.year, []).append(r)
    return {a: twr(rs) for a, rs in sorted(par_annee.items())}


# ---------------------------------------------------------------------------
# Performance en or, corrigée des flux
# ---------------------------------------------------------------------------
# L'étalon de Gave est l'or, pas l'euro. Mais « onces finales / onces initiales »
# a le même défaut que « valeur finale / valeur initiale » : si vous versez de
# l'argent, le rapport monte sans que la stratégie ait rien produit. C'était
# affiché sur la page d'accueil.
#
# La forme close, pour une sous-période :
#
#     (1 + r_or) = (1 + r_eur) / (1 + g_eur)
#
# où `g_eur` est le rendement de l'or EN EUROS. L'équivalent-or du portefeuille
# vaut `oz = V_eur / gold_eur` (diviser la valeur par le prix de l'or en euros),
# donc `g_eur = (V_i / oz_i) / (V_{i−1} / oz_{i−1}) − 1`. En substituant :
#
#     (1 + r_or) = (1 + r_eur) × (V_{i−1} / V_i) × (oz_i / oz_{i−1})
#
# PIÈGE À NE PAS REFAIRE : `r_eur` est le RENDEMENT, pas `1 + r_eur`. Écrire
# `(V_i − V_{i−1} − F_i) / V_i × oz_i / oz_{i−1}` — ce que j'avais fait — perd
# le `+1` et donne un rendement en or nul dès que l'or est stable. La
# vérification ci-dessous existe pour ça.


def rendements_en_or(
    valeurs: list[float],
    flux: list[float] | None,
    onces: list[float],
) -> list[float]:
    """Rendement de chaque sous-période, exprimé en onces d'or.

    Lève `ValueError` si une valeur d'or manque ou n'est pas positive : mieux
    vaut pas de mesure qu'une mesure inventée. Les snapshots importés de la v1
    n'ont pas d'équivalent-or, et il faut le dire plutôt que deviner.
    """
    n = len(valeurs)
    if len(onces) != n:
        raise ValueError("valeurs et onces doivent avoir la même longueur")
    if n < 2:
        return []
    flux = flux or [0.0] * n
    for o in onces:
        if o is None or o <= 0:
            raise ValueError(
                "Équivalent-or manquant ou nul. La performance en or exige un "
                "prix réel du métal à chaque date, pas une valeur de repli."
            )

    sortie = []
    for i in range(1, n):
        if valeurs[i] <= 0 or valeurs[i - 1] <= 0:
            sortie.append(0.0)
            continue
        r_eur = (valeurs[i] - valeurs[i - 1] - flux[i]) / valeurs[i - 1]
        # `+ 1.0` : c'est (1 + r_eur), pas r_eur. Sans lui, un or stable donne
        # un rendement en or nul.
        un_plus_r_or = (1.0 + r_eur) * (valeurs[i - 1] / valeurs[i]) * (onces[i] / onces[i - 1])
        sortie.append(un_plus_r_or - 1.0)
    return sortie


def twr_en_or(
    valeurs: list[float],
    flux: list[float] | None,
    onces: list[float],
) -> float:
    """Performance cumulée en onces d'or, corrigée des apports."""
    return twr(rendements_en_or(valeurs, flux, onces))

# ===========================================================================
# Garde-fou : un TWR n'est juste que si les flux sont enregistrés
# ===========================================================================

# Au-delà de ce taux sur la période, une absence totale d'apports enregistrés
# n'est plus vraisemblable : elle signale une saisie manquante, pas une
# performance.
SEUIL_SUSPICION_SANS_FLUX = 0.15

# En dessous, la période est trop courte pour que le contrôle ait un sens :
# une semaine de hausse ne prouve rien.
JOURS_MINIMUM_POUR_CONTROLE = 45


def controle_apports(
    valeurs: list[float],
    flux: list[float],
    jours: int,
) -> list[str]:
    """Signale les périodes où le TWR ne peut pas être juste.

    POURQUOI CE CONTRÔLE EXISTE
    ---------------------------
    Un TWR neutralise les versements — à condition de les connaître. Quand
    aucun apport n'est enregistré sur la période, le calcul se réduit à
    « valeur finale / valeur initiale », et votre épargne apparaît comme du
    rendement.

    C'est exactement le symptôme rapporté : +26,2 % affichés pour 2026 là où
    Swissquote donne +4,10 % en TWR. Un écart de 22 points ne vient pas d'un
    arrondi : il vient de versements comptés comme du gain.

    L'application ne peut pas savoir qu'un versement a été oublié — elle n'a
    aucun moyen de le deviner. Mais elle peut repérer la situation où c'est le
    plus probable, et le DIRE au lieu d'afficher un chiffre confiant.
    """
    alertes: list[str] = []
    if len(valeurs) < 2 or len(flux) != len(valeurs):
        return alertes

    total_flux = sum(flux)
    if total_flux != 0:
        return alertes                       # des flux sont enregistrés : rien à dire

    if jours < JOURS_MINIMUM_POUR_CONTROLE:
        return alertes

    depart, arrivee = float(valeurs[0]), float(valeurs[-1])
    if depart <= 0:
        return alertes

    variation = arrivee / depart - 1.0
    if variation > SEUIL_SUSPICION_SANS_FLUX:
        alertes.append(
            f"Aucun apport ni retrait n'est enregistré sur ces {jours} jours, "
            f"alors que la valeur passe de {depart:,.0f} € à {arrivee:,.0f} € "
            f"({variation:+.1%}). Si vous avez versé de l'argent sur la période, "
            "il n'est PAS dans la base : la performance ci-dessus compte donc "
            "vos versements comme du rendement. Lancez le diagnostic (onglet "
            "Actions)."
        )
    return alertes

# ===========================================================================
# Sauts non expliqués : le défaut qui a produit +26,2 % au lieu de +4,10 %
# ===========================================================================

# Un portefeuille diversifié de quatre ETF ne bouge pas de 8 % en un jour. Le
# krach tarifaire d'avril 2025 a fait -7,4 % sur celui-ci : c'est le plus gros
# mouvement de marché réel de son histoire. Au-delà de 8 %, la cause n'est plus
# le marché.
SEUIL_SAUT_INEXPLIQUE = 0.08

# Un flux enregistré que la valeur n'a pas suivi : c'est l'autre moitié du
# problème, et elle ne se voit pas de la même façon.
MONTANT_MIN_FLUX_SANS_EFFET = 1000.0
PART_DU_FLUX_INVISIBLE = 0.8
SEUIL_FLUX_SANS_EFFET = 0.15

# Un saut de 8 % sur 1 000 EUR ne vaut pas la peine d'alarmer. Sur 9 000 EUR,
# si.
MONTANT_MIN_SAUT = 500.0


def sauts_non_expliques(
    dates: list,
    valeurs: list[float],
    flux: list[float],
    seuil: float = SEUIL_SAUT_INEXPLIQUE,
    montant_min: float = MONTANT_MIN_SAUT,
) -> list[dict]:
    """Jours où la valeur saute sans qu'aucun flux ne l'explique.

    POURQUOI CETTE FONCTION EXISTE
    ------------------------------
    Le 02/02/2026, sur le portefeuille réel :

        01/02 : 55 639,93 EUR
        02/02 : 64 808,82 EUR     apport enregistré ce jour : 200,00 EUR

    Saut brut : +9 168,89 EUR. L'apport de 200 EUR en explique une part : ce
    qui reste sans flux est **8 968,89 EUR** (+16,12 % en une séance). Le TWR,
    qui ne connaît que les flux
    enregistrés, l'a compté comme du rendement : +16,5 % en une journée sur un
    portefeuille de quatre ETF.

    Résultat affiché pour 2026 : **+26,2 %**. Chez Swissquote : **+4,10 %**.

    Le contrôle existant (`controle_apports`) ne pouvait pas l'attraper : il ne
    se déclenche que lorsqu'AUCUN flux n'est enregistré sur la période. Ici il y
    en a 38 — sur d'autres jours. Le trou est localisé, pas global.

    CE QUE CES SAUTS PEUVENT ÊTRE
    -----------------------------
    Aucun n'est de la performance. Ce sont, par ordre de fréquence :

    - un virement interne (du livret CHF vers le portefeuille investi) : la
      valeur investie monte sans qu'un apport extérieur n'existe ;
    - une position ajoutée à la main dans l'application, sans transaction ;
    - un versement réel non saisi dans le journal des apports ;
    - une erreur de cours sur un actif (un titre coté en yens lu en dollars).

    La fonction ne tranche pas : elle nomme le jour et le montant, pour que la
    question se règle en trente secondes en regardant la date.
    """
    sauts: list[dict] = []
    if not dates or len(valeurs) < 2 or len(valeurs) != len(flux) or len(dates) != len(valeurs):
        return sauts

    for i in range(1, len(valeurs)):
        avant = float(valeurs[i - 1])
        apres = float(valeurs[i])
        if avant <= 0 or not (avant == avant) or not (apres == apres):
            continue

        flux_du_jour = float(flux[i])
        # Ce que la valeur aurait dû devenir si seul le flux avait joué.
        explique = avant + flux_du_jour
        if explique <= 0:
            continue
        residuel = apres - explique
        if abs(residuel) < montant_min:
            continue

        # LE SEUIL DÉPEND DU TEMPS ÉCOULÉ.
        #
        # 8 % en une séance sur quatre ETF n'existe pas : c'est un défaut de
        # données. 8 % en un mois, c'est une bonne année, et ce n'est pas un
        # défaut. Une fois l'historique de la v1 importé, la série est mensuelle
        # jusqu'en avril 2026 — un seuil fixe signalerait alors chaque bon mois
        # comme une anomalie, et une alerte qui crie au loup ne vaut pas mieux
        # que pas d'alerte du tout.
        #
        # L'échelle est celle du mouvement brownien : l'écart-type d'un
        # rendement croît comme la racine du temps. 8 % par jour deviennent
        # 8 % × √30 ≈ 44 % sur un mois, ce qui reste largement au-delà de ce
        # qu'un portefeuille diversifié peut produire sans flux.
        jours = 1
        try:
            jours = max(int((dates[i] - dates[i - 1]).days), 1)
        except (TypeError, AttributeError):
            jours = 1
        seuil_effectif = seuil * math.sqrt(jours)

        variation = residuel / avant
        if abs(variation) < seuil_effectif:
            continue

        sauts.append({
            "i": i,                      # rang du jour fautif, pour recalculer
            "date": dates[i],
            "avant": avant,
            "apres": apres,
            "flux": flux_du_jour,
            "residuel": residuel,
            "residuel_pct": variation,
            "jours": jours,              # écart avec le point précédent
            "seuil": seuil_effectif,     # seuil appliqué, après mise à l'échelle
        })
    return sauts


def fluxs_sans_effet(
    dates: list,
    valeurs: list[float],
    flux: list[float],
    montant_min: float = MONTANT_MIN_FLUX_SANS_EFFET,
    part_invisible: float = PART_DU_FLUX_INVISIBLE,
    seuil: float = SEUIL_FLUX_SANS_EFFET,
) -> list[dict]:
    """Flux enregistrés que la valeur n'a PAS suivis.

    LE MIROIR DE `sauts_non_expliques`
    ----------------------------------
    Cette fonction et l'autre décrivent les deux moitiés du même défaut :

        sauts_non_expliques : la valeur monte sans flux   -> versement manquant
        fluxs_sans_effet    : le flux existe mais la
                              valeur ne suit pas          -> versement fantôme

    Le cas réel, trouvé en préparant l'import de l'historique v1 : le
    29/04/2024, `Historique` enregistre un apport de 10 800 € — et entre le
    30/03 et le 30/04/2024, la valeur investie passe de 31 988 à 31 779 USD,
    soit +23 €. Elle n'a pas bougé de 11 027 €. Le même apport, de 10 800 €,
    enregistré le 23/07/2024, fait bien monter la valeur de 10 658 €.

    Un mois en −37 % au milieu de mois tous compris entre −7 % et +6 %, sur un
    portefeuille de quatre ETF : ce n'est pas un mauvais mois, c'est une ligne
    en trop dans le journal des apports. Sans ce contrôle, l'année 2024
    s'afficherait à −24,9 %.

    POURQUOI DEUX CONDITIONS ET NON UNE
    ------------------------------------
    Exiger seulement que la valeur n'ait pas suivi le flux produirait une fausse
    alerte à chaque krach. Avril 2025 en est l'exemple : 2 620 € d'apports et une
    valeur qui recule de 1 129 € — le trou de 3 749 € est le krach tarifaire, pas
    un défaut. Il faut donc aussi que l'écart soit d'une ampleur qu'aucun marché
    ne produit : 15 % du portefeuille, en plus du flux.

        29/04/2024 : 11 027 € d'écart sur 31 000 € = 35 %  -> défaut
        avril 2025 :  3 749 € d'écart sur 57 000 € =  6,6 % -> krach, silence
    """
    trouves: list[dict] = []
    if not dates or len(valeurs) < 2 or len(valeurs) != len(flux) or len(dates) != len(valeurs):
        return trouves

    for i in range(1, len(valeurs)):
        f = float(flux[i])
        avant = float(valeurs[i - 1])
        apres = float(valeurs[i])
        if avant <= 0 or abs(f) < montant_min:
            continue
        if not (avant == avant) or not (apres == apres):
            continue

        residuel = (apres - avant) - f
        if abs(residuel) < part_invisible * abs(f):
            continue
        if abs(residuel) / avant < seuil:
            continue

        trouves.append({
            "i": i,
            "date": dates[i],
            # La date du point précédent : c'est le DÉBUT de la période, et sans
            # elle le message ne peut pas dire où chercher. Le versement fautif
            # est daté du 29/04/2024, pas du 30/04 — la date du snapshot. Sans
            # les deux bornes, on cherche la mauvaise ligne.
            "date_avant": dates[i - 1],
            "avant": avant,
            "apres": apres,
            "flux": f,
            "residuel": residuel,
            "residuel_pct": residuel / avant,
        })
    return trouves


def anomalie_flux_sans_effet(flux_sans_effet: dict) -> str:
    """Le message à afficher pour un flux que la valeur n'a pas suivi.

    Le message donne les DEUX bornes de la période — et non la seule date du
    snapshot. Vérifié sur le cas réel : le versement litigieux est daté du
    29/04/2024 dans le journal, alors que le snapshot qui le révèle porte le
    30/04/2024. Un message qui n'aurait dit que « 2024-04-30 » aurait envoyé
    chercher une ligne qui n'existe pas.
    """
    # `metrics` ne dépend pas de `ui` — et ne doit pas : `ui` importe streamlit,
    # et le calcul doit rester utilisable dans un robot. Le formatage se fait donc
    # ici, en trois lignes, plutôt que par un import croisé.
    def _jour(v) -> str:
        return v.strftime("%d/%m/%Y") if hasattr(v, "strftime") else "—"

    def fmt(m: float, signe: bool = False, decimales: int = 0) -> str:
        """Un nombre à la française : 11 050, et +23 pour un signe.

        Le millier est une espace, la décimale une virgule — comme `ui.eur`.
        Le remplacement porte sur le NOMBRE, jamais sur la phrase : appliqué à
        la phrase, il effaçait toutes les virgules de ponctuation. Le défaut
        était visible à l'écran (« L'écart est de 11 027 € soit 37.2 % ») et il
        rendait les messages pénibles à lire, ce qui est exactement ce qu'on ne
        veut pas d'une alerte.
        """
        return f"{m:+,.{decimales}f}".replace(",", " ").replace(".", ",") \
            if signe else f"{m:,.{decimales}f}".replace(",", " ").replace(".", ",")

    debut = _jour(flux_sans_effet.get("date_avant"))
    fin_d = _jour(flux_sans_effet["date"])

    f = flux_sans_effet["flux"]
    bouge = flux_sans_effet["apres"] - flux_sans_effet["avant"]
    sens = "un apport" if f > 0 else "un retrait"
    periode = (f"entre le {debut} et le {fin_d}" if debut != "—"
               else f"au {fin_d}")

    return (
        f"**{sens} de {fmt(abs(f))} € enregistré {periode}, et la valeur ne "
        f"suit pas.** Le portefeuille ne varie que de {fmt(bouge, signe=True)} € "
        f"alors qu'il devrait varier d'au moins {fmt(abs(f))} € de ce seul fait. "
        f"L'écart est de {fmt(abs(flux_sans_effet['residuel']))} €, soit "
        f"{abs(flux_sans_effet['residuel_pct']):.1%} du portefeuille : aucun "
        "marché ne produit ça. Le versement a été saisi deux fois, porte une "
        "mauvaise date, ou n'a jamais eu lieu — et dans ce dernier cas c'est la "
        "valorisation de ce mois qui manque. Votre relevé tranchera."
    )


def flux_corrige_des_sauts(flux: list[float], sauts: list[dict]) -> list[float]:
    """Les flux, augmentés de chaque saut non expliqué.

    Sert à répondre à la seule question qui compte : « et si ce mouvement avait
    été enregistré comme un apport, quel serait mon chiffre ? » On ne modifie
    pas les données — on montre ce qu'elles donneraient, ce qui permet de juger
    l'ampleur du défaut au lieu de la deviner.
    """
    corriges = list(flux)
    for saut in sauts:
        i = saut.get("i")
        if isinstance(i, int) and 0 <= i < len(corriges):
            corriges[i] += saut["residuel"]
    return corriges


def anomalie_saut(saut: dict) -> str:
    """Le message à afficher pour un saut non expliqué."""
    brut = saut["date"]
    # Format français comme partout ailleurs : une date ISO se survole, une date
    # jj/mm/aaaa se lit.
    jour = brut.strftime("%d/%m/%Y") if hasattr(brut, "strftime") else str(brut)
    residuel = saut["residuel"]
    sens = "apparaissent" if residuel > 0 else "disparaissent"

    # Même règle que dans `anomalie_flux_sans_effet` : on formate les NOMBRES,
    # jamais la phrase. Le `.replace(",", " ")` global qui vivait ici effaçait
    # les virgules de ponctuation du message.
    def n(x: float, signe: bool = False) -> str:
        return f"{x:+,.0f}".replace(",", " ").replace(".", ",") if signe \
            else f"{x:,.0f}".replace(",", " ").replace(".", ",")

    return (
        f"**{n(residuel, signe=True)} € {sens} le {jour} sans flux enregistré.** "
        f"La valeur investie passe de {n(saut['avant'])} € à "
        f"{n(saut['apres'])} €, alors que {n(saut['flux'])} € de flux sont "
        f"enregistrés ce jour-là — soit {saut['residuel_pct']:+.1%} que le TWR "
        "compte comme du rendement. Un portefeuille diversifié ne bouge pas "
        "ainsi en une séance : c'est un virement interne, une position ajoutée à "
        "la main, ou un versement non saisi. Dans les trois cas, ce n'est pas de "
        "la performance, et les chiffres ci-dessus sont trop flatteurs."
    )
