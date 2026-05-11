import os
import datetime
import pandas as pd
from supabase import create_client

# 1. Connexion sécurisée à Supabase via les variables d'environnement
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Les variables d'environnement SUPABASE_URL ou SUPABASE_KEY sont manquantes.")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def extraire_nombre(val):
    if pd.isna(val) or val is None:
        return 0.0
    val_str = str(val).replace(" ", "").replace("\xa0", "").replace(",", ".").replace("€", "").replace("$", "")
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def run_daily_projection():
    print(f"🤖 Lancement du robot de projection - {datetime.datetime.now()}")

    # 2. Lecture de la table "Données"
    try:
        response = supabase.table("Données").select("*").execute()
        df_donnees = pd.DataFrame(response.data)
    except Exception as e:
        print(f"❌ Erreur lors de la lecture de la table 'Données' : {e}")
        return

    if df_donnees.empty:
        print("⚠️ La table des données actuelles est vide. Annulation.")
        return

    # 3. Extraction de "Total Global" et "Actifs Stratégiques"
    total_global = 0.0
    actifs_strat = 0.0

    for _, row in df_donnees.iterrows():
        classe = str(row.get("Classe d'actif", "")).strip()
        valeur_totale = extraire_nombre(row.get("Valeur totale", "0"))
        
        # On cible la ligne contenant le total global et celle des actifs stratégiques
        if "Total" in classe or "Global" in classe:
            total_global = valeur_totale
        elif "Stratégique" in classe:
            actifs_strat = valeur_totale

    print(f"📊 Valeurs capturées : Total Global = {total_global} $ | Actifs Stratégiques = {actifs_strat} $")

    if total_global == 0.0 and actifs_strat == 0.0:
        print("⚠️ Les valeurs extraites sont à 0. Sauvegarde annulée par sécurité.")
        return

    # 4. Insertion directe de la nouvelle ligne dans la table "Projections"
    nouvelle_ligne = {
        "Date": datetime.date.today().strftime("%d/%m/%Y"),
        "Evolution cumulée $": total_global,
        "Actifs Stratégiques": actifs_strat,
        "Score TWR %": 0.0  # Reste à 0 ou s'ajustera selon tes calculs dans l'app
    }

    try:
        supabase.table("Projections").insert(nouvelle_ligne).execute()
        print("✅ Base 'Projections' mise à jour avec succès par le robot !")
    except Exception as e:
        print(f"❌ Erreur lors de l'insertion dans 'Projections' : {e}")

if __name__ == "__main__":
    run_daily_projection()
