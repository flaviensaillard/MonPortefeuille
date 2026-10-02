"""Utilitaires partagés par les tests.

`importer_v1` purge les lignes issues de l'import avant de réécrire. Pourquoi :
la clé de conflit de l'upsert (`ticker,sens,date,quantite,cours`) contient la
DATE. Une réimportation après correction du parseur de dates ne peut donc pas
écraser les anciennes lignes — la clé diffère, et chaque transaction se
retrouverait en double, une fois à la mauvaise date.

Cette purge passe par `db.client()`, qui exige de vrais identifiants Supabase.
Les tests la neutralisent avec `simuler_supabase()`.
"""

from __future__ import annotations


class _Rep:
    data: list = []


def simuler_supabase(monkeypatch, module) -> None:
    """Remplace `module.db.client()` par une chaîne delete/eq/execute inopérante.

    À appeler dans tout test qui appelle `importer_transactions(dry_run=False)`.
    """
    def table(nom):
        def delete():
            def eq(*args):
                def execute():
                    return _Rep()
                return type("E", (), {"execute": staticmethod(execute)})()
            return type("D", (), {"eq": staticmethod(eq)})()
        return type("T", (), {"delete": staticmethod(delete)})()

    client = type("C", (), {"table": staticmethod(table)})()
    monkeypatch.setattr(module.db, "client", lambda: client)
