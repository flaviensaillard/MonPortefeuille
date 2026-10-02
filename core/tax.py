"""Moteur fiscal français.

CORRECTIONS PAR RAPPORT À LA V1
-------------------------------
1. **Les barèmes n'étaient pas indexés par année de cession.** Une seule série
   était lue dans la config et appliquée à toutes les plus-values. Ici, chaque
   calcul prend l'année d'imposition, et le barème correspondant.

2. **L'or ETC et l'or physique étaient confondus.** Tout ce qui n'était pas du
   cash ou de la crypto partait au régime des valeurs mobilières (150-0 A, donc
   12,8 % + 17,2 %). C'est exact pour un ETC comme IGLN.L, mais l'Université de
   l'Épargne propose aussi l'or physique, qui relève de l'article 150 VI avec un
   régime très différent. Le moteur sait désormais faire la distinction.

3. **L'abattement de 305 € sur la plus-value crypto manquait**, et les
   prélèvements sociaux n'étaient pas isolés.

4. **La comparaison PFU / barème était incomplète** : pas de CSG déductible
   (6,8 %), pas d'abattement pour durée, et l'affichage final abdiquait
   (« À calculer ») en renvoyant la main à l'utilisateur.

AVERTISSEMENT
-------------
Les taux et seuils ci-dessous proviennent de sources publiques concordantes.
Seule la documentation administrative (BOFiP) fait foi. Les points marqués
« À VÉRIFIER » doivent être recoupés avant toute déclaration. Ce module produit
une estimation, pas une déclaration fiscale.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import fiscal_bars as fb
from .models import Classe

if TYPE_CHECKING:                     # annotations seulement : pas d'import à l'exécution
    from .portfolio import Position, Transaction


# ===========================================================================
# Impôt sur le revenu
# ===========================================================================

@dataclass
class ResultatIR:
    impot_brut: float
    decote: float
    impot_net: float
    tmi: float
    bareme_annee: int


def _impot_par_part(revenu_par_part: float, bareme: fb.Bareme | None = None) -> float:
    """Impôt sur un revenu par part, sans décote.

    `bareme.tranches` contient les plafonds des tranches 1 à 4 ; `bareme.taux`
    contient les taux des tranches 2 à 5. La première tranche est à 0 % : il faut
    donc préfixer les taux d'un zéro, sans quoi la première tranche est taxée au
    taux de la deuxième.
    """
    if bareme is None:
        bareme = fb.BAREMES[max(fb.BAREMES)]
    bornes = (0.0,) + bareme.tranches
    taux = (0.0,) + bareme.taux
    impot = 0.0
    for i, t in enumerate(taux):
        bas = bornes[i]
        haut = bornes[i + 1] if i + 1 < len(bornes) else float("inf")
        if revenu_par_part > bas:
            impot += (min(revenu_par_part, haut) - bas) * t
    return impot


def _quotient_plafonne(revenu: float, parts: float, parts_couple: float = 2.0) -> float:
    """Plafonne le gain procuré par les parts au-delà de 2.

    Règle : l'avantage en impôt procuré par chaque demi-part supplémentaire est
    plafonné. Sans ce plafond, un foyer à 3 parts est sur-avantagé.
    À VÉRIFIER : le plafond exact pour 2026.
    """
    if parts <= parts_couple:
        return revenu / parts
    demi_parts = (parts - parts_couple) * 2
    plafond_par_demi_part = 1_759.0
    gain_max = demi_parts * plafond_par_demi_part / 2.0

    impot_sans = _impot_par_part(revenu / parts_couple) * parts_couple
    impot_avec = _impot_par_part(revenu / parts) * parts
    gain = impot_sans - impot_avec
    if gain <= gain_max:
        return revenu / parts

    impot_cible = impot_sans - gain_max
    if impot_cible <= 0:
        return revenu / parts
    bas, haut = revenu / parts_couple, revenu
    for _ in range(80):
        milieu = (bas + haut) / 2
        if _impot_par_part(milieu) * parts_couple < impot_cible:
            bas = milieu
        else:
            haut = milieu
    return (bas + haut) / 2


def _est_couple(statut: str) -> bool:
    s = statut.lower()
    return "mari" in s or "pacs" in s


def impot_revenu(
    revenu: float,
    parts: float,
    annee: int,
    statut: str = "Célibataire",
    avec_decote: bool = True,
) -> ResultatIR:
    """Impôt sur le revenu selon le barème de `annee`.

    `parts` = nombre de parts fiscales (2 pour un couple, +0,5 puis +0,5 puis +1
    par enfant à partir du troisième).
    """
    bareme = fb.bareme_de(annee)
    revenu = max(0.0, float(revenu))
    parts = max(0.5, float(parts))
    couple = _est_couple(statut)

    if couple:
        qf = _quotient_plafonne(revenu, parts, parts_couple=2.0)
        impot = _impot_par_part(qf, bareme) * 2.0
    else:
        qf = revenu / parts
        impot = _impot_par_part(qf, bareme) * parts

    tmi = 0.0
    for i, plafond in enumerate(bareme.tranches):
        if qf > plafond:
            tmi = bareme.taux[i]
        else:
            tmi = bareme.taux[max(0, i - 1)] if i > 0 else 0.0
            break
    else:
        tmi = bareme.taux[-1]

    decote = 0.0
    if avec_decote:
        if couple:
            base, plafond = bareme.decote_base_couple, bareme.decote_plafond_couple
        else:
            base, plafond = bareme.decote_base_celibataire, bareme.decote_plafond_celibataire
        if impot <= plafond:
            decote = max(0.0, base - impot * bareme.decote_taux)
            decote = min(decote, impot)

    net = impot - decote
    # Seuil de non-recouvrement : en dessous de 61 €, l'impôt n'est pas prélevé.
    if net < 61.0:
        net = 0.0

    return ResultatIR(impot_brut=impot, decote=decote, impot_net=net, tmi=tmi, bareme_annee=annee)


# ===========================================================================
# Régime 150-0 A — valeurs mobilières (actions, ETF, ETC)
# ===========================================================================

@dataclass
class PlusValue:
    actif: str
    date_cession: dt.date
    quantite: float
    pru_eur: float
    prix_cession_eur: float
    plus_value_eur: float
    regime: str


@dataclass
class ResultatFiscal:
    regime: str
    annee: int
    plus_value_brute: float
    abattement: float
    plus_value_imposable: float
    impot_ir: float
    prelevements_sociaux: float
    total_du: float
    detail: list[PlusValue] = field(default_factory=list)
    # Renseignes uniquement pour l'article 150 VH bis : le seuil de 305 EUR
    # porte sur les prix de cession, pas sur le gain.
    total_cessions: float | None = None
    exonere_par_franchise: bool | None = None

    @property
    def taux_effectif(self) -> float:
        if self.plus_value_brute <= 0:
            return 0.0
        return self.total_du / self.plus_value_brute


def pv_titres(lignes: list[dict], annee: int) -> ResultatFiscal:
    """Plus-values de valeurs mobilières au régime 150-0 A.

    `lignes` : dicts avec `actif`, `date`, `quantite`, `pru_eur`,
    `prix_cession_eur`.

    L'abattement pour durée de détention ne s'applique qu'aux acquisitions
    antérieures au 1er janvier 2018 et aux actions de certaines sociétés. Il est
    laissé à 0 par défaut et doit être renseigné si le titre est concerné —
    c'est ce sur quoi la v1 renonçait (« À calculer »).
    """
    details: list[PlusValue] = []
    for l in lignes:
        d = l["date"]
        d = d.date() if isinstance(d, dt.datetime) else d
        if d.year != annee:
            continue
        pv = l["prix_cession_eur"] - l["pru_eur"] * l["quantite"]
        details.append(PlusValue(
            actif=l["actif"], date_cession=d, quantite=l["quantite"],
            pru_eur=l["pru_eur"], prix_cession_eur=l["prix_cession_eur"],
            plus_value_eur=pv, regime="150-0 A",
        ))

    pv_brute = sum(x.plus_value_eur for x in details)
    pv_nette = max(0.0, pv_brute)

    # DÉFAUT CORRIGÉ — les deux composantes étaient fausses.
    # `fb.PFU_TAUX - fb.PRELEVEMENTS_SOCIAUX` donnait 0,308 - 0,172 = 0,136,
    # soit 13,6 % d'IR au lieu de 12,8 % : un taux sorti d'une soustraction,
    # jamais d'un texte. Et 17,2 % de PS ne valent plus pour 2026 (18,6 %).
    ir = pv_nette * fb.IR_FORFAITAIRE
    ps = pv_nette * fb.taux_ps(annee)

    return ResultatFiscal(
        regime="150-0 A", annee=annee, plus_value_brute=pv_brute, abattement=0.0,
        plus_value_imposable=pv_nette, impot_ir=ir, prelevements_sociaux=ps,
        total_du=ir + ps, detail=details,
    )


# ===========================================================================
# Régime 150 VH bis — actifs numériques (crypto), calcul global
# ===========================================================================

def pv_crypto(lignes: list[dict], annee: int,
              anomalies: list[str] | None = None) -> ResultatFiscal:
    """Plus-values d'actifs numériques, méthode du calcul global.

    Formulaires 2086-SD. Pour chaque cession de l'année :
      - ligne 211 : date de cession
      - ligne 212 : valeur globale du portefeuille de biens numériques au moment
                    de la cession
      - ligne 213 : prix de cession
      - ligne 220 : prix total d'acquisition du portefeuille
      - ligne 221 : fractions du prix d'acquisition déjà prises en compte
      - ligne 223 : fraction du capital correspondant (220-221) × 213/212
      - ligne 224 : plus-value = 213 - 223

    La plus-value globale annuelle est ensuite réduite d'un abattement de 305 €.

    CORRECTION : la v1 implémentait bien la mécanique des lignes, mais oubliait
    l'abattement de 305 € et n'appliquait aucun taux.
    """
    details: list[PlusValue] = []
    pv_globale = 0.0

    for l in lignes:
        d = l["date"]
        d = d.date() if isinstance(d, dt.datetime) else d
        if d.year != annee or l.get("sens") != "vente":
            continue

        prix_cession = float(l["prix_cession_eur"])
        valeur_globale = float(l.get("valeur_globale_eur", 0.0))
        # Garde-fou explicite : la v1 forçait valeur_globale = prix_cession quand
        # le calcul donnait moins, ce qui masquait les prix historiques manquants.
        #
        # Lever une exception ici faisait sauter toute la page Fiscalité pour une
        # seule cession. On garde la même exigence — pas de chiffre approximatif —
        # mais on la consigne dans `anomalies` quand l'appelant en fournit une,
        # comme pour `calculer_positions`. Sans liste, on lève toujours : les
        # robots, eux, doivent s'arrêter.
        if valeur_globale <= 0:
            message = (
                f"Valeur globale du portefeuille crypto indisponible au {d}. "
                "Impossible de calculer la fraction du capital. "
                "Renseignez le prix de chaque actif détenu à cette date."
            )
            if anomalies is None:
                raise ValueError(message)
            anomalies.append(message)
            continue

        ligne_220 = float(l["cout_total_acquisition_eur"])
        ligne_221 = float(l.get("fractions_deja_prises", 0.0))
        ligne_222 = max(0.0, ligne_220 - ligne_221)
        ligne_223 = ligne_222 * (prix_cession / valeur_globale)
        ligne_224 = prix_cession - ligne_223

        pv_globale += ligne_224
        details.append(PlusValue(
            actif=l["actif"], date_cession=d, quantite=l["quantite"],
            pru_eur=ligne_223 / l["quantite"] if l["quantite"] else 0.0,
            prix_cession_eur=prix_cession, plus_value_eur=ligne_224,
            regime="150 VH bis",
        ))

    # DÉFAUT CORRIGÉ — le seuil de 305 EUR est une FRANCHISE assise sur le
    # total des PRIX DE CESSION, pas un abattement sur le gain.
    #
    #   total des cessions <= 305 EUR : plus-value exonérée, case 3AN vide,
    #                                   la 2086 reste due ;
    #   total des cessions >  305 EUR : imposable DÈS LE PREMIER EURO.
    #
    # L'ancien code retirait jusqu'à 305 EUR du gain. Sur 20 EUR de gain pour
    # 400 EUR de cessions — seuil franchi — il n'imposait rien. C'est faux.
    total_cessions = sum(x.prix_cession_eur for x in details)
    exonere = total_cessions <= fb.CRYPTO_FRANCHISE_CESSIONS

    pv_imposable = 0.0 if exonere else max(0.0, pv_globale)
    abattement = 0.0          # il n'y a pas d'abattement : le champ reste, la valeur est nulle

    ir = pv_imposable * fb.IR_FORFAITAIRE
    ps = pv_imposable * fb.taux_ps(annee)

    return ResultatFiscal(
        regime="150 VH bis", annee=annee, plus_value_brute=pv_globale,
        abattement=abattement, plus_value_imposable=pv_imposable,
        impot_ir=ir, prelevements_sociaux=ps, total_du=ir + ps, detail=details,
        # Champs d'affichage : ce sont eux qui alimentent la ligne 2086 et le
        # controle du seuil sur la page Fiscalite.
        total_cessions=total_cessions, exonere_par_franchise=exonere,
    )


# ===========================================================================
# Régime 150 VI — or physique
# ===========================================================================

def abattement_or_physique(annees_detention: int) -> float:
    """Abattement pour durée de détention de l'or physique.

    5 % par année de détention au-delà de la deuxième année, dans la limite de
    100 % — soit exonération totale à 22 ans.

    À VÉRIFIER : le barème exact de l'abattement et son application à la taxe
    forfaitaire (TFMP) doivent être recoupés avec le BOFiP.
    """
    if annees_detention <= 2:
        return 0.0
    return min(1.0, (annees_detention - 2) * 0.05)


def pv_or_physique(lignes: list[dict], annee: int) -> dict:
    """Or physique : les deux régimes de l'article 150 VI, et le plus favorable.

    Régime A — **Taxe forfaitaire sur les métaux précieux** : 11,5 % du montant
    brut de la cession. Aucun abattement pour durée.

    Régime B — **Plus-value** : 19 % d'IR + 17,2 % de prélèvements sociaux sur la
    plus-value, avec abattement de 5 %/an dès la 3ᵉ année, exonération totale à
    22 ans.

    L'administration applique le régime le plus avantageux, sauf option
    contraire du contribuable. On calcule les deux et on retient le moindre.

    À VÉRIFIER auprès du BOFiP : le traitement de l'abattement sur la TFMP.
    """
    total_brut = 0.0
    total_pv = 0.0
    abattement_moyen = 0.0
    nb = 0

    for l in lignes:
        d = l["date"]
        d = d.date() if isinstance(d, dt.datetime) else d
        if d.year != annee or l.get("sens") != "vente":
            continue
        brut = float(l["prix_cession_eur"])
        pv = brut - float(l["cout_acquisition_eur"])
        da = l["date_acquisition"]
        da = da.date() if isinstance(da, dt.datetime) else da
        annees = (d - da).days / 365.25
        total_brut += brut
        total_pv += pv
        abattement_moyen += abattement_or_physique(int(annees))
        nb += 1

    if nb == 0:
        return {"regime": "150 VI", "annee": annee, "total_du": 0.0,
                "detail": "Aucune cession d'or physique cette année."}

    abatt = abattement_moyen / nb

    # Régime A : TFMP sur le brut.
    tfmp = total_brut * fb.OR_PHYS_TFMP

    # Régime B : PV après abattement.
    pv_nette = max(0.0, total_pv * (1.0 - abatt))
    regime_b = pv_nette * (fb.OR_PHYS_PV_IR + fb.OR_PHYS_PV_PS)

    retenu = "TFMP (11,5 % du brut)" if tfmp <= regime_b else "Plus-value dégressive"
    return {
        "regime": "150 VI",
        "annee": annee,
        "montant_brut_cessions": total_brut,
        "plus_value": total_pv,
        "abattement_duree": abatt,
        "tfmp": tfmp,
        "regime_plus_value": regime_b,
        "regime_retenu": retenu,
        "total_du": min(tfmp, regime_b),
        "detail": f"{nb} cession(s) d'or physique.",
    }


# ===========================================================================
# Choix PFU / barème progressif
# ===========================================================================

def comparer_pfu_bareme(
    plus_value_nette: float,
    autres_revenus: float,
    parts: float,
    annee: int,
    statut: str = "Célibataire",
) -> dict:
    """Compare le PFU et le barème progressif sur une plus-value.

    CORRECTION : la v1 omettait la CSG déductible (6,8 % de la CSG), qui réduit
    le revenu imposable quand on opte pour le barème. Elle appliquait aussi la
    décote dans les deux branches de la soustraction, ce qui pouvait produire un
    écart négatif.
    """
    if plus_value_nette <= 0:
        return {"choix": "PFU", "gain": 0.0, "detail": "Aucune plus-value imposable."}

    # --- Option PFU : 12,8 % d'IR + les PS de l'annee de cession.
    # Le taux de PS depend de l'annee (LFSS 2026, art. 12) : 17,2 % jusqu'en
    # 2025, 18,6 % en 2026. Un taux fige comparait deux regimes sur des bases
    # differentes selon l'exercice.
    pfu_ir = plus_value_nette * fb.IR_FORFAITAIRE
    pfu_ps = plus_value_nette * fb.taux_ps(annee)
    pfu_total = pfu_ir + pfu_ps

    # --- Option barème : la PV s'ajoute au revenu, PS dus par ailleurs.
    csg_deductible = plus_value_nette * fb.CSG_DEDUCTIBLE
    revenu_imposable_supp = plus_value_nette - csg_deductible

    ir_avec = impot_revenu(autres_revenus + revenu_imposable_supp, parts, annee, statut)
    ir_sans = impot_revenu(autres_revenus, parts, annee, statut)
    ir_marginal = max(0.0, ir_avec.impot_net - ir_sans.impot_net)
    ps = plus_value_nette * fb.taux_ps(annee)
    bareme_total = ir_marginal + ps

    avantage = pfu_total - bareme_total
    return {
        "annee": annee,
        "plus_value_nette": plus_value_nette,
        "pfu": {"ir": pfu_ir, "ps": pfu_ps, "total": pfu_total},
        "bareme": {"ir_marginal": ir_marginal, "ps": ps, "total": bareme_total,
                   "csg_deductible": csg_deductible},
        "choix": "Barème progressif" if bareme_total < pfu_total else "PFU",
        "gain": abs(avantage),
        "tmi": ir_avec.tmi,
    }


# ===========================================================================
# Point d'entrée
# ===========================================================================

def cessions_de_lannee(
    transactions: list["Transaction"],
    positions: dict[str, "Position"],
    annee: int,
) -> dict[Classe, list[dict]]:
    """Cessions de l'année, au format attendu par `calculer`.

    CORRECTION : `prix_cession_eur` était rempli avec `t.montant_net`, qui est
    dans la devise de COTATION — dollars pour FLXC.L et BTCUSDT, yen pour
    XJSE.SW. Le PRU, lui, est en euros. La plus-value mélangeait donc deux
    monnaies, et était fausse pour tout actif non coté en euro. Chaque ligne est
    désormais convertie à sa date avec le taux de change réel.
    """
    from . import fx
    from .portfolio import classe_de

    cessions: dict[Classe, list[dict]] = {}
    for t in transactions:
        if not t.est_vente or t.date.year != annee:
            continue
        classe = classe_de(t.ticker)
        pos = positions.get(t.ticker)
        try:
            montant_eur = t.montant_net * fx.taux(t.devise, t.date.isoformat(), "EUR")
        except fx.FXIndisponible:
            # Pas de taux à cette date : on garde le montant brut plutôt que
            # d'inventer une conversion. La plus-value sera fausse, mais au moins
            # elle ne sera pas faussement précise.
            montant_eur = t.montant_net
        cessions.setdefault(classe, []).append({
            "actif": t.ticker,
            "date": t.date,
            "quantite": t.quantite,
            "pru_eur": pos.pru_eur if pos else 0.0,
            "prix_cession_eur": montant_eur,
            "sens": "vente",
        })

    crypto = cessions.get(Classe.CRYPTO)
    if crypto:
        enrichir_crypto(crypto, transactions)
    return cessions


def enrichir_crypto(lignes: list[dict], transactions: list["Transaction"]) -> None:
    """Complète les cessions crypto des trois chiffres de l'article 150 VH bis.

    La plus-value d'une cession de biens numériques ne se calcule **pas** actif
    par actif : on fractionne le capital d'acquisition du portefeuille ENTIER au
    prorata de la valeur cédée (formulaire 2086-SD, lignes 212 à 224). D'où :

      - ligne 212 `valeur_globale_eur` : valeur de **tout** le portefeuille
        crypto détenu au moment de la cession ;
      - ligne 220 `cout_total_acquisition_eur` : prix total d'acquisition de ce
        même portefeuille ;
      - ligne 221 `fractions_deja_prises` : fractions du capital déjà déduites
        lors de cessions antérieures.

    Ces trois grandeurs sont au niveau du portefeuille, jamais de la ligne :
    c'est pourquoi elles ne peuvent pas venir de la transaction elle-même. La
    version précédente ne les fournissait pas, `pv_crypto` les lisait donc à zéro
    et son garde-fou faisait sauter la page entière.
    """
    from . import fx, prices
    from .portfolio import calculer_positions, classe_de

    crypto = sorted(
        (t for t in transactions if classe_de(t.ticker) is Classe.CRYPTO),
        key=lambda t: t.date,
    )
    fractions_prises = 0.0

    for l in sorted(lignes, key=lambda x: x["date"]):
        d = l["date"]
        iso = d.isoformat()

        # Positions détenues à la date de cession : tout ce qui précède, d compris.
        positions = calculer_positions(
            [t for t in crypto if t.date <= d], anomalies=[]
        )

        # ligne 220 — coût d'acquisition du portefeuille crypto
        cout_total = sum(p.cout_total_eur for p in positions.values())

        # ligne 212 — valeur globale du portefeuille crypto à la date de cession
        valeur_globale = 0.0
        manquants: list[str] = []
        for p in positions.values():
            if p.quantite <= 0:
                continue
            try:
                prix = prices.cours(p.ticker, iso)
                taux = fx.taux(p.devise_cotation, iso, "EUR")
            except (prices.CoursIndisponible, fx.FXIndisponible):
                manquants.append(f"{p.ticker} au {d:%d/%m/%Y}")
                continue
            valeur_globale += p.quantite * prix * taux

        # Un prix manquant rend la fraction fausse. On remet à zéro plutôt que de
        # livrer un chiffre approximatif : le garde-fou de `pv_crypto` le dira.
        if manquants:
            valeur_globale = 0.0

        l["valeur_globale_eur"] = valeur_globale
        l["cout_total_acquisition_eur"] = cout_total
        l["fractions_deja_prises"] = fractions_prises

        # Fraction consommée par cette cession, répercutée sur les suivantes.
        if valeur_globale > 0 and cout_total > 0:
            ligne_222 = max(0.0, cout_total - fractions_prises)
            ligne_223 = ligne_222 * (l["prix_cession_eur"] / valeur_globale)
            fractions_prises += ligne_223


def calculer(transactions_par_classe: dict[Classe, list[dict]], annee: int,
             autres_revenus: float = 0.0, parts: float = 1.0,
             statut: str = "Célibataire",
             anomalies: list[str] | None = None) -> dict:
    """Calcule l'ensemble des régimes pour une année d'imposition."""
    resultats: dict = {"annee": annee, "regimes": []}

    classes_titres = (Classe.ACTION_ETF, Classe.OBLIGATION_ETF, Classe.OR)
    if any(c in transactions_par_classe for c in classes_titres):
        lignes: list[dict] = []
        for c in classes_titres:
            lignes.extend(transactions_par_classe.get(c, []))
        r = pv_titres(lignes, annee)
        resultats["regimes"].append({
            "regime": r.regime,
            "pv_brute": r.plus_value_brute,
            "total_du": r.total_du,
            "taux_effectif": r.taux_effectif,
            "nb_cessions": len(r.detail),
        })
        resultats["pv_titres"] = r

    if Classe.CRYPTO in transactions_par_classe:
        r = pv_crypto(transactions_par_classe[Classe.CRYPTO], annee, anomalies)
        resultats["regimes"].append({
            "regime": r.regime,
            "pv_brute": r.plus_value_brute,
            "abattement": r.abattement,
            "total_du": r.total_du,
            "taux_effectif": r.taux_effectif,
            "nb_cessions": len(r.detail),
        })
        resultats["pv_crypto"] = r

    if Classe.OR_PHYSIQUE in transactions_par_classe:
        r = pv_or_physique(transactions_par_classe[Classe.OR_PHYSIQUE], annee)
        resultats["regimes"].append(r)

    pv_totale = sum(g["pv_brute"] for g in resultats["regimes"] if "pv_brute" in g)
    resultats["pv_totale_brute"] = pv_totale
    if pv_totale > 0:
        resultats["comparaison"] = comparer_pfu_bareme(
            max(0.0, pv_totale), autres_revenus, parts, annee, statut
        )

    return resultats
