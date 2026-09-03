import streamlit as st
import pandas as pd
import plotly.express as px

# Configurazione della pagina per grafica estesa
st.set_page_config(page_title="Dashboard Monitoraggio SAL", layout="wide")

def check_password():
    """Verifica credenziali per accesso aziendale privato"""
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
        
    if st.session_state["password_correct"]:
        return True

    # Schermata di login minimale e pulita
    st.title("🔒 Accesso Riservato - Monitoraggio SAL")
    password = st.text_input("Inserisci la password del team:", type="password")
    if st.button("Accedi"):
        # MODIFICA LA PASSWORD QUI SOTTO: CAMBIA Azienda2026! CON QUELLA CHE VUOI TU
        if password == "PASSWORD_TEAM": 
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("❌ Password errata. Riprova.")
    return False

# Blocco di sicurezza
if check_password():
    st.title("📊 Applicazione Avanzata di Data Visualization")
    st.subheader("Monitoraggio SAL Progetti Minipia")

    # Area drag and drop per inserire il file aggiornato in tempo reale dal tuo script JS
    uploaded_file = st.file_input("Trascina qui il file Excel aggiornato (.xlsx)", type=["xlsx"])

    if uploaded_file is not None:
        try:
            # Rileva dinamicamente tutti i fogli creati dal database
            excel_file = pd.ExcelFile(uploaded_file)
            sheet_names = excel_file.sheet_names
            
            # Menu a tendina laterale per navigare tra i vari fogli dei progetti
            st.sidebar.header("Navigazione Fogli")
            selected_sheet = st.sidebar.selectbox("Seleziona il foglio da visualizzare", sheet_names)
            
            # Carica i dati escludendo righe vuote
            df = pd.read_excel(uploaded_file, sheet_name=selected_sheet)
            df.columns = [str(c).strip() for c in df.columns]
            df = df.dropna(how='all')
            
            st.write(f"### 📋 Dati attuali del foglio: **{selected_sheet}**")
            st.dataframe(df, use_container_width=True)
            
            st.markdown("---")
            st.subheader(f"📈 Grafico Avanzamento Interattivo: {selected_sheet}")
            
            colonne = df.columns.tolist()
            if len(colonne) >= 2:
                col_progetti = colonne[0]
                col_percentuali = colonne[1]
                
                # Pulizia automatica delle stringhe delle percentuali (es. trasforma '45%' in 45)
                df[col_percentuali] = pd.to_numeric(df[col_percentuali].astype(str).str.replace('%', ''), errors='coerce')
                df = df.dropna(subset=[col_percentuali])
                
                # Ordina i dati dal SAL più alto a quello più basso per una lettura pulita
                df = df.sort_values(by=col_percentuali, ascending=True)
                
                # Generazione del grafico interattivo avanzato con zoom e dettagli al passaggio del mouse
                fig = px.bar(
                    df, 
                    x=col_percentuali, 
                    y=col_progetti, 
                    orientation='h',
                    text=df[col_percentuali].apply(lambda x: f"{x:.1f}%" if pd.notnull(x) else ""),
                    color=col_percentuali,
                    color_continuous_scale=px.colors.sequential.Viridis, # Palette sfumata ad alto contrasto
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
            st.error(f"Errore nell'elaborazione del file Excel: {e}")
    else:
        st.info("👋 Benvenuto! Carica il file Excel dei tuoi progetti per esplorare i grafici interattivi e navigare tra le tabelle.")
