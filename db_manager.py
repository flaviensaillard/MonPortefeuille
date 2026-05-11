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
    """Télécharge les données depuis Supabase."""
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
    """Écrase et sauvegarde la table avec protection des données (Anti-Zéros)."""
    supabase = get_supabase_client()
    try:
        df_clean = df.copy()
        
        # 🛡️ LA SÉCURITÉ POUR LE ROBOT EST ICI :
        # Si on essaie de sauvegarder la table "Donnees", on vérifie les prix.
        # S'il manque des prix (NaN), on supprime la ligne vide au lieu d'écrire un zéro.
        if table_name == "Donnees" and "Court Num" in df_clean.columns:
            df_clean = df_clean.dropna(subset=["Court Num"])
        
        # Pour les autres petites erreurs mathématiques, on met 0
        df_clean = df_clean.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        
        # On ne lance la sauvegarde QUE s'il reste des données valides.
        # Si le fichier est vide (parce que Yahoo a planté), on ne fait rien, ce qui protège vos données !
        if not df_clean.empty:
            supabase.table(table_name).delete().neq("id", -1).execute()
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
        
        load_sheet.clear()
    except Exception as e:
        st.error(f"⚠️ Erreur d'ajout sur la table {table_name}: {e}")
def obtenir_derniere_projection_veille():
    """
    Récupère l'avant-dernière ou la dernière ligne de la table Projections
    pour servir de point de comparaison J-1 (veille).
    """
    try:
        # On charge la feuille Projections
        df_proj = load_sheet("Projections", [])
        if df_proj.empty:
            return None
        
        # On s'assure du bon tri par date
        df_proj["Date_Parsed"] = pd.to_datetime(df_proj["Date"], dayfirst=True, errors="coerce")
        df_proj = df_proj.dropna(subset=["Date_Parsed"]).sort_values("Date_Parsed")
        
        if not df_proj.empty:
            # On extrait la toute dernière ligne enregistrée (la photo du robot de minuit)
            derniere_ligne = df_proj.iloc[-1]
            return {
                "Total Global": float(derniere_ligne.get("Total Global", 0.0)),
                "Actifs Stratégiques": float(derniere_ligne.get("Actifs Stratégiques", 0.0))
            }
    except Exception as e:
        print(f"Erreur lors de la lecture J-1 : {e}")
    return None
