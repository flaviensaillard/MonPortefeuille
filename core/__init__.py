"""MonPortefeuille 2 — socle de calcul.

Organisation :
    models       — poche, périmètre, classe d'actif, régime fiscal
    fiscal_bars  — barèmes de l'impôt, indexés par année
    fx           — taux de change, jamais devinés
    prices       — cours, jamais devinés
    metrics      — TWR, rendement réel, rendement en or, IRR
    portfolio    — positions calculées depuis les transactions
    rebalance    — rééquilibrage par poche et bande de tolérance
    tax          — moteur fiscal français
    config       — réglages typés, sans clé en double
    db           — accès Supabase
    session      — chargement et calcul partagé par les pages
    ui           — helpers d'affichage

Principe transversal : aucune valeur de repli. Un cours ou un taux manquant est
une erreur affichée, pas un chiffre inventé. C'est la correction structurelle de
la v1.
"""

__version__ = "2.0.0"
