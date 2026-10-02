"""Couche d'accès Supabase.

CORRECTIONS PAR RAPPORT À LA V1
-------------------------------
1. **La table `Donnees` disparaît.** Dans la v1, les quantités étaient saisies à
   la main dans `Donnees` en parallèle de `Transaction`, et les deux pouvaient
   diverger sans que rien ne le détecte. Les positions sont désormais un
   *résultat* calculé depuis les transactions. Une seule source de vérité.

2. **Les credentials en dur sont supprimés.** `take_snapshot.py` et `calc_perf.py`
   de la v1 contenaient l'URL et la clé Supabase commitées en clair dans un repo
   public. Ici, tout passe par les variables d'environnement ou les secrets
   Streamlit, et l'absence de credential est une erreur explicite.

3. **Nouvelles tables, préfixe `pf2_`.** La base est partagée avec la v1 : on ne
   touche pas aux tables existantes, on en crée de nouvelles. La v1 continue de
   fonctionner, et la migration est réversible.
"""

from __future__ import annotations

import logging
import os

import pandas as pd
from . import dates

log = logging.getLogger(__name__)


class SecretsManquants(Exception):
    """Les credentials Supabase ne sont pas configurés."""


# Tables de la nouvelle application.
T_TRANSACTIONS = "pf2_transactions"
T_APPORTS = "pf2_apports"
T_SNAPSHOTS = "pf2_snapshots"
T_COURS = "pf2_cours"
T_FX = "pf2_fx"
T_INFLATION = "pf2_inflation"
T_ALERTES = "pf2_alertes"

TOUTES_LES_TABLES = (
    T_TRANSACTIONS, T_APPORTS, T_SNAPSHOTS, T_COURS, T_FX, T_INFLATION, T_ALERTES,
)


def _credentials() -> tuple[str, str]:
    """Récupère les credentials. Lève `SecretsManquants` s'ils sont absents."""
    url = os.environ.get("SUPABASE_URL")
    cle = os.environ.get("SUPABASE_KEY")

    if not url or not cle:
        try:
            import streamlit as st
            url = url or st.secrets.get("SUPABASE_URL")
            cle = cle or st.secrets.get("SUPABASE_KEY")
        except Exception:
            pass

    if not url or not cle:
        raise SecretsManquants(
            "Credentials Supabase absents. Définissez SUPABASE_URL et SUPABASE_KEY "
            "en variables d'environnement (GitHub Actions) ou dans "
            ".streamlit/secrets.toml. Aucune valeur de repli n'est fournie : "
            "une application qui devine sa base de données est une application "
            "qui écrit au mauvais endroit."
        )
    return url, cle


_client = None


def client():
    """Client Supabase, mémoïsé."""
    global _client
    if _client is None:
        from supabase import create_client
        url, cle = _credentials()
        _client = create_client(url, cle)
    return _client


def reinitialiser() -> None:
    global _client
    _client = None


# ---------------------------------------------------------------------------
# Lecture / écriture générique
# ---------------------------------------------------------------------------

def _traduire_erreur(table: str, exc: Exception) -> Exception:
    """Transforme une erreur PostgREST en message exploitable.

    Le cas qui compte : `42501`, la violation de Row Level Security. Les tables
    `pf2_` ont été créées avec RLS actif et, sans politique, PostgreSQL refuse
    toute écriture avec la clé publique — alors que les lectures aboutissent en
    renvoyant zéro ligne. Le contrôle d'existence concluait donc « la table est
    là », et l'échec n'apparaissait qu'au premier INSERT, sous forme d'un objet
    `APIError` que personne ne peut déchiffrer.

    On renvoie une exception qui dit quoi faire. Le correctif est dans
    `migrations/002_rls.sql`.
    """
    code = str(getattr(exc, "code", None) or "")
    message = str(getattr(exc, "message", "") or exc)

    if code == "42501" or "row-level security" in message:
        return PermissionError(
            f"Écriture refusée sur `{table}` par la sécurité de Supabase "
            f"(Row Level Security).\n"
            f"Vos tables existent et sont lisibles, mais aucune politique "
            f"n'autorise l'écriture avec votre clé.\n"
            f"Correctif : Supabase > SQL Editor > New query, collez le contenu "
            f"de `migrations/002_rls.sql`, puis Run.\n"
            f"(détail technique : {message})"
        )
    if code == "PGRST204" or "schema cache" in message:
        return ValueError(
            f"Colonne inconnue dans `{table}` : {message}\n"
            f"Le code écrit une colonne que la table n'a pas. Vérifiez que "
            f"`migrations/001_init.sql` a bien été exécuté en entier."
        )
    return exc


def verifier_ecriture() -> None:
    """Vérifie qu'une écriture est possible, AVANT de lancer l'opération.

    Pourquoi un contrôle séparé : l'échec RLS est **muet en lecture**. Une table
    verrouillée en écriture se comporte comme une table vide — le contrôle
    d'existence la déclare présente, et la catastrophe n'arrive qu'au premier
    INSERT, après des minutes de traitement.

    On sonde donc `pf2_alertes`, la table la plus simple (tous ses champs ont
    une valeur par défaut sauf titre et message), puis on supprime la sonde.
    Si l'écriture est refusée, on lève tout de suite une erreur qui dit quoi
    faire.
    """
    try:
        rep = client().table(T_ALERTES).insert({
            "titre": "Sonde d'écriture",
            "message": "Ligne de contrôle, supprimée immédiatement.",
        }).execute()
    except Exception as exc:
        raise _traduire_erreur(T_ALERTES, exc) from exc

    for ligne in rep.data or []:
        try:
            client().table(T_ALERTES).delete().eq("id", ligne["id"]).execute()
        except Exception:
            # La sonde reste : sans importance, elle est inoffensive et visible.
            log.warning("Sonde d'écriture non supprimée (id=%s).", ligne.get("id"))


def lire(table: str) -> pd.DataFrame:
    """Lit une table entière."""
    rep = client().table(table).select("*").execute()
    return pd.DataFrame(rep.data or [])


def ecrire(table: str, lignes: list[dict]) -> int:
    """Insère des lignes. Retourne le nombre inséré."""
    if not lignes:
        return 0
    try:
        rep = client().table(table).insert(lignes).execute()
    except Exception as exc:
        raise _traduire_erreur(table, exc) from exc
    return len(rep.data or [])


def remplacer(table: str, lignes: list[dict], on_conflict: str | None = None) -> None:
    """Insère ou met à jour des lignes (upsert), de façon idempotente.

    CORRECTION : la première version faisait `delete().neq("id", -1)` puis
    réinsérait. Deux défauts. D'abord, `pf2_cours`, `pf2_fx` et `pf2_inflation`
    n'ont pas de colonne `id`, donc le delete échouait. Ensuite, un delete suivi
    d'un insert n'est pas idempotent : une panne entre les deux perd les données.

    L'upsert règle les deux problèmes. `on_conflict` permet de cibler un index
    unique quand la clé primaire n'est pas la bonne — cas de `pf2_transactions`,
    dont la clé primaire est `id` mais dont l'unicité réelle porte sur le tuple
    ticker/sens/date/quantite/cours.

    ATTENTION à la forme de l'appel : dans postgrest-py (le client bas niveau de
    supabase-py), `on_conflict` est un **paramètre mot-clé de `upsert()`**, pas
    une méthode chaînable. `builder.upsert(...).on_conflict(...)` lève
    `AttributeError: 'SyncQueryRequestBuilder' object has no attribute
    'on_conflict'`. Il faut donc `upsert(lignes, on_conflict=...)`.
    """
    if not lignes:
        return
    try:
        requete = client().table(table).upsert(lignes, on_conflict=on_conflict or "")
        requete.execute()
    except Exception as exc:
        raise _traduire_erreur(table, exc) from exc


def maj_ligne(table: str, id_ligne, champs: dict) -> None:
    client().table(table).update(champs).eq("id", id_ligne).execute()


def existe(table: str) -> bool:
    """Vrai si la table existe et est accessible.

    CORRECTION : la v1 de cette fonction faisait `select("id")`, ce qui supposait
    que toute table possède une colonne `id`. Ce n'est pas le cas de `pf2_cours`,
    `pf2_fx` et `pf2_inflation`, dont la clé primaire est composite. On interroge
    donc `*` avec une limite d'une ligne : ça marche quelle que soit la structure.
    """
    try:
        client().table(table).select("*").limit(1).execute()
        return True
    except Exception as exc:
        log.warning("Table %s inaccessible : %s", table, exc)
        return False


def tables_presentes() -> dict[str, bool]:
    """État des tables attendues — affiché à l'utilisateur au démarrage."""
    return {t: existe(t) for t in TOUTES_LES_TABLES}


# ---------------------------------------------------------------------------
# Accès métier
# ---------------------------------------------------------------------------

def transactions() -> pd.DataFrame:
    return lire(T_TRANSACTIONS)


def apports() -> pd.DataFrame:
    return lire(T_APPORTS)


def snapshots() -> pd.DataFrame:
    """Lit `pf2_snapshots`, colonne de date renommee en `Date`.

    La table stocke `date` en snake_case (voyez migrations/001_init.sql) ; les
    pages, elles, lisent `Date` — comme pour les transactions, ou
    `ALIAS_COLONNES` fait le meme pont. Sans ce renommage, la PREMIERE ligne de
    `pf2_snapshots` faisait sauter toute l'application avec `KeyError: 'Date'`.

    Le defaut est reste invisible des mois parce que la table etait vide : le
    `if df.empty` court-circuitait avant la ligne fautive. Il a surgi le jour ou
    la reconstitution de l'historique a enfin ecrit des lignes. Un garde-fou
    contre une table vide ne prouve rien sur une table pleine.
    """
    df = lire(T_SNAPSHOTS)
    if df.empty:
        return df

    # La colonne de date s'appelle `date` dans le schéma pf2, mais `Date` dans
    # un import v1. On accepte les deux. Si elle n'a aucun des deux noms, on le
    # DIT au lieu de laisser un `KeyError: 'Date'` muet : l'utilisateur ne peut
    # pas corriger ce qu'il ne peut pas voir.
    colonne = next((c for c in ("Date", "date") if c in df.columns), None)
    if colonne is None:
        raise ValueError(
            "La table pf2_snapshots n'a aucune colonne de date. Colonnes "
            f"trouvées : {', '.join(map(str, df.columns))}. Exécutez "
            "migrations/001_init.sql dans Supabase."
        )
    if colonne != "Date":
        df = df.rename(columns={colonne: "Date"})

    df["Date_DT"] = dates.parser(df["Date"])
    return df.dropna(subset=["Date_DT"]).sort_values("Date_DT").reset_index(drop=True)


def inflation() -> pd.DataFrame:
    """Lit `pf2_inflation`, colonnes renommées en `Annee` / `Inflation`.

    DÉFAUT TROUVÉ À L'AUDIT — c'est LUI qui vidait la page Performance.

    La table stocke `annee` et `inflation` en minuscules (voyez
    migrations/001_init.sql). Toutes les pages, elles, lisent `Annee` et
    `Inflation`, en majuscules. `snapshots()` faisait déjà ce pont pour `date`
    -> `Date` ; `inflation()` ne le faisait pas.

    Conséquence : `session._inflation_par_annee` cherchait `Annee`, ne la
    trouvait pas, et retournait un dictionnaire VIDE — sans erreur, sans
    message. Le robot écrivait bien les chiffres en base ; l'application ne les
    voyait jamais. La page Performance affichait « ⚠️ non renseignée » pour
    chaque année, et la performance réelle était incalculable.

    Le défaut a survécu parce qu'il était SILENCIEUX. Une table vide et une
    table illisible produisaient exactement le même résultat. C'est pourquoi
    cette fonction lève maintenant une erreur explicite quand elle trouve des
    lignes qu'elle ne sait pas nommer.
    """
    df = lire(T_INFLATION)
    if df.empty:
        return df

    renommage = {}
    for attendu, candidats in (
        ("Annee", ("Annee", "annee")),
        ("Inflation", ("Inflation", "inflation")),
    ):
        colonne = next((c for c in candidats if c in df.columns), None)
        if colonne is None:
            raise ValueError(
                f"La table pf2_inflation n'a pas de colonne « {attendu} ». "
                f"Colonnes trouvées : {', '.join(map(str, df.columns))}. "
                "Exécutez migrations/001_init.sql dans Supabase."
            )
        if colonne != attendu:
            renommage[colonne] = attendu
    if renommage:
        df = df.rename(columns=renommage)
    return df


def ajouter_snapshot(ligne: dict) -> None:
    """Ajoute un snapshot daté, de façon idempotente.

    Utilise l'upsert sur la contrainte d'unicité de `date` : relancer le robot
    deux fois le même jour met à jour la ligne au lieu d'en créer une deuxième.
    """
    client().table(T_SNAPSHOTS).upsert(ligne, on_conflict="date").execute()


def ajouter_alerte(titre: str, message: str, niveau: str = "info") -> None:
    """Écrit une alerte dans `pf2_alertes`.

    CORRECTION : cette fonction écrivait `Date`, `Titre`, `Message`, `Niveau`
    avec une majuscule, alors que la table déclare `date`, `titre`, `message`,
    `niveau`. PostgreSQL replie les identifiants non quotés, donc ça passait
    souvent — mais c'est fragile, incohérent avec le reste du code, et ça
    cassait dès qu'une politique ou une vue intervenait.

    On écrit donc les noms exacts du schéma. Et on ne fournit plus `date` : la
    colonne est `timestamptz default now()`, l'horodatage appartient à la base,
    pas au client.
    """
    ecrire(T_ALERTES, [{
        "titre": titre,
        "message": message,
        "niveau": niveau,
    }])
