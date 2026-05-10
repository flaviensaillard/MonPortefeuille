import streamlit as st
import pandas as pd
from supabase import create_client, Client

@st.cache_resource
def get_supabase_client() -> Client:
    """Initialise la connexion à Supabase une seule fois."""
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

# Note : J'ai désactivé le cache temporairement pour ce test
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
    """Écrase l'ancienne table et sauvegarde la nouvelle."""
    supabase = get_supabase_client()
    try:
        supabase.table(table_name).delete().neq("id", -1).execute()
        if not df.empty:
            records = df.to_dict('records')
            supabase.table(table_name).insert(records).execute()
    except Exception as e:
        st.error(f"⚠️ Erreur de sauvegarde sur la table {table_name}: {e}")

def append_to_sheet(table_name, new_row_dict):
    """Ajoute une ligne instantanément."""
    supabase = get_supabase_client()
    try:
        supabase.table(table_name).insert(new_row_dict).execute()
    except Exception as e:
        st.error(f"⚠️ Erreur d'ajout sur la table {table_name}: {e}")
