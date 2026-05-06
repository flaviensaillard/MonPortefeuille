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

# Rafraîchissement automatique de la page toutes les 15 minutes (900 000 ms)
st_autorefresh(interval=15 * 60 * 1000, key="datarefresh")

# --- 2. SÉCURITÉ : MOT DE PASSE ---
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

# --- 3. CONNEXION À GOOGLE SHEETS ---
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
    ws = sh.worksheet(sheet_name)
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
                
                # --- MOTEUR 1 : CRYPTO VIA BINANCE (Anti-Blocage) ---
                if ticker_saisi.endswith("USDT"):
                    for base_url in ["https://api.binance.com", "https://api.binance.us"]:
                        try:
                            url = f"{base_url}/api/v3/klines?symbol={ticker_saisi}&interval=1d&limit=2"
                            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                            with urllib.request.urlopen(req, timeout=3) as response:
                                data = json.loads(response.read().decode())
                                if len(data) >= 2:
                                    prev_close = float(data[0][4]) # Clôture de la veille (Minuit UTC)
                                    prix_usd = float(data[1][4])   # Prix actuel
                                else:
                                    prix_usd = float(data[0][4])
                                    prev_close = prix_usd
                                    
                                var_pct = ((prix_usd - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0
                                symbole = "↗" if var_pct > 0 else ("↘" if var_pct < 0 else "→")
                                st.session_state.variations[ticker_saisi] = f"{symbole} {var_pct:+.2f} %"
                                df_temp.at[index, "Court"] = f"$ {prix_usd:.2f}"
                                changement = True
                                success_binance = True
                                break # Succès : on sort de la boucle de tentative d'URL
                        except:
                            continue # Échec : on tente l'URL suivante
                            
                    if success_binance:
                        continue # La crypto a été mise à jour avec succès, on passe à l'actif suivant du tableau

                # --- MOTEUR 2 : ACTIONS & FOREX VIA YAHOO FINANCE ---
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

@st.cache_data(ttl=86400) # Met en cache le résultat pendant 24h pour ne jamais ralentir ton logiciel
def recuperer_inflation_france():
    """Récupère l'inflation officielle française via l'API de la Banque Mondiale"""
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

# --- 5. CHARGEMENT INITIAL (DEPUIS LE CLOUD) ---
if "apport_dispo" not in st.session_state: st.session_state.apport_dispo = 0.0
if "variations" not in st.session_state: st.session_state.variations = {}

if "donnees" not in st.session_state:
    st.session_state.donnees = nettoyer_dataframe(load_sheet("Donnees", ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]))

if "historique" not in st.session_state:
    df_h = load_sheet("Historique", ["Date", "Type", "Montant $", "Montant €", "Montant Or"])
    for col in ["Montant $", "Montant €", "Montant Or"]:
        if col in df_h.columns: df_h[col] = df_h[col].apply(extraire_nombre)
    st.session_state.historique = df_h

if "projections" not in st.session_state:
    st.session_state.projections = recalculer_toute_la_base_projections(load_sheet("Projections", []))
elif "TG_Evolution cumulée $" not in st.session_state.projections.columns:
    st.session_state.projections = recalculer_toute_la_base_projections(st.session_state.projections)

if "inflation" not in st.session_state:
    df_infl = load_sheet("Inflation", ["Année", "Inflation (%)"])
    if not df_infl.empty and 'Année' in df_infl.columns: df_infl['Année'] = df_infl['Année'].astype(int)
    st.session_state.inflation = df_infl

# --- AUTO-UPDATE SILENCIEUX DE L'INFLATION ---
if "inflation_check_done" not in st.session_state:
    st.session_state.inflation_check_done = True
    dict_infl = recuperer_inflation_france()
    
    if dict_infl and not st.session_state.projections.empty:
        df_p_temp = st.session_state.projections.copy()
        df_p_temp['Date_DT'] = pd.to_datetime(df_p_temp['Date'], dayfirst=True, errors='coerce')
        annees_portefeuille = df_p_temp.dropna(subset=['Date_DT'])['Date_DT'].dt.year.unique()
        
        df_infl_temp = st.session_state.inflation.copy()
        nouveau_infl = []
        changement = False
        
        for a in annees_portefeuille:
            val = 0.0
            if not df_infl_temp[df_infl_temp['Année'] == a].empty:
                val = df_infl_temp[df_infl_temp['Année'] == a].iloc[0]['Inflation (%)']
            
            # Si la Banque Mondiale a une valeur pour cette année, elle écrase la tienne
            if a in dict_infl and dict_infl[a] != val:
                val = dict_infl[a]
                changement = True
                
            nouveau_infl.append({'Année': a, 'Inflation (%)': val})
            
        if changement:
            st.session_state.inflation = pd.DataFrame(nouveau_infl)
            save_sheet("Inflation", st.session_state.inflation)

# --- GESTION DU CHRONOMÈTRE ---
if "dernier_refresh_cours" not in st.session_state:
    st.session_state.dernier_refresh_cours = 0

maintenant = time.time()
if maintenant - st.session_state.dernier_refresh_cours >= 900:
    actualiser_cours_internet(silencieux=(st.session_state.dernier_refresh_cours == 0))
    st.session_state.dernier_refresh_cours = maintenant

# --- 6. NAVIGATION ---
st.sidebar.title("Menu")
page_choisie = st.sidebar.radio("Aller vers :", ["📊 Tableau de bord", "📋 Liste des actifs", "⚖️ Rééquilibrage", "💰 Fonds", "🏖️ Suivi", "📈 Performance", "🌴 Retraite"])

st.sidebar.divider()
if st.sidebar.button("🔄 Recharger l'application", use_container_width=True):
    st.session_state.clear()
    st.rerun()

# --- 7. PAGES DE L'APPLICATION ---

if page_choisie == "📊 Tableau de bord":
    st.title("📊 Vue d'ensemble de mon Patrimoine")
    
    # --- PRÉ-CALCULS DES DONNÉES ---
    df_actuel = st.session_state.donnees
    df_p = st.session_state.projections
    
    val_invest = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    val_total = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows())
    
    cap_actuel = sum(row["Montant $"] if "ajout" in row["Type"].lower() else -row["Montant $"] for _, row in st.session_state.historique.iterrows())
    df_p_live = df_p.copy()
    ligne_live = pd.DataFrame([{
        "Date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "Capital investi": cap_actuel,
        "Actifs Stratégiques": val_invest,
        "Total Global": val_total
    }])
    df_p_live = pd.concat([df_p_live, ligne_live], ignore_index=True)
    df_p_live = recalculer_toute_la_base_projections(df_p_live)
    
    def parse_var_jour(ticker):
        var_str = st.session_state.variations.get(ticker, "0")
        match = re.search(r'([+-]?\d+\.?\d*)', var_str)
        return float(match.group(1)) if match else 0.0

    var_jour_total_global_usd = 0.0
    val_total_veille = 0.0
    
    var_jour_total_usd = 0.0
    val_invest_veille = 0.0
    
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
    
    # --- CALCUL DELTA 1 AN GLISSANT ---
    delta = pct_delta = 0.0
    delta_tg = pct_delta_tg = 0.0
    
    if not df_p.empty:
        df_p_dates = df_p.copy()
        df_p_dates['Date_DT'] = pd.to_datetime(df_p_dates['Date'], dayfirst=True, errors='coerce')
        df_p_dates = df_p_dates.dropna(subset=['Date_DT']).sort_values('Date_DT')
        
        if not df_p_dates.empty:
            now_dt = pd.Timestamp.now()
            target_dt = now_dt - pd.DateOffset(years=1) # On remonte à 1 an (365 jours)
            
            df_past = df_p_dates[df_p_dates['Date_DT'] <= target_dt]
            if not df_past.empty:
                row_ref = df_past.iloc[-1]
            else:
                row_ref = df_p_dates.iloc[0] # Si on a moins d'1 an d'historique, on prend le tout premier point
                
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

    # =========================================================
    # BLOC 1 : PILOTAGE & STATUT
    # =========================================================
    st.subheader("⚙️ 1. Pilotage & Statut")
    col_btn, col_statut = st.columns([1, 2])
    
    with col_btn:
        if st.button("🔄 Actualiser les cours", use_container_width=True):
            actualiser_cours_internet(silencieux=False)
            st.rerun()
            
    with col_statut:
        if besoin_reequilibrage:
            st.warning("⚠️ **Rééquilibrage nécessaire** (Certains de vos actifs ont dépassé les tolérances. Consultez l'onglet Rééquilibrage.)")
        else:
            st.success("✅ **Équilibré** (Votre stratégie d'allocation cible est actuellement respectée.)")
            
    st.divider()

    # =========================================================
    # BLOC 2 : TOTAL GLOBAL
    # =========================================================
    st.subheader("🌍 2. Total Global (Toutes liquidités incluses)")
    
    col_tg_met, col_tg_vide = st.columns(2)
    
    with col_tg_met:
        afficher_montant_double("Total Global", val_total, f"{delta_tg:+,.2f} $ ({pct_delta_tg:+.2f} % sur 1 an glissant)")
        color_jour_tg = "#2ecc71" if var_jour_total_global_usd >= 0 else "#e74c3c"
        symbole_jour_tg = "📈" if var_jour_total_global_usd >= 0 else "📉"
        st.markdown(f"<div style='margin-top: -0.5rem; margin-bottom: 1rem;'><span style='font-size: 1.1em;'>{symbole_jour_tg} Aujourd'hui : <strong style='color:{color_jour_tg}'>{var_jour_total_global_usd:+,.2f} $ ({pct_jour_total_global:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
        
    st.write("")

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

        if df_viz_tg.empty:
            st.warning("Aucun enregistrement trouvé pour cette période.")
        else:
            df_viz_tg.set_index('Date_DT', inplace=True)
            val_debut_tg = df_viz_tg['TG_Evolution cumulée $'].iloc[0]
            val_fin_tg = df_viz_tg['TG_Evolution cumulée $'].iloc[-1]
            actifs_debut_tg = df_viz_tg['Total Global'].iloc[0]

            delta_usd_tg = val_fin_tg - val_debut_tg
            pct_periode_tg = (delta_usd_tg / actifs_debut_tg * 100) if actifs_debut_tg > 0 else 0.0
            
            twr_debut_tg = df_viz_tg['TG_Score TWR %'].iloc[0]
            twr_fin_tg = df_viz_tg['TG_Score TWR %'].iloc[-1]
            mult_d_tg, mult_f_tg = 1 + (twr_debut_tg / 100), 1 + (twr_fin_tg / 100)
            twr_periode_tg = ((mult_f_tg / mult_d_tg) - 1) * 100 if mult_d_tg != 0 else 0.0

            c1_g_tg, c2_g_tg = st.columns([1, 3])
            with c1_g_tg:
                if "ROI" in mode_graph_tg:
                    afficher_montant_double("Gains nets globaux", val_fin_tg, f"{delta_usd_tg:+,.2f} $ ({pct_periode_tg:+.2f} % sur la période)", taille="medium")
                    pct_global_tg = df_viz_tg['TG_Evolution cumulée %'].iloc[-1]
                    color_tg = "green" if pct_global_tg > 0 else "red" if pct_global_tg < 0 else "gray"
                    st.markdown(f"📊 Rentabilité Globale : <strong style='color:{color_tg}'>{pct_global_tg:+.2f} %</strong>", unsafe_allow_html=True)
                else:
                    st.metric("Score TWR Global (%)", f"{twr_fin_tg:+.2f} %", f"{twr_periode_tg:+.2f} % (sur la période)")
                    afficher_montant_double("Gains nets actuels", val_fin_tg, taille="medium")

            with c2_g_tg:
                col_y_tg = 'TG_Evolution cumulée $' if "ROI" in mode_graph_tg else 'TG_Score TWR %'
                df_plot_tg = df_viz_tg.reset_index()
                fig_line_tg = px.line(df_plot_tg, x='Date_DT', y=col_y_tg)
                fig_line_tg.update_traces(line_shape='spline')
                fig_line_tg.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
                fig_line_tg.update_yaxes(zeroline=False, rangemode="normal")
                # Format des dates en Jour/Mois/Année (Axe X et Bulle d'information)
                fig_line_tg.update_xaxes(tickformat="%d/%m/%Y", hoverformat="%d/%m/%Y")
                st.plotly_chart(fig_line_tg, use_container_width=True)

        st.write("")
        st.markdown("**🌍 Répartition du Patrimoine (Total Global)**")
        
        c_tg_p1, c_tg_p2 = st.columns(2)
        with c_tg_p1:
            st.markdown("*Toutes classes d'actifs confondues*")
            df_actifs_global = st.session_state.donnees.copy()
            df_actifs_global['Val_Num'] = df_actifs_global['Valeur totale'].apply(extraire_nombre)
            df_pie_tg = df_actifs_global[df_actifs_global['Val_Num'] > 0].groupby('Type')['Val_Num'].sum().reset_index()
            if not df_pie_tg.empty:
                fig_tg = px.pie(df_pie_tg, values='Val_Num', names='Type', color='Type', color_discrete_map={"🛢️ Action": "#e74c3c", "📜 Obligation": "#3498db", "💰 Or": "#f1c40f", "₿ Crypto": "#9b59b6", "💵 Cash": "#2ecc71"}, hole=0.4)
                fig_tg.update_traces(textposition='inside', textinfo='percent+label')
                fig_tg.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
                st.plotly_chart(fig_tg, use_container_width=True)
                
    else:
        st.info("Aucune donnée disponible pour l'analyse globale. Le premier point sera enregistré cette nuit.")

    st.divider()

    # =========================================================
    # BLOC 3 : ACTIFS STRATÉGIQUES
    # =========================================================
    st.subheader("🎯 3. Actifs Stratégiques (Investissements cibles)")
    
    col_strat_met, col_strat_vide = st.columns(2)
    with col_strat_met:
        afficher_montant_double("Actifs Stratégiques", val_invest, f"{delta:+,.2f} $ ({pct_delta:+.2f} % sur 1 an glissant)")
        color_jour = "#2ecc71" if var_jour_total_usd >= 0 else "#e74c3c"
        symbole_jour = "📈" if var_jour_total_usd >= 0 else "📉"
        st.markdown(f"<div style='margin-top: -0.5rem; margin-bottom: 1rem;'><span style='font-size: 1.1em;'>{symbole_jour} Aujourd'hui : <strong style='color:{color_jour}'>{var_jour_total_usd:+,.2f} $ ({pct_jour_total:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    
    st.write("") 
    
    if df_p.empty: 
        st.info("Aucune donnée disponible pour l'analyse. Le premier point sera enregistré cette nuit.")
    else:
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
                
        if df_viz.empty: st.warning(f"Aucun enregistrement trouvé pour cette période.")
        else:
            df_viz.set_index('Date_DT', inplace=True)
            val_debut = df_viz['Evolution cumulée $'].iloc[0]
            val_fin = df_viz['Evolution cumulée $'].iloc[-1]
            actifs_debut = df_viz['Actifs Stratégiques'].iloc[0]
            
            delta_usd = val_fin - val_debut
            pct_periode = (delta_usd / actifs_debut * 100) if actifs_debut > 0 else 0.0
            pct_global = df_viz['Evolution cumulée %'].iloc[-1]
            
            twr_debut = df_viz['Score TWR %'].iloc[0]
            twr_fin = df_viz['Score TWR %'].iloc[-1]
            mult_d, mult_f = 1 + (twr_debut / 100), 1 + (twr_fin / 100)
            twr_periode = ((mult_f / mult_d) - 1) * 100 if mult_d != 0 else 0.0
            
            c1_g, c2_g = st.columns([1, 3])
            with c1_g:
                if "ROI" in mode_graph:
                    afficher_montant_double("Gains nets de la stratégie", val_fin, f"{delta_usd:+,.2f} $ ({pct_periode:+.2f} % sur la période)", taille="medium")
                    color = "green" if pct_global > 0 else "red" if pct_global < 0 else "gray"
                    st.markdown(f"📊 Rentabilité Stratégique : <strong style='color:{color}'>{pct_global:+.2f} %</strong>", unsafe_allow_html=True)
                else:
                    st.metric("Score TWR Stratégique (%)", f"{twr_fin:+.2f} %", f"{twr_periode:+.2f} % (sur la période)")
                    afficher_montant_double("Gains nets actuels", val_fin, taille="medium")
                    
            with c2_g:
                col_y = 'Evolution cumulée $' if "ROI" in mode_graph else 'Score TWR %'
                df_plot = df_viz.reset_index()
                fig_line = px.line(df_plot, x='Date_DT', y=col_y)
                fig_line.update_traces(line_shape='spline')
                fig_line.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
                fig_line.update_yaxes(zeroline=False, rangemode="normal")
                # Format des dates en Jour/Mois/Année (Axe X et Bulle d'information)
                fig_line.update_xaxes(tickformat="%d/%m/%Y", hoverformat="%d/%m/%Y")
                st.plotly_chart(fig_line, use_container_width=True)

    st.write("")
    st.markdown("**🎯 Répartition détaillée de la stratégie**")
    
    df_actifs_dash = st.session_state.donnees.copy()
    df_actifs_dash['Val_Num'] = df_actifs_dash['Valeur totale'].apply(extraire_nombre)
    df_actifs_dash['Pct_Cible'] = df_actifs_dash['Pourcentage (%)'].apply(extraire_nombre)
    df_strat = df_actifs_dash[df_actifs_dash['Pct_Cible'] > 0]
    
    c_p1, c_p2 = st.columns(2)
    with c_p1:
        st.markdown("*Classes d'actifs ciblées*")
        df_pie1 = df_strat[df_strat['Val_Num'] > 0].groupby('Type')['Val_Num'].sum().reset_index()
        if not df_pie1.empty:
            fig1 = px.pie(df_pie1, values='Val_Num', names='Type', color='Type', color_discrete_map={"🛢️ Action": "#e74c3c", "📜 Obligation": "#3498db", "💰 Or": "#f1c40f", "₿ Crypto": "#9b59b6", "💵 Cash": "#2ecc71"}, hole=0.4)
            fig1.update_traces(textposition='inside', textinfo='percent+label')
            fig1.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
            st.plotly_chart(fig1, use_container_width=True)

    with c_p2:
        st.markdown("*Détail des lignes stratégiques*")
        if not df_strat.empty:
            fig2 = px.pie(df_strat[df_strat['Val_Num'] > 0], values='Val_Num', names='Ticker', hole=0.4)
            fig2.update_traces(textposition='inside', textinfo='percent+label')
            fig2.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
            st.plotly_chart(fig2, use_container_width=True)

    st.divider()

    # =========================================================
    # BLOC 4 : RENTE MENSUELLE
    # =========================================================
    st.subheader("🏖️ 4. Liberté Financière (Rente Mensuelle actuelle)")
    c_rente1, c_rente2 = st.columns(2)
    
    with c_rente1:
        st.write("") 
        inf_estimee_dash = st.slider("Inflation cible à déduire (%) ✍️", min_value=0.0, max_value=15.0, value=2.0, step=0.1, key="dash_infl", help="L'inflation est déduite pour garantir la croissance de votre capital et préserver votre pouvoir d'achat futur.")
        
    with c_rente2:
        taux_reel = ((1 + 0.08) / (1 + (inf_estimee_dash / 100.0))) - 1
        rente_mensuelle_usd = (val_invest * max(0.0, taux_reel)) / 12.0
        
        afficher_montant_double("Rente Mensuelle Nette (Base 8% par an)", rente_mensuelle_usd, couleur_valeur="#3498db")


elif page_choisie == "📋 Liste des actifs":
    st.title("📋 Liste de mes actifs")
    
    df_actuel = st.session_state.donnees.copy()
    val_invest = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    val_total = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows())
    somme_p = sum(extraire_nombre(r["Pourcentage (%)"]) for _, r in df_actuel.iterrows())

    # --- CALCUL VARIATIONS JOUR ---
    def parse_var_jour(ticker):
        var_str = st.session_state.variations.get(ticker, "0")
        match = re.search(r'([+-]?\d+\.?\d*)', var_str)
        return float(match.group(1)) if match else 0.0

    var_jour_total_global_usd = 0.0
    val_total_veille = 0.0
    var_jour_total_usd = 0.0
    val_invest_veille = 0.0
    
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
    # ------------------------------

    c1, c2, c3 = st.columns(3)
    with c1:
        afficher_montant_double("Actifs Stratégiques", val_invest)
        color_jour = "#2ecc71" if var_jour_total_usd >= 0 else "#e74c3c"
        symbole_jour = "📈" if var_jour_total_usd >= 0 else "📉"
        st.markdown(f"<div style='margin-top: -0.5rem; margin-bottom: 1rem;'><span style='font-size: 1.1em;'>{symbole_jour} Aujourd'hui : <strong style='color:{color_jour}'>{var_jour_total_usd:+,.2f} $ ({pct_jour_total:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
        
    with c2:
        afficher_montant_double("Total Global", val_total)
        color_jour_tg = "#2ecc71" if var_jour_total_global_usd >= 0 else "#e74c3c"
        symbole_jour_tg = "📈" if var_jour_total_global_usd >= 0 else "📉"
        st.markdown(f"<div style='margin-top: -0.5rem; margin-bottom: 1rem;'><span style='font-size: 1.1em;'>{symbole_jour_tg} Aujourd'hui : <strong style='color:{color_jour_tg}'>{var_jour_total_global_usd:+,.2f} $ ({pct_jour_total_global:+.2f} %)</strong></span></div>", unsafe_allow_html=True)
    
    with c3:
        ecart = round(100 - somme_p, 2)
        if ecart == 0:
            info_str = "✅ Cible atteinte"
            color_info = "#2ecc71"
        elif ecart > 0:
            info_str = f"⚠️ {ecart:.2f} % manquant"
            color_info = "#e74c3c"
        else:
            info_str = f"⚠️ {abs(ecart):.2f} % en trop"
            color_info = "#e74c3c"
            
        html_repartition = f"""
        <div style="margin-bottom: 0.8rem;">
            <div style="font-size: 0.9rem; opacity: 0.8; margin-bottom: 0.2rem;">Répartition Cible</div>
            <div style="font-size: 1.8rem; font-weight: 600; line-height: 1.2;">
                {somme_p:.2f} %
            </div>
            <div style='font-size: 0.9rem; font-weight: 600; color: {color_info}; padding-top: 0.2rem;'>{info_str}</div>
        </div>
        """
        st.markdown(html_repartition, unsafe_allow_html=True)

    st.divider()

    if st.button("🔄 Actualiser les cours", use_container_width=True):
        actualiser_cours_internet(silencieux=False)
        st.rerun()

    df_actuel['Var. Jour 🔒'] = df_actuel['Ticker'].apply(lambda x: st.session_state.variations.get(str(x).upper(), "→ 0.00 %"))

    config_actifs = {
        "Ticker": st.column_config.TextColumn("Ticker ✍️"),
        "Type": st.column_config.SelectboxColumn("Type ✍️", options=["🛢️ Action", "📜 Obligation", "💰 Or", "₿ Crypto", "💵 Cash"]),
        "Court": st.column_config.TextColumn("Court 🔒", disabled=True),
        "Quantité": st.column_config.TextColumn("Quantité ✍️"),
        "Valeur totale": st.column_config.TextColumn("Valeur totale 🔒", disabled=True),
        "Pourcentage (%)": st.column_config.NumberColumn("Pourcentage (%) ✍️", format="%.2f%%"),
        "Var. Jour 🔒": st.column_config.TextColumn("Var. Jour 🔒", disabled=True)
    }
    
    display_cols = ["Ticker", "Type", "Court", "Quantité", "Valeur totale", "Pourcentage (%)", "Var. Jour 🔒"]
    
    def color_var(v):
        v_str = str(v)
        if "↗" in v_str or "+" in v_str: return 'color: #2ecc71'
        if "↘" in v_str or "-" in v_str: return 'color: #e74c3c'
        return 'color: #95a5a6'
    
    m_dev = df_actuel.apply(lambda row: est_devise_liquide(row.get("Ticker", "")), axis=1)
    res_i = st.data_editor(df_actuel[~m_dev][display_cols].style.map(color_var, subset=["Var. Jour 🔒"]), key="ei", column_config=config_actifs, use_container_width=True, hide_index=True, num_rows="dynamic")
    res_d = st.data_editor(df_actuel[m_dev][display_cols].style.map(color_var, subset=["Var. Jour 🔒"]), key="ed", column_config=config_actifs, use_container_width=True, hide_index=True, num_rows="dynamic")

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
        
    cash_dispo = st.number_input("💵 Nouvel apport à investir ($) ✍️", min_value=0.00, step=100.00, key="apport_dispo")
    st.divider()
    df = st.session_state.donnees
    
    base_reeq = sum(extraire_nombre(r["Valeur totale"]) for _, r in df.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    new_base = base_reeq + cash_dispo
    
    if new_base > 0:
        reeq_list = []
        for _, row in df.iterrows():
            tick = str(row["Ticker"]).upper()
            pct_cib = extraire_nombre(row["Pourcentage (%)"]) / 100
            if pct_cib == 0: continue
            
            val_act = extraire_nombre(row["Valeur totale"])
            diff = (new_base * pct_cib) - val_act
            prix = extraire_nombre(row["Court"])
            qte = diff / prix if prix > 0 else 0
            
            pct_reel = (val_act / new_base) * 100
            
            qte_fmt = f"{abs(round(qte, 6)):.6f}" if "BTC" in tick or "USDT" in tick else f"{abs(int(round(qte)))}"
            signe = "+ " if qte > 0.000001 else "- " if qte < -0.000001 else ""
            
            if abs(diff) < 1000 or abs(pct_reel - (pct_cib * 100)) < 2.0: 
                action, qte_str = f"✅ ÉQUILIBRÉ ($ {abs(diff):,.2f})", f"({signe}{qte_fmt})"
            else: 
                action, qte_str = f"{'🟢 ACHETER' if diff > 0 else '🔴 VENDRE'} $ {abs(diff):,.2f}", f"{signe}{qte_fmt}"
            
            var_str = st.session_state.variations.get(tick, "→ 0.00 %")
            
            reeq_list.append({"Ticker 🔒": tick, "Var. Jour 🔒": var_str, "Actuel ($) 🔒": val_act, "Écart (%) 🔒": (pct_reel - (pct_cib * 100)), "Action 🔒": action, "Qté (+/-) 🔒": qte_str})
        
        def color_reeq(v):
            v_str = str(v)
            if "↗" in v_str or "ACHETER" in v_str or "+" in v_str: return 'color: #2ecc71'
            if "↘" in v_str or "VENDRE" in v_str or "-" in v_str: return 'color: #e74c3c'
            return 'color: #95a5a6'
            
        st.dataframe(pd.DataFrame(reeq_list).style.format({"Actuel ($) 🔒": "$ {:,.2f}", "Écart (%) 🔒": "{:+.2f} %"}).map(color_reeq, subset=["Var. Jour 🔒", "Action 🔒", "Qté (+/-) 🔒"]), use_container_width=True, hide_index=True)

elif page_choisie == "💰 Fonds":
    st.title("💰 Fonds")
    df_h = st.session_state.historique
    with st.expander("➕ Nouveau mouvement"):
        with st.form("f_m"):
            d_m = st.date_input("Date ✍️")
            t_m = st.radio("Type ✍️", ["Ajout de fond propre", "Retrait"], horizontal=True)
            m_s = st.number_input("Montant ✍️", min_value=0.00, format="%.2f")
            d_s = st.selectbox("Devise ✍️", ["$", "€"])
            if st.form_submit_button("Valider"):
                or_px = float(yf.Ticker("GC=F").fast_info.get('lastPrice', 2000.0))
                m_usd = m_s if d_s == "$" else m_s * TAUX_EUR_USD
                m_eur = m_s if d_s == "€" else m_s / TAUX_EUR_USD
                nl = {"Date": d_m.strftime("%d/%m/%Y"), "Type": t_m, "Montant $": m_usd, "Montant €": m_eur, "Montant Or": m_usd/or_px}
                st.session_state.historique = pd.concat([df_h, pd.DataFrame([nl])], ignore_index=True)
                save_sheet("Historique", st.session_state.historique)
                if t_m == "Ajout de fond propre": st.session_state.apport_dispo += m_usd
                st.rerun()
    
    apports = sum(row["Montant $"] if "ajout" in row["Type"].lower() else -row["Montant $"] for _, row in df_h.iterrows())
    afficher_montant_double("Total Apports nets", apports)
    
    if not df_h.empty:
        df_h_v = df_h.copy()
        df_h_v.columns = [f"{col} 🔒" for col in df_h_v.columns]
        df_h_v['DT'] = pd.to_datetime(df_h_v['Date 🔒'], dayfirst=True, errors='coerce')
        st.dataframe(df_h_v.sort_values('DT', ascending=False).drop(columns=['DT']).style.format({"Montant $ 🔒": "$ {:,.2f}", "Montant € 🔒": "{:,.2f} €", "Montant Or 🔒": "{:,.4f} oz"}), use_container_width=True, hide_index=True)

elif page_choisie == "🏖️ Suivi":
    st.title("🏖️ Suivi & Évolution")
    st.write("Ce tableau enregistre vos points de passage. **Votre robot automatique enregistre une nouvelle ligne chaque nuit.** Vous pouvez modifier les 4 premières colonnes manuellement (✍️) en cas d'erreur. Les autres (🔒) se recalculeront automatiquement.")
    
    if not st.session_state.projections.empty:
        df_v = st.session_state.projections.copy()
        df_v['DT'] = pd.to_datetime(df_v['Date'], dayfirst=True, errors='coerce')
        
        config_suivi = {
            "Date": st.column_config.TextColumn("Date ✍️"),
            "Capital investi": st.column_config.NumberColumn("Capital investi ✍️", format="$ %.2f"),
            "Actifs Stratégiques": st.column_config.NumberColumn("Actifs Strat. ✍️", format="$ %.2f"),
            "Total Global": st.column_config.NumberColumn("Total Global ✍️", format="$ %.2f"),
            "Evolution actifs $": st.column_config.NumberColumn("Evol. Actifs ($) 🔒", format="$ %+.2f", disabled=True),
            "Evolution actifs %": st.column_config.NumberColumn("Evol. Actifs (%) 🔒", format="%+.2f %%", disabled=True),
            "Evolution cumulée $": st.column_config.NumberColumn("Evol. Cumulée ($) 🔒", format="$ %+.2f", disabled=True),
            "Evolution cumulée %": st.column_config.NumberColumn("Evol. Cumulée (%) 🔒", format="%+.2f %%", disabled=True),
            "Score TWR %": st.column_config.NumberColumn("Score TWR (%) 🔒", format="%+.2f %%", disabled=True),
            "TG_Evolution cumulée $": st.column_config.NumberColumn("TG Evol. Cumulée ($) 🔒", format="$ %+.2f", disabled=True),
            "TG_Evolution cumulée %": st.column_config.NumberColumn("TG Evol. Cumulée (%) 🔒", format="%+.2f %%", disabled=True),
            "TG_Score TWR %": st.column_config.NumberColumn("TG Score TWR (%) 🔒", format="%+.2f %%", disabled=True)
        }
        
        edited_suivi = st.data_editor(df_v.sort_values('DT', ascending=False).drop(columns=['DT']), column_config=config_suivi, use_container_width=True, hide_index=True)
        
        if not edited_suivi.equals(df_v.sort_values('DT', ascending=False).drop(columns=['DT'])):
            st.session_state.projections = recalculer_toute_la_base_projections(edited_suivi[["Date", "Capital investi", "Actifs Stratégiques", "Total Global"]])
            save_sheet("Projections", st.session_state.projections)
            st.rerun()

elif page_choisie == "📈 Performance":
    st.title("📈 Performances Annuelles & Inflation")
    df_p = st.session_state.projections

    if df_p.empty: st.info("Aucune donnée disponible. Le premier point sera enregistré cette nuit.")
    else:
        try: or_px = float(yf.Ticker("GC=F").fast_info.get('lastPrice', 2000.0))
        except: or_px = 2000.0

        df_viz = df_p.copy()
        df_viz['Date_DT'] = pd.to_datetime(df_viz['Date'], dayfirst=True, errors='coerce')
        df_viz = df_viz.dropna(subset=['Date_DT']).sort_values('Date_DT')
        df_viz['Année'] = df_viz['Date_DT'].dt.year

        df_y = df_viz.groupby('Année').last().reset_index()
        df_y['Année'] = df_y['Année'].astype(int)
        
        df_y['TWR_mult'] = 1 + (df_y['Score TWR %'] / 100)
        df_y['TWR_mult_prev'] = df_y['TWR_mult'].shift(1).fillna(1.0)
        df_y['Performance brute (%)'] = ((df_y['TWR_mult'] / df_y['TWR_mult_prev']) - 1) * 100

        st.session_state.inflation['Année'] = st.session_state.inflation['Année'].astype(int)
        df_y = df_y.merge(st.session_state.inflation, on='Année', how='left').fillna({'Inflation (%)': 0.0})
        
        df_y['Performance nette (%)'] = (((1 + df_y['Performance brute (%)'] / 100) / (1 + df_y['Inflation (%)'] / 100)) - 1) * 100
        df_y['Gains Nets ($)'] = df_y['Evolution cumulée $'] - df_y['Evolution cumulée $'].shift(1).fillna(0)
        df_y['Valeur Bilan (Or)'] = df_y['Actifs Stratégiques'] / or_px
        
        df_hist = df_y[df_y['Année'] < datetime.datetime.now().year]
        st.subheader("📊 Moyennes Historiques (Hors année en cours)")
        
        if not df_hist.empty:
            c_m1, c_m2, c_m3, c_m4 = st.columns(4)
            c_m1.metric("Moyenne Perf. Brute", f"{df_hist['Performance brute (%)'].mean():+.2f} %")
            c_m2.metric("Moyenne Inflation", f"{df_hist['Inflation (%)'].mean():.2f} %")
            c_m3.metric("Moyenne Perf. Nette", f"{df_hist['Performance nette (%)'].mean():+.2f} %")
            with c_m4:
                afficher_montant_double("Moyenne Gains / An", df_hist['Gains Nets ($)'].mean(), taille="medium")
        else: st.info("L'historique est insuffisant.")
        
        st.divider()
        
        st.write("Ce tableau récapitule vos résultats par année civile. L'inflation officielle est **récupérée et mise à jour automatiquement** depuis la Banque Mondiale. Pour l'année en cours, **double-cliquez sur la colonne 'Inflation ✍️'** pour y saisir votre estimation provisoire.")
        
        df_display = df_y[['Année', 'Performance brute (%)', 'Inflation (%)', 'Performance nette (%)', 'Gains Nets ($)', 'Valeur Bilan ($)', 'Valeur Bilan (Or)']].copy()
        df_display['Année'] = df_display['Année'].astype(str)

        # On nettoie drastiquement l'index pour Streamlit
        df_sorted = df_display.sort_values(by='Année', ascending=False).reset_index(drop=True)

        edited_df = st.data_editor(
            df_sorted,
            column_config={
                "Année": st.column_config.TextColumn("Année 🔒", disabled=True),
                "Performance brute (%)": st.column_config.NumberColumn("Perf. Brute (%) 🔒", format="%.2f %%", disabled=True),
                "Inflation (%)": st.column_config.NumberColumn("Inflation ✍️ (%)", format="%.2f %%", step=0.01),
                "Performance nette (%)": st.column_config.NumberColumn("Perf. Nette (%) 🔒", format="%.2f %%", disabled=True),
                "Gains Nets ($)": st.column_config.NumberColumn("Gains Nets ($) 🔒", format="$ %.2f", disabled=True),
                "Valeur Bilan ($)": st.column_config.NumberColumn("Valeur Bilan ($) 🔒", format="$ %.2f", disabled=True),
                "Valeur Bilan (Or)": st.column_config.NumberColumn("Valeur Bilan (Or) 🔒", format="%.2f oz", disabled=True)
            },
            hide_index=True, use_container_width=True
        )

        if not edited_df['Inflation (%)'].equals(df_sorted['Inflation (%)']):
            nouveau_df_inflation = edited_df[['Année', 'Inflation (%)']].copy()
            nouveau_df_inflation['Année'] = nouveau_df_inflation['Année'].astype(int)
            st.session_state.inflation = nouveau_df_inflation
            save_sheet("Inflation", st.session_state.inflation)
            st.rerun()
            
        st.divider()
        st.subheader("📊 Comparaison Brute vs Nette")
        
        df_chart = edited_df.sort_values(by='Année', ascending=True)[['Année', 'Performance brute (%)', 'Performance nette (%)']].melt(id_vars='Année', var_name='Type', value_name='Rentabilité (%)')
        df_chart['Type'] = df_chart['Type'].replace({'Performance brute (%)': "Brute (Avant inflation)", 'Performance nette (%)': "Nette (Pouvoir d'achat réel)"})
        
        fig = px.bar(df_chart, x='Année', y='Rentabilité (%)', color='Type', barmode='group', color_discrete_map={"Brute (Avant inflation)": "#3498db", "Nette (Pouvoir d'achat réel)": "#2ecc71"}, text_auto='.2f')
        fig.update_layout(yaxis_title="Rentabilité (%)", xaxis_title="", legend_title="")
        st.plotly_chart(fig, use_container_width=True)

elif page_choisie == "🌴 Retraite":
    st.title("🌴 Simulateur d'Indépendance Financière")
    st.write("Ce simulateur projette la valeur de votre portefeuille jusqu'à votre retraite et calcule la rente mensuelle perpétuelle que vous pourrez en tirer sans jamais entamer votre capital (en pouvoir d'achat réel).")

    df_actuel = st.session_state.donnees
    capital_initial = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    
    annee_en_cours = datetime.datetime.now().year
    moy_brute_hist = 5.00
    
    if not st.session_state.projections.empty:
        df_viz = st.session_state.projections.copy()
        df_viz['Date_DT'] = pd.to_datetime(df_viz['Date'], dayfirst=True, errors='coerce')
        df_viz = df_viz.dropna(subset=['Date_DT']).sort_values('Date_DT')
        df_viz['Année'] = df_viz['Date_DT'].dt.year
        df_years = df_viz.groupby('Année').last().reset_index()
        
        df_years['TWR_mult'] = 1 + (df_years['Score TWR %'] / 100)
        df_years['TWR_mult_prev'] = df_years['TWR_mult'].shift(1).fillna(1.0)
        df_years['Performance brute (%)'] = ((df_years['TWR_mult'] / df_years['TWR_mult_prev']) - 1) * 100
        
        df_historique = df_years[df_years['Année'] < annee_en_cours]
        if not df_historique.empty: moy_brute_hist = round(df_historique['Performance brute (%)'].mean(), 2)

    st.subheader("⚙️ Paramètres du Simulateur")
    c_p1, c_p2, c_p3 = st.columns(3)
    
    with c_p1:
        annee_retraite = st.number_input("Année de départ (1er Janvier) ✍️", min_value=annee_en_cours+1, max_value=2100, value=2055, step=1)
        apport_mensuel = st.number_input("Apport mensuel d'aujourd'hui ($) ✍️", min_value=0.00, value=250.00, step=50.00)
    with c_p2:
        rendement_a = st.number_input("Performance Scénario A (%) ✍️", min_value=0.00, max_value=30.00, value=round(max(0.00, float(moy_brute_hist)), 2), step=0.01, help="Par défaut : moyenne de vos performances passées.")
        rendement_b = st.number_input("Performance Scénario B (%) ✍️", min_value=0.00, value=8.00, step=0.01)
    with c_p3:
        inflation_estimee = st.number_input("Inflation annuelle estimée (%) ✍️", min_value=0.00, value=2.00, step=0.01)
        taxe_plus_value = st.number_input("Fiscalité sur les retraits (Flat Tax) (%) ✍️", min_value=0.00, max_value=60.00, value=30.00, step=0.10)
        
    st.info(f"💡 **Info :** Vos apports de {apport_mensuel:,.2f} $ augmenteront de {inflation_estimee:.2f} % chaque année dans le simulateur pour suivre l'évolution de votre salaire et de la vie.")
    st.divider()

    years_range = list(range(annee_en_cours, annee_retraite))
    cap_a_nom = cap_b_nom = capital_initial
    app_a = app_b = apport_mensuel
    inf_rate, r_a, r_b = inflation_estimee / 100.0, rendement_a / 100.0, rendement_b / 100.0
    r_a_m, r_b_m = (1 + r_a)**(1/12) - 1, (1 + r_b)**(1/12) - 1

    trajectory_data = []
    for y in years_range:
        months_in_year = 12 if y > annee_en_cours else max(1, 12 - datetime.datetime.now().month + 1)
        for _ in range(months_in_year):
            cap_a_nom = cap_a_nom * (1 + r_a_m) + app_a
            cap_b_nom = cap_b_nom * (1 + r_b_m) + app_b
            
        app_a *= (1 + inf_rate) ; app_b *= (1 + inf_rate)
        years_diff = y - annee_en_cours + 1
        cap_a_real, cap_b_real = cap_a_nom / ((1 + inf_rate)**years_diff), cap_b_nom / ((1 + inf_rate)**years_diff)
        trajectory_data.append({"Année": y, "Capital Net (Scénario A)": round(cap_a_real, 2), "Capital Net (Scénario B)": round(cap_b_real, 2)})

    taux_reel_retraite = ((1 + 0.08) / (1 + inf_rate)) - 1
    rente_a_reelle = cap_a_real * max(0.00, taux_reel_retraite) / 12.0
    rente_b_reelle = cap_b_real * max(0.00, taux_reel_retraite) / 12.0

    st.subheader(f"🎯 Capital projeté au 1er Janvier {annee_retraite}")
    colA, colB = st.columns(2)
    
    with colA:
        st.markdown(f"### Scénario A (Moyenne : {rendement_a:.2f} % / an)")
        afficher_montant_double("💰 Valeur Brute du Magot 🔒", cap_a_nom)
        afficher_montant_double("🛒 Valeur Nette (Pouvoir d'achat) 🔒", cap_a_real)
        st.write("")
        afficher_montant_double("Rente Mensuelle Nette (Avant impôts)", rente_a_reelle, couleur_valeur="#2ecc71")
        afficher_montant_double(f"Après Impôts ({taxe_plus_value:.1f}%)", rente_a_reelle * (1 - taxe_plus_value / 100.0), couleur_valeur="#e67e22", taille="medium")

    with colB:
        st.markdown(f"### Scénario B (Fixe : {rendement_b:.2f} % / an)")
        afficher_montant_double("💰 Valeur Brute du Magot 🔒", cap_b_nom)
        afficher_montant_double("🛒 Valeur Nette (Pouvoir d'achat) 🔒", cap_b_real)
        st.write("")
        afficher_montant_double("Rente Mensuelle Nette (Avant impôts)", rente_b_reelle, couleur_valeur="#3498db")
        afficher_montant_double(f"Après Impôts ({taxe_plus_value:.1f}%)", rente_b_reelle * (1 - taxe_plus_value / 100.0), couleur_valeur="#e67e22", taille="medium")

    st.divider()
    st.subheader("📈 Évolution du Pouvoir d'Achat Réel (Capital Net)")
    
    if trajectory_data:
        df_traj_melted = pd.DataFrame(trajectory_data).melt(id_vars="Année", var_name="Scénario", value_name="Valeur Nette ($)")
        fig = px.line(df_traj_melted, x="Année", y="Valeur Nette ($)", color="Scénario", color_discrete_map={"Capital Net (Scénario A)": "#2ecc71", "Capital Net (Scénario B)": "#3498db"})
        fig.update_traces(line_shape='spline')
        fig.update_layout(yaxis_title="Capital Net d'Inflation ($)", xaxis_title="Année", legend_title="")
        st.plotly_chart(fig, use_container_width=True)
