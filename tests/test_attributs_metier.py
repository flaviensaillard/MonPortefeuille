"""Verificateur statique : aucun attribut inexistant sur les objets metier.

Pourquoi ce fichier existe
--------------------------
`app.py` ecrivait `e.poche.bande` alors que `EcartPoche` n'a pas de champ
`poche` — elle a `bande`. `AttributeError` en production, et aucun test ne
l'avait attrape parce qu'aucun test ne rend le tableau de bord : les tests
couvrent les modules `core/`, pas l'assemblage de `app.py` et des pages.

Une coquille d'attribut est pourtant le defaut le plus facile a detecter sans
executer quoi que ce soit : il suffit de connaitre le type de la variable de
boucle. Ce test le fait, pour toutes les pages.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from core import rebalance
from core.portfolio import Actif, EtatPoche, Position, Transaction


RACINE = pathlib.Path(__file__).resolve().parents[1]

# Fragment de l'expression parcourue -> type de l'objet. La reconnaissance se
# fait par sous-chaine : `for a in sorted(ctx.actifs, key=...)` comme
# `for a in ctx.actifs` designent le meme type.
TYPES = [
    ("ctx.ecarts", rebalance.EcartPoche),
    ("ctx.besoins_reequilibrage", rebalance.Ordre),
    ("ctx.actifs", Actif),
    ("ctx.transactions", Transaction),
    ("ctx.etats.items()", EtatPoche),
    ("ctx.positions", Position),
]

FICHIERS = [RACINE / "app.py"] + sorted((RACINE / "pages").glob("*.py"))


def _attributs_valides(cls) -> set[str]:
    noms = set(dir(cls))
    for champ in dataclasses.fields(cls):
        noms.add(champ.name)
    return noms


def _type_de(expression: str):
    for fragment, cls in TYPES:
        if fragment in expression:
            return cls
    return None


@pytest.mark.parametrize("fichier", FICHIERS, ids=lambda f: f.name)
def test_aucun_attribut_inconnu(fichier):
    source = fichier.read_text(encoding="utf-8")
    arbre = ast.parse(source)
    problemes = []

    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.For):
            continue

        texte_iter = ast.get_source_segment(source, noeud.iter)
        if texte_iter is None:
            continue
        cls = _type_de(texte_iter.strip())
        if cls is None:
            continue

        # Une ou deux variables de boucle : `for e in ...` ou `for cle, etat in ...`
        cibles = noeud.target.elts if isinstance(noeud.target, ast.Tuple) else [noeud.target]
        variables = [c.id for c in cibles if isinstance(c, ast.Name)]
        # Si le type est parcouru via `.items()`, la valeur est le SECOND element.
        valeur = variables[-1] if ".items()" in texte_iter else variables[0]
        if not valeur:
            continue

        valides = _attributs_valides(cls)
        for sous in ast.walk(noeud):
            if not isinstance(sous, ast.Attribute):
                continue
            if not isinstance(sous.value, ast.Name) or sous.value.id != valeur:
                continue
            if sous.attr not in valides:
                problemes.append(
                    f"{fichier.name}:{sous.lineno} "
                    f"{valeur}.{sous.attr} n'existe pas sur {cls.__name__}"
                )

    assert not problemes, "\n".join(problemes)


def test_le_verificateur_couvert_assez_de_boucles():
    """Un verificateur qui ne regarde rien ne sert a rien : on verifie qu'il
    trouve bien les boucles metier dans app.py et les pages."""
    trouves = 0
    for fichier in FICHIERS:
        source = fichier.read_text(encoding="utf-8")
        for noeud in ast.walk(ast.parse(source)):
            if not isinstance(noeud, ast.For):
                continue
            texte = ast.get_source_segment(source, noeud.iter) or ""
            if _type_de(texte.strip()) is not None:
                trouves += 1
    # Les quatre boucles a risque : `ctx.ecarts` (app.py et Reequilibrage),
    # `ctx.actifs` et `ctx.etats.items()` (Liste des actifs). Toutes portent
    # sur des dataclasses, donc sur des attributs qui peuvent etre mal orthographies.
    assert trouves >= 4, f"seulement {trouves} boucles metier detectees"
