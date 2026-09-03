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

    # Riquadro sicuro di caricamento file
    uploaded_file = st.file_uploader("Trascina qui il file Excel esportato (.xlsx)", type=["xlsx"])

    if uploaded_file is not None:
        try:
            # Lettura dei fogli di lavoro inseriti manualmente
            excel_file = pd.ExcelFile(uploaded_file, engine='openpyxl')
            sheet_names = excel_file.sheet_names
            
            # Navigazione dei fogli nella barra laterale
            st.sidebar.header("📁 Navigazione Fogli")
            selected_sheet = st.sidebar.selectbox("Seleziona il foglio da visualizzare", sheet_names)
            
            # Lettura del foglio specifico selezionato dall'utente
            df = pd.read_excel(uploaded_file, sheet_name=selected_sheet, engine='openpyxl')
            
            # Pulizia intestazioni e righe vuote
            df.columns = [str(c).strip() for c in df.columns]
            df = df.dropna(how='all')
            
            st.write(f"### 📋 Dati attuali del foglio: **{selected_sheet}**")
            st.dataframe(df, use_container_width=True)
            
            st.markdown("---")
            
            # Configurazione dinamica delle colonne nella barra laterale
            colonne = df.columns.tolist()
            
            if len(colonne) >= 2:
                st.sidebar.markdown("---")
                st.sidebar.header("⚙️ Configurazione Grafico")
                
                # Selezione della colonna dei progetti
                col_progetti = st.sidebar.selectbox("Colonna Nomi Progetti:", colonne, index=0)
                
                # NUOVO SELETTORE: Scegli il tipo di grafico da visualizzare
                tipo_grafico = st.sidebar.radio(
                    "Scegli lo stile del grafico:",
                    ["Singolo Valore (Stile Classico)", "Confronto Doppio (Fatto vs Da Fare)"]
                )
                
                df_plot = df.copy()
                df_plot = df_plot.dropna(subset=[col_progetti])
                df_plot = df_plot[df_plot[col_progetti].astype(str).str.strip() != ""]
                
                # --- MODALITÀ 1: GRAFICO SINGOLO CLASSICO ---
                if tipo_grafico == "Singolo Valore (Stile Classico)":
                    default_index = 1
                    for i, col in enumerate(colonne):
                        if '%' in col.lower() or 'completamento' in col.lower() or 'sal' in col.lower():
                            default_index = i
                            break
                    
                    col_percentuali = st.sidebar.selectbox("Colonna Valore da mostrare:", colonne, index=default_index)
                    st.subheader(f"📈 Grafico Avanzamento Interattivo: {selected_sheet}")
                    
                    # Pulizia dato singolo
                    df_plot[col_percentuali] = df_plot[col_percentuali].astype(str).str.replace('%', '', regex=False)
                    df_plot[col_percentuali] = pd.to_numeric(df_plot[col_percentuali], errors='coerce')
                    df_plot = df_plot.dropna(subset=[col_percentuali])
                    df_plot = df_plot.sort_values(by=col_percentuali, ascending=True)
                    
                    if not df_plot.empty:
                        suffix = "%" if ("%" in col_percentuali.lower() or "completamento" in col_percentuali.lower()) else ""
                        fig = px.bar(
                            df_plot, 
                            x=col_percentuali, 
                            y=col_progetti, 
                            orientation='h',
                            text=df_plot[col_percentuali].apply(lambda x: f"{x:.1f}{suffix}" if pd.notnull(x) else ""),
                            color=col_percentuali,
                            color_continuous_scale=px.colors.sequential.Viridis,
                            labels={col_percentuali: col_percentuali, col_progetti: "Progetto"}
                        )
                        fig.update_layout(
                            height=max(400, len(df_plot) * 35),
                            margin=dict(l=150, r=40, t=40, b=40),
                            hovermode="y unified"
                        )
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
                    
                    col_valori = st.sidebar.multiselect(
                        "Seleziona le colonne da confrontare:", 
                        colonne, 
                        default=default_selezionati if default_selezionati else [colonne[1]]
                    )
                    
                    if col_valori:
                        st.subheader(f"📈 Grafico di Confronto Interattivo: {selected_sheet}")
                        
                        for col in col_valori:
                            df_plot[col] = df_plot[col].astype(str).str.replace('%', '', regex=False)
                            df_plot[col] = pd.to_numeric(df_plot[col], errors='coerce')
                        
                        if not df_plot.empty:
                            fig = px.bar(
                                df_plot, 
                                x=col_valori, 
                                y=col_progetti, 
                                orientation='h',
                                barmode='group',
                                color_discrete_sequence=px.colors.qualitative.Pastel,
                                labels={col_progetti: "Attività / Progetto", "value": "Valore", "variable": "Metrica"}
                            )
                            fig.update_layout(
                                height=max(450, len(df_plot) * 45),
                                margin=dict(l=150, r=40, t=40, b=40),
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                                hovermode="y unified"
                        )
                            fig.update_yaxes(categoryorder='total ascending')
                            fig.update_traces(textposition='outside', marker_line_width=1, opacity=0.9)
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.warning("Nessun dato numerico valido per il grafico di confronto.")
                    else:
                        st.info("💡 Seleziona almeno una colonna nel menu multiselect per vedere il grafico raggruppato.")
            else:
                st.warning("La struttura di questo foglio non contiene abbastanza colonne per generare il grafico automaticamente.")
                
        except Exception as e:
            st.error(f"Errore nella lettura del file Excel: {e}")
    else:
        st.info("👋 Benvenuto! Scarica il tuo file da Google Sheets in formato Excel (.xlsx) e trascinalo qui dentro per vedere tabelle e grafici interattivi.")
