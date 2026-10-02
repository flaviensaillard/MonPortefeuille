"""Le TWR du portefeuille : une seule source, et plus aucune dérive possible.

Le défaut que ce fichier verrouille
-----------------------------------
`dernière_valeur / première_valeur − 1` était écrit à **quatre** endroits, et
les quatre comptaient les versements comme du rendement :

| Appelant | Ce qu'il affichait |
|---|---|
| `Contexte.perf_globale_pct` → `app.py` | la perf de la page d'accueil |
| tableau annuel de `5_Performance.py` | +66 % là où la stratégie faisait 10 % |
| CAGR historique de `6_Retraite.py` | **76 %/an**, valeur par défaut de la projection |
| `oz_final / oz_initial` → `app.py` | +120 % là où la stratégie faisait 16,9 % |

Sur le portefeuille réel — 10 905 € en avril 2023, 79 394 € en octobre 2026,
alimenté chaque mois — le premier affichait +628 % cumulé.

Le dernier garde-fou de ce fichier est un **contrôle statique** : il lit les
pages et échoue si l'une d'elles recalcule un ratio de valeurs elle-même.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core import metrics
from core import session as S


# ---------------------------------------------------------------------------
# Le cas réel, en réduction
# ---------------------------------------------------------------------------
# 10 905 € au départ, 79 394 € à l'arrivée, 500 € versés chaque mois, et une
# stratégie qui rapporte 10 % par an.
DATES = [dt.date(2023, 4, 1) + dt.timedelta(days=30 * i) for i in range(43)]
CAPITAL = 10_905.0
VALEURS = []
for i, _ in enumerate(DATES):
    VALEURS.append(CAPITAL)
    CAPITAL = CAPITAL * 1.1 ** (1 / 12) + 500.0
FLUX = [0.0] + [500.0] * (len(DATES) - 1)

SNAPSHOTS = pd.DataFrame({
    "Date": pd.to_datetime(DATES),
    "patrimoine_investi_eur": VALEURS,
    "equivalent_or_oz": [v / 2000.0 for v in VALEURS],   # or stable à 2 000 €/oz
})
APPORTS = pd.DataFrame({
    "date": [d.isoformat() for d in DATES[1:]],
    "sens": ["apport"] * (len(DATES) - 1),
    "montant_eur": [500.0] * (len(DATES) - 1),
})


class CtxBidon:
    """Assez de `Contexte` pour les fonctions de `session`."""

    def __init__(self, snapshots, apports):
        self.snapshots = snapshots
        self.apports = apports


@pytest.fixture
def ctx():
    return CtxBidon(SNAPSHOTS, APPORTS)


# ---------------------------------------------------------------------------
class TestTwrPortefeuille:
    def test_les_versements_ne_sont_pas_du_rendement(self, ctx):
        """La valeur a triplé ; le rendement annuel reste de 10 %.

        Attention : `twr_portefeuille` renvoie le rendement CUMULÉ sur toute la
        période, pas l'annualisé. Sur 43 mois à 0,797 %/mois, ça fait 39,4 %.
        """
        assert VALEURS[-1] / VALEURS[0] > 3, "le cas de test doit être parlant"
        assert S.twr_portefeuille(ctx) == pytest.approx(1.00797 ** 42 - 1, abs=0.005)

        # C'est l'ANNUALISÉ qui doit valoir 10 % — c'est lui qui sert de CAGR.
        assert S.twr_annualise_portefeuille(ctx) == pytest.approx(0.10, abs=0.005)

    def test_ce_que_affichait_l_ancien_calcul(self, ctx):
        """Pour que l'ampleur du défaut reste mesurable."""
        ancien = VALEURS[-1] / VALEURS[0] - 1
        assert ancien > 2.0, f"l'ancien calcul donnait {ancien:.0%}"
        assert S.twr_portefeuille(ctx) < ancien / 2

    def test_sans_apports_les_deux_methodes_coincident(self):
        """Contrôle : sans flux, le ratio brut EST le rendement."""
        v = [100.0, 110.0, 121.0]
        snaps = pd.DataFrame({
            "Date": pd.to_datetime([dt.date(2024, 1, 1), dt.date(2024, 6, 1),
                                    dt.date(2024, 12, 31)]),
            "patrimoine_investi_eur": v,
        })
        ctx = CtxBidon(snaps, pd.DataFrame())
        assert S.twr_portefeuille(ctx) == pytest.approx(0.21)

    def test_trop_peu_de_snapshots(self):
        ctx = CtxBidon(SNAPSHOTS.iloc[:1], APPORTS)
        assert S.twr_portefeuille(ctx) is None
        assert S.twr_annualise_portefeuille(ctx) is None

    def test_table_vide(self):
        ctx = CtxBidon(pd.DataFrame(), pd.DataFrame())
        assert S.twr_portefeuille(ctx) is None

    def test_annualise(self, ctx):
        total = S.twr_portefeuille(ctx)
        jours = (DATES[-1] - DATES[0]).days
        assert S.twr_annualise_portefeuille(ctx) == pytest.approx(
            metrics.annualiser(total, jours)
        )

    def test_les_retraits_sont_des_flux_negatifs(self):
        """Un retrait ne doit pas être lu comme un apport."""
        snaps = pd.DataFrame({
            "Date": pd.to_datetime([dt.date(2024, 1, 1), dt.date(2024, 12, 31)]),
            "patrimoine_investi_eur": [10_000.0, 10_500.0],
        })
        apports = pd.DataFrame({
            "date": ["2024-12-31"], "sens": ["retrait"], "montant_eur": [500.0],
        })
        ctx = CtxBidon(snaps, apports)
        assert S.twr_portefeuille(ctx) == pytest.approx(0.10)


class TestTwrEnOrPortefeuille:
    def test_or_stable_le_rendement_est_celui_des_euros(self, ctx):
        """oz = V / 2 000 partout : l'or ne bouge pas, donc même rendement."""
        assert S.twr_en_or_portefeuille(ctx) == pytest.approx(
            S.twr_portefeuille(ctx), rel=1e-9
        )

    def test_l_ancien_ratio_d_onces_s_envole(self, ctx):
        """`oz_final / oz_initial` comptait les versements : +195 % ici."""
        ancien = SNAPSHOTS["equivalent_or_oz"].iloc[-1] / SNAPSHOTS["equivalent_or_oz"].iloc[0] - 1
        assert ancien > 1.9
        assert S.twr_en_or_portefeuille(ctx) < ancien / 2

    def test_or_manquant_renvoie_none_pas_une_valeur_inventee(self):
        """Les snapshots importés de la v1 n'ont pas d'équivalent-or."""
        sans_or = SNAPSHOTS.copy()
        sans_or["equivalent_or_oz"] = None
        assert S.twr_en_or_portefeuille(CtxBidon(sans_or, APPORTS)) is None

        partiel = SNAPSHOTS.copy()
        partiel.loc[partiel.index[3], "equivalent_or_oz"] = None
        assert S.twr_en_or_portefeuille(CtxBidon(partiel, APPORTS)) is None


# ---------------------------------------------------------------------------
# Les formules de metrics, vérifiées sur des cas dont on connaît la réponse
# ---------------------------------------------------------------------------
class TestFormuleEnOr:
    def test_or_stable_redonne_le_rendement_en_euros(self):
        v = [10000.0, 11000.0, 12100.0]
        oz = [10.0, 11.0, 12.1]        # gold_eur = V / oz = 1 000, constant
        assert metrics.twr_en_or(v, None, oz) == pytest.approx(0.21)

    def test_avec_versements_le_resultat_ne_change_pas(self):
        """L'or est stable : les flux ne doivent rien changer au rendement."""
        v = [10000.0, 16000.0, 22000.0]
        oz = [10.0, 16.0, 22.0]
        flux = [0.0, 5000.0, 5000.0]
        assert metrics.twr_en_or(v, flux, oz) == pytest.approx(
            metrics.twr_depuis(v, flux)
        )

    def test_l_or_double_le_portefeuille_plat_perd_la_moitie(self):
        """Le cas qui démasque l'oubli du `+1` dans la formule."""
        assert metrics.twr_en_or([10000.0, 10000.0], None, [10.0, 5.0]) == \
            pytest.approx(-0.50)

    def test_once_nulle_leve_une_erreur(self):
        with pytest.raises(ValueError):
            metrics.twr_en_or([100.0, 110.0], None, [10.0, 0.0])

    def test_longueurs_differentes_levees(self):
        with pytest.raises(ValueError):
            metrics.rendements_en_or([100.0, 110.0], None, [10.0])


# ---------------------------------------------------------------------------
# Gardien statique : plus aucune page ne recalcule un ratio de valeurs
# ---------------------------------------------------------------------------
class TestAucunePageNeRecalcule:
    """Le défaut a été écrit SIX fois. Ce test l'empêche de revenir.

    Il lit le source des pages et échoue si l'une d'elles indexe directement une
    colonne de performance avec `iloc`. Toutes les performances passent par
    `core.session` ; il n'y a aucune raison légitime pour une page de prendre
    la première ou la dernière valeur d'un snapshot elle-même.

    Une différence de DATES reste autorisée — elle mesure une durée, pas une
    performance.
    """

    # Les colonnes qui nourrissent une mesure de performance.
    COLONNES_PERF = ("patrimoine_investi_eur", "equivalent_or_oz")

    def _sources(self):
        import pathlib
        racine = pathlib.Path(__file__).resolve().parent.parent
        return list((racine / "pages").glob("*.py")) + [racine / "app.py"]

    def test_aucune_page_n_indexe_une_colonne_de_performance(self):
        import re
        coupables = []
        for chemin in self._sources():
            for numero, ligne in enumerate(
                chemin.read_text(encoding="utf-8").split("\n"), 1
            ):
                nu = ligne.strip()
                if nu.startswith("#") or not nu:
                    continue
                for colonne in self.COLONNES_PERF:
                    if re.search(rf'["\']{colonne}["\'].*\.iloc\[', nu):
                        coupables.append(f"{chemin.name}:{numero} {nu}")
        assert not coupables, (
            "Ces lignes prennent une valeur de snapshot directement au lieu "
            "d'appeler session.twr_portefeuille / twr_en_or_portefeuille. "
            "C'est exactement ainsi que les versements ont ete comptes comme "
            "du rendement, six fois de suite :\n  " + "\n  ".join(coupables)
        )

    def test_les_differences_de_dates_restent_autorisees(self):
        """Contrôle : le gardien ne doit pas être plus bête qu'il ne faut."""
        import pathlib
        racine = pathlib.Path(__file__).resolve().parent.parent
        source = (racine / "pages" / "5_Performance.py").read_text(encoding="utf-8")
        assert 'snaps["Date"].iloc[-1] - snaps["Date"].iloc[0]' in source


    def test_les_pages_appellent_la_fonction_partagee(self):
        """Au moins une page doit utiliser la source unique."""
        import pathlib
        appels = 0
        for chemin in self._sources():
            source = chemin.read_text(encoding="utf-8")
            appels += source.count("twr_portefeuille(")
            appels += source.count("twr_en_or_portefeuille(")
            appels += source.count("twr_annualise_portefeuille(")
            appels += source.count("flux_par_date(")
        assert appels >= 4, f"seulement {appels} appels à la source unique"



def test_twr_usd_projections_reproduit_swissquote():
    """Vérifie que `serie_performance` calcule le TWR en USD à partir de
    `Actifs Stratégiques` et des variations de `Capital investi` de `Projections`,
    sans injecter le taux de change EUR/USD dans la performance."""
    from unittest.mock import patch
    import pandas as pd
    from core import metrics, session as S

    proj = pd.DataFrame([
        {"Date": "31/12/2023", "Capital investi": 27769.0, "Actifs Stratégiques": 29534.0, "Total Global": 34372.0},
        {"Date": "30/04/2024", "Capital investi": 29757.0, "Actifs Stratégiques": 34039.0, "Total Global": 45658.0},
        {"Date": "31/12/2024", "Capital investi": 50122.0, "Actifs Stratégiques": 57986.0, "Total Global": 69850.0},
        {"Date": "31/12/2025", "Capital investi": 56707.0, "Actifs Stratégiques": 73229.0, "Total Global": 82563.0},
        {"Date": "01/10/2026", "Capital investi": 59629.92, "Actifs Stratégiques": 79007.0, "Total Global": 92313.0},
    ])

    ctx = S.Contexte()
    ctx.taux_eur_usd = 1.1245
    ctx.total_investi_usd = 79007.0
    ctx.total_investi_eur = round(79007.0 / 1.1245, 2)

    def fake_lire(table):
        if table == "Projections":
            return proj
        return pd.DataFrame()

    with patch("core.db.lire", side_effect=fake_lire):
        S._enrichir_historiques_usd(ctx)

    snaps, valeurs, flux = S.serie_performance(ctx)
    assert valeurs == [29534.0, 34039.0, 57986.0, 73229.0, 79007.0]
    assert [round(f, 2) for f in flux] == [0.0, 1988.0, 20365.0, 6585.0, 2922.92]
    rends = [0.0] + metrics.rendements_periode(valeurs, flux)
    par_an = metrics.twr_par_annee([d.date() for d in snaps["Date"]], rends)
    # Aucun faux trou dû à la ligne 29/04/2024 d'Historique ni au taux EUR/USD
    assert par_an[2024] > 0.14
    assert par_an[2025] > 0.13
    assert 0.035 < par_an[2026] < 0.045
