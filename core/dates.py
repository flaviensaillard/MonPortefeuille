"""Dates — un seul endroit pour les bizarreries de pandas et de Yahoo.

Pourquoi ce module existe
-------------------------
`core/fx.py` et `core/prices.py` filtraient tous les deux une série de Yahoo
avec :

    serie[serie.index <= pd.Timestamp(une_date)]

Sauf que Yahoo renvoie un index **conscient du fuseau horaire** —
`datetime64[ns, Europe/London]` pour une paire de change, le fuseau de la place
de cotation pour un titre. Comparer un tel index à un `Timestamp` naive lève :

    TypeError: Invalid comparison between dtype=datetime64[ns, Europe/London]
               and Timestamp

Conséquence en production : **toute recherche de cours ou de taux à une date
passée échouait**. Un portefeuille acheté avant hier ne pouvait pas être
valorisé — l'application affichait « Taux de change USD/EUR indisponible au
2025-01-07 » alors que Yahoo avait parfaitement la donnée.

Le piège est resté invisible longtemps parce que la branche « aujourd'hui »
(`period="5d"`) ne fait aucune comparaison : seules les dates anciennes
tombaient dans le panneau. Et aucun test ne couvrait cette branche.
"""

from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd

# Une date ISO commence par quatre chiffres, un tiret, puis le mois et le jour.
# C'est l'écriture de la base (`pf2_transactions.date`).
_ISO = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")


def index_sans_fuseau(index):
    """Rend un index de dates comparable à un `Timestamp` naive.

    On retire le fuseau en conservant l'heure locale du marché : c'est l'heure
    de la séance, et c'est bien elle qu'on veut comparer à une date de journée.
    Un index déjà naive repart inchangé.
    """
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx


def normaliser(instant) -> pd.Timestamp:
    """Rend un `Timestamp` comparable à un index Yahoo, quel que soit son fuseau."""
    ts = pd.Timestamp(instant)
    if ts.tz is not None:
        ts = ts.tz_localize(None)
    return ts


def _parser_un(valeur, erreurs: str = "coerce"):
    """Parse une date isolee. Voir `parser` pour le contrat complet."""
    # Objets deja dates : rien a deviner, on les rend tels quels.
    if isinstance(valeur, (dt.date, dt.datetime, np.datetime64, pd.Timestamp)):
        return pd.to_datetime(valeur, errors=erreurs)

    if valeur is None:
        return pd.NaT

    texte = str(valeur).strip()
    if not texte:
        return pd.NaT

    if _ISO.match(texte):
        # ISO : la date occupe toujours les dix premiers caracteres, qu'une
        # heure et un fuseau suivent ou non.
        return pd.to_datetime(texte[:10], format="%Y-%m-%d", errors=erreurs)

    # Ecriture de la v1 : jour puis mois. `dayfirst` reste la par securite pour
    # les formes que `%d/%m/%Y` ne couvrirait pas, mais le format est impose.
    # La v1 ecrit parfois une date AVEC son heure : `11/05/2026 12:38:33`.
    # Imposer `%d/%m/%Y` sur la chaine entiere echoue, et la journee serait
    # perdue. On ne garde donc que la partie date.
    partie_date = texte.split(" ")[0].split("T")[0]
    return pd.to_datetime(partie_date, format="%d/%m/%Y", errors=erreurs, dayfirst=True)


def parser(valeur, erreurs: str = "coerce"):
    """Parse une date venant soit de la base, soit d'un CSV de la v1.

    Deux écritures, deux formats imposés — on ne laisse JAMAIS pandas deviner
    ----------------------------------------------------------------------
    L'application stocke ses dates en ISO (`2025-07-01`). La v1, elle, écrit
    `jj/mm/aaaa` (`01/07/2025` pour le 1er juillet). Les deux doivent donner le
    1er juillet 2025.

    Les deux « correctifs » successifs échouaient chacun sur une des deux
    écritures :

    * `dayfirst=True` seul lisait l'ISO à l'envers : `2025-07-01` devenait le
      7 janvier. C'est le défaut d'origine.
    * `format="mixed"` + `dayfirst=True` réparait l'ISO, mais faisait lire
      `01/07/2025` comme le **7 janvier** : `dayfirst` est ignoré quand pandas
      devine le format, et il devine `%m/%d/%Y`.

    Et surtout, le comportement de `dayfirst` couplé à `format="mixed"` n'est
    pas le même d'une version de pandas à l'autre : un code juste ici peut
    donc être faux sur le serveur, sans qu'aucune ligne ne change.

    On devine donc le format à la FORME de la chaîne, puis on l'impose. Aucune
    heuristique, aucune dépendance à la version de pandas.
    """
    # Une colonne entiere : on traite element par element, en conservant l'index.
    # `pd.to_datetime` sur une Series melange les formats et retombe sur ses
    # heuristicites — exactement ce qu'on veut eviter.
    if isinstance(valeur, (pd.Series, pd.Index, np.ndarray, list, tuple)):
        return pd.Series(
            [_parser_un(v, erreurs) for v in valeur],
            index=getattr(valeur, "index", None),
        )
    return _parser_un(valeur, erreurs)


def dernier_avant(serie: pd.Series, limite) -> pd.Series:
    """Sous-ensemble d'une série datée dont l'index est au plus tard à `limite`.

    Tolérant au fuseau horaire des deux côtés. Le remplaçant direct de
    `serie[serie.index <= limite]`, qui plantait sur les données Yahoo.

    Le filtrage est positionnel (masque booléen), donc sûr même si l'index
    contient des doublons ou n'est pas trié.
    """
    if serie is None or len(serie) == 0:
        return serie
    idx = index_sans_fuseau(serie.index)
    seuil = normaliser(limite)
    masque = np.asarray(idx <= seuil)
    return serie[masque]
