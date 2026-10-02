"""Garde-fou : les appels Streamlit doivent utiliser les bons mots-clés.

La page d'accueil plantait avec :

    TypeError: metric() got an unexpected keyword argument 'aide'

**Seize** appels dans quatre fichiers passaient `aide=` à `st.metric()`, qui
attend `help=`. Le projet possède pourtant un helper, `ui.metrique()`, qui fait
exactement cette traduction — mais ces appels-là passaient outre et appelaient
Streamlit directement.

Le défaut était latent : chaque plantage précédent arrêtait l'application avant
qu'elle n'atteigne la première métrique. C'est le troisième défaut de cette
famille (spécificateur de format, `nan`, mots-clés Streamlit) : à chaque fois le
code n'avait jamais été exécuté jusqu'au bout.

Ce test balaie donc le code source lui-même, sans lancer Streamlit.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

st = pytest.importorskip("streamlit")

RACINE = pathlib.Path(__file__).resolve().parent.parent
FICHIERS = [RACINE / "app.py"] + sorted((RACINE / "pages").glob("*.py"))

# Colonnes Streamlit : `c1`, `g2`, `d3`... exposent la même API que `st`.
PREFIXES_COLONNES = ("c", "g", "d")


def _appels_metric(fichier: pathlib.Path):
    """Tous les appels `.metric(...)` d'un fichier, avec leurs mots-clés."""
    arbre = ast.parse(fichier.read_text(encoding="utf-8"))
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.Call):
            continue
        cible = noeud.func
        if not isinstance(cible, ast.Attribute) or cible.attr != "metric":
            continue
        yield noeud


def _kwargs_valides_metric() -> set[str]:
    """Les mots-clés que `st.metric()` accepte réellement."""
    sig = inspect.signature(st.metric)
    valides = {p.name for p in sig.parameters.values()
               if p.name != "self" and p.kind is not inspect.Parameter.VAR_KEYWORD}
    return valides


class TestMetricKwargs:
    def setup_method(self):
        self.valides = _kwargs_valides_metric()

    def test_help_est_bien_le_bon_mot_cle(self):
        """Le cœur du correctif : `aide` n'existe pas, `help` si."""
        assert "help" in self.valides
        assert "aide" not in self.valides

    @pytest.mark.parametrize("fichier", FICHIERS, ids=lambda p: p.name)
    def test_aucun_mot_cle_invalide(self, fichier):
        if not fichier.exists():
            pytest.skip(f"{fichier.name} absent")
        for noeud in _appels_metric(fichier):
            for kw in noeud.keywords:
                if kw.arg is None:
                    continue  # **kwargs déballé : non vérifiable
                assert kw.arg in self.valides, (
                    f"{fichier.name}:{kw.value.lineno} — st.metric() ne prend pas "
                    f"'{kw.arg}='. Utilisez 'help=' ou passez par ui.metrique()."
                )

    def test_le_projet_contient_des_appels_metric(self):
        """Sans ce test, un balayage vide passerait pour un succès."""
        total = sum(1 for f in FICHIERS if f.exists() for _ in _appels_metric(f))
        assert total >= 10, f"seulement {total} appels .metric() trouvés — le balayage est vide"


class TestUiMetriqueTraduitAide:
    """`ui.metrique()` est le helper prévu : il doit traduire `aide` → `help`."""

    def test_metrique_accepte_aide(self):
        import inspect as ins
        from core import ui
        params = set(ins.signature(ui.metrique).parameters)
        assert "aide" in params

    def test_metrique_passe_help_a_streamlit(self, monkeypatch):
        from core import ui
        capture = {}

        def faux_metric(**kwargs):
            capture.update(kwargs)

        monkeypatch.setattr(ui.st, "metric", faux_metric)
        ui.metrique("Libellé", "Valeur", aide="Mon aide")
        assert capture.get("help") == "Mon aide"
        assert "aide" not in capture

    def test_metrique_sans_aide_passe_none(self, monkeypatch):
        from core import ui
        capture = {}

        def faux_metric(**kwargs):
            capture.update(kwargs)

        monkeypatch.setattr(ui.st, "metric", faux_metric)
        ui.metrique("Libellé", "Valeur")
        assert capture.get("help") is None


class TestRafraichissementApresImport:
    """Le cache doit expirer, et on doit pouvoir le vider à la main.

    `charger()` est mémoïsé 5 minutes. Après un import, l'application continuait
    donc à servir les anciennes transactions — un message d'erreur survivait à sa
    propre correction, et l'utilisateur croyait le correctif inefficace. D'où un
    bouton de rafraîchissement explicite.
    """

    def test_charger_expose_une_methode_clear(self):
        from core import session as S
        assert hasattr(S.charger, "clear"), (
            "sans cache, impossible de forcer une relecture"
        )

    def test_vider_cache_ne_leve_pas(self):
        from core import session as S
        S.vider_cache()          # ne doit rien casser

    def test_le_tableau_de_bord_a_un_bouton_de_rafraichissement(self):
        src = (RACINE / "app.py").read_text(encoding="utf-8")
        assert "Rafraîchir" in src, "aucun moyen de forcer la relecture après un import"
        assert "S.vider_cache()" in src

    def test_le_ttl_est_court(self):
        """Un TTL de plusieurs minutes fait survivre une erreur corrigée."""
        src = (RACINE / "core" / "session.py").read_text(encoding="utf-8")
        ligne = next(
            (l for l in src.splitlines() if "st.cache_data" in l and "ttl" in l),
            "",
        )
        assert "ttl=" in ligne, f"TTL introuvable dans : {ligne!r}"
        secondes = int(ligne.split("ttl=")[1].split(",")[0].strip())
        assert secondes <= 300, f"TTL trop long : {secondes} s"
