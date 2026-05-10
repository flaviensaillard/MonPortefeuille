import streamlit as st
import pandas as pd
import gspread
from gspread_dataframe import set_with_dataframe, get_as_dataframe
from google.oauth2.service_account import Credentials
import time

@st.cache_resource
def init_google_sheets():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    gc = gspread.authorize(credentials)
    return gc.open_by_key("1hkZoHQ1vvtbI1DYHR_OnofWn4jG92JGyxJjN-FedsWk")

def execute_with_retry(func, max_attempts=5, initial_delay=2):
    """Bouclier anti-crash : Patiente si Google bloque."""
    delay = initial_delay
    for attempt in range(max_attempts):
        try:
            return func()
        except Exception as e:
            if attempt == max_attempts - 1:
                st.error("⚠️ Échec définitif de communication avec la base de données. Les serveurs de Google bloquent la requête.")
                raise e
            time.sleep(delay)
            delay *= 2

# MAGIE V18 : On met en cache serveur pour éviter de spammer Google
@st.cache_data(ttl=600, show_spinner=False)
def load_sheet(sheet_name, default_cols):
    def _load():
        sh = init_google_sheets()
        ws = sh.worksheet(sheet_name)
        df = get_as_dataframe(ws, evaluate_formulas=True).dropna(how='all').dropna(axis=1, how='all')
        if df.empty: return pd.DataFrame(columns=default_cols)
        return df
    try:
        # Anti-rafale : 1 seconde de pause avant chaque lecture d'onglet
        time.sleep(1)
        return execute_with_retry(_load)
    except Exception:
        return pd.DataFrame(columns=default_cols)

def save_sheet(sheet_name, df):
    def _save():
        sh = init_google_sheets()
        try: 
            ws = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound: 
            ws = sh.add_worksheet(title=sheet_name, rows=100, cols=20)
        ws.clear()
        set_with_dataframe(ws, df, include_index=False)
    execute_with_retry(_save)
    
    # On force Streamlit à vider sa mémoire car la donnée a changé !
    load_sheet.clear()

def append_to_sheet(sheet_name, new_row_dict):
    def _append():
        sh = init_google_sheets()
        ws = sh.worksheet(sheet_name)
        headers = ws.row_values(1)
        if not headers:
            headers = list(new_row_dict.keys())
            ws.append_row(headers)
        row_values = [new_row_dict.get(h, "") for h in headers]
        ws.append_row(row_values)
    execute_with_retry(_append)
    
    # On force Streamlit à vider sa mémoire car la donnée a changé !
    load_sheet.clear()
