import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import io

# Configurazione della pagina per grafica estesa ad alta risoluzione
st.set_page_config(page_title="Dashboard Monitoraggio SAL", layout="wide")

def check_password():
    """Verifica credenziali per accesso aziendale privato"""
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
        
    if st.session_state["password_correct"]:
        return True

    st.title("🔒 Accesso Riservato - Monitoraggio SAL")
    password = st.text_input("Inserisci la password del team:", type="password")
    if st.button("Accedi"):
        if password == st.secrets["PASSWORD_TEAM"]: 
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("❌ Password errata. Riprova.")
    return False

# Blocco di sicurezza
if check_password():
    st.title("📊 Applicazione Avanzata di Data Visualization")
    st.subheader("Monitoraggio SAL Progetti Minipia - Sincronizzato Live con Drive")

    try:
        # ID univoco ed esatto del tuo Foglio Google estratto dallo screenshot
        SHEET_ID = "12gik-EYKeVeJvOpohkPM-nUVJLbiDkKpI-XT9Mx2RAA"
        
        # Link di esportazione nativa ripristinato correttamente
        url_diretto = f"https://google.com{SHEET_ID}/export?format=xlsx"
        
        # AGGIORNAMENTO DI RETE SICURO: Scarica il file tramite richiesta HTTP per evitare il blocco DNS
        response = requests.get(url_diretto, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        file_bytes = io.BytesIO(response.content)
        
        # Lettura della struttura dei fogli dal file scaricato in memoria
        excel_file = pd.ExcelFile(file_bytes, engine='openpyxl')
        sheet_names = excel_file.sheet_names
        
        # Navigazione dei fogli nella barra laterale sinistra (GANTT_SAL_PROGETTI_EPAL, ecc.)
        st.sidebar.header("📁 Navigazione Fogli")
        selected_sheet = st.sidebar.selectbox("Seleziona il foglio da visualizzare", sheet_names)
        
        # Legge i dati in tempo reale dal foglio selezionato
        df = pd.read_excel(file_bytes, sheet_name=selected_sheet, engine='openpyxl')
        
        # Pulizia delle intestazioni di colonna e rimozione righe vuote
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how='all')
        
        st.write(f"### 📋 Dati attuali del foglio: **{selected_sheet}**")
        st.dataframe(df, use_container_width=True)
        
        st.markdown("---")
        
        colonne = df.columns.tolist()
        
        if len(colonne) >= 2:
            st.sidebar.markdown("---")
            st.sidebar.header("⚙️ Configurazione Grafico")
            
            col_progetti = st.sidebar.selectbox("Colonna Nomi Progetti:", colonne, index=0)
            
            tipo_grafico = st.sidebar.radio(
                "Scegli lo stile del grafico:",
                ["Singolo Valore (Stile Classico)", "Confronto Doppio (Fatto vs Da Fare)"]
            )
            
            df_plot = df.copy()
            df_plot = df_plot.dropna(subset=[col_progetti])
            df_plot = df_plot[df_plot[col_progetti].astype(str).str.strip() != ""]
            
            # --- CALCOLO PERCENTUALI COMPLESSIVE MACRO ---
            col_fatto_calc = None
            col_da_fare_calc = None
            for col in colonne:
                if 'fatto (giorni)' in col.lower() or col.lower() == 'fatto':
                    col_fatto_calc = col
                if 'da fare (giorni)' in col.lower() or col.lower() == 'da fare':
                    col_da_fare_calc = col
            
            if col_fatto_calc and col_da_fare_calc:
                tot_fatto = pd.to_numeric(df_plot[col_fatto_calc].astype(str).str.replace('%', '', regex=False), errors='coerce').sum()
                tot_da_fare = pd.to_numeric(df_plot[col_da_fare_calc].astype(str).str.replace('%', '', regex=False), errors='coerce').sum()
                tot_giorni_complessivi = tot_fatto + tot_da_fare
                
                if tot_giorni_complessivi > 0:
                    pct_fatto_complessiva = (tot_fatto / tot_giorni_complessivi) * 100
                    pct_da_fare_complessiva = (tot_da_fare / tot_giorni_complessivi) * 100
                    
                    st.write("### 📈 Stato Avanzamento Complessivo del Foglio (Macro)")
                    macro_col1, macro_col2 = st.columns(2)
                    macro_col1.metric(label="✅ PERCENTUALE COMPLESSIVA FATTA", value=f"{pct_fatto_complessiva:.1f}%", delta=f"Somma giorni: {tot_fatto:.1f}")
                    macro_col2.metric(label="⚠️ PERCENTUALE COMPLESSIVA DA FARE", value=f"{pct_da_fare_complessiva:.1f}%", delta=f"Somma giorni: {tot_da_fare:.1f}", delta_color="inverse")
                    st.markdown("---")

            # --- MODALITÀ 1: GRAFICO SINGOLO SFUMATO ---
            if tipo_grafico == "Singolo Valore (Stile Classico)":
                default_index = 1
                for i, col in enumerate(colonne):
                    if '%' in col.lower() or 'completamento' in col.lower() or 'sal' in col.lower():
                        default_index = i
                        break
                
                col_percentuali = st.sidebar.selectbox("Colonna Valore da mostrare:", colonne, index=default_index)
                st.subheader(f"📈 Grafico Singolo Interattivo: {selected_sheet}")
                
                df_plot[col_percentuali] = df_plot[col_percentuali].astype(str).str.replace('%', '', regex=False)
                df_plot[col_percentuali] = pd.to_numeric(df_plot[col_percentuali], errors='coerce')
                
                # Moltiplica per 100 i decimali se la colonna è di tipo percentuale
                if "%" in col_percentuali.lower() or "completamento" in col_percentuali.lower() or "sal" in col_percentuali.lower():
                    df_plot[col_percentuali] = df_plot[col_percentuali] * 100
                
                df_plot = df_plot.dropna(subset=[col_percentuali])
                df_plot = df_plot.sort_values(by=col_percentuali, ascending=True)
                
                if not df_plot.empty:
                    suffix = "%" if ("%" in col_percentuali.lower() or "completamento" in col_percentuali.lower() or "sal" in col_percentuali.lower()) else ""
                    fig = px.bar(
                        df_plot, x=col_percentuali, y=col_progetti, orientation='h',
                        text=df_plot[col_percentuali].apply(lambda x: f"{x:.1f}{suffix}" if pd.notnull(x) else ""),
                        color=col_percentuali, color_continuous_scale=px.colors.sequential.Viridis,
                        labels={col_percentuali: col_percentuali, col_progetti: "Progetto"}
                    )
                    fig.update_layout(height=max(400, len(df_plot) * 35), margin=dict(l=150, r=40, t=40, b=40), hovermode="y unified")
                    if suffix == "%":
                        fig.update_xaxes(ticksuffix="%")
                    fig.update_yaxes(categoryorder='total ascending')
                    fig.update_traces(textposition='outside', marker_line_color='rgb(8,48,107)', marker_line_width=1.5, opacity=0.9)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning("Nessun dato numerico valido per generare il grafico singolo.")
            
            # --- MODALITÀ 2: GRAFICO DOPPIO RAGGRUPPATO ---
            else:
                default_selezionati = []
                for col in colonne:
                    if 'fatto' in col.lower() or 'da fare' in col.lower():
                        default_selezionati.append(col)
                
                col_valori = st.sidebar.multiselect("Seleziona le colonne da confrontare:", colonne, default=default_selezionati if default_selezionati else [colonne])
                
                if col_valori:
                    st.subheader(f"📈 Grafico di Confronto Interattivo: {selected_sheet}")
                    for col in col_valori:
                        df_plot[col] = df_plot[col].astype(str).str.replace('%', '', regex=False)
                        df_plot[col] = pd.to_numeric(df_plot[col], errors='coerce')
                    
                    if not df_plot.empty:
                        fig = px.bar(
                            df_plot, x=col_valori, y=col_progetti, orientation='h', barmode='group',
                            color_discrete_sequence=px.colors.qualitative.Pastel,
                            labels={col_progetti: "Attività / Progetto", "value": "Valore", "variable": "Metrica"}
                        )
                        fig.update_layout(
                            height=max(450, len(df_plot) * 45), margin=dict(l=150, r=40, t=40, b=40),
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                            hovermode="y unified"
                        )
                        fig.update_yaxes(categoryorder='total ascending')
                        fig.update_traces(textposition='outside', marker_line_width=1, opacity=0.9)
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.warning("Nessun dato numerico valido per il grafico di confronto.")
                else:
                    st.info("💡 Seleziona almeno una colonna nel menu multiselect.")
        else:
            st.warning("La struttura di questo foglio non contiene abbastanza colonne.")
            
    except Exception as e:
        st.error(f"Errore di sincronizzazione Cloud: {e}")
