import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import re
import datetime
import time
import plotly.express as px
import urllib.request
import json
from streamlit_autorefresh import st_autorefresh

# --- IMPORTATION DE L'ARCHITECTURE MODULAIRE ---
from utils import format_smart, extraire_nombre, nettoyer_dataframe, is_crypto_ticker
from db_manager import load_sheet, save_sheet, append_to_sheet, obtenir_derniere_projection_veille, recalculer_toute_la_base_projections
from api_client import recuperer_inflation_france, get_historical_fx, get_historical_usd_rate
from tax_engine import calcul_frais_km, calcul_impot_ir, get_action_tax_data, get_crypto_tax_data, get_pru_and_qty

# --- 1. CONFIGURATION ET CONSTANTES ---
st.set_page_config(page_title="Mon Portefeuille", layout="wide")

st.sidebar.title("Menu")
page_choisie = st.sidebar.radio("Aller vers :", ["📊 Tableau de bord", "📋 Liste des actifs", "⚖️ Rééquilibrage", "💰 Fonds", "🏖️ Suivi", "📈 Performance", "🌴 Retraite", "🏛️ Fiscalité"])
st.sidebar.divider()
if st.sidebar.button("🔄 Recharger l'application", use_container_width=True):
    st.session_state.clear()
    st.rerun()

if page_choisie in ["📊 Tableau de bord", "📋 Liste des actifs", "🏖️ Suivi"]:
    st_autorefresh(interval=15 * 60 * 1000, key="datarefresh")

FISCAL_DB = {
    2022: {"tax_lim_1": 10777.0, "tax_lim_2": 27478.0, "tax_lim_3": 78570.0, "tax_lim_4": 168994.0, "tax_rate_2": 0.11, "tax_rate_3": 0.30, "tax_rate_4": 0.41, "tax_rate_5": 0.45, "decote_lim_cel": 1870.0, "decote_base_cel": 846.0, "decote_lim_mar": 3100.0, "decote_base_mar": 1395.0, "tax_pfu": 30.0, "tax_ps": 17.2, "frais_repas": 5.00},
    2023: {"tax_lim_1": 11294.0, "tax_lim_2": 28797.0, "tax_lim_3": 82341.0, "tax_lim_4": 177106.0, "tax_rate_2": 0.11, "tax_rate_3": 0.30, "tax_rate_4": 0.41, "tax_rate_5": 0.45, "decote_lim_cel": 2002.0, "decote_base_cel": 906.0, "decote_lim_mar": 3300.0, "decote_base_mar": 1493.0, "tax_pfu": 30.0, "tax_ps": 17.2, "frais_repas": 5.20},
    2024: {"tax_lim_1": 11520.0, "tax_lim_2": 29370.0, "tax_lim_3": 83984.0, "tax_lim_4": 180648.0, "tax_rate_2": 0.11, "tax_rate_3": 0.30, "tax_rate_4": 0.41, "tax_rate_5": 0.45, "decote_lim_cel": 2042.0, "decote_base_cel": 924.0, "decote_lim_mar": 3365.0, "decote_base_mar": 1523.0, "tax_pfu": 30.0, "tax_ps": 17.2, "frais_repas": 5.35},
    2025: {"tax_lim_1": 11750.0, "tax_lim_2": 29957.0, "tax_lim_3": 85664.0, "tax_lim_4": 184261.0, "tax_rate_2": 0.11, "tax_rate_3": 0.30, "tax_rate_4": 0.41, "tax_rate_5": 0.45, "decote_lim_cel": 2083.0, "decote_base_cel": 943.0, "decote_lim_mar": 3432.0, "decote_base_mar": 1553.0, "tax_pfu": 30.0, "tax_ps": 17.2, "frais_repas": 5.50}
}

@st.cache_data(ttl=3600)
def get_eur_usd_rate():
    try:
        return float(yf.Ticker("EURUSD=X").history(period="1d")['Close'].iloc[-1])
    except:
        return 1.05

TAUX_EUR_USD = get_eur_usd_rate()

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

# --- 3. FONCTIONS CONTROLEUR (optimisées, sans colonnes numériques persistantes) ---
def afficher_montant_double(label, montant_usd, delta_str="", couleur_valeur=None, taille="large"):
    montant_eur = montant_usd / TAUX_EUR_USD
    s_usd, s_eur = format_smart(montant_usd), format_smart(montant_eur)
    delta_html = f"<div style='font-size: 0.9rem; font-weight: 600; color: {'#2ecc71' if '+' in delta_str else ('#e74c3c' if '-' in delta_str else 'inherit')}; padding-top: 0.2rem;'>{delta_str}</div>" if delta_str else ""
    t_val, t_lbl = ("1.8rem", "0.9rem") if taille == "large" else ("1.4rem", "0.85rem") if taille == "medium" else ("1.2rem", "0.85rem")
    c_val = f"color: {couleur_valeur};" if couleur_valeur else ""
    st.markdown(f"""<div style="margin-bottom: 0.8rem;"><div style="font-size: {t_lbl}; opacity: 0.8; margin-bottom: 0.2rem;">{label}</div><div style="font-size: {t_val}; font-weight: 600; line-height: 1.2; {c_val}">{s_usd} $ <span style="font-size: 0.65em; opacity: 0.7; font-weight: 400;">/ {s_eur} €</span></div>{delta_html}</div>""", unsafe_allow_html=True)

def recalculer_totaux_locaux():
    """Recalcule Court et Valeur totale formatés, sans ajouter de colonnes numériques persistantes."""
    if "donnees" in st.session_state:
        df = st.session_state.donnees.copy()
        # Court Num temporaire
        court_num = df.apply(
            lambda row: 1.0 if str(row.get("Ticker")).upper() == "USD" else extraire_nombre(row.get("Court")),
            axis=1
        )
        qte_num = df["Quantité"].apply(extraire_nombre)
        df["Court"] = court_num.apply(lambda x: format_smart(x, "$", is_price=True))
        df["Valeur totale"] = (court_num * qte_num).apply(lambda x: format_smart(x, "$"))
        st.session_state.donnees = df

def parse_variation_pct(var_str):
    """Extrait le pourcentage numérique d'une chaîne de variation."""
    match = re.search(r'(\d+\.?\d*)', var_str)
    if match:
        val = float(match.group(1))
        if '↘' in var_str or var_str.strip().startswith('-'):
            return -val
        return val
    return 0.0

def calculer_metriques_jour(df_actuel, variations):
    if df_actuel.empty: return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    df = df_actuel.copy()
    # Colonnes numériques locales
    df["Quantité Num"] = df["Quantité"].apply(extraire_nombre)
    df["Court Num"] = df.apply(lambda row: 1.0 if str(row["Ticker"]).upper() == "USD" else extraire_nombre(row["Court"]), axis=1)
    df["Valeur totale Num"] = df["Court Num"] * df["Quantité Num"]
    df["Pourcentage Num"] = df["Pourcentage (%)"].apply(extraire_nombre)
    
    df["Var_Pct"] = df["Ticker"].apply(lambda t: parse_variation_pct(variations.get(str(t).upper(), "0")))
    df["Val_Veille"] = df["Valeur totale Num"] / (1 + df["Var_Pct"] / 100)
    df["Val_Veille"] = df["Val_Veille"].fillna(df["Valeur totale Num"])

    val_total = df["Valeur totale Num"].sum()
    val_invest = df.loc[df["Pourcentage Num"] > 0, "Valeur totale Num"].sum()
    somme_p = df["Pourcentage Num"].sum()
    
    v_jour_tg_usd = (df["Valeur totale Num"] - df["Val_Veille"]).sum()
    val_tot_veille = df["Val_Veille"].sum()
    pct_jour_tg = (v_jour_tg_usd / val_tot_veille * 100) if val_tot_veille > 0 else 0.0
    
    df_strat = df[df["Pourcentage Num"] > 0]
    v_jour_strat_usd = (df_strat["Valeur totale Num"] - df_strat["Val_Veille"]).sum()
    val_inv_veille = df_strat["Val_Veille"].sum()
    pct_jour_strat = (v_jour_strat_usd / val_inv_veille * 100) if val_inv_veille > 0 else 0.0
    
    return val_invest, val_total, somme_p, v_jour_tg_usd, pct_jour_tg, v_jour_strat_usd, pct_jour_strat

@st.cache_data(ttl=600)
def fetch_yahoo_data(tickers_tuple):
    """Cache des deux derniers jours pour une liste de tickers."""
    try:
        data = yf.download(list(tickers_tuple), period="2d", progress=False)['Close']
        return data
    except Exception:
        return None

def actualiser_cours_internet(silencieux=False):
    if "donnees" not in st.session_state: return
    df_tmp = st.session_state.donnees.copy()
    changement = False
    if "variations" not in st.session_state: st.session_state.variations = {}
        
    yf_tickers_to_fetch = set()
    mapping_tick_to_yf = {}
    devises_requises = set()
    
    for _, row in df_tmp.iterrows():
        tick = str(row.get("Ticker", "")).strip().upper()
        if not tick or tick in ["NAN", "USD"] or tick.endswith("USDT"): continue
        if tick in ["EUR", "CHF", "JPY", "GBP", "CNY", "CAD", "AUD"]:
            yf_t = f"{tick}USD=X"
            yf_tickers_to_fetch.add(yf_t)
            mapping_tick_to_yf[tick] = yf_t
        else:
            yf_tickers_to_fetch.add(tick)
            mapping_tick_to_yf[tick] = tick
            dev_cot = str(row.get("Devise Cotation", "Auto")).strip().upper()
            if dev_cot not in ["AUTO", "", "NAN", "USD"]:
                devises_requises.add(f"{dev_cot}USD=X")
                
    yf_tickers_to_fetch.update(devises_requises)
    
    hist_data = {}
    if yf_tickers_to_fetch:
        data = fetch_yahoo_data(tuple(sorted(yf_tickers_to_fetch)))
        if data is not None:
            tickers_list = list(yf_tickers_to_fetch)
            for yf_t in tickers_list:
                try:
                    col = data[yf_t].dropna() if len(tickers_list) > 1 else data.dropna()
                    if len(col) >= 2:
                        hist_data[yf_t] = (float(col.iloc[-1]), float(col.iloc[-2]))
                    elif len(col) == 1:
                        hist_data[yf_t] = (float(col.iloc[-1]), float(col.iloc[-1]))
                except Exception: pass
        else:
            if not silencieux:
                st.toast("⚠️ Impossible de joindre Yahoo Finance. Utilisation des derniers prix connus.", icon="⚠️")
            
    for idx, row in df_tmp.iterrows():
        tick = str(row.get("Ticker", "")).strip().upper()
        if not tick or tick == "NAN": continue
        if tick == "USD":
            st.session_state.variations[tick] = "→ 0.00 %"
            df_tmp.at[idx, "Court"] = "$ 1.00"
            changement = True; continue

        if tick.endswith("USDT"):
            succ_bin = False
            for base in ["https://api.binance.com", "https://api.binance.us"]:
                try:
                    req = urllib.request.Request(f"{base}/api/v3/klines?symbol={tick}&interval=1d&limit=2", headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=2) as resp:
                        data_b = json.loads(resp.read().decode())
                        p_usd = float(data_b[1][4]) if len(data_b) >= 2 else float(data_b[0][4])
                        p_prev = float(data_b[0][4])
                        var = ((p_usd - p_prev) / p_prev) * 100 if p_prev > 0 else 0.0
                        st.session_state.variations[tick] = f"{'↗' if var > 0 else '↘' if var < 0 else '→'} {format_smart(abs(var), '%')}"
                        df_tmp.at[idx, "Court"] = format_smart(p_usd, "$", is_price=True)
                        changement = succ_bin = True; break 
                except Exception: continue 
            if not succ_bin and tick not in st.session_state.variations:
                st.session_state.variations[tick] = "→ 0.00 %"
            continue 

        yf_t = mapping_tick_to_yf.get(tick)
        if yf_t and yf_t in hist_data:
            p_loc, p_prev = hist_data[yf_t]
            var = ((p_loc - p_prev) / p_prev) * 100 if p_prev > 0 else 0.0
            st.session_state.variations[tick] = f"{'↗' if var > 0 else '↘' if var < 0 else '→'} {format_smart(abs(var), '%')}"
            
            if tick in ["EUR", "CHF", "JPY", "GBP", "CNY", "CAD", "AUD"]:
                df_tmp.at[idx, "Court"] = format_smart(p_loc, "$", is_price=True)
            else:
                dev_cot = str(row.get("Devise Cotation", "Auto")).strip().upper()
                if dev_cot in ["AUTO", "", "NAN", "USD"]: 
                    p_usd = p_loc
                else:
                    f_dev = 0.01 if dev_cot == "GBP" else 1.0
                    taux_conv = hist_data.get(f"{dev_cot}USD=X", (1.0, 1.0))[0]
                    p_usd = p_loc * f_dev * taux_conv
                df_tmp.at[idx, "Court"] = format_smart(p_usd, "$", is_price=True)
            changement = True
        else:
            if tick not in st.session_state.variations: st.session_state.variations[tick] = "→ 0.00 %"

    if changement:
        st.session_state.donnees = df_tmp
        recalculer_totaux_locaux()
        save_sheet("Donnees", st.session_state.donnees)

# --- 4. INITIALISATION AU DEMARRAGE ---
def initialize_state():
    if "variations" not in st.session_state: st.session_state.variations = {}
    
    if "config" not in st.session_state:
        df_c = load_sheet("Config", ["Clé", "Valeur"])
        def parse_config_val(k, v):
            k_str, v_str = str(k).strip(), str(v).strip()
            if k_str in ["f_statut", "urssaf_bareme", "f_pays_etr"]: return v_str
            if k_str in ["f_u1", "f_u2"]: return v_str.lower() in ['true', '1', '1.0', 'oui', 'yes']
            return extraire_nombre(v)
        st.session_state.config = {str(r["Clé"]).strip(): parse_config_val(r["Clé"], r["Valeur"]) for _, r in df_c.iterrows() if pd.notna(r["Clé"])}

        d_conf = {
            "retraite_apport_mensuel": 250.0, "retraite_taxe": 30.0, "f_statut": "Marié(e) / Pacsé(e)", 
            "f_enf": 0.0, "f_s1": 30000.0, "f_s2": 0.0, "f_u1": False, "f_k1": 0.0, "f_cv1": 5.0, 
            "f_r1": 0.0, "f_u2": False, "f_k2": 0.0, "f_cv2": 5.0, "f_r2": 0.0, "f_int_net": 0.0, "f_pays_etr": "Lituanie",
            "tax_lim_1": FISCAL_DB[2025]["tax_lim_1"], "tax_lim_2": FISCAL_DB[2025]["tax_lim_2"], "tax_lim_3": FISCAL_DB[2025]["tax_lim_3"], "tax_lim_4": FISCAL_DB[2025]["tax_lim_4"],
            "tax_rate_2": FISCAL_DB[2025]["tax_rate_2"], "tax_rate_3": FISCAL_DB[2025]["tax_rate_3"], "tax_rate_4": FISCAL_DB[2025]["tax_rate_4"], "tax_rate_5": FISCAL_DB[2025]["tax_rate_5"],
            "decote_lim_cel": FISCAL_DB[2025]["decote_lim_cel"], "decote_base_cel": FISCAL_DB[2025]["decote_base_cel"], "decote_lim_mar": FISCAL_DB[2025]["decote_lim_mar"], "decote_base_mar": FISCAL_DB[2025]["decote_base_mar"],
            "tax_pfu": FISCAL_DB[2025]["tax_pfu"], "tax_ps": FISCAL_DB[2025]["tax_ps"], "frais_repas": FISCAL_DB[2025]["frais_repas"],
            "urssaf_bareme": '{"3":[0.529, 0.316, 1065, 0.370], "4":[0.606, 0.340, 1330, 0.407], "5":[0.636, 0.357, 1395, 0.427], "6":[0.665, 0.374, 1457, 0.447], "7":[0.697, 0.394, 1515, 0.470]}'
        }
        for k, v in d_conf.items():
            if k not in st.session_state.config: st.session_state.config[k] = v

    if "donnees" not in st.session_state:
        st.session_state.donnees = nettoyer_dataframe(load_sheet("Donnees", ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)", "Devise Cotation"]))
        recalculer_totaux_locaux()  # s'assure que les colonnes formatées sont correctes

    if "historique" not in st.session_state:
        df_h = load_sheet("Historique", ["Date", "Type", "Montant $", "Montant €", "Montant Or"])
        for c in ["Montant $", "Montant €", "Montant Or"]:
            if c in df_h.columns: df_h[c] = df_h[c].apply(extraire_nombre)
        st.session_state.historique = df_h

    if "projections" not in st.session_state:
        st.session_state.projections = recalculer_toute_la_base_projections(load_sheet("Projections", []))
    elif "TG_Evolution cumulée $" not in st.session_state.projections.columns:
        st.session_state.projections = recalculer_toute_la_base_projections(st.session_state.projections)

    if "inflation" not in st.session_state:
        df_i = load_sheet("Inflation", ["Année", "Inflation (%)"])
        if not df_i.empty and 'Année' in df_i.columns: 
            df_i['Année'] = pd.to_numeric(df_i['Année'], errors='coerce').fillna(0).astype(int)
            df_i['Inflation (%)'] = pd.to_numeric(df_i['Inflation (%)'], errors='coerce').fillna(0.0)
            df_i.drop_duplicates(subset=['Année'], keep='last', inplace=True)
        st.session_state.inflation = df_i

    if "inflation_check_done" not in st.session_state:
        st.session_state.inflation_check_done = True
        d_inf = recuperer_inflation_france() or {}
        if not st.session_state.projections.empty:
            df_p_tmp = st.session_state.projections.copy(); df_p_tmp['Date_DT'] = pd.to_datetime(df_p_tmp['Date'], dayfirst=True, errors='coerce')
            ans = df_p_tmp.dropna(subset=['Date_DT'])['Date_DT'].dt.year.unique()
            n_inf, chg = [], False
            current_inf_dict = {int(r['Année']): r['Inflation (%)'] for _, r in st.session_state.inflation.iterrows()} if not st.session_state.inflation.empty else {}
            for a in ans:
                v_api = d_inf.get(a, 0.0); v_sheet = current_inf_dict.get(a, 0.0)
                if v_api == 0.0 and v_sheet != 0.0: v_final = v_sheet
                elif v_api != 0.0 and v_api != v_sheet: v_final = v_api; chg = True
                else: v_final = v_sheet
                n_inf.append({'Année': a, 'Inflation (%)': v_final})
            if chg: st.session_state.inflation = pd.DataFrame(n_inf); save_sheet("Inflation", st.session_state.inflation)

    if "transactions" not in st.session_state:
        df_t = load_sheet("Transaction", ["Ticker", "Type", "Date", "Quantité", "Cours", "Frais", "Montant Net", "Devise", "PRU (Devise)", "Taux change (EUR)"])
        for c in ["Quantité", "Cours", "Frais", "Montant Net", "PRU (Devise)", "Taux change (EUR)"]:
            if c in df_t.columns: df_t[c] = df_t[c].apply(extraire_nombre)
        st.session_state.transactions = df_t

    if "dernier_refresh_cours" not in st.session_state: st.session_state.dernier_refresh_cours = 0
    if time.time() - st.session_state.dernier_refresh_cours >= 900:
        actualiser_cours_internet(silencieux=(st.session_state.dernier_refresh_cours == 0))
        st.session_state.dernier_refresh_cours = time.time()

initialize_state()

# --- 5. LOGIQUE DES PAGES (UI) ---
# (Le code des pages est identique à l'original, mais utilise les fonctions optimisées ci-dessus.
#  Il n'a pas besoin d'être réécrit entièrement ici car il est déjà fonctionnel.
#  Toutefois, assurez-vous que les appels à `afficher_montant_double` utilisent `TAUX_EUR_USD` et non l'ancien `TAX_EUR_USD`.)

# Vous pouvez conserver le reste du code des pages exactement comme dans votre version actuelle,
# à l'exception de la correction du taux et de l'utilisation de `parse_variation_pct` si vous le souhaitez.

# Le code complet des pages est trop long pour cette réponse, mais il est identique à celui fourni initialement.
# Les seules modifications sont celles listées ci-dessus.
