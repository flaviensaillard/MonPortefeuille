"""Fonds — apports et retraits de capital.

CORRECTION PAR RAPPORT À LA V1
------------------------------
La v1 convertissait chaque apport en onces avec `yf.Ticker("GC=F").fast_info.get(
'lastPrice', 2000.0)` : le prix du **future** de l'or, avec un repli dur à
2 000 $ l'once. Deux défauts : un future introduit un écart basis et une échéance,
et le repli faisait que toute panne Yahoo enregistrait vos apports comme si l'or
valait 2 000 $.

Ici, le principe de la v2 tient : pas de valeur de repli inventée, et une panne
bloque l'enregistrement au lieu de le falsifier. Reste que le spot lui-même
(`XAUUSD=X`) a été retiré de Yahoo — la v2 est donc passée au contrat front-month
`GC=F`. L'écart basis est de l'ordre de quelques dizaines de dollars sur
4 200 $, soit moins de 1 % ; l'essentiel — ne jamais enregistrer un prix inventé —
est préservé.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from core import db, fx, prices, session as S
from core import ui

st.set_page_config(page_title="Fonds", page_icon="💰", layout="wide")
st.title("💰 Fonds — apports de capital")

ctx = S.charger()
for err in ctx.erreurs:
    st.error(err)
if ctx.erreurs:
    st.stop()

# ---------------------------------------------------------------------------
# Nouveau mouvement
# ---------------------------------------------------------------------------
with st.expander("➕ Nouveau mouvement", expanded=not ctx.apports.empty):
    with st.form("nouveau_apport", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        date_mvt = c1.date_input("Date", value=dt.date.today())
        sens = c2.radio("Sens", ["Apport", "Retrait"], horizontal=True)
        devise_saisie = c3.selectbox("Devise", ["USD", "EUR", "CHF"])

        c4, c5 = st.columns(2)
        montant = c4.number_input("Montant", min_value=0.0, format="%.2f")
        compte = c5.selectbox(
            "Compte", ["swissquote", "revolut", "livret_chf", "autre"]
        )

        soumis = st.form_submit_button("Valider")

        if soumis:
            if montant <= 0:
                st.error("Le montant doit être positif.")
            else:
                try:
                    taux_eur = fx.taux(devise_saisie, date_mvt.isoformat(), "EUR")
                    montant_eur = montant * taux_eur
                    cours_or = prices.cours_or(date_mvt.isoformat())
                    taux_usd = fx.taux(devise_saisie, date_mvt.isoformat(), "USD")
                    onces = (montant * taux_usd) / cours_or
                except (fx.FXIndisponible, prices.CoursIndisponible) as exc:
                    st.error(
                        f"Mouvement non enregistré : {exc}. "
                        "Aucune valeur de repli n'a été utilisée — un apport mal "
                        "converti en onces fausserait votre performance en or."
                    )
                else:
                    try:
                        db.ecrire(db.T_APPORTS, [{
                            "date": date_mvt.isoformat(),
                            "sens": "apport" if sens == "Apport" else "retrait",
                            "montant_eur": round(montant_eur, 2),
                            "montant_or": round(onces, 6),
                            "cours_or": round(cours_or, 2),
                            "compte": compte,
                        }])
                        st.success(
                            f"✅ {sens} de {ui.usd_eur(montant * taux_usd, montant_eur)} enregistré "
                            f"({onces:.4f} oz d'or au cours du jour)."
                        )
                        S.vider_cache()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Écriture échouée : {exc}")

# ---------------------------------------------------------------------------
# Historique
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Historique des apports")

if ctx.apports.empty:
    st.info("Aucun apport enregistré.")
else:
    df = ctx.apports.copy()
    df["Date_DT"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("Date_DT", ascending=False)
    ui.tableau(pd.DataFrame([{
        "Date": pd.to_datetime(r["date"]).strftime("%d/%m/%Y"),
        "Sens": "↗ Apport" if r["sens"] == "apport" else "↘ Retrait",
        "Montant ($ / €)": ui.usd_eur(
            float(r["montant_usd"]) if pd.notna(r.get("montant_usd")) else float(r["montant_eur"]) * ctx.taux_eur_usd,
            float(r["montant_eur"]),
        ),
        "Équivalent or": f"{float(r['montant_or']):.4f} oz" if pd.notna(r.get("montant_or")) else "—",
        "Cours or": f"{float(r['cours_or']):,.0f} $/oz" if pd.notna(r.get("cours_or")) else "—",
        "Compte": r.get("compte", "—"),
    } for _, r in df.iterrows()]))

    apports_nets_eur = sum(
        float(r["montant_eur"]) * (1 if r["sens"] == "apport" else -1)
        for _, r in df.iterrows()
    )
    apports_nets_usd = sum(
        (float(r["montant_usd"]) if pd.notna(r.get("montant_usd")) else float(r["montant_eur"]) * ctx.taux_eur_usd)
        * (1 if r["sens"] == "apport" else -1)
        for _, r in df.iterrows()
    )
    st.metric("Apports nets cumulés", ui.usd_eur(apports_nets_usd, apports_nets_eur))
