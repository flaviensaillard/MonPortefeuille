"""Contexte applicatif : charge les données une fois, calcule tout.

CORRECTION PAR RAPPORT À LA V1
------------------------------
La v1 rechargeait et recalculait à chaque rendu de page, et le robot nocturne
écrivait des colonnes TWR dans Supabase depuis un script séparé (`calc_perf.py`).
Deux sources de vérité pour la même grandeur, désynchronisables.

Ici, le TWR est calculé à la demande depuis les snapshots, jamais stocké. Le
robot ne fait qu'écrire des snapshots bruts.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

from . import db, fx, metrics, prices
from .models import Perimetre, POCHES_PAR_CLE
from .portfolio import (
    Actif,
    agreger_par_poche,
    calculer_positions,
    charger_transactions,
    valoriser,
)
from .rebalance import diagnostiquer

log = logging.getLogger(__name__)


@dataclass
class Contexte:
    """Tout ce dont les pages ont besoin, calculé une fois."""

    transactions: list = field(default_factory=list)
    positions: dict = field(default_factory=dict)
    actifs: list[Actif] = field(default_factory=list)
    etats: dict = field(default_factory=dict)
    snapshots: pd.DataFrame = field(default_factory=pd.DataFrame)
    apports: pd.DataFrame = field(default_factory=pd.DataFrame)
    inflation: pd.DataFrame = field(default_factory=pd.DataFrame)

    total_investi_eur: float = 0.0
    total_precaution_eur: float = 0.0
    total_courant_eur: float = 0.0
    patrimoine_total_eur: float = 0.0

    total_investi_usd: float = 0.0
    total_precaution_usd: float = 0.0
    total_courant_usd: float = 0.0
    patrimoine_total_usd: float = 0.0
    taux_eur_usd: float = 1.125

    cours_or: float | None = None
    equivalent_or_oz: float | None = None

    echecs_cours: list[str] = field(default_factory=list)
    echecs_fx: list[str] = field(default_factory=list)
    tables_absentes: list[str] = field(default_factory=list)
    erreurs: list[str] = field(default_factory=list)
    anomalies_transactions: list[str] = field(default_factory=list)
    # Date du dernier import, lue dans `cree_le`. Sert a voir d'un coup
    # d'oeil si l'application regarde des donnees fraiches : un serveur qui
    # tourne sur une vieille version du code affiche une date anterieure au
    # dernier import, et le bandeau d'anomalie survit a sa correction.
    importe_le: str | None = None

    @property
    def ecarts(self):
        return diagnostiquer(self.etats, self.total_investi_eur, self.total_investi_usd)

    @property
    def besoins_reequilibrage(self):
        return [e for e in self.ecarts if e.hors_bande]


def _inflation_par_annee(df: pd.DataFrame) -> dict[int, float]:
    """Inflation annuelle, en fraction (2 % -> 0.02).

    DÉFAUT CORRIGÉ. Cette fonction faisait `if df.empty or "Annee" not in
    df.columns: return {}`. Elle confondait « pas de données » et « données que
    je ne sais pas lire » : `db.inflation()` rendait `annee`/`inflation` en
    minuscules, la colonne `Annee` était donc absente, et la fonction retournait
    un dictionnaire vide — pour toujours, et sans le dire.

    C'était le pire des deux mondes : la performance réelle de chaque année
    s'affichait « non calculable » alors que les chiffres étaient en base.

    Maintenant : une table ABSENTE ou VIDE reste silencieuse (au démarrage,
    c'est normal) ; une table PLEINE dont aucune ligne n'est exploitable lève.
    """
    if df is None or df.empty:
        return {}

    if "Annee" not in df.columns or "Inflation" not in df.columns:
        # `db.inflation()` fait le pont de noms et leve deja si c'est
        # impossible. Si on arrive ici, c'est que la table a ete lue par un
        # autre chemin — un test, un robot. On le dit.
        raise ValueError(
            f"Table d'inflation illisible : colonnes {list(df.columns)}. "
            "Attendu : Annee et Inflation. La performance reelle ne peut pas "
            "etre calculee — mieux vaut une erreur visible qu'un zero."
        )

    out: dict[int, float] = {}
    lignes_ignorees = 0
    for _, r in df.iterrows():
        try:
            out[int(r["Annee"])] = float(r["Inflation"]) / 100.0
        except (TypeError, ValueError):
            lignes_ignorees += 1
            continue

    if not out and lignes_ignorees:
        raise ValueError(
            f"{lignes_ignorees} ligne(s) d'inflation presentes, aucune "
            "exploitable. Les colonnes Annee/Inflation sont peut-etre "
            "inversees — verifiez la table pf2_inflation."
        )
    return out


def _date_dernier_import(df) -> str | None:
    """Date la plus recente de la colonne `cree_le`, ou None si absente.

    Volontairement tolerant : une table sans `cree_le` (ou vide) ne doit pas
    empecher l'application de demarrer.
    """
    try:
        if df is None or df.empty or "cree_le" not in getattr(df, "columns", []):
            return None
        valeurs = pd.to_datetime(df["cree_le"], errors="coerce").dropna()
        if valeurs.empty:
            return None
        return str(valeurs.max())
    except Exception:
        return None



# ---------------------------------------------------------------------------
# Le TWR du portefeuille — une seule implementation, trois appelants
# ---------------------------------------------------------------------------
# `derniere_valeur / premiere_valeur - 1` a ete ecrit a trois endroits :
# `Contexte.perf_globale_pct` (affiche sur la page d'accueil), le tableau
# annuel de `pages/5_Performance.py`, et le CAGR historique qui preremplit le
# scenario A de `pages/6_Retraite.py`. Les trois comptaient les VERSEMENTS
# comme du rendement.
#
# Sur le portefeuille reel — 10 905 EUR en avril 2023, 79 394 EUR en octobre
# 2026, alimente chaque mois — le calcul donnait +628 % cumule, soit **76 % par
# an** apres annualisation. C'est ce chiffre qui preremplissait la projection de
# retraite. La realite est un TWR de l'ordre de 10 a 15 %.
#
# Une seule fonction, testee, et plus aucun appelant ne peut reintroduire le
# defaut sans faire echouer `tests/test_twr_portefeuille.py`.


def flux_par_date(apports: pd.DataFrame, colonne: str = "montant_eur") -> dict:
    """Apports et retraits, dates par jour.

    `apports` doit avoir les colonnes `date`, `sens`, et `colonne`
    (`montant_usd` ou `montant_eur`). Retourne `{date: montant signe}`, un
    apport etant positif.
    """
    if apports is None or apports.empty:
        return {}
    col = colonne if colonne in apports.columns else "montant_eur"
    if not {"date", "sens", col} <= set(apports.columns):
        return {}
    dates = pd.to_datetime(apports["date"], errors="coerce")
    signes = apports["sens"].astype(str).str.strip().str.lower().map(
        {"apport": 1.0, "ajout": 1.0, "retrait": -1.0}
    )
    montants = pd.to_numeric(apports[col], errors="coerce")
    sortie: dict = {}
    for d, s, m in zip(dates, signes, montants):
        if pd.isna(d) or s is None or pd.isna(m):
            continue
        jour = d.date()
        sortie[jour] = sortie.get(jour, 0.0) + s * float(m)
    return sortie


def serie_performance(
    ctx: "Contexte",
) -> tuple[pd.DataFrame, list[float], list[float]]:
    """Série propre `(snapshots, valeurs, flux)` pour le TWR et les diagnostics.

    CONVENTION DE L'UTILISATEUR : tout est compté en DOLLARS ($).
    - Si `patrimoine_investi_usd` est présent dans `ctx.snapshots`, c'est lui
      qui est utilisé (série USD de `Projections` + valorisation USD du jour).
    - Si `capital_investi_usd` est présent (la colonne `Capital investi` de
      `Projections`), le flux de chaque sous-période `[i-1, i]` est
      `capital_investi_usd[i] - capital_investi_usd[i-1]` — exactement la
      définition de `recalculer_toute_la_base_projections` dans la v1, qui
      reproduit au centième de point les performances Swissquote / v1
      (2023 : +9,33 %, 2024 : +15,21 %, 2025 : +13,92 %, 2026 : +4,1 % / +4,47 %).
    - Pour toute période postérieure à `Projections` (ou dans les jeux de tests
      unitaires qui ne fournissent que `patrimoine_investi_eur`), on retombe sur
      `metrics.flux_par_periode` appliqué à `ctx.apports`.
    """
    snaps = ctx.snapshots
    if snaps is None or snaps.empty or "Date" not in snaps.columns:
        return pd.DataFrame(), [], []

    use_usd = (
        "patrimoine_investi_usd" in snaps.columns
        and pd.to_numeric(snaps["patrimoine_investi_usd"], errors="coerce").gt(0).sum() >= 2
    )
    col_val = "patrimoine_investi_usd" if use_usd else "patrimoine_investi_eur"
    if col_val not in snaps.columns:
        return pd.DataFrame(), [], []

    df = snaps.copy()
    df["Date"] = _parser_dates(df["Date"])
    df[col_val] = pd.to_numeric(df[col_val], errors="coerce")
    df = df.dropna(subset=["Date", col_val])
    df = df[df[col_val] > 0].sort_values("Date").reset_index(drop=True)
    if len(df) < 2:
        return df, [], []

    dates_l = [d.date() for d in df["Date"]]
    valeurs = df[col_val].astype(float).tolist()

    col_ap = "montant_usd" if (use_usd and ctx.apports is not None and "montant_usd" in getattr(ctx.apports, "columns", [])) else "montant_eur"
    flux_ap = metrics.flux_par_periode(dates_l, flux_par_date(ctx.apports, col_ap))

    if use_usd and "capital_investi_usd" in df.columns:
        cap = pd.to_numeric(df["capital_investi_usd"], errors="coerce")
        flux: list[float] = [0.0]
        for i in range(1, len(df)):
            if pd.notna(cap.iloc[i]) and pd.notna(cap.iloc[i - 1]):
                flux.append(float(cap.iloc[i] - cap.iloc[i - 1]))
            else:
                flux.append(float(flux_ap[i]))
    else:
        flux = flux_ap

    return df, valeurs, flux


def twr_portefeuille(ctx: "Contexte") -> float | None:
    """TWR depuis le premier snapshot, corrigé des apports et retraits (en $)."""
    df, valeurs, flux = serie_performance(ctx)
    if len(valeurs) < 2:
        return None
    return metrics.twr_depuis(valeurs, flux)


def twr_annualise_portefeuille(ctx: "Contexte") -> float | None:
    """Le même TWR, annualisé sur la durée couverte par les snapshots."""
    snaps = ctx.snapshots
    if snaps is None or snaps.empty or len(snaps) < 2:
        return None
    dates = _parser_dates(snaps["Date"]).dropna()
    if len(dates) < 2:
        return None
    jours = (dates.iloc[-1] - dates.iloc[0]).days
    total = twr_portefeuille(ctx)
    if total is None or jours <= 0:
        return None
    return metrics.annualiser(total, jours)


def twr_en_or_portefeuille(ctx: "Contexte") -> float | None:
    """Performance en onces d'or depuis le premier snapshot, corrigée des apports."""
    df, valeurs, flux = serie_performance(ctx)
    if len(valeurs) < 2 or "equivalent_or_oz" not in df.columns:
        return None
    onces = pd.to_numeric(df["equivalent_or_oz"], errors="coerce")
    if onces.isna().any() or (onces <= 0).any():
        return None
    try:
        return metrics.twr_en_or(valeurs, flux, onces.astype(float).tolist())
    except ValueError:
        return None


def _parser_dates(serie) -> pd.Series:
    """Parse une colonne de dates en Timestamp, sans faire échouer l'appelant."""
    from . import dates
    try:
        return pd.to_datetime(dates.parser(serie))
    except Exception:
        return pd.to_datetime(serie, errors="coerce")


@st.cache_data(ttl=300, show_spinner=False)
def charger(rafraichir_cours: bool = False) -> Contexte:
    """Charge et calcule l'état complet. Mémoïsé 5 minutes."""
    ctx = Contexte()

    # --- Tables ---
    try:
        etat_tables = db.tables_presentes()
        ctx.tables_absentes = [t for t, present in etat_tables.items() if not present]
    except db.SecretsManquants as exc:
        ctx.erreurs.append(str(exc))
        return ctx
    except Exception as exc:
        ctx.erreurs.append(f"Connexion Supabase impossible : {exc}")
        return ctx

    if ctx.tables_absentes:
        ctx.erreurs.append(
            "Tables manquantes : " + ", ".join(ctx.tables_absentes)
            + ". Exécutez migrations/001_init.sql dans Supabase."
        )
        return ctx

    # --- Transactions -> positions ---
    try:
        df_tx = db.transactions()
        ctx.importe_le = _date_dernier_import(df_tx)
        ctx.transactions = charger_transactions(df_tx)
        # Une transaction incohérente ne doit pas vider l'écran : on la consigne
        # et on continue, pour que vous voyiez le reste du portefeuille.
        anomalies: list[str] = []
        ctx.positions = calculer_positions(ctx.transactions, anomalies)
        ctx.anomalies_transactions = anomalies
    except ValueError as exc:
        ctx.erreurs.append(f"Transactions illisibles : {exc}")
        return ctx
    except Exception as exc:
        ctx.erreurs.append(f"Chargement des transactions : {exc}")
        return ctx

    # --- Valorisation ---
    try:
        ctx.actifs, ctx.echecs_cours = valoriser(ctx.positions)
    except fx.FXIndisponible as exc:
        ctx.echecs_fx.append(str(exc))

    # --- Taux EUR -> USD courant (pour l'indication en euros partout) ---
    aujourdhui_iso = dt.date.today().isoformat()
    try:
        ctx.taux_eur_usd = float(fx.taux("EUR", aujourdhui_iso, "USD"))
    except Exception:
        ctx.taux_eur_usd = 1.125
    from . import ui as _ui
    _ui.definir_taux_eur_usd(ctx.taux_eur_usd)

    # --- Liquidités hors transactions (CHF, CNY, USD de la table Donnees v1) ---
    _completer_liquidites_v1(ctx, aujourdhui_iso)

    # --- Agrégation par poche, sur le patrimoine INVESTI seulement ---
    perimetres_eur: dict[str, float] = {p.value: 0.0 for p in Perimetre}
    perimetres_usd: dict[str, float] = {p.value: 0.0 for p in Perimetre}
    for a in ctx.actifs:
        p = POCHES_PAR_CLE.get(a.poche)
        cle = p.perimetre.value if p else Perimetre.INVESTI.value
        perimetres_eur[cle] += a.valeur_eur
        perimetres_usd[cle] += getattr(a, "valeur_usd", a.valeur_eur)

    ctx.total_investi_eur = perimetres_eur[Perimetre.INVESTI.value]
    ctx.total_precaution_eur = perimetres_eur[Perimetre.PRECAUTION.value]
    ctx.total_courant_eur = perimetres_eur[Perimetre.COURANT.value]
    ctx.patrimoine_total_eur = sum(perimetres_eur.values())

    ctx.total_investi_usd = perimetres_usd[Perimetre.INVESTI.value]
    ctx.total_precaution_usd = perimetres_usd[Perimetre.PRECAUTION.value]
    ctx.total_courant_usd = perimetres_usd[Perimetre.COURANT.value]
    ctx.patrimoine_total_usd = sum(perimetres_usd.values())

    ctx.etats = agreger_par_poche(ctx.actifs, ctx.total_investi_eur)

    # --- Or : l'étalon de Gave ---
    try:
        ctx.cours_or = prices.cours_or()
        if ctx.cours_or and ctx.total_investi_usd > 0:
            ctx.equivalent_or_oz = ctx.total_investi_usd / ctx.cours_or
    except prices.CoursIndisponible:
        ctx.echecs_cours.append(prices.TICKER_OR)
    except fx.FXIndisponible:
        ctx.echecs_fx.append("EUR/USD (pour l'équivalent-or)")

    # --- Historiques (en dollars, avec l'indication en euros) ---
    try:
        ctx.snapshots = db.snapshots()
        ctx.apports = db.apports()
        ctx.inflation = db.inflation()
    except Exception as exc:
        ctx.erreurs.append(f"Chargement des historiques : {exc}")

    _enrichir_historiques_usd(ctx)

    return ctx


def _completer_liquidites_v1(ctx: Contexte, jour_iso: str) -> None:
    """Charge les réserves de cash (CHF, CNY, USD, EUR) depuis `Donnees` (v1).

    Dans la v1, les liquidités (`🏦 Cash réserve` et `💵 Cash`) étaient tenues
    directement dans la table `Donnees` et non dans `Transaction`. Si aucune
    ligne de précaution ou de compte courant n'est issue des transactions, on
    les lit dans `Donnees` pour que l'épargne de précaution (CHF, CNY) et le
    compte courant apparaissent sur le tableau de bord.
    """
    from .models import Classe
    from .portfolio import poche_de

    deja = {a.ticker for a in ctx.actifs if a.quantite > 0}
    if any(t in deja for t in ("CHF", "CNY")):
        return
    try:
        df_d = db.lire("Donnees")
    except Exception:
        return
    if df_d is None or df_d.empty or "Ticker" not in df_d.columns:
        return

    for _, r in df_d.iterrows():
        t = str(r.get("Ticker", "")).upper().strip()
        if t not in ("CHF", "CNY", "USD", "EUR") or t in deja:
            continue
        try:
            qte = float(str(r.get("Quantité", 0) or 0).replace(" ", "").replace(",", "."))
        except (TypeError, ValueError):
            continue
        if qte <= 0:
            continue
        try:
            t_eur = 1.0 if t == "EUR" else float(fx.taux(t, jour_iso, "EUR"))
            t_usd = 1.0 if t == "USD" else float(fx.taux(t, jour_iso, "USD"))
        except Exception:
            continue
        poche = poche_de(t)
        ctx.actifs.append(Actif(
            ticker=t,
            classe=Classe.ESPECE,
            devise_cotation=t,
            poche=poche.cle if poche else "precaution",
            quantite=qte,
            prix=1.0,
            valeur_eur=qte * t_eur,
            valeur_usd=qte * t_usd,
            dernier_taux=t_eur,
            dernier_taux_usd=t_usd,
        ))


def _enrichir_historiques_usd(ctx: Contexte) -> None:
    """Attache les colonnes USD (`*_usd`) aux apports et aux snapshots.

    1. Pour `ctx.apports` : chaque ligne reçoit `montant_usd` depuis la table
       `Historique` de la v1 (`Montant $`), ou à défaut depuis
       `montant_or * cours_or` (qui vaut exactement `Montant $`), ou
       `montant_eur * taux_eur_usd`.
    2. Pour `ctx.snapshots` : les colonnes `Actifs Stratégiques`, `Total Global`
       et `Capital investi` de `Projections` (qui sont nativement en dollars
       dans la v1) alimentent `patrimoine_investi_usd`, `patrimoine_total_usd`,
       `precaution_usd` et `capital_investi_usd`. Et si le portefeuille est
       valorisé en direct aujourd'hui (`ctx.total_investi_usd > 0`), le dernier
       point reflète la valeur en direct (comme `df_p_live` dans `app.py` l. 461
       de la v1), ce qui donne la performance 2026 en temps réel.
    """
    from . import dates as _dates

    taux = ctx.taux_eur_usd if ctx.taux_eur_usd > 0 else 1.125

    # --- 1. Apports en USD ---
    if ctx.apports is not None and not ctx.apports.empty:
        ap = ctx.apports.copy()
        usd_par_ref: dict[str, float] = {}
        try:
            df_h = db.lire("Historique")
            if df_h is not None and not df_h.empty and "Montant $" in df_h.columns:
                for _, r in df_h.iterrows():
                    if pd.notna(r.get("id")) and pd.notna(r.get("Montant $")):
                        usd_par_ref[f"v1:id{r.get('id')}"] = abs(float(r["Montant $"]))
        except Exception:
            pass

        montants_usd: list[float] = []
        for _, r in ap.iterrows():
            ref = str(r.get("reference") or "")
            if ref in usd_par_ref:
                montants_usd.append(round(usd_par_ref[ref], 2))
            elif pd.notna(r.get("montant_or")) and pd.notna(r.get("cours_or")) and float(r.get("montant_or") or 0) > 0 and float(r.get("cours_or") or 0) > 0:
                montants_usd.append(round(float(r["montant_or"]) * float(r["cours_or"]), 2))
            else:
                montants_usd.append(round(float(r.get("montant_eur") or 0.0) * taux, 2))
        ap["montant_usd"] = montants_usd
        ctx.apports = ap

    # --- 2. Snapshots en USD (depuis Projections + pf2_snapshots) ---
    df_proj = pd.DataFrame()
    try:
        df_proj = db.lire("Projections")
    except Exception:
        df_proj = pd.DataFrame()

    snaps = ctx.snapshots.copy() if (ctx.snapshots is not None and not ctx.snapshots.empty) else pd.DataFrame()

    if df_proj is not None and not df_proj.empty and "Date" in df_proj.columns:
        p = df_proj.copy()
        p["_dt"] = _dates.parser(p["Date"]).dt.tz_localize(None).dt.normalize()
        p = p.dropna(subset=["_dt"]).sort_values("_dt").drop_duplicates(subset=["_dt"], keep="last")
        for c in ("Actifs Stratégiques", "Total Global", "Capital investi"):
            if c in p.columns:
                p[c] = pd.to_numeric(p[c], errors="coerce")
        p = p.dropna(subset=["Actifs Stratégiques"])
        p = p[p["Actifs Stratégiques"] > 0].reset_index(drop=True)

        # Dictionnaire d'or depuis pf2_snapshots
        or_par_date: dict = {}
        cours_or_par_date: dict = {}
        eur_inv_par_date: dict = {}
        eur_tot_par_date: dict = {}
        if not snaps.empty and "Date" in snaps.columns:
            snaps["_dt"] = _dates.parser(snaps["Date"]).dt.tz_localize(None).dt.normalize()
            for _, sr in snaps.dropna(subset=["_dt"]).iterrows():
                d_cle = sr["_dt"].date()
                if pd.notna(sr.get("equivalent_or_oz")):
                    or_par_date[d_cle] = float(sr["equivalent_or_oz"])
                if pd.notna(sr.get("cours_or_usd")):
                    cours_or_par_date[d_cle] = float(sr["cours_or_usd"])
                if pd.notna(sr.get("patrimoine_investi_eur")):
                    eur_inv_par_date[d_cle] = float(sr["patrimoine_investi_eur"])
                if pd.notna(sr.get("patrimoine_total_eur")):
                    eur_tot_par_date[d_cle] = float(sr["patrimoine_total_eur"])

        lignes_u = []
        for _, r in p.iterrows():
            d_cle = r["_dt"].date()
            inv_u = float(r["Actifs Stratégiques"])
            tot_u = float(r["Total Global"]) if pd.notna(r.get("Total Global")) else inv_u
            cap_u = float(r["Capital investi"]) if pd.notna(r.get("Capital investi")) else None
            inv_e = eur_inv_par_date.get(d_cle, round(inv_u / taux, 2))
            tot_e = eur_tot_par_date.get(d_cle, round(tot_u / taux, 2))
            lignes_u.append({
                "Date": pd.Timestamp(d_cle),
                "date": d_cle.isoformat(),
                "patrimoine_investi_usd": round(inv_u, 2),
                "patrimoine_total_usd": round(tot_u, 2),
                "precaution_usd": round(max(tot_u - inv_u, 0.0), 2),
                "courant_usd": 0.0,
                "capital_investi_usd": round(cap_u, 2) if cap_u is not None else None,
                "patrimoine_investi_eur": round(inv_e, 2),
                "patrimoine_total_eur": round(tot_e, 2),
                "precaution_eur": round(max(tot_e - inv_e, 0.0), 2),
                "courant_eur": 0.0,
                "cours_or_usd": cours_or_par_date.get(d_cle),
                "equivalent_or_oz": or_par_date.get(d_cle),
            })

        # Ajouter les éventuels snapshots de pf2_snapshots postérieurs à Projections
        max_proj = p["_dt"].max().date() if not p.empty else dt.date.min
        if not snaps.empty and "_dt" in snaps.columns:
            apres = snaps[snaps["_dt"].dt.date > max_proj].sort_values("_dt")
            for _, sr in apres.iterrows():
                d_cle = sr["_dt"].date()
                inv_e = float(sr.get("patrimoine_investi_eur") or 0.0)
                tot_e = float(sr.get("patrimoine_total_eur") or inv_e)
                oz = sr.get("equivalent_or_oz")
                co = sr.get("cours_or_usd")
                if pd.notna(oz) and pd.notna(co) and float(oz) > 0 and float(co) > 0:
                    inv_u = float(oz) * float(co)
                    ratio = inv_u / inv_e if inv_e > 0 else taux
                    tot_u = tot_e * ratio
                else:
                    inv_u = inv_e * taux
                    tot_u = tot_e * taux
                lignes_u.append({
                    "Date": pd.Timestamp(d_cle),
                    "date": d_cle.isoformat(),
                    "patrimoine_investi_usd": round(inv_u, 2),
                    "patrimoine_total_usd": round(tot_u, 2),
                    "precaution_usd": round(max(tot_u - inv_u, 0.0), 2),
                    "courant_usd": 0.0,
                    "capital_investi_usd": None,
                    "patrimoine_investi_eur": round(inv_e, 2),
                    "patrimoine_total_eur": round(tot_e, 2),
                    "precaution_eur": round(max(tot_e - inv_e, 0.0), 2),
                    "courant_eur": 0.0,
                    "cours_or_usd": float(co) if pd.notna(co) else None,
                    "equivalent_or_oz": float(oz) if pd.notna(oz) else None,
                })

        df_out = pd.DataFrame(lignes_u)
        # Mise à jour du point du jour avec la valorisation en direct (comme
        # `df_p_live` dans `app.py` l. 461 de la v1) pour que 2026 affiche la
        # performance en direct (ex. +4,1 % à 79 007 $).
        if not df_out.empty and ctx.total_investi_usd > 0:
            idx_last = df_out.index[-1]
            df_out.at[idx_last, "patrimoine_investi_usd"] = round(ctx.total_investi_usd, 2)
            df_out.at[idx_last, "patrimoine_investi_eur"] = round(ctx.total_investi_eur, 2)
            if ctx.patrimoine_total_usd >= ctx.total_investi_usd:
                df_out.at[idx_last, "patrimoine_total_usd"] = round(ctx.patrimoine_total_usd, 2)
                df_out.at[idx_last, "patrimoine_total_eur"] = round(ctx.patrimoine_total_eur, 2)
                df_out.at[idx_last, "precaution_usd"] = round(ctx.total_precaution_usd, 2)
                df_out.at[idx_last, "precaution_eur"] = round(ctx.total_precaution_eur, 2)
            if ctx.equivalent_or_oz:
                df_out.at[idx_last, "equivalent_or_oz"] = round(ctx.equivalent_or_oz, 4)
            if ctx.cours_or:
                df_out.at[idx_last, "cours_or_usd"] = round(ctx.cours_or, 2)

        ctx.snapshots = df_out
    elif not snaps.empty:
        # Repli si `Projections` n'est pas accessible : on déduit l'USD depuis
        # `equivalent_or_oz * cours_or_usd` ou `patrimoine_investi_eur * taux`.
        inv_u_list = []
        tot_u_list = []
        prec_u_list = []
        for _, sr in snaps.iterrows():
            inv_e = float(sr.get("patrimoine_investi_eur") or 0.0)
            tot_e = float(sr.get("patrimoine_total_eur") or inv_e)
            oz = sr.get("equivalent_or_oz")
            co = sr.get("cours_or_usd")
            if pd.notna(oz) and pd.notna(co) and float(oz) > 0 and float(co) > 0:
                inv_u = float(oz) * float(co)
                ratio = inv_u / inv_e if inv_e > 0 else taux
                tot_u = tot_e * ratio
            else:
                inv_u = inv_e * taux
                tot_u = tot_e * taux
            inv_u_list.append(round(inv_u, 2))
            tot_u_list.append(round(tot_u, 2))
            prec_u_list.append(round(max(tot_u - inv_u, 0.0), 2))
        snaps["patrimoine_investi_usd"] = inv_u_list
        snaps["patrimoine_total_usd"] = tot_u_list
        snaps["precaution_usd"] = prec_u_list
        ctx.snapshots = snaps


def inflation_dict(ctx: Contexte) -> dict[int, float]:
    return _inflation_par_annee(ctx.inflation)


def vider_cache() -> None:
    charger.clear()
    prices.vider_cache()
    fx.vider_cache()
