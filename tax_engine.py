import streamlit as st
import pandas as pd
import json
import yfinance as yf
from utils import extraire_nombre, format_smart, est_devise_liquide, is_crypto_ticker
from api_client import get_historical_fx, get_historical_usd_rate

def calcul_frais_km(km, cv):
    try:
        bareme = json.loads(st.session_state.config.get("urssaf_bareme", '{"3":[0.529, 0.316, 1065, 0.370], "4":[0.606, 0.340, 1330, 0.407], "5":[0.636, 0.357, 1395, 0.427], "6":[0.665, 0.374, 1457, 0.447], "7":[0.697, 0.394, 1515, 0.470]}'))
        c = bareme.get(str(cv), bareme["7"])
    except: c = [0.697, 0.394, 1515, 0.470]
    return km * c[0] if km <= 5000 else (km * c[1] + c[2] if km <= 20000 else km * c[3])

def calcul_impot_ir(rev, parts, stat, apply_decote=True):
    qf = rev / parts; imp = 0
    t1 = float(st.session_state.config.get("tax_lim_1", 11294))
    t2 = float(st.session_state.config.get("tax_lim_2", 28797))
    t3 = float(st.session_state.config.get("tax_lim_3", 82341))
    t4 = float(st.session_state.config.get("tax_lim_4", 177106))
    r2 = float(st.session_state.config.get("tax_rate_2", 0.11))
    r3 = float(st.session_state.config.get("tax_rate_3", 0.30))
    r4 = float(st.session_state.config.get("tax_rate_4", 0.41))
    r5 = float(st.session_state.config.get("tax_rate_5", 0.45))
    tr = [(t1, 0.0), (t2, r2), (t3, r3), (t4, r4), (999999999.0, r5)]
    prev_lim = 0.0
    for lim, tx in tr:
        if qf > prev_lim: imp += (min(qf, lim) - prev_lim) * tx
        prev_lim = lim
    imp *= parts
    if apply_decote:
        lim_decote = float(st.session_state.config.get("decote_lim_cel", 2002)) if "Cél" in stat else float(st.session_state.config.get("decote_lim_mar", 3300))
        base_decote = float(st.session_state.config.get("decote_base_cel", 906)) if "Cél" in stat else float(st.session_state.config.get("decote_base_mar", 1493))
        if imp <= lim_decote: imp = max(0, imp - (base_decote - (imp * 0.4525)))
    return 0.0 if imp < 61 else imp

# OPTIMISATION : Le PRU vectorisé
def get_pru_and_qty(ticker, df_transactions):
    df_tick = df_transactions[df_transactions['Ticker'] == ticker].copy()
    if df_tick.empty: return 0.0, 0.0
    if 'Date_DT' not in df_tick.columns: df_tick['Date_DT'] = pd.to_datetime(df_tick['Date'], dayfirst=True, errors='coerce')
    df_tick = df_tick.dropna(subset=['Date_DT']).sort_values('Date_DT')
    
    total_cost_usd, total_qty = 0.0, 0.0
    records = df_tick.to_dict('records') # On transforme le tableau en dictionnaire ultra-léger
    for row in records:
        typ, qte, net_local = str(row['Type']).lower(), extraire_nombre(row['Quantité']), extraire_nombre(row['Montant Net'])
        devise = str(row.get('Devise', 'USD')).strip().upper()
        net_usd = net_local * get_historical_usd_rate(devise, row['Date'], strict=False)
        if "achat" in typ:
            total_cost_usd += net_usd; total_qty += qte
        elif "vente" in typ:
            pru_instant = total_cost_usd / total_qty if total_qty > 0 else 0.0
            total_cost_usd -= pru_instant * qte; total_qty -= qte
            if total_qty <= 0.000001: total_cost_usd, total_qty = 0.0, 0.0
                
    return round(total_cost_usd / total_qty if total_qty > 0 else 0.0, 6), round(total_qty, 6)

def get_action_tax_data(df_transactions, target_year):
    df_a = df_transactions.copy()
    if 'Date_DT' not in df_a.columns: df_a['Date_DT'] = pd.to_datetime(df_a['Date'], dayfirst=True, errors='coerce')
    df_a = df_a.dropna(subset=['Date_DT']).sort_values('Date_DT')
    results, balances = [], {} 
    
    records = df_a.to_dict('records') # 100x plus rapide que iterrows
    for row in records:
        t = str(row['Ticker']).upper()
        if est_devise_liquide(t) or is_crypto_ticker(t): continue
        typ, qte, net_local = str(row['Type']).lower(), extraire_nombre(row['Quantité']), extraire_nombre(row['Montant Net'])
        net_eur = net_local * get_historical_fx(str(row.get('Devise', 'USD')).strip().upper(), row['Date'], strict=False)
        if t not in balances: balances[t] = {'qty': 0.0, 'cost_eur': 0.0}
        if "achat" in typ:
            balances[t]['qty'] += qte; balances[t]['cost_eur'] += net_eur
        elif "vente" in typ:
            pru_eur = balances[t]['cost_eur'] / balances[t]['qty'] if balances[t]['qty'] > 0 else 0.0
            cout_cession_eur = pru_eur * qte; pv_eur = net_eur - cout_cession_eur
            balances[t]['qty'] -= qte; balances[t]['cost_eur'] -= cout_cession_eur
            if balances[t]['qty'] <= 0.00001: balances[t]['qty'], balances[t]['cost_eur'] = 0.0, 0.0
            if row['Date_DT'].year == target_year:
                results.append({"Actif": t, "Date de vente": row['Date'], "Quantité vendue": format_smart(qte, is_price=True), "PRU d'Acquisition (€)": format_smart(pru_eur, "€", is_price=True), "Prix de revente net (€)": format_smart(net_eur, "€", is_price=True), "Plus-value (€)": format_smart(pv_eur, "€"), "Cat": "Action/ETF", "PV Num": pv_eur, "Qte Num": qte, "Acq Num": cout_cession_eur, "Cession Num": net_eur})
    return results

def get_crypto_tax_data(df_transactions, target_year):
    df_c = df_transactions.copy()
    if 'Date_DT' not in df_c.columns: df_c['Date_DT'] = pd.to_datetime(df_c['Date'], dayfirst=True, errors='coerce')
    df_c = df_c.dropna(subset=['Date_DT']).sort_values('Date_DT')
    
    # OPTIMISATION : Pre-fetching (On télécharge tous les prix d'un coup avant la boucle)
    sales_dates = df_c[df_c['Type'].str.lower().str.contains('vente')]['Date_DT']
    historical_prices = {}
    if not sales_dates.empty:
        min_date = (sales_dates.min() - pd.Timedelta(days=5)).strftime('%Y-%m-%d')
        max_date = (sales_dates.max() + pd.Timedelta(days=2)).strftime('%Y-%m-%d')
        all_cryptos = [str(t).upper() for t in df_c['Ticker'].unique() if is_crypto_ticker(t)]
        yf_tickers = [f"{c}-USD" for c in all_cryptos]
        
        if yf_tickers:
            try:
                data = yf.download(yf_tickers, start=min_date, end=max_date, progress=False)['Close']
                for c in all_cryptos:
                    col_name = f"{c}-USD"
                    if isinstance(data, pd.DataFrame) and col_name in data.columns:
                        historical_prices[c] = data[col_name].dropna()
                    elif isinstance(data, pd.Series): # Cas d'une seule crypto
                        historical_prices[c] = data.dropna()
            except Exception: pass

    gross_acq_cost = 0.0
    sum_fractions_deducted = 0.0
    crypto_balances = {}
    results = []
    
    records = df_c.to_dict('records')
    for row in records:
        t = str(row['Ticker']).upper()
        if not is_crypto_ticker(t): continue
        typ, qte, net_local = str(row['Type']).lower(), extraire_nombre(row['Quantité']), extraire_nombre(row['Montant Net'])
        net_eur = net_local * get_historical_fx(str(row.get('Devise', 'USD')).strip().upper(), row['Date'], strict=False)
        
        if "achat" in typ:
            gross_acq_cost += net_eur
            crypto_balances[t] = crypto_balances.get(t, 0.0) + qte
        elif "vente" in typ:
            prix_cession_eur = net_eur 
            valeur_globale = 0.0
            date_vente = row['Date_DT']
            
            for c_tick, c_qty in crypto_balances.items():
                if c_qty > 0.00001:
                    if c_tick == t: 
                        valeur_globale += c_qty * (prix_cession_eur / qte if qte > 0 else 0.0)
                    else:
                        # Lecture instantanée en mémoire au lieu d'appeler l'API
                        h_px_usd = 0.0
                        if c_tick in historical_prices and not historical_prices[c_tick].empty:
                            subset = historical_prices[c_tick][historical_prices[c_tick].index <= date_vente]
                            if not subset.empty: h_px_usd = float(subset.iloc[-1])
                        valeur_globale += (c_qty * h_px_usd * get_historical_fx("USD", row['Date'], strict=False))
                        
            if valeur_globale < prix_cession_eur: valeur_globale = prix_cession_eur
            
            ligne_220 = gross_acq_cost
            ligne_221 = sum_fractions_deducted
            ligne_223 = max(0.0, ligne_220 - ligne_221)
            
            fraction_capital = ligne_223 * (prix_cession_eur / valeur_globale) if valeur_globale > 0 else 0.0
            pv_eur = prix_cession_eur - fraction_capital
            
            sum_fractions_deducted += fraction_capital
            crypto_balances[t] = max(0.0, crypto_balances.get(t, 0.0) - qte)
            
            if row['Date_DT'].year == target_year:
                results.append({
                    "Actif": t, "Date de vente": row['Date'], "Quantité vendue": format_smart(qte, is_price=True),
                    "Ligne 211": row['Date'], "Ligne 212": valeur_globale, "Ligne 213": prix_cession_eur,
                    "Ligne 220": ligne_220, "Ligne 221": ligne_221, "Ligne 223": ligne_223, "Ligne 224": pv_eur,
                    "Cat": "Crypto", "PV Num": pv_eur
                })
    return results
