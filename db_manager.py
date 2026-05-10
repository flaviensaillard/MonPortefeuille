import streamlit as st
import pandas as pd
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
        # On télécharge tout le contenu de la table
        response = supabase.table(table_name).select("*").execute()
        df = pd.DataFrame(response.data)
        
        if df.empty:
            return pd.DataFrame(columns=default_cols)
            
        # Supabase ajoute une colonne 'id' pour se repérer. 
        # On l'enlève en mémoire pour ne pas perturber les mathématiques de ton app.
        if 'id' in df.columns:
            df = df.drop(columns=['id'])
            
        return df
    except Exception as e:
        st.error(f"⚠️ Erreur de lecture sur la table {table_name}.")
        return pd.DataFrame(columns=default_cols)

def save_sheet(table_name, df):
    """Écrase l'ancienne table et sauvegarde la nouvelle (pour le rééquilibrage)."""
    supabase = get_supabase_client()
    try:
        # 1. On vide la table proprement (où l'id n'est pas nul)
        supabase.table(table_name).delete().neq("id", -1).execute()
        
        # 2. On insère les nouvelles données
        if not df.empty:
            records = df.to_dict('records')
            supabase.table(table_name).insert(records).execute()
            
        # On vide le cache de Streamlit pour qu'il voit la mise à jour
        load_sheet.clear()
    except Exception as e:
        st.error(f"⚠️ Erreur de sauvegarde sur la table {table_name}: {e}")

def append_to_sheet(table_name, new_row_dict):
    """Ajoute une ligne instantanément (ex: nouvelle transaction)."""
    supabase = get_supabase_client()
    try:
        supabase.table(table_name).insert(new_row_dict).execute()
        load_sheet.clear()
    except Exception as e:
        st.error(f"⚠️ Erreur d'ajout sur la table {table_name}: {e}")
