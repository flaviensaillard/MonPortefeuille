"""Import des données de la v1 vers la v2.

CORRECTION PAR RAPPORT À LA V1
------------------------------
La v1 contenait une anomalie de données sur XJSE.SW : 9 achats saisis en JPY
(cours ≈ 1 115) et un saisi en USD (cours 6,87), le 05/06/2026. Même titre, deux
conventions de devise, et un PRU qui ne veut plus rien dire.

Cet import **normalise chaque titre sur sa devise de cotation réelle** et signale
les lignes qu'il a dû corriger. Sans ça, le bug serait importé tel quel.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db, fx  # noqa: E402
from core.portfolio import DEVISES_COTATION  # noqa: E402
from core.dates import parser

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("import")

# Tables de la v1.
V1_TRANSACTIONS = "Transaction"
V1_HISTORIQUE = "Historique"          # journal de tresorerie (apports, retraits)
V1_PROJECTIONS = "Projections"        # valorisations : 180 lignes, 04/2023 -> 10/2026


def lire_v1(table: str) -> pd.DataFrame:
    """Lit une table de la v1."""
    rep = db.client().table(table).select("*").execute()
    return pd.DataFrame(rep.data or [])


def _dedupliquer(
    lignes: list[dict],
    cle: tuple[str, ...] = ("ticker", "sens", "date", "quantite", "cours"),
    fusionner: bool = False,
) -> tuple[list[dict], list[str]]:
    """Retire les lignes qui partagent la clé de conflit de `pf2_transactions`.

    Pourquoi c'est nécessaire : `remplacer()` fait un upsert unique. Si le lot
    contient deux lignes de même (ticker, sens, date, quantite, cours),
    PostgreSQL devrait mettre à jour la même ligne cible deux fois dans la même
    commande, et il refuse — `21000 ON CONFLICT DO UPDATE command cannot affect
    row a second time`. Tout l'import échoue, pas seulement la ligne fautive.

    Comment on traite chaque doublon :
    - lignes rigoureusement identiques (seule la `reference` diffère, car elle
      vient de l'identifiant v1) : c'est une double saisie, on en garde une.
    - lignes qui divergent sur `frais` ou `devise` : on garde la première et on
      le dit haut et fort, parce que ce n'est plus une simple double saisie et
      que choisir à votre place serait inventer une donnée.

    `fusionner=True` (transactions seulement) change le traitement du cas
    « même titre, même jour, même cours, même quantité, frais différents ». Ce
    n'est pas une double saisie : ce sont deux achats distincts. Jeter la seconde
    ligne perdait la moitié de la position — 178 unités de XJSE.SW sont parties
    comme ça. Les fusionner est exact, pas inventé : deux achats de 89 unités à
    1 115,60 avec 148 € puis 1 149 € de frais donnent le même PRU qu'un achat de
    178 unités pour 1 297 € de frais. On additionne la quantité et les frais, on
    garde les deux références, et on le dit.

    `fusionner` reste False pour `pf2_apports`, où la clé inclut déjà le montant :
    deux lignes qui la partagent sont de vrais doublons, les additionner
    gonflerait vos apports de capital.

    Retourne `(lignes_dédoublonnées, messages)`.

    `cle` désigne les champs qui définissent l'identité d'une ligne. Pour
    `pf2_transactions`, c'est la clé de l'index unique. Pour `pf2_apports`, qui
    n'a aucun index unique, c'est nous qui la fixons — sans quoi un doublon
    serait inséré sans bruit et gonflerait vos apports de capital.
    """
    CLE = cle
    vues: dict[tuple, dict] = {}
    messages: list[str] = []
    resultat: list[dict] = []

    for ligne in lignes:
        cle_ligne = tuple(ligne.get(c) for c in CLE)
        if cle_ligne not in vues:
            vues[cle_ligne] = ligne
            resultat.append(ligne)
            continue

        premiere = vues[cle_ligne]
        # Champs dont la divergence rend la ligne irréciliable : on ne peut pas
        # décider à la place de l'utilisateur.
        divergentes = [
            champ
            for champ in ("devise", "montant_eur")
            if champ in ligne and champ in premiere
            and ligne.get(champ) != premiere.get(champ)
        ]
        # `frais` est à part : c'est le seul champ réconciliable par fusion.
        frais_divergents = (
            "frais" in ligne and "frais" in premiere
            and ligne.get("frais") != premiere.get("frais")
        )
        etiquette = ligne.get("ticker") or ligne.get("compte") or "?"

        if divergentes:
            # Vraiment ambigu : on ne choisit pas à la place de l'utilisateur.
            messages.append(
                f"{etiquette} {ligne.get('date')} : DOUBLON À DONNÉES "
                f"DIVERGENTES ({', '.join(divergentes)}) — "
                f"{premiere.get('reference')} conservé, "
                f"{ligne.get('reference')} écarté. "
                f"À VÉRIFIER DANS VOTRE RELEVÉ."
            )
            continue

        if fusionner and frais_divergents:
            # Seul cas où la fusion est légitime : les frais diffèrent. Deux
            # ordres passés le même jour, au même cours, pour la même quantité,
            # avec des frais différents, sont nécessairement deux achats
            # distincts — et leur somme est exacte.
            #
            # En revanche, des frais IDENTIQUES laissent l'ambiguïté ouverte :
            # c'est peut-être une double saisie (le cas IGLN.L déjà examiné).
            # Là on garde une ligne et on le dit, comme avant.
            quantite_totale = float(premiere["quantite"]) + float(ligne["quantite"])
            frais_totaux = round(
                float(premiere.get("frais") or 0.0) + float(ligne.get("frais") or 0.0), 6
            )
            frais_avant = premiere.get("frais")
            refs = [premiere.get("reference"), ligne.get("reference")]
            # Copie, surtout pas de mutation : `lignes` appartient à l'appelant.
            fusion = {
                **premiere,
                "quantite": quantite_totale,
                "frais": frais_totaux,
                "reference": "+".join(r for r in refs if r),
            }
            vues[cle_ligne] = fusion
            resultat[resultat.index(premiere)] = fusion
            messages.append(
                f"{etiquette} {ligne.get('date')} : deux achats le même jour au "
                f"même cours ({refs[0]} et {refs[1]}), frais différents "
                f"({frais_avant} puis {ligne.get('frais')}). "
                f"Fusionnés en une ligne de {quantite_totale:g} unités pour "
                f"{frais_totaux:g} de frais — le PRU est inchangé. "
                f"À VÉRIFIER DANS VOTRE RELEVÉ."
            )
            continue

        if frais_divergents:
            # Fusion non demandée (apports) : on signale sans toucher aux données.
            messages.append(
                f"{etiquette} {ligne.get('date')} : DOUBLON À DONNÉES "
                f"DIVERGENTES (frais) — "
                f"{premiere.get('reference')} conservé, "
                f"{ligne.get('reference')} écarté. "
                f"À VÉRIFIER DANS VOTRE RELEVÉ."
            )
            continue

        messages.append(
            f"{etiquette} {ligne.get('date')} : ligne en double dans "
            f"la v1 ({premiere.get('reference')} et "
            f"{ligne.get('reference')}) — une seule conservée."
        )

    return resultat, messages


def importer_transactions(dry_run: bool = False) -> tuple[int, list[str]]:
    """Importe les transactions v1, en normalisant les devises."""
    df = lire_v1(V1_TRANSACTIONS)
    if df.empty:
        log.info("Aucune transaction à importer.")
        return 0, []

    corrections: list[str] = []
    lignes: list[dict] = []

    for _, r in df.iterrows():
        ticker = str(r["Ticker"]).upper().strip()
        typ = str(r["Type"]).strip().lower()
        sens = "achat" if "achat" in typ else "vente" if "vente" in typ else None
        if sens is None:
            corrections.append(f"{ticker} {r['Date']} : type illisible ({r['Type']!r}), ignoré")
            continue

        devise_saisie = str(r.get("Devise", "") or "").upper().strip()
        row_date = r["Date"]

        quantite = float(r["Quantité"])
        cours = float(r["Cours"])
        frais = float(r["Frais"]) if pd.notna(r.get("Frais")) else 0.0

        # --- Devise : la saisie de l'utilisateur fait autorité -----------------
        # `DEVISES_COTATION` ne connaît qu'une dizaine de tickers et renvoyait
        # "USD" par défaut. Conséquence : tout titre européen absent de la table
        # (ASML, MC.PA, SAP.DE...) était réétiqueté en USD, avec un cours en
        # euro lu comme un cours en dollar. La valorisation de la ligne était
        # alors fausse, et l'allocation avec elle.
        #
        # La règle est donc : ce que vous avez saisi dans la v1 est la vérité —
        # c'est ce que dit votre relevé. La table ne sert que de repli quand la
        # v1 a laissé la devise vide. Et si on ne sait pas, on n'invente pas :
        # la ligne est écartée et signalée, plutôt qu'importée avec une devise
        # devinée.
        devise_connue = DEVISES_COTATION.get(ticker)
        if devise_saisie:
            devise = devise_saisie
            if devise_connue and devise_connue != devise_saisie:
                # Incohérence réelle (cas XJSE.SW dans la v1). On NE reconvertit
                # PAS le cours : on n'a pas de taux fiable dans la ligne, et un
                # taux inventé fausserait le PRU. On importe tel quel et on
                # signale, pour que vous tranchiez sur votre relevé.
                corrections.append(
                    f"{ticker} {r['Date']} : saisi en {devise_saisie}, coté "
                    f"habituellement en {devise_connue} — importé TEL QUEL, "
                    f"cours {cours} non converti, À VÉRIFIER DANS VOTRE RELEVÉ"
                )
                log.warning(
                    "  %s %s : devise saisie %s, cotation habituelle %s — "
                    "aucune conversion appliquée, ligne à vérifier.",
                    ticker, r["Date"], devise_saisie, devise_connue,
                )
        elif devise_connue:
            devise = devise_connue
        else:
            corrections.append(
                f"{ticker} {r['Date']} : devise absente dans la v1 et ticker "
                f"inconnu de la table de cotation — ligne NON importée. "
                f"Ajoutez {ticker} à DEVISES_COTATION ou ressaisissez la ligne."
            )
            log.error(
                "  %s %s : devise indéterminable, ligne écartée.", ticker, r["Date"]
            )
            continue

        d = parser(row_date)
        if pd.isna(d):
            corrections.append(f"{ticker} : date illisible ({r['Date']!r}), ignoré")
            continue

        # `montant_net` n'est PAS stocké : c'est une valeur dérivée
        # (quantite x cours, frais inclus), recalculée à la lecture par
        # `charger_transactions()`. La table pf2_transactions n'a pas cette
        # colonne, et l'envoyer faisait échouer l'import avec
        # `PGRST204: Could not find the 'montant_net' column`.
        lignes.append({
            "ticker": ticker,
            "sens": sens,
            "date": d.date().isoformat(),
            "quantite": quantite,
            "cours": cours,
            "frais": frais,
            "devise": devise,
            "source": "import_v1",
            "reference": f"v1:id{r.get('id')}",
        })

    # --- Dédoublonnage --------------------------------------------------------
    # La clé de conflit de `pf2_transactions` est (ticker, sens, date, quantite,
    # cours). Si le lot envoyé contient deux lignes qui partagent cette clé,
    # PostgreSQL refuse tout le lot :
    #
    #     21000  ON CONFLICT DO UPDATE command cannot affect row a second time
    #
    # C'est ce qui arrive quand la v1 contient une transaction saisie deux fois.
    # On ne peut pas « laisser faire » : l'import entier échoue. On ne peut pas
    # non plus fusionner silencieusement deux lignes qui diffèrent — ce serait
    # inventer une donnée. On conserve la première, on signale, et c'est à vous
    # de trancher sur votre relevé.
    lignes, doublons = _dedupliquer(lignes, fusionner=True)
    corrections.extend(doublons)

    if corrections:
        log.warning("%d ligne(s) à vérifier :", len(corrections))
        for c in corrections:
            log.warning("  - %s", c)

    if dry_run:
        log.info("[dry-run] %d transactions seraient importées.", len(lignes))
        return len(lignes), corrections

    if not lignes:
        # Rien à écrire : on n'envoie pas une requête vide à PostgREST.
        log.warning("Aucune transaction importable.")
        return 0, corrections

    try:
        # Purge préalable des lignes issues de l'import.
        #
        # Pourquoi c'est nécessaire : la clé de conflit de l'upsert contient la
        # DATE. Or l'importation précédente a stocké des dates MAL LUES — le
        # parseur d'alors intervertissait jour et mois sur les ISO
        # (`2025-07-01` devenait `2025-01-07`), ce qui a décalé 52 % des lignes.
        # Relancer l'import avec le parseur corrigé ne peut donc PAS écraser les
        # anciennes : la clé diffère, et chaque transaction se retrouverait en
        # double, une fois à la mauvaise date.
        #
        # On ne supprime que ce que l'import a lui-même écrit. Une transaction
        # saisie à la main dans l'application n'est pas touchée.
        db.client().table(db.T_TRANSACTIONS).delete().eq(
            "source", "import_v1"
        ).execute()
        log.info("Anciennes lignes d'import supprimées.")

        # Upsert sur l'index unique : relancer l'import ne crée pas de doublons.
        db.remplacer(
            db.T_TRANSACTIONS, lignes,
            on_conflict="ticker,sens,date,quantite,cours",
        )
        log.info("%d transactions importées.", len(lignes))
    except Exception as exc:
        log.error("Import des transactions échoué : %s", exc)
        raise
    return len(lignes), corrections


def importer_apports(dry_run: bool = False) -> int:
    """Importe les apports de fonds propres de la v1."""
    df = lire_v1(V1_HISTORIQUE)
    if df.empty:
        log.info("Aucun apport à importer.")
        return 0

    lignes = []
    for _, r in df.iterrows():
        typ = str(r.get("Type", "")).lower()
        sens = "apport" if "ajout" in typ else "retrait" if "retrait" in typ else None
        if sens is None:
            continue

        d = parser(r["Date"])
        if pd.isna(d):
            continue

        montant_eur = float(r["Montant €"]) if pd.notna(r.get("Montant €")) else 0.0
        montant_or = float(r["Montant Or"]) if pd.notna(r.get("Montant Or")) else None
        cours_or = None
        if montant_or and montant_or > 0:
            montant_usd = float(r["Montant $"]) if pd.notna(r.get("Montant $")) else 0.0
            cours_or = round(montant_usd / montant_or, 2)

        lignes.append({
            "date": d.date().isoformat(),
            "sens": sens,
            "montant_eur": round(montant_eur, 2),
            "montant_or": round(montant_or, 6) if montant_or else None,
            "cours_or": cours_or,
            "compte": "import_v1",
            "reference": f"v1:id{r.get('id')}",
        })

    # Meme traitement que pour les transactions : `pf2_apports` n'a aucun index
    # unique, donc un doublon de la v1 serait insere sans bruit — et viendrait
    # gonfler vos apports de capital, donc fausser la performance.
    lignes, doublons_apports = _dedupliquer(
        lignes, cle=("date", "sens", "montant_eur", "compte")
    )
    for d in doublons_apports:
        log.warning("  - %s", d)

    if dry_run:
        log.info("[dry-run] %d apports seraient importés.", len(lignes))
        return len(lignes)

    # Idempotence : on repart d'une base propre pour les lignes issues de la v1.
    # La table pf2_apports n'a pas de contrainte d'unicité exploitable en upsert.
    try:
        db.client().table(db.T_APPORTS).delete().eq("compte", "import_v1").execute()
        db.ecrire(db.T_APPORTS, lignes)
        log.info("%d apports importés.", len(lignes))
    except Exception as exc:
        log.error("Import des apports échoué : %s", exc)
        raise
    return len(lignes)



# ---------------------------------------------------------------------------
# Historique de valorisation de la v1 -> pf2_snapshots
# ---------------------------------------------------------------------------
# LA TABLE, ENFIN NOMMEE : `Projections`.
#
# Elle a ete cherchee pendant trois rounds. `Historique` n'en portait que la
# tresorerie ; les vingt-deux autres noms essayes n'existaient pas, et la racine
# PostgREST repondait 401 — l'inventaire des tables n'est pas accessible avec la
# cle publique. La liste est venue du tableau de bord Supabase, et le nom etait
# dans le fichier depuis le debut : 180 lignes, du 01/04/2023 au 01/10/2026,
# exactement la periode demandee.
#
# Verification faite, pas supposee : au 01/10/2026, la somme des cinq positions
# de `Donnees` (IGLN 140, XDW0 334, XJSE 2 255, FLXC 800, BTC 0,05747) vaut
# 79 394,14 — et `Actifs Stratégiques` affiche 79 394,14. `Total Global` vaut
# 92 089,39, soit ces cinq positions plus la tresorerie et RI.PA. La colonne est
# donc bien le perimetre investi, et l'autre bien le patrimoine entier.
#
# ELLES SONT EN DOLLARS. C'est le piege de cette table.
#
# Deux mesures le prouvent. D'abord les cours : `Donnees` affiche IGLN.L a
# 79,96 et Yahoo cote IGLN.L 80,865 — la cote brute en USD de la place de
# Londres, pas une conversion. Ensuite le compteur d'apports : `Total_Apports_
# nets` passe de 58 378,92 a 58 625,92 le 05/06/2026, soit +247,00 — le
# `Montant $` de la ligne, quand la colonne `Montant €` dit 212,42.
#
# D'ou la conversion, faite ligne par ligne avec le taux reel du jour
# (`core.fx`). Sans elle l'historique entrerait 12 % trop haut en niveau et,
# plus grave, la performance melangerait le rendement des actifs et la variation
# de l'euro — deux choses distinctes, et c'est le rendement en euros qui vous
# concerne.
#
# Un jour sans taux de change disponible : la ligne est ECARTEE et signalee.
# On ne comble pas un trou par une valeur plausible, on le montre.
#
# Ce qui N'est PAS importe : les colonnes de performance (`Score TWR %`,
# `Evolution cumulee %`, `Capital investi`). pf2 calcule le TWR a la demande
# depuis les snapshots, il ne le stocke jamais. C'est le principe meme de
# `jobs/daily_snapshot.py` : une seule source de verite, impossible a
# desynchroniser.
ALIAS_SNAPSHOT = {
    "Date": "date",
    "Actifs Stratégiques": "patrimoine_investi_eur",
    "Actifs Strategiques": "patrimoine_investi_eur",
    "Total Global": "patrimoine_total_eur",
}


def _nombre(valeur) -> float | None:
    try:
        if valeur is None or pd.isna(valeur):
            return None
        return float(valeur)
    except (TypeError, ValueError):
        return None


def _taux_usd_eur(jour: str) -> float | None:
    """Le taux USD -> EUR au jour dit, ou `None` s'il est introuvable.

    Le `None` est un resultat, pas une erreur : il signifie « cette ligne ne
    peut pas etre convertie ». L'appelant ecarte la ligne et le dit. Rendre 1.0
    serait la pire des reponses — elle entrerait dans la base sans bruit, avec
    un facteur 1,13 d'erreur et aucun signe visible.
    """
    try:
        t = float(fx.taux("USD", jour, "EUR"))
    except Exception:
        return None
    return t if t > 0 else None


def _purger_la_fenetre(debut: str, fin: str, conservees: set[str],
                       dry_run: bool = False, taille: int = 50) -> int:
    """Retire de `pf2_snapshots` les lignes que l'historique de la v1 remplace.

    POURQUOI CE N'EST PAS UN SIMPLE IMPORT
    --------------------------------------
    Le robot de la v2 ecrit un snapshot par nuit depuis le 18/03/2025. Sa serie
    porte un trou : avant le 02/02/2026 elle vaut environ 9 900 EUR de moins que
    la v1 — mesure faite, au 31/01/2026, en convertissant `Actifs Stratégiques`
    au taux EUR/USD reel du jour (78 416 USD = 65 534 EUR, quand la v2 disait
    55 640 EUR). Ce trou est la vraie cause du saut du 02/02 et du +26,2 %
    affiche pour 2026.

    Depuis fin fevrier 2026 les deux series concordent : 67 685 EUR contre
    67 772 EUR au 28/02 soit 0,13 %, et 70 470 contre 69 795 au 01/10 soit
    0,97 %. Le desaccord est localise avant, pas apres.

    Melanger les deux donnerait une scie : des lignes au bon niveau et des
    lignes 9 900 EUR trop basses, sur la meme courbe. Le TWR, qui multiplie des
    rendements journaliers, lirait chaque passage d'une serie a l'autre comme un
    faux mouvement — exactement le defaut qu'on passe ce temps a faire
    disparaitre. On garde donc la serie de l'utilisateur, qui commence en avril
    2023 et va jusqu'au 01/10/2026.

    POURQUOI APRES L'ECRITURE
    -------------------------
    Un echec laisse des lignes en trop, visibles, que la prochaine execution
    retirera. L'ordre inverse laisserait un trou dans l'historique. Entre une
    ligne de trop et une ligne manquante, le choix ne se discute pas.

    Rien n'est silencieux : le decompte et les plus gros ecarts sont journalises.
    """
    try:
        existants = db.lire(db.T_SNAPSHOTS)
    except Exception as exc:
        log.error("Lecture des snapshots impossible, rien n'est purgé : %s", exc)
        return 0

    if existants.empty or "date" not in existants.columns:
        return 0

    dedans = existants[
        (existants["date"] >= debut) & (existants["date"] <= fin)
    ]
    a_retirer = sorted(set(dedans["date"].astype(str)) - conservees)
    if not a_retirer:
        log.info("Aucune ligne à retirer : la fenêtre est déjà propre.")
        return 0

    # Les plus gros ecarts avec la v1 : c'est ce qui merite d'etre montre, pas
    # un decompte anonyme.
    if "patrimoine_investi_eur" in existants.columns and not dry_run:
        gardees = existants[existants["date"].astype(str).isin(conservees)]
        if not gardees.empty:
            # Le libellé dit « dans la fenêtre » et non « la v2 en avait » :
            # `dedans` compte les lignes de la FENÊTRE, et la série de la v1 peut
            # commencer avant elle. Le premier lancement réel annonçait
            # « la v2 en avait 571 » alors que la v2 en avait 563 — les 8 de
            # trop étaient des lignes de la v1 dans la fenêtre. Le chiffre était
            # juste, le mot était faux, et un mot faux dans un journal de
            # migration coûte une heure d'enquête.
            log.info(
                "Fenêtre %s -> %s : %d ligne(s) retirée(s) sur les %d que "
                "comptait la fenêtre ; %d conservée(s).",
                debut, fin, len(a_retirer), len(dedans), len(conservees & set(
                    dedans["date"].astype(str))),
            )

    if dry_run:
        log.info(
            "[dry-run] %d ligne(s) seraient retirées de la fenêtre "
            "(%s -> %s). Exemples : %s",
            len(a_retirer), debut, fin, ", ".join(a_retirer[:5]),
        )
        return len(a_retirer)

    table = db.client().table(db.T_SNAPSHOTS)
    retires = 0
    for i in range(0, len(a_retirer), taille):
        lot = a_retirer[i:i + taille]
        try:
            table.delete().in_("date", lot).execute()
            retires += len(lot)
        except Exception as exc:
            log.error(
                "Purge interrompue (%d lignes retirees) : %s. Relancez l'import : "
                "il est idempotent.", retires, exc,
            )
            raise
    log.info("%d ligne(s) de la v2 retirées de la fenêtre.", retires)
    return retires


def importer_snapshots(dry_run: bool = False) -> int:
    """Importe l'historique de valorisation de la v1 (`Projections`).

    Les montants de la v1 sont en dollars : ils sont convertis en euros avec le
    taux reel de chaque date. La table remplace la serie du robot sur toute la
    periode qu'elle couvre — voyez `_purger_la_fenetre` pour le pourquoi.
    """
    df = lire_v1(V1_PROJECTIONS)
    if df.empty:
        log.info("Aucune ligne dans %s. Rien a importer.", V1_PROJECTIONS)
        return 0

    # `Projections` ne porte que des valorisations : pas de colonne `Type`, donc
    # pas de ligne de tresorerie a filtrer. Le filtre qui vivait ici servait a
    # `Historique`, ou les deux cohabitaient. Il est retire parce qu'il ne
    # protegeait plus de rien, pas parce qu'il genait.
    sans_taux: list[str] = []
    lignes = []
    for _, r in df.iterrows():
        d = parser(r.get("Date"))
        if pd.isna(d):
            continue
        date_iso = d.date().isoformat()

        investi_usd = _nombre(r.get("Actifs Stratégiques"))
        total_usd = _nombre(r.get("Total Global"))
        if investi_usd is None and total_usd is None:
            continue
        if investi_usd is None:
            investi_usd = total_usd
        if total_usd is None:
            total_usd = investi_usd
        if investi_usd is None or investi_usd <= 0:
            continue

        taux = _taux_usd_eur(date_iso)
        if taux is None:
            sans_taux.append(date_iso)
            continue

        investi = investi_usd * taux
        total = total_usd * taux

        ligne = {
            "date": date_iso,
            "patrimoine_total_eur": round(total, 2),
            "patrimoine_investi_eur": round(investi, 2),
            # La v1 ne distingue pas epargne de precaution et compte courant :
            # tout l'ecart va dans la precaution, et on le dit.
            "precaution_eur": round(max(total - investi, 0.0), 2),
            "courant_eur": 0.0,
            # La repartition par poche n'existe pas dans la v1 : on laisse NULL
            # plutot que d'inventer. La courbe d'allocation ignorera ces lignes.
        }
        lignes.append(ligne)

    if sans_taux:
        log.warning(
            "%d ligne(s) écartée(s), taux USD/EUR introuvable : %s",
            len(sans_taux), ", ".join(sans_taux[:10]),
        )
    if not lignes:
        log.info("Aucune valorisation exploitable dans %s.", V1_PROJECTIONS)
        return 0

    # TRI PAR DATE. Ce n'est pas cosmétique : la fenêtre de purge est calculée
    # sur `lignes[0]` et `lignes[-1]`, et PostgREST ne rend PAS les lignes dans
    # l'ordre des dates. Sur le premier lancement réel, la première ligne rendue
    # portait le 30/07/2024 alors que la plus ancienne de la table est le
    # 01/04/2023 : la fenêtre annoncée partait donc dix-huit mois trop tard.
    # Sans conséquence ce jour-là — le robot n'a rien écrit avant mars 2025 —
    # mais c'était de la chance, pas de la correction. Un tri, et la question
    # ne se pose plus.
    lignes.sort(key=lambda l: l["date"])

    # `pf2_snapshots` a une contrainte d'unicite sur `date`, mais deux lignes de
    # meme date dans le lot la feraient echouer quand meme. On deduplique, et
    # on DIT ce qu'on ecarte : la v1 peut porter deux valuations le meme jour
    # (une saisie, puis une re-saisie), et choisir a votre place serait inventer.
    lignes, doublons = _dedupliquer(lignes, cle=("date",), fusionner=False)
    for d in doublons:
        log.warning("  - %s", d)

    if dry_run:
        log.info(
            "[dry-run] %d snapshots seraient importés (%s -> %s).",
            len(lignes), lignes[0]["date"], lignes[-1]["date"],
        )
        _purger_la_fenetre(lignes[0]["date"], lignes[-1]["date"],
                           {l["date"] for l in lignes}, dry_run=True)
        return len(lignes)

    # Idempotence : `pf2_snapshots` a une contrainte d'unicite sur `date`, donc
    # l'upsert met a jour au lieu de dupliquer. Relancer l'import est sans risque.
    # --- Equivalent-or de chaque valuation ---------------------------------
    # La v1 ne l'a pas, et sans lui la seule courbe qui compte pour Gave — le
    # portefeuille en onces d'or — reste vide sur trois ans et demi. On la
    # calcule depuis le cours reel de l'or et le taux de change reel du jour,
    # exactement comme `jobs/daily_snapshot.py`.
    #
    # Ce n'est PAS une invention : c'est la meme formule, appliquee a des prix
    # historiques veritables. Un jour sans prix disponible est signale, et sa
    # ligne reste sans equivalent-or plutot que de recevoir un zero.
    from core import fx as _fx
    from core import prices as _prices

    or_manquant: list[str] = []
    for ligne in lignes:
        try:
            cours_or = _prices.cours_or(ligne["date"])
            taux_usd = _fx.taux("EUR", ligne["date"], "USD")
            ligne["cours_or_usd"] = round(cours_or, 2)
            ligne["equivalent_or_oz"] = round(
                ligne["patrimoine_investi_eur"] * taux_usd / cours_or, 4
            )
        except Exception as exc:
            or_manquant.append(ligne["date"])
            log.warning("  - equivalent-or indisponible le %s (%s)", ligne["date"], exc)

    ecrits = 0
    for ligne in lignes:
        try:
            db.ajouter_snapshot(ligne)
            ecrits += 1
        except Exception as exc:
            log.error("%s : écriture échouée (%s)", ligne["date"], exc)
            raise
    log.info(
        "%d snapshots importés (%s -> %s).",
        ecrits, lignes[0]["date"], lignes[-1]["date"],
    )

    _purger_la_fenetre(lignes[0]["date"], lignes[-1]["date"],
                       {l["date"] for l in lignes}, dry_run=dry_run)

    if or_manquant:
        log.warning(
            "%d date(s) sans equivalent-or : %s. La performance en or ne sera "
            "pas calculable sur toute la période — c'est honnête, mieux vaut "
            "qu'un prix inventé.",
            len(or_manquant), ", ".join(or_manquant[:10]),
        )
    return ecrits


def main() -> int:
    ap = argparse.ArgumentParser(description="Import des données de la v1 vers la v2.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Simule l'import sans rien écrire.")
    args = ap.parse_args()

    manquantes = [t for t, ok in db.tables_presentes().items() if not ok]
    if manquantes:
        log.error("Tables v2 absentes : %s. Exécutez migrations/001_init.sql.", manquantes)
        return 1

    for table in (V1_TRANSACTIONS, V1_HISTORIQUE, V1_PROJECTIONS):
        if not db.existe(table):
            log.error(
                "Table v1 '%s' introuvable. Vérifiez le nom exact dans "
                "Supabase → Table Editor.", table,
            )
            return 1

    # Contrôle d'écriture AVANT de traiter quoi que ce soit. Une table
    # verrouillée par RLS se comporte comme une table vide en lecture : sans
    # cette sonde, l'échec n'apparaîtrait qu'au premier INSERT.
    try:
        db.verifier_ecriture()
    except PermissionError as exc:
        log.error("%s", exc)
        return 1

    log.info("=== Import des transactions ===")
    importer_transactions(dry_run=args.dry_run)
    log.info("=== Import des apports ===")
    importer_apports(dry_run=args.dry_run)
    log.info("=== Import de l'historique de valorisation (Projections) ===")
    importer_snapshots(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
