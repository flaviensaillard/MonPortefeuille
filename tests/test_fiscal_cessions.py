"""Cessions fiscales — ce que la page Fiscalité ne fournissait pas.

Deux défauts se sont manifestés d'un coup sur `pages/7_Fiscalite.py`, tous deux
du même type : la page construisait des lignes incomplètes, et le moteur fiscal
ne s'en apercevait qu'en plein calcul.

**1. `prix_cession_eur` n'était pas en euros.** Il était rempli avec
`t.montant_net`, qui est dans la devise de COTATION — dollars pour FLXC.L et
BTCUSDT, yen pour XJSE.SW. Le PRU, lui, est en euros. La plus-value mélangeait
donc deux monnaies et était fausse pour tout actif non coté en euro. Silencieux :
aucune erreur, juste un chiffre faux.

**2. Les cessions crypto ne portaient aucun des trois chiffres de l'article
150 VH bis** (lignes 212, 220, 221 du formulaire 2086-SD). `pv_crypto` les lisait
à zéro et son garde-fou faisait sauter la page entière.

D'où les tests ci-dessous, qui verrouillent les deux.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core import tax
from core.models import Classe
from core.portfolio import Transaction


def _tx(ticker: str, typ: str, jour: str, quantite: float, cours: float,
        devise: str = "USD", frais: float = 0.0) -> Transaction:
    net = quantite * cours
    net = net + frais if typ == "achat" else net - frais
    return Transaction(
        ticker=ticker, type=typ, date=dt.date.fromisoformat(jour),
        quantite=quantite, cours=cours, frais=frais, devise=devise,
        montant_net=net,
    )


def _faux_fx(taux: float):
    return lambda devise, date, contre="EUR": taux


def _faux_cours(cours: float):
    return lambda ticker, date=None: cours


# ---------------------------------------------------------------------------
# 1. Le prix de cession doit être converti en euros
# ---------------------------------------------------------------------------
class TestPrixCessionEnEuros:
    def test_un_actif_cote_en_dollars_est_converti(self, monkeypatch):
        monkeypatch.setattr("core.fx.taux", _faux_fx(0.5))
        ventes = [_tx("FLXC.L", "vente", "2025-03-01", 10, 100.0)]
        cessions = tax.cessions_de_lannee(ventes, {}, 2025)
        ligne = cessions[Classe.ACTION_ETF][0]
        # 10 x 100 = 1 000 dollars, à 0,5 € le dollar
        assert ligne["prix_cession_eur"] == pytest.approx(500.0)

    def test_un_actif_cote_en_yen_est_converti(self, monkeypatch):
        monkeypatch.setattr("core.fx.taux", _faux_fx(0.006))
        ventes = [_tx("XJSE.SW", "vente", "2025-03-01", 10, 1000.0, devise="JPY")]
        cessions = tax.cessions_de_lannee(ventes, {}, 2025)
        ligne = cessions[Classe.OBLIGATION_ETF][0]
        assert ligne["prix_cession_eur"] == pytest.approx(10 * 1000.0 * 0.006)

    def test_sans_taux_le_montant_reste_brut(self, monkeypatch):
        """Pas de taux à cette date : mieux vaut un montant brut qu'une invention."""
        from core import fx

        def taux_absent(devise, date, contre="EUR"):
            raise fx.FXIndisponible(devise, contre, date, "pas de taux")

        monkeypatch.setattr("core.fx.taux", taux_absent)
        ventes = [_tx("FLXC.L", "vente", "2025-03-01", 10, 100.0)]
        cessions = tax.cessions_de_lannee(ventes, {}, 2025)
        assert cessions[Classe.ACTION_ETF][0]["prix_cession_eur"] == pytest.approx(1000.0)

    def test_les_achats_ne_sont_pas_des_cessions(self, monkeypatch):
        monkeypatch.setattr("core.fx.taux", _faux_fx(1.0))
        lignes = [_tx("FLXC.L", "achat", "2025-03-01", 10, 100.0)]
        assert tax.cessions_de_lannee(lignes, {}, 2025) == {}

    def test_une_autre_annee_est_ignoree(self, monkeypatch):
        monkeypatch.setattr("core.fx.taux", _faux_fx(1.0))
        lignes = [_tx("FLXC.L", "vente", "2024-03-01", 10, 100.0)]
        assert tax.cessions_de_lannee(lignes, {}, 2025) == {}


# ---------------------------------------------------------------------------
# 2. Les trois chiffres de l'article 150 VH bis
# ---------------------------------------------------------------------------
class TestEnrichirCrypto:
    TRANSACTIONS = [
        _tx("BTCUSDT", "achat", "2024-01-01", 10, 50.0),     # coût 500
        _tx("BTCUSDT", "vente", "2025-06-01", 4, 200.0),     # cession 800
    ]

    def _ligne(self):
        return [{
            "actif": "BTCUSDT", "date": dt.date(2025, 6, 1), "quantite": 4,
            "pru_eur": 50.0, "prix_cession_eur": 800.0, "sens": "vente",
        }]

    def test_valeur_globale_au_moment_de_la_cession(self, monkeypatch):
        """6 unités restantes à 100 € : la valeur globale est 600, pas 800."""
        monkeypatch.setattr("core.fx.taux", _faux_fx(1.0))
        monkeypatch.setattr("core.prices.cours", _faux_cours(100.0))
        lignes = self._ligne()
        tax.enrichir_crypto(lignes, self.TRANSACTIONS)
        assert lignes[0]["valeur_globale_eur"] == pytest.approx(600.0)

    def test_cout_total_d_acquisition_du_portefeuille(self, monkeypatch):
        monkeypatch.setattr("core.fx.taux", _faux_fx(1.0))
        monkeypatch.setattr("core.prices.cours", _faux_cours(100.0))
        lignes = self._ligne()
        tax.enrichir_crypto(lignes, self.TRANSACTIONS)
        # 6 unités restantes au PRU de 50 €
        assert lignes[0]["cout_total_acquisition_eur"] == pytest.approx(300.0)

    def test_premiere_cession_na_aucune_fraction_deja_prise(self, monkeypatch):
        monkeypatch.setattr("core.fx.taux", _faux_fx(1.0))
        monkeypatch.setattr("core.prices.cours", _faux_cours(100.0))
        lignes = self._ligne()
        tax.enrichir_crypto(lignes, self.TRANSACTIONS)
        assert lignes[0]["fractions_deja_prises"] == pytest.approx(0.0)

    def test_prix_manquant_remet_la_valeur_a_zero(self, monkeypatch):
        """Un chiffre approximatif serait pire qu'un garde-fou qui se déclenche."""
        from core import prices

        def cours_absent(ticker, date=None):
            raise prices.CoursIndisponible(ticker, str(date), "pas de cours")

        monkeypatch.setattr("core.fx.taux", _faux_fx(1.0))
        monkeypatch.setattr("core.prices.cours", cours_absent)
        lignes = self._ligne()
        tax.enrichir_crypto(lignes, self.TRANSACTIONS)
        assert lignes[0]["valeur_globale_eur"] == 0.0

    def test_fractions_cumulees_sur_plusieurs_cessions(self, monkeypatch):
        """Deux cessions : la seconde doit tenir compte de la fraction déjà prise."""
        monkeypatch.setattr("core.fx.taux", _faux_fx(1.0))
        monkeypatch.setattr("core.prices.cours", _faux_cours(100.0))
        transactions = self.TRANSACTIONS + [
            _tx("BTCUSDT", "vente", "2025-09-01", 2, 300.0),
        ]
        lignes = [
            {"actif": "BTCUSDT", "date": dt.date(2025, 6, 1), "quantite": 4,
             "pru_eur": 50.0, "prix_cession_eur": 800.0, "sens": "vente"},
            {"actif": "BTCUSDT", "date": dt.date(2025, 9, 1), "quantite": 2,
             "pru_eur": 50.0, "prix_cession_eur": 600.0, "sens": "vente"},
        ]
        tax.enrichir_crypto(lignes, transactions)
        assert lignes[0]["fractions_deja_prises"] == pytest.approx(0.0)
        assert lignes[1]["fractions_deja_prises"] > 0.0

    def test_sans_cession_crypto_rien_nest_ajoute(self):
        lignes: list[dict] = []
        tax.enrichir_crypto(lignes, [_tx("FLXC.L", "vente", "2025-06-01", 4, 200.0)])
        assert lignes == []


# ---------------------------------------------------------------------------
# 3. Le garde-fou ne doit plus faire sauter la page
# ---------------------------------------------------------------------------
LIGNE_SANS_VALEUR = [{
    "actif": "BTCUSDT", "date": dt.date(2025, 6, 1), "quantite": 4,
    "pru_eur": 50.0, "prix_cession_eur": 800.0, "sens": "vente",
    "valeur_globale_eur": 0.0, "cout_total_acquisition_eur": 300.0,
    "fractions_deja_prises": 0.0,
}]


class TestPvCryptoNeFaitPlusSauterLaPage:
    def test_sans_liste_leve_toujours(self):
        """Comportement des robots : mieux vaut s'arrêter que livrer un chiffre faux."""
        with pytest.raises(ValueError, match="Valeur globale"):
            tax.pv_crypto(list(LIGNE_SANS_VALEUR), 2025)

    def test_avec_liste_ne_leve_pas(self):
        anomalies: list[str] = []
        tax.pv_crypto(list(LIGNE_SANS_VALEUR), 2025, anomalies)
        assert len(anomalies) == 1
        assert "Valeur globale" in anomalies[0]

    def test_la_cession_problematique_est_ignoree_pas_les_autres(self):
        """Une ligne illisible ne doit pas emporter les bonnes avec elle."""
        bonne = dict(LIGNE_SANS_VALEUR[0])
        bonne["date"] = dt.date(2025, 7, 1)
        bonne["valeur_globale_eur"] = 1000.0
        anomalies: list[str] = []
        r = tax.pv_crypto(list(LIGNE_SANS_VALEUR) + [bonne], 2025, anomalies)
        assert len(anomalies) == 1
        assert len(r.detail) == 1          # seule la bonne ligne est retenue

    def test_calculer_transmet_les_anomalies(self):
        anomalies: list[str] = []
        resultat = tax.calculer(
            {Classe.CRYPTO: list(LIGNE_SANS_VALEUR)}, 2025, anomalies=anomalies
        )
        assert anomalies
        assert "pv_crypto" in resultat

    def test_calculer_sans_anomalies_leve_encore(self):
        with pytest.raises(ValueError, match="Valeur globale"):
            tax.calculer({Classe.CRYPTO: list(LIGNE_SANS_VALEUR)}, 2025)


# ---------------------------------------------------------------------------
# 4. Le calcul nominal, une fois les chiffres fournis
# ---------------------------------------------------------------------------
class TestCalculCryptoNominal:
    def test_plus_value_calculee_avec_les_chiffres_du_portefeuille(self, monkeypatch):
        monkeypatch.setattr("core.fx.taux", _faux_fx(1.0))
        monkeypatch.setattr("core.prices.cours", _faux_cours(100.0))
        transactions = [
            _tx("BTCUSDT", "achat", "2024-01-01", 10, 50.0),
            _tx("BTCUSDT", "vente", "2025-06-01", 4, 200.0),
        ]
        cessions = tax.cessions_de_lannee(transactions, {}, 2025)
        anomalies: list[str] = []
        resultat = tax.calculer(cessions, 2025, anomalies=anomalies)

        assert anomalies == []
        assert "pv_crypto" in resultat
        r = resultat["pv_crypto"]
        assert r.plus_value_brute > 0

        # ligne 223 = 300 x (800 / 600) = 400 ; ligne 224 = 800 - 400 = 400
        assert r.detail[0].plus_value_eur == pytest.approx(400.0)

    def test_la_franchise_de_305_porte_sur_les_cessions(self, monkeypatch):
        """800 EUR de cessions : bien au-dessus du seuil, donc rien à déduire.

        Ce test s'appelait `test_l_abattement_de_305_est_applique` et attendait
        `min(305, pv_brute)` retirés du gain. Il n'y a pas d'abattement crypto :
        le seuil de 305 EUR est une franchise assise sur les prix de cession.
        """
        monkeypatch.setattr("core.fx.taux", _faux_fx(1.0))
        monkeypatch.setattr("core.prices.cours", _faux_cours(100.0))
        transactions = [
            _tx("BTCUSDT", "achat", "2024-01-01", 10, 50.0),
            _tx("BTCUSDT", "vente", "2025-06-01", 4, 200.0),
        ]
        cessions = tax.cessions_de_lannee(transactions, {}, 2025)
        r = tax.pv_crypto(cessions[Classe.CRYPTO], 2025)
        assert r.total_cessions == pytest.approx(800.0)
        assert r.exonere_par_franchise is False
        assert r.abattement == pytest.approx(0.0)
        assert r.plus_value_imposable == pytest.approx(r.plus_value_brute)
