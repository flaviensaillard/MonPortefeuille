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
    if "password_correct" not in st.session_state: st.session_state["password_correct"] = False
    if not st.session_state["password_correct"]:
        st.markdown("<h1 style='text-align: center;'>🔒 Accès Sécurisé</h1>", unsafe_allow_html=True)
        pwd = st.text_input("Veuillez entrer votre mot de passe :", type="password")
        if pwd == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            st.rerun()
        elif pwd != "": st.error("Mot de passe incorrect.")
        return False
    return True

if not check_password(): st.stop()

# --- 3. CONNEXION GOOGLE SHEETS ---
@st.cache_resource
def init_google_sheets():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    gc = gspread.authorize(credentials)
    return gc.open_by_key("1hkZoHQ1vvtbI1DYHR_OnofWn4jG92JGyxJjN-FedsWk")

try: sh = init_google_sheets()
except Exception as e:
    st.error("Erreur de connexion à Google Sheets.")
    st.stop()

def load_sheet(sheet_name, default_cols):
    try:
        ws = sh.worksheet(sheet_name)
        df = get_as_dataframe(ws, evaluate_formulas=True).dropna(how='all').dropna(axis=1, how='all')
        if df.empty: return pd.DataFrame(columns=default_cols)
        return df
    except: return pd.DataFrame(columns=default_cols)

def save_sheet(sheet_name, df):
    try: ws = sh.worksheet(sheet_name)
    except: ws = sh.add_worksheet(title=sheet_name, rows=100, cols=20)
    ws.clear()
    set_with_dataframe(ws, df, include_index=False)

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

def save_config_param(key, value):
    st.session_state.config[key] = value
    try: save_sheet("Config", pd.DataFrame(list(st.session_state.config.items()), columns=["Clé", "Valeur"]))
    except: pass

def afficher_montant_double(label, montant_usd, delta_str="", couleur_valeur=None, taille="large"):
    montant_eur = montant_usd / TAUX_EUR_USD
    str_usd, str_eur = f"{montant_usd:,.2f}".replace(',', ' '), f"{montant_eur:,.2f}".replace(',', ' ')
    delta_html = f"<div style='font-size: 0.9rem; font-weight: 600; color: {'#2ecc71' if '+' in delta_str else ('#e74c3c' if '-' in delta_str else 'inherit')}; padding-top: 0.2rem;'>{delta_str}</div>" if delta_str else ""
    t_val, t_lbl = ("1.8rem", "0.9rem") if taille == "large" else ("1.4rem", "0.85rem") if taille == "medium" else ("1.2rem", "0.85rem")
    c_val = f"color: {couleur_valeur};" if couleur_valeur else ""
    st.markdown(f"""<div style="margin-bottom: 0.8rem;"><div style="font-size: {t_lbl}; opacity: 0.8; margin-bottom: 0.2rem;">{label}</div><div style="font-size: {t_val}; font-weight: 600; line-height: 1.2; {c_val}">{str_usd} $ <span style="font-size: 0.65em; opacity: 0.7; font-weight: 400;">/ {str_eur} €</span></div>{delta_html}</div>""", unsafe_allow_html=True)

def est_devise_liquide(ticker):
    t = str(ticker).upper().strip()
    return t.endswith("=X") or (any(m in t for m in ["USD", "EUR", "CHF", "JPY", "CNY", "GBP"]) and not any(c in t for c in ["BTC", "ETH"]))

def nettoyer_dataframe(df):
    cols_finales = ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]
    for col in df.columns:
        if "quantit" in str(col).lower() or "qte" in str(col).lower(): df.rename(columns={col: "Quantité"}, inplace=True)
    if "Type" not in df.columns:
        df["Type"] = ""
        for idx, row in df.iterrows():
            tick = str(row.get("Ticker", "")).upper()
            df.at[idx, "Type"] = "💵 Cash" if est_devise_liquide(tick) else "₿ Crypto" if any(c in tick for c in ["BTC", "ETH", "USDT"]) else "🛢️ Action"
    for col in cols_finales:
        if col not in df.columns: df[col] = 0.0 if col == "Pourcentage (%)" else ("$ 0.00" if col in ["Court", "Valeur totale"] else "")
    return df[cols_finales].reset_index(drop=True)

def recalculer_toute_la_base_projections(df):
    if df is None or df.empty: return df
    df_t = df.copy()
    c_base = ["Date", "Capital investi", "Actifs Stratégiques", "Total Global"]
    for i, nom in enumerate(c_base):
        if i < len(df_t.columns): df_t.rename(columns={df_t.columns[i]: nom}, inplace=True)
    for col in ["Capital investi", "Actifs Stratégiques", "Total Global"]: df_t[col] = df_t[col].apply(extraire_nombre)
    df_t['DT_TRI'] = pd.to_datetime(df_t['Date'], dayfirst=True, errors='coerce')
    df_t = df_t.sort_values('DT_TRI').reset_index(drop=True)
    res, c_twr, tg_twr = [], 1.0, 1.0
    for i in range(len(df_t)):
        r = df_t.iloc[i].to_dict()
        cap, act, tg = r["Capital investi"], r["Actifs Stratégiques"], r["Total Global"]
        if i == 0:
            r["Evolution actifs $"] = r["Evolution actifs %"] = 0.0
            r["Evolution cumulée $"], r["Evolution cumulée %"] = act - cap, ((act - cap) / cap * 100) if cap != 0 else 0.0
            r["TG_Evolution cumulée $"], r["TG_Evolution cumulée %"] = tg - cap, ((tg - cap) / cap * 100) if cap != 0 else 0.0
            c_twr *= (1 + ((act - cap) / cap if cap != 0 else 0.0))
            tg_twr *= (1 + ((tg - cap) / cap if cap != 0 else 0.0))
        else:
            prev = df_t.iloc[i-1]
            d_cap = cap - prev["Capital investi"]
            evo_usd = (act - prev["Actifs Stratégiques"]) - d_cap
            r["Evolution actifs $"], r["Evolution actifs %"] = evo_usd, (evo_usd / prev["Actifs Stratégiques"] * 100) if prev["Actifs Stratégiques"] != 0 else 0.0
            r["Evolution cumulée $"], r["Evolution cumulée %"] = act - cap, ((act - cap) / cap * 100) if cap != 0 else 0.0
            c_twr *= (1 + (evo_usd / (prev["Actifs Stratégiques"] + d_cap) if (prev["Actifs Stratégiques"] + d_cap) != 0 else 0.0))
            evo_tg = (tg - prev["Total Global"]) - d_cap
            r["TG_Evolution cumulée $"], r["TG_Evolution cumulée %"] = tg - cap, ((tg - cap) / cap * 100) if cap != 0 else 0.0
            tg_twr *= (1 + (evo_tg / (prev["Total Global"] + d_cap) if (prev["Total Global"] + d_cap) != 0 else 0.0))
        r["Score TWR %"], r["TG_Score TWR %"] = (c_twr - 1) * 100, (tg_twr - 1) * 100
        res.append(r)
    df_f = pd.DataFrame(res)
    if 'DT_TRI' in df_f.columns: df_f.drop(columns=['DT_TRI'], inplace=True)
    return df_f[["Date", "Capital investi", "Actifs Stratégiques", "Total Global", "Evolution actifs $", "Evolution actifs %", "Evolution cumulée $", "Evolution cumulée %", "Score TWR %", "TG_Evolution cumulée $", "TG_Evolution cumulée %", "TG_Score TWR %"]]

def recalculer_totaux_locaux():
    if "donnees" in st.session_state:
        df = st.session_state.donnees.copy()
        for idx, row in df.iterrows():
            c, q = extraire_nombre(row.get("Court", 0)), extraire_nombre(row.get("Quantité", 0))
            df.at[idx, "Valeur totale"] = f"$ {round(c * q, 2):,.2f}"
            df.at[idx, "Court"] = f"$ {c:.2f}"
        st.session_state.donnees = df

def calculer_metriques_jour(df_actuel, variations):
    val_invest = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    val_total = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows())
    somme_p = sum(extraire_nombre(r["Pourcentage (%)"]) for _, r in df_actuel.iterrows())
    
    v_jour_tg_usd = val_tot_veille = v_jour_strat_usd = val_inv_veille = 0.0
    for _, r in df_actuel.iterrows():
        tick = str(r.get("Ticker", "")).strip().upper()
        v_act = extraire_nombre(r["Valeur totale"])
        match = re.search(r'([+-]?\d+\.?\d*)', variations.get(tick, "0"))
        v_pct = float(match.group(1)) if match else 0.0
        v_veil = v_act / (1 + v_pct / 100) if (1 + v_pct / 100) != 0 else v_act
        
        v_jour_tg_usd += (v_act - v_veil)
        val_tot_veille += v_veil
        if extraire_nombre(r["Pourcentage (%)"]) > 0:
            v_jour_strat_usd += (v_act - v_veil)
            val_inv_veille += v_veil
            
    pct_jour_tg = (v_jour_tg_usd / val_tot_veille * 100) if val_tot_veille > 0 else 0.0
    pct_jour_strat = (v_jour_strat_usd / val_inv_veille * 100) if val_inv_veille > 0 else 0.0
    return val_invest, val_total, somme_p, v_jour_tg_usd, pct_jour_tg, v_jour_strat_usd, pct_jour_strat

def actualiser_cours_internet(silencieux=False):
    if "donnees" in st.session_state:
        if not silencieux: st.toast("🔄 Actualisation des cours boursiers en cours...")
        df_tmp = st.session_state.donnees.copy()
        changement, taux_cache = False, {} 
        if "variations" not in st.session_state: st.session_state.variations = {}
        for idx, row in df_tmp.iterrows():
            tick = str(row.get("Ticker", "")).strip().upper()
            if tick and tick != "NAN":
                succ_bin = False
                if tick.endswith("USDT"):
                    for base in ["https://api.binance.com", "https://api.binance.us"]:
                        try:
                            req = urllib.request.Request(f"{base}/api/v3/klines?symbol={tick}&interval=1d&limit=2", headers={'User-Agent': 'Mozilla/5.0'})
                            with urllib.request.urlopen(req, timeout=3) as resp:
                                data = json.loads(resp.read().decode())
                                p_usd = float(data[1][4]) if len(data) >= 2 else float(data[0][4])
                                p_prev = float(data[0][4])
                                var = ((p_usd - p_prev) / p_prev) * 100 if p_prev > 0 else 0.0
                                st.session_state.variations[tick] = f"{'↗' if var > 0 else '↘' if var < 0 else '→'} {var:+.2f} %"
                                df_tmp.at[idx, "Court"] = f"$ {p_usd:.2f}"
                                changement = succ_bin = True
                                break 
                        except: continue 
                    if succ_bin: continue 

                tick_yf = tick.replace("USDT", "-USD") if (tick.endswith("USDT") and not succ_bin) else tick
                try:
                    asset = yf.Ticker(tick_yf)
                    try: p_loc = float(asset.fast_info.get('lastPrice', 0.0))
                    except: p_loc = float(asset.history(period="1d")['Close'].iloc[-1]) if not asset.history(period="1d").empty else 0.0
                    try:
                        p_prev = float(asset.fast_info.get('previous_close', 0.0))
                        if p_prev <= 0.0:
                            h = asset.history(period="5d")
                            if len(h) >= 2: p_prev = float(h['Close'].iloc[-2])
                        if p_prev > 0.0 and p_loc > 0.0:
                            var = ((p_loc - p_prev) / p_prev) * 100
                            st.session_state.variations[tick] = f"{'↗' if var > 0 else '↘' if var < 0 else '→'} {var:+.2f} %"
                        elif tick not in st.session_state.variations: st.session_state.variations[tick] = "→ 0.00 %"
                    except:
                        if tick not in st.session_state.variations: st.session_state.variations[tick] = "→ 0.00 %"
                        
                    if p_loc > 0:
                        dev = str(asset.fast_info.get('currency', 'USD')).strip().upper()
                        f_dev = 0.01 if dev == "GBP" else 1.0
                        if dev in ["", "NONE"]: dev = "USD"
                        p_usd = p_loc * f_dev
                        if dev != "USD":
                            if dev not in taux_cache:
                                try: tx = float(yf.Ticker(f"{dev}USD=X").fast_info.get('lastPrice', 0.0))
                                except: tx = 0.0
                                if tx <= 0.0:
                                    try: tx = 1.0 / float(yf.Ticker(f"{dev}=X").fast_info.get('lastPrice', 0.0))
                                    except: pass
                                taux_cache[dev] = tx if tx > 0 else 1.0
                            p_usd *= taux_cache[dev]
                        df_tmp.at[idx, "Court"] = f"$ {p_usd:.2f}"
                        changement = True
                except: pass
        if changement:
            st.session_state.donnees = df_tmp
            recalculer_totaux_locaux()
            save_sheet("Donnees", st.session_state.donnees)

@st.cache_data(ttl=86400) 
def recuperer_inflation_france():
    try:
        req = urllib.request.Request("https://api.worldbank.org/v2/country/FRA/indicator/FP.CPI.TOTL.ZG?format=json&per_page=20", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if len(data) == 2 and isinstance(data[1], list): return {int(i['date']): round(float(i['value']), 2) for i in data[1] if i['value'] is not None}
    except: pass
    return None

def get_historical_fx(devise, date_val):
    d_clean = str(devise).upper().strip()
    if d_clean in ["EUR", ""]: return 1.0
    t = f"{d_clean}EUR=X"
    try:
        d = pd.to_datetime(date_val, dayfirst=True, errors='coerce')
        if pd.isna(d): return 1.0
        if d >= pd.Timestamp.now() - pd.Timedelta(days=1):
            h = yf.Ticker(t).history(period="1d")
            return float(h['Close'].iloc[-1]) if not h.empty else 1.0
        h = yf.Ticker(t).history(start=(d - pd.Timedelta(days=5)).strftime('%Y-%m-%d'), end=(d + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        if not h.empty: return float(h['Close'].iloc[-1])
        h_fb = yf.Ticker(t).history(period="1d")
        if not h_fb.empty: return float(h_fb['Close'].iloc[-1])
    except: pass
    return 1.0

def get_pru_and_qty(ticker, df_t):
    df_k = df_t[df_t['Ticker'] == ticker].copy()
    if df_k.empty: return 0.0, 0.0
    if 'Date_DT' not in df_k.columns: df_k['Date_DT'] = pd.to_datetime(df_k['Date'], dayfirst=True, errors='coerce')
    df_k = df_k.dropna(subset=['Date_DT']).sort_values('Date_DT')
    t_c = t_q = 0.0
    for _, r in df_k.iterrows():
        typ, q, n = str(r['Type']).lower(), float(r['Quantité']), float(r['Montant Net'])
        if "achat" in typ:
            t_c += n
            t_q += q
        elif "vente" in typ:
            pru = t_c / t_q if t_q > 0 else 0.0
            t_c -= pru * q
            t_q -= q
            if t_q <= 0.000001: t_c = t_q = 0.0
    return (t_c / t_q if t_q > 0 else 0.0), t_q

def calcul_frais_km(km, cv):
    coefs = {3:(0.529, 0.316, 1065, 0.370), 4:(0.606, 0.340, 1330, 0.407), 5:(0.636, 0.357, 1395, 0.427), 6:(0.665, 0.374, 1457, 0.447), 7:(0.697, 0.394, 1515, 0.470)}
    c = coefs.get(cv, coefs[7])
    return km * c[0] if km <= 5000 else (km * c[1] + c[2] if km <= 20000 else km * c[3])

def calcul_impot_ir(rev, parts, stat, apply_decote=True):
    qf, imp = rev / parts, 0
    tr = [(11294,0), (28797,0.11), (82341,0.30), (177106,0.41), (9999999,0.45)]
    for i in range(1, len(tr)):
        if qf > tr[i-1][0]: imp += (min(qf, tr[i][0]) - tr[i-1][0]) * tr[i][1]
    imp *= parts
    if apply_decote:
        lim, base = (2002, 906) if "Cél" in stat else (3300, 1493)
        if imp <= lim: imp = max(0, imp - (base - (imp * 0.4525)))
    return 0.0 if imp < 61 else imp

# --- 5. INITIALISATION ---
if "variations" not in st.session_state: st.session_state.variations = {}

if "config" not in st.session_state:
    df_c = load_sheet("Config", ["Clé", "Valeur"])
    st.session_state.config = {str(r["Clé"]): str(r["Valeur"]) if str(r["Clé"])=="f_statut" else extraire_nombre(r["Valeur"]) for _, r in df_c.iterrows() if pd.notna(r["Clé"])}

d_conf = {"retraite_apport_mensuel": 250.0, "retraite_taxe": 30.0, "f_statut": "Marié(e) / Pacsé(e)", "f_enf": 0.0, "f_s1": 30000.0, "f_s2": 0.0, "f_u1": 0.0, "f_k1": 0.0, "f_cv1": 5.0, "f_r1": 0.0, "f_u2": 0.0, "f_k2": 0.0, "f_cv2": 5.0, "f_r2": 0.0}
for k, v in d_conf.items():
    if k not in st.session_state.config: st.session_state.config[k] = v

if "donnees" not in st.session_state: st.session_state.donnees = nettoyer_dataframe(load_sheet("Donnees", ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]))

if "historique" not in st.session_state:
    df_h = load_sheet("Historique", ["Date", "Type", "Montant $", "Montant €", "Montant Or"])
    for c in ["Montant $", "Montant €", "Montant Or"]:
        if c in df_h.columns: df_h[c] = df_h[c].apply(extraire_nombre)
    st.session_state.historique = df_h

if "projections" not in st.session_state: st.session_state.projections = recalculer_toute_la_base_projections(load_sheet("Projections", []))
elif "TG_Evolution cumulée $" not in st.session_state.projections.columns: st.session_state.projections = recalculer_toute_la_base_projections(st.session_state.projections)

if "inflation" not in st.session_state:
    df_i = load_sheet("Inflation", ["Année", "Inflation (%)"])
    if not df_i.empty and 'Année' in df_i.columns: 
        df_i['Année'], df_i['Inflation (%)'] = pd.to_numeric(df_i['Année'], errors='coerce').fillna(0).astype(int), pd.to_numeric(df_i['Inflation (%)'], errors='coerce').fillna(0.0)
        df_i.drop_duplicates(subset=['Année'], keep='last', inplace=True)
    st.session_state.inflation = df_i

if "transactions" not in st.session_state:
    df_t = load_sheet("Transaction", ["Ticker", "Type", "Date", "Quantité", "Cours", "Frais", "Montant Net", "Devise", "PRU (Devise)", "Taux change (EUR)"])
    for c in ["Quantité", "Cours", "Frais", "Montant Net", "PRU (Devise)", "Taux change (EUR)"]:
        if c in df_t.columns: df_t[c] = df_t[c].apply(extraire_nombre)
    st.session_state.transactions = df_t

if "inflation_check_done" not in st.session_state:
    st.session_state.inflation_check_done = True
    d_inf = recuperer_inflation_france()
    if d_inf and not st.session_state.projections.empty:
        df_p_tmp = st.session_state.projections.copy()
        df_p_tmp['Date_DT'] = pd.to_datetime(df_p_tmp['Date'], dayfirst=True, errors='coerce')
        ans = df_p_tmp.dropna(subset=['Date_DT'])['Date_DT'].dt.year.unique()
        n_inf, chg = [], False
        for a in ans:
            v_off = d_inf.get(a, 0.0)
            v_act = st.session_state.inflation[st.session_state.inflation['Année'] == a].iloc[0]['Inflation (%)'] if not st.session_state.inflation[st.session_state.inflation['Année'] == a].empty else 0.0
            if v_off != v_act: chg = True
            n_inf.append({'Année': a, 'Inflation (%)': v_off})
        if chg:
            st.session_state.inflation = pd.DataFrame(n_inf)
            save_sheet("Inflation", st.session_state.inflation)

if "dernier_refresh_cours" not in st.session_state: st.session_state.dernier_refresh_cours = 0
n_t = time.time()
if n_t - st.session_state.dernier_refresh_cours >= 900:
    actualiser_cours_internet(silencieux=(st.session_state.dernier_refresh_cours == 0))
    st.session_state.dernier_refresh_cours = n_t

# --- 6. NAVIGATION ---
st.sidebar.title("Menu")
page_choisie = st.sidebar.radio("Aller vers :", ["📊 Tableau de bord", "📋 Liste des actifs", "⚖️ Rééquilibrage", "💰 Fonds", "🏖️ Suivi", "📈 Performance", "🌴 Retraite", "🏛️ Fiscalité"])
st.sidebar.divider()
if st.sidebar.button("🔄 Recharger l'application", use_container_width=True):
    st.session_state.clear()
    st.rerun()

# --- 7. PAGES ---
if page_choisie == "📊 Tableau de bord":
    st.title("📊 Vue d'ensemble de mon Patrimoine")
    df_a, df_p = st.session_state.donnees, st.session_state.projections
    
    val_inv, val_tot, somme_p, v_jour_tg_usd, p_jour_tg, v_jour_strat_usd, p_jour_strat = calculer_metriques_jour(df_a, st.session_state.variations)
    cap_actuel = sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _, r in st.session_state.historique.iterrows())
    
    df_p_live = pd.concat([df_p, pd.DataFrame([{"Date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "Capital investi": cap_actuel, "Actifs Stratégiques": val_inv, "Total Global": val_tot}])], ignore_index=True)
    df_p_live = recalculer_toute_la_base_projections(df_p_live)
    
    delta = p_delta = delta_tg = p_delta_tg = 0.0
    if not df_p.empty:
        df_d = df_p.copy()
        df_d['Date_DT'] = pd.to_datetime(df_d['Date'], dayfirst=True, errors='coerce')
        df_d = df_d.dropna(subset=['Date_DT']).sort_values('Date_DT')
        if not df_d.empty:
            df_past = df_d[df_d['Date_DT'] <= pd.Timestamp.now() - pd.DateOffset(years=1)]
            row_ref = df_past.iloc[-1] if not df_past.empty else df_d.iloc[0] 
            v_ref_strat, v_ref_tg = extraire_nombre(row_ref["Actifs Stratégiques"]), extraire_nombre(row_ref["Total Global"])
            delta, delta_tg = val_inv - v_ref_strat, val_tot - v_ref_tg
            if v_ref_strat > 0: p_delta = (delta / v_ref_strat) * 100
            if v_ref_tg > 0: p_delta_tg = (delta_tg / v_ref_tg) * 100

    besoin_req = val_inv > 0 and any(abs((val_inv * (extraire_nombre(r["Pourcentage (%)"])/100)) - extraire_nombre(r["Valeur totale"])) >= 1000 and abs((extraire_nombre(r["Valeur totale"])/val_inv*100) - extraire_nombre(r["Pourcentage (%)"])) >= 2.0 for _, r in df_a.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)

    st.subheader("⚙️ 1. Pilotage & Statut")
    c_btn, c_stat = st.columns([1, 2])
    with c_btn:
        if st.button("🔄 Actualiser les cours", use_container_width=True):
            actualiser_cours_internet(False)
            st.rerun()
    with c_stat:
        if besoin_req: st.warning("⚠️ **Rééquilibrage nécessaire**")
        else: st.success("✅ **Équilibré**")
    st.divider()

    st.subheader("🌍 2. Total Global (Toutes liquidités incluses)")
    c_tg, _ = st.columns(2)
    with c_tg:
        afficher_montant_double("Total Global", val_tot, f"{delta_tg:+,.2f} $ ({p_delta_tg:+.2f} % sur 1 an glissant)")
        st.markdown(f"<div style='margin-top:-0.5rem; margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if v_jour_tg_usd>=0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if v_jour_tg_usd>=0 else '#e74c3c'}'>{v_jour_tg_usd:+,.2f} $ ({p_jour_tg:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    st.write("")

    if not df_p.empty:
        df_v_tg = df_p_live.copy()
        df_v_tg['Date_DT'] = pd.to_datetime(df_v_tg['Date'], dayfirst=True, errors='coerce')
        df_v_tg = df_v_tg.dropna(subset=['Date_DT']).sort_values('Date_DT').reset_index(drop=True)
        st.markdown("**📈 Évolution & Performance globale**")
        cf1, cf2 = st.columns(2)
        f_tg = cf1.radio("Période globale :", ["Depuis le début", "Depuis 1 an", "Depuis le début de l'année"], horizontal=True, key="f_tg")
        m_tg = cf2.radio("Affichage :", ["Rendement Absolu (ROI)", "Score TWR (Talent)"], horizontal=True, key="m_tg")
        n = pd.Timestamp.now()
        if f_tg == "Depuis 1 an": df_v_tg = df_v_tg[df_v_tg['Date_DT'] >= (n - pd.DateOffset(years=1))]
        elif f_tg == "Depuis le début de l'année": df_v_tg = df_v_tg[df_v_tg['Date_DT'] >= pd.Timestamp(year=n.year - 1, month=12, day=31)]
        if not df_v_tg.empty:
            df_v_tg.set_index('Date_DT', inplace=True)
            d_usd = df_v_tg['TG_Evolution cumulée $'].iloc[-1] - df_v_tg['TG_Evolution cumulée $'].iloc[0]
            pct = (d_usd / df_v_tg['Total Global'].iloc[0] * 100) if df_v_tg['Total Global'].iloc[0] > 0 else 0.0
            md, mf = 1+df_v_tg['TG_Score TWR %'].iloc[0]/100, 1+df_v_tg['TG_Score TWR %'].iloc[-1]/100
            twr_p = ((mf/md)-1)*100 if md!=0 else 0.0
            cg1, cg2 = st.columns([1, 3])
            with cg1:
                if "ROI" in m_tg:
                    afficher_montant_double("Gains nets globaux", df_v_tg['TG_Evolution cumulée $'].iloc[-1], f"{d_usd:+,.2f} $ ({pct:+.2f} % sur la période)", taille="medium")
                    p_gl = df_v_tg['TG_Evolution cumulée %'].iloc[-1]
                    st.markdown(f"📊 Rentabilité Globale : <strong style='color:{'green' if p_gl>0 else 'red' if p_gl<0 else 'gray'}'>{p_gl:+.2f} %</strong>", unsafe_allow_html=True)
                else:
                    st.metric("Score TWR Global (%)", f"{df_v_tg['TG_Score TWR %'].iloc[-1]:+.2f} %", f"{twr_p:+.2f} % (sur la période)")
                    afficher_montant_double("Gains nets actuels", df_v_tg['TG_Evolution cumulée $'].iloc[-1], taille="medium")
            with cg2:
                fig_lt = px.line(df_v_tg.reset_index(), x='Date_DT', y='TG_Evolution cumulée $' if "ROI" in m_tg else 'TG_Score TWR %')
                fig_lt.update_traces(line_shape='spline')
                fig_lt.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
                fig_lt.update_yaxes(zeroline=False, rangemode="normal")
                fig_lt.update_xaxes(tickformat="%d/%m/%Y")
                st.plotly_chart(fig_lt, use_container_width=True)

        st.write("")
        st.markdown("**🌍 Répartition du Patrimoine (Total Global)**")
        cp1, _ = st.columns(2)
        with cp1:
            st.markdown("*Toutes classes d'actifs confondues*")
            df_p_tg = df_a.copy()
            df_p_tg['Val'] = df_p_tg['Valeur totale'].apply(extraire_nombre)
            df_pie_tg = df_p_tg[df_p_tg['Val']>0].groupby('Type')['Val'].sum().reset_index()
            if not df_pie_tg.empty:
                fig_tg = px.pie(df_pie_tg, values='Val', names='Type', color='Type', color_discrete_map={"🛢️ Action": "#e74c3c", "📜 Obligation": "#3498db", "💰 Or": "#f1c40f", "₿ Crypto": "#9b59b6", "💵 Cash": "#2ecc71"}, hole=0.4)
                fig_tg.update_traces(textposition='inside', textinfo='percent+label')
                fig_tg.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
                st.plotly_chart(fig_tg, use_container_width=True)
    else: st.info("Aucune donnée globale.")
    st.divider()

    st.subheader("🎯 3. Actifs Stratégiques (Investissements cibles)")
    c_st, _ = st.columns(2)
    with c_st:
        afficher_montant_double("Actifs Stratégiques", val_inv, f"{delta:+,.2f} $ ({p_delta:+.2f} % sur 1 an glissant)")
        st.markdown(f"<div style='margin-top:-0.5rem; margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if v_jour_strat_usd>=0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if v_jour_strat_usd>=0 else '#e74c3c'}'>{v_jour_strat_usd:+,.2f} $ ({p_jour_strat:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    st.write("") 
    
    if df_p.empty: st.info("Aucune donnée.")
    else:
        df_v_s = df_p_live.copy()
        df_v_s['Date_DT'] = pd.to_datetime(df_v_s['Date'], dayfirst=True, errors='coerce')
        df_v_s = df_v_s.dropna(subset=['Date_DT']).sort_values('Date_DT').reset_index(drop=True)
        st.markdown("**📈 Évolution & Performance de la stratégie**")
        cf1, cf2 = st.columns(2)
        f_s = cf1.radio("Sélectionnez la période :", ["Depuis le début", "Depuis 1 an", "Depuis le début de l'année"], horizontal=True, key="f_s")
        m_s = cf2.radio("Affichage :", ["Rendement Absolu (ROI)", "Score TWR (Talent)"], horizontal=True, key="m_s")
        n = pd.Timestamp.now()
        if f_s == "Depuis 1 an": df_v_s = df_v_s[df_v_s['Date_DT'] >= (n - pd.DateOffset(years=1))]
        elif f_s == "Depuis le début de l'année": df_v_s = df_v_s[df_v_s['Date_DT'] >= pd.Timestamp(year=n.year - 1, month=12, day=31)]
        if not df_v_s.empty:
            df_v_s.set_index('Date_DT', inplace=True)
            d_usd = df_v_s['Evolution cumulée $'].iloc[-1] - df_v_s['Evolution cumulée $'].iloc[0]
            pct = (d_usd / df_v_s['Actifs Stratégiques'].iloc[0] * 100) if df_v_s['Actifs Stratégiques'].iloc[0] > 0 else 0.0
            md, mf = 1+df_v_s['Score TWR %'].iloc[0]/100, 1+df_v_s['Score TWR %'].iloc[-1]/100
            twr_p = ((mf/md)-1)*100 if md!=0 else 0.0
            cg1, cg2 = st.columns([1, 3])
            with cg1:
                if "ROI" in m_s:
                    afficher_montant_double("Gains nets de la stratégie", df_v_s['Evolution cumulée $'].iloc[-1], f"{d_usd:+,.2f} $ ({pct:+.2f} % sur la période)", taille="medium")
                    p_gl = df_v_s['Evolution cumulée %'].iloc[-1]
                    st.markdown(f"📊 Rentabilité Stratégique : <strong style='color:{'green' if p_gl>0 else 'red' if p_gl<0 else 'gray'}'>{p_gl:+.2f} %</strong>", unsafe_allow_html=True)
                else:
                    st.metric("Score TWR Stratégique (%)", f"{df_v_s['Score TWR %'].iloc[-1]:+.2f} %", f"{twr_p:+.2f} % (sur la période)")
                    afficher_montant_double("Gains nets actuels", df_v_s['Evolution cumulée $'].iloc[-1], taille="medium")
            with cg2:
                fig_ls = px.line(df_v_s.reset_index(), x='Date_DT', y='Evolution cumulée $' if "ROI" in m_s else 'Score TWR %')
                fig_ls.update_traces(line_shape='spline')
                fig_ls.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
                fig_ls.update_yaxes(zeroline=False, rangemode="normal")
                fig_ls.update_xaxes(tickformat="%d/%m/%Y")
                st.plotly_chart(fig_ls, use_container_width=True)

    st.write("")
    st.markdown("**🎯 Répartition détaillée de la stratégie**")
    df_st = df_a[df_a['Pourcentage (%)'].apply(extraire_nombre)>0].copy()
    df_st['Val'] = df_st['Valeur totale'].apply(extraire_nombre)
    cp1, cp2 = st.columns(2)
    with cp1:
        st.markdown("*Classes d'actifs ciblées*")
        d_p1 = df_st[df_st['Val']>0].groupby('Type')['Val'].sum().reset_index()
        if not d_p1.empty:
            f1 = px.pie(d_p1, values='Val', names='Type', color='Type', color_discrete_map={"🛢️ Action": "#e74c3c", "📜 Obligation": "#3498db", "💰 Or": "#f1c40f", "₿ Crypto": "#9b59b6", "💵 Cash": "#2ecc71"}, hole=0.4)
            f1.update_traces(textposition='inside', textinfo='percent+label')
            f1.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
            st.plotly_chart(f1, use_container_width=True)
    with cp2:
        st.markdown("*Détail des lignes stratégiques*")
        if not df_st[df_st['Val']>0].empty:
            f2 = px.pie(df_st[df_st['Val']>0], values='Val', names='Ticker', hole=0.4)
            f2.update_traces(textposition='inside', textinfo='percent+label')
            f2.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
            st.plotly_chart(f2, use_container_width=True)

    st.divider()
    st.subheader("🏖️ 4. Liberté Financière (Rente Mensuelle actuelle)")
    cr1, cr2 = st.columns(2)
    with cr1:
        st.write("") 
        inf = st.slider("Inflation cible à déduire (%) ✍️", 0.0, 15.0, 2.0, 0.1, key="dash_infl")
    with cr2:
        tx_r = ((1 + 0.08) / (1 + (inf / 100.0))) - 1
        afficher_montant_double("Rente Mensuelle Nette (Base 8% par an)", (val_inv * max(0.0, tx_r)) / 12.0, couleur_valeur="#3498db")

elif page_choisie == "📋 Liste des actifs":
    st.title("📋 Liste de mes actifs")
    st.write("Modifiez l'allocation cible de vos actifs ici.")
    
    df_a = st.session_state.donnees.copy()
    val_inv, val_tot, somme_p, v_jour_tg_usd, p_jour_tg, v_jour_strat_usd, p_jour_strat = calculer_metriques_jour(df_a, st.session_state.variations)

    c1, c2, c3 = st.columns(3)
    with c1:
        afficher_montant_double("Actifs Stratégiques", val_inv)
        st.markdown(f"<div style='margin-top:-0.5rem; margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if v_jour_strat_usd>=0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if v_jour_strat_usd>=0 else '#e74c3c'}'>{v_jour_strat_usd:+,.2f} $ ({p_jour_strat:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    with c2:
        afficher_montant_double("Total Global", val_tot)
        st.markdown(f"<div style='margin-top:-0.5rem; margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if v_jour_tg_usd>=0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if v_jour_tg_usd>=0 else '#e74c3c'}'>{v_jour_tg_usd:+,.2f} $ ({p_jour_tg:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    with c3:
        ec = round(100 - somme_p, 2)
        st.markdown(f"<div style='margin-bottom:0.8rem;'><div style='font-size:0.9rem; opacity:0.8; margin-bottom:0.2rem;'>Répartition Cible</div><div style='font-size:1.8rem; font-weight:600; line-height:1.2;'>{somme_p:.2f} %</div><div style='font-size:0.9rem; font-weight:600; color:{'#2ecc71' if ec==0 else '#e74c3c'}; padding-top:0.2rem;'>{'✅ Cible atteinte' if ec==0 else f'⚠️ {abs(ec):.2f} % manquant/en trop'}</div></div>", unsafe_allow_html=True)
    st.divider()

    if st.button("🔄 Actualiser les cours", use_container_width=True):
        actualiser_cours_internet(False)
        st.rerun()

    df_a['Var. Jour 🔒'] = df_a['Ticker'].apply(lambda x: st.session_state.variations.get(str(x).upper(), "→ 0.00 %"))

    c_act_locked = {
        "Ticker": st.column_config.TextColumn("Ticker ✍️"),
        "Type": st.column_config.SelectboxColumn("Type ✍️", options=["🛢️ Action", "📜 Obligation", "💰 Or", "₿ Crypto", "💵 Cash"]),
        "Court": st.column_config.TextColumn("Court 🔒", disabled=True),
        "Quantité": st.column_config.NumberColumn("Quantité 🔒", disabled=True),
        "Valeur totale": st.column_config.TextColumn("Valeur totale 🔒", disabled=True),
        "Pourcentage (%)": st.column_config.NumberColumn("Pourcentage (%) ✍️", format="%.2f%%"),
        "Var. Jour 🔒": st.column_config.TextColumn("Var. Jour 🔒", disabled=True)
    }
    
    c_act_unlocked = c_act_locked.copy()
    c_act_unlocked["Quantité"] = st.column_config.NumberColumn("Quantité ✍️", disabled=False)
    
    def c_var(v): return 'color:#2ecc71' if "↗" in str(v) or "+" in str(v) else ('color:#e74c3c' if "↘" in str(v) or "-" in str(v) else 'color:#95a5a6')
    d_c = ["Ticker", "Type", "Court", "Quantité", "Valeur totale", "Pourcentage (%)", "Var. Jour 🔒"]
    
    m_dev = df_a.apply(lambda r: est_devise_liquide(r.get("Ticker", "")), axis=1)
    
    st.markdown("### 📈 Actifs d'Investissement")
    st.caption("La colonne Quantité est verrouillée : elle se met à jour automatiquement via vos transactions.")
    r_i = st.data_editor(df_a[~m_dev][d_c].style.map(c_var, subset=["Var. Jour 🔒"]), key="ei", column_config=c_act_locked, use_container_width=True, hide_index=True, num_rows="dynamic")
    
    st.markdown("### 💵 Liquidités (Devises)")
    st.caption("Vous pouvez forcer ou ajuster manuellement la quantité de vos liquidités ici.")
    r_d = st.data_editor(df_a[m_dev][d_c].style.map(c_var, subset=["Var. Jour 🔒"]), key="ed", column_config=c_act_unlocked, use_container_width=True, hide_index=True, num_rows="dynamic")

    n_df = pd.concat([r_i, r_d], ignore_index=True)
    cols = ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]
    if not n_df[cols].equals(st.session_state.donnees[cols]):
        st.session_state.donnees = n_df[cols]
        recalculer_totaux_locaux()
        save_sheet("Donnees", st.session_state.donnees)
        st.rerun()

elif page_choisie == "⚖️ Rééquilibrage":
    st.title("⚖️ Rééquilibrage & Transactions")
    
    with st.expander("➕ Enregistrer une transaction (Achat/Vente)"):
        with st.form("new_trans"):
            c1, c2, c3 = st.columns(3)
            t_d = c1.date_input("Date")
            lt = sorted(st.session_state.donnees['Ticker'].dropna().unique().tolist())
            lt.insert(0, "➕ Nouvel actif...")
            t_sel = c2.selectbox("Actif (Ticker)", lt)
            t_t = c2.text_input("Saisissez le Ticker du nouvel actif") if t_sel == "➕ Nouvel actif..." else t_sel
            t_ty = c3.selectbox("Type", ["Achat", "Vente"])
            c4, c5, c6 = st.columns(3)
            t_q = c4.number_input("Quantité", min_value=0.0, format="%.6f")
            t_c = c5.number_input("Cours de l'actif", min_value=0.0, format="%.4f")
            t_f = c6.number_input("Frais de transaction", min_value=0.0, format="%.2f")
            t_dev = st.selectbox("Devise", ["USD", "EUR", "CHF", "JPY", "GBP"])
            
            if st.form_submit_button("🔨 Valider la transaction"):
                if t_t.strip() == "": st.error("❌ Le Ticker ne peut pas être vide.")
                elif t_q <= 0: st.error("❌ La quantité doit être strictement supérieure à 0.")
                elif t_c <= 0: st.error("❌ Le cours doit être strictement supérieur à 0.")
                else:
                    t_cl = t_t.upper().strip()
                    m_n = (t_q * t_c) + t_f if t_ty == "Achat" else (t_q * t_c) - t_f
                    fx = get_historical_fx(t_dev, t_d.strftime("%Y-%m-%d"))
                    c_pru, c_qty = get_pru_and_qty(t_cl, st.session_state.transactions)
                    
                    r_pru = ((c_pru * c_qty) + m_n) / (c_qty + t_q) if t_ty == "Achat" and (c_qty + t_q) > 0 else c_pru
                    
                    nr = {"Ticker": t_cl, "Type": t_ty, "Date": t_d.strftime("%d/%m/%Y"), "Quantité": t_q, "Cours": t_c, "Frais": t_f, "Montant Net": m_n, "Devise": t_dev, "PRU (Devise)": r_pru, "Taux change (EUR)": fx}
                    st.session_state.transactions = pd.concat([st.session_state.transactions, pd.DataFrame([nr])], ignore_index=True)
                    save_sheet("Transaction", st.session_state.transactions[[c for c in st.session_state.transactions.columns if c != 'Date_DT']])
                    
                    df_d = st.session_state.donnees.copy()
                    i_a = df_d.index[df_d['Ticker'] == t_cl].tolist()
                    if not i_a:
                        df_d = pd.concat([df_d, pd.DataFrame([{"Ticker": t_cl, "Type": "₿ Crypto" if any(c in t_cl for c in ["BTC", "ETH", "USDT"]) else "🛢️ Action", "Quantité": 0.0, "Court": "$ 0.00", "Valeur totale": "$ 0.00", "Pourcentage (%)": 0.0}])], ignore_index=True)
                        i_a = [len(df_d) - 1]
                    
                    idx = i_a[0]
                    nq = max(0.0, extraire_nombre(df_d.at[idx, "Quantité"]) + (t_q if t_ty == "Achat" else -t_q))
                    df_d.at[idx, "Quantité"] = nq
                    
                    i_c = df_d.index[df_d['Ticker'] == t_dev].tolist()
                    if not i_c:
                        df_d = pd.concat([df_d, pd.DataFrame([{"Ticker": t_dev, "Type": "💵 Cash", "Quantité": 0.0, "Court": "$ 0.00", "Valeur totale": "$ 0.00", "Pourcentage (%)": 0.0}])], ignore_index=True)
                        i_c = [len(df_d) - 1]
                    
                    df_d.at[i_c[0], "Quantité"] = extraire_nombre(df_d.at[i_c[0], "Quantité"]) + (-m_n if t_ty == "Achat" else m_n)
                    
                    st.session_state.donnees = nettoyer_dataframe(df_d)
                    recalculer_totaux_locaux()
                    save_sheet("Donnees", st.session_state.donnees)
                    st.success("✅ Transaction enregistrée, PRU calculé et Liquidités mises à jour avec succès !")
                    time.sleep(2)
                    st.rerun()

    st.divider()
    if st.button("🔄 Actualiser les cours", use_container_width=True):
        actualiser_cours_internet(False)
        st.rerun()
        
    st.subheader("⚖️ Analyse de l'allocation")
    df = st.session_state.donnees
    c_usd = sum(extraire_nombre(r["Valeur totale"]) for _, r in df[df.apply(lambda row: est_devise_liquide(row.get("Ticker", "")), axis=1)].iterrows())
    base = sum(extraire_nombre(r["Valeur totale"]) for _, r in df.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0) + c_usd
    
    if base > 0:
        st.info(f"💡 Liquidités disponibles (Toutes devises confondues) : **{c_usd:,.2f} $**")
        res = []
        for _, r in df.iterrows():
            t = str(r["Ticker"]).upper()
            cib = extraire_nombre(r["Pourcentage (%)"]) / 100
            if cib <= 0: continue
            
            act = extraire_nombre(r["Valeur totale"])
            d = (base * cib) - act
            p = extraire_nombre(r["Court"])
            q = d / p if p > 0 else 0
            
            current_pru, _ = get_pru_and_qty(t, st.session_state.transactions)
            p_str = "N/A"
            if current_pru > 0 and p > 0: 
                p_str = f"{(((p / current_pru) - 1) * 100):+.2f} %"
            
            s = "+ " if q > 0.000001 else "- " if q < -0.000001 else ""
            q_fmt = f"({s}{abs(round(q, 6)):.6f})" if "BTC" in t or "USDT" in t else f"({s}{abs(int(round(q)))})"
            act_str = f"✅ ÉQUILIBRÉ ($ {abs(d):,.2f})" if abs(d) < 1000 or abs((act/base*100) - cib*100) < 2.0 else f"{'🟢 ACHETER' if d > 0 else '🔴 VENDRE'} $ {abs(d):,.2f}"
            
            res.append({"Ticker 🔒": t, "PRU 🔒": current_pru, "Var. Jour 🔒": st.session_state.variations.get(t, "→ 0.00 %"), "Perf. Globale 🔒": p_str, "Actuel ($) 🔒": act, "Écart (%) 🔒": (act/base*100) - cib*100, "Action 🔒": act_str, "Qté (+/-) 🔒": q_fmt})
        
        def cr(v): return 'color:#2ecc71' if "↗" in str(v) or "ACHETER" in str(v) or "+" in str(v) else ('color:#e74c3c' if "↘" in str(v) or "VENDRE" in str(v) or "-" in str(v) else 'color:#95a5a6')
        st.dataframe(pd.DataFrame(res).style.format({"PRU 🔒": "{:,.2f}", "Actuel ($) 🔒": "$ {:,.2f}", "Écart (%) 🔒": "{:+.2f} %"}).map(cr, subset=["Var. Jour 🔒", "Action 🔒", "Qté (+/-) 🔒", "Perf. Globale 🔒"]), use_container_width=True, hide_index=True)

elif page_choisie == "💰 Fonds":
    st.title("💰 Fonds")
    st.write("Déclarez ici vos apports de capital (virements depuis votre compte bancaire). L'argent sera automatiquement ajouté à vos liquidités Cash.")
    df_h = st.session_state.historique
    
    with st.expander("➕ Nouveau mouvement"):
        with st.form("f_m"):
            d_m, t_m = st.date_input("Date ✍️"), st.radio("Type ✍️", ["Ajout de fond propre", "Retrait"], horizontal=True)
            m_s, d_s = st.number_input("Montant ✍️", min_value=0.00, format="%.2f"), st.selectbox("Devise ✍️", ["$", "€"])
            if st.form_submit_button("Valider"):
                o_px = float(yf.Ticker("GC=F").fast_info.get('lastPrice', 2000.0))
                m_usd, m_eur = (m_s, m_s/TAUX_EUR_USD) if d_s == "$" else (m_s*TAUX_EUR_USD, m_s)
                st.session_state.historique = pd.concat([df_h, pd.DataFrame([{"Date": d_m.strftime("%d/%m/%Y"), "Type": t_m, "Montant $": m_usd, "Montant €": m_eur, "Montant Or": m_usd/o_px}])], ignore_index=True)
                save_sheet("Historique", st.session_state.historique)
                
                dev = "USD" if d_s == "$" else "EUR"
                df_d = st.session_state.donnees.copy()
                i_c = df_d.index[df_d['Ticker'] == dev].tolist()
                if not i_c:
                    df_d = pd.concat([df_d, pd.DataFrame([{"Ticker": dev, "Type": "💵 Cash", "Quantité": 0.0, "Court": "$ 0.00", "Valeur totale": "$ 0.00", "Pourcentage (%)": 0.0}])], ignore_index=True)
                    i_c = [len(df_d) - 1]
                df_d.at[i_c[0], "Quantité"] = max(0, extraire_nombre(df_d.at[i_c[0], "Quantité"]) + (m_s if t_m == "Ajout de fond propre" else -m_s))
                
                st.session_state.donnees = nettoyer_dataframe(df_d)
                recalculer_totaux_locaux()
                save_sheet("Donnees", st.session_state.donnees)
                st.success("✅ Mouvement enregistré et Liquidités mises à jour !")
                time.sleep(1)
                st.rerun()
    
    afficher_montant_double("Total Apports nets", sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _, r in df_h.iterrows()))
    if not df_h.empty:
        d_v = df_h.copy()
        d_v.columns = [f"{c} 🔒" for c in d_v.columns]
        d_v['DT'] = pd.to_datetime(d_v['Date 🔒'], dayfirst=True, errors='coerce')
        st.dataframe(d_v.sort_values('DT', ascending=False).drop(columns=['DT']).style.format({"Montant $ 🔒": "$ {:,.2f}", "Montant € 🔒": "{:,.2f} €", "Montant Or 🔒": "{:,.4f} oz"}), use_container_width=True, hide_index=True)

elif page_choisie == "🏖️ Suivi":
    st.title("🏖️ Suivi & Évolution")
    st.write("Ce tableau enregistre vos points de passage. **Votre robot automatique enregistre une nouvelle ligne chaque nuit.** Ce tableau est en lecture seule (🔒).")
    if not st.session_state.projections.empty:
        df_v = st.session_state.projections.copy()
        df_v['DT'] = pd.to_datetime(df_v['Date'], dayfirst=True, errors='coerce')
        cf = {"Date": st.column_config.TextColumn("Date 🔒"), "Capital investi": st.column_config.NumberColumn("Cap. inv. 🔒", format="$ %.2f"), "Actifs Stratégiques": st.column_config.NumberColumn("Actifs 🔒", format="$ %.2f"), "Total Global": st.column_config.NumberColumn("Total 🔒", format="$ %.2f"), "Evolution actifs $": st.column_config.NumberColumn("Evol. Actifs ($) 🔒", format="$ %+.2f"), "Evolution actifs %": st.column_config.NumberColumn("Evol. Actifs (%) 🔒", format="%+.2f %%"), "Evolution cumulée $": st.column_config.NumberColumn("Evol. Cumulée ($) 🔒", format="$ %+.2f"), "Evolution cumulée %": st.column_config.NumberColumn("Evol. Cumulée (%) 🔒", format="%+.2f %%"), "Score TWR %": st.column_config.NumberColumn("Score TWR (%) 🔒", format="%+.2f %%"), "TG_Evolution cumulée $": st.column_config.NumberColumn("TG Evol. Cumulée ($) 🔒", format="$ %+.2f"), "TG_Evolution cumulée %": st.column_config.NumberColumn("TG Evol. Cumulée (%) 🔒", format="%+.2f %%"), "TG_Score TWR %": st.column_config.NumberColumn("TG Score TWR (%) 🔒", format="%+.2f %%")}
        st.dataframe(df_v.sort_values('DT', ascending=False).drop(columns=['DT']), column_config=cf, use_container_width=True, hide_index=True)

elif page_choisie == "📈 Performance":
    st.title("📈 Performances Annuelles & Inflation")
    df_p = st.session_state.projections
    if df_p.empty: st.info("Aucune donnée.")
    else:
        try: o_px = float(yf.Ticker("GC=F").fast_info.get('lastPrice', 2000.0))
        except: o_px = 2000.0

        df_v = df_p.copy()
        df_v['DT'] = pd.to_datetime(df_v['Date'], dayfirst=True, errors='coerce')
        df_v = df_v.dropna(subset=['DT']).sort_values('DT')
        df_v['A'] = df_v['DT'].dt.year

        dy = df_v.groupby('A').last().reset_index()
        dy['A'] = dy['A'].astype(int)
        dy['Perf brute (%)'] = (( (1+dy['Score TWR %']/100) / (1+dy['Score TWR %'].shift(1).fillna(0)/100) ) - 1) * 100
        
        j_1 = (df_v[df_v['A'] == df_v['DT'].min().year]['DT'].max() - df_v['DT'].min()).days
        if 0 < j_1 < 330:
            idx = dy[dy['A'] == df_v['DT'].min().year].index
            if not idx.empty: dy.loc[idx, 'Perf brute (%)'] = (((1 + dy.loc[idx, 'Perf brute (%)'].values[0]/100) ** (365.25/j_1)) - 1) * 100

        dy = dy.merge(st.session_state.inflation, left_on='A', right_on='Année', how='left').fillna({'Inflation (%)': 0.0})
        dy['Perf nette (%)'] = (((1 + dy['Perf brute (%)']/100) / (1 + dy['Inflation (%)']/100)) - 1) * 100
        dy['Gains ($)'] = dy['Evolution cumulée $'] - dy['Evolution cumulée $'].shift(1).fillna(0)
        dy['Or (oz)'] = dy['Actifs Stratégiques'] / o_px
        
        dh = dy[dy['A'] < datetime.datetime.now().year].copy()
            
        st.subheader("📊 Moyennes Historiques (Hors année en cours)")
        if 0 < j_1 < 330: st.info(f"💡 **Note :** Votre année de lancement ({df_v['DT'].min().year}) a été annualisée.")
        
        if not dh.empty:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Moyenne Perf. Brute", f"{dh['Perf brute (%)'].mean():+.2f} %")
            c2.metric("Moyenne Inflation", f"{dh['Inflation (%)'].mean():.2f} %")
            c3.metric("Moyenne Perf. Nette", f"{dh['Perf nette (%)'].mean():+.2f} %")
            with c4: afficher_montant_double("Moyenne Gains / An", dh['Gains ($)'].mean(), taille="medium")
        else: st.info("Historique insuffisant pour les moyennes.")
        
        st.divider()
        dy['A'] = dy['A'].astype(str)
        st.dataframe(dy.sort_values('A', ascending=False)[['A', 'Perf brute (%)', 'Inflation (%)', 'Perf nette (%)', 'Gains ($)', 'Actifs Stratégiques', 'Or (oz)']], column_config={"A": "Année 🔒", "Perf brute (%)": st.column_config.NumberColumn("Perf Brute (%) 🔒", format="%.2f %%"), "Inflation (%)": st.column_config.NumberColumn("Inflation (%) 🔒", format="%.2f %%"), "Perf nette (%)": st.column_config.NumberColumn("Perf Nette (%) 🔒", format="%.2f %%"), "Gains ($)": st.column_config.NumberColumn("Gains ($) 🔒", format="$ %.2f"), "Actifs Stratégiques": st.column_config.NumberColumn("Bilan ($) 🔒", format="$ %.2f"), "Or (oz)": st.column_config.NumberColumn("Bilan (Or) 🔒", format="%.2f oz")}, hide_index=True, use_container_width=True)

        st.divider()
        st.subheader("📊 Comparaison Brute vs Nette")
        dc = dy.sort_values('A')[['A', 'Perf brute (%)', 'Perf nette (%)']].melt('A', var_name='T', value_name='R (%)')
        dc['T'] = dc['T'].replace({'Perf brute (%)': "Brute", 'Perf nette (%)': "Nette"})
        st.plotly_chart(px.bar(dc, x='A', y='R (%)', color='T', barmode='group', color_discrete_map={"Brute": "#3498db", "Nette": "#2ecc71"}, text_auto='.2f').update_layout(yaxis_title="Rentabilité (%)", xaxis_title="", legend_title=""), use_container_width=True)

elif page_choisie == "🌴 Retraite":
    st.title("🌴 Simulateur d'Indépendance Financière")
    cap_i = sum(extraire_nombre(r["Valeur totale"]) for _, r in st.session_state.donnees.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    an = datetime.datetime.now().year
    
    m_h = 5.0
    if not st.session_state.projections.empty:
        df_v = st.session_state.projections.copy()
        df_v['DT'] = pd.to_datetime(df_v['Date'], dayfirst=True, errors='coerce')
        dy = df_v.dropna(subset=['DT']).sort_values('DT').groupby(df_v['DT'].dt.year).last().reset_index()
        dy['P'] = (( (1+dy['Score TWR %']/100) / (1+dy['Score TWR %'].shift(1).fillna(0)/100) ) - 1) * 100
        if (df_v['DT'].max() - df_v['DT'].min()).days < 330 and not dy.empty: dy.loc[0, 'P'] = (((1 + dy.loc[0, 'P']/100) ** (365.25/(df_v['DT'].max() - df_v['DT'].min()).days)) - 1) * 100 if (df_v['DT'].max() - df_v['DT'].min()).days > 0 else 0
        dh = dy[dy['DT'] < an]
        if not dh.empty: m_h = round(dh['P'].mean(), 2)

    st.subheader("⚙️ Paramètres du Simulateur")
    c1, c2, c3 = st.columns(3)
    def s_rp():
        for k in ["in_app", "in_tax"]:
            st.session_state.config[k.replace("in_","retraite_") + ("_mensuel" if "app" in k else "")] = st.session_state[k]
        try: save_sheet("Config", pd.DataFrame(list(st.session_state.config.items()), columns=["Clé", "Valeur"]))
        except: pass

    a_ret = c1.number_input("Année de départ", an+1, 2100, 2055)
    app = c1.number_input("Apport mensuel d'aujourd'hui ($)", 0.0, step=50.0, value=float(st.session_state.config.get("retraite_apport_mensuel", 250.0)), key="in_app", on_change=s_rp)
    r_a = c2.number_input("Performance Scénario A (%)", 0.0, 30.0, max(0.0, float(m_h)))
    r_b = c2.number_input("Performance Scénario B (%)", 0.0, 30.0, 8.0)
    inf = c3.number_input("Inflation annuelle (%)", 0.0, 15.0, 2.0)
    tax = c3.number_input("Flat Tax (%)", 0.0, 60.0, float(st.session_state.config.get("retraite_taxe", 30.0)), key="in_tax", on_change=s_rp)
    st.divider()

    c_a = c_b = cap_i
    ap_a = ap_b = app
    t_d = []
    for y in range(an, a_ret):
        for _ in range(12 if y > an else max(1, 13 - datetime.datetime.now().month)):
            c_a, c_b = c_a * (1 + ((1+r_a/100)**(1/12)-1)) + ap_a, c_b * (1 + ((1+r_b/100)**(1/12)-1)) + ap_b
        ap_a *= (1 + inf/100) ; ap_b *= (1 + inf/100)
        c_a_n, c_b_n = c_a / ((1+inf/100)**(y-an+1)), c_b / ((1+inf/100)**(y-an+1))
        t_d.append({"Année": y, "A": round(c_a_n, 2), "B": round(c_b_n, 2)})

    tx_r = max(0.0, ((1.08)/(1+inf/100))-1)
    st.subheader(f"🎯 Capital projeté au 1er Janvier {a_ret}")
    cA, cB = st.columns(2)
    with cA:
        st.markdown(f"### Scénario A ({r_a:.2f} %)")
        afficher_montant_double("💰 Brut 🔒", c_a)
        afficher_montant_double("🛒 Net 🔒", c_a_n)
        afficher_montant_double("Rente Nette (Avant impôts)", c_a_n*tx_r/12, couleur_valeur="#2ecc71")
        afficher_montant_double(f"Après Impôts ({tax}%)", (c_a_n*tx_r/12)*(1-tax/100), couleur_valeur="#e67e22", taille="medium")
    with cB:
        st.markdown(f"### Scénario B ({r_b:.2f} %)")
        afficher_montant_double("💰 Brut 🔒", c_b)
        afficher_montant_double("🛒 Net 🔒", c_b_n)
        afficher_montant_double("Rente Nette (Avant impôts)", c_b_n*tx_r/12, couleur_valeur="#3498db")
        afficher_montant_double(f"Après Impôts ({tax}%)", (c_b_n*tx_r/12)*(1-tax/100), couleur_valeur="#e67e22", taille="medium")

    if t_d:
        st.divider()
        st.plotly_chart(px.line(pd.DataFrame(t_d).melt("Année", var_name="S", value_name="Net"), x="Année", y="Net", color="S", color_discrete_map={"A":"#2ecc71", "B":"#3498db"}).update_layout(yaxis_title="Capital Net ($)", xaxis_title=""), use_container_width=True)

elif page_choisie == "🏛️ Fiscalité":
    st.title("🏛️ Simulateur Fiscal (Lecture Drive)")
    st.write("Cet outil lit instantanément votre feuille 'Transaction', choisit la meilleure imposition, et estime votre impôt.")

    df_t = st.session_state.transactions.copy()
    if 'Date_DT' not in df_t.columns: df_t['Date_DT'] = pd.to_datetime(df_t['Date'], dayfirst=True, errors='coerce')
    ad = sorted(df_t['Date_DT'].dropna().dt.year.unique().tolist(), reverse=True)
    a_f = st.selectbox("📅 Sélectionner l'année des revenus :", ad if ad else [datetime.datetime.now().year])
    st.divider()

    st.subheader("👤 1. Ma Situation Familiale & Professionnelle")
    def u_fc():
        for k in ["in_statut", "in_enf", "in_s1", "in_s2", "in_u1", "in_k1", "in_cv1", "in_r1", "in_u2", "in_k2", "in_cv2", "in_r2"]:
            if k in st.session_state: st.session_state.config[k.replace("in_", "f_")] = st.session_state[k]
        try: save_sheet("Config", pd.DataFrame(list(st.session_state.config.items()), columns=["Clé", "Valeur"]))
        except: pass

    cs1, cs2 = st.columns(2)
    with cs1:
        st_m = st.radio("Statut", ["Célibataire / Divorcé(e) / Veuf(ve)", "Marié(e) / Pacsé(e)"], index=0 if st.session_state.config.get("f_statut", "Célibataire / Divorcé(e) / Veuf(ve)") == "Célibataire / Divorcé(e) / Veuf(ve)" else 1, key="in_statut", on_change=u_fc)
        enf = st.number_input("Enfants", 0, 10, int(st.session_state.config.get("f_enf", 0)), key="in_enf", on_change=u_fc)
    with cs2:
        s1 = st.number_input("Salaires (Déclarant 1) €", 0.0, value=float(st.session_state.config.get("f_s1", 30000)), step=1000.0, key="in_s1", on_change=u_fc)
        s2 = st.number_input("Salaires (Déclarant 2) €", 0.0, value=float(st.session_state.config.get("f_s2", 0)), step=1000.0, key="in_s2", on_change=u_fc) if "Marié" in st_m else 0.0

    st.markdown("---")
    st.markdown("#### 🚗 Frais Professionnels")
    cf1, cf2 = st.columns(2)
    with cf1:
        u1 = st.checkbox("Frais réels (Vous)", value=bool(int(st.session_state.config.get("f_u1", 0))), key="in_u1", on_change=u_fc)
        fr1 = 0.0
        if u1:
            k1 = st.number_input("KM (Vous)", 0, 100000, int(st.session_state.config.get("f_k1", 0)), step=1000, key="in_k1", on_change=u_fc)
            cv1 = st.selectbox("CV (Vous)", [3, 4, 5, 6, 7], index=[3,4,5,6,7].index(int(st.session_state.config.get("f_cv1", 5))), key="in_cv1", on_change=u_fc)
            r1 = st.number_input("Repas (Vous)", 0, 300, int(st.session_state.config.get("f_r1", 0)), step=10, key="in_r1", on_change=u_fc)
            fr1 = calcul_frais_km(k1, cv1) + (r1 * 5.35)
            st.info(f"💰 Frais (Vous) : {fr1:,.2f} €")
    
    fr2 = 0.0
    if "Marié" in st_m:
        with cf2:
            u2 = st.checkbox("Frais réels (Conjoint)", value=bool(int(st.session_state.config.get("f_u2", 0))), key="in_u2", on_change=u_fc)
            if u2:
                k2 = st.number_input("KM (Conjoint)", 0, 100000, int(st.session_state.config.get("f_k2", 0)), step=1000, key="in_k2", on_change=u_fc)
                cv2 = st.selectbox("CV (Conjoint)", [3, 4, 5, 6, 7], index=[3,4,5,6,7].index(int(st.session_state.config.get("f_cv2", 5))), key="in_cv2", on_change=u_fc)
                r2 = st.number_input("Repas (Conjoint)", 0, 300, int(st.session_state.config.get("f_r2", 0)), step=10, key="in_r2", on_change=u_fc)
                fr2 = calcul_frais_km(k2, cv2) + (r2 * 5.35)
                st.info(f"💰 Frais (Conjoint) : {fr2:,.2f} €")
    st.divider()

    df_v = df_t[(df_t['Type'].str.lower().str.contains('vente')) & (df_t['Date_DT'].dt.year == a_f)].copy()
    rf = []
    for _, r in df_v.iterrows():
        t = str(r['Ticker']).upper()
        if est_devise_liquide(t): continue
        q, n, pru, fx = float(r['Quantité']), float(r['Montant Net']), float(r.get('PRU (Devise)', 0)), float(r.get('Taux change (EUR)', 1))
        pv = (n - (pru * q)) * fx
        rf.append({"Actif": t, "Date": r['Date'], "Qté": q, "PRU": pru, "Net": n, "FX": fx, "PV €": pv, "Cat": "Crypto" if any(c in t for c in ["BTC","ETH","USDT"]) else "Action"})

    df_f = pd.DataFrame(rf)
    st.subheader(f"📝 2. Détail des Ventes (Année {a_f})")
    if df_f.empty:
        st.info("Aucune cession détectée.")
        pv_a = pv_c = 0.0
    else:
        pv_a = df_f[df_f['Cat'] == 'Action']['PV €'].sum()
        pv_c = df_f[df_f['Cat'] == 'Crypto']['PV €'].sum()
        tabs = st.tabs(sorted(df_f["Actif"].unique()))
        for i, a in enumerate(sorted(df_f["Actif"].unique())):
            with tabs[i]:
                d = df_f[df_f['Actif'] == a].drop(columns=['Actif', 'Cat'])
                st.dataframe(d, column_config={"PRU": st.column_config.NumberColumn(format="%.2f"), "Net": st.column_config.NumberColumn(format="%.2f"), "FX": st.column_config.NumberColumn(format="%.4f"), "PV €": st.column_config.NumberColumn(format="%.2f €")}, hide_index=True, use_container_width=True)
                st.markdown(f"**Bilan : <span style='color:{'green' if d['PV €'].sum()>=0 else 'red'}'>{d['PV €'].sum():+.2f} €</span>**", unsafe_allow_html=True)
    st.divider()

    p = (1 if "Cél" in st_m else 2) + (0.5 if enf <= 2 else 0) * enf + (1.0 if enf >= 3 else 0)
    rn1, rn2 = s1 - max(s1*0.1, fr1), s2 - max(s2*0.1, fr2)
    imp_s = calcul_impot_ir(rn1 + rn2, p, st_m)
    
    st.subheader("💡 3. Imposition & Prélèvement")
    if df_f.empty or pv_a <= 0: choix, c_b = "Aucun", 0.0
    else:
        c_p = pv_a * 0.3
        c_b = (calcul_impot_ir(rn1 + rn2 + pv_a, p, st_m) - imp_s) + (pv_a * 0.172)
        choix = "Barème" if c_b < c_p else "PFU"
        st.success(f"✅ Option fiscale : **{choix}** (Impôt bourse estimé : {min(c_b, c_p):,.2f} €)")

    t_f = (imp_s / (s1+s2) * 100) if (s1+s2)>0 else 0.0
    t_p = (calcul_impot_ir(rn1, 1.0, "Célibataire", False) / s1 * 100) if s1 > 0 else 0.0
    
    ct1, ct2 = st.columns(2)
    ct1.info(f"👨‍👩‍👧‍👦 **Taux Commun : {t_f:.1f} %** (Impôt salaires foyer : {imp_s:,.2f} €/an)")
    if "Marié" in st_m: ct2.success(f"👤 **Ton Taux Perso : {t_p:.1f} %** (Prélèvement ~ {(s1*t_p/100)/12:,.2f} €/mois)")
    
    st.divider()
    st.subheader("📝 4. Résumé pour la Déclaration")
    cd1, cd2 = st.columns(2)
    with cd1:
        st.markdown("### 🔹 Formulaire 3916\n- Case 8UU (sur la 2042) : À cocher.\n- Swissquote Bank SA, Gland, Suisse")
        st.markdown("### 🔹 Formulaire 2074")
        if pv_a > 0: st.markdown(f"- Ligne 905 : {pv_a:,.0f} €")
        elif pv_a < 0: st.markdown(f"- Ligne 913 : {abs(pv_a):,.0f} €")
    with cd2:
        st.markdown("### 🔹 Formulaire 2086 (Crypto)")
        if pv_c > 0: st.markdown(f"- Case 3AN : {pv_c:,.0f} €")
        elif pv_c < 0: st.markdown(f"- Case 3BN : {abs(pv_c):,.0f} €")
        st.markdown("### 🔹 Formulaire 2042")
        if pv_a > 0: st.markdown(f"- Case 3VG : {pv_a:,.0f} €\n- Case 2OP : **{'À COCHER' if choix=='Barème' else 'NE PAS COCHER'}**")
        elif pv_a < 0: st.markdown(f"- Case 3VH : {abs(pv_a):,.0f} €")
