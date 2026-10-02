"""Tests des indicateurs de performance.

Ces tests verrouillent les corrections apportées à la v1 : TWR par sous-périodes,
rendement réel de Fisher, rendement en or, IRR.
"""

from __future__ import annotations

import datetime as dt
import math

import pytest

from core import metrics


class TestTWR:
    def test_aucun_flux(self):
        # 100 -> 110 -> 121 : +10 % puis +10 %, TWR = 21 %.
        r = metrics.rendements_periode([100.0, 110.0, 121.0])
        assert metrics.twr(r) == pytest.approx(0.21)

    def test_avec_apport_neutralise(self):
        # 100 -> apport 50 -> 160. Le gain réel est 10 sur 100.
        # r = (160 - 150 - 50) / 100 = 0.60... non :
        # valeurs = [100, 160], flux = [0, 50]
        # r = (160 - 100 - 50) / 100 = 0.10
        r = metrics.rendements_periode([100.0, 160.0], [0.0, 50.0])
        assert r == pytest.approx([0.10])

    def test_retrait_est_un_flux_negatif(self):
        # 100 -> retrait 20 -> 90. Le marché a perdu 10 sur 100 ?
        # r = (90 - 100 - (-20)) / 100 = 0.10
        r = metrics.rendements_periode([100.0, 90.0], [0.0, -20.0])
        assert r == pytest.approx([0.10])

    def test_serie_vide(self):
        assert metrics.twr([]) == 0.0

    def test_chainage_multi_periode(self):
        # 10 %, puis -5 % : TWR = 1,10 * 0,95 - 1 = 4,5 %
        assert metrics.twr([0.10, -0.05]) == pytest.approx(0.045)


class TestRendementReel:
    def test_fisher(self):
        # 5 % nominal, 2 % inflation -> ~2,94 % réel
        assert metrics.rendement_reel(0.05, 0.02) == pytest.approx(1.05 / 1.02 - 1)

    def test_zero_inflation(self):
        assert metrics.rendement_reel(0.05, 0.0) == pytest.approx(0.05)

    def test_deflation(self):
        # Inflation négative : le réel dépasse le nominal.
        assert metrics.rendement_reel(0.05, -0.02) == pytest.approx(1.05 / 0.98 - 1)


class TestRendementEnOr:
    def test_gain_en_onces(self):
        # Capital 100 -> 110, or 2000 -> 2200.
        # onces : 0,05 -> 0,05. Performance nulle.
        assert metrics.rendement_en_or(100, 110, 2000, 2200) == pytest.approx(0.0)

    def test_perte_malgre_gain_en_euros(self):
        # Capital 100 -> 105, or 2000 -> 2500.
        # onces : 0,050 -> 0,042. Perte de 16 %.
        perf = metrics.rendement_en_or(100, 105, 2000, 2500)
        assert perf == pytest.approx(-0.16)

    def test_refuse_un_cour_invalide(self):
        # La v1 remplaçait un cours manquant par 2 000 $ et calculait quand même.
        with pytest.raises(ValueError):
            metrics.rendement_en_or(100, 110, 0, 2000)
        with pytest.raises(ValueError):
            metrics.rendement_en_or(100, 110, 2000, -1)


class TestIRR:
    def test_un_seul_apport_pas_de_solution(self):
        assert metrics.irr([(dt.date(2020, 1, 1), -100)]) is None

    def test_doublement_simple(self):
        # 100 investis, 200 récupérés un an plus tard : IRR ≈ 100 %.
        flux = [(dt.date(2020, 1, 1), -100.0), (dt.date(2021, 1, 1), 200.0)]
        r = metrics.irr(flux)
        assert r is not None
        assert r == pytest.approx(1.0, abs=0.01)

    def test_periode_incomplete(self):
        flux = [(dt.date(2020, 1, 1), -100.0), (dt.date(2020, 7, 1), 110.0)]
        r = metrics.irr(flux)
        assert r is not None
        # 110 contre 100 sur 182 jours (0,498 an) donne 21,08 % annualisé :
        # 1,1^(1/0,498) - 1. Le décompte réel des jours compte.
        assert r == pytest.approx(0.2108, abs=0.001)


class TestAnnualisation:
    def test_douze_mois(self):
        # +21 % sur exactement un an reste +21 % annualisé.
        assert metrics.annualiser(0.21, 365) == pytest.approx(0.21, abs=0.001)

    def test_deux_ans(self):
        # +21 % sur deux ans = ~10 % par an.
        assert metrics.annualiser(0.21, 730) == pytest.approx(0.10, abs=0.002)

    def test_duree_nulle_leve(self):
        with pytest.raises(ValueError):
            metrics.annualiser(0.21, 0)


# ---------------------------------------------------------------------------
# Les flux se rangent par PÉRIODE, pas par date exacte
# ---------------------------------------------------------------------------
class TestFluxParPeriode:
    """Défaut trouvé en préparant l'import de l'historique v1.

    `rendements_periode` calcule `r_i = (V_i - V_{i-1} - F_i) / V_{i-1}` : `F_i`
    appartient à la période qui se TERMINE en i. Écrire `flux_jour.get(dates[i])`
    ne trouve donc que les flux tombés exactement un jour de snapshot.

    Tant que le robot écrivait un snapshot par nuit, les deux coïncidaient — sauf
    la nuit où le robot échouait : l'apport de ce jour-là disparaissait sans un
    mot, et gonflait le TWR de son montant. Depuis que la série de la v1 est
    mensuelle, le décalage devient la règle.
    """

    def test_un_flux_exactement_sur_la_date_reste_trouve(self):
        """Cas quotidien : rien ne change par rapport à l'ancien calcul."""
        dates = [dt.date(2026, 1, 1), dt.date(2026, 1, 2), dt.date(2026, 1, 3)]
        flux = {dt.date(2026, 1, 2): 500.0}
        assert metrics.flux_par_periode(dates, flux) == [0.0, 500.0, 0.0]

    def test_un_flux_entre_deux_snapshots_est_rattache_a_la_periode(self):
        """Le cas qui compte : série mensuelle, versement le 13 du mois."""
        dates = [dt.date(2026, 1, 31), dt.date(2026, 2, 28), dt.date(2026, 3, 31)]
        flux = {dt.date(2026, 2, 13): 900.0}
        assert metrics.flux_par_periode(dates, flux) == [0.0, 900.0, 0.0]

    def test_plusieurs_flux_dans_une_meme_periode_s_additionnent(self):
        dates = [dt.date(2026, 1, 31), dt.date(2026, 2, 28)]
        flux = {dt.date(2026, 2, 5): 200.0, dt.date(2026, 2, 20): 300.0}
        assert metrics.flux_par_periode(dates, flux) == [0.0, 500.0]

    def test_un_retrait_entre_en_negatif(self):
        dates = [dt.date(2026, 1, 31), dt.date(2026, 2, 28)]
        flux = {dt.date(2026, 2, 5): -2000.0}
        assert metrics.flux_par_periode(dates, flux) == [0.0, -2000.0]

    def test_un_flux_du_premier_jour_n_est_pas_compte_dans_une_periode(self):
        """Il est déjà dans la valeur de départ : le compter une seconde fois
        ferait apparaître un faux rendement négatif."""
        dates = [dt.date(2026, 1, 31), dt.date(2026, 2, 28)]
        flux = {dt.date(2026, 1, 31): 5000.0}
        assert metrics.flux_par_periode(dates, flux)[1] == 0.0

    def test_un_flux_anterieur_au_premier_snapshot_est_ignore(self):
        dates = [dt.date(2026, 1, 31), dt.date(2026, 2, 28)]
        flux = {dt.date(2026, 1, 15): 5000.0}
        assert metrics.flux_par_periode(dates, flux) == [0.0, 0.0]

    def test_une_borne_est_exclusive_et_l_autre_inclusive(self):
        """Un flux pile sur la date de début appartient à la période
        PRÉCÉDENTE ; un flux pile sur la date de fin appartient à celle-ci."""
        dates = [dt.date(2026, 1, 31), dt.date(2026, 2, 28), dt.date(2026, 3, 31)]
        flux = {dt.date(2026, 2, 28): 100.0, dt.date(2026, 3, 31): 200.0}
        out = metrics.flux_par_periode(dates, flux)
        assert out == [0.0, 100.0, 200.0]

    def test_twr_sur_une_serie_mensuelle_avec_versements(self):
        """Le cas réel, chiffres ronds : 10 000 € + 1 000 € versés le 13/01,
        portefeuille à 11 550 € fin janvier. La performance est de +5 %,
        pas de +15,5 %."""
        dates = [dt.date(2025, 12, 31), dt.date(2026, 1, 31)]
        valeurs = [10000.0, 11550.0]
        flux_jour = {dt.date(2026, 1, 13): 1000.0}
        flux = metrics.flux_par_periode(dates, flux_jour)
        rendements = metrics.rendements_periode(valeurs, flux)
        assert metrics.twr(rendements) == pytest.approx(0.055, abs=1e-9)

    def test_une_serie_vide(self):
        assert metrics.flux_par_periode([], {dt.date(2026, 1, 1): 100.0}) == []

    def test_aucun_flux(self):
        dates = [dt.date(2026, 1, 31), dt.date(2026, 2, 28)]
        assert metrics.flux_par_periode(dates, {}) == [0.0, 0.0]

    def test_les_flux_nuls_ne_decalent_rien(self):
        dates = [dt.date(2026, 1, 31), dt.date(2026, 2, 28)]
        flux = {dt.date(2026, 2, 10): 0.0, dt.date(2026, 2, 20): 300.0}
        assert metrics.flux_par_periode(dates, flux) == [0.0, 300.0]


# ---------------------------------------------------------------------------
# Le seuil des sauts suit le temps écoulé
# ---------------------------------------------------------------------------
class TestLeSeuilSuitLeTemps:
    """8 % en une séance sur quatre ETF n'existe pas : c'est un défaut de
    données. 8 % en un mois, c'est une bonne année, et ce n'est pas un défaut.

    Une fois l'historique de la v1 importé, la série est mensuelle jusqu'en avril
    2026. Avec un seuil fixe, chaque bon mois aurait été signalé comme une
    anomalie — et une alerte qui crie au loup ne vaut pas mieux que pas d'alerte.
    """

    def test_un_jour_garde_le_seuil_de_8_pour_cent(self):
        dates = [dt.date(2026, 1, 31), dt.date(2026, 2, 1)]
        sauts = metrics.sauts_non_expliques(dates, [100000.0, 108500.0], [0.0, 0.0])
        assert len(sauts) == 1
        assert sauts[0]["seuil"] == pytest.approx(0.08)
        assert sauts[0]["jours"] == 1

    def test_le_meme_saut_sur_un_mois_ne_declenche_pas(self):
        """+8,5 % en un mois : c'est un mois ordinaire, pas une anomalie."""
        dates = [dt.date(2026, 1, 31), dt.date(2026, 3, 2)]   # 30 jours
        sauts = metrics.sauts_non_expliques(dates, [100000.0, 108500.0], [0.0, 0.0])
        assert sauts == []

    def test_le_prix_de_la_granularite_mensuelle(self):
        """+25 % en un mois sans flux n'est PAS signalé. C'est assumé.

        Sur une série mensuelle, le détecteur ne voit plus que les défauts
        énormes (+44 % ou plus). C'est le prix de la granularité — impossible de
        distinguer un mauvais mois d'un versement oublié quand on n'a qu'un point
        par mois. Le robot quotidien, lui, continue d'écrire un point chaque
        nuit dès aujourd'hui : là où il reprend la main, le seuil redevient 8 %,
        et c'est là que le défaut importe.

        Ce test existe pour que ce compromis soit écrit quelque part plutôt que
        découvert par surprise.
        """
        dates = [dt.date(2026, 1, 31), dt.date(2026, 3, 2)]
        sauts = metrics.sauts_non_expliques(dates, [100000.0, 125000.0], [0.0, 0.0])
        assert sauts == [], "25 % < 8 % x racine(30), donc non signalé"

    def test_un_mois_avec_un_trou_énorme_declenche(self):
        """+50 % en un mois sans flux : aucun portefeuille diversifié ne fait ça,
        même en un an."""
        dates = [dt.date(2026, 1, 31), dt.date(2026, 3, 2)]
        sauts = metrics.sauts_non_expliques(dates, [100000.0, 150000.0], [0.0, 0.0])
        assert len(sauts) == 1
        assert sauts[0]["jours"] == 30

    def test_le_seuil_croit_comme_la_racine_du_temps(self):
        """L'échelle du mouvement brownien : √30 ≈ 5,48, donc un mois tolère
        environ 43,8 %, pas 240 %."""
        dates = [dt.date(2026, 1, 31), dt.date(2026, 3, 2)]
        sauts = metrics.sauts_non_expliques(dates, [100000.0, 160000.0], [0.0, 0.0])
        assert len(sauts) == 1, "+60 % en un mois reste une anomalie"
        attendu = 0.08 * math.sqrt(30)
        assert sauts[0]["seuil"] == pytest.approx(attendu, rel=1e-6)

    def test_le_saut_du_02_02_2026_reste_detecte(self):
        """La mise à l'échelle ne doit pas perdre le défaut qu'on a mis trois
        rounds à trouver : 16,12 % en une séance."""
        dates = [dt.date(2026, 2, 1), dt.date(2026, 2, 2)]
        sauts = metrics.sauts_non_expliques(
            dates, [55639.93, 64808.82], [0.0, 200.0]
        )
        assert len(sauts) == 1
        assert sauts[0]["residuel"] == pytest.approx(8968.89, abs=0.01)

    def test_la_baisse_de_10_pour_cent_sur_un_mois_ne_declenche_pas(self):
        dates = [dt.date(2026, 1, 31), dt.date(2026, 3, 2)]
        sauts = metrics.sauts_non_expliques(dates, [100000.0, 90000.0], [0.0, 0.0])
        assert sauts == []

    def test_des_dates_non_triees_ne_font_pas_planter(self):
        """Un écart négatif donnerait une racine carrée de nombre négatif."""
        dates = [dt.date(2026, 2, 1), dt.date(2026, 1, 31)]
        sauts = metrics.sauts_non_expliques(dates, [100000.0, 160000.0], [0.0, 0.0])
        assert isinstance(sauts, list)


class TestFluxSansEffet:
    """Le miroir de `sauts_non_expliques`.

        sauts_non_expliques : la valeur monte sans flux  -> versement manquant
        fluxs_sans_effet    : le flux est là, la valeur
                              ne suit pas                 -> versement fantôme

    Le cas réel, trouvé en préparant l'import de l'historique v1 : le
    29/04/2024, `Historique` enregistre un apport de 10 800 €, et la valeur
    investie passe de 31 988 à 31 779 USD entre le 30/03 et le 30/04 — soit
    +23 €. Elle n'a pas bougé de 11 027 €. Le même apport, de 10 800 €, daté du
    23/07/2024, fait bien monter la valeur de 10 658 €.
    """

    # Les trois cas réels, en DOLLARS — l'unité de la v1. Comparer des valeurs
    # en dollars à des apports en euros fausserait le test d'un facteur 1,08.
    def test_le_29_avril_2024_est_detecte(self):
        """Apports du mois : 268,24 $ (11/04) + 11 579,85 $ (29/04)."""
        dates = [dt.date(2024, 3, 30), dt.date(2024, 4, 30)]
        trouves = metrics.fluxs_sans_effet(dates, [31988.0, 31779.0], [0.0, 11848.09])
        assert len(trouves) == 1
        assert trouves[0]["date"] == dt.date(2024, 4, 30)
        # La valeur recule de 209 $ alors que 11 848 $ viennent d'entrer.
        assert trouves[0]["residuel"] == pytest.approx(-12057.09)
        assert abs(trouves[0]["residuel_pct"]) > 0.35

    def test_le_23_juillet_2024_ne_declenche_pas(self):
        """Le même apport de 10 800 €, un autre mois, où la valeur le suit."""
        dates = [dt.date(2024, 6, 30), dt.date(2024, 7, 30)]
        trouves = metrics.fluxs_sans_effet(dates, [41975.0, 53941.0], [0.0, 11554.33])
        assert trouves == []

    def test_le_krach_d_avril_2025_ne_declenche_pas(self):
        """LA RAISON DES DEUX CONDITIONS.

        2 974 $ d'apports, une valeur qui recule de 1 129 $ : le trou de
        4 103 $ est le krach tarifaire. Une condition unique sur « la valeur n'a
        pas suivi le flux » aurait crié au loup ici — et une alerte qu'on
        n'écoute plus vaut moins que pas d'alerte.
        """
        dates = [dt.date(2025, 3, 30), dt.date(2025, 4, 30)]
        trouves = metrics.fluxs_sans_effet(dates, [64273.0, 63144.0], [0.0, 2973.92])
        assert trouves == [], "6,4 % d'écart, c'est un marché ; 38 %, non"

    def test_un_petit_apport_ignore_n_est_pas_examine(self):
        """Sous le seuil de montant, une absence d'effet n'est pas concluante :
        200 € peuvent disparaître dans un mouvement de marché ordinaire."""
        dates = [dt.date(2026, 1, 31), dt.date(2026, 2, 28)]
        trouves = metrics.fluxs_sans_effet(dates, [100000.0, 100000.0], [0.0, 200.0])
        assert trouves == []

    def test_un_retrait_fantome_aussi(self):
        """Le contrôle ne présume pas du sens : un retrait enregistré qui ne
        fait pas baisser la valeur est le même défaut."""
        dates = [dt.date(2025, 1, 31), dt.date(2025, 2, 28)]
        trouves = metrics.fluxs_sans_effet(dates, [50000.0, 50020.0], [0.0, -12000.0])
        assert len(trouves) == 1
        assert trouves[0]["flux"] == -12000.0

    def test_un_apport_suivi_par_un_mois_fort_ne_declenche_pas(self):
        """Un apport de 5 000 € dans un mois à +6 % : la valeur a bougé bien
        plus que le flux, aucun soupçon."""
        dates = [dt.date(2025, 7, 30), dt.date(2025, 8, 31)]
        trouves = metrics.fluxs_sans_effet(dates, [50000.0, 58200.0], [0.0, 5000.0])
        assert trouves == []

    def test_une_serie_vide(self):
        assert metrics.fluxs_sans_effet([], [], []) == []

    def test_series_de_longueurs_differentes(self):
        dates = [dt.date(2026, 1, 1), dt.date(2026, 2, 1)]
        assert metrics.fluxs_sans_effet(dates, [100.0], [0.0, 0.0]) == []

    def test_le_message_nomme_le_montant_et_les_deux_bornes(self):
        """Les DEUX bornes de la période, et non la seule date du snapshot.

        Vérifié sur le cas réel : le versement litigieux est daté du 29/04/2024
        dans le journal, alors que le snapshot qui le révèle porte le 30/04/2024.
        Un message qui n'aurait dit que « 2024-04-30 » envoyait chercher une
        ligne qui n'existe pas.
        """
        dates = [dt.date(2024, 3, 30), dt.date(2024, 4, 30)]
        trouve = metrics.fluxs_sans_effet(dates, [31988.0, 31779.0], [0.0, 11050.0])[0]
        message = metrics.anomalie_flux_sans_effet(trouve)
        assert "30/03/2024" in message
        assert "30/04/2024" in message
        assert "11 050" in message
        assert "apport" in message

    def test_les_dates_sont_en_format_francais(self):
        """Jamais d'ISO dans un message : une date ISO se survole, une date
        jj/mm/aaaa se lit."""
        dates = [dt.date(2024, 3, 30), dt.date(2024, 4, 30)]
        trouve = metrics.fluxs_sans_effet(dates, [31988.0, 31779.0], [0.0, 11050.0])[0]
        message = metrics.anomalie_flux_sans_effet(trouve)
        assert "2024-04-30" not in message
        assert "2024-03-30" not in message

    def test_la_ponctuation_du_message_survit_au_formatage(self):
        """REGRESSION. Le message finissait par `.replace(",", " ")`, appliqué à
        la PHRASE ENTIÈRE : toutes les virgules de ponctuation disparaissaient.
        Le défaut était visible à l'écran (« L'écart est de 11 027 € soit
        37.2 % ») et il rendait l'alerte pénible à lire — or une alerte qu'on ne
        lit pas ne sert à rien.

        On formate les NOMBRES, jamais la phrase.
        """
        dates = [dt.date(2024, 3, 30), dt.date(2024, 4, 30)]
        trouve = metrics.fluxs_sans_effet(dates, [31988.0, 31779.0], [0.0, 11050.0])[0]
        message = metrics.anomalie_flux_sans_effet(trouve)
        assert "11 259 €, soit" in message, "la virgule après le montant"
        assert "deux fois, porte" in message, "la virgule de l'énumération"
        assert "une mauvaise date, ou" in message

    def test_le_message_jumeau_formate_aussi_ses_nombres(self):
        """`anomalie_saut` avait exactement le même défaut."""
        saut = {
            "date": dt.date(2026, 2, 2), "avant": 55639.93, "apres": 64808.82,
            "flux": 200.0, "residuel": 8968.89, "residuel_pct": 0.1612,
        }
        message = metrics.anomalie_saut(saut)
        assert "02/02/2026" in message
        assert "2026-02-02" not in message
        assert "8 969" in message, "le millier est une espace"
        assert "55 640" in message

    def test_le_message_distingue_un_retrait(self):
        dates = [dt.date(2025, 1, 31), dt.date(2025, 2, 28)]
        trouve = metrics.fluxs_sans_effet(dates, [50000.0, 50020.0], [0.0, -12000.0])[0]
        assert "retrait" in metrics.anomalie_flux_sans_effet(trouve)


class TestLeFormatageDesNombresDansLesMessages:
    """Le millier est une espace, la décimale une virgule — comme `ui.eur`."""

    def test_millier_avec_espace(self):
        dates = [dt.date(2024, 3, 30), dt.date(2024, 4, 30)]
        trouve = metrics.fluxs_sans_effet(
            dates, [3198800.0, 3177900.0], [0.0, 1105000.0])[0]
        message = metrics.anomalie_flux_sans_effet(trouve)
        assert "1 105 000" in message
        assert "1,105000".replace(",", "") not in message

    def test_le_signe_du_mouvement_est_conserve(self):
        """La valeur recule alors qu'un apport entre : le message doit dire
        « -209 € », avec le signe. Un montant absolu laisserait croire que la
        valeur a monté."""
        dates = [dt.date(2024, 3, 30), dt.date(2024, 4, 30)]
        trouve = metrics.fluxs_sans_effet(dates, [31988.0, 31779.0], [0.0, 11050.0])[0]
        message = metrics.anomalie_flux_sans_effet(trouve)
        assert "-209" in message
