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
        pwd = st.text_input("Veuillez entrer votre mot de passe :", type="password")
        if pwd == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            st.rerun()
        elif pwd != "":
            st.error("Mot de passe incorrect.")
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
        return df if not df.empty else pd.DataFrame(columns=default_cols)
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
    save_sheet("Config", pd.DataFrame(list(st.session_state.config.items()), columns=["Clé", "Valeur"]))

def afficher_montant_double(label, montant_usd, delta_str="", couleur_valeur=None, taille="large"):
    montant_eur = montant_usd / TAUX_EUR_USD
    str_usd = f"{montant_usd:,.2f}".replace(',', ' ')
    str_eur = f"{montant_eur:,.2f}".replace(',', ' ')
    delta_html = ""
    if delta_str:
        cd = "#2ecc71" if "+" in delta_str else ("#e74c3c" if "-" in delta_str else "inherit")
        delta_html = f"<div style='font-size:0.9rem; font-weight:600; color:{cd}; padding-top:0.2rem;'>{delta_str}</div>"
    t_val = "1.8rem" if taille == "large" else ("1.4rem" if taille == "medium" else "1.2rem")
    t_lbl = "0.9rem" if taille == "large" else "0.85rem"
    c_val = f"color: {couleur_valeur};" if couleur_valeur else ""
    st.markdown(f"""
    <div style="margin-bottom: 0.8rem;">
        <div style="font-size:{t_lbl}; opacity:0.8; margin-bottom:0.2rem;">{label}</div>
        <div style="font-size:{t_val}; font-weight:600; line-height:1.2; {c_val}">
            {str_usd} $ <span style="font-size:0.65em; opacity:0.7; font-weight:400;">/ {str_eur} €</span>
        </div>{delta_html}
    </div>""", unsafe_allow_html=True)

def est_devise_liquide(ticker):
    t = str(ticker).upper().strip()
    if t.endswith("=X"): return True
    return any(m in t for m in ["USD", "EUR", "CHF", "JPY", "CNY", "GBP"]) and not any(c in t for c in ["BTC", "ETH"])

def nettoyer_dataframe(df):
    cols = ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]
    for c in df.columns:
        if "quantit" in str(c).lower() or "qte" in str(c).lower(): df.rename(columns={c: "Quantité"}, inplace=True)
    if "Type" not in df.columns:
        df["Type"] = ""
        for idx, row in df.iterrows():
            t = str(row.get("Ticker", "")).upper()
            if est_devise_liquide(t): df.at[idx, "Type"] = "💵 Cash"
            elif "BTC" in t or "ETH" in t or t.endswith("USDT"): df.at[idx, "Type"] = "₿ Crypto"
            else: df.at[idx, "Type"] = "🛢️ Action"
    for c in cols:
        if c not in df.columns: df[c] = 0.0 if c == "Pourcentage (%)" else ("$ 0.00" if c in ["Court", "Valeur totale"] else "")
    return df[cols].reset_index(drop=True)

def recalculer_toute_la_base_projections(df):
    if df is None or df.empty: return df
    df_t = df.copy()
    c_base = ["Date", "Capital investi", "Actifs Stratégiques", "Total Global"]
    for i, nom in enumerate(c_base):
        if i < len(df_t.columns): df_t.rename(columns={df_t.columns[i]: nom}, inplace=True)
    for c in ["Capital investi", "Actifs Stratégiques", "Total Global"]: df_t[c] = df_t[c].apply(extraire_nombre)
    df_t['DT_TRI'] = pd.to_datetime(df_t['Date'], dayfirst=True, errors='coerce')
    df_t = df_t.sort_values('DT_TRI').reset_index(drop=True)
    
    res, c_twr, tg_twr = [], 1.0, 1.0
    for i in range(len(df_t)):
        r = df_t.iloc[i].to_dict()
        cap, act, tg = r["Capital investi"], r["Actifs Stratégiques"], r["Total Global"]
        if i == 0:
            r["Evolution actifs $"] = r["Evolution actifs %"] = 0.0
            r["Evolution cumulée $"] = act - cap
            r["Evolution cumulée %"] = ((act - cap) / cap * 100) if cap != 0 else 0.0
            c_twr *= (1 + ((act - cap) / cap if cap != 0 else 0.0))
            r["TG_Evolution cumulée $"] = tg - cap
            r["TG_Evolution cumulée %"] = ((tg - cap) / cap * 100) if cap != 0 else 0.0
            tg_twr *= (1 + ((tg - cap) / cap if cap != 0 else 0.0))
        else:
            prev = df_t.iloc[i-1]
            d_cap = cap - prev["Capital investi"]
            evo_usd = (act - prev["Actifs Stratégiques"]) - d_cap
            r["Evolution actifs $"] = evo_usd
            r["Evolution actifs %"] = (evo_usd / prev["Actifs Stratégiques"] * 100) if prev["Actifs Stratégiques"] != 0 else 0.0
            r["Evolution cumulée $"] = act - cap
            r["Evolution cumulée %"] = ((act - cap) / cap * 100) if cap != 0 else 0.0
            c_twr *= (1 + (evo_usd / (prev["Actifs Stratégiques"] + d_cap) if (prev["Actifs Stratégiques"] + d_cap) != 0 else 0.0))
            evo_tg = (tg - prev["Total Global"]) - d_cap
            r["TG_Evolution cumulée $"] = tg - cap
            r["TG_Evolution cumulée %"] = ((tg - cap) / cap * 100) if cap != 0 else 0.0
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
            df.at[idx, "Valeur totale"], df.at[idx, "Court"] = f"$ {round(c*q, 2):,.2f}", f"$ {c:.2f}"
        st.session_state.donnees = df

def actualiser_cours_internet(silencieux=False):
    if "donnees" in st.session_state:
        if not silencieux: st.toast("🔄 Actualisation des cours en direct...")
        df_temp = st.session_state.donnees.copy()
        changement = False
        taux_cache = {} 
        if "variations" not in st.session_state: st.session_state.variations = {}
        for idx, row in df_temp.iterrows():
            tick = str(row.get("Ticker", "")).strip().upper()
            if tick and tick != "NAN":
                succ = False
                if tick.endswith("USDT"):
                    for base in ["https://api.binance.com", "https://api.binance.us"]:
                        try:
                            url = f"{base}/api/v3/klines?symbol={tick}&interval=1d&limit=2"
                            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=3) as resp:
                                data = json.loads(resp.read().decode())
                                p_usd = float(data[1][4]) if len(data) >= 2 else float(data[0][4])
                                p_prev = float(data[0][4])
                                var = ((p_usd - p_prev) / p_prev) * 100 if p_prev > 0 else 0.0
                                st.session_state.variations[tick] = f"{'↗' if var > 0 else '↘' if var < 0 else '→'} {var:+.2f} %"
                                df_temp.at[idx, "Court"] = f"$ {p_usd:.2f}"
                                changement = succ = True
                                break 
                        except: continue 
                if succ: continue 
                try:
                    asset = yf.Ticker(tick.replace("USDT", "-USD"))
                    p_loc = float(asset.fast_info.get('lastPrice', 0.0))
                    if p_loc == 0: p_loc = float(asset.history(period="1d")['Close'].iloc[-1])
                    p_prev = float(asset.fast_info.get('previous_close', 0.0))
                    if p_prev == 0: p_prev = float(asset.history(period="5d")['Close'].iloc[-2])
                    var = ((p_loc - p_prev) / p_prev) * 100 if p_prev > 0 else 0.0
                    st.session_state.variations[tick] = f"{'↗' if var > 0 else '↘' if var < 0 else '→'} {var:+.2f} %"
                    dev = str(asset.fast_info.get('currency', 'USD')).upper()
                    p_usd = p_loc * (0.01 if dev == "GBP" else 1.0)
                    if dev not in ["USD", "", "NONE", "GBP"]:
                        if dev not in taux_cache:
                            try: taux_cache[dev] = float(yf.Ticker(f"{dev}USD=X").fast_info.get('lastPrice', 1.0))
                            except: taux_cache[dev] = 1.0
                        p_usd *= taux_cache[dev]
                    df_temp.at[idx, "Court"] = f"$ {p_usd:.2f}"
                    changement = True
                except:
                    if tick not in st.session_state.variations: st.session_state.variations[tick] = "→ 0.00 %"
        if changement:
            st.session_state.donnees = df_temp
            recalculer_totaux_locaux()
            save_sheet("Donnees", st.session_state.donnees)

@st.cache_data(ttl=86400) 
def recuperer_inflation_france():
    try:
        url = "https://api.worldbank.org/v2/country/FRA/indicator/FP.CPI.TOTL.ZG?format=json&per_page=20"
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if len(data) == 2: return {int(i['date']): round(float(i['value']), 2) for i in data[1] if i['value'] is not None}
    except: pass
    return None

def calcul_frais_km(km, cv):
    coefs = {3:(0.529, 0.316, 1065, 0.370), 4:(0.606, 0.340, 1330, 0.407), 5:(0.636, 0.357, 1395, 0.427), 6:(0.665, 0.374, 1457, 0.447), 7:(0.697, 0.394, 1515, 0.470)}
    c = coefs.get(cv, coefs[7])
    return km * c[0] if km <= 5000 else (km * c[1] + c[2] if km <= 20000 else km * c[3])

def calcul_impot_ir(rev, parts, statut, apply_decote=True):
    qf = rev / parts
    imp = sum((min(qf, lim) - prev) * tx for lim, prev, tx in [(28797, 11294, 0.11), (82341, 28797, 0.30), (177106, 82341, 0.41), (9999999, 177106, 0.45)] if qf > prev) * parts
    if apply_decote:
        lim_d, base_d = (2002, 906) if "Cél" in statut else (3300, 1493)
        if imp <= lim_d: imp = max(0, imp - (base_d - (imp * 0.4525)))
    return 0.0 if imp < 61 else imp

# --- 5. CHARGEMENT INITIAL ---
if "config" not in st.session_state:
    df_c = load_sheet("Config", ["Clé", "Valeur"])
    st.session_state.config = {str(r["Clé"]): r["Valeur"] for _, r in df_c.iterrows() if pd.notna(r["Clé"])}
    
defs = {"apport_dispo":0.0, "retraite_apport_mensuel":250.0, "retraite_taxe":30.0, "f_statut":"Marié(e) / Pacsé(e)", "f_enf":0, "f_s1":30000.0, "f_s2":0.0, "f_u1":0, "f_k1":0, "f_cv1":5, "f_r1":0, "f_u2":0, "f_k2":0, "f_cv2":5, "f_r2":0}
for k, v in defs.items():
    if k not in st.session_state.config: st.session_state.config[k] = v

if "donnees" not in st.session_state: st.session_state.donnees = nettoyer_dataframe(load_sheet("Donnees", []))
if "historique" not in st.session_state:
    st.session_state.historique = load_sheet("Historique", ["Date", "Type", "Montant $", "Montant €", "Montant Or"])
    for c in ["Montant $", "Montant €", "Montant Or"]: st.session_state.historique[c] = st.session_state.historique[c].apply(extraire_nombre)
if "projections" not in st.session_state: st.session_state.projections = recalculer_toute_la_base_projections(load_sheet("Projections", []))
if "inflation" not in st.session_state: 
    df_i = load_sheet("Inflation", ["Année", "Inflation (%)"])
    if not df_i.empty:
        df_i['Année'] = pd.to_numeric(df_i['Année'], errors='coerce').fillna(0).astype(int)
        df_i['Inflation (%)'] = pd.to_numeric(df_i['Inflation (%)'], errors='coerce').fillna(0.0)
        df_i.drop_duplicates(subset=['Année'], keep='last', inplace=True)
    st.session_state.inflation = df_i
if "transactions" not in st.session_state:
    df_t = load_sheet("Transaction", ["Ticker", "Type", "Date", "Quantité", "Cours", "Frais", "Montant Net", "Devise", "PRU (Devise)", "Taux change (EUR)"])
    for c in ["Quantité", "Cours", "Frais", "Montant Net", "PRU (Devise)", "Taux change (EUR)"]: df_t[c] = df_t[c].apply(extraire_nombre)
    st.session_state.transactions = df_t

if "inflation_check_done" not in st.session_state:
    st.session_state.inflation_check_done = True
    d_inf = recuperer_inflation_france()
    if d_inf and not st.session_state.projections.empty:
        ans = pd.to_datetime(st.session_state.projections['Date'], dayfirst=True, errors='coerce').dt.year.dropna().unique()
        n_inf = []
        for a in ans:
            v_off = d_inf.get(a, 0.0)
            n_inf.append({'Année': a, 'Inflation (%)': v_off})
        st.session_state.inflation = pd.DataFrame(n_inf)
        save_sheet("Inflation", st.session_state.inflation)

if "dernier_refresh_cours" not in st.session_state: st.session_state.dernier_refresh_cours = 0
if time.time() - st.session_state.dernier_refresh_cours >= 900:
    actualiser_cours_internet(silencieux=(st.session_state.dernier_refresh_cours == 0))
    st.session_state.dernier_refresh_cours = time.time()

# --- 6. NAVIGATION ---
st.sidebar.title("Menu")
page = st.sidebar.radio("Aller vers :", ["📊 Tableau de bord", "📋 Liste des actifs", "⚖️ Rééquilibrage", "💰 Fonds", "🏖️ Suivi", "📈 Performance", "🌴 Retraite", "🏛️ Fiscalité"])

st.sidebar.divider()
if st.sidebar.button("🔄 Recharger l'app", use_container_width=True):
    st.session_state.clear()
    st.rerun()

# --- 7. PAGES ---
if page == "📊 Tableau de bord":
    st.title("📊 Vue d'ensemble de mon Patrimoine")
    df_a, df_p = st.session_state.donnees, st.session_state.projections
    v_inv = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_a.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    v_tot = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_a.iterrows())
    cap = sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _, r in st.session_state.historique.iterrows())
    
    df_p_live = pd.concat([df_p, pd.DataFrame([{"Date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "Capital investi": cap, "Actifs Stratégiques": v_inv, "Total Global": v_tot}])], ignore_index=True)
    df_p_live = recalculer_toute_la_base_projections(df_p_live)
    
    var_glob_usd = var_strat_usd = val_tot_v = val_inv_v = 0.0
    for _, r in df_a.iterrows():
        t = str(r.get("Ticker", "")).strip().upper()
        v_act = extraire_nombre(r["Valeur totale"])
        v_pct = float(re.search(r'([+-]?\d+\.?\d*)', st.session_state.variations.get(t, "0")).group(1)) if re.search(r'([+-]?\d+\.?\d*)', st.session_state.variations.get(t, "0")) else 0.0
        v_v = v_act / (1 + v_pct / 100) if (1 + v_pct / 100) != 0 else v_act
        var_glob_usd += (v_act - v_v)
        val_tot_v += v_v
        if extraire_nombre(r["Pourcentage (%)"]) > 0:
            var_strat_usd += (v_act - v_v)
            val_inv_v += v_v
            
    pct_glob = (var_glob_usd / val_tot_v * 100) if val_tot_v > 0 else 0.0
    pct_strat = (var_strat_usd / val_inv_v * 100) if val_inv_v > 0 else 0.0
    
    delta = p_delta = delta_tg = p_delta_tg = 0.0
    if not df_p.empty:
        df_d = df_p.copy()
        df_d['DT'] = pd.to_datetime(df_d['Date'], dayfirst=True, errors='coerce')
        df_d = df_d.dropna(subset=['DT']).sort_values('DT')
        if not df_d.empty:
            df_past = df_d[df_d['DT'] <= pd.Timestamp.now() - pd.DateOffset(years=1)]
            row_ref = df_past.iloc[-1] if not df_past.empty else df_d.iloc[0] 
            v_ref_strat, v_ref_tg = extraire_nombre(row_ref["Actifs Stratégiques"]), extraire_nombre(row_ref["Total Global"])
            delta, delta_tg = v_inv - v_ref_strat, v_tot - v_ref_tg
            if v_ref_strat > 0: p_delta = (delta / v_ref_strat) * 100
            if v_ref_tg > 0: p_delta_tg = (delta_tg / v_ref_tg) * 100

    besoin_req = any(abs((v_inv * (extraire_nombre(r["Pourcentage (%)"])/100)) - extraire_nombre(r["Valeur totale"])) >= 1000 and abs((extraire_nombre(r["Valeur totale"])/v_inv*100) - extraire_nombre(r["Pourcentage (%)"])) >= 2.0 for _, r in df_a.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0) if v_inv > 0 else False

    st.subheader("⚙️ 1. Pilotage & Statut")
    colb, cols = st.columns([1, 2])
    with colb:
        if st.button("🔄 Actualiser les cours", use_container_width=True):
            actualiser_cours_internet(False)
            st.rerun()
    with cols:
        if besoin_req: st.warning("⚠️ **Rééquilibrage nécessaire**")
        else: st.success("✅ **Équilibré**")
    st.divider()

    st.subheader("🌍 2. Total Global")
    c_tg, _ = st.columns(2)
    with c_tg:
        afficher_montant_double("Total Global", v_tot, f"{delta_tg:+,.2f} $ ({p_delta_tg:+.2f} % sur 1 an glissant)")
        st.markdown(f"<div style='margin-top:-0.5rem; margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if var_glob_usd>=0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if var_glob_usd>=0 else '#e74c3c'}'>{var_glob_usd:+,.2f} $ ({pct_glob:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    
    if not df_p.empty:
        df_v_tg = df_p_live.copy()
        df_v_tg['DT'] = pd.to_datetime(df_v_tg['Date'], dayfirst=True, errors='coerce')
        df_v_tg = df_v_tg.dropna(subset=['DT']).sort_values('DT').reset_index(drop=True)
        st.markdown("**📈 Évolution globale**")
        cf1, cf2 = st.columns(2)
        f_tg = cf1.radio("Période globale :", ["Depuis le début", "Depuis 1 an", "Depuis le début de l'année"], horizontal=True, key="f_tg")
        m_tg = cf2.radio("Affichage :", ["Rendement Absolu (ROI)", "Score TWR (Talent)"], horizontal=True, key="m_tg")
        n = pd.Timestamp.now()
        if f_tg == "Depuis 1 an": df_v_tg = df_v_tg[df_v_tg['DT'] >= (n - pd.DateOffset(years=1))]
        elif f_tg == "Depuis le début de l'année": df_v_tg = df_v_tg[df_v_tg['DT'] >= pd.Timestamp(year=n.year - 1, month=12, day=31)]
        if not df_v_tg.empty:
            df_v_tg.set_index('DT', inplace=True)
            d_usd = df_v_tg['TG_Evolution cumulée $'].iloc[-1] - df_v_tg['TG_Evolution cumulée $'].iloc[0]
            pct = (d_usd / df_v_tg['Total Global'].iloc[0] * 100) if df_v_tg['Total Global'].iloc[0] > 0 else 0.0
            twr_p = (( (1+df_v_tg['TG_Score TWR %'].iloc[-1]/100) / (1+df_v_tg['TG_Score TWR %'].iloc[0]/100) ) - 1) * 100
            cg1, cg2 = st.columns([1, 3])
            with cg1:
                if "ROI" in m_tg:
                    afficher_montant_double("Gains nets", df_v_tg['TG_Evolution cumulée $'].iloc[-1], f"{d_usd:+,.2f} $ ({pct:+.2f} %)", taille="medium")
                else:
                    st.metric("Score TWR", f"{df_v_tg['TG_Score TWR %'].iloc[-1]:+.2f} %", f"{twr_p:+.2f} % (sur la période)")
            with cg2:
                fig_l = px.line(df_v_tg.reset_index(), x='DT', y='TG_Evolution cumulée $' if "ROI" in m_tg else 'TG_Score TWR %')
                fig_l.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
                st.plotly_chart(fig_l, use_container_width=True)

        c_p1, c_p2 = st.columns(2)
        with c_p1:
            df_pie = df_a.copy()
            df_pie['V'] = df_pie['Valeur totale'].apply(extraire_nombre)
            df_pie = df_pie[df_pie['V']>0]
            if not df_pie.empty: st.plotly_chart(px.pie(df_pie, values='V', names='Type', hole=0.4, color_discrete_map={"🛢️ Action": "#e74c3c", "📜 Obligation": "#3498db", "💰 Or": "#f1c40f", "₿ Crypto": "#9b59b6", "💵 Cash": "#2ecc71"}).update_layout(showlegend=False, margin=dict(t=0,b=0,l=0,r=0)), use_container_width=True)

    st.divider()
    st.subheader("🎯 3. Actifs Stratégiques")
    c_s, _ = st.columns(2)
    with c_s:
        afficher_montant_double("Actifs Stratégiques", v_inv, f"{delta:+,.2f} $ ({p_delta:+.2f} % sur 1 an glissant)")
        st.markdown(f"<div style='margin-top:-0.5rem; margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if var_strat_usd>=0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if var_strat_usd>=0 else '#e74c3c'}'>{var_strat_usd:+,.2f} $ ({pct_strat:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    
    if not df_p.empty:
        df_v_s = df_p_live.copy()
        df_v_s['DT'] = pd.to_datetime(df_v_s['Date'], dayfirst=True, errors='coerce')
        df_v_s = df_v_s.dropna(subset=['DT']).sort_values('DT').reset_index(drop=True)
        st.markdown("**📈 Évolution de la stratégie**")
        cf1, cf2 = st.columns(2)
        f_s = cf1.radio("Période strat :", ["Depuis le début", "Depuis 1 an", "Depuis le début de l'année"], horizontal=True, key="f_s")
        m_s = cf2.radio("Affichage strat :", ["Rendement Absolu (ROI)", "Score TWR (Talent)"], horizontal=True, key="m_s")
        n = pd.Timestamp.now()
        if f_s == "Depuis 1 an": df_v_s = df_v_s[df_v_s['DT'] >= (n - pd.DateOffset(years=1))]
        elif f_s == "Depuis le début de l'année": df_v_s = df_v_s[df_v_s['DT'] >= pd.Timestamp(year=n.year - 1, month=12, day=31)]
        if not df_v_s.empty:
            df_v_s.set_index('DT', inplace=True)
            d_usd = df_v_s['Evolution cumulée $'].iloc[-1] - df_v_s['Evolution cumulée $'].iloc[0]
            pct = (d_usd / df_v_s['Actifs Stratégiques'].iloc[0] * 100) if df_v_s['Actifs Stratégiques'].iloc[0] > 0 else 0.0
            twr_p = (( (1+df_v_s['Score TWR %'].iloc[-1]/100) / (1+df_v_s['Score TWR %'].iloc[0]/100) ) - 1) * 100
            cg1, cg2 = st.columns([1, 3])
            with cg1:
                if "ROI" in m_s:
                    afficher_montant_double("Gains nets strat", df_v_s['Evolution cumulée $'].iloc[-1], f"{d_usd:+,.2f} $ ({pct:+.2f} %)", taille="medium")
                else:
                    st.metric("Score TWR Strat", f"{df_v_s['Score TWR %'].iloc[-1]:+.2f} %", f"{twr_p:+.2f} %")
            with cg2:
                fig_l = px.line(df_v_s.reset_index(), x='DT', y='Evolution cumulée $' if "ROI" in m_s else 'Score TWR %')
                fig_l.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
                st.plotly_chart(fig_l, use_container_width=True)

    df_strat = df_a[df_a['Pourcentage (%)'].apply(extraire_nombre) > 0].copy()
    df_strat['V'] = df_strat['Valeur totale'].apply(extraire_nombre)
    cp1, cp2 = st.columns(2)
    with cp1:
        if not df_strat.empty: st.plotly_chart(px.pie(df_strat[df_strat['V']>0], values='V', names='Type', hole=0.4, color='Type', color_discrete_map={"🛢️ Action": "#e74c3c", "📜 Obligation": "#3498db", "💰 Or": "#f1c40f", "₿ Crypto": "#9b59b6", "💵 Cash": "#2ecc71"}).update_layout(showlegend=False, margin=dict(t=0,b=0,l=0,r=0)), use_container_width=True)
    with cp2:
        if not df_strat.empty: st.plotly_chart(px.pie(df_strat[df_strat['V']>0], values='V', names='Ticker', hole=0.4).update_layout(showlegend=False, margin=dict(t=0,b=0,l=0,r=0)), use_container_width=True)

    st.divider()
    st.subheader("🏖️ 4. Rente Mensuelle (Base 8%)")
    cr1, cr2 = st.columns(2)
    with cr1: inf = st.slider("Inflation cible à déduire (%)", 0.0, 15.0, 2.0, 0.1)
    with cr2: afficher_montant_double("Rente Mensuelle Nette", (v_inv * max(0.0, ((1.08)/(1+inf/100))-1)) / 12.0, couleur_valeur="#3498db")

elif page == "📋 Liste des actifs":
    st.title("📋 Liste de mes actifs")
    df_a = st.session_state.donnees.copy()
    v_inv = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_a.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    v_tot = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_a.iterrows())
    somme_p = sum(extraire_nombre(r["Pourcentage (%)"]) for _, r in df_a.iterrows())

    c1, c2, c3 = st.columns(3)
    with c1: afficher_montant_double("Actifs Stratégiques", v_inv)
    with c2: afficher_montant_double("Total Global", v_tot)
    with c3:
        ec = round(100 - somme_p, 2)
        st.markdown(f"<div style='margin-bottom:0.8rem;'><div style='font-size:0.9rem; opacity:0.8;'>Répartition Cible</div><div style='font-size:1.8rem; font-weight:600;'>{somme_p:.2f} %</div><div style='font-size:0.9rem; font-weight:600; color:{'#2ecc71' if ec==0 else '#e74c3c'}'>{'✅ Cible atteinte' if ec==0 else f'⚠️ {abs(ec):.2f} % manquant/en trop'}</div></div>", unsafe_allow_html=True)
    st.divider()

    if st.button("🔄 Actualiser les cours", use_container_width=True):
        actualiser_cours_internet(False)
        st.rerun()

    df_a['Var 🔒'] = df_a['Ticker'].apply(lambda x: st.session_state.variations.get(str(x).upper(), "→ 0.00 %"))
    conf = {"Ticker": st.column_config.TextColumn("Ticker"), "Type": st.column_config.SelectboxColumn("Type", options=["🛢️ Action", "📜 Obligation", "💰 Or", "₿ Crypto", "💵 Cash"]), "Court": st.column_config.TextColumn("Court 🔒", disabled=True), "Quantité": st.column_config.TextColumn("Quantité"), "Valeur totale": st.column_config.TextColumn("Valeur totale 🔒", disabled=True), "Pourcentage (%)": st.column_config.NumberColumn("Cible %", format="%.2f%%"), "Var 🔒": st.column_config.TextColumn("Var 🔒", disabled=True)}
    d_cols = ["Ticker", "Type", "Court", "Quantité", "Valeur totale", "Pourcentage (%)", "Var 🔒"]
    
    def cv(v): return 'color:#2ecc71' if "↗" in str(v) or "+" in str(v) else ('color:#e74c3c' if "↘" in str(v) or "-" in str(v) else 'color:#95a5a6')
    m_dev = df_a.apply(lambda r: est_devise_liquide(r.get("Ticker", "")), axis=1)
    r_i = st.data_editor(df_a[~m_dev][d_cols].style.map(cv, subset=["Var 🔒"]), key="ei", column_config=conf, use_container_width=True, hide_index=True, num_rows="dynamic")
    r_d = st.data_editor(df_a[m_dev][d_cols].style.map(cv, subset=["Var 🔒"]), key="ed", column_config=conf, use_container_width=True, hide_index=True, num_rows="dynamic")

    n_df = pd.concat([r_i, r_d], ignore_index=True)
    c_cols = ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]
    if not n_df[c_cols].equals(st.session_state.donnees[c_cols]):
        st.session_state.donnees = n_df[c_cols]
        recalculer_totaux_locaux()
        save_sheet("Donnees", st.session_state.donnees)
        st.rerun()

elif page == "⚖️ Rééquilibrage":
    st.title("⚖️ Rééquilibrage & Transactions")
    
    with st.expander("➕ Enregistrer une transaction"):
        with st.form("new_trans"):
            c1, c2, c3 = st.columns(3)
            t_d = c1.date_input("Date")
            t_t = c2.selectbox("Actif (Ticker)", sorted(st.session_state.donnees['Ticker'].unique().tolist()))
            t_ty = c3.selectbox("Type", ["Achat", "Vente"])
            c4, c5, c6 = st.columns(3)
            t_q = c4.number_input("Quantité", min_value=0.0, format="%.6f")
            t_c = c5.number_input("Cours", min_value=0.0, format="%.4f")
            t_f = c6.number_input("Frais", min_value=0.0, format="%.2f")
            t_dev = st.selectbox("Devise", ["USD", "EUR", "CHF", "JPY", "GBP"])
            if st.form_submit_button("Valider la transaction"):
                m_net = (t_q * t_c) + t_f if t_ty == "Achat" else (t_q * t_c) - t_f
                nr = {"Ticker": t_t, "Type": t_ty, "Date": t_d.strftime("%d/%m/%Y"), "Quantité": t_q, "Cours": t_c, "Frais": t_f, "Montant Net": m_net, "Devise": t_dev, "PRU (Devise)": 0, "Taux change (EUR)": 0}
                st.session_state.transactions = pd.concat([st.session_state.transactions, pd.DataFrame([nr])], ignore_index=True)
                save_sheet("Transaction", st.session_state.transactions.drop(columns=['Date_DT'], errors='ignore'))
                st.success("✅ Transaction enregistrée !")
                st.rerun()

    st.divider()
    app = st.number_input("Apport disponible ($)", value=float(st.session_state.config.get("apport_dispo", 0)), on_change=lambda: save_config_param("apport_dispo", st.session_state.app_i), key="app_i")
    df = st.session_state.donnees
    base = sum(extraire_nombre(r["Valeur totale"]) for _, r in df.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0) + app
    
    if base > 0:
        res = []
        for _, r in df.iterrows():
            cib = extraire_nombre(r["Pourcentage (%)"])/100
            if cib <= 0: continue
            act = extraire_nombre(r["Valeur totale"])
            d = (base * cib) - act
            qte = d / extraire_nombre(r["Court"]) if extraire_nombre(r["Court"]) > 0 else 0
            
            p_str = "N/A"
            df_t = st.session_state.transactions
            f_buy = df_t[(df_t['Ticker'] == r['Ticker']) & (df_t['Type'].str.lower().str.contains('achat'))].sort_values('Date_DT').head(1) if 'Date_DT' in df_t.columns else pd.DataFrame()
            if not f_buy.empty and f_buy.iloc[0]['Cours'] > 0:
                p_str = f"{(((extraire_nombre(r['Court']) / f_buy.iloc[0]['Cours']) - 1) * 100):+.2f} %"

            s = "+ " if qte > 0 else "- " if qte < 0 else ""
            q_fmt = f"({s}{abs(qte):.6f})" if "BTC" in str(r["Ticker"]).upper() else f"({s}{int(abs(qte))})"
            act_str = f"✅ ÉQUILIBRÉ (${abs(d):,.2f})" if abs(d) < 1000 or abs((act/base*100) - cib*100) < 2.0 else f"{'🟢 ACHETER' if d>0 else '🔴 VENDRE'} ${abs(d):,.2f}"
            res.append({"Ticker 🔒": str(r["Ticker"]).upper(), "Perf 🔒": p_str, "Actuel ($) 🔒": act, "Écart (%) 🔒": (act/base*100) - cib*100, "Action 🔒": act_str, "Qté 🔒": q_fmt})
        
        def cr(v): return 'color:#2ecc71' if "ACHETER" in str(v) or "+" in str(v) else ('color:#e74c3c' if "VENDRE" in str(v) or "-" in str(v) else 'color:#95a5a6')
        st.dataframe(pd.DataFrame(res).style.format({"Actuel ($) 🔒":"${:,.2f}", "Écart (%) 🔒":"{:+.2f}%"}).map(cr, subset=["Action 🔒", "Qté 🔒", "Perf 🔒"]), use_container_width=True, hide_index=True)

elif page == "💰 Fonds":
    st.title("💰 Fonds")
    with st.expander("➕ Nouveau mouvement"):
        with st.form("f_m"):
            d_m, t_m = st.date_input("Date"), st.radio("Type", ["Ajout de fond propre", "Retrait"], horizontal=True)
            m_s, d_s = st.number_input("Montant", min_value=0.00), st.selectbox("Devise", ["$", "€"])
            if st.form_submit_button("Valider"):
                m_usd = m_s if d_s == "$" else m_s * TAUX_EUR_USD
                or_px = float(yf.Ticker("GC=F").fast_info.get('lastPrice', 2000.0))
                st.session_state.historique = pd.concat([st.session_state.historique, pd.DataFrame([{"Date": d_m.strftime("%d/%m/%Y"), "Type": t_m, "Montant $": m_usd, "Montant €": m_usd/TAUX_EUR_USD, "Montant Or": m_usd/or_px}])], ignore_index=True)
                save_sheet("Historique", st.session_state.historique)
                if t_m == "Ajout de fond propre": save_config_param("apport_dispo", float(st.session_state.config.get("apport_dispo", 0)) + m_usd)
                st.rerun()
    afficher_montant_double("Total Apports", sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _, r in st.session_state.historique.iterrows()))
    st.dataframe(st.session_state.historique, use_container_width=True, hide_index=True)

elif page == "🏖️ Suivi":
    st.title("🏖️ Suivi Temporel")
    st.dataframe(st.session_state.projections.sort_index(ascending=False), use_container_width=True, hide_index=True)

elif page == "📈 Performance":
    st.title("📈 Performances Annuelles")
    df_p = st.session_state.projections
    if df_p.empty: st.info("Aucune donnée.")
    else:
        df_v = df_p.copy()
        df_v['DT'] = pd.to_datetime(df_v['Date'], dayfirst=True)
        df_y = df_v.groupby(df_v['DT'].dt.year).last().reset_index()
        df_y['Perf Brute'] = (( (1+df_y['Score TWR %']/100) / (1+df_y['Score TWR %'].shift(1).fillna(0)/100) ) - 1) * 100
        df_y = df_y.merge(st.session_state.inflation, left_on='DT', right_on='Année', how='left').fillna(0)
        df_y['Perf Nette'] = (((1+df_y['Perf Brute']/100) / (1+df_y['Inflation (%)']/100)) - 1) * 100
        df_y['Gains'] = df_y['Evolution cumulée $'] - df_y['Evolution cumulée $'].shift(1).fillna(0)
        st.dataframe(df_y[['DT', 'Perf Brute', 'Inflation (%)', 'Perf Nette', 'Gains']], use_container_width=True, hide_index=True)

elif page == "🌴 Retraite":
    st.title("🌴 Simulateur Retraite")
    c1, c2, c3 = st.columns(3)
    a = c1.number_input("Année retraite", 2025, 2100, 2055)
    app = c1.number_input("Apport mensuel ($)", value=float(st.session_state.config.get("retraite_apport_mensuel", 250)), key="in_r_app", on_change=lambda: save_config_param("retraite_apport_mensuel", st.session_state.in_r_app))
    r_a = c2.number_input("Rendement A (%)", value=8.0)
    r_b = c2.number_input("Rendement B (%)", value=10.0)
    inf = c3.number_input("Inflation (%)", value=2.0)
    tax = c3.number_input("Taxe (%)", value=float(st.session_state.config.get("retraite_taxe", 30)), key="in_r_tax", on_change=lambda: save_config_param("retraite_taxe", st.session_state.in_r_tax))
    st.info("Simulation basée sur la capitalisation des apports et rendements saisis.")

elif page == "🏛️ Fiscalité":
    st.title("🏛️ Simulateur Fiscal (Lecture Drive)")
    
    df_t = st.session_state.transactions
    years = sorted(pd.to_datetime(df_t['Date'], dayfirst=True, errors='coerce').dropna().dt.year.unique().tolist(), reverse=True)
    annee = st.selectbox("Année fiscale :", years if years else [datetime.datetime.now().year])
    st.divider()

    st.subheader("👤 1. Ma Situation")
    c1, c2 = st.columns(2)
    st_mat = c1.radio("Statut", ["Célibataire", "Marié(e) / Pacsé(e)"], index=0 if st.session_state.config.get("f_statut","") == "Célibataire" else 1, key="in_statut", on_change=lambda: save_config_param("f_statut", st.session_state.in_statut))
    enf = c1.number_input("Enfants", 0, 15, int(st.session_state.config.get("f_enf",0)), key="in_enf", on_change=lambda: save_config_param("f_enf", st.session_state.in_enf))
    s1 = c2.number_input("Salaire 1 (€)", 0, 500000, int(float(st.session_state.config.get("f_s1",30000))), key="in_s1", on_change=lambda: save_config_param("f_s1", st.session_state.in_s1))
    s2 = c2.number_input("Salaire 2 (€)", 0, 500000, int(float(st.session_state.config.get("f_s2",0))), key="in_s2", on_change=lambda: save_config_param("f_s2", st.session_state.in_s2)) if "Marié" in st_mat else 0.0

    st.markdown("---")
    cf1, cf2 = st.columns(2)
    with cf1:
        u1 = st.checkbox("Frais réels 1", value=bool(int(st.session_state.config.get("f_u1",0))), key="in_u1", on_change=lambda: save_config_param("f_u1", int(st.session_state.in_u1)))
        k1, cv1, r1 = 0, 5, 0
        if u1:
            k1 = st.number_input("KM 1", 0, 60000, int(st.session_state.config.get("f_k1",0)), key="in_k1", on_change=lambda: save_config_param("f_k1", st.session_state.in_k1))
            cv1 = st.selectbox("CV 1", [3,4,5,6,7], index=int(st.session_state.config.get("f_cv1",5))-3, key="in_cv1", on_change=lambda: save_config_param("f_cv1", st.session_state.in_cv1))
            r1 = st.number_input("Repas 1", 0, 250, int(st.session_state.config.get("f_r1",0)), key="in_r1", on_change=lambda: save_config_param("f_r1", st.session_state.in_r1))
            st.info(f"Frais estimés : {calcul_frais_km(k1, cv1) + r1*5.35:,.2f} €")
    with cf2:
        u2 = st.checkbox("Frais réels 2", value=bool(int(st.session_state.config.get("f_u2",0))), key="in_u2", on_change=lambda: save_config_param("f_u2", int(st.session_state.in_u2)))
        k2, cv2, r2 = 0, 5, 0
        if u2 and "Marié" in st_mat:
            k2 = st.number_input("KM 2", 0, 60000, int(st.session_state.config.get("f_k2",0)), key="in_k2", on_change=lambda: save_config_param("f_k2", st.session_state.in_k2))
            cv2 = st.selectbox("CV 2", [3,4,5,6,7], index=int(st.session_state.config.get("f_cv2",5))-3, key="in_cv2", on_change=lambda: save_config_param("f_cv2", st.session_state.in_cv2))
            r2 = st.number_input("Repas 2", 0, 250, int(st.session_state.config.get("f_r2",0)), key="in_r2", on_change=lambda: save_config_param("f_r2", st.session_state.in_r2))
            st.info(f"Frais estimés : {calcul_frais_km(k2, cv2) + r2*5.35:,.2f} €")
    st.divider()

    df_v = df_t.copy()
    if 'Date_DT' not in df_v.columns: df_v['Date_DT'] = pd.to_datetime(df_v['Date'], dayfirst=True, errors='coerce')
    df_v = df_v[(df_v['Type'].str.lower().str.contains('vente')) & (df_v['Date_DT'].dt.year == annee)].copy()
    
    pv_a = pv_c = 0.0
    if df_v.empty: st.info(f"Aucune vente en {annee}.")
    else:
        res_v = []
        for _, row in df_v.iterrows():
            t = str(row['Ticker']).upper()
            if est_devise_liquide(t): continue
            qte, net, pru, fx = row['Quantité'], row['Montant Net'], row['PRU (Devise)'], row['Taux change (EUR)']
            pv_eur = (net - (pru * qte)) * fx
            cat = "Crypto" if any(c in t for c in ["BTC","ETH","USDT"]) else "Action"
            if cat == "Action": pv_a += pv_eur
            else: pv_c += pv_eur
            res_v.append({"Actif": t, "Date": row['Date'], "Qté": qte, "PRU": pru, "Net": net, "FX": fx, "PV €": pv_eur, "Cat": cat})
        
        df_res = pd.DataFrame(res_v)
        if not df_res.empty:
            st.subheader("📝 2. Détail des Ventes")
            tabs = st.tabs(sorted(df_res['Actif'].unique()))
            for i, act in enumerate(sorted(df_res['Actif'].unique())):
                with tabs[i]:
                    d_act = df_res[df_res['Actif'] == act].drop(columns=['Actif', 'Cat'])
                    st.dataframe(d_act.style.format({"PRU":"{:.2f}","Net":"{:.2f}","FX":"{:.4f}","PV €":"{:.2f} €"}), hide_index=True, use_container_width=True)
                    st.markdown(f"**Bilan {act} : {d_act['PV €'].sum():+.2f} €**")
    st.divider()

    fr1 = max(s1 * 0.10, calcul_frais_km(k1, cv1) + (r1 * 5.35 if k1>0 else 0))
    fr2 = max(s2 * 0.10, calcul_frais_km(k2, cv2) + (r2 * 5.35 if k2>0 else 0))
    rn = (s1 - fr1) + (s2 - fr2)
    p = (1 if "Cél" in st_mat else 2) + (0.5 if enf <= 2 else 0) * enf + (1.0 if enf >= 3 else 0)
    
    imp_s = calcul_impot_ir(rn, p, st_mat)
    
    st.subheader("💡 3. Bilan & Prélèvement")
    if pv_a == 0: choix, imp_b = "Aucun", 0.0
    elif pv_a <= 0: choix, imp_b = "Aucun (Bilan négatif)", 0.0
    else:
        pfu = pv_a * 0.30
        bar = (calcul_impot_ir(rn + pv_a, p, st_mat) - imp_s) + (pv_a * 0.172)
        if bar < pfu: choix, imp_b = "Barème", bar
        else: choix, imp_b = "PFU", pfu
        st.success(f"✅ Option fiscale actions : **{choix}** (Coût : {imp_b:,.2f} €)")

    t_f = (imp_s / (s1 + s2) * 100) if (s1+s2)>0 else 0.0
    t_p1 = (calcul_impot_ir(s1 - fr1, 1.0, "Célibataire", False) / s1 * 100) if s1 > 0 else 0.0
    
    st.write(f"Impôt salaires foyer : **{imp_s:,.2f} € / an**")
    cr1, cr2 = st.columns(2)
    cr1.info(f"**Taux Commun : {t_f:.1f} %**")
    if "Marié" in st_mat: cr2.success(f"**Ton Taux Perso : {t_p1:.1f} %** (Prélèvement ~ {(s1*t_p1/100)/12:,.2f} €/mois)")
    if imp_b > 0: st.warning(f"Reliquat Bourse à payer en sept. : **{imp_b:,.2f} €**")
    
    st.divider()
    st.subheader("📝 4. Résumé Déclaration")
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
        if pv_a > 0:
            st.markdown(f"- Case 3VG : {pv_a:,.0f} €")
            st.markdown(f"- Case 2OP : **{'COCHER' if choix=='Barème' else 'NE PAS COCHER'}**")
        elif pv_a < 0: st.markdown(f"- Case 3VH : {abs(pv_a):,.0f} €")
