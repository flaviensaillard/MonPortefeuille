"""Appartenance des tickers aux poches.

Défaut de production : FLXC.L (Franklin FTSE China) était classé dans la poche
« Énergie » à côté de XDW0.L, et la poche « Asie / Chine » ne contenait que
RI.PA — un titre que l'utilisateur ne détient pas. Résultat à l'écran :
« Asie / Chine » à 0 %, ce qui est illisible alors qu'on détient 800 parts d'un
ETF chinois. La leçon : une classe d'actifs n'est pas une devinette, elle se
vérifie contre le nom du titre.
"""

from __future__ import annotations

from core.models import POCHES, poche_de


def test_flxc_est_dans_la_poche_asie():
    """FLXC.L = Franklin FTSE China UCITS ETF (ISIN IE00BHZRR147)."""
    assert poche_de("FLXC.L") is not None
    assert poche_de("FLXC.L").cle == "asie"


def test_flxc_n_est_pas_dans_energie():
    assert "FLXC.L" not in poche_de("XDW0.L").membres
    energie = next(p for p in POCHES if p.cle == "energie")
    assert energie.membres == ["XDW0.L"]


def test_poche_asie_non_vide():
    """Une poche sans membre détenu affiche 0 % — d'où le message absurde."""
    asie = next(p for p in POCHES if p.cle == "asie")
    assert asie.membres, "la poche Asie ne doit jamais être vide"
    assert "FLXC.L" in asie.membres


def test_toutes_les_poches_investies_ont_un_membre():
    for poche in POCHES:
        if poche.cible > 0:
            assert poche.membres, f"poche {poche.cle} : cible > 0 sans membre"


def test_chaque_ticker_appartient_a_au_plus_une_poche():
    vus: dict[str, str] = {}
    for poche in POCHES:
        for ticker in poche.membres:
            assert ticker not in vus, (
                f"{ticker} est dans {vus[ticker]} ET {poche.cle} : "
                "il serait compté deux fois dans l'allocation"
            )
            vus[ticker] = poche.cle
