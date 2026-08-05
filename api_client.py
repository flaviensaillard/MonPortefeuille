import streamlit as st
import pandas as pd
import yfinance as yf
import urllib.request
import json
import time

def fetch_with_retry(func, max_attempts=3, delay=1):
    """Bouclier anti-crash : Réessaie automatiquement si API bloquée."""
    for attempt in range(max_attempts):
        try:
            result = func()
            if result is not None:
                return result
        except Exception as e:
            if attempt == max_attempts - 1:
                return None
            time.sleep(delay * (2 ** attempt))
    return None

@st.cache_data(ttl=86400) 
def recuperer_inflation_france():
    """Récupère l'inflation française avec fallback Banque Mondiale."""
    def _fetch():
        inflation_data = {}
        
        # Tentative 1 : INSEE (plus précis)
        try:
            req = urllib.request.Request(
                "https://www.insee.fr/fr/statistiques/serie/telecharger/001759970?ordre=chronologique&format=csv", 
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                lines = resp.read().decode('utf-8', errors='ignore').split('\n')
            
            yearly_indices = {}
            for line in lines:
                parts = line.strip().split(';')
                if len(parts) >= 2 and '-' in parts[0]:
                    try:
                        year = int(parts[0].split('-')[0])
                        val = float(parts[1].replace(',', '.').replace('"', '').strip())
                        if year not in yearly_indices:
                            yearly_indices[year] = []
                        yearly_indices[year].append(val)
                    except:
                        pass
            
            if yearly_indices:
                years = sorted(yearly_indices.keys())
                for i in range(1, len(years)):
                    y = years[i]
                    prev_y = y - 1
                    if prev_y in yearly_indices:
                        avg_current = sum(yearly_indices[y]) / len(yearly_indices[y])
                        avg_prev = sum(yearly_indices[prev_y]) / len(yearly_indices[prev_y])
                        if avg_prev > 0:
                            inflation = ((avg_current / avg_prev) - 1) * 100
                            if y >= 2023:
                                inflation_data[y] = round(inflation, 2)
        except:
            pass
        
        # Fallback : Banque Mondiale
        if not inflation_data:
            try:
                req = urllib.request.Request(
                    "https://api.worldbank.org/v2/country/FRA/indicator/FP.CPI.TOTL.ZG?format=json&per_page=20", 
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                    if len(data) == 2 and isinstance(data[1], list): 
                        for item in data[1]:
                            if item['value'] is not None:
                                year = int(item['date'])
                                if year not in inflation_data:
                                    inflation_data[year] = round(float(item['value']), 2)
            except:
                pass
        
        return inflation_data if inflation_data else None

    return fetch_with_retry(_fetch)


@st.cache_data(ttl=300)
def get_bulk_crypto_prices(tickers_list, start_date, end_date):
    """
    Télécharge les prix de plusieurs cryptos en UNE SEULE requête Yahoo Finance.
    Utilisé par tax_engine.py pour éviter 50 appels API.
    """
    if not tickers_list:
        return {}
    
    try:
        # Formater les tickers pour Yahoo Finance (BTC-USD, ETH-USD, etc.)
        yf_tickers = [f"{t}-USD" for t in tickers_list if not t.endswith("-USD")]
        yf_tickers += [t for t in tickers_list if t.endswith("-USD")]
        
        if not yf_tickers:
            return {}
        
        # Télécharger toutes les cryptos en une fois
        data = yf.download(yf_tickers, start=start_date, end=end_date, progress=False)['Close']
        
        result = {}
        for ticker in tickers_list:
            yf_t = f"{ticker}-USD" if not ticker.endswith("-USD") else ticker
            if isinstance(data, pd.DataFrame) and yf_t in data.columns:
                series = data[yf_t].dropna()
                if not series.empty:
                    result[ticker] = series
            elif isinstance(data, pd.Series) and len(tickers_list) == 1:
                # Cas d'une seule crypto
                series = data.dropna()
                if not series.empty:
                    result[ticker] = series
        
        return result
    except Exception as e:
        print(f"Erreur bulk crypto: {e}")
        return {}
