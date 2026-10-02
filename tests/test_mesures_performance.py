"""Deux mesures qui vivaient dans la page et n'étaient testables nulle part.

Ces deux défauts ont vécu des mois dans `pages/5_Performance.py` sans qu'aucun
test ne les voie, parce qu'ils étaient enfouis dans du code Streamlit. Ils ont
été remontés dans `core/metrics.py` pour pouvoir être mesurés.

Le premier est le plus grave : il affichait **57 points de rendement imaginaire**
la première année. Le second rendait la performance réelle fausse de 60 points
dès que le robot quotidien tournait.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core import metrics


# ---------------------------------------------------------------------------
# DÉFAUT A — la performance annuelle ignorait les apports
# ---------------------------------------------------------------------------
class TestTwrParAnnee:
    def test_les_apports_ne_sont_pas_du_rendement(self):
        """Un portefeuille à 9 %/an ne doit pas s'afficher à +66 %.

        Le défaut : `dernière_valeur / première_valeur - 1`. Avec 500 € versés
        chaque mois, la valeur finale gonfle des versements et le rapport
        explose. Le rendement de l'année doit être le chaînage des
        sous-périodes, qui neutralise les flux.
        """
        # Deux annees COMPLETES : 12 mois chacune, dates au premier du mois.
        dates, valeurs = [], []
        capital = 10_000.0
        for mois in range(24):
            dates.append(dt.date(2023 + mois // 12, mois % 12 + 1, 1))
            valeurs.append(capital)
            capital = capital * 1.1 ** (1 / 12) + 500.0
        flux = [0.0] + [500.0] * 23

        rendements = [0.0] + metrics.rendements_periode(valeurs, flux)
        par_annee = metrics.twr_par_annee(dates, rendements)

        assert set(par_annee) == {2023, 2024}

        # 2024 est complete : douze mois a 10 %/an.
        assert par_annee[2024] == pytest.approx(0.10, abs=0.002), (
            f"2024 affiche {par_annee[2024]:.1%} au lieu de 10,0 % : "
            "les apports sont comptés comme du rendement"
        )

        # 2023 part du premier snapshot : on ne connait pas le rendement
        # d'avant, elle compte donc onze sous-periodes et non douze. C'est
        # honnete — et c'est exactement ce que fait `twr_par_annee`.
        assert par_annee[2023] == pytest.approx(1.1 ** (11 / 12) - 1, abs=0.002)

        # Ce que l'ANCIEN calcul affichait, pour que l'ecart soit mesure :
        ancien = {}
        for annee in (2023, 2024):
            vs = [valeurs[i] for i, d in enumerate(dates) if d.year == annee]
            ancien[annee] = vs[-1] / vs[0] - 1
        assert ancien[2023] > 0.60, (
            f"l'ancien calcul donnait {ancien[2023]:.1%} : il fallait le corriger"
        )
        assert par_annee[2023] < ancien[2023] - 0.4

    def test_une_annee_incomplete_ne_compte_que_ses_propres_mois(self):
        """Onze mois à 10 %/an font 9,1 %, pas 10 % — et pas la valeur totale.

        Le défaut `fin / debut - 1` mélangeait les années : le rendement de 2025
        incluait la fin de 2024.
        """
        dates = [dt.date(2025, m, 1) for m in range(2, 13)]
        valeurs = [100.0 * 1.1 ** (i / 12) for i in range(11)]
        rendements = [0.0] + metrics.rendements_periode(valeurs)
        assert metrics.twr_par_annee(dates, rendements)[2025] == pytest.approx(
            1.1 ** (10 / 12) - 1
        )

    def test_sans_aucun_apport_le_resultat_est_le_meme(self):
        """Sans flux, les deux méthodes doivent coïncider — c'est le contrôle."""
        valeurs = [100.0, 110.0, 121.0]
        dates = [dt.date(2024, 1, 1), dt.date(2024, 6, 1), dt.date(2024, 12, 31)]
        rendements = [0.0] + metrics.rendements_periode(valeurs)
        assert metrics.twr_par_annee(dates, rendements)[2024] == pytest.approx(0.21)

    def test_une_annee_est_partielle_si_la_serie_commence_en_cours(self):
        """Le rendement d'une année ne compte que ses propres sous-périodes."""
        dates = [dt.date(2024, 11, 1), dt.date(2024, 12, 1), dt.date(2025, 1, 1)]
        valeurs = [100.0, 110.0, 121.0]
        rendements = [0.0] + metrics.rendements_periode(valeurs)
        par_annee = metrics.twr_par_annee(dates, rendements)
        assert par_annee[2024] == pytest.approx(0.10)
        assert par_annee[2025] == pytest.approx(0.10)

    def test_longueurs_differentes_levees(self):
        with pytest.raises(ValueError):
            metrics.twr_par_annee([dt.date(2024, 1, 1)], [0.0, 0.1])

    def test_une_seule_annee(self):
        dates = [dt.date(2024, 1, 1), dt.date(2024, 12, 31)]
        rendements = [0.0, 0.25]
        assert metrics.twr_par_annee(dates, rendements) == {2024: pytest.approx(0.25)}


# ---------------------------------------------------------------------------
# DÉFAUT B — l'inflation était élevée au nombre de lignes
# ---------------------------------------------------------------------------
class TestInflationCumulee:
    INFLATION = {2023: 0.049, 2024: 0.020, 2025: 0.009}

    def test_pondere_par_le_temps_pas_par_le_nombre_de_lignes(self):
        """Le facteur ne doit PAS dépendre de la fréquence des snapshots.

        Le défaut : `(1 + infl) ** (nb_snapshots_dans_l_annee / 12)`. Avec des
        snapshots mensuels ça tombait juste ; avec le robot quotidien, une
        année de 250 lignes donnait une inflation à la puissance 20. Le
        facteur doit être identique sur la même période calendaire.
        """
        attendu = 1.049 * 1.020 * 1.009
        assert metrics.inflation_cumulee(
            self.INFLATION, dt.date(2023, 1, 1), dt.date(2026, 1, 1)
        ) == pytest.approx(attendu, rel=1e-4)

    def test_periode_partielle_au_prorata_des_jours(self):
        """Six mois à 2 % ne font pas 2 % d'inflation, mais ~1 %."""
        facteur = metrics.inflation_cumulee(
            {2024: 0.02}, dt.date(2024, 7, 1), dt.date(2025, 1, 1)
        )
        assert facteur == pytest.approx(1.02 ** (184 / 365.25))

    def test_annee_sans_donnee_est_sautee_pas_zero(self):
        """Une année inconnue ne doit PAS valoir 0 % — c'était le défaut de la v1."""
        facteur = metrics.inflation_cumulee(
            {2024: 0.02}, dt.date(2024, 1, 1), dt.date(2026, 1, 1)
        )
        assert facteur == pytest.approx(1.02 ** (366 / 365.25)), (
            "2025 est inconnue : il est ignore, pas remplace par 0 %"
        )

    def test_periode_vide(self):
        assert metrics.inflation_cumulee(self.INFLATION, dt.date(2024, 1, 1),
                                         dt.date(2024, 1, 1)) == 1.0

    def test_dates_inversees(self):
        assert metrics.inflation_cumulee(self.INFLATION, dt.date(2026, 1, 1),
                                         dt.date(2023, 1, 1)) == 1.0
