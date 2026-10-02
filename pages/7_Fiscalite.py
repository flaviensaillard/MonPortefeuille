"""Fiscalité — le calcul, et le guide pour remplir.

CORRECTIONS PAR RAPPORT À LA V1
-------------------------------
1. **Un seul jeu de barèmes pour tous les exercices.** La v1 lisait
   `st.session_state.config` et appliquait les mêmes seuils à une plus-value de
   2025 et à une de 2026. Ici, le barème est choisi par **année de cession**.

2. **L'endpoint `api.gouv.fr/impots/bareme/{year}` n'existe pas.** Le `try`
   échouait en silence et l'app retombait sur sa table interne en annonçant une
   fiabilité « Officielle ».

3. **L'or ETC et l'or physique étaient confondus.** L'article 150 VI est
   désormais calculé à part.

4. **L'abattement de 305 € sur la crypto n'existait pas** — et n'existe
   toujours pas : c'est une FRANCHISE assise sur les prix de cession. Voyez
   `core/fiscal_bars.py`.

5. **La comparaison PFU / barème abdiquait** et oubliait la CSG déductible.

CE QUI A ÉTÉ CORRIGÉ ENSUITE — LES TROIS DÉFAUTS QUE VOUS AVEZ VUS
------------------------------------------------------------------
6. **Le guide avait disparu.** La page calculait l'impôt sans jamais dire dans
   quelle case l'inscrire. `core/guide_fiscal.py` le reconstruit, formulaire par
   formulaire, montant par montant, depuis vos données.

7. **`st.stop()` s'exécutait dès qu'il n'y avait aucune cession.** Le guide
   disparaissait donc précisément les années où il sert le plus : un compte à
   l'étranger se déclare même sans une seule opération, et une moins-value non
   déclarée est une moins-value perdue. La page ne s'arrête plus jamais avant
   d'avoir montré le guide.

8. **`_gain_latent_reel(ctx, snaps)` était appelée avec deux arguments pour une
   fonction qui n'en prend qu'un.** `TypeError` dès qu'il y avait deux
   snapshots — la page entière tombait. La fonction ne se sert que de `ctx`.

9. **Les taux étaient faux.** Le PFU était calculé comme `0,308 - 0,172`, soit
   13,6 % d'IR au lieu des 12,8 % de l'article 200 A. Et les prélèvements
   sociaux ne sont plus à 17,2 % : la LFSS 2026 (art. 12) les porte à 18,6 %
   sur le capital mobilier, soit un PFU de 31,4 %.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from core import fiscal_bars as fb
from core import guide_fiscal as guide
from core import session as S, tax
from core import dates
from core import ui
from core.models import Classe
from core.portfolio import classe_de

st.set_page_config(page_title="Fiscalité", page_icon="🏛️", layout="wide")
st.title("🏛️ Fiscalité")
st.caption(
    "Le calcul de l'impôt, et le guide pour remplir votre déclaration : "
    "numéros de formulaires, cases à remplir, montants à inscrire."
)

ctx = S.charger()
for err in ctx.erreurs:
    st.error(err)
if ctx.erreurs:
    st.stop()

# ---------------------------------------------------------------------------
# Identité fiscale
# ---------------------------------------------------------------------------
with st.expander("👤 Identité fiscale", expanded=False):
    c1, c2, c3 = st.columns(3)
    statut = c1.selectbox(
        "Statut", ["Marié(e) / Pacsé(e)", "Célibataire", "Veuf(ve)", "Divorcé(e)"],
    )
    parts = c2.number_input(
        "Parts fiscales", min_value=0.5, max_value=6.0, step=0.5, value=3.0,
        help="Couple = 2. +0,5 pour le 1er enfant, +0,5 pour le 2e, +1 du 3e.",
    )
    autres_revenus = c3.number_input(
        "Autres revenus imposables (€)", min_value=0.0, step=1000.0, value=0.0,
        help="Salaires et autres revenus, hors plus-values de l'année choisie. "
             "Sert à comparer le PFU et le barème progressif.",
    )

annee = st.selectbox(
    "Année des revenus à déclarer",
    options=list(range(dt.date.today().year, 2021, -1)),
    help="Les revenus de l'année N sont déclarés au printemps N+1. Le barème "
         "retenu est celui de l'année des revenus.",
)

try:
    st.caption(
        f"Barème {annee} — source : {fb.source_de(annee)}. "
        f"PFU {annee} : {fb.IR_FORFAITAIRE:.1%} d'IR + {fb.taux_ps(annee):.1%} "
        f"de prélèvements sociaux = **{fb.taux_pfu(annee):.1%}**."
    )
except ValueError as exc:
    st.error(str(exc))
    st.stop()

if annee in fb.PS_ANNEE_INCERTAINE:
    st.warning(
        f"**Le taux de prélèvements sociaux de {annee} est discuté.** "
        + fb.PS_ANNEE_INCERTAINE[annee]
    )

# ---------------------------------------------------------------------------
# Où sont vos comptes — la seule chose que l'application ne peut pas savoir
# ---------------------------------------------------------------------------
st.divider()
st.subheader("1. Vos comptes à l'étranger")
st.caption(
    "L'application ne peut pas savoir où vos comptes sont domiciliés : "
    "la même néobanque peut être adossée à une entité française, irlandaise ou "
    "britannique. Cette question vous appartient, et une erreur dessus coûte "
    "1 500 € par compte et par an."
)

defauts = guide.comptes_par_defaut()
choisis: list[str] = []
c1, c2 = st.columns(2)
with c1:
    for libelle in defauts:
        if st.checkbox(libelle, value=True, key=f"ctr_{libelle[:20]}"):
            choisis.append(libelle)
with c2:
    autre = st.text_input(
        "Autre compte à l'étranger",
        placeholder="Ex. : compte courant Revolut Ltd (Royaume-Uni)",
        help="Laissez vide si vous n'en avez pas. Ajoutez un compte par ligne, "
             "séparés par des points-virgules.",
    )
if autre.strip():
    choisis.extend(x.strip() for x in autre.split(";") if x.strip())

# ---------------------------------------------------------------------------
# Reconstituer les cessions de l'année depuis les transactions
# ---------------------------------------------------------------------------
cessions = tax.cessions_de_lannee(ctx.transactions, ctx.positions, annee)
aucune_cession = not any(cessions.values())

anomalies: list[str] = []
if aucune_cession:
    # On ne s'arrête PAS. Le guide doit s'afficher : une année sans cession
    # n'est pas une année sans obligation déclarative.
    resultat: dict = {"annee": annee, "regimes": []}
else:
    resultat = tax.calculer(
        cessions, annee, autres_revenus, parts, statut, anomalies
    )

comparaison = None
if not aucune_cession:
    pv_totale = sum(
        l["prix_cession_eur"] - l["pru_eur"] * l["quantite"]
        for lst in cessions.values() for l in lst
    )
    if pv_totale > 0:
        try:
            comparaison = tax.comparer_pfu_bareme(
                pv_totale, autres_revenus, parts, annee, statut
            )
        except ValueError:
            comparaison = None

# ---------------------------------------------------------------------------
# LE GUIDE
# ---------------------------------------------------------------------------
st.divider()
st.subheader("2. Le guide de déclaration")

etapes = guide.construire_guide(
    annee=annee,
    resultats=resultat,
    comptes_etrangers=choisis,
    comparaison=comparaison,
)

if not etapes:
    st.info(
        f"Aucun formulaire à remplir pour {annee} d'après vos données. "
        "Cela suppose qu'aucun de vos comptes n'est à l'étranger — cochez-en un "
        "ci-dessus s'il y en a, la 3916-bis se dépose tous les ans."
    )
else:
    st.caption(
        f"{len(etapes)} formulaire(s) pour les revenus {annee}, "
        f"à déposer au printemps {annee + 1}. Dans l'ordre du parcours en ligne."
    )

    for etape in etapes:
        marque = "obligatoire" if etape.obligatoire else "facultatif"
        with st.container(border=True):
            st.markdown(f"### {etape.ordre}. {etape.titre}")
            st.markdown(
                f"**Formulaire {etape.formulaire}** — {marque}. {etape.raison}"
            )
            st.caption(f"📍 {etape.ou}")

            for case in etape.cases:
                if case.montant is None:
                    gauche, droite = st.columns([3, 2])
                    gauche.markdown(
                        f"**Case {case.numero}** — {case.libelle}  \n"
                        f"<span style='font-size:0.85em;color:#666'>{case.comment}</span>",
                        unsafe_allow_html=True,
                    )
                    droite.markdown(
                        "<div style='text-align:right;font-size:1.4em;"
                        "color:#888'>—</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    gauche, droite = st.columns([3, 2])
                    gauche.markdown(
                        f"**Case {case.numero}** — {case.libelle}  \n"
                        f"<span style='font-size:0.85em;color:#666'>{case.comment}</span>",
                        unsafe_allow_html=True,
                    )
                    droite.markdown(
                        f"<div style='text-align:right'>"
                        f"<span style='font-size:0.8em;color:#666'>à inscrire</span>"
                        f"<br><span style='font-size:1.7em;font-weight:600'>"
                        f"{ui.eur(case.montant)}</span></div>",
                        unsafe_allow_html=True,
                    )

            if etape.sanction:
                st.warning(f"⚠️ **Oubli** : {etape.sanction}")
            if etape.reference:
                st.caption(f"Référence : {etape.reference}")

    recap = [
        {
            "Formulaire": c.formulaire,
            "Case": c.numero,
            "Libellé": c.libelle,
            "Montant à inscrire": ui.eur(c.montant) if c.montant is not None else "—",
        }
        for e in etapes for c in e.cases
    ]
    with st.expander("📋 Récapitulatif à imprimer", expanded=False):
        ui.tableau(pd.DataFrame(recap))
        st.download_button(
            "Télécharger le récapitulatif (CSV)",
            pd.DataFrame(recap).to_csv(index=False, sep=";").encode("utf-8-sig"),
            file_name=f"declaration-{annee}.csv",
            mime="text/csv",
        )

# ---------------------------------------------------------------------------
# Le calcul détaillé
# ---------------------------------------------------------------------------
st.divider()
st.subheader("3. Le calcul détaillé")

if anomalies:
    st.warning(
        "**Calcul incomplet.** " + " ".join(anomalies) +
        " Le chiffre affiché ne porte donc pas sur toutes vos cessions."
    )

if aucune_cession:
    st.info(
        f"Aucune cession enregistrée en {annee}. Cette page liste les "
        "plus-values **réalisées**, pas les plus-values latentes — vendre "
        "n'est pas la même chose que détenir."
    )

    latente = sum(p.pv_latente_eur for p in ctx.positions.values() if p.quantite > 0)
    if latente > 0:
        st.metric("Plus-value latente si vous vendiez tout aujourd'hui", ui.eur(latente))
        st.caption(
            "Ce n'est pas un revenu imposable : rien n'est dû tant que vous ne "
            "vendez pas. C'est le chiffre qui alimente le point de vigilance "
            "plus bas."
        )

for r in resultat.get("regimes", []):
    with st.container():
        st.markdown(f"#### Régime {r['regime']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Plus-value brute", ui.eur(r.get("pv_brute", 0.0)))
        if "abattement" in r:
            c2.metric("Franchise 305 €", "exonéré" if r.get("abattement") is None else "—",
                      help="Le seuil de 305 € porte sur les prix de cession, pas "
                           "sur le gain — ce n'est pas un abattement.")
        else:
            c2.metric("Cessions", r.get("nb_cessions", 0))
        c3.metric("Impôt dû", ui.eur(r.get("total_du", 0.0)))
        st.caption(
            f"Taux effectif : {ui.pct(r.get('taux_effectif', 0.0))} — "
            f"{r.get('detail', '')}"
        )
        st.divider()

if comparaison:
    st.subheader("PFU ou barème progressif ?")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Prélèvement forfaitaire unique (PFU)**")
        st.metric(f"IR ({fb.IR_FORFAITAIRE:.1%})", ui.eur(comparaison["pfu"]["ir"]))
        st.metric(f"Prélèvements sociaux ({fb.taux_ps(annee):.1%})",
                  ui.eur(comparaison["pfu"]["ps"]))
        st.metric("Total", ui.eur(comparaison["pfu"]["total"]))
    with c2:
        st.markdown("**Barème progressif**")
        st.metric("IR marginal", ui.eur(comparaison["bareme"]["ir_marginal"]))
        st.metric("Prélèvements sociaux", ui.eur(comparaison["bareme"]["ps"]))
        st.metric("CSG déductible", ui.eur(-comparaison["bareme"]["csg_deductible"]),
                  help="6,8 points de CSG viennent en déduction du revenu "
                       "imposable — uniquement au barème, et ce montant ne "
                       "change pas avec la hausse de la LFSS 2026.")
        st.metric("Total", ui.eur(comparaison["bareme"]["total"]))

    if comparaison["choix"] == "Barème progressif":
        st.success(
            f"✅ **Le barème progressif est plus avantageux** — vous économisez "
            f"{ui.eur(comparaison['gain'])}. TMI retenue : {ui.pct(comparaison['tmi'])}. "
            "Cochez la case 2OP."
        )
    else:
        st.success(
            f"✅ **La Flat Tax est plus avantageuse** — vous économisez "
            f"{ui.eur(comparaison['gain'])}. Ne cochez pas la 2OP."
        )

    st.info(
        "L'option est **globale et annuelle** : elle porte sur l'ensemble des "
        "plus-values et gains de l'année, et se choisit au moment de la "
        "déclaration. Ce calcul ne concerne que les plus-values de valeurs "
        "mobilières et d'actifs numériques."
    )

if any(cessions.values()):
    st.subheader("Détail des cessions")
    lignes = []
    for classe, lst in cessions.items():
        for l in lst:
            pv = l["prix_cession_eur"] - l["pru_eur"] * l["quantite"]
            lignes.append({
                "Actif": l["actif"],
                "Classe": classe.value.replace("_", " "),
                "Date": l["date"].strftime("%d/%m/%Y"),
                "Quantité": ui.quantite(l["quantite"]),
                "PRU (€)": ui.eur(l["pru_eur"]),
                "Prix de cession (€)": ui.eur(l["prix_cession_eur"]),
                "Plus-value (€)": ui.eur(pv),
            })
    if lignes:
        ui.tableau(pd.DataFrame(lignes))

# ---------------------------------------------------------------------------
# Points de vigilance
# ---------------------------------------------------------------------------
st.divider()
st.subheader("4. Points de vigilance")

vigilance: list[str] = []

latente = sum(p.pv_latente_eur for p in ctx.positions.values() if p.quantite > 0)
if latente > 0:
    vigilance.append(
        f"**Plus-value latente : {ui.eur(latente)}.** Elle n'est pas imposable "
        "tant que vous ne vendez pas. Si vous cédiez tout cette année, une part "
        f"serait absorbée par le PFU à {fb.taux_pfu(annee):.1%} — soit environ "
        f"{ui.eur(latente * fb.taux_pfu(annee))}. Étaler les cessions sur "
        "plusieurs exercices peut réduire la note, ou l'inverse selon votre TMI."
    )

if any(c == Classe.OR for c in cessions):
    vigilance.append(
        "**Votre or est un ETC (IGLN.L), pas de l'or physique.** Le régime "
        "150-0 A des valeurs mobilières s'applique — pas l'article 150 VI, qui "
        "offre une exonération totale après 22 ans de détention. C'est une "
        "différence de régime, pas de durée. La qualification fiscale exacte "
        "d'un ETC adossé à de l'or physique reste discutée : faites-la "
        "confirmer si le montant en jeu le justifie."
    )

if any(p.quantite > 0 and classe_de(t) == Classe.CRYPTO
       for t, p in ctx.positions.items()):
    vigilance.append(
        "**Crypto détenue.** Deux obligations distinctes : la 2086 pour les "
        "cessions, et la 3916-bis si la plateforme est hors de France. La "
        "seconde est due même les années sans aucune vente."
    )

if annee in fb.PS_ANNEE_INCERTAINE:
    vigilance.append(
        f"**Le taux de prélèvements sociaux de {annee} est discuté** entre "
        "17,2 % et 18,6 %. L'écart vaut 1,4 point d'impôt : sur 10 000 € de "
        "plus-value, 140 €. La question est réglée pour 2026 (18,6 %), "
        "litigieuse pour 2025."
    )

for v in vigilance:
    st.info(v)
if not vigilance:
    st.caption("Aucun point de vigilance particulier pour l'instant.")

# ---------------------------------------------------------------------------
# Avertissement
# ---------------------------------------------------------------------------
st.divider()
st.warning(
    "**Ce module produit une estimation, pas une déclaration.** Les taux et "
    "seuils proviennent de sources publiques concordantes ; seule la "
    "documentation administrative (BOFiP) fait foi. Les points à recouper "
    "avant de valider :\n"
    "- le barème et la décote de l'année ;\n"
    "- le plafonnement du quotient familial ;\n"
    "- la qualification fiscale exacte d'un ETC or (IGLN.L) ;\n"
    "- l'entité gestionnaire de vos comptes à l'étranger, qui détermine le "
    "pays à inscrire sur la 3916-bis ;\n"
    "- la date d'entrée en vigueur retenue pour la hausse de CSG de 2025."
)
