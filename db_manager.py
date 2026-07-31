import streamlit as st
import pandas as pd
import numpy as np
import re
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
        
        # 🛡️ SÉCURITÉ POUR LE ROBOT :
        # S'il manque des prix (NaN), on supprime la ligne vide au lieu d'écrire un zéro.
        if table_name == "Donnees" and "Court Num" in df_clean.columns:
            df_clean = df_clean.dropna(subset=["Court Num"])
        
        # Pour les autres petites erreurs mathématiques, on met 0
        df_clean = df_clean.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        
        # On ne lance la sauvegarde QUE s'il reste des données valides.
        if not df_clean.empty:
            # ✅ CORRECTION : Supprime proprement les lignes existantes une par une
            existing_ids = supabase.table(table_name).select("id").execute()
            if existing_ids.data:
                for row in existing_ids.data:
                    supabase.table(table_name).delete().eq("id", row["id"]).execute()
            
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
    Récupère la dernière ligne valide de la table Projections
    pour servir de point de comparaison J-1 (veille).
    """
    def _extraire_nombre_local(valeur):
        if pd.isna(valeur) or valeur is None:
            return 0.0
        val_str = str(valeur).replace(" ", "").replace("\xa0", "").replace(",", ".").strip()
        match = re.search(r'([-+]?\d*\.?\d+)', val_str)
        return float(match.group(1)) if match else 0.0

    try:
        df_proj = load_sheet("Projections", [])
        if df_proj is None or df_proj.empty:
            return None
        
        df_proj = df_proj.copy()
        
        # Nettoyage robuste de la date pour éviter le crash des saisies manuelles
        df_proj['Date_Propre'] = df_proj['Date'].astype(str).str.slice(0, 10)
        df_proj["Date_Parsed"] = pd.to_datetime(df_proj["Date_Propre"], dayfirst=True, errors="coerce")
        df_proj = df_proj.dropna(subset=["Date_Parsed"]).sort_values("Date_Parsed")
        
        if not df_proj.empty:
            # On extrait la toute dernière ligne enregistrée (la veille réelle disponible)
            derniere_ligne = df_proj.iloc[-1]
            
            return {
                "Total Global": _extraire_nombre_local(derniere_ligne.get("Total Global", 0.0)),
                "Actifs Stratégiques": _extraire_nombre_local(derniere_ligne.get("Actifs Stratégiques", 0.0))
            }
    except Exception as e:
        print(f"Erreur lors de la lecture J-1 : {e}")
    return None

def recalculer_toute_la_base_projections(df):
    """
    Prend les données brutes de Supabase et recalcule proprement 
    l'évolution ligne par ligne par rapport à la date CHRONOLOGIQUE précédente.
    Modifié pour immuniser le calcul contre les apports et retraits de capital (Vrai TWR).
    """
    if df is None or df.empty:
        return pd.DataFrame()
        
    df = df.copy()
    
    # 1. Nettoyage et conversion des dates pour un tri chronologique parfait
    df['Date_Propre'] = df['Date'].astype(str).str.slice(0, 10)
    df['Date_DT'] = pd.to_datetime(df['Date_Propre'], dayfirst=True, errors='coerce')
    
    # On trie du plus ANCIEN au plus RÉCENT pour pouvoir faire le calcul différentiel (.diff())
    df = df.dropna(subset=['Date_DT']).sort_values('Date_DT').reset_index(drop=True)
    
    # 2. Sécurisation numérique de toutes les colonnes clés
    for col in ['Capital investi', 'Actifs Stratégiques', 'Total Global']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0.0)

    # 3. CALCULS DES ÉVOLUTIONS CORRIGÉS (Prise en compte des apports de capital)
    df['Variation_Capital'] = df['Capital investi'].diff().fillna(0.0)
    
    df['Evolution actifs $'] = df['Actifs Stratégiques'].diff().fillna(0.0) - df['Variation_Capital']
    
    val_precedente_strat = df['Actifs Stratégiques'].shift(1)
    df['Evolution actifs %'] = (df['Evolution actifs $'] / val_precedente_strat * 100).fillna(0.0)
    
    df['Evolution cumulée $'] = df['Actifs Stratégiques'] - df['Capital investi']
    df['Evolution cumulée %'] = ((df['Actifs Stratégiques'] - df['Capital investi']) / df['Capital investi'] * 100).fillna(0.0)
    
    df['TG_Evolution cumulée $'] = df['Total Global'] - df['Capital investi']
    df['TG_Evolution cumulée %'] = ((df['Total Global'] - df['Capital investi']) / df['Capital investi'] * 100).fillna(0.0)
    
    # 4. CALCULS DES SCORES TWR GÉOMÉTRIQUES CUMULÉS
    df['Rendement_Multiplicateur'] = 1 + (df['Evolution actifs %'] / 100)
    df['Score TWR %'] = (df['Rendement_Multiplicateur'].cumprod() - 1) * 100
    
    df['TG_Score TWR %'] = df['Score TWR %']

    # On supprime les colonnes de travail temporaires pour rendre un dataframe propre
    df = df.drop(columns=['Date_Propre', 'Date_DT', 'Variation_Capital', 'Rendement_Multiplicateur'])
    
    return df
