import streamlit as st
import pandas as pd
import numpy as np
from supabase import create_client, Client

@st.cache_resource
def get_supabase_client() -> Client:
    """Initialise la connexion à Supabase une seule fois."""
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

@st.cache_data(ttl=3600, show_spinner=False)
def load_sheet(table_name, default_cols):
    """Télécharge les données depuis Supabase à la vitesse de la lumière."""
    supabase = get_supabase_client()
    try:
        response = supabase.table(table_name).select("*").execute()
        df = pd.DataFrame(response.data)
        
        if df.empty:
            return pd.DataFrame(columns=default_cols)
            
        if 'id' in df.columns:
            df = df.drop(columns=['id'])
            
        return df
    except Exception as e:
        st.error(f"⚠️ Erreur de lecture sur la table {table_name}.")
        return pd.DataFrame(columns=default_cols)

def save_sheet(table_name, df):
    """Écrase et sauvegarde la table en nettoyant les valeurs invalides."""
    supabase = get_supabase_client()
    try:
        df_clean = df.copy()
        df_clean = df_clean.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        
        supabase.table(table_name).delete().neq("id", -1).execute()
        
        if not df_clean.empty:
            records = df_clean.to_dict('records')
            supabase.table(table_name).insert(records).execute()
            
        # On force la mise à jour de l'affichage
        load_sheet.clear()
    except Exception as e:
        st.error(f"⚠️ Erreur de sauvegarde sur la table {table_name}: {e}")

def append_to_sheet(table_name, new_row_dict):
    """Ajoute une ligne instantanément en nettoyant les valeurs invalides."""
    supabase = get_supabase_client()
    try:
        clean_dict = {k: (0.0 if pd.isna(v) or v == np.inf or v == -np.inf else v) for k, v in new_row_dict.items()}
        supabase.table(table_name).insert(clean_dict).execute()
        
        # On force la mise à jour de l'affichage
        load_sheet.clear()
    except Exception as e:
        st.error(f"⚠️ Erreur d'ajout sur la table {table_name}: {e}")
