import os
import datetime
import pandas as pd
from supabase import create_client

def extraire_nombre(val):
    if pd.isna(val) or val is None:
        return 0.0
    val_str = str(val).replace(" ", "").replace("\xa0", "").replace(",", ".").replace("€", "").replace("$", "")
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def run_snapshot():
    # 1. Connexion à Supabase via les variables d'environnement de GitHub
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    
    if not url or not key:
        print("❌ Erreur : Variables d'environnement SUPABASE_URL ou SUPABASE_KEY manquantes.")
        return
        
    supabase = create_client(url, key)
    
    # 2. Récupération des données actuelles dans la table "Données"
    try:
        response = supabase.table("Données").select("*").execute()
        df_donnees = pd.DataFrame(response.data)
    except Exception as e:
        print(f"❌ Impossible de lire la table 'Données' : {e}")
        return

    if df_donnees.empty:
        print("⚠️ La table 'Données' est vide. Annulation.")
        return

    # 3. Extraction des 3 valeurs cibles
    total_global = 0.0
    actifs_strat = 0.0
    capital_investi = 0.0

    for _, row in df_donnees.iterrows():
        classe = str(row.get("Classe d'actif", "")).strip()
        valeur_totale = extraire_nombre(row.get("Valeur totale", "0"))
        
        # Identification des lignes (ajuste les noms si tes colonnes diffèrent légèrement)
        if "Total" in class_name or "Global" in classe:
            total_global = valeur_totale
        elif "Stratégique" in classe:
            actifs_strat = valeur_totale
        elif "Capital" in classe or "Investi" in classe:
            capital_investi = valeur_totale

    print(f"📸 Valeurs lues : Global = {total_global} | Stratégique = {actifs_strat} | Capital = {capital_investi}")

    # 4. Écriture de la photo dans la table "Projections"
    date_aujourdhui = datetime.date.today().strftime("%d/%m/%Y")
    
    nouvelle_photo = {
        "Date": date_aujourdhui,
        "Total global": total_global,
        "Actifs stratégiques": actifs_strat,
        "Capital investi": capital_investi
    }

    try:
        # On vérifie si une photo existe déjà pour aujourd'hui pour éviter les doublons
        check = supabase.table("Projections").select("*").eq("Date", date_aujourdhui).execute()
        if len(check.data) > 0:
            print(f"ℹ️ Une photo existe déjà pour le {date_aujourdhui}. Pas d'écriture.")
            return
            
        supabase.table("Projections").insert(nouvelle_photo).execute()
        print(f"✅ Photo du {date_aujourdhui} enregistrée avec succès !")
    except Exception as e:
        print(f"❌ Erreur lors de l'enregistrement de la photo : {e}")

if __name__ == "__main__":
    run_snapshot()
