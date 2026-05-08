import streamlit as st
import pandas as pd
import yfinance as yf
import urllib.request
import json

@st.cache_data(ttl=86400) 
def recuperer_inflation_france():
    inflation_data = {}
    try:
        req = urllib.request.Request(
            "https://www.insee.fr/fr/statistiques/serie/telecharger/001759970?ordre=chronologique&format=csv", 
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}
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
                    if year not in yearly_indices: yearly_indices[year] = []
                    yearly_indices[year].append(val)
                except: pass
        if yearly_indices:
            years = sorted(yearly_indices.keys())
            for i in range(1, len(years)):
                y = years[i]; prev_y = y - 1
                if prev_y in yearly_indices:
                    inflation = ((sum(yearly_indices[y]) / len(yearly_indices[y])) / (sum(yearly_indices[prev_y]) / len(yearly_indices[prev_y])) - 1) * 100
                    if y >= 2023: inflation_data[y] = round(inflation, 2)
    except Exception: pass
    return inflation_data if inflation_data else None

@st.cache_data(ttl=3600)
def get_historical_fx(devise, date_val, strict=False):
    d_clean = str(devise).upper().strip()
    if d_clean in ["EUR", ""]: return 1.0
    t = f"{d_clean}EUR=X"
    try:
        d = pd.to_datetime(date_val, dayfirst=True, errors='coerce')
        if pd.isna(d): raise ValueError("Date Invalide")
        if d >= pd.Timestamp.now() - pd.Timedelta(days=1):
            h = yf.Ticker(t).history(period="1d")
            if not h.empty: return float(h['Close'].iloc[-1])
        else:
            h = yf.Ticker(t).history(start=(d - pd.Timedelta(days=5)).strftime('%Y-%m-%d'), end=(d + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
            if not h.empty: return float(h['Close'].iloc[-1])
    except Exception: pass
    if strict: raise ValueError(f"⚠️ Échec réseau : Impossible de vérifier le taux {d_clean}/EUR.")
    return 1.0

@st.cache_data(ttl=3600)
def get_historical_usd_rate(devise, date_val, strict=False):
    d_clean = str(devise).upper().strip()
    if d_clean in ["USD", ""]: return 1.0
    t = f"{d_clean}USD=X"
    try:
        d = pd.to_datetime(date_val, dayfirst=True, errors='coerce')
        if pd.isna(d): raise ValueError("Date Invalide")
        if d >= pd.Timestamp.now() - pd.Timedelta(days=1):
            h = yf.Ticker(t).history(period="1d")
            if not h.empty: return float(h['Close'].iloc[-1])
        else:
            h = yf.Ticker(t).history(start=(d - pd.Timedelta(days=5)).strftime('%Y-%m-%d'), end=(d + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
            if not h.empty: return float(h['Close'].iloc[-1])
    except Exception: pass
    if strict: raise ValueError(f"⚠️ Échec réseau : Impossible de vérifier le taux {d_clean}/USD.")
    return 1.0
