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
    stato = normalizza_testo(value)
    stati_completati = {"completo", "completato", "completed", "chiuso", "concluso", "terminato", "finito"}
    return stato in stati_completati or stato.startswith("complet")


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
    if "EPAL":
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


def arricchisci_portafoglio_minds(df_portfolio, metriche_minds):
    if df_portfolio.empty or not metriche_minds:
        return df_portfolio

    out = df_portfolio.copy()
    for idx in out.index:
        p_key = chiave_progetto(out.at[idx, "Progetto"])
        t_key = normalizza_team(out.at[idx, "Team"]) if "Team" in out.columns else "N/D"
        
        m = metriche_minds.get((p_key, t_key), metriche_minds.get(p_key, None))
        if m:
            out.at[idx, "Fatto"] = m["giorni_fatti"]
            out.at[idx, "Da fare"] = m["giorni_da_fare"]

            tot = m["giorni_totali"]
            if pd.notna(tot) and tot > 0 and pd.notna(m["giorni_fatti"]):
                sal_calc = (m["giorni_fatti"] / tot) * 100.0
                out.at[idx, "SAL sorgente"] = sal_calc
                out.at[idx, "SAL"] = min(max(sal_calc, 0.0), 100.0)
                out.at[idx, "Stato"] = stato_da_sal(sal_calc)

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
        fatto = serie_numerica(df_valido[col_fatto]).clip(lower=0)
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
        out["Fatto"] = serie_numerica(df.loc[indici, col_fatto]).clip(lower=0)
    else:
        out["Fatto"] = float("nan")
        
    if col_da_fare:
        out["Da fare"] = serie_numerica(df.loc[indici, col_da_fare])
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
    base = costruisci_portafoglio(df, "N/D", source_sheet)
    if base.empty:
        return base

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
            
        validi_gg = f_ser.notna() & d_ser.notna()

        if validi_gg.any():
            fatto = f_ser.loc[validi_gg].clip(lower=0).sum()
            da_fare = d_ser.loc[validi_gg].sum()
            totale_gg = fatto + da_fare
        else:
            fatto = da_fare = totale_gg = float("nan")

        sal_vals = pd.to_numeric(gruppo["SAL sorgente"], errors="coerce").dropna()
        if not sal_vals.empty:
            sal_sorgente = sal_vals.iloc[0] if len(sal_vals) == 1 else sal_vals.mean()
        elif pd.notna(totale_gg) and totale_gg > 0:
            sal_sorgente = (fatto / totale_gg) * 100
        else:
            sal_vis = pd.to_numeric(gruppo["SAL"], errors="coerce").dropna()
            if not sal_vis.empty:
                sal_sorgente = sal_vis.mean()
            else:
                sal_sorgente = float("nan")

        if pd.notna(sal_sorgente):
            sal = min(max(float(sal_sorgente), 0), 100)
        else:
            sal = float("nan")
            
        if "Anomalia SAL" in gruppo.columns:
            anomalie = bool(gruppo["Anomalia SAL"].fillna(False).astype(bool).any())
        else:
            anomalie = False
            
        anomalia_cons = anomalie or (pd.notna(sal_sorgente) and (sal_sorgente < 0 or sal_sorgente > 100))

        stati = []
        if "Stato sorgente" in gruppo.columns:
            for v in gruppo["Stato sorgente"].tolist():
                if pd.isna(v):
                    continue
                v_str = str(v).strip()
                if v_str and v_str not in stati:
                    stati.append(v_str)

        stato_sorgente = " | ".join(stati)
        
        if "Stato sorgente" in gruppo.columns:
            completo_sorgente = any(stato_sorgente_e_completo(v) for v in gruppo["Stato sorgente"].tolist())
        else:
            completo_sorgente = False
            
        if completo_sorgente:
            stato = "Completato"
        else:
            stato = stato_da_sal(sal)

        sal_atteso = float("nan")
        if "SAL atteso" in gruppo.columns:
            sa = pd.to_numeric(gruppo["SAL atteso"], errors="coerce").dropna()
            if not sa.empty:
                sal_atteso = sa.mean()

        if pd.notna(sal) and pd.notna(sal_atteso):
            scostamento = sal - sal_atteso
        else:
            scostamento = float("nan")

        fogli = []
        if "Foglio origine" in gruppo.columns:
            for v in gruppo["Foglio origine"].tolist():
                if pd.isna(v):
                    continue
                v_str = str(v).strip()
                if v_str and v_str not in fogli:
                    fogli.append(v_str)

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
            "Foglio origine": " | ".join(fogli),
            "Occorrenze consolidate": len(gruppo)
        })

    return pd.DataFrame(righe).reset_index(drop=True)


def portfolio_sal(df):
    if df.empty:
        return (float("nan"), "N/D")
        
    validi = df["Fatto"].notna() & df["Da fare"].notna()
    if len(df) > 0 and validi.all():
        f = df["Fatto"].clip(lower=0).sum()
        r = df["Da fare"].clip(lower=0).sum()
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

        # Se il dato e gia stato letto dal foglio MINDS_RIEPILOGO, lo protegge e non lo sovrascrive
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
        fatto = serie_numerica(df.loc[indici, col_fatto]).clip(lower=0)
    else:
        fatto = pd.Series(float("nan"), index=indici)
        
    if col_da_fare:
        da_fare = serie_numerica(df.loc[indici, col_da_fare]).clip(lower=0)
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
        if team_df.empty:
            continue
        sal, metodo = portfolio_sal(team_df)
        righe.append({
            "Team": team, 
            "SAL": sal, 
            "Metodo": metodo, 
            "Progetti in corso": len(team_df)
        })
        
    confronto = pd.DataFrame(righe)
    if confronto.empty:
        st.info("Confronto progetti in corso non disponibile.")
        return
        
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

header_left, header_right = st.columns([6, 1])
with header_left:
    st.title("📊 Dashboard Monitoraggio SAL MiniPIA")
    st.markdown('<div class="dashboard-subtitle">Portafoglio progetti · EPAL · MGIO</div>', unsafe_allow_html=True)
with header_right:
    if st.button("Esci", use_container_width=True):
        st.session_state["password_correct"] = False
        st.rerun()

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
    
vista = st.sidebar.radio("Vista", ["Executive", "Avanzamento", "Dettaglio progetto", "Dati sorgente"])


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

if gantt_combinato:
    portfolio_base = costruisci_portafoglio_combinato(fogli[gantt_combinato], portfolio_epal, portfolio_mgio, gantt_combinato)
else:
    portfolio_base = portfolio_concat.copy()

portfolio_tutti = consolida_progetti_univoci(portfolio_base)
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

if vista != "Dati sorgente" and not portfolio.empty:
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
        
    port_in_corso = portfolio_filtrato[portfolio_filtrato["Stato"] != "Completato"]
    sal_in_corso, metodo_sal = portfolio_sal(port_in_corso)

    st.subheader(f"Portfolio · {scope}")
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Progetti", len(portfolio_filtrato))
    k2.metric("SAL in corso", formatta_percentuale(sal_in_corso))
    k3.metric("Completati", int((portfolio_filtrato["Stato"] == "Completato").sum()))
    k4.metric("Iniziale", int((portfolio_filtrato["Stato"] == "In stato iniziale").sum()))
    k5.metric("Intermedio", int((portfolio_filtrato["Stato"] == "In stato intermedio").sum()))
    k6.metric("Avanzato", int((portfolio_filtrato["Stato"] == "In stato avanzato").sum()))
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
            f = port_in_corso["Fatto"].dropna().sum()
            r = port_in_corso["Da fare"].dropna().sum()
            if f > 0 or r > 0:
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

    # Allinea esattamente i contatori alle metriche ufficiali del portafoglio se gia disponibili
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

    # RIGA 1: Metriche principali
    p1, p2, p3, p4, p5 = st.columns(5)
    p1.metric("SAL", formatta_percentuale(sal_vis))
    p2.metric("Giorni Totali", formatta_numero(riep_gg["giorni_totali"]))
    p3.metric("Giorni fatti", formatta_numero(riep_gg["giorni_fatti"]))
    p4.metric("Giorni da fare", formatta_numero(riep_gg["giorni_da_fare"]))
    p5.metric("Stato", riepilogo["Stato"])

    # RIGA 2: Percentuali aggiuntive allineate sotto Giorni fatti e Giorni da fare
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
