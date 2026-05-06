import streamlit as st
import pandas as pd
import yfinance as yf
import re
import datetime
import time
import plotly.express as px
from streamlit_autorefresh import st_autorefresh
import gspread
from gspread_dataframe import set_with_dataframe, get_as_dataframe
from google.oauth2.service_account import Credentials
import urllib.request
import json

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Mon Portefeuille", layout="wide")
st_autorefresh(interval=15 * 60 * 1000, key="datarefresh")

# --- 2. SÉCURITÉ ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if not st.session_state["password_correct"]:
        st.markdown("<h1 style='text-align: center;'>🔒 Accès Sécurisé</h1>", unsafe_allow_html=True)
        pwd = st.text_input("Veuillez entrer votre mot de passe pour accéder au Family Office :", type="password")
        if pwd == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            st.rerun()
        elif pwd != "":
            st.error("Mot de passe incorrect. Accès refusé.")
        return False
    return True

if not check_password():
    st.stop()

# --- 3. CONNEXION GOOGLE SHEETS ---
@st.cache_resource
def init_google_sheets():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    gc = gspread.authorize(credentials)
    sh = gc.open_by_key("1hkZoHQ1vvtbI1DYHR_OnofWn4jG92JGyxJjN-FedsWk")
    return sh

try:
    sh = init_google_sheets()
except Exception as e:
    st.error("Erreur de connexion à Google Sheets. Vérifie tes clés secrètes !")
    st.stop()

def load_sheet(sheet_name, default_cols):
    try:
        ws = sh.worksheet(sheet_name)
        df = get_as_dataframe(ws, evaluate_formulas=True).dropna(how='all').dropna(axis=1, how='all')
        if df.empty: return pd.DataFrame(columns=default_cols)
        return df
    except:
        return pd.DataFrame(columns=default_cols)

def save_sheet(sheet_name, df):
    try:
        ws = sh.worksheet(sheet_name)
    except:
        ws = sh.add_worksheet(title=sheet_name, rows=100, cols=20)
    ws.clear()
    set_with_dataframe(ws, df, include_index=False)

# --- VARIABLES GLOBALES DE TAUX ---
try: TAUX_EUR_USD = float(yf.Ticker("EURUSD=X").history(period="1d")['Close'].iloc[-1])
except: TAUX_EUR_USD = 1.0

# --- 4. FONCTIONS OUTILS ---
def extraire_nombre(valeur):
    if pd.isna(valeur) or str(valeur).strip() == "" or str(valeur).lower() == "nan": return 0.0
    nettoye = re.sub(r'[^\d,.-]', '', str(valeur))
    if ',' in nettoye and '.' in nettoye: nettoye = nettoye.replace(',', '')
    elif ',' in nettoye: nettoye = nettoye.replace(',', '.')
    try: return float(nettoye)
    except: return 0.0

def afficher_montant_double(label, montant_usd, delta_str="", couleur_valeur=None, taille="large"):
    montant_eur = montant_usd / TAUX_EUR_USD
    str_usd = f"{montant_usd:,.2f}".replace(',', ' ')
    str_eur = f"{montant_eur:,.2f}".replace(',', ' ')
    
    delta_html = ""
    if delta_str:
        couleur_delta = "#2ecc71" if "+" in delta_str else ("#e74c3c" if "-" in delta_str else "inherit")
        delta_html = f"<div style='font-size: 0.9rem; font-weight: 600; color: {couleur_delta}; padding-top: 0.2rem;'>{delta_str}</div>"
        
    t_val = "1.8rem" if taille == "large" else ("1.4rem" if taille == "medium" else "1.2rem")
    t_lbl = "0.9rem" if taille == "large" else "0.85rem"
    c_val = f"color: {couleur_valeur};" if couleur_valeur else ""
    
    html = f"""
    <div style="margin-bottom: 0.8rem;">
        <div style="font-size: {t_lbl}; opacity: 0.8; margin-bottom: 0.2rem;">{label}</div>
        <div style="font-size: {t_val}; font-weight: 600; line-height: 1.2; {c_val}">
            {str_usd} $ <span style="font-size: 0.65em; opacity: 0.7; font-weight: 400;">/ {str_eur} €</span>
        </div>
        {delta_html}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

def est_devise_liquide(ticker):
    ticker_up = str(ticker).upper().strip()
    if ticker_up.endswith("=X"): return True
    mots_cash = ["USD", "EUR", "CHF", "JPY", "CNY"]
    return any(mot in ticker_up for mot in mots_cash) and not any(c in ticker_up for c in ["BTC", "ETH"])

def nettoyer_dataframe(df):
    cols_finales = ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]
    for col in df.columns:
        if "quantit" in str(col).lower() or "qte" in str(col).lower(): df.rename(columns={col: "Quantité"}, inplace=True)

    if "Type" not in df.columns:
        df["Type"] = ""
        for idx, row in df.iterrows():
            tick = str(row.get("Ticker", "")).upper()
            if est_devise_liquide(tick): df.at[idx, "Type"] = "💵 Cash"
            elif "BTC" in tick or "ETH" in tick or tick.endswith("USDT"): df.at[idx, "Type"] = "₿ Crypto"
            else: df.at[idx, "Type"] = "🛢️ Action"

    for col in cols_finales:
        if col not in df.columns:
            df[col] = 0.0 if col == "Pourcentage (%)" else ("$ 0.00" if col in ["Court", "Valeur totale"] else "")
            
    return df[cols_finales].reset_index(drop=True)

def recalculer_toute_la_base_projections(df):
    if df is None or df.empty: return df
    df_travail = df.copy()
    colonnes_base = ["Date", "Capital investi", "Actifs Stratégiques", "Total Global"]
    
    if not all(c in df_travail.columns for c in colonnes_base):
        for i, nom in enumerate(colonnes_base):
            if i < len(df_travail.columns): df_travail.rename(columns={df_travail.columns[i]: nom}, inplace=True)

    for col in ["Capital investi", "Actifs Stratégiques", "Total Global"]:
        df_travail[col] = df_travail[col].apply(extraire_nombre)

    df_travail['DT_TRI'] = pd.to_datetime(df_travail['Date'], dayfirst=True, errors='coerce')
    df_travail = df_travail.sort_values('DT_TRI').reset_index(drop=True)
    
    resultats = []
    current_twr_mult = 1.0
    tg_current_twr_mult = 1.0

    for i in range(len(df_travail)):
        row = df_travail.iloc[i].to_dict()
        cap = row["Capital investi"]
        actifs = row["Actifs Stratégiques"]
        tg = row["Total Global"]
        
        if i == 0:
            row["Evolution actifs $"] = 0.0 ; row["Evolution actifs %"] = 0.0
            row["Evolution cumulée $"] = actifs - cap
            row["Evolution cumulée %"] = ((actifs - cap) / cap * 100) if cap != 0 else 0.0
            r_twr = (actifs - cap) / cap if cap != 0 else 0.0
            current_twr_mult *= (1 + r_twr)
            
            row["TG_Evolution cumulée $"] = tg - cap
            row["TG_Evolution cumulée %"] = ((tg - cap) / cap * 100) if cap != 0 else 0.0
            tg_r_twr = (tg - cap) / cap if cap != 0 else 0.0
            tg_current_twr_mult *= (1 + tg_r_twr)
        else:
            prev = df_travail.iloc[i-1]
            diff_cap = cap - prev["Capital investi"]
            
            evo_usd = (actifs - prev["Actifs Stratégiques"]) - diff_cap
            row["Evolution actifs $"] = evo_usd
            row["Evolution actifs %"] = (evo_usd / prev["Actifs Stratégiques"] * 100) if prev["Actifs Stratégiques"] != 0 else 0.0
            row["Evolution cumulée $"] = actifs - cap
            row["Evolution cumulée %"] = ((actifs - cap) / cap * 100) if cap != 0 else 0.0
            base_twr = prev["Actifs Stratégiques"] + diff_cap
            r_twr = evo_usd / base_twr if base_twr != 0 else 0.0
            current_twr_mult *= (1 + r_twr)
            
            evo_tg_usd = (tg - prev["Total Global"]) - diff_cap
            row["TG_Evolution cumulée $"] = tg - cap
            row["TG_Evolution cumulée %"] = ((tg - cap) / cap * 100) if cap != 0 else 0.0
            base_tg_twr = prev["Total Global"] + diff_cap
            tg_r_twr = evo_tg_usd / base_tg_twr if base_tg_twr != 0 else 0.0
            tg_current_twr_mult *= (1 + tg_r_twr)
            
        row["Score TWR %"] = (current_twr_mult - 1) * 100
        row["TG_Score TWR %"] = (tg_current_twr_mult - 1) * 100
        resultats.append(row)
    
    df_final = pd.DataFrame(resultats)
    if 'DT_TRI' in df_final.columns: df_final.drop(columns=['DT_TRI'], inplace=True)
    ordre = ["Date", "Capital investi", "Actifs Stratégiques", "Total Global", "Evolution actifs $", "Evolution actifs %", "Evolution cumulée $", "Evolution cumulée %", "Score TWR %", "TG_Evolution cumulée $", "TG_Evolution cumulée %", "TG_Score TWR %"]
    return df_final[ordre]

def recalculer_totaux_locaux():
    if "donnees" in st.session_state:
        df = st.session_state.donnees.copy()
        for index, row in df.iterrows():
            c_num = extraire_nombre(row.get("Court", 0))
            q_num = extraire_nombre(row.get("Quantité", 0))
            df.at[index, "Valeur totale"] = f"$ {round(c_num * q_num, 2):,.2f}"
            df.at[index, "Court"] = f"$ {c_num:.2f}"
        st.session_state.donnees = df

def actualiser_cours_internet(silencieux=False):
    if "donnees" in st.session_state:
        if not silencieux: st.toast("🔄 Actualisation et conversion des cours boursiers en cours...")
        df_temp = st.session_state.donnees.copy()
        changement = False
        taux_de_change_cache = {} 
        
        if "variations" not in st.session_state:
            st.session_state.variations = {}
            
        for index, row in df_temp.iterrows():
            ticker_saisi = str(row.get("Ticker", "")).strip().upper()
            if ticker_saisi != "" and ticker_saisi != "NAN":
                success_binance = False
                if ticker_saisi.endswith("USDT"):
                    for base_url in ["https://api.binance.com", "https://api.binance.us"]:
                        try:
                            url = f"{base_url}/api/v3/klines?symbol={ticker_saisi}&interval=1d&limit=2"
                            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                            with urllib.request.urlopen(req, timeout=3) as response:
                                data = json.loads(response.read().decode())
                                if len(data) >= 2:
                                    prev_close = float(data[0][4]) 
                                    prix_usd = float(data[1][4])   
                                else:
                                    prix_usd = float(data[0][4])
                                    prev_close = prix_usd
                                var_pct = ((prix_usd - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0
                                symbole = "↗" if var_pct > 0 else ("↘" if var_pct < 0 else "→")
                                st.session_state.variations[ticker_saisi] = f"{symbole} {var_pct:+.2f} %"
                                df_temp.at[index, "Court"] = f"$ {prix_usd:.2f}"
                                changement = True
                                success_binance = True
                                break 
                        except:
                            continue 
                    if success_binance:
                        continue 

                ticker_yf = ticker_saisi.replace("USDT", "-USD") if (ticker_saisi.endswith("USDT") and not success_binance) else ticker_saisi
                try:
                    asset = yf.Ticker(ticker_yf)
                    try: prix_local = float(asset.fast_info.get('lastPrice', 0.0))
                    except:
                        hist = asset.history(period="1d")
                        prix_local = float(hist['Close'].iloc[-1]) if not hist.empty else 0.0
                    try:
                        try: prev_close = float(asset.fast_info.get('previous_close', 0.0))
                        except: prev_close = 0.0
                        if prev_close <= 0.0:
                            hist = asset.history(period="5d")
                            if len(hist) >= 2: prev_close = float(hist['Close'].iloc[-2])
                        if prev_close > 0.0 and prix_local > 0.0:
                            var_pct = ((prix_local - prev_close) / prev_close) * 100
                            symbole = "↗" if var_pct > 0 else ("↘" if var_pct < 0 else "→")
                            st.session_state.variations[ticker_saisi] = f"{symbole} {var_pct:+.2f} %"
                        else:
                            if ticker_saisi not in st.session_state.variations:
                                st.session_state.variations[ticker_saisi] = "→ 0.00 %"
                    except:
                        if ticker_saisi not in st.session_state.variations:
                                st.session_state.variations[ticker_saisi] = "→ 0.00 %"
                        
                    if prix_local > 0:
                        try: devise = str(asset.fast_info.get('currency', 'USD')).strip().upper()
                        except: devise = "USD"
                            
                        facteur = 0.01 if devise == "GBP" else 1.0
                        if devise in ["", "NONE"]: devise = "USD"
                        if devise == "GBP": devise = "GBP"

                        prix_usd = prix_local * facteur
                        
                        if devise != "USD":
                            if devise not in taux_de_change_cache:
                                taux = 0.0
                                try: taux = float(yf.Ticker(f"{devise}USD=X").fast_info.get('lastPrice', 0.0))
                                except: pass
                                if taux <= 0.0:
                                    try:
                                        taux_inv = float(yf.Ticker(f"{devise}=X").fast_info.get('lastPrice', 0.0))
                                        if taux_inv > 0: taux = 1.0 / taux_inv
                                    except: pass
                                taux_de_change_cache[devise] = taux if taux > 0 else 1.0
                            
                            prix_usd *= taux_de_change_cache[devise]

                        df_temp.at[index, "Court"] = f"$ {prix_usd:.2f}"
                        changement = True
                except: pass
                
        if changement:
            st.session_state.donnees = df_temp
            recalculer_totaux_locaux()
            save_sheet("Donnees", st.session_state.donnees)

@st.cache_data(ttl=86400) 
def recuperer_inflation_france():
    try:
        url = "https://api.worldbank.org/v2/country/FRA/indicator/FP.CPI.TOTL.ZG?format=json&per_page=20"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            if len(data) == 2 and isinstance(data[1], list):
                inflation_dict = {}
                for item in data[1]:
                    if item['value'] is not None:
                        inflation_dict[int(item['date'])] = round(float(item['value']), 2)
                return inflation_dict
    except:
        pass
    return None

@st.cache_data(ttl=3600)
def get_historical_fx(devise, date_val):
    devise_clean = str(devise).upper().strip()
    if devise_clean in ["EUR", ""]: return 1.0
    ticker = f"{devise_clean}EUR=X"
    try:
        d = pd.to_datetime(date_val, dayfirst=True, errors='coerce')
        if pd.isna(d): return 1.0
        if d >= pd.Timestamp.now() - pd.Timedelta(days=1):
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty: return float(hist['Close'].iloc[-1])
            return 1.0
        d_start = d - pd.Timedelta(days=5)
        d_end = d + pd.Timedelta(days=1)
        hist = yf.Ticker(ticker).history(start=d_start.strftime('%Y-%m-%d'), end=d_end.strftime('%Y-%m-%d'))
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        hist_fallback = yf.Ticker(ticker).history(period="1d")
        if not hist_fallback.empty: return float(hist_fallback['Close'].iloc[-1])
    except: pass
    return 1.0

# Formules du Barème Fiscal (Standard ~2024/2025)
def calcul_frais_km(km, cv):
    if cv <= 3:
        if km <= 5000: return km * 0.529
        elif km <= 20000: return (km * 0.316) + 1065
        else: return km * 0.370
    elif cv == 4:
        if km <= 5000: return km * 0.606
        elif km <= 20000: return (km * 0.340) + 1330
        else: return km * 0.407
    elif cv == 5:
        if km <= 5000: return km * 0.636
        elif km <= 20000: return (km * 0.357) + 1395
        else: return km * 0.427
    elif cv == 6:
        if km <= 5000: return km * 0.665
        elif km <= 20000: return (km * 0.374) + 1457
        else: return km * 0.447
    else: # 7 et +
        if km <= 5000: return km * 0.697
        elif km <= 20000: return (km * 0.394) + 1515
        else: return km * 0.470

def calcul_impot_ir(revenu_net_global, parts):
    qf = revenu_net_global / parts
    impot = 0
    if qf > 11294: impot += (min(qf, 28797) - 11294) * 0.11
    if qf > 28797: impot += (min(qf, 82341) - 28797) * 0.30
    if qf > 82341: impot += (min(qf, 177106) - 82341) * 0.41
    if qf > 177106: impot += (qf - 177106) * 0.45
    return impot * parts

# --- 5. CHARGEMENT INITIAL (DEPUIS LE CLOUD) ---
if "variations" not in st.session_state: st.session_state.variations = {}

if "config" not in st.session_state:
    df_config = load_sheet("Config", ["Clé", "Valeur"])
    st.session_state.config = {}
    if not df_config.empty:
        for _, row in df_config.iterrows():
            if pd.notna(row["Clé"]):
                st.session_state.config[str(row["Clé"])] = extraire_nombre(row["Valeur"])

if "apport_dispo" not in st.session_state.config: st.session_state.config["apport_dispo"] = 0.0
if "retraite_apport_mensuel" not in st.session_state.config: st.session_state.config["retraite_apport_mensuel"] = 250.0
if "retraite_taxe" not in st.session_state.config: st.session_state.config["retraite_taxe"] = 30.0

if "apport_dispo" not in st.session_state: st.session_state.apport_dispo = float(st.session_state.config["apport_dispo"])
if "donnees" not in st.session_state: st.session_state.donnees = nettoyer_dataframe(load_sheet("Donnees", ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]))

if "historique" not in st.session_state:
    df_h = load_sheet("Historique", ["Date", "Type", "Montant $", "Montant €", "Montant Or"])
    for col in ["Montant $", "Montant €", "Montant Or"]:
        if col in df_h.columns: df_h[col] = df_h[col].apply(extraire_nombre)
    st.session_state.historique = df_h

if "projections" not in st.session_state: st.session_state.projections = recalculer_toute_la_base_projections(load_sheet("Projections", []))
elif "TG_Evolution cumulée $" not in st.session_state.projections.columns: st.session_state.projections = recalculer_toute_la_base_projections(st.session_state.projections)

if "inflation" not in st.session_state:
    df_infl = load_sheet("Inflation", ["Année", "Inflation (%)"])
    if not df_infl.empty and 'Année' in df_infl.columns: 
        df_infl['Année'] = pd.to_numeric(df_infl['Année'], errors='coerce').fillna(0).astype(int)
        df_infl['Inflation (%)'] = pd.to_numeric(df_infl['Inflation (%)'], errors='coerce').fillna(0.0)
        df_infl.drop_duplicates(subset=['Année'], keep='last', inplace=True)
    st.session_state.inflation = df_infl

if "transactions" not in st.session_state:
    df_trans = load_sheet("Transaction", ["Ticker", "Type", "Date", "Quantité", "Cours", "Frais", "Montant Net", "Devise"])
    for col in ["Quantité", "Cours", "Frais", "Montant Net"]:
        if col in df_trans.columns: df_trans[col] = df_trans[col].apply(extraire_nombre)
    st.session_state.transactions = df_trans

if "inflation_check_done" not in st.session_state:
    st.session_state.inflation_check_done = True
    dict_infl = recuperer_inflation_france()
    if dict_infl is not None and not st.session_state.projections.empty:
        df_p_temp = st.session_state.projections.copy()
        df_p_temp['Date_DT'] = pd.to_datetime(df_p_temp['Date'], dayfirst=True, errors='coerce')
        annees_portefeuille = df_p_temp.dropna(subset=['Date_DT'])['Date_DT'].dt.year.unique()
        df_infl_temp = st.session_state.inflation.copy()
        nouveau_infl = []
        changement = False
        for a in annees_portefeuille:
            val_officielle = 0.0
            if a in dict_infl: val_officielle = dict_infl[a]
            val_actuelle = 0.0
            if not df_infl_temp[df_infl_temp['Année'] == a].empty:
                val_actuelle = df_infl_temp[df_infl_temp['Année'] == a].iloc[0]['Inflation (%)']
            if val_officielle != val_actuelle: changement = True
            nouveau_infl.append({'Année': a, 'Inflation (%)': val_officielle})
        if changement:
            st.session_state.inflation = pd.DataFrame(nouveau_infl)
            save_sheet("Inflation", st.session_state.inflation)

if "dernier_refresh_cours" not in st.session_state: st.session_state.dernier_refresh_cours = 0
maintenant = time.time()
if maintenant - st.session_state.dernier_refresh_cours >= 900:
    actualiser_cours_internet(silencieux=(st.session_state.dernier_refresh_cours == 0))
    st.session_state.dernier_refresh_cours = maintenant

# --- 6. NAVIGATION ---
st.sidebar.title("Menu")
page_choisie = st.sidebar.radio("Aller vers :", ["📊 Tableau de bord", "📋 Liste des actifs", "⚖️ Rééquilibrage", "💰 Fonds", "🏖️ Suivi", "📈 Performance", "🌴 Retraite", "🏛️ Fiscalité"])

st.sidebar.divider()
if st.sidebar.button("🔄 Recharger l'application", use_container_width=True):
    st.session_state.clear()
    st.rerun()

# --- 7. PAGES DE L'APPLICATION ---
if page_choisie == "📊 Tableau de bord":
    st.title("📊 Vue d'ensemble de mon Patrimoine")
    df_actuel = st.session_state.donnees
    df_p = st.session_state.projections
    
    val_invest = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    val_total = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows())
    
    cap_actuel = sum(row["Montant $"] if "ajout" in row["Type"].lower() else -row["Montant $"] for _, row in st.session_state.historique.iterrows())
    df_p_live = df_p.copy()
    ligne_live = pd.DataFrame([{"Date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "Capital investi": cap_actuel, "Actifs Stratégiques": val_invest, "Total Global": val_total}])
    df_p_live = pd.concat([df_p_live, ligne_live], ignore_index=True)
    df_p_live = recalculer_toute_la_base_projections(df_p_live)
    
    def parse_var_jour(ticker):
        var_str = st.session_state.variations.get(ticker, "0")
        match = re.search(r'([+-]?\d+\.?\d*)', var_str)
        return float(match.group(1)) if match else 0.0

    var_jour_total_global_usd = var_jour_total_usd = val_total_veille = val_invest_veille = 0.0
    for _, r in df_actuel.iterrows():
        tick = str(r.get("Ticker", "")).strip().upper()
        v_actuelle = extraire_nombre(r["Valeur totale"])
        v_pct = parse_var_jour(tick)
        v_veille = v_actuelle / (1 + v_pct / 100) if (1 + v_pct / 100) != 0 else v_actuelle
        
        var_jour_total_global_usd += (v_actuelle - v_veille)
        val_total_veille += v_veille
        if extraire_nombre(r["Pourcentage (%)"]) > 0:
            var_jour_total_usd += (v_actuelle - v_veille)
            val_invest_veille += v_veille
            
    pct_jour_total_global = (var_jour_total_global_usd / val_total_veille * 100) if val_total_veille > 0 else 0.0
    pct_jour_total = (var_jour_total_usd / val_invest_veille * 100) if val_invest_veille > 0 else 0.0
    
    delta = pct_delta = delta_tg = pct_delta_tg = 0.0
    if not df_p.empty:
        df_p_dates = df_p.copy()
        df_p_dates['Date_DT'] = pd.to_datetime(df_p_dates['Date'], dayfirst=True, errors='coerce')
        df_p_dates = df_p_dates.dropna(subset=['Date_DT']).sort_values('Date_DT')
        if not df_p_dates.empty:
            now_dt = pd.Timestamp.now()
            target_dt = now_dt - pd.DateOffset(years=1) 
            df_past = df_p_dates[df_p_dates['Date_DT'] <= target_dt]
            row_ref = df_past.iloc[-1] if not df_past.empty else df_p_dates.iloc[0] 
            
            val_ref_strat = extraire_nombre(row_ref["Actifs Stratégiques"])
            delta = val_invest - val_ref_strat
            if val_ref_strat > 0: pct_delta = (delta / val_ref_strat) * 100
            
            val_ref_tg = extraire_nombre(row_ref["Total Global"])
            delta_tg = val_total - val_ref_tg
            if val_ref_tg > 0: pct_delta_tg = (delta_tg / val_ref_tg) * 100

    besoin_reequilibrage = False
    if val_invest > 0:
        for _, row in df_actuel.iterrows():
            pct_cib = extraire_nombre(row["Pourcentage (%)"]) / 100
            if pct_cib == 0: continue
            val_act = extraire_nombre(row["Valeur totale"])
            diff = (val_invest * pct_cib) - val_act
            pct_reel = (val_act / val_invest) * 100
            if abs(diff) >= 1000 and abs(pct_reel - (pct_cib * 100)) >= 2.0:
                besoin_reequilibrage = True
                break

    st.subheader("⚙️ 1. Pilotage & Statut")
    col_btn, col_statut = st.columns([1, 2])
    with col_btn:
        if st.button("🔄 Actualiser les cours", use_container_width=True):
            actualiser_cours_internet(silencieux=False)
            st.rerun()
    with col_statut:
        if besoin_reequilibrage: st.warning("⚠️ **Rééquilibrage nécessaire** (Certains actifs ont dépassé les tolérances.)")
        else: st.success("✅ **Équilibré** (Votre stratégie d'allocation cible est respectée.)")
    st.divider()

    st.subheader("🌍 2. Total Global (Toutes liquidités incluses)")
    col_tg_met, col_tg_vide = st.columns(2)
    with col_tg_met:
        afficher_montant_double("Total Global", val_total, f"{delta_tg:+,.2f} $ ({pct_delta_tg:+.2f} % sur 1 an glissant)")
        color_jour_tg = "#2ecc71" if var_jour_total_global_usd >= 0 else "#e74c3c"
        symbole_jour_tg = "📈" if var_jour_total_global_usd >= 0 else "📉"
        st.markdown(f"<div style='margin-top: -0.5rem; margin-bottom: 1rem;'><span style='font-size: 1.1em;'>{symbole_jour_tg} Aujourd'hui : <strong style='color:{color_jour_tg}'>{var_jour_total_global_usd:+,.2f} $ ({pct_jour_total_global:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    
    if not df_p.empty:
        df_viz_tg = df_p_live.copy()
        df_viz_tg['Date_DT'] = pd.to_datetime(df_viz_tg['Date'], dayfirst=True, errors='coerce')
        df_viz_tg = df_viz_tg.dropna(subset=['Date_DT']).sort_values('Date_DT').reset_index(drop=True)
        st.markdown("**📈 Évolution & Performance globale**")
        c_f1_tg, c_f2_tg = st.columns(2)
        with c_f1_tg: filtre_tg = st.radio("Période globale :", ["Depuis le début", "Depuis 1 an", "Depuis le début de l'année"], horizontal=True, key="filtre_tg")
        with c_f2_tg: mode_graph_tg = st.radio("Affichage :", ["Rendement Absolu (ROI)", "Score TWR (Talent)"], horizontal=True, key="mode_tg")
        now = pd.Timestamp.now()
        if filtre_tg == "Depuis 1 an": df_viz_tg = df_viz_tg[df_viz_tg['Date_DT'] >= (now - pd.DateOffset(years=1))]
        elif filtre_tg == "Depuis le début de l'année": df_viz_tg = df_viz_tg[df_viz_tg['Date_DT'] >= pd.Timestamp(year=now.year - 1, month=12, day=31)]
        if df_viz_tg.empty: st.warning("Aucun enregistrement trouvé pour cette période.")
        else:
            df_viz_tg.set_index('Date_DT', inplace=True)
            val_debut_tg, val_fin_tg = df_viz_tg['TG_Evolution cumulée $'].iloc[0], df_viz_tg['TG_Evolution cumulée $'].iloc[-1]
            actifs_debut_tg = df_viz_tg['Total Global'].iloc[0]
            delta_usd_tg = val_fin_tg - val_debut_tg
            pct_periode_tg = (delta_usd_tg / actifs_debut_tg * 100) if actifs_debut_tg > 0 else 0.0
            
            c1_g_tg, c2_g_tg = st.columns([1, 3])
            with c1_g_tg:
                if "ROI" in mode_graph_tg:
                    afficher_montant_double("Gains nets globaux", val_fin_tg, f"{delta_usd_tg:+,.2f} $ ({pct_periode_tg:+.2f} % sur la période)", taille="medium")
                else:
                    st.metric("Score TWR Global (%)", f"{df_viz_tg['TG_Score TWR %'].iloc[-1]:+.2f} %")
                    afficher_montant_double("Gains nets actuels", val_fin_tg, taille="medium")
            with c2_g_tg:
                col_y_tg = 'TG_Evolution cumulée $' if "ROI" in mode_graph_tg else 'TG_Score TWR %'
                fig_line_tg = px.line(df_viz_tg.reset_index(), x='Date_DT', y=col_y_tg)
                fig_line_tg.update_traces(line_shape='spline')
                fig_line_tg.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
                fig_line_tg.update_xaxes(tickformat="%d/%m/%Y")
                st.plotly_chart(fig_line_tg, use_container_width=True)

        st.markdown("**🌍 Répartition du Patrimoine (Total Global)**")
        c_tg_p1, c_tg_p2 = st.columns(2)
        with c_tg_p1:
            df_actifs_global = st.session_state.donnees.copy()
            df_actifs_global['Val_Num'] = df_actifs_global['Valeur totale'].apply(extraire_nombre)
            df_pie_tg = df_actifs_global[df_actifs_global['Val_Num'] > 0].groupby('Type')['Val_Num'].sum().reset_index()
            if not df_pie_tg.empty:
                fig_tg = px.pie(df_pie_tg, values='Val_Num', names='Type', color='Type', color_discrete_map={"🛢️ Action": "#e74c3c", "📜 Obligation": "#3498db", "💰 Or": "#f1c40f", "₿ Crypto": "#9b59b6", "💵 Cash": "#2ecc71"}, hole=0.4)
                fig_tg.update_traces(textposition='inside', textinfo='percent+label')
                fig_tg.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
                st.plotly_chart(fig_tg, use_container_width=True)
    st.divider()

    st.subheader("🎯 3. Actifs Stratégiques (Investissements cibles)")
    col_strat_met, col_strat_vide = st.columns(2)
    with col_strat_met:
        afficher_montant_double("Actifs Stratégiques", val_invest, f"{delta:+,.2f} $ ({pct_delta:+.2f} % sur 1 an glissant)")
        color_jour = "#2ecc71" if var_jour_total_usd >= 0 else "#e74c3c"
        symbole_jour = "📈" if var_jour_total_usd >= 0 else "📉"
        st.markdown(f"<div style='margin-top: -0.5rem; margin-bottom: 1rem;'><span style='font-size: 1.1em;'>{symbole_jour} Aujourd'hui : <strong style='color:{color_jour}'>{var_jour_total_usd:+,.2f} $ ({pct_jour_total:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    
    if not df_p.empty:
        df_viz = df_p_live.copy()
        df_viz['Date_DT'] = pd.to_datetime(df_viz['Date'], dayfirst=True, errors='coerce')
        df_viz = df_viz.dropna(subset=['Date_DT']).sort_values('Date_DT').reset_index(drop=True)
        st.markdown("**📈 Évolution & Performance de la stratégie**")
        c_f1, c_f2 = st.columns(2)
        with c_f1: filtre = st.radio("Sélectionnez la période :", ["Depuis le début", "Depuis 1 an", "Depuis le début de l'année"], horizontal=True, key="filtre_strat")
        with c_f2: mode_graph = st.radio("Affichage :", ["Rendement Absolu (ROI)", "Score TWR (Talent)"], horizontal=True, key="mode_strat")
        now = pd.Timestamp.now()
        if filtre == "Depuis 1 an": df_viz = df_viz[df_viz['Date_DT'] >= (now - pd.DateOffset(years=1))]
        elif filtre == "Depuis le début de l'année": df_viz = df_viz[df_viz['Date_DT'] >= pd.Timestamp(year=now.year - 1, month=12, day=31)]
        if df_viz.empty: st.warning("Aucun enregistrement.")
        else:
            df_viz.set_index('Date_DT', inplace=True)
            val_debut, val_fin = df_viz['Evolution cumulée $'].iloc[0], df_viz['Evolution cumulée $'].iloc[-1]
            delta_usd = val_fin - val_debut
            pct_periode = (delta_usd / df_viz['Actifs Stratégiques'].iloc[0] * 100) if df_viz['Actifs Stratégiques'].iloc[0] > 0 else 0.0
            c1_g, c2_g = st.columns([1, 3])
            with c1_g:
                if "ROI" in mode_graph:
                    afficher_montant_double("Gains nets de la stratégie", val_fin, f"{delta_usd:+,.2f} $ ({pct_periode:+.2f} % sur la période)", taille="medium")
                else:
                    st.metric("Score TWR Stratégique (%)", f"{df_viz['Score TWR %'].iloc[-1]:+.2f} %")
            with c2_g:
                col_y = 'Evolution cumulée $' if "ROI" in mode_graph else 'Score TWR %'
                fig_line = px.line(df_viz.reset_index(), x='Date_DT', y=col_y)
                fig_line.update_traces(line_shape='spline')
                fig_line.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
                st.plotly_chart(fig_line, use_container_width=True)

    st.markdown("**🎯 Répartition détaillée de la stratégie**")
    df_actifs_dash = st.session_state.donnees.copy()
    df_actifs_dash['Val_Num'] = df_actifs_dash['Valeur totale'].apply(extraire_nombre)
    df_actifs_dash['Pct_Cible'] = df_actifs_dash['Pourcentage (%)'].apply(extraire_nombre)
    df_strat = df_actifs_dash[df_actifs_dash['Pct_Cible'] > 0]
    c_p1, c_p2 = st.columns(2)
    with c_p1:
        df_pie1 = df_strat[df_strat['Val_Num'] > 0].groupby('Type')['Val_Num'].sum().reset_index()
        if not df_pie1.empty:
            fig1 = px.pie(df_pie1, values='Val_Num', names='Type', color='Type', hole=0.4)
            fig1.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig1, use_container_width=True)
    with c_p2:
        if not df_strat.empty:
            fig2 = px.pie(df_strat[df_strat['Val_Num'] > 0], values='Val_Num', names='Ticker', hole=0.4)
            fig2.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig2, use_container_width=True)
    st.divider()

    st.subheader("🏖️ 4. Liberté Financière (Rente Mensuelle actuelle)")
    c_rente1, c_rente2 = st.columns(2)
    with c_rente1: inf_estimee_dash = st.slider("Inflation cible à déduire (%) ✍️", min_value=0.0, max_value=15.0, value=2.0, step=0.1, key="dash_infl")
    with c_rente2:
        taux_reel = ((1 + 0.08) / (1 + (inf_estimee_dash / 100.0))) - 1
        afficher_montant_double("Rente Mensuelle Nette (Base 8% par an)", (val_invest * max(0.0, taux_reel)) / 12.0, couleur_valeur="#3498db")

elif page_choisie == "📋 Liste des actifs":
    st.title("📋 Liste de mes actifs")
    df_actuel = st.session_state.donnees.copy()
    val_invest = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    val_total = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows())
    somme_p = sum(extraire_nombre(r["Pourcentage (%)"]) for _, r in df_actuel.iterrows())

    c1, c2, c3 = st.columns(3)
    with c1: afficher_montant_double("Actifs Stratégiques", val_invest)
    with c2: afficher_montant_double("Total Global", val_total)
    with c3:
        ecart = round(100 - somme_p, 2)
        info_str = "✅ Cible atteinte" if ecart == 0 else (f"⚠️ {ecart:.2f} % manquant" if ecart > 0 else f"⚠️ {abs(ecart):.2f} % en trop")
        st.metric("Répartition Cible", f"{somme_p:.2f} %", info_str, delta_color="normal" if ecart==0 else "inverse")
    st.divider()

    if st.button("🔄 Actualiser les cours", use_container_width=True):
        actualiser_cours_internet(silencieux=False)
        st.rerun()

    df_actuel['Var. Jour 🔒'] = df_actuel['Ticker'].apply(lambda x: st.session_state.variations.get(str(x).upper(), "→ 0.00 %"))
    display_cols = ["Ticker", "Type", "Court", "Quantité", "Valeur totale", "Pourcentage (%)", "Var. Jour 🔒"]
    
    def color_var(v):
        v_str = str(v)
        if "↗" in v_str or "+" in v_str: return 'color: #2ecc71'
        if "↘" in v_str or "-" in v_str: return 'color: #e74c3c'
        return 'color: #95a5a6'
    
    m_dev = df_actuel.apply(lambda row: est_devise_liquide(row.get("Ticker", "")), axis=1)
    res_i = st.data_editor(df_actuel[~m_dev][display_cols].style.map(color_var, subset=["Var. Jour 🔒"]), key="ei", use_container_width=True, hide_index=True, num_rows="dynamic")
    res_d = st.data_editor(df_actuel[m_dev][display_cols].style.map(color_var, subset=["Var. Jour 🔒"]), key="ed", use_container_width=True, hide_index=True, num_rows="dynamic")

    new_df = pd.concat([res_i, res_d], ignore_index=True)
    core_cols = ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]
    if not new_df[core_cols].equals(st.session_state.donnees[core_cols]):
        st.session_state.donnees = new_df[core_cols]
        recalculer_totaux_locaux()
        save_sheet("Donnees", st.session_state.donnees)
        st.rerun()

elif page_choisie == "⚖️ Rééquilibrage":
    st.title("⚖️ Stratégie de Rééquilibrage")
    if st.button("🔄 Actualiser les cours", use_container_width=True):
        actualiser_cours_internet(silencieux=False)
        st.rerun()
        
    def on_apport_change():
        st.session_state.config["apport_dispo"] = st.session_state.apport_input
        save_sheet("Config", pd.DataFrame(list(st.session_state.config.items()), columns=["Clé", "Valeur"]))

    cash_dispo = st.number_input("💵 Nouvel apport à investir ($) ✍️", min_value=0.00, step=100.00, value=float(st.session_state.config.get("apport_dispo", 0.0)), key="apport_input", on_change=on_apport_change)
    st.divider()
    
    df = st.session_state.donnees
    base_reeq = sum(extraire_nombre(r["Valeur totale"]) for _, r in df.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    new_base = base_reeq + cash_dispo
    
    if new_base > 0:
        reeq_list = []
        for _, row in df.iterrows():
            pct_cib = extraire_nombre(row["Pourcentage (%)"]) / 100
            if pct_cib == 0: continue
            val_act = extraire_nombre(row["Valeur totale"])
            diff = (new_base * pct_cib) - val_act
            prix = extraire_nombre(row["Court"])
            qte = diff / prix if prix > 0 else 0
            pct_reel = (val_act / new_base) * 100
            
            action = f"✅ ÉQUILIBRÉ ($ {abs(diff):,.2f})" if abs(diff) < 1000 or abs(pct_reel - (pct_cib * 100)) < 2.0 else (f"🟢 ACHETER $ {abs(diff):,.2f}" if diff > 0 else f"🔴 VENDRE $ {abs(diff):,.2f}")
            reeq_list.append({"Ticker 🔒": str(row["Ticker"]).upper(), "Actuel ($) 🔒": val_act, "Écart (%) 🔒": pct_reel - (pct_cib * 100), "Action 🔒": action, "Qté (+/-) 🔒": f"{qte:+.2f}"})
        st.dataframe(pd.DataFrame(reeq_list).style.format({"Actuel ($) 🔒": "$ {:,.2f}", "Écart (%) 🔒": "{:+.2f} %"}), use_container_width=True, hide_index=True)

elif page_choisie == "💰 Fonds":
    st.title("💰 Fonds")
    df_h = st.session_state.historique
    with st.expander("➕ Nouveau mouvement"):
        with st.form("f_m"):
            d_m, t_m = st.date_input("Date ✍️"), st.radio("Type ✍️", ["Ajout de fond propre", "Retrait"], horizontal=True)
            m_s, d_s = st.number_input("Montant ✍️", min_value=0.00, format="%.2f"), st.selectbox("Devise ✍️", ["$", "€"])
            if st.form_submit_button("Valider"):
                m_usd = m_s if d_s == "$" else m_s * TAUX_EUR_USD
                nl = {"Date": d_m.strftime("%d/%m/%Y"), "Type": t_m, "Montant $": m_usd, "Montant €": m_usd/TAUX_EUR_USD, "Montant Or": m_usd/float(yf.Ticker("GC=F").fast_info.get('lastPrice', 2000.0))}
                st.session_state.historique = pd.concat([df_h, pd.DataFrame([nl])], ignore_index=True)
                save_sheet("Historique", st.session_state.historique)
                if t_m == "Ajout de fond propre":
                    st.session_state.config["apport_dispo"] = float(st.session_state.config.get("apport_dispo", 0)) + m_usd
                    save_sheet("Config", pd.DataFrame(list(st.session_state.config.items()), columns=["Clé", "Valeur"]))
                st.rerun()
    
    afficher_montant_double("Total Apports nets", sum(row["Montant $"] if "ajout" in row["Type"].lower() else -row["Montant $"] for _, row in df_h.iterrows()))
    st.dataframe(df_h.sort_index(ascending=False), use_container_width=True, hide_index=True)

elif page_choisie == "🏖️ Suivi":
    st.title("🏖️ Suivi & Évolution")
    if not st.session_state.projections.empty: st.dataframe(st.session_state.projections.sort_index(ascending=False), use_container_width=True, hide_index=True)

elif page_choisie == "📈 Performance":
    st.title("📈 Performances Annuelles & Inflation")
    df_p = st.session_state.projections
    if df_p.empty: st.info("Aucune donnée disponible.")
    else:
        df_viz = df_p.copy()
        df_viz['DT'] = pd.to_datetime(df_viz['Date'], dayfirst=True)
        df_y = df_viz.groupby(df_viz['DT'].dt.year).last().reset_index()
        df_y['Perf. brute (%)'] = df_y['Score TWR %'].pct_change().fillna(0) * 100
        df_y = df_y.merge(st.session_state.inflation, left_on='DT', right_on='Année', how='left').fillna(0)
        st.dataframe(df_y, use_container_width=True, hide_index=True)

elif page_choisie == "🌴 Retraite":
    st.title("🌴 Simulateur d'Indépendance Financière")
    
    def on_retraite_change():
        st.session_state.config["retraite_apport_mensuel"] = st.session_state.ret_app
        st.session_state.config["retraite_taxe"] = st.session_state.ret_tax
        save_sheet("Config", pd.DataFrame(list(st.session_state.config.items()), columns=["Clé", "Valeur"]))

    c1, c2, c3 = st.columns(3)
    with c1: app_m = st.number_input("Apport mensuel ($) ✍️", value=float(st.session_state.config.get("retraite_apport_mensuel", 250.0)), key="ret_app", on_change=on_retraite_change)
    with c3: taxe = st.number_input("Flat Tax (%) ✍️", value=float(st.session_state.config.get("retraite_taxe", 30.0)), key="ret_tax", on_change=on_retraite_change)

elif page_choisie == "🏛️ Fiscalité":
    st.title("🏛️ Simulateur Fiscal (Détail par Actif & PRU)")
    st.write("Cet outil calcule vos plus-values à partir de votre feuille 'Transaction', choisit la meilleure imposition, et estime le montant exact de votre impôt.")

    if not st.session_state.transactions.empty:
        annees_dispos = sorted(pd.to_datetime(st.session_state.transactions['Date'], dayfirst=True, errors='coerce').dropna().dt.year.unique().tolist(), reverse=True)
    else: annees_dispos = []
    if not annees_dispos: annees_dispos = [datetime.datetime.now().year]
    annee_fiscale = st.selectbox("📅 Sélectionner l'année des revenus (à déclarer l'année suivante) :", annees_dispos)
    
    st.divider()

    st.subheader("👤 1. Ma Situation Familiale & Professionnelle")
    c_sit1, c_sit2 = st.columns(2)
    with c_sit1:
        statut = st.radio("Situation matrimoniale ✍️", ["Célibataire / Divorcé(e) / Veuf(ve)", "Marié(e) / Pacsé(e)"])
        enfants = st.number_input("Nombre d'enfants à charge ✍️", min_value=0, max_value=10, value=0, step=1)
    with c_sit2:
        salaire_1 = st.number_input("Vos revenus nets imposables (Salaires, etc.) en € ✍️", min_value=0.0, value=30000.0, step=1000.0)
        salaire_2 = 0.0
        if "Marié" in statut:
            salaire_2 = st.number_input("Revenus nets imposables conjoint(e) en € ✍️", min_value=0.0, value=30000.0, step=1000.0)
        salaire_total = salaire_1 + salaire_2

    st.markdown("---")
    st.markdown("#### 🚗 Frais Professionnels (Frais Réels)")
    use_frais_reels = st.checkbox("Déclarer aux frais réels (Calcul précis au lieu de l'abattement standard de 10%)")
    frais_reels = 0.0
    
    if use_frais_reels:
        c_frais1, c_frais2, c_frais3 = st.columns(3)
        km = c_frais1.number_input("Kilomètres annuels (Trajet pro) ✍️", min_value=0, max_value=100000, value=0, step=1000)
        cv = c_frais2.selectbox("Puissance du véhicule (CV) ✍️", [3, 4, 5, 6, 7], help="7 correspond à 7 CV et plus.")
        repas = c_frais3.number_input("Jours de repas au travail ✍️", min_value=0, max_value=300, value=0, step=10)

        # Calcul Barème kilométrique
        frais_km = 0.0
        if cv <= 3:
            if km <= 5000: frais_km = km * 0.529
            elif km <= 20000: frais_km = (km * 0.316) + 1065
            else: frais_km = km * 0.370
        elif cv == 4:
            if km <= 5000: frais_km = km * 0.606
            elif km <= 20000: frais_km = (km * 0.340) + 1330
            else: frais_km = km * 0.407
        elif cv == 5:
            if km <= 5000: frais_km = km * 0.636
            elif km <= 20000: frais_km = (km * 0.357) + 1395
            else: frais_km = km * 0.427
        elif cv == 6:
            if km <= 5000: frais_km = km * 0.665
            elif km <= 20000: frais_km = (km * 0.374) + 1457
            else: frais_km = km * 0.447
        else:
            if km <= 5000: frais_km = km * 0.697
            elif km <= 20000: frais_km = (km * 0.394) + 1515
            else: frais_km = km * 0.470
            
        frais_repas = repas * 5.35 # Valeur forfaitaire standard DGFIP
        frais_reels = frais_km + frais_repas
        st.info(f"💰 Estimation de vos Frais Réels déductibles : **{frais_reels:,.2f} €** (Km: {frais_km:,.2f} € + Repas: {frais_repas:,.2f} €)")

    st.divider()

    # --- MOTEUR DE CALCUL PRU ET PV ---
    df_all = st.session_state.transactions.copy()
    df_all['Date_DT'] = pd.to_datetime(df_all['Date'], dayfirst=True, errors='coerce')
    df_all = df_all.dropna(subset=['Date_DT']).sort_values('Date_DT')

    pru_data = {}
    rapport_fiscal = []

    for idx, row in df_all.iterrows():
        t = str(row['Ticker']).upper()
        if est_devise_liquide(t): continue
        
        type_t = str(row['Type']).lower().strip()
        qte = float(row['Quantité'])
        net = float(row['Montant Net'])
        devise = str(row['Devise']).strip().upper()
        date_t = row['Date']
        annee_trans = row['Date_DT'].year
        
        if t not in pru_data: pru_data[t] = {"qte": 0.0, "cout_total": 0.0}
        
        if "achat" in type_t:
            pru_data[t]["qte"] += qte
            pru_data[t]["cout_total"] += net
        elif "vente" in type_t:
            current_pru = pru_data[t]["cout_total"] / pru_data[t]["qte"] if pru_data[t]["qte"] > 0 else 0.0
            cout_de_la_vente = current_pru * qte
            pv_devise = net - cout_de_la_vente
            taux_eur = get_historical_fx(devise, date_t)
            pv_eur = pv_devise * taux_eur
            
            pru_data[t]["cout_total"] -= cout_de_la_vente
            pru_data[t]["qte"] -= qte
            
            if annee_trans == annee_fiscale:
                is_crypto = "BTC" in t or "ETH" in t or t.endswith("USDT")
                rapport_fiscal.append({
                    "Actif": t, "Date de vente": date_t, "Quantité vendue": qte,
                    "PRU Moyen (Devise)": current_pru, "Prix de revente net (Devise)": net,
                    "Plus-value (Devise)": pv_devise, "Devise": devise,
                    "Taux de change (Vers EUR)": taux_eur, "Plus-value (€)": pv_eur,
                    "Catégorie": "Crypto" if is_crypto else "Action/ETF"
                })

    df_fiscal = pd.DataFrame(rapport_fiscal)

    st.subheader(f"📝 2. Détail des Ventes (Année {annee_fiscale})")
    
    if df_fiscal.empty:
        st.info(f"Aucune cession d'actifs (actions ou cryptos) détectée dans la feuille 'Transaction' pour l'année {annee_fiscale}.")
        plus_values_actions = moins_values_actions = 0.0
        plus_values_crypto = moins_values_crypto = 0.0
    else:
        st.write("Ce tableau est généré automatiquement d'après vos transactions. Les conversions en Euros utilisent les taux de change historiques exacts du jour de chaque vente.")
        
        df_actions = df_fiscal[df_fiscal["Catégorie"] == "Action/ETF"]
        df_cryptos = df_fiscal[df_fiscal["Catégorie"] == "Crypto"]
        
        plus_values_actions = df_actions[df_actions["Plus-value (€)"] > 0]["Plus-value (€)"].sum()
        moins_values_actions = abs(df_actions[df_actions["Plus-value (€)"] < 0]["Plus-value (€)"].sum())
        
        plus_values_crypto = df_cryptos[df_cryptos["Plus-value (€)"] > 0]["Plus-value (€)"].sum()
        moins_values_crypto = abs(df_cryptos[df_cryptos["Plus-value (€)"] < 0]["Plus-value (€)"].sum())

        actifs_vendus = sorted(df_fiscal["Actif"].unique().tolist())
        tabs = st.tabs(actifs_vendus)
        
        for i, actif in enumerate(actifs_vendus):
            with tabs[i]:
                df_actif = df_fiscal[df_fiscal["Actif"] == actif].copy()
                st.dataframe(
                    df_actif.drop(columns=["Actif", "Catégorie"]),
                    column_config={
                        "PRU Moyen (Devise)": st.column_config.NumberColumn(format="%.2f"),
                        "Prix de revente net (Devise)": st.column_config.NumberColumn(format="%.2f"),
                        "Plus-value (Devise)": st.column_config.NumberColumn(format="%.2f"),
                        "Taux de change (Vers EUR)": st.column_config.NumberColumn(format="%.4f"),
                        "Plus-value (€)": st.column_config.NumberColumn(format="%.2f €")
                    },
                    use_container_width=True, hide_index=True
                )
                res_actif = df_actif["Plus-value (€)"].sum()
                color_res = "green" if res_actif >= 0 else "red"
                st.markdown(f"*Bilan de l'année pour **{actif}** : <strong style='color:{color_res}'>{res_actif:+.2f} €</strong>*", unsafe_allow_html=True)

    bilan_net_actions = plus_values_actions - moins_values_actions
    bilan_net_crypto = plus_values_crypto - moins_values_crypto

    st.divider()

    # --- CALCULS FISCAUX ---
    parts = 1.0 if "Célibataire" in statut else 2.0
    if enfants == 1: parts += 0.5
    elif enfants == 2: parts += 1.0
    elif enfants >= 3: parts += 1.0 + (enfants - 2)

    # Détermination du revenu imposable (Abattement 10% ou Frais réels)
    abattement_10 = salaire_total * 0.10
    deduction_appliquee = max(abattement_10, frais_reels)
    revenu_base_net = salaire_total - deduction_appliquee

    def calcul_impot_ir(revenu_net_global, nb_parts):
        qf = revenu_net_global / nb_parts
        impot = 0
        if qf > 11294: impot += (min(qf, 28797) - 11294) * 0.11
        if qf > 28797: impot += (min(qf, 82341) - 28797) * 0.30
        if qf > 82341: impot += (min(qf, 177106) - 82341) * 0.41
        if qf > 177106: impot += (qf - 177106) * 0.45
        return impot * nb_parts

    impot_salaires_seuls = calcul_impot_ir(revenu_base_net, parts)
    
    # Calcul du TMI sur les salaires seuls
    qf_base = revenu_base_net / parts
    if qf_base <= 11294: tmi = 0
    elif qf_base <= 28797: tmi = 11
    elif qf_base <= 82341: tmi = 30
    elif qf_base <= 177106: tmi = 41
    else: tmi = 45

    st.subheader("💡 3. Recommandation d'imposition (Actions/ETF)")
    
    if df_fiscal.empty or (plus_values_actions == 0 and moins_values_actions == 0):
        choix = "Aucun"
    elif bilan_net_actions <= 0:
        st.success("✅ **Bilan Négatif ou Nul :** Vous n'avez pas d'impôts à payer sur vos cessions boursières classiques cette année.")
        choix = "Aucun (Bilan négatif)"
    else:
        # PFU (Flat Tax)
        cout_pfu = bilan_net_actions * 0.30
        
        # Barème Progressif
        impot_avec_bourse = calcul_impot_ir(revenu_base_net + bilan_net_actions, parts)
        surcout_ir = impot_avec_bourse - impot_salaires_seuls
        prelevements_sociaux = bilan_net_actions * 0.172
        cout_bareme = surcout_ir + prelevements_sociaux
        
        taux_moyen_bareme = (cout_bareme / bilan_net_actions) * 100

        if cout_bareme < cout_pfu:
            st.success("✅ **Le Barème Progressif est plus avantageux pour vous !**")
            st.write(f"Sur vos {bilan_net_actions:,.2f} € de plus-values nettes :")
            st.write(f"- Avec la Flat Tax (30%) : l'impôt serait de **{cout_pfu:,.2f} €**.")
            st.write(f"- Avec le Barème : l'impôt est de **{cout_bareme:,.2f} €** *(Taux d'imposition effectif sur vos plus-values : {taux_moyen_bareme:.1f} %)*.")
            choix = "Barème"
        else:
            st.success("✅ **La Flat Tax (PFU) est plus avantageuse pour vous !**")
            st.write(f"Sur vos {bilan_net_actions:,.2f} € de plus-values nettes :")
            st.write(f"- Avec le Barème, la hausse de vos revenus vous ferait basculer dans les tranches hautes, l'impôt serait de **{cout_bareme:,.2f} €** *(Taux d'imposition effectif sur vos plus-values : {taux_moyen_bareme:.1f} %)*.")
            st.write(f"- Avec la Flat Tax : l'impôt est plafonné à **{cout_pfu:,.2f} €** (Exactement 30%).")
            choix = "PFU"

    st.divider()
    st.subheader("📝 4. Résumé pour votre déclaration d'impôts")
    st.caption("⚠️ *Avertissement : Ce simulateur est une aide indicative.*")
    
    c_decl1, c_decl2 = st.columns(2)
    with c_decl1:
        st.markdown("### 🔹 Formulaire 3916 (Comptes étrangers)")
        st.markdown("- **Case 8UU (sur la 2042) :** À cocher.")
        st.markdown("- **Informations à fournir sur le 3916 :**")
        st.markdown("  - *Intitulé :* Swissquote Bank SA")
        st.markdown("  - *Adresse :* Chemin de la Crétaux 33, 1196 Gland, Suisse")
        
        st.markdown("### 🔹 Formulaire 2074 (Actions / ETF)")
        if plus_values_actions > 0: st.markdown(f"- **Ligne 905 :** {plus_values_actions:,.0f} €")
        if moins_values_actions > 0: st.markdown(f"- **Ligne 913 :** {moins_values_actions:,.0f} €")
        
    with c_decl2:
        st.markdown("### 🔹 Formulaire 2086 (Cryptomonnaies)")
        if bilan_net_crypto > 0: st.markdown(f"- **Case 3AN** (Plus-value) : **{bilan_net_crypto:,.0f} €**")
        elif bilan_net_crypto < 0: st.markdown(f"- **Case 3BN** (Moins-value) : **{abs(bilan_net_crypto):,.0f} €**")
        else: st.markdown("- Aucune plus ou moins-value crypto cette année.")

        st.markdown("### 🔹 Déclaration Principale (Formulaire 2042)")
        if bilan_net_actions > 0:
            st.markdown(f"- **Case 3VG** (Plus-values nettes) : Indiquer **{bilan_net_actions:,.0f} €**")
            if choix == "Barème": st.markdown("- **Case 2OP** : **À cocher absolument**.")
            else: st.markdown("- **Case 2OP** : **À laisser DÉCOCHÉE**.")
        elif bilan_net_actions < 0:
            st.markdown(f"- **Case 3VH** (Moins-values nettes) : Indiquer **{abs(bilan_net_actions):,.0f} €**")
