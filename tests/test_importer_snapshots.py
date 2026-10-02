"""Import de l'historique de valorisation de la v1 — `importer_snapshots()`.

Pourquoi ce fichier existe
--------------------------
La table s'appelle `Projections`. Elle a été cherchée pendant trois rounds :
`Historique` n'en portait que la trésorerie, et les vingt-deux autres noms
essayés n'existaient pas. Ces 180 lignes, du 01/04/2023 au 01/10/2026, sont
l'historique complet du portefeuille.

Ces tests vérifient surtout que **rien n'est inventé** :

- la répartition par poche n'existe pas dans la v1, donc elle reste NULL ;
- un jour sans taux de change disponible voit sa ligne ÉCARTÉE, pas convertie
  au hasard — le pire des résultats serait de rendre 1,0 et d'écrire un facteur
  1,13 d'erreur sans bruit ;
- les montants de la v1 sont en DOLLARS et doivent être convertis.

Et une chose qui n'est pas de l'invention mais un choix : la série du robot de
la v2 est retirée sur la période que la v1 couvre. Voyez `_purger_la_fenetre`
pour le pourquoi — en un mot, la v2 était 9 900 € trop basse avant le
02/02/2026, et mélanger les deux séries produirait un faux mouvement de plus.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jobs import importer_v1 as imp

COLONNES_V1 = ["Date", "Capital investi", "Actifs Stratégiques", "Total Global", "id"]

# Deux valuations le même jour : la v1 peut porter une saisie puis une re-saisie.
LIGNES = [
    {"Date": "01/04/2023", "Capital investi": 10905, "Actifs Stratégiques": 10905,
     "Total Global": 33668, "id": 1},
    {"Date": "30/04/2023", "Capital investi": 10978, "Actifs Stratégiques": 11101,
     "Total Global": 33791, "id": 2},
    # Une date avec son heure : la v1 en produit.
    {"Date": "11/05/2026 12:38:33", "Capital investi": 58378.92,
     "Actifs Stratégiques": 80555.353049, "Total Global": 100885.623608, "id": 46},
    # Deux lignes le même jour, valeurs différentes.
    {"Date": "04/06/2026", "Capital investi": 58378.92,
     "Actifs Stratégiques": 78430.569202, "Total Global": 98457.991551, "id": 73},
    {"Date": "04/06/2026", "Capital investi": 58378.92,
     "Actifs Stratégiques": 78336.930560, "Total Global": 98426.765209, "id": 74},
]


class _Ecrites(list):
    """Les lignes écrites — et, accrochés dessus, ce qui a été retiré.

    Une liste, parce que les tests la parcourent comme avant ; des attributs en
    plus, parce que la purge doit pouvoir être vérifiée sans casser les
    assertions existantes.
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.retires: list[str] = []
        self.existants: list[dict] = []


class _FauxClient:
    """Le minimum du client Supabase : `select`, `upsert`, `insert`, `delete`."""

    def __init__(self, ecrites, existants):
        self.ecrites = ecrites
        self.existants = existants
        self.retires = ecrites.retires

    def table(self, nom):
        return _FauxTable(self)

    # `db.lire` fait `client().table(t).select("*").execute()`.
    def select(self, *a, **k):
        pass


class _Reponse:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _FauxTable:
    def __init__(self, client):
        self.c = client

    def select(self, *a, **k):
        return _Reponse(list(self.c.existants))

    def upsert(self, ligne, on_conflict=None):
        self.c.ecrites.append(ligne)
        return _Reponse([])

    def insert(self, lignes):
        if isinstance(lignes, dict):
            lignes = [lignes]
        self.c.ecrites.extend(lignes)
        return _Reponse(list(lignes))

    def update(self, champs):
        return _Suppression(self.c)

    def delete(self):
        return _Suppression(self.c)


class _Suppression:
    def __init__(self, client):
        self.c = client

    def eq(self, *a, **k):
        return _Reponse([])

    def in_(self, champ, lot):
        self.c.retires.extend(lot)
        return _Reponse([])


@pytest.fixture
def _v1(monkeypatch):
    """Remplace `lire_v1` et neutralise l'écriture. Aucun accès réseau."""
    ecritures = _Ecrites()
    client = _FauxClient(ecritures, ecritures.existants)
    monkeypatch.setattr(imp.db, "client", lambda: client)
    monkeypatch.setattr(imp.db, "lire", lambda t: pd.DataFrame(ecritures.existants))
    monkeypatch.setattr(imp, "lire_v1", lambda t: pd.DataFrame(LIGNES))
    # Le taux de change est réel dans l'application et figé ici : un test qui
    # dépend de Yahoo tomberait au premier incident chez eux, et personne ne
    # saurait si c'est le code ou le réseau.
    monkeypatch.setattr(imp, "_taux_usd_eur", lambda jour: 1.0)
    return ecritures


# --------------------------------------------------------------------------
def test_toutes_les_dates_sont_lues(_v1, caplog):
    """180 lignes réelles en comptaient une avec son heure : elle doit passer."""
    with caplog.at_level("WARNING"):
        n = imp.importer_snapshots(dry_run=False)
    assert n == 4, "4 dates uniques sur 5 lignes (un doublon écarté)"
    dates = {e["date"] for e in _v1}
    assert "2026-05-11" in dates, "la date avec heure doit être importée"


def test_la_repartition_par_poche_reste_null(_v1):
    """La v1 ne connaît pas les poches : on n'invente pas."""
    imp.importer_snapshots(dry_run=False)
    for ligne in _v1:
        for cle in ("poche_rv_eur", "poche_energie_eur", "poche_asie_eur",
                    "poche_jgb_eur"):
            assert cle not in ligne, f"{cle} serait inventée"


def test_la_precaution_est_l_ecart(_v1):
    """La v1 ne distingue pas précaution et courant : l'écart va en précaution."""
    imp.importer_snapshots(dry_run=False)
    premiere = next(e for e in _v1 if e["date"] == "2023-04-01")
    assert premiere["precaution_eur"] == pytest.approx(33668.0 - 10905.0)
    assert premiere["courant_eur"] == 0.0


def test_le_doublon_est_signale(_v1, caplog):
    """Une ligne écartée doit être dite, jamais silencieuse."""
    with caplog.at_level("WARNING"):
        imp.importer_snapshots(dry_run=False)
    messages = " ".join(r.message for r in caplog.records)
    assert "2026-06-04" in messages, "le doublon doit apparaître dans les logs"


def test_le_dry_run_n_ecrit_rien(_v1):
    n = imp.importer_snapshots(dry_run=True)
    assert n == 4
    assert _v1 == [], "le dry-run ne doit rien écrire"


def test_les_lignes_de_tresorerie_sont_ignorees(monkeypatch, _v1):
    """Une ligne portant `Type` = Ajout/Retrait n'est pas une valorisation."""
    lignes = LIGNES + [
        {"Date": "15/06/2026", "Type": "Ajout", "Montant €": 500,
         "Actifs Stratégiques": None, "Total Global": None, "id": 80},
    ]
    monkeypatch.setattr(imp, "lire_v1", lambda t: pd.DataFrame(lignes))
    n = imp.importer_snapshots(dry_run=False)
    assert n == 4, "la ligne de trésorerie ne doit pas devenir un snapshot"


def test_table_vide(monkeypatch):
    monkeypatch.setattr(imp, "lire_v1", lambda t: pd.DataFrame())
    assert imp.importer_snapshots(dry_run=False) == 0


def test_les_colonnes_de_performance_ne_sont_pas_importees(monkeypatch, _v1):
    """pf2 calcule le TWR à la demande ; il ne le stocke jamais."""
    lignes = [{**LIGNES[0], "Score TWR %": 1.5, "Evolution cumulée %": 208.7}]
    monkeypatch.setattr(imp, "lire_v1", lambda t: pd.DataFrame(lignes))
    imp.importer_snapshots(dry_run=False)
    for ligne in _v1:
        assert "Score TWR %" not in ligne
        assert "Evolution cumulée %" not in ligne


# --------------------------------------------------------------------------
# La conversion : les montants de la v1 sont en dollars
# --------------------------------------------------------------------------
class TestLaConversionDesDollars:
    """`Actifs Stratégiques` et `Total Global` sont en USD.

    Deux preuves, faites sur les données réelles plutôt que supposées :

    1. les cours de `Donnees` sont les cotes brutes de Yahoo (IGLN.L à 79,96
       quand Yahoo cote 80,865 — la cote USD de Londres, pas une conversion) ;
    2. `Total_Apports_nets` passe de 58 378,92 à 58 625,92 le 05/06/2026, soit
       +247,00 : le `Montant $` de la ligne, quand `Montant €` dit 212,42.

    Sans conversion, l'historique entre 12 % trop haut en niveau et, plus grave,
    la performance mélange le rendement des actifs et la variation de l'euro.
    """

    def test_les_montants_sont_converti(self, _v1, monkeypatch):
        monkeypatch.setattr(imp, "_taux_usd_eur", lambda jour: 0.90)
        imp.importer_snapshots(dry_run=False)
        premiere = next(e for e in _v1 if e["date"] == "2023-04-01")
        assert premiere["patrimoine_investi_eur"] == pytest.approx(10905 * 0.90)
        assert premiere["patrimoine_total_eur"] == pytest.approx(33668 * 0.90)

    def test_la_precaution_reste_l_ecart_apres_conversion(self, _v1, monkeypatch):
        """L'écart entre les deux colonnes ne dépend pas du taux : il subit la
        même multiplication, donc la répartition reste juste."""
        monkeypatch.setattr(imp, "_taux_usd_eur", lambda jour: 1.13)
        imp.importer_snapshots(dry_run=False)
        premiere = next(e for e in _v1 if e["date"] == "2023-04-01")
        ecart = (33668 - 10905) * 1.13
        assert premiere["precaution_eur"] == pytest.approx(ecart)

    def test_un_jour_sans_taux_voit_sa_ligne_ecartee(self, _v1, monkeypatch, caplog):
        """Rendre 1,0 serait la pire des réponses : la ligne entrerait en base
        avec un facteur 1,13 d'erreur et aucun signe visible."""
        monkeypatch.setattr(
            imp, "_taux_usd_eur",
            lambda jour: None if jour == "2023-04-30" else 1.0,
        )
        with caplog.at_level("WARNING"):
            n = imp.importer_snapshots(dry_run=False)

        assert n == 3, "les quatre dates moins celle sans taux"
        assert "2023-04-30" not in {e["date"] for e in _v1}
        assert "2023-04-30" in caplog.text
        assert "écartée" in caplog.text

    def test_un_taux_null_ou_negatif_ne_passe_pas(self):
        """`_taux_usd_eur` ne rend jamais 0 ni un nombre négatif."""
        import types as _t
        class Rep:
            data = []
        def faux(jour):
            return 0.0
        # On teste directement `_taux_usd_eur` sur un cas dégénéré : la fonction
        # doit transformer un taux nul en refus, pas le laisser passer.
        vrai_fx = imp.fx.taux
        imp.fx.taux = faux
        try:
            assert imp._taux_usd_eur("2023-04-01") is None
        finally:
            imp.fx.taux = vrai_fx

    def test_une_exception_de_change_devient_un_refus(self):
        """Le réseau tombe : on écarte la ligne, on ne fait pas échouer tout
        l'import — et surtout on n'écrit pas 1,0."""
        def boum(*a, **k):
            raise RuntimeError("Yahoo indisponible")
        vrai_fx = imp.fx.taux
        imp.fx.taux = boum
        try:
            assert imp._taux_usd_eur("2023-04-01") is None
        finally:
            imp.fx.taux = vrai_fx


# --------------------------------------------------------------------------
# La purge de la fenêtre
# --------------------------------------------------------------------------
class TestLaPurgeDeLaFenetre:
    """La v2 était 9 900 € trop basse avant le 02/02/2026.
    Mesure : au 31/01/2026, `Actifs Stratégiques` = 78 416 USD, soit 65 534 €
    au taux réel du jour, quand la v2 affichait 55 640 €.

    Depuis fin février les deux séries concordent (0,13 % au 28/02, 0,97 % au
    01/10). Le désaccord est localisé avant. Mélanger les deux créerait une
    scie, et le TWR lirait chaque passage d'une série à l'autre comme un
    mouvement de plus.
    """

    def _existants(self, dates):
        return [{"date": d, "patrimoine_investi_eur": 1000.0} for d in dates]

    def test_les_lignes_de_la_v2_hors_dates_v1_sont_retirees(self, _v1):
        _v1.existants.extend(self._existants([
            "2023-04-01", "2023-04-15", "2023-04-30", "2023-05-02",
        ]))
        imp.importer_snapshots(dry_run=False)
        assert "2023-04-15" in _v1.retires
        assert "2023-05-02" in _v1.retires

    def test_les_dates_de_la_v1_sont_conservees(self, _v1):
        _v1.existants.extend(self._existants(["2023-04-01", "2023-04-15"]))
        imp.importer_snapshots(dry_run=False)
        assert "2023-04-01" not in _v1.retires

    def test_rien_hors_de_la_fenetre_n_est_touche(self, _v1):
        """Un snapshot de 2024 a sa place : la v1 ne couvre pas ce trou, le
        robot de la v2 le remplit et on n'y touche pas."""
        _v1.existants.extend(self._existants(["2022-01-01", "2027-01-01"]))
        imp.importer_snapshots(dry_run=False)
        assert "2022-01-01" not in _v1.retires
        assert "2027-01-01" not in _v1.retires

    def test_le_dry_run_ne_retire_rien(self, _v1, caplog):
        _v1.existants.extend(self._existants(["2023-04-15"]))
        with caplog.at_level("INFO"):
            imp.importer_snapshots(dry_run=True)
        assert _v1.retires == []
        assert "seraient retir" in caplog.text

    def test_le_dry_run_dit_combien_de_lignes_seraient_retirees(self, _v1, caplog):
        _v1.existants.extend(self._existants(["2023-04-15", "2023-04-20"]))
        with caplog.at_level("INFO"):
            imp.importer_snapshots(dry_run=True)
        assert "2 ligne(s) seraient retir" in caplog.text

    def test_une_lecture_impossible_ne_fait_pas_echouer_l_import(self, _v1, monkeypatch, caplog):
        """Si la lecture des snapshots tombe, on garde l'écriture et on signale.
        Refuser tout l'import pour une purge ratée serait pire : les 180 lignes
        de la v1 seraient perdues pour une question de ménage."""
        def boum(t):
            raise RuntimeError("PostgREST indisponible")
        monkeypatch.setattr(imp.db, "lire", boum)
        with caplog.at_level("ERROR"):
            n = imp.importer_snapshots(dry_run=False)
        assert n == 4
        assert "rien n'est purgé" in caplog.text


class TestLaFenetreNeDependPasDeLOrdreDeLaReponse:
    """PostgREST ne rend PAS les lignes dans l'ordre des dates.

    Sur le premier lancement réel de l'import, la première ligne rendue portait
    le 30/07/2024 alors que la plus ancienne de la table est le 01/04/2023. La
    fenêtre de purge — calculée sur la première et la dernière ligne du lot —
    partait donc dix-huit mois trop tard, et le journal annonçait
    « 2024-07-30 -> 2026-10-01 » au lieu de « 2023-04-01 -> 2026-10-01 ».

    Sans conséquence ce jour-là : le robot n'avait rien écrit avant mars 2025.
    Mais c'était de la chance. Une ligne rendue dans un ordre différent aurait
    pu laisser survivre des lignes de la v2 en début de fenêtre — exactement les
    lignes fausses qu'on veut retirer.
    """

    DESORDRE = [
        {"Date": "30/07/2024", "Capital investi": 49212,
         "Actifs Stratégiques": 53941, "Total Global": 76631, "id": 3},
        {"Date": "30/04/2024", "Capital investi": 28586,
         "Actifs Stratégiques": 31779, "Total Global": 54469, "id": 2},
        {"Date": "01/04/2023", "Capital investi": 10905,
         "Actifs Stratégiques": 10905, "Total Global": 33668, "id": 1},
        {"Date": "30/04/2023", "Capital investi": 10978,
         "Actifs Stratégiques": 11101, "Total Global": 33791, "id": 4},
    ]

    def test_la_fenetre_annoncee_part_de_la_plus_ancienne_date(self, _v1, monkeypatch, caplog):
        monkeypatch.setattr(imp, "lire_v1", lambda t: pd.DataFrame(self.DESORDRE))
        with caplog.at_level("INFO"):
            imp.importer_snapshots(dry_run=True)
        assert "2023-04-01 -> 2024-07-30" in caplog.text
        assert "(2024-07-30 -> " not in caplog.text.replace("2023-04-01 -> 2024-07-30", "")

    def test_chaque_date_est_ecrite(self, _v1, monkeypatch):
        monkeypatch.setattr(imp, "lire_v1", lambda t: pd.DataFrame(self.DESORDRE))
        imp.importer_snapshots(dry_run=False)
        assert {e["date"] for e in _v1} == {
            "2023-04-01", "2023-04-30", "2024-04-30", "2024-07-30"}

    def test_une_ligne_de_la_v2_au_debut_de_fenetre_est_bien_retiree(self, _v1, monkeypatch):
        """Le cas que le désordre aurait laissé passer : une ligne de la v2
        ANTÉRIEURE à la première ligne rendue par Supabase."""
        monkeypatch.setattr(imp, "lire_v1", lambda t: pd.DataFrame(self.DESORDRE))
        _v1.existants.extend([
            {"date": "2023-06-15", "patrimoine_investi_eur": 1000.0},
            {"date": "2024-05-15", "patrimoine_investi_eur": 1000.0},
        ])
        imp.importer_snapshots(dry_run=False)
        assert "2023-06-15" in _v1.retires, \
            "une ligne de la v2 entre avril 2023 et juillet 2024 doit partir"
        assert "2024-05-15" in _v1.retires
