import streamlit as st
import pandas as pd
import gspread
from gspread_dataframe import set_with_dataframe, get_as_dataframe
from google.oauth2.service_account import Credentials
import time
import random

@st.cache_resource
def get_gc_client():
    """Initialise le client Google une seule fois pour toute l'application."""
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    return gspread.authorize(credentials)

@st.cache_resource
def get_spreadsheet():
    """Ouvre le fichier Excel une seule fois pour réduire les appels API."""
    gc = get_gc_client()
    return gc.open_by_key("1hkZoHQ1vvtbI1DYHR_OnofWn4jG92JGyxJjN-FedsWk")

def execute_with_retry(func, max_attempts=5, initial_delay=5):
    """Bouclier anti-quota : Attend de plus en plus longtemps si Google bloque."""
    delay = initial_delay
    for attempt in range(max_attempts):
        try:
            return func()
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                if attempt == max_attempts - 1:
                    st.error("⚠️ Google limite l'accès à cause d'un trop grand nombre de requêtes. Veuillez attendre 5 minutes avant de rafraîchir.")
                    raise e
                # On attend avec un 'jitter' (délai aléatoire) pour éviter les collisions
                wait_time = delay + random.uniform(1, 3)
                time.sleep(wait_time)
                delay *= 2
            else:
                raise e

# V19 : TTL passé à 1 heure (3600s) pour minimiser les contacts avec Google
@st.cache_data(ttl=3600, show_spinner=False)
def load_sheet(table_name, default_cols):
    """Télécharge les données depuis Supabase (MODE DEBUG)."""
    supabase = get_supabase_client()
    try:
        # On tente de télécharger les données
        response = supabase.table(table_name).select("*").execute()
        df = pd.DataFrame(response.data)
        
        # 🚨 ALARME VISUELLE POUR NOUS AIDER 🚨
        if df.empty:
            st.warning(f"🔍 DEBUG - Table '{table_name}' lue avec succès, mais Supabase dit qu'elle contient 0 ligne !")
        else:
            st.success(f"✅ DEBUG - Table '{table_name}' lue ! J'ai trouvé {len(df)} lignes et ces colonnes : {list(df.columns)}")
        
        if df.empty:
            return pd.DataFrame(columns=default_cols)
            
        if 'id' in df.columns:
            df = df.drop(columns=['id'])
            
        return df
        
    except Exception as e:
        # 🚨 ALARME ROUGE : Affiche l'erreur exacte du serveur
        st.error(f"❌ ERREUR CRITIQUE sur la table '{table_name}' : {str(e)}")
        return pd.DataFrame(columns=default_cols)

def save_sheet(sheet_name, df):
    def _save():
        sh = get_spreadsheet()
        try: ws = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound: 
            ws = sh.add_worksheet(title=sheet_name, rows=100, cols=20)
        ws.clear()
        set_with_dataframe(ws, df, include_index=False)
    execute_with_retry(_save)
    # Vider le cache de lecture pour forcer la mise à jour après une écriture
    load_sheet.clear()

def append_to_sheet(sheet_name, new_row_dict):
    def _append():
        sh = get_spreadsheet()
        ws = sh.worksheet(sheet_name)
        headers = ws.row_values(1)
        if not headers:
            headers = list(new_row_dict.keys())
            ws.append_row(headers)
        row_values = [new_row_dict.get(h, "") for h in headers]
        ws.append_row(row_values)
    execute_with_retry(_append)
    load_sheet.clear()
