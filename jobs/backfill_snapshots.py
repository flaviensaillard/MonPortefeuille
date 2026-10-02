"""Reconstitution de l'historique — remplit `pf2_snapshots` depuis le grand livre.

Pourquoi ce robot existe
------------------------
Les pages « Suivi » et « Performance » affichent des courbes Plotly toutes
prêtes, mais elles sont conditionnées à `ctx.snapshots` — et `pf2_snapshots`
n'est rempli que par le robot nocturne, **un soir à la fois**. Un portefeuille
ouvert depuis un an aurait donc mis un an à avoir un an d'historique, et pendant
tout ce temps l'application n'affichait aucun graphique.

Ce robot reconstitue le passé à partir de ce qui existe déjà : le grand livre des
transactions, les cours historiques de Yahoo et les taux de change historiques.
Aucune donnée nouvelle n'est nécessaire.

Ce qu'il écrit, et ce qu'il n'écrit pas
---------------------------------------
Il écrit **exactement** les colonnes de `jobs/daily_snapshot.py`, dans le même
format, sur la même contrainte d'unicité (`date`). Les deux robots sont donc
interchangeables : le nocturne reprendra le relais au premier soir qui suit.

Il ne devine rien. Si un cours ou un taux manque à une date, la journée est
**sautée et signalée**, jamais écrite avec un zéro — un zéro créerait un trou
dans la courbe, exactement ce que la v1 faisait.

Le PRU est celui du grand livre, pas celui du jour : à chaque date on recalcule
les positions à partir des seules transactions antérieures, puis on les valorise
au cours de ce jour-là.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db, fx, prices  # noqa: E402
from core.models import POCHES_PAR_CLE, Perimetre  # noqa: E402
from core.portfolio import calculer_positions, charger_transactions, valoriser  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("backfill")

# Au-delà, on prévient : Yahoo refuse les très longues plages et le robot
# deviendrait lent au point de dépasser le délai de GitHub Actions.
PLAFOND_JOURS = 1500


def _premier_jour(transactions) -> dt.date | None:
    if not transactions:
        return None
    return min(t.date for t in transactions)


def _snapshot_au(transactions, jour: dt.date) -> dict | None:
    """Calcule la ligne de snapshot pour un jour donné, ou None si impossible."""
    iso = jour.isoformat()

    # Positions telles qu'elles étaient ce jour-là : seules les transactions
    # déjà réalisées. Le PRU suit, puisqu'il est calculé sur ce sous-ensemble.
    du_jour = [t for t in transactions if t.date <= jour]
    if not du_jour:
        return None

    anomalies: list[str] = []
    positions = calculer_positions(du_jour, anomalies)
    if not positions:
        return None

    try:
        actifs, echecs = valoriser(positions, iso)
    except fx.FXIndisponible as exc:
        log.warning("%s : taux de change indisponible (%s)", iso, exc)
        return None

    if not actifs:
        log.warning("%s : aucun cours disponible", iso)
        return None
    if echecs:
        log.info("%s : cours manquants pour %s", iso, ", ".join(echecs))

    totaux = {p.value: 0.0 for p in Perimetre}
    poches = {cle: 0.0 for cle in POCHES_PAR_CLE}
    for a in actifs:
        p = POCHES_PAR_CLE.get(a.poche)
        cle = p.perimetre.value if p else Perimetre.INVESTI.value
        totaux[cle] += a.valeur_eur
        poches[a.poche] = poches.get(a.poche, 0.0) + a.valeur_eur

    try:
        cours_or = prices.cours_or(iso)
        taux_usd = fx.taux("EUR", iso, "USD")
    except (prices.CoursIndisponible, fx.FXIndisponible) as exc:
        log.warning("%s : cours de l'or indisponible (%s)", iso, exc)
        return None

    equivalent_or = (totaux[Perimetre.INVESTI.value] * taux_usd) / cours_or

    return {
        "date": iso,
        "patrimoine_total_eur": round(sum(totaux.values()), 2),
        "patrimoine_investi_eur": round(totaux[Perimetre.INVESTI.value], 2),
        "precaution_eur": round(totaux[Perimetre.PRECAUTION.value], 2),
        "courant_eur": round(totaux[Perimetre.COURANT.value], 2),
        "cours_or_usd": round(cours_or, 2),
        "equivalent_or_oz": round(equivalent_or, 4),
        "poche_rv_eur": round(poches.get("rv", 0.0), 2),
        "poche_energie_eur": round(poches.get("energie", 0.0), 2),
        "poche_asie_eur": round(poches.get("asie", 0.0), 2),
        "poche_jgb_eur": round(poches.get("jgb", 0.0), 2),
    }


def main() -> int:
    # Limite optionnelle, en jours, pour un premier essai prudent :
    # BACKFILL_JOURS=30 ne reconstitue que le dernier mois.
    limite = os.environ.get("BACKFILL_JOURS", "").strip()

    manquantes = [t for t, ok in db.tables_presentes().items() if not ok]
    if manquantes:
        log.error("Tables absentes : %s. Exécutez migrations/001_init.sql.", manquantes)
        return 1

    transactions = charger_transactions(db.transactions())
    if not transactions:
        log.error("Aucune transaction. Rien à reconstituer.")
        return 1

    premier = _premier_jour(transactions)
    dernier = dt.date.today()
    total = (dernier - premier).days + 1
    log.info(
        "Reconstitution de %s à %s (%d jours, %d transactions)",
        premier.isoformat(), dernier.isoformat(), total, len(transactions),
    )

    if limite:
        try:
            demande = int(limite)
        except ValueError:
            log.error("BACKFILL_JOURS doit être un entier, pas %r.", limite)
            return 1
        if demande > 0 and total > demande:
            log.info("Limite à %d jours (BACKFILL_JOURS).", demande)
            premier = dernier - dt.timedelta(days=demande - 1)
            total = demande

    if total > PLAFOND_JOURS:
        log.warning(
            "Plage de %d jours, au-delà du plafond de %d : seuls les %d derniers "
            "jours seront traités.", total, PLAFOND_JOURS, PLAFOND_JOURS,
        )
        premier = dernier - dt.timedelta(days=PLAFOND_JOURS - 1)

    ecrits = sautes = 0
    jour = premier
    while jour <= dernier:
        ligne = _snapshot_au(transactions, jour)
        if ligne is None:
            sautes += 1
        else:
            try:
                db.ajouter_snapshot(ligne)
                ecrits += 1
            except Exception as exc:
                log.error("%s : écriture échouée (%s)", ligne["date"], exc)
                sautes += 1
        jour += dt.timedelta(days=1)

    log.info("Terminé : %d snapshots écrits, %d journées sautées.", ecrits, sautes)
    if ecrits:
        try:
            db.ajouter_alerte(
                "Historique reconstitué",
                f"{ecrits} snapshots écrits de {premier.isoformat()} à "
                f"{dernier.isoformat()}. Les pages Suivi et Performance "
                f"affichent désormais l'historique complet.",
                niveau="info",
            )
        except Exception as exc:
            log.warning("Alerte non enregistrée : %s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
