import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from io import BytesIO
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st


# ============================================================
# CONFIGURAZIONE GENERALE
# ============================================================

st.set_page_config(
    page_title="Dashboard Monitoraggio SAL MiniPIA",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

SHEET_ID = "12gik-EYKeVeJvOpohkPM-nUVJLbiDkKpI-XT9Mx2RAA"

GANTT_EPAL = "GANTT_SAL_PROGETTI_EPAL"
GANTT_MGIO = "GANTT_SAL_PROGETTI_MGIO"

GANTT_COMBINATI_POSSIBILI = [
    "GANTT_SAL_PROGETTI_EPAL+MGIO",
    "GANTT_SAL_PROGETTI_MGIO+EPAL",
]

TEMPLATE_SAL = {
    "SAL_ANAL_PRED (EPAL)",
    "SAL_ANAL_PRED (MGIO)",
    "SAL_ANAL_PRED (EPAL+MGIO)",
    "SAL_ANAL_PRED (MGIO+EPAL)",
}

CACHE_TTL_SECONDS = 300

SOGLIA_INIZIALE = 33.33
SOGLIA_INTERMEDIO = 66.67

TOLLERANZA_COHERENZA_SAL = 1.0

ORDINE_STATI = [
    "In stato iniziale",
    "In stato intermedio",
    "In stato avanzato",
    "Completato",
]

COLORI_STATO = {
    "In stato iniziale": "#D62728",
    "In stato intermedio": "#F2C94C",
    "In stato avanzato": "#2CA02C",
    "Completato": "#167D3E",
    "N/D": "#A0A0A0",
}

COLORI_RIPARTIZIONE = {
    "Fatto": "#2E7D32",
    "Da fare": "#D9DDE3",
}

PLOTLY_CONFIG = {
    "displaylogo": False,
    "responsive": True,
    "modeBarButtonsToRemove": [
        "lasso2d",
        "select2d",
    ],
}


# ============================================================
# STILE STREAMLIT
# ============================================================

st.markdown(
    """
    <style>

        .block-container {
            padding-top: 1.25rem;
            padding-bottom: 2.5rem;
        }

        [data-testid="stMetric"] {
            border: 1px solid rgba(128,128,128,.22);
            border-radius: 12px;
            padding: .85rem 1rem;
            background: rgba(128,128,128,.035);
        }

        [data-testid="stMetricLabel"] {
            font-weight: 600;
        }

        div[data-testid="stExpander"] {
            border-radius: 10px;
        }

        .dashboard-subtitle {
            opacity: .72;
            margin-top: -.45rem;
            margin-bottom: .25rem;
        }

    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# FUNZIONI GENERALI
# ============================================================

def ora_italiana():
    try:
        return datetime.now(ZoneInfo("Europe/Rome"))
    except Exception:
        return datetime.now()


def pulisci_testo_emoji(text):
    if not text or pd.isna(text):
        return ""
    text = str(text)
    text = re.sub(r"[^\w\s\(\)\+\&\.-]", " ", text)
    return text


def pulisci_nome_progetto_per_match(text):
    if not text or pd.isna(text):
        return ""
    text = pulisci_testo_emoji(text)
    def filtri_parentesi(match):
        inner = match.group(1).upper().replace(" ", "")
        if inner in ["EPAL", "MGIO", "EPAL+MGIO", "MGIO+EPAL"]:
            return match.group(0)
        return " "
    text = re.sub(r"\(([^)]*)\)", filtri_parentesi, text)
    text = text.replace("_", " ")
    return normalizza_testo(text)


def normalizza_testo(value):
    if value is None or pd.isna(value):
        return ""
    text = pulisci_testo_emoji(value)
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", " ", text.lower().strip())
    return text


def chiave_progetto(value):
    text = normalizza_testo(value)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenizza(value):
    text = chiave_progetto(value)
    stopwords = {
        "sal", "epal", "mgio", "progetto", "progetti", "gantt",
        "anal", "pred", "minipia", "srl", "spa", "soc", "coop", "cooperativa",
        "benefit", "e", "dei", "del", "della", "dello", "degli", "di", "da",
        "le", "la", "il", "l",
    }
    return {
        token for token in text.split()
        if len(token) > 1 and token not in stopwords
    }


def pulisci_dataframe(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")
    return df


# ============================================================
# CONVERSIONE NUMERICA
# ============================================================

def serie_numerica(serie):
    def converti(value):
        if pd.isna(value):
            return float("nan")
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace("\u00A0", "").replace(" ", "")
        if not text:
            return float("nan")

        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        else:
            text = text.replace(",", ".")

        text = text.replace("%", "")

        try:
            return float(text)
        except ValueError:
            return float("nan")

    return serie.apply(converti).astype("float64")


# ============================================================
# CONVERSIONE PERCENTUALI EXCEL
# ============================================================

def percentuale_da_excel(serie):
    valori = []
    gia_percentuali = []

    for value in serie:
        if pd.isna(value):
            valori.append(float("nan"))
            gia_percentuali.append(False)
            continue

        if isinstance(value, str) and "%" in value:
            numero = serie_numerica(pd.Series([value])).iloc[0]
            valori.append(numero)
            gia_percentuali.append(True)
        else:
            numero = serie_numerica(pd.Series([value])).iloc[0]
            valori.append(numero)
            gia_percentuali.append(False)

    risultato = pd.Series(valori, index=serie.index, dtype="float64")
    mask_percentuale = pd.Series(gia_percentuali, index=serie.index, dtype="bool")

    valori_inferenza = risultato[(~mask_percentuale) & risultato.notna()]

    if not valori_inferenza.empty:
        quota_frazioni = (valori_inferenza.abs() <= 1.5).mean()
        mediana = valori_inferenza.abs().median()

        if quota_frazioni >= 0.60 or mediana <= 1.5:
            risultato.loc[(~mask_percentuale) & risultato.notna()] *= 100

    return risultato


# ============================================================
# FORMATTAZIONE
# ============================================================

def formatta_percentuale(value, decimali=1):
    if pd.isna(value):
        return "N/D"
    return f"{float(value):.{decimali}f}%".replace(".", ",")


def formatta_numero(value, decimali=1):
    if pd.isna(value):
        return "N/D"
    return f"{float(value):.{decimali}f}".replace(".", ",")


# ============================================================
# ORDINAMENTO PORTAFOGLIO
# ============================================================

def ordina_portafoglio(df, ordinamento):
    out = df.copy()

    if ordinamento == "SAL crescente":
        return out.sort_values(["SAL", "Progetto"], ascending=[True, True], na_position="last")

    if ordinamento == "SAL decrescente":
        return out.sort_values(["SAL", "Progetto"], ascending=[False, True], na_position="last")

    if ordinamento == "Nome progetto":
        return out.sort_values(
            "Progetto",
            ascending=True,
            key=lambda serie: serie.astype(str).str.lower(),
            na_position="last",
        )

    return out


# ============================================================
# CLASSIFICAZIONE SAL
# ============================================================

def stato_da_sal(value):
    if pd.isna(value):
        return "N/D"

    valore = min(max(float(value), 0), 100)

    if valore >= 100:
        return "Completato"
    if valore <= SOGLIA_INIZIALE:
        return "In stato iniziale"
    if valore <= SOGLIA_INTERMEDIO:
        return "In stato intermedio"

    return "In stato avanzato"


# ============================================================
# RICONOSCIMENTO COLONNE
# ============================================================

def trova_colonna(df, exact=None, contains_all=None, contains_any=None, exclude=None):
    exact = exact or []
    contains_all = contains_all or []
    contains_any = contains_any or []
    exclude = exclude or []

    nomi = {col: normalizza_testo(col) for col in df.columns}

    for candidato in exact:
        candidato_norm = normalizza_testo(candidato)
        for col, nome in nomi.items():
            if nome == candidato_norm:
                return col

    if contains_all:
        for col, nome in nomi.items():
            if any(normalizza_testo(x) in nome for x in exclude):
                continue
            if all(normalizza_testo(x) in nome for x in contains_all):
                return col

    if contains_any:
        for col, nome in nomi.items():
            if any(normalizza_testo(x) in nome for x in exclude):
                continue
            if any(normalizza_testo(x) in nome for x in contains_any):
                return col

    return None


def trova_colonna_progetto(df):
    col = trova_colonna(
        df,
        exact=[
            "PROGETTO", "PROGETTO EPAL", "PROGETTO MGIO",
            "NOME PROGETTO", "CLIENTE", "COMMESSA",
            "ATTIVITÀ / PROGETTO", "ATTIVITA / PROGETTO",
        ],
        contains_any=["progetto", "cliente", "commessa"],
    )

    if col is not None:
        return col

    for col in df.columns:
        serie = df[col].dropna()
        if serie.empty:
            continue
        quota_testo = serie.apply(lambda x: isinstance(x, str)).mean()
        if quota_testo >= 0.50:
            return col

    if len(df.columns):
        return df.columns[0]

    return None


def trova_colonna_attivita(df):
    col = trova_colonna(
        df,
        exact=["ATTIVITÀ", "ATTIVITA", "DESCRIZIONE", "FASE", "TASK"],
        contains_any=["attivita", "descrizione", "fase", "task"],
        exclude=["percentuale", "completamento"],
    )

    if col is not None:
        return col

    return trova_colonna_progetto(df)


def trova_colonna_sal(df):
    col = trova_colonna(
        df,
        exact=[
            "% COMPLETAMENTO", "PERCENTUALE COMPLETAMENTO",
            "COMPLETAMENTO", "SAL", "% SAL",
        ],
    )

    if col is not None:
        return col

    col = trova_colonna(
        df,
        contains_any=["completamento", "percentuale", "% sal"],
        exclude=[
            "atteso", "previsto", "target", "pianificato",
            "rosso", "giallo", "verde",
        ],
    )

    if col is not None:
        return col

    return trova_colonna(
        df,
        contains_any=["sal"],
        exclude=["atteso", "previsto", "target", "pianificato"],
    )


def trova_colonna_stato(df):
    return trova_colonna(
        df,
        exact=["STATO", "STATUS", "STATO PROGETTO"],
        contains_any=["stato", "status"],
    )


def trova_colonna_fatto(df):
    col = trova_colonna(
        df,
        exact=[
            "FATTO (GIORNI)", "FATTO", "GIORNI FATTI",
            "GIORNI EFFETTUATI", "GIORNI FATTO",
        ],
    )

    if col is not None:
        return col

    return trova_colonna(df, contains_all=["fatto", "giorn"])


def trova_colonna_da_fare(df):
    col = trova_colonna(
        df,
        exact=[
            "DA FARE (GIORNI)", "DA FARE", "GIORNI DA FARE",
            "GIORNI RESIDUI", "RESIDUO",
        ],
    )

    if col is not None:
        return col

    return trova_colonna(df, contains_any=["da fare", "residui", "residuo"])


def trova_colonna_sal_atteso(df):
    return trova_colonna(
        df,
        contains_any=[
            "sal atteso", "sal previsto", "completamento atteso",
            "completamento previsto", "target", "pianificato",
        ],
    )


def trova_colonna_team(df):
    return trova_colonna(
        df,
        exact=["TEAM", "RESPONSABILE", "CONSULENTE", "OWNER"],
        contains_any=["team", "responsabile", "consulente", "owner"],
    )


# ============================================================
# STATO SORGENTE
# ============================================================

def stato_sorgente_e_completo(value):
    if value is None or pd.isna(value):
        return False

    stato = normalizza_testo(value)
    stati_completati = {
        "completo", "completato", "completed", "chiuso",
        "concluso", "terminato", "finito",
    }

    return stato in stati_completati or stato.startswith("complet")


# ============================================================
# STATO UFFICIALE PROGETTO
# ============================================================

def normalizza_stato_progetto(stato_sorgente, sal):
    if stato_sorgente_e_completo(stato_sorgente):
        return "Completato"
    return stato_da_sal(sal)


# ============================================================
# TIPO SAL
# ============================================================

def tipo_sal_da_nome(nome):
    nome_norm = normalizza_testo(nome).replace(" ", "")

    if "(epal+mgio)" in nome_norm or "(mgio+epal)" in nome_norm:
        return "EPAL+MGIO"
    if "(epal)" in nome_norm:
        return "EPAL"
    if "(mgio)" in nome_norm:
        return "MGIO"

    return None


# ============================================================
# COLONNE GIORNI SAL
# ============================================================

def trova_colonne_giorni_sal(df, nome_foglio):
    col_fatto = trova_colonna_fatto(df)
    col_da_fare = trova_colonna_da_fare(df)

    if col_fatto is not None and col_da_fare is not None:
        return (col_fatto, col_da_fare, "intestazioni del foglio SAL")

    if len(df.columns) >= 9:
        candidato_fatto = col_fatto if col_fatto is not None else df.columns[7]
        candidato_da_fare = col_da_fare if col_da_fare is not None else df.columns[8]

        valori_fatto = serie_numerica(df[candidato_fatto])
        valori_da_fare = serie_numerica(df[candidato_da_fare])

        if valori_fatto.notna().any() and valori_da_fare.notna().any():
            return (candidato_fatto, candidato_da_fare, "colonne H:I del SAL individuale")

    return (col_fatto, col_da_fare, "intestazioni disponibili")


# ============================================================
# RIGHE ATTIVITÀ VALIDE
# ============================================================

def testo_non_nan(serie):
    return ~serie.isin(["nan", "none", "nat"])


def mask_righe_attivita_valide(df, col_attivita=None):
    if col_attivita is None:
        return pd.Series(True, index=df.index, dtype=bool)

    testo = df[col_attivita].astype(str).map(normalizza_testo)

    mask = (
        testo.ne("")
        & testo_non_nan(testo)
        & ~testo.str.match(r"^(totale|totali|total)\b", na=False)
    )

    return mask


# ============================================================
# CALCOLO GIORNI DAL SAL
# ============================================================

def calcola_giorni_progetto(df_sal, nome_foglio):
    (col_fatto, col_da_fare, fonte_colonne) = trova_colonne_giorni_sal(df_sal, nome_foglio)

    risultato_vuoto = {
        "disponibile": False,
        "giorni_fatti": float("nan"),
        "giorni_da_fare": float("nan"),
        "giorni_totali": float("nan"),
        "pct_fatti": float("nan"),
        "pct_da_fare": float("nan"),
        "col_fatto": col_fatto,
        "col_da_fare": col_da_fare,
        "fonte": fonte_colonne,
    }

    if col_fatto is None or col_da_fare is None:
        return risultato_vuoto

    fatto = serie_numerica(df_sal[col_fatto]).clip(lower=0)
    da_fare = serie_numerica(df_sal[col_da_fare]).clip(lower=0)

    col_attivita = trova_colonna_attivita(df_sal)
    mask = mask_righe_attivita_valide(df_sal, col_attivita)
    mask &= (fatto.notna() | da_fare.notna())

    fatto = fatto.loc[mask]
    da_fare = da_fare.loc[mask]

    if fatto.empty and da_fare.empty:
        return risultato_vuoto

    tot_fatto = fatto.fillna(0).sum()
    tot_da_fare = da_fare.fillna(0).sum()
    totale = tot_fatto + tot_da_fare

    if totale <= 0:
        return {
            **risultato_vuoto,
            "disponibile": True,
            "giorni_fatti": tot_fatto,
            "giorni_da_fare": tot_da_fare,
            "giorni_totali": totale,
            "pct_fatti": 0.0,
            "pct_da_fare": 0.0,
        }

    pct_fatti = (tot_fatto / totale) * 100
    pct_da_fare = (tot_da_fare / totale) * 100

    return {
        "disponibile": True,
        "giorni_fatti": tot_fatto,
        "giorni_da_fare": tot_da_fare,
        "giorni_totali": totale,
        "pct_fatti": pct_fatti,
        "pct_da_fare": pct_da_fare,
        "col_fatto": col_fatto,
        "col_da_fare": col_da_fare,
        "fonte": fonte_colonne,
    }


def calcola_giorni_da_gantt(fatto, da_fare):
    if pd.isna(fatto) or pd.isna(da_fare):
        return None

    fatto = max(float(fatto), 0)
    da_fare = max(float(da_fare), 0)
    totale = fatto + da_fare

    if totale <= 0:
        return None

    return {
        "disponibile": True,
        "giorni_fatti": fatto,
        "giorni_da_fare": da_fare,
        "giorni_totali": totale,
        "pct_fatti": (fatto / totale) * 100,
        "pct_da_fare": (da_fare / totale) * 100,
        "fonte": "GANTT del progetto",
    }


# ============================================================
# PASSWORD
# ============================================================

def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if st.session_state["password_correct"]:
        return True

    st.title("🔒 Accesso riservato")
    st.caption("Dashboard Monitoraggio SAL MiniPIA")

    try:
        password_attesa = st.secrets["PASSWORD_TEAM"]
    except Exception:
        st.error("Il secret PASSWORD_TEAM non è configurato.")
        return False

    password = st.text_input("Inserisci la password del team", type="password")

    if st.button("Accedi", type="primary", use_container_width=True):
        if password == password_attesa:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("Password errata.")

    return False


# ============================================================
# CARICAMENTO GOOGLE SHEETS
# ============================================================

@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def carica_workbook(sheet_id):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"

    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})

    with urlopen(request, timeout=60) as response:
        contenuto = response.read()

    fogli = pd.read_excel(BytesIO(contenuto), sheet_name=None, engine="openpyxl")
    fogli = {nome: pulisci_dataframe(df) for nome, df in fogli.items()}

    return (fogli, ora_italiana())


# ============================================================
# GANTT COMBINATO
# ============================================================

def trova_gantt_combinato(sheet_names):
    for nome in GANTT_COMBINATI_POSSIBILI:
        if nome in sheet_names:
            return nome
    return None


# ============================================================
# COSTRUZIONE PORTAFOGLIO
# ============================================================

def costruisci_portafoglio(df, team, source_sheet):
    if df is None or df.empty:
        return pd.DataFrame()

    col_progetto = trova_colonna_progetto(df)
    col_sal = trova_colonna_sal(df)
    col_stato = trova_colonna_stato(df)
    col_fatto = trova_colonna_fatto(df)
    col_da_fare = trova_colonna_da_fare(df)
    col_sal_atteso = trova_colonna_sal_atteso(df)

    if col_progetto is None:
        return pd.DataFrame()

    out = pd.DataFrame({"Progetto": df[col_progetto]})
    out["Progetto"] = out["Progetto"].astype(str).str.strip()

    chiavi = out["Progetto"].map(chiave_progetto)
    mask = chiavi.ne("") & ~chiavi.isin({"nan", "none", "totale", "totali", "total"})

    out = out.loc[mask].copy()
    indici = out.index

    out["Team"] = team

    if col_fatto is not None:
        out["Fatto"] = serie_numerica(df.loc[indici, col_fatto]).clip(lower=0)
    else:
        out["Fatto"] = float("nan")

    if col_da_fare is not None:
        out["Da fare"] = serie_numerica(df.loc[indici, col_da_fare]).clip(lower=0)
    else:
        out["Da fare"] = float("nan")

    if col_sal is not None:
        out["SAL sorgente"] = percentuale_da_excel(df.loc[indici, col_sal])
    else:
        denominatore = out["Fatto"] + out["Da fare"]
        out["SAL sorgente"] = out["Fatto"].div(denominatore.where(denominatore > 0)) * 100

    out["SAL"] = out["SAL sorgente"].clip(lower=0, upper=100)
    out["Anomalia SAL"] = (out["SAL sorgente"] < 0) | (out["SAL sorgente"] > 100)

    if col_stato is not None:
        stato_sorgente = df.loc[indici, col_stato]
        out["Stato sorgente"] = stato_sorgente.where(stato_sorgente.notna(), "").astype(str).str.strip()
    else:
        out["Stato sorgente"] = ""

    out["Stato"] = [
        normalizza_stato_progetto(st_sorg, sal)
        for st_sorg, sal in zip(out["Stato sorgente"], out["SAL"])
    ]

    if col_sal_atteso is not None:
        out["SAL atteso"] = percentuale_da_excel(df.loc[indici, col_sal_atteso]).clip(lower=0, upper=100)
        out["Scostamento"] = out["SAL"] - out["SAL atteso"]
    else:
        out["SAL atteso"] = float("nan")
        out["Scostamento"] = float("nan")

    out["Foglio origine"] = source_sheet

    return out.reset_index(drop=True)


# ============================================================
# NORMALIZZAZIONE TEAM
# ============================================================

def normalizza_team(value):
    text = normalizza_testo(value)
    ha_epal = "epal" in text
    ha_mgio = "mgio" in text

    if ha_epal and ha_mgio:
        return "EPAL+MGIO"
    if ha_epal:
        return "EPAL"
    if ha_mgio:
        return "MGIO"

    return "N/D"


def team_da_insieme(teams):
    teams = {team for team in teams if team and team != "N/D"}

    if "EPAL+MGIO" in teams or ("EPAL" in teams and "MGIO" in teams):
        return "EPAL+MGIO"
    if "EPAL" in teams:
        return "EPAL"
    if "MGIO" in teams:
        return "MGIO"

    return "N/D"


# ============================================================
# PORTAFOGLIO COMBINATO
# ============================================================

def costruisci_portafoglio_combinato(df, portfolio_epal, portfolio_mgio, source_sheet):
    base = costruisci_portafoglio(df, "N/D", source_sheet)
    if base.empty:
        return base

    epal_keys = set(portfolio_epal["Progetto"].map(chiave_progetto)) if not portfolio_epal.empty and "Progetto" in portfolio_epal.columns else set()
    mgio_keys = set(portfolio_mgio["Progetto"].map(chiave_progetto)) if not portfolio_mgio.empty and "Progetto" in portfolio_mgio.columns else set()

    col_progetto = trova_colonna_progetto(df)
    col_team = trova_colonna_team(df)

    team_map = {}
    if col_progetto is not None and col_team is not None:
        for progetto, team in zip(df[col_progetto], df[col_team]):
            key = chiave_progetto(progetto)
            if not key:
                continue
            team_norm = normalizza_team(team)
            if team_norm == "N/D":
                continue
            if key not in team_map:
                team_map[key] = set()
            team_map[key].add(team_norm)

    def assegna_team(progetto):
        key = chiave_progetto(progetto)
        if key in team_map:
            team_spec = team_da_insieme(team_map[key])
            if team_spec != "N/D":
                return team_spec

        in_epal = key in epal_keys
        in_mgio = key in mgio_keys

        if in_epal and in_mgio:
            return "EPAL+MGIO"
        if in_epal:
            return "EPAL"
        if in_mgio:
            return "MGIO"

        return "N/D"

    base["Team"] = base["Progetto"].apply(assegna_team)
    return base


# ============================================================
# CONSOLIDAMENTO PROGETTI UNIVOCI
# ============================================================

def consolida_progetti_univoci(df):
    if df is None or df.empty:
        return pd.DataFrame()

    temp = df.copy()
    temp["Chiave progetto"] = temp["Progetto"].map(chiave_progetto)
    temp = temp[temp["Chiave progetto"].ne("")].copy()

    if temp.empty:
        return pd.DataFrame()

    righe = []

    for chiave, gruppo in temp.groupby("Chiave progetto", sort=False):
        nomi_validi = [str(x).strip() for x in gruppo["Progetto"].tolist() if pd.notna(x) and str(x).strip()]
        progetto = nomi_validi[0] if nomi_validi else chiave

        teams = {str(x).strip() for x in gruppo["Team"].tolist() if pd.notna(x)}
        team = team_da_insieme(teams)

        fatto_series = pd.to_numeric(gruppo["Fatto"], errors="coerce") if "Fatto" in gruppo.columns else pd.Series(dtype=float)
        da_fare_series = pd.to_numeric(gruppo["Da fare"], errors="coerce") if "Da fare" in gruppo.columns else pd.Series(dtype=float)

        validi_giorni = fatto_series.notna() & da_fare_series.notna()

        if validi_giorni.any():
            fatto = fatto_series.loc[validi_giorni].clip(lower=0).sum()
            da_fare = da_fare_series.loc[validi_giorni].clip(lower=0).sum()
            totale_giorni = fatto + da_fare
        else:
            fatto = float("nan")
            da_fare = float("nan")
            totale_giorni = float("nan")

        sal_values = pd.to_numeric(gruppo["SAL sorgente"], errors="coerce").dropna()

        if not sal_values.empty:
            sal_sorgente = sal_values.iloc[0] if len(sal_values) == 1 else sal_values.mean()
        elif pd.notna(totale_giorni) and totale_giorni > 0:
            sal_sorgente = (fatto / totale_giorni) * 100
        else:
            sal_vis = pd.to_numeric(gruppo["SAL"], errors="coerce").dropna()
            sal_sorgente = sal_vis.mean() if not sal_vis.empty else float("nan")

        sal = min(max(float(sal_sorgente), 0), 100) if pd.notna(sal_sorgente) else float("nan")

        anomalie_orig = bool(gruppo["Anomalia SAL"].fillna(False).astype(bool).any()) if "Anomalia SAL" in gruppo.columns else False
        anomalia_cons = anomalie_orig or (pd.notna(sal_sorgente) and (sal_sorgente < 0 or sal_sorgente > 100))

        stati_sorgente = []
        if "Stato sorgente" in gruppo.columns:
            for value in gruppo["Stato sorgente"].tolist():
                if pd.isna(value):
                    continue
                text = str(value).strip()
                if text and text not in stati_sorgente:
                    stati_sorgente.append(text)

        stato_sorgente = " | ".join(stati_sorgente)

        completo_da_sorgente = any(
            stato_sorgente_e_completo(v) for v in gruppo["Stato sorgente"].tolist()
        ) if "Stato sorgente" in gruppo.columns else False

        stato = "Completato" if completo_da_sorgente else stato_da_sal(sal)

        sal_atteso = float("nan")
        if "SAL atteso" in gruppo.columns:
            sal_attesi = pd.to_numeric(gruppo["SAL atteso"], errors="coerce").dropna()
            if not sal_attesi.empty:
                sal_atteso = sal_attesi.mean()

        scostamento = (sal - sal_atteso) if (pd.notna(sal) and pd.notna(sal_atteso)) else float("nan")

        fogli_origine = []
        if "Foglio origine" in gruppo.columns:
            for value in gruppo["Foglio origine"].tolist():
                if pd.isna(value):
                    continue
                text = str(value).strip()
                if text and text not in fogli_origine:
                    fogli_origine.append(text)

        righe.append({
            "Progetto": progetto,
            "Team": team,
            "Fatto": fatto,
            "Da fare": da_fare,
            "SAL sorgente": sal_sorgente,
            "SAL": sal,
            "Anomalia SAL": anomalia_cons,
            "Stato sorgente": stato_sorgente,
            "Stato": stato,
            "SAL atteso": sal_atteso,
            "Scostamento": scostamento,
            "Foglio origine": " | ".join(fogli_origine),
            "Occorrenze consolidate": len(gruppo),
        })

    return pd.DataFrame(righe).reset_index(drop=True)


# ============================================================
# SAL COMPLESSIVO PORTAFOGLIO
# ============================================================

def portfolio_sal(df):
    if df.empty:
        return (float("nan"), "N/D")

    validi_giorni = df["Fatto"].notna() & df["Da fare"].notna()

    if len(df) > 0 and validi_giorni.all():
        fatto = df["Fatto"].clip(lower=0).sum()
        residuo = df["Da fare"].clip(lower=0).sum()
        totale = fatto + residuo

        if totale > 0:
            return ((fatto / totale) * 100, "ponderato sui giorni")

    sal_validi = df["SAL"].dropna()

    if not sal_validi.empty:
        return (sal_validi.mean(), "media dei SAL disponibili")

    return (float("nan"), "N/D")


# ============================================================
# FLAG PROGETTI PRESENTI IN ENTRAMBI
# ============================================================

def aggiungi_flag_condiviso(df, portfolio_epal, portfolio_mgio):
    df = df.copy()

    epal_keys = set(portfolio_epal["Progetto"].map(chiave_progetto)) if not portfolio_epal.empty and "Progetto" in portfolio_epal.columns else set()
    mgio_keys = set(portfolio_mgio["Progetto"].map(chiave_progetto)) if not portfolio_mgio.empty and "Progetto" in portfolio_mgio.columns else set()

    condivisi = epal_keys & mgio_keys

    df["Presente in entrambi"] = df["Progetto"].map(chiave_progetto).isin(condivisi)
    return df


# ============================================================
# FOGLI SAL
# ============================================================

def lista_fogli_sal(sheet_names, team=None):
    risultati = []
    for nome in sheet_names:
        norm = normalizza_testo(nome)
        if re.match(r"^sal[\s_#-]", norm) or norm.startswith("sal"):
            if "gantt" in norm:
                continue
            risultati.append(nome)
    return risultati


# ============================================================
# MAPPA E MATCHING DEGLI INDICATORI SAL
# ============================================================

def calcola_totali_foglio_sal(df_sal, nome_foglio):
    riepilogo = calcola_giorni_progetto(df_sal, nome_foglio)
    if riepilogo["disponibile"]:
        return riepilogo["giorni_fatti"], riepilogo["giorni_da_fare"]
    return None, None


def estrai_nome_progetto_da_foglio_sal(nome_foglio):
    if nome_foglio is None:
        return ""

    testo = str(nome_foglio).strip()
    testo = re.sub(r"^\s*sal[_\s-]*", "", testo, flags=re.IGNORECASE)
    testo = re.sub(r"\s*\((?:EPAL|MGIO|EPAL\s*\+\s*MGIO|MGIO\s*\+\s*EPAL)\)\s*$", "", testo, flags=re.IGNORECASE)
    testo = testo.replace("_", " ")
    return re.sub(r"\s+", " ", testo).strip()


def mappa_tutti_i_sal(fogli):
    mappa_sal = []
    for nome_foglio, df_sal in fogli.items():
        norm_nome = normalizza_testo(nome_foglio)
        if not norm_nome.startswith("sal"):
            continue
        if "gantt" in norm_nome or nome_foglio in TEMPLATE_SAL:
            continue

        tot_f, tot_df = calcola_totali_foglio_sal(df_sal, nome_foglio)
        nome_p = estrai_nome_progetto_da_foglio_sal(nome_foglio)

        mappa_sal.append({
            "foglio": nome_foglio,
            "nome_p": nome_p,
            "chiave_p": chiave_progetto(nome_p),
            "tokens_p": tokenizza(nome_p),
            "tipo": tipo_sal_da_nome(nome_foglio),
            "fatto": tot_f,
            "da_fare": tot_df,
        })
    return mappa_sal


def trova_sal_corrispondenti(progetto_nome, mappa_sal):
    if not progetto_nome or pd.isna(progetto_nome):
        return []

    p_pulisce = pulisci_nome_progetto_per_match(progetto_nome)
    p_chiave = chiave_progetto(p_pulisce)
    p_tokens = tokenizza(p_pulisce)

    trovati = []
    for item in mappa_sal:
        k_sal = item["chiave_p"]
        t_sal = item["tokens_p"]

        if not k_sal:
            continue

        # Match chiave esatta o inclusione testuale
        if k_sal == p_chiave or k_sal in p_chiave or p_chiave in k_sal:
            trovati.append(item)
            continue

        # Match token
        if t_sal and (t_sal.issubset(p_tokens) or p_tokens.issubset(t_sal)):
            trovati.append(item)
            continue

        inter = t_sal & p_tokens
        if inter and (len(inter) / min(len(t_sal), len(p_tokens))) >= 0.5:
            trovati.append(item)
            continue

        if SequenceMatcher(None, k_sal, p_chiave).ratio() >= 0.65:
            trovati.append(item)
            continue

    return trovati


def trova_foglio_sal_migliore(progetto, team, sheet_names):
    candidati = lista_fogli_sal(sheet_names)
    scores = []
    for nome in candidati:
        n_p = estrai_nome_progetto_da_foglio_sal(nome)
        c_proj = chiave_progetto(progetto)
        c_sal = chiave_progetto(n_p)
        sc = SequenceMatcher(None, c_proj, c_sal).ratio()
        scores.append((nome, sc))
    scores.sort(key=lambda x: x[1], reverse=True)
    if scores:
        return scores[0]
    return None, 0.0


# ============================================================
# GIORNI DI PROGETTO PER LE VISTE DI PORTAFOGLIO
# ============================================================

def arricchisci_portafoglio_con_giorni_sal(df, fogli, sheet_names):
    """
    Calcola la somma dei giorni fatti e da fare sommando le righe delle attività
    dei fogli SAL di dettaglio per ciascun progetto in Executive.
    """
    if df is None or df.empty:
        return df

    out = df.copy()
    mappa_sal = mappa_tutti_i_sal(fogli)

    out["Fatto"] = float("nan")
    out["Da fare"] = float("nan")

    for idx in out.index:
        progetto = out.at[idx, "Progetto"]
        fogli_trovati = trova_sal_corrispondenti(progetto, mappa_sal)

        fatto_somma = 0.0
        da_fare_somma = 0.0
        almeno_uno_valido = False

        for item in fogli_trovati:
            f = item["fatto"]
            df_val = item["da_fare"]
            if pd.notna(f) or pd.notna(df_val):
                fatto_somma += (f if pd.notna(f) else 0.0)
                da_fare_somma += (df_val if pd.notna(df_val) else 0.0)
                almeno_uno_valido = True

        if almeno_uno_valido:
            out.at[idx, "Fatto"] = fatto_somma
            out.at[idx, "Da fare"] = da_fare_somma
        else:
            orig_f = df.at[idx, "Fatto"] if "Fatto" in df.columns else float("nan")
            orig_df = df.at[idx, "Da fare"] if "Da fare" in df.columns else float("nan")
            if pd.notna(orig_f) and pd.notna(orig_df):
                out.at[idx, "Fatto"] = orig_f
                out.at[idx, "Da fare"] = orig_df

    return out


# ============================================================
# DETTAGLIO ATTIVITÀ
# ============================================================

def costruisci_attivita(df, nome_foglio):
    if df is None or df.empty:
        return pd.DataFrame()

    col_attivita = trova_colonna_attivita(df)
    col_sal = trova_colonna_sal(df)
    (col_fatto, col_da_fare, _) = trova_colonne_giorni_sal(df, nome_foglio)

    if col_attivita is None:
        return pd.DataFrame()

    out = pd.DataFrame({"Attività": df[col_attivita]})
    out["Attività"] = out["Attività"].astype(str).str.strip()

    mask = mask_righe_attivita_valide(df, col_attivita)
    out = out.loc[mask].copy()
    indici = out.index

    if col_fatto is not None:
        out["Fatto"] = serie_numerica(df.loc[indici, col_fatto]).clip(lower=0)
    else:
        out["Fatto"] = float("nan")

    if col_da_fare is not None:
        out["Da fare"] = serie_numerica(df.loc[indici, col_da_fare]).clip(lower=0)
    else:
        out["Da fare"] = float("nan")

    if col_sal is not None:
        out["SAL sorgente"] = percentuale_da_excel(df.loc[indici, col_sal])
    else:
        denominatore = out["Fatto"] + out["Da fare"]
        out["SAL sorgente"] = out["Fatto"].div(denominatore.where(denominatore > 0)) * 100

    out["SAL"] = out["SAL sorgente"].clip(lower=0, upper=100)
    out["Anomalia SAL"] = (out["SAL sorgente"] < 0) | (out["SAL sorgente"] > 100)
    out["Stato"] = out["SAL"].apply(stato_da_sal)

    return out.dropna(subset=["SAL"], how="all").reset_index(drop=True)


# ============================================================
# GRAFICO RANKING
# ============================================================

def grafico_ranking(df, titolo, ordinamento="SAL decrescente"):
    plot_df = df.dropna(subset=["SAL"]).copy()
    plot_df = ordina_portafoglio(plot_df, ordinamento)

    if plot_df.empty:
        st.info("Nessun SAL disponibile.")
        return

    plot_df["Etichetta SAL"] = plot_df["SAL"].apply(formatta_percentuale)
    plot_df["SAL sorgente display"] = plot_df["SAL sorgente"].apply(lambda x: formatta_percentuale(x, 2))

    ordine_progetti = plot_df["Progetto"].tolist()

    fig = px.bar(
        plot_df,
        x="SAL",
        y="Progetto",
        orientation="h",
        color="Stato",
        color_discrete_map=COLORI_STATO,
        category_orders={"Stato": ORDINE_STATI, "Progetto": ordine_progetti},
        text="Etichetta SAL",
        custom_data=["Team", "SAL sorgente display", "Stato", "Stato sorgente", "Anomalia SAL"],
        labels={"SAL": "Avanzamento", "Progetto": "", "Stato": "Stato"},
        title=titolo,
    )

    fig.update_xaxes(
        range=[0, 100],
        tickmode="array",
        tickvals=list(range(0, 101, 10)),
        ticktext=[f"{x}%" for x in range(0, 101, 10)],
        title="SAL",
    )

    fig.update_yaxes(
        title=None,
        automargin=True,
        autorange="reversed",
        categoryorder="array",
        categoryarray=ordine_progetti,
    )

    fig.update_layout(
        height=max(430, len(plot_df) * 38),
        margin=dict(l=20, r=85, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )

    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        marker_line_width=0.5,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Team: %{customdata[0]}<br>"
            "SAL visualizzato: %{x:.1f}%<br>"
            "SAL sorgente: %{customdata[1]}<br>"
            "Stato dashboard: %{customdata[2]}<br>"
            "Stato GANTT: %{customdata[3]}<br>"
            "Anomalia SAL: %{customdata[4]}"
            "<extra></extra>"
        ),
    )

    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


# ============================================================
# DISTRIBUZIONE STATI
# ============================================================

def grafico_distribuzione_stati(df):
    conteggi = (
        df["Stato"]
        .value_counts()
        .reindex(ORDINE_STATI, fill_value=0)
        .rename_axis("Stato")
        .reset_index(name="Progetti")
    )

    fig = px.pie(
        conteggi,
        names="Stato",
        values="Progetti",
        hole=0.58,
        color="Stato",
        color_discrete_map=COLORI_STATO,
        category_orders={"Stato": ORDINE_STATI},
        title="Distribuzione dello stato dei progetti",
    )

    fig.update_traces(
        textposition="inside",
        textinfo="percent+label",
        hovertemplate=(
            "<b>%{label}</b><br>"
            "Progetti: %{value}<br>"
            "Quota: %{percent}"
            "<extra></extra>"
        ),
    )

    fig.update_layout(
        height=390,
        margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5),
    )

    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


# ============================================================
# CONFRONTO TEAM
# ============================================================

def grafico_confronto_team(df):
    ordine_team = ["EPAL", "MGIO", "EPAL+MGIO"]
    righe = []

    for team in ordine_team:
        team_df = df[(df["Team"] == team) & (df["Stato"] != "Completato")].copy()
        if team_df.empty:
            continue

        sal, metodo = portfolio_sal(team_df)
        righe.append({
            "Team": team,
            "SAL": sal,
            "Metodo": metodo,
            "Progetti in corso": len(team_df),
        })

    confronto = pd.DataFrame(righe)

    if confronto.empty:
        st.info("Confronto dei progetti in corso non disponibile.")
        return

    confronto["Etichetta"] = confronto["SAL"].apply(formatta_percentuale)

    fig = px.bar(
        confronto,
        x="SAL",
        y="Team",
        orientation="h",
        text="Etichetta",
        custom_data=["Metodo", "Progetti in corso"],
        title="Confronto SAL progetti in corso per portafoglio",
        labels={"SAL": "SAL progetti in corso", "Team": ""},
    )

    fig.update_xaxes(
        range=[0, 100],
        tickvals=[0, 20, 40, 60, 80, 100],
        ticktext=["0%", "20%", "40%", "60%", "80%", "100%"],
    )

    fig.update_layout(
        height=390,
        margin=dict(l=20, r=70, t=60, b=40),
        showlegend=False,
    )

    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "SAL progetti in corso: %{x:.1f}%<br>"
            "Progetti in corso: %{customdata[1]}<br>"
            "Calcolo: %{customdata[0]}"
            "<extra></extra>"
        ),
    )

    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


# ============================================================
# SAL REALE VS ATTESO
# ============================================================

def grafico_reale_atteso(df):
    validi = df[df["SAL atteso"].notna() & df["SAL"].notna()].copy()
    if validi.empty:
        return False

    validi["SAL reale"] = validi["SAL"]

    long_df = validi.melt(
        id_vars=["Progetto", "Team"],
        value_vars=["SAL reale", "SAL atteso"],
        var_name="Metrica",
        value_name="Percentuale",
    )

    fig = px.bar(
        long_df,
        x="Percentuale",
        y="Progetto",
        color="Metrica",
        orientation="h",
        barmode="group",
        title="SAL reale vs SAL atteso",
        labels={"Percentuale": "SAL", "Progetto": ""},
        custom_data=["Team"],
    )

    fig.update_xaxes(
        range=[0, 100],
        tickvals=list(range(0, 101, 10)),
        ticktext=[f"{x}%" for x in range(0, 101, 10)],
    )

    fig.update_layout(
        height=max(430, len(validi) * 50),
        margin=dict(l=20, r=30, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )

    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    return True


# ============================================================
# GRAFICO ATTIVITÀ
# ============================================================

def grafico_attivita(df_attivita, progetto):
    plot_df = df_attivita.sort_values("SAL", ascending=True).copy()
    plot_df["Etichetta"] = plot_df["SAL"].apply(formatta_percentuale)

    fig = px.bar(
        plot_df,
        x="SAL",
        y="Attività",
        orientation="h",
        color="Stato",
        color_discrete_map=COLORI_STATO,
        category_orders={"Stato": ORDINE_STATI},
        text="Etichetta",
        title=f"Avanzamento attività — {progetto}",
        labels={"SAL": "SAL", "Attività": "", "Stato": "Stato"},
    )

    fig.update_xaxes(
        range=[0, 100],
        tickvals=list(range(0, 101, 10)),
        ticktext=[f"{x}%" for x in range(0, 101, 10)],
    )

    fig.update_yaxes(automargin=True)

    fig.update_layout(
        height=max(430, len(plot_df) * 40),
        margin=dict(l=20, r=85, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )

    fig.update_traces(textposition="outside", cliponaxis=False, marker_line_width=0.5)
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


# ============================================================
# BARRA FATTO / DA FARE
# ============================================================

def grafico_ripartizione_lavoro(pct_fatti, pct_da_fare):
    if pd.isna(pct_fatti) or pd.isna(pct_da_fare):
        return

    df_progress = pd.DataFrame({
        "Voce": ["Lavoro complessivo", "Lavoro complessivo"],
        "Stato": ["Fatto", "Da fare"],
        "Percentuale": [pct_fatti, pct_da_fare],
        "Etichetta": [
            f"Fatto {formatta_percentuale(pct_fatti)}",
            f"Da fare {formatta_percentuale(pct_da_fare)}",
        ],
    })

    fig = px.bar(
        df_progress,
        x="Percentuale",
        y="Voce",
        color="Stato",
        orientation="h",
        barmode="stack",
        text="Etichetta",
        color_discrete_map=COLORI_RIPARTIZIONE,
        title="Ripartizione complessiva del lavoro",
        labels={"Percentuale": "", "Voce": "", "Stato": ""},
    )

    fig.update_xaxes(
        range=[0, 100],
        tickmode="array",
        tickvals=[0, 20, 40, 60, 80, 100],
        ticktext=["0%", "20%", "40%", "60%", "80%", "100%"],
    )

    fig.update_yaxes(showticklabels=False, title=None)

    fig.update_traces(
        textposition="inside",
        insidetextanchor="middle",
        hovertemplate="<b>%{fullData.name}</b><br>%{x:.1f}%<extra></extra>",
    )

    fig.update_layout(
        height=245,
        margin=dict(l=20, r=20, t=60, b=35),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        uniformtext_minsize=10,
        uniformtext_mode="hide",
    )

    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


# ============================================================
# TABELLA PORTAFOGLIO
# ============================================================

def tabella_portafoglio(df):
    colonne = ["Progetto", "Team", "SAL", "Stato", "Stato sorgente", "Fatto", "Da fare"]

    if "SAL atteso" in df.columns and df["SAL atteso"].notna().any():
        colonne += ["SAL atteso", "Scostamento"]

    tabella = df[colonne].copy()

    tabella["Fatto"] = tabella["Fatto"].apply(formatta_numero)
    tabella["Da fare"] = tabella["Da fare"].apply(formatta_numero)

    configurazione = {
        "SAL": st.column_config.ProgressColumn(
            "SAL", min_value=0, max_value=100, format="%.1f%%"
        ),
        "Stato sorgente": st.column_config.TextColumn("Stato GANTT"),
        "Fatto": st.column_config.TextColumn("Giorni fatti"),
        "Da fare": st.column_config.TextColumn("Giorni da fare"),
    }

    if "SAL atteso" in tabella.columns:
        configurazione["SAL atteso"] = st.column_config.ProgressColumn(
            "SAL atteso", min_value=0, max_value=100, format="%.1f%%"
        )
        configurazione["Scostamento"] = st.column_config.NumberColumn(
            "Scostamento (p.p.)", format="%.1f"
        )

    st.dataframe(
        tabella,
        use_container_width=True,
        hide_index=True,
        column_config=configurazione,
    )


# ============================================================
# CSV
# ============================================================

def csv_bytes(df):
    return df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


# ============================================================
# AVVIO APP
# ============================================================

if not check_password():
    st.stop()


# ============================================================
# HEADER
# ============================================================

header_left, header_right = st.columns([6, 1])

with header_left:
    st.title("📊 Dashboard Monitoraggio SAL MiniPIA")
    st.markdown(
        '<div class="dashboard-subtitle">Portafoglio progetti · EPAL · MGIO</div>',
        unsafe_allow_html=True,
    )

with header_right:
    if st.button("Esci", use_container_width=True):
        st.session_state["password_correct"] = False
        st.rerun()


# ============================================================
# CARICAMENTO GOOGLE SHEETS
# ============================================================

try:
    with st.spinner("Caricamento dati dal Dashboard..."):
        (fogli, timestamp_caricamento) = carica_workbook(SHEET_ID)
except Exception as exc:
    st.error("Impossibile caricare il Dashboard Google Sheets.")
    st.exception(exc)
    st.stop()

sheet_names = list(fogli.keys())
gantt_combinato_nome = trova_gantt_combinato(sheet_names)

st.caption(
    f"● Dati caricati dall'app: {timestamp_caricamento.strftime('%d/%m/%Y - %H:%M')} "
    f"· Cache {CACHE_TTL_SECONDS // 60} min"
)


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title("Controlli")

if st.sidebar.button("🔄 Aggiorna dati", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

vista = st.sidebar.radio(
    "Vista",
    ["Executive", "Avanzamento", "Dettaglio progetto", "Dati sorgente"],
)


# ============================================================
# PORTAFOGLIO EPAL E MGIO
# ============================================================

portfolio_epal = costruisci_portafoglio(fogli[GANTT_EPAL], "EPAL", GANTT_EPAL) if GANTT_EPAL in fogli else pd.DataFrame()
portfolio_mgio = costruisci_portafoglio(fogli[GANTT_MGIO], "MGIO", GANTT_MGIO) if GANTT_MGIO in fogli else pd.DataFrame()

portfolio_epal = arricchisci_portafoglio_con_giorni_sal(portfolio_epal, fogli, sheet_names)
portfolio_mgio = arricchisci_portafoglio_con_giorni_sal(portfolio_mgio, fogli, sheet_names)

portfolio_concat = pd.concat([portfolio_epal, portfolio_mgio], ignore_index=True)


# ============================================================
# GANTT COMBINATO
# ============================================================

portfolio_da_gantt_combinato = (
    costruisci_portafoglio_combinato(fogli[gantt_combinato_nome], portfolio_epal, portfolio_mgio, gantt_combinato_nome)
    if gantt_combinato_nome is not None else pd.DataFrame()
)


# ============================================================
# PORTAFOGLIO TUTTI - EPAL+MGIO
# ============================================================

portfolio_base_tutti = portfolio_da_gantt_combinato.copy() if not portfolio_da_gantt_combinato.empty else portfolio_concat.copy()

portfolio_tutti = consolida_progetti_univoci(portfolio_base_tutti)
portfolio_tutti = aggiungi_flag_condiviso(portfolio_tutti, portfolio_epal, portfolio_mgio)
portfolio_tutti = arricchisci_portafoglio_con_giorni_sal(portfolio_tutti, fogli, sheet_names)


# ============================================================
# SCELTA PORTAFOGLIO
# ============================================================

scope_options = ["Tutti - EPAL+MGIO", "EPAL", "MGIO"]
if not portfolio_tutti.empty and (portfolio_tutti["Team"] == "EPAL+MGIO").any():
    scope_options.append("EPAL+MGIO")

scope = st.sidebar.radio("Portfolio", scope_options)

if scope == "EPAL":
    portfolio = portfolio_epal.copy()
elif scope == "MGIO":
    portfolio = portfolio_mgio.copy()
elif scope == "EPAL+MGIO":
    portfolio = portfolio_tutti[portfolio_tutti["Team"] == "EPAL+MGIO"].copy()
else:
    portfolio = portfolio_tutti.copy()


# ============================================================
# FILTRI
# ============================================================

portfolio_filtrato = portfolio.copy()

if vista != "Dati sorgente" and not portfolio.empty:
    st.sidebar.markdown("---")
    st.sidebar.subheader("Filtri")

    ricerca = st.sidebar.text_input("🔎 Cerca progetto", key=f"ricerca_{scope}")
    filtro_stato = st.sidebar.selectbox(
        "Stato",
        ["Tutti", "In stato iniziale", "In stato intermedio", "In stato avanzato", "Completato"],
        key=f"stato_{scope}",
    )
    range_sal = st.sidebar.slider(
        "Intervallo SAL", min_value=0, max_value=100, value=(0, 100), step=1, key=f"range_sal_{scope}"
    )

    if ricerca.strip():
        portfolio_filtrato = portfolio_filtrato[
            portfolio_filtrato["Progetto"].astype(str).str.contains(ricerca.strip(), case=False, na=False)
        ]

    if filtro_stato != "Tutti":
        portfolio_filtrato = portfolio_filtrato[portfolio_filtrato["Stato"] == filtro_stato]

    portfolio_filtrato = portfolio_filtrato[
        portfolio_filtrato["SAL"].between(range_sal[0], range_sal[1], inclusive="both")
    ]


# ============================================================
# VISTA EXECUTIVE
# ============================================================

if vista == "Executive":
    if portfolio_filtrato.empty:
        st.info("Nessun progetto corrisponde ai filtri selezionati.")
        st.stop()

    portfolio_in_corso = portfolio_filtrato[portfolio_filtrato["Stato"] != "Completato"].copy()
    sal_progetti_in_corso, metodo_sal = portfolio_sal(portfolio_in_corso)

    totale_progetti = len(portfolio_filtrato)
    completati = int((portfolio_filtrato["Stato"] == "Completato").sum())
    stato_iniziale = int((portfolio_filtrato["Stato"] == "In stato iniziale").sum())
    stato_intermedio = int((portfolio_filtrato["Stato"] == "In stato intermedio").sum())
    stato_avanzato = int((portfolio_filtrato["Stato"] == "In stato avanzato").sum())
    anomalie = int(portfolio_filtrato["Anomalia SAL"].sum())

    st.subheader(f"Portfolio · {scope}")

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Progetti", totale_progetti)
    k2.metric("SAL progetti in corso", formatta_percentuale(sal_progetti_in_corso))
    k3.metric("Completati", completati)
    k4.metric("In stato iniziale", stato_iniziale)
    k5.metric("In stato intermedio", stato_intermedio)
    k6.metric("In stato avanzato", stato_avanzato)

    st.caption(f"Metodo SAL progetti in corso: {metodo_sal}.")

    if anomalie > 0:
        st.warning(
            f"Rilevati {anomalie} valori SAL fuori dall'intervallo 0–100%. "
            "Nei grafici la barra viene limitata a 0–100%, mentre il valore sorgente rimane disponibile."
        )
        with st.expander("Visualizza anomalie SAL"):
            anomalie_df = portfolio_filtrato[portfolio_filtrato["Anomalia SAL"]][
                ["Progetto", "Team", "SAL sorgente", "SAL", "Stato", "Stato sorgente"]
            ].copy()
            anomalie_df["SAL sorgente"] = anomalie_df["SAL sorgente"].apply(lambda x: formatta_percentuale(x, 2))
            anomalie_df["SAL"] = anomalie_df["SAL"].apply(formatta_percentuale)
            st.dataframe(anomalie_df, use_container_width=True, hide_index=True)

    ordinamento_executive = st.radio(
        "Ordinamento",
        ["SAL crescente", "SAL decrescente", "Nome progetto"],
        index=1,
        horizontal=True,
        key=f"ordinamento_executive_{scope}",
    )

    portfolio_executive_ordinato = ordina_portafoglio(portfolio_filtrato, ordinamento_executive)

    grafico_ranking(portfolio_filtrato, "Avanzamento dei progetti", ordinamento=ordinamento_executive)

    col_left, col_right = st.columns(2)

    with col_left:
        grafico_distribuzione_stati(portfolio_filtrato)

    with col_right:
        if scope == "Tutti - EPAL+MGIO":
            grafico_confronto_team(portfolio_filtrato)
        else:
            fatto = portfolio_in_corso["Fatto"].dropna().sum()
            residuo = portfolio_in_corso["Da fare"].dropna().sum()

            if fatto > 0 or residuo > 0:
                carico = pd.DataFrame({
                    "Voce": ["Giorni fatti", "Giorni da fare"],
                    "Giorni": [fatto, residuo],
                })
                fig = px.bar(
                    carico,
                    x="Giorni",
                    y="Voce",
                    orientation="h",
                    title="Carico di lavoro dei progetti in corso",
                    text="Giorni",
                )
                fig.update_traces(texttemplate="%{text:.1f}", textposition="outside")
                fig.update_layout(height=390, margin=dict(l=20, r=55, t=60, b=40), showlegend=False)
                st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
            else:
                st.info("I giorni fatti / da fare non sono disponibili per questo portafoglio.")

    # PRIORITÀ OPERATIVE
    priorita = (
        portfolio_filtrato[portfolio_filtrato["Stato"].isin(["In stato iniziale", "In stato intermedio"])]
        .sort_values(["SAL", "Progetto"])
        .head(10)
    )

    if not priorita.empty:
        st.markdown("---")
        st.subheader("Priorità operative")

        priorita_tabella = priorita[["Progetto", "Team", "SAL", "Stato", "Fatto", "Da fare"]].copy()
        priorita_tabella["Fatto"] = priorita_tabella["Fatto"].apply(formatta_numero)
        priorita_tabella["Da fare"] = priorita_tabella["Da fare"].apply(formatta_numero)

        st.dataframe(
            priorita_tabella,
            use_container_width=True,
            hide_index=True,
            column_config={
                "SAL": st.column_config.ProgressColumn("SAL", min_value=0, max_value=100, format="%.1f%%"),
                "Fatto": st.column_config.TextColumn("Giorni fatti"),
                "Da fare": st.column_config.TextColumn("Giorni da fare"),
            },
        )

    if "SAL atteso" in portfolio_filtrato.columns and portfolio_filtrato["SAL atteso"].notna().any():
        st.markdown("---")
        grafico_reale_atteso(portfolio_executive_ordinato)

    st.markdown("---")
    st.subheader("Portafoglio progetti")

    tabella_portafoglio(portfolio_executive_ordinato)

    st.download_button(
        "⬇️ Scarica portafoglio filtrato in CSV",
        data=csv_bytes(portfolio_executive_ordinato),
        file_name=f"portfolio_sal_{scope.lower().replace(' ', '_').replace('+', 'piu')}.csv",
        mime="text/csv",
    )


# ============================================================
# VISTA AVANZAMENTO
# ============================================================

elif vista == "Avanzamento":
    if portfolio_filtrato.empty:
        st.info("Nessun progetto corrisponde ai filtri selezionati.")
        st.stop()

    st.subheader(f"Analisi avanzamento · {scope}")

    ordinamento = st.radio(
        "Ordinamento",
        ["SAL crescente", "SAL decrescente", "Nome progetto"],
        index=1,
        horizontal=True,
        key=f"ordinamento_avanzamento_{scope}",
    )

    avanzamento_ordinato = ordina_portafoglio(portfolio_filtrato, ordinamento)

    grafico_ranking(portfolio_filtrato, "Ranking SAL", ordinamento=ordinamento)

    st.markdown("---")
    tabella_portafoglio(avanzamento_ordinato)

    if "SAL atteso" in portfolio_filtrato.columns and portfolio_filtrato["SAL atteso"].notna().any():
        st.markdown("---")
        grafico_reale_atteso(avanzamento_ordinato)


# ============================================================
# VISTA DETTAGLIO PROGETTO
# ============================================================

elif vista == "Dettaglio progetto":
    if portfolio.empty:
        st.info("Nessun progetto disponibile.")
        st.stop()

    options = portfolio[["Progetto", "Team"]].drop_duplicates().sort_values(["Progetto", "Team"]).copy()
    options["Label"] = options["Progetto"] + " · " + options["Team"]

    scelta = st.selectbox("Seleziona il progetto", options["Label"].tolist())
    riga_scelta = options[options["Label"] == scelta].iloc[0]

    progetto = riga_scelta["Progetto"]
    team = riga_scelta["Team"]

    righe_progetto = portfolio[(portfolio["Progetto"] == progetto) & (portfolio["Team"] == team)]

    if righe_progetto.empty:
        st.warning("Dati del progetto non disponibili.")
        st.stop()

    riepilogo = righe_progetto.iloc[0]

    foglio_auto, score = trova_foglio_sal_migliore(progetto, team, sheet_names)
    candidati_sal = lista_fogli_sal(sheet_names, team) or lista_fogli_sal(sheet_names, None)

    st.subheader(progetto)
    st.caption(f"Team: {team}")

    if not candidati_sal:
        st.warning("Non è stato individuato alcun foglio SAL compatibile.")
        st.stop()

    default_idx = candidati_sal.index(foglio_auto) if (foglio_auto is not None and foglio_auto in candidati_sal) else 0

    foglio_sal = st.selectbox(
        "Foglio SAL associato",
        candidati_sal,
        index=default_idx,
        help="La dashboard associa automaticamente il foglio SAL più compatibile.",
    )

    df_sal = fogli[foglio_sal]
    riepilogo_giorni = calcola_giorni_progetto(df_sal, foglio_sal)

    if not riepilogo_giorni["disponibile"]:
        fallback_gantt = calcola_giorni_da_gantt(riepilogo["Fatto"], riepilogo["Da fare"])
        if fallback_gantt is not None:
            riepilogo_giorni = fallback_gantt

    sal_gantt = riepilogo["SAL"]
    sal_visualizzato = riepilogo_giorni["pct_fatti"] if pd.isna(sal_gantt) and riepilogo_giorni["disponibile"] else sal_gantt

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("SAL progetto", formatta_percentuale(sal_visualizzato))
    p2.metric("Giorni fatti", formatta_numero(riepilogo_giorni["giorni_fatti"]))
    p3.metric("Giorni da fare", formatta_numero(riepilogo_giorni["giorni_da_fare"]))
    p4.metric("Stato", riepilogo["Stato"])

    q1, q2 = st.columns(2)
    q1.metric("✅ Percentuale complessiva giorni fatti", formatta_percentuale(riepilogo_giorni["pct_fatti"]))
    q2.metric("⏳ Percentuale complessiva giorni da fare", formatta_percentuale(riepilogo_giorni["pct_da_fare"]))

    if riepilogo_giorni["disponibile"]:
        grafico_ripartizione_lavoro(riepilogo_giorni["pct_fatti"], riepilogo_giorni["pct_da_fare"])

    attivita = costruisci_attivita(df_sal, foglio_sal)

    if not attivita.empty:
        st.markdown("---")
        grafico_attivita(attivita, progetto)

        st.subheader("Dettaglio attività")
        tab_attivita = attivita[["Attività", "SAL", "Stato", "Fatto", "Da fare"]].copy()

        st.dataframe(
            tab_attivita,
            use_container_width=True,
            hide_index=True,
            column_config={
                "SAL": st.column_config.ProgressColumn("SAL", min_value=0, max_value=100, format="%.1f%%"),
                "Fatto": st.column_config.NumberColumn("Giorni fatti", format="%.1f"),
                "Da fare": st.column_config.NumberColumn("Giorni da fare", format="%.1f"),
            },
        )

    with st.expander("Visualizza dati sorgente del foglio SAL"):
        st.dataframe(df_sal, use_container_width=True)


# ============================================================
# VISTA DATI SORGENTE
# ============================================================

elif vista == "Dati sorgente":
    st.subheader("Dati sorgente")

    preferiti = []
    if gantt_combinato_nome is not None:
        preferiti.append(gantt_combinato_nome)

    for nome in [GANTT_EPAL, GANTT_MGIO]:
        if nome in sheet_names:
            preferiti.append(nome)

    altri = [nome for nome in sheet_names if nome not in preferiti]
    elenco = preferiti + altri

    foglio_raw = st.selectbox("Foglio da visualizzare", elenco)
    df_raw = fogli[foglio_raw]

    st.caption(f"{len(df_raw)} righe · {len(df_raw.columns)} colonne")
    st.dataframe(df_raw, use_container_width=True)

    st.download_button(
        "⬇️ Scarica foglio in CSV",
        data=csv_bytes(df_raw),
        file_name=f"{re.sub(r'[^A-Za-z0-9_-]+', '_', foglio_raw)}.csv",
        mime="text/csv",
    )
