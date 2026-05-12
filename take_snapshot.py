import os
import datetime
import pandas as pd
from supabase import create_client

def extraire_nombre(val):
    if pd.isna(val) or val is None:
        return 0.0
    # Nettoyage complet des caractères de devises et espaces
    val_str = str(val).replace(" ", "").replace("\xa0", "").replace(",", ".").replace("€", "").replace("$", "")
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def run_snapshot():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("❌ Erreur : Variables URL ou KEY manquantes.")
        return
        
    supabase = create_client(url, key)
    
    # 1. 📈 CALCUL DU TOTAL GLOBAL ET STRATÉGIQUE DEPUIS LA TABLE "Donnees"
    total_global = 0.0
    actifs_strat = 0.0
    
    try:
        response = supabase.table("Donnees").select("*").execute()
        df_donnees = pd.DataFrame(response.data)
        
        if not df_donnees.empty:
            for _, row in df_donnees.iterrows():
                quantite = extraire_nombre(row.get("Quantité", 0.0))
                court = extraire_nombre(row.get("Court", 0.0))
                pourcentage = extraire_nombre(row.get("Pourcentage", 0.0))
                
                # Valeur en direct de la ligne d'actif
                valeur_ligne = quantite * court
                
                # Le global cumule absolument tout
                total_global += valeur_ligne
                
                # Le stratégique prend uniquement si le pourcentage est défini (différent de 0)
                if pourcentage != 0.0:
                    actifs_strat += valeur_ligne
                    
            print(f"🔄 Calculs Données : Global = {total_global} $ | Stratégique = {actifs_strat} $")
    except Exception as e:
        print(f"❌ Erreur lors du calcul des totaux en direct : {e}")
        return

    # 2. 💰 RÉCUPÉRATION DU CAPITAL DEPUIS L'HISTORIQUE
    capital_investi = 0.0
    try:
        response_hist = supabase.table("Historique").select("Total_Apports_nets").execute()
        df_hist = pd.DataFrame(response_hist.data)
        
        if not df_hist.empty and "Total_Apports_nets" in df_hist.columns:
            # On prend la toute dernière valeur enregistrée dans l'historique
            capital_investi = extraire_nombre(df_hist["Total_Apports_nets"].iloc[-1])
            print(f"💰 Capital récupéré depuis l'Historique : {capital_investi} $")
        else:
            print("⚠️ Colonne 'Total_Apports_nets' introuvable. Récupération du dernier capital connu...")
            last_p = supabase.table("Projections").select("Capital investi").order("Date", desc=True).limit(1).execute()
            if last_p.data:
                capital_investi = extraire_nombre(last_p.data[0].get("Capital investi", 0.0))
    except Exception as e:
        print(f"⚠️ Erreur lors de la lecture du Capital : {e}")

# 3. 📸 ÉCRITURE DE LA PHOTO DANS LA TABLE "Projections"
    date_aujourdhui = datetime.date.today().strftime("%d/%m/%Y")
    nouvelle_photo = {
        "Date": date_aujourdhui,
        "Total global": total_global,
        "Actifs Stratégiques": actifs_strat,
        "Capital investi": capital_investi
    }

    try:
        # 🛠️ ON FORCE L'INSERTION POUR LE TEST (Sécurité doublon désactivée)
        supabase.table("Projections").insert(nouvelle_photo).execute()
        print(f"✅ TEST REUSSI : Photo du {date_aujourdhui} enregistrée avec succès !")
    except Exception as e:
        print(f"❌ Erreur lors de l'enregistrement dans Projections : {e}")

if __name__ == "__main__":
    run_snapshot()
