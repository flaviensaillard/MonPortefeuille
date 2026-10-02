"""MonPortefeuille 2 — Tableau de bord.

Ce que cette page corrige par rapport à la v1 :

- La **performance est donnée en onces d'or**, pas seulement en euros. C'est
  l'étalon de Gave : « l'or montera tant que les monnaies ne redeviendront pas des
  réserves de valeur ». La v1 collectait une colonne `Montant Or` à chaque apport
  et ne s'en servait jamais.

- L'**épargne de précaution est affichée séparément** du portefeuille investi. La
  v1 la mélangeait dans l'assiette de rééquilibrage, ce qui faussait toutes les
  dérives.

- Aucune valeur de repli. Si un cours ou un taux manque, un bandeau le dit.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from core import prices
from core import session as S
from core import ui
from core.models import Perimetre

st.set_page_config(page_title="Mon Portefeuille", page_icon="📊", layout="wide")

st.title("📊 Tableau de bord")

# `charger()` est mémoïsé 5 minutes (`st.cache_data(ttl=300)`). Après un import,
# l'application continuait donc à servir les anciennes transactions — d'où un
# message d'erreur qui survivait à sa propre correction. Le bouton vide le cache
# et relance le calcul.
col_titre, col_rafraichir = st.columns([5, 1])
with col_rafraichir:
    if st.button("🔄 Rafraîchir", width="stretch",
                 help="Relecture depuis Supabase. À utiliser après un import."):
        S.vider_cache()
        st.rerun()

ctx = S.charger()

# Fraicheur des donnees. Un bandeau d'anomalie qui survit a sa correction vient
# presque toujours d'un serveur qui tourne sur une vieille version du code : la
# date ci-dessous le montre immediatement.
if ctx.importe_le:
    st.caption(f"Données importées le {ctx.importe_le[:16].replace('T', ' ')} — "
               f"si cette date est anterieure a votre dernier import, "
               f"cliquez sur 🔄 Rafraîchir.")

for err in ctx.erreurs:
    st.error(err)

ui.appareil({"tables": not ctx.tables_absentes})
ui.bandeau_erreurs(ctx.echecs_cours, "cours")
ui.bandeau_erreurs(ctx.echecs_fx, "taux de change")

# Transactions incohérentes : on prévient sans bloquer. Une ligne douteuse ne
# doit pas vous priver de la vue d'ensemble de votre patrimoine.
if ctx.anomalies_transactions:
    # Tickers concernés, déduits des messages. Une vente sans position ne dit pas
    # ce qui manque : montrer les lignes du titre permet de le voir tout de suite.
    tickers_concernes = sorted({
        mot for a in ctx.anomalies_transactions for mot in a.split()
        if any(mot == t.ticker for t in ctx.transactions)
    })
    with st.expander(
        f"⚠️ {len(ctx.anomalies_transactions)} transaction(s) incohérente(s) "
        f"dans vos données", expanded=True
    ):
        st.warning(
            "Ces lignes ont été ignorées dans le calcul des positions. "
            "Vos chiffres sont donc partiels — corrigez-les dans la v1 "
            "puis relancez l'import."
        )
        for a in ctx.anomalies_transactions:
            st.markdown(f"- {a}")

        if tickers_concernes:
            st.markdown("---")
            st.markdown(
                "**Ce que contient votre base pour ce(s) titre(s).** "
                "Une vente ne peut aboutir que si un achat la précède, et en "
                "quantité suffisante. Comparez les dates."
            )
            detail = pd.DataFrame([{
                "Ticker": t.ticker,
                "Sens": t.type,
                "Date": t.date.strftime("%d/%m/%Y"),
                "Quantité": t.quantite,
                "Cours": t.cours,
                "Devise": t.devise,
            } for t in sorted(
                (t for t in ctx.transactions if t.ticker in tickers_concernes),
                key=lambda t: (t.date, 0 if t.est_achat else 1),
            )])
            st.dataframe(detail, hide_index=True, width="stretch")
            st.caption(
                "Trié par date, achats avant ventes. Si un achat apparaît après "
                "une vente, c'est la date qu'il faut corriger dans la v1."
            )

if ctx.erreurs:
    st.stop()

# ---------------------------------------------------------------------------
# Patrimoine
# ---------------------------------------------------------------------------
st.caption(f"Au {dt.date.today().strftime('%d/%m/%Y')}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Patrimoine total",
          ui.usd_eur(ctx.patrimoine_total_usd, ctx.patrimoine_total_eur),
          help="Investi + épargne de précaution + compte courant ($ avec indication €).")
c2.metric("Portefeuille investi",
          ui.usd_eur(ctx.total_investi_usd, ctx.total_investi_eur),
          help="Actifs stratégiques soumis à l'allocation cible ($ avec indication €).")
c3.metric("Épargne de précaution",
          ui.usd_eur(ctx.total_precaution_usd, ctx.total_precaution_eur),
          help="Réserve CHF / CNY disponible en 5 minutes. Jamais rééquilibrée.")
c4.metric("Compte courant",
          ui.usd_eur(ctx.total_courant_usd, ctx.total_courant_eur),
          help="Liquidités courantes hors portefeuille d'investissement.")

# ---------------------------------------------------------------------------
# L'étalon de Gave
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🪙 La mesure qui compte", help="Performance exprimée en onces d'or, l'étalon de valeur de Charles Gave.")

g1, g2, g3 = st.columns(3)
if ctx.cours_or:
    g1.metric("Cours de l'or", f"{ctx.cours_or:,.0f} $/oz",
              help=f"Contrat à terme {prices.TICKER_OR} (COMEX) : Yahoo ne fournit plus le spot.")
else:
    g1.metric("Cours de l'or", "—")

if ctx.equivalent_or_oz is not None:
    g2.metric("Portefeuille investi en or", f"{ctx.equivalent_or_oz:,.2f} oz",
              help="Combien d'onces d'or votre portefeuille investi achète aujourd'hui.")
else:
    g2.metric("Portefeuille investi en or", "—")

perf_or = None
if not ctx.snapshots.empty and "equivalent_or_oz" in ctx.snapshots.columns:
    perf_or = S.twr_en_or_portefeuille(ctx)

# `perf_eur` : le TWR, PAS `derniere / premiere - 1`. Ce dernier comptait vos
# versements comme du rendement — +628 % cumule sur le portefeuille reel, la
# ou la strategie en avait produit une fraction. Voyez `twr_portefeuille`.
perf_usd = S.twr_portefeuille(ctx)
if perf_or is not None:
    g3.metric("Performance en or", ui.pct(perf_or, signe=True),
              delta=ui.pct(perf_usd, signe=True) if perf_usd is not None else None,
              help="Depuis le premier snapshot. Le delta compare à la performance en dollars ($).")
elif perf_usd is not None:
    g3.metric("Performance ($)", ui.pct(perf_usd, decimales=2, signe=True))
else:
    g3.metric("Performance", "—", help="Aucun snapshot enregistré.")

if perf_or is not None and perf_or < 0:
    st.warning(
        f"**Votre portefeuille perd de l'or.** En {ui.pct(perf_or, signe=True)}, "
        "vous achetez moins d'onces qu'au début. Même si la performance en dollars "
        "est positive, vous vous appauvrissez dans l'étalon qui compte."
    )

# ---------------------------------------------------------------------------
# Allocation par poche
# ---------------------------------------------------------------------------
st.divider()
st.subheader("⚖️ Allocation par poche")

lignes = []
for e in ctx.ecarts:
    lignes.append({
        "Poche": e.poche_nom,
        "Cible": ui.pct(e.poids_cible),
        "Réel": ui.pct(e.poids_reel),
        "Écart": ui.points(e.ecart_points),
        "Bande": f"±{e.bande * 100:.0f} pts",
        "Valeur ($ / €)": ui.usd_eur(e.valeur_usd, e.valeur_eur),
        "État": "🔴 hors bande" if e.hors_bande else "🟢 dans la bande",
    })

if lignes:
    df = ui.tableau(pd.DataFrame(lignes))

    hors = ctx.besoins_reequilibrage
    if hors:
        st.warning(
            f"**{len(hors)} poche(s) hors bande.** Voir la page Rééquilibrage pour "
            "les ordres proposés."
        )
    else:
        st.success("Toutes les poches sont dans leur bande de tolérance.")
else:
    st.info("Aucune position investie.")

# ---------------------------------------------------------------------------
# Épargne de précaution — rappel du rôle
# ---------------------------------------------------------------------------
if ctx.total_precaution_usd > 0 or ctx.total_precaution_eur > 0:
    st.divider()
    st.subheader("🏦 Épargne de précaution")
    mois = 6
    st.caption(
        f"{ui.usd_eur(ctx.total_precaution_usd, ctx.total_precaution_eur)} disponibles en 5 minutes. "
        f"Soit environ {ui.usd_eur(ctx.total_precaution_usd / mois, ctx.total_precaution_eur / mois, decimales=0)}/mois sur {mois} mois "
        "de dépenses — à ajuster selon votre besoin réel."
    )
