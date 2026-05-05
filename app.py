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

# --- 4. FONCTIONS OUTILS ---
def extraire_nombre(valeur):
    if pd.isna(valeur) or str(valeur).strip() == "" or str(valeur).lower() == "nan": return 0.0
    nettoye = re.sub(r'[^\d,.-]', '', str(valeur))
    if ',' in nettoye and '.' in nettoye: nettoye = nettoye.replace(',', '')
    elif ',' in nettoye: nettoye = nettoye.replace(',', '.')
    try: return float(nettoye)
    except: return 0.0

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
            elif "BTC" in tick or "ETH" in tick: df.at[idx, "Type"] = "💰 Or-BTC"
            else: df.at[idx, "Type"] = "🛢️ Action"

    for col in cols_finales:
        if col not in df.columns:
            df[col] = 0.0 if col == "Pourcentage (%)" else ("$ 0.00" if col in ["Court", "Valeur totale"] else "")
            
    return df[cols_finales].reset_index(drop=True)

def recalculer_toute_la_base_projections(df):
    if df is None or df.empty: return df
    df_travail = df.copy()
    colonnes_base = ["Date", "Capital investi", "Actifs", "Epargne totale"]
    
    if not all(c in df_travail.columns for c in colonnes_base):
        for i, nom in enumerate(colonnes_base):
            if i < len(df_travail.columns): df_travail.rename(columns={df_travail.columns[i]: nom}, inplace=True)

    for col in ["Capital investi", "Actifs", "Epargne totale"]:
        df_travail[col] = df_travail[col].apply(extraire_nombre)

    df_travail['DT_TRI'] = pd.to_datetime(df_travail['Date'], dayfirst=True, errors='coerce')
    df_travail = df_travail.sort_values('DT_TRI').reset_index(drop=True)
    
    resultats = []
    first_epargne = df_travail.at[0, "Epargne totale"] if len(df_travail) > 0 else 0
    current_twr_mult = 1.0

    for i in range(len(df_travail)):
        row = df_travail.iloc[i].to_dict()
        cap = row["Capital investi"]
        actifs = row["Actifs"]
        epg = row["Epargne totale"]
        
        if i == 0:
            row["Evolution actifs $"] = 0.0 ; row["Evolution actifs %"] = 0.0
            row["Evolution cumulée $"] = actifs - cap
            row["Evolution cumulée %"] = ((actifs - cap) / cap * 100) if cap != 0 else 0.0
            row["Plus-value Épargne $"] = 0.0 ; row["Plus-value Épargne %"] = 0.0
            r_twr = (actifs - cap) / cap if cap != 0 else 0.0
            current_twr_mult *= (1 + r_twr)
        else:
            prev = df_travail.iloc[i-1]
            diff_cap = cap - prev["Capital investi"]
            evo_usd = (actifs - prev["Actifs"]) - diff_cap
            row["Evolution actifs $"] = evo_usd
            row["Evolution actifs %"] = (evo_usd / prev["Actifs"] * 100) if prev["Actifs"] != 0 else 0.0
            row["Evolution cumulée $"] = actifs - cap
            row["Evolution cumulée %"] = ((actifs - cap) / cap * 100) if cap != 0 else 0.0
            row["Plus-value Épargne $"] = epg - first_epargne
            row["Plus-value Épargne %"] = ((epg - first_epargne) / first_epargne * 100) if first_epargne != 0 else 0.0
            base_twr = prev["Actifs"] + diff_cap
            r_twr = evo_usd / base_twr if base_twr != 0 else 0.0
            current_twr_mult *= (1 + r_twr)
            
        row["Score TWR %"] = (current_twr_mult - 1) * 100
        resultats.append(row)
    
    df_final = pd.DataFrame(resultats)
    if 'DT_TRI' in df_final.columns: df_final.drop(columns=['DT_TRI'], inplace=True)
    ordre = ["Date", "Capital investi", "Actifs", "Epargne totale", "Evolution actifs $", "Evolution actifs %", "Evolution cumulée $", "Evolution cumulée %", "Plus-value Épargne $", "Plus-value Épargne %", "Score TWR %"]
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
        
        for index, row in df_temp.iterrows():
            ticker = str(row.get("Ticker", "")).strip().upper()
            if ticker != "" and ticker != "NAN":
                try:
                    asset = yf.Ticker(ticker)
                    try: prix_local = float(asset.fast_info.get('lastPrice', 0.0))
                    except:
                        hist = asset.history(period="1d")
                        prix_local = float(hist['Close'].iloc[-1]) if not hist.empty else 0.0
                        
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

# --- 5. CHARGEMENT INITIAL (DEPUIS LE CLOUD) ---
if "apport_dispo" not in st.session_state: st.session_state.apport_dispo = 0.0

if "donnees" not in st.session_state:
    st.session_state.donnees = nettoyer_dataframe(load_sheet("Donnees", ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]))

if "historique" not in st.session_state:
    df_h = load_sheet("Historique", ["Date", "Type", "Montant $", "Montant €", "Montant Or"])
    for col in ["Montant $", "Montant €", "Montant Or"]:
        if col in df_h.columns: df_h[col] = df_h[col].apply(extraire_nombre)
    st.session_state.historique = df_h

if "projections" not in st.session_state:
    st.session_state.projections = recalculer_toute_la_base_projections(load_sheet("Projections", []))

if "inflation" not in st.session_state:
    df_infl = load_sheet("Inflation", ["Année", "Inflation (%)"])
    if not df_infl.empty and 'Année' in df_infl.columns: df_infl['Année'] = df_infl['Année'].astype(int)
    st.session_state.inflation = df_infl

# --- VARIABLES GLOBALES DE TAUX ---
try: TAUX_EUR_USD = float(yf.Ticker("EURUSD=X").history(period="1d")['Close'].iloc[-1])
except: TAUX_EUR_USD = 1.0

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
    
    if st.button("🔄 Actualiser les cours", use_container_width=True):
        actualiser_cours_internet(silencieux=False)
        st.rerun()
        
    df_actuel = st.session_state.donnees
    df_p = st.session_state.projections
    
    # MODIFICATION : Uniquement les actifs présents dans rééquilibrage (Pct > 0)
    val_invest = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    val_total = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows())
    
    delta = pct_delta = 0.0
    if not df_p.empty:
        derniere_val = extraire_nombre(df_p.iloc[-1]["Actifs"])
        delta = val_invest - derniere_val
        if derniere_val > 0: pct_delta = (delta / derniere_val) * 100

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Global", f"$ {val_total:,.2f}")
    c2.metric("Actifs Stratégiques", f"$ {val_invest:,.2f}", f"{delta:+,.2f} $ ({pct_delta:+.2f} % depuis MAJ)")
    st.divider()
    
    if df_p.empty: st.info("Aucune donnée disponible. Figez d'abord une situation dans l'onglet '🏖️ Suivi'.")
    else:
        df_viz = df_p.copy()
        df_viz['Date_DT'] = pd.to_datetime(df_viz['Date'], dayfirst=True, errors='coerce')
        df_viz = df_viz.dropna(subset=['Date_DT']).sort_values('Date_DT').reset_index(drop=True)
        
        c_f1, c_f2 = st.columns(2)
        with c_f1: filtre = st.radio("Sélectionnez la période :", ["Depuis le début", "Depuis 1 an", "Depuis le début de l'année"], horizontal=True)
        with c_f2: mode_graph = st.radio("Affichage :", ["Rendement Absolu (ROI)", "Score TWR (Talent)"], horizontal=True)
            
        st.divider()
        now = pd.Timestamp.now()
        
        if filtre == "Depuis 1 an": df_viz = df_viz[df_viz['Date_DT'] >= (now - pd.DateOffset(years=1))]
        elif filtre == "Depuis le début de l'année": df_viz = df_viz[df_viz['Date_DT'] >= pd.Timestamp(year=now.year, month=1, day=1)]
                
        if df_viz.empty: st.warning(f"Aucun enregistrement trouvé.")
        else:
            df_viz.set_index('Date_DT', inplace=True)
            val_debut = df_viz['Evolution cumulée $'].iloc[0]
            val_fin = df_viz['Evolution cumulée $'].iloc[-1]
            actifs_debut = df_viz['Actifs'].iloc[0]
            
            delta_usd = val_fin - val_debut
            pct_periode = (delta_usd / actifs_debut * 100) if actifs_debut > 0 else 0.0
            pct_global = df_viz['Evolution cumulée %'].iloc[-1]
            
            twr_debut = df_viz['Score TWR %'].iloc[0]
            twr_fin = df_viz['Score TWR %'].iloc[-1]
            mult_d, mult_f = 1 + (twr_debut / 100), 1 + (twr_fin / 100)
            twr_periode = ((mult_f / mult_d) - 1) * 100 if mult_d != 0 else 0.0
            
            st.subheader(f"📈 Analyse de la Performance")
            c1_g, c2_g = st.columns([1, 3])
            
            with c1_g:
                if "ROI" in mode_graph:
                    st.metric("Gains nets totaux ($)", f"$ {val_fin:,.2f}", f"{delta_usd:+,.2f} $ ({pct_periode:+.2f} % sur la période)")
                    st.caption(f"soit en valeur finale : **{val_fin / TAUX_EUR_USD:,.2f} €**")
                    color = "green" if pct_global > 0 else "red" if pct_global < 0 else "gray"
                    st.markdown(f"📊 Rentabilité Globale : <strong style='color:{color}'>{pct_global:+.2f} %</strong>", unsafe_allow_html=True)
                else:
                    st.metric("Score TWR Global (%)", f"{twr_fin:+.2f} %", f"{twr_periode:+.2f} % (sur la période)")
                    st.markdown(f"💵 Gains nets actuels : **$ {val_fin:,.2f}**")
                    
            with c2_g:
                if "ROI" in mode_graph: st.line_chart(df_viz['Evolution cumulée $'])
                else: st.line_chart(df_viz['Score TWR %'])
            
            st.divider()
            st.subheader("🥧 Répartition de la Stratégie (Rééquilibrage)")
            
            df_actifs = st.session_state.donnees.copy()
            df_actifs['Val_Num'] = df_actifs['Valeur totale'].apply(extraire_nombre)
            df_actifs['Pct_Cible'] = df_actifs['Pourcentage (%)'].apply(extraire_nombre)
            
            # FILTRE : Uniquement les actifs cibles
            df_strat = df_actifs[df_actifs['Pct_Cible'] > 0]
            
            c_p1, c_p2 = st.columns(2)
            with c_p1:
                st.markdown("**Classes d'actifs ciblées**")
                df_pie1 = df_strat[df_strat['Val_Num'] > 0].groupby('Type')['Val_Num'].sum().reset_index()
                if not df_pie1.empty:
                    fig1 = px.pie(df_pie1, values='Val_Num', names='Type', color='Type', color_discrete_map={"🛢️ Action": "#e74c3c", "📜 Obligation": "#3498db", "💰 Or-BTC": "#f1c40f", "💵 Cash": "#2ecc71"}, hole=0.4)
                    fig1.update_traces(textposition='inside', textinfo='percent+label')
                    fig1.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
                    st.plotly_chart(fig1, use_container_width=True)

            with c_p2:
                st.markdown("**Détail des lignes stratégiques**")
                if not df_strat.empty:
                    fig2 = px.pie(df_strat[df_strat['Val_Num'] > 0], values='Val_Num', names='Ticker', hole=0.4)
                    fig2.update_traces(textposition='inside', textinfo='percent+label')
                    fig2.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
                    st.plotly_chart(fig2, use_container_width=True)

elif page_choisie == "📋 Liste des actifs":
    st.title("📋 Liste de mes actifs")
    
    df_actuel = st.session_state.donnees
    # Affichage cohérent avec le dashboard
    val_invest = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
    val_total = sum(extraire_nombre(r["Valeur totale"]) for _, r in df_actuel.iterrows())
    somme_p = sum(extraire_nombre(r["Pourcentage (%)"]) for _, r in df_actuel.iterrows())

    c1, c2, c3 = st.columns(3)
    c1.metric("Actifs Stratégiques", f"$ {val_invest:,.2f}".replace(',',' '))
    c1.caption(f"soit {val_invest / TAUX_EUR_USD:,.2f} €")
    c2.metric("Total Global", f"$ {val_total:,.2f}".replace(',',' '))
    c2.caption(f"soit {val_total / TAUX_EUR_USD:,.2f} €")
    c3.metric("Répartition Cible", f"{round(somme_p, 2):.2f}%", f"{round(100 - somme_p, 2):.2f}% restant", delta_color="inverse" if somme_p > 100 else "normal")

    st.divider()

    if st.button("🔄 Actualiser les cours", use_container_width=True):
        actualiser_cours_internet(silencieux=False)
        st.rerun()

    config_actifs = {
        "Ticker": st.column_config.TextColumn("Ticker ✍️"),
        "Type": st.column_config.SelectboxColumn("Type ✍️", options=["🛢️ Action", "📜 Obligation", "💰 Or-BTC", "💵 Cash"]),
        "Quantité": st.column_config.TextColumn("Quantité ✍️"),
        "Pourcentage (%)": st.column_config.NumberColumn("Pourcentage (%) ✍️", format="%.2f%%"),
        "Court": st.column_config.TextColumn("Court 🔒", disabled=True),
        "Valeur totale": st.column_config.TextColumn("Valeur totale 🔒", disabled=True)
    }
    
    m_dev = df_actuel.apply(lambda row: est_devise_liquide(row.get("Ticker", "")), axis=1)
    res_i = st.data_editor(df_actuel[~m_dev], key="ei", column_config=config_actifs, use_container_width=True, hide_index=True, num_rows="dynamic")
    res_d = st.data_editor(df_actuel[m_dev], key="ed", column_config=config_actifs, use_container_width=True, hide_index=True, num_rows="dynamic")

    new_df = pd.concat([res_i, res_d], ignore_index=True)
    if not new_df.equals(st.session_state.donnees):
        st.session_state.donnees = new_df
        recalculer_totaux_locaux()
        save_sheet("Donnees", st.session_state.donnees)
        st.rerun()

elif page_choisie == "⚖️ Rééquilibrage":
    st.title("⚖️ Stratégie de Rééquilibrage")
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
            
            qte_fmt = f"{abs(round(qte, 6)):.6f}" if "BTC" in tick else f"{abs(int(round(qte)))}"
            signe = "+ " if qte > 0.000001 else "- " if qte < -0.000001 else ""
            
            if abs(diff) < 1000 and abs(pct_reel - (pct_cib * 100)) < 2.0: action, qte_str = "✅ ÉQUILIBRÉ", f"({signe}{qte_fmt})"
            else: action, qte_str = f"{'🟢 ACHETER' if diff > 0 else '🔴 VENDRE'} $ {abs(diff):,.2f}", f"{signe}{qte_fmt}"
            
            reeq_list.append({"Ticker 🔒": tick, "Actuel ($) 🔒": val_act, "Écart (%) 🔒": (pct_reel - (pct_cib * 100)), "Action 🔒": action, "Qté (+/-) 🔒": qte_str})
        
        def color_reeq(v):
            if "ACHETER" in str(v) or "+" in str(v): return 'color: #2ecc71'
            if "VENDRE" in str(v) or "-" in str(v): return 'color: #e74c3c'
            return 'color: #95a5a6'
        st.dataframe(pd.DataFrame(reeq_list).style.format({"Actuel ($) 🔒": "$ {:,.2f}", "Écart (%) 🔒": "{:+.2f} %"}).map(color_reeq, subset=["Action 🔒", "Qté (+/-) 🔒"]), use_container_width=True, hide_index=True)

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
    st.metric("Total Apports nets ($)", f"$ {apports:,.2f}")
    
    if not df_h.empty:
        df_h_v = df_h.copy()
        df_h_v.columns = [f"{col} 🔒" for col in df_h_v.columns]
        df_h_v['DT'] = pd.to_datetime(df_h_v['Date 🔒'], dayfirst=True, errors='coerce')
        st.dataframe(df_h_v.sort_values('DT', ascending=False).drop(columns=['DT']).style.format({"Montant $ 🔒": "$ {:,.2f}", "Montant € 🔒": "{:,.2f} €", "Montant Or 🔒": "{:,.4f} oz"}), use_container_width=True, hide_index=True)

elif page_choisie == "🏖️ Suivi":
    st.title("🏖️ Suivi & Évolution")
    st.write("Ce tableau enregistre vos points de passage. **En cas d'erreur de clic sur le bouton MAJ, vous pouvez modifier les 4 premières colonnes manuellement (✍️).** Les autres (🔒) se recalculeront automatiquement.")
    
    if st.button("📸 MAJ Graphiques", use_container_width=True, type="primary"):
        cap = sum(row["Montant $"] if "ajout" in row["Type"].lower() else -row["Montant $"] for _, row in st.session_state.historique.iterrows())
        # MODIFICATION : Ici aussi on ne suit que les actifs cibles pour la performance
        act = sum(extraire_nombre(r["Valeur totale"]) for _, r in st.session_state.donnees.iterrows() if extraire_nombre(r["Pourcentage (%)"]) > 0)
        epg = sum(extraire_nombre(r["Valeur totale"]) for _, r in st.session_state.donnees.iterrows())
        nl = {"Date": datetime.datetime.now().strftime("%d/%m/%Y"), "Capital investi": cap, "Actifs": act, "Epargne totale": epg}
        st.session_state.projections = recalculer_toute_la_base_projections(pd.concat([st.session_state.projections, pd.DataFrame([nl])], ignore_index=True))
        save_sheet("Projections", st.session_state.projections)
        st.rerun()

    if not st.session_state.projections.empty:
        df_v = st.session_state.projections.copy()
        df_v['DT'] = pd.to_datetime(df_v['Date'], dayfirst=True, errors='coerce')
        
        config_suivi = {
            "Date": st.column_config.TextColumn("Date ✍️"),
            "Capital investi": st.column_config.NumberColumn("Capital investi ✍️", format="$ %.2f"),
            "Actifs": st.column_config.NumberColumn("Actifs ✍️", format="$ %.2f"),
            "Epargne totale": st.column_config.NumberColumn("Epargne totale ✍️", format="$ %.2f"),
            "Evolution actifs $": st.column_config.NumberColumn("Evol. Actifs ($) 🔒", format="$ %+.2f", disabled=True),
            "Evolution actifs %": st.column_config.NumberColumn("Evol. Actifs (%) 🔒", format="%+.2f %%", disabled=True),
            "Evolution cumulée $": st.column_config.NumberColumn("Evol. Cumulée ($) 🔒", format="$ %+.2f", disabled=True),
            "Evolution cumulée %": st.column_config.NumberColumn("Evol. Cumulée (%) 🔒", format="%+.2f %%", disabled=True),
            "Plus-value Épargne $": st.column_config.NumberColumn("PV Épargne ($) 🔒", format="$ %+.2f", disabled=True),
            "Plus-value Épargne %": st.column_config.NumberColumn("PV Épargne (%) 🔒", format="%+.2f %%", disabled=True),
            "Score TWR %": st.column_config.NumberColumn("Score TWR (%) 🔒", format="%+.2f %%", disabled=True)
        }
        
        edited_suivi = st.data_editor(df_v.sort_values('DT', ascending=False).drop(columns=['DT']), column_config=config_suivi, use_container_width=True, hide_index=True)
        
        if not edited_suivi.equals(df_v.sort_values('DT', ascending=False).drop(columns=['DT'])):
            st.session_state.projections = recalculer_toute_la_base_projections(edited_suivi[["Date", "Capital investi", "Actifs", "Epargne totale"]])
            save_sheet("Projections", st.session_state.projections)
            st.rerun()

elif page_choisie == "📈 Performance":
    st.title("📈 Performances Annuelles & Inflation")
    df_p = st.session_state.projections

    if df_p.empty: st.info("Aucune donnée disponible. Figez d'abord une situation dans l'onglet '🏖️ Suivi'.")
    else:
        try: or_px = float(yf.Ticker("GC=F").fast_info.get('lastPrice', 2000.0))
        except: or_px = 2000.0

        df_viz = df_p.copy()
        df_viz['Date_DT'] = pd.to_datetime(df_viz['Date'], dayfirst=True, errors='coerce')
        df_viz = df_viz.dropna(subset=['Date_DT']).sort_values('Date_DT')
        df_viz['Année'] = df_viz['Date_DT'].dt.year

        df_y = df_viz.groupby('Année').last().reset_index()
        df_y['TWR_mult'] = 1 + (df_y['Score TWR %'] / 100)
        df_y['TWR_mult_prev'] = df_y['TWR_mult'].shift(1).fillna(1.0)
        df_y['Performance brute (%)'] = ((df_y['TWR_mult'] / df_y['TWR_mult_prev']) - 1) * 100

        df_y = df_y.merge(st.session_state.inflation, on='Année', how='left').fillna({'Inflation (%)': 0.0})
        df_y['Performance nette (%)'] = (((1 + df_y['Performance brute (%)'] / 100) / (1 + df_y['Inflation (%)'] / 100)) - 1) * 100
        df_y['Gains Nets ($)'] = df_y['Evolution cumulée $'] - df_y['Evolution cumulée $'].shift(1).fillna(0)
        df_y['Valeur Bilan (Or)'] = df_y['Actifs'] / or_px
        
        df_hist = df_y[df_y['Année'] < datetime.datetime.now().year]
        st.subheader("📊 Moyennes Historiques (Hors année en cours)")
        
        if not df_hist.empty:
            c_m1, c_m2, c_m3, c_m4 = st.columns(4)
            c_m1.metric("Moyenne Perf. Brute", f"{df_hist['Performance brute (%)'].mean():+.2f} %")
            c_m2.metric("Moyenne Inflation", f"{df_hist['Inflation (%)'].mean():.2f} %")
            c_m3.metric("Moyenne Perf. Nette", f"{df_hist['Performance nette (%)'].mean():+.2f} %")
            c_m4.metric("Moyenne Gains / An", f"$ {df_hist['Gains Nets ($)'].mean():+,.2f}")
        else: st.info("L'historique est insuffisant.")
        
        st.divider()
        st.write("Ce tableau récapitule vos résultats par année civile. **Double-cliquez sur la colonne 'Inflation ✍️'** pour y saisir l'inflation manuellement. Le reste est verrouillé (🔒).")
        
        df_display = df_y[['Année', 'Performance brute (%)', 'Inflation (%)', 'Performance nette (%)', 'Gains Nets ($)', 'Actifs', 'Valeur Bilan (Or)']].copy()
        df_display.columns = ['Année', 'Performance brute (%)', 'Inflation (%)', 'Performance nette (%)', 'Gains Nets ($)', 'Valeur Bilan ($)', 'Valeur Bilan (Or)']
        df_display['Année'] = df_display['Année'].astype(str)

        edited_df = st.data_editor(
            df_display.sort_values(by='Année', ascending=False),
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

        if not edited_df['Inflation (%)'].equals(df_display.sort_values(by='Année', ascending=False)['Inflation (%)']):
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

    st.sidebar.subheader("⚙️ Paramètres de Retraite")
    annee_retraite = st.sidebar.number_input("Année de départ (1er Janvier) ✍️", min_value=annee_en_cours+1, max_value=2100, value=2055, step=1)
    apport_mensuel = st.sidebar.number_input("Apport mensuel d'aujourd'hui ($) ✍️", min_value=0.00, value=250.00, step=50.00)
    inflation_estimee = st.sidebar.number_input("Inflation annuelle estimée (%) ✍️", min_value=0.00, value=2.00, step=0.01)
    rendement_b = st.sidebar.number_input("Performance Scénario B (%) ✍️", min_value=0.00, value=8.00, step=0.01)
    
    st.info(f"💡 **Info :** Vos apports de {apport_mensuel:,.2f} $ augmenteront de {inflation_estimee:.2f} % chaque année dans le simulateur pour suivre l'évolution de votre salaire et de la vie.")
    
    c_s1, c_s2 = st.columns(2)
    with c_s1: rendement_a = st.slider("Ajuster la Performance (Scénario A) ✍️", min_value=0.00, max_value=30.00, value=round(max(0.00, float(moy_brute_hist)), 2), step=0.01, help="Par défaut : moyenne de vos performances passées.")
    with c_s2: taxe_plus_value = st.slider("Fiscalité sur les retraits (Flat Tax) (%) ✍️", min_value=0.00, max_value=60.00, value=30.00, step=0.10)

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
        st.metric("💰 Valeur Brute du Magot 🔒", f"$ {cap_a_nom:,.2f}")
        st.caption(f"soit **{cap_a_nom / TAUX_EUR_USD:,.2f} €**")
        st.write("")
        st.metric("🛒 Valeur Nette (Pouvoir d'achat) 🔒", f"$ {cap_a_real:,.2f}")
        st.caption(f"soit **{cap_a_real / TAUX_EUR_USD:,.2f} €**")
        st.write("")
        st.markdown(f"<h4 style='color: #2ecc71; margin-bottom: 0px;'>Rente Mensuelle Nette : $ {rente_a_reelle:,.2f}</h4>", unsafe_allow_html=True)
        st.caption(f"soit **{rente_a_reelle / TAUX_EUR_USD:,.2f} €** (Avant impôts).", unsafe_allow_html=True)
        st.markdown(f"<h5 style='color: #e67e22; margin-top: 10px; margin-bottom: 0px;'>Après Impôts ({taxe_plus_value:.1f}%) : $ {rente_a_reelle * (1 - taxe_plus_value / 100.0):,.2f}</h5>", unsafe_allow_html=True)
        st.caption(f"soit **{rente_a_reelle * (1 - taxe_plus_value / 100.0) / TAUX_EUR_USD:,.2f} €** nets dans votre poche.", unsafe_allow_html=True)

    with colB:
        st.markdown(f"### Scénario B (Fixe : {rendement_b:.2f} % / an)")
        st.metric("💰 Valeur Brute du Magot 🔒", f"$ {cap_b_nom:,.2f}")
        st.caption(f"soit **{cap_b_nom / TAUX_EUR_USD:,.2f} €**")
        st.write("")
        st.metric("🛒 Valeur Nette (Pouvoir d'achat) 🔒", f"$ {cap_b_real:,.2f}")
        st.caption(f"soit **{cap_b_real / TAUX_EUR_USD:,.2f} €**")
        st.write("")
        st.markdown(f"<h4 style='color: #3498db; margin-bottom: 0px;'>Rente Mensuelle Nette : $ {rente_b_reelle:,.2f}</h4>", unsafe_allow_html=True)
        st.caption(f"soit **{rente_b_reelle / TAUX_EUR_USD:,.2f} €** (Avant impôts).", unsafe_allow_html=True)
        st.markdown(f"<h5 style='color: #e67e22; margin-top: 10px; margin-bottom: 0px;'>Après Impôts ({taxe_plus_value:.1f}%) : $ {rente_b_reelle * (1 - taxe_plus_value / 100.0):,.2f}</h5>", unsafe_allow_html=True)
        st.caption(f"soit **{rente_b_reelle * (1 - taxe_plus_value / 100.0) / TAUX_EUR_USD:,.2f} €** nets dans votre poche.", unsafe_allow_html=True)

    st.divider()
    st.subheader("📈 Évolution du Pouvoir d'Achat Réel (Capital Net)")
    
    if trajectory_data:
        df_traj_melted = pd.DataFrame(trajectory_data).melt(id_vars="Année", var_name="Scénario", value_name="Valeur Nette ($)")
        fig = px.line(df_traj_melted, x="Année", y="Valeur Nette ($)", color="Scénario", color_discrete_map={"Capital Net (Scénario A)": "#2ecc71", "Capital Net (Scénario B)": "#3498db"})
        fig.update_layout(yaxis_title="Capital Net d'Inflation ($)", xaxis_title="Année", legend_title="")
        st.plotly_chart(fig, use_container_width=True)
