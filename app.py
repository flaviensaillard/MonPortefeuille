import streamlit as st, pandas as pd, yfinance as yf, re, datetime, time, plotly.express as px, gspread, urllib.request, json
from streamlit_autorefresh import st_autorefresh
from gspread_dataframe import set_with_dataframe, get_as_dataframe
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Mon Portefeuille", layout="wide")
st_autorefresh(interval=15 * 60 * 1000, key="datarefresh")

def check_password():
    if "pwd_ok" not in st.session_state: st.session_state["pwd_ok"] = False
    if not st.session_state["pwd_ok"]:
        st.markdown("<h1 style='text-align: center;'>🔒 Accès Sécurisé</h1>", unsafe_allow_html=True)
        pwd = st.text_input("Mot de passe :", type="password")
        if pwd == st.secrets["APP_PASSWORD"]: st.session_state["pwd_ok"] = True; st.rerun()
        elif pwd != "": st.error("Accès refusé.")
        return False
    return True

if not check_password(): st.stop()

@st.cache_resource
def init_gs():
    gc = gspread.authorize(Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']))
    return gc.open_by_key("1hkZoHQ1vvtbI1DYHR_OnofWn4jG92JGyxJjN-FedsWk")

try: sh = init_gs()
except: st.error("Erreur Sheets."); st.stop()

def load_sh(name, cols):
    try:
        df = get_as_dataframe(sh.worksheet(name), evaluate_formulas=True).dropna(how='all').dropna(axis=1, how='all')
        return df if not df.empty else pd.DataFrame(columns=cols)
    except: return pd.DataFrame(columns=cols)

def save_sh(name, df):
    try: ws = sh.worksheet(name)
    except: ws = sh.add_worksheet(title=name, rows=100, cols=20)
    ws.clear(); set_with_dataframe(ws, df, include_index=False)

try: TAUX_EUR_USD = float(yf.Ticker("EURUSD=X").history(period="1d")['Close'].iloc[-1])
except: TAUX_EUR_USD = 1.0

def ext_nb(v):
    if pd.isna(v) or str(v).strip()=="" or str(v).lower()=="nan": return 0.0
    n = re.sub(r'[^\d,.-]', '', str(v)).replace(',', '') if ',' in re.sub(r'[^\d,.-]', '', str(v)) and '.' in re.sub(r'[^\d,.-]', '', str(v)) else re.sub(r'[^\d,.-]', '', str(v)).replace(',', '.')
    try: return round(float(n), 5)
    except: return 0.0

def save_cfg(k, v):
    st.session_state.config[k] = v
    try: save_sh("Config", pd.DataFrame(list(st.session_state.config.items()), columns=["Clé", "Valeur"]))
    except: pass

def aff_mt(lbl, mt_usd, d_str="", c_val=None, t="large"):
    mt_eur, s_usd, s_eur = mt_usd/TAUX_EUR_USD, f"{mt_usd:,.2f}".replace(',',' '), f"{mt_usd/TAUX_EUR_USD:,.2f}".replace(',',' ')
    dh = f"<div style='font-size:0.9rem; font-weight:600; color:{'#2ecc71' if '+' in d_str else ('#e74c3c' if '-' in d_str else 'inherit')}; padding-top:0.2rem;'>{d_str}</div>" if d_str else ""
    t_v, t_l = ("1.8rem", "0.9rem") if t=="large" else ("1.4rem", "0.85rem") if t=="medium" else ("1.2rem", "0.85rem")
    st.markdown(f'<div style="margin-bottom:0.8rem;"><div style="font-size:{t_l}; opacity:0.8;">{lbl}</div><div style="font-size:{t_v}; font-weight:600; {f"color:{c_val};" if c_val else ""}">{s_usd} $ <span style="font-size:0.65em; opacity:0.7;">/ {s_eur} €</span></div>{dh}</div>', unsafe_allow_html=True)

def est_dev(t): return str(t).upper().strip().endswith("=X") or any(m in str(t).upper() for m in ["USD","EUR","CHF","JPY","CNY","GBP"])

def clean_df(df):
    for c in df.columns:
        if "quantit" in str(c).lower(): df.rename(columns={c: "Quantité"}, inplace=True)
    if "Type" not in df.columns: df["Type"] = ["💵 Cash" if est_dev(t) else "₿ Crypto" if any(c in t for c in ["BTC","ETH","USDT"]) else "🛢️ Action" for t in df.get("Ticker", [""]).astype(str).str.upper()]
    for c in ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]:
        if c not in df.columns: df[c] = 0.0 if c=="Pourcentage (%)" else "$ 0.00" if c in ["Court", "Valeur totale"] else ""
    return df[["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]].reset_index(drop=True)

def calc_proj(df):
    if df is None or df.empty: return df
    d = df.copy()
    for i, n in enumerate(["Date", "Capital investi", "Actifs Stratégiques", "Total Global"]):
        if i < len(d.columns): d.rename(columns={d.columns[i]: n}, inplace=True)
    for c in ["Capital investi", "Actifs Stratégiques", "Total Global"]: d[c] = d[c].apply(ext_nb)
    d['DT_TRI'] = pd.to_datetime(d['Date'], dayfirst=True, errors='coerce')
    d = d.sort_values('DT_TRI').reset_index(drop=True)
    res, ct, tt = [], 1.0, 1.0
    for i in range(len(d)):
        r = d.iloc[i].to_dict()
        cap, act, tg = r["Capital investi"], r["Actifs Stratégiques"], r["Total Global"]
        if i == 0:
            r["Evolution actifs $"], r["Evolution actifs %"], r["Evolution cumulée $"], r["Evolution cumulée %"] = 0.0, 0.0, act-cap, ((act-cap)/cap*100) if cap!=0 else 0.0
            r["TG_Evolution cumulée $"], r["TG_Evolution cumulée %"] = tg-cap, ((tg-cap)/cap*100) if cap!=0 else 0.0
            ct *= (1 + ((act-cap)/cap if cap!=0 else 0.0)); tt *= (1 + ((tg-cap)/cap if cap!=0 else 0.0))
        else:
            p = d.iloc[i-1]; dc = cap - p["Capital investi"]; eu = (act - p["Actifs Stratégiques"]) - dc; et = (tg - p["Total Global"]) - dc
            r["Evolution actifs $"], r["Evolution actifs %"] = eu, (eu/p["Actifs Stratégiques"]*100) if p["Actifs Stratégiques"]!=0 else 0.0
            r["Evolution cumulée $"], r["Evolution cumulée %"] = act-cap, ((act-cap)/cap*100) if cap!=0 else 0.0
            ct *= (1 + (eu/(p["Actifs Stratégiques"]+dc) if (p["Actifs Stratégiques"]+dc)!=0 else 0.0))
            r["TG_Evolution cumulée $"], r["TG_Evolution cumulée %"] = tg-cap, ((tg-cap)/cap*100) if cap!=0 else 0.0
            tt *= (1 + (et/(p["Total Global"]+dc) if (p["Total Global"]+dc)!=0 else 0.0))
        r["Score TWR %"], r["TG_Score TWR %"] = (ct-1)*100, (tt-1)*100
        res.append(r)
    return pd.DataFrame(res)[["Date", "Capital investi", "Actifs Stratégiques", "Total Global", "Evolution actifs $", "Evolution actifs %", "Evolution cumulée $", "Evolution cumulée %", "Score TWR %", "TG_Evolution cumulée $", "TG_Evolution cumulée %", "TG_Score TWR %"]]

def recalc_loc():
    if "donnees" in st.session_state:
        d = st.session_state.donnees.copy()
        for idx, r in d.iterrows():
            c, q = ext_nb(r.get("Court", 0)), ext_nb(r.get("Quantité", 0))
            d.at[idx, "Valeur totale"], d.at[idx, "Court"] = f"$ {round(c*q, 2):,.2f}", f"$ {c:.2f}"
        st.session_state.donnees = d

def act_cours(sil=False):
    if "donnees" in st.session_state:
        if not sil: st.toast("🔄 Actualisation des cours...")
        d, chg, tx_c = st.session_state.donnees.copy(), False, {}
        if "variations" not in st.session_state: st.session_state.variations = {}
        for i, r in d.iterrows():
            t = str(r.get("Ticker", "")).strip().upper()
            if t and t != "NAN":
                sb = False
                if t.endswith("USDT"):
                    for b in ["https://api.binance.com", "https://api.binance.us"]:
                        try:
                            data = json.loads(urllib.request.urlopen(urllib.request.Request(f"{b}/api/v3/klines?symbol={t}&interval=1d&limit=2", headers={'User-Agent':'Mozilla/5.0'}), timeout=3).read().decode())
                            pu, pp = (float(data[1][4]), float(data[0][4])) if len(data)>=2 else (float(data[0][4]), float(data[0][4]))
                            v = ((pu-pp)/pp)*100 if pp>0 else 0.0
                            st.session_state.variations[t] = f"{'↗' if v>0 else '↘' if v<0 else '→'} {v:+.2f} %"
                            d.at[i, "Court"] = f"$ {pu:.2f}"; chg = sb = True; break
                        except: continue
                if sb: continue
                try:
                    ast = yf.Ticker(t.replace("USDT", "-USD"))
                    pl = float(ast.fast_info.get('lastPrice', float(ast.history(period="1d")['Close'].iloc[-1] if not ast.history(period="1d").empty else 0.0)))
                    pp = float(ast.fast_info.get('previous_close', float(ast.history(period="5d")['Close'].iloc[-2] if len(ast.history(period="5d"))>=2 else 0.0)))
                    st.session_state.variations[t] = f"{'↗' if (pl-pp)>0 else '↘' if (pl-pp)<0 else '→'} {((pl-pp)/pp)*100 if pp>0 else 0.0:+.2f} %"
                    if pl>0:
                        dev = str(ast.fast_info.get('currency', 'USD')).strip().upper(); fdev = 0.01 if dev=="GBP" else 1.0
                        pu = pl * fdev
                        if dev not in ["USD","","NONE","GBP"]:
                            if dev not in tx_c:
                                try: tx_c[dev] = float(yf.Ticker(f"{dev}USD=X").fast_info.get('lastPrice', 1.0))
                                except: tx_c[dev] = 1.0
                            pu *= tx_c[dev]
                        d.at[i, "Court"] = f"$ {pu:.2f}"; chg = True
                except: st.session_state.variations[t] = st.session_state.variations.get(t, "→ 0.00 %")
        if chg: st.session_state.donnees = d; recalc_loc(); save_sh("Donnees", st.session_state.donnees)

@st.cache_data(ttl=86400)
def recup_inf():
    try:
        data = json.loads(urllib.request.urlopen(urllib.request.Request("https://api.worldbank.org/v2/country/FRA/indicator/FP.CPI.TOTL.ZG?format=json&per_page=20", headers={'User-Agent':'Mozilla/5.0'}), timeout=5).read().decode())
        if len(data)==2 and isinstance(data[1], list): return {int(i['date']): round(float(i['value']), 2) for i in data[1] if i['value'] is not None}
    except: pass; return None

def get_fx(dev, d_val):
    c = str(dev).upper().strip()
    if c in ["EUR", ""]: return 1.0
    try:
        d = pd.to_datetime(d_val, dayfirst=True)
        h = yf.Ticker(f"{c}EUR=X").history(start=(d-pd.Timedelta(days=5)).strftime('%Y-%m-%d'), end=(d+pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        return float(h['Close'].iloc[-1]) if not h.empty else 1.0
    except: return 1.0

def get_pru_qty(t, dt):
    dk = dt[dt['Ticker']==t].copy()
    if dk.empty: return 0.0, 0.0
    dk['DT'] = pd.to_datetime(dk['Date'], dayfirst=True, errors='coerce')
    tc = tq = 0.0
    for _, r in dk.dropna(subset=['DT']).sort_values('DT').iterrows():
        ty, q, n = str(r['Type']).lower(), ext_nb(r['Quantité']), ext_nb(r['Montant Net'])
        if "achat" in ty: tc += n; tq += q
        elif "vente" in ty:
            tc -= (tc/tq if tq>0 else 0)*q; tq -= q
            if tq <= 0.00001: tc = tq = 0.0
    return (round(tc/tq, 5) if tq>0 else 0.0), round(tq, 5)

def calc_ir(rev, pts, stt, dec=True):
    q, i = rev/pts, sum((min(rev/pts, l)-p)*t for l,p,t in [(28797,11294,0.11),(82341,28797,0.3),(177106,82341,0.41),(9999999,177106,0.45)] if rev/pts>p)*pts
    if dec:
        lm, bs = (2002, 906) if "Cél" in stt else (3300, 1493)
        if i<=lm: i = max(0, i-(bs-(i*0.4525)))
    return 0.0 if i<61 else i

if "config" not in st.session_state:
    st.session_state.config = {str(r["Clé"]): str(r["Valeur"]) if str(r["Clé"])=="f_statut" else ext_nb(r["Valeur"]) for _, r in load_sh("Config", ["Clé","Valeur"]).iterrows() if pd.notna(r["Clé"])}
for k,v in {"retraite_apport_mensuel":250,"retraite_taxe":30,"f_statut":"Marié","f_enf":0,"f_s1":30000,"f_s2":0,"f_u1":0,"f_k1":0,"f_cv1":5,"f_r1":0,"f_u2":0,"f_k2":0,"f_cv2":5,"f_r2":0}.items():
    if k not in st.session_state.config: st.session_state.config[k] = v

if "donnees" not in st.session_state: st.session_state.donnees = clean_df(load_sh("Donnees", ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]))
if "transactions" not in st.session_state:
    dt = load_sh("Transaction", ["Ticker", "Type", "Date", "Quantité", "Cours", "Frais", "Montant Net", "Devise", "PRU (Devise)", "Taux change (EUR)"])
    for c in ["Quantité", "Cours", "Frais", "Montant Net", "PRU (Devise)", "Taux change (EUR)"]: dt[c] = dt[c].apply(ext_nb)
    st.session_state.transactions = dt
if "historique" not in st.session_state:
    dh = load_sh("Historique", ["Date", "Type", "Montant $", "Montant €", "Montant Or"])
    for c in ["Montant $", "Montant €", "Montant Or"]: dh[c] = dh[c].apply(ext_nb)
    st.session_state.historique = dh
if "projections" not in st.session_state: st.session_state.projections = calc_proj(load_sh("Projections", []))
if "inflation" not in st.session_state:
    di = load_sh("Inflation", ["Année", "Inflation (%)"])
    if not di.empty: di['Année'], di['Inflation (%)'] = pd.to_numeric(di['Année'], errors='coerce').fillna(0).astype(int), pd.to_numeric(di['Inflation (%)'], errors='coerce').fillna(0.0)
    st.session_state.inflation = di.drop_duplicates(subset=['Année'], keep='last')

if "inf_chk" not in st.session_state:
    st.session_state.inf_chk = True; dinf = recup_inf()
    if dinf and not st.session_state.projections.empty:
        dp = st.session_state.projections.copy(); dp['DT'] = pd.to_datetime(dp['Date'], dayfirst=True, errors='coerce')
        n_inf, chg = [], False
        for a in dp.dropna(subset=['DT'])['DT'].dt.year.unique():
            vo = dinf.get(a, 0.0); va = st.session_state.inflation[st.session_state.inflation['Année']==a].iloc[0]['Inflation (%)'] if not st.session_state.inflation[st.session_state.inflation['Année']==a].empty else 0.0
            if vo != va: chg = True
            n_inf.append({'Année': a, 'Inflation (%)': vo})
        if chg: st.session_state.inflation = pd.DataFrame(n_inf); save_sh("Inflation", st.session_state.inflation)

if "last_ref" not in st.session_state: st.session_state.last_ref = 0
if time.time() - st.session_state.last_ref >= 900: act_cours(st.session_state.last_ref==0); st.session_state.last_ref = time.time()

st.sidebar.title("Menu")
page = st.sidebar.radio("Navigation", ["📊 Tableau de bord", "📋 Liste des actifs", "⚖️ Rééquilibrage", "💰 Fonds", "🏖️ Suivi", "📈 Performance", "🌴 Retraite", "🏛️ Fiscalité"])
st.sidebar.divider()
if st.sidebar.button("🔄 Recharger", use_container_width=True): st.session_state.clear(); st.rerun()

if page == "📊 Tableau de bord":
    st.title("📊 Vue d'ensemble")
    da, dp = st.session_state.donnees, st.session_state.projections; v_inv, v_tot = sum(ext_nb(r["Valeur totale"]) for _,r in da.iterrows() if ext_nb(r["Pourcentage (%)"])>0), sum(ext_nb(r["Valeur totale"]) for _,r in da.iterrows())
    dpl = calc_proj(pd.concat([dp, pd.DataFrame([{"Date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "Capital investi": sum(r["Montant $"] if "ajout" in r["Type"].lower() else -r["Montant $"] for _, r in st.session_state.historique.iterrows()), "Actifs Stratégiques": v_inv, "Total Global": v_tot}])], ignore_index=True))
    
    vg = vs = vt_v = vi_v = 0.0
    for _, r in da.iterrows():
        v = ext_nb(r["Valeur totale"]); p = float(re.search(r'([+-]?\d+\.?\d*)', st.session_state.variations.get(str(r["Ticker"]).upper(), "0")).group(1)) if re.search(r'([+-]?\d+\.?\d*)', st.session_state.variations.get(str(r["Ticker"]).upper(), "0")) else 0.0
        vl = v/(1+p/100) if (1+p/100)!=0 else v
        vg += (v-vl); vt_v += vl
        if ext_nb(r["Pourcentage (%)"])>0: vs += (v-vl); vi_v += vl
    
    dtg = dts = pdtg = pdts = 0.0
    if not dp.empty:
        dpd = dp.copy(); dpd['DT'] = pd.to_datetime(dpd['Date'], dayfirst=True, errors='coerce')
        d_p = dpd.dropna(subset=['DT']).sort_values('DT')
        if not d_p.empty:
            rp = d_p[d_p['DT'] <= pd.Timestamp.now()-pd.DateOffset(years=1)].iloc[-1] if not d_p[d_p['DT'] <= pd.Timestamp.now()-pd.DateOffset(years=1)].empty else d_p.iloc[0]
            dts, dtg = v_inv-ext_nb(rp["Actifs Stratégiques"]), v_tot-ext_nb(rp["Total Global"])
            if ext_nb(rp["Actifs Stratégiques"])>0: pdts = dts/ext_nb(rp["Actifs Stratégiques"])*100
            if ext_nb(rp["Total Global"])>0: pdtg = dtg/ext_nb(rp["Total Global"])*100

    breq = val_inv > 0 and any(abs((v_inv*(ext_nb(r["Pourcentage (%)"])/100))-ext_nb(r["Valeur totale"]))>=1000 and abs((ext_nb(r["Valeur totale"])/v_inv*100)-ext_nb(r["Pourcentage (%)"]))>=2.0 for _,r in da.iterrows() if ext_nb(r["Pourcentage (%)"])>0)
    
    cb, cs = st.columns([1, 2])
    with cb:
        if st.button("🔄 Actualiser"): act_cours(False); st.rerun()
    with cs: st.warning("⚠️ Rééquilibrage nécessaire") if breq else st.success("✅ Équilibré")
    
    st.divider(); st.subheader("🌍 Total Global"); ctg, _ = st.columns(2)
    with ctg:
        aff_mt("Total Global", v_tot, f"{dtg:+,.2f} $ ({pdtg:+.2f} % sur 1 an glissant)")
        st.markdown(f"<span>{'📈' if vg>=0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if vg>=0 else '#e74c3c'}'>{vg:+,.2f} $ ({(vg/vt_v*100) if vt_v>0 else 0:+.2f} %)</strong></span>", unsafe_allow_html=True)
    
    if not dp.empty:
        dv = dpl.copy(); dv['DT'] = pd.to_datetime(dv['Date'], dayfirst=True, errors='coerce'); dv = dv.dropna(subset=['DT']).sort_values('DT')
        f_g = st.radio("Période:", ["Début", "1 an", "YTD"], horizontal=True, key="fg")
        if f_g == "1 an": dv = dv[dv['DT'] >= pd.Timestamp.now()-pd.DateOffset(years=1)]
        elif f_g == "YTD": dv = dv[dv['DT'] >= pd.Timestamp(year=pd.Timestamp.now().year-1, month=12, day=31)]
        if not dv.empty:
            st.plotly_chart(px.line(dv, x='DT', y='TG_Evolution cumulée $').update_layout(xaxis_title="", yaxis_title="Gain ($)"), use_container_width=True)
            dpie = da.copy(); dpie['V'] = dpie['Valeur totale'].apply(ext_nb); st.plotly_chart(px.pie(dpie[dpie['V']>0], values='V', names='Type', hole=0.4), use_container_width=True)

    st.divider(); st.subheader("🎯 Actifs Stratégiques"); cst, _ = st.columns(2)
    with cst:
        aff_mt("Actifs Stratégiques", v_inv, f"{dts:+,.2f} $ ({pdts:+.2f} % sur 1 an glissant)")
        st.markdown(f"<span>{'📈' if vs>=0 else '📉'} Aujourd'hui : <strong style='color:{'#2ecc71' if vs>=0 else '#e74c3c'}'>{vs:+,.2f} $ ({(vs/vi_v*100) if vi_v>0 else 0:+.2f} %)</strong></span>", unsafe_allow_html=True)
    
    ds = da[da['Pourcentage (%)'].apply(ext_nb)>0].copy(); ds['V'] = ds['Valeur totale'].apply(ext_nb)
    if not ds.empty: st.plotly_chart(px.pie(ds[ds['V']>0], values='V', names='Ticker', hole=0.4), use_container_width=True)
    
    st.divider(); st.subheader("🏖️ Rente Mensuelle Nette")
    inf = st.slider("Inflation cible à déduire (%)", 0.0, 15.0, 2.0, 0.1)
    aff_mt("Rente Mensuelle Nette (Base 8%)", (v_inv * max(0.0, ((1.08)/(1+inf/100))-1)) / 12.0, couleur_valeur="#3498db")

elif page == "📋 Liste des actifs":
    st.title("📋 Liste des actifs")
    da = st.session_state.donnees.copy()
    vi = sum(ext_nb(r["Valeur totale"]) for _,r in da.iterrows() if ext_nb(r["Pourcentage (%)"])>0); sp = sum(ext_nb(r["Pourcentage (%)"]) for _,r in da.iterrows())
    c1, c2 = st.columns(2)
    with c1: aff_mt("Actifs Stratégiques", vi)
    with c2: st.markdown(f"<div style='font-size:1.8rem; font-weight:bold;'>{sp:.2f} % Cible</div>", unsafe_allow_html=True)
    
    if st.button("🔄 Actualiser les cours"): act_cours(False); st.rerun()
    da['Var'] = da['Ticker'].apply(lambda x: st.session_state.variations.get(str(x).upper(), "→ 0.00 %"))
    
    def cr(v): return 'color:#2ecc71' if "↗" in str(v) or "+" in str(v) else ('color:#e74c3c' if "↘" in str(v) or "-" in str(v) else 'color:#95a5a6')
    m_dev = da.apply(lambda r: est_dev(r.get("Ticker", "")), axis=1); dc = ["Ticker", "Type", "Court", "Quantité", "Valeur totale", "Pourcentage (%)", "Var"]
    
    st.markdown("### 📈 Investissements (Quantité verrouillée)")
    ri = st.data_editor(da[~m_dev][dc].style.map(cr, subset=["Var"]), column_config={"Ticker":st.column_config.TextColumn("Ticker"),"Type":st.column_config.SelectboxColumn("Type",options=["🛢️ Action","📜 Obligation","💰 Or","₿ Crypto"]),"Court":st.column_config.TextColumn("Court",disabled=True),"Quantité":st.column_config.NumberColumn("Quantité",disabled=True,format="%.5f"),"Valeur totale":st.column_config.TextColumn("Valeur totale",disabled=True),"Pourcentage (%)":st.column_config.NumberColumn("Cible %",format="%.2f%%"),"Var":st.column_config.TextColumn("Var",disabled=True)}, use_container_width=True, hide_index=True)
    
    st.markdown("### 💵 Liquidités (Quantité modifiable)")
    rd = st.data_editor(da[m_dev][dc].style.map(cr, subset=["Var"]), column_config={"Ticker":st.column_config.TextColumn("Ticker"),"Type":st.column_config.SelectboxColumn("Type",options=["💵 Cash"]),"Court":st.column_config.TextColumn("Court",disabled=True),"Quantité":st.column_config.NumberColumn("Quantité",disabled=False,format="%.5f"),"Valeur totale":st.column_config.TextColumn("Valeur totale",disabled=True),"Pourcentage (%)":st.column_config.NumberColumn("Cible %",format="%.2f%%"),"Var":st.column_config.TextColumn("Var",disabled=True)}, use_container_width=True, hide_index=True)
    
    n_df = pd.concat([ri, rd], ignore_index=True)[["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]]
    if not n_df.equals(st.session_state.donnees[["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)"]]):
        st.session_state.donnees = n_df; recalc_loc(); save_sh("Donnees", st.session_state.donnees); st.rerun()

elif page == "⚖️ Rééquilibrage":
    st.title("⚖️ Rééquilibrage & Transactions")
    with st.expander("➕ Enregistrer Transaction", expanded=True):
        with st.form("trans"):
            c1, c2, c3 = st.columns(3); f_d = c1.date_input("Date"); l_t = sorted(st.session_state.donnees['Ticker'].dropna().unique().tolist()); l_t.insert(0,"➕ Nouvel Actif")
            f_s = c2.selectbox("Ticker", l_t); f_t = c2.text_input("Saisir Ticker") if f_s=="➕ Nouvel Actif" else f_s; f_ty = c3.selectbox("Type", ["Achat", "Vente"])
            c4, c5, c6 = st.columns(3); f_q = c4.number_input("Quantité", 0.0, format="%.5f"); f_c = c5.number_input("Cours", 0.0, format="%.5f"); f_f = c6.number_input("Frais", 0.0, format="%.2f")
            f_dev = st.selectbox("Devise", ["USD", "EUR", "CHF", "JPY", "GBP"])
            if st.form_submit_button("Valider"):
                if not f_t.strip() or f_q<=0 or f_c<=0: st.error("❌ Formulaire invalide.")
                else:
                    t_c = f_t.upper().strip(); m_n = round((f_q*f_c)+f_f if f_ty=="Achat" else (f_q*f_c)-f_f, 5); fx = get_fx(f_dev, f_d.strftime("%Y-%m-%d"))
                    cpru, cqty = get_pru_qty(t_c, st.session_state.transactions)
                    npru = round(((cpru*cqty)+m_n)/(cqty+f_q), 5) if f_ty=="Achat" and (cqty+f_q)>0 else cpru
                    st.session_state.transactions = pd.concat([st.session_state.transactions, pd.DataFrame([{"Ticker":t_c,"Type":f_ty,"Date":f_d.strftime("%d/%m/%Y"),"Quantité":f_q,"Cours":f_c,"Frais":f_f,"Montant Net":m_n,"Devise":f_dev,"PRU (Devise)":npru,"Taux change (EUR)":fx}])], ignore_index=True)
                    save_sh("Transaction", st.session_state.transactions[[c for c in st.session_state.transactions.columns if c!='Date_DT']])
                    
                    dfd = st.session_state.donnees.copy()
                    if t_c not in dfd['Ticker'].values: dfd = pd.concat([dfd, pd.DataFrame([{"Ticker":t_c,"Type":"₿ Crypto" if any(x in t_c for x in ["BTC","ETH","USDT"]) else "🛢️ Action","Quantité":0.0,"Court":"$ 0","Valeur totale":"$ 0","Pourcentage (%)":0}])], ignore_index=True)
                    ia = dfd.index[dfd['Ticker']==t_c][0]; dfd.at[ia,"Quantité"] = max(0.0, ext_nb(dfd.at[ia,"Quantité"]) + (f_q if f_ty=="Achat" else -f_q))
                    if f_dev not in dfd['Ticker'].values: dfd = pd.concat([dfd, pd.DataFrame([{"Ticker":f_dev,"Type":"💵 Cash","Quantité":0.0,"Court":"$ 0","Valeur totale":"$ 0","Pourcentage (%)":0}])], ignore_index=True)
                    ic = dfd.index[dfd['Ticker']==f_dev][0]; dfd.at[ic,"Quantité"] = max(0.0, ext_nb(dfd.at[ic,"Quantité"]) + (-m_n if f_ty=="Achat" else m_n))
                    st.session_state.donnees = clean_df(dfd); recalc_loc(); save_sh("Donnees", st.session_state.donnees); st.success("✅ Fait !"); time.sleep(1); st.rerun()

    st.divider(); df = st.session_state.donnees
    csh = sum(ext_nb(r["Valeur totale"]) for _,r in df[df.apply(lambda x: est_dev(x["Ticker"]), axis=1)].iterrows())
    bs = sum(ext_nb(r["Valeur totale"]) for _,r in df.iterrows() if ext_nb(r["Pourcentage (%)"])>0) + csh
    
    if bs>0:
        st.info(f"💡 Liquidités disponibles : {csh:,.2f} $"); r_l = []
        for _, r in df.iterrows():
            tc = str(r["Ticker"]).upper(); cb = ext_nb(r["Pourcentage (%)"])/100
            if cb<=0: continue
            ac, pr = ext_nb(r["Valeur totale"]), ext_nb(r["Court"]); d = (bs*cb)-ac; q = d/pr if pr>0 else 0
            pru, _ = get_pru_qty(tc, st.session_state.transactions)
            pf = f"{((pr/pru)-1)*100:+.2f} %" if pru>0 and pr>0 else "N/A"
            r_l.append({"Ticker":tc,"PRU":pru,"Var":st.session_state.variations.get(tc,"→ 0.00 %"),"Perf":pf,"Actuel":ac,"Ecart":ac/bs*100-cb*100,"Action":f"{'✅ OK' if abs(d)<1000 or abs(ac/bs*100-cb*100)<2 else ('🟢 ACHETER' if d>0 else '🔴 VENDRE')} ${abs(d):,.2f}","Qte":f"({'+' if q>0 else '-'}{abs(q):.5f})"})
        st.dataframe(pd.DataFrame(r_l).style.map(lambda v: 'color:#2ecc71' if 'ACHETER' in str(v) or '+' in str(v) else ('color:#e74c3c' if 'VENDRE' in str(v) or '-' in str(v) else 'color:gray'), subset=["Action","Qte","Perf"]), use_container_width=True)

elif page == "💰 Fonds":
    st.title("💰 Fonds"); dh = st.session_state.historique
    with st.form("fm"):
        c1, c2 = st.columns(2); d, t = c1.date_input("Date"), c1.radio("Type", ["Ajout", "Retrait"]); m, dev = c2.number_input("Montant",0.0), c2.selectbox("Devise",["$","€"])
        if st.form_submit_button("Valider"):
            mu = m if dev=="$" else m*TAUX_EUR_USD; dh = pd.concat([dh, pd.DataFrame([{"Date":d.strftime("%d/%m/%Y"),"Type":t,"Montant $":mu,"Montant €":m if dev=="€" else m/TAUX_EUR_USD,"Montant Or":mu/2000.0}])], ignore_index=True)
            dt = st.session_state.donnees.copy(); tk = "USD" if dev=="$" else "EUR"
            if tk not in dt['Ticker'].values: dt = pd.concat([dt, pd.DataFrame([{"Ticker":tk,"Type":"💵 Cash","Quantité":0.0,"Court":"$0","Valeur totale":"$0","Pourcentage (%)":0}])], ignore_index=True)
            ic = dt.index[dt['Ticker']==tk][0]; dt.at[ic,"Quantité"] = max(0.0, ext_nb(dt.at[ic,"Quantité"]) + (m if t=="Ajout" else -m))
            st.session_state.donnees = clean_df(dt); recalc_loc(); save_sh("Donnees", st.session_state.donnees); st.session_state.historique = dh; save_sh("Historique", dh); st.rerun()
    st.dataframe(dh, use_container_width=True)

elif page == "🏖️ Suivi": st.title("🏖️ Suivi"); st.dataframe(st.session_state.projections.sort_index(ascending=False), use_container_width=True)

elif page == "📈 Performance":
    st.title("📈 Performance"); dp = st.session_state.projections
    if dp.empty: st.info("Vide.")
    else:
        dv = dp.copy(); dv['DT'] = pd.to_datetime(dv['Date'], dayfirst=True, errors='coerce'); dy = dv.dropna(subset=['DT']).sort_values('DT').groupby(dv['DT'].dt.year).last().reset_index()
        dy['P'] = (((1+dy['Score TWR %']/100)/(1+dy['Score TWR %'].shift(1).fillna(0)/100))-1)*100
        dy = dy.merge(st.session_state.inflation, left_on=dy['DT'].dt.year, right_on='Année', how='left').fillna({'Inflation (%)':0})
        dy['N'] = (((1+dy['P']/100)/(1+dy['Inflation (%)']/100))-1)*100
        st.dataframe(dy[['DT', 'P', 'Inflation (%)', 'N']], use_container_width=True)

elif page == "🌴 Retraite":
    st.title("🌴 Simulateur Retraite")
    st.write(f"Projection sur un apport de {st.session_state.config.get('retraite_apport_mensuel', 250)} $/mois et Flat Tax {st.session_state.config.get('retraite_taxe', 30)}%.")

elif page == "🏛️ Fiscalité":
    st.title("🏛️ Fiscalité")
    dt = st.session_state.transactions.copy(); dt['DT'] = pd.to_datetime(dt['Date'], dayfirst=True, errors='coerce')
    af = st.selectbox("Année", sorted(dt['DT'].dropna().dt.year.unique().tolist(), reverse=True) if not dt.empty else [2024])
    st.divider()
    
    def sf():
        for k in ["in_statut","in_enf","in_s1","in_s2","in_u1","in_k1","in_cv1","in_r1","in_u2","in_k2","in_cv2","in_r2"]:
            if k in st.session_state: st.session_state.config[k.replace("in_","f_")] = st.session_state[k]
        save_cfg("dummy", 0)

    c1, c2 = st.columns(2)
    stm = c1.radio("Statut", ["Célibataire", "Marié"], index=1 if "Marié" in str(st.session_state.config.get("f_statut","")) else 0, key="in_statut", on_change=sf)
    enf = c1.number_input("Enfants", 0, 10, int(st.session_state.config.get("f_enf",0)), key="in_enf", on_change=sf)
    s1 = c2.number_input("Salaire 1", 0.0, value=float(st.session_state.config.get("f_s1",30000)), key="in_s1", on_change=sf)
    s2 = c2.number_input("Salaire 2", 0.0, value=float(st.session_state.config.get("f_s2",0)), key="in_s2", on_change=sf) if "Marié" in stm else 0.0
    
    cf1, cf2 = st.columns(2)
    with cf1:
        u1 = st.checkbox("Frais réels 1", value=bool(st.session_state.config.get("f_u1",0)), key="in_u1", on_change=sf)
        k1 = st.number_input("KM1",0, value=int(st.session_state.config.get("f_k1",0)), key="in_k1", on_change=sf) if u1 else 0
        cv1 = st.selectbox("CV1",[3,4,5,6,7], index=[3,4,5,6,7].index(int(st.session_state.config.get("f_cv1",5))), key="in_cv1", on_change=sf) if u1 else 5
        r1 = st.number_input("Repas1",0, value=int(st.session_state.config.get("f_r1",0)), key="in_r1", on_change=sf) if u1 else 0
    with cf2:
        u2 = st.checkbox("Frais réels 2", value=bool(st.session_state.config.get("f_u2",0)), key="in_u2", on_change=sf) if "Marié" in stm else False
        k2 = st.number_input("KM2",0, value=int(st.session_state.config.get("f_k2",0)), key="in_k2", on_change=sf) if u2 else 0
        cv2 = st.selectbox("CV2",[3,4,5,6,7], index=[3,4,5,6,7].index(int(st.session_state.config.get("f_cv2",5))), key="in_cv2", on_change=sf) if u2 else 5
        r2 = st.number_input("Repas2",0, value=int(st.session_state.config.get("f_r2",0)), key="in_r2", on_change=sf) if u2 else 0

    dv = dt[(dt['Type'].str.lower().str.contains('vente')) & (dt['DT'].dt.year == af)].copy()
    pva = pvc = 0.0
    if not dv.empty:
        rf = []
        for _, r in dv.iterrows():
            t = str(r['Ticker']).upper(); q, n, pru, fx = r['Quantité'], r['Montant Net'], r.get('PRU (Devise)',0), r.get('Taux change (EUR)',1)
            pv = (n - (pru*q)) * fx; cat = "Crypto" if any(c in t for c in ["BTC","ETH","USDT"]) else "Action"
            if cat=="Action": pva += pv
            else: pvc += pv
            rf.append({"Actif":t, "Qté":q, "PRU":pru, "Net":n, "FX":fx, "PV €":pv})
        st.subheader("📝 Détail des Ventes")
        st.dataframe(pd.DataFrame(rf), use_container_width=True)

    fr1 = max(s1*0.1, calcul_frais_km(k1,cv1)+r1*5.35); fr2 = max(s2*0.1, calcul_frais_km(k2,cv2)+r2*5.35)
    rn = s1 - fr1 + s2 - fr2; pt = (1 if "Cél" in stm else 2) + (0.5 if enf<=2 else 0)*enf + (1 if enf>=3 else 0)
    ims = calcul_impot_ir(rn, pt, stm); imb = calcul_impot_ir(rn+pva, pt, stm)
    
    st.subheader("💡 Bilan Fiscal")
    if pva > 0:
        pfu = pva * 0.3; bar = (imb-ims)+(pva*0.172)
        st.success(f"Option : **{'Barème' if bar<pfu else 'PFU'}** (Coût: {min(bar,pfu):,.2f} €)")
    
    tp1 = (calcul_impot_ir(s1-fr1, 1, "Célibataire", False)/s1*100) if s1>0 else 0
    tf = (ims/(s1+s2)*100) if s1+s2>0 else 0
    st.info(f"Taux Foyer : {tf:.1f}% | Taux Perso 1 : {tp1:.1f}%")
