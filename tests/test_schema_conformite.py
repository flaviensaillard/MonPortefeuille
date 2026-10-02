"""Conformité du code au schéma SQL réel.

Pourquoi ce fichier existe
--------------------------
Trois défauts ont atteint l'utilisateur parce que rien ne rapprochait le code
du schéma de la base. Chacun coûte un aller-retour :

1. `existe()` interrogeait `select("id")` — trois tables ont une clé primaire
   composite et pas de colonne `id`.
2. `remplacer()` chaînait `.on_conflict(...)`, qui n'est pas une méthode.
3. **L'import écrivait une colonne `montant_net` qui n'existe pas** dans
   `pf2_transactions` (`PGRST204`). Puis on a découvert que
   `charger_transactions()` lisait les colonnes au format v1 (`Ticker`,
   `Quantité`...) alors que la table stocke du snake_case — le robot de snapshot
   aurait échoué au tour suivant.

Ces tests lisent `migrations/001_init.sql`, en extraient les colonnes réelles,
et vérifient que ce que le code écrit et ce qu'il lit correspondent. Aucun
accès réseau, aucune credential.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from _support import simuler_supabase
from core.portfolio import charger_transactions

RACINE = Path(__file__).resolve().parent.parent
SQL = (RACINE / "migrations" / "001_init.sql").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Lecture du schéma
# ---------------------------------------------------------------------------

def colonnes_par_table(sql: str = SQL) -> dict[str, set[str]]:
    """Extrait {table: colonnes} du fichier de migration.

    Volontairement conservateur : une ligne n'est retenue comme colonne que si
    elle commence par un identifiant suivi d'un type SQL. Tout ce qui est
    contrainte (`primary key`, `unique`, `check`, `constraint`, `create index`)
    est ignoré, ainsi que les commentaires.
    """
    tables: dict[str, set[str]] = {}
    for bloc in re.findall(
        r"create\s+table\s+(?:if\s+not\s+exists\s+)?(\w+)\s*\((.*?)\n\);",
        sql,
        re.S | re.I,
    ):
        nom, corps = bloc
        colonnes: set[str] = set()
        for ligne in corps.splitlines():
            ligne = ligne.split("--", 1)[0].strip().rstrip(",")
            if not ligne:
                continue
            if re.match(
                r"^(primary\s+key|unique|check|constraint|foreign\s+key|"
                r"exclude|like\s)\b",
                ligne,
                re.I,
            ):
                continue
            m = re.match(r"^([a-z_][a-z0-9_]*)\s+\S", ligne, re.I)
            if m:
                colonnes.add(m.group(1).lower())
        tables[nom.lower()] = colonnes
    return tables


SCHEMA = colonnes_par_table()


def test_le_sql_a_bien_ete_lu():
    """Garde-fou : si le parseur casse, les tests ci-dessous mentiraient."""
    assert len(SCHEMA) == 7, f"attendu 7 tables pf2_*, trouvé {sorted(SCHEMA)}"
    assert all(t.startswith("pf2_") for t in SCHEMA)
    # Une colonne sensible de chaque table, pour vérifier qu'on ne lit pas du vide.
    assert "ticker" in SCHEMA["pf2_transactions"]
    assert "montant_eur" in SCHEMA["pf2_apports"]
    assert "date" in SCHEMA["pf2_snapshots"]
    assert "taux" in SCHEMA["pf2_fx"]
    assert "cours" in SCHEMA["pf2_cours"]
    assert "annee" in SCHEMA["pf2_inflation"]


# ---------------------------------------------------------------------------
# Ce que le code écrit doit exister dans la table
# ---------------------------------------------------------------------------

@pytest.fixture
def captures_ecriture(monkeypatch):
    """Intercepte toute écriture et renvoie la liste des (table, lignes).

    `db.client()` est remplacé par un faux client qui traite le
    `delete().eq().execute()` de l'import des apports, mais lève si on tente
    une vraie lecture : ces tests ne doivent joindre aucun réseau.
    """
    import core.db as db

    ecritures: list[tuple[str, list[dict]]] = []

    class Rep:
        data = []

    class FauxDelete:
        def eq(self, *_a, **_k):
            return self

        def execute(self):
            return Rep()

    class FauxTable:
        def delete(self):
            return FauxDelete()

        def select(self, *_a, **_k):
            raise AssertionError("lecture réseau interdite dans ce test")

    class FauxClient:
        def table(self, _nom):
            return FauxTable()

    def faux_remplacer(table, lignes, on_conflict=None):
        ecritures.append((table, lignes))
        return Rep()

    def faux_ecrire(table, lignes):
        ecritures.append((table, lignes))
        return len(lignes)

    monkeypatch.setattr(db, "remplacer", faux_remplacer)
    monkeypatch.setattr(db, "ecrire", faux_ecrire)
    monkeypatch.setattr(db, "client", lambda: FauxClient())
    return ecritures


def _df_v1_transactions() -> pd.DataFrame:
    """Un lot de transactions au format de la table v1 `Transaction`."""
    return pd.DataFrame([
        {"id": 1, "Ticker": "asml", "Type": "Achat", "Date": "15/01/2024",
         "Quantité": 10, "Cours": 700, "Frais": 2, "Devise": "EUR",
         "Taux change (EUR)": 1.0},
        {"id": 2, "Ticker": "IGLN.L", "Type": "Achat", "Date": "03/03/2024",
         "Quantité": 4, "Cours": 118.5, "Frais": 0, "Devise": "USD",
         "Taux change (EUR)": 0.92},
    ])


def _df_v1_historique() -> pd.DataFrame:
    return pd.DataFrame([
        {"id": 7, "Date": "02/01/2024", "Type": "Ajout", "Montant €": 5000,
         "Montant Or": 4.1, "Montant $": 8600},
    ])


def test_import_transactions_n_ecrit_que_des_colonnes_existantes(captures_ecriture, monkeypatch):
    """Le bug n°3 : `montant_net` n'existe pas dans pf2_transactions."""
    import jobs.importer_v1 as imp

    monkeypatch.setattr(imp, "lire_v1", lambda t: _df_v1_transactions())
    imp.importer_transactions(dry_run=False)

    assert len(captures_ecriture) == 1
    table, lignes = captures_ecriture[0]
    assert table == imp.db.T_TRANSACTIONS
    autorisees = SCHEMA[table]
    for ligne in lignes:
        inconnues = set(ligne) - autorisees
        assert not inconnues, (
            f"colonnes absentes de {table} : {sorted(inconnues)} — "
            f"la migration les ignore, PostgREST renvoie PGRST204"
        )


def test_import_apports_n_ecrit_que_des_colonnes_existantes(captures_ecriture, monkeypatch):
    import jobs.importer_v1 as imp

    monkeypatch.setattr(imp, "lire_v1", lambda t: _df_v1_historique())
    imp.importer_apports(dry_run=False)

    assert len(captures_ecriture) == 1
    table, lignes = captures_ecriture[0]
    assert table == imp.db.T_APPORTS
    autorisees = SCHEMA[table]
    for ligne in lignes:
        inconnues = set(ligne) - autorisees
        assert not inconnues, f"colonnes absentes de {table} : {sorted(inconnues)}"


def test_import_transactions_ne_stocke_pas_montant_net(captures_ecriture, monkeypatch):
    """Explicite : `montant_net` est dérivé, il ne doit jamais être écrit."""
    import jobs.importer_v1 as imp

    monkeypatch.setattr(imp, "lire_v1", lambda t: _df_v1_transactions())
    imp.importer_transactions(dry_run=False)

    _, lignes = captures_ecriture[0]
    assert all("montant_net" not in l for l in lignes)


def test_import_remplit_les_colonnes_obligatoires(captures_ecriture, monkeypatch):
    """Une ligne sans ticker ou sans cours passerait le test précédent à vide."""
    import jobs.importer_v1 as imp

    monkeypatch.setattr(imp, "lire_v1", lambda t: _df_v1_transactions())
    imp.importer_transactions(dry_run=False)

    _, lignes = captures_ecriture[0]
    assert len(lignes) == 2
    for ligne in lignes:
        assert ligne["ticker"] in ("ASML", "IGLN.L")
        assert ligne["sens"] in ("achat", "vente")
        assert ligne["quantite"] > 0
        assert ligne["cours"] > 0
        assert ligne["devise"]
        assert ligne["source"] == "import_v1"
        assert ligne["reference"].startswith("v1:id")


# ---------------------------------------------------------------------------
# Ce que le code lit doit correspondre à ce que la table stocke
# ---------------------------------------------------------------------------

def test_lecture_des_lignes_v2_depuis_pf2_transactions():
    """Le bug n°3 (suite) : la table stocke du snake_case, le lecteur
    attendait les intitulés de la v1. Un aller-retour doit fonctionner."""
    from core.portfolio import charger_transactions

    df_v2 = pd.DataFrame([
        {"ticker": "ASML", "sens": "achat", "date": "2024-01-15",
         "quantite": 10, "cours": 700, "frais": 2, "devise": "EUR",
         "source": "import_v1", "reference": "v1:id1"},
        {"ticker": "IGLN.L", "sens": "achat", "date": "2024-03-03",
         "quantite": 4, "cours": 118.5, "frais": 0, "devise": "USD",
         "source": "import_v1", "reference": "v1:id2"},
    ])

    transactions = charger_transactions(df_v2)

    assert len(transactions) == 2
    assert transactions[0].ticker == "ASML"
    assert transactions[0].est_achat
    assert transactions[0].quantite == 10
    assert transactions[0].cours == 700
    assert transactions[0].devise == "EUR"
    # montant_net est recalculé, pas lu : 10 x 700 + 2 de frais.
    assert transactions[0].montant_net == pytest.approx(7002.0)


def test_lecture_des_lignes_v1_toujours_supportee():
    """Le CSV de la v1 doit continuer de passer : on a ajouté le support
    snake_case, pas remplacé l'ancien format."""
    from core.portfolio import charger_transactions

    df_v1 = pd.DataFrame([
        {"Ticker": "ASML", "Type": "Achat", "Date": "15/01/2024",
         "Quantité": 10, "Cours": 700, "Frais": 2, "Devise": "EUR"},
    ])

    transactions = charger_transactions(df_v1)
    assert len(transactions) == 1
    assert transactions[0].ticker == "ASML"
    assert transactions[0].montant_net == pytest.approx(7002.0)


def test_aller_retour_ecriture_puis_lecture(captures_ecriture, monkeypatch):
    """Le test qui compte : ce que l'import écrit doit se relire tel quel."""
    import jobs.importer_v1 as imp
    from core.portfolio import charger_transactions

    monkeypatch.setattr(imp, "lire_v1", lambda t: _df_v1_transactions())
    imp.importer_transactions(dry_run=False)

    _, lignes = captures_ecriture[0]
    relues = charger_transactions(pd.DataFrame(lignes))

    assert len(relues) == 2
    assert {t.ticker for t in relues} == {"ASML", "IGLN.L"}
    # Le PRU doit survivre à l'aller-retour, sinon la valorisation ment.
    asml = next(t for t in relues if t.ticker == "ASML")
    assert asml.cours == 700
    assert asml.montant_net == pytest.approx(7002.0)


def test_colonne_manquante_leve_une_erreur_claire():
    """Si le schéma dérive encore, l'erreur doit nommer la colonne."""
    from core.portfolio import charger_transactions

    df_casse = pd.DataFrame([{"ticker": "ASML", "sens": "achat"}])
    with pytest.raises(ValueError, match="Colonnes manquantes"):
        charger_transactions(df_casse)


# ---------------------------------------------------------------------------
# La devise : la saisie de l'utilisateur fait autorité
# ---------------------------------------------------------------------------
# `DEVISES_COTATION` ne connaît qu'une dizaine de tickers et renvoyait "USD"
# par défaut. Tout titre européen absent de la table était donc réétiqueté en
# USD, son cours en euro étant ensuite lu comme un cours en dollar. La
# valorisation de la ligne était fausse, et l'allocation avec elle.

def _importer_une_ligne(monkeypatch, ligne: dict):
    """Fait passer une seule ligne v1 dans l'import, et renvoie le résultat."""
    import jobs.importer_v1 as imp

    ecritures: list[tuple[str, list[dict]]] = []

    class Rep:
        data = []

    monkeypatch.setattr(imp.db, "remplacer", lambda t, l, on_conflict=None: (
        ecritures.append((t, l)), Rep())[1])
    monkeypatch.setattr(imp, "lire_v1", lambda t: pd.DataFrame([ligne]))
    simuler_supabase(monkeypatch, imp)

    nombre, corrections = imp.importer_transactions(dry_run=False)
    return nombre, corrections, ecritures


LIGNE_BASE = {
    "id": 1, "Ticker": "ASML", "Type": "Achat", "Date": "15/01/2024",
    "Quantité": 10, "Cours": 700, "Frais": 2, "Devise": "EUR",
    "Taux change (EUR)": 1.0,
}


def test_devise_saisie_est_conservee_meme_si_le_ticker_est_inconnu(monkeypatch):
    """Le bug : ASML est coté en EUR, la table ne le connaît pas et forc USD."""
    nombre, corrections, ecritures = _importer_une_ligne(monkeypatch, LIGNE_BASE)

    assert nombre == 1
    assert ecritures[0][1][0]["devise"] == "EUR"
    assert corrections == [], "aucune correction ne doit être signalée"


def test_devise_absente_repli_sur_la_table_de_cotation(monkeypatch):
    ligne = {**LIGNE_BASE, "Ticker": "IGLN.L", "Devise": None}
    nombre, corrections, ecritures = _importer_une_ligne(monkeypatch, ligne)

    assert nombre == 1
    assert ecritures[0][1][0]["devise"] == "USD"  # IGLN.L est coté à Londres
    assert corrections == []


def test_devise_absente_et_ticker_inconnu_ligne_ecartee(monkeypatch):
    """On n'invente pas une devise : la ligne est écartée et signalée."""
    ligne = {**LIGNE_BASE, "Ticker": "ZZZ.PA", "Devise": None}
    nombre, corrections, ecritures = _importer_une_ligne(monkeypatch, ligne)

    assert nombre == 0
    assert ecritures == []
    assert len(corrections) == 1
    assert "devise absente" in corrections[0]
    assert "NON importée" in corrections[0]
    assert "ZZZ.PA" in corrections[0]


def test_incoherence_reelle_signalee_sans_conversion(monkeypatch):
    """Cas XJSE.SW : saisi en USD, coté en JPY.

    On importe tel quel et on signale. Surtout on NE convertit PAS le cours :
    un taux inventé fausserait le PRU de toutes les lignes suivantes.
    """
    ligne = {**LIGNE_BASE, "Ticker": "XJSE.SW", "Devise": "USD", "Cours": 6.87138,
             "Quantité": 100}
    nombre, corrections, ecritures = _importer_une_ligne(monkeypatch, ligne)

    assert nombre == 1
    ligne_ecrite = ecritures[0][1][0]
    assert ligne_ecrite["devise"] == "USD"          # la saisie est respectée
    assert ligne_ecrite["cours"] == 6.87138         # et le cours n'est pas touché
    assert len(corrections) == 1
    assert "À VÉRIFIER" in corrections[0]


def test_devise_vide_est_traite_comme_absente(monkeypatch):
    """Une chaîne vide ne doit pas être prise pour une devise saisie."""
    ligne = {**LIGNE_BASE, "Ticker": "IGLN.L", "Devise": ""}
    nombre, corrections, ecritures = _importer_une_ligne(monkeypatch, ligne)

    assert nombre == 1
    assert ecritures[0][1][0]["devise"] == "USD"
    assert corrections == []


# ---------------------------------------------------------------------------
# Aucune devise ne doit être devinée
# ---------------------------------------------------------------------------
# `DEVISES_COTATION` ne connaît qu'une dizaine de tickers. Renvoyer "USD" par
# défaut faisait que tout titre européen absent de la table était réétiqueté en
# dollars, son cours en euro étant ensuite lu comme un cours en dollar.

def test_devise_cotation_de_renvoie_none_si_inconnu():
    """Un ticker inconnu ne doit PAS retourner "USD"."""
    from core.portfolio import devise_cotation_de

    assert devise_cotation_de("ASML") is None
    assert devise_cotation_de("MC.PA") is None
    assert devise_cotation_de("SAP.DE") is None
    assert devise_cotation_de("NIMPORTEQUOI") is None


def test_devise_cotation_de_renvoie_la_valeur_connue():
    from core.portfolio import devise_cotation_de

    assert devise_cotation_de("IGLN.L") == "USD"
    assert devise_cotation_de("XJSE.SW") == "JPY"
    assert devise_cotation_de("igln.l") == "USD"      # insensible à la casse
    assert devise_cotation_de(" ri.PA ") == "EUR"     # et aux espaces


def test_csv_sans_devise_et_ticker_inconnu_leve_une_erreur():
    """Une transaction sans devise ne peut pas être valorisée."""
    from core.portfolio import charger_transactions

    df = pd.DataFrame([
        {"Ticker": "ZZZ.PA", "Type": "Achat", "Date": "15/01/2024",
         "Quantité": 10, "Cours": 700, "Frais": 0, "Devise": ""},
    ])
    with pytest.raises(ValueError, match="devise absente"):
        charger_transactions(df)


def test_csv_sans_devise_mais_ticker_connu_passe():
    from core.portfolio import charger_transactions

    df = pd.DataFrame([
        {"Ticker": "IGLN.L", "Type": "Achat", "Date": "15/01/2024",
         "Quantité": 4, "Cours": 118.5, "Frais": 0, "Devise": ""},
    ])
    transactions = charger_transactions(df)
    assert transactions[0].devise == "USD"


def test_devise_de_yahoo_renvoie_none_sans_reseau(monkeypatch):
    """Si Yahoo est muet, on renvoie `None` — jamais une devise devinée."""
    import core.prices as prices

    def faux_ticker(_t):
        raise RuntimeError("pas de réseau")

    monkeypatch.setattr(prices.yf, "Ticker", faux_ticker)
    prices._cache_devise.clear()

    assert prices.devise_de("ASML") is None
    assert prices.devise_de("") is None
    prices._cache_devise.clear()


# ---------------------------------------------------------------------------
# La migration doit couvrir la sécurité de toutes les tables
# ---------------------------------------------------------------------------
# Le défaut qui a coûté le plus de tours : les tables `pf2_` ont été créées avec
# RLS actif et ma migration ne contenait AUCUNE politique. Les écritures étaient
# refusées (42501) alors que les lectures aboutissaient en renvoyant zéro ligne —
# donc `existe()` répondait « la table est là » et l'échec n'apparaissait qu'au
# premier INSERT.

def test_chaque_table_a_une_politique_rls():
    """Les 7 tables doivent avoir une politique, sinon l'écriture est refusée."""
    policies = set(re.findall(r"create policy \w+ on (\w+)", SQL))
    assert policies == set(SCHEMA), (
        f"tables sans politique : {sorted(set(SCHEMA) - policies)} — "
        f"l'écriture y sera refusée avec l'erreur 42501"
    )


def test_les_politiques_autorisent_l_ecriture():
    """Une politique en lecture seule ne suffit pas : il faut `with check`."""
    blocs = re.findall(
        r"create policy \w+ on (\w+)\s+for all\s+to ([^;]+?)\s+using \(true\)"
        r"\s+with check \(true\);",
        SQL,
        re.S,
    )
    trouvees = {t for t, _ in blocs}
    assert trouvees == set(SCHEMA), (
        f"politiques incomplètes : {sorted(set(SCHEMA) - trouvees)}"
    )
    for _table, roles in blocs:
        assert "anon" in roles and "authenticated" in roles


def test_rls_est_active_sur_chaque_table():
    activees = set(re.findall(r"alter table (\w+) enable row level security", SQL))
    assert activees == set(SCHEMA)


def test_le_fichier_002_existe_et_est_complet():
    """L'utilisateur doit pouvoir corler un seul fichier dans Supabase."""
    chemin = RACINE / "migrations" / "002_rls.sql"
    assert chemin.exists(), (
        "002_rls.sql manquant : sans lui, l'utilisateur devrait relancer toute "
        "la migration pour obtenir les politiques"
    )
    contenu = chemin.read_text(encoding="utf-8")
    assert "create policy" in contenu
    assert "drop policy if exists" in contenu   # idempotent
    assert len(re.findall(r"create policy", contenu)) == 7


def test_la_migration_principale_contient_aussi_les_politiques():
    """Une installation neuve ne doit pas dépendre de 002 pour être complète."""
    assert len(re.findall(r"create policy", SQL)) == 7


def test_les_tables_v1_ne_sont_pas_touchees():
    """Les politiques ne doivent porter que sur `pf2_`, pas sur les tables v1."""
    policies = set(re.findall(r"create policy \w+ on (\w+)", SQL))
    assert all(t.startswith("pf2_") for t in policies), policies


# ---------------------------------------------------------------------------
# Les clés écrites doivent correspondre EXACTEMENT au schéma
# ---------------------------------------------------------------------------
# `ajouter_alerte` écrivait `Date`, `Titre`, `Message`, `Niveau` là où la table
# déclare `date`, `titre`, `message`, `niveau`. PostgreSQL replie les
# identifiants non quotés, donc ça passait souvent — mais c'est fragile, et ça
# cassait dès qu'une vue ou une politique intervenait.

def test_ajouter_alerte_ecrit_les_colonnes_exactes(captures_ecriture, monkeypatch):
    from core.db import ajouter_alerte

    ajouter_alerte("Titre de test", "Message de test", niveau="attention")

    table, lignes = captures_ecriture[0]
    assert table == "pf2_alertes"
    ligne = lignes[0]
    assert set(ligne) <= SCHEMA[table], (
        f"colonnes inconnues : {sorted(set(ligne) - SCHEMA[table])}"
    )
    # Les trois champs métier, avec la casse du schéma.
    assert ligne["titre"] == "Titre de test"
    assert ligne["message"] == "Message de test"
    assert ligne["niveau"] == "attention"


def test_ajouter_alerte_ne_fournit_pas_la_date(captures_ecriture, monkeypatch):
    """`date` est `timestamptz default now()` : l'horodatage appartient à la base."""
    from core.db import ajouter_alerte

    ajouter_alerte("T", "M")
    _, lignes = captures_ecriture[0]
    assert "date" not in lignes[0]
    assert "Date" not in lignes[0]


def test_ajouter_alerte_niveau_par_defaut(captures_ecriture, monkeypatch):
    from core.db import ajouter_alerte

    ajouter_alerte("T", "M")
    _, lignes = captures_ecriture[0]
    assert lignes[0]["niveau"] == "info"


# ---------------------------------------------------------------------------
# La règle des frais, telle que la v1 l'écrit
# ---------------------------------------------------------------------------
class TestLeSigneDesFrais:
    """`Montant Net` de la v1 est : quantité × cours PLUS frais à l'achat,
    MOINS frais à la vente. Vérifié sur les 96 lignes réelles : le calcul tombe
    juste au centime près sur 96 lignes, et les 34 lignes qui « échouaient »
    étaient TOUTES des ventes, avec un écart d'exactement deux fois les frais.

    Le CSV de la v1 ne porte pas cette règle : elle se déduit des chiffres. Un
    import qui se tromperait de signe sur les ventes se tromperait de 2 000 $
    sur l'ensemble du journal — et personne ne le verrait, parce que les deux
    conventions donnent des montants plausibles.

    Deux pièges de plus, vérifiés le même jour :

    - la devise n'est PAS globale au fichier. `Cours`, `Frais` et `Montant Net`
      sont dans la devise de la ligne, celle de la colonne `Devise` juste à
      côté : 87 lignes en USD, et les 9 lignes XJSE.SW en JPY (cours ≈ 1 115,
      pas 6,87) ;
    - `Montant Or` de la table `Historique` donne le cours de l'or, ce qui
      confirme que `Montant $` est bien en dollars : 1 955,73 $/oz en juillet
      2023, 4 478,70 $/oz en septembre 2026.
    """

    def _tx(self, sens, qte, cours, frais, devise="USD"):
        return pd.DataFrame([{
            "Ticker": "IGLN.L", "Type": sens, "Date": "15/04/2025",
            "Quantité": qte, "Cours": cours, "Frais": frais, "Devise": devise,
        }])

    def test_les_frais_s_ajoutent_a_l_achat(self):
        tx = charger_transactions(self._tx("achat", 14, 62.6425, 5.85))
        assert tx[0].montant_net == pytest.approx(14 * 62.6425 + 5.85)

    def test_les_frais_se_deduisent_a_la_vente(self):
        """La ligne réelle : vente de 14 IGLN.L à 62,6425, frais 5,85,
        montant net 871,15 dans la table."""
        tx = charger_transactions(self._tx("vente", 14, 62.6425, 5.85))
        assert tx[0].montant_net == pytest.approx(871.15, abs=0.01)

    def test_le_signe_ne_depend_pas_de_la_casse(self):
        """La v1 mélange « vente » et « Vente »."""
        minuscule = charger_transactions(self._tx("vente", 14, 74.0, 12.40))
        majuscule = charger_transactions(self._tx("Vente", 14, 74.0, 12.40))
        assert minuscule[0].montant_net == pytest.approx(majuscule[0].montant_net)

    def test_aucune_course_aux_frais_manquants(self):
        """Une ligne sans frais ne doit pas se retrouver en négatif."""
        tx = charger_transactions(self._tx("vente", 10, 50.0, 0.0))
        assert tx[0].montant_net == pytest.approx(500.0)

    def test_la_devise_de_la_ligne_est_conservee(self):
        """Une ligne XJSE.SW en JPY le reste : c'est la colonne `Devise` qui
        commande, pas une supposition sur le titre."""
        tx = charger_transactions(self._tx("achat", 338, 1115.6, 1764.0, "JPY"))
        assert tx[0].devise == "JPY"
        assert tx[0].montant_net == pytest.approx(338 * 1115.6 + 1764.0)
