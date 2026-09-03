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
    st.subheader("Monitoraggio SAL Progetti Minipia")

    # Riquadro sicuro di caricamento file (Nessun dato o link salvato su Internet)
    uploaded_file = st.file_uploader("Trascina qui il file Excel esportato (.xlsx)", type=["xlsx"])

    if uploaded_file is not None:
        try:
            # Lettura dei fogli di lavoro inseriti manualmente
            excel_file = pd.ExcelFile(uploaded_file, engine='openpyxl')
            sheet_names = excel_file.sheet_names
            
            # Navigazione dei fogli nella barra laterale
            st.sidebar.header("Navigazione Fogli")
            selected_sheet = st.sidebar.selectbox("Seleziona il foglio da visualizzare", sheet_names)
            
            # Lettura del foglio specifico selezionato dall'utente
            df = pd.read_excel(uploaded_file, sheet_name=selected_sheet, engine='openpyxl')
            
            # Pulizia intestazioni e righe vuote
            df.columns = [str(c).strip() for c in df.columns]
            df = df.dropna(how='all')
            
            st.write(f"### 📋 Dati attuali del foglio: **{selected_sheet}**")
            st.dataframe(df, use_container_width=True)
            
            st.markdown("---")
            st.subheader(f"📈 Grafico Avanzamento Interattivo: {selected_sheet}")
            
            colonne = df.columns.tolist()
            if len(colonne) >= 2:
                col_progetti = colonne[0]     # Prima colonna: I nomi dei progetti
                col_percentuali = colonne[1]  # Seconda colonna: % Completamento
                
                # Converte le stringhe percentuali in numeri decimali puliti
                df[col_percentuali] = pd.to_numeric(df[col_percentuali].astype(str).str.replace('%', ''), errors='coerce')
                df = df.dropna(subset=[col_percentuali])
                df = df.sort_values(by=col_percentuali, ascending=True)
                
                # Generazione del grafico a barre orizzontali interattivo
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
                
                # Configurazione layout ed assi senza conflitti
                fig.update_layout(
                    height=600,
                    margin=dict(l=150, r=20, t=40, b=40),
                    hovermode="y unified"
                )
                fig.update_xaxes(ticksuffix="%")
                fig.update_yaxes(categoryorder='total ascending')
                fig.update_traces(textposition='outside', marker_line_color='rgb(8,48,107)', marker_line_width=1.5, opacity=0.9)
                
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("La struttura di questo foglio non contiene abbastanza colonne per generare il grafico automaticamente.")
                
        except Exception as e:
            st.error(f"Errore nella lettura del file Excel: {e}")
    else:
        st.info("👋 Benvenuto! Scarica il tuo file da Google Sheets in formato Excel (.xlsx) e trascinalo qui dentro per vedere tabelle e grafici interattivi.")
