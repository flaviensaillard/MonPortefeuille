import os
import pandas as pd
import yfinance as yf
from supabase import create_client

# Connexion à Supabase via les Secrets de ton GitHub
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

def run_robot():
    # 1. On récupère les lignes de ton portefeuille actuel
    response = supabase.table("Donnees").select("*").execute()
    df = pd.DataFrame(response.data)
    
    if df.empty:
        print("Aucune donnée trouvée dans la table Donnees.")
        return

    total_global = 0.0
    actifs_strategiques = 0.0

    # 2. On calcule les valeurs à minuit
    for _, row in df.iterrows():
        ticker = str(row['Ticker']).strip()
        quantite = float(row.get('Quantité', 0.0))
        
        # Récupération du prix actuel (Yahoo Finance)
        # Si c'est du cash (EUR, USD, etc.), le cours est de 1.0
        if ticker in ["EUR", "USD", "CHF", "CNY", "Cash", "Liquidités"]:
            court_num = 1.0
        else:
            # Traduction pour la crypto si besoin (ex: BTCUSDT -> BTC-USD)
            ticker_yahoo = ticker.replace("USDT", "-USD") if "USDT" in ticker else ticker
            try:
                data = yf.Ticker(ticker_yahoo).history(period="1d")
                if not data.empty:
                    court_num = float(data['Close'].iloc[-1])
                    # On met à jour le cours dans la table Donnees pour que l'app soit à jour
                    supabase.table("Donnees").update({"Court Num": court_num}).eq("Ticker", ticker).execute()
                else:
                    court_num = float(row.get('Court Num', 0.0))
            except Exception:
                court_num = float(row.get('Court Num', 0.0))

        valeur_ligne = court_num * quantite
        total_global += valeur_ligne
        
        # Si ce n'est pas du cash, c'est un actif stratégique (Action, ETF, Crypto, Or...)
        if ticker not in ["EUR", "USD", "CHF", "CNY", "Cash", "Liquidités"]:
            actifs_strategiques += valeur_ligne

    # 3. On enregistre la photo dans la table "Historique"
    # Nous utilisons exactement les noms de colonnes standardisés
    historique_entry = {
        "Date": pd.Timestamp.now().strftime('%Y-%m-%d'),
        "Total Global": round(total_global, 2),
        "Actifs Strategiques": round(actifs_strategiques, 2)
    }

    try:
        supabase.table("Historique").insert(historique_entry).execute()
        print(f"✅ Succès ! Valeurs enregistrées pour aujourd'hui :")
        print(f"   - Total Global : {total_global:.2f} €")
        print(f"   - Actifs Stratégiques : {actifs_strategiques:.2f} €")
    except Exception as e:
        print(f"❌ Erreur lors de l'écriture dans la table Historique : {e}")
        print("Vérifie que ta table 'Historique' possède bien les colonnes 'Date', 'Total Global' et 'Actifs Strategiques' (avec exactement ces majuscules et espaces).")

if __name__ == "__main__":
    run_robot()
