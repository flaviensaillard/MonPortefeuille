import os
import pandas as pd
import yfinance as yf
from supabase import create_client

# Connexion à Supabase via les variables d'environnement (GitHub Secrets)
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

def run_robot():
    # 1. Récupérer les actifs depuis la table Donnees
    response = supabase.table("Donnees").select("*").execute()
    df = pd.DataFrame(response.data)
    
    if df.empty:
        print("Aucune donnée trouvée.")
        return

    # 2. Mettre à jour les prix via Yahoo Finance
    total_valeur = 0
    for index, row in df.iterrows():
        ticker = row['Ticker']
        try:
            data = yf.Ticker(ticker).history(period="1d")
            new_price = data['Close'].iloc[-1]
            # Mise à jour dans Supabase
            supabase.table("Donnees").update({"Court Num": new_price}).eq("Ticker", ticker).execute()
            total_valeur += new_price * row['Quantité']
        except:
            print(f"Erreur pour {ticker}")

    # 3. Enregistrer la "photo" de minuit dans l'Historique
    # (Adapte les noms de colonnes selon ta table Historique)
    history_entry = {
        "Date": pd.Timestamp.now().strftime('%Y-%m-%d'),
        "Valeur Totale": total_valeur
    }
    supabase.table("Historique").insert(history_entry).execute()
    print("Robot terminé avec succès !")

if __name__ == "__main__":
    run_robot()
