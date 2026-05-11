import os
import pandas as pd
import yfinance as yf
from supabase import create_client

# Connexion à Supabase via les Secrets de ton GitHub
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

def run_robot():
    # 1. Récupérer les données des actifs actuels
    response = supabase.table("Donnees").select("*").execute()
    df = pd.DataFrame(response.data)
    
    if df.empty:
        print("Aucune donnée trouvée dans la table Donnees.")
        return

    total_global = 0.0
    actifs_strategiques = 0.0
    capital_investi = 0.0

    # 2. Calculer les valeurs en direct et le capital investi
    for _, row in df.iterrows():
        ticker = str(row['Ticker']).strip()
        quantite = float(row.get('Quantité', 0.0))
        
        # Récupération du Prix d'Achat (PAF) pour le Capital Investi
        # (J'utilise .get() avec des alternatives courantes pour le nom de ta colonne d'achat)
        paf = float(row.get('PAF', row.get('Prix d achat', row.get('Prix d\'achat', 0.0))))
        capital_investi += paf * quantite
        
        # Récupération du cours actuel (Yahoo Finance)
        if ticker in ["EUR", "USD", "CHF", "CNY", "Cash", "Liquidités"]:
            court_num = 1.0
        else:
            ticker_yahoo = ticker.replace("USDT", "-USD") if "USDT" in ticker else ticker
            try:
                data = yf.Ticker(ticker_yahoo).history(period="1d")
                if not data.empty:
                    court_num = float(data['Close'].iloc[-1])
                    # Mise à jour du cours actuel dans la table Donnees
                    supabase.table("Donnees").update({"Court Num": court_num}).eq("Ticker", ticker).execute()
                else:
                    court_num = float(row.get('Court Num', 0.0))
            except Exception:
                court_num = float(row.get('Court Num', 0.0))

        valeur_actuelle_ligne = court_num * quantite
        total_global += valeur_actuelle_ligne
        
        # Si ce n'est pas du cash, c'est un actif stratégique
        if ticker not in ["EUR", "USD", "CHF", "CNY", "Cash", "Liquidités"]:
            actifs_strategiques += valeur_actuelle_ligne

    # 3. Enregistrer la ligne dans la table "projections"
    # On respecte STRICTEMENT tes colonnes : Date, Capital investi, actifs stratégiques, Total global
    projections_entry = {
        "Date": pd.Timestamp.now().strftime('%Y-%m-%d'),
        "Capital investi": round(capital_investi, 2),
        "actifs stratégiques": round(actifs_strategiques, 2),
        "Total global": round(total_global, 2)
    }

    try:
        supabase.table("projections").insert(projections_entry).execute()
        print("✅ Succès ! Ligne enregistrée dans la table 'projections' :")
        print(f"   - Date : {projections_entry['Date']}")
        print(f"   - Capital investi : {projections_entry['Capital investi']:.2f} €")
        print(f"   - Actifs stratégiques : {projections_entry['actifs stratégiques']:.2f} €")
        print(f"   - Total global : {projections_entry['Total global']:.2f} €")
    except Exception as e:
        print(f"❌ Erreur lors de l'écriture dans la table projections : {e}")

if __name__ == "__main__":
    run_robot()
