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

import numpy as np
import plotly.graph_objects as go

# Aggiungi tra le configurazioni dei colori:
COLORI_TEAM = {
    "EPAL": "#1E40AF",
    "MGIO": "#D97706",
}

# Aggiungi per l'ordinamento cronologico dei mesi:
MESI_ORDINE = {
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
}

# ============================================================
# CONFIGURAZIONE GENERALE
# ============================================================

st.set_page_config(
    page_title="Dashboard Monitoraggio SAL MiniPIA",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    SHEET_ID = st.secrets["SOURCE_FILE_ID"]
except Exception:
    st.error("Il secret SOURCE_FILE_ID non è configurato nelle impostazioni di Streamlit.")
    st.stop()


GANTT_EPAL = "GANTT_SAL_PROGETTI_EPAL"
GANTT_MGIO = "GANTT_SAL_PROGETTI_MGIO"
GANTT_MINDS = "MINDS_SAL"

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

        [data-testid="stMetricValue"] {
            font-size: 1.3rem !important;
            white-space: normal !important;
            word-break: break-word !important;
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
# FUNZIONI GENERALI E PULIZIA TESTO
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


def pulisci_nome_progetto(text):
    if not text or pd.isna(text):
        return ""
    text_str = str(text)
    
    def filtri_parentesi(match):
        val = match.group(1).strip().upper().replace(" ", "")
        if val in ["EPAL", "MGIO", "EPAL+MGIO", "MGIO+EPAL"]:
            return f"({val})"
        return " "
        
    cleaned = re.sub(r"\(([^)]*)\)", filtri_parentesi, text_str)
    return cleaned.strip()


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
        token
        for token in text.split()
        if len(token) > 1
        and token not in stopwords
    }


def pulisci_dataframe(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")
    return df


# ============================================================
# CONVERSIONE NUMERICA E PERCENTUALI
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


def formatta_percentuale(value, decimali=1):
    if pd.isna(value):
        return "N/D"
    return f"{float(value):.{decimali}f}%".replace(".", ",")


def formatta_numero(value, decimali=1):
    if pd.isna(value):
        return "N/D"
    return f"{float(value):.{decimali}f}".replace(".", ",")


# ============================================================
# CLASSIFICAZIONE SAL E STATO
# ============================================================

def ordina_portafoglio(df, ordinamento):
    out = df.copy()
    if ordinamento == "SAL crescente":
        return out.sort_values(["SAL", "Progetto"], ascending=[True, True], na_position="last")
    if ordinamento == "SAL decrescente":
        return out.sort_values(["SAL", "Progetto"], ascending=[False, True], na_position="last")
    if ordinamento == "Nome progetto":
        return out.sort_values(
            "Progetto", ascending=True, key=lambda serie: serie.astype(str).str.lower(), na_position="last"
        )
    return out


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


def stato_sorgente_e_completo(value):
    if value is None or pd.isna(value):
        return False
    text_raw = str(value)
    if "✅" in text_raw or "✔" in text_raw:
        return True
    stato = normalizza_testo(value)
    stati_completati = {"completo", "completato", "completed", "chiuso", "concluso", "terminato", "finito"}
    return stato in stati_completati or stato.startswith("complet") or "completo" in stato or "completato" in stato


def normalizza_stato_progetto(stato_sorgente, sal):
    if stato_sorgente_e_completo(stato_sorgente):
        return "Completato"
    return stato_da_sal(sal)


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
# RICONOSCIMENTO COLONNE E FOGLI
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
            "NOME PROGETTO", "CLIENTE", "COMMESSA", "UTENTE",
            "ATTIVITÀ / PROGETTO", "ATTIVITA / PROGETTO",
        ],
        contains_any=["progetto", "cliente", "commessa", "utente"],
    )
    if col is not None:
        return col

    for col in df.columns:
        serie = df[col].dropna()
        if serie.empty:
            continue
        if serie.apply(lambda x: isinstance(x, str)).mean() >= 0.50:
            return col

    if len(df.columns) > 0:
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
        exact=["% COMPLETAMENTO", "PERCENTUALE COMPLETAMENTO", "COMPLETAMENTO", "SAL", "% SAL", "SAL %"],
    )
    if col is not None:
        return col

    col = trova_colonna(
        df,
        contains_any=["completamento", "percentuale", "% sal", "sal %"],
        exclude=["atteso", "previsto", "target", "pianificato", "rosso", "giallo", "verde"],
    )
    if col is not None:
        return col

    return trova_colonna(df, contains_any=["sal"], exclude=["atteso", "previsto", "target", "pianificato"])


def trova_colonna_stato(df):
    return trova_colonna(df, exact=["STATO", "STATUS", "STATO PROGETTO"], contains_any=["stato", "status"])


def trova_colonna_fatto(df):
    col = trova_colonna(
        df,
        exact=[
            "GIORNI FATTI", "FATTO (GIORNI)", "FATTO", "GIORNI EFFETTUATI",
            "GIORNI FATTO", "GG FATTI", "GG FATTO", "GIORNI UOMO FATTI", "FATTI", "GIORNI FATTO (GG)"
        ],
        contains_any=["fatto", "fatti", "effettuati", "svolti"],
        exclude=["da fare", "da_fare", "atteso", "previsto", "target", "pianificato"]
    )
    if col is not None:
        return col
    return trova_colonna(df, contains_all=["giorn", "fatt"])


def trova_colonna_da_fare(df):
    col = trova_colonna(
        df,
        exact=[
            "GIORNI DA FARE", "DA FARE (GIORNI)", "DA FARE",
            "GIORNI RESIDUI", "RESIDUO", "RESIDUI", "GG DA FARE",
            "GG RESIDUI", "GIORNI RIMANENTI", "RIMANENTI",
            "TOT GIORNI RESIDUI", "TOT ORE RESIDUE"
        ],
        contains_any=["da fare", "da_fare", "residui", "residuo", "rimanenti"],
        exclude=["fatto", "fatti", "atteso", "previsto", "minuti", "ore"]
    )
    if col is not None:
        return col
    return trova_colonna(df, contains_all=["giorn", "fare"])


def trova_colonna_lavoro_totale_ore(df):
    return trova_colonna(
        df,
        exact=["TOT ORE", "TOT ORE PROGETTO", "ORE TOTALI", "VALORE"],
        contains_any=["tot ore", "ore totali", "valore"]
    )


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


def trova_colonne_giorni_sal(df, nome_foglio):
    col_fatto = trova_colonna_fatto(df)
    col_da_fare = trova_colonna_da_fare(df)

    if col_fatto is not None and col_da_fare is not None:
        return (col_fatto, col_da_fare, "intestazioni del foglio SAL")

    num_cols = len(df.columns)
    candidates = []
    if num_cols >= 9:
        candidates.append((df.columns[7], df.columns[8]))
    if num_cols >= 8:
        candidates.append((df.columns[6], df.columns[7]))
    if num_cols >= 7:
        candidates.append((df.columns[5], df.columns[6]))

    for c1, c2 in candidates:
        if c1 == c2:
            continue
        s1 = serie_numerica(df[c1])
        s2 = serie_numerica(df[c2])
        if s1.notna().any() or s2.notna().any():
            f_col = col_fatto if col_fatto is not None else c1
            df_col = col_da_fare if col_da_fare is not None else c2
            return (f_col, df_col, "colonne adiacenti del SAL")

    return (col_fatto, col_da_fare, "intestazioni disponibili")


def tipo_sal_da_nome(nome):
    nome_norm = normalizza_testo(nome).replace(" ", "")
    if "(epal+mgio)" in nome_norm or "(mgio+epal)" in nome_norm:
        return "EPAL+MGIO"
    if "(epal)" in nome_norm:
        return "EPAL"
    if "(mgio)" in nome_norm:
        return "MGIO"
    return None


def estrai_nome_progetto_da_foglio_sal(nome_foglio):
    if nome_foglio is None:
        return ""
    testo = str(nome_foglio).strip()
    testo = re.sub(r"^\s*sal[_\s-]*", "", testo, flags=re.IGNORECASE)
    testo = re.sub(r"\s*\((?:EPAL|MGIO|EPAL\s*\+\s*MGIO|MGIO\s*\+\s*EPAL)\)\s*$", "", testo, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", testo.replace("_", " ")).strip()


def score_match_foglio(progetto, foglio, team=None):
    p_key = chiave_progetto(pulisci_nome_progetto(progetto))
    f_key = chiave_progetto(estrai_nome_progetto_da_foglio_sal(foglio))

    if not p_key or not f_key:
        return 0.0

    base_score = 0.0

    if p_key == f_key:
        base_score = 1.0
    elif ("anal" in p_key and "pred" in p_key) and ("anal" in f_key and "pred" in f_key):
        base_score = 0.95
    else:
        p_ns, f_ns = p_key.replace(" ", ""), f_key.replace(" ", "")
        if p_ns == f_ns:
            base_score = 0.98
        elif f_ns in p_ns or p_ns in f_ns:
            if min(len(p_ns), len(f_ns)) / max(len(p_ns), len(f_ns)) >= 0.35:
                base_score = 0.85

    if base_score == 0.0:
        p_words = tokenizza(pulisci_nome_progetto(progetto))
        f_words = tokenizza(estrai_nome_progetto_da_foglio_sal(foglio))
        
        if p_words and f_words:
            matches = sum(1 for fw in f_words if any(pw.startswith(fw) or fw.startswith(pw) for pw in p_words))
            if matches > 0 and matches == len(f_words):
                base_score = 0.80 + (0.15 * (matches / max(len(p_words), len(f_words))))
            else:
                intersection = p_words & f_words
                if intersection:
                    jaccard = len(intersection) / len(p_words | f_words)
                    if jaccard >= 0.30:
                        base_score = 0.70 + (0.20 * jaccard)

    if base_score == 0.0:
        seq_ratio = SequenceMatcher(None, p_key, f_key).ratio()
        if seq_ratio >= 0.60:
            base_score = seq_ratio

    if base_score > 0.0 and team:
        tipo_foglio = tipo_sal_da_nome(foglio)
        if tipo_foglio == team:
            base_score += 0.05
        elif team == "EPAL+MGIO" and tipo_foglio == "EPAL+MGIO":
            base_score += 0.08

    return base_score


def opport_ricerca_foglio(progetto, filtro_team, sheet_names, soglia_minima=0.35):
    candidati = []
    for nome in sheet_names:
        norm_nome = normalizza_testo(nome)
        if not norm_nome.startswith("sal") or "gantt" in norm_nome:
            continue
        
        tipo = tipo_sal_da_nome(nome)
        if filtro_team == "EPAL+MGIO":
            candidati.append(nome)
        elif filtro_team is None or tipo in {filtro_team, "EPAL+MGIO", None}:
            candidati.append(nome)

    if not candidati:
        return None, 0.0
        
    scores = sorted(
        [(nome, score_match_foglio(progetto, nome, filtro_team)) for nome in candidati],
        key=lambda x: x[1],
        reverse=True
    )
    if scores and scores[0][1] >= soglia_minima:
        return scores[0]
    return None, 0.0


def trova_foglio_sal_migliore(progetto, team, sheet_names):
    foglio, score = opport_ricerca_foglio(progetto, team, sheet_names, 0.35)
    if foglio:
        return foglio, score
    return opport_ricerca_foglio(progetto, None, sheet_names, 0.35)


def lista_fogli_sal(sheet_names, team=None):
    risultati = []
    for nome in sheet_names:
        norm = normalizza_testo(nome)
        if not norm.startswith("sal") or "gantt" in norm:
            continue
            
        tipo = tipo_sal_da_nome(nome)
        if team is None:
            risultati.append(nome)
        elif team == "EPAL" and tipo in {"EPAL", "EPAL+MGIO", None}:
            risultati.append(nome)
        elif team == "MGIO" and tipo in {"MGIO", "EPAL+MGIO", None}:
            risultati.append(nome)
        elif team == "EPAL+MGIO":
            risultati.append(nome)
        elif team not in {"EPAL", "MGIO", "EPAL+MGIO"}:
            risultati.append(nome)
            
    return risultati


# ============================================================
# ESTREZIONE METRICHE DAL FOGLIO UNIFICATO MINDS_RIEPILOGO
# ============================================================

def calcola_metriche_minds(fogli):
    if "MINDS_RIEPILOGO" not in fogli:
        return {}

    df_riep = fogli["MINDS_RIEPILOGO"]
    col_proj = trova_colonna_progetto(df_riep)
    col_team = trova_colonna_team(df_riep)
    col_gt = trova_colonna(df_riep, exact=["GIORNI TOTALI"])
    col_gf = trova_colonna(df_riep, exact=["GIORNI FATTI"])
    col_gdf = trova_colonna(df_riep, exact=["GIORNI DA FARE"])

    if not col_proj:
        return {}

    metriche_minds = {}
    for _, row in df_riep.iterrows():
        p_key = chiave_progetto(row[col_proj])
        if not p_key:
            continue
        t_key = normalizza_team(row[col_team]) if col_team and pd.notna(row[col_team]) else "N/D"

        gt = serie_numerica(pd.Series([row[col_gt]])).iloc[0] if col_gt else float("nan")
        gf = serie_numerica(pd.Series([row[col_gf]])).iloc[0] if col_gf else float("nan")
        gdf = serie_numerica(pd.Series([row[col_gdf]])).iloc[0] if col_gdf else float("nan")

        item = {
            "giorni_totali": gt,
            "giorni_fatti": gf,
            "giorni_da_fare": gdf,
        }
        metriche_minds[(p_key, t_key)] = item
        metriche_minds[p_key] = item

    return metriche_minds

def estrai_dati_monitor_mensile(fogli):
    if "MINDS_MONITOR_MENSILE" not in fogli:
        return None, None

    df_mon = fogli["MINDS_MONITOR_MENSILE"]
    if df_mon.empty:
        return None, None

    # Helper per estrarre Mese Nome, Mese Num e Anno in modo sicuro anche da Timestamp Excel
    def parsing_mese_anno(val_m, val_a_raw):
        if pd.isna(val_m):
            return "", 0, 2025

        if isinstance(val_m, (datetime, pd.Timestamp)):
            m_num = val_m.month
            anno = val_m.year
            mesi_inv = {
                9: "settembre",
                10: "ottobre",
                11: "novembre",
                12: "dicembre",
                1: "gennaio",
                2: "febbraio",
                3: "marzo",
                4: "aprile",
                5: "maggio",
                6: "giugno",
                7: "luglio",
                8: "agosto",
            }
            return mesi_inv.get(m_num, ""), m_num, anno

        s = str(val_m).strip().lower()
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            try:
                dt = pd.to_datetime(s)
                mesi_inv = {
                    9: "settembre",
                    10: "ottobre",
                    11: "novembre",
                    12: "dicembre",
                    1: "gennaio",
                    2: "febbraio",
                    3: "marzo",
                    4: "aprile",
                    5: "maggio",
                    6: "giugno",
                    7: "luglio",
                    8: "agosto",
                }
                return mesi_inv.get(dt.month, ""), dt.month, dt.year
            except Exception:
                pass

        match_year = re.search(r"\b(20\d{2})\b", s)
        if match_year:
            anno = int(match_year.group(1))
        else:
            try:
                anno = int(float(str(val_a_raw)))
            except Exception:
                anno = 2025

        m_nome = ""
        m_num = 0
        for nome, num in MESI_ORDINE.items():
            if nome in s:
                m_nome = nome
                m_num = num
                break

        return m_nome, m_num, anno

    # 1. Dettaglio Analitico Attività (Colonne A:G)
    df_db = df_mon.iloc[:, :7].copy()
    df_db.columns = [
        "ANNO_RAW",
        "MESE",
        "PROGETTO",
        "TEAM",
        "ATTIVITÀ",
        "MINUTI",
        "GIORNI",
    ]

    df_db["GIORNI"] = serie_numerica(df_db["GIORNI"])
    df_db["PROGETTO"] = df_db["PROGETTO"].astype(str).str.strip()
    df_db["TEAM"] = df_db["TEAM"].astype(str).str.strip().str.upper()

    df_db = df_db.dropna(subset=["PROGETTO", "GIORNI"]).copy()
    df_db = df_db[
        (df_db["GIORNI"] > 0)
        & (~df_db["PROGETTO"].str.lower().isin(["nan", "none", "totale", ""]))
    ].copy()

    parsed_db = [
        parsing_mese_anno(m, a)
        for m, a in zip(df_db["MESE"], df_db["ANNO_RAW"])
    ]
    df_db["MESE_NOME"] = [p[0] for p in parsed_db]
    df_db["MESE_NUM"] = [p[1] for p in parsed_db]
    df_db["ANNO"] = [p[2] for p in parsed_db]

    df_db["SORT_KEY"] = df_db["ANNO"] * 100 + df_db["MESE_NUM"]
    df_db["PERIODO"] = (
        df_db["MESE_NOME"].str.capitalize() + " " + df_db["ANNO"].astype(str)
    )

    # 2. Riepilogo Ufficiale Giorni Lavorati per Team (Colonne K:O)
    if df_mon.shape[1] >= 15:
        df_tot_sub = df_mon.iloc[:, 10:15].copy()
        df_tot_sub.columns = ["ANNO_C", "MESE_C", "EPAL", "MGIO", "TOTALE_MESE"]

        df_tot_sub["EPAL"] = serie_numerica(df_tot_sub["EPAL"])
        df_tot_sub["MGIO"] = serie_numerica(df_tot_sub["MGIO"])
        df_tot_sub["TOTALE_MESE"] = serie_numerica(df_tot_sub["TOTALE_MESE"])

        df_tot_raw = df_tot_sub[
            (df_tot_sub["MESE_C"].notna())
            & (
                ~df_tot_sub["MESE_C"]
                .astype(str)
                .str.upper()
                .str.contains("TOTALE|ANNO|MESE")
            )
        ].copy()

        df_tot_long = df_tot_raw.melt(
            id_vars=["ANNO_C", "MESE_C", "TOTALE_MESE"],
            value_vars=["EPAL", "MGIO"],
            var_name="TEAM",
            value_name="GIORNI_LAVORATI_UFFICIALI",
        )
        df_tot_long["TEAM"] = (
            df_tot_long["TEAM"].astype(str).str.strip().str.upper()
        )

        parsed_tot = [
            parsing_mese_anno(m, a)
            for m, a in zip(df_tot_long["MESE_C"], df_tot_long["ANNO_C"])
        ]
        df_tot_long["MESE_NOME"] = [p[0] for p in parsed_tot]
        df_tot_long["MESE_NUM"] = [p[1] for p in parsed_tot]
        df_tot_long["ANNO"] = [p[2] for p in parsed_tot]

        df_tot_long["SORT_KEY"] = (
            df_tot_long["ANNO"] * 100 + df_tot_long["MESE_NUM"]
        )
        df_tot_long["PERIODO"] = (
            df_tot_long["MESE_NOME"].str.capitalize()
            + " "
            + df_tot_long["ANNO"].astype(str)
        )
    else:
        df_tot_long = pd.DataFrame()

    return df_db, df_tot_long

def arricchisci_portafoglio_minds(df_portfolio, metriche_minds):
    if df_portfolio.empty or not metriche_minds:
        return df_portfolio

    out = df_portfolio.copy()
    for idx in out.index:
        p_key = chiave_progetto(out.at[idx, "Progetto"])
        t_key = normalizza_team(out.at[idx, "Team"]) if "Team" in out.columns else "N/D"
        
        m = metriche_minds.get((p_key, t_key), metriche_minds.get(p_key, None))
        if m:
            if pd.isna(out.at[idx, "Fatto"]) and pd.notna(m["giorni_fatti"]):
                out.at[idx, "Fatto"] = round(m["giorni_fatti"], 1)
            if pd.isna(out.at[idx, "Da fare"]) and pd.notna(m["giorni_da_fare"]):
                out.at[idx, "Da fare"] = round(m["giorni_da_fare"], 1)

            tot = m["giorni_totali"]
            if pd.notna(tot) and tot > 0 and pd.notna(out.at[idx, "Fatto"]):
                sal_calc = (out.at[idx, "Fatto"] / tot) * 100.0
                if pd.isna(out.at[idx, "SAL sorgente"]):
                    out.at[idx, "SAL sorgente"] = sal_calc
                    out.at[idx, "SAL"] = min(max(sal_calc, 0.0), 100.0)
                    ss = out.at[idx, "Stato sorgente"] if "Stato sorgente" in out.columns else ""
                    out.at[idx, "Stato"] = normalizza_stato_progetto(ss, sal_calc)

    return out

# ============================================================
# GESTIONE RIGHE E CALCOLO GIORNI
# ============================================================

def testo_non_nan(serie):
    return ~serie.isin(["nan", "none", "nat"])


def mask_righe_attivita_valide(df, col_attivita=None):
    if col_attivita is None:
        return pd.Series(True, index=df.index, dtype=bool)
    testo = df[col_attivita].astype(str).map(normalizza_testo)
    return testo.ne("") & testo_non_nan(testo) & ~testo.str.match(r"^(totale|totali|total)\b", na=False)


def calcola_giorni_progetto(df_sal, nome_foglio):
    col_fatto, col_da_fare, fonte_colonne = trova_colonne_giorni_sal(df_sal, nome_foglio)
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

    if col_fatto is None and col_da_fare is None:
        return risultato_vuoto

    col_attivita = trova_colonna_attivita(df_sal)
    df_valido = df_sal.loc[mask_righe_attivita_valide(df_sal, col_attivita)].copy()
    if df_valido.empty:
        return risultato_vuoto

    col_sal = trova_colonna_sal(df_sal)
    if col_sal:
        sal_serie = percentuale_da_excel(df_valido[col_sal])
    else:
        sal_serie = pd.Series(float("nan"), index=df_valido.index)

    if col_fatto:
        fatto = serie_numerica(df_valido[col_fatto])
    else:
        fatto = pd.Series(float("nan"), index=df_valido.index)
        
    if col_da_fare:
        da_fare = serie_numerica(df_valido[col_da_fare])
    else:
        da_fare = pd.Series(float("nan"), index=df_valido.index)

    def ricava_fatto_complementare(f, r, s):
        if pd.notna(f) and f > 0:
            return f
        if pd.isna(s) or pd.isna(r):
            return f
        if s <= 0:
            return 0.0
        if s >= 100:
            return f
        return r * (s / (100.0 - s))

    fatto = pd.Series(
        [ricava_fatto_complementare(f, r, s) for f, r, s in zip(fatto, da_fare, sal_serie)], 
        index=df_valido.index
    )

    mask_valida = (fatto.notna() | da_fare.notna())
    fatto = fatto.loc[mask_valida]
    da_fare = da_fare.loc[mask_valida]

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
            "pct_da_fare": 0.0
        }

    return {
        "disponibile": True,
        "giorni_fatti": tot_fatto,
        "giorni_da_fare": tot_da_fare,
        "giorni_totali": totale,
        "pct_fatti": (tot_fatto / totale) * 100,
        "pct_da_fare": (tot_da_fare / totale) * 100,
        "col_fatto": col_fatto,
        "col_da_fare": col_da_fare,
        "fonte": fonte_colonne,
    }


def calcola_giorni_da_gantt(fatto, da_fare):
    if pd.isna(fatto) or pd.isna(da_fare):
        return None
    fatto = float(fatto)
    da_fare = float(da_fare)
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


def calcola_giorni_da_sal_dettaglio(progetto, team, fogli, sheet_names, sal_gantt=None):
    team_norm = normalizza_team(team)

    if team_norm == "EPAL+MGIO":
        foglio_epal, _ = opport_ricerca_foglio(progetto, "EPAL", sheet_names, 0.35)
        foglio_mgio, _ = opport_ricerca_foglio(progetto, "MGIO", sheet_names, 0.35)

        if foglio_epal and foglio_epal in fogli:
            res_epal = calcola_giorni_progetto(fogli[foglio_epal], foglio_epal)
        else:
            res_epal = {"disponibile": False}
            
        if foglio_mgio and foglio_mgio in fogli:
            res_mgio = calcola_giorni_progetto(fogli[foglio_mgio], foglio_mgio)
        else:
            res_mgio = {"disponibile": False}

        valid_epal = res_epal["disponibile"]
        valid_mgio = res_mgio["disponibile"]

        if foglio_epal and foglio_mgio and foglio_epal != foglio_mgio and valid_epal and valid_mgio:
            return (res_epal["giorni_fatti"] + res_mgio["giorni_fatti"]), (res_epal["giorni_da_fare"] + res_mgio["giorni_da_fare"])
            
        if valid_epal:
            return res_epal["giorni_fatti"], res_epal["giorni_da_fare"]
            
        if valid_mgio:
            return res_mgio["giorni_fatti"], res_mgio["giorni_da_fare"]

        foglio_comb, _ = opport_ricerca_foglio(progetto, None, sheet_names, 0.35)
        if foglio_comb and foglio_comb in fogli:
            res_comb = calcola_giorni_progetto(fogli[foglio_comb], foglio_comb)
            if res_comb["disponibile"]:
                return res_comb["giorni_fatti"], res_comb["giorni_da_fare"]

        return float("nan"), float("nan")
    else:
        foglio_single, _ = opport_ricerca_foglio(progetto, team_norm, sheet_names, 0.35)
        if not foglio_single:
            foglio_single, _ = opport_ricerca_foglio(progetto, None, sheet_names, 0.35)

        if foglio_single and foglio_single in fogli:
            res = calcola_giorni_progetto(fogli[foglio_single], foglio_single)
            if res["disponibile"]:
                return res["giorni_fatti"], res["giorni_da_fare"]

        return float("nan"), float("nan")


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

    if col_fatto:
        out["Fatto"] = serie_numerica(df.loc[indici, col_fatto]).round(1)
    else:
        out["Fatto"] = float("nan")
        
    if col_da_fare:
        out["Da fare"] = serie_numerica(df.loc[indici, col_da_fare]).round(1)
    else:
        out["Da fare"] = float("nan")
    
    if col_sal:
        out["SAL sorgente"] = percentuale_da_excel(df.loc[indici, col_sal])
    else:
        denominatore = out["Fatto"] + out["Da fare"]
        out["SAL sorgente"] = (out["Fatto"].div(denominatore.where(denominatore > 0)) * 100)

    out["SAL"] = out["SAL sorgente"].clip(lower=0, upper=100)
    out["Anomalia SAL"] = (out["SAL sorgente"] < 0) | (out["SAL sorgente"] > 100)

    def fallback_fatto(riga):
        f, r, s = riga["Fatto"], riga["Da fare"], riga["SAL"]
        if pd.notna(f) and f > 0:
            return f
        if pd.isna(s) or pd.isna(r):
            return f
        if s <= 0:
            return 0.0
        if s >= 100:
            return r
        return r * (s / (100.0 - s))

    out["Fatto"] = out.apply(fallback_fatto, axis=1)

    if col_sal is None:
        denominatore = out["Fatto"] + out["Da fare"]
        out["SAL sorgente"] = (out["Fatto"].div(denominatore.where(denominatore > 0)) * 100)
        out["SAL"] = out["SAL sorgente"].clip(lower=0, upper=100)

    if col_stato:
        out["Stato sorgente"] = df.loc[indici, col_stato].where(df.loc[indici, col_stato].notna(), "").astype(str).str.strip()
    else:
        out["Stato sorgente"] = ""

    # Imposta pulita la dicitura COMPLETO evitando duplicazioni di testo
    for idx in indici:
        nome_orig = str(df.loc[idx, col_progetto])
        cur = out.at[idx, "Stato sorgente"]
        if stato_sorgente_e_completo(cur) or "✅" in nome_orig or "✔" in nome_orig:
            out.at[idx, "Stato sorgente"] = "COMPLETO"

    out["Stato"] = [normalizza_stato_progetto(ss, sal) for ss, sal in zip(out["Stato sorgente"], out["SAL"])]

    if col_sal_atteso:
        out["SAL atteso"] = percentuale_da_excel(df.loc[indici, col_sal_atteso]).clip(lower=0, upper=100)
        out["Scostamento"] = out["SAL"] - out["SAL atteso"]
    else:
        out["SAL atteso"] = float("nan")
        out["Scostamento"] = float("nan")

    out["Foglio origine"] = source_sheet
    return out.reset_index(drop=True)


def costruisci_portafoglio_combinato(df, portfolio_epal, portfolio_mgio, source_sheet):
    if df is None or df.empty:
        return pd.DataFrame()

    base = costruisci_portafoglio(df, "N/D", source_sheet)
    if base.empty:
        return base

    col_fatto_epal = trova_colonna(df, contains_all=["fatto", "epal"])
    col_da_fare_epal = trova_colonna(df, contains_all=["da fare", "epal"]) or trova_colonna(df, contains_all=["residui", "epal"])
    col_fatto_mgio = trova_colonna(df, contains_all=["fatto", "mgio"])
    col_da_fare_mgio = trova_colonna(df, contains_all=["da fare", "mgio"]) or trova_colonna(df, contains_all=["residui", "mgio"])

    if col_fatto_epal or col_fatto_mgio or col_da_fare_epal or col_da_fare_mgio:
        f_epal = serie_numerica(df[col_fatto_epal]) if col_fatto_epal else pd.Series(0, index=df.index)
        f_mgio = serie_numerica(df[col_fatto_mgio]) if col_fatto_mgio else pd.Series(0, index=df.index)
        d_epal = serie_numerica(df[col_da_fare_epal]) if col_da_fare_epal else pd.Series(0, index=df.index)
        d_mgio = serie_numerica(df[col_da_fare_mgio]) if col_da_fare_mgio else pd.Series(0, index=df.index)

        fatto_tot = f_epal.fillna(0) + f_mgio.fillna(0)
        da_fare_tot = d_epal.fillna(0) + d_mgio.fillna(0)

        mask_validi = (f_epal.notna() | f_mgio.notna() | d_epal.notna() | d_mgio.notna())
        base["Fatto"] = fatto_tot.where(mask_validi, float("nan")).round(1)
        base["Da fare"] = da_fare_tot.where(mask_validi, float("nan")).round(1)

    ek = set(portfolio_epal["Progetto"].map(chiave_progetto)) if not portfolio_epal.empty else set()
    mk = set(portfolio_mgio["Progetto"].map(chiave_progetto)) if not portfolio_mgio.empty else set()

    col_progetto = trova_colonna_progetto(df)
    col_team = trova_colonna_team(df)

    team_map = {}
    if col_progetto and col_team:
        for progetto, team in zip(df[col_progetto], df[col_team]):
            key = chiave_progetto(progetto)
            team_norm = normalizza_team(team)
            if key and team_norm != "N/D":
                if key not in team_map:
                    team_map[key] = set()
                team_map[key].add(team_norm)

    def assegna_team(progetto):
        key = chiave_progetto(progetto)
        if key in team_map:
            ts = team_da_insieme(team_map[key])
            if ts != "N/D":
                return ts
        if key in ek and key in mk:
            return "EPAL+MGIO"
        if key in ek:
            return "EPAL"
        if key in mk:
            return "MGIO"
        return "N/D"

    base["Team"] = base["Progetto"].apply(assegna_team)
    return base


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
        nomi = [str(x).strip() for x in gruppo["Progetto"].tolist() if pd.notna(x) and str(x).strip()]
        progetto = nomi[0] if nomi else chiave
        team = team_da_insieme({str(x).strip() for x in gruppo["Team"].tolist() if pd.notna(x)})

        if "Fatto" in gruppo.columns:
            f_ser = pd.to_numeric(gruppo["Fatto"], errors="coerce")
        else:
            f_ser = pd.Series(dtype=float)
            
        if "Da fare" in gruppo.columns:
            d_ser = pd.to_numeric(gruppo["Da fare"], errors="coerce")
        else:
            d_ser = pd.Series(dtype=float)
            
        if f_ser.notna().any() or d_ser.notna().any():
            fatto = round(f_ser.dropna().sum(), 1)
            da_fare = round(d_ser.dropna().sum(), 1)
            totale_gg = round(fatto + da_fare, 1)
        else:
            fatto = da_fare = totale_gg = float("nan")

        stati = []
        if "Stato sorgente" in gruppo.columns:
            for v in gruppo["Stato sorgente"].tolist():
                if pd.notna(v) and str(v).strip():
                    v_str = str(v).strip()
                    if v_str not in stati:
                        stati.append(v_str)

        stato_sorgente = " | ".join(stati)
        completo_sorgente = any(stato_sorgente_e_completo(v) for v in stati)

        sal_vals = pd.to_numeric(gruppo["SAL sorgente"], errors="coerce").dropna()
        if completo_sorgente:
            sal_sorgente = 100.0
            sal = 100.0
            stato = "Completato"
            stato_sorgente = "COMPLETO"
        else:
            if not sal_vals.empty:
                sal_sorgente = sal_vals.iloc[0] if len(sal_vals) == 1 else sal_vals.mean()
            elif pd.notna(totale_gg) and totale_gg > 0:
                sal_sorgente = (fatto / totale_gg) * 100
            else:
                sal_vis = pd.to_numeric(gruppo["SAL"], errors="coerce").dropna()
                sal_sorgente = sal_vis.mean() if not sal_vis.empty else float("nan")

            sal = min(max(float(sal_sorgente), 0), 100) if pd.notna(sal_sorgente) else float("nan")
            stato = stato_da_sal(sal)

        sal_atteso = float("nan")
        if "SAL atteso" in gruppo.columns:
            sa = pd.to_numeric(gruppo["SAL atteso"], errors="coerce").dropna()
            if not sa.empty:
                sal_atteso = sa.mean()

        scostamento = (sal - sal_atteso) if (pd.notna(sal) and pd.notna(sal_atteso)) else float("nan")

        fogli = []
        if "Foglio origine" in gruppo.columns:
            for v in gruppo["Foglio origine"].tolist():
                if pd.notna(v) and str(v).strip():
                    v_str = str(v).strip()
                    if v_str not in fogli:
                        fogli.append(v_str)

        righe.append({
            "Progetto": progetto,
            "Team": team,
            "Fatto": fatto,
            "Da fare": da_fare,
            "SAL sorgente": sal_sorgente,
            "SAL": sal,
            "Anomalia SAL": False,
            "Stato sorgente": stato_sorgente,
            "Stato": stato,
            "SAL atteso": sal_atteso,
            "Scostamento": scostamento,
            "Foglio origine": " | ".join(fogli),
            "Occorrenze consolidate": len(gruppo)
        })

    return pd.DataFrame(righe).reset_index(drop=True)


def portfolio_sal(df):
    if df.empty:
        return (float("nan"), "N/D")
        
    validi = df["Fatto"].notna() & df["Da fare"].notna()
    if len(df) > 0 and validi.all():
        f = df["Fatto"].sum()
        r = df["Da fare"].sum()
        if (f + r) > 0:
            return ((f / (f + r)) * 100, "ponderato sui giorni")
            
    sal_validi = df["SAL"].dropna()
    if not sal_validi.empty:
        return (sal_validi.mean(), "media dei SAL disponibili")
        
    return (float("nan"), "N/D")


def aggiungi_flag_condiviso(df, portfolio_epal, portfolio_mgio):
    df = df.copy()
    if not portfolio_epal.empty:
        ek = set(portfolio_epal["Progetto"].map(chiave_progetto))
    else:
        ek = set()
        
    if not portfolio_mgio.empty:
        mk = set(portfolio_mgio["Progetto"].map(chiave_progetto))
    else:
        mk = set()
        
    df["Presente in entrambi"] = df["Progetto"].map(chiave_progetto).isin(ek & mk)
    return df


def arricchisci_portafoglio_con_giorni_sal_dettaglio(df, fogli, sheet_names):
    if df is None or df.empty:
        return df
        
    out = df.copy()
    for idx in out.index:
        orig_f = out.at[idx, "Fatto"] if "Fatto" in out.columns else float("nan")
        orig_d = out.at[idx, "Da fare"] if "Da fare" in out.columns else float("nan")

        if pd.notna(orig_f) and pd.notna(orig_d):
            continue

        progetto = out.at[idx, "Progetto"]
        team = out.at[idx, "Team"] if "Team" in out.columns else "N/D"
        sal_gantt = out.at[idx, "SAL"] if "SAL" in out.columns else float("nan")

        fatto, da_fare = calcola_giorni_da_sal_dettaglio(progetto, team, fogli, sheet_names, sal_gantt)
        
        if pd.notna(fatto) and pd.notna(da_fare):
            out.at[idx, "Fatto"] = fatto
            out.at[idx, "Da fare"] = da_fare
            
    return out


# ============================================================
# DETTAGLIO ATTIVITÀ
# ============================================================

def costruisci_attivita(df, nome_foglio):
    if df is None or df.empty:
        return pd.DataFrame()
        
    col_att = trova_colonna_attivita(df)
    col_sal = trova_colonna_sal(df)
    col_fatto, col_da_fare, _ = trova_colonne_giorni_sal(df, nome_foglio)
    
    if col_att is None:
        return pd.DataFrame()

    out = pd.DataFrame({"Attività": df[col_att].astype(str).str.strip()})
    mask = mask_righe_attivita_valide(df, col_att)
    out = out.loc[mask].copy()
    indici = out.index

    if col_sal:
        out["SAL sorgente"] = percentuale_da_excel(df.loc[indici, col_sal])
    else:
        out["SAL sorgente"] = float("nan")
        
    if col_fatto:
        fatto = serie_numerica(df.loc[indici, col_fatto])
    else:
        fatto = pd.Series(float("nan"), index=indici)
        
    if col_da_fare:
        da_fare = serie_numerica(df.loc[indici, col_da_fare])
    else:
        da_fare = pd.Series(float("nan"), index=indici)

    def ricava(f, r, s):
        if pd.notna(f) and f > 0:
            return f
        if pd.isna(s) or pd.isna(r):
            return f
        if s <= 0:
            return 0.0
        if s >= 100:
            return f
        return r * (s / (100.0 - s))

    out["Fatto"] = [ricava(f, r, s) for f, r, s in zip(fatto, da_fare, out["SAL sorgente"])]
    out["Da fare"] = da_fare

    if col_sal is None:
        den = out["Fatto"] + out["Da fare"]
        out["SAL sorgente"] = out["Fatto"].div(den.where(den > 0)) * 100
        
    out["SAL"] = out["SAL sorgente"].clip(lower=0, upper=100)

    out_cons = out.groupby("Attività", as_index=False, sort=False).agg({
        "SAL": "mean", 
        "SAL sorgente": "mean", 
        "Fatto": "sum", 
        "Da fare": "sum"
    })
    
    out_cons["Anomalia SAL"] = (out_cons["SAL sorgente"] < 0) | (out_cons["SAL sorgente"] > 100)
    out_cons["Stato"] = out_cons["SAL"].apply(stato_da_sal)
    
    return out_cons.dropna(subset=["SAL"], how="all").reset_index(drop=True)


# ============================================================
# VISTE GRAFICHE
# ============================================================

def grafico_ranking(df, titolo, ordinamento="SAL crescente"):
    plot_df = ordina_portafoglio(df.dropna(subset=["SAL"]).copy(), ordinamento)
    if plot_df.empty:
        st.info("Nessun SAL disponibile.")
        return

    plot_df["Etichetta SAL"] = plot_df["SAL"].apply(formatta_percentuale)
    plot_df["SAL sorgente display"] = plot_df["SAL sorgente"].apply(lambda x: formatta_percentuale(x, 2))
    ord_progetti = plot_df["Progetto"].tolist()

    fig = px.bar(
        plot_df, 
        x="SAL", 
        y="Progetto", 
        orientation="h", 
        color="Stato",
        color_discrete_map=COLORI_STATO, 
        category_orders={"Stato": ORDINE_STATI, "Progetto": ord_progetti},
        text="Etichetta SAL",
        custom_data=["Team", "SAL sorgente display", "Stato", "Stato sorgente", "Anomalia SAL"],
        labels={"SAL": "Avanzamento", "Progetto": "", "Stato": "Stato"}, 
        title=titolo,
    )
    fig.update_xaxes(
        range=[0, 100], 
        tickvals=list(range(0, 101, 10)), 
        ticktext=[f"{x}%" for x in range(0, 101, 10)], 
        title="SAL"
    )
    fig.update_yaxes(
        title=None, 
        automargin=True, 
        autorange="reversed", 
        categoryorder="array", 
        categoryarray=ord_progetti
    )
    fig.update_layout(
        height=max(430, len(plot_df) * 38), 
        margin=dict(l=20, r=85, t=100, b=40), 
        legend=dict(orientation="h", yanchor="bottom", y=1.12, x=0, xanchor="left")
    )
    fig.update_traces(
        textposition="outside", 
        cliponaxis=False, 
        marker_line_width=0.5,
        hovertemplate="<b>%{y}</b><br>Team: %{customdata[0]}<br>SAL: %{x:.1f}%<br>SAL sorgente: %{customdata[1]}<br>Stato dashboard: %{customdata[2]}<br>Stato GANTT: %{customdata[3]}<br>Anomalia: %{customdata[4]}<extra></extra>",
    )
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


def grafico_distribuzione_stati(df):
    conteggi = df["Stato"].value_counts().reindex(ORDINE_STATI, fill_value=0).rename_axis("Stato").reset_index(name="Progetti")
    fig = px.pie(
        conteggi, 
        names="Stato", 
        values="Progetti", 
        hole=0.58, 
        color="Stato",
        color_discrete_map=COLORI_STATO, 
        category_orders={"Stato": ORDINE_STATI}, 
        title="Distribuzione stato progetti",
    )
    fig.update_traces(
        textposition="inside", 
        textinfo="percent+label", 
        hovertemplate="<b>%{label}</b><br>Progetti: %{value}<br>Quota: %{percent}<extra></extra>"
    )
    fig.update_layout(
        height=390, 
        margin=dict(l=20, r=20, t=60, b=20), 
        legend=dict(orientation="h", y=-0.15, xanchor="center", x=0.5)
    )
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


def grafico_confronto_team(df):
    righe = []
    for team in ["EPAL", "MGIO", "EPAL+MGIO"]:
        team_df = df[(df["Team"] == team) & (df["Stato"] != "Completato")].copy()
        if not team_df.empty:
            sal, metodo = portfolio_sal(team_df)
            num_prog = len(team_df)
        else:
            sal, metodo = 0.0, "Nessun progetto in corso"
            num_prog = 0
            
        righe.append({
            "Team": team, 
            "SAL": sal if pd.notna(sal) else 0.0, 
            "Metodo": metodo, 
            "Progetti in corso": num_prog
        })
        
    confronto = pd.DataFrame(righe)
    confronto["Etichetta"] = confronto["SAL"].apply(formatta_percentuale)
    
    fig = px.bar(
        confronto, 
        x="SAL", 
        y="Team", 
        orientation="h", 
        text="Etichetta", 
        custom_data=["Metodo", "Progetti in corso"],
        title="SAL progetti in corso per team", 
        labels={"SAL": "SAL", "Team": ""},
        category_orders={"Team": ["EPAL", "MGIO", "EPAL+MGIO"]}
    )
    fig.update_xaxes(
        range=[0, 100], 
        tickvals=[0, 20, 40, 60, 80, 100], 
        ticktext=["0%", "20%", "40%", "60%", "80%", "100%"]
    )
    fig.update_layout(
        height=390, 
        margin=dict(l=20, r=70, t=60, b=40), 
        showlegend=False
    )
    fig.update_traces(
        textposition="outside", 
        cliponaxis=False, 
        hovertemplate="<b>%{y}</b><br>SAL: %{x:.1f}%<br>Progetti: %{customdata[1]}<br>Calcolo: %{customdata[0]}<extra></extra>"
    )
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


def grafico_reale_atteso(df):
    validi = df[df["SAL atteso"].notna() & df["SAL"].notna()].copy()
    if validi.empty:
        return False
        
    validi["SAL reale"] = validi["SAL"]
    long_df = validi.melt(
        id_vars=["Progetto", "Team"], 
        value_vars=["SAL reale", "SAL atteso"], 
        var_name="Metrica", 
        value_name="Percentuale"
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
        ticktext=[f"{x}%" for x in range(0, 101, 10)]
    )
    fig.update_layout(
        height=max(430, len(validi) * 50), 
        margin=dict(l=20, r=30, t=60, b=40), 
        legend=dict(orientation="h", y=1.02, x=0)
    )
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    return True


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
        barmode="group", 
        title=f"Avanzamento — {progetto}",
        labels={"SAL": "SAL", "Attività": "", "Stato": "Stato"},
    )
    fig.update_xaxes(
        range=[0, 100], 
        tickvals=list(range(0, 101, 10)), 
        ticktext=[f"{x}%" for x in range(0, 101, 10)]
    )
    fig.update_yaxes(automargin=True)
    fig.update_layout(
        height=max(430, len(plot_df) * 40), 
        margin=dict(l=20, r=85, t=100, b=40), 
        legend=dict(orientation="h", yanchor="bottom", y=1.12, x=0, xanchor="left")
    )
    fig.update_traces(
        textposition="outside", 
        cliponaxis=False, 
        marker_line_width=0.5
    )
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


def grafico_ripartizione_lavoro(pct_fatti, pct_da_fare):
    if pd.isna(pct_fatti) or pd.isna(pct_da_fare):
        return
        
    df_progress = pd.DataFrame({
        "Voce": ["Lavoro complessivo", "Lavoro complessivo"], 
        "Stato": ["Fatto", "Da fare"], 
        "Percentuale": [pct_fatti, pct_da_fare],
        "Etichetta": [f"Fatto {formatta_percentuale(pct_fatti)}", f"Da fare {formatta_percentuale(pct_da_fare)}"],
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
        title="Ripartizione complessiva", 
        labels={"Percentuale": "", "Voce": "", "Stato": ""},
    )
    fig.update_xaxes(
        range=[0, 100], 
        tickvals=[0, 20, 40, 60, 80, 100], 
        ticktext=["0%", "20%", "40%", "60%", "80%", "100%"]
    )
    fig.update_yaxes(showticklabels=False, title=None)
    fig.update_traces(
        textposition="inside", 
        insidetextanchor="middle", 
        hovertemplate="<b>%{fullData.name}</b><br>%{x:.1f}%<extra></extra>"
    )
    fig.update_layout(
        height=260, 
        margin=dict(l=20, r=20, t=80, b=35), 
        legend=dict(orientation="h", yanchor="bottom", y=1.12, x=0, xanchor="left"), 
        uniformtext_minsize=10, 
        uniformtext_mode="hide"
    )
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


def tabella_portafoglio(df):
    df_tab = df.copy()
    
    df_tab["Giorni totali"] = df_tab["Fatto"].fillna(0) + df_tab["Da fare"].fillna(0)
    mask_nan = df_tab["Fatto"].isna() & df_tab["Da fare"].isna()
    df_tab.loc[mask_nan, "Giorni totali"] = float("nan")

    colonne = ["Progetto", "Team", "SAL", "Stato", "Stato sorgente", "Giorni totali", "Fatto", "Da fare"]
    if "SAL atteso" in df_tab.columns and df_tab["SAL atteso"].notna().any():
        colonne += ["SAL atteso", "Scostamento"]
        
    tabella = df_tab[colonne].copy()
    tabella["Giorni totali"] = tabella["Giorni totali"].apply(formatta_numero)
    tabella["Fatto"] = tabella["Fatto"].apply(formatta_numero)
    tabella["Da fare"] = tabella["Da fare"].apply(formatta_numero)

    config = {
        "SAL": st.column_config.ProgressColumn("SAL", min_value=0, max_value=100, format="%.1f%%"),
        "Stato sorgente": st.column_config.TextColumn("Stato GANTT"),
        "Giorni totali": st.column_config.TextColumn("Giorni totali"),
        "Fatto": st.column_config.TextColumn("Giorni fatti"),
        "Da fare": st.column_config.TextColumn("Giorni da fare"),
    }
    if "SAL atteso" in tabella.columns:
        config["SAL atteso"] = st.column_config.ProgressColumn("SAL atteso", min_value=0, max_value=100, format="%.1f%%")
        config["Scostamento"] = st.column_config.NumberColumn("Scostamento (p.p.)", format="%.1f")
        
    st.dataframe(tabella, use_container_width=True, hide_index=True, column_config=config)


def csv_bytes(df):
    return df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
        
    if st.session_state["password_correct"]:
        return True

    st.title("🔒 Accesso riservato")
    st.caption("Dashboard Monitoraggio SAL MiniPIA")
    
    try:
        pwd_attesa = st.secrets["PASSWORD_TEAM"]
    except Exception:
        st.error("PASSWORD_TEAM non configurata.")
        return False

    pwd = st.text_input("Inserisci password", type="password")
    if st.button("Accedi", type="primary", use_container_width=True):
        if pwd == pwd_attesa:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("Password errata.")
    return False


# ============================================================
# CARICAMENTO DATI EXCEL
# ============================================================

@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def carica_workbook(sheet_id):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=60) as response:
        contenuto = response.read()
    fogli = {nome: pulisci_dataframe(df) for nome, df in pd.read_excel(BytesIO(contenuto), sheet_name=None, engine="openpyxl").items()}
    return (fogli, ora_italiana())


# ============================================================
# APP STREAMLIT (LOGICA PRINCIPALE E UI)
# ============================================================

if not check_password():
    st.stop()

header_left, header_right = st.columns([10, 1])
with header_left:
    st.title("📊 Dashboard Monitoraggio SAL MiniPIA")
    st.markdown('<div class="dashboard-subtitle">Portafoglio progetti · EPAL · MGIO</div>', unsafe_allow_html=True)
with header_right:
    if st.button("Esci", use_container_width=True):
        st.session_state["password_correct"] = False
        st.rerun()

# ============================================================
# BANNER PRESENTAZIONE CON RETE NEURALE & CARD INDUSTRIA 4.0/5.0
# ============================================================
st.markdown(
    """<style>
@keyframes glowPulse {
    0%   { opacity: 0.25; stroke-width: 1.0; }
    50%  { opacity: 0.95; stroke-width: 2.0; }
    100% { opacity: 0.25; stroke-width: 1.0; }
}
@keyframes nodePulse {
    0%   { r: 3px; opacity: 0.4; }
    50%  { r: 6px; opacity: 1; }
    100% { r: 3px; opacity: 0.4; }
}
.net-edge { stroke: #167D3E; animation: glowPulse 4s ease-in-out infinite; }
.net-edge-bright { stroke: #00C9A7; animation: glowPulse 3s ease-in-out infinite; }
.net-node-green { fill: #167D3E; animation: nodePulse 3.5s ease-in-out infinite; }
.net-node-cyan { fill: #00C9A7; animation: nodePulse 2.5s ease-in-out infinite; }

.tech-card-container {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
}
.tech-card {
    position: relative;
    width: 105px;
    height: 68px;
    border-radius: 8px;
    overflow: hidden;
    border: 1.5px solid rgba(22, 125, 62, 0.35);
    box-shadow: 0 3px 8px rgba(0,0,0,0.12);
    transition: transform 0.3s ease, border-color 0.3s ease;
}
.tech-card:hover {
    transform: translateY(-2px) scale(1.03);
    border-color: #00C9A7;
}
.tech-card img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}
</style>
<div style="background: linear-gradient(90deg, rgba(22, 125, 62, 0.14) 0%, rgba(10, 40, 70, 0.08) 100%); border-left: 4px solid #167D3E; padding: 16px 20px 10px 20px; border-radius: 12px; margin-top: 8px; margin-bottom: 22px; box-shadow: 0px 4px 12px rgba(0,0,0,0.05); overflow: hidden;">
<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 12px;">
<div style="flex: 1; min-width: 280px;">
<div style="font-size: 0.82rem; font-weight: 800; letter-spacing: 1px; color: #167D3E; margin-bottom: 4px; text-transform: uppercase;">INNOVAZIONE E GOVERNANCE DIGITALE</div>
<div style="font-size: 0.93rem; font-style: italic; font-weight: 500; color: inherit; line-height: 1.35;">"L'obiettivo è trasformare i dati in informazioni, e le informazioni in conoscenza strategica."</div>
<div style="font-size: 0.78rem; font-weight: 600; opacity: 0.75; margin-top: 4px;">— Carly Fiorina</div>
</div>
<div class="tech-card-container">
<div class="tech-card">
<img src="https://fondazioneri.it/wp-content/uploads/2018/05/cosa-%C3%A8-innvazione-FRI-scalia-gallery-fullwidth.jpg" alt="Fondazione Ricerca e Innovazione">
</div>
<div class="tech-card">
<img src="https://png.pngtree.com/thumb_back/fh260/background/20260131/pngtree-artificial-intelligence-concept-with-abstract-neural-network-and-glowing-nodes-background-image_21250755.webp" alt="Artificial Intelligence Neural Network">
</div>
<div class="tech-card">
<img src="https://www.fusenetworks.com/images/easyblog_shared/August_2020/8-24-20/294409557_technology_innovation_400.jpg" alt="Technology Innovation Fuse Networks">
</div>
<div class="tech-card">
<img src="https://thumbs.dreamstime.com/b/professional-data-analytics-modern-business-intelligence-dashboard-tablet-corporate-setting-sophisticated-displayed-sleek-375871052.jpg" alt="Data Analytics Dashboard">
</div>
</div>
</div>
<div style="width: 100%; border-top: 1px dashed rgba(22, 125, 62, 0.25); padding-top: 8px;">
<svg width="100%" height="60" viewBox="0 0 1000 60" preserveAspectRatio="none">
<line x1="10" y1="32" x2="60" y2="12" class="net-edge" style="animation-delay: 0.0s;"/>
<line x1="10" y1="32" x2="55" y2="52" class="net-edge" style="animation-delay: 0.3s;"/>
<line x1="60" y1="12" x2="55" y2="52" class="net-edge-bright" style="animation-delay: 0.6s;"/>
<line x1="60" y1="12" x2="120" y2="32" class="net-edge-bright" style="animation-delay: 0.2s;"/>
<line x1="55" y1="52" x2="120" y2="32" class="net-edge" style="animation-delay: 0.5s;"/>

<line x1="120" y1="32" x2="175" y2="10" class="net-edge" style="animation-delay: 0.8s;"/>
<line x1="120" y1="32" x2="170" y2="54" class="net-edge" style="animation-delay: 1.0s;"/>
<line x1="175" y1="10" x2="170" y2="54" class="net-edge-bright" style="animation-delay: 1.2s;"/>
<line x1="175" y1="10" x2="240" y2="32" class="net-edge-bright" style="animation-delay: 0.4s;"/>
<line x1="170" y1="54" x2="240" y2="32" class="net-edge" style="animation-delay: 0.9s;"/>

<line x1="240" y1="32" x2="295" y2="16" class="net-edge" style="animation-delay: 1.4s;"/>
<line x1="240" y1="32" x2="290" y2="48" class="net-edge" style="animation-delay: 1.1s;"/>
<line x1="295" y1="16" x2="290" y2="48" class="net-edge-bright" style="animation-delay: 1.6s;"/>
<line x1="295" y1="16" x2="360" y2="32" class="net-edge-bright" style="animation-delay: 0.7s;"/>
<line x1="290" y1="48" x2="360" y2="32" class="net-edge" style="animation-delay: 1.3s;"/>

<line x1="360" y1="32" x2="415" y2="8" class="net-edge" style="animation-delay: 1.8s;"/>
<line x1="360" y1="32" x2="410" y2="56" class="net-edge" style="animation-delay: 2.0s;"/>
<line x1="415" y1="8" x2="410" y2="56" class="net-edge-bright" style="animation-delay: 2.2s;"/>
<line x1="415" y1="8" x2="480" y2="32" class="net-edge-bright" style="animation-delay: 1.5s;"/>
<line x1="410" y1="56" x2="480" y2="32" class="net-edge" style="animation-delay: 1.9s;"/>

<line x1="480" y1="32" x2="535" y2="14" class="net-edge" style="animation-delay: 2.4s;"/>
<line x1="480" y1="32" x2="530" y2="50" class="net-edge" style="animation-delay: 2.1s;"/>
<line x1="535" y1="14" x2="530" y2="50" class="net-edge-bright" style="animation-delay: 2.6s;"/>
<line x1="535" y1="14" x2="600" y2="32" class="net-edge-bright" style="animation-delay: 1.7s;"/>
<line x1="530" y1="50" x2="600" y2="32" class="net-edge" style="animation-delay: 2.3s;"/>

<line x1="600" y1="32" x2="655" y2="10" class="net-edge" style="animation-delay: 2.8s;"/>
<line x1="600" y1="32" x2="650" y2="54" class="net-edge" style="animation-delay: 2.5s;"/>
<line x1="655" y1="10" x2="650" y2="54" class="net-edge-bright" style="animation-delay: 3.0s;"/>
<line x1="655" y1="10" x2="720" y2="32" class="net-edge-bright" style="animation-delay: 2.9s;"/>
<line x1="650" y1="54" x2="720" y2="32" class="net-edge" style="animation-delay: 3.2s;"/>

<line x1="720" y1="32" x2="775" y2="16" class="net-edge" style="animation-delay: 0.1s;"/>
<line x1="720" y1="32" x2="770" y2="48" class="net-edge" style="animation-delay: 0.4s;"/>
<line x1="775" y1="16" x2="770" y2="48" class="net-edge-bright" style="animation-delay: 0.7s;"/>
<line x1="775" y1="16" x2="840" y2="32" class="net-edge-bright" style="animation-delay: 0.3s;"/>
<line x1="770" y1="48" x2="840" y2="32" class="net-edge" style="animation-delay: 0.8s;"/>

<line x1="840" y1="32" x2="895" y2="12" class="net-edge" style="animation-delay: 1.2s;"/>
<line x1="840" y1="32" x2="890" y2="52" class="net-edge" style="animation-delay: 1.5s;"/>
<line x1="895" y1="12" x2="890" y2="52" class="net-edge-bright" style="animation-delay: 1.7s;"/>
<line x1="895" y1="14" x2="960" y2="32" class="net-edge-bright" style="animation-delay: 2.0s;"/>
<line x1="890" y1="52" x2="960" y2="32" class="net-edge" style="animation-delay: 2.2s;"/>
<line x1="960" y1="32" x2="990" y2="18" class="net-edge" style="animation-delay: 2.5s;"/>
<line x1="960" y1="32" x2="990" y2="46" class="net-edge" style="animation-delay: 2.7s;"/>

<circle cx="10" cy="32" r="4" class="net-node-green" style="animation-delay: 0.0s;"/>
<circle cx="60" cy="12" r="5" class="net-node-cyan" style="animation-delay: 0.3s;"/>
<circle cx="55" cy="52" r="4" class="net-node-green" style="animation-delay: 0.6s;"/>
<circle cx="120" cy="32" r="6" class="net-node-cyan" style="animation-delay: 0.2s;"/>

<circle cx="175" cy="10" r="4" class="net-node-green" style="animation-delay: 0.8s;"/>
<circle cx="170" cy="54" r="4" class="net-node-green" style="animation-delay: 1.0s;"/>
<circle cx="240" cy="32" r="6" class="net-node-cyan" style="animation-delay: 0.4s;"/>

<circle cx="295" cy="16" r="5" class="net-node-cyan" style="animation-delay: 1.4s;"/>
<circle cx="290" cy="48" r="4" class="net-node-green" style="animation-delay: 1.1s;"/>
<circle cx="360" cy="32" r="6" class="net-node-cyan" style="animation-delay: 0.7s;"/>

<circle cx="415" cy="8" r="4" class="net-node-green" style="animation-delay: 1.8s;"/>
<circle cx="410" cy="56" r="5" class="net-node-cyan" style="animation-delay: 2.0s;"/>
<circle cx="480" cy="32" r="6" class="net-node-cyan" style="animation-delay: 1.5s;"/>

<circle cx="535" cy="14" r="4" class="net-node-green" style="animation-delay: 2.4s;"/>
<circle cx="530" cy="50" r="4" class="net-node-green" style="animation-delay: 2.1s;"/>
<circle cx="600" cy="32" r="6" class="net-node-cyan" style="animation-delay: 1.7s;"/>

<circle cx="655" cy="10" r="5" class="net-node-cyan" style="animation-delay: 2.8s;"/>
<circle cx="650" cy="54" r="4" class="net-node-green" style="animation-delay: 2.5s;"/>
<circle cx="720" cy="32" r="6" class="net-node-cyan" style="animation-delay: 2.9s;"/>

<circle cx="775" cy="16" r="4" class="net-node-green" style="animation-delay: 0.1s;"/>
<circle cx="770" cy="48" r="4" class="net-node-green" style="animation-delay: 0.4s;"/>
<circle cx="840" cy="32" r="6" class="net-node-cyan" style="animation-delay: 0.3s;"/>

<circle cx="895" cy="12" r="5" class="net-node-cyan" style="animation-delay: 1.2s;"/>
<circle cx="890" cy="52" r="4" class="net-node-green" style="animation-delay: 1.5s;"/>
<circle cx="960" cy="32" r="6" class="net-node-cyan" style="animation-delay: 2.0s;"/>
<circle cx="990" cy="18" r="3" class="net-node-green" style="animation-delay: 2.5s;"/>
<circle cx="990" cy="46" r="3" class="net-node-green" style="animation-delay: 2.7s;"/>
</svg>
</div>
</div>""",
    unsafe_allow_html=True
)

try:
    with st.spinner("Caricamento dati in corso..."):
        fogli, timestamp_caricamento = carica_workbook(SHEET_ID)
except Exception as exc:
    st.error("Errore caricamento da Google Sheets.")
    st.exception(exc)
    st.stop()

sheet_names = list(fogli.keys())
gantt_combinato = next((n for n in GANTT_COMBINATI_POSSIBILI if n in sheet_names), None)

st.caption(f"● Dati live: {timestamp_caricamento.strftime('%d/%m/%Y %H:%M')} · Cache {CACHE_TTL_SECONDS//60} min")

st.sidebar.title("Controlli")
if st.sidebar.button("🔄 Aggiorna dati", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
    
vista = st.sidebar.radio(
    "Vista",
    [
        "Executive",
        "Saturazione & Capacità",
        "Avanzamento",
        "Dettaglio progetto",
        "Dati sorgente",
    ],
)


# ============================================================
# COSTRUZIONE PORTAFOGLIO
# ============================================================

if GANTT_EPAL in fogli:
    portfolio_epal = costruisci_portafoglio(fogli[GANTT_EPAL], "EPAL", GANTT_EPAL)
else:
    portfolio_epal = pd.DataFrame()
    
if GANTT_MGIO in fogli:
    portfolio_mgio = costruisci_portafoglio(fogli[GANTT_MGIO], "MGIO", GANTT_MGIO)
else:
    portfolio_mgio = pd.DataFrame()

metriche_minds = calcola_metriche_minds(fogli)
portfolio_epal = arricchisci_portafoglio_minds(portfolio_epal, metriche_minds)
portfolio_mgio = arricchisci_portafoglio_minds(portfolio_mgio, metriche_minds)

portfolio_epal = arricchisci_portafoglio_con_giorni_sal_dettaglio(portfolio_epal, fogli, sheet_names)
portfolio_mgio = arricchisci_portafoglio_con_giorni_sal_dettaglio(portfolio_mgio, fogli, sheet_names)
portfolio_concat = pd.concat([portfolio_epal, portfolio_mgio], ignore_index=True)

# Consolida i dati certi direttamente dai fogli singoli EPAL e MGIO
portfolio_tutti = consolida_progetti_univoci(portfolio_concat)
portfolio_tutti = aggiungi_flag_condiviso(portfolio_tutti, portfolio_epal, portfolio_mgio)
portfolio_tutti = arricchisci_portafoglio_con_giorni_sal_dettaglio(portfolio_tutti, fogli, sheet_names)

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

portfolio_filtrato = portfolio.copy()

if (
    vista not in ["Dati sorgente", "Saturazione & Capacità"]
    and not portfolio.empty
):
    st.sidebar.markdown("---")
    st.sidebar.subheader("Filtri")
    ricerca = st.sidebar.text_input("🔎 Cerca progetto", key=f"ricerca_{scope}")
    filtro_stato = st.sidebar.selectbox(
        "Stato", 
        ["Tutti", "In stato iniziale", "In stato intermedio", "In stato avanzato", "Completato"], 
        key=f"stato_{scope}"
    )
    range_sal = st.sidebar.slider("SAL", 0, 100, (0, 100), 1, key=f"range_{scope}")

    if ricerca.strip():
        portfolio_filtrato = portfolio_filtrato[portfolio_filtrato["Progetto"].astype(str).str.contains(ricerca.strip(), case=False, na=False)]
    if filtro_stato != "Tutti":
        portfolio_filtrato = portfolio_filtrato[portfolio_filtrato["Stato"] == filtro_stato]
        
    portfolio_filtrato = portfolio_filtrato[portfolio_filtrato["SAL"].between(range_sal[0], range_sal[1], inclusive="both")]


# ============================================================
# RENDERING VISTE
# ============================================================

if vista == "Executive":
    if portfolio_filtrato.empty:
        st.info("Nessun progetto.")
        st.stop()
        
    port_in_corso = portfolio_filtrato[portfolio_filtrato["Stato"] != "Completato"].copy()
    
    # Arrotonda i singoli valori a 1 cifra decimale per allinearsi a Google Sheets
    port_in_corso["Fatto"] = port_in_corso["Fatto"].round(1)
    port_in_corso["Da fare"] = port_in_corso["Da fare"].round(1)
    
    num_in_corso = len(port_in_corso)
    sal_in_corso, metodo_sal = portfolio_sal(port_in_corso)

    # CSS con font ingrandito e andata a capo ottimizzata per massima leggibilità
    st.markdown(
        """
        <style>
            [data-testid="stMetricLabel"],
            [data-testid="stMetricLabel"] *,
            [data-testid="stMetricLabel"] div,
            [data-testid="stMetricLabel"] p {
                font-size: 0.80rem !important;
                font-weight: 600 !important;
                white-space: normal !important;
                text-overflow: unset !important;
                overflow: visible !important;
                line-height: 1.25 !important;
            }
        </style>
        """,
        unsafe_allow_html=True
    )

    st.subheader(f"Portfolio · {scope}")
    k1, k2, k3, k4, k5, k6 = st.columns(6)

    n_tot = len(portfolio_filtrato)
    sal_txt = formatta_percentuale(sal_in_corso)
    n_comp = int((portfolio_filtrato["Stato"] == "Completato").sum())
    n_iniz = int((portfolio_filtrato["Stato"] == "In stato iniziale").sum())
    n_inter = int((portfolio_filtrato["Stato"] == "In stato intermedio").sum())
    n_avanz = int((portfolio_filtrato["Stato"] == "In stato avanzato").sum())

    def crea_card(titolo, valore, colore_bordo=None):
        bordo_css = f"2.5px solid {colore_bordo}" if colore_bordo else "1.5px solid rgba(128,128,128,.22)"
        return f"""
        <div style="
            border-radius: 12px;
            padding: 0.75rem 0.5rem;
            background: rgba(128,128,128,.035);
            border: {bordo_css};
            color: inherit;
            height: 82px;
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            box-shadow: 0px 2px 4px rgba(0,0,0,0.03);
        ">
            <div style="
                font-size: 0.80rem; 
                font-weight: 600; 
                opacity: 0.90; 
                line-height: 1.15; 
                letter-spacing: -0.2px; 
                white-space: nowrap; 
                overflow: hidden; 
                text-overflow: clip;
            ">{titolo}</div>
            <div style="font-size: 1.35rem; font-weight: 700; margin-top: auto;">{valore}</div>
        </div>
        """

    with k1:
        st.markdown(crea_card("Progetti totali", n_tot), unsafe_allow_html=True)
    with k2:
        st.markdown(crea_card("SAL in corso", sal_txt), unsafe_allow_html=True)
    with k3:
        st.markdown(crea_card("Completati", n_comp), unsafe_allow_html=True)
    with k4:
        st.markdown(crea_card("In stato iniziale", n_iniz, COLORI_STATO["In stato iniziale"]), unsafe_allow_html=True)
    with k5:
        st.markdown(crea_card("In stato intermedio", n_inter, COLORI_STATO["In stato intermedio"]), unsafe_allow_html=True)
    with k6:
        st.markdown(crea_card("In stato avanzato", n_avanz, COLORI_STATO["In stato avanzato"]), unsafe_allow_html=True)

    # Riquadro sottostante separato correttamente
    _, col_in_corso = st.columns([1, 1])
    with col_in_corso:
        st.markdown(
            f"""
            <div style="
                background: rgba(128,128,128,.035);
                border: 1px solid rgba(128,128,128,.22);
                color: inherit;
                text-align: center;
                padding: 8px 12px;
                border-radius: 12px;
                font-weight: 700;
                font-size: 0.88rem;
                margin-top: 10px;
                margin-bottom: 8px;
                letter-spacing: 0.5px;
            ">
                PROGETTI IN CORSO: {num_in_corso}
            </div>
            """,
            unsafe_allow_html=True
        )

    st.caption(f"Calcolo: {metodo_sal}")

    ord_exec = st.radio("Ordinamento", ["SAL crescente", "SAL decrescente", "Nome progetto"], index=0, horizontal=True)
    port_ord = ordina_portafoglio(portfolio_filtrato, ord_exec)
    grafico_ranking(portfolio_filtrato, "Avanzamento progetti", ord_exec)

    col1, col2 = st.columns(2)
    with col1:
        grafico_distribuzione_stati(portfolio_filtrato)
    with col2:
        if scope == "Tutti - EPAL+MGIO":
            grafico_confronto_team(portfolio_filtrato)
        else:
            # Modifica: calcola i giorni fatti e da fare usando 'port_in_corso'
            f = round(port_in_corso["Fatto"].dropna().sum(), 1)
            r = round(port_in_corso["Da fare"].dropna().sum(), 1)
            if pd.notna(f) or pd.notna(r):
                fig = px.bar(
                    pd.DataFrame({"Voce": ["Fatto", "Da fare"], "Giorni": [f, r]}), 
                    x="Giorni", y="Voce", orientation="h", title="Carico di lavoro", text="Giorni"
                )
                fig.update_traces(texttemplate="%{text:.1f}", textposition="outside")
                fig.update_layout(height=390, showlegend=False)
                st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
            else:
                st.info("Giorni non disponibili.")

    priorita = portfolio_filtrato[portfolio_filtrato["Stato"].isin(["In stato iniziale", "In stato intermedio"])].sort_values(["SAL", "Progetto"]).head(10)
    if not priorita.empty:
        st.markdown("---")
        st.subheader("Priorità operative")
        tabella_portafoglio(priorita)

    if "SAL atteso" in portfolio_filtrato.columns and portfolio_filtrato["SAL atteso"].notna().any():
        st.markdown("---")
        grafico_reale_atteso(port_ord)

    st.markdown("---")
    st.subheader("Portafoglio progetti")
    tabella_portafoglio(port_ord)
    st.download_button("⬇️ Scarica CSV", data=csv_bytes(port_ord), file_name=f"portfolio_{scope}.csv", mime="text/csv")

elif vista == "Effort & Carico di Lavoro":
    df_db_mon, _ = estrai_dati_monitor_mensile(fogli)

    if df_db_mon is None or df_db_mon.empty:
        st.warning(
            "⚠️ Foglio `MINDS_MONITOR_MENSILE` non individuato o privo di dati validi."
        )
        st.stop()

    st.sidebar.markdown("---")
    st.sidebar.subheader("Filtri Analitici")

    # 1. Automazione Selezione Team tramite la variabile globale "scope"
    if scope == "EPAL":
        team_cap_sel = ["EPAL"]
    elif scope == "MGIO":
        team_cap_sel = ["MGIO"]
    else:
        team_cap_sel = ["EPAL", "MGIO"]

    # 2. Slider di Scorrimento Temporale
    periodi_ordinati = (
        df_db_mon.sort_values("SORT_KEY")["PERIODO"].unique().tolist()
    )

    if periodi_ordinati:
        periodo_range = st.sidebar.select_slider(
            "Intervallo Temporale",
            options=periodi_ordinati,
            value=(periodi_ordinati[0], periodi_ordinati[-1]),
            key="periodo_range_sat",
        )
        idx_inizio = periodi_ordinati.index(periodo_range[0])
        idx_fine = periodi_ordinati.index(periodo_range[1])
        periodi_sel = periodi_ordinati[idx_inizio : idx_fine + 1]
    else:
        periodi_sel = []

    # 3. Filtro Progetto
    progetto_cap_sel = st.sidebar.multiselect(
        "Isola singole Commesse",
        options=sorted(df_db_mon["PROGETTO"].unique()),
        default=[],
        key="progetto_cap_sel",
    )

    # Filtraggio Dati Base
    df_db_filt = df_db_mon[
        (df_db_mon["TEAM"].isin(team_cap_sel))
        & (df_db_mon["PERIODO"].isin(periodi_sel))
    ].copy()

    if progetto_cap_sel:
        df_db_filt = df_db_filt[
            df_db_filt["PROGETTO"].isin(progetto_cap_sel)
        ].copy()

    st.subheader("📅 Consuntivo Effort & Carico di Lavoro Mensile")
    st.markdown(
        "Monitoraggio delle giornate lavorate trasversali per team calcolate "
        "direttamente come **somma dei giorni interi lavorati effettivi**."
    )

    # KPI SUMMARY CARDS DINAMICHE
    num_teams = len(team_cap_sel)
    cols = st.columns(3 if num_teams == 2 else [1, 2])

    if num_teams == 2:
        tot_epal = df_db_filt[df_db_filt["TEAM"] == "EPAL"]["GIORNI"].sum()
        tot_mgio = df_db_filt[df_db_filt["TEAM"] == "MGIO"]["GIORNI"].sum()
        tot_complessivo = df_db_filt["GIORNI"].sum()

        cols[0].metric("Giorni Interi EPAL", f"{tot_epal:.1f} gg")
        cols[1].metric("Giorni Interi MGIO", f"{tot_mgio:.1f} gg")
        cols[2].metric("Totale Complessivo", f"{tot_complessivo:.1f} gg")
    else:
        team_singolo = team_cap_sel[0]
        tot_singolo = df_db_filt["GIORNI"].sum()
        cols[0].metric(
            f"Giorni Interi {team_singolo}", f"{tot_singolo:.1f} gg"
        )

    st.markdown("---")

    df_trend_mensile = (
        df_db_filt.groupby(["PERIODO", "SORT_KEY", "TEAM"], as_index=False)["GIORNI"]
        .sum()
        .sort_values("SORT_KEY")
    )

    # GRAFICI 1 & 2: Volumi e Trend
    st.markdown("### 📊 Volumi Mensili e Trend dei Giorni Interi Lavorati")

    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        fig_mon_bar = go.Figure()
        for team_name in team_cap_sel:
            df_t = df_trend_mensile[df_trend_mensile["TEAM"] == team_name]
            fig_mon_bar.add_trace(
                go.Bar(
                    x=df_t["PERIODO"],
                    y=df_t["GIORNI"],
                    name=f"{team_name}",
                    marker_color=COLORI_TEAM.get(team_name, "#2563EB"),
                    text=df_t["GIORNI"].round(1),
                    textposition="auto",
                )
            )
        fig_mon_bar.update_xaxes(type="category")
        fig_mon_bar.update_layout(
            barmode="group",
            height=400,
            hovermode="x unified",
            xaxis_title="Periodo Mensile",
            yaxis_title="Giorni Interi Lavorati",
            title="Volumi (Istogramma)",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
            ),
            template="plotly_white",
        )
        st.plotly_chart(
            fig_mon_bar, use_container_width=True, config=PLOTLY_CONFIG
        )

    with col_chart2:
        fig_mon_line = go.Figure()
        for team_name in team_cap_sel:
            df_t = df_trend_mensile[df_trend_mensile["TEAM"] == team_name]
            fig_mon_line.add_trace(
                go.Scatter(
                    x=df_t["PERIODO"],
                    y=df_t["GIORNI"],
                    name=f"Trend {team_name}",
                    mode="lines+markers",
                    line=dict(
                        width=3,
                        color=COLORI_TEAM.get(team_name, "#2563EB"),
                        shape="spline",
                    ),
                    marker=dict(size=8),
                )
            )
        fig_mon_line.update_xaxes(type="category")
        fig_mon_line.update_layout(
            height=400,
            hovermode="x unified",
            xaxis_title="Periodo Mensile",
            yaxis_title="Giorni Interi Lavorati",
            title="Andamento Storico (Linea di Tendenza)",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
            ),
            template="plotly_white",
        )
        st.plotly_chart(
            fig_mon_line, use_container_width=True, config=PLOTLY_CONFIG
        )

    st.markdown("---")

    # GRAFICI 3 & 4: Allocazione Effort per Commessa
    st.markdown("### 🧩 Allocazione Effort per Commessa")

    if df_db_filt.empty:
        st.info(
            "Nessun dettaglio attività disponibile per i filtri selezionati."
        )
    else:
        col_mon_left, col_mon_right = st.columns([2, 1])

        df_m_p = (
            df_db_filt.groupby(
                ["PERIODO", "SORT_KEY", "PROGETTO"], as_index=False
            )["GIORNI"]
            .sum()
        )

        df_m_p["RANK_MESE"] = df_m_p.groupby("PERIODO")["GIORNI"].rank(
            method="first", ascending=False
        )

        df_m_p["COMMESSA_DISPLAY"] = df_m_p.apply(
            lambda r: r["PROGETTO"]
            if r["RANK_MESE"] <= 3
            else "Altri Progetti",
            axis=1,
        )

        df_top3_mensile = (
            df_m_p.groupby(
                ["PERIODO", "SORT_KEY", "COMMESSA_DISPLAY"], as_index=False
            )["GIORNI"]
            .sum()
            .sort_values("SORT_KEY")
        )

        ordine_mesi = df_top3_mensile["PERIODO"].unique().tolist()

        with col_mon_left:
            fig_mon_proj = px.bar(
                df_top3_mensile,
                x="PERIODO",
                y="GIORNI",
                color="COMMESSA_DISPLAY",
                title=(
                    "Scomposizione Mensile Giorni Interi Lavorati (Top 3 Commesse per Mese)"
                ),
                barmode="stack",
                category_orders={"PERIODO": ordine_mesi},
                template="plotly_white",
                height=480,
            )
            fig_mon_proj.update_xaxes(type="category", title="Periodo Mensile")
            fig_mon_proj.update_yaxes(title="Giorni Interi Lavorati")
            fig_mon_proj.update_layout(
                legend_title_text="Commessa",
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.22,
                    xanchor="center",
                    x=0.5,
                ),
            )
            st.plotly_chart(
                fig_mon_proj, use_container_width=True, config=PLOTLY_CONFIG
            )

        with col_mon_right:
            df_top_p = (
                df_db_filt.groupby("PROGETTO")["GIORNI"]
                .sum()
                .reset_index()
                .sort_values("GIORNI", ascending=False)
            )
            fig_mon_pie = px.pie(
                df_top_p,
                names="PROGETTO",
                values="GIORNI",
                title="Distribuzione & Quota Effort per Commessa",
                hole=0.45,
                template="plotly_white",
                height=480,
            )
            st.plotly_chart(
                fig_mon_pie, use_container_width=True, config=PLOTLY_CONFIG
            )

        st.markdown("---")
        st.markdown("### 📋 Registro Dettagliato Attività per Mese")

        df_tab_dettaglio = (
            df_db_filt[
                [
                    "PERIODO",
                    "PROGETTO",
                    "TEAM",
                    "ATTIVITÀ",
                    "MINUTI",
                    "GIORNI",
                    "SORT_KEY",
                ]
            ]
            .sort_values(["SORT_KEY", "PROGETTO", "TEAM"])
            .drop(columns=["SORT_KEY"])
        )

        st.dataframe(
            df_tab_dettaglio,
            use_container_width=True,
            hide_index=True,
            column_config={
                "PERIODO": st.column_config.TextColumn("Mese / Anno"),
                "PROGETTO": st.column_config.TextColumn("Progetto / Commessa"),
                "TEAM": st.column_config.TextColumn("Team"),
                "ATTIVITÀ": st.column_config.TextColumn("Attività Svolta"),
                "MINUTI": st.column_config.NumberColumn(
                    "Minuti", format="%d min"
                ),
                "GIORNI": st.column_config.NumberColumn(
                    "Giorni Interi Lavorati", format="%.2f gg"
                ),
            },
        )

        st.download_button(
            "⬇️ Scarica Registro Attività (CSV)",
            data=csv_bytes(df_tab_dettaglio),
            file_name=f"registro_attivita_mensili_{scope}.csv",
            mime="text/csv",
        )

        # ==========================================
        # TABELLA DI DETTAGLIO ATTIVITÀ MESE PER MESE
        # ==========================================
        st.markdown("---")
        st.markdown("### 📋 Registro Dettagliato Attività per Mese")

        df_tab_dettaglio = (
            df_db_filt[
                [
                    "PERIODO",
                    "PROGETTO",
                    "TEAM",
                    "ATTIVITÀ",
                    "MINUTI",
                    "GIORNI",
                    "SORT_KEY",
                ]
            ]
            .sort_values(["SORT_KEY", "PROGETTO", "TEAM"])
            .drop(columns=["SORT_KEY"])
        )

        st.dataframe(
            df_tab_dettaglio,
            use_container_width=True,
            hide_index=True,
            column_config={
                "PERIODO": st.column_config.TextColumn("Mese / Anno"),
                "PROGETTO": st.column_config.TextColumn("Progetto / Commessa"),
                "TEAM": st.column_config.TextColumn("Team"),
                "ATTIVITÀ": st.column_config.TextColumn("Attività Svolta"),
                "MINUTI": st.column_config.NumberColumn(
                    "Minuti", format="%d min"
                ),
                "GIORNI": st.column_config.NumberColumn(
                    "Giorni Lavorati", format="%.2f gg"
                ),
            },
        )

        st.download_button(
            "⬇️ Scarica Registro Attività (CSV)",
            data=csv_bytes(df_tab_dettaglio),
            file_name=f"registro_attivita_mensili_{scope}.csv",
            mime="text/csv",
        )

elif vista == "Avanzamento":
    if portfolio_filtrato.empty:
        st.info("Nessun progetto.")
        st.stop()
        
    st.subheader(f"Avanzamento · {scope}")
    ord_avanz = st.radio("Ordinamento", ["SAL crescente", "SAL decrescente", "Nome progetto"], index=0, horizontal=True)
    port_ord = ordina_portafoglio(portfolio_filtrato, ord_avanz)
    grafico_ranking(portfolio_filtrato, "Ranking SAL", ord_avanz)
    st.markdown("---")
    tabella_portafoglio(port_ord)
    
    if "SAL atteso" in portfolio_filtrato.columns and portfolio_filtrato["SAL atteso"].notna().any():
        st.markdown("---")
        grafico_reale_atteso(port_ord)


elif vista == "Dettaglio progetto":
    if portfolio.empty:
        st.info("Nessun progetto.")
        st.stop()
        
    opts = portfolio[["Progetto", "Team"]].drop_duplicates().sort_values(["Progetto", "Team"]).copy()
    opts["Label"] = opts["Progetto"] + " · " + opts["Team"]
    
    scelta = st.selectbox("Seleziona", opts["Label"].tolist())
    riga = opts[opts["Label"] == scelta].iloc[0]
    prog, team = riga["Progetto"], riga["Team"]
    riepilogo = portfolio[(portfolio["Progetto"] == prog) & (portfolio["Team"] == team)].iloc[0]

    foglio_auto, _ = trova_foglio_sal_migliore(prog, team, sheet_names)
    candidati = lista_fogli_sal(sheet_names, team) or lista_fogli_sal(sheet_names, None)

    st.subheader(prog)
    st.caption(f"Team: {team}")
    
    if not candidati:
        st.warning("Foglio SAL non trovato.")
        st.stop()

    foglio_sal = st.selectbox(
        "Foglio SAL", 
        candidati, 
        index=candidati.index(foglio_auto) if foglio_auto in candidati else 0
    )
    df_sal = fogli[foglio_sal]

    if pd.notna(riepilogo.get("Fatto")) and pd.notna(riepilogo.get("Da fare")):
        f_minds = float(riepilogo["Fatto"])
        d_minds = float(riepilogo["Da fare"])
        tot_minds = f_minds + d_minds
        pct_f = (f_minds / tot_minds * 100) if tot_minds > 0 else 0.0
        pct_d = (d_minds / tot_minds * 100) if tot_minds > 0 else 0.0
        riep_gg = {
            "disponibile": True,
            "giorni_fatti": f_minds,
            "giorni_da_fare": d_minds,
            "giorni_totali": tot_minds,
            "pct_fatti": pct_f,
            "pct_da_fare": pct_d,
        }
    else:
        riep_gg = calcola_giorni_progetto(df_sal, foglio_sal)
        if not riep_gg["disponibile"]:
            fb = calcola_giorni_da_gantt(riepilogo["Fatto"], riepilogo["Da fare"])
            if fb:
                riep_gg = fb

    if pd.isna(riepilogo["SAL"]) and riep_gg["disponibile"]:
        sal_vis = riep_gg["pct_fatti"]
    else:
        sal_vis = riepilogo["SAL"]

    p1, p2, p3, p4, p5 = st.columns(5)
    p1.metric("SAL", formatta_percentuale(sal_vis))
    p2.metric("Giorni Totali", formatta_numero(riep_gg["giorni_totali"]))
    p3.metric("Giorni fatti", formatta_numero(riep_gg["giorni_fatti"]))
    p4.metric("Giorni da fare", formatta_numero(riep_gg["giorni_da_fare"]))
    p5.metric("Stato", riepilogo["Stato"])

    if riep_gg["disponibile"]:
        _, _, p3_pct, p4_pct, _ = st.columns(5)
        p3_pct.metric("% Giorni fatti", formatta_percentuale(riep_gg["pct_fatti"]))
        p4_pct.metric("% Giorni da fare", formatta_percentuale(riep_gg["pct_da_fare"]))

    if riep_gg["disponibile"]:
        grafico_ripartizione_lavoro(riep_gg["pct_fatti"], riep_gg["pct_da_fare"])

    att = costruisci_attivita(df_sal, foglio_sal)
    if not att.empty:
        st.markdown("---")
        grafico_attivita(att, prog)
        st.subheader("Dettaglio attività")
        st.dataframe(
            att[["Attività", "SAL", "Stato", "Fatto", "Da fare"]], 
            use_container_width=True, 
            hide_index=True, 
            column_config={"SAL": st.column_config.ProgressColumn(format="%.1f%%")}
        )
        
    with st.expander("Sorgente SAL"):
        st.dataframe(df_sal, use_container_width=True)


elif vista == "Dati sorgente":
    st.subheader("Dati sorgente")
    f_raw = st.selectbox("Foglio", list(fogli.keys()))
    df_raw = fogli[f_raw]
    
    st.caption(f"{len(df_raw)} righe · {len(df_raw.columns)} colonne")
    st.dataframe(df_raw, use_container_width=True)
    st.download_button(
        "⬇️ CSV", 
        csv_bytes(df_raw), 
        f"{re.sub(r'[^A-Za-z0-9_-]+', '_', f_raw)}.csv", 
        "text/csv"
    )
