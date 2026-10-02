"""Reconstitution de l'historique — `jobs/backfill_snapshots.py`.

Pourquoi ce fichier existe
--------------------------
Les pages « Suivi » et « Performance » affichent des courbes Plotly toutes
prêtes, mais elles sont conditionnées à `pf2_snapshots` — que seul le robot
nocturne remplit, **un soir à la fois**. Un portefeuille ouvert depuis un an
aurait donc mis un an à avoir un an d'historique, et l'application n'affichait
aucun graphique pendant tout ce temps.

Le robot reconstitue le passé depuis le grand livre des transactions, les cours
historiques de Yahoo et les taux de change historiques. Ces tests vérifient
surtout deux règles : **la date butoir est respectée** (une journée ne voit pas
le futur) et **le schéma écrit est identique à celui du robot nocturne** (sinon
les deux robots ne seraient pas interchangeables).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core import portfolio
from jobs import backfill_snapshots as bf

# Les colonnes exactes qu'écrit `jobs/daily_snapshot.py`. Les deux robots
# doivent rester interchangeables : le nocturne reprend le relais au premier
# soir qui suit la reconstitution.
COLONNES_ATTENDUES = {
    "date", "patrimoine_total_eur", "patrimoine_investi_eur", "precaution_eur",
    "courant_eur", "cours_or_usd", "equivalent_or_oz", "poche_rv_eur",
    "poche_energie_eur", "poche_asie_eur", "poche_jgb_eur",
}


def _tx(ticker, sens, date, quantite, cours, devise="USD"):
    return portfolio.Transaction(
        ticker=ticker, type=sens, date=dt.date.fromisoformat(date),
        quantite=quantite, cours=cours, frais=0.0, devise=devise,
        montant_net=quantite * cours,
    )


GRAND_LIVRE = [
    _tx("FLXC.L", "achat", "2025-03-18", 100, 30.0),
    _tx("FLXC.L", "achat", "2025-04-15", 50, 26.0),
    _tx("FLXC.L", "vente", "2025-07-01", 22, 29.0),
    _tx("FLXC.L", "achat", "2025-10-13", 18, 33.0),
]


def _simuler_yahoo(monkeypatch, valeurs):
    """Cours plat dans le temps : la valeur ne dépend que de la quantité.

    Une paire de change (symbole en `=X`) et l'or (`=F`) doivent valoir 1,0 :
    sinon la conversion multiplie la valeur par le cours simulé.
    """
    idx = pd.date_range("2025-01-02", periods=len(valeurs), tz="Europe/London")
    frame = pd.DataFrame({"Close": valeurs}, index=idx)

    class Ticker:
        def __init__(self, symbole):
            self.symbole = symbole

        def history(self, period=None, start=None, end=None):
            if "=X" in self.symbole or "=F" in self.symbole:
                plat = pd.DataFrame({"Close": [1.0] * len(valeurs)}, index=idx)
                return plat
            return frame

    for module in (bf.prices, bf.fx):
        monkeypatch.setattr(module.yf, "Ticker", Ticker)


@pytest.fixture(autouse=True)
def _caches_vides(monkeypatch):
    bf.prices.vider_cache()
    bf.fx.vider_cache()
    bf.prices._series.clear()
    yield
    bf.prices.vider_cache()
    bf.fx.vider_cache()
    bf.prices._series.clear()


# --------------------------------------------------------------------------
def test_le_schema_ecrit_est_celui_du_robot_nocturne(monkeypatch):
    _simuler_yahoo(monkeypatch, [30.0, 30.0, 30.0])
    ligne = bf._snapshot_au(GRAND_LIVRE, dt.date(2025, 6, 1))
    assert ligne is not None
    assert set(ligne) == COLONNES_ATTENDUES, (
        "le robot nocturne et la reconstitution doivent écrire les mêmes colonnes"
    )


def test_une_journee_anterieure_a_tout_achat_est_sautee(monkeypatch):
    _simuler_yahoo(monkeypatch, [30.0, 30.0, 30.0])
    assert bf._snapshot_au(GRAND_LIVRE, dt.date(2025, 1, 5)) is None


def test_la_date_butoir_est_respectee(monkeypatch):
    """Une journée ne doit voir que les transactions déjà réalisées.

    Au 1er juin 2025, la vente du 1er juillet n'a pas encore eu lieu : la
    position est donc de 150 parts, pas 128. C'est le garde-fou contre le biais
    d'anticulation, côté grand livre.
    """
    _simuler_yahoo(monkeypatch, [30.0, 30.0, 30.0])
    ligne = bf._snapshot_au(GRAND_LIVRE, dt.date(2025, 6, 1))
    assert ligne is not None
    # 150 parts à 30 USD = 4 500 USD, convertis au taux simulé de 1,0
    assert ligne["patrimoine_investi_eur"] == pytest.approx(4500.0, rel=1e-3)


def test_apres_la_vente_la_position_diminue(monkeypatch):
    _simuler_yahoo(monkeypatch, [30.0, 30.0, 30.0])
    avant = bf._snapshot_au(GRAND_LIVRE, dt.date(2025, 6, 30))
    apres = bf._snapshot_au(GRAND_LIVRE, dt.date(2025, 7, 2))
    assert avant is not None and apres is not None
    assert apres["patrimoine_investi_eur"] < avant["patrimoine_investi_eur"]


def test_la_poche_asie_est_peuplee(monkeypatch):
    """FLXC.L est dans « Asie / Chine » : la correction de poche doit se voir."""
    _simuler_yahoo(monkeypatch, [30.0, 30.0, 30.0])
    ligne = bf._snapshot_au(GRAND_LIVRE, dt.date(2025, 6, 1))
    assert ligne["poche_asie_eur"] == pytest.approx(4500.0, rel=1e-3)


def test_le_premier_jour_est_celui_de_la_premiere_transaction():
    assert bf._premier_jour(GRAND_LIVRE) == dt.date(2025, 3, 18)
    assert bf._premier_jour([]) is None


def test_grand_livre_vide_renvoie_none(monkeypatch):
    _simuler_yahoo(monkeypatch, [30.0, 30.0, 30.0])
    assert bf._snapshot_au([], dt.date(2025, 6, 1)) is None


def test_le_plafond_borne_la_plage():
    """Au-delà, on prévient plutôt que de mouliner des milliers de jours."""
    assert bf.PLAFOND_JOURS >= 400
    assert bf.PLAFOND_JOURS <= 2000


def test_la_serie_est_chargee_une_seule_fois(monkeypatch):
    """Le gain de performance : un appel Yahoo par ticker, pas un par journée."""
    appels = []

    class Ticker:
        def __init__(self, symbole):
            self.symbole = symbole

        def history(self, period=None, start=None, end=None):
            appels.append(self.symbole)
            idx = pd.date_range("2025-01-02", periods=3, tz="Europe/London")
            return pd.DataFrame({"Close": [30.0, 30.0, 30.0]}, index=idx)

    for module in (bf.prices, bf.fx):
        monkeypatch.setattr(module.yf, "Ticker", Ticker)
    bf.prices._series.clear()

    for jour in [dt.date(2025, 6, 1), dt.date(2025, 6, 2), dt.date(2025, 6, 3)]:
        bf._snapshot_au(GRAND_LIVRE, jour)

    # Un titre, un seul chargement d'histoire malgré trois journées.
    assert appels.count("FLXC.L") == 1, f"{len(appels)} appels : {appels}"
