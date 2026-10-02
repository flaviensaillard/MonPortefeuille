"""`db.snapshots()` — le pont snake_case vers `Date`.

Pourquoi ce fichier existe
--------------------------
L'application plantait avec `Chargement des historiques : 'Date'` — un
`KeyError` sur la colonne de date. La table `pf2_snapshots` stocke `date` en
snake_case ; les pages lisent `Date`. `db.snapshots()` ne faisait pas le pont,
alors que `charger_transactions()` le fait depuis longtemps pour les
transactions.

Le défaut est resté invisible des mois parce que **la table était vide** : le
`if df.empty` court-circuitait avant la ligne fautive. Il a surgi le jour où la
reconstitution de l'historique a enfin écrit des lignes.

La leçon, verrouillée ici : un garde-fou contre une table vide ne prouve rien
sur une table pleine. Les deux cas doivent être testés.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core import db

# Tel que PostgREST renvoie `pf2_snapshots` : snake_case, dates ISO.
LIGNES_SUPABASE = [
    {
        "id": 1, "date": "2026-09-29",
        "patrimoine_total_eur": 69500.0, "patrimoine_investi_eur": 69200.0,
        "precaution_eur": 0.0, "courant_eur": 300.0,
        "cours_or_usd": 4180.0, "equivalent_or_oz": 14.6,
        "poche_rv_eur": 14000.0, "poche_energie_eur": 22000.0,
        "poche_asie_eur": 19900.0, "poche_jgb_eur": 13400.0,
    },
    {
        "id": 2, "date": "2026-09-30",
        "patrimoine_total_eur": 69560.31, "patrimoine_investi_eur": 69560.31,
        "precaution_eur": 0.0, "courant_eur": 0.0,
        "cours_or_usd": 4186.7, "equivalent_or_oz": 14.65,
        "poche_rv_eur": 14192.96, "poche_energie_eur": 22014.71,
        "poche_asie_eur": 19899.79, "poche_jgb_eur": 13452.85,
    },
]


@pytest.fixture
def _simuler_lecture(monkeypatch):
    """Remplace `db.lire` pour rendre le contenu qu'on lui dicte."""
    def poser(contenu):
        monkeypatch.setattr(db, "lire", lambda table: contenu.copy())
    return poser


# --------------------------------------------------------------------------
# Le cas qui plantait : une table PLEINE
# --------------------------------------------------------------------------
def test_table_pleine_ne_leve_plus_keyerror(_simuler_lecture):
    """Le défaut de production : `KeyError: 'Date'` sur la première ligne."""
    _simuler_lecture(pd.DataFrame(LIGNES_SUPABASE))
    df = db.snapshots()          # ne doit PAS lever
    assert "Date" in df.columns


def test_table_pleine_garde_toutes_les_lignes(_simuler_lecture):
    _simuler_lecture(pd.DataFrame(LIGNES_SUPABASE))
    df = db.snapshots()
    assert len(df) == 2


def test_les_colonnes_lues_par_les_pages_sont_presentes(_simuler_lecture):
    """`4_Suivi.py` lit `Date` + snake_case ; `5_Performance.py` lit `Date`."""
    _simuler_lecture(pd.DataFrame(LIGNES_SUPABASE))
    df = db.snapshots()
    for colonne in ["Date", "patrimoine_investi_eur", "precaution_eur",
                    "equivalent_or_oz", "poche_rv_eur", "poche_asie_eur"]:
        assert colonne in df.columns, f"{colonne} manque : les pages plantent"


def test_les_dates_sont_triees(_simuler_lecture):
    _simuler_lecture(pd.DataFrame(LIGNES_SUPABASE))
    df = db.snapshots()
    assert df["Date"].tolist() == sorted(df["Date"].tolist())


def test_le_renommage_est_idempotent(_simuler_lecture):
    """Un DataFrame déjà au format `Date` repart inchangé."""
    deja = pd.DataFrame(LIGNES_SUPABASE).rename(columns={"date": "Date"})
    _simuler_lecture(deja)
    df = db.snapshots()
    assert "Date" in df.columns and len(df) == 2


# --------------------------------------------------------------------------
# Le cas qui masquait le défaut : une table VIDE
# --------------------------------------------------------------------------
def test_table_vide_ne_leve_pas(_simuler_lecture):
    _simuler_lecture(pd.DataFrame())
    df = db.snapshots()
    assert df.empty


def test_table_vide_sans_aucune_colonne(_simuler_lecture):
    """`pd.DataFrame([])` n'a AUCUNE colonne : c'est ce que renvoie Supabase
    sur une table vide. Le `if df.empty` doit court-circuiter avant tout accès."""
    _simuler_lecture(pd.DataFrame([]))
    df = db.snapshots()
    assert df.empty


def test_ligne_a_date_illisible_est_ecartee(_simuler_lecture):
    lignes = [dict(LIGNES_SUPABASE[0]), {**LIGNES_SUPABASE[1], "date": "pas-une-date"}]
    _simuler_lecture(pd.DataFrame(lignes))
    df = db.snapshots()
    assert len(df) == 1, "une date illisible doit être écartée, pas faire échouer"
