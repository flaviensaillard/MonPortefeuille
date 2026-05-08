import streamlit as st
import pandas as pd
import gspread
from gspread_dataframe import set_with_dataframe, get_as_dataframe
from google.oauth2.service_account import Credentials

@st.cache_resource
def init_google_sheets():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    gc = gspread.authorize(credentials)
    # Remplacer par ta propre clé Google Sheets si nécessaire
    return gc.open_by_key("1hkZoHQ1vvtbI1DYHR_OnofWn4jG92JGyxJjN-FedsWk")

def load_sheet(sheet_name, default_cols):
    try:
        sh = init_google_sheets()
        ws = sh.worksheet(sheet_name)
        df = get_as_dataframe(ws, evaluate_formulas=True).dropna(how='all').dropna(axis=1, how='all')
        if df.empty: return pd.DataFrame(columns=default_cols)
        return df
    except Exception as e:
        return pd.DataFrame(columns=default_cols)

def save_sheet(sheet_name, df):
    try:
        sh = init_google_sheets()
        try: 
            ws = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound: 
            ws = sh.add_worksheet(title=sheet_name, rows=100, cols=20)
        ws.clear()
        set_with_dataframe(ws, df, include_index=False)
    except Exception as e:
        st.error(f"⚠️ Échec de l'enregistrement dans '{sheet_name}'. Vérifiez les quotas de l'API Google.")

def append_to_sheet(sheet_name, new_row_dict):
    try:
        sh = init_google_sheets()
        ws = sh.worksheet(sheet_name)
        headers = ws.row_values(1)
        if not headers:
            headers = list(new_row_dict.keys())
            ws.append_row(headers)
        row_values = [new_row_dict.get(h, "") for h in headers]
        ws.append_row(row_values)
    except Exception as e:
        raise ValueError("⚠️ Échec de communication avec la base de données Google Sheets. Opération annulée.")
