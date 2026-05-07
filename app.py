import streamlit as st, pandas as pd, yfinance as yf, re, datetime, time, plotly.express as px, gspread, urllib.request, json
from streamlit_autorefresh import st_autorefresh
from gspread_dataframe import set_with_dataframe, get_as_dataframe
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Mon Portefeuille", layout="wide")
st_autorefresh(interval=15 * 60 * 1000, key="datarefresh")

def check_password():
    if "password_correct" not in st.session_state: st.session_state["password_correct"] = False
    if not st.session_state["password_correct"]:
        st.markdown("<h1 style='text-align: center;'>🔒 Accès Sécurisé</h1>", unsafe_allow_html=True)
        pwd = st.text_input("Veuillez entrer votre mot de passe :", type="password")
        if pwd == st.secrets["APP_PASSWORD"]: st.session_state["password_correct"] = True; st.rerun()
        elif pwd != "": st.error("Mot de passe incorrect.")
        return False
    return True
if not check_password(): st.stop()

@st.cache_resource
def init_google_sheets():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    return gspread.authorize(Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)).open_by_key("1hkZoHQ1vvtbI1DYHR_OnofWn4jG92JGyxJjN-FedsWk")

try: sh = init_google_sheets()
except Exception as e: st.error("Erreur de connexion à Google Sheets."); st.stop()

def load_sheet(sheet_name, default_cols):
    try:
        df = get_as_dataframe(sh.worksheet(sheet_name), evaluate_formulas=True).dropna(how='all').dropna(axis=1, how='all')
        return df if not df.empty else pd.DataFrame(columns=default_cols)
    except: return pd.DataFrame(columns=default_cols)

def save_sheet(sheet_name, df):
    try:
        try: ws = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound: ws = sh.add_worksheet(title=sheet_name, rows=100, cols=20)
        ws.clear(); set_with_dataframe(ws, df, include_index=False)
    except: pass

try: TAUX_EUR_USD = float(yf.Ticker("EURUSD=X").history(period="1d")['Close'].iloc[-1])
except: TAUX_EUR_USD = 1.0

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
    s_usd, s_eur = f"{montant_usd:,.2f}".replace(',', ' '), f"{montant_eur:,.2f}".replace(',', ' ')
    d_html = f"<div style='font-size: 0.9rem; font-weight: 600; color: {'#2ecc71' if '+' in delta_str else ('#e74c3c' if '-' in delta_str else 'inherit')}; padding-top: 0.2rem;'>{delta_str}</div>" if delta_str else ""
    t_val, t_lbl = ("1.8rem", "0.9rem") if taille == "large" else ("1.4rem", "0.85rem") if taille == "medium" else ("1.2rem", "0.85rem")
    c_val = f"color: {couleur_valeur};" if couleur_valeur else ""
    st.markdown(f"""<div style="margin-bottom: 0.8rem;"><div style="font-size: {t_lbl}; opacity: 0.8; margin-bottom: 0.2rem;">{label}</div><div style="font-size: {t_val}; font-weight: 600; line-height: 1.2; {c_val}">{s_usd} $ <span style="font-size: 0.65em; opacity: 0.7; font-weight: 400;">/ {s_eur} €</span></div>{d_html}</div>""", unsafe_allow_html=True)

def est_devise_liquide(ticker):
    t = str(ticker).upper().strip()
    return t.endswith("=X") or (any(m in t for m in ["USD", "EUR", "CHF", "JPY", "CNY", "GBP", "CAD", "AUD"]) and not any(c in t for c in ["BTC", "ETH"]))

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
            t = str(row.get("Type", "")).upper().replace(r'[^\w\s]', '').strip()
            if "ACTION" in t: df.at[idx, "Type"] = "🛢️ Action"
            elif "OBLIGATION" in t: df.at[idx, "Type"] = "📜 Obligation"
            elif "OR" in t: df.at[idx, "Type"] = "💰 Or"
            elif "CRYPTO" in t: df.at[idx, "Type"] = "₿ Crypto"
            elif "RÉSERVE" in t or "RESERVE" in t: df.at[idx, "Type"] = "🏦 Cash réserve"
            elif "CASH" in t: df.at[idx, "Type"] = "💵 Cash"
            else: df.at[idx, "Type"] = "💵 Cash" if est_devise_liquide(str(row.get("Ticker", ""))) else "🛢️ Action"
    for col in cols_finales:
        if col not in df.columns: df[col] = 0.0 if col in ["Quantité", "Pourcentage (%)"] else ("$ 0.00" if col in ["Court", "Valeur totale"] else "")
    if "Quantité" in df.columns: df["Quantité"] = df["Quantité"].apply(extraire_nombre)
    if "Pourcentage (%)" in df.columns: df["Pourcentage (%)"] = df["Pourcentage (%)"].apply(extraire_nombre)
    return df[cols_finales].reset_index(drop=True)

@st.cache_data(ttl=86400)
def get_historical_usd_rate(devise, date_val):
    d_clean = str(devise).upper().strip()
    if d_clean in ["USD", ""]: return 1.0
    try:
        d = pd.to_datetime(date_val, dayfirst=True, errors='coerce')
        if pd.isna(d): return 1.0
        if d >= pd.Timestamp.now() - pd.Timedelta(days=1):
            h = yf.Ticker(f"{d_clean}USD=X").history(period="1d")
            return float(h['Close'].iloc[-1]) if not h.empty else 1.0
        h = yf.Ticker(f"{d_clean}USD=X").history(start=(d - pd.Timedelta(days=5)).strftime('%Y-%m-%d'), end=(d + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        return float(h['Close'].iloc[-1]) if not h.empty else 1.0
    except: return 1.0

@st.cache_data(ttl=86400)
def get_historical_fx(devise, date_val):
    d_clean = str(devise).upper().strip()
    if d_clean in ["EUR", ""]: return 1.0
    try:
        d = pd.to_datetime(date_val, dayfirst=True, errors='coerce')
        if pd.isna(d): return 1.0
        if d >= pd.Timestamp.now() - pd.Timedelta(days=1):
            h = yf.Ticker(f"{d_clean}EUR=X").history(period="1d")
            return float(h['Close'].iloc[-1]) if not h.empty else 1.0
        h = yf.Ticker(f"{d_clean}EUR=X").history(start=(d - pd.Timedelta(days=5)).strftime('%Y-%m-%d'), end=(d + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        return float(h['Close'].iloc[-1]) if not h.empty else 1.0
    except: return 1.0

def get_pru_and_qty(ticker, df_transactions):
    df_tick = df_transactions[df_transactions['Ticker'] == ticker].copy()
    if df_tick.empty: return 0.0, 0.0
    if 'Date_DT' not in df_tick.columns: df_tick['Date_DT'] = pd.to_datetime(df_tick['Date'], dayfirst=True, errors='coerce')
    df_tick = df_tick.dropna(subset=['Date_DT']).sort_values('Date_DT')
    t_cost_usd, t_qty = 0.0, 0.0
    for _, r in df_tick.iterrows():
        typ, qte, net_local = str(r['Type']).lower(), extraire_nombre(r['Quantité']), extraire_nombre(r['Montant Net'])
        net_usd = net_local * get_historical_usd_rate(str(r.get('Devise', 'USD')).strip().upper(), r['Date'])
        if "achat" in typ:
            t_cost_usd += net_usd; t_qty += qte
        elif "vente" in typ:
            pru_inst = t_cost_usd / t_qty if t_qty > 0 else 0.0
            t_cost_usd -= pru_inst * qte; t_qty -= qte
            if t_qty <= 0.000001: t_cost_usd, t_qty = 0.0, 0.0
    return round(t_cost_usd / t_qty if t_qty > 0 else 0.0, 5), round(t_qty, 5)

def recalculer_toute_la_base_projections(df):
    if df is None or df.empty: return df
    df_t = df.copy()
    for i, nom in enumerate(["Date", "Capital investi", "Actifs Stratégiques", "Total Global"]):
        if i < len(df_t.columns): df_t.rename(columns={df_t.columns[i]: nom}, inplace=True)
    for col in ["Capital investi", "Actifs Stratégiques", "Total Global"]: df_t[col] = df_t[col].apply(extraire_nombre)
    df_t['DT_TRI'] = pd.to_datetime(df_t['Date'], dayfirst=True, errors='coerce')
    df_t = df_t.sort_values('DT_TRI').reset_index(drop=True)
    res, c_twr, tg_twr = [], 1.0, 1.0
    for i in range(len(df_t)):
        r = df_t.iloc[i].to_dict(); cap, act, tg = r["Capital investi"], r["Actifs Stratégiques"], r["Total Global"]
        if i == 0:
            r["Evolution actifs $"] = r["Evolution actifs %"] = 0.0
            r["Evolution cumulée $"], r["Evolution cumulée %"] = act - cap, ((act - cap)/cap*100) if cap != 0 else 0.0
            r["TG_Evolution cumulée $"], r["TG_Evolution cumulée %"] = tg - cap, ((tg - cap)/cap*100) if cap != 0 else 0.0
            c_twr *= (1 + ((act - cap)/cap if cap != 0 else 0.0)); tg_twr *= (1 + ((tg - cap)/cap if cap != 0 else 0.0))
        else:
            prev = df_t.iloc[i-1]; d_cap = cap - prev["Capital investi"]
            evo_usd, evo_tg = (act - prev["Actifs Stratégiques"]) - d_cap, (tg - prev["Total Global"]) - d_cap
            r["Evolution actifs $"], r["Evolution actifs %"] = evo_usd, (evo_usd / prev["Actifs Stratégiques"] * 100) if prev["Actifs Stratégiques"] != 0 else 0.0
            r["Evolution cumulée $"], r["Evolution cumulée %"] = act - cap, ((act - cap)/cap*100) if cap != 0 else 0.0
            r["TG_Evolution cumulée $"], r["TG_Evolution cumulée %"] = tg - cap, ((tg - cap)/cap*100) if cap != 0 else 0.0
            c_twr *= (1 + (evo_usd / (prev["Actifs Stratégiques"] + d_cap) if (prev["Actifs Stratégiques"] + d_cap) != 0 else 0.0))
            tg_twr *= (1 + (evo_tg / (prev["Total Global"] + d_cap) if (prev["Total Global"] + d_cap) != 0 else 0.0))
        r["Score TWR %"], r["TG_Score TWR %"] = (c_twr - 1) * 100, (tg_twr - 1) * 100
        res.append(r)
    df_f = pd.DataFrame(res)
    if 'DT_TRI' in df_f.columns: df_f.drop(columns=['DT_TRI'], inplace=True)
    return df_f

def recalculer_totaux_locaux():
    if "donnees" in st.session_state:
        df = st.session_state.donnees.copy()
        for idx, row in df.iterrows():
            c, q = extraire_nombre(row.get("Court", 0)), extraire_nombre(row.get("Quantité", 0))
            df.at[idx, "Valeur totale"] = f"$ {round(c * q, 2):,.2f}"; df.at[idx, "Court"] = f"$ {c:.2f}"
        st.session_state.donnees = df

def calculer_metriques_jour(df_actuel, variations):
    val_invest = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    val_total = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows())
    somme_p = sum(extraire_nombre(r["Pourcentage (%)"]) for _, r in df_actuel.iterrows())
    v_j_tg = val_t_v = v_j_st = val_i_v = 0.0
    for _, r in df_actuel.iterrows():
        tick = str(r.get("Ticker", "")).strip().upper(); v_act = extraire_nombre(r["Valeur totale"])
        match = re.search(r'([+-]?\d+\.?\d*)', variations.get(tick, "0")); v_pct = float(match.group(1)) if match else 0.0
        v_veil = v_act / (1 + v_pct / 100) if (1 + v_pct / 100) != 0 else v_act
        v_j_tg += (v_act - v_veil); val_t_v += v_veil
        if extraire_nombre(r["Pourcentage (%)"]) > 0: v_j_st += (v_act - v_veil); val_i_v += v_veil
    return val_invest, val_total, somme_p, v_j_tg, (v_j_tg/val_t_v*100 if val_t_v>0 else 0.0), v_j_st, (v_j_st/val_i_v*100 if val_i_v>0 else 0.0)

def actualiser_cours_internet(silencieux=False):
    if "donnees" in st.session_state:
        if not silencieux: st.toast("🔄 Actualisation...")
        df_tmp = st.session_state.donnees.copy(); changement, taux_cache = False, {} 
        if "variations" not in st.session_state: st.session_state.variations = {}
        for idx, row in df_tmp.iterrows():
            tick = str(row.get("Ticker", "")).strip().upper()
            if tick and tick != "NAN":
                if tick == "USD":
                    st.session_state.variations[tick] = "→ 0.00 %"; df_tmp.at[idx, "Court"] = "$ 1.00"; changement = True; continue
                if tick in ["EUR", "CHF", "JPY", "GBP", "CNY", "CAD", "AUD"]:
                    try:
                        asset = yf.Ticker(f"{tick}USD=X")
                        try: p_usd = float(asset.fast_info.get('lastPrice', 0.0))
                        except: p_usd = float(asset.history(period="1d")['Close'].iloc[-1]) if not asset.history(period="1d").empty else 0.0
                        try:
                            p_prev = float(asset.fast_info.get('previous_close', 0.0))
                            if p_prev <= 0.0: h = asset.history(period="5d"); p_prev = float(h['Close'].iloc[-2]) if len(h) >= 2 else 0.0
                            if p_prev > 0.0 and p_usd > 0.0:
                                var = ((p_usd - p_prev) / p_prev) * 100
                                st.session_state.variations[tick] = f"{'↗' if var > 0 else '↘' if var < 0 else '→'} {var:+.2f} %"
                            else: st.session_state.variations[tick] = "→ 0.00 %"
                        except: st.session_state.variations[tick] = "→ 0.00 %"
                        df_tmp.at[idx, "Court"] = f"$ {p_usd:.4f}"; changement = True; continue
                    except: pass
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
                                df_tmp.at[idx, "Court"] = f"$ {p_usd:.2f}"; changement = succ_bin = True; break 
                        except: continue 
                if succ_bin: continue 
                tick_yf = tick.replace("USDT", "-USD") if (tick.endswith("USDT") and not succ_bin) else tick
                try:
                    asset = yf.Ticker(tick_yf)
                    try: p_loc = float(asset.fast_info.get('lastPrice', 0.0))
                    except: p_loc = float(asset.history(period="1d")['Close'].iloc[-1]) if not asset.history(period="1d").empty else 0.0
                    try:
                        p_prev = float(asset.fast_info.get('previous_close', 0.0))
                        if p_prev <= 0.0: h = asset.history(period="5d"); p_prev = float(h['Close'].iloc[-2]) if len(h) >= 2 else 0.0
                        if p_prev > 0.0 and p_loc > 0.0: var = ((p_loc - p_prev) / p_prev) * 100; st.session_state.variations[tick] = f"{'↗' if var > 0 else '↘' if var < 0 else '→'} {var:+.2f} %"
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
        if changement: st.session_state.donnees = df_tmp; recalculer_totaux_locaux(); save_sheet("Donnees", st.session_state.donnees)

@st.cache_data(ttl=86400) 
def recuperer_inflation_france():
    try:
        req = urllib.request.Request("https://api.worldbank.org/v2/country/FRA/indicator/FP.CPI.TOTL.ZG?format=json&per_page=20", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if len(data) == 2 and isinstance(data[1], list): return {int(i['date']): round(float(i['value']), 2) for i in data[1] if i['value'] is not None}
    except: pass
    return None

def calcul_frais_km(km, cv):
    c = {3:(0.529, 0.316, 1065, 0.370), 4:(0.606, 0.340, 1330, 0.407), 5:(0.636, 0.357, 1395, 0.427), 6:(0.665, 0.374, 1457, 0.447), 7:(0.697, 0.394, 1515, 0.470)}.get(cv, (0.697, 0.394, 1515, 0.470))
    return km * c[0] if km <= 5000 else (km * c[1] + c[2] if km <= 20000 else km * c[3])

def calcul_impot_ir(rev, parts, stat, apply_decote=True):
    qf, imp = rev / parts, 0
    for lim, prev, tx in [(28797, 11294, 0.11), (82341, 28797, 0.30), (177106, 82341, 0.41), (9999999, 177106, 0.45)]:
        if qf > prev: imp += (min(qf, lim) - prev) * tx
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
for k, v in {"retraite_apport_mensuel": 250.0, "retraite_taxe": 30.0, "f_statut": "Marié(e) / Pacsé(e)", "f_enf": 0.0, "f_s1": 30000.0, "f_s2": 0.0, "f_u1": 0.0, "f_k1": 0.0, "f_cv1": 5.0, "f_r1": 0.0, "f_u2": 0.0, "f_k2": 0.0, "f_cv2": 5.0, "f_r2": 0.0}.items():
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
        df_i['Année'] = pd.to_numeric(df_i['Année'], errors='coerce').fillna(0).astype(int)
        df_i['Inflation (%)'] = pd.to_numeric(df_i['Inflation (%)'], errors='coerce').fillna(0.0)
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
        if chg: st.session_state.inflation = pd.DataFrame(n_inf); save_sheet("Inflation", st.session_state.inflation)
if "dernier_refresh_cours" not in st.session_state: st.session_state.dernier_refresh_cours = 0
if time.time() - st.session_state.dernier_refresh_cours >= 900: actualiser_cours_internet(silencieux=(st.session_state.dernier_refresh_cours == 0)); st.session_state.dernier_refresh_cours = time.time()

# --- 6. NAVIGATION ---
st.sidebar.title("Menu")
page_choisie = st.sidebar.radio("Aller vers :", ["📊 Tableau de bord", "📋 Liste des actifs", "⚖️ Rééquilibrage", "💰 Fonds", "🏖️ Suivi", "📈 Performance", "🌴 Retraite", "🏛️ Fiscalité"])
st.sidebar.divider()
if st.sidebar.button("🔄 Recharger l'application", use_container_width=True): st.session_state.clear(); st.rerun()

# --- 7. PAGES DE L'APPLICATION ---
if page_choisie == "📊 Tableau de bord":
    st.title("📊 Vue d'ensemble de mon Patrimoine")
    df_actuel, df_p = st.session_state.donnees, st.session_state.projections
    val_invest, val_total, somme_p, v_jour_tg_usd, pct_jour_tg, v_jour_strat_usd, pct_jour_strat = calculer_metriques_jour(df_actuel, st.session_state.variations)
    cap_actuel = sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _, r in st.session_state.historique.iterrows())
    df_p_live = recalculer_toute_la_base_projections(pd.concat([df_p, pd.DataFrame([{"Date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "Capital investi": cap_actuel, "Actifs Stratégiques": val_invest, "Total Global": val_total}])], ignore_index=True))
    
    delta = p_delta = delta_tg = p_delta_tg = 0.0
    if not df_p.empty:
        df_d = df_p.copy(); df_d['Date_DT'] = pd.to_datetime(df_d['Date'], dayfirst=True, errors='coerce'); df_d = df_d.dropna(subset=['Date_DT']).sort_values('Date_DT')
        if not df_d.empty:
            df_past = df_d[df_d['Date_DT'] <= pd.Timestamp.now() - pd.DateOffset(years=1)]
            row_ref = df_past.iloc[-1] if not df_past.empty else df_d.iloc[0] 
            v_ref_strat, v_ref_tg = extraire_nombre(row_ref["Actifs Stratégiques"]), extraire_nombre(row_ref["Total Global"])
            delta, delta_tg = val_invest - v_ref_strat, val_total - v_ref_tg
            if v_ref_strat > 0: p_delta = (delta / v_ref_strat) * 100
            if v_ref_tg > 0: p_delta_tg = (delta_tg / v_ref_tg) * 100

    besoin_req = False
    if val_invest > 0:
        for _, r in df_actuel.iterrows():
            cib = extraire_nombre(r["Pourcentage (%)"]) / 100
            if cib > 0:
                act = extraire_nombre(r["Valeur totale"])
                if abs((val_invest * cib) - act) >= 1000 and abs((act / val_invest * 100) - (cib * 100)) >= 2.0:
                    besoin_req = True; break

    st.subheader("⚙️ 1. Pilotage & Statut")
    c_btn, c_stat = st.columns([1, 2])
    with c_btn:
        if st.button("🔄 Actualiser les cours", use_container_width=True): actualiser_cours_internet(False); st.rerun()
    with c_stat:
        if besoin_req: st.warning("⚠️ **Rééquilibrage nécessaire** (Certains actifs ont dépassé les tolérances.)")
        else: st.success("✅ **Équilibré** (Votre stratégie d'allocation cible est respectée.)")
    st.divider()

    st.subheader("🌍 2. Total Global (Toutes liquidités incluses)")
    c_tg, _ = st.columns(2)
    with c_tg:
        afficher_montant_double("Total Global", val_total, f"{delta_tg:+,.2f} $ ({p_delta_tg:+.2f} % sur 1 an glissant)")
        st.markdown(f"<div style='margin-top:-0.5rem; margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if v_jour_tg_usd >= 0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if v_jour_tg_usd >= 0 else '#e74c3c'}'>{v_jour_tg_usd:+,.2f} $ ({pct_jour_tg:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    
    if not df_p.empty:
        df_v_tg = df_p_live.copy(); df_v_tg['Date_DT'] = pd.to_datetime(df_v_tg['Date'], dayfirst=True, errors='coerce')
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
            md, mf = 1 + df_v_tg['TG_Score TWR %'].iloc[0] / 100, 1 + df_v_tg['TG_Score TWR %'].iloc[-1] / 100
            twr_p = ((mf / md) - 1) * 100 if md != 0 else 0.0
            
            cg1, cg2 = st.columns([1, 3])
            with cg1:
                if "ROI" in m_tg:
                    afficher_montant_double("Gains nets globaux", df_v_tg['TG_Evolution cumulée $'].iloc[-1], f"{d_usd:+,.2f} $ ({pct:+.2f} % sur la période)", taille="medium")
                    p_gl = df_v_tg['TG_Evolution cumulée %'].iloc[-1]
                    st.markdown(f"📊 Rentabilité Globale : <strong style='color:{'green' if p_gl > 0 else 'red' if p_gl < 0 else 'gray'}'>{p_gl:+.2f} %</strong>", unsafe_allow_html=True)
                else:
                    st.metric("Score TWR Global (%)", f"{df_v_tg['TG_Score TWR %'].iloc[-1]:+.2f} %", f"{twr_p:+.2f} % (sur la période)")
                    afficher_montant_double("Gains nets actuels", df_v_tg['TG_Evolution cumulée $'].iloc[-1], taille="medium")
            with cg2:
                fig_lt = px.line(df_v_tg.reset_index(), x='Date_DT', y='TG_Evolution cumulée $' if "ROI" in m_tg else 'TG_Score TWR %')
                fig_lt.update_traces(line_shape='spline'); fig_lt.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0)); fig_lt.update_yaxes(zeroline=False, rangemode="normal")
                st.plotly_chart(fig_lt, use_container_width=True)

        st.write(""); st.markdown("**🌍 Répartition du Patrimoine (Total Global)**")
        cp1, _ = st.columns(2)
        with cp1:
            st.markdown("*Toutes classes d'actifs confondues*")
            df_p_tg = df_actuel.copy(); df_p_tg['Val'] = df_p_tg['Valeur totale'].apply(extraire_nombre)
            df_pie_tg = df_p_tg[df_p_tg['Val'] > 0].groupby('Type')['Val'].sum().reset_index()
            if not df_pie_tg.empty:
                fig_tg = px.pie(df_pie_tg, values='Val', names='Type', color='Type', color_discrete_map={"🛢️ Action": "#e74c3c", "📜 Obligation": "#3498db", "💰 Or": "#f1c40f", "₿ Crypto": "#9b59b6", "💵 Cash": "#2ecc71", "🏦 Cash réserve": "#f39c12"}, hole=0.4)
                fig_tg.update_traces(textposition='inside', textinfo='percent+label'); fig_tg.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
                st.plotly_chart(fig_tg, use_container_width=True)
    else: st.info("Aucune donnée globale disponible pour l'analyse.")
    st.divider()

    st.subheader("🎯 3. Actifs Stratégiques (Investissements cibles)")
    c_st, _ = st.columns(2)
    with c_st:
        afficher_montant_double("Actifs Stratégiques", val_invest, f"{delta:+,.2f} $ ({p_delta:+.2f} % sur 1 an glissant)")
        st.markdown(f"<div style='margin-top:-0.5rem; margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if v_jour_strat_usd >= 0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if v_jour_strat_usd >= 0 else '#e74c3c'}'>{v_jour_strat_usd:+,.2f} $ ({pct_jour_strat:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    
    if df_p.empty: st.info("Aucune donnée.")
    else:
        df_v_s = df_p_live.copy(); df_v_s['Date_DT'] = pd.to_datetime(df_v_s['Date'], dayfirst=True, errors='coerce')
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
            md, mf = 1 + df_v_s['Score TWR %'].iloc[0] / 100, 1 + df_v_s['Score TWR %'].iloc[-1] / 100
            twr_p = ((mf / md) - 1) * 100 if md != 0 else 0.0
            
            cg1, cg2 = st.columns([1, 3])
            with cg1:
                if "ROI" in m_s:
                    afficher_montant_double("Gains nets de la stratégie", df_v_s['Evolution cumulée $'].iloc[-1], f"{d_usd:+,.2f} $ ({pct:+.2f} % sur la période)", taille="medium")
                    p_gl = df_v_s['Evolution cumulée %'].iloc[-1]
                    st.markdown(f"📊 Rentabilité Stratégique : <strong style='color:{'green' if p_gl > 0 else 'red' if p_gl < 0 else 'gray'}'>{p_gl:+.2f} %</strong>", unsafe_allow_html=True)
                else:
                    st.metric("Score TWR Stratégique (%)", f"{df_v_s['Score TWR %'].iloc[-1]:+.2f} %", f"{twr_p:+.2f} % (sur la période)")
                    afficher_montant_double("Gains nets actuels", df_v_s['Evolution cumulée $'].iloc[-1], taille="medium")
            with cg2:
                fig_ls = px.line(df_v_s.reset_index(), x='Date_DT', y='Evolution cumulée $' if "ROI" in m_s else 'Score TWR %')
                fig_ls.update_traces(line_shape='spline'); fig_ls.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0)); fig_ls.update_yaxes(zeroline=False, rangemode="normal")
                st.plotly_chart(fig_ls, use_container_width=True)

    st.write(""); st.markdown("**🎯 Répartition détaillée de la stratégie**")
    df_st = df_actuel[df_actuel['Pourcentage (%)'].apply(extraire_nombre) > 0].copy(); df_st['Val'] = df_st['Valeur totale'].apply(extraire_nombre)
    cp1, cp2 = st.columns(2)
    with cp1:
        st.markdown("*Classes d'actifs ciblées*")
        d_p1 = df_st[df_st['Val'] > 0].groupby('Type')['Val'].sum().reset_index()
        if not d_p1.empty:
            f1 = px.pie(d_p1, values='Val', names='Type', color='Type', color_discrete_map={"🛢️ Action": "#e74c3c", "📜 Obligation": "#3498db", "💰 Or": "#f1c40f", "₿ Crypto": "#9b59b6", "💵 Cash": "#2ecc71", "🏦 Cash réserve": "#f39c12"}, hole=0.4)
            f1.update_traces(textposition='inside', textinfo='percent+label'); f1.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
            st.plotly_chart(f1, use_container_width=True)
    with cp2:
        st.markdown("*Détail des lignes stratégiques*")
        if not df_st[df_st['Val'] > 0].empty:
            f2 = px.pie(df_st[df_st['Val'] > 0], values='Val', names='Ticker', hole=0.4)
            f2.update_traces(textposition='inside', textinfo='percent+label'); f2.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
            st.plotly_chart(f2, use_container_width=True)

    st.divider(); st.subheader("🏖️ 4. Liberté Financière (Rente Mensuelle actuelle)")
    cr1, cr2 = st.columns(2)
    with cr1: inf = st.slider("Inflation cible à déduire (%) ✍️", 0.0, 15.0, 2.0, 0.1, key="dash_infl")
    with cr2:
        tx_r = ((1 + 0.08) / (1 + (inf / 100.0))) - 1
        afficher_montant_double("Rente Mensuelle Nette (Base 8% par an)", (val_invest * max(0.0, tx_r)) / 12.0, couleur_valeur="#3498db")

elif page_choisie == "📋 Liste des actifs":
    st.title("📋 Liste de mes actifs"); st.write("Modifiez l'allocation cible de vos actifs ici. **La colonne Quantité est verrouillée pour vos investissements** et se met à jour via l'onglet 'Rééquilibrage'.")
    df_actuel = st.session_state.donnees.copy()
    val_invest, val_total, somme_p, v_jour_tg_usd, pct_jour_tg, v_jour_strat_usd, pct_jour_strat = calculer_metriques_jour(df_actuel, st.session_state.variations)

    c1, c2, c3 = st.columns(3)
    with c1:
        afficher_montant_double("Actifs Stratégiques", val_invest)
        st.markdown(f"<div style='margin-top:-0.5rem; margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if v_jour_strat_usd >= 0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if v_jour_strat_usd >= 0 else '#e74c3c'}'>{v_jour_strat_usd:+,.2f} $ ({pct_jour_strat:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    with c2:
        afficher_montant_double("Total Global", val_total)
        st.markdown(f"<div style='margin-top:-0.5rem; margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if v_jour_tg_usd >= 0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if v_jour_tg_usd >= 0 else '#e74c3c'}'>{v_jour_tg_usd:+,.2f} $ ({pct_jour_tg:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    with c3:
        ec = round(100 - somme_p, 2)
        st.markdown(f"<div style='margin-bottom:0.8rem;'><div style='font-size:0.9rem; opacity:0.8; margin-bottom:0.2rem;'>Répartition Cible</div><div style='font-size:1.8rem; font-weight:600; line-height:1.2;'>{somme_p:.2f} %</div><div style='font-size:0.9rem; font-weight:600; color:{'#2ecc71' if ec == 0 else '#e74c3c'}; padding-top:0.2rem;'>{'✅ Cible atteinte' if ec == 0 else f'⚠️ {abs(ec):.2f} % manquant/en trop'}</div></div>", unsafe_allow_html=True)
    st.divider()

    if st.button("🔄 Actualiser les cours", use_container_width=True): actualiser_cours_internet(False); st.rerun()

    df_actuel['Var. Jour 🔒'] = df_actuel['Ticker'].apply(lambda x: st.session_state.variations.get(str(x).upper(), "→ 0.00 %"))
    c_act_locked = {
        "Ticker": st.column_config.TextColumn("Ticker ✍️"),
        "Type": st.column_config.SelectboxColumn("Type ✍️", options=["🛢️ Action", "📜 Obligation", "💰 Or", "₿ Crypto", "💵 Cash", "🏦 Cash réserve"]),
        "Court": st.column_config.TextColumn("Court 🔒", disabled=True),
        "Quantité": st.column_config.NumberColumn("Quantité 🔒", disabled=True, format="%.5f"),
        "Valeur totale": st.column_config.TextColumn("Valeur totale 🔒", disabled=True),
        "Pourcentage (%)": st.column_config.NumberColumn("Pourcentage (%) ✍️", format="%.2f %%"),
        "Var. Jour 🔒": st.column_config.TextColumn("Var. Jour 🔒", disabled=True)
    }
    c_act_unlocked = c_act_locked.copy(); c_act_unlocked["Quantité"] = st.column_config.NumberColumn("Quantité ✍️", disabled=False, format="%.5f")
    
    def c_var(v): return 'color:#2ecc71' if "↗" in str(v) or "+" in str(v) else ('color:#e74c3c' if "↘" in str(v) or "-" in str(v) else 'color:#95a5a6')
    d_c = ["Ticker", "Type", "Court", "Quantité", "Valeur totale", "Pourcentage (%)", "Var. Jour 🔒"]
    m_dev = df_actuel['Type'].astype(str).str.contains("Cash", na=False)
    
    st.markdown("### 📈 Actifs d'Investissement"); st.caption("La colonne Quantité est verrouillée : elle se met à jour automatiquement via vos transactions.")
    res_i = st.data_editor(df_actuel[~m_dev][d_c].style.map(c_var, subset=["Var. Jour 🔒"]), key="ei", column_config=c_act_locked, use_container_width=True, hide_index=True, num_rows="dynamic")
    st.markdown("### 💵 Liquidités (Devises & Réserves)"); st.caption("Vous pouvez forcer ou ajuster manuellement la quantité de vos liquidités ici.")
    res_d = st.data_editor(df_actuel[m_dev][d_c].style.map(c_var, subset=["Var. Jour 🔒"]), key="ed", column_config=c_act_unlocked, use_container_width=True, hide_index=True, num_rows="dynamic")

    new_df = pd.concat([res_i, res_d], ignore_index=True)
    cols = ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]
    if not new_df[cols].equals(st.session_state.donnees[cols]):
        st.session_state.donnees = new_df[cols]; recalculer_totaux_locaux(); save_sheet("Donnees", st.session_state.donnees); st.rerun()

elif page_choisie == "⚖️ Rééquilibrage":
    st.title("⚖️ Rééquilibrage & Transactions")
    with st.expander("➕ Enregistrer une transaction (Achat/Vente)"):
        with st.form("new_trans"):
            c1, c2, c3 = st.columns(3); t_d = c1.date_input("Date")
            lt = sorted(st.session_state.donnees['Ticker'].dropna().unique().tolist()); lt.insert(0, "➕ Nouvel actif...")
            t_sel = c2.selectbox("Actif (Ticker)", lt)
            t_t = c2.text_input("Saisissez le Ticker du nouvel actif") if t_sel == "➕ Nouvel actif..." else t_sel
            t_ty = c3.selectbox("Type", ["Achat", "Vente"])
            c4, c5, c6 = st.columns(3)
            t_q = c4.number_input("Quantité", min_value=0.0, format="%.5f"); t_c = c5.number_input("Cours de l'actif", min_value=0.0, format="%.5f"); t_f = c6.number_input("Frais de transaction", min_value=0.0, format="%.2f")
            t_dev = st.selectbox("Devise", ["USD", "EUR", "CHF", "JPY", "GBP"])
            if st.form_submit_button("🔨 Valider la transaction"):
                if t_t.strip() == "": st.error("❌ Le Ticker ne peut pas être vide.")
                elif t_q <= 0: st.error("❌ La quantité doit être strictement supérieure à 0.")
                elif t_c <= 0: st.error("❌ Le cours doit être strictement supérieur à 0.")
                else:
                    t_cl = t_t.upper().strip(); m_n = round((t_q * t_c) + t_f if t_ty == "Achat" else (t_q * t_c) - t_f, 5)
                    fx = get_historical_fx(t_dev, t_d.strftime("%Y-%m-%d"))
                    c_pru_usd, c_qty = get_pru_and_qty(t_cl, st.session_state.transactions)
                    if t_ty == "Achat":
                        fx_usd = get_historical_usd_rate(t_dev, t_d.strftime("%Y-%m-%d"))
                        net_usd = m_n * fx_usd; new_cost_usd = (c_pru_usd * c_qty) + net_usd; new_qty = c_qty + t_q
                        r_pru_usd = round(new_cost_usd / new_qty, 5) if new_qty > 0 else 0.0
                    else: r_pru_usd = c_pru_usd
                    nr = {"Ticker": t_cl, "Type": t_ty, "Date": t_d.strftime("%d/%m/%Y"), "Quantité": t_q, "Cours": t_c, "Frais": t_f, "Montant Net": m_n, "Devise": t_dev, "PRU (Devise)": r_pru_usd, "Taux change (EUR)": fx}
                    st.session_state.transactions = pd.concat([st.session_state.transactions, pd.DataFrame([nr])], ignore_index=True)
                    cols_to_save = [c for c in st.session_state.transactions.columns if c != 'Date_DT']
                    save_sheet("Transaction", st.session_state.transactions[cols_to_save])
                    df_d = st.session_state.donnees.copy()
                    i_a = df_d.index[df_d['Ticker'] == t_cl].tolist()
                    if not i_a:
                        cat_t = "₿ Crypto" if any(c in t_cl for c in ["BTC", "ETH", "USDT"]) else "🛢️ Action"
                        df_d = pd.concat([df_d, pd.DataFrame([{"Ticker": t_cl, "Type": cat_t, "Quantité": 0.0, "Court": "$ 0.00", "Valeur totale": "$ 0.00", "Pourcentage (%)": 0.0}])], ignore_index=True)
                        i_a = [len(df_d) - 1]
                    idx = i_a[0]
                    df_d.at[idx, "Quantité"] = max(0.0, extraire_nombre(df_d.at[idx, "Quantité"]) + (t_q if t_ty == "Achat" else -t_q))
                    i_c = df_d.index[df_d['Ticker'] == t_dev].tolist()
                    if not i_c:
                        df_d = pd.concat([df_d, pd.DataFrame([{"Ticker": t_dev, "Type": "💵 Cash", "Quantité": 0.0, "Court": "$ 0.00", "Valeur totale": "$ 0.00", "Pourcentage (%)": 0.0}])], ignore_index=True)
                        i_c = [len(df_d) - 1]
                    df_d.at[i_c[0], "Quantité"] = max(0.0, extraire_nombre(df_d.at[i_c[0], "Quantité"]) + (-m_n if t_ty == "Achat" else m_n))
                    st.session_state.donnees = nettoyer_dataframe(df_d); recalculer_totaux_locaux(); save_sheet("Donnees", st.session_state.donnees)
                    st.success("✅ Transaction enregistrée, PRU calculé en USD et Liquidités mises à jour avec succès !"); time.sleep(2); st.rerun()

    st.divider(); 
    if st.button("🔄 Actualiser les cours", use_container_width=True): actualiser_cours_internet(False); st.rerun()
    st.subheader("⚖️ Analyse de l'allocation")
    df = st.session_state.donnees
    c_usd = sum(extraire_nombre(r["Valeur totale"]) for _, r in df[df["Type"] == "💵 Cash"].iterrows())
    base = sum(extraire_nombre(r["Valeur totale"]) for _, r in df.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0) + c_usd
    if base > 0:
        st.info(f"💡 Liquidités disponibles pour investissement (Type '💵 Cash' pur) : **{c_usd:,.2f} $**")
        res = []
        for _, r in df.iterrows():
            t = str(r["Ticker"]).upper(); cib = extraire_nombre(r["Pourcentage (%)"]) / 100
            if cib <= 0: continue
            act = extraire_nombre(r["Valeur totale"]); d = (base * cib) - act; p = extraire_nombre(r["Court"]); q = d / p if p > 0 else 0
            current_pru_usd, _ = get_pru_and_qty(t, st.session_state.transactions)
            p_str = "N/A"
            if current_pru_usd > 0 and p > 0: p_str = f"{(((p / current_pru_usd) - 1) * 100):+.2f} %"
            s = "+ " if q > 0.000001 else "- " if q < -0.000001 else ""
            q_fmt = f"({s}{abs(round(q, 5)):.5f})" if "BTC" in t or "USDT" in t else f"({s}{abs(int(round(q)))})"
            act_str = f"✅ ÉQUILIBRÉ (${abs(d):,.2f})" if abs(d) < 1000 or abs((act/base*100) - cib*100) < 2.0 else f"{'🟢 ACHETER' if d > 0 else '🔴 VENDRE'}$ {abs(d):,.2f}"
            res.append({"Ticker 🔒": t, "PRU ($) 🔒": current_pru_usd, "Var. Jour 🔒": st.session_state.variations.get(t, "→ 0.00 %"), "Perf. Globale 🔒": p_str, "Actuel ($) 🔒": act, "Écart (%) 🔒": (act/base*100) - cib*100, "Action 🔒": act_str, "Qté (+/-) 🔒": q_fmt})
        def cr(v):
            if "↗" in str(v) or "ACHETER" in str(v) or "+" in str(v): return 'color:#2ecc71'
            if "↘" in str(v) or "VENDRE" in str(v) or "-" in str(v): return 'color:#e74c3c'
            return 'color:#95a5a6'
        st.dataframe(pd.DataFrame(res).style.format({"PRU ($) 🔒": "$ {:,.5f}", "Actuel ($) 🔒": "$ {:,.2f}", "Écart (%) 🔒": "{:+.2f} %"}).map(cr, subset=["Var. Jour 🔒", "Action 🔒", "Qté (+/-) 🔒", "Perf. Globale 🔒"]), use_container_width=True, hide_index=True)

elif page_choisie == "💰 Fonds":
    st.title("💰 Fonds")
    st.write("Déclarez ici vos apports de capital (virements depuis votre compte bancaire). L'argent sera automatiquement ajouté à vos liquidités Cash.")
    df_h = st.session_state.historique
    with st.expander("➕ Nouveau mouvement"):
        with st.form("f_m"):
            d_m = st.date_input("Date ✍️"); t_m = st.radio("Type ✍️", ["Ajout de fond propre", "Retrait"], horizontal=True)
            m_s = st.number_input("Montant ✍️", min_value=0.00, format="%.2f"); d_s = st.selectbox("Devise ✍️", ["$", "€"])
            if st.form_submit_button("Valider"):
                o_px = float(yf.Ticker("GC=F").fast_info.get('lastPrice', 2000.0))
                m_usd = m_s if d_s == "$" else m_s * TAUX_EUR_USD; m_eur = m_s if d_s == "€" else m_s / TAUX_EUR_USD
                nl = {"Date": d_m.strftime("%d/%m/%Y"), "Type": t_m, "Montant $": m_usd, "Montant €": m_eur, "Montant Or": m_usd/o_px}
                st.session_state.historique = pd.concat([df_h, pd.DataFrame([nl])], ignore_index=True)
                save_sheet("Historique", st.session_state.historique)
                dev = "USD" if d_s == "$" else "EUR"
                df_d = st.session_state.donnees.copy(); i_c = df_d.index[df_d['Ticker'] == dev].tolist()
                if not i_c:
                    df_d = pd.concat([df_d, pd.DataFrame([{"Ticker": dev, "Type": "💵 Cash", "Quantité": 0.0, "Court": "$ 0.00", "Valeur totale": "$ 0.00", "Pourcentage (%)": 0.0}])], ignore_index=True)
                    i_c = [len(df_d) - 1]
                idx_c = i_c[0]; cash_actuel = extraire_nombre(df_d.at[idx_c, "Quantité"])
                df_d.at[idx_c, "Quantité"] = (cash_actuel + m_s) if t_m == "Ajout de fond propre" else max(0.0, cash_actuel - m_s)
                st.session_state.donnees = nettoyer_dataframe(df_d); recalculer_totaux_locaux(); save_sheet("Donnees", st.session_state.donnees)
                st.success("✅ Mouvement enregistré et Liquidités mises à jour !"); time.sleep(1); st.rerun()
    
    apports = sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _, r in df_h.iterrows())
    afficher_montant_double("Total Apports nets", apports)
    if not df_h.empty:
        d_v = df_h.copy(); d_v.columns = [f"{c} 🔒" for c in d_v.columns]
        d_v['DT'] = pd.to_datetime(d_v['Date 🔒'], dayfirst=True, errors='coerce')
        st.dataframe(d_v.sort_values('DT', ascending=False).drop(columns=['DT']).style.format({"Montant $ 🔒": "$ {:,.2f}", "Montant € 🔒": "{:,.2f} €", "Montant Or 🔒": "{:,.4f} oz"}), use_container_width=True, hide_index=True)

elif page_choisie == "🏖️ Suivi":
    st.title("🏖️ Suivi & Évolution")
    st.write("Ce tableau enregistre vos points de passage. **Votre robot automatique enregistre une nouvelle ligne chaque nuit.** Ce tableau est en lecture seule (🔒).")
    if not st.session_state.projections.empty:
        df_v = st.session_state.projections.copy()
        df_v['DT'] = pd.to_datetime(df_v['Date'], dayfirst=True, errors='coerce')
        config_suivi = {"Date": st.column_config.TextColumn("Date 🔒"), "Capital investi": st.column_config.NumberColumn("Cap. inv. 🔒", format="$ %.2f"), "Actifs Stratégiques": st.column_config.NumberColumn("Actifs 🔒", format="$ %.2f"), "Total Global": st.column_config.NumberColumn("Total 🔒", format="$ %.2f"), "Evolution actifs $": st.column_config.NumberColumn("Evol. Actifs ($) 🔒", format="$ %+.2f"), "Evolution actifs %": st.column_config.NumberColumn("Evol. Actifs (%) 🔒", format="%+.2f %%"), "Evolution cumulée $": st.column_config.NumberColumn("Evol. Cumulée ($) 🔒", format="$ %+.2f"), "Evolution cumulée %": st.column_config.NumberColumn("Evol. Cumulée (%) 🔒", format="%+.2f %%"), "Score TWR %": st.column_config.NumberColumn("Score TWR (%) 🔒", format="%+.2f %%"), "TG_Evolution cumulée $": st.column_config.NumberColumn("TG Evol. Cumulée ($) 🔒", format="$ %+.2f"), "TG_Evolution cumulée %": st.column_config.NumberColumn("TG Evol. Cumulée (%) 🔒", format="%+.2f %%"), "TG_Score TWR %": st.column_config.NumberColumn("TG Score TWR (%) 🔒", format="%+.2f %%")}
        st.dataframe(df_v.sort_values('DT', ascending=False).drop(columns=['DT']), column_config=config_suivi, use_container_width=True, hide_index=True)

elif page_choisie == "📈 Performance":
    st.title("📈 Performances Annuelles & Inflation")
    df_p = st.session_state.projections
    if df_p.empty: st.info("Aucune donnée disponible. Le premier point sera enregistré cette nuit.")
    else:
        try: or_px = float(yf.Ticker("GC=F").fast_info.get('lastPrice', 2000.0))
        except: or_px = 2000.0
        df_viz = df_p.copy(); df_viz['Date_DT'] = pd.to_datetime(df_viz['Date'], dayfirst=True, errors='coerce')
        df_viz = df_viz.dropna(subset=['Date_DT']).sort_values('Date_DT'); df_viz['Année'] = df_viz['Date_DT'].dt.year
        df_y = df_viz.groupby('Année').last().reset_index(); df_y['Année'] = df_y['Année'].astype(int)
        df_y['TWR_mult'] = 1 + (df_y['Score TWR %'] / 100); df_y['TWR_mult_prev'] = df_y['TWR_mult'].shift(1).fillna(1.0)
        df_y['Performance brute (%)'] = ((df_y['TWR_mult'] / df_y['TWR_mult_prev']) - 1) * 100
        date_debut_absolue = df_viz['Date_DT'].min(); annee_debut_absolue = date_debut_absolue.year
        jours_annee_1 = (df_viz[df_viz['Année'] == annee_debut_absolue]['Date_DT'].max() - date_debut_absolue).days
        if jours_annee_1 > 0 and jours_annee_1 < 330:
            idx = df_y[df_y['Année'] == annee_debut_absolue].index
            if not idx.empty:
                perf_abs = df_y.loc[idx, 'Performance brute (%)'].values[0] / 100.0
                perf_ann = ((1 + perf_abs) ** (365.25 / jours_annee_1)) - 1
                df_y.loc[idx, 'Performance brute (%)'] = perf_ann * 100.0
        st.session_state.inflation['Année'] = st.session_state.inflation['Année'].astype(int)
        df_y = df_y.merge(st.session_state.inflation, on='Année', how='left').fillna({'Inflation (%)': 0.0})
        df_y['Performance nette (%)'] = (((1 + df_y['Performance brute (%)'] / 100) / (1 + df_y['Inflation (%)'] / 100)) - 1) * 100
        df_y['Gains Nets ($)'] = df_y['Evolution cumulée $'] - df_y['Evolution cumulée $'].shift(1).fillna(0)
        df_y['Valeur Bilan (Or)'] = df_y['Actifs Stratégiques'] / or_px
        df_hist = df_y[df_y['Année'] < datetime.datetime.now().year].copy()
            
        st.subheader("📊 Moyennes Historiques (Hors année en cours)")
        if jours_annee_1 > 0 and jours_annee_1 < 330: st.info(f"💡 **Note :** Votre année de lancement ({annee_debut_absolue}) ayant duré moins d'un an, son pourcentage de rentabilité a été **annualisé** (projeté mathématiquement sur un rythme de 12 mois complets). Cela permet de l'intégrer à vos moyennes et de la comparer à l'inflation.")
        if not df_hist.empty:
            c_m1, c_m2, c_m3, c_m4 = st.columns(4)
            c_m1.metric("Moyenne Perf. Brute", f"{df_hist['Performance brute (%)'].mean():+.2f} %")
            c_m2.metric("Moyenne Inflation", f"{df_hist['Inflation (%)'].mean():.2f} %")
            c_m3.metric("Moyenne Perf. Nette", f"{df_hist['Performance nette (%)'].mean():+.2f} %")
            with c_m4: afficher_montant_double("Moyenne Gains / An", df_hist['Gains Nets ($)'].mean(), taille="medium")
        else: st.info("L'historique complet est insuffisant pour calculer une moyenne.")
        
        st.divider(); st.write("Ce tableau récapitule vos résultats par année civile. L'inflation officielle est **récupérée et mise à jour de manière 100% automatique** depuis la Banque Mondiale. Si l'année en cours n'a pas encore de chiffre officiel, la valeur par défaut est de 0 %.")
        df_display = df_y[['Année', 'Performance brute (%)', 'Inflation (%)', 'Performance nette (%)', 'Gains Nets ($)', 'Actifs Stratégiques', 'Valeur Bilan (Or)']].copy()
        df_display.rename(columns={'Actifs Stratégiques': 'Valeur Bilan ($)'}, inplace=True)
        df_display['Année'] = df_display['Année'].astype(str)
        df_sorted = df_display.sort_values(by='Année', ascending=False).reset_index(drop=True)
        st.dataframe(df_sorted, column_config={"Année": st.column_config.TextColumn("Année 🔒"), "Performance brute (%)": st.column_config.NumberColumn("Perf. Brute (%) 🔒", format="%.2f %%"), "Inflation (%)": st.column_config.NumberColumn("Inflation (%) 🔒", format="%.2f %%"), "Performance nette (%)": st.column_config.NumberColumn("Perf. Nette (%) 🔒", format="%.2f %%"), "Gains Nets ($)": st.column_config.NumberColumn("Gains Nets ($) 🔒", format="$ %.2f"), "Valeur Bilan ($)": st.column_config.NumberColumn("Valeur Bilan ($) 🔒", format="$ %.2f"), "Valeur Bilan (Or)": st.column_config.NumberColumn("Valeur Bilan (Or) 🔒", format="%.2f oz")}, hide_index=True, use_container_width=True)
        st.divider(); st.subheader("📊 Comparaison Brute vs Nette")
        df_chart = df_sorted.sort_values(by='Année', ascending=True)[['Année', 'Performance brute (%)', 'Performance nette (%)']].melt(id_vars='Année', var_name='Type', value_name='Rentabilité (%)')
        df_chart['Type'] = df_chart['Type'].replace({'Performance brute (%)': "Brute (Avant inflation)", 'Performance nette (%)': "Nette (Pouvoir d'achat réel)"})
        fig = px.bar(df_chart, x='Année', y='Rentabilité (%)', color='Type', barmode='group', color_discrete_map={"Brute (Avant inflation)": "#3498db", "Nette (Pouvoir d'achat réel)": "#2ecc71"}, text_auto='.2f')
        fig.update_layout(yaxis_title="Rentabilité (%)", xaxis_title="", legend_title=""); st.plotly_chart(fig, use_container_width=True)

elif page_choisie == "🌴 Retraite":
    st.title("🌴 Simulateur d'Indépendance Financière")
    st.write("Ce simulateur projette la valeur de votre portefeuille jusqu'à votre retraite et calcule la rente mensuelle perpétuelle que vous pourrez en tirer sans jamais entamer votre capital (en pouvoir d'achat réel).")

    df_actuel = st.session_state.donnees
    capital_initial = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    annee_en_cours = datetime.datetime.now().year; moy_brute_hist = 5.00
    
    if not st.session_state.projections.empty:
        df_viz = st.session_state.projections.copy()
        df_viz['Date_DT'] = pd.to_datetime(df_viz['Date'], dayfirst=True, errors='coerce')
        df_viz = df_viz.dropna(subset=['Date_DT']).sort_values('Date_DT')
        df_viz['Année'] = df_viz['Date_DT'].dt.year
        df_years = df_viz.groupby('Année').last().reset_index()
        df_years['TWR_mult'] = 1 + (df_years['Score TWR %'] / 100); df_years['TWR_mult_prev'] = df_years['TWR_mult'].shift(1).fillna(1.0)
        df_years['Performance brute (%)'] = ((df_years['TWR_mult'] / df_years['TWR_mult_prev']) - 1) * 100
        df_historique = df_years[df_years['Année'] < annee_en_cours]
        if not df_historique.empty: moy_brute_hist = round(df_historique['Performance brute (%)'].mean(), 2)

    st.subheader("⚙️ Paramètres du Simulateur"); c_p1, c_p2, c_p3 = st.columns(3)
    def on_retraite_params_change():
        keys_to_save = ["in_app", "in_tax"]
        for k in keys_to_save:
            if k in st.session_state: st.session_state.config[k.replace("in_", "retraite_") + ("_mensuel" if "app" in k else "")] = st.session_state[k]
        try: save_sheet("Config", pd.DataFrame(list(st.session_state.config.items()), columns=["Clé", "Valeur"]))
        except: pass

    with c_p1:
        annee_retraite = st.number_input("Année de départ (1er Janvier) ✍️", min_value=annee_en_cours+1, max_value=2100, value=2055, step=1)
        apport_mensuel = st.number_input("Apport mensuel d'aujourd'hui ($) ✍️", min_value=0.00, step=50.00, value=float(st.session_state.config.get("retraite_apport_mensuel", 250.0)), key="in_app", on_change=on_retraite_params_change)
    with c_p2:
        rendement_a = st.number_input("Performance Scénario A (%) ✍️", min_value=0.00, max_value=30.00, value=round(max(0.00, float(moy_brute_hist)), 2), step=0.01, help="Par défaut : moyenne de vos performances passées.")
        rendement_b = st.number_input("Performance Scénario B (%) ✍️", min_value=0.00, value=8.00, step=0.01)
    with c_p3:
        inflation_estimee = st.number_input("Inflation annuelle estimée (%) ✍️", min_value=0.00, value=2.00, step=0.01)
        taxe_plus_value = st.number_input("Fiscalité sur les retraits (Flat Tax) (%) ✍️", min_value=0.00, max_value=60.00, step=0.10, value=float(st.session_state.config.get("retraite_taxe", 30.0)), key="in_tax", on_change=on_retraite_params_change)
        
    st.info(f"💡 **Info :** Vos apports de {apport_mensuel:,.2f} $ augmenteront de {inflation_estimee:.2f} % chaque année dans le simulateur pour suivre l'évolution de votre salaire et de la vie.")
    st.divider()

    years_range = list(range(annee_en_cours, annee_retraite))
    cap_v_a = cap_v_b = capital_initial; gains_a = gains_b = 0.0; app_a = app_b = apport_mensuel
    inf_rate, r_a, r_b = inflation_estimee / 100.0, rendement_a / 100.0, rendement_b / 100.0
    r_a_m, r_b_m = (1 + r_a)**(1/12) - 1, (1 + r_b)**(1/12) - 1

    trajectory_data = []
    for y in years_range:
        for _ in range(12 if y > annee_en_cours else max(1, 13 - datetime.datetime.now().month)):
            int_a = (cap_v_a + gains_a) * r_a_m; gains_a += int_a; cap_v_a += app_a
            int_b = (cap_v_b + gains_b) * r_b_m; gains_b += int_b; cap_v_b += app_b
        app_a *= (1 + inf_rate); app_b *= (1 + inf_rate); years_diff = y - annee_en_cours + 1
        cap_a_nom, cap_b_nom = cap_v_a + gains_a, cap_v_b + gains_b
        cap_a_real, cap_b_real = cap_a_nom / ((1 + inf_rate)**years_diff), cap_b_nom / ((1 + inf_rate)**years_diff)
        trajectory_data.append({"Année": y, "Capital Net (Scénario A)": round(cap_a_real, 2), "Capital Net (Scénario B)": round(cap_b_real, 2)})

    tx_r = max(0.0, ((1.08)/(1+inf_rate))-1)
    st.subheader(f"🎯 Capital projeté au 1er Janvier {annee_retraite}"); colA, colB = st.columns(2)
    
    total_a = cap_v_a + gains_a; ratio_gains_a = gains_a / total_a if total_a > 0 else 0.0
    rente_br_a = cap_a_real * tx_r / 12; rente_net_a = rente_br_a * (1 - (ratio_gains_a * taxe_plus_value / 100.0))

    with colA:
        st.markdown(f"### Scénario A (Moyenne : {rendement_a:.2f} % / an)")
        afficher_montant_double("💰 Valeur Brute du Magot 🔒", cap_a_nom)
        afficher_montant_double("🛒 Valeur Nette (Pouvoir d'achat) 🔒", cap_a_real); st.write("")
        afficher_montant_double("Rente Mensuelle Nette (Avant impôts)", rente_br_a, couleur_valeur="#2ecc71")
        afficher_montant_double(f"Après Impôts ({taxe_plus_value:.1f}% sur {ratio_gains_a*100:.1f}% de gains)", rente_net_a, couleur_valeur="#e67e22", taille="medium")

    total_b = cap_v_b + gains_b; ratio_gains_b = gains_b / total_b if total_b > 0 else 0.0
    rente_br_b = cap_b_real * tx_r / 12; rente_net_b = rente_br_b * (1 - (ratio_gains_b * taxe_plus_value / 100.0))

    with colB:
        st.markdown(f"### Scénario B (Fixe : {rendement_b:.2f} % / an)")
        afficher_montant_double("💰 Valeur Brute du Magot 🔒", cap_b_nom)
        afficher_montant_double("🛒 Valeur Nette (Pouvoir d'achat) 🔒", cap_b_real); st.write("")
        afficher_montant_double("Rente Mensuelle Nette (Avant impôts)", rente_br_b, couleur_valeur="#3498db")
        afficher_montant_double(f"Après Impôts ({taxe_plus_value:.1f}% sur {ratio_gains_b*100:.1f}% de gains)", rente_net_b, couleur_valeur="#e67e22", taille="medium")

    if trajectory_data:
        st.divider(); st.subheader("📈 Évolution du Pouvoir d'Achat Réel (Capital Net)")
        df_traj_melted = pd.DataFrame(trajectory_data).melt(id_vars="Année", var_name="Scénario", value_name="Valeur Nette ($)")
        fig = px.line(df_traj_melted, x="Année", y="Valeur Nette ($)", color="Scénario", color_discrete_map={"Capital Net (Scénario A)": "#2ecc71", "Capital Net (Scénario B)": "#3498db"})
        fig.update_traces(line_shape='spline'); fig.update_layout(yaxis_title="Capital Net d'Inflation ($)", xaxis_title="Année", legend_title=""); st.plotly_chart(fig, use_container_width=True)

elif page_choisie == "🏛️ Fiscalité":
    st.title("🏛️ Simulateur Fiscal (Lecture Drive)")
    st.write("Cet outil lit instantanément votre feuille 'Transaction', choisit la meilleure imposition, et estime votre impôt.")

    df_t = st.session_state.transactions.copy()
    if 'Date_DT' not in df_t.columns: df_t['Date_DT'] = pd.to_datetime(df_t['Date'], dayfirst=True, errors='coerce')
    annees_dispos = sorted(df_t['Date_DT'].dropna().dt.year.unique().tolist(), reverse=True)
    if not annees_dispos: annees_dispos = [datetime.datetime.now().year]
    annee_fiscale = st.selectbox("📅 Sélectionner l'année des revenus (à déclarer l'année suivante) :", annees_dispos)
    st.divider()

    st.subheader("👤 1. Ma Situation Familiale & Professionnelle")
    def update_fiscal_config():
        keys_to_save = ["in_statut", "in_enf", "in_s1", "in_s2", "in_u1", "in_k1", "in_cv1", "in_r1", "in_u2", "in_k2", "in_cv2", "in_r2"]
        for key in keys_to_save:
            if key in st.session_state: st.session_state.config[key.replace("in_", "f_")] = st.session_state[key]
        try: save_sheet("Config", pd.DataFrame(list(st.session_state.config.items()), columns=["Clé", "Valeur"]))
        except Exception: pass

    c_sit1, c_sit2 = st.columns(2)
    with c_sit1:
        idx_statut = 0 if st.session_state.config.get("f_statut", "Célibataire / Divorcé(e) / Veuf(ve)") == "Célibataire / Divorcé(e) / Veuf(ve)" else 1
        statut = st.radio("Situation matrimoniale ✍️", ["Célibataire / Divorcé(e) / Veuf(ve)", "Marié(e) / Pacsé(e)"], index=idx_statut, key="in_statut", on_change=update_fiscal_config)
        enfants = st.number_input("Nombre d'enfants à charge ✍️", min_value=0, max_value=10, value=int(st.session_state.config.get("f_enf", 0)), step=1, key="in_enf", on_change=update_fiscal_config)
    mariage_actif = "Marié" in statut

    with c_sit2:
        st.markdown("**Vos revenus nets imposables (Vous)**")
        salaire_1 = st.number_input("Salaires, etc. en € (Déclarant 1) ✍️", min_value=0.0, value=float(st.session_state.config.get("f_s1", 30000.0)), step=1000.0, key="in_s1", on_change=update_fiscal_config)
        salaire_2 = 0.0
        if mariage_actif:
            st.markdown("**Vos revenus nets imposables (Conjoint)**")
            salaire_2 = st.number_input("Salaires, etc. en € (Déclarant 2) ✍️", min_value=0.0, value=float(st.session_state.config.get("f_s2", 0.0)), step=1000.0, key="in_s2", on_change=update_fiscal_config)
        salaire_total = salaire_1 + salaire_2

    st.markdown("---"); st.markdown("#### 🚗 Frais Professionnels (Frais Réels)"); st.write("Le logiciel calculera automatiquement si la déduction de vos frais réels est plus avantageuse que l'abattement standard de 10 %.")
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        use_frais_1 = st.checkbox("Déclarer aux frais réels (Vous)", value=bool(int(st.session_state.config.get("f_u1", 0))), key="in_u1", on_change=update_fiscal_config)
        frais_reels_1 = 0.0
        if use_frais_1:
            km_1 = st.number_input("Kilomètres annuels (Trajet pro) - Vous ✍️", min_value=0, max_value=100000, value=int(st.session_state.config.get("f_k1", 0)), step=1000, key="in_k1", on_change=update_fiscal_config)
            cv_1 = st.selectbox("Puissance du véhicule (CV) - Vous ✍️", [3, 4, 5, 6, 7], index=[3, 4, 5, 6, 7].index(int(st.session_state.config.get("f_cv1", 5))), key="in_cv1", on_change=update_fiscal_config)
            repas_1 = st.number_input("Jours de repas au travail - Vous ✍️", min_value=0, max_value=300, value=int(st.session_state.config.get("f_r1", 0)), step=10, key="in_r1", on_change=update_fiscal_config)
            frais_km_1 = calcul_frais_km(km_1, cv_1); frais_repas_1 = repas_1 * 5.35; frais_reels_1 = frais_km_1 + frais_repas_1
            st.info(f"💰 Frais Réels estimés (Vous) : **{frais_reels_1:,.2f} €**")

    frais_reels_2 = 0.0
    if mariage_actif:
        with col_f2:
            use_frais_2 = st.checkbox("Déclarer aux frais réels (Conjoint)", value=bool(int(st.session_state.config.get("f_u2", 0))), key="in_u2", on_change=update_fiscal_config)
            if use_frais_2:
                km_2 = st.number_input("Kilomètres annuels (Trajet pro) - Conjoint ✍️", min_value=0, max_value=100000, value=int(st.session_state.config.get("f_k2", 0)), step=1000, key="in_k2", on_change=update_fiscal_config)
                cv_2 = st.selectbox("Puissance du véhicule (CV) - Conjoint ✍️", [3, 4, 5, 6, 7], index=[3, 4, 5, 6, 7].index(int(st.session_state.config.get("f_cv2", 5))), key="in_cv2", on_change=update_fiscal_config)
                repas_2 = st.number_input("Jours de repas au travail - Conjoint ✍️", min_value=0, max_value=300, value=int(st.session_state.config.get("f_r2", 0)), step=10, key="in_r2", on_change=update_fiscal_config)
                frais_km_2 = calcul_frais_km(km_2, cv_2); frais_repas_2 = repas_2 * 5.35; frais_reels_2 = frais_km_2 + frais_repas_2
                st.info(f"💰 Frais Réels estimés (Conjoint) : **{frais_reels_2:,.2f} €**")

    st.divider()
    df_ventes = df_t[(df_t['Type'].str.lower().str.contains('vente')) & (df_t['Date_DT'].dt.year == annee_fiscale)].copy()
    rapport_fiscal = []

    for idx, row in df_ventes.iterrows():
        t = str(row['Ticker']).upper()
        if est_devise_liquide(t): continue
        qte = extraire_nombre(row['Quantité'])
        net_local = extraire_nombre(row['Montant Net'])
        pru_usd = extraire_nombre(row.get('PRU (Devise)', 0.0))
        devise = str(row.get('Devise', 'USD')).strip().upper()
        
        fx_to_usd = get_historical_usd_rate(devise, row['Date']); net_usd = net_local * fx_to_usd
        cout_vente_usd = pru_usd * qte; pv_usd = net_usd - cout_vente_usd
        fx_usd_to_eur = get_historical_fx("USD", row['Date']); pv_eur = pv_usd * fx_usd_to_eur
        is_crypto = "BTC" in t or "ETH" in t or t.endswith("USDT")
        
        rapport_fiscal.append({"Actif": t, "Date de vente": row['Date'], "Quantité vendue": qte, "PRU Moyen ($)": pru_usd, "Prix de revente net ($)": net_usd, "Plus-value ($)": pv_usd, "Devise initiale": devise, "Taux de change (USD vers EUR)": fx_usd_to_eur, "Plus-value (€)": pv_eur, "Catégorie": "Crypto" if is_crypto else "Action/ETF"})

    df_fiscal = pd.DataFrame(rapport_fiscal)
    st.subheader(f"📝 2. Détail des Ventes Boursières (Année {annee_fiscale})")
    
    if df_fiscal.empty:
        st.info(f"Aucune cession d'actifs (actions ou cryptos) détectée dans la feuille 'Transaction' pour l'année {annee_fiscale}.")
        plus_values_actions = moins_values_actions = plus_values_crypto = moins_values_crypto = 0.0
    else:
        st.write("Ce tableau lit les transactions déjà enregistrées et figées dans Google Sheets.")
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
                st.dataframe(df_actif.drop(columns=["Actif", "Catégorie"]), column_config={"PRU Moyen ($)": st.column_config.NumberColumn(format="$ %.5f"), "Prix de revente net ($)": st.column_config.NumberColumn(format="$ %.2f"), "Plus-value ($)": st.column_config.NumberColumn(format="$ %.2f"), "Taux de change (USD vers EUR)": st.column_config.NumberColumn(format="%.4f"), "Plus-value (€)": st.column_config.NumberColumn(format="%.2f €")}, use_container_width=True, hide_index=True)
                res_actif = df_actif["Plus-value (€)"].sum(); color_res = "green" if res_actif >= 0 else "red"
                st.markdown(f"*Bilan de l'année pour **{actif}** : <strong style='color:{color_res}'>{res_actif:+.2f} €</strong>*", unsafe_allow_html=True)

    bilan_net_actions = plus_values_actions - moins_values_actions
    bilan_net_crypto = plus_values_crypto - moins_values_crypto
    st.divider()

    parts = 1.0 if "Célibataire" in statut else 2.0
    if enfants == 1: parts += 0.5
    elif enfants == 2: parts += 1.0
    elif enfants >= 3: parts += 1.0 + (enfants - 2)

    deduction_1 = max(salaire_1 * 0.10, frais_reels_1); deduction_2 = max(salaire_2 * 0.10, frais_reels_2)
    revenu_net_1 = salaire_1 - deduction_1; revenu_net_2 = salaire_2 - deduction_2; revenu_base_net_global = revenu_net_1 + revenu_net_2

    impot_salaires_seuls = calcul_impot_ir(revenu_base_net_global, parts, statut, apply_decote=True)
    
    st.subheader("💡 3. Recommandation d'imposition & Prélèvement à la Source")
    if df_fiscal.empty or (plus_values_actions == 0 and moins_values_actions == 0):
        choix = "Aucun"; cout_pfu = 0.0; cout_bareme = 0.0
    elif bilan_net_actions <= 0:
        st.success("✅ **Bilan Négatif ou Nul :** Vous n'avez pas d'impôts à payer sur vos cessions boursières classiques cette année.")
        choix = "Aucun (Bilan négatif)"; cout_pfu = 0.0; cout_bareme = 0.0
    else:
        cout_pfu = bilan_net_actions * 0.30
        impot_avec_bourse = calcul_impot_ir(revenu_base_net_global + bilan_net_actions, parts, statut, apply_decote=True)
        surcout_ir = impot_avec_bourse - impot_salaires_seuls; prelevements_sociaux = bilan_net_actions * 0.172; cout_bareme = surcout_ir + prelevements_sociaux
        taux_moyen_bareme = (cout_bareme / bilan_net_actions) * 100
        if cout_bareme < cout_pfu:
            st.success("✅ **Le Barème Progressif est plus avantageux pour vos plus-values !**")
            st.write(f"Sur vos {bilan_net_actions:,.2f} € de plus-values nettes :\n- Avec la Flat Tax (30%) : l'impôt serait de **{cout_pfu:,.2f} €**.\n- Avec le Barème : l'impôt est de **{cout_bareme:,.2f} €** *(Taux effectif : {taux_moyen_bareme:.1f} %)*.")
            choix = "Barème"
        else:
            st.success("✅ **La Flat Tax (PFU) est plus avantageuse pour vos plus-values !**")
            st.write(f"Sur vos {bilan_net_actions:,.2f} € de plus-values nettes :\n- Avec le Barème, la hausse de vos revenus vous ferait basculer dans les tranches hautes, l'impôt serait de **{cout_bareme:,.2f} €**.\n- Avec la Flat Tax : l'impôt est plafonné à **{cout_pfu:,.2f} €**.")
            choix = "PFU"

    taux_commun = (impot_salaires_seuls / salaire_total * 100) if salaire_total > 0 else 0.0
    impot_theorique_1 = calcul_impot_ir(revenu_net_1, 1.0, "Célibataire", apply_decote=False)
    taux_perso_1 = (impot_theorique_1 / salaire_1 * 100) if salaire_1 > 0 else 0.0
    mensualite_perso_1 = (salaire_1 * (taux_perso_1 / 100)) / 12.0

    st.markdown("#### 📌 Bilan de vos impôts globaux estimés")
    st.write(f"L'impôt total de votre foyer sur les salaires s'élève à **{impot_salaires_seuls:,.2f} € / an**.")
    col_taux1, col_taux2 = st.columns(2)
    with col_taux1: st.info(f"👨‍👩‍👧‍👦 **Option 1 : Le Taux Commun**\n\nLe taux unique appliqué aux deux membres du foyer.\n\n**Taux estimé : {taux_commun:.1f} %**")
    if mariage_actif:
        with col_taux2: st.success(f"👤 **Option 2 : Le Taux Personnalisé (Vous)**\n\nLe taux propre à votre salaire brut.\n\n**Votre Taux : {taux_perso_1:.1f} %**\n\n*(Prélevé sur votre salaire : {mensualite_perso_1:,.2f} € / mois)*")
    if bilan_net_actions > 0:
        impot_bourse = cout_bareme if choix == "Barème" else cout_pfu
        st.markdown(f"> ⚠️ **Attention :** L'impôt supplémentaire sur vos plus-values boursières (**{impot_bourse:,.2f} €**) n'est pas prélevé tous les mois. Il sera à régler en une fois lors de la régularisation de septembre.")

    st.divider(); st.subheader("📝 4. Résumé pour votre déclaration d'impôts"); st.caption("⚠️ *Avertissement : Ce simulateur est une aide indicative.*")
    c_decl1, c_decl2 = st.columns(2)
    with c_decl1:
        st.markdown("### 🔹 Formulaire 3916 (Comptes étrangers)\n- **Case 8UU (sur la 2042) :** À cocher.\n- **Informations à fournir sur le 3916 :**\n  - *Intitulé :* Swissquote Bank SA\n  - *Adresse :* Chemin de la Crétaux 33, 1196 Gland, Suisse")
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
            st.markdown("- **Case 2OP** : **À cocher absolument**.") if choix == "Barème" else st.markdown("- **Case 2OP** : **À laisser DÉCOCHÉE**.")
        elif bilan_net_actions < 0: st.markdown(f"- **Case 3VH** (Moins-values nettes) : Indiquer **{abs(bilan_net_actions):,.0f} €**")
