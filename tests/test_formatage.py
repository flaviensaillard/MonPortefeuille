"""Formatage des nombres — `ui.eur()` et `ui.pct()`.

Ces tests existent parce qu'un défaut de spécification de format a vécu
jusqu'en production : `f"{montant:,.{decimales} f}"` (un espace entre la
précision et le type) lève `ValueError: Invalid format specifier`, et rien ne
l'avait attrapé. Chaque plantage précédent arrêtait l'application avant qu'elle
ne mette en forme le premier montant.

La leçon : un helper de formatage ne se teste pas avec `None` seulement, il se
teste avec de vrais nombres. Un `None` ne prouve qu'une seule des branches.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core import ui


class TestEur:
    def test_nombre_simple(self):
        # C'est exactement l'appel qui plantait : pages/1_Liste_des_actifs.py:52.
        assert ui.eur(1234.5) == "1 234,50 €"

    def test_zero(self):
        assert ui.eur(0) == "0,00 €"

    def test_negatif(self):
        assert ui.eur(-9876.54) == "-9 876,54 €"

    def test_grand_montant_groupe_par_milliers(self):
        assert ui.eur(1000000.75) == "1 000 000,75 €"

    def test_decimales_demandees(self):
        assert ui.eur(3.14159, decimales=4) == "3,1416 €"
        assert ui.eur(3.14159, decimales=0) == "3 €"

    def test_none_renvoie_un_tiret_cadratin(self):
        assert ui.eur(None) == "—"

    def test_chaine_numerique_est_coercee(self):
        # Une chaîne lève `ValueError`, pas `TypeError` : le seul garde-fou sur
        # `None` donnait une fausse confiance.
        assert ui.eur("1234.5") == "1 234,50 €"

    def test_chaine_non_numerique_renvoie_le_tiret(self):
        assert ui.eur("abc") == "—"

    def test_tres_petit_montant(self):
        assert ui.eur(0.004) == "0,00 €"

    def test_nan_renvoie_le_tiret_pas_nan_euro(self):
        """NaN traverse `<= 0` : sans ce garde-fou il s'afficherait « nan € ».

        Un NaN dans une valorisation est pire qu'une absence — ça ressemble à un
        montant, et ça contamine tout le tableau.
        """
        assert ui.eur(float("nan")) == "—"
        assert ui.pct(float("nan")) == "—"
        assert ui.points(float("nan")) == "—"

    def test_infini_renvoie_le_tiret(self):
        assert ui.eur(float("inf")) == "—"
        assert ui.eur(float("-inf")) == "—"
        assert ui.pct(float("inf")) == "—"

    def test_aucune_exception_sur_une_liste_de_cas(self):
        """Balayage large : toute valeur doit produire une chaîne, jamais lever."""
        for valeur in [0, 1, -1, 0.5, 12345.6789, 1e12, -1e12, 1e-9,
                      "42", "0.1", None, "", "—", float("inf"), float("-inf"),
                      float("nan"), [], {}, object()]:
            sortie = ui.eur(valeur)
            assert isinstance(sortie, str)
            assert sortie
            assert "nan" not in sortie.lower()
            assert "inf" not in sortie.lower()


class TestPct:
    def test_nombre_simple(self):
        assert ui.pct(0.1234) == "12.3 %"

    def test_negatif(self):
        assert ui.pct(-0.05) == "-5.0 %"

    def test_zero(self):
        assert ui.pct(0) == "0.0 %"

    def test_none(self):
        assert ui.pct(None) == "—"

    def test_signe(self):
        assert ui.pct(0.1234, signe=True) == "+12.3 %"
        assert ui.pct(-0.1234, signe=True) == "-12.3 %"
        assert ui.pct(None, signe=True) == "—"

    def test_aucune_exception_sur_une_liste_de_cas(self):
        for valeur in [0, 1, -1, 0.5, 12.345, 1e6, -1e6, "42", None, "",
                      float("nan"), float("inf"), [], {}, object()]:
            assert isinstance(ui.pct(valeur), str)


class TestAutresHelpers:
    """Les autres helpers de formatage du même module."""

    def test_points(self):
        assert ui.points(1.234) == "+1.2 pts"
        assert ui.points(-1.234) == "-1.2 pts"

    def test_couleur_ecart(self):
        assert ui.couleur_ecart(0.0, 2.0) == "normal"
        assert ui.couleur_ecart(1.0, 2.0) == "normal"
        assert ui.couleur_ecart(3.0, 2.0) == "attention"
        assert ui.couleur_ecart(6.0, 2.0) == "critique"

    def test_points_ne_leve_pas(self):
        for valeur in [0, 1.5, -1.5, 1e9, -1e9, float("nan"), float("inf")]:
            assert isinstance(ui.points(valeur), str)


class TestFormatSpecifiers:
    """Garde-fou contre la réapparition du défaut.

    Un drapeau (` `, `,`, `0`, `+`, `-`) doit précéder la largeur et la
    précision. `f"{x:,.2 f}"` place l'espace après la précision : Python refuse.
    """

    @pytest.mark.parametrize("spec", [
        ",.2f",        # attendu
        " ,.2f",       # espace avant : correct aussi
        ",.4f",
        ",.0f",
        ".2f",
        "10,.2f",
    ])
    def test_spec_valide_ne_leve_pas(self, spec):
        assert f"{1234.5:{spec}}"

    @pytest.mark.parametrize("spec", [
        ",.2 f",       # le défaut d'origine
        ",.4 f",
        ".2 f",
        ",.0 f",
    ])
    def test_spec_avec_espace_apres_la_precision_leve(self, spec):
        """Documente pourquoi le défaut était invisible : il ne se déclenche
        qu'à l'exécution, sur une valeur réelle, jamais à l'import."""
        with pytest.raises(ValueError, match="Invalid format specifier"):
            f"{1234.5:{spec}}"


class TestQuantite:
    """`ui.quantite()` — assez de décimales pour lire une quantité d'actifs.

    L'affichage était `f"{q:,.4f}"` : « 0,0575 » pour 0,05747 BTC, et « 0,0000 »
    pour une petite poche crypto. Deux positions devenaient indistinguables, et
    la quantité réelle était illisible. Le nombre de décimales s'adapte donc à
    la grandeur de la quantité.
    """

    @pytest.mark.parametrize("valeur, attendu", [
        (0.05747, "0,05747"),      # le cas BTC : 4 décimales ne suffisaient pas
        (0.005747, "0,005747"),
        (0.06, "0,06"),
        (0.0001, "0,0001"),
        (0.00001234, "0,00001234"),
        (800, "800"),               # un nombre entier ne prend pas de décimales
        (2255, "2 255"),
        (140, "140"),
        (-22.0, "-22"),
        (0, "0"),
    ])
    def test_quantite(self, valeur, attendu):
        assert ui.quantite(valeur) == attendu

    def test_garde_assez_de_chiffres_significatifs(self):
        """0,05747 ne doit pas s'afficher 0,0575 : on perd de l'information.

        Le bon critere est l'aller-retour : relu, le texte doit redonner la
        quantite d'origine. Compter des caracteres ne prouve rien.
        """
        for valeur in [0.05747, 0.005747, 0.00001234, 2255, 800, 0.06]:
            texte = ui.quantite(valeur)
            relu = float(texte.replace(" ", "").replace(",", "."))
            assert relu == pytest.approx(valeur), f"{valeur} -> {texte!r}"

    def test_nan_et_none(self):
        assert ui.quantite(None) == "—"
        assert ui.quantite(float("nan")) == "—"

    def test_jamais_leve_sur_une_valeur_reelle(self):
        """Un helper de formatage plante en production, pas dans les tests."""
        for v in [0.0, 1e-12, 1e12, 0.05747, 112988.0, 12345678.9, -0.0000001]:
            assert isinstance(ui.quantite(v), str)


class TestFormatageDesDates:
    """`ui.jour` : une date en français, jamais en ISO.

    Le défaut qu'il corrige : les alertes de la page Performance affichaient
    « 2024-04-30 » quand le versement litigieux est daté du 29/04/2024. Une date
    ISO se survole — on ne voit pas qu'elle désigne un autre jour que celui
    qu'on cherche. Le reste de l'application parle en jj/mm/aaaa depuis le
    début ; les alertes parlent la même langue maintenant.
    """

    def test_un_date(self):
        assert ui.jour(dt.date(2024, 4, 30)) == "30/04/2024"

    def test_un_datetime(self):
        assert ui.jour(dt.datetime(2026, 2, 2, 23, 59)) == "02/02/2026"

    def test_une_chaine_iso(self):
        assert ui.jour("2024-04-30") == "30/04/2024"

    def test_un_timestamp_pandas(self):
        assert ui.jour(pd.Timestamp("2024-03-30")) == "30/03/2024"

    def test_none_rend_le_defaut(self):
        assert ui.jour(None) == "—"
        assert ui.jour(None, defaut="·") == "·"

    def test_une_chaine_illisible_ne_leve_pas(self):
        """Une date illisible doit afficher un tiret, pas faire sauter la page."""
        assert ui.jour("pas une date") == "—"
        assert ui.jour("") == "—"

    def test_nat_pandas(self):
        assert ui.jour(pd.NaT) == "—"

    def test_le_defaut_est_personnalisable(self):
        assert ui.jour(float("nan"), defaut="non daté") == "non daté"



def test_usd_affiche_dollar_espace_insécable():
    from core import ui
    assert ui.usd(79007.0) == "79 007,00 $"
    assert ui.usd(79007.0, decimales=0) == "79 007 $"


def test_usd_eur_affiche_dollar_puis_euro():
    from core import ui
    ui.definir_taux_eur_usd(1.1245)
    assert ui.usd_eur(79007.0, 70259.67) == "79 007,00 $ / 70 259,67 €"
    assert ui.usd_eur(1124.50) == "1 124,50 $ / 1 000,00 €"
