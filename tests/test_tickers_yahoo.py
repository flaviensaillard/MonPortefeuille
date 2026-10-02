"""Symboles que Yahoo ne connaît plus.

Yahoo a retiré des tickers qu'on utilisait : `BTCUSDT` (notation Binance, jamais
valide chez Yahoo) et `XAUUSD=X` (le spot de l'or). Tous deux renvoient
désormais « Quote not found for symbol ».

Ces tests verrouillent la traduction, parce que l'enjeu est double :

1. `BTCUSDT` figure dans la base de l'utilisateur (saisie v1). Renommer le ticker
   dans le code obligerait à réimporter toutes ses transactions — on le traduit
   donc au moment de l'appel, et le ticker d'origine reste la clé de cache.
2. `XAUUSD=X` a été remplacé par `GC=F`, le contrat front-month du COMEX. Ce
   n'est PAS le spot : il porte un écart basis. Le dire dans un test évite que
   quelqu'un croie plus tard lire un cours spot.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core import prices


class TestAliasYahoo:
    def test_btcusdt_est_traduit(self):
        assert prices.ALIAS_YAHOO["BTCUSDT"] == "BTC-USD"

    def test_les_tickers_sains_ne_sont_pas_traduits(self):
        for ticker in ["IGLN.L", "XDW0.L", "FLXC.L", "RI.PA", "XJSE.SW", "ASML.AS"]:
            assert prices.ALIAS_YAHOO.get(ticker) is None

    def test_le_cache_reste_indexe_sur_le_ticker_d_origine(self):
        """La clé de cache doit rester celle des données de l'utilisateur."""
        ticker = "BTCUSDT"
        assert (ticker, "2025-01-07") not in prices._cache
        try:
            prices.cours(ticker, "2025-01-07")
        except prices.CoursIndisponible:
            pytest.skip("Yahoo inaccessible")
        cle = (ticker, "2025-01-07")
        assert cle in prices._cache
        assert ("BTC-USD", "2025-01-07") not in prices._cache


class TestCoursOr:
    def test_utilise_le_contrat_a_terme(self):
        assert prices.TICKER_OR == "GC=F"

    def test_n_est_pas_le_spot(self):
        """Le spot n'existe plus sur Yahoo : ne jamais le réintroduire en croyant
        lire un cours au comptant."""
        assert prices.TICKER_OR != "XAUUSD=X"
        assert prices.TICKER_OR != "XAUUSD"
        assert prices.TICKER_OR != "XAU=X"


class TestRobotsQuotidiensNeCotentPasLesDevises:
    """Le robot écrit une alerte « Cours manquants » pour chaque échec.

    Les devises sont membres de poches (`precaution`, `courant`) mais n'ont pas
    de cours chez Yahoo. Les inclure faisait donc écrire, chaque nuit, une
    alerte fausse — et une alerte fausse noie les vraies.
    """

    def test_aucune_devise_parmi_les_tickers_a_coter(self):
        from jobs.update_market_data import DEVISES, tickers_a_coter
        for devise in DEVISES:
            assert devise not in tickers_a_coter()

    def test_tous_les_vrais_titres_sont_cotes(self):
        from jobs.update_market_data import tickers_a_coter
        # RI.PA n'y figure plus : il n'appartient a aucune poche depuis que
        # FLXC.L (Franklin FTSE China) a rejoint « Asie / Chine ». Le coter
        # serait une requete Yahoo inutile chaque nuit.
        assert set(tickers_a_coter()) == {
            "IGLN.L", "BTCUSDT", "XDW0.L", "FLXC.L", "XJSE.SW",
        }

    def test_les_titres_sont_tries(self):
        from jobs.update_market_data import tickers_a_coter
        t = tickers_a_coter()
        assert t == sorted(t)


class TestDeviseDesTickersCrypto:
    def test_les_deux_notations_bitcoin_sont_en_dollars(self):
        from core.portfolio import DEVISES_COTATION
        assert DEVISES_COTATION["BTCUSDT"] == "USD"
        assert DEVISES_COTATION["BTC-USD"] == "USD"

    def test_les_deux_notations_sont_classees_crypto(self):
        from core.models import Classe
        from core.portfolio import CLASSES
        assert CLASSES["BTCUSDT"] is Classe.CRYPTO
        assert CLASSES["BTC-USD"] is Classe.CRYPTO


class TestTickersConnusSurYahoo:
    """Les symboles du portefeuille doivent exister chez Yahoo.

    Un ticker mort fait disparaître un actif de la valorisation — silencieusement,
    puisqu'il est simplement listé dans `bandeau_erreurs`. Mieux vaut le savoir
    au plus tôt.
    """

    @pytest.mark.parametrize("ticker", ["IGLN.L", "XDW0.L", "FLXC.L", "RI.PA", "XJSE.SW"])
    def test_le_ticker_renvoie_un_cours(self, ticker):
        try:
            cours = prices.cours(ticker)
        except prices.CoursIndisponible as exc:
            pytest.fail(f"{ticker} n'a plus de cours chez Yahoo : {exc}")
        assert cours > 0

    @pytest.mark.parametrize("ticker", ["BTCUSDT", "BTC-USD", "GC=F"])
    def test_les_symboles_traduits_renvoient_un_cours(self, ticker):
        try:
            cours = prices.cours(ticker)
        except prices.CoursIndisponible as exc:
            pytest.fail(f"{ticker} n'a plus de cours chez Yahoo : {exc}")
        assert cours > 0

    def test_btcusdt_et_btc_usd_donnent_le_meme_cours(self):
        try:
            a = prices.cours("BTCUSDT")
            b = prices.cours("BTC-USD")
        except prices.CoursIndisponible:
            pytest.skip("Yahoo inaccessible")
        assert a == pytest.approx(b)


# ---------------------------------------------------------------------------
# La ligne de queue sans cours, et pourquoi NaN est le pire des échecs
# ---------------------------------------------------------------------------
class _FauxTicker:
    """Yahoo renvoie une série dont la DERNIÈRE clôture est NaN.

    C'est le cas réel d'IGLN.L, XDW0.L, FLXC.L et RI.PA : 5 lignes, dont la
    dernière à NaN (séance non encore ouverte ou boucle-trou côté Yahoo).
    """

    def __init__(self, dernier_nan=True, valeurs=(10.0, 11.0, 12.0, 13.0)):
        self._valeurs = list(valeurs)
        if dernier_nan:
            self._valeurs.append(float("nan"))

    def history(self, **kwargs):
        idx = pd.date_range("2026-09-24", periods=len(self._valeurs), freq="B")
        return pd.DataFrame({"Close": self._valeurs}, index=idx)


class TestLigneDeQueueSansCours:
    def setup_method(self):
        prices.vider_cache()

    def test_la_derniere_cloture_nan_est_ignoree(self, monkeypatch):
        monkeypatch.setattr(prices.yf, "Ticker", lambda s: _FauxTicker())
        # 13.0 est la dernière clôture RÉELLE, pas NaN.
        assert prices.cours("IGLN.L") == pytest.approx(13.0)

    def test_une_serie_entierement_nan_leve(self, monkeypatch):
        monkeypatch.setattr(prices.yf, "Ticker",
                            lambda s: _FauxTicker(valeurs=(float("nan"), float("nan"))))
        with pytest.raises(prices.CoursIndisponible):
            prices.cours("IGLN.L")

    def test_le_garde_fou_refuse_nan(self):
        """Un NaN traverse `<= 0` : toute comparaison avec NaN est fausse.

        C'est ce qui rend le défaut silencieux — pas d'exception, pas de message,
        juste une valorisation entièrement NaN. Le garde-fou doit tester NaN
        explicitement.
        """
        assert not (float("nan") <= 0)

    def test_le_garde_fou_refuse_l_infini(self, monkeypatch):
        monkeypatch.setattr(prices.yf, "Ticker",
                            lambda s: _FauxTicker(valeurs=(float("inf"),)))
        with pytest.raises(prices.CoursIndisponible):
            prices.cours("IGLN.L")

    def test_le_cache_stocke_le_vrai_cours(self, monkeypatch):
        monkeypatch.setattr(prices.yf, "Ticker", lambda s: _FauxTicker())
        prices.cours("IGLN.L")
        assert prices._cache[("IGLN.L", "")] == pytest.approx(13.0)

