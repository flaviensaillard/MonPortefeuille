"""Le guide de déclaration : quel formulaire, quelle case, quel montant.

POURQUOI CE MODULE EXISTE
-------------------------
La page Fiscalité calculait l'impôt, mais elle ne disait pas OÙ l'inscrire. Un
montant juste dans une case inconnue ne sert à rien le jour de la déclaration.
Votre v1 affichait un guide complet ; il avait disparu.

Ce module construit ce guide **depuis vos données**, pas depuis un texte
générique. Chaque montant est calculé, chaque case est nommée, chaque formulaire
est situé dans le parcours de déclaration en ligne.

CE QUI EST DÉCLARÉ, ET CE QUI EST DÉDUIT
----------------------------------------
Le module ne devine jamais où se trouve un compte. Le pays d'un compte est une
**information que vous fournissez** (`comptes_etrangers`) : l'application ne
peut pas savoir si votre compte Revolut est adossé à une entité française,
irlandaise ou britannique, et une erreur là-dessus coûte 1 500 € par an.

Ce qui est déduit des données : les montants (ils sont en base), le régime
fiscal de chaque actif (il est dans `models.REGIMES_FISCAUX`), et le caractère
imposable ou non d'une cession (il suit l'article 150 VH bis).

DÉFAUTS DE L'ANCIENNE PAGE, CORRIGÉS ICI
----------------------------------------
1. `st.stop()` était appelé dès qu'il n'y avait aucune cession. Le guide
   disparaissait donc précisément les années où il faut quand même déclarer
   quelque chose — un compte à l'étranger se déclare même sans une seule
   opération, et une moins-value non déclarée est une moins-value perdue.
2. Aucun numéro de formulaire n'apparaissait : ni 2042-C, ni 2086, ni 3916-bis.
3. Aucun montant n'était présenté sous la forme « inscrivez ceci dans cette
   case », alors que c'est la seule chose dont on a besoin sous les yeux.

RÉFÉRENCES
----------
CGI art. 150-0 A (valeurs mobilières), 150 VH bis (actifs numériques),
150 VI (métaux précieux), 200 A (PFU), 1649 A (comptes à l'étranger) ;
LFSS 2026, loi n° 2025-1403 du 30/12/2025, art. 12 (CSG capital à 10,6 %).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import fiscal_bars as fb
from .models import Classe


@dataclass
class Case:
    """Une case à remplir : son numéro, ce qu'elle attend, et combien."""

    formulaire: str
    numero: str
    libelle: str
    montant: float | None
    comment: str                      # d'où vient le montant, ou quoi y écrire
    obligatoire: bool = True


@dataclass
class Etape:
    """Un formulaire, et les cases qu'il porte."""

    ordre: int
    formulaire: str
    titre: str
    obligatoire: bool
    raison: str
    ou: str                           # chemin dans la déclaration en ligne
    cases: list[Case] = field(default_factory=list)
    sanction: str = ""
    reference: str = ""


def _franchise_crypto_depassee(resultat) -> bool | None:
    return getattr(resultat, "exonere_par_franchise", None)


def construire_guide(
    *,
    annee: int,
    resultats: dict,
    comptes_etrangers: list[str],
    comparaison: dict | None = None,
) -> list[Etape]:
    """Construit la liste des formulaires à remplir, dans l'ordre du parcours.

    `annee` est l'année des REVENUS. La déclaration se fait en `annee + 1`.
    """
    etapes: list[Etape] = []
    ordre = 0

    # -----------------------------------------------------------------------
    # 3916-bis — les comptes à l'étranger. TOUJOURS en premier.
    # -----------------------------------------------------------------------
    # C'est l'étape la plus coûteuse à oublier et la plus facile à omettre :
    # elle ne dépend d'aucun revenu. Un compte détenu, utilisé ou clôturé dans
    # l'année se déclare, même sans un seul mouvement.
    if comptes_etrangers:
        ordre += 1
        lignes = [Case(
            formulaire="3916-bis",
            numero="—",
            libelle=f"Compte : {libelle}",
            montant=None,
            comment=(
                "Un formulaire 3916-bis PAR compte. Indiquez le numéro de "
                "compte (IBAN), la date d'ouverture et l'adresse de "
                "l'établissement gestionnaire."
            ),
        ) for libelle in comptes_etrangers]

        etapes.append(Etape(
            ordre=ordre,
            formulaire="3916-bis",
            titre="Comptes ouverts, détenus, utilisés ou clos à l'étranger",
            obligatoire=True,
            raison=(
                "Vous détenez au moins un compte hors de France. L'obligation "
                "porte sur l'EXISTENCE du compte, pas sur son activité."
            ),
            ou=(
                "impots.gouv.fr → Déclarer mes revenus → étape 3 « Revenus et "
                "charges » → onglet « Divers » → cocher la case 8UU, puis "
                "« Comptes et contrats à l'étranger » pour saisir chaque compte."
            ),
            cases=lignes + [Case(
                formulaire="2042",
                numero="8UU",
                libelle="Comptes ouverts, détenus, utilisés ou clos à l'étranger",
                montant=None,
                comment=(
                    "Case à cocher, pas un montant. C'est cet oubli-là qui "
                    "coûte le plus cher : 1 500 € par compte et par année, "
                    "MÊME si tous vos revenus ont été correctement déclarés."
                ),
            )],
            sanction="1 500 € par compte non déclaré et par an (10 000 € si l'État est non coopératif). Délai de reprise porté à 10 ans.",
            reference="CGI art. 1649 A",
        ))

    # -----------------------------------------------------------------------
    # 2042-C — plus-values de cessions de valeurs mobilières (150-0 A)
    # -----------------------------------------------------------------------
    titre_pv = resultats.get("pv_titres")
    if titre_pv is not None:
        ordre += 1
        pv_nette = titre_pv.plus_value_imposable
        brute = titre_pv.plus_value_brute

        cases: list[Case] = []
        if brute > 0:
            cases.append(Case(
                formulaire="2042-C",
                numero="3VG",
                libelle="Plus-value nette de cessions de valeurs mobilières",
                montant=pv_nette,
                comment=(
                    "Le montant NET : plus-values de l'année diminuées des "
                    "moins-values de l'année, puis des moins-values "
                    "reportables antérieures (les plus anciennes d'abord)."
                ),
            ))
        elif brute < 0:
            cases.append(Case(
                formulaire="2042-C",
                numero="3VH",
                libelle="Moins-value nette de cessions de valeurs mobilières",
                montant=-brute,
                comment=(
                    "Un montant POSITIF, jamais en négatif. Elle ne réduit ni "
                    "vos salaires ni vos revenus fonciers : elle n'est "
                    "imputable que sur des plus-values de même nature, et elle "
                    "se reporte 10 ans. Une moins-value non déclarée est une "
                    "moins-value perdue — le fisc ne reconstruit pas "
                    "l'historique à votre place."
                ),
            ))
        else:
            cases.append(Case(
                formulaire="2042-C",
                numero="—",
                libelle="Aucune plus-value ni moins-value de valeurs mobilières",
                montant=None,
                comment="Rien à reporter : vos cessions de l'année s'équilibrent.",
                obligatoire=False,
            ))

        if abs(brute) > 0:
            cases.append(Case(
                formulaire="2074-CMV",
                numero="—",
                libelle="Décompte des plus ou moins-values de cessions de valeurs mobilières",
                montant=None,
                comment=(
                    "Obligatoire dès qu'il y a une moins-value ou un report à "
                    "imputer. Sans cette annexe, l'administration peut "
                    "considérer l'imputation comme non justifiée et la refuser."
                ),
                obligatoire=True,
            ))

        etapes.append(Etape(
            ordre=ordre,
            formulaire="2042-C",
            titre="Cessions de valeurs mobilières et droits sociaux",
            obligatoire=True,
            raison=(
                f"{len(titre_pv.detail)} cession(s) enregistrée(s) en {annee} "
                f"pour un résultat de {brute:+,.2f} €.".replace(",", " ")
            ),
            ou=(
                "impots.gouv.fr → étape 3 → « Plus-values et gains assimilés » "
                "(2042-C). Rien n'est pré-rempli pour les comptes étrangers."
            ),
            cases=cases,
            sanction="Défaut de déclaration : 80 % de majoration, et la prescription passe de 3 à 10 ans.",
            reference="CGI art. 150-0 A et 200 A",
        ))

    # -----------------------------------------------------------------------
    # 2086 — actifs numériques
    # -----------------------------------------------------------------------
    crypto = resultats.get("pv_crypto")
    if crypto is not None:
        ordre += 1
        depassee = _franchise_crypto_depassee(crypto)
        total_cessions = getattr(crypto, "total_cessions", None)

        cases = [Case(
            formulaire="2086",
            numero="211 à 224",
            libelle="Décompte de chaque cession d'actif numérique",
            montant=total_cessions,
            comment=(
                "Une ligne par cession. Chaque ligne demande la valeur globale "
                "du portefeuille à la date de cession — c'est elle qui permet "
                "de fractionner le prix d'acquisition, et sans elle le calcul "
                "est faux."
            ),
        )]

        # L'ORDRE DES TESTS COMPTE, et je l'avais inversé.
        #
        # Ma première version testait la franchise AVANT le signe du résultat :
        # une moins-value de 300 EUR sur 900 EUR de cessions partait donc en
        # case 3AN — « plus-value » — avec un montant negatif. La 3BN, qui est
        # faite pour elle, restait vide. Le seuil est franchi, mais ce n'est pas
        # une plus-value pour autant.
        if crypto.plus_value_brute < 0 and depassee is False:
            cases.append(Case(
                formulaire="2042-C",
                numero="3BN",
                libelle="Moins-value de cession d'actifs numériques",
                montant=-crypto.plus_value_brute,
                comment=(
                    f"Vous avez cédé {total_cessions:,.2f} € et dégagé une "
                    "moins-value. Montant POSITIF en case 3BN. Imputable "
                    "uniquement sur des plus-values d'actifs numériques, et "
                    "reportable 10 ans.".replace(",", " ")
                ),
            ))
        elif depassee is False:
            cases.append(Case(
                formulaire="2042-C",
                numero="3AN",
                libelle="Plus-value de cession d'actifs numériques",
                montant=crypto.plus_value_imposable,
                comment=(
                    f"Vous avez cédé {total_cessions:,.2f} €, au-dessus du seuil "
                    f"de {fb.CRYPTO_FRANCHISE_CESSIONS:.0f} € : la plus-value est "
                    "imposable dès le premier euro. Il n'y a AUCUN abattement à "
                    "déduire — le seuil déclenche l'imposition, il n'en retire "
                    "rien.".replace(",", " ")
                ),
            ))
        elif crypto.plus_value_brute > 0:
            cases.append(Case(
                formulaire="2042-C",
                numero="3AN",
                libelle="Plus-value — mais le seuil de 305 € n'est pas franchi",
                montant=None,
                comment=(
                    f"Vous avez cédé {total_cessions:,.2f} € et dégagé "
                    f"{crypto.plus_value_brute:,.2f} € de gain, mais le total "
                    f"cédé ne dépasse pas {fb.CRYPTO_FRANCHISE_CESSIONS:.0f} € : "
                    "la plus-value est exonérée. La 2086 se dépose quand même."
                    .replace(",", " ")
                ),
                obligatoire=False,
            ))
        else:
            cases.append(Case(
                formulaire="2042-C",
                numero="3AN / 3BN",
                libelle="Rien à reporter — seuil de 305 € non franchi",
                montant=None,
                comment=(
                    f"Le total de vos prix de cession ({total_cessions:,.2f} €) "
                    f"ne dépasse pas {fb.CRYPTO_FRANCHISE_CESSIONS:.0f} € : la "
                    "plus-value est exonérée et les cases 3AN/3BN restent vides. "
                    "La 2086 se dépose quand même — c'est elle qui prouve que "
                    "vous êtes sous le seuil.".replace(",", " ")
                ),
                obligatoire=False,
            ))

        etapes.append(Etape(
            ordre=ordre,
            formulaire="2086",
            titre="Plus ou moins-values de cessions d'actifs numériques",
            obligatoire=True,
            raison=(
                f"{len(crypto.detail)} cession(s) d'actifs numériques en {annee}. "
                "La déclaration est due dès la première cession imposable, "
                "même exonérée d'impôt."
            ),
            ou=(
                "impots.gouv.fr → étape 3 → bouton « Déclarations annexes » → "
                "2086. Le résultat est reporté automatiquement en 3AN/3BN de la "
                "2042-C."
            ),
            cases=cases,
            reference="CGI art. 150 VH bis",
        ))

    # -----------------------------------------------------------------------
    # 2047 — revenus de source étrangère
    # -----------------------------------------------------------------------
    besoins_2047 = crypto is not None or titre_pv is not None
    if besoins_2047 and comptes_etrangers:
        ordre += 1
        cases = []
        if titre_pv is not None and titre_pv.plus_value_imposable > 0:
            cases.append(Case(
                formulaire="2042-C",
                numero="3VG (repris de la 2047, cadre 3)",
                libelle="Plus-value de cession de valeurs mobilières étrangères",
                montant=titre_pv.plus_value_imposable,
                comment=(
                    "Les titres sont détenus hors de France : la plus-value est "
                    "de source étrangère, donc la 2047 est le chemin obligé "
                    "vers la case 3VG."
                ),
            ))
        cases.append(Case(
            formulaire="2042-C",
            numero="8VL / 8VM / 8WM / 8UM",
            libelle="Crédits d'impôt conventionnels",
            montant=None,
            comment=(
                "À remplir seulement si un impôt a été retenu à la source à "
                "l'étranger. Sur des ETF capitalisants détenus en direct et "
                "sans dividende versé, il n'y a rien à créditer — ces cases "
                "restent vides."
            ),
            obligatoire=False,
        ))
        etapes.append(Etape(
            ordre=ordre,
            formulaire="2047",
            titre="Revenus encaissés à l'étranger",
            obligatoire=True,
            raison=(
                "Vos titres sont détenus chez un établissement hors de France. "
                "La 2047 est la déclaration annexe obligatoire pour tout revenu "
                "— plus-value comprise — encaissé hors de France."
            ),
            ou=(
                "impots.gouv.fr → étape 3 → cocher « Revenus encaissés à "
                "l'étranger ». Le formulaire s'ajoute automatiquement."
            ),
            cases=cases,
            reference="CGI art. 170 et conventions fiscales bilatérales",
        ))

    # -----------------------------------------------------------------------
    # Choix du régime : 2OP
    # -----------------------------------------------------------------------
    if comparaison and comparaison.get("plus_value_nette", 0) > 0:
        ordre += 1
        retenu = comparaison["choix"]
        etapes.append(Etape(
            ordre=ordre,
            formulaire="2042",
            titre="Option pour le barème progressif",
            obligatoire=False,
            raison=(
                f"Sur vos plus-values, le régime le plus avantageux est : "
                f"{retenu}."
            ),
            ou=(
                "impots.gouv.fr → étape 3 → « Option pour le barème progressif "
                "de l'impôt sur le revenu » : c'est la case 2OP."
            ),
            cases=[Case(
                formulaire="2042",
                numero="2OP",
                libelle="Option globale pour le barème progressif de l'IR",
                montant=None,
                comment=(
                    f"À cocher UNIQUEMENT si le barème vous coûte moins cher. "
                    f"Calcul sur vos seules plus-values : PFU "
                    f"{comparaison['pfu']['total']:,.2f} € contre barème "
                    f"{comparaison['bareme']['total']:,.2f} €, soit "
                    f"{retenu} retenu et {comparaison['gain']:,.2f} € "
                    "d'économie. L'option est GLOBALE et annuelle : elle porte "
                    "sur tous vos revenus du capital, pas seulement sur les "
                    "plus-values.".replace(",", " ")
                ),
                obligatoire=retenu == "Barème progressif",
            )],
            reference="CGI art. 200 A",
        ))

    # -----------------------------------------------------------------------
    # Or physique — 150 VI. Vous n'en détenez pas, mais le régime est piégeux.
    # -----------------------------------------------------------------------
    or_physique = resultats.get("pv_or_physique")
    if or_physique:
        ordre += 1
        etapes.append(Etape(
            ordre=ordre,
            formulaire="2042-C",
            titre="Cessions de métaux précieux — article 150 VI",
            obligatoire=True,
            raison="Vous avez cédé de l'or ou des métaux précieux sous forme physique.",
            ou="impots.gouv.fr → étape 3 → « Plus-values et gains assimilés ».",
            cases=[Case(
                formulaire="2042-C",
                numero="3VP / 3VQ",
                libelle="Cessions de métaux précieux et d'objets d'art",
                montant=or_physique.get("total_du"),
                comment=(
                    "Régime distinct des valeurs mobilières : taxe forfaitaire "
                    "de 11,5 % sur le prix de cession, OU impôt sur la "
                    "plus-value réelle avec abattement de 5 % par année de "
                    "détention au-delà de la deuxième, exonération totale à "
                    "22 ans. Le choix se fait case par case."
                ),
            )],
            reference="CGI art. 150 VI et 150 VK",
        ))

    return etapes


def comptes_par_defaut() -> list[str]:
    """Comptes à l'étranger que l'application sait déjà.

    On ne devine rien : ces deux entrées viennent des descriptions des poches
    (`models.POCHES`), écrites quand le portefeuille a été créé. Le compte
    courant n'y figure pas — Revolut peut être adossé à une entité française,
    irlandaise ou britannique, et cette question-là vous appartient.
    """
    return [
        "Compte-titres Swissquote — Swissquote Bank Europe SA, Luxembourg",
        "Livret CHF Swissquote — Swissquote Bank Europe SA, Luxembourg",
    ]
