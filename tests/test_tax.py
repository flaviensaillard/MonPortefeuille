"""Tests du moteur fiscal.

Ces tests verrouillent les corrections apportées à la v1 : barèmes indexés par
année, abattement crypto, distinction or ETC / or physique, CSG déductible.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core import fiscal_bars as fb
from core import tax


class TestImpotsRevenus:
    def test_bareme_par_annee(self):
        """Le barème retenu est celui de l'année d'imposition, pas d'aujourd'hui."""
        r2023 = tax.impot_revenu(50_000, 2.0, 2023, "Marié(e) / Pacsé(e)")
        r2025 = tax.impot_revenu(50_000, 2.0, 2025, "Marié(e) / Pacsé(e)")
        # Les seuils sont réévalués : l'impôt baisse légèrement.
        assert r2025.impot_net < r2023.impot_net
        assert r2023.bareme_annee == 2023
        assert r2025.bareme_annee == 2025

    def test_annee_inconnue_leve(self):
        """La v1 appliquait le barème du jour à toutes les années."""
        with pytest.raises(ValueError):
            tax.impot_revenu(50_000, 2.0, 2031, "Célibataire")

    def test_tranches_progressives(self):
        # Célibataire, 1 part, revenu par part de 15 000.
        # 11 % sur (15 000 - 11 600) = 374 €
        r = tax.impot_revenu(15_000, 1.0, 2025, "Célibataire", avec_decote=False)
        assert r.impot_brut == pytest.approx(374.0)

    def test_decote_reduit_l_impot_faible(self):
        sans = tax.impot_revenu(14_000, 1.0, 2025, "Célibataire", avec_decote=False)
        avec = tax.impot_revenu(14_000, 1.0, 2025, "Célibataire", avec_decote=True)
        assert avec.decote > 0
        assert avec.impot_net < sans.impot_net

    def test_seuil_de_non_recouvrement(self):
        # Un impôt dérisoire est ramené à zéro (seuil de 61 €).
        r = tax.impot_revenu(11_700, 1.0, 2025, "Célibataire")
        assert r.impot_net == 0.0

    def test_tmi(self):
        r = tax.impot_revenu(60_000, 1.0, 2025, "Célibataire")
        assert r.tmi == pytest.approx(0.30)

    def test_parts_plafonnees(self):
        """Sans plafonnement du quotient, un foyer à 3 parts est sur-avantagé."""
        # 3 parts : couple + 2 enfants.
        r = tax.impot_revenu(200_000, 3.0, 2025, "Marié(e) / Pacsé(e)", avec_decote=False)
        # Le plafond garantit que l'impôt reste strictement décroissant mais bridé.
        assert r.impot_brut > 0


class TestPlusValuesTitres:
    def test_regime_150_0_a(self):
        lignes = [{
            "actif": "XDW0.L", "date": dt.date(2025, 6, 1), "quantite": 10,
            "pru_eur": 50.0, "prix_cession_eur": 700.0,
        }]
        r = tax.pv_titres(lignes, 2025)
        assert r.regime == "150-0 A"
        assert r.plus_value_brute == pytest.approx(200.0)
        # Cession 2025 : 12,8 % d'IR + 17,2 % de PS = 30 % de 200 = 60,00.
        #
        # Ce test attendait 61,60 (30,8 %). L'ancien taux venait d'une
        # soustraction : `PFU_TAUX - PRELEVEMENTS_SOCIAUX` = 0,308 - 0,172 =
        # 0,136 d'IR, au lieu des 12,8 % de l'article 200 A du CGI.
        assert r.impot_ir == pytest.approx(25.60)
        assert r.prelevements_sociaux == pytest.approx(34.40)
        assert r.total_du == pytest.approx(60.00)

    def test_seule_l_annee_demandee(self):
        lignes = [
            {"actif": "A", "date": dt.date(2024, 6, 1), "quantite": 1,
             "pru_eur": 10.0, "prix_cession_eur": 20.0},
            {"actif": "B", "date": dt.date(2025, 6, 1), "quantite": 1,
             "pru_eur": 10.0, "prix_cession_eur": 30.0},
        ]
        r = tax.pv_titres(lignes, 2025)
        assert len(r.detail) == 1
        assert r.plus_value_brute == pytest.approx(20.0)

    def test_moins_value_compensee(self):
        lignes = [
            {"actif": "A", "date": dt.date(2025, 1, 1), "quantite": 1,
             "pru_eur": 100.0, "prix_cession_eur": 50.0},
            {"actif": "B", "date": dt.date(2025, 6, 1), "quantite": 1,
             "pru_eur": 10.0, "prix_cession_eur": 40.0},
        ]
        r = tax.pv_titres(lignes, 2025)
        assert r.plus_value_brute == pytest.approx(-20.0)
        assert r.total_du == 0.0


class TestCrypto:
    def test_cessions_au_dessus_du_seuil_tout_est_imposable(self):
        """Le seuil de 305 EUR porte sur les PRIX DE CESSION, pas sur le gain.

        DÉFAUT CORRIGÉ. Ce test s'appelait `test_abattement_305` et attendait
        305 EUR retirés du gain. L'article 150 VH bis du CGI ne prévoit aucun
        abattement : il prévoit une FRANCHISE, assise sur le total des prix de
        cession de l'année. Au-delà de 305 EUR de cessions, la plus-value est
        imposable dès le premier euro.
        """
        lignes = [{
            "actif": "BTCUSDT", "date": dt.date(2025, 3, 1), "quantite": 1,
            "prix_cession_eur": 1_000.0,
            "valeur_globale_eur": 1_000.0,
            "cout_total_acquisition_eur": 500.0,
            "fractions_deja_prises": 0.0,
            "sens": "vente",
        }]
        r = tax.pv_crypto(lignes, 2025)
        assert r.plus_value_brute == pytest.approx(500.0)
        assert r.total_cessions == pytest.approx(1_000.0)
        assert r.exonere_par_franchise is False
        # Aucun abattement : la totalite du gain est imposable.
        assert r.abattement == pytest.approx(0.0)
        assert r.plus_value_imposable == pytest.approx(500.0)
        assert r.total_du == pytest.approx(150.0)      # 500 x 30 % en 2025

    def test_sous_le_seuil_de_305_tout_est_exonere(self):
        """200 EUR de cessions : sous le seuil, la plus-value est exonérée.

        Le gain est entier — 200 EUR, soit 100 % de la cession — mais le total
        cédé ne franchit pas 305 EUR. Rien à payer. La 2086 reste due : c'est
        elle qui prouve qu'on est sous le seuil.
        """
        lignes = [{
            "actif": "BTCUSDT", "date": dt.date(2025, 3, 1), "quantite": 1,
            "prix_cession_eur": 200.0, "valeur_globale_eur": 200.0,
            "cout_total_acquisition_eur": 0.0, "fractions_deja_prises": 0.0,
            "sens": "vente",
        }]
        r = tax.pv_crypto(lignes, 2025)
        assert r.total_cessions == pytest.approx(200.0)
        assert r.exonere_par_franchise is True
        assert r.plus_value_imposable == pytest.approx(0.0)
        assert r.total_du == pytest.approx(0.0)

    def test_le_seuil_se_franchit_au_centime_pres(self):
        """306 EUR de cessions : le gain devient imposable dès le premier euro.

        C'est le point que l'ancien code ratait. 20 EUR de gain sur 400 EUR de
        cessions : l'ancien `abattement = min(305, 20)` ramenait l'impôt à zéro.
        Le seuil est pourtant franchi.
        """
        lignes = [{
            "actif": "BTCUSDT", "date": dt.date(2025, 3, 1), "quantite": 1,
            "prix_cession_eur": 400.0, "valeur_globale_eur": 400.0,
            "cout_total_acquisition_eur": 380.0, "fractions_deja_prises": 0.0,
            "sens": "vente",
        }]
        r = tax.pv_crypto(lignes, 2025)
        assert r.plus_value_brute == pytest.approx(20.0)
        assert r.exonere_par_franchise is False
        assert r.plus_value_imposable == pytest.approx(20.0)
        assert r.total_du == pytest.approx(6.0)        # 20 x 30 %

    def test_le_seuil_porte_sur_les_cessions_pas_sur_le_gain(self):
        """Deux cessions de 200 EUR : 400 EUR cédés, donc imposable.

        Prises séparément, chaque cession est sous 305 EUR. C'est le TOTAL de
        l'année qui compte — et il se calcule au niveau du foyer fiscal.
        """
        base = {
            "actif": "BTCUSDT", "quantite": 1, "valeur_globale_eur": 400.0,
            "cout_total_acquisition_eur": 380.0, "fractions_deja_prises": 0.0,
            "sens": "vente",
        }
        lignes = [
            {**base, "date": dt.date(2025, 3, 1), "prix_cession_eur": 200.0},
            {**base, "date": dt.date(2025, 9, 1), "prix_cession_eur": 200.0},
        ]
        r = tax.pv_crypto(lignes, 2025)
        assert r.total_cessions == pytest.approx(400.0)
        assert r.exonere_par_franchise is False

    def test_valeur_globale_manquante_leve(self):
        """La v1 forçait valeur_globale = prix_cession, masquant le problème."""
        lignes = [{
            "actif": "BTCUSDT", "date": dt.date(2025, 3, 1), "quantite": 1,
            "prix_cession_eur": 1_000.0, "valeur_globale_eur": 0.0,
            "cout_total_acquisition_eur": 500.0, "fractions_deja_prises": 0.0,
            "sens": "vente",
        }]
        with pytest.raises(ValueError):
            tax.pv_crypto(lignes, 2025)

    def test_achats_ignores(self):
        lignes = [{
            "actif": "BTCUSDT", "date": dt.date(2025, 3, 1), "quantite": 1,
            "prix_cession_eur": 1_000.0, "valeur_globale_eur": 1_000.0,
            "cout_total_acquisition_eur": 1_000.0, "sens": "achat",
        }]
        r = tax.pv_crypto(lignes, 2025)
        assert r.plus_value_brute == 0.0


class TestOrPhysique:
    def test_abattement_duree(self):
        assert tax.abattement_or_physique(1) == 0.0
        assert tax.abattement_or_physique(3) == pytest.approx(0.05)
        assert tax.abattement_or_physique(22) == pytest.approx(1.0)
        assert tax.abattement_or_physique(30) == pytest.approx(1.0)

    def test_deux_regimes_et_le_plus_favorable(self):
        """La v1 ne connaissait que le régime des valeurs mobilières (47,2 %)."""
        lignes = [{
            "actif": "OR_PHYS", "date": dt.date(2025, 6, 1), "quantite": 1,
            "prix_cession_eur": 20_000.0, "cout_acquisition_eur": 10_000.0,
            "date_acquisition": dt.date(2015, 1, 1), "sens": "vente",
        }]
        r = tax.pv_or_physique(lignes, 2025)
        assert r["regime"] == "150 VI"
        # TFMP = 11,5 % de 20 000 = 2 300
        assert r["tfmp"] == pytest.approx(2_300.0)
        # 10 ans de détention -> abattement de 40 %.
        # Régime PV : 36,2 % de (10 000 × 0,60) = 2 172
        assert r["abattement_duree"] == pytest.approx(0.40)
        assert r["regime_plus_value"] == pytest.approx(2_172.0)
        # Le régime PV est retenu : 2 172 < 2 300.
        assert r["regime_retenu"] == "Plus-value dégressive"
        assert r["total_du"] == pytest.approx(2_172.0)

    def test_exoneration_a_22_ans(self):
        lignes = [{
            "actif": "OR_PHYS", "date": dt.date(2025, 6, 1), "quantite": 1,
            "prix_cession_eur": 20_000.0, "cout_acquisition_eur": 10_000.0,
            "date_acquisition": dt.date(2000, 1, 1), "sens": "vente",
        }]
        r = tax.pv_or_physique(lignes, 2025)
        assert r["total_du"] == pytest.approx(0.0)

    def test_or_etc_reste_au_regime_titres(self):
        """Un ETC n'est PAS de l'or physique : il reste en 150-0 A."""
        lignes = [{
            "actif": "IGLN.L", "date": dt.date(2025, 6, 1), "quantite": 10,
            "pru_eur": 50.0, "prix_cession_eur": 700.0,
        }]
        r = tax.pv_titres(lignes, 2025)
        assert r.regime == "150-0 A"
        # 30 % pour une cession 2025, pas 30,8 %.
        assert r.total_du == pytest.approx(60.00)


class TestPfuOuBareme:
    def test_bareme_moins_cher_a_tmi_faible(self):
        """À TMI faible, le barème progressif bat le PFU."""
        comp = tax.comparer_pfu_bareme(5_000, 20_000, 2.0, 2025, "Marié(e) / Pacsé(e)")
        assert comp["choix"] == "Barème progressif"

    def test_csg_deductible_prise_en_compte(self):
        """La v1 oubliait la CSG déductible de 6,8 %."""
        comp = tax.comparer_pfu_bareme(5_000, 20_000, 2.0, 2025, "Marié(e) / Pacsé(e)")
        assert comp["bareme"]["csg_deductible"] == pytest.approx(340.0)

    def test_pas_de_plus_value_pas_de_choix(self):
        comp = tax.comparer_pfu_bareme(0, 20_000, 2.0, 2025, "Célibataire")
        assert comp["gain"] == 0.0


class TestBaremeTable:
    def test_toutes_les_annees_ont_une_source(self):
        for a in fb.annees_disponibles():
            assert fb.source_de(a) != "Inconnue"

    def test_annee_inconnue(self):
        with pytest.raises(ValueError):
            fb.bareme_de(1999)

    def test_pfu_par_annee(self):
        """Le PFU n'est pas un chiffre fixe : il suit les prélèvements sociaux.

        DÉFAUT CORRIGÉ. Ce test verrouillait 0,308 pour toutes les années.
        L'ancien code composait ce taux en soustrayant — 0,308 - 0,172 = 0,136
        d'IR — au lieu de lire les 12,8 % de l'article 200 A du CGI.

        La LFSS 2026 (art. 12) porte la CSG capital de 9,2 % à 10,6 %, donc les
        PS de 17,2 % à 18,6 % : le PFU passe de 30 % à 31,4 %.
        """
        assert fb.IR_FORFAITAIRE == pytest.approx(0.128)
        assert fb.taux_ps(2024) == pytest.approx(0.172)
        assert fb.taux_pfu(2024) == pytest.approx(0.300)
        assert fb.taux_ps(2025) == pytest.approx(0.172)
        assert fb.taux_pfu(2025) == pytest.approx(0.300)
        assert fb.taux_ps(2026) == pytest.approx(0.186)
        assert fb.taux_pfu(2026) == pytest.approx(0.314)

    def test_annee_sans_taux_leve_au_lieu_de_deviner(self):
        """Une année inconnue doit crier, pas retomber sur un taux par défaut."""
        with pytest.raises(ValueError):
            fb.taux_ps(2017)
        with pytest.raises(ValueError):
            fb.taux_ps(2027)

    def test_la_csg_deductible_ne_suit_pas_la_hausse(self):
        """Les 1,4 point de LFSS 2026 sont entierement non deductibles.

        La CSG deductible reste figee a 6,8 points (CGI art. 154 quinquies).
        """
        assert fb.CSG_DEDUCTIBLE == pytest.approx(0.068)
