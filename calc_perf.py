from supabase import create_client
import pandas as pd
import numpy as np
import re

url = "https://bxcpmpseagwcgaavmygo.supabase.co"
key = "sb_publishable_NDcVoF7diyTCS-03Paqlig_srlEytG9"
supabase = create_client(url, key)

# Charger les projections
response = supabase.table("Projections").select("*").order("Date", desc=False).execute()
df = pd.DataFrame(response.data)

# Trier par date correctement
df['Date_DT'] = pd.to_datetime(df['Date'].astype(str).str.slice(0, 10), dayfirst=True, errors='coerce')
df = df.dropna(subset=['Date_DT']).sort_values('Date_DT').reset_index(drop=True)

# Sécurisation numérique
for col in ['Capital investi', 'Actifs Stratégiques', 'Total Global']:
    df[col] = pd.to_numeric(df[col].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce').fillna(0.0)

# Calcul TWR
df['Variation_Capital'] = df['Capital investi'].diff().fillna(0.0)
df['Evolution actifs $'] = df['Actifs Stratégiques'].diff().fillna(0.0) - df['Variation_Capital']
val_prec = df['Actifs Stratégiques'].shift(1)
df['Evolution actifs %'] = (df['Evolution actifs $'] / val_prec * 100).fillna(0.0)
df['Evolution cumulée $'] = df['Actifs Stratégiques'] - df['Capital investi']
df['Evolution cumulée %'] = ((df['Actifs Stratégiques'] - df['Capital investi']) / df['Capital investi'] * 100).fillna(0.0)
df['TG_Evolution cumulée $'] = df['Total Global'] - df['Capital investi']
df['TG_Evolution cumulée %'] = ((df['Total Global'] - df['Capital investi']) / df['Capital investi'] * 100).fillna(0.0)
df['Rendement_Multiplicateur'] = 1 + (df['Evolution actifs %'] / 100)
df['Score TWR %'] = (df['Rendement_Multiplicateur'].cumprod() - 1) * 100
df['TG_Score TWR %'] = df['Score TWR %']

# Mettre à jour chaque ligne dans Supabase
for i, row in df.iterrows():
    supabase.table("Projections").update({
        "Evolution actifs $": row['Evolution actifs $'],
        "Evolution actifs %": row['Evolution actifs %'],
        "Evolution cumulée $": row['Evolution cumulée $'],
        "Evolution cumulée %": row['Evolution cumulée %'],
        "Score TWR %": row['Score TWR %'],
        "TG_Evolution cumulée $": row['TG_Evolution cumulée $'],
        "TG_Evolution cumulée %": row['TG_Evolution cumulée %'],
        "TG_Score TWR %": row['TG_Score TWR %']
    }).eq("id", row['id']).execute()

print("✅ Colonnes TWR mises à jour !")