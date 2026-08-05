import streamlit as st
import pandas as pd
import numpy as np
import re
import time
from supabase import create_client, Client

@st.cache_resource
def get_supabase_client() -> Client:
    """Initialise la connexion à Supabase avec timeout."""
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

def _execute_with_retry(operation, max_retries=3, operation_name="Supabase"):
    """Exécute une opération Supabase avec retry automatique."""
    supabase = get_supabase_client()
    last_error = None
    
    for attempt in range(max_retries):
        try:
            result = operation(supabase)
            return result
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
                if "connection" in str(e).lower() or "timeout" in str(e).lower():
                    get_supabase_client.clear()
    
    raise Exception(f"Échec après {max_retries} tentatives sur {operation_name}: {last_error}")

@st.cache_data(ttl=300, show_spinner=False)
def load_sheet(table_name, default_cols):
    """Télécharge les données depuis Supabase avec cache intelligent."""
    def _load(supabase):
        response = supabase.table(table_name).select("*").execute()
        df = pd.DataFrame(response.data)
        
        if df.empty:
            return pd.DataFrame(columns=default_cols)
            
        if 'id' in df.columns:
            df = df.drop(columns=['id'])
            
        return df
    
    try:
        return _execute_with_retry(_load, operation_name=f"Chargement {table_name}")
    except Exception as e:
        st.error(f"⚠️ Erreur de lecture sur la table {table_name}: {e}")
        return pd.DataFrame(columns=default_cols)

def save_sheet(table_name, df):
    """Sauvegarde optimisée avec UPSERT au lieu de DELETE + INSERT."""
    supabase = get_supabase_client()
    try:
        df_clean = df.copy().replace([np.inf, -np.inf], np.nan).fillna(0.0)
        
        if table_name == "Donnees" and "Court" in df_clean.columns:
            df_clean = df_clean[df_clean["Court"].apply(lambda x: "0.00" not in str(x) and "$ 0.00" not in str(x))]
        
        if not df_clean.empty:
            existing = supabase.table(table_name).select("id").execute()
            existing_ids = [row["id"] for row in (existing.data or [])]
            
            records = df_clean.to_dict('records')
            
            if len(records) <= 10:
                if existing_ids:
                    for i, record in enumerate(records):
                        if i < len(existing_ids):
                            record["id"] = existing_ids[i]
                    for extra_id in existing_ids[len(records):]:
                        supabase.table(table_name).delete().eq("id", extra_id).execute()
                
                supabase.table(table_name).upsert(records).execute()
            else:
                if existing_ids:
                    for i in range(0, len(existing_ids), 100):
                        batch = existing_ids[i:i+100]
                        supabase.table(table_name).delete().in_("id", batch).execute()
                
                for i in range(0, len(records), 100):
                    batch = records[i:i+100]
                    supabase.table(table_name).insert(batch).execute()
        
        load_sheet.clear()
        return True
        
    except Exception as e:
        st.session_state[f"backup_{table_name}"] = df.copy()
        st.error(f"⚠️ Erreur de sauvegarde sur {table_name}. Données en mémoire.")
        return False

def append_to_sheet(table_name, new_row_dict):
    """Ajoute une ligne avec retry."""
    def _append(supabase):
        clean_dict = {}
        for k, v in new_row_dict.items():
            if pd.isna(v) or v == np.inf or v == -np.inf:
                clean_dict[k] = 0.0
            else:
                clean_dict[k] = v
        return supabase.table(table_name).insert(clean_dict).execute()
    
    try:
        _execute_with_retry(_append, operation_name=f"Ajout {table_name}")
        load_sheet.clear()
        return True
    except Exception as e:
        st.error(f"⚠️ Erreur d'ajout sur {table_name}: {e}")
        return False

def obtenir_derniere_projection_veille():
    """Récupère la dernière projection (version optimisée)."""
    try:
        supabase = get_supabase_client()
        
        response = supabase.table("Projections")\
            .select("Date, Total Global, Actifs Stratégiques")\
            .order("id", desc=True)\
            .limit(1)\
            .execute()
        
        if response.data and len(response.data) > 0:
            row = response.data[0]
            return {
                "Total Global": _safe_float(row.get("Total Global", 0)),
                "Actifs Stratégiques": _safe_float(row.get("Actifs Stratégiques", 0))
            }
    except Exception as e:
        print(f"Erreur J-1: {e}")
    
    try:
        df_proj = load_sheet("Projections", [])
        if df_proj is not None and not df_proj.empty:
            df_proj = df_proj.copy()
            df_proj['Date_Parsed'] = pd.to_datetime(df_proj['Date'].astype(str).str.slice(0, 10), 
                                                     dayfirst=True, errors='coerce')
            df_proj = df_proj.dropna(subset=["Date_Parsed"]).sort_values("Date_Parsed")
            if not df_proj.empty:
                derniere = df_proj.iloc[-1]
                return {
                    "Total Global": _safe_float(derniere.get("Total Global", 0)),
                    "Actifs Stratégiques": _safe_float(derniere.get("Actifs Stratégiques", 0))
                }
    except:
        pass
    
    return None

def _safe_float(value, default=0.0):
    """Conversion sécurisée en float."""
    if value is None or pd.isna(value):
        return default
    try:
        if isinstance(value, str):
            value = value.replace(" ", "").replace(",", ".").replace("$", "").replace("€", "")
        return float(value)
    except:
        return default

def recalculer_toute_la_base_projections(df):
    """Version optimisée avec vectorisation numpy."""
    if df is None or df.empty:
        return pd.DataFrame()
        
    df = df.copy()
    
    df['Date_Propre'] = df['Date'].astype(str).str.slice(0, 10)
    df['Date_DT'] = pd.to_datetime(df['Date_Propre'], dayfirst=True, errors='coerce')
    df = df.dropna(subset=['Date_DT']).sort_values('Date_DT').reset_index(drop=True)
    
    for col in ['Capital investi', 'Actifs Stratégiques', 'Total Global']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
    
    df['Variation_Capital'] = df['Capital investi'].diff().fillna(0.0)
    df['Evolution actifs $'] = df['Actifs Stratégiques'].diff().fillna(0.0) - df['Variation_Capital']
    
    val_prec = df['Actifs Stratégiques'].shift(1)
    df['Evolution actifs %'] = np.where(val_prec > 0, 
                                        (df['Evolution actifs $'] / val_prec * 100), 
                                        0.0)
    
    df['Evolution cumulée $'] = df['Actifs Stratégiques'] - df['Capital investi']
    df['Evolution cumulée %'] = np.where(df['Capital investi'] > 0,
                                         ((df['Actifs Stratégiques'] - df['Capital investi']) / df['Capital investi'] * 100),
                                         0.0)
    
    df['TG_Evolution cumulée $'] = df['Total Global'] - df['Capital investi']
    df['TG_Evolution cumulée %'] = np.where(df['Capital investi'] > 0,
                                            ((df['Total Global'] - df['Capital investi']) / df['Capital investi'] * 100),
                                            0.0)
    
    df['Rendement_Multiplicateur'] = 1 + (df['Evolution actifs %'] / 100)
    df['Score TWR %'] = (df['Rendement_Multiplicateur'].cumprod() - 1) * 100
    df['TG_Score TWR %'] = df['Score TWR %']
    
    df = df.drop(columns=['Date_Propre', 'Date_DT', 'Variation_Capital', 'Rendement_Multiplicateur'])
    
    return df
