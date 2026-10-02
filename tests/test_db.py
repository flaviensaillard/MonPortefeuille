"""Tests de la couche d'accès Supabase (`core/db.py`).

Pourquoi ce fichier existe
--------------------------
Deux bugs réels sont passés en production et ont coûté un aller-retour à
l'utilisateur, parce que rien ne testait `core/db.py` :

1. `existe()` faisait `select("id")`. Les tables `pf2_cours`, `pf2_fx` et
   `pf2_inflation` ont une clé primaire **composite** et pas de colonne `id` :
   la requête échouait, et le robot concluait à tort que les tables manquaient.

2. `remplacer()` chaînait `.on_conflict(...)` sur le builder. Dans postgrest-py,
   `on_conflict` est un paramètre mot-clé de `upsert()`, pas une méthode :
   `AttributeError` à l'exécution.

Ces tests n'appellent pas Supabase. Ils interceptent `httpx.Client.send` et
vérifient la requête HTTP réellement produite — c'est la forme exacte du bug,
et ça ne dépend d'aucune credential.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import Response


@pytest.fixture
def client_supabase(monkeypatch):
    """Un vrai client supabase branché sur une URL fictive.

    Le transport HTTP est remplacé : chaque requête est capturée au lieu d'être
    envoyée. On récupère ainsi la méthode, l'URL et le corps, ce qui suffit à
    vérifier qu'un `on_conflict` est bien parti.
    """
    monkeypatch.setenv("SUPABASE_URL", "https://exemple.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "cle-fictive-de-test")

    captures: list[tuple[str, str, str]] = []

    def faux_send(self, request, **kwargs):
        captures.append((request.method, str(request.url), request.read().decode()))
        return Response(200, json=[], request=request)

    monkeypatch.setattr(httpx.Client, "send", faux_send)

    import core.db as db
    db._client = None  # le client est mémoïsé : on repart d'un état propre
    yield db, captures
    db._client = None


def url_de(captures) -> str:
    return captures[-1][1]


def corps_de(captures) -> str:
    return captures[-1][2]


# ---------------------------------------------------------------------------
# remplacer() : l'upsert idempotent
# ---------------------------------------------------------------------------

def test_remplacer_transmet_on_conflict_dans_l_url(client_supabase):
    """Le bug n°2 : `on_conflict` doit partir en paramètre d'URL de PostgREST."""
    db, captures = client_supabase
    db.remplacer(
        db.T_TRANSACTIONS,
        [{"ticker": "ASML", "sens": "achat", "date": "2024-01-15",
          "quantite": 10, "cours": 700, "devise": "EUR"}],
        on_conflict="ticker,sens,date,quantite,cours",
    )
    url = url_de(captures)
    assert url.startswith("https://exemple.supabase.co/rest/v1/pf2_transactions")
    assert captures[-1][0] == "POST"
    assert "on_conflict=" in url
    # les virgules sont encodées mais l'ordre des colonnes doit être intact
    assert "ticker%2Csens%2Cdate%2Cquantite%2Ccours" in url


def test_remplacer_sans_cible_de_conflit(client_supabase):
    """Sans cible de conflit, PostgREST doit se rabattre sur la clé primaire."""
    db, captures = client_supabase
    db.remplacer(db.T_COURS, [{"ticker": "IGLN.L", "date": "2026-09-30",
                               "cours": 120.5, "devise": "USD"}])
    url = url_de(captures)
    assert "/rest/v1/pf2_cours" in url
    assert "on_conflict" not in url


def test_remplacer_liste_vide_n_appelle_rien(client_supabase):
    db, captures = client_supabase
    db.remplacer(db.T_TRANSACTIONS, [], on_conflict="ticker")
    assert captures == []


def test_remplacer_envoie_les_lignes_en_corps(client_supabase):
    db, captures = client_supabase
    db.remplacer(db.T_FX, [{"devise": "USD", "contre": "EUR", "date": "2026-09-30",
                            "taux": 0.85}], on_conflict="devise,contre,date")
    assert "USD" in corps_de(captures)
    assert "0.85" in corps_de(captures)


# ---------------------------------------------------------------------------
# ajouter_snapshot()
# ---------------------------------------------------------------------------

def test_snapshot_utilise_la_contrainte_unique_de_date(client_supabase):
    db, captures = client_supabase
    db.ajouter_snapshot({"date": "2026-09-30", "patrimoine_total_eur": 100000,
                         "patrimoine_investi_eur": 90000})
    url = url_de(captures)
    assert "/rest/v1/pf2_snapshots" in url
    assert "on_conflict=date" in url
    assert "patrimoine_total_eur" in corps_de(captures)


# ---------------------------------------------------------------------------
# existe() : le bug n°1
# ---------------------------------------------------------------------------

def test_existe_interroge_select_etoile(client_supabase):
    """Le bug n°1 : `select("id")` cassait sur les tables à clé composite."""
    db, captures = client_supabase
    assert db.existe(db.T_COURS) is True
    assert "select=%2A" in url_de(captures)
    assert "select=%22id%22" not in url_de(captures)


def test_existe_renvoie_faux_si_la_table_manque(client_supabase, monkeypatch):
    db, captures = client_supabase

    def faux_send(self, request, **kwargs):
        return Response(404, json={"message": "relation does not exist"}, request=request)

    monkeypatch.setattr(httpx.Client, "send", faux_send)
    db._client = None
    assert db.existe("pf2_inexistante") is False


def test_toutes_les_tables_composees_sont_probees(client_supabase):
    """Les trois tables à clé primaire composite doivent répondre sans erreur."""
    db, _ = client_supabase
    for table in (db.T_COURS, db.T_FX, db.T_INFLATION):
        assert db.existe(table) is True, table


# ---------------------------------------------------------------------------
# ecrire()
# ---------------------------------------------------------------------------

def test_ecrire_est_un_insert_pur(client_supabase):
    db, captures = client_supabase
    db.ecrire(db.T_ALERTES, [{"Titre": "test", "Message": "ok", "Niveau": "info"}])
    url = url_de(captures)
    assert "/rest/v1/pf2_alertes" in url
    assert "on_conflict" not in url  # un insert, jamais un upsert


def test_ecrire_liste_vide(client_supabase):
    db, captures = client_supabase
    assert db.ecrire(db.T_ALERTES, []) == 0
    assert captures == []


# ---------------------------------------------------------------------------
# Traduction des erreurs PostgREST
# ---------------------------------------------------------------------------
# Une table verrouillée par RLS se comporte comme une table VIDE en lecture :
# `existe()` la déclare présente, et l'échec n'arrive qu'au premier INSERT.
# D'où l'intérêt de dire à l'utilisateur quoi faire, plutôt que de lui montrer
# un objet `APIError`.

def _api_error(message, code):
    from postgrest.exceptions import APIError
    return APIError({"message": message, "code": code, "hint": None, "details": None})


def test_erreur_rls_devient_un_message_actionnable():
    from core.db import _traduire_erreur

    exc = _api_error(
        'new row violates row-level security policy for table "pf2_transactions"',
        "42501",
    )
    res = _traduire_erreur("pf2_transactions", exc)

    assert isinstance(res, PermissionError)
    assert "Row Level Security" in str(res)
    assert "002_rls.sql" in str(res)      # le mode d'emploi est dans le message
    assert "SQL Editor" in str(res)


def test_erreur_colonne_inconnue_est_expliquee():
    from core.db import _traduire_erreur

    exc = _api_error(
        "Could not find the 'montant_net' column of 'pf2_transactions' "
        "in the schema cache",
        "PGRST204",
    )
    res = _traduire_erreur("pf2_transactions", exc)

    assert isinstance(res, ValueError)
    assert "montant_net" in str(res)
    assert "001_init.sql" in str(res)


def test_erreur_inconnue_est_lassee_telle_quelle():
    """On ne masque pas une vraie panne réseau derrière un message rassurant."""
    from core.db import _traduire_erreur

    originale = _api_error("connection reset", "500")
    assert _traduire_erreur("pf2_transactions", originale) is originale


def test_erreur_sans_attribut_code_est_geree():
    """Certaines exceptions n'ont ni `code` ni `message`."""
    from core.db import _traduire_erreur

    assert isinstance(_traduire_erreur("pf2_transactions", RuntimeError("boom")),
                      RuntimeError)


def test_remplacer_traduit_l_erreur_rls(client_supabase, monkeypatch):
    """Le chemin complet : `remplacer()` doit lever `PermissionError`, pas APIError."""
    db, _ = client_supabase

    class FauxUpsert:
        def execute(self):
            raise _api_error(
                'new row violates row-level security policy for table "pf2_transactions"',
                "42501",
            )

    class FauxTable:
        def upsert(self, *_a, **_k):
            return FauxUpsert()

    class FauxClient:
        def table(self, _n):
            return FauxTable()

    monkeypatch.setattr(db, "client", lambda: FauxClient())

    with pytest.raises(PermissionError, match="002_rls.sql"):
        db.remplacer(db.T_TRANSACTIONS, [{"ticker": "ASML"}],
                     on_conflict="ticker")
