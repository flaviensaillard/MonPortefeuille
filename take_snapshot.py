import os
import datetime
import pandas as pd
import yfinance as yf
from supabase import create_client

def extraire_nombre(val):
    if pd.isna(val) or val is None:
        return 0.0
    val_str = str(val).replace(" ", "").replace("\xa0", "").replace(",", ".").replace("€", "").replace("$", "").replace("%", "")
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def run_snapshot():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    
    if not url or not key:
        try:
            import streamlit as st
            url = st.secrets["SUPABASE_URL"]
            key = st.secrets["SUPABASE_KEY"]
        except:
            pass
    
    if not url or not key:
        # Fallback en dur
        url = "https://bxcpmpseagwcgaavmygo.supabase.co"
        key = "sb_publishable_NDcVoF7diyTCS-03Paqlig_srlEytG9"
        
    supabase = create_client(url, key)
    
    # 1. CALCUL DU TOTAL GLOBAL ET STRATÉGIQUE
    total_global = 0.0
    actifs_strat = 0.0
    
    try:
        response = supabase.table("Donnees").select("*").execute()
        df_donnees = pd.DataFrame(response.data)
        
        if not df_donnees.empty:
            for _, row in df_donnees.iterrows():
                quantite = extraire_nombre(row.get("Quantité", 0.0))
                court = extraire_nombre(row.get("Court", 0.0))
                pourcentage = extraire_nombre(row.get("Pourcentage (%)", 0.0))
                valeur_ligne = quantite * court
                total_global += valeur_ligne
                if pourcentage > 0.0:
                    actifs_strat += valeur_ligne
                    
            print(f"🔄 Calculs Données : Global = {total_global} $ | Stratégique = {actifs_strat} $")
    except Exception as e:
        print(f"❌ Erreur lors du calcul des totaux en direct : {e}")
        return

    # 2. RÉCUPÉRER LES VARIATIONS YAHOO FINANCE
    print("📡 Récupération des variations Yahoo Finance...")
    tickers_to_fetch = []
    ticker_mapping = {}
    
    for _, row in df_donnees.iterrows():
        tick = str(row.get("Ticker", "")).strip().upper()
        if tick and tick not in ["NAN", "USD"]:
            if tick.endswith("USDT"):
                yf_t = tick.replace("USDT", "-USD")
            elif tick in ["EUR", "CHF", "JPY", "GBP", "CNY", "CAD", "AUD"]:
                yf_t = f"{tick}USD=X"
            else:
                yf_t = tick
            tickers_to_fetch.append(yf_t)
            ticker_mapping[yf_t] = tick
    
    if tickers_to_fetch:
        try:
            data = yf.download(tickers_to_fetch, period="2d", progress=False)
            if 'Close' in data.columns:
                closes = data['Close']
                for yf_t in ticker_mapping:
                    tick = ticker_mapping[yf_t]
                    try:
                        if yf_t in closes.columns:
                            vals = closes[yf_t].dropna()
                            if len(vals) >= 2:
                                pct = ((float(vals.iloc[-1]) - float(vals.iloc[-2])) / float(vals.iloc[-2])) * 100
                                arrow = "↗" if pct > 0 else "↘" if pct < 0 else "→"
                                var_str = f"{arrow} {abs(pct):.2f} %"
                                supabase.table("Donnees").update({"Var. Jour 🔒": var_str}).eq("Ticker", tick).execute()
                    except: pass
            print("✅ Variations mises à jour")
        except Exception as e:
            print(f"⚠️ Erreur Yahoo Finance: {e}")

    # 3. RÉCUPÉRATION DU CAPITAL DEPUIS L'HISTORIQUE
    capital_investi = 0.0
    try:
        response_hist = supabase.table("Historique").select("Total_Apports_nets").execute()
        df_hist = pd.DataFrame(response_hist.data)
        if not df_hist.empty and "Total_Apports_nets" in df_hist.columns:
            capital_investi = extraire_nombre(df_hist["Total_Apports_nets"].iloc[-1])
    except: pass

    # 4. ÉCRITURE DE LA PHOTO DANS LA TABLE "Projections"
    date_aujourdhui = datetime.date.today().strftime("%d/%m/%Y")
    nouvelle_photo = {
        "Date": date_aujourdhui,
        "Total Global": total_global,
        "Actifs Stratégiques": actifs_strat,
        "Capital investi": capital_investi
    }

    try:
        existing = supabase.table("Projections").select("id").eq("Date", date_aujourdhui).execute()
        if existing.data:
            supabase.table("Projections").update(nouvelle_photo).eq("Date", date_aujourdhui).execute()
            print(f"✅ Mise à jour : Photo du {date_aujourdhui} actualisée !")
        else:
            supabase.table("Projections").insert(nouvelle_photo).execute()
            print(f"✅ Nouvelle photo : {date_aujourdhui} enregistrée !")
    except Exception as e:
        print(f"❌ Erreur lors de l'enregistrement dans Projections : {e}")

if __name__ == "__main__":
    run_snapshot()