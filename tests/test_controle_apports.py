"""Quand l'application doit refuser de faire confiance à son propre chiffre.

Un TWR neutralise les versements — à condition de les connaître. Quand aucun
apport n'est enregistré, `twr()` se réduit à `fin / début - 1`, et l'épargne
apparaît comme du rendement.

C'est le symptôme rapporté : **+26,2 % affichés pour 2026** là où Swissquote
donne **+4,10 %**. Un écart de 22 points ne vient pas d'un arrondi.

L'application ne peut pas savoir qu'un versement a été oublié. Mais elle peut
reconnaître la situation où c'est le plus probable, et le dire — au lieu
d'afficher un chiffre confiant qui a l'air d'avoir été vérifié.
"""

from __future__ import annotations

import pytest

from core import metrics


class TestControleApports:
    def test_detecte_le_cas_du_symptome(self):
        """61 000 € qui deviennent 77 000 € en neuf mois, sans un seul apport.

        Une hausse de 26 % en neuf mois sans versement est possible, mais elle
        est exactement ce que produit une épargne non enregistrée. On prévient.
        """
        valeurs = [61_000.0, 65_000.0, 71_000.0, 77_000.0]
        flux = [0.0, 0.0, 0.0, 0.0]
        alertes = metrics.controle_apports(valeurs, flux, jours=273)
        assert len(alertes) == 1
        assert "26" in alertes[0] or "+26" in alertes[0]
        assert "rendement" in alertes[0]

    def test_silencieux_si_les_apports_sont_enregistres(self):
        """Le même parcours AVEC les apports : plus rien à signaler.

        C'est le contrôle qui compte : l'alerte ne doit pas se déclencher quand
        tout est en règle, sinon on apprend à l'ignorer.
        """
        valeurs = [61_000.0, 65_000.0, 71_000.0, 77_000.0]
        flux = [0.0, 2_400.0, 4_800.0, 7_200.0]
        assert metrics.controle_apports(valeurs, flux, jours=273) == []

    def test_silencieux_sur_une_hausse_moderee(self):
        """+3 % sans apport sur trois mois : c'est la Bourse, pas un oubli."""
        valeurs = [61_000.0, 62_000.0, 62_830.0]
        flux = [0.0, 0.0, 0.0]
        assert metrics.controle_apports(valeurs, flux, jours=273) == []

    def test_silencieux_sur_une_periode_courte(self):
        """Deux semaines de hausse ne prouvent rien — même à +20 %."""
        valeurs = [10_000.0, 12_000.0]
        flux = [0.0, 0.0]
        assert metrics.controle_apports(valeurs, flux, jours=14) == []

    def test_silencieux_si_un_retrait_est_enregistre(self):
        """Un flux non nul suffit : la correction est active, le TWR vaut."""
        valeurs = [61_000.0, 55_000.0, 77_000.0]
        flux = [0.0, -8_000.0, 0.0]
        assert metrics.controle_apports(valeurs, flux, jours=273) == []

    def test_ne_plante_pas_sur_des_entrees_vides(self):
        assert metrics.controle_apports([], [], 0) == []
        assert metrics.controle_apports([100.0], [0.0], 300) == []

    def test_ne_plante_pas_si_les_tailles_different(self):
        """Le nombre de flux doit égaler le nombre de valeurs. Sinon on se
        taît plutôt que de comparer deux listes qui ne vont pas ensemble."""
        assert metrics.controle_apports([1.0, 2.0, 3.0], [0.0], 200) == []

    def test_ne_plante_pas_si_la_valeur_de_depart_est_nulle(self):
        assert metrics.controle_apports([0.0, 100.0], [0.0, 0.0], 200) == []


class TestLeTWRResteJusteAvecLesFlux:
    """Le contrôle ne remplace pas le calcul : il le surveille."""

    def test_avec_les_apports_le_twr_retrouve_la_performance_reelle(self):
        """61 000 € qui finissent à 70 761 € après 9 x 800 € de versements.

        La performance de la stratégie est de +4,10 % — comme Swissquote.
        Sans les flux, le même parcours afficherait +16,0 %.
        """
        depart = 61_000.0
        versement = 800.0
        performance = 0.041 / 9          # +4,10 % annualisé, réparti sur 9 mois

        valeurs = [depart]
        flux = [0.0]
        for _ in range(9):
            nouvelles = valeurs[-1] * (1 + performance) + versement
            valeurs.append(nouvelles)
            flux.append(versement)

        rendements = metrics.rendements_periode(valeurs, flux)
        twr = metrics.twr(rendements)
        assert twr == pytest.approx(0.041, abs=0.002), (
            f"TWR = {twr:.4f} — les versements ont fui dans le rendement"
        )

        # Et le calcul naïf, celui que produit une table d'apports vide :
        naif = valeurs[-1] / valeurs[0] - 1
        assert naif > 0.15, "le calcul naïf devrait être nettement trop flatteur"
        assert metrics.controle_apports(valeurs, [0.0] * len(valeurs), 273)
