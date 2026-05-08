import pandas as pd
import numpy as np
import re

def format_smart(val, symbol="", force_sign=False, is_price=False):
    if pd.isna(val) or str(val).strip() == "": return ""
    try:
        v = float(val)
        # Correction : on force 2 décimales si c'est un pourcentage
        if symbol == "%":
            dec = 2
        else:
            dec = 6 if is_price or (abs(v) > 0 and abs(v) < 1) else 2
            
        s = f"{v:+,.{dec}f}" if force_sign else f"{v:,.{dec}f}"
        parts = s.split('.')
        int_part = parts[0].replace(',', ' ')
        if len(parts) > 1:
            frac_part = parts[1]
            if dec > 2: frac_part = frac_part.rstrip('0')
            if len(frac_part) == 0: frac_part = "00"
            elif len(frac_part) == 1: frac_part += "0"
            num_str = f"{int_part}.{frac_part}"
        else:
            num_str = f"{int_part}.00"
        if num_str in ['+.00', '-.00', '+0.00', '-0.00', '.00']: num_str = "0.00"
        
        if symbol == "$": return f"$ {num_str}"
        elif symbol == "€": return f"{num_str} €"
        elif symbol == "%": return f"{num_str} %"
        elif symbol == "oz": return f"{num_str} oz"
        else: return num_str
    except: return str(val)

def extraire_nombre(valeur):
    if pd.isna(valeur) or str(valeur).strip() == "" or str(valeur).lower() == "nan": return 0.0
    nettoye = re.sub(r'[^\d,.-]', '', str(valeur))
    if ',' in nettoye and '.' in nettoye: nettoye = nettoye.replace(',', '')
    elif ',' in nettoye: nettoye = nettoye.replace(',', '.')
    try: return round(float(nettoye), 6)
    except: return 0.0

def is_crypto_ticker(ticker):
    t = str(ticker).upper().strip()
    return t in ["BTC", "ETH", "USDT", "SOL", "ADA", "XRP", "DOT", "DOGE", "AVAX", "LINK", "BNB"] or t.endswith(("-USD", "USDT"))

def est_devise_liquide(ticker):
    t = str(ticker).upper().strip()
    return t.endswith("=X") or (any(m in t for m in ["USD", "EUR", "CHF", "JPY", "CNY", "GBP", "CAD", "AUD"]) and not is_crypto_ticker(t))

def _clean_ticker_v4(t):
    t_clean = str(t).upper().strip()
    if t_clean.endswith("USD=X") and len(t_clean) == 8: return t_clean[:3]
    if t_clean.endswith("=X") and len(t_clean) == 5: return t_clean[:3]
    return t_clean

def _assign_type_v4(row):
    t = re.sub(r'[^\w\s]', '', str(row.get("Type", ""))).strip().upper()
    tick = str(row.get("Ticker", "")).upper().strip()
    if "ACTION" in t: return "🛢️ Action"
    elif "OBLIGATION" in t: return "📜 Obligation"
    elif "OR" in t: return "💰 Or"
    elif "CRYPTO" in t: return "₿ Crypto"
    elif "RÉSERVE" in t or "RESERVE" in t: return "🏦 Cash réserve"
    elif "CASH" in t: return "💵 Cash"
    else: return "💵 Cash" if est_devise_liquide(tick) else "₿ Crypto" if is_crypto_ticker(tick) else "🛢️ Action"

def nettoyer_dataframe(df):
    cols_finales = ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)", "Devise Cotation"]
    if df.empty: return pd.DataFrame(columns=cols_finales)
    
    df.columns = [str(c).replace("quantit", "Quantité").replace("qte", "Quantité").replace("cotation", "Devise Cotation") for c in df.columns]
    if "Ticker" in df.columns: df["Ticker"] = df["Ticker"].apply(_clean_ticker_v4)
    if "Type" not in df.columns: df["Type"] = ""
    df["Type"] = df.apply(_assign_type_v4, axis=1)

    for col in cols_finales:
        if col not in df.columns:
            if col == "Devise Cotation": df[col] = "Auto"
            elif col in ["Quantité", "Pourcentage (%)"]: df[col] = 0.0
            else: df[col] = "$ 0.00"

    df["Devise Cotation"] = df["Devise Cotation"].fillna("Auto").apply(lambda x: "Auto" if str(x).strip() == "" else str(x).strip().capitalize() if str(x).strip().lower() == "auto" else str(x).strip().upper())
    df["Quantité"] = df["Quantité"].apply(extraire_nombre)
    df["Pourcentage (%)"] = df["Pourcentage (%)"].apply(extraire_nombre)
    
    return df.groupby(["Ticker", "Type"], as_index=False).agg({"Quantité": "sum", "Court": "first", "Valeur totale": "first", "Pourcentage (%)": "sum", "Devise Cotation": "first"})[cols_finales]
