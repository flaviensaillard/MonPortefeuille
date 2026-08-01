import flet as ft
import sys
import os

# Ajouter le dossier courant au path pour importer tes modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import datetime
import plotly.graph_objects as go
import base64
from supabase import create_client

# Importer TES modules existants
from utils import extraire_nombre, format_smart
from db_manager import load_sheet, obtenir_derniere_projection_veille, recalculer_toute_la_base_projections

# =============================================
# CONNEXION À SUPABASE
# =============================================
url = "https://bxcpmpseagwcgaavmygo.supabase.co"
key = "sb_publishable_NDcVoF7diyTCS-03Paqlig_srlEytG9"
supabase = create_client(url, key)

# =============================================
# COULEURS
# =============================================
FOND = "#0D1117"
CARTE = "#161B22"
TEXTE = "#E6EDF3"
TEXTE_GRIS = "#8B949E"
VERT = "#3FB950"
ROUGE = "#F85149"
BLEU = "#58A6FF"
ORANGE = "#D2991D"

# =============================================
# CACHE SIMPLE
# =============================================
_cache_donnees = None
_cache_projections = None

def get_donnees():
    global _cache_donnees
    if _cache_donnees is None:
        _cache_donnees = load_sheet("Donnees", ["Ticker", "Type", "Quantité", "Court", "Valeur totale", "Pourcentage (%)", "Devise Cotation"])
    return _cache_donnees

def get_projections():
    global _cache_projections
    if _cache_projections is None:
        df = load_sheet("Projections", [])
        if not df.empty:
            df = recalculer_toute_la_base_projections(df)
        _cache_projections = df
    return _cache_projections

# =============================================
# GRAPHIQUES PLOTLY
# =============================================
def creer_graphique(dates_str, valeurs, titre, couleur):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates_str, y=valeurs, mode='lines', line=dict(color=couleur, width=2),
        fill='tozeroy', fillcolor=f'rgba({int(couleur[1:3],16)},{int(couleur[3:5],16)},{int(couleur[5:7],16)},0.15)'))
    y_min, y_max = min(valeurs), max(valeurs)
    marge = (y_max - y_min) * 0.1 if y_max != y_min else y_max * 0.05
    fig.update_layout(template='plotly_dark', paper_bgcolor=CARTE, plot_bgcolor=CARTE,
        title=dict(text=titre, font=dict(size=14, color=TEXTE)), margin=dict(l=30, r=20, t=50, b=30), height=300,
        xaxis=dict(showgrid=False, color=TEXTE_GRIS, tickfont=dict(size=10)),
        yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.05)', color=TEXTE_GRIS, tickfont=dict(size=10), range=[y_min-marge, y_max+marge]))
    return base64.b64encode(fig.to_image(format="png", scale=2)).decode()

def creer_camembert(labels, valeurs):
    couleurs = ['#3FB950', '#58A6FF', '#F85149', '#D2991D', '#8B949E', '#BC8CFF']
    fig = go.Figure(data=[go.Pie(labels=labels, values=valeurs, hole=0.4, marker=dict(colors=couleurs[:len(labels)]), textinfo='percent+label', textfont=dict(size=11, color=TEXTE))])
    fig.update_layout(template='plotly_dark', paper_bgcolor=CARTE, plot_bgcolor=CARTE, margin=dict(l=20, r=20, t=20, b=20), height=350)
    return base64.b64encode(fig.to_image(format="png", scale=2)).decode()

# =============================================
# APPLICATION
# =============================================
def main(page: ft.Page):
    page.title = "Mon Portefeuille"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = FOND
    page.padding = 0
    page.appbar = ft.AppBar(title=ft.Text("📊 Mon Portefeuille", weight=ft.FontWeight.BOLD, color=TEXTE), center_title=True, bgcolor=CARTE)
    contenu = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True)

    PERIODE = "all"

    # =============================================
    # DASHBOARD
    # =============================================
    def page_dashboard():
        nonlocal PERIODE
        contenu.controls.clear()
        contenu.controls.append(ft.ProgressBar(color=BLEU))
        page.update()

        try:
            df = get_donnees()
            df_proj = get_projections()

            # Calculs totaux
            tg = 0.0
            a_s = 0.0
            actifs_par_type = {}
            besoin = False

            if not df.empty:
                for _, r in df.iterrows():
                    v = extraire_nombre(str(r.get("Quantité", 0))) * extraire_nombre(str(r.get("Court", 0)))
                    p = extraire_nombre(str(r.get("Pourcentage (%)", 0)))
                    t = str(r.get("Type", ""))
                    tg += v
                    if p > 0:
                        a_s += v
                        actifs_par_type[t] = actifs_par_type.get(t, 0) + v

            # Vérifier besoin rééquilibrage
            if a_s > 0:
                for _, r in df.iterrows():
                    p = extraire_nombre(str(r.get("Pourcentage (%)", 0)))
                    if p > 0:
                        v = extraire_nombre(str(r.get("Quantité", 0))) * extraire_nombre(str(r.get("Court", 0)))
                        cible = a_s * (p / 100)
                        ecart_pct = abs((v / a_s * 100) - p)
                        if ecart_pct >= 2.0 and abs(cible - v) >= 1000:
                            besoin = True
                            break

            # Delta J-1
            photo = obtenir_derniere_projection_veille()
            delta_tg_txt = ""
            delta_strat_txt = ""
            if photo:
                vg = photo.get("Total Global", 0)
                vs = photo.get("Actifs Stratégiques", 0)
                if vg and vg > 0:
                    d = tg - vg
                    delta_tg_txt = f"{d:+.2f} $ ({(d/vg)*100:+.2f}%) Aujourd'hui"
                if vs and vs > 0:
                    d = a_s - vs
                    delta_strat_txt = f"{d:+.2f} $ ({(d/vs)*100:+.2f}%) Aujourd'hui"

            # Performance 1 an et filtre période
            delta_1an = 0.0
            pct_1an = 0.0
            df_filtre = pd.DataFrame()

            if not df_proj.empty:
                df_proj['DT'] = pd.to_datetime(df_proj['Date'].astype(str).str.slice(0, 10), dayfirst=True, errors='coerce')
                df_proj = df_proj.dropna(subset=['DT']).sort_values('DT')
                n = pd.Timestamp.now()

                if PERIODE == "1y":
                    df_filtre = df_proj[df_proj['DT'] >= n - pd.DateOffset(years=1)]
                elif PERIODE == "ytd":
                    df_filtre = df_proj[df_proj['DT'] >= pd.Timestamp(year=n.year, month=1, day=1)]
                else:
                    df_filtre = df_proj

                if not df_filtre.empty:
                    vd = extraire_nombre(str(df_filtre.iloc[0].get('Actifs Stratégiques', 0)))
                    if vd > 0:
                        delta_1an = a_s - vd
                        pct_1an = (delta_1an / vd) * 100

            # === CONSTRUCTION INTERFACE ===
            contenu.controls.clear()

            # Titre
            contenu.controls.append(ft.Container(content=ft.Text("📊 Tableau de Bord", size=22, weight=ft.FontWeight.BOLD, color=TEXTE), padding=ft.Padding(16, 16, 16, 8)))

            # Statut
            contenu.controls.append(ft.Container(content=ft.Row([ft.Text("🔴" if besoin else "🟢", size=16), ft.Text("Reequilibrage necessaire" if besoin else "Portefeuille equilibre", size=14, color=ROUGE if besoin else VERT, weight=ft.FontWeight.BOLD)]), bgcolor=CARTE, border_radius=12, padding=ft.Padding(16, 12, 16, 12), margin=ft.Margin(16, 0, 16, 8)))

            # Total Global
            contenu.controls.append(ft.Container(content=ft.Column([ft.Text("🌍 Total Global", size=12, color=TEXTE_GRIS), ft.Text(format_smart(tg, "$"), size=28, weight=ft.FontWeight.BOLD, color=TEXTE)] + ([ft.Text(delta_tg_txt, size=12, color=VERT if "+" in delta_tg_txt else ROUGE)] if delta_tg_txt else [])), bgcolor=CARTE, border_radius=12, padding=20, margin=ft.Margin(16, 0, 16, 8)))

            # Actifs Stratégiques
            contenu.controls.append(ft.Container(content=ft.Column([ft.Text("🎯 Actifs Strategiques", size=12, color=TEXTE_GRIS), ft.Text(format_smart(a_s, "$"), size=28, weight=ft.FontWeight.BOLD, color=VERT)] + ([ft.Text(delta_strat_txt, size=12, color=VERT if "+" in delta_strat_txt else ROUGE)] if delta_strat_txt else [])), bgcolor=CARTE, border_radius=12, padding=20, margin=ft.Margin(16, 0, 16, 8)))

            # Performance 1 an
            fleche = "📈" if delta_1an >= 0 else "📉"
            couleur_perf = VERT if delta_1an >= 0 else ROUGE
            contenu.controls.append(ft.Container(content=ft.Text(f"{fleche} Performance 1 an : {format_smart(delta_1an, '$')} ({format_smart(pct_1an, '%')})", size=14, weight=ft.FontWeight.BOLD, color=couleur_perf), padding=ft.Padding(16, 8, 16, 8)))

            # Boutons période
            def changer_p(p):
                nonlocal PERIODE
                PERIODE = p
                page_dashboard()

            contenu.controls.append(ft.Container(content=ft.Row([ft.Text("Periode:", size=13, color=TEXTE_GRIS),
                ft.ElevatedButton("Tout", on_click=lambda e: changer_p("all"), style=ft.ButtonStyle(bgcolor=BLEU if PERIODE == "all" else CARTE, color=TEXTE)),
                ft.ElevatedButton("1 an", on_click=lambda e: changer_p("1y"), style=ft.ButtonStyle(bgcolor=BLEU if PERIODE == "1y" else CARTE, color=TEXTE)),
                ft.ElevatedButton("Annee", on_click=lambda e: changer_p("ytd"), style=ft.ButtonStyle(bgcolor=BLEU if PERIODE == "ytd" else CARTE, color=TEXTE))], spacing=8), padding=ft.Padding(16, 0, 16, 12)))

            # Graphiques
            if not df_filtre.empty:
                ds = [d.strftime('%d/%m/%y') for d in df_filtre['DT'].tolist()]
                vt = [extraire_nombre(str(v)) for v in df_filtre['Total Global'].tolist()]
                vs = [extraire_nombre(str(v)) for v in df_filtre['Actifs Stratégiques'].tolist()]
                if len(ds) > 1:
                    contenu.controls.append(ft.Container(content=ft.Image(src=f"data:image/png;base64,{creer_graphique(ds, vt, 'Total Global', BLEU)}", width=page.width - 32), padding=ft.Padding(16, 0, 16, 8)))
                    contenu.controls.append(ft.Container(content=ft.Image(src=f"data:image/png;base64,{creer_graphique(ds, vs, 'Actifs Strategiques', VERT)}", width=page.width - 32), padding=ft.Padding(16, 0, 16, 8)))

            # Camembert
            if actifs_par_type:
                contenu.controls.append(ft.Container(height=8))
                contenu.controls.append(ft.Container(content=ft.Text("Repartition", size=16, weight=ft.FontWeight.BOLD, color=TEXTE), padding=ft.Padding(16, 8, 16, 8)))
                contenu.controls.append(ft.Container(content=ft.Image(src=f"data:image/png;base64,{creer_camembert(list(actifs_par_type.keys()), list(actifs_par_type.values()))}", width=page.width - 32), padding=ft.Padding(16, 0, 16, 8)))

            contenu.controls.append(ft.Container(height=80))

        except Exception as e:
            contenu.controls.clear()
            contenu.controls.append(ft.Container(content=ft.Text(f"Erreur: {e}", color=ROUGE), padding=20))
        page.update()

    # =============================================
    # ACTIFS
    # =============================================
    def page_actifs():
        contenu.controls.clear()
        contenu.controls.append(ft.ProgressBar(color=BLEU))
        page.update()
        try:
            df = get_donnees()
            contenu.controls.clear()
            contenu.controls.append(ft.Container(content=ft.Text("📋 Liste des Actifs", size=22, weight=ft.FontWeight.BOLD, color=TEXTE), padding=ft.Padding(16, 16, 16, 8)))

            if df.empty:
                contenu.controls.append(ft.Text("Aucun actif", color=TEXTE_GRIS))
            else:
                for _, r in df.iterrows():
                    t = str(r.get("Ticker", ""))
                    p = extraire_nombre(str(r.get("Pourcentage (%)", 0)))
                    v = extraire_nombre(str(r.get("Quantité", 0))) * extraire_nombre(str(r.get("Court", 0)))
                    q = extraire_nombre(str(r.get("Quantité", 0)))
                    var_jour = "→ 0.00 %"
                    contenu.controls.append(ft.Container(content=ft.Column([
                        ft.Row([ft.Text(t, weight=ft.FontWeight.BOLD, color=TEXTE, size=14), ft.Text(var_jour, size=11, color=TEXTE_GRIS)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        ft.Row([ft.Text(f"Valeur: {format_smart(v, '$')}", size=12, color=VERT), ft.Text(f"Qte: {q} | Cible: {format_smart(p, '%')}", size=11, color=TEXTE_GRIS)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ]), bgcolor=CARTE, border_radius=8, padding=ft.Padding(16, 12, 16, 12), margin=ft.Margin(16, 0, 16, 4)))
            contenu.controls.append(ft.Container(height=80))
        except Exception as e:
            contenu.controls.clear()
            contenu.controls.append(ft.Container(content=ft.Text(f"Erreur: {e}", color=ROUGE), padding=20))
        page.update()

    # =============================================
    # RÉÉQUILIBRAGE
    # =============================================
    def page_rebalance():
        contenu.controls.clear()
        contenu.controls.append(ft.ProgressBar(color=BLEU))
        page.update()
        try:
            df = get_donnees()
            a_s = 0.0
            cash = 0.0
            actifs = []

            if not df.empty:
                for _, r in df.iterrows():
                    v = extraire_nombre(str(r.get("Quantité", 0))) * extraire_nombre(str(r.get("Court", 0)))
                    p = extraire_nombre(str(r.get("Pourcentage (%)", 0)))
                    t = str(r.get("Type", ""))
                    if "Cash" in t:
                        cash += v
                    if p > 0:
                        a_s += v
                        actifs.append({"t": str(r.get("Ticker", "")), "v": v, "p": p, "c": extraire_nombre(str(r.get("Court", 0)))})

            contenu.controls.clear()
            contenu.controls.append(ft.Container(content=ft.Text("⚖️ Reequilibrage", size=22, weight=ft.FontWeight.BOLD, color=TEXTE), padding=ft.Padding(16, 16, 16, 8)))
            contenu.controls.append(ft.Container(content=ft.Text(f"💵 Liquidites disponibles : {format_smart(cash, '$')}", size=14, weight=ft.FontWeight.BOLD, color=VERT), bgcolor=CARTE, border_radius=12, padding=ft.Padding(16, 12, 16, 12), margin=ft.Margin(16, 0, 16, 12)))

            contenu.controls.append(ft.Container(content=ft.Text("Analyse des ecarts", size=16, weight=ft.FontWeight.BOLD, color=TEXTE), padding=ft.Padding(16, 0, 16, 8)))

            if a_s > 0:
                for a in actifs:
                    cv = a_s * (a["p"] / 100)
                    ecart = cv - a["v"]
                    ep = ((a["v"] / a_s * 100) - a["p"]) if a_s > 0 else 0
                    besoin = abs(ep) >= 2.0 and abs(ecart) >= 1000
                    if besoin:
                        action = f"{'🟢 ACHETER' if ecart > 0 else '🔴 VENDRE'} {format_smart(abs(ecart), '$')}"
                        couleur = VERT if ecart > 0 else ROUGE
                    else:
                        action = "✅ Equilibre"
                        couleur = TEXTE_GRIS

                    contenu.controls.append(ft.Container(content=ft.Column([
                        ft.Row([ft.Text(a["t"], weight=ft.FontWeight.BOLD, color=TEXTE), ft.Text(f"Cible: {format_smart(a['p'], '%')}", size=12, color=TEXTE_GRIS)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        ft.Row([ft.Text(f"Actuel: {format_smart(a['v'], '$')}", size=12, color=TEXTE_GRIS), ft.Text(f"Ecart: {format_smart(ep, '%')}", size=12, color=couleur)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        ft.Text(action, size=13, weight=ft.FontWeight.BOLD, color=couleur),
                    ]), bgcolor=CARTE, border_radius=8, padding=ft.Padding(16, 12, 16, 12), margin=ft.Margin(16, 0, 16, 6)))
            else:
                contenu.controls.append(ft.Text("Aucun actif strategique", color=TEXTE_GRIS))

            contenu.controls.append(ft.Container(height=80))
        except Exception as e:
            contenu.controls.clear()
            contenu.controls.append(ft.Container(content=ft.Text(f"Erreur: {e}", color=ROUGE), padding=20))
        page.update()

    # =============================================
    # PERFORMANCE
    # =============================================
    def page_performance():
        contenu.controls.clear()
        contenu.controls.append(ft.ProgressBar(color=BLEU))
        page.update()
        try:
            df = get_projections()
            contenu.controls.clear()
            contenu.controls.append(ft.Container(content=ft.Text("📈 Performances Annuelles", size=22, weight=ft.FontWeight.BOLD, color=TEXTE), padding=ft.Padding(16, 16, 16, 8)))

            if df.empty:
                contenu.controls.append(ft.Text("Aucune donnee", color=TEXTE_GRIS))
            elif 'Score TWR %' not in df.columns:
                contenu.controls.append(ft.Text("Donnees TWR non disponibles", color=TEXTE_GRIS))
            else:
                df['DT'] = pd.to_datetime(df['Date'].astype(str).str.slice(0, 10), dayfirst=True, errors='coerce')
                df = df.dropna(subset=['DT']).sort_values('DT')
                df['Annee'] = df['DT'].dt.year
                grp = df.groupby('Annee').last().reset_index()

                for _, r in grp.iterrows():
                    an = int(r['Annee'])
                    twr = extraire_nombre(str(r.get('Score TWR %', 0)))
                    couleur = VERT if twr >= 0 else ROUGE
                    fleche = "📈" if twr >= 0 else "📉"
                    contenu.controls.append(ft.Container(content=ft.Row([ft.Text(f"{an}", weight=ft.FontWeight.BOLD, color=TEXTE, size=16), ft.Text(f"{fleche} {format_smart(twr, '%')}", size=16, weight=ft.FontWeight.BOLD, color=couleur)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), bgcolor=CARTE, border_radius=8, padding=ft.Padding(16, 12, 16, 12), margin=ft.Margin(16, 0, 16, 4)))

            contenu.controls.append(ft.Container(height=80))
        except Exception as e:
            contenu.controls.clear()
            contenu.controls.append(ft.Container(content=ft.Text(f"Erreur: {e}", color=ROUGE), padding=20))
        page.update()

    # =============================================
    # RETRAITE
    # =============================================
    def page_retraite():
        contenu.controls.clear()
        contenu.controls.append(ft.ProgressBar(color=BLEU))
        page.update()
        try:
            df = get_donnees()
            capital = 0.0
            if not df.empty:
                for _, r in df.iterrows():
                    if extraire_nombre(str(r.get("Pourcentage (%)", 0))) > 0:
                        capital += extraire_nombre(str(r.get("Quantité", 0))) * extraire_nombre(str(r.get("Court", 0)))

            contenu.controls.clear()
            contenu.controls.append(ft.Container(content=ft.Text("🌴 Simulateur Retraite", size=22, weight=ft.FontWeight.BOLD, color=TEXTE), padding=ft.Padding(16, 16, 16, 8)))
            contenu.controls.append(ft.Container(content=ft.Column([ft.Text("Capital Actuel (Actifs Strategiques)", size=12, color=TEXTE_GRIS), ft.Text(format_smart(capital, "$"), size=32, weight=ft.FontWeight.BOLD, color=VERT)]), bgcolor=CARTE, border_radius=12, padding=20, margin=ft.Margin(16, 0, 16, 12)))
            contenu.controls.append(ft.Container(content=ft.Column([ft.Text("Rente mensuelle estimee (4% / an)", size=12, color=TEXTE_GRIS), ft.Text(format_smart(capital * 0.04 / 12, "$"), size=22, weight=ft.FontWeight.BOLD, color=BLEU)]), bgcolor=CARTE, border_radius=12, padding=16, margin=ft.Margin(16, 0, 16, 8)))
            contenu.controls.append(ft.Container(content=ft.Column([ft.Text("Rente prudente (3% / an)", size=12, color=TEXTE_GRIS), ft.Text(format_smart(capital * 0.03 / 12, "$"), size=22, weight=ft.FontWeight.BOLD, color=ORANGE)]), bgcolor=CARTE, border_radius=12, padding=16, margin=ft.Margin(16, 0, 16, 8)))
            contenu.controls.append(ft.Container(height=80))
        except Exception as e:
            contenu.controls.clear()
            contenu.controls.append(ft.Container(content=ft.Text(f"Erreur: {e}", color=ROUGE), padding=20))
        page.update()

    # =============================================
    # NAVIGATION
    # =============================================
    def changer_onglet(e):
        i = e.control.selected_index
        if i == 0: page_dashboard()
        elif i == 1: page_actifs()
        elif i == 2: page_rebalance()
        elif i == 3: page_performance()
        elif i == 4: page_retraite()
        page.update()

    navigation = ft.NavigationBar(selected_index=0, on_change=changer_onglet, bgcolor=CARTE,
        destinations=[ft.NavigationBarDestination(icon=ft.Text(e), label=l) for e, l in [("📊", "Dashboard"), ("📋", "Actifs"), ("⚖️", "Rebalance"), ("📈", "Perf"), ("🌴", "Retraite")]])

    page.add(contenu)
    page.navigation_bar = navigation
    page_dashboard()

ft.app(target=main)