import streamlit as st, pandas as pd, yfinance as yf, re, datetime, time, plotly.express as px, gspread, urllib.request, json
from streamlit_autorefresh import st_autorefresh
from gspread_dataframe import set_with_dataframe, get_as_dataframe
from google.oauth2.service_account import Credentials

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
except Exception as e: st.error("Erreur de connexion à Google Sheets."); st.stop()

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
    ws.clear(); set_with_dataframe(ws, df, include_index=False)

try: TAUX_EUR_USD = float(yf.Ticker("EURUSD=X").history(period="1d")['Close'].iloc[-1])
except: TAUX_EUR_USD = 1.0

# --- 4. FONCTIONS OUTILS ---
def extraire_nombre(valeur):
    if pd.isna(valeur) or str(valeur).strip() == "" or str(valeur).lower() == "nan": return 0.0
    nettoye = re.sub(r'[^\d,.-]', '', str(valeur))
    if ',' in nettoye and '.' in nettoye: nettoye = nettoye.replace(',', '')
    elif ',' in nettoye: nettoye = nettoye.replace(',', '.')
    try: return round(float(nettoye), 5)
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
    else:
        for idx, row in df.iterrows():
            t = str(row.get("Type", "")).upper()
            if "ACTION" in t: df.at[idx, "Type"] = "🛢️ Action"
            elif "OBLIGATION" in t: df.at[idx, "Type"] = "📜 Obligation"
            elif "OR" in t: df.at[idx, "Type"] = "💰 Or"
            elif "CRYPTO" in t: df.at[idx, "Type"] = "₿ Crypto"
            elif "RÉSERVE" in t or "RESERVE" in t: df.at[idx, "Type"] = "🏦 Cash réserve"
            elif "CASH" in t: df.at[idx, "Type"] = "💵 Cash"
    for col in cols_finales:
        if col not in df.columns: df[col] = 0.0 if col in ["Quantité", "Pourcentage (%)"] else ("$ 0.00" if col in ["Court", "Valeur totale"] else "")
    df["Quantité"] = df["Quantité"].apply(extraire_nombre)
    df["Pourcentage (%)"] = df["Pourcentage (%)"].apply(extraire_nombre)
    return df[cols_finales].reset_index(drop=True)

def get_pru_and_qty(ticker, df_t):
    # CHERCHE LE PRU LE PLUS RÉCENT DANS TRANSACTION
    df_k = df_t[df_t['Ticker'] == ticker].copy()
    if df_k.empty: return 0.0, 0.0
    df_k['Date_DT'] = pd.to_datetime(df_k['Date'], dayfirst=True, errors='coerce')
    df_k = df_k.dropna(subset=['Date_DT']).sort_values('Date_DT', ascending=False)
    # On prend la toute dernière ligne
    latest_row = df_k.iloc[0]
    pru_recu = extraire_nombre(latest_row.get('PRU (Devise)', 0.0))
    # Quantité cumulée réelle
    q_tot = 0.0
    for _, r in df_k.sort_values('Date_DT').iterrows():
        q_tot += extraire_nombre(r['Quantité']) if str(r['Type']).lower() == "achat" else -extraire_nombre(r['Quantité'])
    return round(pru_recu, 5), round(max(0, q_tot), 5)

def recalculer_toute_la_base_projections(df):
    if df is None or df.empty: return df
    df_t = df.copy(); c_base = ["Date", "Capital investi", "Actifs Stratégiques", "Total Global"]
    for i, nom in enumerate(c_base):
        if i < len(df_t.columns): df_t.rename(columns={df_t.columns[i]: nom}, inplace=True)
    for col in ["Capital investi", "Actifs Stratégiques", "Total Global"]: df_t[col] = df_t[col].apply(extraire_nombre)
    df_t['DT_TRI'] = pd.to_datetime(df_t['Date'], dayfirst=True, errors='coerce')
    df_t = df_t.sort_values('DT_TRI').reset_index(drop=True)
    res, c_twr, tg_twr = [], 1.0, 1.0
    for i in range(len(df_t)):
        r = df_t.iloc[i].to_dict(); cap, act, tg = r["Capital investi"], r["Actifs Stratégiques"], r["Total Global"]
        if i == 0:
            r["Evolution actifs $"] = r["Evolution actifs %"] = 0.0
            r["Evolution cumulée $"], r["Evolution cumulée %"] = act - cap, ((act - cap) / cap * 100) if cap != 0 else 0.0
            r["TG_Evolution cumulée $"], r["TG_Evolution cumulée %"] = tg - cap, ((tg - cap) / cap * 100) if cap != 0 else 0.0
            c_twr *= (1 + ((act - cap) / cap if cap != 0 else 0.0)); tg_twr *= (1 + ((tg - cap) / cap if cap != 0 else 0.0))
        else:
            prev = df_t.iloc[i-1]; d_cap = cap - prev["Capital investi"]; evo_usd = (act - prev["Actifs Stratégiques"]) - d_cap
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
    v_j_tg = val_tot_v = v_j_st = val_inv_v = 0.0
    for _, r in df_actuel.iterrows():
        tick = str(r.get("Ticker", "")).strip().upper(); v_act = extraire_nombre(r["Valeur totale"])
        match = re.search(r'([+-]?\d+\.?\d*)', variations.get(tick, "0")); v_pct = float(match.group(1)) if match else 0.0
        v_veil = v_act / (1 + v_pct / 100) if (1 + v_pct / 100) != 0 else v_act
        v_j_tg += (v_act - v_veil); val_tot_v += v_veil
        if extraire_nombre(r["Pourcentage (%)"]) > 0: v_j_st += (v_act - v_veil); val_inv_v += v_veil
    return val_invest, val_total, somme_p, v_j_tg, (v_j_tg/val_tot_v*100 if val_tot_v>0 else 0.0), v_j_st, (v_j_st/val_inv_v*100 if val_inv_v>0 else 0.0)

def actualiser_cours_internet(silencieux=False):
    if "donnees" in st.session_state:
        if not silencieux: st.toast("🔄 Actualisation des cours boursiers...")
        df_tmp = st.session_state.donnees.copy(); changement, taux_cache = False, {} 
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
                                p_usd, p_prev = float(data[1][4]), float(data[0][4])
                                var = ((p_usd - p_prev) / p_prev) * 100 if p_prev > 0 else 0.0
                                st.session_state.variations[tick] = f"{'↗' if var > 0 else '↘' if var < 0 else '→'} {var:+.2f} %"
                                df_tmp.at[idx, "Court"] = f"$ {p_usd:.2f}"; changement = succ_bin = True; break 
                        except: continue 
                if succ_bin: continue 
                try:
                    asset = yf.Ticker(tick.replace("USDT", "-USD"))
                    try: p_loc = float(asset.fast_info.get('lastPrice', 0.0))
                    except: p_loc = float(asset.history(period="1d")['Close'].iloc[-1]) if not asset.history(period="1d").empty else 0.0
                    try:
                        p_prev = float(asset.fast_info.get('previous_close', 0.0))
                        if p_prev <= 0.0:
                            h = asset.history(period="5d"); p_prev = float(h['Close'].iloc[-2]) if len(h)>=2 else 0.0
                        if p_prev > 0.0 and p_loc > 0.0:
                            var = ((p_loc - p_prev) / p_prev) * 100
                            st.session_state.variations[tick] = f"{'↗' if var > 0 else '↘' if var < 0 else '→'} {var:+.2f} %"
                        elif tick not in st.session_state.variations: st.session_state.variations[tick] = "→ 0.00 %"
                    except: 
                        if tick not in st.session_state.variations: st.session_state.variations[tick] = "→ 0.00 %"
                    if p_loc > 0:
                        dev = str(asset.fast_info.get('currency', 'USD')).strip().upper(); f_dev = 0.01 if dev == "GBP" else 1.0; p_usd = p_loc * f_dev
                        if dev != "USD" and dev not in ["", "NONE"]:
                            if dev not in taux_cache:
                                try: tx = float(yf.Ticker(f"{dev}USD=X").fast_info.get('lastPrice', 0.0))
                                except: tx = 0.0
                                if tx <= 0.0:
                                    try: tx = 1.0 / float(yf.Ticker(f"{dev}=X").fast_info.get('lastPrice', 0.0))
                                    except: pass
                                taux_cache[dev] = tx if tx > 0 else 1.0
                            p_usd *= taux_cache[dev]
                        df_tmp.at[idx, "Court"] = f"$ {p_usd:.2f}"; changement = True
                except: pass
        if changement:
            st.session_state.donnees = df_tmp; recalculer_totaux_locaux(); save_sheet("Donnees", st.session_state.donnees)

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
    d_clean = str(devise).upper().strip(); t = f"{d_clean}EUR=X"
    if d_clean in ["EUR", ""]: return 1.0
    try:
        d = pd.to_datetime(date_val, dayfirst=True, errors='coerce')
        if pd.isna(d): return 1.0
        if d >= pd.Timestamp.now() - pd.Timedelta(days=1):
            h = yf.Ticker(t).history(period="1d"); return float(h['Close'].iloc[-1]) if not h.empty else 1.0
        h = yf.Ticker(t).history(start=(d - pd.Timedelta(days=5)).strftime('%Y-%m-%d'), end=(d + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        if not h.empty: return float(h['Close'].iloc[-1])
        return 1.0
    except: return 1.0

def calcul_frais_km(km, cv):
    coefs = {3:(0.529, 0.316, 1065, 0.370), 4:(0.606, 0.340, 1330, 0.407), 5:(0.636, 0.357, 1395, 0.427), 6:(0.665, 0.374, 1457, 0.447), 7:(0.697, 0.394, 1515, 0.470)}
    c = coefs.get(cv, coefs[7]); return km * c[0] if km <= 5000 else (km * c[1] + c[2] if km <= 20000 else km * c[3])

def calcul_impot_ir(rev, parts, stat, apply_decote=True):
    qf, imp = rev / parts, 0; tr = [(28797, 11294, 0.11), (82341, 28797, 0.30), (177106, 82341, 0.41), (9999999, 177106, 0.45)]
    for lim, prev, tx in tr:
        if qf > prev: imp += (min(qf, lim) - prev) * tx
    imp *= parts
    if apply_decote:
        l_d, b_d = (2002, 906) if "Cél" in stat else (3300, 1493)
        if imp <= l_d: imp = max(0, imp - (b_d - (imp * 0.4525)))
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
    for c in ["Montant $", "Montant €", "Montant Or"]: df_h[c] = df_h[c].apply(extraire_nombre)
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
    for c in ["Quantité", "Cours", "Frais", "Montant Net", "PRU (Devise)", "Taux change (EUR)"]: df_t[c] = df_t[c].apply(extraire_nombre)
    st.session_state.transactions = df_t

if "inf_chk" not in st.session_state:
    st.session_state.inf_chk = True; d_inf = recuperer_inflation_france()
    if d_inf and not st.session_state.projections.empty:
        df_p_tmp = st.session_state.projections.copy(); df_p_tmp['Date_DT'] = pd.to_datetime(df_p_tmp['Date'], dayfirst=True, errors='coerce')
        ans, n_inf, chg = df_p_tmp.dropna(subset=['Date_DT'])['Date_DT'].dt.year.unique(), [], False
        for a in ans:
            v_off = d_inf.get(a, 0.0); v_act = st.session_state.inflation[st.session_state.inflation['Année'] == a].iloc[0]['Inflation (%)'] if not st.session_state.inflation[st.session_state.inflation['Année'] == a].empty else 0.0
            if v_off != v_act: chg = True
            n_inf.append({'Année': a, 'Inflation (%)': v_off})
        if chg: st.session_state.inflation = pd.DataFrame(n_inf); save_sheet("Inflation", st.session_state.inflation)

if "dernier_ref" not in st.session_state: st.session_state.dernier_ref = 0
if time.time() - st.session_state.dernier_ref >= 900: actualiser_cours_internet(st.session_state.dernier_ref == 0); st.session_state.dernier_ref = time.time()

# --- 6. NAVIGATION ---
st.sidebar.title("Menu")
page = st.sidebar.radio("Aller vers :", ["📊 Tableau de bord", "📋 Liste des actifs", "⚖️ Rééquilibrage", "💰 Fonds", "🏖️ Suivi", "📈 Performance", "🌴 Retraite", "🏛️ Fiscalité"])
st.sidebar.divider()
if st.sidebar.button("🔄 Recharger l'application", use_container_width=True): st.session_state.clear(); st.rerun()

# --- 7. PAGES ---
if page == "📊 Tableau de bord":
    st.title("📊 Vue d'ensemble de mon Patrimoine")
    da, dp = st.session_state.donnees, st.session_state.projections
    v_inv, v_tot, somme_p, v_j_tg, p_j_tg, v_j_st, p_j_st = calculer_metriques_jour(da, st.session_state.variations)
    cap = sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _, r in st.session_state.historique.iterrows())
    dpl = pd.concat([dp, pd.DataFrame([{"Date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "Capital investi": cap, "Actifs Stratégiques": v_inv, "Total Global": v_tot}])], ignore_index=True)
    dpl = recalculer_toute_la_base_projections(dpl)
    delta = p_delta = delta_tg = p_delta_tg = 0.0
    if not dp.empty:
        dpd = dp.copy(); dpd['DT'] = pd.to_datetime(dpd['Date'], dayfirst=True, errors='coerce'); d_p = dpd.dropna(subset=['DT']).sort_values('DT')
        if not d_p.empty:
            rp = d_p[d_p['DT'] <= pd.Timestamp.now() - pd.DateOffset(years=1)].iloc[-1] if not d_p[d_p['DT'] <= pd.Timestamp.now() - pd.DateOffset(years=1)].empty else d_p.iloc[0]
            delta, delta_tg = v_inv - extraire_nombre(rp["Actifs Stratégiques"]), v_tot - extraire_nombre(rp["Total Global"])
            if extraire_nombre(rp["Actifs Stratégiques"]) > 0: p_delta = (delta / extraire_nombre(rp["Actifs Stratégiques"])) * 100
            if extraire_nombre(rp["Total Global"]) > 0: p_delta_tg = (delta_tg / extraire_nombre(rp["Total Global"])) * 100
    breq = val_inv > 0 and any(abs((val_inv * (extraire_nombre(r["Pourcentage (%)"])/100)) - extraire_nombre(r["Valeur totale"])) >= 1000 and abs((extraire_nombre(r["Valeur totale"])/val_inv*100) - extraire_nombre(r["Pourcentage (%)"])) >= 2.0 for _, r in da.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    c1, c2 = st.columns([1, 2])
    with c1: 
        if st.button("🔄 Actualiser les cours"): actualiser_cours_internet(False); st.rerun()
    with c2: st.warning("⚠️ Rééquilibrage nécessaire") if breq else st.success("✅ Équilibré")
    st.divider(); st.subheader("🌍 Total Global"); ctg, _ = st.columns(2)
    with ctg:
        afficher_montant_double("Total Global", v_tot, f"{delta_tg:+,.2f} $ ({p_delta_tg:+.2f} % sur 1 an glissant)")
        st.markdown(f"<span>{'📈' if v_j_tg>=0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if v_j_tg>=0 else '#e74c3c'}'>{v_j_tg:+,.2f} $ ({p_j_tg:+.2f} %)</strong></span>", unsafe_allow_html=True)
    if not dp.empty:
        dv = dpl.copy(); dv['DT'] = pd.to_datetime(dv['Date'], dayfirst=True, errors='coerce'); dv = dv.dropna(subset=['DT']).sort_values('DT')
        fg = st.radio("Période globale :", ["Depuis le début", "Depuis 1 an", "YTD"], horizontal=True, key="fg")
        mg = st.radio("Affichage :", ["ROI", "TWR"], horizontal=True, key="mg")
        n = pd.Timestamp.now()
        if fg == "Depuis 1 an": dv = dv[dv['DT'] >= (n - pd.DateOffset(years=1))]
        elif fg == "YTD": dv = dv[dv['DT'] >= pd.Timestamp(year=n.year - 1, month=12, day=31)]
        if not dv.empty:
            dv.set_index('DT', inplace=True); d_usd = dv['TG_Evolution cumulée $'].iloc[-1] - dv['TG_Evolution cumulée $'].iloc[0]
            cg1, cg2 = st.columns([1, 3])
            with cg1:
                if mg == "ROI": afficher_montant_double("Gains nets", dv['TG_Evolution cumulée $'].iloc[-1], taille="medium")
                else: st.metric("Score TWR (%)", f"{dv['TG_Score TWR %'].iloc[-1]:+.2f} %")
            with cg2: st.plotly_chart(px.line(dv.reset_index(), x='DT', y='TG_Evolution cumulée $' if mg == "ROI" else 'TG_Score TWR %').update_layout(xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0)), use_container_width=True)
            dpie = da.copy(); dpie['V'] = dpie['Valeur totale'].apply(extraire_nombre)
            if not dpie[dpie['V']>0].empty: st.plotly_chart(px.pie(dpie[dpie['V']>0], values='V', names='Type', color='Type', color_discrete_map={"🛢️ Action": "#e74c3c", "📜 Obligation": "#3498db", "💰 Or": "#f1c40f", "₿ Crypto": "#9b59b6", "💵 Cash": "#2ecc71", "🏦 Cash réserve": "#f39c12"}, hole=0.4), use_container_width=True)
    st.divider(); st.subheader("🎯 Actifs Stratégiques"); cst, _ = st.columns(2)
    with cst:
        afficher_montant_double("Actifs Stratégiques", val_inv, f"{delta:+,.2f} $ ({p_delta:+.2f} % sur 1 an glissant)")
        st.markdown(f"<span>{'📈' if v_j_st>=0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if v_j_st>=0 else '#e74c3c'}'>{v_j_st:+,.2f} $ ({p_j_st:+.2f} %)</strong></span>", unsafe_allow_html=True)
    st.divider(); st.subheader("🏖️ Liberté Financière"); cr1, cr2 = st.columns(2)
    with cr1: inf_d = st.slider("Inflation cible à déduire (%)", 0.0, 15.0, 2.0, 0.1, key="di")
    with cr2: tx_r = ((1 + 0.08) / (1 + (inf_d / 100.0))) - 1; afficher_montant_double("Rente Mensuelle Nette (Base 8%)", (val_inv * max(0.0, tx_r)) / 12.0, couleur_valeur="#3498db")

elif page == "📋 Liste des actifs":
    st.title("📋 Liste de mes actifs"); da = st.session_state.donnees.copy()
    val_inv, val_tot, somme_p, v_j_tg, p_j_tg, v_j_st, p_j_st = calculer_metriques_jour(da, st.session_state.variations)
    c1, c2, c3 = st.columns(3)
    with c1: afficher_montant_double("Actifs Stratégiques", val_inv)
    with c2: afficher_montant_double("Total Global", val_tot)
    with c3: 
        ec = round(100 - somme_p, 2); st.markdown(f"<div>Répartition Cible<br><b>{somme_p:.2f} %</b><br><span style='color:{'#2ecc71' if ec==0 else '#e74c3c'}'>{'✅ OK' if ec==0 else f'⚠️ {abs(ec):.2f} % écart'}</span></div>", unsafe_allow_html=True)
    st.divider(); 
    if st.button("🔄 Actualiser les cours"): actualiser_cours_internet(False); st.rerun()
    da['Var.'] = da['Ticker'].apply(lambda x: st.session_state.variations.get(str(x).upper(), "→ 0.00 %"))
    conf_l = {"Ticker": "Ticker", "Type": st.column_config.SelectboxColumn("Type", options=["🛢️ Action", "📜 Obligation", "💰 Or", "₿ Crypto", "💵 Cash", "🏦 Cash réserve"]), "Court": "Court 🔒", "Quantité": st.column_config.NumberColumn("Quantité 🔒", disabled=True, format="%.5f"), "Valeur totale": "Valeur totale 🔒", "Pourcentage (%)": "Cible %", "Var.": "Var. 🔒"}
    conf_u = conf_l.copy(); conf_u["Quantité"] = st.column_config.NumberColumn("Quantité ✍️", format="%.5f")
    def cv(v): return 'color:#2ecc71' if "↗" in str(v) or "+" in str(v) else ('color:#e74c3c' if "↘" in str(v) or "-" in str(v) else 'color:#95a5a6')
    m_dev = da.apply(lambda r: est_devise_liquide(r.get("Ticker", "")), axis=1)
    st.markdown("### 📈 Investissements")
    ri = st.data_editor(da[~m_dev].style.map(cv, subset=["Var."]), key="ei", column_config=conf_l, use_container_width=True, hide_index=True)
    st.markdown("### 💵 Liquidités (Modifiables)")
    rd = st.data_editor(da[m_dev].style.map(cv, subset=["Var."]), key="ed", column_config=conf_u, use_container_width=True, hide_index=True)
    n_df = pd.concat([ri, rd], ignore_index=True)
    if not n_df[["Ticker", "Type", "Quantité", "Pourcentage (%)"]].equals(st.session_state.donnees[["Ticker", "Type", "Quantité", "Pourcentage (%)"]]):
        st.session_state.donnees = n_df; recalculer_totaux_locaux(); save_sheet("Donnees", st.session_state.donnees); st.rerun()

elif page == "⚖️ Rééquilibrage":
    st.title("⚖️ Rééquilibrage & Transactions")
    with st.expander("➕ Enregistrer Transaction", expanded=True):
        with st.form("nt"):
            c1, c2, c3 = st.columns(3); td = c1.date_input("Date"); lt = sorted(st.session_state.donnees['Ticker'].dropna().unique().tolist()); lt.insert(0, "➕ Nouvel actif")
            ts = c2.selectbox("Actif", lt); tt = c2.text_input("Ticker") if ts == "➕ Nouvel actif" else ts; ty = c3.selectbox("Type", ["Achat", "Vente"])
            c4, c5, c6 = st.columns(3); tq = c4.number_input("Quantité", min_value=0.0, format="%.5f"); tc = c5.number_input("Cours", min_value=0.0, format="%.5f"); tf = c6.number_input("Frais", min_value=0.0, format="%.2f")
            dv = st.selectbox("Devise", ["USD", "EUR", "CHF", "JPY", "GBP"])
            if st.form_submit_button("Valider"):
                if not tt or tq<=0 or tc<=0: st.error("❌ Formulaire incomplet.")
                else:
                    tc_l = tt.upper().strip(); mn = round((tq * tc) + tf if ty == "Achat" else (tq * tc) - tf, 5); fx = get_historical_fx(dv, td.strftime("%Y-%m-%d"))
                    cpru, cqty = get_pru_and_qty(tc_l, st.session_state.transactions)
                    npru = round(((cpru * cqty) + mn) / (cqty + tq), 5) if ty == "Achat" and (cqty + tq) > 0 else cpru
                    st.session_state.transactions = pd.concat([st.session_state.transactions, pd.DataFrame([{"Ticker":tc_l,"Type":ty,"Date":td.strftime("%d/%m/%Y"),"Quantité":tq,"Cours":tc,"Frais":tf,"Montant Net":mn,"Devise":dv,"PRU (Devise)":npru,"Taux change (EUR)":fx}])], ignore_index=True)
                    save_sheet("Transaction", st.session_state.transactions[[c for c in st.session_state.transactions.columns if c != 'Date_DT']])
                    dfd = st.session_state.donnees.copy()
                    if tc_l not in dfd['Ticker'].values: dfd = pd.concat([dfd, pd.DataFrame([{"Ticker":tc_l,"Type":"🛢️ Action","Quantité":0.0,"Pourcentage (%)":0.0}])], ignore_index=True)
                    ia = dfd.index[dfd['Ticker'] == tc_l][0]; dfd.at[ia,"Quantité"] = max(0.0, extraire_nombre(dfd.at[ia, "Quantité"]) + (tq if ty == "Achat" else -tq))
                    ic = dfd.index[dfd['Ticker'] == dv].tolist()
                    if ic: dfd.at[ic[0], "Quantité"] = extraire_nombre(dfd.at[ic[0], "Quantité"]) + (-mn if ty == "Achat" else mn)
                    st.session_state.donnees = nettoyer_dataframe(dfd); recalculer_totaux_locaux(); save_sheet("Donnees", st.session_state.donnees); st.success("✅ Fait !"); time.sleep(1); st.rerun()
    st.divider(); 
    if st.button("🔄 Actualiser les cours"): actualiser_cours_internet(False); st.rerun()
    df = st.session_state.donnees; cash = sum(extraire_nombre(r["Valeur totale"]) for _, r in df[df["Type"] == "💵 Cash"].iterrows())
    base = sum(extraire_nombre(r["Valeur totale"]) for _, r in df.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0) + cash
    if base > 0:
        st.info(f"💡 Cash disponible (💵 Cash uniquement) : **{cash:,.2f} $**"); r_l = []
        for _, r in df.iterrows():
            tc = str(r["Ticker"]).upper(); cb = extraire_nombre(r["Pourcentage (%)"])/100; if cb <= 0: continue
            ac, pr = extraire_nombre(r["Valeur totale"]), extraire_nombre(r["Court"]); d = (base * cb) - ac; q = d / pr if pr > 0 else 0
            pru, _ = get_pru_and_qty(tc, st.session_state.transactions); pf = f"{((pr/pru-1)*100):+.2f} %" if pru>0 else "N/A"
            act_s = f"✅ OK" if abs(d) < 1000 or abs(ac/base*100-cb*100)<2 else f"{'🟢 ACHETER' if d>0 else '🔴 VENDRE'} ${abs(d):,.2f}"
            r_l.append({"Ticker": tc, "PRU 🔒": pru, "Var.": st.session_state.variations.get(tc, "→ 0.00 %"), "Perf. Globale 🔒": pf, "Actuel": ac, "Action": act_s, "Qte": f"({'+' if q>0 else '-'}{abs(q):.5f})"})
        def cr(v): return 'color:#2ecc71' if "ACHETER" in str(v) or "+" in str(v) else ('color:#e74c3c' if "VENDRE" in str(v) or "-" in str(v) else 'color:#95a5a6')
        st.dataframe(pd.DataFrame(r_l).style.format({"PRU 🔒":"{:.5f}","Actuel":"${:,.2f}"}).map(cr, subset=["Var.", "Action", "Qte", "Perf. Globale 🔒"]), use_container_width=True, hide_index=True)

elif page == "💰 Fonds":
    st.title("💰 Fonds"); dfh = st.session_state.historique
    with st.expander("➕ Nouveau mouvement"):
        with st.form("fm"):
            dm, tm = st.date_input("Date"), st.radio("Type", ["Ajout de fond propre", "Retrait"], horizontal=True)
            ms, ds = st.number_input("Montant", min_value=0.0), st.selectbox("Devise", ["$", "€"])
            if st.form_submit_button("Valider"):
                mu, me = (ms, ms/TAUX_EUR_USD) if ds=="$" else (ms*TAUX_EUR_USD, ms); opx = float(yf.Ticker("GC=F").fast_info.get('lastPrice', 2000.0))
                st.session_state.historique = pd.concat([dfh, pd.DataFrame([{"Date":dm.strftime("%d/%m/%Y"), "Type":tm, "Montant $":mu, "Montant €":me, "Montant Or":mu/opx}])], ignore_index=True)
                save_sheet("Historique", st.session_state.historique)
                tk = "USD" if ds=="$" else "EUR"; dfd = st.session_state.donnees.copy(); ic = dfd.index[dfd['Ticker'] == tk].tolist()
                if ic: dfd.at[ic[0], "Quantité"] = extraire_nombre(dfd.at[ic[0], "Quantité"]) + (ms if tm=="Ajout de fond propre" else -ms)
                st.session_state.donnees = nettoyer_dataframe(dfd); recalculer_totaux_locaux(); save_sheet("Donnees", st.session_state.donnees); st.rerun()
    afficher_montant_double("Total Apports nets", sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _, r in dfh.iterrows()))
    st.dataframe(dfh.sort_values('Date', ascending=False), use_container_width=True, hide_index=True)

elif page == "🏖️ Suivi":
    st.title("🏖️ Suivi & Évolution"); st.dataframe(st.session_state.projections.sort_index(ascending=False), use_container_width=True, hide_index=True)

elif page == "📈 Performance":
    st.title("📈 Performances Annuelles & Inflation"); dp = st.session_state.projections
    if dp.empty: st.info("Aucune donnée.")
    else:
        dv = dp.copy(); dv['DT'] = pd.to_datetime(dv['Date'], dayfirst=True, errors='coerce'); dy = dv.dropna(subset=['DT']).sort_values('DT').groupby(dv['DT'].dt.year).last().reset_index()
        dy['Perf brute (%)'] = (((1+dy['Score TWR %']/100) / (1+dy['Score TWR %'].shift(1).fillna(0)/100)) - 1) * 100
        dy = dy.merge(st.session_state.inflation, left_on=dy['DT'].dt.year, right_on='Année', how='left').fillna({'Inflation (%)': 0.0})
        dy['Perf nette (%)'] = (((1 + dy['Perf brute (%)']/100) / (1 + dy['Inflation (%)']/100)) - 1) * 100
        st.dataframe(dy[['DT', 'Perf brute (%)', 'Inflation (%)', 'Perf nette (%)', 'Total Global']], use_container_width=True, hide_index=True)

elif page == "🌴 Retraite":
    st.title("🌴 Simulateur Retraite"); st.write(f"Projection sur apport de {st.session_state.config.get('retraite_apport_mensuel', 250)} $/mois.")

elif page == "🏛️ Fiscalité":
    st.title("🏛️ Fiscalité (Données Drive)"); def u_fc():
        for k in ["in_statut", "in_enf", "in_s1", "in_s2", "in_u1", "in_k1", "in_cv1", "in_r1", "in_u2", "in_k2", "in_cv2", "in_r2"]:
            if k in st.session_state: st.session_state.config[k.replace("in_", "f_")] = st.session_state[key]
        try: save_sheet("Config", pd.DataFrame(list(st.session_state.config.items()), columns=["Clé", "Valeur"]))
        except: pass
    cs1, cs2 = st.columns(2)
    with cs1:
        st_m = st.radio("Statut", ["Célibataire / Divorcé(e) / Veuf(ve)", "Marié(e) / Pacsé(e)"], index=0 if st.session_state.config.get("f_statut","") == "Célibataire / Divorcé(e) / Veuf(ve)" else 1, key="in_statut", on_change=u_fc)
        enf = st.number_input("Enfants", 0, 10, int(st.session_state.config.get("f_enf", 0)), key="in_enf", on_change=u_fc)
    with cs2:
        s1 = st.number_input("Salaire 1 €", 0.0, value=float(st.session_state.config.get("f_s1", 30000)), key="in_s1", on_change=u_fc)
        s2 = st.number_input("Salaire 2 €", 0.0, value=float(st.session_state.config.get("f_s2", 0)), key="in_s2", on_change=u_fc) if "Marié" in st_m else 0.0
    st.divider(); dt = st.session_state.transactions.copy(); dt['DT'] = pd.to_datetime(dt['Date'], dayfirst=True, errors='coerce')
    af = st.selectbox("Année fiscale :", sorted(dt['DT'].dropna().dt.year.unique().tolist(), reverse=True) if not dt.empty else [2024])
    dv = dt[(dt['Type'].str.lower().str.contains('vente')) & (dt['DT'].dt.year == af)].copy(); pva = 0.0
    if not dv.empty:
        rf = []
        for _, r in dv.iterrows():
            t = str(r['Ticker']).upper(); if est_devise_liquide(t): continue
            q, n, pru, fx = r['Quantité'], r['Montant Net'], r.get('PRU (Devise)', 0.0), r.get('Taux change (EUR)', 1.0)
            pv = (n - (pru * q)) * fx; pva += pv; rf.append({"Actif": t, "Date": r['Date'], "Qté": q, "PRU": pru, "Net": n, "PV €": pv})
        st.dataframe(pd.DataFrame(rf), use_container_width=True); st.metric("Total Plus-values (€)", f"{pva:,.2f} €")
