import streamlit as st
import pandas as pd
import plotly.express as px
import re

# Configurazione della pagina per grafica estesa
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
        if password == "Innov_TeAm2026!": 
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("❌ Password errata. Riprova.")
    return False

# Blocco di sicurezza
if check_password():
    st.title("📊 Applicazione Avanzata di Data Visualization")
    st.subheader("Monitoraggio SAL Progetti Minipia - Cloud Sinc")

    try:
        # Il codice legge il link direttamente dalla cassaforte protetta di Streamlit
        LINK_DRIVE = st.secrets["LINK_GOOGLE_DRIVE"]

        # Trasforma il link di Drive in un link di download diretto in background
        if "://google.com" in LINK_DRIVE:
            file_id = re.search(r'/d/([^/]+)', LINK_DRIVE)
            if file_id:
                url_diretto = f"https://google.com{file_id.group(1)}/export?format=xlsx"
            else:
                url_diretto = LINK_DRIVE
        else:
            url_diretto = LINK_DRIVE

        # Lettura automatica del file dal Cloud
        excel_file = pd.ExcelFile(url_diretto)
        sheet_names = excel_file.sheet_names
        
        st.sidebar.header("Navigazione Fogli")
        selected_sheet = st.sidebar.selectbox("Seleziona il foglio da visualizzare", sheet_names)
        
        df = pd.read_excel(url_diretto, sheet_name=selected_sheet)
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how='all')
        
        st.write(f"### 📋 Dati attuali del foglio: **{selected_sheet}**")
        st.dataframe(df, use_container_width=True)
        
        st.markdown("---")
        st.subheader(f"📈 Grafico Avanzamento Interattivo: {selected_sheet}")
        
        colonne = df.columns.tolist()
        if len(colonne) >= 2:
            col_progetti = colonne
            col_percentuali = colonne
            
            df[col_percentuali] = pd.to_numeric(df[col_percentuali].astype(str).str.replace('%', ''), errors='coerce')
            df = df.dropna(subset=[col_percentuali])
            df = df.sort_values(by=col_percentuali, ascending=True)
            
            fig = px.bar(
                df, 
                x=col_percentuali, 
                y=col_progetti, 
                orientation='h',
                text=df[col_percentuali].apply(lambda x: f"{x:.1f}%" if pd.notnull(x) else ""),
                color=col_percentuali,
                color_continuous_scale=px.colors.sequential.Viridis,
                labels={col_percentuali: "Stato Avanzamento Lavori (SAL)", col_progetti: "Progetto"}
            )
            
            fig.update_layout(
                height=600,
                xaxis_suffix="%",
                yaxis={'categoryorder':'total ascending'},
                margin=dict(l=150, r=20, t=40, b=40),
                hovermode="y unified"
            )
            fig.update_traces(textposition='outside', marker_line_color='rgb(8,48,107)', marker_line_width=1.5, opacity=0.9)
            
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("Struttura delle colonne non idonea alla generazione automatica del grafico.")
            
    except Exception as e:
        st.error(f"Impossibile leggere il file. Verifica la configurazione nei Secrets. Errore: {e}")
