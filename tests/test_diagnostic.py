"""Le diagnostic : un outil qui se trompe en silence ne diagnostique rien.

Ces tests portent sur les défauts trouvés DANS le diagnostic lui-même, en le
faisant tourner sur une base simulée aux dimensions réelles.

1. **Il comptait au lieu de montrer.** La version 2 annonçait « 42 lignes d'un
   autre type » dans la table v1 `Historique` et concluait « aucune valuation » —
   sans jamais afficher une seule de ces 42 lignes. Un contrôle qui compte sans
   montrer transforme une ignorance en chiffre, et le chiffre a l'air d'un fait.

2. **Il lisait les dates ISO à l'envers.** `pd.to_datetime(colonne,
   dayfirst=True)` sur `2026-10-01` ne lève pas : il rend le **10 janvier**.
   Les tables de la v1 sont en `jj/mm/aaaa` et veulent `dayfirst` ; celles de la
   v2 sont en ISO et le refusent. Le diagnostic appliquait le même traitement
   aux deux.

   C'est précisément le défaut que `core/dates.py` documente longuement — et je
   l'avais réintroduit dans l'outil censé le détecter.

3. **Il comparait des `Type` par égalité là où l'import compare par
   sous-chaîne.** D'où « 1 mouvement » annoncé quand l'import en trouvait 38.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jobs import diagnostic


class TestEtendueDesDates:
    def test_iso_ne_se_lit_pas_a_l_envers(self):
        """DÉFAUT CORRIGÉ. `2026-10-01` devenait le 10 janvier.

        Le test porte sur les deux bornes : un seul des deux sens aurait pu
        passer par chance.
        """
        df = pd.DataFrame({"date": ["2026-10-01", "2026-01-15"]})
        assert diagnostic._date_range(df) == "2026-01-15 → 2026-10-01"

    def test_iso_au_premier_du_mois(self):
        """Le jour « 01 » est le cas qui casse : il devient le mois."""
        df = pd.DataFrame({"date": ["2026-08-01"]})
        assert diagnostic._date_range(df) == "2026-08-01 → 2026-08-01"

    def test_format_v1_jour_puis_mois(self):
        """L'écriture de la v1 est `jj/mm/aaaa` et doit rester lue ainsi."""
        df = pd.DataFrame({"Date": ["01/10/2026", "15/01/2026"]})
        assert diagnostic._date_range(df) == "2026-01-15 → 2026-10-01"

    def test_les_deux_formats_donnent_la_meme_journee(self):
        """`01/07/2025` et `2025-07-01` sont le 1er juillet, pas le 7 janvier."""
        iso = pd.DataFrame({"date": ["2025-07-01"]})
        v1 = pd.DataFrame({"Date": ["01/07/2025"]})
        assert diagnostic._date_range(iso) == diagnostic._date_range(v1)

    def test_date_avec_heure(self):
        """La v1 écrit parfois une date AVEC son heure."""
        df = pd.DataFrame({"Date": ["11/05/2026 12:38:33"]})
        assert "2026-05-11" in diagnostic._date_range(df)

    def test_colonne_absente(self):
        assert diagnostic._date_range(pd.DataFrame({"x": [1]})) == "dates illisibles"

    def test_dates_illisibles(self):
        assert diagnostic._date_range(pd.DataFrame({"date": ["pas une date"]})) == "dates illisibles"


class TestAffichage:
    """`_montre` : la fonction qui manquait à la version 2."""

    def test_affiche_les_lignes_au_lieu_de_les_compter(self, caplog):
        df = pd.DataFrame([
            {"Date": "01/08/2026", "Type": "Valorisation", "Montant €": 47380.0},
        ])
        with caplog.at_level("INFO"):
            diagnostic._montre(df)
        texte = caplog.text
        assert "Valorisation" in texte, "la valeur de la colonne Type n'apparaît pas"
        assert "47380" in texte, "le montant n'apparaît pas"

    def test_table_vide_ne_plante_pas(self, caplog):
        with caplog.at_level("INFO"):
            diagnostic._montre(pd.DataFrame())
        assert "aucune ligne" in caplog.text

    def test_accepte_un_dataframe_rendu_par_le_client(self, caplog):
        """Le client peut rendre un DataFrame : `data or []` levait
        « truth value of a DataFrame is ambiguous »."""
        with caplog.at_level("INFO"):
            diagnostic._montre(pd.DataFrame([{"a": 1, "b": "x"}]))
        assert "a" in caplog.text


class TestFormatage:
    def test_none_et_nan_deviennent_un_point(self):
        assert diagnostic._fmt(None) == "·"
        assert diagnostic._fmt(float("nan")) == "·"

    def test_entier_sans_decimale(self):
        assert diagnostic._fmt(15000.0) == "15000"

    def test_decimal_garde_ses_chiffres(self):
        assert "43321.84" in diagnostic._fmt(43321.84).replace("\u202f", "").replace(" ", "")

    def test_texte_passe_tel_quel(self):
        assert diagnostic._fmt("Valorisation") == "Valorisation"


class TestDetectionDesTypes:
    """L'import compare par sous-chaîne ; la v2 du diagnostic comparait par
    égalité. Deux mesures, deux vérités — la mauvaise était affichée."""

    @pytest.mark.parametrize("valeur", ["Ajout", "Ajout mensuel", "apport", "Apport initial"])
    def test_les_apports_par_sous_chaine(self, valeur):
        serie = pd.Series([valeur]).fillna("").astype(str).str.lower()
        assert serie.str.contains("ajout|apport", regex=True).iloc[0]

    @pytest.mark.parametrize("valeur", ["Retrait", "retrait mensuel"])
    def test_les_retraits_par_sous_chaine(self, valeur):
        serie = pd.Series([valeur]).fillna("").astype(str).str.lower()
        assert serie.str.contains("retrait", regex=True).iloc[0]

    @pytest.mark.parametrize("valeur", ["Valorisation", "Total", ""])
    def test_ni_apport_ni_retrait(self, valeur):
        serie = pd.Series([valeur]).fillna("").astype(str).str.lower()
        assert not serie.str.contains("ajout|apport|retrait", regex=True).iloc[0]


class TestSautsSansFlux:
    """La cause du +26,2 % de 2026.

    Entre le 01/02 et le 02/02/2026, la valeur passe de 55 639,93 à
    64 808,82 € — soit +9 168,89 €. Un apport de 200 € a bien été saisi ce
    jour-là : le saut brut fait donc +16,48 %, et ce qui reste sans
    explication est de **8 968,89 €** (+16,12 %). C'est ce chiffre-là que le
    TWR a compté comme du rendement, et c'est celui qu'il faut corriger."""

    # Les dates et les valeurs réelles, telles que le run du 01/10/2026 les a
    # sorties. Le saut se lit entre le 01/02 et le 02/02.
    REEL = [("2026-01-29", 55100.0), ("2026-01-30", 55300.0),
            ("2026-02-01", 55639.93), ("2026-02-02", 64808.82),
            ("2026-02-03", 64900.0)]

    def _snaps(self, paires):
        return pd.DataFrame({
            "date": [d for d, _ in paires],
            "patrimoine_investi_eur": [v for _, v in paires],
        })

    def _apports(self, date, montant):
        return pd.DataFrame([{"date": date, "sens": "apport", "montant_eur": montant}])

    def test_le_saut_reel_est_trouve(self, caplog):
        snaps = self._snaps(self.REEL)
        with caplog.at_level("INFO"):
            diagnostic.examiner_sauts(snaps, self._apports("2026-02-02", 200.0))
        assert "2026-02-02" in caplog.text
        # 9 168,89 € de saut brut, moins les 200 € d'apport saisi du jour.
        assert "8 968.89" in caplog.text
        assert "Total à déclarer comme apports : 8 968.89" in caplog.text
        

    def test_la_date_est_celle_du_jour_ou_la_valeur_change(self, caplog):
        """Le 01/02 la valeur ne saute pas : le saut est daté du 02/02, le jour
        où elle passe de 55 639,93 à 64 808,82."""
        snaps = self._snaps(self.REEL)
        with caplog.at_level("INFO"):
            diagnostic.examiner_sauts(snaps, self._apports("2026-02-02", 200.0))
        ligne = [l for l in caplog.text.splitlines() if "55 639.93" in l][0]
        assert "2026-02-02" in ligne
        assert "200" in ligne

    def test_le_verdict_nomme_la_date_et_le_montant(self):
        snaps = self._snaps(self.REEL)
        diagnostic.verdicts.clear()
        diagnostic.examiner_sauts(snaps, self._apports("2026-02-02", 200.0))
        assert len(diagnostic.verdicts) == 1
        assert "2026-02-02" in diagnostic.verdicts[0]
        assert "8,968.89" in diagnostic.verdicts[0]
        diagnostic.verdicts.clear()

    def test_aucune_date_ecrite_en_dur_dans_la_phrase(self):
        """La phrase disait « Le 02/02/2026 » quoi qu'il arrive : une date
        figée survit au correctif et désigne un jour qui n'est plus fautif."""
        snaps = self._snaps([("2026-03-01", 50000.0), ("2026-03-02", 50100.0),
                             ("2026-03-03", 60000.0), ("2026-03-04", 60100.0)])
        diagnostic.verdicts.clear()
        diagnostic.examiner_sauts(snaps, pd.DataFrame())
        assert "2026-03-03" in diagnostic.verdicts[0]
        assert "2026-02-02" not in diagnostic.verdicts[0]
        diagnostic.verdicts.clear()

    def test_un_apport_du_montant_du_saut_ne_declenche_rien(self, caplog):
        """Si le versement est saisi le jour même, il n'y a pas de saut."""
        snaps = self._snaps(self.REEL)
        with caplog.at_level("INFO"):
            diagnostic.examiner_sauts(snaps, self._apports("2026-02-02", 9168.89))
        assert "Aucun saut" in caplog.text

    def test_une_baisse_de_marche_ordinaire_ne_declenche_rien(self, caplog):
        snaps = self._snaps([("2025-04-03", 58000.0), ("2025-04-04", 54000.0),
                             ("2025-04-05", 53500.0), ("2025-04-06", 53100.0)])
        with caplog.at_level("INFO"):
            diagnostic.examiner_sauts(snaps, pd.DataFrame())
        assert "Aucun saut" in caplog.text

    def test_le_krach_du_4_avril_2025_n_est_pas_un_saut(self):
        """−7,4 % en une séance : c'est le krach tarifaire, un vrai mouvement de
        marché. Le seuil est à 8 % pour ne pas le confondre avec un défaut."""
        snaps = self._snaps([("2025-04-03", 60100.0), ("2025-04-04", 55680.0)])
        sauts = diagnostic.metrics.sauts_non_expliques(
            list(snaps["date"]),
            list(snaps["patrimoine_investi_eur"]),
            [0.0, 0.0],
        )
        assert sauts == []

    def test_sans_apports_du_tout(self, caplog):
        """Aucun flux : le saut est signalé (ici le 03/03)."""
        snaps = self._snaps([("2026-03-01", 50000.0), ("2026-03-02", 50100.0),
                             ("2026-03-03", 60000.0)])
        with caplog.at_level("INFO"):
            diagnostic.examiner_sauts(snaps, pd.DataFrame())
        assert "2026-03-03" in caplog.text

    def test_snapshots_vides(self, caplog):
        with caplog.at_level("INFO"):
            diagnostic.examiner_sauts(pd.DataFrame(), pd.DataFrame())
        assert "Pas de quoi" in caplog.text


class TestReconciliationDesApports:
    """La v1 et la v2 doivent compter les mêmes apports. Même règle des deux
    côtés : sous-chaîne, jamais l'égalité."""

    def _hist(self, types, montants):
        return pd.DataFrame({
            "Date": ["01/01/2025"] * len(types),
            "Type": types,
            "Montant €": montants,
        })

    def test_les_deux_se_rejoignent(self, caplog):
        hist = self._hist(["Ajout fonds propres", "invest. prog."], [15000.0, 50.0])
        apports = pd.DataFrame([{"date": "2025-01-01", "sens": "apport", "montant_eur": 15000.0}])
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_apports(hist, apports)
        assert "Rien à corriger" in caplog.text

    def test_les_invest_prog_ne_sont_comptees_ni_d_un_cote_ni_de_l_autre(self, caplog):
        """5 lignes 'invest. prog.' à 50 € : ni apports, ni retraits. La v1 ne
        les comptait pas, la v2 ne doit pas les compter."""
        hist = self._hist(["invest. prog."] * 5, [50.0] * 5)
        apports = pd.DataFrame(columns=["date", "sens", "montant_eur"])
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_apports(hist, apports)
        bloc = caplog.text.split("V1 — Historique")[-1].split("V2")[0]
        assert "apports" in bloc and " 0 €  (0 lignes)" in bloc
        assert "retraits" in bloc and " 0 €  (0 lignes)" in bloc

    def test_il_manque_des_apports_dans_la_v2(self, caplog):
        hist = self._hist(["Ajout fonds propres"], [15000.0])
        apports = pd.DataFrame([{"date": "2025-01-01", "sens": "apport", "montant_eur": 5000.0}])
        diagnostic.verdicts.clear()
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_apports(hist, apports)
        assert "10 000" in caplog.text or "10000" in caplog.text
        assert any("manque" in v for v in diagnostic.verdicts)
        diagnostic.verdicts.clear()

    def test_le_retrait_entre_en_negatif(self, caplog):
        hist = self._hist(["Ajout fonds propres", "Retrait"], [15000.0, 2000.0])
        apports = pd.DataFrame([
            {"date": "2025-01-01", "sens": "apport", "montant_eur": 15000.0},
            {"date": "2025-01-01", "sens": "retrait", "montant_eur": 2000.0},
        ])
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_apports(hist, apports)
        assert "13 000" in caplog.text or "13000" in caplog.text
        assert "Rien à corriger" in caplog.text

    def test_la_v2_en_trop(self, caplog):
        hist = self._hist(["Ajout fonds propres"], [15000.0])
        apports = pd.DataFrame([{"date": "2025-01-01", "sens": "apport", "montant_eur": 20000.0}])
        diagnostic.verdicts.clear()
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_apports(hist, apports)
        assert any("doublons" in v for v in diagnostic.verdicts)
        diagnostic.verdicts.clear()

    def test_historique_illisible(self, caplog):
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_apports(pd.DataFrame(), pd.DataFrame())
        assert "illisible" in caplog.text


class TestRequeteSql:
    def test_la_requete_est_donnee_a_copier(self, caplog):
        with caplog.at_level("INFO"):
            diagnostic.requete_sql()
        assert "information_schema.tables" in caplog.text
        assert "Table Editor" in caplog.text


class TestReconciliationDuCapital:
    """`Projections.Capital investi` est le cumul que la v1 tenait elle-même,
    en dollars. C'est un juge indépendant de chaque ligne du journal.

    Sur les données réelles, ce contrôle trouve UNE période sur trois ans et
    demi : du 30/03 au 30/04/2024, 11 848 $ d'apports sont enregistrés et le
    capital RECULE de 500 $. Les autres gros versements le font bouger — +8 889 $
    en mai 2024 pour 9 161 $, +11 019 $ en juillet 2024 pour 11 554 $.
    """

    def _hist(self, lignes):
        """`lignes` : (date jj/mm/aaaa, type, montant $)."""
        return pd.DataFrame([
            {"Date": d, "Type": t, "Montant $": m, "Montant €": m / 1.08,
             "Montant Or": m / 2000.0}
            for d, t, m in lignes
        ])

    def _proj(self, lignes):
        """`lignes` : (date jj/mm/aaaa, capital investi en $)."""
        return pd.DataFrame([
            {"Date": d, "Capital investi": c, "Actifs Stratégiques": c * 1.3,
             "Total Global": c * 1.7}
            for d, c in lignes
        ])

    def _brancher(self, monkeypatch, hist, proj):
        diagnostic.connexion_ok = True
        monkeypatch.setattr(
            diagnostic, "lire_v1",
            lambda t: hist if t == diagnostic.V1_HISTORIQUE else proj,
        )

    def test_le_trou_d_avril_2024_est_trouve(self, monkeypatch, caplog):
        hist = self._hist([
            ("29/02/2024", "Ajout fonds propres", 260.0),
            ("11/04/2024", "Ajout fonds propres", 268.24),
            ("29/04/2024", "Ajout fonds propres", 11579.85),
            ("30/04/2024", "Ajout fonds propres", 200.0),
        ])
        proj = self._proj([
            ("30/01/2024", 28184.0), ("28/02/2024", 27861.0),
            ("30/03/2024", 29086.0), ("30/04/2024", 28586.0),
        ])
        self._brancher(monkeypatch, hist, proj)
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_capital(hist)

        assert "2024-04-30" in caplog.text
        assert "2024-03-30" in caplog.text
        # 268,24 + 11 579,85 + 200,00 = 12 048,09 $ d'apports en avril 2024,
        # pour un capital qui recule de 500 $.
        assert "12 048" in caplog.text
        assert "-500" in caplog.text

    def test_un_journal_qui_suit_le_capital_est_muet(self, monkeypatch, caplog):
        """Chaque apport bouge le capital d'autant : rien à signaler."""
        hist = self._hist([
            ("11/04/2024", "Ajout fonds propres", 268.24),
            ("02/05/2024", "Ajout fonds propres", 8676.79),
            ("23/07/2024", "Ajout fonds propres", 11336.23),
        ])
        proj = self._proj([
            ("30/03/2024", 29000.0), ("30/04/2024", 29268.24),
            ("30/05/2024", 37945.03), ("30/07/2024", 49281.26),
        ])
        self._brancher(monkeypatch, hist, proj)
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_capital(hist)
        assert "Aucun écart" in caplog.text

    def test_les_dates_jj_mm_aaaa_sont_lues_dans_le_bon_ordre(self, monkeypatch, caplog):
        """REGRESSION. `pd.to_datetime` sur du jj/mm/aaaa sans `dayfirst` lit
        le 29/04/2024 comme le 4 du 29ᵉ mois — c'est-à-dire jamais. Le défaut a
        déjà été corrigé une fois dans ce projet (`_date_range`), et il est
        revenu dans ce contrôle-ci ; il repart par la porte qu'il est entré."""
        hist = self._hist([
            ("11/04/2024", "Ajout fonds propres", 268.24),
            ("29/04/2024", "Ajout fonds propres", 11579.85),
        ])
        proj = self._proj([
            ("30/03/2024", 29086.0), ("30/04/2024", 28586.0),
        ])
        self._brancher(monkeypatch, hist, proj)
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_capital(hist)

        assert "2024-04-30" in caplog.text, \
            "le 29/04/2024 doit tomber dans la période mars → avril 2024"
        assert "2024-04-29" not in caplog.text.split("→")[0], \
            "une date inversée donnerait une période absurde"

    def test_un_petit_apport_ne_declenche_pas(self, monkeypatch, caplog):
        """Sous 2 000 $ de flux, une absence de mouvement n'est pas concluante :
        le capital de la v1 bougeait de ±1 000 $ tout seul avant 2025."""
        hist = self._hist([("11/04/2024", "Ajout fonds propres", 500.0)])
        proj = self._proj([("30/03/2024", 29000.0), ("30/04/2024", 29000.0)])
        self._brancher(monkeypatch, hist, proj)
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_capital(hist)
        assert "Aucun écart" in caplog.text

    def test_les_invest_prog_ne_sont_pas_des_mouvements(self, monkeypatch, caplog):
        """Les 5 lignes « invest. prog. » ne sont ni des apports ni des retraits,
        et le capital investi de la v1 ne les compte pas non plus. Le contrôle
        doit donc rester muet — c'est une confirmation croisée de la règle
        d'import."""
        hist = self._hist([
            ("05/08/2024", "invest. prog.", 54.76),
            ("16/09/2024", "invest. prog.", 55.62),
        ])
        proj = self._proj([("30/07/2024", 49212.0), ("30/09/2024", 51178.0)])
        self._brancher(monkeypatch, hist, proj)
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_capital(hist)
        assert "Aucun écart" in caplog.text

    def test_un_retrait_compte_en_negatif(self, monkeypatch, caplog):
        """Un retrait fait BAISSER le capital : le signe doit suivre."""
        hist = self._hist([("22/05/2024", "Retrait", 53.48)])
        proj = self._proj([("30/04/2024", 29000.0), ("30/05/2024", 29000.0)])
        self._brancher(monkeypatch, hist, proj)
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_capital(hist)
        assert "Aucun écart" in caplog.text

    def test_projections_absente(self, monkeypatch, caplog):
        self._brancher(monkeypatch, self._hist([("11/04/2024", "Ajout", 5000.0)]),
                       pd.DataFrame())
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_capital(self._hist([("11/04/2024", "Ajout", 5000.0)]))
        assert "Contrôle sauté" in caplog.text

    def test_historique_illisible(self, monkeypatch, caplog):
        with caplog.at_level("INFO"):
            diagnostic.reconcilier_capital(pd.DataFrame())
        assert "illisible" in caplog.text

    def test_le_verdict_nomme_la_periode_et_le_montant(self, monkeypatch):
        hist = self._hist([
            ("29/04/2024", "Ajout fonds propres", 11579.85),
            ("11/04/2024", "Ajout fonds propres", 268.24),
        ])
        proj = self._proj([("30/03/2024", 29086.0), ("30/04/2024", 28586.0)])
        self._brancher(monkeypatch, hist, proj)
        diagnostic.verdicts.clear()
        diagnostic.reconcilier_capital(hist)
        assert diagnostic.verdicts
        assert "2024" in diagnostic.verdicts[0]
        assert "12 348" in diagnostic.verdicts[0] or "12348" in diagnostic.verdicts[0]
        diagnostic.verdicts.clear()
