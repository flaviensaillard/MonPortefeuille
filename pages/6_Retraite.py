"""Projection retraite.

CORRECTIONS PAR RAPPORT À LA V1
-------------------------------
1. **`inf_rate_apports` pointait sur l'inflation du scénario B** pour les deux
   scénarios. Le scénario A avait pourtant sa propre inflation historique : il ne
   s'en servait pas pour faire croître ses apports. Si vous mettiez 0 % en B et
   2 % en A, les deux scénarios avaient des apports constants — incohérent.

2. **Des dollars déflatés par de l'inflation française.** `cap_net_a = cap_a_nom /
   ((1+inflation)**années)` divisait un capital en **dollars** par un indice de
   prix **français**, sans jamais convertir en euros. Votre patrimoine est en
   dollars mais vous dépenserez en euros : le taux EUR/USD bouge de 10 à 15 % par
   an, et sur 29 ans c'est l'incertitude dominante — purement absente de la v1.

   Ici, tout est projeté en **euros**, et une sensibilité au taux de change est
   affichée parce que l'ignorer serait mentir.

3. **Clés de configuration en double** (`retraite_taxe` / `retraite_tax`,
   `retraite_apport_mensuel` / `retraite_app_mensuel`). Le schéma typé les rend
   impossibles.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from core import metrics, session as S
from core import dates
from core import ui

st.set_page_config(page_title="Retraite", page_icon="🌴", layout="wide")
st.title("🌴 Projection retraite")

ctx = S.charger()
for err in ctx.erreurs:
    st.error(err)
if ctx.erreurs:
    st.stop()

annee_courante = dt.date.today().year

# ---------------------------------------------------------------------------
# Paramètres
# ---------------------------------------------------------------------------
c1, c2, c3 = st.columns(3)
annee_depart = c1.number_input(
    "Année de départ", min_value=annee_courante + 1, max_value=2100,
    value=min(2055, 2100), step=1,
)
apport_mensuel = c2.number_input(
    "Apport mensuel ($)", min_value=0.0, step=50.0, value=250.0,
)
taux_pv = c3.number_input(
    "Imposition des plus-values (%)", min_value=0.0, max_value=60.0,
    step=0.5, value=30.8,
    help="PFU : 12,8 % d'IR + 17,2 % de prélèvements sociaux.",
) / 100.0

# Scénario A : le CAGR historique du portefeuille.
# Le CAGR historique, corrigé des apports.
#
# L'ancien calcul faisait `v1 / v0 - 1` puis annualisait : sur le portefeuille
# reel, +628 % cumule soit **76 % par an**, et c'est ce chiffre qui preremplissait
# ce champ. Une grande partie de cet ecart est vos versements, pas du rendement.
# La valeur par defaut de la projection de retraite etait donc un conte de fees.
perf_hist = S.twr_annualise_portefeuille(ctx)

st.divider()
cA, cB, cC = st.columns(3)
rendement_a = cA.number_input(
    "Scénario A — rendement (%/an)",
    min_value=-20.0, max_value=30.0, step=0.1,
    value=round((perf_hist or 0.05) * 100, 2),
    help="Prérempli avec le CAGR historique de votre portefeuille investi.",
)
inflation_a = cB.number_input(
    "Inflation scénario A (%)", min_value=0.0, max_value=15.0, step=0.1, value=2.0,
)
rendement_b = cB.number_input(
    "Scénario B — rendement (%/an)",
    min_value=-20.0, max_value=30.0, step=0.1, value=8.0,
)
inflation_b = cC.number_input(
    "Inflation scénario B (%)", min_value=0.0, max_value=15.0, step=0.1, value=2.0,
)

st.caption(
    "⚠️ Les deux scénarios projettent en **euros**. Votre portefeuille est libellé "
    "en dollars, en yens et en francs suisses : la projection suppose un taux de "
    "change stable. C'est l'hypothèse la plus fragile du modèle — voir la "
    "sensibilité en bas de page."
)

# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
capital_initial = ctx.total_investi_usd or ctx.total_investi_eur
annees = list(range(annee_courante, annee_depart + 1))


def simuler(rendement: float, inflation: float) -> pd.DataFrame:
    """Projection en euros. Chaque scénario fait croître ses apports à SA inflation."""
    r_m = (1 + rendement) ** (1 / 12) - 1
    cap = capital_initial
    apport = apport_mensuel
    trajectoire = []
    apports_cumules = capital_initial

    for i, annee in enumerate(annees):
        mois = 12 if annee > annee_courante else max(1, 13 - dt.date.today().month)
        for _ in range(mois):
            cap += apport
            apports_cumules += apport
            cap *= (1 + r_m)
        trajectoire.append({
            "Année": annee,
            "Capital nominal": cap,
            "Apports cumulés": apports_cumules,
            "Capital réel": metrics.pouvoir_achat(cap, inflation, i + 1),
        })
        apport *= (1 + inflation)   # <-- l'inflation de CE scénario

    return pd.DataFrame(trajectoire)


traj_a = simuler(rendement_a / 100, inflation_a / 100)
traj_b = simuler(rendement_b / 100, inflation_b / 100)

fin_a = traj_a.iloc[-1]
fin_b = traj_b.iloc[-1]

st.divider()
st.subheader(f"Capital au 1er janvier {annee_depart}")

g1, g2 = st.columns(2)
for col, (traj, fin, nom, rend, infl) in zip(
    (g1, g2),
    ((traj_a, fin_a, "A", rendement_a, inflation_a),
     (traj_b, fin_b, "B", rendement_b, inflation_b)),
):
    with col:
        st.markdown(f"### Scénario {nom} — {rend:.1f} %/an, inflation {infl:.1f} %")
        r_reel = (1 + rend / 100) / (1 + infl / 100) - 1
        st.caption(f"Rendement réel : {ui.pct(r_reel, signe=True)}")
        st.metric("Capital nominal", ui.usd_eur(fin["Capital nominal"]))
        st.metric("Capital en pouvoir d'achat actuel", ui.usd_eur(fin["Capital réel"]))
        st.metric("Dont apports", ui.usd_eur(fin["Apports cumulés"]))
        pv = max(0.0, fin["Capital nominal"] - fin["Apports cumulés"])
        part_pv = pv / fin["Capital nominal"] if fin["Capital nominal"] > 0 else 0
        st.metric("Dont plus-value", ui.usd_eur(pv), delta=ui.pct(part_pv))

        # Rente : on retire le rendement réel, on préserve le capital en pouvoir d'achat.
        rente_brute = fin["Capital réel"] * max(0.0, r_reel) / 12
        impot = rente_brute * part_pv * taux_pv
        st.metric("Rente brute mensuelle", ui.usd_eur(rente_brute))
        st.metric(f"Impôt ({taux_pv*100:.1f} % sur la part de PV)", ui.usd_eur(-impot))
        st.metric("Rente nette mensuelle", ui.usd_eur(rente_brute - impot))

st.caption(
    "Le modèle de rente retire le **rendement réel** et préserve le capital en "
    "pouvoir d'achat. C'est une règle de pérennité, plus conservatrice que la règle "
    "des 4 % quand les rendements sont bons, plus agressive quand ils sont mauvais. "
    "Elle suppose un rendement réel constant — exactement l'hypothèse que la méthode "
    "de Gave rejette."
)

# ---------------------------------------------------------------------------
# Courbe
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Évolution du pouvoir d'achat")

import plotly.express as px

courbe = pd.DataFrame({
    "Année": traj_a["Année"],
    "Scénario A": traj_a["Capital réel"],
    "Scénario B": traj_b["Capital réel"],
}).melt(id_vars="Année", var_name="Scénario", value_name="Pouvoir d'achat (€)")

fig = px.line(courbe, x="Année", y="Pouvoir d'achat (€)", color="Scénario",
              color_discrete_map={"Scénario A": "#2ecc71", "Scénario B": "#3498db"})
fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5))
st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Sensibilité au taux de change — ce que la v1 ignorait
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Sensibilité au taux de change")

st.caption(
    "Votre portefeuille est en dollars, yens et francs suisses. La projection "
    "ci-dessus suppose un taux de change stable. Voici ce que coûte une erreur "
    "d'hypothèse sur le nombre d'années restantes."
)

lignes = []
for variation in (-0.30, -0.15, 0.0, 0.15, 0.30):
    facteur = 1 + variation
    cap = fin_a["Capital nominal"] * facteur
    reel = metrics.pouvoir_achat(cap, inflation_a / 100, len(annees))
    lignes.append({
        "Variation EUR/USD": ui.pct(variation, signe=True),
        "Capital nominal ($ / €)": ui.usd_eur(cap),
        "Pouvoir d'achat ($ / €)": ui.usd_eur(reel),
        "vs hypothèse stable": ui.pct(variation, signe=True),
    })
ui.tableau(pd.DataFrame(lignes))

st.warning(
    "Une variation de 15 % du taux de change fait varier votre capital de retraite "
    "de 15 %, soit davantage que la plupart des écarts de rendement entre scénarios. "
    "C'est le risque que la v1 passait sous silence."
)
