"""Ordre des transactions et transactions incohérentes.

Pourquoi ce fichier existe
---------------------------
L'utilisateur a vu, sans raison apparente :

    Transactions illisibles : Vente de 22.0 FLXC.L le 2025-01-07 sans position
    détenue.

La donnée était pourtant correcte. Le tri de `charger_transactions` était
`(date, ticker)`. Pour un achat et une vente du même titre le même jour, la clé
de tri est donc identique — et le tri stable de Python conserve l'ordre de la
table d'origine. Si la vente était simplement rangée avant son achat dans la v1,
le calcul des positions la traitait la première et échouait.

D'où deux corrections : traiter les achats avant les ventes à date égale, et
rapporter une ligne incohérente au lieu de faire échouer toute l'application.
"""

from __future__ import annotations

import pandas as pd
import pytest


def _df(lignes):
    """Des lignes au format snake_case de `pf2_transactions`."""
    return pd.DataFrame(lignes)


ACHAT_FLXC = {
    "ticker": "FLXC.L", "sens": "achat", "date": "2025-01-07",
    "quantite": 22, "cours": 100, "frais": 0, "devise": "GBP",
    "source": "import_v1", "reference": "v1:id10",
}
VENTE_FLXC = {
    "ticker": "FLXC.L", "sens": "vente", "date": "2025-01-07",
    "quantite": 22, "cours": 110, "frais": 0, "devise": "GBP",
    "source": "import_v1", "reference": "v1:id11",
}


# ---------------------------------------------------------------------------
# Le bug : achat et vente le même jour
# ---------------------------------------------------------------------------

def test_achat_avant_vente_le_meme_jour():
    """La reproduction exacte : la vente est rangée AVANT l'achat dans la table."""
    from core.portfolio import charger_transactions

    tx = charger_transactions(_df([VENTE_FLXC, ACHAT_FLXC]))

    # L'achat doit être traité en premier, quoi que dise l'ordre de la table.
    assert tx[0].est_achat, "l'achat doit précéder la vente à date égale"
    assert tx[1].est_vente


def test_achat_avant_vente_le_meme_jour_calcule_la_position(monkeypatch):
    """Et le calcul des positions aboutit, au lieu de lever « sans position »."""
    from core import fx, portfolio

    monkeypatch.setattr(fx, "taux", lambda devise, date, contre="EUR": 1.0)

    tx = portfolio.charger_transactions(_df([VENTE_FLXC, ACHAT_FLXC]))
    positions = portfolio.calculer_positions(tx)

    pos = positions["FLXC.L"]
    # 22 achetés puis 22 vendus le même jour : la position retombe à zéro.
    assert pos.quantite == pytest.approx(0.0)


def test_achat_partiellement_vendu_le_meme_jour(monkeypatch):
    from core import fx, portfolio

    monkeypatch.setattr(fx, "taux", lambda devise, date, contre="EUR": 1.0)

    vente = {**VENTE_FLXC, "quantite": 10}
    tx = portfolio.charger_transactions(_df([vente, ACHAT_FLXC]))
    positions = portfolio.calculer_positions(tx)

    assert positions["FLXC.L"].quantite == pytest.approx(12.0)


def test_le_tri_reste_chronologique(monkeypatch):
    """Le correctif ne doit pas casser l'ordre entre des jours différents."""
    from core import portfolio

    tx = portfolio.charger_transactions(_df([
        VENTE_FLXC,
        {**ACHAT_FLXC, "date": "2025-01-02"},   # achat AVANT la vente
        {**ACHAT_FLXC, "date": "2025-01-09"},   # achat APRÈS la vente
    ]))

    dates = [t.date.isoformat() for t in tx]
    assert dates == sorted(dates), "le tri doit rester chronologique"


def test_ordre_des_achats_entre_eux_inchange(monkeypatch):
    """Deux achats le même jour : leur ordre relatif est sans importance pour le
    PRU, mais il ne doit pas être mélangé avec les ventes."""
    from core import portfolio

    a1 = {**ACHAT_FLXC, "quantite": 10, "cours": 100}
    a2 = {**ACHAT_FLXC, "quantite": 12, "cours": 105}
    tx = portfolio.charger_transactions(_df([VENTE_FLXC, a2, a1]))

    assert [t.cours for t in tx[:2]] == [105, 100]   # ordre stable
    assert tx[2].est_vente


# ---------------------------------------------------------------------------
# Résilience : une ligne douteuse ne vide plus l'écran
# ---------------------------------------------------------------------------

def test_vente_sans_position_leve_encore_sans_liste(monkeypatch):
    """Comportement des robots : mieux vaut s'arrêter que calculer faux."""
    from core import fx, portfolio

    monkeypatch.setattr(fx, "taux", lambda devise, date, contre="EUR": 1.0)
    tx = portfolio.charger_transactions(_df([VENTE_FLXC]))

    with pytest.raises(ValueError, match="sans position détenue"):
        portfolio.calculer_positions(tx)


def test_vente_sans_position_est_consignee_si_on_passe_une_liste(monkeypatch):
    """Comportement de l'application : on signale et on continue."""
    from core import fx, portfolio

    monkeypatch.setattr(fx, "taux", lambda devise, date, contre="EUR": 1.0)
    tx = portfolio.charger_transactions(_df([VENTE_FLXC]))

    anomalies: list[str] = []
    positions = portfolio.calculer_positions(tx, anomalies)

    assert len(anomalies) == 1
    assert "FLXC.L" in anomalies[0]
    assert "sans position détenue" in anomalies[0]
    # La ligne fautive est ignorée, elle ne crée pas de quantité négative.
    assert positions["FLXC.L"].quantite == pytest.approx(0.0)


def test_vente_superieure_au_detenu_est_consignee(monkeypatch):
    from core import fx, portfolio

    monkeypatch.setattr(fx, "taux", lambda devise, date, contre="EUR": 1.0)

    vente = {**VENTE_FLXC, "date": "2025-02-01", "quantite": 50}
    tx = portfolio.charger_transactions(_df([ACHAT_FLXC, vente]))

    anomalies: list[str] = []
    portfolio.calculer_positions(tx, anomalies)

    assert len(anomalies) == 1
    assert "supérieure" in anomalies[0]


def test_portefeuille_sain_ne_produit_aucune_anomalie(monkeypatch):
    """Un portefeuille cohérent ne doit pas être signalé à tort."""
    from core import fx, portfolio

    monkeypatch.setattr(fx, "taux", lambda devise, date, contre="EUR": 1.0)
    tx = portfolio.charger_transactions(_df([
        ACHAT_FLXC,
        {**VENTE_FLXC, "quantite": 10},
    ]))

    anomalies: list[str] = []
    positions = portfolio.calculer_positions(tx, anomalies)

    assert anomalies == []
    assert positions["FLXC.L"].quantite == pytest.approx(12.0)
