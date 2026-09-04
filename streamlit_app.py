import re
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime
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
        return datetime.now(
            ZoneInfo("Europe/Rome")
        )

    except Exception:
        return datetime.now()


def normalizza_testo(value):

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


def chiave_progetto(value):

    text = normalizza_testo(value)

    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        text
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def tokenizza(value):

    text = chiave_progetto(value)

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

    df = df.copy()

    df.columns = [
        str(c).strip()
        for c in df.columns
    ]

    # Non vengono eliminate le colonne Unnamed:
    # nei SAL individuali servono a preservare
    # le posizioni fisiche delle colonne H:I.

    df = df.dropna(
        how="all"
    )

    return df


# ============================================================
# CONVERSIONE NUMERICA
# ============================================================

def serie_numerica(serie):

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

    valori = []
    gia_percentuali = []

    for value in serie:

        if pd.isna(value):

            valori.append(
                float("nan")
            )

            gia_percentuali.append(
                False
            )

            continue

        if (
            isinstance(value, str)
            and "%" in value
        ):

            numero = serie_numerica(
                pd.Series([value])
            ).iloc[0]

            valori.append(
                numero
            )

            gia_percentuali.append(
                True
            )

        else:

            numero = serie_numerica(
                pd.Series([value])
            ).iloc[0]

            valori.append(
                numero
            )

            gia_percentuali.append(
                False
            )

    risultato = pd.Series(
        valori,
        index=serie.index,
        dtype="float64",
    )

    mask_percentuale = pd.Series(
        gia_percentuali,
        index=serie.index,
        dtype="bool",
    )

    valori_inferenza = risultato[
        (~mask_percentuale)
        & risultato.notna()
    ]

    if not valori_inferenza.empty:

        quota_frazioni = (
            valori_inferenza.abs()
            <= 1.5
        ).mean()

        mediana = (
            valori_inferenza
            .abs()
            .median()
        )

        if (
            quota_frazioni >= 0.60
            or mediana <= 1.5
        ):

            risultato.loc[
                (~mask_percentuale)
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
        return "N/D"

    return (
        f"{float(value):.{decimali}f}%"
        .replace(".", ",")
    )


def formatta_numero(
    value,
    decimali=1
):

    if pd.isna(value):
        return "N/D"

    return (
        f"{float(value):.{decimali}f}"
        .replace(".", ",")
    )


# ============================================================
# ORDINAMENTO PORTAFOGLIO
# ============================================================

def ordina_portafoglio(
    df,
    ordinamento
):

    out = df.copy()

    if ordinamento == "SAL crescente":

        return out.sort_values(
            ["SAL", "Progetto"],
            ascending=[
                True,
                True
            ],
            na_position="last",
        )

    if ordinamento == "SAL decrescente":

        return out.sort_values(
            ["SAL", "Progetto"],
            ascending=[
                False,
                True
            ],
            na_position="last",
        )

    if ordinamento == "Nome progetto":

        return out.sort_values(
            "Progetto",
            ascending=True,
            key=lambda serie:
                serie
                .astype(str)
                .str.lower(),
            na_position="last",
        )

    return out


# ============================================================
# CLASSIFICAZIONE SAL
# ============================================================

def stato_da_sal(value):

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

    if valore <= SOGLIA_INIZIALE:
        return "In stato iniziale"

    if valore <= SOGLIA_INTERMEDIO:
        return "In stato intermedio"

    return "In stato avanzato"


# ============================================================
# RICONOSCIMENTO COLONNE
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

    nomi = {
        col: normalizza_testo(col)
        for col in df.columns
    }

    for candidato in exact:

        candidato_norm = normalizza_testo(
            candidato
        )

        for col, nome in nomi.items():

            if nome == candidato_norm:
                return col

    if contains_all:

        for col, nome in nomi.items():

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

    if contains_any:

        for col, nome in nomi.items():

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
            "PROGETTO EPAL",
            "PROGETTO MGIO",
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


def trova_colonna_stato(df):

    return trova_colonna(
        df,
        exact=[
            "STATO",
            "STATUS",
            "STATO PROGETTO",
        ],
        contains_any=[
            "stato",
            "status",
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
            "GIORNI FATTO",
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
            "RESIDUO",
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
# STATO SORGENTE
# ============================================================

def stato_sorgente_e_completo(value):

    if value is None:
        return False

    if pd.isna(value):
        return False

    stato = normalizza_testo(
        value
    )

    stati_completati = {
        "completo",
        "completato",
        "completed",
        "chiuso",
        "concluso",
        "terminato",
        "finito",
    }

    return (
        stato in stati_completati
        or stato.startswith(
            "complet"
        )
    )


# ============================================================
# STATO UFFICIALE PROGETTO
# ============================================================

def normalizza_stato_progetto(
    stato_sorgente,
    sal
):
    """
    REGOLA PRINCIPALE:

    Lo stato COMPLETO presente nel GANTT
    prevale sempre sul valore numerico del SAL.
    """

    if stato_sorgente_e_completo(
        stato_sorgente
    ):

        return "Completato"

    return stato_da_sal(
        sal
    )


# ============================================================
# TIPO SAL
# ============================================================

def tipo_sal_da_nome(nome):

    nome_norm = (
        normalizza_testo(nome)
        .replace(" ", "")
    )

    if (
        "(epal+mgio)" in nome_norm
        or "(mgio+epal)" in nome_norm
    ):
        return "EPAL+MGIO"

    if "(epal)" in nome_norm:
        return "EPAL"

    if "(mgio)" in nome_norm:
        return "MGIO"

    return None


# ============================================================
# COLONNE GIORNI SAL
# ============================================================

def trova_colonne_giorni_sal(
    df,
    nome_foglio
):

    col_fatto = trova_colonna_fatto(
        df
    )

    col_da_fare = trova_colonna_da_fare(
        df
    )

    if (
        col_fatto is not None
        and col_da_fare is not None
    ):

        return (
            col_fatto,
            col_da_fare,
            "intestazioni del foglio SAL"
        )

    tipo = tipo_sal_da_nome(
        nome_foglio
    )

    if (
        tipo in {
            "EPAL",
            "MGIO"
        }
        and len(df.columns) >= 9
    ):

        candidato_fatto = (
            col_fatto
            if col_fatto is not None
            else df.columns[7]
        )

        candidato_da_fare = (
            col_da_fare
            if col_da_fare is not None
            else df.columns[8]
        )

        valori_fatto = serie_numerica(
            df[candidato_fatto]
        )

        valori_da_fare = serie_numerica(
            df[candidato_da_fare]
        )

        if (
            valori_fatto.notna().any()
            and valori_da_fare.notna().any()
        ):

            return (
                candidato_fatto,
                candidato_da_fare,
                "colonne H:I del SAL individuale"
            )

    return (
        col_fatto,
        col_da_fare,
        "intestazioni disponibili"
    )


# ============================================================
# RIGHE ATTIVITÀ VALIDE
# ============================================================

def testo_non_nan(serie):

    return ~serie.isin(
        [
            "nan",
            "none",
            "nat",
        ]
    )


def mask_righe_attivita_valide(
    df,
    col_attivita=None
):

    if col_attivita is None:

        return pd.Series(
            True,
            index=df.index,
            dtype=bool,
        )

    testo = (
        df[col_attivita]
        .astype(str)
        .map(normalizza_testo)
    )

    mask = (
        testo.ne("")
        &
        testo_non_nan(testo)
        &
        ~testo.str.match(
            r"^(totale|totali|total)\b",
            na=False,
        )
    )

    return mask


# ============================================================
# CALCOLO GIORNI DAL SAL
# ============================================================

def calcola_giorni_progetto(
    df_sal,
    nome_foglio
):

    (
        col_fatto,
        col_da_fare,
        fonte_colonne
    ) = trova_colonne_giorni_sal(
        df_sal,
        nome_foglio
    )

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

    if (
        col_fatto is None
        or col_da_fare is None
    ):
        return risultato_vuoto

    fatto = (
        serie_numerica(
            df_sal[col_fatto]
        )
        .clip(lower=0)
    )

    da_fare = (
        serie_numerica(
            df_sal[col_da_fare]
        )
        .clip(lower=0)
    )

    col_attivita = trova_colonna_attivita(
        df_sal
    )

    mask = mask_righe_attivita_valide(
        df_sal,
        col_attivita
    )

    mask &= (
        fatto.notna()
        |
        da_fare.notna()
    )

    fatto = fatto.loc[
        mask
    ]

    da_fare = da_fare.loc[
        mask
    ]

    if (
        fatto.empty
        and da_fare.empty
    ):
        return risultato_vuoto

    tot_fatto = (
        fatto
        .fillna(0)
        .sum()
    )

    tot_da_fare = (
        da_fare
        .fillna(0)
        .sum()
    )

    totale = (
        tot_fatto
        + tot_da_fare
    )

    if totale <= 0:

        return {
            **risultato_vuoto,
            "giorni_fatti":
                tot_fatto,
            "giorni_da_fare":
                tot_da_fare,
            "giorni_totali":
                totale,
        }

    pct_fatti = (
        tot_fatto
        / totale
    ) * 100

    pct_da_fare = (
        tot_da_fare
        / totale
    ) * 100

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


def calcola_giorni_da_gantt(
    fatto,
    da_fare
):

    if (
        pd.isna(fatto)
        or pd.isna(da_fare)
    ):
        return None

    fatto = max(
        float(fatto),
        0
    )

    da_fare = max(
        float(da_fare),
        0
    )

    totale = (
        fatto
        + da_fare
    )

    if totale <= 0:
        return None

    return {
        "disponibile": True,
        "giorni_fatti": fatto,
        "giorni_da_fare": da_fare,
        "giorni_totali": totale,
        "pct_fatti":
            (fatto / totale) * 100,
        "pct_da_fare":
            (da_fare / totale) * 100,
        "fonte":
            "GANTT del progetto",
    }


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
            "non è configurato."
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
# GANTT COMBINATO
# ============================================================

def trova_gantt_combinato(
    sheet_names
):

    for nome in GANTT_COMBINATI_POSSIBILI:

        if nome in sheet_names:
            return nome

    return None


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

    col_stato = trova_colonna_stato(
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
            "Progetto":
                df[col_progetto]
        }
    )

    out["Progetto"] = (
        out["Progetto"]
        .astype(str)
        .str.strip()
    )

    chiavi = (
        out["Progetto"]
        .map(
            chiave_progetto
        )
    )

    mask = (
        chiavi.ne("")
        &
        ~chiavi.isin(
            {
                "nan",
                "none",
                "totale",
                "totali",
                "total",
            }
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
            .clip(lower=0)
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
            .clip(lower=0)
        )

    else:

        out["Da fare"] = float(
            "nan"
        )

    # --------------------------------------------------------
    # SAL SORGENTE
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

    # --------------------------------------------------------
    # SAL VISUALIZZATO
    # --------------------------------------------------------

    out["SAL"] = (
        out["SAL sorgente"]
        .clip(
            lower=0,
            upper=100
        )
    )

    # --------------------------------------------------------
    # ANOMALIA SAL
    # --------------------------------------------------------

    out["Anomalia SAL"] = (
        (
            out["SAL sorgente"] < 0
        )
        |
        (
            out["SAL sorgente"] > 100
        )
    )

    # --------------------------------------------------------
    # STATO SORGENTE
    # --------------------------------------------------------

    if col_stato is not None:

        stato_sorgente = (
            df.loc[
                indici,
                col_stato
            ]
        )

        out["Stato sorgente"] = (
            stato_sorgente
            .where(
                stato_sorgente.notna(),
                ""
            )
            .astype(str)
            .str.strip()
        )

    else:

        out["Stato sorgente"] = ""

    # --------------------------------------------------------
    # STATO DASHBOARD
    # --------------------------------------------------------

    out["Stato"] = [
        normalizza_stato_progetto(
            stato_sorgente,
            sal
        )
        for stato_sorgente, sal
        in zip(
            out["Stato sorgente"],
            out["SAL"]
        )
    ]

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
# NORMALIZZAZIONE TEAM
# ============================================================

def normalizza_team(value):

    text = normalizza_testo(
        value
    )

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

    teams = {
        team
        for team in teams
        if team
        and team != "N/D"
    }

    if "EPAL+MGIO" in teams:
        return "EPAL+MGIO"

    if (
        "EPAL" in teams
        and "MGIO" in teams
    ):
        return "EPAL+MGIO"

    if "EPAL" in teams:
        return "EPAL"

    if "MGIO" in teams:
        return "MGIO"

    return "N/D"


# ============================================================
# PORTAFOGLIO COMBINATO
# ============================================================

def costruisci_portafoglio_combinato(
    df,
    portfolio_epal,
    portfolio_mgio,
    source_sheet,
):

    base = costruisci_portafoglio(
        df,
        "N/D",
        source_sheet,
    )

    if base.empty:
        return base

    if (
        not portfolio_epal.empty
        and
        "Progetto"
        in portfolio_epal.columns
    ):

        epal_keys = set(
            portfolio_epal[
                "Progetto"
            ]
            .map(
                chiave_progetto
            )
        )

    else:

        epal_keys = set()

    if (
        not portfolio_mgio.empty
        and
        "Progetto"
        in portfolio_mgio.columns
    ):

        mgio_keys = set(
            portfolio_mgio[
                "Progetto"
            ]
            .map(
                chiave_progetto
            )
        )

    else:

        mgio_keys = set()

    col_progetto = trova_colonna_progetto(
        df
    )

    col_team = trova_colonna_team(
        df
    )

    team_map = {}

    if (
        col_progetto is not None
        and col_team is not None
    ):

        for progetto, team in zip(
            df[col_progetto],
            df[col_team],
        ):

            key = chiave_progetto(
                progetto
            )

            if not key:
                continue

            team_norm = normalizza_team(
                team
            )

            if team_norm == "N/D":
                continue

            if key not in team_map:
                team_map[key] = set()

            team_map[key].add(
                team_norm
            )

    def assegna_team(progetto):

        key = chiave_progetto(
            progetto
        )

        if key in team_map:

            team_specifico = (
                team_da_insieme(
                    team_map[key]
                )
            )

            if team_specifico != "N/D":
                return team_specifico

        in_epal = (
            key in epal_keys
        )

        in_mgio = (
            key in mgio_keys
        )

        if in_epal and in_mgio:
            return "EPAL+MGIO"

        if in_epal:
            return "EPAL"

        if in_mgio:
            return "MGIO"

        return "N/D"

    base["Team"] = (
        base["Progetto"]
        .apply(
            assegna_team
        )
    )

    return base


# ============================================================
# CONSOLIDAMENTO PROGETTI UNIVOCI
# ============================================================

def consolida_progetti_univoci(df):
    """
    Una sola riga per ciascun progetto MiniPIA distinto.

    REGOLA STATO:
    se almeno una riga sorgente del progetto riporta
    uno stato equivalente a COMPLETO, lo stato
    consolidato è COMpletato.

    Lo stato sorgente prevale quindi sul SAL numerico.
    """

    if (
        df is None
        or df.empty
    ):
        return pd.DataFrame()

    temp = df.copy()

    temp[
        "Chiave progetto"
    ] = (
        temp["Progetto"]
        .map(
            chiave_progetto
        )
    )

    temp = temp[
        temp[
            "Chiave progetto"
        ].ne("")
    ].copy()

    if temp.empty:
        return pd.DataFrame()

    righe = []

    for (
        chiave,
        gruppo
    ) in temp.groupby(
        "Chiave progetto",
        sort=False,
    ):

        gruppo = gruppo.copy()

        # ----------------------------------------------------
        # NOME PROGETTO
        # ----------------------------------------------------

        nomi_validi = [
            str(x).strip()
            for x
            in gruppo[
                "Progetto"
            ].tolist()
            if pd.notna(x)
            and str(x).strip()
        ]

        progetto = (
            nomi_validi[0]
            if nomi_validi
            else chiave
        )

        # ----------------------------------------------------
        # TEAM
        # ----------------------------------------------------

        teams = {
            str(x).strip()
            for x
            in gruppo[
                "Team"
            ].tolist()
            if pd.notna(x)
        }

        team = team_da_insieme(
            teams
        )

        # ----------------------------------------------------
        # GIORNI
        # ----------------------------------------------------

        fatto_series = (
            pd.to_numeric(
                gruppo["Fatto"],
                errors="coerce",
            )
            if "Fatto"
            in gruppo.columns
            else pd.Series(
                dtype=float
            )
        )

        da_fare_series = (
            pd.to_numeric(
                gruppo["Da fare"],
                errors="coerce",
            )
            if "Da fare"
            in gruppo.columns
            else pd.Series(
                dtype=float
            )
        )

        validi_giorni = (
            fatto_series.notna()
            &
            da_fare_series.notna()
        )

        if validi_giorni.any():

            fatto = (
                fatto_series.loc[
                    validi_giorni
                ]
                .clip(
                    lower=0
                )
                .sum()
            )

            da_fare = (
                da_fare_series.loc[
                    validi_giorni
                ]
                .clip(
                    lower=0
                )
                .sum()
            )

            totale_giorni = (
                fatto
                + da_fare
            )

        else:

            fatto = float("nan")
            da_fare = float("nan")
            totale_giorni = float("nan")

        # ----------------------------------------------------
        # SAL SORGENTE
        # ----------------------------------------------------
        #
        # Se esiste una sola riga, il dato sorgente
        # viene mantenuto esattamente.
        #
        # Se esistono più righe per lo stesso progetto,
        # viene utilizzata la media dei SAL sorgente.
        #
        # Questo evita che eventuali valori anomali
        # come 113,32% vengano nascosti.
        # ----------------------------------------------------

        sal_values = (
            pd.to_numeric(
                gruppo[
                    "SAL sorgente"
                ],
                errors="coerce",
            )
            .dropna()
        )

        if not sal_values.empty:

            if len(
                sal_values
            ) == 1:

                sal_sorgente = (
                    sal_values.iloc[0]
                )

            else:

                sal_sorgente = (
                    sal_values.mean()
                )

        elif (
            pd.notna(
                totale_giorni
            )
            and totale_giorni > 0
        ):

            sal_sorgente = (
                fatto
                / totale_giorni
                * 100
            )

        else:

            sal_values_visualizzati = (
                pd.to_numeric(
                    gruppo["SAL"],
                    errors="coerce",
                )
                .dropna()
            )

            if not sal_values_visualizzati.empty:

                sal_sorgente = (
                    sal_values_visualizzati.mean()
                )

            else:

                sal_sorgente = float(
                    "nan"
                )

        # ----------------------------------------------------
        # SAL VISUALIZZATO
        # ----------------------------------------------------

        if pd.notna(
            sal_sorgente
        ):

            sal = min(
                max(
                    float(
                        sal_sorgente
                    ),
                    0
                ),
                100
            )

        else:

            sal = float(
                "nan"
            )

        # ----------------------------------------------------
        # ANOMALIA SAL
        # ----------------------------------------------------

        anomalie_originali = False

        if (
            "Anomalia SAL"
            in gruppo.columns
        ):

            anomalie_originali = bool(
                gruppo[
                    "Anomalia SAL"
                ]
                .fillna(False)
                .astype(bool)
                .any()
            )

        anomalia_consolidata = (
            anomalie_originali
            or
            (
                pd.notna(
                    sal_sorgente
                )
                and
                (
                    sal_sorgente < 0
                    or sal_sorgente > 100
                )
            )
        )

        # ----------------------------------------------------
        # STATO SORGENTE
        # ----------------------------------------------------

        stati_sorgente = []

        if (
            "Stato sorgente"
            in gruppo.columns
        ):

            for value in gruppo[
                "Stato sorgente"
            ].tolist():

                if pd.isna(value):
                    continue

                text = str(
                    value
                ).strip()

                if (
                    text
                    and
                    text not in stati_sorgente
                ):

                    stati_sorgente.append(
                        text
                    )

        stato_sorgente = (
            " | ".join(
                stati_sorgente
            )
        )

        # ----------------------------------------------------
        # STATO CONSOLIDATO
        # ----------------------------------------------------
        #
        # REGOLA DEFINITIVA:
        #
        # lo STATO sorgente del GANTT prevale
        # sul valore percentuale del SAL.
        #
        # Se almeno uno stato sorgente indica
        # COMPLETO, il progetto è Completato.
        # ----------------------------------------------------

        completo_da_sorgente = any(
            stato_sorgente_e_completo(
                value
            )
            for value
            in gruppo[
                "Stato sorgente"
            ].tolist()
        )

        if completo_da_sorgente:

            stato = "Completato"

        else:

            stato = stato_da_sal(
                sal
            )

        # ----------------------------------------------------
        # SAL ATTESO
        # ----------------------------------------------------

        sal_atteso = float(
            "nan"
        )

        if (
            "SAL atteso"
            in gruppo.columns
        ):

            sal_attesi = (
                pd.to_numeric(
                    gruppo[
                        "SAL atteso"
                    ],
                    errors="coerce",
                )
                .dropna()
            )

            if not sal_attesi.empty:

                sal_atteso = (
                    sal_attesi.mean()
                )

        if (
            pd.notna(sal)
            and
            pd.notna(
                sal_atteso
            )
        ):

            scostamento = (
                sal
                - sal_atteso
            )

        else:

            scostamento = float(
                "nan"
            )

        # ----------------------------------------------------
        # FOGLI ORIGINE
        # ----------------------------------------------------

        fogli_origine = []

        if (
            "Foglio origine"
            in gruppo.columns
        ):

            for value in gruppo[
                "Foglio origine"
            ].tolist():

                if pd.isna(value):
                    continue

                text = str(
                    value
                ).strip()

                if (
                    text
                    and
                    text not in fogli_origine
                ):

                    fogli_origine.append(
                        text
                    )

        # ----------------------------------------------------
        # OUTPUT
        # ----------------------------------------------------

        righe.append(
            {
                "Progetto":
                    progetto,

                "Team":
                    team,

                "Fatto":
                    fatto,

                "Da fare":
                    da_fare,

                "SAL sorgente":
                    sal_sorgente,

                "SAL":
                    sal,

                "Anomalia SAL":
                    anomalia_consolidata,

                "Stato sorgente":
                    stato_sorgente,

                "Stato":
                    stato,

                "SAL atteso":
                    sal_atteso,

                "Scostamento":
                    scostamento,

                "Foglio origine":
                    " | ".join(
                        fogli_origine
                    ),

                "Occorrenze consolidate":
                    len(gruppo),
            }
        )

    return (
        pd.DataFrame(
            righe
        )
        .reset_index(
            drop=True
        )
    )


# ============================================================
# SAL COMPLESSIVO PORTAFOGLIO
# ============================================================

def portfolio_sal(df):

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

    # Il SAL ponderato viene calcolato solo quando
    # tutti i progetti considerati dispongono sia
    # dei giorni fatti sia dei giorni da fare.
    if (
        len(df) > 0
        and validi_giorni.all()
    ):

        fatto = (
            df["Fatto"]
            .clip(lower=0)
            .sum()
        )

        residuo = (
            df["Da fare"]
            .clip(lower=0)
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

    # Se manca la coppia di giorni anche per un solo progetto,
    # viene utilizzata la media dei SAL di tutti i progetti
    # considerati, evitando un calcolo ponderato parziale.
    sal_validi = (
        df["SAL"]
        .dropna()
    )

    if not sal_validi.empty:

        return (
            sal_validi.mean(),
            "media dei SAL disponibili"
        )

    return (
        float("nan"),
        "N/D"
    )


# ============================================================
# FLAG PROGETTI PRESENTI IN ENTRAMBI
# ============================================================

def aggiungi_flag_condiviso(
    df,
    portfolio_epal,
    portfolio_mgio,
):

    df = df.copy()

    if (
        not portfolio_epal.empty
        and
        "Progetto"
        in portfolio_epal.columns
    ):

        epal_keys = set(
            portfolio_epal[
                "Progetto"
            ]
            .map(
                chiave_progetto
            )
        )

    else:

        epal_keys = set()

    if (
        not portfolio_mgio.empty
        and
        "Progetto"
        in portfolio_mgio.columns
    ):

        mgio_keys = set(
            portfolio_mgio[
                "Progetto"
            ]
            .map(
                chiave_progetto
            )
        )

    else:

        mgio_keys = set()

    condivisi = (
        epal_keys
        & mgio_keys
    )

    df[
        "Presente in entrambi"
    ] = (
        df["Progetto"]
        .map(
            chiave_progetto
        )
        .isin(
            condivisi
        )
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

        if nome in TEMPLATE_SAL:
            continue

        if not normalizza_testo(
            nome
        ).startswith(
            "sal_"
        ):
            continue

        tipo = tipo_sal_da_nome(
            nome
        )

        if team is None:

            risultati.append(
                nome
            )

            continue

        if team == "EPAL":

            if tipo in {
                "EPAL",
                "EPAL+MGIO"
            }:

                risultati.append(
                    nome
                )

        elif team == "MGIO":

            if tipo in {
                "MGIO",
                "EPAL+MGIO"
            }:

                risultati.append(
                    nome
                )

        elif team == "EPAL+MGIO":

            if tipo == "EPAL+MGIO":

                risultati.append(
                    nome
                )

        else:

            risultati.append(
                nome
            )

    return risultati


# ============================================================
# MATCH RIGOROSO PROGETTO -> SAL
# ============================================================

def estrai_nome_progetto_da_foglio_sal(nome_foglio):
    """
    Estrae dal nome del foglio SAL la parte che identifica
    il progetto, eliminando:
    - prefisso SAL_ / SAL;
    - suffisso di team (EPAL), (MGIO), (EPAL+MGIO), ecc.;
    - separatori tecnici come underscore ripetuti.

    Esempio:
        SAL_vetreria Cristal (EPAL) -> vetreria Cristal
    """

    if nome_foglio is None:
        return ""

    testo = str(nome_foglio).strip()

    testo = re.sub(
        r"^\s*sal[_\s-]*",
        "",
        testo,
        flags=re.IGNORECASE,
    )

    testo = re.sub(
        r"\s*\((?:EPAL|MGIO|EPAL\s*\+\s*MGIO|MGIO\s*\+\s*EPAL)\)\s*$",
        "",
        testo,
        flags=re.IGNORECASE,
    )

    testo = testo.replace("_", " ")

    testo = re.sub(
        r"\s+",
        " ",
        testo,
    ).strip()

    return testo


def chiave_match_progetto(value):
    """
    Chiave conservativa per il matching progetto/SAL.
    Non elimina parole descrittive del progetto, così da
    evitare associazioni troppo permissive.
    """

    if value is None:
        return ""

    testo = normalizza_testo(value)

    # Rimuove eventuali simboli usati soltanto a video.
    testo = testo.replace("✅", " ")
    testo = testo.replace("☑", " ")

    # Uniforma & senza alterare il significato.
    testo = testo.replace("&", " e ")

    testo = re.sub(
        r"[^a-z0-9]+",
        " ",
        testo,
    )

    return re.sub(
        r"\s+",
        " ",
        testo,
    ).strip()


def chiave_compatta_match(value):
    """
    Variante senza spazi, utile per gestire differenze
    puramente grafiche come Co.Ge.Sa. / CoGeSa.
    """

    return re.sub(
        r"[^a-z0-9]",
        "",
        chiave_match_progetto(value),
    )


def varianti_match_progetto(value):
    """
    Genera un piccolo insieme di alias controllati.

    La variante completa resta sempre quella principale.
    Per nomi del tipo:
        CoGeSa (Co.Ge.Sa.)
    vengono considerate anche la parte esterna e quella
    interna alle parentesi, evitando però trasformazioni
    eccessivamente aggressive.
    """

    if value is None:
        return set()

    testo_originale = str(value).strip()

    if not testo_originale:
        return set()

    parti = {testo_originale}

    # Parte esterna alle parentesi.
    senza_parentesi = re.sub(
        r"\([^)]*\)",
        " ",
        testo_originale,
    ).strip()

    if senza_parentesi:
        parti.add(senza_parentesi)

    # Contenuti tra parentesi come alias espliciti.
    for contenuto in re.findall(
        r"\(([^)]*)\)",
        testo_originale,
    ):
        contenuto = contenuto.strip()
        if contenuto:
            parti.add(contenuto)

    varianti = set()

    for parte in parti:

        chiave = chiave_match_progetto(parte)
        compatta = chiave_compatta_match(parte)

        if chiave:
            varianti.add(chiave)

        if compatta:
            varianti.add(compatta)

    return varianti


def token_match_progetto(value):

    testo = chiave_match_progetto(value)

    stopwords = {
        "srl",
        "spa",
        "sas",
        "snc",
        "societa",
        "soc",
        "coop",
        "cooperativa",
        "benefit",
    }

    return {
        token
        for token in testo.split()
        if len(token) >= 2
        and token not in stopwords
    }


def metriche_match_progetto_sal(
    progetto,
    nome_foglio,
):
    """
    Valuta la compatibilità tra un progetto del GANTT
    e un foglio SAL.

    Le corrispondenze esatte hanno sempre priorità.
    Il fuzzy matching viene usato solo come fallback e
    richiede una similarità elevata.
    """

    nome_sal = estrai_nome_progetto_da_foglio_sal(
        nome_foglio
    )

    varianti_progetto = varianti_match_progetto(
        progetto
    )

    varianti_sal = varianti_match_progetto(
        nome_sal
    )

    if (
        not varianti_progetto
        or not varianti_sal
    ):
        return {
            "esatto": False,
            "score": 0.0,
            "similarita": 0.0,
            "jaccard": 0.0,
            "metodo": "nessuna corrispondenza",
        }

    # --------------------------------------------------------
    # 1. MATCH ESATTO SU UNA DELLE VARIANTI CONTROLLATE
    # --------------------------------------------------------

    if varianti_progetto & varianti_sal:

        return {
            "esatto": True,
            "score": 1.0,
            "similarita": 1.0,
            "jaccard": 1.0,
            "metodo": "nome esatto",
        }

    # --------------------------------------------------------
    # 2. MATCH MOLTO FORTE PER CONTENIMENTO
    # --------------------------------------------------------
    # Viene accettato soltanto quando la parte più corta
    # rappresenta almeno l'85% della più lunga. Questo evita
    # casi come "Cristal" -> "vetreria Cristal".
    # --------------------------------------------------------

    miglior_contenimento = 0.0

    for a in varianti_progetto:
        for b in varianti_sal:

            if not a or not b:
                continue

            if a in b or b in a:

                rapporto = (
                    min(len(a), len(b))
                    / max(len(a), len(b))
                )

                miglior_contenimento = max(
                    miglior_contenimento,
                    rapporto,
                )

    if miglior_contenimento >= 0.85:

        return {
            "esatto": False,
            "score": 0.97,
            "similarita": miglior_contenimento,
            "jaccard": 1.0,
            "metodo": "contenimento forte",
        }

    # --------------------------------------------------------
    # 3. FUZZY MATCHING CONTROLLATO
    # --------------------------------------------------------

    progetto_chiave = chiave_match_progetto(
        progetto
    )

    sal_chiave = chiave_match_progetto(
        nome_sal
    )

    similarita = SequenceMatcher(
        None,
        progetto_chiave,
        sal_chiave,
    ).ratio()

    tokens_progetto = token_match_progetto(
        progetto
    )

    tokens_sal = token_match_progetto(
        nome_sal
    )

    unione = (
        tokens_progetto
        | tokens_sal
    )

    intersezione = (
        tokens_progetto
        & tokens_sal
    )

    if unione:
        jaccard = (
            len(intersezione)
            / len(unione)
        )
    else:
        jaccard = 0.0

    # Lo score finale premia soprattutto la somiglianza
    # testuale ma richiede anche coerenza dei token.
    score = (
        0.75 * similarita
        +
        0.25 * jaccard
    )

    return {
        "esatto": False,
        "score": score,
        "similarita": similarita,
        "jaccard": jaccard,
        "metodo": "fuzzy controllato",
    }


def trova_foglio_sal_rigoroso(
    progetto,
    team,
    sheet_names,
    tipi_ammessi=None,
    fogli_esclusi=None,
    soglia_fuzzy=0.90,
    margine_minimo=0.08,
):
    """
    Associa un progetto a un foglio SAL in modo rigoroso.

    Regole di sicurezza:
    1. vengono considerati solo i SAL del team ammesso;
    2. un match esatto è preferito a qualsiasi fuzzy match;
    3. se esistono più match esatti, il risultato è ambiguo;
    4. il fuzzy match è accettato solo con score >= 0,90;
    5. il miglior fuzzy match deve superare il secondo di
       almeno 0,08 punti;
    6. lo stesso foglio può essere escluso se già assegnato
       a un altro progetto nella medesima elaborazione.

    In caso di dubbio restituisce None invece di attribuire
    i giorni del SAL sbagliato.
    """

    fogli_esclusi = set(
        fogli_esclusi or []
    )

    candidati = [
        nome
        for nome in lista_fogli_sal(
            sheet_names,
            None
        )
        if nome not in fogli_esclusi
    ]

    if tipi_ammessi is not None:

        tipi_ammessi = set(
            tipi_ammessi
        )

        candidati = [
            nome
            for nome in candidati
            if tipo_sal_da_nome(nome)
            in tipi_ammessi
        ]

    elif team is not None:

        team_norm = normalizza_team(
            team
        )

        if team_norm in {
            "EPAL",
            "MGIO",
            "EPAL+MGIO",
        }:

            candidati = [
                nome
                for nome in candidati
                if tipo_sal_da_nome(nome)
                == team_norm
            ]

    if not candidati:

        return {
            "foglio": None,
            "score": 0.0,
            "metodo": "nessun candidato del team",
            "ambiguo": False,
        }

    valutazioni = []

    for nome in candidati:

        metriche = metriche_match_progetto_sal(
            progetto,
            nome,
        )

        valutazioni.append(
            {
                "foglio": nome,
                **metriche,
            }
        )

    # --------------------------------------------------------
    # MATCH ESATTI
    # --------------------------------------------------------

    esatti = [
        x
        for x in valutazioni
        if x["esatto"]
    ]

    if len(esatti) == 1:

        return {
            "foglio": esatti[0]["foglio"],
            "score": 1.0,
            "metodo": esatti[0]["metodo"],
            "ambiguo": False,
        }

    if len(esatti) > 1:

        return {
            "foglio": None,
            "score": 1.0,
            "metodo": "match esatto ambiguo",
            "ambiguo": True,
        }

    # --------------------------------------------------------
    # MATCH NON ESATTI
    # --------------------------------------------------------

    valutazioni.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    migliore = valutazioni[0]

    secondo_score = (
        valutazioni[1]["score"]
        if len(valutazioni) > 1
        else 0.0
    )

    margine = (
        migliore["score"]
        - secondo_score
    )

    # Contenimento forte: score 0,97.
    # Fuzzy: almeno 0,90 e margine netto sul secondo.
    if (
        migliore["score"] >= soglia_fuzzy
        and
        margine >= margine_minimo
    ):

        return {
            "foglio": migliore["foglio"],
            "score": migliore["score"],
            "metodo": migliore["metodo"],
            "ambiguo": False,
        }

    return {
        "foglio": None,
        "score": migliore["score"],
        "metodo": "corrispondenza non sufficientemente sicura",
        "ambiguo": (
            margine < margine_minimo
        ),
    }


def score_match_foglio(
    progetto,
    foglio,
    team=None,
):
    """
    Wrapper mantenuto per compatibilità con il resto
    dell'applicazione. Usa la nuova metrica rigorosa.
    """

    if team is not None:

        tipo = tipo_sal_da_nome(
            foglio
        )

        team_norm = normalizza_team(
            team
        )

        # Il team deve essere coerente. Il controllo è
        # volutamente rigido per evitare associazioni incrociate.
        if (
            team_norm in {
                "EPAL",
                "MGIO",
                "EPAL+MGIO",
            }
            and tipo != team_norm
        ):
            return 0.0

    return metriche_match_progetto_sal(
        progetto,
        foglio,
    )["score"]


def trova_foglio_sal_migliore(
    progetto,
    team,
    sheet_names,
):
    """
    Utilizzata nella vista Dettaglio progetto.

    Cerca prima il SAL dello stesso team. Per EPAL/MGIO,
    se non esiste un'associazione sicura, prova un eventuale
    SAL combinato. Non effettua mai un fallback indiscriminato
    su fogli di altri progetti/team.
    """

    team_norm = normalizza_team(
        team
    )

    risultato = trova_foglio_sal_rigoroso(
        progetto=progetto,
        team=team_norm,
        sheet_names=sheet_names,
        tipi_ammessi={team_norm}
        if team_norm in {
            "EPAL",
            "MGIO",
            "EPAL+MGIO",
        }
        else None,
    )

    if risultato["foglio"] is not None:

        return (
            risultato["foglio"],
            risultato["score"],
        )

    # Fallback controllato per un progetto individuale che
    # disponga soltanto del SAL combinato.
    if team_norm in {
        "EPAL",
        "MGIO",
    }:

        risultato_combinato = (
            trova_foglio_sal_rigoroso(
                progetto=progetto,
                team="EPAL+MGIO",
                sheet_names=sheet_names,
                tipi_ammessi={
                    "EPAL+MGIO"
                },
            )
        )

        if (
            risultato_combinato["foglio"]
            is not None
        ):

            return (
                risultato_combinato["foglio"],
                risultato_combinato["score"],
            )

    return (
        None,
        risultato["score"],
    )


# ============================================================
# GIORNI DI PROGETTO PER LE VISTE DI PORTAFOGLIO
# ============================================================

def miglior_sal_con_giorni(
    progetto,
    team,
    fogli,
    sheet_names,
    tipi_ammessi=None,
    fogli_esclusi=None,
):
    """
    Individua prima il foglio SAL con il matching rigoroso
    e soltanto dopo calcola i giorni.

    Se il nome non è sufficientemente sicuro, restituisce
    None e la vista mostra N/D: è preferibile un N/D a
    riportare i giorni di un altro progetto.
    """

    risultato_match = trova_foglio_sal_rigoroso(
        progetto=progetto,
        team=team,
        sheet_names=sheet_names,
        tipi_ammessi=tipi_ammessi,
        fogli_esclusi=fogli_esclusi,
    )

    nome_foglio = risultato_match[
        "foglio"
    ]

    if (
        nome_foglio is None
        or nome_foglio not in fogli
    ):
        return None

    riepilogo = calcola_giorni_progetto(
        fogli[nome_foglio],
        nome_foglio,
    )

    if not riepilogo["disponibile"]:
        return None

    return {
        "foglio": nome_foglio,
        "score": risultato_match["score"],
        "metodo": risultato_match["metodo"],
        "giorni_fatti":
            riepilogo["giorni_fatti"],
        "giorni_da_fare":
            riepilogo["giorni_da_fare"],
        "giorni_totali":
            riepilogo["giorni_totali"],
    }


def arricchisci_portafoglio_con_giorni_sal(
    df,
    fogli,
    sheet_names,
):
    """
    Completa Fatto e Da fare usando associazioni SAL rigorose.

    Regole Executive:
    - EPAL -> giorni del SAL EPAL del progetto;
    - MGIO -> giorni del SAL MGIO del progetto;
    - EPAL+MGIO -> UNA SOLA riga, con:
          giorni fatti EPAL + giorni fatti MGIO
          giorni da fare EPAL + giorni da fare MGIO
    - per un progetto condiviso la somma viene eseguita solo
      se entrambe le componenti sono state associate in modo
      sicuro;
    - se manca una componente viene provato il SAL combinato;
    - lo stesso foglio SAL non può essere attribuito a due
      progetti diversi nella stessa elaborazione;
    - una coppia parziale di giorni non viene mai mostrata:
      o sono disponibili entrambi, oppure entrambi diventano N/D.
    """

    if (
        df is None
        or df.empty
    ):
        return df

    out = df.copy()

    if "Fatto" not in out.columns:
        out["Fatto"] = float("nan")

    if "Da fare" not in out.columns:
        out["Da fare"] = float("nan")

    out["Fonte giorni"] = ""
    out["Foglio SAL giorni"] = ""
    out["Metodo associazione giorni"] = ""
    out["Score associazione giorni"] = float("nan")

    # Impedisce il riutilizzo dello stesso SAL per due progetti
    # diversi all'interno della stessa vista di portafoglio.
    fogli_usati = set()

    for idx, riga in out.iterrows():

        fatto_gantt = riga.get(
            "Fatto",
            float("nan")
        )

        da_fare_gantt = riga.get(
            "Da fare",
            float("nan")
        )

        # Se il GANTT contiene già una coppia completa,
        # la coppia rimane autorevole.
        if (
            pd.notna(fatto_gantt)
            and
            pd.notna(da_fare_gantt)
        ):

            out.at[idx, "Fonte giorni"] = "GANTT"
            out.at[
                idx,
                "Metodo associazione giorni"
            ] = "giorni già presenti nel GANTT"

            continue

        # Una coppia parziale non deve restare visibile.
        out.at[idx, "Fatto"] = float("nan")
        out.at[idx, "Da fare"] = float("nan")

        progetto = riga["Progetto"]

        team = normalizza_team(
            riga.get(
                "Team",
                "N/D"
            )
        )

        risultato = None

        # ====================================================
        # PROGETTO CONDIVISO EPAL+MGIO
        # ====================================================

        if team == "EPAL+MGIO":

            risultato_epal = (
                miglior_sal_con_giorni(
                    progetto=progetto,
                    team="EPAL",
                    fogli=fogli,
                    sheet_names=sheet_names,
                    tipi_ammessi={"EPAL"},
                    fogli_esclusi=fogli_usati,
                )
            )

            esclusi_mgio = set(
                fogli_usati
            )

            if risultato_epal is not None:
                esclusi_mgio.add(
                    risultato_epal["foglio"]
                )

            risultato_mgio = (
                miglior_sal_con_giorni(
                    progetto=progetto,
                    team="MGIO",
                    fogli=fogli,
                    sheet_names=sheet_names,
                    tipi_ammessi={"MGIO"},
                    fogli_esclusi=esclusi_mgio,
                )
            )

            # Somma solo con entrambe le componenti sicure.
            if (
                risultato_epal is not None
                and
                risultato_mgio is not None
            ):

                risultato = {
                    "giorni_fatti": (
                        risultato_epal["giorni_fatti"]
                        +
                        risultato_mgio["giorni_fatti"]
                    ),
                    "giorni_da_fare": (
                        risultato_epal["giorni_da_fare"]
                        +
                        risultato_mgio["giorni_da_fare"]
                    ),
                    "giorni_totali": (
                        risultato_epal["giorni_totali"]
                        +
                        risultato_mgio["giorni_totali"]
                    ),
                    "foglio": (
                        risultato_epal["foglio"]
                        + " + "
                        + risultato_mgio["foglio"]
                    ),
                    "fogli_utilizzati": [
                        risultato_epal["foglio"],
                        risultato_mgio["foglio"],
                    ],
                    "score": min(
                        risultato_epal["score"],
                        risultato_mgio["score"],
                    ),
                    "metodo": (
                        "somma SAL EPAL + MGIO; "
                        + risultato_epal["metodo"]
                        + " / "
                        + risultato_mgio["metodo"]
                    ),
                }

            else:

                # Fallback: usa un SAL combinato solo se anche
                # questo è associato in modo rigoroso.
                risultato_combinato = (
                    miglior_sal_con_giorni(
                        progetto=progetto,
                        team="EPAL+MGIO",
                        fogli=fogli,
                        sheet_names=sheet_names,
                        tipi_ammessi={
                            "EPAL+MGIO"
                        },
                        fogli_esclusi=fogli_usati,
                    )
                )

                if risultato_combinato is not None:

                    risultato = {
                        **risultato_combinato,
                        "fogli_utilizzati": [
                            risultato_combinato[
                                "foglio"
                            ]
                        ],
                    }

        # ====================================================
        # PROGETTO EPAL
        # ====================================================

        elif team == "EPAL":

            risultato_epal = (
                miglior_sal_con_giorni(
                    progetto=progetto,
                    team="EPAL",
                    fogli=fogli,
                    sheet_names=sheet_names,
                    tipi_ammessi={"EPAL"},
                    fogli_esclusi=fogli_usati,
                )
            )

            if risultato_epal is not None:

                risultato = {
                    **risultato_epal,
                    "fogli_utilizzati": [
                        risultato_epal["foglio"]
                    ],
                }

            else:

                risultato_combinato = (
                    miglior_sal_con_giorni(
                        progetto=progetto,
                        team="EPAL+MGIO",
                        fogli=fogli,
                        sheet_names=sheet_names,
                        tipi_ammessi={
                            "EPAL+MGIO"
                        },
                        fogli_esclusi=fogli_usati,
                    )
                )

                if risultato_combinato is not None:

                    risultato = {
                        **risultato_combinato,
                        "fogli_utilizzati": [
                            risultato_combinato[
                                "foglio"
                            ]
                        ],
                    }

        # ====================================================
        # PROGETTO MGIO
        # ====================================================

        elif team == "MGIO":

            risultato_mgio = (
                miglior_sal_con_giorni(
                    progetto=progetto,
                    team="MGIO",
                    fogli=fogli,
                    sheet_names=sheet_names,
                    tipi_ammessi={"MGIO"},
                    fogli_esclusi=fogli_usati,
                )
            )

            if risultato_mgio is not None:

                risultato = {
                    **risultato_mgio,
                    "fogli_utilizzati": [
                        risultato_mgio["foglio"]
                    ],
                }

            else:

                risultato_combinato = (
                    miglior_sal_con_giorni(
                        progetto=progetto,
                        team="EPAL+MGIO",
                        fogli=fogli,
                        sheet_names=sheet_names,
                        tipi_ammessi={
                            "EPAL+MGIO"
                        },
                        fogli_esclusi=fogli_usati,
                    )
                )

                if risultato_combinato is not None:

                    risultato = {
                        **risultato_combinato,
                        "fogli_utilizzati": [
                            risultato_combinato[
                                "foglio"
                            ]
                        ],
                    }

        # ====================================================
        # TEAM NON RICONOSCIUTO
        # ====================================================

        else:

            # Nessun fuzzy indiscriminato su tutti i SAL.
            # Se il team non è riconoscibile, il dato rimane N/D.
            risultato = None

        # ====================================================
        # AGGIORNAMENTO RIGA
        # ====================================================

        if risultato is not None:

            out.at[idx, "Fatto"] = (
                risultato["giorni_fatti"]
            )

            out.at[idx, "Da fare"] = (
                risultato["giorni_da_fare"]
            )

            out.at[idx, "Fonte giorni"] = "SAL"

            out.at[
                idx,
                "Foglio SAL giorni"
            ] = risultato["foglio"]

            out.at[
                idx,
                "Metodo associazione giorni"
            ] = risultato.get(
                "metodo",
                "match rigoroso"
            )

            out.at[
                idx,
                "Score associazione giorni"
            ] = risultato.get(
                "score",
                float("nan")
            )

            for nome_foglio in risultato.get(
                "fogli_utilizzati",
                []
            ):
                fogli_usati.add(
                    nome_foglio
                )

        else:

            out.at[
                idx,
                "Fonte giorni"
            ] = "N/D"

            out.at[
                idx,
                "Metodo associazione giorni"
            ] = (
                "nessun SAL associato con sufficiente sicurezza"
            )

    return out


# ============================================================
# DETTAGLIO ATTIVITÀ
# ============================================================

def costruisci_attivita(
    df,
    nome_foglio,
):

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

    (
        col_fatto,
        col_da_fare,
        _
    ) = trova_colonne_giorni_sal(
        df,
        nome_foglio
    )

    if col_attivita is None:
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "Attività":
                df[col_attivita]
        }
    )

    out[
        "Attività"
    ] = (
        out[
            "Attività"
        ]
        .astype(str)
        .str.strip()
    )

    mask = (
        mask_righe_attivita_valide(
            df,
            col_attivita
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
            .clip(
                lower=0
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
            .clip(
                lower=0
            )
        )

    else:

        out["Da fare"] = float(
            "nan"
        )

    if col_sal is not None:

        out[
            "SAL sorgente"
        ] = (
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

        out[
            "SAL sorgente"
        ] = (
            out["Fatto"]
            .div(
                denominatore.where(
                    denominatore > 0
                )
            )
            * 100
        )

    out["SAL"] = (
        out[
            "SAL sorgente"
        ]
        .clip(
            lower=0,
            upper=100
        )
    )

    out[
        "Anomalia SAL"
    ] = (
        (
            out[
                "SAL sorgente"
            ] < 0
        )
        |
        (
            out[
                "SAL sorgente"
            ] > 100
        )
    )

    out["Stato"] = (
        out["SAL"]
        .apply(
            stato_da_sal
        )
    )

    return (
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


# ============================================================
# GRAFICO RANKING
# ============================================================

def grafico_ranking(
    df,
    titolo,
    ordinamento="SAL decrescente"
):

    plot_df = (
        df
        .dropna(
            subset=[
                "SAL"
            ]
        )
        .copy()
    )

    plot_df = ordina_portafoglio(
        plot_df,
        ordinamento
    )

    if plot_df.empty:

        st.info(
            "Nessun SAL disponibile."
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

    ordine_progetti = (
        plot_df[
            "Progetto"
        ]
        .tolist()
    )

    fig = px.bar(
        plot_df,
        x="SAL",
        y="Progetto",
        orientation="h",
        color="Stato",
        color_discrete_map=COLORI_STATO,
        category_orders={
            "Stato":
                ORDINE_STATI,
            "Progetto":
                ordine_progetti,
        },
        text="Etichetta SAL",
        custom_data=[
            "Team",
            "SAL sorgente display",
            "Stato",
            "Stato sorgente",
            "Anomalia SAL",
        ],
        labels={
            "SAL":
                "Avanzamento",
            "Progetto":
                "",
            "Stato":
                "Stato",
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
            for x
            in range(
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
        autorange="reversed",
        categoryorder="array",
        categoryarray=ordine_progetti,
    )

    fig.update_layout(
        height=max(
            430,
            len(
                plot_df
            ) * 38
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
            "Stato dashboard: %{customdata[2]}<br>"
            "Stato GANTT: %{customdata[3]}<br>"
            "Anomalia SAL: %{customdata[4]}"
            "<extra></extra>"
        ),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config=PLOTLY_CONFIG,
    )


# ============================================================
# DISTRIBUZIONE STATI
# ============================================================

def grafico_distribuzione_stati(
    df
):

    conteggi = (
        df["Stato"]
        .value_counts()
        .reindex(
            ORDINE_STATI,
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
        category_orders={
            "Stato":
                ORDINE_STATI
        },
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
# CONFRONTO TEAM
# ============================================================

def grafico_confronto_team(df):

    ordine_team = [
        "EPAL",
        "MGIO",
        "EPAL+MGIO",
    ]

    righe = []

    for team in ordine_team:

        team_df = df[
            (
                df["Team"]
                == team
            )
            &
            (
                df["Stato"]
                != "Completato"
            )
        ].copy()

        if team_df.empty:
            continue

        sal, metodo = (
            portfolio_sal(
                team_df
            )
        )

        righe.append(
            {
                "Team":
                    team,
                "SAL":
                    sal,
                "Metodo":
                    metodo,
                "Progetti in corso":
                    len(
                        team_df
                    ),
            }
        )

    confronto = (
        pd.DataFrame(
            righe
        )
    )

    if confronto.empty:

        st.info(
            "Confronto dei progetti "
            "in corso non disponibile."
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
            "Progetti in corso",
        ],
        title=(
            "Confronto SAL progetti "
            "in corso per portafoglio"
        ),
        labels={
            "SAL":
                "SAL progetti in corso",
            "Team":
                "",
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
            "SAL progetti in corso: %{x:.1f}%<br>"
            "Progetti in corso: %{customdata[1]}<br>"
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

    long_df = (
        validi.melt(
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
            "Percentuale":
                "SAL",
            "Progetto":
                "",
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
            for x
            in range(
                0,
                101,
                10
            )
        ],
    )

    fig.update_layout(
        height=max(
            430,
            len(
                validi
            ) * 50
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
        category_orders={
            "Stato":
                ORDINE_STATI
        },
        text="Etichetta",
        title=(
            f"Avanzamento attività — "
            f"{progetto}"
        ),
        labels={
            "SAL":
                "SAL",
            "Attività":
                "",
            "Stato":
                "Stato",
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
            for x
            in range(
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
            len(
                plot_df
            ) * 40
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
# BARRA FATTO / DA FARE
# ============================================================

def grafico_ripartizione_lavoro(
    pct_fatti,
    pct_da_fare,
):

    if (
        pd.isna(
            pct_fatti
        )
        or
        pd.isna(
            pct_da_fare
        )
    ):
        return

    df_progress = pd.DataFrame(
        {
            "Voce": [
                "Lavoro complessivo",
                "Lavoro complessivo",
            ],
            "Stato": [
                "Fatto",
                "Da fare",
            ],
            "Percentuale": [
                pct_fatti,
                pct_da_fare,
            ],
            "Etichetta": [
                (
                    "Fatto "
                    + formatta_percentuale(
                        pct_fatti
                    )
                ),
                (
                    "Da fare "
                    + formatta_percentuale(
                        pct_da_fare
                    )
                ),
            ],
        }
    )

    fig = px.bar(
        df_progress,
        x="Percentuale",
        y="Voce",
        color="Stato",
        orientation="h",
        barmode="stack",
        text="Etichetta",
        color_discrete_map=(
            COLORI_RIPARTIZIONE
        ),
        title=(
            "Ripartizione complessiva "
            "del lavoro"
        ),
        labels={
            "Percentuale":
                "",
            "Voce":
                "",
            "Stato":
                "",
        },
    )

    fig.update_xaxes(
        range=[
            0,
            100
        ],
        tickmode="array",
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

    fig.update_yaxes(
        showticklabels=False,
        title=None,
    )

    fig.update_traces(
        textposition="inside",
        insidetextanchor="middle",
        hovertemplate=(
            "<b>%{fullData.name}</b><br>"
            "%{x:.1f}%"
            "<extra></extra>"
        ),
    )

    fig.update_layout(
        height=245,
        margin=dict(
            l=20,
            r=20,
            t=60,
            b=35,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
        ),
        uniformtext_minsize=10,
        uniformtext_mode="hide",
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
        "Stato sorgente",
        "Fatto",
        "Da fare",
    ]

    if (
        "SAL atteso"
        in df.columns
        and
        df[
            "SAL atteso"
        ]
        .notna()
        .any()
    ):

        colonne += [
            "SAL atteso",
            "Scostamento",
        ]

    tabella = df[
        colonne
    ].copy()

    # I giorni vengono visualizzati in forma testuale
    # così, se un dato non è disponibile, compare N/D
    # invece di None.
    tabella[
        "Fatto"
    ] = (
        tabella[
            "Fatto"
        ]
        .apply(
            formatta_numero
        )
    )

    tabella[
        "Da fare"
    ] = (
        tabella[
            "Da fare"
        ]
        .apply(
            formatta_numero
        )
    )

    configurazione = {

        "SAL":
            st.column_config.ProgressColumn(
                "SAL",
                min_value=0,
                max_value=100,
                format="%.1f%%",
            ),

        "Stato sorgente":
            st.column_config.TextColumn(
                "Stato GANTT"
            ),

        "Fatto":
            st.column_config.TextColumn(
                "Giorni fatti"
            ),

        "Da fare":
            st.column_config.TextColumn(
                "Giorni da fare"
            ),
    }

    if (
        "SAL atteso"
        in tabella.columns
    ):

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
                "Scostamento (p.p.)",
                format="%.1f",
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

header_left, header_right = (
    st.columns(
        [
            6,
            1
        ]
    )
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
# CARICAMENTO DATI
# ============================================================

try:

    with st.spinner(
        "Caricamento dati dal Dashboard..."
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

gantt_combinato_nome = (
    trova_gantt_combinato(
        sheet_names
    )
)


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


if st.sidebar.button(
    "🔄 Aggiorna dati",
    use_container_width=True,
):

    st.cache_data.clear()

    st.rerun()


vista = st.sidebar.radio(
    "Vista",
    [
        "Executive",
        "Avanzamento",
        "Dettaglio progetto",
        "Dati sorgente",
    ],
)


# ============================================================
# PORTAFOGLIO EPAL
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
# PORTAFOGLIO MGIO
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
# ARRICCHIMENTO EPAL E MGIO CON I GIORNI DEI SAL
# ============================================================
#
# I giorni vengono ricavati dai fogli SAL e portati a livello
# di portafoglio, così possono essere visualizzati anche nelle
# viste Executive e Avanzamento.
# ============================================================

portfolio_epal = (
    arricchisci_portafoglio_con_giorni_sal(
        portfolio_epal,
        fogli,
        sheet_names,
    )
)


portfolio_mgio = (
    arricchisci_portafoglio_con_giorni_sal(
        portfolio_mgio,
        fogli,
        sheet_names,
    )
)


# ============================================================
# FALLBACK EPAL + MGIO
# ============================================================

portfolio_concat = pd.concat(
    [
        portfolio_epal,
        portfolio_mgio,
    ],
    ignore_index=True,
)


# ============================================================
# GANTT COMBINATO
# ============================================================

if gantt_combinato_nome is not None:

    portfolio_da_gantt_combinato = (
        costruisci_portafoglio_combinato(
            fogli[
                gantt_combinato_nome
            ],
            portfolio_epal,
            portfolio_mgio,
            gantt_combinato_nome,
        )
    )

else:

    portfolio_da_gantt_combinato = (
        pd.DataFrame()
    )


# ============================================================
# PORTAFOGLIO TUTTI - EPAL+MGIO
# ============================================================
#
# Una sola riga per ogni progetto MiniPIA distinto.
# ============================================================

if not portfolio_da_gantt_combinato.empty:

    portfolio_base_tutti = (
        portfolio_da_gantt_combinato
        .copy()
    )

else:

    portfolio_base_tutti = (
        portfolio_concat
        .copy()
    )


portfolio_tutti = (
    consolida_progetti_univoci(
        portfolio_base_tutti
    )
)


portfolio_tutti = (
    aggiungi_flag_condiviso(
        portfolio_tutti,
        portfolio_epal,
        portfolio_mgio,
    )
)


# ============================================================
# GIORNI DEL PORTAFOGLIO CONSOLIDATO
# ============================================================
#
# Per i progetti condivisi EPAL+MGIO la vista complessiva
# contiene una sola riga di progetto, ma i giorni sono la somma
# delle componenti EPAL e MGIO.
# ============================================================

portfolio_tutti = (
    arricchisci_portafoglio_con_giorni_sal(
        portfolio_tutti,
        fogli,
        sheet_names,
    )
)


# ============================================================
# SCELTA PORTAFOGLIO
# ============================================================

scope_options = [
    "Tutti - EPAL+MGIO",
    "EPAL",
    "MGIO",
]

if (
    not portfolio_tutti.empty
    and
    (
        portfolio_tutti[
            "Team"
        ]
        == "EPAL+MGIO"
    ).any()
):

    scope_options.append(
        "EPAL+MGIO"
    )


scope = st.sidebar.radio(
    "Portfolio",
    scope_options,
)


if scope == "EPAL":

    portfolio = (
        portfolio_epal.copy()
    )

elif scope == "MGIO":

    portfolio = (
        portfolio_mgio.copy()
    )

elif scope == "EPAL+MGIO":

    portfolio = (
        portfolio_tutti[
            portfolio_tutti[
                "Team"
            ]
            == "EPAL+MGIO"
        ]
        .copy()
    )

else:

    portfolio = (
        portfolio_tutti.copy()
    )


# ============================================================
# FILTRI
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


    ricerca = st.sidebar.text_input(
        "🔎 Cerca progetto",
        key=f"ricerca_{scope}",
    )


    filtro_stato = (
        st.sidebar.selectbox(
            "Stato",
            [
                "Tutti",
                "In stato iniziale",
                "In stato intermedio",
                "In stato avanzato",
                "Completato",
            ],
            key=f"stato_{scope}",
        )
    )


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
            key=f"range_sal_{scope}",
        )
    )


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


    if filtro_stato != "Tutti":

        portfolio_filtrato = (
            portfolio_filtrato[
                portfolio_filtrato[
                    "Stato"
                ]
                == filtro_stato
            ]
        )


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


    # ========================================================
    # PROGETTI IN CORSO
    # ========================================================

    portfolio_in_corso = (
        portfolio_filtrato[
            portfolio_filtrato[
                "Stato"
            ]
            != "Completato"
        ]
        .copy()
    )


    (
        sal_progetti_in_corso,
        metodo_sal
    ) = portfolio_sal(
        portfolio_in_corso
    )


    # ========================================================
    # KPI
    # ========================================================

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


    stato_iniziale = int(
        (
            portfolio_filtrato[
                "Stato"
            ]
            == "In stato iniziale"
        )
        .sum()
    )


    stato_intermedio = int(
        (
            portfolio_filtrato[
                "Stato"
            ]
            == "In stato intermedio"
        )
        .sum()
    )


    stato_avanzato = int(
        (
            portfolio_filtrato[
                "Stato"
            ]
            == "In stato avanzato"
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


    k1, k2, k3, k4, k5, k6 = (
        st.columns(6)
    )


    k1.metric(
        "Progetti",
        totale_progetti
    )


    k2.metric(
        "SAL progetti in corso",
        formatta_percentuale(
            sal_progetti_in_corso
        ),
    )


    k3.metric(
        "Completati",
        completati
    )


    k4.metric(
        "In stato iniziale",
        stato_iniziale
    )


    k5.metric(
        "In stato intermedio",
        stato_intermedio
    )


    k6.metric(
        "In stato avanzato",
        stato_avanzato
    )


    st.caption(
        "Metodo SAL progetti in corso: "
        f"{metodo_sal}."
    )


    # ========================================================
    # ANOMALIE SAL
    # ========================================================

    if anomalie > 0:

        st.warning(
            f"Rilevati {anomalie} valori SAL "
            "fuori dall'intervallo 0–100%. "
            "Nei grafici la barra viene limitata "
            "a 0–100%, mentre il valore sorgente "
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
                        "Stato",
                        "Stato sorgente",
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
    # ORDINAMENTO EXECUTIVE
    # ========================================================

    ordinamento_executive = st.radio(
        "Ordinamento",
        [
            "SAL crescente",
            "SAL decrescente",
            "Nome progetto",
        ],
        index=1,
        horizontal=True,
        key=(
            f"ordinamento_executive_"
            f"{scope}"
        ),
    )


    portfolio_executive_ordinato = (
        ordina_portafoglio(
            portfolio_filtrato,
            ordinamento_executive
        )
    )


    # ========================================================
    # RANKING
    # ========================================================

    grafico_ranking(
        portfolio_filtrato,
        "Avanzamento dei progetti",
        ordinamento=(
            ordinamento_executive
        ),
    )


    # ========================================================
    # DISTRIBUZIONE + CONFRONTO
    # ========================================================

    col_left, col_right = (
        st.columns(2)
    )


    with col_left:

        grafico_distribuzione_stati(
            portfolio_filtrato
        )


    with col_right:

        if (
            scope
            == "Tutti - EPAL+MGIO"
        ):

            grafico_confronto_team(
                portfolio_filtrato
            )

        else:

            fatto = (
                portfolio_in_corso[
                    "Fatto"
                ]
                .dropna()
                .sum()
            )

            residuo = (
                portfolio_in_corso[
                    "Da fare"
                ]
                .dropna()
                .sum()
            )


            if (
                fatto > 0
                or residuo > 0
            ):

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
                        "Carico di lavoro "
                        "dei progetti in corso"
                    ),
                    text="Giorni",
                )


                fig.update_traces(
                    texttemplate=(
                        "%{text:.1f}"
                    ),
                    textposition=(
                        "outside"
                    ),
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

            else:

                st.info(
                    "I giorni fatti / da fare "
                    "non sono disponibili nel "
                    "GANTT di portafoglio. "
                    "Sono comunque calcolati "
                    "nei singoli fogli SAL."
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
                    "In stato iniziale",
                    "In stato intermedio",
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

        priorita_tabella = (
            priorita[
                [
                    "Progetto",
                    "Team",
                    "SAL",
                    "Stato",
                    "Fatto",
                    "Da fare",
                ]
            ]
            .copy()
        )

        priorita_tabella[
            "Fatto"
        ] = (
            priorita_tabella[
                "Fatto"
            ]
            .apply(
                formatta_numero
            )
        )

        priorita_tabella[
            "Da fare"
        ] = (
            priorita_tabella[
                "Da fare"
            ]
            .apply(
                formatta_numero
            )
        )

        st.dataframe(
            priorita_tabella,
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
                    st.column_config.TextColumn(
                        "Giorni fatti"
                    ),

                "Da fare":
                    st.column_config.TextColumn(
                        "Giorni da fare"
                    ),
            },
        )


    # ========================================================
    # SAL REALE VS ATTESO
    # ========================================================

    if (
        "SAL atteso"
        in portfolio_filtrato.columns
        and
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
            portfolio_executive_ordinato
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
        portfolio_executive_ordinato
    )


    st.download_button(
        "⬇️ Scarica portafoglio filtrato in CSV",
        data=csv_bytes(
            portfolio_executive_ordinato
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
        index=1,
        horizontal=True,
        key=(
            f"ordinamento_avanzamento_"
            f"{scope}"
        ),
    )


    avanzamento = (
        portfolio_filtrato.copy()
    )


    avanzamento_ordinato = (
        ordina_portafoglio(
            avanzamento,
            ordinamento
        )
    )


    grafico_ranking(
        avanzamento,
        "Ranking SAL",
        ordinamento=ordinamento,
    )


    st.markdown(
        "---"
    )


    tabella_portafoglio(
        avanzamento_ordinato
    )


    if (
        "SAL atteso"
        in avanzamento.columns
        and
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
            avanzamento_ordinato
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
        options["Progetto"]
        + " · "
        + options["Team"]
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
            "Dati del progetto "
            "non disponibili."
        )

        st.stop()


    riepilogo = (
        righe_progetto.iloc[0]
    )


    (
        foglio_auto,
        score
    ) = trova_foglio_sal_migliore(
        progetto,
        team,
        sheet_names,
    )


    candidati_sal = (
        lista_fogli_sal(
            sheet_names,
            team
        )
    )


    if not candidati_sal:

        candidati_sal = (
            lista_fogli_sal(
                sheet_names,
                None
            )
        )


    st.subheader(
        progetto
    )


    st.caption(
        f"Team: {team}"
    )


    if not candidati_sal:

        st.warning(
            "Non è stato individuato "
            "alcun foglio SAL compatibile."
        )

        st.stop()


    default_idx = 0


    if (
        foglio_auto is not None
        and
        foglio_auto
        in candidati_sal
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
            "La dashboard associa "
            "automaticamente il foglio SAL "
            "più compatibile con il progetto. "
            "È possibile modificare "
            "manualmente la selezione."
        ),
    )


    if (
        foglio_auto is not None
        and
        score >= 0.30
    ):

        st.caption(
            "Associazione automatica: "
            f"{foglio_auto}"
        )

    elif score < 0.30:

        st.info(
            "L'associazione automatica "
            "ha una corrispondenza limitata. "
            "Verifica il foglio SAL selezionato."
        )


    df_sal = fogli[
        foglio_sal
    ]


    riepilogo_giorni = (
        calcola_giorni_progetto(
            df_sal,
            foglio_sal,
        )
    )


    if not riepilogo_giorni[
        "disponibile"
    ]:

        fallback_gantt = (
            calcola_giorni_da_gantt(
                riepilogo[
                    "Fatto"
                ],
                riepilogo[
                    "Da fare"
                ],
            )
        )

        if fallback_gantt is not None:

            riepilogo_giorni = (
                fallback_gantt
            )


    sal_gantt = (
        riepilogo["SAL"]
    )


    if (
        pd.isna(
            sal_gantt
        )
        and
        riepilogo_giorni[
            "disponibile"
        ]
    ):

        sal_visualizzato = (
            riepilogo_giorni[
                "pct_fatti"
            ]
        )

    else:

        sal_visualizzato = (
            sal_gantt
        )


    stato_visualizzato = (
        riepilogo[
            "Stato"
        ]
    )


    p1, p2, p3, p4 = (
        st.columns(4)
    )


    p1.metric(
        "SAL progetto",
        formatta_percentuale(
            sal_visualizzato
        ),
    )


    p2.metric(
        "Giorni fatti",
        formatta_numero(
            riepilogo_giorni[
                "giorni_fatti"
            ]
        ),
    )


    p3.metric(
        "Giorni da fare",
        formatta_numero(
            riepilogo_giorni[
                "giorni_da_fare"
            ]
        ),
    )


    p4.metric(
        "Stato",
        stato_visualizzato,
    )


    q1, q2 = (
        st.columns(2)
    )


    q1.metric(
        "✅ Percentuale complessiva giorni fatti",
        formatta_percentuale(
            riepilogo_giorni[
                "pct_fatti"
            ]
        ),
    )


    q2.metric(
        "⏳ Percentuale complessiva giorni da fare",
        formatta_percentuale(
            riepilogo_giorni[
                "pct_da_fare"
            ]
        ),
    )


    if riepilogo_giorni[
        "disponibile"
    ]:

        st.caption(
            "Calcolo effettuato su "
            f"{formatta_numero(riepilogo_giorni['giorni_totali'])} "
            "giorni complessivi "
            f"({riepilogo_giorni['fonte']})."
        )

    else:

        st.warning(
            "Non è stato possibile individuare "
            "una coppia valida di valori "
            "'giorni fatti / giorni da fare' "
            "nel SAL selezionato."
        )


    if riepilogo_giorni[
        "disponibile"
    ]:

        grafico_ripartizione_lavoro(
            riepilogo_giorni[
                "pct_fatti"
            ],
            riepilogo_giorni[
                "pct_da_fare"
            ],
        )


    if (
        pd.notna(
            sal_gantt
        )
        and
        riepilogo_giorni[
            "disponibile"
        ]
    ):

        sal_calcolato = (
            riepilogo_giorni[
                "pct_fatti"
            ]
        )


        scostamento_coerenza = (
            sal_gantt
            - sal_calcolato
        )


        if (
            abs(
                scostamento_coerenza
            )
            >
            TOLLERANZA_COHERENZA_SAL
        ):

            st.warning(
                "⚠️ Il SAL riportato nel GANTT "
                "non coincide con il SAL calcolato "
                "sui giorni del progetto. "
                f"SAL GANTT: "
                f"{formatta_percentuale(sal_gantt)} · "
                f"SAL calcolato: "
                f"{formatta_percentuale(sal_calcolato)} · "
                f"Scostamento: "
                f"{formatta_numero(scostamento_coerenza)} p.p."
            )

        else:

            st.success(
                "✓ SAL del GANTT coerente "
                "con la percentuale calcolata "
                "sui giorni "
                f"(scostamento "
                f"{formatta_numero(scostamento_coerenza)} p.p.)."
            )


    if (
        normalizza_testo(
            riepilogo[
                "Stato sorgente"
            ]
        )
        != ""
    ):

        st.caption(
            "Stato ufficiale nel GANTT: "
            f"{riepilogo['Stato sorgente']}."
        )


    if bool(
        riepilogo[
            "Anomalia SAL"
        ]
    ):

        st.warning(
            "Il valore SAL presente nel "
            "dato sorgente è "
            f"{formatta_percentuale(riepilogo['SAL sorgente'], 2)}. "
            "La rappresentazione grafica "
            "viene limitata a un massimo "
            "del 100%, senza modificare "
            "il dato sorgente."
        )


    if (
        "SAL atteso"
        in riepilogo.index
        and
        pd.notna(
            riepilogo[
                "SAL atteso"
            ]
        )
    ):

        scostamento = (
            riepilogo[
                "Scostamento"
            ]
        )


        st.metric(
            "Scostamento rispetto "
            "al SAL atteso",
            (
                f"{scostamento:+.1f} p.p."
                .replace(
                    ".",
                    ","
                )
            ),
        )


    attivita = (
        costruisci_attivita(
            df_sal,
            foglio_sal,
        )
    )


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
                f"{len(anomalie_attivita)} "
                "attività presentano un SAL "
                "sorgente fuori dall'intervallo "
                "0–100%."
            )

    else:

        st.info(
            "Il foglio SAL selezionato "
            "non contiene una struttura "
            "riconoscibile di attività."
        )


    with st.expander(
        "Visualizza dati sorgente "
        "del foglio SAL"
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


    preferiti = []


    if gantt_combinato_nome is not None:

        preferiti.append(
            gantt_combinato_nome
        )


    for nome in [
        GANTT_EPAL,
        GANTT_MGIO,
    ]:

        if nome in sheet_names:

            preferiti.append(
                nome
            )


    altri = [
        nome
        for nome
        in sheet_names
        if nome not in preferiti
    ]


    elenco = (
        preferiti
        + altri
    )


    foglio_raw = (
        st.selectbox(
            "Foglio da visualizzare",
            elenco
        )
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
