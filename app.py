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
from functools import lru_cache
from streamlit_autorefresh import st_autorefresh
import logging

# --- IMPORTATION DE L'ARCHITECTURE MODULAIRE ---
from utils import format_smart, extraire_nombre, nettoyer_dataframe, is_crypto_ticker
from db_manager import load_sheet, save_sheet, append_to_sheet, obtenir_derniere_projection_veille, recalculer_toute_la_base_projections
from api_client import recuperer_inflation_france
from tax_engine import calcul_frais_km, calcul_impot_ir, get_action_tax_data, get_crypto_tax_data, get_pru_and_qty, get_historical_fx, get_historical_usd_rate

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 1. CONFIGURATION ET CONSTANTES ---
st.set_page_config(page_title="Mon Portefeuille", layout="wide")

st.sidebar.title("Menu")
page_choisie = st.sidebar.radio("Aller vers :", [
    "📊 Tableau de bord", "📋 Liste des actifs", "⚖️ Rééquilibrage",
    "💰 Fonds", "🏖️ Suivi", "📈 Performance", "🌴 Retraite", "🏛️ Fiscalité"
])
st.sidebar.divider()
if st.sidebar.button("🔄 Recharger l'application", use_container_width=True):
    st.session_state.clear()
    st.rerun()

if page_choisie in ["📊 Tableau de bord", "📋 Liste des actifs", "🏖️ Suivi"]:
    st_autorefresh(interval=15 * 60 * 1000, key="datarefresh")

FISCAL_DB = {
    2022: {"tax_lim_1": 10777.0, "tax_lim_2": 27478.0, "tax_lim_3": 78570.0, "tax_lim_4": 168994.0,
           "tax_rate_2": 0.11, "tax_rate_3": 0.30, "tax_rate_4": 0.41, "tax_rate_5": 0.45,
           "decote_lim_cel": 1870.0, "decote_base_cel": 846.0, "decote_lim_mar": 3100.0, "decote_base_mar": 1395.0,
           "tax_pfu": 30.0, "tax_ps": 17.2, "frais_repas": 5.00},
    2023: {"tax_lim_1": 11294.0, "tax_lim_2": 28797.0, "tax_lim_3": 82341.0, "tax_lim_4": 177106.0,
           "tax_rate_2": 0.11, "tax_rate_3": 0.30, "tax_rate_4": 0.41, "tax_rate_5": 0.45,
           "decote_lim_cel": 2002.0, "decote_base_cel": 906.0, "decote_lim_mar": 3300.0, "decote_base_mar": 1493.0,
           "tax_pfu": 30.0, "tax_ps": 17.2, "frais_repas": 5.20},
    2024: {"tax_lim_1": 11520.0, "tax_lim_2": 29370.0, "tax_lim_3": 83984.0, "tax_lim_4": 180648.0,
           "tax_rate_2": 0.11, "tax_rate_3": 0.30, "tax_rate_4": 0.41, "tax_rate_5": 0.45,
           "decote_lim_cel": 2042.0, "decote_base_cel": 924.0, "decote_lim_mar": 3365.0, "decote_base_mar": 1523.0,
           "tax_pfu": 30.0, "tax_ps": 17.2, "frais_repas": 5.35},
    2025: {"tax_lim_1": 11750.0, "tax_lim_2": 29957.0, "tax_lim_3": 85664.0, "tax_lim_4": 184261.0,
           "tax_rate_2": 0.11, "tax_rate_3": 0.30, "tax_rate_4": 0.41, "tax_rate_5": 0.45,
           "decote_lim_cel": 2083.0, "decote_base_cel": 943.0, "decote_lim_mar": 3432.0, "decote_base_mar": 1553.0,
           "tax_pfu": 30.0, "tax_ps": 17.2, "frais_repas": 5.50}
}

# --- SYSTÈME DE RÉCUPÉRATION AUTOMATIQUE DES BARÈMES FISCAUX ---
@st.cache_data(ttl=86400 * 15, show_spinner=False)
def get_fiscal_bars_for_year_cached(year):
    return _fetch_fiscal_bars(year)

def get_fiscal_bars_for_year(year):
    try:
        bars = get_fiscal_bars_for_year_cached(year)
        return bars, bars.get('_source', 'Cache local'), bars.get('_fiabilite', 'Élevée')
    except:
        pass
    bars = _fetch_fiscal_bars(year)
    return bars, bars.get('_source', 'Estimation'), bars.get('_fiabilite', 'Moyenne')

def _fetch_fiscal_bars(year):
    bars = None
    source = "Inconnue"
    fiabilite = "Faible"
    try:
        url = f"https://api.gouv.fr/impots/bareme/{year}"
        req = urllib.request.Request(url, headers={'User-Agent': 'MonPortefeuille/1.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            bars = {
                "tax_lim_1": float(data['tranche1_plafond']), "tax_lim_2": float(data['tranche2_plafond']),
                "tax_lim_3": float(data['tranche3_plafond']), "tax_lim_4": float(data['tranche4_plafond']),
                "tax_rate_2": float(data['tranche2_taux'])/100, "tax_rate_3": float(data['tranche3_taux'])/100,
                "tax_rate_4": float(data['tranche4_taux'])/100, "tax_rate_5": float(data['tranche5_taux'])/100,
                "tax_pfu": 30.0, "tax_ps": 17.2, "frais_repas": float(data.get('forfait_repas', 5.50))
            }
            source = "API gouvernementale"; fiabilite = "Officielle"
    except: pass
    if bars is None and year in FISCAL_DB:
        bars = FISCAL_DB[year].copy(); source = "Base de données interne"; fiabilite = "Exacte (vérifiée)"
    if bars is None and year > max(FISCAL_DB.keys()):
        last_year = max(FISCAL_DB.keys()); base = FISCAL_DB[last_year].copy()
        try:
            inflation_data = recuperer_inflation_france() or {}
            avg_inflation = sum(inflation_data.values())/len(inflation_data) if inflation_data else 2.0
        except: avg_inflation = 2.0
        f = 1 + avg_inflation/100
        bars = {
            "tax_lim_1": round(base["tax_lim_1"]*f,0), "tax_lim_2": round(base["tax_lim_2"]*f,0),
            "tax_lim_3": round(base["tax_lim_3"]*f,0), "tax_lim_4": round(base["tax_lim_4"]*f,0),
            "tax_rate_2": base["tax_rate_2"], "tax_rate_3": base["tax_rate_3"],
            "tax_rate_4": base["tax_rate_4"], "tax_rate_5": base["tax_rate_5"],
            "decote_lim_cel": round(base["decote_lim_cel"]*f,0), "decote_base_cel": round(base["decote_base_cel"]*f,0),
            "decote_lim_mar": round(base["decote_lim_mar"]*f,0), "decote_base_mar": round(base["decote_base_mar"]*f,0),
            "tax_pfu": base["tax_pfu"], "tax_ps": base["tax_ps"], "frais_repas": round(base["frais_repas"]*f,2)
        }
        source = "Estimation (inflation)"; fiabilite = "Approximative"
    if bars is None:
        bars = FISCAL_DB.get(max(FISCAL_DB.keys()), FISCAL_DB[2025]).copy()
        source = "Fallback"; fiabilite = "Dégradée"
    bars['_source'] = source; bars['_fiabilite'] = fiabilite; bars['_year'] = year
    return bars

# --- FONCTIONS DE CALCUL PARTAGÉES ---
def calculer_performances_annuelles():
    """Calcule les performances annuelles et les stocke dans st.session_state."""
    if "perf_data" not in st.session_state:
        df_p = st.session_state.projections
        annee_en_cours = datetime.datetime.now().year
        if df_p.empty:
            st.session_state.perf_data = {"df_y": pd.DataFrame(), "moyenne_brute": 5.00, "moyenne_inflation": 2.00}
            return
        df_viz = df_p.copy(); df_viz['Date_DT'] = pd.to_datetime(df_viz['Date'], dayfirst=True, errors='coerce')
        df_viz = df_viz.dropna(subset=['Date_DT']).sort_values('Date_DT')
        df_viz['Année'] = df_viz['Date_DT'].dt.year
        df_lasts = df_viz.groupby('Année').last().reset_index()
        twr_fin_annee = dict(zip(df_lasts['Année'], df_lasts['Score TWR %']))
        perf_brutes = []
        for _, row in df_lasts.iterrows():
            annee_actuelle = row['Année']; twr_fin = row['Score TWR %']
            if (annee_actuelle-1) in twr_fin_annee:
                twr_debut = twr_fin_annee[annee_actuelle-1]
                perf_annee = (((1+twr_fin/100)/(1+twr_debut/100))-1)*100
            else: perf_annee = twr_fin
            perf_brutes.append(perf_annee)
        df_y = df_lasts.copy(); df_y['Année'] = df_y['Année'].astype(int)
        df_y['Performance brute (%)'] = perf_brutes
        jours_annee_1 = (df_viz[df_viz['Année']==df_viz['Date_DT'].min().year]['Date_DT'].max()-df_viz['Date_DT'].min()).days
        if jours_annee_1>0 and jours_annee_1<330 and not df_y[df_y['Année']==df_viz['Date_DT'].min().year].empty:
            p = df_y.loc[df_y['Année']==df_viz['Date_DT'].min().year,'Performance brute (%)'].values[0]
            df_y.loc[df_y['Année']==df_viz['Date_DT'].min().year,'Performance brute (%)'] = (((1+p/100)**(365.25/jours_annee_1))-1)*100
        df_inf = st.session_state.inflation.copy()
        if not df_inf.empty:
            df_inf['Année'] = df_inf['Année'].astype(int)
            df_y = df_y.merge(df_inf, on='Année', how='left').fillna({'Inflation (%)': 0.0})
        else: df_y['Inflation (%)'] = 0.0
        df_y['Performance nette (%)'] = (((1+df_y['Performance brute (%)']/100)/(1+df_y['Inflation (%)']/100))-1)*100
        df_y['Gains Nets ($)'] = df_y['Evolution cumulée $'] - df_y['Evolution cumulée $'].shift(1).fillna(0)
        df_hist = df_y[df_y['Année'] < annee_en_cours]
        moyenne_brute = round(df_hist['Performance brute (%)'].mean(),2) if not df_hist.empty else 5.00
        moyenne_inflation = round(df_hist['Inflation (%)'].mean(),2) if not df_hist.empty else 2.00
        st.session_state.perf_data = {"df_y": df_y, "moyenne_brute": moyenne_brute, "moyenne_inflation": moyenne_inflation}

def get_moyenne_performance_brute():
    if "perf_data" not in st.session_state: calculer_performances_annuelles()
    return st.session_state.perf_data["moyenne_brute"]

def get_moyenne_inflation():
    if "perf_data" not in st.session_state: calculer_performances_annuelles()
    return st.session_state.perf_data["moyenne_inflation"]

def get_perf_data():
    if "perf_data" not in st.session_state: calculer_performances_annuelles()
    return st.session_state.perf_data

@st.cache_data(ttl=3600)
def get_eur_usd_rate():
    for attempt in range(3):
        try:
            rate = float(yf.Ticker("EURUSD=X").history(period="1d")['Close'].iloc[-1])
            if rate > 0: return rate
        except Exception as e:
            logger.warning(f"Tentative {attempt+1} EUR/USD: {e}")
            if attempt < 2: time.sleep(2**attempt)
    return 1.05

TAUX_EUR_USD = get_eur_usd_rate()

# --- 2. SÉCURITÉ ---
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

# --- 3. FONCTIONS CONTROLEUR ---
@lru_cache(maxsize=256)
def extraire_nombre_cached(value_str): return extraire_nombre(value_str)

def afficher_montant_double(label, montant_usd, delta_str="", couleur_valeur=None, taille="large"):
    try:
        montant_eur = montant_usd / TAUX_EUR_USD
        s_usd, s_eur = format_smart(montant_usd), format_smart(montant_eur)
        delta_html = ""
        if delta_str:
            color = '#2ecc71' if '+' in delta_str else ('#e74c3c' if '-' in delta_str else 'inherit')
            delta_html = f"<div style='font-size:0.9rem;font-weight:600;color:{color};padding-top:0.2rem;'>{delta_str}</div>"
        t_val, t_lbl = {"large":("1.8rem","0.9rem"),"medium":("1.4rem","0.85rem"),"small":("1.2rem","0.85rem")}.get(taille,("1.8rem","0.9rem"))
        c_val = f"color:{couleur_valeur};" if couleur_valeur else ""
        st.markdown(f"""<div style="margin-bottom:0.8rem;"><div style="font-size:{t_lbl};opacity:0.8;margin-bottom:0.2rem;">{label}</div><div style="font-size:{t_val};font-weight:600;line-height:1.2;{c_val}">{s_usd} $ <span style="font-size:0.65em;opacity:0.7;font-weight:400;">/ {s_eur} €</span></div>{delta_html}</div>""", unsafe_allow_html=True)
    except Exception as e: logger.error(f"Erreur affichage: {e}")

def recalculer_totaux_locaux():
    if "donnees" not in st.session_state: return
    try:
        df = st.session_state.donnees.copy()
        court_num = df.apply(lambda row: 1.0 if str(row.get("Ticker")).upper()=="USD" else extraire_nombre_cached(str(row.get("Court","0"))), axis=1)
        qte_num = df["Quantité"].apply(lambda x: extraire_nombre_cached(str(x)))
        df["Court"] = court_num.apply(lambda x: format_smart(x,"$",is_price=True))
        df["Valeur totale"] = (court_num*qte_num).apply(lambda x: format_smart(x,"$"))
        st.session_state.donnees = df
    except Exception as e: logger.error(f"Erreur recalcul: {e}")

def parse_variation_pct(var_str):
    if not var_str or var_str=="N/A": return 0.0
    match = re.search(r'(\d+\.?\d*)', str(var_str))
    if match:
        val = float(match.group(1))
        if '↘' in str(var_str) or str(var_str).strip().startswith('-'): return -val
        return val
    return 0.0

def calculer_metriques_jour(df_actuel, variations):
    if df_actuel.empty: return 0.0,0.0,0.0,0.0,0.0,0.0,0.0
    try:
        df = df_actuel.copy()
        df["Quantité Num"] = df["Quantité"].apply(lambda x: extraire_nombre_cached(str(x)))
        df["Court Num"] = df.apply(lambda row: 1.0 if str(row["Ticker"]).upper()=="USD" else extraire_nombre_cached(str(row["Court"])), axis=1)
        df["Valeur totale Num"] = df["Court Num"]*df["Quantité Num"]
        df["Pourcentage Num"] = df["Pourcentage (%)"].apply(lambda x: extraire_nombre_cached(str(x)))
        df["Var_Pct"] = df["Ticker"].apply(lambda t: parse_variation_pct(variations.get(str(t).upper(),"0")))
        df["Val_Veille"] = df["Valeur totale Num"]/(1+df["Var_Pct"]/100)
        df["Val_Veille"] = df["Val_Veille"].fillna(df["Valeur totale Num"])
        val_total = df["Valeur totale Num"].sum()
        val_invest = df.loc[df["Pourcentage Num"]>0,"Valeur totale Num"].sum()
        somme_p = df["Pourcentage Num"].sum()
        v_jour_tg_usd = (df["Valeur totale Num"]-df["Val_Veille"]).sum()
        val_tot_veille = df["Val_Veille"].sum()
        pct_jour_tg = (v_jour_tg_usd/val_tot_veille*100) if val_tot_veille>0 else 0.0
        df_strat = df[df["Pourcentage Num"]>0]
        v_jour_strat_usd = (df_strat["Valeur totale Num"]-df_strat["Val_Veille"]).sum()
        val_inv_veille = df_strat["Val_Veille"].sum()
        pct_jour_strat = (v_jour_strat_usd/val_inv_veille*100) if val_inv_veille>0 else 0.0
        return val_invest,val_total,somme_p,v_jour_tg_usd,pct_jour_tg,v_jour_strat_usd,pct_jour_strat
    except Exception as e:
        logger.error(f"Erreur metriques: {e}")
        return 0.0,0.0,0.0,0.0,0.0,0.0,0.0

@st.cache_data(ttl=600)
def fetch_yahoo_data(tickers_tuple):
    for attempt in range(2):
        try:
            data = yf.download(list(tickers_tuple), period="2d", progress=False)['Close']
            if data is not None: return data
        except Exception as e:
            logger.warning(f"Yahoo tentative {attempt+1}: {e}")
            if attempt < 1: time.sleep(3)
    return None

def actualiser_cours_internet(silencieux=False):
    if "donnees" not in st.session_state: return
    df_tmp = st.session_state.donnees.copy()
    changement = False
    if "variations" not in st.session_state: st.session_state.variations = {}
    yf_tickers_to_fetch = set(); mapping_tick_to_yf = {}; devises_requises = set()
    for _, row in df_tmp.iterrows():
        tick = str(row.get("Ticker","")).strip().upper()
        if not tick or tick in ["NAN","USD"] or tick.endswith("USDT"): continue
        if tick in ["EUR","CHF","JPY","GBP","CNY","CAD","AUD"]:
            yf_t = f"{tick}USD=X"; yf_tickers_to_fetch.add(yf_t); mapping_tick_to_yf[tick] = yf_t
        else:
            yf_tickers_to_fetch.add(tick); mapping_tick_to_yf[tick] = tick
            dev_cot = str(row.get("Devise Cotation","Auto")).strip().upper()
            if dev_cot not in ["AUTO","","NAN","USD"]: devises_requises.add(f"{dev_cot}USD=X")
    yf_tickers_to_fetch.update(devises_requises)
    hist_data = {}
    if yf_tickers_to_fetch:
        data = fetch_yahoo_data(tuple(sorted(yf_tickers_to_fetch)))
        if data is not None:
            tickers_list = list(yf_tickers_to_fetch)
            for yf_t in tickers_list:
                try:
                    col = data[yf_t].dropna() if len(tickers_list)>1 else data.dropna()
                    if len(col)>=2: hist_data[yf_t] = (float(col.iloc[-1]),float(col.iloc[-2]))
                    elif len(col)==1: hist_data[yf_t] = (float(col.iloc[-1]),float(col.iloc[-1]))
                except: pass
        elif not silencieux: st.toast("⚠️ Impossible de joindre Yahoo Finance.", icon="⚠️")
    for idx, row in df_tmp.iterrows():
        tick = str(row.get("Ticker","")).strip().upper()
        if not tick or tick=="NAN": continue
        if tick=="USD":
            st.session_state.variations[tick] = "→ 0.00 %"; df_tmp.at[idx,"Court"] = "$ 1.00"; changement = True; continue
        if tick.endswith("USDT"):
            succ_bin = False
            for base in ["https://api.binance.com","https://api.binance.us"]:
                try:
                    req = urllib.request.Request(f"{base}/api/v3/klines?symbol={tick}&interval=1d&limit=2", headers={'User-Agent':'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=2) as resp:
                        data_b = json.loads(resp.read().decode())
                        p_usd = float(data_b[1][4]) if len(data_b)>=2 else float(data_b[0][4])
                        p_prev = float(data_b[0][4])
                        var = ((p_usd-p_prev)/p_prev)*100 if p_prev>0 else 0.0
                        st.session_state.variations[tick] = f"{'↗' if var>0 else '↘' if var<0 else '→'} {format_smart(abs(var),'%')}"
                        df_tmp.at[idx,"Court"] = format_smart(p_usd,"$",is_price=True)
                        changement = succ_bin = True; break
                except: continue
            if not succ_bin and tick not in st.session_state.variations: st.session_state.variations[tick] = "→ 0.00 %"
            continue
        yf_t = mapping_tick_to_yf.get(tick)
        if yf_t and yf_t in hist_data:
            p_loc, p_prev = hist_data[yf_t]
            var = ((p_loc-p_prev)/p_prev)*100 if p_prev>0 else 0.0
            st.session_state.variations[tick] = f"{'↗' if var>0 else '↘' if var<0 else '→'} {format_smart(abs(var),'%')}"
            if tick in ["EUR","CHF","JPY","GBP","CNY","CAD","AUD"]: df_tmp.at[idx,"Court"] = format_smart(p_loc,"$",is_price=True)
            else:
                dev_cot = str(row.get("Devise Cotation","Auto")).strip().upper()
                if dev_cot in ["AUTO","","NAN","USD"]: p_usd = p_loc
                else:
                    f_dev = 0.01 if dev_cot=="GBP" else 1.0
                    taux_conv = hist_data.get(f"{dev_cot}USD=X",(1.0,1.0))[0]
                    p_usd = p_loc*f_dev*taux_conv
                df_tmp.at[idx,"Court"] = format_smart(p_usd,"$",is_price=True)
            changement = True
        elif tick not in st.session_state.variations: st.session_state.variations[tick] = "→ 0.00 %"
    if changement:
        st.session_state.donnees = df_tmp; recalculer_totaux_locaux(); save_sheet("Donnees", st.session_state.donnees)

# --- 4. INITIALISATION AU DEMARRAGE ---
def initialize_state():
    if "variations" not in st.session_state: st.session_state.variations = {}
    if "config" not in st.session_state:
        try:
            df_c = load_sheet("Config", ["Clé","Valeur"])
            def parse_config_val(k,v):
                k_str, v_str = str(k).strip(), str(v).strip()
                if k_str in ["f_statut","urssaf_bareme","f_pays_etr"]: return v_str
                if k_str in ["f_u1","f_u2"]: return v_str.lower() in ['true','1','1.0','oui','yes']
                return extraire_nombre(v)
            st.session_state.config = {str(r["Clé"]).strip(): parse_config_val(r["Clé"],r["Valeur"]) for _,r in df_c.iterrows() if pd.notna(r["Clé"])}
            d_conf = {
                "retraite_apport_mensuel":250.0,"retraite_taxe":30.0,"f_statut":"Marié(e) / Pacsé(e)",
                "f_enf":0.0,"f_s1":30000.0,"f_s2":0.0,"f_u1":False,"f_k1":0.0,"f_cv1":5.0,
                "f_r1":0.0,"f_u2":False,"f_k2":0.0,"f_cv2":5.0,"f_r2":0.0,"f_int_net":0.0,"f_pays_etr":"Lituanie",
                "tax_lim_1":FISCAL_DB[2025]["tax_lim_1"],"tax_lim_2":FISCAL_DB[2025]["tax_lim_2"],
                "tax_lim_3":FISCAL_DB[2025]["tax_lim_3"],"tax_lim_4":FISCAL_DB[2025]["tax_lim_4"],
                "tax_rate_2":FISCAL_DB[2025]["tax_rate_2"],"tax_rate_3":FISCAL_DB[2025]["tax_rate_3"],
                "tax_rate_4":FISCAL_DB[2025]["tax_rate_4"],"tax_rate_5":FISCAL_DB[2025]["tax_rate_5"],
                "decote_lim_cel":FISCAL_DB[2025]["decote_lim_cel"],"decote_base_cel":FISCAL_DB[2025]["decote_base_cel"],
                "decote_lim_mar":FISCAL_DB[2025]["decote_lim_mar"],"decote_base_mar":FISCAL_DB[2025]["decote_base_mar"],
                "tax_pfu":FISCAL_DB[2025]["tax_pfu"],"tax_ps":FISCAL_DB[2025]["tax_ps"],
                "frais_repas":FISCAL_DB[2025]["frais_repas"],
                "urssaf_bareme":'{"3":[0.529,0.316,1065,0.370],"4":[0.606,0.340,1330,0.407],"5":[0.636,0.357,1395,0.427],"6":[0.665,0.374,1457,0.447],"7":[0.697,0.394,1515,0.470]}'
            }
            for k,v in d_conf.items():
                if k not in st.session_state.config: st.session_state.config[k] = v
        except Exception as e: logger.error(f"Config: {e}")
    if "donnees" not in st.session_state:
        try:
            st.session_state.donnees = nettoyer_dataframe(load_sheet("Donnees",["Ticker","Type","Quantité","Court","Valeur totale","Pourcentage (%)","Devise Cotation"]))
            recalculer_totaux_locaux()
        except Exception as e: logger.error(f"Donnees: {e}")
    else: recalculer_totaux_locaux()
    if "historique" not in st.session_state:
        try:
            df_h = load_sheet("Historique",["Date","Type","Montant $","Montant €","Montant Or"])
            for c in ["Montant $","Montant €","Montant Or"]:
                if c in df_h.columns: df_h[c] = df_h[c].apply(extraire_nombre)
            st.session_state.historique = df_h
        except Exception as e: logger.error(f"Historique: {e}")
    if "projections" not in st.session_state:
        try: st.session_state.projections = recalculer_toute_la_base_projections(load_sheet("Projections",[]))
        except Exception as e: logger.error(f"Projections: {e}")
    elif "TG_Evolution cumulée $" not in st.session_state.projections.columns:
        try: st.session_state.projections = recalculer_toute_la_base_projections(st.session_state.projections)
        except Exception as e: logger.error(f"Recalcul projections: {e}")
    if "inflation" not in st.session_state:
        try:
            df_i = load_sheet("Inflation",["Année","Inflation (%)"])
            if not df_i.empty and 'Année' in df_i.columns:
                df_i['Année'] = pd.to_numeric(df_i['Année'],errors='coerce').fillna(0).astype(int)
                df_i['Inflation (%)'] = pd.to_numeric(df_i['Inflation (%)'],errors='coerce').fillna(0.0)
                df_i.drop_duplicates(subset=['Année'],keep='last',inplace=True)
            st.session_state.inflation = df_i
        except Exception as e: logger.error(f"Inflation: {e}")
    if "inflation_check_done" not in st.session_state:
        st.session_state.inflation_check_done = True
        try:
            d_inf = recuperer_inflation_france() or {}
            if not st.session_state.projections.empty:
                df_p_tmp = st.session_state.projections.copy(); df_p_tmp['Date_DT'] = pd.to_datetime(df_p_tmp['Date'],dayfirst=True,errors='coerce')
                ans = df_p_tmp.dropna(subset=['Date_DT'])['Date_DT'].dt.year.unique()
                n_inf, chg = [], False
                current_inf_dict = {int(r['Année']):r['Inflation (%)'] for _,r in st.session_state.inflation.iterrows()} if not st.session_state.inflation.empty else {}
                for a in ans:
                    v_api = d_inf.get(a,0.0); v_sheet = current_inf_dict.get(a,0.0)
                    if v_api==0.0 and v_sheet!=0.0: v_final = v_sheet
                    elif v_api!=0.0 and v_api!=v_sheet: v_final = v_api; chg = True
                    else: v_final = v_sheet
                    n_inf.append({'Année':a,'Inflation (%)':v_final})
                if chg: st.session_state.inflation = pd.DataFrame(n_inf); save_sheet("Inflation",st.session_state.inflation)
        except Exception as e: logger.error(f"Inflation check: {e}")
    if "transactions" not in st.session_state:
        try:
            df_t = load_sheet("Transaction",["Ticker","Type","Date","Quantité","Cours","Frais","Montant Net","Devise","PRU (Devise)","Taux change (EUR)"])
            for c in ["Quantité","Cours","Frais","Montant Net","PRU (Devise)","Taux change (EUR)"]:
                if c in df_t.columns: df_t[c] = df_t[c].apply(extraire_nombre)
            st.session_state.transactions = df_t
        except Exception as e: logger.error(f"Transactions: {e}")
    if "perf_data" in st.session_state: del st.session_state.perf_data
    calculer_performances_annuelles()
    if "dernier_refresh_cours" not in st.session_state: st.session_state.dernier_refresh_cours = 0
    if time.time()-st.session_state.dernier_refresh_cours >= 900:
        try:
            actualiser_cours_internet(silencieux=(st.session_state.dernier_refresh_cours==0))
            st.session_state.dernier_refresh_cours = time.time()
        except Exception as e: logger.error(f"Refresh cours: {e}")

initialize_state()

# --- 5. LOGIQUE DES PAGES (UI) ---

if page_choisie == "📊 Tableau de bord":
    st.title("📊 Vue d'ensemble de mon Patrimoine")
    df_actuel, df_p = st.session_state.donnees, st.session_state.projections
    val_invest, val_total, somme_p, v_jour_tg_usd, pct_jour_tg, v_jour_strat_usd, pct_jour_strat = calculer_metriques_jour(df_actuel, st.session_state.variations)
    cap_actuel = sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _,r in st.session_state.historique.iterrows())
    df_p_live = recalculer_toute_la_base_projections(pd.concat([df_p, pd.DataFrame([{"Date":datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"),"Capital investi":cap_actuel,"Actifs Stratégiques":val_invest,"Total Global":val_total}])], ignore_index=True))
    
    delta = p_delta = delta_tg = p_delta_tg = 0.0
    if not df_p.empty:
        df_d = df_p.copy(); df_d['Date_DT'] = pd.to_datetime(df_d['Date'],dayfirst=True,errors='coerce'); df_d = df_d.dropna(subset=['Date_DT']).sort_values('Date_DT')
        if not df_d.empty:
            df_past = df_d[df_d['Date_DT'] <= pd.Timestamp.now()-pd.DateOffset(years=1)]
            row_ref = df_past.iloc[-1] if not df_past.empty else df_d.iloc[0]
            v_ref_strat, v_ref_tg = extraire_nombre(row_ref["Actifs Stratégiques"]), extraire_nombre(row_ref["Total Global"])
            delta, delta_tg = val_invest-v_ref_strat, val_total-v_ref_tg
            if v_ref_strat>0: p_delta = (delta/v_ref_strat)*100
            if v_ref_tg>0: p_delta_tg = (delta_tg/v_ref_tg)*100

    besoin_req = False
    if val_invest>0:
        if (df_actuel["Pourcentage (%)"].apply(extraire_nombre)>0).any():
            for _,r in df_actuel[df_actuel["Pourcentage (%)"].apply(extraire_nombre)>0].iterrows():
                cib = extraire_nombre(r["Pourcentage (%)"])/100
                act = extraire_nombre(r["Valeur totale"])
                if abs((val_invest*cib)-act)>=1000 and abs((act/val_invest*100)-(cib*100))>=2.0: besoin_req = True; break

    st.subheader("⚙️ 1. Pilotage & Statut")
    c_btn, c_stat = st.columns([1,2])
    with c_btn:
        if st.button("🔄 Actualiser les cours", use_container_width=True):
            with st.spinner("📡 Connexion aux marchés..."): actualiser_cours_internet(False)
            st.rerun()
    with c_stat:
        if besoin_req: st.warning("⚠️ **Rééquilibrage nécessaire**")
        else: st.success("✅ **Équilibré**")
    st.divider()

    photo_veille = obtenir_derniere_projection_veille()
    delta_global_txt = delta_strat_txt = ""
    if photo_veille:
        try:
            val_global_veille = float(photo_veille["Total Global"]); val_strat_veille = float(photo_veille["Actifs Stratégiques"])
            if val_global_veille>0:
                diff_val = val_total-val_global_veille
                delta_global_txt = f"{diff_val:+.2f} $ ({(diff_val/val_global_veille)*100:+.2f}%) Aujourd'hui"
            if val_strat_veille>0:
                diff_strat_val = val_invest-val_strat_veille
                delta_strat_txt = f"{diff_strat_val:+.2f} $ ({(diff_strat_val/val_strat_veille)*100:+.2f}%) Aujourd'hui"
        except: pass

    st.subheader("🌍 2. Total Global")
    c_tg, _ = st.columns(2)
    with c_tg:
        afficher_montant_double("Total Global", val_total, delta_str=delta_global_txt)
        st.markdown(f"<div style='margin-top:-0.5rem;margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if delta_tg>=0 else '📉'} Performance 1 an : <strong style='color:{'#2ecc71' if delta_tg>=0 else '#e74c3c'}'>{format_smart(delta_tg,'$',force_sign=True)} ({format_smart(p_delta_tg,'%',force_sign=True)})</strong></span></div>", unsafe_allow_html=True)
    
    if not df_p.empty:
        df_v_tg = df_p_live.copy(); df_v_tg['Date_DT'] = pd.to_datetime(df_v_tg['Date'],dayfirst=True,errors='coerce')
        df_v_tg = df_v_tg.dropna(subset=['Date_DT']).sort_values('Date_DT').reset_index(drop=True)
        st.markdown("**📈 Évolution & Performance globale**")
        cf1, cf2 = st.columns(2)
        f_tg = cf1.radio("Période :",["Depuis le début","Depuis 1 an","Depuis le début de l'année","Depuis le début du mois"],horizontal=True,key="f_tg")
        m_tg = cf2.radio("Affichage :",["Rendement Absolu (ROI)","Score TWR (Talent)"],horizontal=True,key="f_tg_mode")
        n = pd.Timestamp.now()
        if f_tg=="Depuis 1 an": df_v_tg = df_v_tg[df_v_tg['Date_DT']>=(n-pd.DateOffset(years=1))]
        elif f_tg=="Depuis le début de l'année": df_v_tg = df_v_tg[df_v_tg['Date_DT']>=pd.Timestamp(year=n.year-1,month=12,day=31)]
        elif f_tg=="Depuis le début du mois": df_v_tg = df_v_tg[df_v_tg['Date_DT']>=n.replace(day=1,hour=0,minute=0,second=0,microsecond=0)]
        if not df_v_tg.empty:
            df_v_tg.set_index('Date_DT',inplace=True)
            d_usd = df_v_tg['TG_Evolution cumulée $'].iloc[-1]-df_v_tg['TG_Evolution cumulée $'].iloc[0]
            pct = (d_usd/df_v_tg['Total Global'].iloc[0]*100) if df_v_tg['Total Global'].iloc[0]>0 else 0.0
            md, mf = 1+df_v_tg['TG_Score TWR %'].iloc[0]/100, 1+df_v_tg['TG_Score TWR %'].iloc[-1]/100
            twr_p = ((mf/md)-1)*100 if md!=0 else 0.0
            cg1, cg2 = st.columns([1,3])
            with cg1:
                if "ROI" in m_tg: afficher_montant_double("Gains nets globaux",df_v_tg['TG_Evolution cumulée $'].iloc[-1],f"{format_smart(d_usd,'$',force_sign=True)} ({format_smart(pct,'%',force_sign=True)} sur la période)",taille="medium")
                else: st.metric("Score TWR Global (%)",f"{format_smart(df_v_tg['TG_Score TWR %'].iloc[-1],'%',force_sign=True)}",f"{format_smart(twr_p,'%',force_sign=True)} (sur la période)")
            with cg2:
                fig_lt = px.line(df_v_tg.reset_index(),x='Date_DT',y='TG_Evolution cumulée $' if "ROI" in m_tg else 'TG_Score TWR %')
                fig_lt.update_traces(line_shape='spline')
                fig_lt.update_layout(xaxis_title="",yaxis_title="",margin=dict(l=0,r=0,t=10,b=0),showlegend=False)
                st.plotly_chart(fig_lt,use_container_width=True)
        st.markdown("**🌍 Répartition du Patrimoine**")
        cp1, _ = st.columns(2)
        with cp1:
            df_p_tg = df_actuel.copy(); df_p_tg['Val'] = df_p_tg['Valeur totale'].apply(extraire_nombre)
            df_pie_tg = df_p_tg[df_p_tg['Val']>0].groupby('Type')['Val'].sum().reset_index()
            if not df_pie_tg.empty:
                fig_tg = px.pie(df_pie_tg,values='Val',names='Type',color='Type',color_discrete_map={"🛢️ Action":"#e74c3c","📜 Obligation":"#3498db","💰 Or":"#f1c40f","₿ Crypto":"#9b59b6","💵 Cash":"#2ecc71","🏦 Cash réserve":"#f39c12"},hole=0.4)
                fig_tg.update_traces(textposition='inside',textinfo='percent+label')
                fig_tg.update_layout(showlegend=False,margin=dict(t=0,b=0,l=0,r=0))
                st.plotly_chart(fig_tg,use_container_width=True)
    st.divider()

    st.subheader("🎯 3. Actifs Stratégiques")
    c_st, _ = st.columns(2)
    with c_st:
        afficher_montant_double("Actifs Stratégiques",val_invest,delta_str=delta_strat_txt)
        st.markdown(f"<div style='margin-top:-0.5rem;margin-bottom:1rem;'><span style='font-size:1.1em;'>{'📈' if delta>=0 else '📉'} Performance 1 an : <strong style='color:{'#2ecc71' if delta>=0 else '#e74c3c'}'>{format_smart(delta,'$',force_sign=True)} ({format_smart(p_delta,'%',force_sign=True)})</strong></span></div>",unsafe_allow_html=True)
    if df_p.empty: st.info("Aucune donnée.")
    else:
        df_v_s = df_p_live.copy(); df_v_s['Date_DT'] = pd.to_datetime(df_v_s['Date'],dayfirst=True,errors='coerce')
        df_v_s = df_v_s.dropna(subset=['Date_DT']).sort_values('Date_DT').reset_index(drop=True)
        st.markdown("**📈 Évolution & Performance de la stratégie**")
        cf1, cf2 = st.columns(2)
        f_s = cf1.radio("Période :",["Depuis le début","Depuis 1 an","Depuis le début de l'année","Depuis le début du mois"],horizontal=True,key="f_s")
        m_s = cf2.radio("Affichage :",["Rendement Absolu (ROI)","Score TWR (Talent)"],horizontal=True,key="f_s_mode")
        n = pd.Timestamp.now()
        if f_s=="Depuis 1 an": df_v_s = df_v_s[df_v_s['Date_DT']>=(n-pd.DateOffset(years=1))]
        elif f_s=="Depuis le début de l'année": df_v_s = df_v_s[df_v_s['Date_DT']>=pd.Timestamp(year=n.year-1,month=12,day=31)]
        elif f_s=="Depuis le début du mois": df_v_s = df_v_s[df_v_s['Date_DT']>=n.replace(day=1,hour=0,minute=0,second=0,microsecond=0)]
        if not df_v_s.empty:
            df_v_s.set_index('Date_DT',inplace=True)
            d_usd = df_v_s['Evolution cumulée $'].iloc[-1]-df_v_s['Evolution cumulée $'].iloc[0]
            pct = (d_usd/df_v_s['Actifs Stratégiques'].iloc[0]*100) if df_v_s['Actifs Stratégiques'].iloc[0]>0 else 0.0
            md, mf = 1+df_v_s['Score TWR %'].iloc[0]/100, 1+df_v_s['Score TWR %'].iloc[-1]/100
            twr_p = ((mf/md)-1)*100 if md!=0 else 0.0
            cg1, cg2 = st.columns([1,3])
            with cg1:
                if "ROI" in m_s: afficher_montant_double("Gains nets de la stratégie",df_v_s['Evolution cumulée $'].iloc[-1],f"{format_smart(d_usd,'$',force_sign=True)} ({format_smart(pct,'%',force_sign=True)} sur la période)",taille="medium")
                else: st.metric("Score TWR Stratégique (%)",f"{format_smart(df_v_s['Score TWR %'].iloc[-1],'%',force_sign=True)}",f"{format_smart(twr_p,'%',force_sign=True)} (sur la période)")
            with cg2:
                fig_ls = px.line(df_v_s.reset_index(),x='Date_DT',y='Evolution cumulée $' if "ROI" in m_s else 'Score TWR %')
                fig_ls.update_traces(line_shape='spline')
                fig_ls.update_layout(xaxis_title="",yaxis_title="",margin=dict(l=0,r=0,t=10,b=0),showlegend=False)
                st.plotly_chart(fig_ls,use_container_width=True)
    st.markdown("**🎯 Répartition détaillée de la stratégie**")
    df_st = df_actuel[df_actuel['Pourcentage (%)'].apply(extraire_nombre)>0].copy(); df_st['Val'] = df_st['Valeur totale'].apply(extraire_nombre)
    cp1, cp2 = st.columns(2)
    with cp1:
        d_p1 = df_st[df_st['Val']>0].groupby('Type')['Val'].sum().reset_index()
        if not d_p1.empty:
            f1 = px.pie(d_p1,values='Val',names='Type',color='Type',color_discrete_map={"🛢️ Action":"#e74c3c","📜 Obligation":"#3498db","💰 Or":"#f1c40f","₿ Crypto":"#9b59b6","💵 Cash":"#2ecc71","🏦 Cash réserve":"#f39c12"},hole=0.4)
            f1.update_traces(textposition='inside',textinfo='percent+label')
            f1.update_layout(showlegend=False,margin=dict(t=0,b=0,l=0,r=0))
            st.plotly_chart(f1,use_container_width=True)
    with cp2:
        if not df_st[df_st['Val']>0].empty:
            f2 = px.pie(df_st[df_st['Val']>0],values='Val',names='Ticker',hole=0.4)
            f2.update_traces(textposition='inside',textinfo='percent+label')
            f2.update_layout(showlegend=False,margin=dict(t=0,b=0,l=0,r=0))
            st.plotly_chart(f2,use_container_width=True)
    st.divider(); st.subheader("🏖️ 4. Liberté Financière")
    cr1, cr2 = st.columns(2)
    with cr1: inf = st.slider("Inflation cible à déduire (%) ✍️",0.0,15.0,2.0,0.1,key="dash_infl")
    with cr2:
        tx_r = ((1+0.08)/(1+(inf/100.0)))-1
        afficher_montant_double("Rente Mensuelle Nette",(val_invest*max(0.0,tx_r))/12.0,couleur_valeur="#3498db")

elif page_choisie == "📋 Liste des actifs":
    st.title("📋 Liste de mes actifs")
    st.write("Modifiez l'allocation cible. **Quantité verrouillée** pour les investissements.")
    df_actuel = st.session_state.donnees.copy()
    val_invest, val_total, somme_p, _, _, _, _ = calculer_metriques_jour(df_actuel, st.session_state.variations)
    c1, c2, c3 = st.columns(3)
    with c1: afficher_montant_double("Actifs Stratégiques",val_invest)
    with c2: afficher_montant_double("Total Global",val_total)
    with c3:
        ec = round(100-somme_p,2); c_info = '#2ecc71' if ec==0 else '#e74c3c'
        st.markdown(f"<div style='margin-bottom:0.8rem;'><div style='font-size:0.9rem;opacity:0.8;'>Répartition Cible</div><div style='font-size:1.8rem;font-weight:600;'>{format_smart(somme_p,'%')}</div><div style='font-size:0.9rem;font-weight:600;color:{c_info};'>{'✅ Cible atteinte' if ec==0 else f'⚠️ {format_smart(abs(ec),'%')} manquant/en trop'}</div></div>",unsafe_allow_html=True)
    st.divider()
    if st.button("🔄 Actualiser les cours",use_container_width=True):
        with st.spinner("📡 Connexion..."): actualiser_cours_internet(False)
        st.rerun()
    df_actuel['Var. Jour 🔒'] = df_actuel['Ticker'].apply(lambda x: st.session_state.variations.get(str(x).upper(),"→ 0.00 %"))
    c_act_locked = {
        "Ticker":st.column_config.TextColumn("Ticker ✍️"),"Type":st.column_config.SelectboxColumn("Type ✍️",options=["🛢️ Action","📜 Obligation","💰 Or","₿ Crypto","💵 Cash","🏦 Cash réserve"]),
        "Devise Cotation":st.column_config.TextColumn("Devise Live ✍️"),"Court":st.column_config.TextColumn("Court 🔒",disabled=True),
        "Quantité":st.column_config.NumberColumn("Quantité 🔒",disabled=True),"Valeur totale":st.column_config.TextColumn("Valeur totale 🔒",disabled=True),
        "Pourcentage (%)":st.column_config.NumberColumn("Cible % ✍️"),"Var. Jour 🔒":st.column_config.TextColumn("Var. Jour 🔒",disabled=True)
    }
    c_act_unlocked = c_act_locked.copy(); c_act_unlocked["Quantité"] = st.column_config.NumberColumn("Quantité ✍️",disabled=False)
    def c_var(v): return 'color:#2ecc71' if "↗" in str(v) or "+" in str(v) else ('color:#e74c3c' if "↘" in str(v) or "-" in str(v) else 'color:#95a5a6')
    m_dev = df_actuel['Type'].astype(str).str.contains("Cash",na=False)
    d_c = ["Ticker","Type","Devise Cotation","Court","Quantité","Valeur totale","Pourcentage (%)","Var. Jour 🔒"]
    st.markdown("### 📈 Actifs d'Investissement")
    res_i = st.data_editor(df_actuel[~m_dev][d_c].style.map(c_var,subset=["Var. Jour 🔒"]),key="ei",column_config=c_act_locked,use_container_width=True,hide_index=True,num_rows="dynamic")
    st.markdown("### 💵 Liquidités")
    res_d = st.data_editor(df_actuel[m_dev][d_c].style.map(c_var,subset=["Var. Jour 🔒"]),key="ed",column_config=c_act_unlocked,use_container_width=True,hide_index=True,num_rows="dynamic")
    new_df = pd.concat([res_i,res_d],ignore_index=True)
    if not new_df[["Ticker","Type","Quantité","Pourcentage (%)","Devise Cotation"]].equals(st.session_state.donnees[["Ticker","Type","Quantité","Pourcentage (%)","Devise Cotation"]]):
        st.session_state.donnees = new_df; recalculer_totaux_locaux(); save_sheet("Donnees",st.session_state.donnees); st.rerun()

elif page_choisie == "⚖️ Rééquilibrage":
    st.title("⚖️ Rééquilibrage & Transactions")
    if st.button("🔄 Actualiser les cours",use_container_width=True):
        with st.spinner("📡 Connexion..."): actualiser_cours_internet(False)
        st.rerun()
    st.write("")
    with st.expander("➕ Enregistrer une transaction"):
        with st.form("new_trans"):
            c1,c2,c3 = st.columns(3); t_d = c1.date_input("Date")
            lt = sorted(st.session_state.donnees['Ticker'].dropna().unique().tolist()); lt.insert(0,"➕ Nouvel actif...")
            t_sel = c2.selectbox("Actif (Ticker)",lt)
            t_t = c2.text_input("Nouveau Ticker") if t_sel=="➕ Nouvel actif..." else t_sel
            t_ty = c3.selectbox("Type",["Achat","Vente"])
            c4,c5,c6 = st.columns(3)
            t_q = c4.number_input("Quantité",min_value=0.0,format="%.6f")
            t_c = c5.number_input("Cours unitaire",min_value=0.0,format="%.6f")
            t_f = c6.number_input("Frais",min_value=0.0,format="%.6f")
            t_dev = st.selectbox("Devise",["USD","EUR","CHF","JPY","GBP","CNY","CAD","AUD"])
            if st.form_submit_button("🔨 Valider"):
                try:
                    if t_t.strip()=="" or t_q<=0 or t_c<=0: st.error("❌ Vérifiez les champs.")
                    else:
                        t_cl = t_t.upper().strip()
                        m_n = round((t_q*t_c)+t_f if t_ty=="Achat" else (t_q*t_c)-t_f,6)
                        fx = get_historical_fx(t_dev,t_d.strftime("%Y-%m-%d"),strict=True)
                        c_pru_usd, c_qty = get_pru_and_qty(t_cl,st.session_state.transactions)
                        if t_ty=="Achat":
                            net_usd = m_n*get_historical_usd_rate(t_dev,t_d.strftime("%Y-%m-%d"),strict=True)
                            new_qty = c_qty+t_q
                            r_pru_usd = round(((c_pru_usd*c_qty)+net_usd)/new_qty,6) if new_qty>0 else 0.0
                        else: r_pru_usd = c_pru_usd
                        nr = {"Ticker":t_cl,"Type":t_ty,"Date":t_d.strftime("%d/%m/%Y"),"Quantité":t_q,"Cours":t_c,"Frais":t_f,"Montant Net":m_n,"Devise":t_dev,"PRU (Devise)":r_pru_usd,"Taux change (EUR)":fx}
                        append_to_sheet("Transaction",nr)
                        st.session_state.transactions = pd.concat([st.session_state.transactions,pd.DataFrame([nr])],ignore_index=True)
                        df_d = st.session_state.donnees.copy()
                        if t_cl not in df_d['Ticker'].values:
                            df_d = pd.concat([df_d,pd.DataFrame([{"Ticker":t_cl,"Type":"₿ Crypto" if is_crypto_ticker(t_cl) else "🛢️ Action","Quantité":0.0,"Court":"$ 0.00","Valeur totale":"$ 0.00","Pourcentage (%)":0.0,"Devise Cotation":"Auto"}])],ignore_index=True)
                        idx = df_d.index[df_d['Ticker']==t_cl].tolist()[0]
                        df_d.at[idx,"Quantité"] = max(0.0,extraire_nombre(df_d.at[idx,"Quantité"])+(t_q if t_ty=="Achat" else -t_q))
                        if t_dev not in df_d['Ticker'].values:
                            df_d = pd.concat([df_d,pd.DataFrame([{"Ticker":t_dev,"Type":"💵 Cash","Quantité":0.0,"Court":"$ 0.00","Valeur totale":"$ 0.00","Pourcentage (%)":0.0,"Devise Cotation":"Auto"}])],ignore_index=True)
                        i_c = df_d.index[df_d['Ticker']==t_dev].tolist()[0]
                        df_d.at[i_c,"Quantité"] = max(0.0,extraire_nombre(df_d.at[i_c,"Quantité"])+(-m_n if t_ty=="Achat" else m_n))
                        st.session_state.donnees = nettoyer_dataframe(df_d); recalculer_totaux_locaux(); save_sheet("Donnees",st.session_state.donnees)
                        st.success("✅ Transaction enregistrée !"); time.sleep(1); st.rerun()
                except ValueError as e: st.error(str(e))
    st.divider(); st.subheader("⚖️ Analyse de l'allocation")
    df = st.session_state.donnees
    c_usd = sum(extraire_nombre(r["Valeur totale"]) for _,r in df[df["Type"]=="💵 Cash"].iterrows())
    base = sum(extraire_nombre(r["Valeur totale"]) for _,r in df.iterrows() if extraire_nombre(r["Pourcentage (%)"])>0)+c_usd
    if base>0:
        st.info(f"💡 Liquidités disponibles : **{format_smart(c_usd,'$')}**")
        res = []
        for _,r in df.iterrows():
            t = str(r["Ticker"]).upper(); cib = extraire_nombre(r["Pourcentage (%)"])/100
            if cib<=0: continue
            act, p = extraire_nombre(r["Valeur totale"]), extraire_nombre(r["Court"])
            d = (base*cib)-act; q = d/p if p>0 else 0
            ecart_absolu_pct = (act/base*100)-(cib*100)
            besoin = abs(ecart_absolu_pct)>=2.0 and abs(d)>=1000.0
            action_txt = f"{'🟢 ACHETER' if d>0 else '🔴 VENDRE'} {format_smart(abs(d),'$')}" if besoin else f"✅ ÉQUILIBRÉ ({format_smart(abs(d),'$')})"
            current_pru_usd, _ = get_pru_and_qty(t,st.session_state.transactions)
            res.append({"Ticker 🔒":t,"PRU ($) 🔒":format_smart(current_pru_usd,"$",is_price=True),"Var. Jour 🔒":st.session_state.variations.get(t,"→ 0.00 %"),"Perf. Globale 🔒":format_smart(((p/current_pru_usd)-1)*100,"%",force_sign=True) if current_pru_usd>0 and p>0 else "N/A","Actuel ($) 🔒":format_smart(act,"$"),"Écart (%) 🔒":format_smart(ecart_absolu_pct,"%",force_sign=True),"Action 🔒":action_txt,"Qté (+/-) 🔒":f"({'+ ' if q>0.000001 else '- ' if q<-0.000001 else ''}{format_smart(abs(q),is_price=True)})"})
        def cr(v): return 'color:#2ecc71' if "↗" in str(v) or "ACHETER" in str(v) or "+" in str(v) else ('color:#e74c3c' if "↘" in str(v) or "VENDRE" in str(v) or "-" in str(v) else 'color:#95a5a6')
        st.dataframe(pd.DataFrame(res).style.map(cr,subset=["Var. Jour 🔒","Action 🔒","Qté (+/-) 🔒","Perf. Globale 🔒"]),use_container_width=True,hide_index=True)

elif page_choisie == "💰 Fonds":
    st.title("💰 Fonds")
    st.write("Déclarez vos apports de capital.")
    with st.expander("➕ Nouveau mouvement"):
        with st.form("f_m"):
            d_m = st.date_input("Date ✍️"); t_m = st.radio("Type ✍️",["Ajout de fond propre","Retrait"],horizontal=True)
            m_s = st.number_input("Montant ✍️",min_value=0.00,format="%.2f"); d_s = st.selectbox("Devise ✍️",["$","€"])
            if st.form_submit_button("Valider"):
                try:
                    o_px = float(yf.Ticker("GC=F").fast_info.get('lastPrice',2000.0))
                    m_usd = m_s if d_s=="$" else m_s*TAUX_EUR_USD; m_eur = m_s if d_s=="€" else m_s/TAUX_EUR_USD
                    ancien_total = sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _,r in st.session_state.historique.iterrows())
                    nl = {"Date":d_m.strftime("%d/%m/%Y"),"Type":t_m,"Montant $":m_usd,"Montant €":m_eur,"Montant Or":m_usd/o_px,"Total_Apports_nets":ancien_total+(m_usd if t_m=="Ajout de fond propre" else -m_usd)}
                    append_to_sheet("Historique",nl)
                    st.session_state.historique = pd.concat([st.session_state.historique,pd.DataFrame([nl])],ignore_index=True)
                    dev = "USD" if d_s=="$" else "EUR"
                    df_d = st.session_state.donnees.copy()
                    if dev not in df_d['Ticker'].values:
                        df_d = pd.concat([df_d,pd.DataFrame([{"Ticker":dev,"Type":"💵 Cash","Quantité":0.0,"Court":"$ 0.00","Valeur totale":"$ 0.00","Pourcentage (%)":0.0,"Devise Cotation":"Auto"}])],ignore_index=True)
                    idx_c = df_d.index[df_d['Ticker']==dev].tolist()[0]
                    df_d.at[idx_c,"Quantité"] = max(0.0,extraire_nombre(df_d.at[idx_c,"Quantité"])+(m_s if t_m=="Ajout de fond propre" else -m_s))
                    st.session_state.donnees = nettoyer_dataframe(df_d); recalculer_totaux_locaux(); save_sheet("Donnees",st.session_state.donnees)
                    st.success("✅ Mouvement enregistré !"); time.sleep(1); st.rerun()
                except ValueError as e: st.error(str(e))
    afficher_montant_double("Total Apports nets",sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _,r in st.session_state.historique.iterrows()))
    if not st.session_state.historique.empty:
        d_v = st.session_state.historique.copy(); d_v.columns = [f"{c} 🔒" for c in d_v.columns]; d_v['DT'] = pd.to_datetime(d_v['Date 🔒'],dayfirst=True,errors='coerce')
        for c,s in [("Montant $ 🔒","$"),("Montant € 🔒","€"),("Montant Or 🔒","oz")]:
            if c in d_v.columns: d_v[c] = d_v[c].apply(lambda x: format_smart(x,s))
        st.dataframe(d_v.sort_values('DT',ascending=False).drop(columns=['DT']),use_container_width=True,hide_index=True)

elif page_choisie == "🏖️ Suivi":
    st.title("🏖️ Suivi & Évolution")
    if not st.session_state.projections.empty:
        df_v = st.session_state.projections.copy()
        df_v['Date_Propre'] = df_v['Date'].astype(str).str.slice(0,10)
        df_v['DT'] = pd.to_datetime(df_v['Date_Propre'],dayfirst=True,errors='coerce').fillna(pd.Timestamp.now())
        df_v = df_v.sort_values('DT',ascending=False)
        df_v['Date'] = df_v['DT'].dt.strftime('%d/%m/%Y')
        df_v = df_v.drop(columns=['DT','Date_Propre'])
        for c in ["Capital investi","Actifs Stratégiques","Total Global","Evolution actifs $","Evolution cumulée $","TG_Evolution cumulée $"]:
            if c in df_v.columns: df_v[c] = df_v[c].apply(lambda x: format_smart(x,"$",force_sign=("Evolution" in c)))
        for c in ["Evolution actifs %","Evolution cumulée %","Score TWR %","TG_Evolution cumulée %","TG_Score TWR %"]:
            if c in df_v.columns: df_v[c] = df_v[c].apply(lambda x: format_smart(x,"%",force_sign=True))
        st.dataframe(df_v,column_config={c:st.column_config.TextColumn(c+" 🔒") for c in df_v.columns},use_container_width=True,hide_index=True)

elif page_choisie == "📈 Performance":
    st.title("📈 Performances Annuelles & Inflation")
    perf_data = get_perf_data()
    df_y = perf_data["df_y"].copy()
    if df_y.empty: st.info("Aucune donnée.")
    else:
        try: or_px = float(yf.Ticker("GC=F").fast_info.get('lastPrice',2000.0))
        except: or_px = 2000.0
        df_y['Valeur Bilan (Or)'] = df_y['Actifs Stratégiques']/or_px
        st.subheader("📊 Moyennes Historiques")
        df_hist = df_y[df_y['Année']<datetime.datetime.now().year].copy()
        if not df_hist.empty:
            c_m1,c_m2,c_m3,c_m4 = st.columns(4)
            c_m1.metric("Perf. Brute",format_smart(perf_data["moyenne_brute"],"%",force_sign=True))
            c_m2.metric("Inflation",format_smart(perf_data["moyenne_inflation"],"%"))
            c_m3.metric("Perf. Nette",format_smart(df_hist['Performance nette (%)'].mean(),"%",force_sign=True))
            with c_m4: afficher_montant_double("Gains / An",df_hist['Gains Nets ($)'].mean(),taille="medium")
        else: st.info("Historique insuffisant.")
        st.divider(); st.write("Résultats par année civile.")
        df_display = df_y[['Année','Performance brute (%)','Inflation (%)','Performance nette (%)','Gains Nets ($)','Actifs Stratégiques','Valeur Bilan (Or)']].copy()
        df_display.rename(columns={'Actifs Stratégiques':'Valeur Bilan ($)'},inplace=True)
        df_display['Année'] = df_display['Année'].astype(str)
        df_sorted = df_display.sort_values(by='Année',ascending=False).reset_index(drop=True)
        for c in ["Performance brute (%)","Inflation (%)","Performance nette (%)"]: df_sorted[c] = df_sorted[c].apply(lambda x: format_smart(x,"%"))
        for c in ["Gains Nets ($)","Valeur Bilan ($)"]: df_sorted[c] = df_sorted[c].apply(lambda x: format_smart(x,"$"))
        df_sorted["Valeur Bilan (Or)"] = df_sorted["Valeur Bilan (Or)"].apply(lambda x: format_smart(x,"oz"))
        st.dataframe(df_sorted,column_config={c:st.column_config.TextColumn(c+" 🔒") for c in df_sorted.columns},hide_index=True,use_container_width=True)
        st.divider(); st.subheader("📊 Comparaison Brute vs Nette")
        df_chart = df_sorted.sort_values(by='Année',ascending=True)[['Année','Performance brute (%)','Performance nette (%)']].copy()
        df_chart['Performance brute (%)'] = df_chart['Performance brute (%)'].str.replace(' %','').astype(float)
        df_chart['Performance nette (%)'] = df_chart['Performance nette (%)'].str.replace(' %','').astype(float)
        df_chart = df_chart.melt(id_vars='Année',var_name='Type',value_name='Rentabilité (%)')
        df_chart['Type'] = df_chart['Type'].replace({'Performance brute (%)':"Brute",'Performance nette (%)':"Nette"})
        fig_bar = px.bar(df_chart,x='Année',y='Rentabilité (%)',color='Type',barmode='group',color_discrete_map={"Brute":"#3498db","Nette":"#2ecc71"},text_auto='.2f')
        fig_bar.update_layout(yaxis_title="Rentabilité (%)",xaxis_title="",legend=dict(orientation="h",yanchor="bottom",y=-0.2,xanchor="center",x=0.5))
        st.plotly_chart(fig_bar,use_container_width=True)

elif page_choisie == "🌴 Retraite":
    st.title("🌴 Simulateur d'Indépendance Financière")
    df_actuel = st.session_state.donnees
    capital_initial = sum(extraire_nombre(r["Valeur totale"]) for _,r in df_actuel.iterrows() if extraire_nombre(r["Pourcentage (%)"])>0)
    annee_en_cours = datetime.datetime.now().year
    moy_brute_hist = get_moyenne_performance_brute()
    moy_inflation_hist = get_moyenne_inflation()

    st.subheader("⚙️ Paramètres"); c_p1,c_p2,c_p3 = st.columns(3)
    def on_retraite_params_change():
        for k in ["in_app","in_tax"]:
            if k in st.session_state: st.session_state.config[k.replace("in_","retraite_")+("_mensuel" if "app" in k else "")] = st.session_state[k]
        try: save_sheet("Config",pd.DataFrame(list(st.session_state.config.items()),columns=["Clé","Valeur"]))
        except: pass

    with c_p1:
        annee_retraite = st.number_input("Année de départ ✍️",min_value=annee_en_cours+1,max_value=2100,value=2055,step=1)
        apport_mensuel = st.number_input("Apport mensuel ($) ✍️",min_value=0.00,step=50.00,value=float(st.session_state.config.get("retraite_apport_mensuel",250.0)),key="in_app",on_change=on_retraite_params_change)
    with c_p2:
        st.markdown(f"""
        <div style="margin-bottom:0.8rem;">
            <div style="font-size:0.9rem;opacity:0.8;margin-bottom:0.2rem;">Scénario A (%) 🔒</div>
            <div style="font-size:1.4rem;font-weight:600;line-height:1.2;color:#2ecc71;">
                {format_smart(moy_brute_hist,'%')} <span style="font-size:0.65em;opacity:0.7;font-weight:400;">(moy. historique)</span>
            </div>
            <div style="font-size:0.8rem;opacity:0.6;margin-top:0.2rem;">Inflation moy. : {format_smart(moy_inflation_hist,'%')} (auto)</div>
        </div>
        """,unsafe_allow_html=True)
        rendement_a = moy_brute_hist
        inflation_a = moy_inflation_hist
        rendement_b = st.number_input("Scénario B (%) ✍️",min_value=0.00,value=8.00,step=0.01)
    with c_p3:
        inflation_estimee = st.number_input("Inflation Scénario B (%) ✍️",min_value=0.00,value=2.00,step=0.01)
        taxe_plus_value = st.number_input("Flat Tax (%) ✍️",min_value=0.00,max_value=60.00,step=0.10,value=float(st.session_state.config.get("retraite_taxe",float(st.session_state.config.get("tax_pfu",30.0)))),key="in_tax",on_change=on_retraite_params_change)
    st.divider()

    # --- SIMULATION ---
    cap_v_a = cap_v_b = capital_initial; gains_a = gains_b = 0.0
    app_a = app_b = apport_mensuel
    inf_rate_a = inflation_a/100.0; inf_rate_b = inflation_estimee/100.0
    r_a = rendement_a/100.0; r_b = rendement_b/100.0
    r_a_m = (1+r_a)**(1/12)-1; r_b_m = (1+r_b)**(1/12)-1

     # Total des apports passés
    total_apports_passes = sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _,r in st.session_state.historique.iterrows())
    
    # Apports futurs (identiques pour les deux scénarios)
    total_apports_futurs = 0
    app_temp = apport_mensuel
    
    trajectory_data = []
    for y in range(annee_en_cours, annee_retraite):
        mois_dans_annee = 12 if y>annee_en_cours else max(1,13-datetime.datetime.now().month)
        
        for _ in range(mois_dans_annee):
            # Ajouter l'apport du mois (identique pour A et B)
            cap_v_a += app_temp
            cap_v_b += app_temp
            total_apports_futurs += app_temp
            
            # Calculer les intérêts (différents selon le scénario)
            int_a = cap_v_a * r_a_m
            int_b = cap_v_b * r_b_m
            cap_v_a += int_a
            cap_v_b += int_b
        
        # L'apport mensuel augmente avec l'inflation des apports (identique)
        app_temp *= (1+inf_rate_apports)
        
        years_diff = y-annee_en_cours+1
        trajectory_data.append({
            "Année":y,
            "Capital Net (Scénario A)":round(cap_v_a/((1+inf_rate_a)**years_diff),2),
            "Capital Net (Scénario B)":round(cap_v_b/((1+inf_rate_b)**years_diff),2)
        })
    
    years_diff = annee_retraite-annee_en_cours
    cap_a_nom = cap_v_a
    cap_b_nom = cap_v_b

    total_apports = total_apports_passes+total_apports_futurs  # Identique pour A et B

    tx_r_a = max(0.0,((1.08)/(1+inf_rate_a))-1)
    tx_r_b = max(0.0,((1.08)/(1+inf_rate_b))-1)

    st.subheader(f"🎯 Capital projeté au 1er Janvier {annee_retraite}"); colA,colB = st.columns(2)

    # --- SCÉNARIO A ---
    total_a = cap_v_a
    plus_value_a = max(0,total_a-total_apports)
    part_plus_value_a = (plus_value_a/total_a*100) if total_a>0 else 0
    rente_br_a = (cap_a_nom/((1+inf_rate_a)**years_diff))*tx_r_a/12
    impot_a = rente_br_a*(part_plus_value_a/100)*(taxe_plus_value/100)
    rente_nette_a = rente_br_a-impot_a
    
    with colA:
        st.markdown(f"### Scénario A ({format_smart(rendement_a,'%')}/an, infl. {format_smart(inflation_a,'%')})")
        afficher_montant_double("💰 Capital Brut Final",cap_a_nom)
        afficher_montant_double("🛒 Capital Net (pouvoir d'achat)",cap_a_nom/((1+inf_rate_a)**years_diff))
        st.write("")
        afficher_montant_double("📥 Total Apports",total_apports,couleur_valeur="#95a5a6")
        afficher_montant_double("📈 Plus-Value",plus_value_a,couleur_valeur="#2ecc71")
        st.metric("📊 Part Plus-Value",f"{format_smart(part_plus_value_a,'%')}")
        st.write("")
        afficher_montant_double("💰 Rente Brute Mensuelle",rente_br_a,couleur_valeur="#f39c12")
        afficher_montant_double(f"💸 Impôt ({format_smart(taxe_plus_value,'%')} sur PV)",impot_a,couleur_valeur="#e74c3c",taille="medium")
        afficher_montant_double("🟢 Rente Nette Mensuelle",rente_nette_a,couleur_valeur="#2ecc71")

    # --- SCÉNARIO B ---
    total_b = cap_v_b
    plus_value_b = max(0,total_b-total_apports)  # Même total_apports que A
    part_plus_value_b = (plus_value_b/total_b*100) if total_b>0 else 0
    rente_br_b = (cap_b_nom/((1+inf_rate_b)**years_diff))*tx_r_b/12
    impot_b = rente_br_b*(part_plus_value_b/100)*(taxe_plus_value/100)
    rente_nette_b = rente_br_b-impot_b
    
    with colB:
        st.markdown(f"### Scénario B ({format_smart(rendement_b,'%')}/an, infl. {format_smart(inflation_estimee,'%')})")
        afficher_montant_double("💰 Capital Brut Final",cap_b_nom)
        afficher_montant_double("🛒 Capital Net (pouvoir d'achat)",cap_b_nom/((1+inf_rate_b)**years_diff))
        st.write("")
        afficher_montant_double("📥 Total Apports",total_apports,couleur_valeur="#95a5a6")  # Identique
        afficher_montant_double("📈 Plus-Value",plus_value_b,couleur_valeur="#3498db")
        st.metric("📊 Part Plus-Value",f"{format_smart(part_plus_value_b,'%')}")
        st.write("")
        afficher_montant_double("💰 Rente Brute Mensuelle",rente_br_b,couleur_valeur="#f39c12")
        afficher_montant_double(f"💸 Impôt ({format_smart(taxe_plus_value,'%')} sur PV)",impot_b,couleur_valeur="#e74c3c",taille="medium")
        afficher_montant_double("🟢 Rente Nette Mensuelle",rente_nette_b,couleur_valeur="#3498db")

    if trajectory_data:
        st.divider(); st.subheader("📈 Évolution du Pouvoir d'Achat")
        fig_ret = px.line(pd.DataFrame(trajectory_data).melt(id_vars="Année",var_name="Scénario",value_name="Valeur Nette ($)"),x="Année",y="Valeur Nette ($)",color="Scénario",color_discrete_map={"Capital Net (Scénario A)":"#2ecc71","Capital Net (Scénario B)":"#3498db"})
        fig_ret.update_traces(line_shape='spline')
        fig_ret.update_layout(yaxis_title="Capital Net ($)",xaxis_title="",legend=dict(orientation="h",yanchor="bottom",y=-0.2,xanchor="center",x=0.5))
        st.plotly_chart(fig_ret,use_container_width=True)

elif page_choisie == "🏛️ Fiscalité":
    st.title("🏛️ Simulateur Fiscal")
    with st.spinner("Analyse des transactions..."):
        df_t = st.session_state.transactions.copy()
        if 'Date_DT' not in df_t.columns: df_t['Date_DT'] = pd.to_datetime(df_t.get('Date'),dayfirst=True,errors='coerce')
        annee_en_cours_sys = datetime.datetime.now().year; annee_defaut_sys = annee_en_cours_sys-1
        annees_dispos = sorted(df_t['Date_DT'].dropna().dt.year.unique().tolist(),reverse=True) if not df_t.empty else []
        if annee_defaut_sys not in annees_dispos: annees_dispos.append(annee_defaut_sys)
        annees_dispos = sorted(list(set(annees_dispos)),reverse=True)
        idx_defaut = annees_dispos.index(annee_defaut_sys)
        def on_year_change():
            y = st.session_state.annee_fiscale_select
            bars,_,_ = get_fiscal_bars_for_year(y)
            for k,v in bars.items():
                if not k.startswith('_'): st.session_state.config[k] = v
                if f"in_{k}" in st.session_state: st.session_state[f"in_{k}"] = v
        annee_fiscale = st.selectbox("📅 Année des revenus :",annees_dispos,index=idx_defaut,key="annee_fiscale_select",on_change=on_year_change)
        
        # Bannière
        bars,source,fiabilite = get_fiscal_bars_for_year(annee_fiscale)
        if fiabilite=="Officielle": emoji,border_color,message = "🟢","#28a745","Données fiscales officielles - Calcul fiable à 100%"
        elif fiabilite in ["Exacte (vérifiée)","Élevée"]: emoji,border_color,message = "🟢","#28a745","Barèmes vérifiés - Calcul fiable"
        elif fiabilite in ["Approximative","Moyenne"]: emoji,border_color,message = "🟡","#ffc107","Barèmes estimés (basés sur l'inflation) - Calcul approximatif"
        else: emoji,border_color,message = "🔴","#dc3545","Barèmes potentiellement obsolètes - Calcul non fiable"
        st.markdown(f"""
        <div style="background-color:#1e1e1e;color:#ffffff;border-radius:10px;padding:12px 16px;margin-bottom:15px;border-left:5px solid {border_color};font-size:0.95rem;">
            <strong>{emoji} Barèmes fiscaux {annee_fiscale}</strong><br>
            <span style="font-size:0.85rem;opacity:0.85;">Source : {source}</span><br>
            <span style="font-size:0.85rem;opacity:0.85;">{message}</span>
        </div>
        """,unsafe_allow_html=True)
        st.divider()

        df_actions = pd.DataFrame(get_action_tax_data(df_t,annee_fiscale))
        df_cryptos = pd.DataFrame(get_crypto_tax_data(df_t,annee_fiscale))
        if not df_actions.empty:
            df_a_net_per_asset = df_actions.groupby("Actif")["PV Num"].sum().reset_index()
            plus_values_actions = df_a_net_per_asset[df_a_net_per_asset["PV Num"]>0]["PV Num"].sum()
            moins_values_actions = abs(df_a_net_per_asset[df_a_net_per_asset["PV Num"]<0]["PV Num"].sum())
        else: plus_values_actions=0.0; moins_values_actions=0.0; df_a_net_per_asset=pd.DataFrame()
        plus_values_crypto = df_cryptos[df_cryptos["PV Num"]>0]["PV Num"].sum() if not df_cryptos.empty else 0.0
        moins_values_crypto = abs(df_cryptos[df_cryptos["PV Num"]<0]["PV Num"].sum()) if not df_cryptos.empty else 0.0
        bilan_net_actions = plus_values_actions-moins_values_actions
        bilan_net_crypto = plus_values_crypto-moins_values_crypto

    def update_fiscal_config():
        key_mapping = {
            "in_statut":"f_statut","in_enf":"f_enf","in_s1":"f_s1","in_s2":"f_s2",
            "in_u1":"f_u1","in_k1":"f_k1","in_cv1":"f_cv1","in_r1":"f_r1",
            "in_u2":"f_u2","in_k2":"f_k2","in_cv2":"f_cv2","in_r2":"f_r2",
            "in_int_net":"f_int_net","in_pays_etr":"f_pays_etr",
            "in_tax_lim_1":"tax_lim_1","in_tax_lim_2":"tax_lim_2","in_tax_lim_3":"tax_lim_3","in_tax_lim_4":"tax_lim_4",
            "in_tax_rate_2":"tax_rate_2","in_tax_rate_3":"tax_rate_3","in_tax_rate_4":"tax_rate_4","in_tax_rate_5":"tax_rate_5",
            "in_decote_lim_cel":"decote_lim_cel","in_decote_base_cel":"decote_base_cel",
            "in_decote_lim_mar":"decote_lim_mar","in_decote_base_mar":"decote_base_mar",
            "in_tax_pfu":"tax_pfu","in_tax_ps":"tax_ps","in_frais_repas":"frais_repas"
        }
        for in_key,out_key in key_mapping.items():
            if in_key in st.session_state: st.session_state.config[out_key] = st.session_state[in_key]
        try: save_sheet("Config",pd.DataFrame(list(st.session_state.config.items()),columns=["Clé","Valeur"]))
        except: pass

    st.subheader("👤 1. Situation Familiale")
    c_sit1,c_sit2 = st.columns(2)
    with c_sit1: statut = st.radio("Situation matrimoniale ✍️",["Célibataire / Divorcé(e) / Veuf(ve)","Marié(e) / Pacsé(e)"],index=0 if st.session_state.config.get("f_statut")=="Célibataire / Divorcé(e) / Veuf(ve)" else 1,key="in_statut",on_change=update_fiscal_config)
    with c_sit2: enfants = st.number_input("Enfants à charge ✍️",min_value=0,max_value=10,value=int(st.session_state.config.get("f_enf",0)),step=1,key="in_enf",on_change=update_fiscal_config)
    st.divider()
    salaire_1_val = float(st.session_state.config.get("f_s1",30000.0))
    salaire_2_val = float(st.session_state.config.get("f_s2",0.0)) if "Marié" in statut else 0.0
    frais_reels_1_val = calcul_frais_km(int(st.session_state.config.get("f_k1",0)),int(st.session_state.config.get("f_cv1",5)))+(int(st.session_state.config.get("f_r1",0))*float(st.session_state.config.get("frais_repas",5.35))) if st.session_state.config.get("f_u1",False) else 0.0
    frais_reels_2_val = calcul_frais_km(int(st.session_state.config.get("f_k2",0)),int(st.session_state.config.get("f_cv2",5)))+(int(st.session_state.config.get("f_r2",0))*float(st.session_state.config.get("frais_repas",5.35))) if st.session_state.config.get("f_u2",False) else 0.0
    interets_net_val = float(st.session_state.config.get("f_int_net",0.0))
    parts = 1.0 if "Célibataire" in statut else 2.0
    if enfants==1: parts+=0.5
    elif enfants==2: parts+=1.0
    elif enfants>=3: parts+=1.0+(enfants-2)
    revenu_base_net_global = (salaire_1_val-max(salaire_1_val*0.10,frais_reels_1_val))+(salaire_2_val-max(salaire_2_val*0.10,frais_reels_2_val))+interets_net_val
    impot_salaires_seuls = calcul_impot_ir(revenu_base_net_global,parts,statut,apply_decote=True)
    if (df_actions.empty and df_cryptos.empty) or (plus_values_actions==0 and moins_values_actions==0): choix="Aucun"; cout_pfu=cout_bareme=0.0
    elif bilan_net_actions<=0: choix="Aucun (Bilan négatif)"; cout_pfu=cout_bareme=0.0
    else:
        cout_pfu = bilan_net_actions*(float(st.session_state.config.get("tax_pfu",30.0))/100.0)
        cout_bareme = (calcul_impot_ir(revenu_base_net_global+bilan_net_actions,parts,statut,apply_decote=True)-impot_salaires_seuls)+(bilan_net_actions*(float(st.session_state.config.get("tax_ps",17.2))/100.0))
        choix = "Barème" if cout_bareme<cout_pfu else "PFU"

    st.subheader(f"📝 2. Antisèche du Fisc (Revenus {annee_fiscale})")
    exp_2042 = st.expander("📁 Formulaire 2042 (Déclaration Principale)",expanded=False)
    with exp_2042:
        st.markdown("### ⚙️ Paramètres Fiscaux & Revenus")
        if st.checkbox("Modifier les barèmes (Mode Avancé)"):
            col_b1,col_b2,col_b3 = st.columns(3)
            with col_b1:
                st.number_input("Plafond T1 (€)",value=float(st.session_state.config.get("tax_lim_1",11294.0)),key="in_tax_lim_1",on_change=update_fiscal_config)
                st.number_input("Plafond T2 (€)",value=float(st.session_state.config.get("tax_lim_2",28797.0)),key="in_tax_lim_2",on_change=update_fiscal_config)
                st.number_input("Plafond T3 (€)",value=float(st.session_state.config.get("tax_lim_3",82341.0)),key="in_tax_lim_3",on_change=update_fiscal_config)
                st.number_input("Plafond T4 (€)",value=float(st.session_state.config.get("tax_lim_4",177106.0)),key="in_tax_lim_4",on_change=update_fiscal_config)
            with col_b2:
                st.number_input("Taux T2",value=float(st.session_state.config.get("tax_rate_2",0.11)),step=0.01,key="in_tax_rate_2",on_change=update_fiscal_config)
                st.number_input("Taux T3",value=float(st.session_state.config.get("tax_rate_3",0.30)),step=0.01,key="in_tax_rate_3",on_change=update_fiscal_config)
                st.number_input("Taux T4",value=float(st.session_state.config.get("tax_rate_4",0.41)),step=0.01,key="in_tax_rate_4",on_change=update_fiscal_config)
                st.number_input("Taux T5",value=float(st.session_state.config.get("tax_rate_5",0.45)),step=0.01,key="in_tax_rate_5",on_change=update_fiscal_config)
                st.number_input("PFU (%)",value=float(st.session_state.config.get("tax_pfu",30.0)),step=0.1,key="in_tax_pfu",on_change=update_fiscal_config)
                st.number_input("Repas URSSAF (€)",value=float(st.session_state.config.get("frais_repas",5.35)),step=0.01,key="in_frais_repas",on_change=update_fiscal_config)
            with col_b3:
                st.number_input("Décote Cél. (€)",value=float(st.session_state.config.get("decote_lim_cel",2002.0)),key="in_decote_lim_cel",on_change=update_fiscal_config)
                st.number_input("Base Cél. (€)",value=float(st.session_state.config.get("decote_base_cel",906.0)),key="in_decote_base_cel",on_change=update_fiscal_config)
                st.number_input("Décote Couple (€)",value=float(st.session_state.config.get("decote_lim_mar",3300.0)),key="in_decote_lim_mar",on_change=update_fiscal_config)
                st.number_input("Base Couple (€)",value=float(st.session_state.config.get("decote_base_mar",1493.0)),key="in_decote_base_mar",on_change=update_fiscal_config)
        st.markdown("### 🔹 Revenus nets (Salaires)")
        c_sal1,c_sal2 = st.columns(2)
        with c_sal1: st.number_input("Déclarant 1 (€) ✍️",min_value=0.0,value=float(st.session_state.config.get("f_s1",30000.0)),step=1000.0,key="in_s1",on_change=update_fiscal_config)
        with c_sal2:
            if "Marié" in statut: st.number_input("Déclarant 2 (€) ✍️",min_value=0.0,value=float(st.session_state.config.get("f_s2",0.0)),step=1000.0,key="in_s2",on_change=update_fiscal_config)
        st.markdown("### 🚗 Frais Professionnels")
        col_f1,col_f2 = st.columns(2)
        with col_f1:
            use_frais_1 = st.checkbox("Frais réels (Vous)",value=bool(st.session_state.config.get("f_u1",False)),key="in_u1",on_change=update_fiscal_config)
            if use_frais_1:
                st.number_input("Km annuels - Vous ✍️",min_value=0,value=int(st.session_state.config.get("f_k1",0)),step=1000,key="in_k1",on_change=update_fiscal_config)
                st.selectbox("CV - Vous ✍️",[3,4,5,6,7],index=[3,4,5,6,7].index(int(st.session_state.config.get("f_cv1",5))),key="in_cv1",on_change=update_fiscal_config)
                st.number_input("Jours repas - Vous ✍️",min_value=0,value=int(st.session_state.config.get("f_r1",0)),step=10,key="in_r1",on_change=update_fiscal_config)
                st.info(f"💰 Frais Réels (Vous) : **{format_smart(frais_reels_1_val,'€')}**")
        if "Marié" in statut:
            with col_f2:
                use_frais_2 = st.checkbox("Frais réels (Conjoint)",value=bool(st.session_state.config.get("f_u2",False)),key="in_u2",on_change=update_fiscal_config)
                if use_frais_2:
                    st.number_input("Km annuels - Conjoint ✍️",min_value=0,value=int(st.session_state.config.get("f_k2",0)),step=1000,key="in_k2",on_change=update_fiscal_config)
                    st.selectbox("CV - Conjoint ✍️",[3,4,5,6,7],index=[3,4,5,6,7].index(int(st.session_state.config.get("f_cv2",5))),key="in_cv2",on_change=update_fiscal_config)
                    st.number_input("Jours repas - Conjoint ✍️",min_value=0,value=int(st.session_state.config.get("f_r2",0)),step=10,key="in_r2",on_change=update_fiscal_config)
                    st.info(f"💰 Frais Réels (Conjoint) : **{format_smart(frais_reels_2_val,'€')}**")
        out_2042_lines = st.container()

    with st.expander("📁 Formulaire 2047 (Revenus étrangers)",expanded=False):
        st.markdown("### 🔹 Rubrique 2")
        c_rev1,c_rev2 = st.columns(2)
        with c_rev1: pays_etranger = st.text_input("Pays ✍️",value=st.session_state.config.get("f_pays_etr","Lituanie"),key="in_pays_etr",on_change=update_fiscal_config)
        with c_rev2: interets_net = st.number_input("Montant Net (€) ✍️",min_value=0.0,value=float(st.session_state.config.get("f_int_net",0.0)),step=10.0,key="in_int_net",on_change=update_fiscal_config)
        if interets_net<=0: st.info("Aucun revenu déclaré.")
        else:
            st.markdown(f"- **Ligne 250** : Pays : `{pays_etranger}` | Montant : `{format_smart(interets_net,'€')}`")
            st.markdown(f"- **Ligne 251** : `{format_smart(interets_net,'€')}`")
            st.markdown(f"- **Ligne 252 (2TR)** : `{format_smart(interets_net,'€')}`")

    with out_2042_lines:
        st.divider()
        st.markdown("### 🔹 Lignes à reporter (2042)")
        st.markdown("**Plus-Values :**")
        if bilan_net_actions>0:
            st.markdown(f"- **3VG** : `{format_smart(bilan_net_actions,'€')}`")
            if choix=="Barème": st.markdown("- **2OP** : `À cocher`.")
            else: st.markdown("- **2OP** : `DÉCOCHÉE`.")
        elif bilan_net_actions<0: st.markdown(f"- **3VH** : `{format_smart(abs(bilan_net_actions),'€')}`")
        else: st.markdown("- Aucune.")
        st.markdown("**Cryptos :**")
        if bilan_net_crypto>0: st.markdown(f"- **3AN** : `{format_smart(bilan_net_crypto,'€')}`")
        elif bilan_net_crypto<0: st.markdown(f"- **3BN** : `{format_smart(abs(bilan_net_crypto),'€')}`")
        else: st.markdown("- Aucune.")
        st.markdown("**Intérêts :**")
        if interets_net_val>0: st.markdown(f"- **2TR** : `{format_smart(interets_net_val,'€')}`")
        else: st.markdown("- Aucun.")

    with st.expander("📁 Formulaire 2074 (Plus-values Classiques)",expanded=False):
        if df_actions.empty: st.info("Aucune cession.")
        else:
            st.markdown("### Synthèse")
            if plus_values_actions>0: st.markdown(f"- **Ligne 905** : `{format_smart(plus_values_actions,'€')}`")
            if moins_values_actions>0: st.markdown(f"- **Ligne 913** : `{format_smart(moins_values_actions,'€')}`")
            st.divider(); st.markdown("### Cadre 3 : Cessions")
            for actif in sorted(df_actions["Actif"].unique().tolist()):
                df_actif_a = df_actions[df_actions["Actif"]==actif]
                qte_totale = df_actif_a["Qte Num"].sum(); cession_totale = df_actif_a["Cession Num"].sum()
                acq_totale = df_actif_a["Acq Num"].sum(); pv_totale = df_actif_a["PV Num"].sum()
                st.markdown(f"**👉 {actif}**")
                st.markdown(f"- **514** : `{format_smart(cession_totale/qte_totale if qte_totale>0 else 0,'€',is_price=True)}`")
                st.markdown(f"- **515** : `{format_smart(qte_totale,is_price=True)}`")
                st.markdown(f"- **516** : `{format_smart(cession_totale,'€')}`")
                st.markdown(f"- **520** : `{format_smart(acq_totale/qte_totale if qte_totale>0 else 0,'€',is_price=True)}`")
                st.markdown(f"- **521** : `{format_smart(acq_totale,'€')}`")
                st.info(f"**Bilan :** {format_smart(pv_totale,'€',force_sign=True)}")

    with st.expander("📁 Formulaire 2086 (Cryptomonnaies)",expanded=False):
        if df_cryptos.empty: st.info("Aucune cession.")
        else:
            for actif in sorted(df_cryptos["Actif"].unique().tolist()):
                df_actif_c = df_cryptos[df_cryptos["Actif"]==actif]
                st.markdown(f"#### 🪙 {actif} (Bilan : {format_smart(df_actif_c['PV Num'].sum(),'€',force_sign=True)})")
                for _,row_c in df_actif_c.iterrows():
                    st.markdown(f"**Vente du {row_c['Date de vente']}**")
                    st.markdown(f"- **213** : `{format_smart(row_c['Ligne 213'],'€')}`")
                    st.markdown(f"- **220** : `{format_smart(row_c['Ligne 220'],'€')}`")
                    st.markdown(f"- **224** : `{format_smart(row_c['Ligne 224'],'€',force_sign=True)}`")

    st.divider()
    st.subheader("💡 3. Recommandation")
    if bilan_net_actions>0:
        taux_moyen_bareme = (cout_bareme/bilan_net_actions)*100
        if choix=="Barème": st.success(f"✅ **Barème Progressif** plus avantageux : **{format_smart(cout_bareme,'€')}** (Taux effectif : {format_smart(taux_moyen_bareme,'%')})")
        else: st.success(f"✅ **Flat Tax (PFU)** plus avantageuse : **{format_smart(cout_pfu,'€')}**")
    taux_commun = (impot_salaires_seuls/(salaire_1_val+salaire_2_val+interets_net_val)*100) if (salaire_1_val+salaire_2_val+interets_net_val)>0 else 0.0
    taux_perso_1 = (calcul_impot_ir((salaire_1_val-max(salaire_1_val*0.10,frais_reels_1_val)),1.0,"Célibataire",apply_decote=False)/salaire_1_val*100) if salaire_1_val>0 else 0.0
    st.markdown("#### 📌 Bilan Impôts")
    st.write(f"Impôt foyer : **{format_smart(impot_salaires_seuls,'€')} / an**.")
    col_taux1,col_taux2 = st.columns(2)
    with col_taux1: st.info(f"👨‍👩‍👧‍👦 **Taux Commun : {format_smart(taux_commun,'%')}**")
    if "Marié" in statut: 
        with col_taux2: st.success(f"👤 **Taux Personnalisé : {format_smart(taux_perso_1,'%')}**")
