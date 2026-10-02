"""Dédoublonnage de l'import v1 — l'erreur PostgreSQL 21000.

Ces tests sont dans un fichier à part parce qu'ils portent sur un défaut de
données, pas sur une règle métier : la v1 contenait une transaction saisie deux
fois, et un upsert ne peut pas mettre à jour la même ligne cible deux fois dans
une seule commande.
"""

from __future__ import annotations

import pandas as pd

from _support import simuler_supabase
import pytest

from jobs.importer_v1 import _dedupliquer


# ---------------------------------------------------------------------------
# Dédoublonnage : l'erreur 21000
# ---------------------------------------------------------------------------
# `remplacer()` fait un upsert unique. Si le lot contient deux lignes de même
# (ticker, sens, date, quantite, cours), PostgreSQL doit mettre à jour la même
# ligne cible deux fois dans la même commande et refuse :
#
#     21000  ON CONFLICT DO UPDATE command cannot affect row a second time
#
# C'est ce qui arrive quand la v1 contient une transaction saisie deux fois.
# Tout l'import échoue — d'où la nécessité de dédoublonner AVANT l'upsert.

def _ligne_v2(**kw):
    base = {
        "ticker": "IGLN.L", "sens": "achat", "date": "2024-03-03",
        "quantite": 4, "cours": 118.5, "frais": 0, "devise": "USD",
        "source": "import_v1", "reference": "v1:id1",
    }
    base.update(kw)
    return base


def test_doublon_exact_est_neutralise():
    """Deux lignes identiques sauf la référence : on en garde une."""
    from jobs.importer_v1 import _dedupliquer

    lignes = [_ligne_v2(), _ligne_v2(reference="v1:id2")]
    resultat, messages = _dedupliquer(lignes)

    assert len(resultat) == 1
    assert len(messages) == 1
    assert "en double" in messages[0]
    assert "v1:id1" in messages[0] and "v1:id2" in messages[0]


def test_doublon_a_frais_differents_est_fusionne():
    """Même clé, frais différents : deux achats distincts, on les fusionne.

    Ce test encodait l'ancien comportement — garder la première ligne et signaler
    « DIVERGENTES ». C'est ce qui a fait perdre 178 unités de XJSE.SW en
    production : la clé de conflit ignore `frais`, donc deux achats du même jour
    au même cours mais avec des frais différents partageaient la clé, et le
    second était jeté.
    """
    from jobs.importer_v1 import _dedupliquer

    lignes = [_ligne_v2(), _ligne_v2(reference="v1:id2", frais=5)]
    resultat, messages = _dedupliquer(lignes, fusionner=True)

    assert len(resultat) == 1
    assert len(messages) == 1
    assert "Fusionnés" in messages[0]
    assert "frais différents" in messages[0]
    assert "À VÉRIFIER" in messages[0]
    # La quantité et les frais sont additionnés, pas perdus.
    assert resultat[0]["quantite"] == pytest.approx(_ligne_v2()["quantite"] * 2)
    assert resultat[0]["frais"] == pytest.approx(5)


def test_sans_fusionner_les_frais_divergents_restent_signales():
    """Sans `fusionner`, l'ancien comportement est conservé (cas des apports)."""
    from jobs.importer_v1 import _dedupliquer

    lignes = [_ligne_v2(), _ligne_v2(reference="v1:id2", frais=5)]
    resultat, messages = _dedupliquer(lignes)

    assert len(resultat) == 1
    assert len(messages) == 1
    assert "DIVERGENTES" in messages[0]
    assert "frais" in messages[0]


def test_doublon_sur_devise_est_signale():
    from jobs.importer_v1 import _dedupliquer

    lignes = [_ligne_v2(), _ligne_v2(reference="v1:id2", devise="EUR")]
    resultat, messages = _dedupliquer(lignes)

    assert len(resultat) == 1
    assert "devise" in messages[0]


def test_sans_doublon_rien_n_est_retire():
    from jobs.importer_v1 import _dedupliquer

    lignes = [
        _ligne_v2(reference="v1:id1"),
        _ligne_v2(reference="v1:id2", date="2024-04-01"),
        _ligne_v2(reference="v1:id3", ticker="XJSE.SW", devise="JPY"),
    ]
    resultat, messages = _dedupliquer(lignes)

    assert len(resultat) == 3
    assert messages == []


def test_trois_occurrences_ne_gardent_qu_une_ligne():
    from jobs.importer_v1 import _dedupliquer

    lignes = [_ligne_v2(reference=f"v1:id{i}") for i in range(3)]
    resultat, messages = _dedupliquer(lignes)

    assert len(resultat) == 1
    assert len(messages) == 2


def test_import_avec_doublon_n_envoie_qu_une_ligne(monkeypatch):
    """Le test qui compte : l'upsert ne doit jamais recevoir deux fois la même clé.

    C'est la reproduction exacte de l'erreur 21000 en production.
    """
    import jobs.importer_v1 as imp

    ecritures: list[tuple[str, list[dict]]] = []

    class Rep:
        data = []

    def faux_remplacer(table, lignes, on_conflict=None):
        ecritures.append((table, lignes))
        return Rep()

    # Deux saisies identiques dans la v1 : même ticker, date, quantité, cours.
    df = pd.DataFrame([
        {"id": 1, "Ticker": "IGLN.L", "Type": "Achat", "Date": "03/03/2024",
         "Quantité": 4, "Cours": 118.5, "Frais": 0, "Devise": "USD"},
        {"id": 2, "Ticker": "IGLN.L", "Type": "Achat", "Date": "03/03/2024",
         "Quantité": 4, "Cours": 118.5, "Frais": 0, "Devise": "USD"},
    ])

    monkeypatch.setattr(imp.db, "remplacer", faux_remplacer)
    monkeypatch.setattr(imp, "lire_v1", lambda t: df)
    simuler_supabase(monkeypatch, imp)

    nombre, corrections = imp.importer_transactions(dry_run=False)

    assert nombre == 1, "le doublon doit être neutralisé avant l'upsert"
    assert len(ecritures) == 1
    table, lignes = ecritures[0]
    assert table == imp.db.T_TRANSACTIONS
    assert len(lignes) == 1

    # Aucune clé de conflit ne doit apparaître deux fois dans le lot.
    cles = [tuple(l[c] for c in ("ticker", "sens", "date", "quantite", "cours"))
            for l in lignes]
    assert len(cles) == len(set(cles)), "doublon de clé de conflit dans le lot"
    assert any("en double" in c for c in corrections)


def test_les_doublons_sont_signales_meme_en_dry_run(monkeypatch):
    """Le dry-run doit révéler les doublons, pas seulement l'import réel."""
    import jobs.importer_v1 as imp

    df = pd.DataFrame([
        {"id": 1, "Ticker": "IGLN.L", "Type": "Achat", "Date": "03/03/2024",
         "Quantité": 4, "Cours": 118.5, "Frais": 0, "Devise": "USD"},
        {"id": 2, "Ticker": "IGLN.L", "Type": "Achat", "Date": "03/03/2024",
         "Quantité": 4, "Cours": 118.5, "Frais": 0, "Devise": "USD"},
    ])
    monkeypatch.setattr(imp, "lire_v1", lambda t: df)

    nombre, corrections = imp.importer_transactions(dry_run=True)
    assert nombre == 1
    assert any("en double" in c for c in corrections)


def test_dedup_des_apports_avec_une_cle_differente():
    """`pf2_apports` n'a aucun index unique : on fixe nous-mêmes la clé."""
    from jobs.importer_v1 import _dedupliquer

    lignes = [
        {"date": "2024-01-02", "sens": "apport", "montant_eur": 5000,
         "compte": "import_v1", "reference": "v1:id7"},
        {"date": "2024-01-02", "sens": "apport", "montant_eur": 5000,
         "compte": "import_v1", "reference": "v1:id8"},
    ]
    resultat, messages = _dedupliquer(
        lignes, cle=("date", "sens", "montant_eur", "compte")
    )

    assert len(resultat) == 1
    assert len(messages) == 1
    assert "en double" in messages[0]


def test_apports_distincts_ne_sont_pas_fusionnes():
    """Deux apports le même jour mais de montants différents : ce n'est pas un doublon."""
    from jobs.importer_v1 import _dedupliquer

    lignes = [
        {"date": "2024-01-02", "sens": "apport", "montant_eur": 5000,
         "compte": "import_v1", "reference": "v1:id7"},
        {"date": "2024-01-02", "sens": "apport", "montant_eur": 3000,
         "compte": "import_v1", "reference": "v1:id8"},
    ]
    resultat, messages = _dedupliquer(
        lignes, cle=("date", "sens", "montant_eur", "compte")
    )

    assert len(resultat) == 2
    assert messages == []


def test_apport_et_retrait_le_meme_jour_ne_sont_pas_fusionnes():
    """Un apport et un retrait le même jour sont deux opérations, pas un doublon."""
    from jobs.importer_v1 import _dedupliquer

    lignes = [
        {"date": "2024-01-02", "sens": "apport", "montant_eur": 5000,
         "compte": "import_v1", "reference": "v1:id7"},
        {"date": "2024-01-02", "sens": "retrait", "montant_eur": 5000,
         "compte": "import_v1", "reference": "v1:id8"},
    ]
    resultat, messages = _dedupliquer(
        lignes, cle=("date", "sens", "montant_eur", "compte")
    )

    assert len(resultat) == 2
    assert messages == []


def test_import_apports_avec_doublon_n_insere_qu_une_ligne(monkeypatch):
    import jobs.importer_v1 as imp

    ecritures: list[tuple[str, list[dict]]] = []

    class Rep:
        data = []

    monkeypatch.setattr(imp.db, "ecrire",
                        lambda t, l: (ecritures.append((t, l)), len(l))[1])
    monkeypatch.setattr(
        imp.db, "client",
        lambda: type("C", (), {"table": staticmethod(
            lambda n: type("T", (), {
                "delete": staticmethod(lambda: type("D", (), {
                    "eq": staticmethod(lambda *a: type("E", (), {
                        "execute": staticmethod(lambda: Rep())})()),
                })()),
            })())})(),
    )

    df = pd.DataFrame([
        {"id": 7, "Date": "02/01/2024", "Type": "Ajout", "Montant €": 5000,
         "Montant Or": 4.1, "Montant $": 8600},
        {"id": 8, "Date": "02/01/2024", "Type": "Ajout", "Montant €": 5000,
         "Montant Or": 4.1, "Montant $": 8600},
    ])
    monkeypatch.setattr(imp, "lire_v1", lambda t: df)

    nombre = imp.importer_apports(dry_run=False)

    assert nombre == 1, "le doublon d'apport doit être neutralisé"
    assert len(ecritures) == 1
    assert len(ecritures[0][1]) == 1


# ---------------------------------------------------------------------------
# Purge avant réécriture — les dates corrigées ne doivent pas créer de doublons
# ---------------------------------------------------------------------------
def test_reimport_purge_avant_decrire(monkeypatch):
    """La clé de conflit de l'upsert contient la DATE.

    L'importation précédente a stocké des dates mal lues — le parseur d'alors
    intervertissait jour et mois sur les ISO (`2025-07-01` devenait
    `2025-01-07`), ce qui a décalé 52 % des lignes. Relancer l'import avec le
    parseur corrigé ne peut donc PAS écraser les anciennes : la clé diffère, et
    chaque transaction se retrouverait en double, une fois à la mauvaise date.

    D'où la purge préalable, qui doit passer AVANT l'écriture.
    """
    import jobs.importer_v1 as imp

    journal: list[str] = []
    filtres: list[tuple] = []

    class Rep:
        data = []

    class FauxDelete:
        def eq(self, colonne, valeur):
            filtres.append((colonne, valeur))
            journal.append("purger")

            class E:
                def execute(self):
                    return Rep()

            return E()

    class FauxTable:
        def delete(self):
            journal.append("delete")
            return FauxDelete()

    class FauxClient:
        def table(self, nom):
            return FauxTable()

    def faux_remplacer(table, lignes, on_conflict=None):
        journal.append("ecrire")
        return Rep()

    monkeypatch.setattr(imp.db, "remplacer", faux_remplacer)
    monkeypatch.setattr(imp.db, "client", lambda: FauxClient())

    df = pd.DataFrame([
        {"id": 1, "Ticker": "FLXC.L", "Type": "Vente", "Date": "01/07/2025",
         "Quantité": 22, "Cours": 29.04, "Frais": 6.81, "Devise": "USD"},
    ])
    monkeypatch.setattr(imp, "lire_v1", lambda t: df)

    imp.importer_transactions(dry_run=False)

    assert journal[0] == "delete", "la purge doit être tentée avant l'écriture"
    assert journal.index("purger") < journal.index("ecrire"), journal
    # Seules les lignes écrites par l'import sont purgées : une transaction
    # saisie à la main dans l'application doit survivre.
    assert filtres == [("source", "import_v1")], filtres


def test_la_purge_n_est_pas_faite_en_dry_run(monkeypatch):
    """Un dry-run ne doit rien toucher à la base."""
    import jobs.importer_v1 as imp

    journal: list[str] = []

    class Rep:
        data = []

    class FauxDelete:
        def eq(self, colonne, valeur):
            journal.append("purger")

            class E:
                def execute(self):
                    return Rep()

            return E()

    class FauxTable:
        def delete(self):
            return FauxDelete()

    class FauxClient:
        def table(self, nom):
            return FauxTable()

    monkeypatch.setattr(imp.db, "client", lambda: FauxClient())
    df = pd.DataFrame([
        {"id": 1, "Ticker": "FLXC.L", "Type": "Vente", "Date": "01/07/2025",
         "Quantité": 22, "Cours": 29.04, "Frais": 6.81, "Devise": "USD"},
    ])
    monkeypatch.setattr(imp, "lire_v1", lambda t: df)

    imp.importer_transactions(dry_run=True)

    assert "purger" not in journal, "un dry-run ne doit rien supprimer"


# ---------------------------------------------------------------------------
# Fusion plutôt que perte — deux achats le même jour au même cours
# ---------------------------------------------------------------------------
def _achat(ticker, jour, quantite, cours, frais, ref):
    return {"ticker": ticker, "sens": "achat", "date": jour, "quantite": quantite,
            "cours": cours, "frais": frais, "devise": "JPY",
            "source": "import_v1", "reference": ref}


def test_deux_achats_meme_jour_meme_cours_sont_fusionnes():
    """Le cas réel : 89 unités à 1 115,60 avec 148 € de frais, puis 89 unités au
    même cours avec 1 149 € de frais. La clé de conflit ne voit pas la différence
    (elle ignore `frais`), donc l'ancien code jetait la seconde ligne — et 89
    unités disparaissaient en silence.
    """
    lignes = [
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 148, "v1:id67"),
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 1149, "v1:id68"),
    ]
    garde, messages = _dedupliquer(lignes, fusionner=True)

    assert len(garde) == 1
    assert garde[0]["quantite"] == pytest.approx(178.0)
    assert garde[0]["frais"] == pytest.approx(1297.0)
    assert len(messages) == 1
    assert "Fusionnés" in messages[0]


def test_la_fusion_ne_change_pas_le_pru():
    """C'est le cœur de l'argument : fusionner est exact, pas approximatif.

    Deux achats de 89 unités à 1 115,60 (frais 148 et 1 149) donnent exactement
    le même PRU qu'un achat de 178 unités avec 1 297 € de frais.
    """
    separes = [
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 148, "v1:id67"),
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 1149, "v1:id68"),
    ]
    garde, _ = _dedupliquer(separes, fusionner=True)

    cout_separe = sum(l["quantite"] * l["cours"] + l["frais"] for l in separes)
    cout_fusion = garde[0]["quantite"] * garde[0]["cours"] + garde[0]["frais"]
    assert cout_fusion == pytest.approx(cout_separe)

    pru_separe = cout_separe / sum(l["quantite"] for l in separes)
    pru_fusion = cout_fusion / garde[0]["quantite"]
    assert pru_fusion == pytest.approx(pru_separe)


def test_les_deux_references_sont_conservees():
    lignes = [
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 148, "v1:id67"),
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 1149, "v1:id68"),
    ]
    garde, _ = _dedupliquer(lignes, fusionner=True)
    assert "v1:id67" in garde[0]["reference"]
    assert "v1:id68" in garde[0]["reference"]


def test_trois_achats_le_meme_jour_sont_tous_fusionnes():
    lignes = [
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 148, "v1:id67"),
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 1149, "v1:id68"),
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 500, "v1:id99"),
    ]
    garde, messages = _dedupliquer(lignes, fusionner=True)
    assert len(garde) == 1
    assert garde[0]["quantite"] == pytest.approx(267.0)
    assert garde[0]["frais"] == pytest.approx(1797.0)
    assert len(messages) == 2


def test_des_cours_differents_ne_sont_pas_fusionnes():
    """Deux cours différents = deux lignes distinctes, la clé de conflit les
    sépare déjà. Rien à fusionner."""
    lignes = [
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 148, "v1:id67"),
        _achat("XJSE.SW", "2026-02-02", 89, 1116.25, 548, "v1:id70"),
    ]
    garde, messages = _dedupliquer(lignes, fusionner=True)
    assert len(garde) == 2
    assert messages == []


def test_devise_divergente_reste_signalee_sans_fusion():
    """Deux devises sur le même titre, même jour : là on ne choisit pas."""
    lignes = [
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 148, "v1:id67"),
        {**_achat("XJSE.SW", "2026-02-02", 89, 1115.6, 1149, "v1:id68"),
         "devise": "USD"},
    ]
    garde, messages = _dedupliquer(lignes, fusionner=True)
    assert len(garde) == 1
    assert garde[0]["quantite"] == pytest.approx(89.0), "pas de fusion sur devise"
    assert "DIVERGENTES" in messages[0]


def test_sans_fusionner_le_comportement_est_inchange():
    """Les apports gardent l'ancien comportement : les additionner gonflerait
    les apports de capital."""
    lignes = [
        {"date": "2024-01-02", "sens": "Ajout", "montant_eur": 5000,
         "compte": "import_v1", "reference": "v1:id7"},
        {"date": "2024-01-02", "sens": "Ajout", "montant_eur": 5000,
         "compte": "import_v1", "reference": "v1:id8"},
    ]
    garde, messages = _dedupliquer(
        lignes, cle=("date", "sens", "montant_eur", "compte"), fusionner=False
    )
    assert len(garde) == 1
    assert "en double" in messages[0]


def test_la_fonction_ne_mute_pas_son_entree():
    """`lignes` appartient à l'appelant : le dédoublonnage ne doit pas le modifier.

    Un bug de la première version du correctif mutait les dictionnaires d'origine,
    ce qui faussait tout calcul fait ensuite sur les données non dédoublonnées.
    """
    lignes = [
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 148, "v1:id67"),
        _achat("XJSE.SW", "2026-02-02", 89, 1115.6, 1149, "v1:id68"),
    ]
    avant = [dict(l) for l in lignes]
    _dedupliquer(lignes, fusionner=True)
    assert lignes == avant, "l'entrée a été modifiée"


def test_frais_identiques_restent_une_double_saisie_signalee():
    """Deux lignes rigoureusement identiques : l'ambiguïté reste entière.

    C'est le cas IGLN.L déjà examiné — une double saisie est possible, et décider
    à votre place serait inventer une donnée. On garde une ligne et on le dit.
    """
    lignes = [
        _achat("IGLN.L", "2024-03-03", 4, 118.5, 0, "v1:id41"),
        _achat("IGLN.L", "2024-03-03", 4, 118.5, 0, "v1:id42"),
    ]
    garde, messages = _dedupliquer(lignes, fusionner=True)
    assert len(garde) == 1
    assert garde[0]["quantite"] == pytest.approx(4.0), "pas de fusion si les frais sont égaux"
    assert len(messages) == 1
    assert "en double" in messages[0]
