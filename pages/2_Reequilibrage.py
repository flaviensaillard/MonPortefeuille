"""Rééquilibrage et saisie des transactions.

CORRECTIONS PAR RAPPORT À LA V1
-------------------------------
1. **L'assiette.** La v1 ne sommait que le type `💵 Cash` et ignorait
   `🏦 Cash réserve` : 12 277 €, soit 13 % du patrimoine, étaient absents du
   dénominateur et toutes les dérives affichées étaient fausses.

   Le vrai défaut était plus profond : l'épargne de précaution n'a rien à faire
   dans une assiette d'allocation, mais elle doit être *excluse pour une raison
   énoncée*, pas oubliée par un filtre de type. Ici le périmètre est porté par le
   modèle (`Perimetre.INVESTI` vs `PRECAUTION` / `COURANT`).

2. **Le seuil était unique et mécanique** : `abs(écart) >= 2.0 and abs(montant) >= 1000`.
   Un plancher de 1 000 $ dispensait de rééquilibrer une petite poche même très
   dérivée. On travaille par poche, avec sa bande et son propre seuil de rentabilité.

3. **Aucune traçabilité de la source de financement.** La v1 débitait une ligne
   de cash du ticker de la transaction, créant des soldes négatifs fantômes.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from core import db, fx, prices, session as S
from core import ui
from core.models import POCHES_INVESTIES
from core.rebalance import generer_ordres
from core.portfolio import devise_cotation_de

st.set_page_config(page_title="Rééquilibrage", page_icon="⚖️", layout="wide")
st.title("⚖️ Rééquilibrage")

ctx = S.charger()
for err in ctx.erreurs:
    st.error(err)
ui.bandeau_erreurs(ctx.echecs_cours, "cours")
if ctx.erreurs:
    st.stop()

# ---------------------------------------------------------------------------
# Diagnostic par poche
# ---------------------------------------------------------------------------
st.subheader("Diagnostic")

if not ctx.ecarts:
    st.info("Aucune poche investie.")
    st.stop()

lignes = []
for e in ctx.ecarts:
    lignes.append({
        "Poche": e.poche_nom,
        "Cible": ui.pct(e.poids_cible),
        "Réel": ui.pct(e.poids_reel),
        "Écart": ui.points(e.ecart_points),
        "Bande": f"±{e.bande * 100:.0f} pts",
        "Valeur ($ / €)": ui.usd_eur(e.valeur_usd, e.valeur_eur),
        "Valeur cible ($ / €)": ui.usd_eur(e.valeur_cible_usd, e.valeur_cible_eur),
        "Ajustement ($ / €)": ui.usd_eur(e.ecart_usd, e.ecart_eur),
        "État": "🔴 Hors bande" if e.hors_bande else "🟢 Dans la bande",
    })
ui.tableau(pd.DataFrame(lignes))

hors = ctx.besoins_reequilibrage
if hors:
    st.warning(
        f"**{len(hors)} poche(s) hors bande.** "
        + " · ".join(f"{e.poche_nom} ({ui.points(e.ecart_points)})" for e in hors)
    )
else:
    st.success("Toutes les poches sont dans leur bande. Aucun rééquilibrage nécessaire.")

# ---------------------------------------------------------------------------
# Ordres proposés
# ---------------------------------------------------------------------------
ordres, a_surveiller = generer_ordres(ctx.ecarts, seuil_min_eur=250.0)

if ordres:
    st.divider()
    st.subheader("Ordres proposés")
    st.caption(
        "Répartis au prorata de la valeur de chaque actif dans sa poche. "
        "Le seuil de 250 € évite de payer des frais de courtage supérieurs à la "
        "correction obtenue."
    )
    ui.tableau(pd.DataFrame([{
        "Actif": o.ticker,
        "Sens": "🟢 Achat" if o.sens == "achat" else "🔴 Vente",
        "Montant ($ / €)": ui.usd_eur(o.montant_usd, o.montant_eur),
        "Quantité": ui.quantite(o.quantite),
        "Poche": o.poche,
        "Motif": o.motif,
    } for o in ordres]))

if a_surveiller:
    st.info(
        "**Écart hors bande mais sous le seuil de rentabilité** (frais > correction) : "
        + ", ".join(f"{e.poche_nom} ({ui.points(e.ecart_points)})" for e in a_surveiller)
        + ". À traiter au prochain apport plutôt que par un ordre dédié."
    )

# ---------------------------------------------------------------------------
# Enregistrer une transaction
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Enregistrer une transaction")

with st.expander("➕ Nouvelle transaction"):
    st.caption(
        "Saisissez le cours dans la **devise de cotation du titre**. "
        "Ne le convertissez jamais à la main : c'est ce qui avait rendu le PRU de "
        "XJSE.SW inexploitable dans la v1."
    )

    with st.form("nouvelle_transaction", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        date_tx = c1.date_input("Date", value=dt.date.today())
        tickers_connus = sorted({t.ticker for t in ctx.transactions})
        ticker = c2.selectbox("Actif", options=tickers_connus + ["➕ Nouveau…"])
        if ticker == "➕ Nouveau…":
            ticker = c2.text_input("Nouveau ticker")
        sens = c3.radio("Sens", ["Achat", "Vente"], horizontal=True)

        c4, c5, c6 = st.columns(3)
        quantite = c4.number_input("Quantité", min_value=0.0, format="%.6f")
        cours = c5.number_input("Cours unitaire", min_value=0.0, format="%.6f")
        frais = c6.number_input("Frais", min_value=0.0, format="%.2f", value=0.0)

        # On ne présélectionne une devise que si on la connaît vraiment. Sinon
        # le champ reste sur USD, ce qui était une devinette déguisée.
        devise_connue = devise_cotation_de(ticker) if ticker else None
        devise_defaut = devise_connue or "USD"
        devise = st.selectbox(
            "Devise de cotation", ["USD", "EUR", "CHF", "JPY", "GBP", "CNY"],
            index=max(0, ["USD", "EUR", "CHF", "JPY", "GBP", "CNY"].index(devise_defaut))
            if devise_defaut in ["USD", "EUR", "CHF", "JPY", "GBP", "CNY"] else 0,
        )
        source = st.selectbox("Source", ["manuel", "swissquote", "revolut"])

        soumis = st.form_submit_button("🔨 Enregistrer")

        if soumis:
            problemes = []
            if not ticker or ticker == "➕ Nouveau…":
                problemes.append("Ticker manquant.")
            if quantite <= 0:
                problemes.append("La quantité doit être positive.")
            if cours <= 0:
                problemes.append("Le cours doit être positif.")

            # Contrôle de cohérence devise/ticker. On n'avertit que si on sait
            # de quoi on parle : un ticker inconnu ne doit pas déclencher une
            # mise en garde infondée.
            devise_attendue = devise_cotation_de(ticker) if ticker else None
            if ticker and devise_attendue and devise != devise_attendue:
                st.warning(
                    f"`{ticker}` est coté en **{devise_attendue}** mais vous "
                    f"avez saisi **{devise}**. Vérifiez — c'est exactement l'erreur qui "
                    "a corrompu le PRU de XJSE.SW dans la v1."
                )

            if problemes:
                for p in problemes:
                    st.error(p)
            else:
                try:
                    # Vérification que le taux de change est disponible AVANT d'écrire.
                    fx.taux(devise, date_tx.isoformat(), "EUR")
                except fx.FXIndisponible as exc:
                    st.error(
                        f"Transaction non enregistrée : {exc}. "
                        "Aucune valeur de repli n'a été utilisée."
                    )
                else:
                    # `montant_net` est une valeur dérivée : elle est recalculée
                    # à la lecture, pas stockée. La table pf2_transactions n'a
                    # pas cette colonne.
                    ligne = {
                        "ticker": ticker.upper().strip(),
                        "sens": sens.lower(),
                        "date": date_tx.isoformat(),
                        "quantite": quantite,
                        "cours": cours,
                        "frais": frais,
                        "devise": devise,
                        "source": source,
                    }
                    try:
                        db.ecrire(db.T_TRANSACTIONS, [ligne])
                        st.success(f"✅ {sens} de {quantite} {ticker.upper()} enregistré.")
                        S.vider_cache()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Écriture échouée : {exc}")

# ---------------------------------------------------------------------------
# Rappel de la règle de Gave
# ---------------------------------------------------------------------------
st.divider()
st.info(
    "**Règle de Gave à vérifier avant chaque transaction :** "
    "« N'ayez aucun contrat (cash ou obligation) dans la zone euro. » "
    "Si l'actif que vous saisissez est libellé en euros, l'application ne le "
    "bloquera pas — mais vous venez de violer le principe d'exclusion."
)
