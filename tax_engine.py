import streamlit as st
import pandas as pd
import json
import yfinance as yf
from utils import extraire_nombre, format_smart, est_devise_liquide, is_crypto_ticker
from api_client import get_historical_fx, get_historical_usd_rate, get_bulk_crypto_prices

# Cache global pour les taux de change (évite les appels répétés)
_fx_cache = {}

def _cached_fx(devise, date_str, against="EUR"):
    """Cache local pour les taux de change."""
    key = f"{devise}_{date_str}_{against}"
    if key not in _fx_cache:
        if against == "EUR":
            _fx_cache[key] = get_historical_fx(devise, date_str, strict=False)
        else:
            _fx_cache[key] = get_historical_usd_rate(devise, date_str, strict=False)
    return _fx_cache[key]

def calcul_frais_km(km, cv):
    """Calcul des frais kilométriques selon le barème URSSAF."""
    try:
        bareme_str = st.session_state.config.get("urssaf_bareme", 
            '{"3":[0.529, 0.316, 1065, 0.370], "4":[0.606, 0.340, 1330, 0.407], '
            '"5":[0.636, 0.357, 1395, 0.427], "6":[0.665, 0.374, 1457, 0.447], '
            '"7":[0.697, 0.394, 1515, 0.470]}')
        bareme = json.loads(bareme_str)
        c = bareme.get(str(cv), bareme["7"])
    except:
        c = [0.697, 0.394, 1515, 0.470]
    
    if km <= 5000:
        return km * c[0]
    elif km <= 20000:
        return km * c[1] + c[2]
    else:
        return km * c[3]

def calcul_impot_ir(rev, parts, stat, apply_decote=True):
    """Calcul de l'impôt sur le revenu (version optimisée avec config locale)."""
    # Récupérer la config une seule fois
    config = st.session_state.config
    
    t1 = float(config.get("tax_lim_1", 11294))
    t2 = float(config.get("tax_lim_2", 28797))
    t3 = float(config.get("tax_lim_3", 82341))
    t4 = float(config.get("tax_lim_4", 177106))
    r2 = float(config.get("tax_rate_2", 0.11))
    r3 = float(config.get("tax_rate_3", 0.30))
    r4 = float(config.get("tax_rate_4", 0.41))
    r5 = float(config.get("tax_rate_5", 0.45))
    
    qf = rev / parts
    
    # Calcul de l'impôt avec tranches
    tranches = [
        (0, t1, 0.0),
        (t1, t2, r2),
        (t2, t3, r3),
        (t3, t4, r4),
        (t4, float('inf'), r5)
    ]
    
    imp = 0.0
    for borne_inf, borne_sup, taux in tranches:
        if qf > borne_inf:
            montant_tranche = min(qf, borne_sup) - borne_inf
            imp += montant_tranche * taux
    
    imp *= parts
    
    # Décote si applicable
    if apply_decote:
        if "Cél" in stat:
            lim_decote = float(config.get("decote_lim_cel", 2002))
            base_decote = float(config.get("decote_base_cel", 906))
        else:
            lim_decote = float(config.get("decote_lim_mar", 3300))
            base_decote = float(config.get("decote_base_mar", 1493))
        
        if imp <= lim_decote:
            imp = max(0, imp - (base_decote - (imp * 0.4525)))
    
    return 0.0 if imp < 61 else imp


def get_pru_and_qty(ticker, df_transactions):
    """
    Calcule le PRU (Prix de Revient Unitaire) et la quantité restante.
    Version optimisée avec cache des taux de change.
    """
    df_tick = df_transactions[df_transactions['Ticker'] == ticker].copy()
    if df_tick.empty:
        return 0.0, 0.0
    
    # Conversion des dates une seule fois
    if 'Date_DT' not in df_tick.columns:
        df_tick['Date_DT'] = pd.to_datetime(df_tick['Date'], dayfirst=True, errors='coerce')
    df_tick = df_tick.dropna(subset=['Date_DT']).sort_values('Date_DT')
    
    total_cost_usd = 0.0
    total_qty = 0.0
    
    # Utiliser to_dict('records') - plus rapide que iterrows()
    for row in df_tick.to_dict('records'):
        typ = str(row['Type']).lower()
        qte = extraire_nombre(row['Quantité'])
        net_local = extraire_nombre(row['Montant Net'])
        devise = str(row.get('Devise', 'USD')).strip().upper()
        date_str = str(row['Date'])
        
        # Utiliser le cache des taux de change
        net_usd = net_local * _cached_fx(devise, date_str, against="USD")
        
        if "achat" in typ:
            total_cost_usd += net_usd
            total_qty += qte
        elif "vente" in typ:
            if total_qty > 0:
                pru_instant = total_cost_usd / total_qty
                total_cost_usd -= pru_instant * qte
                total_qty -= qte
                if total_qty <= 0.000001:
                    total_cost_usd = 0.0
                    total_qty = 0.0
    
    if total_qty > 0:
        return round(total_cost_usd / total_qty, 6), round(total_qty, 6)
    return 0.0, 0.0


def get_action_tax_data(df_transactions, target_year):
    """
    Calcule les plus-values actions/ETF pour l'année fiscale.
    Version optimisée avec cache des taux de change.
    """
    df_a = df_transactions.copy()
    
    # Conversion des dates une seule fois
    if 'Date_DT' not in df_a.columns:
        df_a['Date_DT'] = pd.to_datetime(df_a['Date'], dayfirst=True, errors='coerce')
    df_a = df_a.dropna(subset=['Date_DT']).sort_values('Date_DT')
    
    results = []
    balances = {}
    
    for row in df_a.to_dict('records'):
        t = str(row['Ticker']).upper()
        
        # Ignorer les devises et cryptos
        if est_devise_liquide(t) or is_crypto_ticker(t):
            continue
        
        typ = str(row['Type']).lower()
        qte = extraire_nombre(row['Quantité'])
        net_local = extraire_nombre(row['Montant Net'])
        devise = str(row.get('Devise', 'USD')).strip().upper()
        date_str = str(row['Date'])
        
        # Utiliser le cache des taux de change
        net_eur = net_local * _cached_fx(devise, date_str, against="EUR")
        
        if t not in balances:
            balances[t] = {'qty': 0.0, 'cost_eur': 0.0}
        
        if "achat" in typ:
            balances[t]['qty'] += qte
            balances[t]['cost_eur'] += net_eur
        elif "vente" in typ:
            if balances[t]['qty'] > 0:
                pru_eur = balances[t]['cost_eur'] / balances[t]['qty']
                cout_cession_eur = pru_eur * qte
                pv_eur = net_eur - cout_cession_eur
                
                balances[t]['qty'] -= qte
                balances[t]['cost_eur'] -= cout_cession_eur
                
                if balances[t]['qty'] <= 0.00001:
                    balances[t]['qty'] = 0.0
                    balances[t]['cost_eur'] = 0.0
                
                # Ne garder que les ventes de l'année cible
                if row['Date_DT'].year == target_year:
                    results.append({
                        "Actif": t,
                        "Date de vente": row['Date'],
                        "Quantité vendue": format_smart(qte, is_price=True),
                        "PRU d'Acquisition (€)": format_smart(pru_eur, "€", is_price=True),
                        "Prix de revente net (€)": format_smart(net_eur, "€", is_price=True),
                        "Plus-value (€)": format_smart(pv_eur, "€"),
                        "Cat": "Action/ETF",
                        "PV Num": pv_eur,
                        "Qte Num": qte,
                        "Acq Num": cout_cession_eur,
                        "Cession Num": net_eur
                    })
    
    return results


def get_crypto_tax_data(df_transactions, target_year):
    """
    Calcule les plus-values crypto pour l'année fiscale.
    Version optimisée avec bulk download et cache des taux.
    """
    df_c = df_transactions.copy()
    
    # Conversion des dates une seule fois
    if 'Date_DT' not in df_c.columns:
        df_c['Date_DT'] = pd.to_datetime(df_c['Date'], dayfirst=True, errors='coerce')
    df_c = df_c.dropna(subset=['Date_DT']).sort_values('Date_DT')
    
    # --- OPTIMISATION : Bulk download de TOUS les prix crypto ---
    sales_dates = df_c[df_c['Type'].str.lower().str.contains('vente')]['Date_DT']
    historical_prices = {}
    
    if not sales_dates.empty:
        min_date = (sales_dates.min() - pd.Timedelta(days=5)).strftime('%Y-%m-%d')
        max_date = (sales_dates.max() + pd.Timedelta(days=2)).strftime('%Y-%m-%d')
        
        # Récupérer TOUTES les cryptos du portefeuille
        all_cryptos = [str(t).upper() for t in df_c['Ticker'].unique() if is_crypto_ticker(t)]
        
        # Utiliser la fonction bulk optimisée de api_client
        historical_prices = get_bulk_crypto_prices(all_cryptos, min_date, max_date)
    
    gross_acq_cost = 0.0
    sum_fractions_deducted = 0.0
    crypto_balances = {}
    results = []
    
    for row in df_c.to_dict('records'):
        t = str(row['Ticker']).upper()
        
        # Ignorer les non-cryptos
        if not is_crypto_ticker(t):
            continue
        
        typ = str(row['Type']).lower()
        qte = extraire_nombre(row['Quantité'])
        net_local = extraire_nombre(row['Montant Net'])
        devise = str(row.get('Devise', 'USD')).strip().upper()
        date_str = str(row['Date'])
        
        # Utiliser le cache des taux de change
        net_eur = net_local * _cached_fx(devise, date_str, against="EUR")
        
        if "achat" in typ:
            gross_acq_cost += net_eur
            crypto_balances[t] = crypto_balances.get(t, 0.0) + qte
        elif "vente" in typ:
            prix_cession_eur = net_eur
            valeur_globale = 0.0
            date_vente = row['Date_DT']
            
            # Calcul de la valeur globale du portefeuille au moment de la vente
            for c_tick, c_qty in crypto_balances.items():
                if c_qty > 0.00001:
                    if c_tick == t:
                        # Prix de la crypto vendue
                        valeur_globale += c_qty * (prix_cession_eur / qte if qte > 0 else 0.0)
                    else:
                        # Prix des autres cryptos (depuis le cache bulk)
                        h_px_usd = 0.0
                        if c_tick in historical_prices:
                            series = historical_prices[c_tick]
                            # Prendre le prix le plus proche avant la date de vente
                            series_before = series[series.index <= date_vente]
                            if not series_before.empty:
                                h_px_usd = float(series_before.iloc[-1])
                        
                        if h_px_usd > 0:
                            valeur_globale += c_qty * h_px_usd * _cached_fx("USD", date_str, against="EUR")
            
            # Protection : valeur globale au moins égale au prix de cession
            if valeur_globale < prix_cession_eur:
                valeur_globale = prix_cession_eur
            
            # Calcul de la plus-value selon la formule fiscale française
            ligne_220 = gross_acq_cost
            ligne_221 = sum_fractions_deducted
            ligne_223 = max(0.0, ligne_220 - ligne_221)
            
            fraction_capital = ligne_223 * (prix_cession_eur / valeur_globale) if valeur_globale > 0 else 0.0
            pv_eur = prix_cession_eur - fraction_capital
            
            sum_fractions_deducted += fraction_capital
            crypto_balances[t] = max(0.0, crypto_balances.get(t, 0.0) - qte)
            
            # Ne garder que les ventes de l'année cible
            if row['Date_DT'].year == target_year:
                results.append({
                    "Actif": t,
                    "Date de vente": row['Date'],
                    "Quantité vendue": format_smart(qte, is_price=True),
                    "Ligne 211": row['Date'],
                    "Ligne 212": valeur_globale,
                    "Ligne 213": prix_cession_eur,
                    "Ligne 220": ligne_220,
                    "Ligne 221": ligne_221,
                    "Ligne 223": ligne_223,
                    "Ligne 224": pv_eur,
                    "Cat": "Crypto",
                    "PV Num": pv_eur
                })
    
    return results
