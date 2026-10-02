"""Le sens du taux de change — un défaut qui sous-estimait l'étalon de Gave.

`fx.taux(devise, date, contre)` doit répondre à « combien de `contre` vaut une
unité de `devise` ». L'ancien `_ticker_fx` renvoyait `f"{contre}{devise}=X"`
quand `devise == "EUR"`, en supposant que Yahoo inverse certaines paires rares.

Vérifié en direct : Yahoo cote les DEUX sens de toutes les paires usuelles. Le
code demandait donc `USDEUR=X` là où il fallait `EURUSD=X`, et :

| Paire demandée | Renvoyé | Attendu |
|---|---|---|
| EUR -> USD | 0,8858 | **1,1289** |
| EUR -> JPY | 0,0056 | **178,35** |

Toutes les conversions DEPUIS l'euro étaient inversées. La plus visible :
`equivalent_or_oz`, la mesure que Gave retient, était sous-estimée de 22 %.

Ces tests portent sur la logique de `_ticker_fx`, pas sur le réseau : un test
qui dépend de Yahoo casserait à chaque coupure de courant.
"""

from __future__ import annotations

import pytest

from core import fx


class TestSensDuTaux:
    @pytest.mark.parametrize("devise, contre, attendu", [
        ("EUR", "USD", "EURUSD=X"),
        ("USD", "EUR", "USDEUR=X"),
        ("EUR", "JPY", "EURJPY=X"),
        ("JPY", "EUR", "JPYEUR=X"),
        ("EUR", "CHF", "EURCHF=X"),
        ("CHF", "EUR", "CHFEUR=X"),
        ("USD", "JPY", "USDJPY=X"),
    ])
    def test_le_symbole_est_direct_dans_les_deux_sens(self, devise, contre, attendu):
        """Le symbole doit toujours être `devise` puis `contre`.

        C'est tout le contenu du correctif : l'ancien code inversait l'ordre
        quand `devise == "EUR"`.
        """
        assert fx._ticker_fx(devise, contre) == attendu

    def test_paire_identique_pas_de_symbole(self):
        assert fx._ticker_fx("EUR", "EUR") == ""

    def test_les_deux_sens_ne_sont_jamais_le_meme_symbole(self):
        """Si les deux directions donnent le même symbole, l'une des deux ment.

        C'était le cas : EUR->USD et USD->EUR renvoyaient tous deux `USDEUR=X`,
        donc la même valeur — 0,8858 — pour deux questions opposées.
        """
        for a, b in (("EUR", "USD"), ("EUR", "JPY"), ("EUR", "CHF")):
            assert fx._ticker_fx(a, b) != fx._ticker_fx(b, a)

    def test_taux_depuis_l_euro_est_superieur_a_un(self, monkeypatch):
        """1 euro vaut PLUS d'un dollar : le taux EUR->USD dépasse 1.

        Test de bon sens, sans réseau : on injecte une fausse série Yahoo.
        """
        import pandas as pd

        # Un Yahoo fidèle : chaque paire a SA valeur, et l'inverse de l'autre.
        valeurs = {"EURUSD=X": 1.13, "USDEUR=X": 1 / 1.13}

        def histoire(period=None, **kw):
            return pd.DataFrame(
                {"Close": [valeurs[periode_symbole[0]]]},
                index=pd.to_datetime(["2026-09-30"]),
            )

        periode_symbole = [""]
        classe_faux = type("T", (), {"history": staticmethod(histoire)})
        monkeypatch.setattr(
            fx.yf, "Ticker", lambda s: (periode_symbole.__setitem__(0, s), classe_faux())[1]
        )
        fx.vider_cache()

        assert fx.taux("EUR", "2026-09-30", "USD") == pytest.approx(1.13)
        fx.vider_cache()
        assert fx.taux("USD", "2026-09-30", "EUR") == pytest.approx(1 / 1.13)
