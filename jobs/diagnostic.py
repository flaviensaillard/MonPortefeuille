# -*- coding: utf-8 -*-
"""Diagnostic v3 — il MONTRE les données, il ne les compte plus.

POURQUOI CETTE VERSION EXISTE
-----------------------------
La version 2 comptait 42 lignes d'un « autre type » dans la table Historique
de la v1, puis concluait « aucune valuation » — sans jamais afficher une seule
de ces lignes. Un contrôle qui compte sans montrer ne peut rien révéler : il
transforme une ignorance en chiffre, et le chiffre a l'air d'un fait.

C'est exactement le défaut que l'audit venait de corriger ailleurs. Je l'avais
dans mon propre outil de diagnostic.

CE QUE LA VERSION 3 FAIT
------------------------
1. Elle LISTE TOUTES LES TABLES du projet Supabase. La table qui contient vos
   180 valuations existe quelque part ; tant qu'on ne l'a pas nommée, on
   cherche à l'aveugle.
2. Elle AFFICHE les valeurs distinctes de `Type` et des lignes brutes, au lieu
   de les classer.
3. Elle AFFICHE la série de valeurs des snapshots, mois par mois, et les plus
   gros sauts journaliers — c'est là que se cache le +26,2 %.
4. Elle RECALCULE le TWR par année avec vos données, et le compare au calcul
   naïf. L'écart entre les deux dit exactement combien vos versements pèsent.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
import urllib.request

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dates, db, metrics  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("diagnostic")

connexion_ok = True
verdicts: list[str] = []

# Les sauts sans flux, calculés une fois par `examiner_sauts` et relus par le
# bloc à copier. Deux calculs du même chiffre finissent toujours par diverger.
derniers_sauts: list[dict] = []

# Noms plausibles pour un journal de valorisations de la v1. Utilises seulement
# si l'enumeration complete echoue.
NOMS_PLAUSIBLES = [
    "Valuation", "Valuations", "Valorisation", "Valorisations",
    "Valeur", "Valeurs", "Valeur_Portefeuille", "Portefeuille",
    "Snapshot", "Snapshots", "Patrimoine", "Evolution", "Évolution",
    "Historique_Valorisation", "Suivi", "Performance", "Mensuel",
    "Historique_Mensuel", "Mois", "Vue", "Tableau",
]


def _titre(texte: str) -> None:
    log.info("")
    log.info("=" * 74)
    log.info("%s", texte)
    log.info("=" * 74)


def _fmt(valeur) -> str:
    """Formate une cellule pour l'affichage. Jamais de troncature muette."""
    if valeur is None or (isinstance(valeur, float) and pd.isna(valeur)):
        return "·"
    if isinstance(valeur, float):
        if valeur == int(valeur) and abs(valeur) < 1e12:
            return f"{int(valeur)}"
        return f"{valeur:,.4f}".replace(",", " ").rstrip("0").rstrip(".")
    return str(valeur)


def _montre(df: pd.DataFrame, max_lignes: int = 60) -> None:
    """Affiche un tableau. C'est la fonction qui manquait à la v2.

    Les chiffres sont alignés à droite : une colonne de montants alignée à
    gauche se lit mal, et une virgule qui flotte d'une ligne à l'autre fait
    perdre l'ordre de grandeur — qui est exactement ce qu'on cherche ici.
    """
    if df is None or df.empty:
        log.info("    (aucune ligne)")
        return

    df = df.head(max_lignes)
    cols = list(df.columns)

    numerique = {}
    for c in cols:
        try:
            pd.to_numeric(df[c])
            numerique[c] = True
        except (TypeError, ValueError):
            numerique[c] = False

    cellules = {c: [_fmt(v) for v in df[c]] for c in cols}
    largeurs = {c: max(len(str(c)), *(len(x) for x in cellules[c])) for c in cols}

    def ligne(valeurs):
        parts = []
        for c in cols:
            texte = str(valeurs[c] if isinstance(valeurs, dict) else valeurs[cols.index(c)])
            parts.append(texte.rjust(largeurs[c]) if numerique[c] else texte.ljust(largeurs[c]))
        return "    " + "  ".join(parts).rstrip()

    log.info(ligne({c: c for c in cols}))
    log.info("    " + "  ".join("-" * largeurs[c] for c in cols))
    for i in range(len(df)):
        log.info(ligne({c: cellules[c][i] for c in cols}))
    if len(df) == max_lignes:
        log.info("    … (tronqué à %d lignes)", max_lignes)


# ===========================================================================
# 0. Connexion, et LISTE DE TOUTES LES TABLES
# ===========================================================================

def _liste_tables() -> list[str]:
    """Énumère toutes les tables exposées par PostgREST.

    Supabase expose sa racine REST en OpenAPI : `definitions` contient une
    entrée par table. C'est la seule façon de trouver une table dont on ne
    connaît pas le nom — et c'est précisément le problème.
    """
    url, cle = db._credentials()
    base = url.rstrip("/") + "/rest/v1/"
    requete = urllib.request.Request(base, headers={
        "apikey": cle,
        "Authorization": f"Bearer {cle}",
        "Accept": "application/openapi+json",
    })
    with urllib.request.urlopen(requete, timeout=60) as reponse:
        spec = json.loads(reponse.read().decode("utf-8"))
    return sorted((spec.get("definitions") or {}).keys())


def sonder_connexion() -> None:
    global connexion_ok
    _titre("0. LA CONNEXION, ET TOUTES VOS TABLES")

    try:
        import supabase  # noqa: F401
    except ImportError:
        connexion_ok = False
        log.info("  Le module « supabase » n'est pas installé.")
        verdicts.append("Le module « supabase » n'est pas installé : rien ne peut être lu.")
        return

    try:
        url, cle = db._credentials()
    except Exception as exc:
        connexion_ok = False
        log.info("  Identifiants absents : %s", exc)
        verdicts.append(
            "SUPABASE_URL et/ou SUPABASE_KEY sont absents. Dans GitHub : "
            "Settings → Secrets and variables → Actions."
        )
        return

    log.info("  URL : %s", url)
    log.info("  Clé : %s…%s (%d caractères)", cle[:6], cle[-4:], len(cle))

    try:
        db.client().table(db.T_SNAPSHOTS).select("*").limit(1).execute()
        log.info("  Connexion établie. ✓")
    except Exception as exc:
        connexion_ok = False
        log.info("  ÉCHEC DE CONNEXION : %s", str(exc)[:300])
        verdicts.append(f"Impossible de joindre la base : {str(exc)[:200]}")
        return

    # ---- L'INVENTAIRE ----
    log.info("")
    try:
        tables = _liste_tables()
    except Exception as exc:
        log.info("  Énumération automatique impossible (%s).", str(exc)[:120])
        log.info("  Essai par noms plausibles :")
        tables = []
        for nom in NOMS_PLAUSIBLES:
            try:
                if db.existe(nom):
                    tables.append(nom)
            except Exception:
                pass
        if not tables:
            log.info("    aucune table trouvée sous un nom plausible")
            verdicts.append(
                "Impossible d'énumérer les tables du projet, et aucune table "
                "plausible de valorisations n'a été trouvée. Donnez-moi la liste "
                "depuis le tableau de bord Supabase (Table Editor, colonne de "
                "gauche)."
            )
            return

    log.info("  %d table(s) dans le projet :", len(tables))
    for t in tables:
        marque = ""
        if t.startswith("pf2_"):
            marque = "   <- v2"
        elif t.startswith("_") or t in ("spatial_ref_sys",):
            marque = "   (interne Supabase)"
        log.info("    %-38s%s", t, marque)

    inconnues = [t for t in tables
                 if not t.startswith("pf2_") and t != "spatial_ref_sys"]
    if inconnues:
        log.info("")
        log.info("  Tables hors v2 : %s", ", ".join(inconnues))
        log.info("  -> Ce sont elles qu'il faut regarder pour retrouver vos valuations.")


examiner_tables_v1 = None          # defini plus bas, pour lisibilite


def _date_range(df: pd.DataFrame, colonnes=("Date", "date")) -> str:
    """Étendue des dates d'une colonne, quel que soit le format.

    DÉFAUT CORRIGÉ DANS MON PROPRE OUTIL. Cette fonction faisait
    `pd.to_datetime(colonne, dayfirst=True)`, appliqué aveuglément.

    Or `dayfirst=True` sur une date ISO ne lève pas : il la LIT À L'ENVERS.

        2026-10-01 -> 2026-01-10
        2023-08-01 -> 2023-01-08

    Le 1er octobre devenait le 10 janvier, en silence. Les tables de la v1 sont
    en `jj/mm/aaaa` et veulent `dayfirst` ; celles de la v2 sont en ISO et le
    refusent. Le diagnostic appliquait le même traitement aux deux.

    C'est le défaut que `core/dates.py` documente longuement — et je l'ai
    réintroduit dans l'outil censé le détecter. On réutilise donc LE parseur de
    l'application : il choisit le format à la forme de la chaîne, sans jamais
    laisser pandas deviner.
    """
    for c in colonnes:
        if c in df.columns:
            d = dates.parser(df[c]).dropna()
            if not d.empty:
                return f"{d.min().date()} → {d.max().date()}"
    return "dates illisibles"


# Les tables de la v1 que le diagnostic interroge.
V1_HISTORIQUE = "Historique"        # journal de trésorerie (apports, retraits)
V1_PROJECTIONS = "Projections"      # valorisations : 180 lignes, 04/2023 -> 10/2026


def lire_v1(table: str) -> pd.DataFrame:
    """Lit une table de la v1, sans rien journaliser ni conclure.

    `examiner_v1` raconte ce qu'elle voit ; celle-ci se contente de rendre les
    lignes, pour les contrôles qui ont besoin des deux tables en même temps.
    """
    if not connexion_ok:
        return pd.DataFrame()
    try:
        rep = db.client().table(table).select("*").execute()
        donnees = getattr(rep, "data", None)
        return donnees if isinstance(donnees, pd.DataFrame) else pd.DataFrame(donnees or [])
    except Exception:
        return pd.DataFrame()


def examiner_v1(table: str) -> pd.DataFrame:
    """Inspecte une table de la v1 : colonnes, types distincts, lignes brutes."""
    if not connexion_ok:
        return pd.DataFrame()

    _titre(f"TABLE v1 : {table}")

    try:
        rep = db.client().table(table).select("*").execute()
        donnees = getattr(rep, "data", None)
        # `donnees or []` levait « truth value of a DataFrame is ambiguous » des
        # que l'appelant rendait un DataFrame au lieu d'une liste. Une ligne de
        # diagnostic qui plante sur le format qu'elle teste ne diagnostique rien.
        df = donnees if isinstance(donnees, pd.DataFrame) else pd.DataFrame(donnees or [])
    except Exception as exc:
        log.info("  Lecture impossible : %s", str(exc)[:200])
        return pd.DataFrame()

    if df.empty:
        log.info("  Table VIDE.")
        return df

    log.info("  %d lignes, %s", len(df), _date_range(df))
    log.info("  Colonnes : %s", ", ".join(map(str, df.columns)))

    if "Type" in df.columns:
        log.info("")
        log.info("  ▸ Valeurs distinctes de « Type » — c'est ce qu'il fallait voir :")
        compte = df["Type"].fillna("(vide)").astype(str).value_counts()
        for valeur, n in compte.items():
            log.info("      %-32s %3d ligne(s)", repr(valeur), n)
        # Le filtre de `importer_apports` : sous-chaine, pas egalite. Mon
        # diagnostic v2 testait l'egalite et annoncait « 1 mouvement » la ou
        # l'import en trouvait 38. Deux mesures, deux verites.
        types_bas = df["Type"].fillna("").astype(str).str.lower()
        n_apport = int(types_bas.str.contains("ajout|apport", regex=True).sum())
        n_retrait = int(types_bas.str.contains("retrait", regex=True).sum())
        log.info("")
        log.info("      dont, par sous-chaîne (méthode de l'import) : "
                 "%d apport(s), %d retrait(s)", n_apport, n_retrait)
        restantes = df[~(types_bas.str.contains("ajout|apport|retrait", regex=True))]
        if not restantes.empty:
            log.info("")
            log.info("  ▸ LES %d LIGNES QUI NE SONT NI APPORTS NI RETRAITS :",
                     len(restantes))
            log.info("      (la version 2 de ce diagnostic les comptait sans les montrer)")
            _montre(restantes)

    log.info("")
    log.info("  ▸ Lignes brutes (les 5 premières, puis les 5 dernières) :")
    _montre(pd.concat([df.head(5), df.tail(5)]).drop_duplicates())

    return df


# ===========================================================================
# Les snapshots : ou est le +26,2 % ?
# ===========================================================================

def examiner_snapshots() -> pd.DataFrame:
    _titre("VOS SNAPSHOTS — la série de valeurs")

    try:
        snaps = db.lire(db.T_SNAPSHOTS)
    except Exception as exc:
        log.info("  Lecture impossible : %s", exc)
        return pd.DataFrame()

    if snaps.empty:
        log.info("  AUCUN snapshot.")
        verdicts.append("pf2_snapshots est vide : aucune performance n'est calculable.")
        return snaps

    snaps["Date_DT"] = pd.to_datetime(snaps["date"], errors="coerce")
    snaps = snaps.dropna(subset=["Date_DT"]).sort_values("Date_DT").reset_index(drop=True)

    colonne = "patrimoine_investi_eur"
    if colonne not in snaps.columns:
        log.info("  Colonne « %s » absente. Présentes : %s", colonne, list(snaps.columns))
        return snaps

    valeurs = pd.to_numeric(snaps[colonne], errors="coerce")

    log.info("  %d snapshots, du %s au %s",
             len(snaps), snaps["Date_DT"].min().date(), snaps["Date_DT"].max().date())
    log.info("  Valeur investie : de %s € à %s €",
             _fmt(float(valeurs.min())), _fmt(float(valeurs.max())))

    # --- La série, mois par mois ---
    log.info("")
    log.info("  ▸ Premier et dernier snapshot de chaque mois :")
    mois = snaps.assign(_m=snaps["Date_DT"].dt.to_period("M"))
    lignes = []
    for periode, groupe in mois.groupby("_m"):
        prem, dern = groupe.iloc[0], groupe.iloc[-1]
        v0 = float(pd.to_numeric(prem[colonne], errors="coerce"))
        v1 = float(pd.to_numeric(dern[colonne], errors="coerce"))
        lignes.append({
            "mois": str(periode),
            "du": str(prem["Date_DT"].date()),
            "valeur_depart": v0,
            "au": str(dern["Date_DT"].date()),
            "valeur_fin": v1,
            "variation_%": (v1 / v0 - 1) * 100 if v0 else 0.0,
        })
    _montre(pd.DataFrame(lignes), max_lignes=48)

    # --- Les plus gros sauts journaliers ---
    log.info("")
    log.info("  ▸ Les 12 plus gros MOUVEMENTS d'un jour à l'autre, toutes")
    log.info("     amplitudes — un krach y figure, et ce n'est pas un défaut.")
    log.info("     Les sauts au sens strict (8 % sans flux) sont comptés plus")
    log.info("     haut, dans « LES SAUTS SANS FLUX ».")

    apports = pd.DataFrame()
    try:
        apports = db.lire(db.T_APPORTS)
    except Exception:
        pass

    flux = {}
    if not apports.empty and {"date", "sens", "montant_eur"} <= set(apports.columns):
        d = pd.to_datetime(apports["date"], errors="coerce")
        s = apports["sens"].astype(str).str.strip().str.lower().map(
            {"apport": 1.0, "ajout": 1.0, "retrait": -1.0})
        m = pd.to_numeric(apports["montant_eur"], errors="coerce")
        for dd, ss, mm in zip(d, s, m):
            if pd.isna(dd) or ss is None or pd.isna(mm):
                continue
            flux[dd.date()] = flux.get(dd.date(), 0.0) + ss * float(mm)

    sauts = []
    for i in range(1, len(snaps)):
        v0 = float(pd.to_numeric(snaps.loc[i - 1, colonne], errors="coerce"))
        v1 = float(pd.to_numeric(snaps.loc[i, colonne], errors="coerce"))
        if not v0 or pd.isna(v0) or pd.isna(v1):
            continue
        jour = snaps.loc[i, "Date_DT"].date()
        f = flux.get(jour, 0.0)
        sauts.append({
            "date": str(jour),
            "veille": v0,
            "jour": v1,
            "apport_du_jour": f,
            "variation_%": (v1 / v0 - 1) * 100,
            "hors_apport_%": ((v1 - f) / v0 - 1) * 100 if v0 else 0.0,
        })
    sauts = pd.DataFrame(sauts)
    if not sauts.empty:
        sauts["_abs"] = sauts["variation_%"].abs()
        _montre(sauts.sort_values("_abs", ascending=False).head(12)
                .drop(columns="_abs"), max_lignes=12)
    return snaps


# ===========================================================================
# Le TWR, recalculé sur vos données
# ===========================================================================

def examiner_sauts(snaps: pd.DataFrame, apports: pd.DataFrame) -> None:
    """Les journées où la valeur saute sans flux — c'est là qu'est le +26,2 %."""
    _titre("LES SAUTS SANS FLUX — l'origine du chiffre trop flatteur")

    if snaps.empty or "patrimoine_investi_eur" not in snaps.columns:
        log.info("  Pas de quoi calculer.")
        return

    s2 = snaps.copy()
    s2["_d"] = pd.to_datetime(s2["date"], errors="coerce")
    s2 = s2.dropna(subset=["_d"]).sort_values("_d").reset_index(drop=True)

    flux = {}
    if not apports.empty and {"date", "sens", "montant_eur"} <= set(apports.columns):
        dd = pd.to_datetime(apports["date"], errors="coerce")
        ss = apports["sens"].astype(str).str.strip().str.lower().map(
            {"apport": 1.0, "ajout": 1.0, "retrait": -1.0})
        mm = pd.to_numeric(apports["montant_eur"], errors="coerce")
        for x, y, z in zip(dd, ss, mm):
            if pd.isna(x) or y is None or pd.isna(z):
                continue
            flux[x.date()] = flux.get(x.date(), 0.0) + y * float(z)

    dates = [d.date() for d in s2["_d"]]
    valeurs = pd.to_numeric(s2["patrimoine_investi_eur"], errors="coerce").fillna(0).tolist()
    fluxs = metrics.flux_par_periode(dates, flux)

    sauts = metrics.sauts_non_expliques(dates, valeurs, fluxs)
    derniers_sauts[:] = sauts

    if not sauts:
        log.info("  Aucun saut au-dessus du seuil. ✓")
        return

    log.info("  %d journée(s) où la valeur bouge sans flux pour l'expliquer.",
             len(sauts))
    log.info("")
    log.info("  Le TWR ne connaît que les flux enregistrés : il compte ces")
    log.info("  montants comme du rendement. Ce n'est pas du rendement.")
    log.info("")
    _montre(pd.DataFrame([{
        "date": str(x["date"]),
        "avant": x["avant"],
        "apres": x["apres"],
        "flux_du_jour": x["flux"],
        "sans_flux": x["residuel"],
        "variation_%": x["residuel_pct"] * 100,
    } for x in sauts]), max_lignes=40)

    total = sum(x["residuel"] for x in sauts)
    log.info("")
    log.info("  Total à déclarer comme apports : %s €", _fmt(total))

    if len(sauts) == 1:
        # Jamais de date écrite en dur : on lit celle du saut. Une phrase figée
        # survivrait au correctif et désignerait un jour qui n'est plus fautif.
        x = sauts[0]
        try:
            jour = pd.Timestamp(x["date"]).strftime("%d/%m/%Y")
        except Exception:
            jour = str(x["date"])
        log.info("")
        log.info("  ▸ Le %s, la valeur fait %+.2f %% en une séance.", jour,
                 x["residuel_pct"] * 100)
        log.info("    Un portefeuille diversifié ne fait pas ça. C'est un virement")
        log.info("    interne, une position ajoutée à la main, ou un versement")
        log.info("    réel jamais saisi dans le journal des apports.")

    for x in sauts:
        verdicts.append(
            f"Saut le {x['date']} : {x['residuel']:+,.2f} € sans flux "
            f"({x['residuel_pct']:+.2%}). À déclarer comme apport à cette date."
        )

    # --- Le miroir : les flux que la valeur n'a pas suivis ---
    fantomes = metrics.fluxs_sans_effet(dates, valeurs, fluxs)
    if fantomes:
        log.info("")
        log.info("  ── Et à l'envers ──")
        log.info("")
        log.info("  %d flux enregistré(s) que la valeur n'a PAS suivi(s) :",
                 len(fantomes))
        for x in fantomes:
            log.info("    %s : flux de %s €, la valeur ne bouge que de %s € "
                     "(écart %s €, %+.2f %%)",
                     x["date"], _fmt(x["flux"]), _fmt(x["apres"] - x["avant"]),
                     _fmt(abs(x["residuel"])), x["residuel_pct"] * 100)
            verdicts.append(
                f"Le {x['date']}, un flux de {x['flux']:,.2f} € est enregistré et "
                f"la valeur ne suit pas (écart de {abs(x['residuel']):,.2f} €, "
                f"{abs(x['residuel_pct']):.2%}). Ligne en trop, mauvaise date, ou "
                "valorisation manquante : votre relevé tranchera.".replace(",", " ")
            )


def recalculer_twr(snaps: pd.DataFrame) -> None:
    _titre("LE TWR, RECALCULÉ SUR VOS DONNÉES")

    if snaps.empty or "patrimoine_investi_eur" not in snaps.columns:
        log.info("  Pas de quoi calculer.")
        return

    try:
        apports = db.lire(db.T_APPORTS)
    except Exception as exc:
        log.info("  Apports illisibles : %s", exc)
        return

    flux = {}
    if not apports.empty and {"date", "sens", "montant_eur"} <= set(apports.columns):
        d = pd.to_datetime(apports["date"], errors="coerce")
        s = apports["sens"].astype(str).str.strip().str.lower().map(
            {"apport": 1.0, "ajout": 1.0, "retrait": -1.0})
        m = pd.to_numeric(apports["montant_eur"], errors="coerce")
        for dd, ss, mm in zip(d, s, m):
            if pd.isna(dd) or ss is None or pd.isna(mm):
                continue
            flux[dd.date()] = flux.get(dd.date(), 0.0) + ss * float(mm)

    dates = snaps["Date_DT"].tolist()
    valeurs = pd.to_numeric(snaps["patrimoine_investi_eur"], errors="coerce").fillna(0).tolist()
    # Par période, pas par date exacte : la série de la v1 est mensuelle jusqu'en
    # avril 2026, et un versement tombé entre deux snapshots serait perdu.
    fluxs = metrics.flux_par_periode([d.date() for d in dates], flux)

    rendements = metrics.rendements_periode(valeurs, fluxs)
    colonne_r = [0.0] + list(rendements)

    try:
        par_an = metrics.twr_par_annee(dates, colonne_r)
    except Exception as exc:
        log.info("  twr_par_annee a échoué : %s", exc)
        return

    lignes = []
    for annee in sorted(par_an):
        idx = [i for i, d in enumerate(dates) if d.year == annee]
        if not idx:
            continue
        v0 = valeurs[idx[0]]
        v1 = valeurs[idx[-1]]
        f = sum(fluxs[i] for i in idx)
        lignes.append({
            "annee": int(annee),
            "valeur_debut": v0,
            "valeur_fin": v1,
            "apports": f,
            "TWR": par_an[annee] * 100,
            "naif_%": ((v1 / v0 - 1) * 100) if v0 else 0.0,
            "ecart_pts": ((v1 / v0 - 1) - par_an[annee]) * 100 if v0 else 0.0,
        })

    df = pd.DataFrame(lignes)
    log.info("  La colonne « écart » est ce que vos versements comptent à tort")
    log.info("  comme du rendement quand on oublie de les retirer :")
    log.info("")
    _montre(df, max_lignes=20)

    log.info("")
    log.info("  Note : « TWR » utilise vos apports tels qu'ils sont en base.")
    log.info("  « naif » est le calcul fin/début - 1, celui de la v1.")
    return df


# ===========================================================================
# Verdict
# ===========================================================================

def reconcilier_apports(hist: pd.DataFrame, apports: pd.DataFrame) -> None:
    """Compare le cumul de la v1 et le total de la v2.

    La v1 porte une colonne `Total_Apports_nets` — un cumul qu'elle tient
    elle-même. Si ce cumul ne rejoint pas la somme des apports de la v2, c'est
    qu'il manque des lignes, et on sait combien.
    """
    _titre("RÉCONCILIATION DES APPORTS v1 → v2")

    if hist is None or hist.empty or "Montant €" not in hist.columns:
        log.info("  Historique v1 illisible.")
        return

    types = hist["Type"].fillna("").astype(str).str.lower()
    est_apport = types.str.contains("ajout|apport", regex=True)
    est_retrait = types.str.contains("retrait", regex=True)

    montants = pd.to_numeric(hist["Montant €"], errors="coerce").fillna(0.0)
    brut_v1 = float(montants[est_apport].sum())
    retraits_v1 = float(montants[est_retrait].sum())
    net_v1 = brut_v1 - retraits_v1

    log.info("  V1 — Historique :")
    log.info("    apports                : %s €  (%d lignes)",
             _fmt(brut_v1), int(est_apport.sum()))
    log.info("    retraits               : %s €  (%d lignes)",
             _fmt(retraits_v1), int(est_retrait.sum()))
    log.info("    NET enregistré         : %s €", _fmt(net_v1))
    if "Total_Apports_nets" in hist.columns:
        cumul = pd.to_numeric(hist["Total_Apports_nets"], errors="coerce").dropna()
        if not cumul.empty:
            log.info("    cumul v1 en fin de table: %s €", _fmt(float(cumul.iloc[-1])))

    if apports is None or apports.empty or "montant_eur" not in apports.columns:
        log.info("")
        log.info("  V2 — aucun apport.")
        return

    m2 = pd.to_numeric(apports["montant_eur"], errors="coerce").fillna(0.0)
    sens2 = apports["sens"].astype(str).str.strip().str.lower()
    brut_v2 = float(m2[sens2 == "apport"].sum())
    retraits_v2 = float(m2[sens2 == "retrait"].sum())
    net_v2 = brut_v2 - retraits_v2

    log.info("")
    log.info("  V2 — pf2_apports :")
    log.info("    apports                : %s €  (%d lignes)",
             _fmt(brut_v2), int((sens2 == "apport").sum()))
    log.info("    retraits               : %s €  (%d lignes)",
             _fmt(retraits_v2), int((sens2 == "retrait").sum()))
    log.info("    NET enregistré         : %s €", _fmt(net_v2))

    ecart = net_v1 - net_v2
    log.info("")
    log.info("  ▸ ÉCART v1 - v2          : %s €", _fmt(ecart))

    if abs(ecart) < 1.0:
        log.info("    Les deux se rejoignent. Rien à corriger.")
        return

    if ecart > 0:
        verdicts.append(
            f"Il manque {ecart:,.2f} € d'apports dans la v2 : la v1 en compte "
            f"{net_v1:,.2f} € nets, la v2 {net_v2:,.2f} €. Un versement — ou "
            "plusieurs — n'a pas été importé.".replace(",", " ")
        )
    else:
        verdicts.append(
            f"La v2 compte {-ecart:,.2f} € d'apports DE PLUS que la v1. "
            "Vérifiez les doublons.".replace(",", " ")
        )


def reconcilier_capital(hist: pd.DataFrame) -> None:
    """Chaque apport a-t-il fait bouger le capital investi de la v1 ?

    `Projections.Capital investi` est le cumul que la v1 tenait elle-même, en
    dollars — la même unité que la colonne `Montant $` de `Historique`. C'est
    donc un juge indépendant de chaque ligne du journal : si le capital ne bouge
    pas alors qu'un versement est enregistré, c'est que le versement n'a pas eu
    lieu, ou qu'il est noté deux fois.

    CE QUE CE CONTRÔLE A TROUVÉ, sur les données réelles : le 29/04/2024, un
    apport de 10 800 € (11 579,85 $) est enregistré — et le capital investi
    RECULE de 500 $ ce mois-là. Les autres gros versements, eux, le font bien
    bouger : +8 889 $ en mai 2024 pour 9 161 $ d'apports, +11 019 $ en juillet
    2024 pour 11 554 $. Le signal est donc net, pas noyé dans le bruit.

    Et pour cause : c'est la même conclusion que trois autres mesures — la valeur
    du portefeuille ne bouge pas de 11 848 $ en avril 2024, la trésorerie reste
    figée à 22 690 $, et le TWR que la v1 affiche pour 2024 (+15,21 %) est
    incompatible avec ce flux (il donnerait −24,9 %).

    Le contrôle est générique : il ne sait rien d'avril 2024, il compare deux
    colonnes et signale les écarts. C'est ce qui manquait aux deux rounds
    précédents, où je cherchais la bonne ligne à la main.
    """
    _titre("CHAQUE APPORT A-T-IL BOUGÉ LE CAPITAL INVESTI ?")

    if hist is None or hist.empty or "Montant $" not in hist.columns:
        log.info("  Historique v1 illisible.")
        return

    try:
        proj = lire_v1(V1_PROJECTIONS)
    except Exception as exc:
        log.info("  Table « Projections » illisible (%s). Contrôle sauté.", exc)
        return
    if proj is None or proj.empty or "Capital investi" not in proj.columns:
        log.info("  Pas de colonne « Capital investi ». Contrôle sauté.")
        return

    j = hist.copy()
    # `dates.parser`, jamais `pd.to_datetime` : le journal v1 est en jj/mm/aaaa,
    # et l'appel direct a déjà produit deux bugs de diagnostic dans ce projet.
    j["_d"] = dates.parser(j["Date"])
    j = j.dropna(subset=["_d"])
    types = j["Type"].fillna("").astype(str).str.lower()
    signe = pd.Series(0.0, index=j.index)
    signe[types.str.contains("ajout|apport")] = 1.0
    signe[types.str.contains("retrait")] = -1.0
    j["flux"] = pd.to_numeric(j["Montant $"], errors="coerce").fillna(0.0) * signe
    j = j[j["flux"] != 0.0]

    p = proj.copy()
    p["_d"] = dates.parser(p["Date"])
    p = p.dropna(subset=["_d"]).sort_values("_d")
    p["_d"] = p["_d"].dt.normalize()
    p = p.drop_duplicates(subset=["_d"], keep="last").reset_index(drop=True)
    p["cap"] = pd.to_numeric(p["Capital investi"], errors="coerce")
    p = p.dropna(subset=["cap"]).reset_index(drop=True)

    if len(p) < 2:
        log.info("  Trop peu de points pour comparer.")
        return

    lignes = []
    for i in range(1, len(p)):
        deb, fin = p.loc[i - 1, "_d"], p.loc[i, "_d"]
        ecart_jours = (fin - deb).days
        if ecart_jours < 20 and i != len(p) - 1:
            continue
        flux = float(j.loc[(j["_d"] > deb) & (j["_d"] <= fin), "flux"].sum())
        if abs(flux) < 2000.0:
            continue                      # trop petit pour conclure quoi que ce soit
        delta = float(p.loc[i, "cap"] - p.loc[i - 1, "cap"])
        residuel = delta - flux
        if abs(residuel) < 2000.0:
            continue
        lignes.append({
            "periode": f"{deb.date()} → {fin.date()}",
            "flux_apports": flux,
            "capital_bouge_de": delta,
            "non_enregistre": residuel,
        })

    if not lignes:
        log.info("  Aucun écart : chaque apport a fait bouger le capital. ✓")
        return

    log.info("  %d période(s) où un apport enregistré n'a pas bougé le capital :",
             len(lignes))
    log.info("")
    _montre(pd.DataFrame(lignes), max_lignes=20)
    log.info("")
    log.info("  Un apport qui n'augmente pas le capital investi n'a pas eu lieu —")
    log.info("  ou il figure deux fois. C'est ce chiffre qu'il faut corriger dans")
    log.info("  🪙 Mouvements de fonds, pas le total de l'année.")
    for l in lignes:
        verdicts.append(
            f"Entre {l['periode'].replace(' → ', ' et ')}, {l['flux_apports']:,.0f} $ "
            f"d'apports sont enregistrés mais le capital investi varie de "
            f"{l['capital_bouge_de']:,.0f} $ : un versement de "
            f"{abs(l['non_enregistre']):,.0f} $ n'a pas eu lieu.".replace(",", " ")
        )


def requete_sql() -> None:
    """La question à laquelle seul le tableau de bord peut répondre."""
    _titre("LA LISTE DES TABLES — une requête, quinze secondes")

    log.info("  Je n'ai pas pu énumérer les tables du projet : la clé publique")
    log.info("  n'a pas accès à cet inventaire (HTTP 401). Les noms que j'ai")
    log.info("  essayés n'existent pas :")
    log.info("")
    log.info("      Valuation, Valorisation, Valeur, Portefeuille, Snapshot,")
    log.info("      Patrimoine, Evolution, Suivi, Performance, Mensuel,")
    log.info("      Historique_Mensuel, Mois, Vue, Tableau")
    log.info("")
    log.info("  Aucun n'existe. Donc vos 180 valorisations sont SOIT dans une")
    log.info("  table dont je n'ai pas trouvé le nom, SOIT nulle part — et dans")
    log.info("  ce cas la v1 calculait sa performance à la volée, sans les")
    log.info("  stocker.")
    log.info("")
    log.info("  ▸ Ouvrez Supabase → SQL Editor → New query, collez ceci,")
    log.info("    exécutez, et envoyez-moi le résultat :")
    log.info("")
    log.info("      select table_name, (select count(*) from pg_class")
    log.info("             where relname = table_name) as existe")
    log.info("      from information_schema.tables")
    log.info("      where table_schema = 'public'")
    log.info("      order by table_name;")
    log.info("")
    log.info("  Ou, sans SQL : Supabase → Table Editor → la colonne de gauche")
    log.info("  liste toutes les tables. Une capture d'écran suffit.")
    log.info("")
    log.info("  C'est la seule chose qui me manque. Je ne peux pas la deviner,")
    log.info("  et je n'essaierai pas : mes deux dernières hypothèses étaient")
    log.info("  fausses toutes les deux.")


def bloc_a_envoyer(tables: list[str], hist: pd.DataFrame, tr: pd.DataFrame,
                   snaps: pd.DataFrame, apports: pd.DataFrame) -> None:
    """Le résumé que je lis. Court, chiffré, sans mise en forme."""
    _titre("À COPIER-COLLER — envoyez-moi exactement ceci")

    L: list[str] = [f"DIAGNOSTIC-V3 {dt.date.today().isoformat()}",
                    f"connexion={'ok' if connexion_ok else 'ECHEC'}"]
    if not connexion_ok:
        for x in L:
            log.info("  %s", x)
        return

    L.append("tables=" + "|".join(tables))

    if hist is not None and not hist.empty:
        L.append(f"v1.Historique: {len(hist)} lignes, {_date_range(hist)}")
        L.append("  colonnes=" + "|".join(map(str, hist.columns)))
        if "Type" in hist.columns:
            for valeur, n in hist["Type"].fillna("(vide)").astype(str).value_counts().items():
                L.append(f"  TYPE {valeur!r} = {n}")
            bas = hist["Type"].fillna("").astype(str).str.lower()
            reste = hist[~bas.str.contains("ajout|apport|retrait", regex=True)]
            if not reste.empty:
                L.append(f"  HORS-APPORT: {len(reste)} lignes")
                for _, ligne in reste.iterrows():
                    L.append("    " + " | ".join(_fmt(ligne[c]) for c in hist.columns))

    if tr is not None and not tr.empty:
        L.append(f"v1.Transaction: {len(tr)} lignes, {_date_range(tr)}")
        L.append("  colonnes=" + "|".join(map(str, tr.columns)))

    if snaps is not None and not snaps.empty:
        L.append(f"v2.snapshots: {len(snaps)} lignes, {_date_range(snaps)}")
        if "patrimoine_investi_eur" in snaps.columns:
            v = pd.to_numeric(snaps["patrimoine_investi_eur"], errors="coerce").dropna()
            L.append(f"  valeur: {v.min():.2f} -> {v.max():.2f}")
            # Les 5 plus gros sauts, avec l'apport du jour : c'est la ligne qui
            # dit si un mouvement interne a ete compte comme du rendement.
            s2 = snaps.copy()
            s2["_d"] = pd.to_datetime(s2["date"], errors="coerce")
            s2 = s2.dropna(subset=["_d"]).sort_values("_d").reset_index(drop=True)
            flux = {}
            if apports is not None and not apports.empty and \
                    {"date", "sens", "montant_eur"} <= set(apports.columns):
                dd = pd.to_datetime(apports["date"], errors="coerce")
                ss = apports["sens"].astype(str).str.strip().str.lower().map(
                    {"apport": 1.0, "ajout": 1.0, "retrait": -1.0})
                mm = pd.to_numeric(apports["montant_eur"], errors="coerce")
                for x, y, z in zip(dd, ss, mm):
                    if pd.isna(x) or y is None or pd.isna(z):
                        continue
                    flux[x.date()] = flux.get(x.date(), 0.0) + y * float(z)
            sauts = []
            for i in range(1, len(s2)):
                v0 = pd.to_numeric(s2.loc[i-1, "patrimoine_investi_eur"], errors="coerce")
                v1 = pd.to_numeric(s2.loc[i, "patrimoine_investi_eur"], errors="coerce")
                if pd.isna(v0) or pd.isna(v1) or not v0:
                    continue
                sauts.append((abs(v1/v0 - 1), s2.loc[i, "_d"].date(), v0, v1,
                              flux.get(s2.loc[i, "_d"].date(), 0.0)))
            sauts.sort(reverse=True)
            for _, jour, v0, v1, f in sauts[:5]:
                L.append(f"  MOUVEMENT {jour}: {v0:.2f} -> {v1:.2f} "
                         f"({(v1/v0-1)*100:+.2f}%), apport_du_jour={f:.2f}")

    if derniers_sauts:
        L.append(f"SautsSANS_FLUX={len(derniers_sauts)}, "
                 f"total={sum(x['residuel'] for x in derniers_sauts):.2f}")
        for x in derniers_sauts:
            L.append(f"  {x['date']} {x['avant']:.2f} -> {x['apres']:.2f} "
                     f"flux={x['flux']:.2f} sans_flux={x['residuel']:+.2f} "
                     f"({x['residuel_pct']:+.2%})")
    else:
        L.append("SautsSANS_FLUX=aucun")

    if apports is not None and not apports.empty:
        m = pd.to_numeric(apports["montant_eur"], errors="coerce").fillna(0)
        L.append(f"v2.apports: {len(apports)} lignes, total {m.sum():.2f}")
        if "date" in apports.columns:
            an = pd.to_datetime(apports["date"], errors="coerce").dt.year
            for a, g in apports.assign(_a=an).groupby("_a"):
                gm = pd.to_numeric(g["montant_eur"], errors="coerce").fillna(0)
                L.append(f"  {int(a) if pd.notna(a) else '?'}: {len(g)} lignes, {gm.sum():.2f}")

    L.append(f"VERDICTS={len(verdicts)}")
    for i, v in enumerate(verdicts, 1):
        L.append(f"  {i}. {v[:200]}")

    log.info("")
    for x in L:
        log.info("  %s", x)


def conclure() -> None:
    _titre("VERDICT")
    if not verdicts:
        log.info("  Rien d'anormal détecté.")
        return
    for i, v in enumerate(verdicts, 1):
        log.info("")
        log.info("  %d. %s", i, v)
    log.info("")


def main() -> int:
    log.info("Diagnostic v3 — %s (lecture seule)",
             dt.datetime.now().strftime("%d/%m/%Y %H:%M"))

    tables: list[str] = []
    hist = tr = snaps = apports = pd.DataFrame()

    try:
        tables = _liste_tables()
    except Exception:
        pass
    sonder_connexion()

    if connexion_ok:
        if not tables:
            try:
                tables = _liste_tables()
            except Exception:
                tables = []

        for nom in ("Historique", "Transaction"):
            df = examiner_v1(nom)
            if nom == "Historique":
                hist = df
            else:
                tr = df

        snaps = examiner_snapshots()

        try:
            apports = db.lire(db.T_APPORTS)
        except Exception:
            apports = pd.DataFrame()

        examiner_sauts(snaps, apports)
        reconcilier_apports(hist, apports)
        reconcilier_capital(hist)
        recalculer_twr(snaps)
        requete_sql()
        bloc_a_envoyer(tables, hist, tr, snaps, apports)

        _titre("ÉTAT DE LA v2")
        for nom, table in (("transactions", db.T_TRANSACTIONS),
                           ("apports", db.T_APPORTS),
                           ("inflation", db.T_INFLATION)):
            try:
                df = db.lire(table)
                log.info("  %-14s %3d ligne(s)   %s", nom, len(df), _date_range(df))
            except Exception as exc:
                log.info("  %-14s ERREUR (%s)", nom, str(exc)[:80])

        try:
            infl = db.inflation()
            if not infl.empty:
                log.info("")
                log.info("  Inflation : %s", ", ".join(
                    f"{int(r['Annee'])}:{float(r['Inflation']):+.2f}%"
                    for _, r in infl.sort_values("Annee").iterrows()))
        except Exception as exc:
            log.info("  Inflation illisible : %s", str(exc)[:80])

    conclure()
    return 0


if __name__ == "__main__":
    sys.exit(main())
