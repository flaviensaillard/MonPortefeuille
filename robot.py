import os
import datetime
import pandas as pd
# On importe tes fonctions de connexion à la base de données depuis ton code existant
# (Adapte l'import selon l'endroit où sont définies tes fonctions de lecture/écriture)
from app import read_sheet, save_sheet, extraire_nombre

def run_daily_projection():
    print(f"🤖 Lancement du robot de projection - {datetime.datetime.now()}")
    
    # 1. Récupération des données actuelles du portefeuille
    try:
        df_donnees = read_sheet("Données") # Ou le nom exact de ta table contenant le bilan actuel
    except Exception as e:
        print(f"❌ Erreur lors de la lecture des données actuelles : {e}")
        return

    if df_donnees.empty:
        print("⚠️ La table des données actuelles est vide. Annulation.")
        return

    # 2. Extraction des valeurs "Total Global" et "Actifs Stratégiques"
    # (Le code ci-dessous mime la logique d'extraction de ton app.py)
    total_global = 0.0
    actifs_strat = 0.0

    for _, row in df_donnees.iterrows():
        classe = str(row.get("Classe d'actif", "")).strip()
        valeur_totale = extraire_nombre(row.get("Valeur totale", "0"))
        
        if "Total" in classe or "Global" in classe:
            total_global = valeur_totale
        elif "Stratégique" in classe:
            actifs_strat = valeur_totale

    # Si les libellés exacts dépendent de ta structure, on s'assure d'avoir des chiffres cohérents
    print(f"📊 Valeurs détectées : Total Global = {total_global} $ | Actifs Stratégiques = {actifs_strat} $")

    # 3. Récupération et mise à jour de la table "Projections"
    try:
        df_projections = read_sheet("Projections")
    except:
        df_projections = pd.DataFrame(columns=["Date", "Evolution cumulée $", "Actifs Stratégiques", "Score TWR %"])

    # Nouvelle ligne à insérer
    nouvelle_ligne = {
        "Date": datetime.date.today().strftime("%d/%m/%Y"),
        "Evolution cumulée $": total_global,
        "Actifs Stratégiques": actifs_strat,
        "Score TWR %": 0.0 # Optionnel : à recalculer si tu veux alimenter ton TWR automatiquement
    }

    # On ajoute la ligne et on sauvegarde
    df_projections = pd.concat([df_projections, pd.DataFrame([nouvelle_ligne])], ignore_index=True)
    
    try:
        save_sheet("Projections", df_projections)
        print("✅ Base 'Projections' mise à jour avec succès par le robot !")
    except Exception as e:
        print(f"❌ Erreur lors de la sauvegarde des projections : {e}")

if __name__ == "__main__":
    run_daily_projection()
