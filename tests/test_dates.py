"""Dates et fuseaux horaires — l'erreur « Invalid comparison ».

Pourquoi ce fichier existe
--------------------------
`core/fx.py` filtrait une série Yahoo avec `serie[serie.index <= Timestamp]`.
Yahoo renvoie un index **conscient du fuseau** (`datetime64[ns, Europe/London]`
pour une paire de change) ; comparer ça à un `Timestamp` naive lève :

    TypeError: Invalid comparison between dtype=datetime64[ns, Europe/London]
               and Timestamp

En production, ça faisait échouer **toute** recherche de cours ou de taux à une
date passée. L'utilisateur a vu :

    Chargement des transactions : Taux de change USD/EUR indisponible au
    2025-01-07 (Invalid comparison between dtype=datetime64[s, Europe/London]
    and Timestamp)

alors que Yahoo avait parfaitement la donnée.

Le défaut est resté invisible parce que la branche « aujourd'hui »
(`period="5d"`) ne compare rien : seules les dates anciennes tombaient dans le
panneau, et aucun test ne couvrait cette branche.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import numpy as np
import pytest

from core import dates


# ---------------------------------------------------------------------------
# Le helper
# ---------------------------------------------------------------------------

def test_index_avec_fuseau_est_rendu_comparable():
    """La reproduction minimale du bug."""
    idx = pd.date_range("2025-01-02", periods=5, tz="Europe/London")
    nu = dates.index_sans_fuseau(idx)

    assert nu.tz is None
    # L'heure locale du marché est conservée, pas décalée.
    assert nu[0] == pd.Timestamp("2025-01-02 00:00:00")

    # Et la comparaison qui plantait passe désormais.
    assert (nu <= pd.Timestamp("2025-01-07")).any()


def test_comparaison_directe_leve_bien_typeerror():
    """Garde-fou : si pandas change et accepte la comparaison, ce test le dit."""
    idx = pd.date_range("2025-01-02", periods=5, tz="Europe/London")
    with pytest.raises(TypeError):
        idx <= pd.Timestamp("2025-01-07")


def test_index_naive_repart_inchange():
    idx = pd.date_range("2025-01-02", periods=3)
    assert dates.index_sans_fuseau(idx).equals(idx)


def test_dernier_avant_avec_index_conscient_du_fuseau():
    """Le cas de production : une série Yahoo, une date passée."""
    idx = pd.date_range("2025-01-02", periods=5, tz="Europe/London")
    serie = pd.Series([1.10, 1.11, 1.12, 1.13, 1.14], index=idx)

    res = dates.dernier_avant(serie, pd.Timestamp("2025-01-04"))

    assert len(res) == 3                      # 02, 03, 04
    assert res.iloc[-1] == pytest.approx(1.12)


def test_dernier_avant_accepte_une_limite_avec_fuseau():
    idx = pd.date_range("2025-01-02", periods=5, tz="Europe/London")
    serie = pd.Series([1.10, 1.11, 1.12, 1.13, 1.14], index=idx)

    limite = pd.Timestamp("2025-01-04", tz="Europe/London")
    res = dates.dernier_avant(serie, limite)

    assert len(res) == 3


def test_dernier_avant_serie_vide():
    vide = pd.Series([], dtype=float)
    assert len(dates.dernier_avant(vide, pd.Timestamp("2025-01-04"))) == 0


def test_dernier_avant_ne_rien_rendre_si_tout_est_posterieur():
    idx = pd.date_range("2025-06-02", periods=3, tz="Europe/London")
    serie = pd.Series([1.0, 1.1, 1.2], index=idx)

    assert len(dates.dernier_avant(serie, pd.Timestamp("2025-01-01"))) == 0


def test_dernier_avant_tolere_un_index_non_trie():
    """Le filtrage est positionnel : l'ordre de l'index n'a pas d'importance."""
    idx = pd.to_datetime(
        ["2025-01-06", "2025-01-02", "2025-01-04"]
    ).tz_localize("Europe/London")
    serie = pd.Series([3.0, 1.0, 2.0], index=idx)

    res = dates.dernier_avant(serie, pd.Timestamp("2025-01-04"))
    assert len(res) == 2


# ---------------------------------------------------------------------------
# Les deux appelants, avec un Yahoo simulé
# ---------------------------------------------------------------------------

class FauxTicker:
    """Renvoie un historique exactement dans la forme que donne Yahoo."""

    def __init__(self, frame):
        self._frame = frame

    def history(self, period=None, start=None, end=None):
        if period:                       # branche « aujourd'hui »
            return self._frame
        pd.to_datetime(self._rame_index_milieu(start, end))
        return self._frame

    @staticmethod
    def _rame_index_milieu(start, end):
        return None


def _simuler_yahoo(monkeypatch, module, valeurs, fuseau="Europe/London"):
    """Patche `yf.Ticker` pour renvoyer une série consciente du fuseau."""
    idx = pd.date_range("2025-01-02", periods=len(valeurs), tz=fuseau)
    frame = pd.DataFrame({"Close": valeurs}, index=idx)

    class Ticker:
        def __init__(self, _symbole):
            pass

        def history(self, period=None, start=None, end=None):
            if period is not None:
                return frame
            # Respecte la fenêtre demandée, comme le fait Yahoo.
            debut = pd.Timestamp(start, tz=fuseau)
            fin = pd.Timestamp(end, tz=fuseau)
            return frame[(frame.index >= debut) & (frame.index <= fin)]

    monkeypatch.setattr(module.yf, "Ticker", Ticker)
    return frame


def test_fx_historique_ne_leve_plus_typeerror(monkeypatch):
    """Le test qui reproduit l'erreur vue en production."""
    from core import fx

    _simuler_yahoo(monkeypatch, fx, [1.10, 1.11, 1.12, 1.13, 1.14])
    fx.vider_cache()

    # Une date passée : c'est la branche qui plantait.
    taux = fx.taux("USD", "2025-01-07")

    assert taux == pytest.approx(1.14)


def test_fx_historique_prend_le_dernier_cours_anterieur(monkeypatch):
    from core import fx

    _simuler_yahoo(monkeypatch, fx, [1.10, 1.11, 1.12, 1.13, 1.14])
    fx.vider_cache()

    # Le taux retenu est celui du JOUR DEMANDÉ, pas celui du lendemain.
    assert fx.taux("USD", "2025-01-02") == pytest.approx(1.10)
    assert fx.taux("USD", "2025-01-03") == pytest.approx(1.11)
    assert fx.taux("USD", "2025-01-04") == pytest.approx(1.12)


def test_fx_n_utilise_jamais_le_taux_du_lendemain(monkeypatch):
    """Régression : le seuil était `d + 1 jour`, donc une opération datée du 2
    janvier était valorisée avec le taux du 3. Biais d'anticipation."""
    from core import fx

    _simuler_yahoo(monkeypatch, fx, [1.10, 1.11, 1.12, 1.13, 1.14])
    fx.vider_cache()

    assert fx.taux("USD", "2025-01-02") != pytest.approx(1.11)
    assert fx.taux("USD", "2025-01-02") == pytest.approx(1.10)


def test_prices_historique_ne_leve_plus_typeerror(monkeypatch):
    """Même défaut, même correctif, dans `core/prices.py`."""
    from core import prices

    _simuler_yahoo(monkeypatch, prices, [100.0, 101.0, 102.0, 103.0, 104.0])
    prices.vider_cache()

    assert prices.cours("ASML.AS", "2025-01-07") == pytest.approx(104.0)


def test_cours_or_historique_ne_leve_plus_typeerror(monkeypatch):
    """`cours_or(date)` sert à convertir les apports en onces."""
    from core import prices

    _simuler_yahoo(monkeypatch, prices, [2600.0, 2610.0, 2620.0, 2630.0, 2640.0])
    prices.vider_cache()

    assert prices.cours_or("2025-01-05") == pytest.approx(2630.0)


def test_parser_corrige_linversion_jour_mois_des_dates_iso():
    """La régression la plus importante de ce fichier.

    `pd.to_datetime("2025-01-07", dayfirst=True)` renvoie le 1er JUILLET :
    `dayfirst` lit « 01 » comme le jour et « 07 » comme le mois. Or l'application
    stocke ses dates en ISO. Toute date dont le jour est ≤ 12 — environ 39 % des
    dates — était donc décalée, et le cours ou le taux recherché à la mauvaise
    date.
    """
    assert dates.parser("2025-01-07").date().isoformat() == "2025-01-07"
    assert dates.parser("2025-03-04").date().isoformat() == "2025-03-04"
    assert dates.parser("2025-12-11").date().isoformat() == "2025-12-11"


def test_parser_comprend_aussi_le_format_de_la_v1():
    """Un CSV de la v1 écrit jj/mm/aaaa : il doit continuer de passer."""
    assert dates.parser("07/01/2025").date().isoformat() == "2025-01-07"
    assert dates.parser("15/01/2025").date().isoformat() == "2025-01-15"


def test_parser_sur_une_serie_entiere():
    serie = pd.Series(["2025-01-07", "2025-01-15", "07/01/2025"])
    res = dates.parser(serie)
    assert [d.date().isoformat() for d in res] == [
        "2025-01-07", "2025-01-15", "2025-01-07"
    ]


def test_parser_renvoie_nat_sur_une_valeur_illisible():
    assert pd.isna(dates.parser("pas une date"))


class TestLesDeuxEcrituresDonnentLeMemeJour:
    """Le défaut de production, verrouillé.

    La base écrit l'ISO (`2025-07-01`), la v1 écrit `jj/mm/aaaa` (`01/07/2025`).
    Les deux désignent le 1er juillet 2025 et doivent donner le même jour.

    Deux « correctifs » successifs ont échoué, chacun sur une des deux écritures :
      * `dayfirst=True` seul lisait l'ISO à l'envers (2025-07-01 -> 7 janvier) ;
      * `format="mixed"` + `dayfirst=True` réparait l'ISO mais faisait lire
        `01/07/2025` comme le 7 janvier.

    Et le comportement de `dayfirst` couplé à `format="mixed"` n'est pas le même
    d'une version de pandas à l'autre : un code juste en local peut être faux sur
    le serveur, sans qu'aucune ligne ne change. D'où un format IMPOSÉ d'après la
    forme de la chaîne, sans aucune heuristique.
    """

    @pytest.mark.parametrize("iso, v1", [
        ("2025-07-01", "01/07/2025"),   # 1er juillet — le cas FLXC.L
        ("2025-10-01", "01/10/2025"),   # 1er octobre — le second cas FLXC.L
        ("2026-01-05", "05/01/2026"),
        ("2026-09-01", "01/09/2026"),
        ("2025-03-18", "18/03/2025"),
        ("2026-02-02", "02/02/2026"),
    ])
    def test_les_deux_ecritures_concordent(self, iso, v1):
        assert dates.parser(iso).date() == dates.parser(v1).date()

    @pytest.mark.parametrize("iso", ["2025-07-01", "2025-10-01", "2026-01-05"])
    def test_l_iso_est_respecte(self, iso):
        """Une date ISO ne doit jamais être inversée."""
        assert dates.parser(iso).date().isoformat() == iso

    @pytest.mark.parametrize("v1, attendu", [
        ("01/07/2025", "2025-07-01"),   # 1er juillet, PAS le 7 janvier
        ("01/10/2025", "2025-10-01"),   # 1er octobre, PAS le 10 janvier
        ("07/01/2025", "2025-01-07"),   # 7 janvier
        ("10/01/2025", "2025-01-10"),   # 10 janvier
        ("05/01/2026", "2026-01-05"),
        ("18/03/2025", "2025-03-18"),
    ])
    def test_le_jour_premier_de_la_v1_est_respecte(self, v1, attendu):
        """En `jj/mm/aaaa`, le premier nombre est le JOUR."""
        assert dates.parser(v1).date().isoformat() == attendu

    @pytest.mark.parametrize("valeur", [
        "2025-07-01",
        "2025-07-01 00:00:00+00",
        "2025-07-01T00:00:00+00:00",
        dt.date(2025, 7, 1),
        dt.datetime(2025, 7, 1),
        pd.Timestamp("2025-07-01"),
        pd.Timestamp("2025-07-01", tz="UTC"),
        np.datetime64("2025-07-01"),
    ])
    def test_toutes_les_formes_de_la_base(self, valeur):
        """Tout ce que Supabase peut renvoyer doit donner le 1er juillet."""
        assert dates.parser(valeur).date() == dt.date(2025, 7, 1)

    def test_une_serie_entiere(self):
        serie = pd.Series(["2025-07-01", "01/10/2025", "18/03/2025"])
        res = dates.parser(serie)
        assert [d.date().isoformat() for d in res] == [
            "2025-07-01", "2025-10-01", "2025-03-18",
        ]

    @pytest.mark.parametrize("valeur", ["", None, float("nan"), "abc", "2025-13-45"])
    def test_valeurs_illisibles(self, valeur):
        assert pd.isna(dates.parser(valeur))


class TestDateAvecHeure:
    """La v1 écrit parfois une date AVEC son heure.

    `11/05/2026 12:38:33` doit donner le 11 mai 2026. Imposer `%d/%m/%Y` sur la
    chaîne entière échoue, et la journée serait perdue en silence — 180 lignes
    d'historique en comptaient une exactement comme ça.
    """

    @pytest.mark.parametrize("valeur, attendu", [
        ("11/05/2026 12:38:33", "2026-05-11"),
        ("11/05/2026 00:00:00", "2026-05-11"),
        ("01/07/2025 23:59:59", "2025-07-01"),
        ("2025-07-01 12:38:33", "2025-07-01"),
        ("2025-07-01T12:38:33+00:00", "2025-07-01"),
    ])
    def test_l_heure_est_ignoree(self, valeur, attendu):
        assert dates.parser(valeur).date().isoformat() == attendu

    def test_date_sans_heure_inchangee(self):
        assert dates.parser("01/07/2025").date().isoformat() == "2025-07-01"
