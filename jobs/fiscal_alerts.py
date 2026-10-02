"""Alertes fiscales — exécuté par GitHub Actions.

CORRECTION PAR RAPPORT À LA V1
------------------------------
La v1 ne produisait aucune alerte. Or deux échéances fiscales ont un coût réel et
passent inaperçues :

1. **Le seuil de non-recouvrement de 61 €** — en dessous, l'impôt n'est pas prélevé.
2. **L'abattement de 305 € sur les plus-values crypto** — il est annuel et global,
   pas par cession. Le perdre coûte 305 € d'abattement, soit ~94 € d'impôt.

Ce robot écrit ses alertes dans `pf2_alertes`, que la page Fiscalité affiche.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db, fiscal_bars as fb  # noqa: E402
from core.models import Classe  # noqa: E402
from core.portfolio import calculer_positions, charger_transactions, classe_de  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("alertes")


def main() -> int:
    annee = dt.date.today().year

    try:
        transactions = charger_transactions(db.transactions())
    except ValueError as exc:
        log.error("Transactions illisibles : %s", exc)
        return 1

    if not transactions:
        log.info("Aucune transaction.")
        return 0

    alertes = 0

    # --- 1. Fin d'année : dernières semaines pour réaliser des moins-values ---
    aujourdhui = dt.date.today()
    if aujourdhui.month == 12:
        jours_restants = (dt.date(annee, 12, 31) - aujourdhui).days
        if jours_restants <= 20:
            db.ajouter_alerte(
                "Fin d'année fiscale",
                f"Il reste {jours_restants} jours. Les plus-values et moins-values "
                "réalisées cette année se compensent. Une moins-value réalisée "
                f"maintenant réduit l'impôt de {annee} ; reportée à {annee + 1}, "
                "elle ne servira qu'aux dix exercices suivants.",
                niveau="attention",
            )
            alertes += 1

    # --- 2. Crypto : la franchise de 305 € sur les PRIX DE CESSION ---
    # DÉFAUT CORRIGÉ : l'alerte parlait d'un « abattement » s'appliquant « sur
    # la plus-value GLOBALE ». C'est l'inverse. Le seuil de 305 € (art. 150 VH
    # bis) porte sur le TOTAL DES PRIX DE CESSION de l'année, et il exonère
    # tout ou rien : au-delà, la plus-value est imposable dès le premier euro.
    # Une alerte qui décrit mal la règle est pire qu'une alerte absente : elle
    # fait croire à une déduction qui n'existe pas.
    ventes_crypto = [
        t for t in transactions
        if t.est_vente and t.date.year == annee and classe_de(t.ticker) == Classe.CRYPTO
    ]
    if ventes_crypto:
        total = sum(t.montant_net for t in ventes_crypto)
        franchise = fb.CRYPTO_FRANCHISE_CESSIONS
        if total <= franchise:
            message = (
                f"{len(ventes_crypto)} cession(s) crypto en {annee} pour "
                f"{total:,.0f} €. Vous êtes SOUS le seuil de {franchise:.0f} € de "
                "prix de cession : la plus-value de l'année est exonérée. "
                "Le formulaire 2086 reste à déposer — c'est lui qui prouve que "
                "vous êtes sous le seuil. Rien à reporter en case 3AN."
            )
        else:
            message = (
                f"{len(ventes_crypto)} cession(s) crypto en {annee} pour "
                f"{total:,.0f} €. Vous DÉPASSEZ le seuil de {franchise:.0f} € de "
                "prix de cession : la plus-value est imposable dès le premier "
                "euro. Il n'y a aucun abattement à déduire — le seuil n'enlève "
                "rien, il déclenche. Report en case 3AN (ou 3BN si moins-value)."
            )
        db.ajouter_alerte("Seuil crypto de 305 €", message, niveau="info")
        alertes += 1

    # --- 3. Barème disponible pour l'année en cours ? ---
    try:
        fb.bareme_de(annee)
    except ValueError:
        db.ajouter_alerte(
            "Barème fiscal manquant",
            f"Aucun barème enregistré pour {annee}. Les calculs d'impôt de cette "
            "année échoueront. Renseignez-le dans core/fiscal_bars.py — "
            "ne devinez pas les seuils.",
            niveau="critique",
        )
        alertes += 1

    # --- 4. Positions en euros : le principe d'exclusion de Gave ---
    en_euros = sorted({
        t.ticker for t in transactions
        if classe_de(t.ticker) in (Classe.ACTION_ETF, Classe.OBLIGATION_ETF)
        and t.devise == "EUR"
    })
    if en_euros:
        db.ajouter_alerte(
            "Principe d'exclusion violé",
            "Actifs libellés en euros : " + ", ".join(en_euros) + ". "
            "Gave : « N'ayez aucun contrat (cash ou obligation) dans la zone euro. »",
            niveau="attention",
        )
        alertes += 1

    log.info("%d alerte(s) émise(s) pour %d.", alertes, annee)
    return 0


if __name__ == "__main__":
    sys.exit(main())
