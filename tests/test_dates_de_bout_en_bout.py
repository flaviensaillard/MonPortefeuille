"""La base telle qu'elle est, lue telle qu'elle est lue.

Pourquoi ce fichier existe
--------------------------
Trois tours de suite, le même symptôme est revenu — « Vente de 22.0 FLXC.L le
2025-01-07 sans position détenue » — alors que la base contenait la date
correcte. Chaque fois, le code était juste en local et faux sur le serveur.

La cause : `pd.to_datetime(..., dayfirst=True)` interprète l'ISO à l'envers, et
`format="mixed"` ne corrige que l'ISO en cassant le `jj/mm/aaaa` de la v1. Et le
comportement du couple `dayfirst` + `format="mixed"` dépend de la version de
pandas : un code juste ici peut être faux là-bas, sans qu'aucune ligne bouge.

Ces tests lisent donc un tableau **au format exact de la table `pf2_transactions`**
— colonnes snake_case, dates ISO — et vérifient le résultat final. C'est le seul
niveau auquel le défaut était invisible : chaque brique était juste isolément.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core import portfolio

# La séquence FLXC.L réelle, dans l'ordre de la base. Les deux ventes sont
# postérieures aux achats : il ne doit rester AUCUNE anomalie.
LIGNES = [
    # ticker, sens, date, quantite, cours, frais, devise
    ("FLXC.L", "achat", "2025-03-18", 677, 30.4658, 111.79, "USD"),
    ("FLXC.L", "achat", "2025-04-15", 80, 26.34, 34.01, "USD"),
    ("FLXC.L", "achat", "2025-04-30", 15, 27.14, 4.46, "USD"),
    ("FLXC.L", "vente", "2025-07-01", 22, 29.04, 6.81, "USD"),
    ("FLXC.L", "achat", "2025-08-04", 7, 30.375, 4.17, "USD"),
    ("FLXC.L", "vente", "2025-10-01", 13, 34.86, 4.53, "USD"),
    ("FLXC.L", "achat", "2025-10-13", 18, 33.605, 6.76, "USD"),
    ("FLXC.L", "achat", "2025-10-17", 9, 32.86, 4.29, "USD"),
    ("FLXC.L", "vente", "2025-10-22", 9, 33.545, 4.30, "USD"),
    ("FLXC.L", "achat", "2025-11-21", 24, 32.035, 7.00, "USD"),
    ("FLXC.L", "achat", "2025-12-24", 7, 32.775, 4.19, "USD"),
    ("FLXC.L", "achat", "2026-01-05", 8, 33.34, 4.25, "USD"),
    ("FLXC.L", "achat", "2026-01-21", 7, 33.72, 4.20, "USD"),
    ("FLXC.L", "achat", "2026-01-28", 8, 34.565, 4.26, "USD"),
    ("FLXC.L", "achat", "2026-01-29", 7, 34.765, 4.22, "USD"),
    ("FLXC.L", "vente", "2026-02-02", 97, 33.245, 35.69, "USD"),
    ("FLXC.L", "vente", "2026-02-02", 34, 33.245, 1.69, "USD"),
    ("FLXC.L", "achat", "2026-02-04", 14, 33.08, 4.54, "USD"),
    ("FLXC.L", "achat", "2026-02-27", 22, 32.335, 6.92, "USD"),
    ("FLXC.L", "achat", "2026-03-02", 38, 31.87, 12.67, "USD"),
    ("FLXC.L", "achat", "2026-04-01", 9, 30.535, 4.26, "USD"),
    ("FLXC.L", "achat", "2026-07-01", 8, 28.40, 4.90, "USD"),
    ("FLXC.L", "achat", "2026-09-01", 17, 29.565, 7.77, "USD"),
]

COLONNES = ["ticker", "sens", "date", "quantite", "cours", "frais", "devise",
            "source", "reference"]


def _df():
    return pd.DataFrame(
        [list(l) + ["import_v1", f"v1:id{i}"] for i, l in enumerate(LIGNES)],
        columns=COLONNES,
    )


def test_aucune_anomalie_sur_la_sequence_reelle():
    """Le garde-fou principal : la base réelle ne doit produire aucune anomalie."""
    tx = portfolio.charger_transactions(_df())
    anomalies: list[str] = []
    portfolio.calculer_positions(tx, anomalies)
    assert anomalies == [], f"anomalies inattendues : {anomalies}"


def test_la_vente_du_1er_juillet_est_lue_au_1er_juillet():
    """Le défaut exact : 2025-07-01 lu comme 2025-01-07."""
    tx = portfolio.charger_transactions(_df())
    vente = next(t for t in tx if t.type == "vente" and t.quantite == 22)
    assert vente.date.isoformat() == "2025-07-01"


def test_la_vente_du_1er_octobre_est_lue_au_1er_octobre():
    tx = portfolio.charger_transactions(_df())
    vente = next(t for t in tx if t.type == "vente" and t.quantite == 13)
    assert vente.date.isoformat() == "2025-10-01"


def test_la_position_finale_est_de_800_parts():
    """677 + 80 + 15 + 7 + 18 + 9 + 24 + 7 + 8 + 7 + 8 + 7 + 14 + 22 + 38 + 9
    + 8 + 17 - 22 - 13 - 9 - 97 - 34 = 800, le nombre de parts détenu."""
    tx = portfolio.charger_transactions(_df())
    anomalies: list[str] = []
    positions = portfolio.calculer_positions(tx, anomalies)
    assert positions["FLXC.L"].quantite == pytest.approx(800.0)


def test_chaque_vente_est_precedee_d_un_achat_suffisant():
    """À chaque date de vente, la position doit être positive ET assez grande."""
    tx = portfolio.charger_transactions(_df())
    position = 0.0
    for t in sorted(tx, key=lambda t: (t.date, 0 if t.est_achat else 1)):
        if t.est_achat:
            position += t.quantite
        else:
            assert position > 1e-9, f"vente de {t.quantite} le {t.date} sans position"
            assert t.quantite <= position + 1e-6, (
                f"vente de {t.quantite} le {t.date} > position {position}"
            )
            position -= t.quantite
