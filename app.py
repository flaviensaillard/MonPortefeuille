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

# --- 3. CONNEXION À GOOGLE DRIVE / SHEETS ---
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
    st.error("Erreur de connexion à Google Sheets.")
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

# --- VARIABLES GLOBALES DE TAUX (Direct) ---
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
    for i, nom in enumerate(colonnes_base):
        if i < len(df_travail.columns): df_travail.rename(columns={df_travail.columns[i]: nom}, inplace=True)
    for col in ["Capital investi", "Actifs Stratégiques", "Total Global"]:
        df_travail[col] = df_travail[col].apply(extraire_nombre)
    df_travail['DT_TRI'] = pd.to_datetime(df_travail['Date'], dayfirst=True, errors='coerce')
    df_travail = df_travail.sort_values('DT_TRI').reset_index(drop=True)
    resultats = []
    c_twr = tg_twr = 1.0
    for i in range(len(df_travail)):
        row = df_travail.iloc[i].to_dict()
        cap, actifs, tg = row["Capital investi"], row["Actifs Stratégiques"], row["Total Global"]
        if i == 0:
            row["Evolution actifs $"] = 0.0 ; row["Evolution actifs %"] = 0.0
            row["Evolution cumulée $"] = actifs - cap
            row["Evolution cumulée %"] = ((actifs - cap) / cap * 100) if cap != 0 else 0.0
            c_twr *= (1 + ((actifs - cap) / cap if cap != 0 else 0.0))
            row["TG_Evolution cumulée $"] = tg - cap
            row["TG_Evolution cumulée %"] = ((tg - cap) / cap * 100) if cap != 0 else 0.0
            tg_twr *= (1 + ((tg - cap) / cap if cap != 0 else 0.0))
        else:
            prev = df_travail.iloc[i-1]
            diff_cap = cap - prev["Capital investi"]
            evo_usd = (actifs - prev["Actifs Stratégiques"]) - diff_cap
            row["Evolution actifs $"] = evo_usd
            row["Evolution actifs %"] = (evo_usd / prev["Actifs Stratégiques"] * 100) if prev["Actifs Stratégiques"] != 0 else 0.0
            row["Evolution cumulée $"] = actifs - cap
            row["Evolution cumulée %"] = ((actifs - cap) / cap * 100) if cap != 0 else 0.0
            c_twr *= (1 + (evo_usd / (prev["Actifs Stratégiques"] + diff_cap) if (prev["Actifs Stratégiques"] + diff_cap) != 0 else 0.0))
            evo_tg = (tg - prev["Total Global"]) - diff_cap
            row["TG_Evolution cumulée $"] = tg - cap
            row["TG_Evolution cumulée %"] = ((tg - cap) / cap * 100) if cap != 0 else 0.0
            tg_twr *= (1 + (evo_tg / (prev["Total Global"] + diff_cap) if (prev["Total Global"] + diff_cap) != 0 else 0.0))
        row["Score TWR %"], row["TG_Score TWR %"] = (c_twr - 1) * 100, (tg_twr - 1) * 100
        resultats.append(row)
    df_f = pd.DataFrame(resultats)
    if 'DT_TRI' in df_f.columns: df_f.drop(columns=['DT_TRI'], inplace=True)
    return df_f

def recalculer_totaux_locaux():
    if "donnees" in st.session_state:
        df = st.session_state.donnees.copy()
        for index, row in df.iterrows():
            c_num, q_num = extraire_nombre(row.get("Court", 0)), extraire_nombre(row.get("Quantité", 0))
            df.at[index, "Valeur totale"] = f"$ {round(c_num * q_num, 2):,.2f}"
            df.at[index, "Court"] = f"$ {c_num:.2f}"
        st.session_state.donnees = df

def actualiser_cours_internet(silencieux=False):
    if "donnees" in st.session_state:
        if not silencieux: st.toast("🔄 Actualisation des cours en direct...")
        df_temp = st.session_state.donnees.copy()
        changement = False
        taux_cache = {} 
        if "variations" not in st.session_state: st.session_state.variations = {}
        for index, row in df_temp.iterrows():
            tick = str(row.get("Ticker", "")).strip().upper()
            if tick != "" and tick != "NAN":
                success_bin = False
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
                                df_temp.at[index, "Court"] = f"$ {p_usd:.2f}"
                                changement = success_bin = True
                                break 
                        except: continue 
                if success_bin: continue 
                try:
                    asset = yf.Ticker(tick.replace("USDT", "-USD"))
                    inf = asset.fast_info
                    p_loc = float(inf.get('lastPrice', 0.0))
                    if p_loc == 0: p_loc = float(asset.history(period="1d")['Close'].iloc[-1])
                    p_prev = float(inf.get('previous_close', 0.0))
                    if p_prev == 0: p_prev = float(asset.history(period="5d")['Close'].iloc[-2])
                    var = ((p_loc - p_prev) / p_prev) * 100 if p_prev > 0 else 0.0
                    st.session_state.variations[tick] = f"{'↗' if var > 0 else '↘' if var < 0 else '→'} {var:+.2f} %"
                    dev = str(inf.get('currency', 'USD')).upper()
                    p_usd = p_loc * (0.01 if dev == "GBP" else 1.0)
                    if dev not in ["USD", "", "NONE"]:
                        if dev not in taux_cache:
                            try: taux_cache[dev] = float(yf.Ticker(f"{dev}USD=X").fast_info.get('lastPrice', 1.0))
                            except: taux_cache[dev] = 1.0
                        p_usd *= taux_cache[dev]
                    df_temp.at[index, "Court"] = f"$ {p_usd:.2f}"
                    changement = True
                except: continue
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

# --- FORMULES FISCALES ---
def calcul_frais_km(km, cv):
    coefs = {3:(0.529, 0.316, 1065, 0.370), 4:(0.606, 0.340, 1330, 0.407), 5:(0.636, 0.357, 1395, 0.427), 6:(0.665, 0.374, 1457, 0.447), 7:(0.697, 0.394, 1515, 0.470)}
    c = coefs.get(cv, coefs[7])
    return km * c[0] if km <= 5000 else (km * c[1] + c[2] if km <= 20000 else km * c[3])

def calcul_impot_ir(rev, parts, statut, apply_decote=True):
    qf = rev / parts
    imp = 0
    for lim, tx in [(11294,0), (28797,0.11), (82341,0.30), (177106,0.41), (9999999,0.45)]:
        prev_lim = 11294 if tx == 0.11 else 28797 if tx == 0.30 else 82341 if tx == 0.41 else 177106 if tx == 0.45 else 0
        if qf > prev_lim: imp += (min(qf, lim) - prev_lim) * tx
    imp *= parts
    if apply_decote:
        lim_d, base_d = (2002, 906) if "Célibataire" in statut else (3300, 1493)
        if imp <= lim_d: imp = max(0, imp - (base_d - (imp * 0.4525)))
    return 0.0 if imp < 61 else imp

# --- 5. CHARGEMENT INITIAL ---
if "config" not in st.session_state:
    df_c = load_sheet("Config", ["Clé", "Valeur"])
    st.session_state.config = {str(r["Clé"]): extraire_nombre(r["Valeur"]) for _, r in df_c.iterrows() if pd.notna(r["Clé"])}
for k, v in {"apport_dispo":0.0, "retraite_apport_mensuel":250.0, "retraite_taxe":30.0}.items():
    if k not in st.session_state.config: st.session_state.config[k] = v

if "donnees" not in st.session_state: st.session_state.donnees = nettoyer_dataframe(load_sheet("Donnees", []))
if "historique" not in st.session_state: st.session_state.historique = load_sheet("Historique", ["Date", "Type", "Montant $", "Montant €", "Montant Or"])
if "projections" not in st.session_state: st.session_state.projections = recalculer_toute_la_base_projections(load_sheet("Projections", []))
if "inflation" not in st.session_state: st.session_state.inflation = load_sheet("Inflation", ["Année", "Inflation (%)"])
if "transactions" not in st.session_state: 
    df_t = load_sheet("Transaction", ["Ticker", "Type", "Date", "Quantité", "Cours", "Frais", "Montant Net", "Devise", "PRU (Devise)", "Taux change (EUR)"])
    for c in ["Quantité", "Cours", "Frais", "Montant Net", "PRU (Devise)", "Taux change (EUR)"]: df_t[c] = df_t[c].apply(extraire_nombre)
    st.session_state.transactions = df_t

# --- AUTO-REFRESH COURS ---
if "dernier_refresh" not in st.session_state: st.session_state.dernier_refresh = 0
if time.time() - st.session_state.dernier_refresh >= 900:
    actualiser_cours_internet(silencieux=(st.session_state.dernier_refresh == 0))
    st.session_state.dernier_refresh = time.time()

# --- 6. NAVIGATION ---
st.sidebar.title("Menu")
page = st.sidebar.radio("Aller vers :", ["📊 Tableau de bord", "📋 Liste des actifs", "⚖️ Rééquilibrage", "💰 Fonds", "🏖️ Suivi", "📈 Performance", "🌴 Retraite", "🏛️ Fiscalité"])
if st.sidebar.button("🔄 Recharger l'application", use_container_width=True):
    st.session_state.clear()
    st.rerun()

# --- 7. PAGES ---
if page == "📊 Tableau de bord":
    st.title("📊 Vue d'ensemble")
    df_a, df_p = st.session_state.donnees, st.session_state.projections
    v_inv = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_a.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    v_tot = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_a.iterrows())
    cap = sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _, r in st.session_state.historique.iterrows())
    
    col1, col2 = st.columns(2)
    with col1: afficher_montant_double("Total Global", v_tot)
    with col2: afficher_montant_double("Actifs Stratégiques", v_inv)
    
    if not df_p.empty:
        fig = px.line(df_p, x='Date', y='Total Global', title="Évolution du Patrimoine")
        st.plotly_chart(fig, use_container_width=True)
    
    st.markdown("**🌍 Répartition par Type**")
    df_pie = df_a.copy()
    df_pie['Val'] = df_pie['Valeur totale'].apply(extraire_nombre)
    fig_p = px.pie(df_pie[df_pie['Val']>0], values='Val', names='Type', hole=0.4)
    st.plotly_chart(fig_p, use_container_width=True)

elif page == "📋 Liste des actifs":
    st.title("📋 Mes Actifs")
    df_e = st.data_editor(st.session_state.donnees, use_container_width=True, hide_index=True, num_rows="dynamic")
    if not df_e.equals(st.session_state.donnees):
        st.session_state.donnees = df_e
        recalculer_totaux_locaux()
        save_sheet("Donnees", st.session_state.donnees)
        st.rerun()

elif page == "⚖️ Rééquilibrage":
    st.title("⚖️ Rééquilibrage")
    app = st.number_input("Nouvel apport ($)", value=float(st.session_state.config["apport_dispo"]))
    df = st.session_state.donnees
    v_strat = sum(extraire_nombre(r["Valeur totale"]) for _, r in df.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    base = v_strat + app
    res = []
    for _, r in df.iterrows():
        cib = extraire_nombre(r["Pourcentage (%)"])/100
        if cib <= 0: continue
        act = extraire_nombre(r["Valeur totale"])
        diff = (base * cib) - act
        res.append({"Ticker": r["Ticker"], "Actuel": act, "Cible %": cib*100, "Action": f"{'ACHETER' if diff>0 else 'VENDRE'} ${abs(diff):,.2f}"})
    st.table(res)

elif page == "💰 Fonds":
    st.title("💰 Historique des Fonds")
    st.dataframe(st.session_state.historique, use_container_width=True, hide_index=True)

elif page == "🏖️ Suivi":
    st.title("🏖️ Suivi Temporel")
    st.dataframe(st.session_state.projections.sort_index(ascending=False), use_container_width=True, hide_index=True)

elif page == "📈 Performance":
    st.title("📈 Performance Annuelle")
    st.write("Analyse des rendements par année civile comparés à l'inflation.")
    if not st.session_state.projections.empty:
        df_perf = st.session_state.projections.copy()
        df_perf['Année'] = pd.to_datetime(df_perf['Date'], dayfirst=True).dt.year
        st.dataframe(df_perf.groupby('Année').last(), use_container_width=True)

elif page == "🌴 Retraite":
    st.title("🌴 Simulateur Retraite")
    c1, c2 = st.columns(2)
    age = c1.number_input("Année de départ", 2025, 2070, 2050)
    tx = c2.number_input("Rendement estimé (%)", 0.0, 15.0, 8.0)
    st.info("Simulation de capitalisation composée basée sur vos apports mensuels configurés.")

elif page == "🏛️ Fiscalité":
    st.title("🏛️ Fiscalité (Lecture Drive)")
    st.write("Calcul automatique des plus-values basé sur les données figées dans votre Google Sheet.")

    df_t = st.session_state.transactions
    years = sorted(pd.to_datetime(df_t['Date'], dayfirst=True, errors='coerce').dropna().dt.year.unique().tolist(), reverse=True)
    annee = st.selectbox("Année fiscale :", years if years else [2024])
    
    st.subheader("👤 Situation")
    c1, c2 = st.columns(2)
    statut = c1.radio("Statut", ["Célibataire", "Marié(e) / Pacsé(e)"])
    enf = c1.number_input("Enfants", 0, 10, 0)
    s1 = c2.number_input("Salaire Déclarant 1 (€)", 0, 200000, 30000)
    s2 = c2.number_input("Salaire Déclarant 2 (€)", 0, 200000, 0) if "Marié" in statut else 0.0

    # Frais Réels
    st.markdown("---")
    colf1, colf2 = st.columns(2)
    f1_km = colf1.number_input("Km Pro (Vous)", 0, 50000, 0)
    f1_cv = colf1.selectbox("CV (Vous)", [3,4,5,6,7], index=2)
    f2_km = colf2.number_input("Km Pro (Conjoint)", 0, 50000, 0) if s2 > 0 else 0
    f2_cv = colf2.selectbox("CV (Conjoint)", [3,4,5,6,7], index=2) if s2 > 0 else 7

    fr1 = max(s1 * 0.10, calcul_frais_km(f1_km, f1_cv) + (230 * 5.35 if f1_km > 0 else 0))
    fr2 = max(s2 * 0.10, calcul_frais_km(f2_km, f2_cv) + (230 * 5.35 if f2_km > 0 else 0))
    rev_net = (s1 - fr1) + (s2 - fr2)
    parts = (1 if "Cél" in statut else 2) + (0.5 if enf <= 2 else 0) * enf + (0.5 if enf >= 3 else 0)

    # Calcul Plus-values (Lecture seule)
    df_v = df_t[(df_t['Type'].str.lower().str.contains('vente')) & (pd.to_datetime(df_t['Date'], dayfirst=True).dt.year == annee)].copy()
    
    if df_v.empty:
        st.info("Aucune vente détectée pour cette année.")
        pv_actions = pv_crypto = 0.0
    else:
        df_v['PV_EUR'] = (df_v['Montant Net'] - (df_v['Quantité'] * df_v['PRU (Devise)'])) * df_v['Taux change (EUR)']
        df_v['Cat'] = df_v['Ticker'].apply(lambda x: "Crypto" if any(c in str(x).upper() for c in ["BTC","ETH","USDT"]) else "Action")
        
        st.subheader("📝 Détail des cessions")
        tabs = st.tabs(sorted(df_v['Ticker'].unique()))
        for i, t in enumerate(sorted(df_v['Ticker'].unique())):
            with tabs[i]:
                st.dataframe(df_v[df_v['Ticker']==t][['Date','Quantité','PRU (Devise)','Taux change (EUR)','PV_EUR']], hide_index=True)
        
        pv_actions = df_v[df_v['Cat']=="Action"]['PV_EUR'].sum()
        pv_crypto = df_v[df_v['Cat']=="Crypto"]['PV_EUR'].sum()

    impot_seul = calcul_impot_ir(rev_net, parts, statut)
    impot_total = calcul_impot_ir(rev_net + max(0, pv_actions), parts, statut)
    
    st.divider()
    st.subheader("💡 Bilan Global Estimé")
    
    tx_foyer = (impot_seul / (s1 + s2) * 100) if (s1+s2)>0 else 0.0
    imp_theorique_perso = calcul_impot_ir(s1 - fr1, 1.0, "Célibataire", False)
    tx_perso = (imp_theorique_perso / s1 * 100) if s1 > 0 else 0.0

    cres1, cres2 = st.columns(2)
    cres1.metric("Taux Commun (Foyer)", f"{tx_foyer:.1f} %")
    cres2.metric("Ton Taux Personnalisé", f"{tx_perso:.1f} %", help="Calculé sur ton seul revenu sans enfants.")
    
    st.write(f"Impôt mensuel prélevé sur ton salaire (Taux Perso) : **{ (s1 * tx_perso/100) / 12 :.2f} € / mois**")
    
    if pv_actions > 0:
        cout_bar = (impot_total - impot_seul) + (pv_actions * 0.172)
        cout_pfu = pv_actions * 0.30
        choix = "Barème" if cout_bar < cout_pfu else "Flat Tax"
        st.success(f"Conseil : Utilisez le **{choix}** pour vos actions (Économie : {abs(cout_bar - cout_pfu):.2f} €)")
