"""Mise à jour de l'inflation annuelle — depuis la source, pas d'une table figée.

CORRECTION PAR RAPPORT À LA V1
------------------------------
La v1 avait une ligne 2026 à **0,00 %** dans sa table `Inflation`, et
`calculer_performances_annuelles` faisait `fillna({'Inflation (%)': 0.0})`.
Résultat : la performance réelle de l'année en cours était égale à la performance
nominale. Une année sans donnée doit être une année SANS donnée, pas une année à
zéro pour cent.

CE QUI A CHANGÉ ENCORE — VOS CHIFFRES N'ÉTAIENT PAS RENSEIGNÉS
--------------------------------------------------------------
Ce robot lisait une table écrites à la main, arrêtée à 2025, et n'écrivait
l'année en cours qu'en décembre (`if month >= 12`). D'où le trou que vous voyez
dans la page Performance : les années 2026 n'avaient aucun chiffre, et c'est
VOUS qui deviez le fournir.

C'est à l'application de trouver ces chiffres. Elle les trouve maintenant à la
source : le fichier IPC de l'INSEE, téléchargé à chaque passage.

LA SOURCE
---------
`https://api.insee.fr/melodi/file/DS_IPC_PRINC/DS_IPC_PRINC_CSV_FR`

L'API Mélodi de l'INSEE est en accès libre, sans clé. Le fichier est un zip
d'environ 38 Mo contenant l'indice des prix à la consommation, toutes catégories
et toutes périodes depuis 1996.

La série retenue est l'agrégat « ensemble des ménages » :

    IND_TYPE = "IX"   (indice, pas un taux)
    PRODUCT_GROUP = "_Z"    (tous produits — « Non applicable »)
    COICOP_2018 = "00"
    GEO = "F"         (France — le code est F, PAS "FRANCE")
    TPH_CPI = "_T"    (tous ménages)
    FREQ = "M"        (mensuel)

PIÈGE : `PRODUCT_GROUP = "4000"` est l'**Alimentation**, pas l'ensemble. Les
codes 4000 à 5329 sont des divisions de la nomenclature. Seul `_Z` combiné à
`COICOP_2018 = "00"` donne l'ensemble des ménages. Se tromper d'agrégat donne
une inflation alimentaire sur un portefeuille — l'erreur ne se voit pas.

COMMENT L'INFLATION EST CALCULÉE
--------------------------------
    inflation(N) = moyenne des 12 indices mensuels de N
                   -----------------------------------  - 1
                   moyenne des 12 indices mensuels de N-1

C'est la variation de l'indice moyen annuel, exactement ce que le déflateur doit
mesurer. Elle reproduit les chiffres publiés au dixième près :

    2021 : +1,64 %   INSEE publie +1,6 %
    2022 : +5,22 %   INSEE publie +5,2 %
    2023 : +4,88 %   INSEE publie +4,9 %
    2024 : +2,00 %   INSEE publie +2,0 %
    2025 : +0,94 %   INSEE publie +0,9 %

PIÈGE QUI M'A EU : une première version lisait la série sans séparer les bases
périodiques, et tombait sur un mélange de bases 2015 et 2025. 2022 sortait alors
à +4,88 % au lieu de +5,22 % — un écart de 0,34 point dont j'ai d'abord accusé
l'INSEE. Le fichier contient PLUSIEURS séries pour le même agrégat, une par base
(`BASE_PER`). Il faut filtrer sur la plus récente, sinon on additionne des
indices qui ne sont pas dans la même échelle. Voyez `par_base` ci-dessous.

L'ANNÉE EN COURS
----------------
Elle n'a pas de moyenne annuelle avant décembre. On publie donc la variation
réalisée depuis décembre de l'année précédente — l'inflation effectivement
encaissée sur les mois écoulés. Elle est marquée **provisoire** dans `source`,
avec le nombre de mois couverts. Le déflateur de la performance de l'année en
cours doit porter sur la même période que la performance : neuf mois contre neuf
mois, pas neuf mois contre douze.

Rien n'est inventé : si la source est injoignable, aucune ligne n'est écrite et
le log le dit. Une année absente reste absente.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import os
import re
import statistics
import sys
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("inflation")

URL_MELODI = "https://api.insee.fr/melodi/file/DS_IPC_PRINC/DS_IPC_PRINC_CSV_FR"

# En dessous, la série n'est plus representative et l'INSEE a change de base
# plusieurs fois : on ne remonte pas plus loin que le besoin de l'application.
PREMIERE_ANNEE = 2015

# Perimetre de l'agregat « ensemble des menages ». Voyez l'avertissement sur le
# code 4000 dans le docstring : cet en-tete est le coeur du robot.
SELECTEUR = {
    "IND_TYPE": "IX",
    "PRODUCT_GROUP": "_Z",
    "COICOP_2018": "00",
    "GEO": "F",
    "TPH_CPI": "_T",
    "FREQ": "M",
}


def _telecharger() -> bytes:
    """Récupère le zip Mélodi."""
    requete = urllib.request.Request(
        URL_MELODI,
        headers={"User-Agent": "MonPortefeuille2/1.0 (suivi de portefeuille personnel)"},
    )
    with urllib.request.urlopen(requete, timeout=180) as reponse:
        contenu = reponse.read()
    if contenu[:2] != b"PK":
        raise ValueError(
            f"La reponse de l'INSEE n'est pas un zip ({len(contenu)} octets). "
            "L'URL a peut-etre change — verifiez-la avant de continuer."
        )
    return contenu


def serie_mensuelle(zip_bytes: bytes) -> dict[str, float]:
    """Extrait la série mensuelle de l'indice, tous ménages, France entière."""
    classeur = zipfile.ZipFile(io.BytesIO(zip_bytes))
    noms = [n for n in classeur.namelist() if n.endswith("_data.csv")]
    if not noms:
        raise ValueError(
            f"Aucun fichier de donnees dans l'archive INSEE : {classeur.namelist()}"
        )

    par_base: dict[str, dict[str, float]] = {}
    with classeur.open(noms[0]) as brut:
        texte = io.TextIOWrapper(brut, encoding="utf-8", errors="replace")
        lecteur = csv.reader(texte, delimiter=";")
        colonnes = {c: i for i, c in enumerate(next(lecteur))}

        manquantes = [c for c in (*SELECTEUR, "TIME_PERIOD", "OBS_VALUE", "BASE_PER")
                      if c not in colonnes]
        if manquantes:
            raise ValueError(
                f"Colonnes absentes du fichier INSEE : {manquantes}. "
                f"Colonnes presentes : {sorted(colonnes)}. Le format a change."
            )

        for ligne in lecteur:
            if any(ligne[colonnes[c]] != v for c, v in SELECTEUR.items()):
                continue
            periode = ligne[colonnes["TIME_PERIOD"]]
            valeur = ligne[colonnes["OBS_VALUE"]]
            # PIEGE : le fichier contient des OBS_VALUE vides (fin de serie sur
            # certaines ventilations). `float("")` leve ; une serie tronquee en
            # silence donnerait une inflation fausse sans le moindre message.
            if not valeur or not re.fullmatch(r"\d{4}-\d{2}", periode):
                continue
            par_base.setdefault(ligne[colonnes["BASE_PER"]], {})[periode] = float(valeur)

    if not par_base:
        raise ValueError(
            "Serie « ensemble des menages » introuvable. Le selecteur (PRODUCT_GROUP"
            "=_Z, COICOP_2018=00) a peut-etre change cote INSEE — verifiez les"
            " metadonnees avant de toucher au calcul."
        )

    # L'INSEE change de base periodique (2015, 2020, 2025...). On prend la plus
    # recente : c'est la serie courante, et elle couvre tout l'historique
    # rebase — on a verifie qu'elle remonte a 1996.
    base = max(par_base, key=lambda b: int(b) if str(b).strip().isdigit() else 0)
    serie = par_base[base]
    log.info("Base retenue : %s — %d mois, de %s a %s.",
             base, len(serie), min(serie), max(serie))
    return serie


def inflations(serie: dict[str, float]) -> dict[int, tuple[float, int, bool]]:
    """Inflation par année : {année: (taux en %, mois couverts, provisoire)}."""
    par_an: dict[int, list[float]] = {}
    for periode, valeur in serie.items():
        par_an.setdefault(int(periode[:4]), []).append(valeur)

    annee_courante = dt.date.today().year
    resultat: dict[int, tuple[float, int, bool]] = {}

    for annee in sorted(par_an):
        if annee < PREMIERE_ANNEE:
            continue
        mois = par_an[annee]
        if len(mois) == 12 and annee - 1 in par_an and len(par_an[annee - 1]) == 12:
            taux = (statistics.mean(mois) / statistics.mean(par_an[annee - 1]) - 1) * 100
            resultat[annee] = (taux, 12, False)
        elif annee == annee_courante and annee - 1 in par_an and len(par_an[annee - 1]) == 12:
            # Année en cours : variation depuis décembre précédent.
            decembre = max(p for p in serie if p.startswith(f"{annee - 1}-"))
            dernier = max(p for p in serie if p.startswith(f"{annee}-"))
            taux = (serie[dernier] / serie[decembre] - 1) * 100
            resultat[annee] = (taux, int(dernier[5:7]), True)
    return resultat


def main() -> int:
    annee_courante = dt.date.today().year

    try:
        existantes = db.inflation()
    except Exception as exc:
        log.error("Lecture de l'inflation échouée : %s", exc)
        return 1

    deja = set()
    if not existantes.empty and "annee" in existantes.columns:
        deja = {int(a) for a in existantes["annee"].dropna()}

    try:
        serie = serie_mensuelle(_telecharger())
    except Exception as exc:
        # Panne reseau ou changement de format : on n'ecrit RIEN. Les chiffres
        # deja en base restent valides, et le trou reste visible plutot que
        # comble par une valeur inventee.
        log.error("Serie INSEE indisponible : %s", exc)
        log.error("Aucune ligne ecrite. L'inflation deja en base est conservee.")
        return 1

    calculees = inflations(serie)

    a_ecrire = []
    for annee, (taux, mois, provisoire) in sorted(calculees.items()):
        if annee in deja and not provisoire:
            continue        # une annee close et deja en base ne bouge pas
        if provisoire:
            source = (
                f"INSEE — ensemble des ménages, IPC base 2025, "
                f"{mois} mois sur 12, provisoire"
            )
        else:
            source = "INSEE — ensemble des ménages, IPC base 2025"
        a_ecrire.append({
            "annee": annee,
            "inflation": round(taux, 3),
            "source": source,
        })

    if not a_ecrire:
        log.info("Inflation déjà à jour (%d années en base).", len(deja))
    else:
        try:
            # UPSERT, pas INSERT. La clé primaire de `pf2_inflation` est `annee` :
            # un insert échoue en 409 dès que l'année existe déjà, et elle existe
            # toujours — le robot tourne chaque nuit sur les mêmes années. Le
            # premier lancement réel s'est arrêté là, sur « Key (annee)=(2021)
            # already exists », sans rien écrire du tout : pas même le 2026 qui
            # manquait. L'upsert rend le robot idempotent, ce qu'un robot
            # quotidien doit être par définition.
            db.remplacer(db.T_INFLATION, a_ecrire, on_conflict="annee")
        except Exception as exc:
            log.error("Écriture de l'inflation échouée : %s", exc)
            return 1
        for ligne in a_ecrire:
            marque = "  (provisoire)" if "provisoire" in ligne["source"] else ""
            log.info("  %d : %+.2f %%%s", ligne["annee"], ligne["inflation"], marque)

    connues = sorted(set(calculees) | deja)
    if connues:
        manquantes = [a for a in range(connues[0], annee_courante + 1) if a not in connues]
        if manquantes:
            log.warning(
                "Années sans inflation : %s. La performance réelle de ces années "
                "ne sera pas calculable — c'est voulu, mieux vaut un trou visible "
                "qu'un 0 %% qui ment.",
                manquantes,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
