import re
import unicodedata
from datetime import datetime
from io import BytesIO
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st


# ============================================================
# CONFIGURAZIONE
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
GANTT_COMBINATO = "GANTT_SAL_PROGETTI_EPAL+MGIO"

TEMPLATE_SAL = {
    "SAL_ANAL_PRED (EPAL)",
    "SAL_ANAL_PRED (MGIO)",
    "SAL_ANAL_PRED (EPAL+MGIO)",
}

# Cache dei dati Google Sheets.
# 300 secondi = 5 minuti.
CACHE_TTL_SECONDS = 300

# Soglie semaforiche.
SOGLIA_ROSSO = 33.33
SOGLIA_GIALLO = 66.67

COLORI_STATO = {
    "Critico": "#D62728",
    "In avanzamento": "#F2C94C",
    "Avanzato": "#2CA02C",
    "Completato": "#167D3E",
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
    """
    Restituisce data e ora italiane.
    """

    try:
        return datetime.now(
            ZoneInfo("Europe/Rome")
        )

    except Exception:
        return datetime.now()


def normalizza_testo(value):
    """
    Normalizza un testo per confronti,
    riconoscimento colonne e matching.
    """

    if value is None:
        return ""

    text = unicodedata.normalize(
        "NFKD",
        str(value)
    )

    text = "".join(
        c
        for c in text
        if not unicodedata.combining(c)
    )

    text = re.sub(
        r"\s+",
        " ",
        text.lower().strip()
    )

    return text


def tokenizza(value):
    """
    Tokenizzazione utilizzata per associare
    automaticamente un progetto al relativo foglio SAL.
    """

    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        normalizza_testo(value)
    )

    stopwords = {
        "sal",
        "epal",
        "mgio",
        "progetto",
        "progetti",
        "gantt",
        "anal",
        "pred",
        "minipia",
        "srl",
        "spa",
        "soc",
        "coop",
    }

    return {
        token
        for token in text.split()
        if len(token) > 1
        and token not in stopwords
    }


def pulisci_dataframe(df):
    """
    Pulizia minima del DataFrame.
    """

    df = df.copy()

    df.columns = [
        str(c).strip()
        for c in df.columns
    ]

    colonne_da_eliminare = [
        col
        for col in df.columns
        if str(col)
        .lower()
        .startswith("unnamed")
        and df[col].isna().all()
    ]

    if colonne_da_eliminare:
        df = df.drop(
            columns=colonne_da_eliminare
        )

    df = df.dropna(
        how="all"
    )

    return df


# ============================================================
# CONVERSIONE NUMERICA
# ============================================================

def serie_numerica(serie):
    """
    Converte una serie in numerica,
    gestendo punto, virgola e percentuale.
    """

    def converti(value):

        if pd.isna(value):
            return float("nan")

        if isinstance(
            value,
            (int, float)
        ):
            return float(value)

        text = (
            str(value)
            .strip()
            .replace("\u00A0", "")
            .replace(" ", "")
        )

        if not text:
            return float("nan")

        # Caso 1.234,56
        if "," in text and "." in text:

            if text.rfind(",") > text.rfind("."):

                text = (
                    text
                    .replace(".", "")
                    .replace(",", ".")
                )

            else:

                text = text.replace(
                    ",",
                    ""
                )

        else:

            text = text.replace(
                ",",
                "."
            )

        text = text.replace(
            "%",
            ""
        )

        try:
            return float(text)

        except ValueError:
            return float("nan")

    return serie.apply(
        converti
    ).astype(
        "float64"
    )


# ============================================================
# CONVERSIONE PERCENTUALI EXCEL
# ============================================================

def percentuale_da_excel(serie):
    """
    Converte i valori percentuali in punti percentuali.

    Esempi:

    0.5993  -> 59.93
    0.8055  -> 80.55
    0.9850  -> 98.50
    1.0000  -> 100.00
    1.1332  -> 113.32

    Se invece il dato è già:

    59.93   -> 59.93
    "59,93%" -> 59.93

    L'inferenza viene effettuata sull'intera serie.
    Questo evita il problema per cui un singolo
    valore superiore a 1 blocca la conversione
    della colonna Excel.
    """

    valori = []
    valori_gia_percentuali = []

    for value in serie:

        if pd.isna(value):

            valori.append(
                float("nan")
            )

            valori_gia_percentuali.append(
                False
            )

            continue

        if (
            isinstance(value, str)
            and "%" in value
        ):

            valore = serie_numerica(
                pd.Series([value])
            ).iloc[0]

            valori.append(
                valore
            )

            valori_gia_percentuali.append(
                True
            )

        else:

            valore = serie_numerica(
                pd.Series([value])
            ).iloc[0]

            valori.append(
                valore
            )

            valori_gia_percentuali.append(
                False
            )

    risultato = pd.Series(
        valori,
        index=serie.index,
        dtype="float64",
    )

    mask_gia_percentuale = pd.Series(
        valori_gia_percentuali,
        index=serie.index,
        dtype="bool",
    )

    valori_da_inferire = risultato[
        (~mask_gia_percentuale)
        & risultato.notna()
    ]

    if not valori_da_inferire.empty:

        quota_frazioni = (
            valori_da_inferire.abs()
            <= 1.5
        ).mean()

        mediana = (
            valori_da_inferire
            .abs()
            .median()
        )

        # Se la maggior parte della serie
        # è nel formato Excel 0-1,
        # moltiplica per 100.
        if (
            quota_frazioni >= 0.60
            or mediana <= 1.5
        ):

            risultato.loc[
                (~mask_gia_percentuale)
                & risultato.notna()
            ] *= 100

    return risultato


# ============================================================
# FORMATTAZIONE
# ============================================================

def formatta_percentuale(
    value,
    decimali=1
):

    if pd.isna(value):
        return "—"

    return (
        f"{float(value):.{decimali}f}%"
        .replace(".", ",")
    )


def formatta_numero(
    value,
    decimali=1
):

    if pd.isna(value):
        return "—"

    return (
        f"{float(value):.{decimali}f}"
        .replace(".", ",")
    )


# ============================================================
# STATO SAL
# ============================================================

def stato_da_sal(value):
    """
    Classificazione semaforica.

    0 - 33,33%       = Critico
    33,33 - 66,67%   = In avanzamento
    66,67 - <100%    = Avanzato
    100%             = Completato
    """

    if pd.isna(value):
        return "N/D"

    valore = min(
        max(
            float(value),
            0
        ),
        100
    )

    if valore >= 100:
        return "Completato"

    if valore <= SOGLIA_ROSSO:
        return "Critico"

    if valore <= SOGLIA_GIALLO:
        return "In avanzamento"

    return "Avanzato"


# ============================================================
# RICONOSCIMENTO AUTOMATICO DELLE COLONNE
# ============================================================

def trova_colonna(
    df,
    exact=None,
    contains_all=None,
    contains_any=None,
    exclude=None,
):

    exact = exact or []
    contains_all = contains_all or []
    contains_any = contains_any or []
    exclude = exclude or []

    nomi_normalizzati = {
        col: normalizza_testo(col)
        for col in df.columns
    }

    # 1. Match esatto
    for candidato in exact:

        candidato_norm = normalizza_testo(
            candidato
        )

        for col, nome in nomi_normalizzati.items():

            if nome == candidato_norm:
                return col

    # 2. Deve contenere tutti i termini
    if contains_all:

        for col, nome in nomi_normalizzati.items():

            if any(
                normalizza_testo(x) in nome
                for x in exclude
            ):
                continue

            if all(
                normalizza_testo(x) in nome
                for x in contains_all
            ):
                return col

    # 3. Deve contenere almeno un termine
    if contains_any:

        for col, nome in nomi_normalizzati.items():

            if any(
                normalizza_testo(x) in nome
                for x in exclude
            ):
                continue

            if any(
                normalizza_testo(x) in nome
                for x in contains_any
            ):
                return col

    return None


def trova_colonna_progetto(df):

    col = trova_colonna(
        df,
        exact=[
            "PROGETTO",
            "NOME PROGETTO",
            "CLIENTE",
            "COMMESSA",
            "ATTIVITÀ / PROGETTO",
            "ATTIVITA / PROGETTO",
        ],
        contains_any=[
            "progetto",
            "cliente",
            "commessa",
        ],
    )

    if col is not None:
        return col

    # Fallback:
    # prima colonna prevalentemente testuale.
    for col in df.columns:

        serie = df[col].dropna()

        if serie.empty:
            continue

        quota_testo = serie.apply(
            lambda x: isinstance(
                x,
                str
            )
        ).mean()

        if quota_testo >= 0.50:
            return col

    if len(df.columns):
        return df.columns[0]

    return None


def trova_colonna_attivita(df):

    col = trova_colonna(
        df,
        exact=[
            "ATTIVITÀ",
            "ATTIVITA",
            "DESCRIZIONE",
            "FASE",
            "TASK",
        ],
        contains_any=[
            "attivita",
            "descrizione",
            "fase",
            "task",
        ],
        exclude=[
            "percentuale",
            "completamento",
        ],
    )

    if col is not None:
        return col

    return trova_colonna_progetto(
        df
    )


def trova_colonna_sal(df):

    col = trova_colonna(
        df,
        exact=[
            "% COMPLETAMENTO",
            "PERCENTUALE COMPLETAMENTO",
            "COMPLETAMENTO",
            "SAL",
            "% SAL",
        ],
    )

    if col is not None:
        return col

    col = trova_colonna(
        df,
        contains_any=[
            "completamento",
            "percentuale",
            "% sal",
        ],
        exclude=[
            "atteso",
            "previsto",
            "target",
            "pianificato",
            "rosso",
            "giallo",
            "verde",
        ],
    )

    if col is not None:
        return col

    return trova_colonna(
        df,
        contains_any=[
            "sal"
        ],
        exclude=[
            "atteso",
            "previsto",
            "target",
            "pianificato",
        ],
    )


def trova_colonna_fatto(df):

    col = trova_colonna(
        df,
        exact=[
            "FATTO (GIORNI)",
            "FATTO",
            "GIORNI FATTI",
            "GIORNI EFFETTUATI",
        ],
    )

    if col is not None:
        return col

    return trova_colonna(
        df,
        contains_all=[
            "fatto",
            "giorn",
        ],
    )


def trova_colonna_da_fare(df):

    col = trova_colonna(
        df,
        exact=[
            "DA FARE (GIORNI)",
            "DA FARE",
            "GIORNI DA FARE",
            "GIORNI RESIDUI",
        ],
    )

    if col is not None:
        return col

    return trova_colonna(
        df,
        contains_any=[
            "da fare",
            "residui",
            "residuo",
        ],
    )


def trova_colonna_sal_atteso(df):
    """
    Il confronto SAL reale / atteso
    viene attivato soltanto se nel sorgente
    esiste realmente una colonna compatibile.
    """

    return trova_colonna(
        df,
        contains_any=[
            "sal atteso",
            "sal previsto",
            "completamento atteso",
            "completamento previsto",
            "target",
            "pianificato",
        ],
    )


def trova_colonna_team(df):

    return trova_colonna(
        df,
        exact=[
            "TEAM",
            "RESPONSABILE",
            "CONSULENTE",
            "OWNER",
        ],
        contains_any=[
            "team",
            "responsabile",
            "consulente",
            "owner",
        ],
    )


# ============================================================
# PASSWORD
# ============================================================

def check_password():

    if (
        "password_correct"
        not in st.session_state
    ):

        st.session_state[
            "password_correct"
        ] = False

    if st.session_state[
        "password_correct"
    ]:

        return True

    st.title(
        "🔒 Accesso riservato"
    )

    st.caption(
        "Dashboard Monitoraggio SAL MiniPIA"
    )

    try:

        password_attesa = st.secrets[
            "PASSWORD_TEAM"
        ]

    except Exception:

        st.error(
            "Il secret PASSWORD_TEAM "
            "non è configurato in Streamlit."
        )

        return False

    password = st.text_input(
        "Inserisci la password del team",
        type="password",
    )

    if st.button(
        "Accedi",
        type="primary",
        use_container_width=True,
    ):

        if password == password_attesa:

            st.session_state[
                "password_correct"
            ] = True

            st.rerun()

        else:

            st.error(
                "Password errata."
            )

    return False


# ============================================================
# CARICAMENTO GOOGLE SHEETS
# ============================================================

@st.cache_data(
    ttl=CACHE_TTL_SECONDS,
    show_spinner=False,
)
def carica_workbook(sheet_id):
    """
    Scarica il Google Sheet una sola volta
    e legge tutti i fogli del workbook.

    Questo evita di richiamare Google ad ogni
    selezione o interazione.
    """

    url = (
        "https://docs.google.com/spreadsheets/d/"
        f"{sheet_id}/export?format=xlsx"
    )

    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
    )

    with urlopen(
        request,
        timeout=60
    ) as response:

        contenuto = response.read()

    fogli = pd.read_excel(
        BytesIO(contenuto),
        sheet_name=None,
        engine="openpyxl",
    )

    fogli = {
        nome: pulisci_dataframe(df)
        for nome, df in fogli.items()
    }

    return (
        fogli,
        ora_italiana()
    )


# ============================================================
# COSTRUZIONE PORTAFOGLIO
# ============================================================

def costruisci_portafoglio(
    df,
    team,
    source_sheet
):

    if (
        df is None
        or df.empty
    ):
        return pd.DataFrame()

    col_progetto = trova_colonna_progetto(
        df
    )

    col_sal = trova_colonna_sal(
        df
    )

    col_fatto = trova_colonna_fatto(
        df
    )

    col_da_fare = trova_colonna_da_fare(
        df
    )

    col_sal_atteso = trova_colonna_sal_atteso(
        df
    )

    if col_progetto is None:
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "Progetto": df[
                col_progetto
            ]
        }
    )

    out["Progetto"] = (
        out["Progetto"]
        .astype(str)
        .str.strip()
    )

    mask = (
        out["Progetto"].ne("")
        & out["Progetto"]
        .str.lower()
        .ne("nan")
        & ~out["Progetto"]
        .str.lower()
        .isin(
            [
                "totale",
                "totali",
                "total",
            ]
        )
    )

    out = out.loc[
        mask
    ].copy()

    indici = out.index

    out["Team"] = team

    # --------------------------------------------------------
    # GIORNI FATTI
    # --------------------------------------------------------

    if col_fatto is not None:

        out["Fatto"] = (
            serie_numerica(
                df.loc[
                    indici,
                    col_fatto
                ]
            )
            .clip(
                lower=0
            )
        )

    else:

        out["Fatto"] = float(
            "nan"
        )

    # --------------------------------------------------------
    # GIORNI DA FARE
    # --------------------------------------------------------

    if col_da_fare is not None:

        out["Da fare"] = (
            serie_numerica(
                df.loc[
                    indici,
                    col_da_fare
                ]
            )
            .clip(
                lower=0
            )
        )

    else:

        out["Da fare"] = float(
            "nan"
        )

    # --------------------------------------------------------
    # SAL
    # --------------------------------------------------------

    if col_sal is not None:

        out["SAL sorgente"] = (
            percentuale_da_excel(
                df.loc[
                    indici,
                    col_sal
                ]
            )
        )

    else:

        denominatore = (
            out["Fatto"]
            + out["Da fare"]
        )

        out["SAL sorgente"] = (
            out["Fatto"]
            .div(
                denominatore.where(
                    denominatore > 0
                )
            )
            * 100
        )

    # Il valore originale viene mantenuto.
    # Il valore usato per le barre viene limitato 0-100.
    out["SAL"] = (
        out["SAL sorgente"]
        .clip(
            lower=0,
            upper=100
        )
    )

    # --------------------------------------------------------
    # ANOMALIE
    # --------------------------------------------------------

    out["Anomalia SAL"] = (
        (
            out["SAL sorgente"]
            < 0
        )
        |
        (
            out["SAL sorgente"]
            > 100
        )
    )

    # --------------------------------------------------------
    # STATO
    # --------------------------------------------------------

    out["Stato"] = (
        out["SAL"]
        .apply(
            stato_da_sal
        )
    )

    # --------------------------------------------------------
    # SAL ATTESO
    # --------------------------------------------------------

    if col_sal_atteso is not None:

        out["SAL atteso"] = (
            percentuale_da_excel(
                df.loc[
                    indici,
                    col_sal_atteso
                ]
            )
            .clip(
                lower=0,
                upper=100
            )
        )

        out["Scostamento"] = (
            out["SAL"]
            - out["SAL atteso"]
        )

    else:

        out["SAL atteso"] = float(
            "nan"
        )

        out["Scostamento"] = float(
            "nan"
        )

    out["Foglio origine"] = (
        source_sheet
    )

    return (
        out
        .reset_index(
            drop=True
        )
    )


# ============================================================
# SAL COMPLESSIVO DEL PORTAFOGLIO
# ============================================================

def portfolio_sal(df):
    """
    Preferisce il calcolo ponderato
    sui giorni fatti / giorni da fare.

    Se i giorni non sono disponibili,
    utilizza la media dei SAL.
    """

    if df.empty:

        return (
            float("nan"),
            "N/D"
        )

    validi_giorni = (
        df["Fatto"].notna()
        &
        df["Da fare"].notna()
    )

    if validi_giorni.any():

        fatto = (
            df.loc[
                validi_giorni,
                "Fatto"
            ]
            .sum()
        )

        residuo = (
            df.loc[
                validi_giorni,
                "Da fare"
            ]
            .sum()
        )

        totale = (
            fatto
            + residuo
        )

        if totale > 0:

            return (
                (
                    fatto
                    / totale
                )
                * 100,
                "ponderato sui giorni"
            )

    if df["SAL"].notna().any():

        return (
            df["SAL"].mean(),
            "media dei SAL disponibili"
        )

    return (
        float("nan"),
        "N/D"
    )


# ============================================================
# PROGETTI PRESENTI IN ENTRAMBI I PORTAFOGLI
# ============================================================

def aggiungi_flag_doppio_portafoglio(
    df
):

    df = df.copy()

    if df.empty:

        df[
            "Presente in entrambi"
        ] = False

        return df

    chiave = (
        df["Progetto"]
        .map(
            normalizza_testo
        )
    )

    team_count = (
        pd.DataFrame(
            {
                "chiave": chiave,
                "Team": df["Team"],
            }
        )
        .groupby(
            "chiave"
        )["Team"]
        .nunique()
    )

    condivisi = set(
        team_count[
            team_count > 1
        ].index
    )

    df[
        "Presente in entrambi"
    ] = chiave.isin(
        condivisi
    )

    return df


# ============================================================
# FOGLI SAL
# ============================================================

def lista_fogli_sal(
    sheet_names,
    team=None
):

    risultati = []

    for nome in sheet_names:

        # Esclude i template.
        if nome in TEMPLATE_SAL:
            continue

        nome_norm = normalizza_testo(
            nome
        )

        if not nome_norm.startswith(
            "sal_"
        ):
            continue

        if (
            team == "EPAL"
            and "(epal)"
            not in nome_norm
        ):
            continue

        if (
            team == "MGIO"
            and "(mgio)"
            not in nome_norm
        ):
            continue

        risultati.append(
            nome
        )

    return risultati


# ============================================================
# MATCH AUTOMATICO PROGETTO -> FOGLIO SAL
# ============================================================

def score_match_foglio(
    progetto,
    foglio
):

    progetto_norm = normalizza_testo(
        progetto
    )

    foglio_norm = normalizza_testo(
        foglio
    )

    if (
        not progetto_norm
        or not foglio_norm
    ):
        return 0.0

    score = 0.0

    if progetto_norm in foglio_norm:
        score += 0.70

    progetto_tokens = tokenizza(
        progetto
    )

    foglio_tokens = tokenizza(
        foglio
    )

    if progetto_tokens:

        overlap = (
            len(
                progetto_tokens
                & foglio_tokens
            )
            / len(
                progetto_tokens
            )
        )

        score += (
            0.30
            * overlap
        )

    return min(
        score,
        1.0
    )


def trova_foglio_sal_migliore(
    progetto,
    team,
    sheet_names
):

    candidati = lista_fogli_sal(
        sheet_names,
        team
    )

    if not candidati:

        return (
            None,
            0.0
        )

    scores = [
        (
            nome,
            score_match_foglio(
                progetto,
                nome
            )
        )
        for nome in candidati
    ]

    scores.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return scores[0]


# ============================================================
# COSTRUZIONE DETTAGLIO ATTIVITÀ
# ============================================================

def costruisci_attivita(df):

    if (
        df is None
        or df.empty
    ):
        return pd.DataFrame()

    col_attivita = trova_colonna_attivita(
        df
    )

    col_sal = trova_colonna_sal(
        df
    )

    col_fatto = trova_colonna_fatto(
        df
    )

    col_da_fare = trova_colonna_da_fare(
        df
    )

    if col_attivita is None:

        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "Attività": df[
                col_attivita
            ]
        }
    )

    out["Attività"] = (
        out["Attività"]
        .astype(str)
        .str.strip()
    )

    mask = (
        out["Attività"].ne("")
        &
        out["Attività"]
        .str.lower()
        .ne("nan")
        &
        ~out["Attività"]
        .str.lower()
        .isin(
            [
                "totale",
                "totali",
                "total",
            ]
        )
    )

    out = out.loc[
        mask
    ].copy()

    indici = out.index

    if col_fatto is not None:

        out["Fatto"] = (
            serie_numerica(
                df.loc[
                    indici,
                    col_fatto
                ]
            )
        )

    else:

        out["Fatto"] = float(
            "nan"
        )

    if col_da_fare is not None:

        out["Da fare"] = (
            serie_numerica(
                df.loc[
                    indici,
                    col_da_fare
                ]
            )
        )

    else:

        out["Da fare"] = float(
            "nan"
        )

    if col_sal is not None:

        out["SAL sorgente"] = (
            percentuale_da_excel(
                df.loc[
                    indici,
                    col_sal
                ]
            )
        )

    else:

        denominatore = (
            out["Fatto"]
            + out["Da fare"]
        )

        out["SAL sorgente"] = (
            out["Fatto"]
            .div(
                denominatore.where(
                    denominatore
                    > 0
                )
            )
            * 100
        )

    out["SAL"] = (
        out["SAL sorgente"]
        .clip(
            lower=0,
            upper=100
        )
    )

    out["Anomalia SAL"] = (
        (
            out["SAL sorgente"]
            < 0
        )
        |
        (
            out["SAL sorgente"]
            > 100
        )
    )

    out["Stato"] = (
        out["SAL"]
        .apply(
            stato_da_sal
        )
    )

    out = (
        out
        .dropna(
            subset=[
                "SAL"
            ],
            how="all"
        )
        .reset_index(
            drop=True
        )
    )

    return out


# ============================================================
# GRAFICO RANKING PROGETTI
# ============================================================

def grafico_ranking(
    df,
    titolo
):

    plot_df = (
        df
        .dropna(
            subset=[
                "SAL"
            ]
        )
        .sort_values(
            "SAL",
            ascending=True
        )
        .copy()
    )

    if plot_df.empty:

        st.info(
            "Nessun SAL disponibile "
            "per il grafico."
        )

        return

    plot_df[
        "Etichetta SAL"
    ] = (
        plot_df["SAL"]
        .apply(
            formatta_percentuale
        )
    )

    plot_df[
        "SAL sorgente display"
    ] = (
        plot_df[
            "SAL sorgente"
        ]
        .apply(
            lambda x:
            formatta_percentuale(
                x,
                2
            )
        )
    )

    fig = px.bar(
        plot_df,
        x="SAL",
        y="Progetto",
        orientation="h",
        color="Stato",
        color_discrete_map=COLORI_STATO,
        text="Etichetta SAL",
        custom_data=[
            "Team",
            "SAL sorgente display",
            "Stato",
            "Anomalia SAL",
        ],
        labels={
            "SAL": "Avanzamento",
            "Progetto": "",
            "Stato": "Stato",
        },
        title=titolo,
    )

    fig.update_xaxes(
        range=[
            0,
            100
        ],
        tickmode="array",
        tickvals=list(
            range(
                0,
                101,
                10
            )
        ),
        ticktext=[
            f"{x}%"
            for x in range(
                0,
                101,
                10
            )
        ],
        title="SAL",
    )

    fig.update_yaxes(
        title=None,
        automargin=True,
    )

    fig.update_layout(
        height=max(
            430,
            len(plot_df)
            * 38
        ),
        margin=dict(
            l=20,
            r=85,
            t=60,
            b=40,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
        ),
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
            "Stato: %{customdata[2]}<br>"
            "Anomalia: %{customdata[3]}"
            "<extra></extra>"
        ),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config=PLOTLY_CONFIG,
    )


# ============================================================
# DISTRIBUZIONE STATO
# ============================================================

def grafico_distribuzione_stati(df):

    ordine = [
        "Critico",
        "In avanzamento",
        "Avanzato",
        "Completato",
    ]

    conteggi = (
        df["Stato"]
        .value_counts()
        .reindex(
            ordine,
            fill_value=0
        )
        .rename_axis(
            "Stato"
        )
        .reset_index(
            name="Progetti"
        )
    )

    fig = px.pie(
        conteggi,
        names="Stato",
        values="Progetti",
        hole=0.58,
        color="Stato",
        color_discrete_map=COLORI_STATO,
        title=(
            "Distribuzione dello stato "
            "dei progetti"
        ),
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
        margin=dict(
            l=20,
            r=20,
            t=60,
            b=20,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.15,
            xanchor="center",
            x=0.5,
        ),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config=PLOTLY_CONFIG,
    )


# ============================================================
# CONFRONTO EPAL / MGIO
# ============================================================

def grafico_confronto_team(df):

    righe = []

    for team in [
        "EPAL",
        "MGIO"
    ]:

        team_df = df[
            df["Team"]
            == team
        ]

        if team_df.empty:
            continue

        sal, metodo = portfolio_sal(
            team_df
        )

        righe.append(
            {
                "Team": team,
                "SAL": sal,
                "Metodo": metodo,
                "Progetti": len(
                    team_df
                ),
            }
        )

    confronto = pd.DataFrame(
        righe
    )

    if confronto.empty:

        st.info(
            "Il confronto EPAL/MGIO "
            "non è disponibile."
        )

        return

    confronto[
        "Etichetta"
    ] = (
        confronto["SAL"]
        .apply(
            formatta_percentuale
        )
    )

    fig = px.bar(
        confronto,
        x="SAL",
        y="Team",
        orientation="h",
        text="Etichetta",
        custom_data=[
            "Metodo",
            "Progetti",
        ],
        title=(
            "Confronto portafogli "
            "EPAL e MGIO"
        ),
        labels={
            "SAL": "SAL portafoglio",
            "Team": "",
        },
    )

    fig.update_xaxes(
        range=[
            0,
            100
        ],
        tickvals=[
            0,
            20,
            40,
            60,
            80,
            100,
        ],
        ticktext=[
            "0%",
            "20%",
            "40%",
            "60%",
            "80%",
            "100%",
        ],
    )

    fig.update_layout(
        height=390,
        margin=dict(
            l=20,
            r=70,
            t=60,
            b=40,
        ),
        showlegend=False,
    )

    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "SAL: %{x:.1f}%<br>"
            "Progetti: %{customdata[1]}<br>"
            "Calcolo: %{customdata[0]}"
            "<extra></extra>"
        ),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config=PLOTLY_CONFIG,
    )


# ============================================================
# SAL REALE VS ATTESO
# ============================================================

def grafico_reale_atteso(df):
    """
    Viene mostrato solo quando la fonte
    contiene realmente il SAL atteso.
    """

    validi = df[
        df["SAL atteso"].notna()
        &
        df["SAL"].notna()
    ].copy()

    if validi.empty:
        return False

    validi[
        "SAL reale"
    ] = validi[
        "SAL"
    ]

    long_df = validi.melt(
        id_vars=[
            "Progetto",
            "Team",
        ],
        value_vars=[
            "SAL reale",
            "SAL atteso",
        ],
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
        title=(
            "SAL reale vs SAL atteso"
        ),
        labels={
            "Percentuale": "SAL",
            "Progetto": "",
        },
        custom_data=[
            "Team"
        ],
    )

    fig.update_xaxes(
        range=[
            0,
            100
        ],
        tickvals=list(
            range(
                0,
                101,
                10
            )
        ),
        ticktext=[
            f"{x}%"
            for x in range(
                0,
                101,
                10
            )
        ],
    )

    fig.update_layout(
        height=max(
            430,
            len(validi)
            * 50
        ),
        margin=dict(
            l=20,
            r=30,
            t=60,
            b=40,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
        ),
    )

    fig.update_traces(
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Team: %{customdata[0]}<br>"
            "%{fullData.name}: %{x:.1f}%"
            "<extra></extra>"
        )
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config=PLOTLY_CONFIG,
    )

    return True


# ============================================================
# GRAFICO ATTIVITÀ
# ============================================================

def grafico_attivita(
    df_attivita,
    progetto
):

    plot_df = (
        df_attivita
        .sort_values(
            "SAL",
            ascending=True
        )
        .copy()
    )

    plot_df[
        "Etichetta"
    ] = (
        plot_df["SAL"]
        .apply(
            formatta_percentuale
        )
    )

    fig = px.bar(
        plot_df,
        x="SAL",
        y="Attività",
        orientation="h",
        color="Stato",
        color_discrete_map=COLORI_STATO,
        text="Etichetta",
        title=(
            f"Avanzamento attività — "
            f"{progetto}"
        ),
        labels={
            "SAL": "SAL",
            "Attività": "",
            "Stato": "Stato",
        },
    )

    fig.update_xaxes(
        range=[
            0,
            100
        ],
        tickvals=list(
            range(
                0,
                101,
                10
            )
        ),
        ticktext=[
            f"{x}%"
            for x in range(
                0,
                101,
                10
            )
        ],
    )

    fig.update_yaxes(
        automargin=True
    )

    fig.update_layout(
        height=max(
            430,
            len(plot_df)
            * 40
        ),
        margin=dict(
            l=20,
            r=85,
            t=60,
            b=40,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
        ),
    )

    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        marker_line_width=0.5,
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config=PLOTLY_CONFIG,
    )


# ============================================================
# TABELLA PORTAFOGLIO
# ============================================================

def tabella_portafoglio(df):

    colonne = [
        "Progetto",
        "Team",
        "SAL",
        "Stato",
        "Fatto",
        "Da fare",
    ]

    if df[
        "SAL atteso"
    ].notna().any():

        colonne += [
            "SAL atteso",
            "Scostamento",
        ]

    if (
        "Presente in entrambi"
        in df.columns
        and
        df[
            "Presente in entrambi"
        ].any()
    ):

        colonne.append(
            "Presente in entrambi"
        )

    tabella = df[
        colonne
    ].copy()

    configurazione = {

        "SAL":
            st.column_config.ProgressColumn(
                "SAL",
                min_value=0,
                max_value=100,
                format="%.1f%%",
            ),

        "Fatto":
            st.column_config.NumberColumn(
                "Giorni fatti",
                format="%.1f",
            ),

        "Da fare":
            st.column_config.NumberColumn(
                "Giorni da fare",
                format="%.1f",
            ),
    }

    if "SAL atteso" in tabella.columns:

        configurazione[
            "SAL atteso"
        ] = (
            st.column_config.ProgressColumn(
                "SAL atteso",
                min_value=0,
                max_value=100,
                format="%.1f%%",
            )
        )

        configurazione[
            "Scostamento"
        ] = (
            st.column_config.NumberColumn(
                "Scostamento",
                format="%+.1f p.p.",
            )
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

    return (
        df.to_csv(
            index=False,
            sep=";",
            decimal=",",
        )
        .encode(
            "utf-8-sig"
        )
    )


# ============================================================
# AVVIO APP
# ============================================================

if not check_password():
    st.stop()


# ============================================================
# HEADER
# ============================================================

header_left, header_right = st.columns(
    [
        6,
        1
    ]
)

with header_left:

    st.title(
        "📊 Dashboard Monitoraggio SAL MiniPIA"
    )

    st.markdown(
        """
        <div class="dashboard-subtitle">
        Portafoglio progetti · EPAL · MGIO
        </div>
        """,
        unsafe_allow_html=True,
    )


with header_right:

    if st.button(
        "Esci",
        use_container_width=True,
    ):

        st.session_state[
            "password_correct"
        ] = False

        st.rerun()


# ============================================================
# CARICAMENTO WORKBOOK
# ============================================================

try:

    with st.spinner(
        "Caricamento dati dal Dashboard Google Sheets..."
    ):

        (
            fogli,
            timestamp_caricamento
        ) = carica_workbook(
            SHEET_ID
        )

except Exception as exc:

    st.error(
        "Impossibile caricare "
        "il Dashboard Google Sheets."
    )

    st.exception(
        exc
    )

    st.stop()


sheet_names = list(
    fogli.keys()
)


# ============================================================
# STATO SINCRONIZZAZIONE APP
# ============================================================

st.caption(
    "● Dati caricati dall'app: "
    f"{timestamp_caricamento.strftime('%d/%m/%Y - %H:%M')} "
    f"· Cache {CACHE_TTL_SECONDS // 60} min"
)


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title(
    "Controlli"
)


# ------------------------------------------------------------
# REFRESH
# ------------------------------------------------------------

if st.sidebar.button(
    "🔄 Aggiorna dati",
    use_container_width=True,
):

    st.cache_data.clear()

    st.rerun()


# ------------------------------------------------------------
# VISTA
# ------------------------------------------------------------

vista = st.sidebar.radio(
    "Vista",
    [
        "Executive",
        "Avanzamento",
        "Dettaglio progetto",
        "Dati sorgente",
    ],
)


# ------------------------------------------------------------
# PORTAFOGLIO
# ------------------------------------------------------------

scope = st.sidebar.radio(
    "Portfolio",
    [
        "Tutti - EPAL+MGIO",
        "EPAL",
        "MGIO",
    ],
)


# ============================================================
# COSTRUZIONE GANTT EPAL
# ============================================================

if GANTT_EPAL in fogli:

    portfolio_epal = (
        costruisci_portafoglio(
            fogli[
                GANTT_EPAL
            ],
            "EPAL",
            GANTT_EPAL,
        )
    )

else:

    portfolio_epal = (
        pd.DataFrame()
    )


# ============================================================
# COSTRUZIONE GANTT MGIO
# ============================================================

if GANTT_MGIO in fogli:

    portfolio_mgio = (
        costruisci_portafoglio(
            fogli[
                GANTT_MGIO
            ],
            "MGIO",
            GANTT_MGIO,
        )
    )

else:

    portfolio_mgio = (
        pd.DataFrame()
    )


# ============================================================
# COSTRUZIONE EPAL + MGIO
# ============================================================

# Per il consolidato vengono uniti
# i due GANTT individuali.
#
# Questo garantisce che per ogni progetto
# rimanga sempre disponibile l'informazione:
#
# TEAM = EPAL / MGIO

portfolio_combinato = pd.concat(
    [
        portfolio_epal,
        portfolio_mgio,
    ],
    ignore_index=True,
)


portfolio_combinato = (
    aggiungi_flag_doppio_portafoglio(
        portfolio_combinato
    )
)


# ============================================================
# SELEZIONE PORTAFOGLIO
# ============================================================

if scope == "EPAL":

    portfolio = (
        portfolio_epal.copy()
    )


elif scope == "MGIO":

    portfolio = (
        portfolio_mgio.copy()
    )


else:

    portfolio = (
        portfolio_combinato.copy()
    )


# ============================================================
# FALLBACK GANTT EPAL+MGIO
# ============================================================

# Se per qualsiasi ragione i due GANTT
# individuali non fossero disponibili,
# l'app tenta di utilizzare direttamente
# GANTT_SAL_PROGETTI_EPAL+MGIO.
#
# Questo è possibile se il foglio contiene
# una colonna TEAM / RESPONSABILE / CONSULENTE.

if (
    scope == "Tutti - EPAL+MGIO"
    and portfolio.empty
    and GANTT_COMBINATO in fogli
):

    df_combinato = fogli[
        GANTT_COMBINATO
    ]

    col_team = trova_colonna_team(
        df_combinato
    )

    if col_team is not None:

        parti = []

        for team in [
            "EPAL",
            "MGIO",
        ]:

            mask = (
                df_combinato[
                    col_team
                ]
                .astype(str)
                .str.upper()
                .str.contains(
                    team,
                    na=False,
                )
            )

            parte = (
                costruisci_portafoglio(
                    df_combinato.loc[
                        mask
                    ],
                    team,
                    GANTT_COMBINATO,
                )
            )

            parti.append(
                parte
            )

        portfolio = (
            pd.concat(
                parti,
                ignore_index=True,
            )
        )

        portfolio = (
            aggiungi_flag_doppio_portafoglio(
                portfolio
            )
        )


# ============================================================
# FILTRI PORTAFOGLIO
# ============================================================

portfolio_filtrato = (
    portfolio.copy()
)


if (
    vista != "Dati sorgente"
    and
    not portfolio.empty
):

    st.sidebar.markdown(
        "---"
    )

    st.sidebar.subheader(
        "Filtri"
    )


    # --------------------------------------------------------
    # RICERCA PROGETTO
    # --------------------------------------------------------

    ricerca = st.sidebar.text_input(
        "🔎 Cerca progetto"
    )


    # --------------------------------------------------------
    # STATO
    # --------------------------------------------------------

    filtro_stato = (
        st.sidebar.selectbox(
            "Stato",
            [
                "Tutti",
                "Critico",
                "In avanzamento",
                "Avanzato",
                "Completato",
            ],
        )
    )


    # --------------------------------------------------------
    # RANGE SAL
    # --------------------------------------------------------

    range_sal = (
        st.sidebar.slider(
            "Intervallo SAL",
            min_value=0,
            max_value=100,
            value=(
                0,
                100
            ),
            step=1,
        )
    )


    # --------------------------------------------------------
    # APPLICAZIONE RICERCA
    # --------------------------------------------------------

    if ricerca.strip():

        portfolio_filtrato = (
            portfolio_filtrato[
                portfolio_filtrato[
                    "Progetto"
                ]
                .astype(str)
                .str.contains(
                    ricerca.strip(),
                    case=False,
                    na=False,
                )
            ]
        )


    # --------------------------------------------------------
    # APPLICAZIONE STATO
    # --------------------------------------------------------

    if filtro_stato != "Tutti":

        portfolio_filtrato = (
            portfolio_filtrato[
                portfolio_filtrato[
                    "Stato"
                ]
                == filtro_stato
            ]
        )


    # --------------------------------------------------------
    # APPLICAZIONE RANGE
    # --------------------------------------------------------

    portfolio_filtrato = (
        portfolio_filtrato[
            portfolio_filtrato[
                "SAL"
            ]
            .between(
                range_sal[0],
                range_sal[1],
                inclusive="both",
            )
        ]
    )


# ============================================================
# VISTA EXECUTIVE
# ============================================================

if vista == "Executive":

    if portfolio_filtrato.empty:

        st.info(
            "Nessun progetto corrisponde "
            "ai filtri selezionati."
        )

        st.stop()


    # --------------------------------------------------------
    # SAL PORTAFOGLIO
    # --------------------------------------------------------

    (
        sal_portafoglio,
        metodo_sal
    ) = portfolio_sal(
        portfolio_filtrato
    )


    # --------------------------------------------------------
    # KPI
    # --------------------------------------------------------

    totale_progetti = len(
        portfolio_filtrato
    )


    completati = int(
        (
            portfolio_filtrato[
                "Stato"
            ]
            == "Completato"
        )
        .sum()
    )


    in_corso = (
        totale_progetti
        - completati
    )


    critici = int(
        (
            portfolio_filtrato[
                "Stato"
            ]
            == "Critico"
        )
        .sum()
    )


    anomalie = int(
        portfolio_filtrato[
            "Anomalia SAL"
        ]
        .sum()
    )


    st.subheader(
        f"Portfolio · {scope}"
    )


    k1, k2, k3, k4, k5 = (
        st.columns(5)
    )


    k1.metric(
        "Progetti",
        totale_progetti
    )


    k2.metric(
        "SAL portafoglio",
        formatta_percentuale(
            sal_portafoglio
        ),
    )


    k3.metric(
        "Completati",
        completati
    )


    k4.metric(
        "In corso",
        in_corso
    )


    k5.metric(
        "Critici",
        critici
    )


    st.caption(
        "Metodo SAL portafoglio: "
        f"{metodo_sal}."
    )


    # ========================================================
    # ANOMALIE SAL
    # ========================================================

    if anomalie > 0:

        st.warning(
            f"Rilevati {anomalie} valori SAL "
            "fuori dall'intervallo 0–100%. "
            "Nei grafici la barra è limitata "
            "a 0–100%, ma il valore sorgente "
            "rimane disponibile per il controllo."
        )


        with st.expander(
            "Visualizza anomalie SAL"
        ):

            anomalie_df = (
                portfolio_filtrato[
                    portfolio_filtrato[
                        "Anomalia SAL"
                    ]
                ][
                    [
                        "Progetto",
                        "Team",
                        "SAL sorgente",
                        "SAL",
                    ]
                ]
                .copy()
            )


            anomalie_df[
                "SAL sorgente"
            ] = (
                anomalie_df[
                    "SAL sorgente"
                ]
                .apply(
                    lambda x:
                    formatta_percentuale(
                        x,
                        2
                    )
                )
            )


            anomalie_df[
                "SAL"
            ] = (
                anomalie_df[
                    "SAL"
                ]
                .apply(
                    formatta_percentuale
                )
            )


            st.dataframe(
                anomalie_df,
                use_container_width=True,
                hide_index=True,
            )


    # ========================================================
    # PROGETTI PRESENTI IN ENTRAMBI
    # ========================================================

    if (
        "Presente in entrambi"
        in portfolio_filtrato.columns
        and
        portfolio_filtrato[
            "Presente in entrambi"
        ]
        .any()
    ):

        n_condivisi = (
            portfolio_filtrato.loc[
                portfolio_filtrato[
                    "Presente in entrambi"
                ],
                "Progetto",
            ]
            .map(
                normalizza_testo
            )
            .nunique()
        )


        st.info(
            f"{n_condivisi} progetto/i risultano "
            "presenti sia nel portafoglio EPAL "
            "sia nel portafoglio MGIO. "
            "La dashboard li mantiene distinti "
            "per team, senza presumere che siano duplicati."
        )


    # ========================================================
    # RANKING
    # ========================================================

    grafico_ranking(
        portfolio_filtrato,
        "Avanzamento dei progetti"
    )


    # ========================================================
    # DISTRIBUZIONE + CONFRONTO TEAM
    # ========================================================

    col_left, col_right = (
        st.columns(2)
    )


    with col_left:

        grafico_distribuzione_stati(
            portfolio_filtrato
        )


    with col_right:

        if scope == "Tutti - EPAL+MGIO":

            grafico_confronto_team(
                portfolio_filtrato
            )


        else:

            # ------------------------------------------------
            # CARICO DI LAVORO
            # ------------------------------------------------

            fatto = (
                portfolio_filtrato[
                    "Fatto"
                ]
                .dropna()
                .sum()
            )


            residuo = (
                portfolio_filtrato[
                    "Da fare"
                ]
                .dropna()
                .sum()
            )


            carico = pd.DataFrame(
                {
                    "Voce": [
                        "Giorni fatti",
                        "Giorni da fare",
                    ],
                    "Giorni": [
                        fatto,
                        residuo,
                    ],
                }
            )


            fig = px.bar(
                carico,
                x="Giorni",
                y="Voce",
                orientation="h",
                title=(
                    "Carico di lavoro registrato"
                ),
                text="Giorni",
            )


            fig.update_traces(
                texttemplate="%{text:.1f}",
                textposition="outside",
            )


            fig.update_layout(
                height=390,
                margin=dict(
                    l=20,
                    r=55,
                    t=60,
                    b=40,
                ),
                showlegend=False,
            )


            st.plotly_chart(
                fig,
                use_container_width=True,
                config=PLOTLY_CONFIG,
            )


    # ========================================================
    # PRIORITÀ OPERATIVE
    # ========================================================

    priorita = (
        portfolio_filtrato[
            portfolio_filtrato[
                "Stato"
            ]
            .isin(
                [
                    "Critico",
                    "In avanzamento",
                ]
            )
        ]
        .sort_values(
            [
                "SAL",
                "Progetto",
            ]
        )
        .head(10)
    )


    if not priorita.empty:

        st.markdown(
            "---"
        )

        st.subheader(
            "Priorità operative"
        )


        priorita_view = (
            priorita[
                [
                    "Progetto",
                    "Team",
                    "SAL",
                    "Stato",
                    "Da fare",
                ]
            ]
            .copy()
        )


        st.dataframe(
            priorita_view,
            use_container_width=True,
            hide_index=True,
            column_config={

                "SAL":
                    st.column_config.ProgressColumn(
                        "SAL",
                        min_value=0,
                        max_value=100,
                        format="%.1f%%",
                    ),

                "Da fare":
                    st.column_config.NumberColumn(
                        "Giorni da fare",
                        format="%.1f",
                    ),
            },
        )


    # ========================================================
    # SAL REALE VS ATTESO
    # ========================================================

    if (
        portfolio_filtrato[
            "SAL atteso"
        ]
        .notna()
        .any()
    ):

        st.markdown(
            "---"
        )

        grafico_reale_atteso(
            portfolio_filtrato
        )


    # ========================================================
    # TABELLA PORTAFOGLIO
    # ========================================================

    st.markdown(
        "---"
    )

    st.subheader(
        "Portafoglio progetti"
    )


    tabella_portafoglio(
        portfolio_filtrato
    )


    # ========================================================
    # DOWNLOAD
    # ========================================================

    st.download_button(
        "⬇️ Scarica portafoglio filtrato in CSV",
        data=csv_bytes(
            portfolio_filtrato
        ),
        file_name=(
            "portfolio_sal_"
            + scope
            .lower()
            .replace(
                " ",
                "_"
            )
            .replace(
                "+",
                "piu"
            )
            + ".csv"
        ),
        mime="text/csv",
    )


# ============================================================
# VISTA AVANZAMENTO
# ============================================================

elif vista == "Avanzamento":

    if portfolio_filtrato.empty:

        st.info(
            "Nessun progetto corrisponde "
            "ai filtri selezionati."
        )

        st.stop()


    st.subheader(
        f"Analisi avanzamento · {scope}"
    )


    ordinamento = st.radio(
        "Ordinamento",
        [
            "SAL crescente",
            "SAL decrescente",
            "Nome progetto",
        ],
        horizontal=True,
    )


    avanzamento = (
        portfolio_filtrato.copy()
    )


    if ordinamento == "SAL crescente":

        avanzamento = (
            avanzamento
            .sort_values(
                "SAL",
                ascending=True
            )
        )


    elif ordinamento == "SAL decrescente":

        avanzamento = (
            avanzamento
            .sort_values(
                "SAL",
                ascending=False
            )
        )


    else:

        avanzamento = (
            avanzamento
            .sort_values(
                "Progetto",
                ascending=True
            )
        )


    grafico_ranking(
        avanzamento,
        "Ranking SAL"
    )


    st.markdown(
        "---"
    )


    tabella_portafoglio(
        avanzamento
    )


    if (
        avanzamento[
            "SAL atteso"
        ]
        .notna()
        .any()
    ):

        st.markdown(
            "---"
        )

        grafico_reale_atteso(
            avanzamento
        )


# ============================================================
# VISTA DETTAGLIO PROGETTO
# ============================================================

elif vista == "Dettaglio progetto":

    if portfolio.empty:

        st.info(
            "Nessun progetto disponibile."
        )

        st.stop()


    # ========================================================
    # SELEZIONE PROGETTO
    # ========================================================

    options = (
        portfolio[
            [
                "Progetto",
                "Team",
            ]
        ]
        .drop_duplicates()
        .sort_values(
            [
                "Progetto",
                "Team",
            ]
        )
        .copy()
    )


    options[
        "Label"
    ] = (
        options[
            "Progetto"
        ]
        + " · "
        + options[
            "Team"
        ]
    )


    scelta = st.selectbox(
        "Seleziona il progetto",
        options[
            "Label"
        ]
        .tolist(),
    )


    riga_scelta = (
        options[
            options[
                "Label"
            ]
            == scelta
        ]
        .iloc[0]
    )


    progetto = (
        riga_scelta[
            "Progetto"
        ]
    )


    team = (
        riga_scelta[
            "Team"
        ]
    )


    righe_progetto = (
        portfolio[
            (
                portfolio[
                    "Progetto"
                ]
                == progetto
            )
            &
            (
                portfolio[
                    "Team"
                ]
                == team
            )
        ]
    )


    if righe_progetto.empty:

        st.warning(
            "Dati del progetto non disponibili."
        )

        st.stop()


    riepilogo = (
        righe_progetto.iloc[0]
    )


    # ========================================================
    # HEADER PROGETTO
    # ========================================================

    st.subheader(
        progetto
    )


    st.caption(
        f"Team: {team}"
    )


    # ========================================================
    # KPI PROGETTO
    # ========================================================

    p1, p2, p3, p4 = (
        st.columns(4)
    )


    p1.metric(
        "SAL",
        formatta_percentuale(
            riepilogo[
                "SAL"
            ]
        ),
    )


    p2.metric(
        "Giorni fatti",
        formatta_numero(
            riepilogo[
                "Fatto"
            ]
        ),
    )


    p3.metric(
        "Giorni da fare",
        formatta_numero(
            riepilogo[
                "Da fare"
            ]
        ),
    )


    p4.metric(
        "Stato",
        riepilogo[
            "Stato"
        ],
    )


    # ========================================================
    # ANOMALIA PROGETTO
    # ========================================================

    if bool(
        riepilogo[
            "Anomalia SAL"
        ]
    ):

        st.warning(
            "Il SAL sorgente è "
            f"{formatta_percentuale(riepilogo['SAL sorgente'], 2)}. "
            "La barra viene limitata a 100% "
            "esclusivamente per la visualizzazione."
        )


    # ========================================================
    # SCOSTAMENTO
    # ========================================================

    if pd.notna(
        riepilogo[
            "SAL atteso"
        ]
    ):

        scostamento = (
            riepilogo[
                "Scostamento"
            ]
        )


        st.metric(
            "Scostamento rispetto al SAL atteso",
            (
                f"{scostamento:+.1f} p.p."
                .replace(
                    ".",
                    ","
                )
            ),
        )


    # ========================================================
    # ASSOCIAZIONE AUTOMATICA FOGLIO SAL
    # ========================================================

    (
        foglio_auto,
        score
    ) = (
        trova_foglio_sal_migliore(
            progetto,
            team,
            sheet_names,
        )
    )


    candidati_sal = (
        lista_fogli_sal(
            sheet_names,
            team
        )
    )


    if not candidati_sal:

        st.info(
            "Non sono stati trovati "
            f"fogli SAL compatibili "
            f"con il team {team}."
        )

        st.stop()


    default_idx = 0


    if (
        foglio_auto is not None
        and
        score >= 0.30
    ):

        default_idx = (
            candidati_sal.index(
                foglio_auto
            )
        )


    foglio_sal = st.selectbox(
        "Foglio SAL associato",
        candidati_sal,
        index=default_idx,
        help=(
            "La dashboard propone automaticamente "
            "il foglio più compatibile con il nome "
            "del progetto. La selezione può essere "
            "corretta manualmente."
        ),
    )


    if (
        foglio_auto is not None
        and
        score >= 0.30
    ):

        st.caption(
            "Associazione automatica proposta: "
            f"{foglio_auto}"
        )


    # ========================================================
    # LETTURA SAL
    # ========================================================

    df_sal = fogli[
        foglio_sal
    ]


    attivita = (
        costruisci_attivita(
            df_sal
        )
    )


    # ========================================================
    # GRAFICO ATTIVITÀ
    # ========================================================

    if not attivita.empty:

        st.markdown(
            "---"
        )


        grafico_attivita(
            attivita,
            progetto
        )


        st.subheader(
            "Dettaglio attività"
        )


        tab_attivita = (
            attivita[
                [
                    "Attività",
                    "SAL",
                    "Stato",
                    "Fatto",
                    "Da fare",
                ]
            ]
            .copy()
        )


        st.dataframe(
            tab_attivita,
            use_container_width=True,
            hide_index=True,
            column_config={

                "SAL":
                    st.column_config.ProgressColumn(
                        "SAL",
                        min_value=0,
                        max_value=100,
                        format="%.1f%%",
                    ),

                "Fatto":
                    st.column_config.NumberColumn(
                        "Giorni fatti",
                        format="%.1f",
                    ),

                "Da fare":
                    st.column_config.NumberColumn(
                        "Giorni da fare",
                        format="%.1f",
                    ),
            },
        )


        anomalie_attivita = (
            attivita[
                attivita[
                    "Anomalia SAL"
                ]
            ]
        )


        if not anomalie_attivita.empty:

            st.warning(
                f"{len(anomalie_attivita)} attività "
                "presentano un SAL sorgente fuori "
                "dall'intervallo 0–100%."
            )


    else:

        st.info(
            "Il foglio SAL selezionato "
            "non contiene una struttura "
            "riconoscibile con attività e SAL."
        )


    # ========================================================
    # DATI SORGENTE PROGETTO
    # ========================================================

    with st.expander(
        "Visualizza dati sorgente del foglio SAL"
    ):

        st.dataframe(
            df_sal,
            use_container_width=True,
        )


# ============================================================
# VISTA DATI SORGENTE
# ============================================================

elif vista == "Dati sorgente":

    st.subheader(
        "Dati sorgente"
    )


    # I tre GANTT vengono proposti per primi.
    preferiti = [
        nome
        for nome in [
            GANTT_COMBINATO,
            GANTT_EPAL,
            GANTT_MGIO,
        ]
        if nome in sheet_names
    ]


    altri = [
        nome
        for nome in sheet_names
        if nome not in preferiti
    ]


    elenco = (
        preferiti
        + altri
    )


    foglio_raw = st.selectbox(
        "Foglio da visualizzare",
        elenco
    )


    df_raw = fogli[
        foglio_raw
    ]


    st.caption(
        f"{len(df_raw)} righe · "
        f"{len(df_raw.columns)} colonne"
    )


    st.dataframe(
        df_raw,
        use_container_width=True,
    )


    st.download_button(
        "⬇️ Scarica foglio in CSV",
        data=csv_bytes(
            df_raw
        ),
        file_name=(
            re.sub(
                r"[^A-Za-z0-9_-]+",
                "_",
                foglio_raw
            )
            + ".csv"
        ),
        mime="text/csv",
    )
