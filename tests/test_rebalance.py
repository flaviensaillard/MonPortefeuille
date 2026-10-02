"""Tests du moteur de rééquilibrage.

Ces tests verrouillent les corrections apportées à la v1 : l'assiette exclut
explicitement l'épargne de précaution, et les bandes sont par poche.
"""

from __future__ import annotations

import pytest

from core.models import Perimetre, Poche
from core.portfolio import EtatPoche, agreger_par_poche
from core.rebalance import EcartPoche, diagnostiquer, generer_ordres


def _actif(ticker, valeur, poche):
    from core.models import Actif, Classe
    return Actif(ticker=ticker, classe=Classe.OR, devise_cotation="USD",
                 poche=poche, quantite=1.0, prix=valeur, valeur_eur=valeur)


class TestAssiette:
    def test_la_precaution_n_entre_pas_dans_l_assiette(self):
        """Le bug central de la v1 : les 'Cash réserve' étaient ignorés.

        Le bon comportement n'est pas de les inclure dans l'assiette — leur
        pondération n'a aucun sens — mais de les EXCLURE explicitement, et de
        les afficher à part.
        """
        actifs = [
            _actif("IGLN.L", 8000, "rv"),
            _actif("XDW0.L", 12000, "energie"),
            _actif("CHF", 12000, "precaution"),
        ]
        etats = agreger_par_poche(actifs, total_investi_eur=20000.0)

        # 20 000 investis, pas 32 000.
        assert etats["rv"].poids_reel == pytest.approx(0.40)
        assert etats["energie"].poids_reel == pytest.approx(0.60)
        # La précaution est suivie mais son poids reste à 0 : hors allocation.
        assert etats["precaution"].valeur_eur == pytest.approx(12000.0)
        assert etats["precaution"].poids_reel == pytest.approx(0.0)

    def test_actif_inconnu_est_signale(self):
        actifs = [_actif("ZZZZ", 5000, "inconnu")]
        etats = agreger_par_poche(actifs, total_investi_eur=5000.0)
        assert "inconnu" in etats


class TestBandes:
    def _etat(self, cle, reel, cible, bande, valeur=10000.0):
        return EtatPoche(
            poche=Poche(cle=cle, nom=cle, cible=cible, bande=bande,
                        perimetre=Perimetre.INVESTI),
            valeur_eur=valeur, poids_reel=reel, poids_cible=cible,
        )

    def test_dans_la_bande(self):
        e = self._etat("rv", 0.20, 0.20, 0.03)
        assert not e.hors_bande

    def test_hors_bande_au_dela_de_trois_points(self):
        e = self._etat("rv", 0.24, 0.20, 0.03)
        assert e.hors_bande
        assert e.ecart_points == pytest.approx(4.0)

    def test_exactement_sur_la_borne(self):
        e = self._etat("rv", 0.23, 0.20, 0.03)
        assert not e.hors_bande

    def test_bande_plus_large_pour_les_poches_grosses(self):
        """La réserve de valeur a une bande plus serrée que les poches actions."""
        rv = self._etat("rv", 0.24, 0.20, 0.03)
        energie = self._etat("energie", 0.34, 0.30, 0.05)
        assert rv.hors_bande and not energie.hors_bande


class TestOrdres:
    def _ecart(self, cle, nom, reel, cible, bande, total=100000.0):
        return EcartPoche(
            poche_cle=cle, poche_nom=nom, poids_reel=reel, poids_cible=cible,
            bande=bande, valeur_eur=total * reel,
            valeur_cible_eur=total * cible, actifs=[],
        )

    def test_poche_dans_la_bande_pas_d_ordre(self):
        ecarts = [self._ecart("rv", "Réserve de valeur", 0.20, 0.20, 0.03)]
        ordres, _ = generer_ordres(ecarts, seuil_min_eur=250.0)
        assert ordres == []

    def test_poche_hors_bande_genere_un_ordre(self):
        from core.models import Actif, Classe
        # 11 200 détenus pour une cible de 20 000 -> acheter 8 800.
        e = self._ecart("rv", "Réserve de valeur", 0.112, 0.20, 0.03, total=100_000.0)
        e.actifs = [Actif(ticker="IGLN.L", classe=Classe.OR, devise_cotation="USD",
                          poche="rv", quantite=140, prix=80.0,
                          valeur_eur=11_200.0, dernier_taux=0.92)]
        ordres, _ = generer_ordres([e], seuil_min_eur=250.0)
        assert len(ordres) == 1
        assert ordres[0].sens == "achat"
        assert ordres[0].ticker == "IGLN.L"
        assert ordres[0].montant_eur == pytest.approx(8_800.0)

    def test_sous_le_seuil_de_rentabilite_pas_d_ordre(self):
        """La v1 avait un plancher de 1 000 $ qui dispensait de rééquilibrer."""
        from core.models import Actif, Classe
        # 16 % détenus pour une cible de 20 % : hors bande (écart 4 pts > 3 pts).
        # Mais l'ajustement ne vaut que 200 €, sous le seuil de 250 €.
        e = self._ecart("rv", "Réserve de valeur", 0.16, 0.20, 0.03, total=5_000.0)
        e.actifs = [Actif(ticker="IGLN.L", classe=Classe.OR, devise_cotation="USD",
                          poche="rv", quantite=8, prix=100.0,
                          valeur_eur=800.0, dernier_taux=1.0)]
        assert e.hors_bande
        ordres, a_surveiller = generer_ordres([e], seuil_min_eur=250.0)
        assert ordres == []
        assert len(a_surveiller) == 1

    def test_repartition_au_prorata_dans_la_poche(self):
        e = self._ecart("rv", "Réserve de valeur", 0.10, 0.20, 0.03, total=100000.0)
        from core.models import Actif, Classe
        e.actifs = [
            Actif(ticker="IGLN.L", classe=Classe.OR, devise_cotation="USD",
                  poche="rv", quantite=100, prix=50.0, valeur_eur=5000.0, dernier_taux=1.0),
            Actif(ticker="BTCUSDT", classe=Classe.CRYPTO, devise_cotation="USD",
                  poche="rv", quantite=1, prix=5000.0, valeur_eur=5000.0, dernier_taux=1.0),
        ]
        ordres, _ = generer_ordres([e], seuil_min_eur=250.0)
        assert len(ordres) == 2
        montants = {o.ticker: o.montant_eur for o in ordres}
        assert montants["IGLN.L"] == pytest.approx(montants["BTCUSDT"])


class TestDiagnostic:
    def test_tri_par_urgence(self):
        etats = {
            "rv": EtatPoche(poche=Poche("rv", "RV", 0.20, 0.03, perimetre=Perimetre.INVESTI),
                            poids_reel=0.10, poids_cible=0.20),
            "energie": EtatPoche(poche=Poche("energie", "Énergie", 0.30, 0.05,
                                             perimetre=Perimetre.INVESTI),
                                 poids_reel=0.31, poids_cible=0.30),
            "precaution": EtatPoche(poche=Poche("precaution", "Précaution", 0.0, 0.0,
                                                perimetre=Perimetre.PRECAUTION),
                                    poids_reel=0.5, poids_cible=0.0),
        }
        ecarts = diagnostiquer(etats, total_investi_eur=100000.0)
        # La précaution est exclue du diagnostic.
        assert [e.poche_cle for e in ecarts] == ["rv", "energie"]
        # La plus dérivée en relatif passe en premier.
        assert ecarts[0].poche_cle == "rv"
