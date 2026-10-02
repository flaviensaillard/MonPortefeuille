"""Performance.

CORRECTIONS PAR RAPPORT À LA V1
-------------------------------
1. **Le TWR était un cumprod de ratios Dietz mensuels**, ce qui suppose que les
   apports arrivent en fin de période. On chaîne ici les rendements de
   sous-période, définition standard du TWR.

2. **Aucune performance n'était mesurée en or.** C'était pourtant la seule qui
   compte pour Gave, et la v1 collectait déjà les données pour la calculer.

3. **Aucun rendement pondéré par les flux (IRR).** Le TWR répond à « qu'a fait la
   stratégie », l'IRR à « qu'ai-je gagné avec mon calendrier d'apports ». Les deux
   sont nécessaires.

4. **L'inflation 2026 valait 0,00 %** dans la v1, ce qui rendait la performance
   réelle de l'année en cours égale à la nominale.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import streamlit as st

from core import metrics, session as S
from core import dates
from core import ui

st.set_page_config(page_title="Performance", page_icon="📈", layout="wide")
st.title("📈 Performance")

ctx = S.charger()
for err in ctx.erreurs:
    st.error(err)
ui.bandeau_erreurs(ctx.echecs_cours, "cours")
if ctx.erreurs:
    st.stop()

if ctx.snapshots.empty or len(ctx.snapshots) < 2:
    st.info("Il faut au moins deux snapshots pour calculer une performance.")
    st.stop()

# Série en DOLLARS ($) issue de `session.serie_performance(ctx)` :
# utilise `Actifs Stratégiques` et les variations de `Capital investi` de
# `Projections` (puis `flux_par_date` pour toute période postérieure).
flux_jour = S.flux_par_date(ctx.apports)
snaps, valeurs, flux = S.serie_performance(ctx)
if len(valeurs) < 2:
    st.info("Il faut au moins deux snapshots exploitables pour calculer une performance.")
    st.stop()

rendements = metrics.rendements_periode(valeurs, flux)
twr_total = metrics.twr(rendements)

jours = (snaps["Date"].iloc[-1] - snaps["Date"].iloc[0]).days
twr_ann = metrics.annualiser(twr_total, jours)

# ---------------------------------------------------------------------------
# Le TWR n'est juste que si les flux sont enregistrés
# ---------------------------------------------------------------------------
# Un TWR neutralise les versements — à condition de les connaître. Quand aucun
# apport n'est enregistré, le calcul se réduit à « fin / début » et l'épargne
# apparaît comme du rendement. On ne peut pas savoir qu'un versement a été
# oublié, mais on peut repérer le cas où c'est le plus probable, et le dire.
for alerte in metrics.controle_apports(valeurs, flux, jours):
    st.error("⚠️ " + alerte)

# --- Les sauts non expliqués, jour par jour ---
# DÉFAUT QUI A PRODUIT +26,2 % AU LIEU DE +4,10 %.
#
# Le 02/02/2026, la valeur investie passe de 55 639,93 € à 64 808,82 € alors que
# 200 € de flux sont enregistrés ce jour-là. Le TWR compte les 9 169 € restants
# comme du rendement : +16,5 % en une séance sur un portefeuille de quatre ETF.
#
# Le contrôle ci-dessus ne pouvait pas l'attraper : il ne se déclenche que
# lorsqu'AUCUN flux n'existe sur la période. Ici il y en a 38 — sur d'autres
# jours. Le trou est localisé, pas global.
sauts = metrics.sauts_non_expliques(
    [d.date() for d in snaps["Date"]], valeurs, flux
)

if sauts:
    st.divider()
    st.subheader("⚠️ Mouvements sans flux enregistré")
    st.caption(
        "Ces mouvements ne sont pas de la performance. Le chiffre plus haut les "
        "compte comme telle, donc il est trop flatteur. Corrigez-les, et il "
        "deviendra juste."
    )

    for saut in sauts:
        st.error(metrics.anomalie_saut(saut))

    flux_corrige = metrics.flux_corrige_des_sauts(flux, sauts)
    rendements_corriges = metrics.rendements_periode(valeurs, flux_corrige)
    twr_corrige = metrics.twr(rendements_corriges)

    c1, c2 = st.columns(2)
    c1.metric("TWR affiché ci-dessus", ui.pct(twr_total, signe=True),
              help="Ce qu'on obtient en prenant vos données telles quelles.")
    c2.metric("TWR une fois ces mouvements enregistrés",
              ui.pct(twr_corrige, signe=True),
              delta=ui.points((twr_corrige - twr_total) * 100, 2),
              help="Ce que vous auriez gagné si ces mouvements avaient été "
                   "saisis comme des apports.")

    st.info(
        "**Comment corriger.** Chaque saut est un mouvement réel qui n'a pas "
        "été saisi : un virement depuis le livret CHF, une position ajoutée à "
        "la main, ou un versement oublié. Déclarez-le comme un **apport** à sa "
        "date dans 🪙 Mouvements de fonds, et le chiffre se corrige tout seul. "
        "Pour ce portefeuille, c'est environ **"
        f"{ui.usd_eur(sum(s['residuel'] for s in sauts))}** à répartir sur "
        f"{len(sauts)} date(s)."
    )

# --- Les flux que la valeur n'a pas suivis ---
# Le miroir exact du bloc ci-dessus : là, la valeur montait sans flux ; ici, un
# flux est enregistré et la valeur ne suit pas. Le cas réel trouvé dans
# l'historique v1 : un apport de 10 800 € le 29/04/2024, et une valeur qui
# augmente de 23 € ce mois-là. Sans ce contrôle, 2024 s'affiche à −24,9 %.
fantomes = metrics.fluxs_sans_effet(
    [d.date() for d in snaps["Date"]], valeurs, flux
)

if fantomes:
    st.divider()
    st.subheader("⚠️ Apports que la valeur n'a pas suivis")
    st.caption(
        "Un versement est enregistré, mais la valeur du portefeuille ne bouge "
        "pas d'autant. C'est l'inverse du cas précédent, et le résultat est le "
        "même : le chiffre plus haut est faux."
    )

    for f in fantomes:
        st.error(metrics.anomalie_flux_sans_effet(f))

        # Nommer les versements de la période. Sans ça, le message donne un
        # montant et deux bornes, et il faut aller chercher soi-même : sur le cas
        # réel, la ligne fautive est datée du 29/04/2024 quand la période se
        # termine le 30/04. Autant la montrer.
        debut_p = f.get("date_avant")
        fin_p = f.get("date")
        if debut_p is not None and fin_p is not None and not ctx.apports.empty:
            ap = ctx.apports.copy()
            ap["_d"] = dates.parser(ap["date"])
            dedans = ap[(ap["_d"].dt.date > debut_p) & (ap["_d"].dt.date <= fin_p)]
            if not dedans.empty:
                detail = " · ".join(
                    f"**{ui.jour(r['_d'])}** — "
                    f"{'apport' if str(r['sens']).lower().startswith('app') else 'retrait'} "
                    f"de {ui.eur(float(r['montant_eur']))}"
                    for _, r in dedans.sort_values("_d").iterrows()
                )
                st.caption(f"Versements enregistrés sur cette période : {detail}")

    st.info(
        "**Deux causes possibles, et une seule est dans vos données.** Soit le "
        "versement n'a jamais eu lieu — une ligne saisie deux fois, ou une date "
        "erronée —, soit il a eu lieu, mais la valorisation de ce mois-là a été "
        "oubliée. Regardez votre relevé à cette date : la réponse y est en "
        "trente secondes. C'est la seule chose que je ne peux pas deviner."
    )

with st.expander("🔍 Traçabilité — ce sur quoi porte ce calcul", expanded=False):
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Snapshots", len(snaps))
    t2.metric("Période", f"{(jours / 365.25):.1f} ans")
    cap_total = (
        float(snaps["capital_investi_usd"].dropna().iloc[-1])
        if "capital_investi_usd" in snaps.columns and snaps["capital_investi_usd"].notna().any()
        else sum(abs(f) for f in flux)
    )
    t3.metric("Capital investi ($ / €)", ui.usd_eur(cap_total))
    annees_couvertes = sorted({int(a) for a in snaps["Date"].dt.year})
    inflation_dict = S.inflation_dict(ctx)
    manquantes = [a for a in annees_couvertes if a not in inflation_dict]
    t4.metric("Inflation connue", f"{len(annees_couvertes) - len(manquantes)}/{len(annees_couvertes)} ans")

    if not ctx.apports.empty:
        st.caption(
            f"{len(ctx.apports)} ligne(s) dans pf2_apports, "
            f"du {dates.parser(ctx.apports['date']).min().date()} "
            f"au {dates.parser(ctx.apports['date']).max().date()}."
        )
    else:
        st.caption(
            "**pf2_apports est vide.** Le TWR ne peut pas corriger vos "
            "versements : il les compte comme du rendement. C'est la cause la "
            "plus fréquente d'un chiffre trop flatteur."
        )
        st.caption(
            "Lancez « Import des données v1 » (onglet Actions) avec "
            "`dry_run = false`, puis « Diagnostic » pour vérifier."
        )

# ---------------------------------------------------------------------------
# Indicateurs principaux
# ---------------------------------------------------------------------------
st.subheader("Ce que la stratégie a produit")

c1, c2, c3 = st.columns(3)
c1.metric("TWR cumulé ($)", ui.pct(twr_total, decimales=2, signe=True),
          help="Time-Weighted Return en dollars ($) : neutralise l'effet de vos apports.")
c2.metric("TWR annualisé ($)", ui.pct(twr_ann, decimales=2, signe=True),
          help=f"Sur {jours} jours ({jours / 365.25:.1f} ans).")
c3.metric("Volatilité annualisée", ui.pct(metrics.volatilite(rendements), decimales=2, signe=True),
          help="Écart-type des rendements de sous-période annualisé.")

# ---------------------------------------------------------------------------
# Les trois lectures de la même performance
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Trois lectures de la même période")
st.caption(
    "Une performance n'a de sens que rapportée à un étalon. Gave en retient un : "
    "l'or. « L'or montera tant que les monnaies ne redeviendront pas des réserves "
    "de valeur. »"
)

# 1. En dollars ($).
perf_usd = twr_total

# 2. En pouvoir d'achat réel (hors inflation).
inflation = S.inflation_dict(ctx)
d0, d1 = snaps["Date"].iloc[0].date(), snaps["Date"].iloc[-1].date()
infl_periode = metrics.inflation_cumulee(inflation, d0, d1)
perf_reel = (1.0 + perf_usd) / infl_periode - 1.0 if infl_periode > 0 else None

# 3. En onces d'or — corrigée des apports, comme les deux autres lectures.
perf_or = S.twr_en_or_portefeuille(ctx)

d1, d2, d3 = st.columns(3)
d1.metric("En dollars ($)", ui.pct(perf_usd, decimales=2, signe=True))
d2.metric("Hors inflation (réelle)", ui.pct(perf_reel, decimales=2, signe=True) if perf_reel is not None else "—",
          help="Déflaté par l'inflation officielle.")
d3.metric("En onces d'or", ui.pct(perf_or, decimales=2, signe=True) if perf_or is not None else "—",
          help="L'étalon de Gave.")

if perf_or is not None and perf_or < perf_usd:
    st.warning(
        f"Votre portefeuille a gagné {ui.pct(perf_usd, decimales=2, signe=True)} en dollars mais "
        f"**{ui.pct(perf_or, decimales=2, signe=True)} en or**. La monnaie a fait le travail à "
        "votre place : en étalon de valeur réel, vous avez moins gagné qu'en nominal."
    )

# ---------------------------------------------------------------------------
# Rendement pondéré par les flux
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Ce que vous, personnellement, avez gagné")

flux_irr = []
for i, d in enumerate(snaps["Date"]):
    if i == 0:
        flux_irr.append((d.date(), -valeurs[0]))
    elif i == len(valeurs) - 1:
        flux_irr.append((d.date(), valeurs[-1]))
    else:
        flux_irr.append((d.date(), -flux[i]))

taux_irr = metrics.irr(flux_irr)
c1, c2 = st.columns(2)
c1.metric("IRR (rendement pondéré)", ui.pct(taux_irr, signe=True) if taux_irr is not None else "—",
          help="Tient compte de votre calendrier d'apports réel.")
c2.metric("Écart TWR / IRR",
          ui.points((taux_irr - twr_ann) * 100, 2) if taux_irr is not None else "—",
          help="Positif : vos apports ont été bien placés. Négatif : vous avez "
               "alimenté le portefeuille au mauvais moment.")

# ---------------------------------------------------------------------------
# Par année
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Par année")

snaps["Annee"] = snaps["Date"].dt.year

# Le rendement de chaque sous-période, corrigé des flux. `rendements_periode`
# renvoie n-1 valeurs pour n valeurs : la i-ème est le rendement qui MÈNE à la
# ligne i. On la range donc dans la ligne d'arrivée.
snaps["Rendement"] = [0.0] + metrics.rendements_periode(valeurs, flux)

# Rendement de chaque annee = chainage des sous-periodes qui se terminent
# dans cette annee. C'est la definition standard, et la seule qui neutralise
# les apports. Le calcul precedent faisait `derniere / premiere - 1` : il
# comptait vos versements comme du rendement.
rendements_annuels = metrics.twr_par_annee(
    [d.date() for d in snaps["Date"]], snaps["Rendement"].tolist()
)

snaps["_val_usd"] = valeurs
bilan_par_annee = snaps.groupby("Annee").last()

lignes = []
for annee, perf in rendements_annuels.items():
    infl = inflation.get(annee)
    reel = (1 + perf) / (1 + infl) - 1.0 if infl is not None else None
    val_fin_u = float(bilan_par_annee.loc[annee, "_val_usd"]) if annee in bilan_par_annee.index else None
    lignes.append({
        "Année": int(annee),
        "Performance ($)": ui.pct(perf, decimales=2, signe=True),
        "Inflation": ui.pct(infl, decimales=2, signe=True) if infl is not None else "⚠️ non renseignée",
        "Réelle": ui.pct(reel, decimales=2, signe=True) if reel is not None else "—",
        "Valeur bilan ($ / €)": ui.usd_eur(val_fin_u) if val_fin_u is not None else "—",
    })

if lignes:
    ui.tableau(pd.DataFrame(lignes))
    st.caption(
        "Performances calculées en **dollars ($)** (hors effet de change EUR/USD), "
        "avec indication de la valeur bilan en euros. Note : en 2023 (9 mois, "
        "d'avril à décembre), la performance sur la période est de **+9,33 %** "
        "(soit **+12,68 %** en rythme annualisé sur 12 mois dans la v1)."
    )

    annees_sans_inflation = [
        a for a in sorted({int(x) for x in snaps["Annee"]}) if a not in inflation
    ]
    if annees_sans_inflation:
        st.warning(
            "**Années sans inflation renseignée** : "
            + ", ".join(str(a) for a in annees_sans_inflation)
            + ". La performance réelle de ces années ne peut pas être calculée. "
            "La v1 masquait ce trou en remplissant avec 0 %."
        )
else:
    st.info("Pas assez de données pour un détail annuel.")
