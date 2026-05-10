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

# Le mode DEBUG est conservé (pas de mise en cache pour qu'il s'affiche à chaque fois)
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

def save_sheet(table_name, df):
    """Écrase et sauvegarde la table en nettoyant les valeurs invalides (NaN)."""
    supabase = get_supabase_client()
    try:
        # 🧹 LE BOUCLIER ANTI-BUG : Nettoyage des NaN (Not a Number) pour Supabase
        df_clean = df.copy()
        df_clean = df_clean.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        
        # 1. On vide la table (grâce à la nouvelle colonne id !)
        supabase.table(table_name).delete().neq("id", -1).execute()
        
        # 2. On insère les nouvelles données
        if not df_clean.empty:
            records = df_clean.to_dict('records')
            supabase.table(table_name).insert(records).execute()
            
    except Exception as e:
        st.error(f"⚠️ Erreur de sauvegarde sur la table {table_name}: {e}")

def append_to_sheet(table_name, new_row_dict):
    """Ajoute une ligne instantanément en nettoyant les valeurs invalides."""
    supabase = get_supabase_client()
    try:
        # 🧹 LE BOUCLIER ANTI-BUG pour l'ajout ligne par ligne
        clean_dict = {k: (0.0 if pd.isna(v) or v == np.inf or v == -np.inf else v) for k, v in new_row_dict.items()}
        
        supabase.table(table_name).insert(clean_dict).execute()
    except Exception as e:
        st.error(f"⚠️ Erreur d'ajout sur la table {table_name}: {e}")
