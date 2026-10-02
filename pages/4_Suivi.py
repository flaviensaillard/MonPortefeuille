"""Suivi — historique des snapshots.

CORRECTION PAR RAPPORT À LA V1
------------------------------
La v1 avait deux colonnes de performance cumulée (`Evolution cumulée %` et
`TG_Evolution cumulée %`) qui divergeaient de 21 points, parce que
`TG_Score TWR %` était une copie littérale de `Score TWR %`. Deux mesures de la
même chose, dont une fausse, affichées côte à côte sans explication.

Ici, une seule mesure par concept, et chacune dit ce qu'elle mesure.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import session as S
from core import dates
from core import ui

st.set_page_config(page_title="Suivi", page_icon="🏖️", layout="wide")
st.title("🏖️ Suivi")

ctx = S.charger()
for err in ctx.erreurs:
    st.error(err)
if ctx.erreurs:
    st.stop()

if ctx.snapshots.empty:
    st.info(
        "Aucun snapshot. Le robot GitHub Actions en écrit un chaque soir "
        "(`jobs/daily_snapshot.py`). Vous pouvez aussi le déclencher à la main."
    )
    st.stop()

df = ctx.snapshots.copy()
df["Date"] = dates.parser(df["Date"])
df = df.dropna(subset=["Date"]).sort_values("Date")

# ---------------------------------------------------------------------------
# Courbe du patrimoine
# ---------------------------------------------------------------------------
st.subheader("Évolution du patrimoine")
st.caption(
    "Le portefeuille investi et l'épargne de précaution sont tracés séparément : "
    "ils n'ont pas le même rôle et ne se rééquilibrent pas de la même façon."
)

import plotly.express as px

col_inv = "patrimoine_investi_usd" if "patrimoine_investi_usd" in df.columns else "patrimoine_investi_eur"
col_prec = "precaution_usd" if "precaution_usd" in df.columns else "precaution_eur"
cols_courbe = [col_inv, col_prec]
if "capital_investi_usd" in df.columns and df["capital_investi_usd"].notna().any():
    cols_courbe.append("capital_investi_usd")

fig = px.line(
    df,
    x="Date",
    y=cols_courbe,
    labels={"value": "Dollars ($)", "variable": ""},
    color_discrete_map={
        col_inv: "#2ecc71",
        col_prec: "#f39c12",
        "capital_investi_usd": "#3498db",
    },
)
fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5))
newnames = {
    col_inv: "Actifs stratégiques ($)",
    col_prec: "Épargne de précaution ($)",
    "capital_investi_usd": "Capital investi ($)",
}
fig.for_each_trace(lambda t: t.update(name=newnames.get(t.name, t.name)))
st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# La courbe qui compte : en onces d'or
# ---------------------------------------------------------------------------
if "equivalent_or_oz" in df.columns and df["equivalent_or_oz"].notna().any():
    st.divider()
    st.subheader("🪙 Le portefeuille investi en onces d'or")
    st.caption(
        "C'est la seule courbe qui réponde à la question de Gave : "
        "« suis-je en train de m'enrichir ou de m'appauvrir ? » "
        "Une courbe en euros peut monter tandis que celle-ci descend."
    )
    fig_or = px.line(df, x="Date", y="equivalent_or_oz",
                     labels={"equivalent_or_oz": "Onces d'or"})
    fig_or.update_traces(line_color="#f1c40f")
    fig_or.update_layout(yaxis_title="Onces d'or")
    st.plotly_chart(fig_or, use_container_width=True)

    # La variation en or, corrigee des apports.
    #
    # `oz_final / oz_initial` montait avec vos versements : +120 % la ou la
    # strategie en avait produit 16,9 %. Meme defaut, meme remede — une
    # seule fonction, dans `core.session`.
    variation = S.twr_en_or_portefeuille(ctx)
    if variation is not None:
        st.metric(
            "Variation depuis le premier snapshot",
            ui.pct(variation, signe=True),
        )

# ---------------------------------------------------------------------------
# Allocation par poche dans le temps
# ---------------------------------------------------------------------------
POCHES = {
    "poche_rv_eur": "Réserve de valeur",
    "poche_energie_eur": "Énergie",
    "poche_asie_eur": "Asie / Chine",
    "poche_jgb_eur": "Obligations japonaises",
}
presentes = [c for c in POCHES if c in df.columns and df[c].notna().any()]
if len(presentes) >= 2:
    st.divider()
    st.subheader("Allocation par poche")
    st.caption(
        "Les parts relatives de chaque poche. Une poche qui grossit sans que "
        "vous ayez rien décidé, c'est le marché qui vous fait dériver de votre "
        "pondération — c'est exactement ce que la page Rééquilibrage corrige."
    )
    part = df[presentes].div(df[presentes].sum(axis=1), axis=0).fillna(0.0) * 100.0
    part["Date"] = df["Date"].values
    fig_poches = px.area(
        part,
        x="Date",
        y=presentes,
        labels={"value": "Part du portefeuille", "variable": ""},
        color_discrete_map={
            "poche_rv_eur": "#f1c40f",
            "poche_energie_eur": "#e74c3c",
            "poche_asie_eur": "#e67e22",
            "poche_jgb_eur": "#3498db",
        },
    )
    fig_poches.for_each_trace(
        lambda t: t.update(name=POCHES.get(t.name, t.name))
    )
    fig_poches.update_layout(yaxis_ticksuffix=" %")
    st.plotly_chart(fig_poches, use_container_width=True)

# ---------------------------------------------------------------------------
# Tableau des snapshots
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Détail des snapshots")

df_desc = df.sort_values("Date", ascending=False)
affichage = pd.DataFrame([{
    "Date": r["Date"].strftime("%d/%m/%Y"),
    "Capital investi ($ / €)": ui.usd_eur(r.get("capital_investi_usd")) if pd.notna(r.get("capital_investi_usd")) else "—",
    "Actifs stratégiques ($ / €)": ui.usd_eur(r.get("patrimoine_investi_usd", r["patrimoine_investi_eur"]), r["patrimoine_investi_eur"]),
    "Patrimoine total ($ / €)": ui.usd_eur(r.get("patrimoine_total_usd", r["patrimoine_total_eur"]), r["patrimoine_total_eur"]),
    "Précaution ($ / €)": ui.usd_eur(r.get("precaution_usd", r["precaution_eur"]), r["precaution_eur"]),
    "Or (oz)": f"{r['equivalent_or_oz']:.2f}" if pd.notna(r.get("equivalent_or_oz")) else "—",
} for _, r in df_desc.iterrows()])

ui.tableau(affichage)
