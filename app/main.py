"""Aplikacja sieci sklepów (Streamlit).  Uruchomienie:  streamlit run app/main.py

Każdy laptop to stanowisko jednego sklepu (MOJ_SKLEP w pliku .env):
  WRO – Wrocław, baza Oracle, prowadzi katalog sieci,
  WAW – Warszawa, baza SQL Server, pobiera kopię katalogu z Wrocławia.
"""
import datetime as dt

import pandas as pd
import streamlit as st

import operacje as op
from bazy import NAZWA, SKLEPY, pomiar, status, zapytanie
from config import MOJ_SKLEP

st.set_page_config(page_title="Sieć sklepów", page_icon="🛒", layout="wide")

sklep = MOJ_SKLEP
drugi = "WRO" if sklep == "WAW" else "WAW"
KOLEJNOSC = [sklep, drugi]                      # mój sklep zawsze pierwszy
SILNIK = {"WRO": "Oracle AI Database 26ai Free", "WAW": "SQL Server 2022 Developer"}
NISKI_STAN = 2

# ================================================================ SQL
SQL_STANY = (
    "SELECT t.id_produktu, t.nazwa, k.nazwa AS kategoria, s.cena, "
    "s.ilosc_dostepna, s.ilosc_zarezerwowana "
    "FROM stany_magazynowe s "
    "JOIN typy_produktow t ON t.id_produktu = s.id_produktu "
    "JOIN kategorie k ON k.id_kategorii = t.id_kategorii "
    "WHERE t.aktywny = 1 "
    "ORDER BY t.id_produktu")

SQL_REZERWACJE = (
    "SELECT r.id_rezerwacji, r.sklep_zlecajacy, r.id_produktu, t.nazwa, r.ilosc, "
    "r.klient, r.status, r.data_utworzenia "
    "FROM rezerwacje r JOIN typy_produktow t ON t.id_produktu = r.id_produktu ")

SQL_ZAMOWIENIA = (
    "SELECT z.id_zamowienia, z.data_zamowienia, t.nazwa, p.ilosc, p.cena_jednostkowa, "
    "z.wartosc, z.status "
    "FROM zamowienia z "
    "JOIN pozycje_zamowien p ON p.id_zamowienia = z.id_zamowienia "
    "JOIN typy_produktow t ON t.id_produktu = p.id_produktu "
    "WHERE z.data_zamowienia >= ? AND z.data_zamowienia < ? "
    "ORDER BY z.id_zamowienia DESC")

SQL_KATALOG = ("SELECT t.id_produktu, t.nazwa, k.nazwa AS kategoria, t.opis, t.aktywny{extra} "
               "FROM typy_produktow t JOIN kategorie k ON k.id_kategorii = t.id_kategorii "
               "ORDER BY t.id_produktu")

SQL_LOG = "SELECT TOP {n} czas, zrodlo, status, kategorii, produktow, zmian, komunikat " \
          "FROM log_synchronizacji ORDER BY id_wpisu DESC"

# ================================================================ wygląd tabel
C = st.column_config
KOLUMNY = {
    "sklep":               C.TextColumn("Sklep", width="small"),
    "id_produktu":         C.NumberColumn("ID", width="small", format="%d"),
    "nazwa":               C.TextColumn("Produkt", width="medium"),
    "kategoria":           C.TextColumn("Kategoria", width="small"),
    "opis":                C.TextColumn("Opis", width="medium"),
    "aktywny":             C.CheckboxColumn("Aktywny", width="small"),
    "cena":                C.NumberColumn("Cena", width="small", format="%.2f zł"),
    "cena_jednostkowa":    C.NumberColumn("Cena szt.", width="small", format="%.2f zł"),
    "ilosc_dostepna":      C.NumberColumn("Dostępne", width="small", format="%d"),
    "ilosc_zarezerwowana": C.NumberColumn("Zarezerw.", width="small", format="%d"),
    "data_synchronizacji": C.DatetimeColumn("Pobrano", width="small", format="DD.MM HH:mm:ss"),
    "id_rezerwacji":       C.NumberColumn("Nr", width="small", format="%d"),
    "sklep_zlecajacy":     C.TextColumn("Zlecił", width="small"),
    "ilosc":               C.NumberColumn("Ilość", width="small", format="%d"),
    "klient":              C.TextColumn("Klient", width="medium"),
    "status":              C.TextColumn("Status", width="small"),
    "data_utworzenia":     C.DatetimeColumn("Utworzono", width="small", format="DD.MM.YYYY HH:mm"),
    "data_raportu":        C.DateColumn("Dzień", width="small", format="DD.MM.YYYY"),
    "liczba_zamowien":     C.NumberColumn("Zamówienia", width="small", format="%d"),
    "suma_sprzedazy":      C.NumberColumn("Sprzedaż", width="small", format="%.2f zł"),
    "id_zamowienia":       C.NumberColumn("Nr", width="small", format="%d"),
    "data_zamowienia":     C.DatetimeColumn("Data", width="small", format="DD.MM.YYYY HH:mm"),
    "wartosc":             C.NumberColumn("Wartość", width="small", format="%.2f zł"),
    "czas":                C.DatetimeColumn("Czas", width="small", format="DD.MM HH:mm:ss"),
    "zrodlo":              C.TextColumn("Uruchomił", width="small"),
    "kategorii":           C.NumberColumn("Kategorii", width="small", format="%d"),
    "produktow":           C.NumberColumn("Produktów", width="small", format="%d"),
    "zmian":               C.NumberColumn("Zmian", width="small", format="%d"),
    "komunikat":           C.TextColumn("Komunikat", width="large"),
}
for s in SKLEPY:
    KOLUMNY[f"cena_{s}"] = C.NumberColumn(f"Cena · {NAZWA[s]}", width="small", format="%.2f zł")
    KOLUMNY[f"dost_{s}"] = C.NumberColumn(f"Szt. · {NAZWA[s]}", width="small", format="%d")

LICZBY = ["cena", "cena_jednostkowa", "suma_sprzedazy", "wartosc"]
STATUS_IKONA = {"AKTYWNA": "🟡 aktywna", "ZREALIZOWANA": "🟢 zrealizowana", "ANULOWANA": "⚪ anulowana",
                "NOWE": "🟡 nowe", "ZREALIZOWANE": "🟢 zrealizowane", "ANULOWANE": "⚪ anulowane",
                "OK": "🟢 OK", "BŁĄD": "🔴 błąd"}

st.markdown("""
<style>
  .block-container {padding-top: 2rem; max-width: 1500px;}
  h1 {font-size: 2rem !important;}
  [data-testid="stSidebar"] {min-width: 270px;}
  [data-testid="stMetricValue"] {font-size: 1.8rem;}
</style>""", unsafe_allow_html=True)


# ================================================================ pomocnicze
def przygotuj(df):
    """Ujednolica typy (Oracle i SQL Server zwracają liczby inaczej) i ładniej pokazuje dane."""
    df = df.copy()
    for c in ("id_produktu", "id_rezerwacji", "id_zamowienia", "id_kategorii"):
        if c in df:
            df[c] = pd.to_numeric(df[c]).astype("Int64")
    for c in LICZBY:
        if c in df:
            df[c] = pd.to_numeric(df[c].astype(float))
    if "aktywny" in df:
        df["aktywny"] = df["aktywny"].astype(int).astype(bool)
    for c in ("opis", "klient", "komunikat"):
        if c in df:
            df[c] = df[c].fillna("")
    if "status" in df:
        df["status"] = df["status"].map(lambda x: STATUS_IKONA.get(x, x))
    if "sklep_zlecajacy" in df:
        df["sklep_zlecajacy"] = df["sklep_zlecajacy"].map(lambda x: NAZWA.get(str(x).strip(), x))
    return df


def pokaz(df, blad, pusty="Brak danych"):
    if blad:
        st.warning(blad)
    elif df is None or df.empty:
        st.info(pusty)
    else:
        st.dataframe(przygotuj(df), hide_index=True, column_config=KOLUMNY)


def po_akcji(wynik):
    """Zapamiętuje komunikat i odświeża stronę, żeby tabele pokazały nowe dane."""
    st.session_state["komunikat"] = wynik
    st.rerun()


def dzis():
    poczatek = dt.datetime.combine(dt.date.today(), dt.time())
    return poczatek, poczatek + dt.timedelta(days=1)


def jedna_liczba(s, sql, params=()):
    df, blad = zapytanie(s, sql, params)
    if blad or df is None or df.empty:
        return None
    return df.iloc[0, 0]


def podwidok(etykieta, opcje, klucz):
    return st.radio(etykieta, opcje, horizontal=True, key=klucz, label_visibility="collapsed")


# ================================================================ panel boczny
st.sidebar.markdown(f"## {NAZWA[sklep]}")
st.sidebar.caption(f"{SILNIK[sklep]} · stanowisko ustawione w pliku .env (MOJ_SKLEP={sklep})")
widok = st.sidebar.radio("Widok", [
    "Pulpit", "Produkty w sieci", "Sprzedaż", "Rezerwacje",
    "Magazyn", "Katalog sieci", "Raporty", "Rozproszenie"])
st.sidebar.divider()
st.sidebar.markdown("**Status węzłów**")
DOSTEPNY = {}
for s in KOLEJNOSC:
    DOSTEPNY[s], opis = status(s)
    st.sidebar.markdown(f"{'🟢' if DOSTEPNY[s] else '🔴'} **{NAZWA[s]}** – {opis}")
if st.sidebar.button("↻ Odśwież", use_container_width=True):
    st.rerun()

st.title(f"Sieć sklepów · {NAZWA[sklep]}")
if "komunikat" in st.session_state:
    ok, tekst = st.session_state.pop("komunikat")
    (st.success if ok else st.error)(tekst)


# ================================================================ Pulpit
if widok == "Pulpit":
    od, do = dzis()
    for kol, s in zip(st.columns(2, gap="large"), KOLEJNOSC):
        with kol, st.container(border=True):
            st.markdown(f"### {'🟢' if DOSTEPNY[s] else '🔴'} {NAZWA[s]}{' · mój sklep' if s == sklep else ''}")
            st.caption(SILNIK[s])
            if not DOSTEPNY[s]:
                st.error("Węzeł niedostępny – jego dane są chwilowo niewidoczne, reszta sieci działa dalej.")
                continue
            stan, _ = zapytanie(s, "SELECT COUNT(*), COALESCE(SUM(ilosc_dostepna), 0) FROM stany_magazynowe")
            sprz, _ = zapytanie(s, "SELECT COUNT(*), COALESCE(SUM(wartosc), 0) FROM zamowienia "
                                   "WHERE data_zamowienia >= ? AND data_zamowienia < ? "
                                   "AND status <> 'ANULOWANE'", (od, do))
            rez = jedna_liczba(s, "SELECT COUNT(*) FROM rezerwacje WHERE status = 'AKTYWNA'")
            m1, m2, m3 = st.columns(3)
            if stan is not None:
                m1.metric("Produkty / sztuki", f"{int(stan.iloc[0, 0])} / {int(stan.iloc[0, 1])}")
            if sprz is not None:
                m2.metric("Sprzedaż dziś", f"{float(sprz.iloc[0, 1]):.2f} zł",
                          f"{int(sprz.iloc[0, 0])} zamówień", delta_color="off")
            if rez is not None:
                m3.metric("Aktywne rezerwacje", int(rez))

    c1, c2 = st.columns(2, gap="large")
    with c1, st.container(border=True):
        st.markdown("#### Ostatnie pobranie katalogu (Wrocław → Warszawa)")
        log, blad = zapytanie("WAW", SQL_LOG.format(n=1))
        if blad:
            st.warning(blad)
        elif log is None or log.empty:
            st.info("Katalog nie był jeszcze pobierany z dziennikiem (uruchom 04_log_synchronizacji.sql).")
        else:
            w = log.iloc[0]
            kiedy = pd.to_datetime(w["czas"]).strftime("%d.%m %H:%M:%S")
            if w["status"] == "OK":
                st.success(f"{kiedy} · {w['zrodlo']} · {int(w['produktow'] or 0)} produktów, "
                           f"{int(w['zmian'] or 0)} zmian")
            else:
                st.error(f"{kiedy} · {w['zrodlo']} · błąd: {w['komunikat']}")
    with c2, st.container(border=True):
        st.markdown(f"#### Do zrobienia w sklepie {NAZWA[sklep]}")
        rez, _ = zapytanie(sklep, SQL_REZERWACJE + "WHERE r.status = 'AKTYWNA' ORDER BY r.data_utworzenia")
        niskie, _ = zapytanie(sklep, "SELECT t.nazwa, s.ilosc_dostepna FROM stany_magazynowe s "
                                     "JOIN typy_produktow t ON t.id_produktu = s.id_produktu "
                                     "WHERE t.aktywny = 1 AND s.ilosc_dostepna <= ? "
                                     "ORDER BY s.ilosc_dostepna", (NISKI_STAN,))
        if not DOSTEPNY[sklep]:
            st.error("Mój węzeł jest niedostępny.")
        else:
            n_rez = 0 if rez is None else len(rez)
            st.markdown(f"- **{n_rez}** rezerwacji czeka na wydanie lub anulowanie"
                        + (" → zakładka *Rezerwacje*" if n_rez else ""))
            if niskie is None or niskie.empty:
                st.markdown(f"- żaden produkt nie ma stanu ≤ {NISKI_STAN} szt.")
            else:
                lista = ", ".join(f"{r.nazwa} ({int(r.ilosc_dostepna)})" for r in niskie.itertuples())
                st.markdown(f"- niski stan: {lista} → zakładka *Magazyn*")



# ================================================================ Produkty w sieci
elif widok == "Produkty w sieci":
    st.subheader("Produkty w całej sieci")
    czesci, bledy = [], []
    for s in KOLEJNOSC:
        df, blad = zapytanie(s, SQL_STANY)
        if blad:
            bledy.append(blad)
        elif df is not None and not df.empty:
            df = przygotuj(df)
            czesci.append(df.rename(columns={"cena": f"cena_{s}", "ilosc_dostepna": f"dost_{s}"})
                            .drop(columns=["ilosc_zarezerwowana"]))
    for b in bledy:
        st.warning(b)
    if czesci:
        razem = czesci[0]
        for df in czesci[1:]:
            razem = razem.merge(df, on="id_produktu", how="outer", suffixes=("", "_2"))
            for c in ("nazwa", "kategoria"):
                razem[c] = razem[c].fillna(razem.pop(f"{c}_2"))
        c1, c2, c3 = st.columns([3, 2, 1], vertical_alignment="bottom")
        fraza = c1.text_input("Szukaj produktu", placeholder="np. buty")
        kategoria = c2.selectbox("Kategoria", ["wszystkie"] + sorted(razem["kategoria"].dropna().unique()))
        tylko = c3.checkbox("Tylko dostępne", value=True)
        if fraza:
            razem = razem[razem["nazwa"].str.contains(fraza, case=False, na=False)]
        if kategoria != "wszystkie":
            razem = razem[razem["kategoria"] == kategoria]
        dost = [f"dost_{s}" for s in KOLEJNOSC if f"dost_{s}" in razem]
        if tylko and dost:
            razem = razem[razem[dost].fillna(0).sum(axis=1) > 0]
        kolumny = ["id_produktu", "nazwa", "kategoria"] + [
            c for s in KOLEJNOSC for c in (f"cena_{s}", f"dost_{s}") if c in razem]
        st.dataframe(razem[kolumny].sort_values("nazwa"), hide_index=True, column_config=KOLUMNY)
        st.caption("Puste pole = sklep nie ma tego produktu. Rezerwację złożysz w zakładce *Rezerwacje*.")


# ================================================================ Sprzedaż
elif widok == "Sprzedaż":
    st.subheader(f"Sprzedaż · {NAZWA[sklep]}")
    stany, blad = zapytanie(sklep, SQL_STANY)
    if blad:
        st.error(blad)
    else:
        dostepne = stany[stany["ilosc_dostepna"].astype(int) > 0] if stany is not None else pd.DataFrame()
        if dostepne.empty:
            st.info("Brak towaru do sprzedaży")
        else:
            with st.form("sprzedaz", clear_on_submit=True, border=True):
                opisy = {int(r.id_produktu): f"{r.nazwa}  ·  {float(r.cena):.2f} zł  ·  na stanie {int(r.ilosc_dostepna)}"
                         for r in dostepne.itertuples()}
                c1, c2 = st.columns([4, 1])
                id_p = c1.selectbox("Produkt", list(opisy), format_func=opisy.get)
                ile = c2.number_input("Ilość", min_value=1, step=1)
                if st.form_submit_button("Sprzedaj", type="primary"):
                    po_akcji(op.sprzedaj(sklep, id_p, int(ile)))
    st.markdown("#### Dzisiejsze zamówienia")
    df, blad = zapytanie(sklep, SQL_ZAMOWIENIA, dzis())
    pokaz(df, blad, "Dziś nie było jeszcze sprzedaży")


# ================================================================ Rezerwacje
elif widok == "Rezerwacje":
    st.subheader("Rezerwacje")
    pod = podwidok("Rezerwacje", ["Nowa rezerwacja", f"Do wydania w sklepie {NAZWA[sklep]}",
                                  "Złożone przez mój sklep"], "rez_widok")

    if pod.startswith("Nowa rezerwacja"):
        gdzie = st.radio("Sklep, w którym leży towar", KOLEJNOSC, format_func=NAZWA.get, horizontal=True)
        towar, blad = zapytanie(gdzie, SQL_STANY)
        if blad:
            st.error(f"{blad} – rezerwacja w tym sklepie jest teraz niemożliwa")
        else:
            dostepne = towar[towar["ilosc_dostepna"].astype(int) > 0] if towar is not None else pd.DataFrame()
            if dostepne.empty:
                st.info("Brak dostępnych produktów w tym sklepie")
            else:
                with st.form("rezerwacja", clear_on_submit=True, border=True):
                    opisy = {int(r.id_produktu): f"{r.nazwa}  ·  dostępne {int(r.ilosc_dostepna)}  ·  {float(r.cena):.2f} zł"
                             for r in dostepne.itertuples()}
                    c1, c2, c3 = st.columns([3, 1, 2])
                    id_p = c1.selectbox("Produkt", list(opisy), format_func=opisy.get)
                    ile = c2.number_input("Ilość", min_value=1, step=1)
                    klient = c3.text_input("Klient (imię, telefon)")
                    if st.form_submit_button("Zarezerwuj", type="primary"):
                        po_akcji(op.zarezerwuj(gdzie, sklep, id_p, int(ile), klient.strip() or None))
        if gdzie != sklep:
            st.caption(f"Rezerwacja zapisze się w bazie sklepu {NAZWA[gdzie]} – tam, gdzie leży towar.")

    elif pod.startswith("Do wydania"):
        moje, blad = zapytanie(sklep, SQL_REZERWACJE + "ORDER BY r.data_utworzenia DESC")
        if moje is not None and not moje.empty:
            aktywne = moje[moje["status"] == "AKTYWNA"]
            if aktywne.empty:
                st.success("Nie ma aktywnych rezerwacji do obsłużenia.")
            else:
                with st.container(border=True):
                    opisy = {int(r.id_rezerwacji): f"Nr {int(r.id_rezerwacji)} · {r.nazwa} × {int(r.ilosc)} · "
                                                   f"{r.klient or 'bez nazwiska'} · zlecił: {NAZWA.get(str(r.sklep_zlecajacy).strip())}"
                             for r in aktywne.itertuples()}
                    c1, c2, c3 = st.columns([4, 1, 1], vertical_alignment="bottom")
                    id_r = c1.selectbox("Aktywna rezerwacja", list(opisy), format_func=opisy.get)
                    if c2.button("Wydaj (sprzedaż)", use_container_width=True):
                        po_akcji(op.zrealizuj(sklep, id_r))
                    if c3.button("Anuluj", use_container_width=True):
                        po_akcji(op.anuluj(sklep, id_r))
        pokaz(moje, blad, "Brak rezerwacji towaru z tego sklepu")
        st.caption("Wszystkie rezerwacje towaru z mojego magazynu – także te złożone przez drugi sklep.")

    else:
        for kol, s in zip(st.columns(2, gap="large"), KOLEJNOSC):
            with kol:
                st.markdown(f"**Towar w sklepie: {NAZWA[s]}**")
                df, blad = zapytanie(s, SQL_REZERWACJE + "WHERE r.sklep_zlecajacy = ? "
                                                         "ORDER BY r.data_utworzenia DESC", (sklep,))
                pokaz(df, blad, "Brak rezerwacji")
        st.caption("Moje rezerwacje leżą w dwóch bazach – aplikacja odpytuje obie osobno.")


# ================================================================ Magazyn
elif widok == "Magazyn":
    st.subheader(f"Magazyn · {NAZWA[sklep]}")
    stany, blad = zapytanie(sklep, SQL_STANY)
    pokaz(stany, blad, "Sklep nie ma jeszcze żadnych produktów")
    pod = podwidok("Magazyn", ["Przyjęcie towaru", "Zmiana ceny",
                               f"Przekazanie do sklepu {NAZWA[drugi]}"], "mag_widok")
    produkty, blad_p = zapytanie(sklep, "SELECT id_produktu, nazwa FROM typy_produktow "
                                        "WHERE aktywny = 1 ORDER BY id_produktu")
    w_sklepie = set() if stany is None else {int(x) for x in stany["id_produktu"]}

    if blad or blad_p:
        st.error("Mój węzeł jest niedostępny – operacje magazynowe są wstrzymane.")
    elif pod.startswith("Przyjęcie"):
        with st.form("przyjecie", clear_on_submit=True, border=True):
            mapa = {int(r.id_produktu): f"{int(r.id_produktu)} – {r.nazwa}"
                                        + ("" if int(r.id_produktu) in w_sklepie else "   (nowy w sklepie)")
                    for r in produkty.itertuples()}
            c1, c2, c3 = st.columns([3, 1, 1])
            id_p = c1.selectbox("Produkt z katalogu sieci", list(mapa), format_func=mapa.get)
            ile = c2.number_input("Ilość", min_value=1, step=1)
            cena = c3.number_input("Cena (dla nowego)", min_value=0.0, step=0.01, format="%.2f")
            if st.form_submit_button("Przyjmij", type="primary"):
                po_akcji(op.przyjmij_towar(sklep, id_p, int(ile), cena or None))
        st.caption("Produkt oznaczony „nowy w sklepie” jest w katalogu sieci, ale ten sklep go jeszcze nie ma – podaj cenę.")
    elif pod.startswith("Zmiana ceny"):
        if stany is None or stany.empty:
            st.info("Brak produktów w sklepie")
        else:
            with st.form("cena", border=True):
                mapa = {int(r.id_produktu): f"{r.nazwa} (teraz {float(r.cena):.2f} zł)" for r in stany.itertuples()}
                c1, c2 = st.columns([3, 1])
                id_p = c1.selectbox("Produkt", list(mapa), format_func=mapa.get)
                nowa = c2.number_input("Nowa cena", min_value=0.0, step=0.01, format="%.2f")
                if st.form_submit_button("Zmień cenę", type="primary"):
                    po_akcji(op.zmien_cene(sklep, id_p, nowa))
            st.caption(f"Cena zmieni się tylko w sklepie {NAZWA[sklep]}. Katalog sieci się nie zmienia.")
    else:
        dostepne = stany[stany["ilosc_dostepna"].astype(int) > 0] if stany is not None else pd.DataFrame()
        if dostepne.empty:
            st.info("Brak towaru do przekazania")
        else:
            with st.form("transfer", clear_on_submit=True, border=True):
                mapa = {int(r.id_produktu): f"{r.nazwa} (na stanie {int(r.ilosc_dostepna)})" for r in dostepne.itertuples()}
                c1, c2 = st.columns([3, 1])
                id_p = c1.selectbox("Produkt", list(mapa), format_func=mapa.get)
                ile = c2.number_input("Ilość", min_value=1, step=1)
                if st.form_submit_button(f"Przekaż do sklepu {NAZWA[drugi]}", type="primary"):
                    po_akcji(op.przekaz(sklep, drugi, id_p, int(ile)))


# ================================================================ Katalog sieci
elif widok == "Katalog sieci":
    st.subheader("Katalog sieci")
    if sklep == "WRO":
        st.info("Wrocław **prowadzi katalog sieci** (oryginał w Oracle). Warszawa pobiera jego kopię. "
                "Nowy produkt nie jest jeszcze w żadnym sklepie – pojawi się po *Przyjęciu towaru*.", icon="ℹ️")
        kat, blad = zapytanie("WRO", SQL_KATALOG.format(extra=""))
        pokaz(kat, blad)
        if not blad:
            kategorie, _ = zapytanie("WRO", "SELECT id_kategorii, nazwa FROM kategorie ORDER BY nazwa")
            pod = podwidok("Katalog", ["Nowy produkt", "Nowa kategoria", "Wycofanie / przywrócenie"], "kat_widok")
            if pod == "Nowy produkt":
                with st.form("nowy_produkt", clear_on_submit=True, border=True):
                    c1, c2 = st.columns(2)
                    nazwa = c1.text_input("Nazwa produktu")
                    mapa_kat = dict(zip(kategorie["id_kategorii"].astype(int), kategorie["nazwa"]))
                    id_kat = c2.selectbox("Kategoria", list(mapa_kat), format_func=mapa_kat.get)
                    opis = st.text_input("Opis (opcjonalnie)")
                    if st.form_submit_button("Dodaj do katalogu", type="primary") and nazwa.strip():
                        po_akcji(op.dodaj_produkt(nazwa.strip(), id_kat, opis.strip() or None))
            elif pod == "Nowa kategoria":
                with st.form("nowa_kategoria", clear_on_submit=True, border=True):
                    nazwa = st.text_input("Nazwa kategorii")
                    if st.form_submit_button("Dodaj kategorię", type="primary") and nazwa.strip():
                        po_akcji(op.dodaj_kategorie(nazwa.strip()))
            else:
                with st.form("aktywnosc", border=True):
                    mapa = {int(r.id_produktu): f"{int(r.id_produktu)} – {r.nazwa}" for r in kat.itertuples()}
                    c1, c2 = st.columns([3, 2])
                    id_p = c1.selectbox("Produkt", list(mapa), format_func=mapa.get)
                    aktywny = c2.radio("Stan", ["aktywny", "wycofany"], horizontal=True)
                    if st.form_submit_button("Zapisz", type="primary"):
                        po_akcji(op.ustaw_aktywny(id_p, aktywny == "aktywny"))
                st.caption("Produktów się nie usuwa (są do nich zamówienia) – wycofany znika ze sklepów.")
    else:
        st.info("Katalog sieci prowadzi **Wrocław** (oryginał w Oracle). Warszawa ma jego **kopię tylko do odczytu** – "
                "pobieraną automatycznie co 5 minut (SQL Server Agent) albo przyciskiem.", icon="ℹ️")
        kopia, blad_k = zapytanie("WAW", SQL_KATALOG.format(extra=", t.data_synchronizacji"))
        oryginal, blad_o = zapytanie("WRO", SQL_KATALOG.format(extra=""))
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        if blad_o:
            c1.warning("Wrocław jest niedostępny – pracujemy na ostatniej pobranej kopii katalogu.")
        elif kopia is not None:
            o = przygotuj(oryginal).set_index("id_produktu")[["nazwa", "kategoria", "opis", "aktywny"]]
            k = przygotuj(kopia).set_index("id_produktu")[["nazwa", "kategoria", "opis", "aktywny"]]
            nowe = len(o.index.difference(k.index))
            wspolne = o.index.intersection(k.index)
            zmienione = int((o.loc[wspolne] != k.loc[wspolne]).any(axis=1).sum())
            if nowe or zmienione:
                c1.warning(f"We Wrocławiu są zmiany do pobrania: {nowe} nowych, {zmienione} zmienionych produktów.")
            else:
                c1.success("Kopia jest aktualna – taka sama jak katalog we Wrocławiu.")
        if c2.button("⬇️ Pobierz katalog z Wrocławia", type="primary", use_container_width=True,
                     disabled=bool(blad_o)):
            po_akcji(op.synchronizuj())
        pokaz(kopia, blad_k)
        st.markdown("**Historia pobrań**")
        log, blad = zapytanie("WAW", SQL_LOG.format(n=5))
        pokaz(log, blad, "Brak wpisów")


# ================================================================ Raporty
elif widok == "Raporty":
    st.subheader("Raport dzienny sieci")
    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    dzien = c1.date_input("Dzień", dt.date.today(), format="DD.MM.YYYY")
    if c2.button("Generuj raport", type="primary", use_container_width=True):
        df, blad = op.raport(dzien)
        if blad:
            st.error(blad)
        elif df is not None and not df.empty:
            kolumny = st.columns(len(df) + 1)
            for kol, r in zip(kolumny, df.itertuples()):
                kol.metric(NAZWA.get(str(r.sklep).strip(), r.sklep), f"{float(r.suma_sprzedazy):.2f} zł",
                           f"{int(r.liczba_zamowien)} zamówień", delta_color="off")
            kolumny[-1].metric("Cała sieć", f"{df['suma_sprzedazy'].astype(float).sum():.2f} zł",
                               f"{int(df['liczba_zamowien'].astype(int).sum())} zamówień", delta_color="off")
            if len(df) < 2:
                st.warning("Raport zawiera tylko Warszawę – Wrocław nie odpowiedział.")
    st.markdown("**Zapisane raporty** (tabela `raporty_dzienne` w SQL Serverze)")
    df, blad = zapytanie("WAW", "SELECT * FROM raporty_dzienne ORDER BY data_raportu DESC, sklep")
    pokaz(df, blad, "Brak raportów")
    st.markdown(f"**Zamówienia w wybranym dniu · {NAZWA[sklep]}**")
    od = dt.datetime.combine(dzien, dt.time())
    df, blad = zapytanie(sklep, SQL_ZAMOWIENIA, (od, od + dt.timedelta(days=1)))
    pokaz(df, blad, "Brak zamówień")


# ================================================================ Rozproszenie
elif widok == "Rozproszenie":
    st.subheader("Jak to jest rozproszone")
    pod = podwidok("Rozproszenie", ["Fragmentacja", "Replikacja", "Widoki rozproszone",
                                    "Wydajność"], "roz_widok")

    if pod == "Fragmentacja":
        TABELE = [
            ("kategorie", "replikacja Oracle → SQL Server"),
            ("typy_produktow", "replikacja Oracle → SQL Server"),
            ("stany_magazynowe", "fragmentacja pozioma pierwotna (sklep)"),
            ("zamowienia", "fragmentacja pozioma pierwotna (sklep)"),
            ("pozycje_zamowien", "fragmentacja pozioma pochodna"),
            ("rezerwacje", "fragmentacja pozioma (sklep z towarem)"),
            ("raporty_dzienne", "tylko SQL Server"),
        ]
        wiersze = []
        for tabela, sposob in TABELE:
            w = {"Tabela": tabela, "Sposób rozproszenia": sposob}
            for s in SKLEPY:
                if tabela == "raporty_dzienne" and s == "WRO":
                    w[NAZWA[s]] = "—"
                    continue
                n = jedna_liczba(s, f"SELECT COUNT(*) FROM {tabela}")
                w[NAZWA[s]] = "niedostępny" if n is None else str(int(n))
            wiersze.append(w)
        st.dataframe(pd.DataFrame(wiersze), hide_index=True)
        st.caption("Liczba wierszy każdej tabeli w każdym węźle. Tabele replikowane mają w obu tyle samo "
                   "(po synchronizacji), fragmenty – każdy swoje.")

    elif pod == "Replikacja":
        st.markdown("Każde uruchomienie `dbo.sp_synchronizuj_katalog` (job co 5 min, przycisk w aplikacji, "
                    "ręcznie w SSMS) zapisuje się w `dbo.log_synchronizacji`.")
        log, blad = zapytanie("WAW", SQL_LOG.format(n=20))
        pokaz(log, blad, "Brak wpisów")

    elif pod == "Widoki rozproszone":
        st.markdown("**`dbo.v_dostepnosc_globalna`** – fragment WAW (lokalnie) ∪ fragment WRO (z Oracle przez "
                    "`OPENQUERY`), nazwy z lokalnej kopii katalogu")
        df, blad = zapytanie("WAW", "SELECT * FROM v_dostepnosc_globalna ORDER BY id_produktu, sklep")
        pokaz(df, blad)
        st.markdown("**`dbo.v_rezerwacje_globalne`** – rezerwacje z obu węzłów w jednym widoku")
        df, blad = zapytanie("WAW", "SELECT * FROM v_rezerwacje_globalne ORDER BY data_utworzenia DESC")
        pokaz(df, blad, "Brak rezerwacji")
        st.caption("Gdy Oracle nie działa, widoki zwracają błąd – dlatego zwykłe ekrany aplikacji "
                   "odpytują oba węzły osobno.")

    else:
        st.markdown("Ten sam zbiór danych (stany Wrocławia) pobrany na cztery sposoby. Średnia z 5 prób, "
                    "mierzona z aplikacji.")
        if st.button("Zmierz", type="primary"):
            testy = [
                ("SQL Server – tabela lokalna (stany Warszawy)", "WAW",
                 "SELECT id_produktu, cena, ilosc_dostepna FROM dbo.stany_magazynowe"),
                ("Aplikacja → Oracle bezpośrednio", "WRO",
                 "SELECT id_produktu, cena, ilosc_dostepna FROM stany_magazynowe"),
                ("SQL Server → Oracle: OPENQUERY", "WAW",
                 "SELECT * FROM OPENQUERY(ORACLE_WRO, 'SELECT ID_PRODUKTU, CENA, ILOSC_DOSTEPNA "
                 "FROM STANY_MAGAZYNOWE')"),
                ("SQL Server → Oracle: nazwa czteroczłonowa", "WAW",
                 "SELECT ID_PRODUKTU, CENA, ILOSC_DOSTEPNA FROM ORACLE_WRO..SKLEP_WRO.STANY_MAGAZYNOWE"),
            ]
            wyniki = []
            for opis, s, sql in testy:
                try:
                    ms, n = pomiar(s, sql)
                    wyniki.append({"Sposób": opis, "Czas [ms]": round(ms, 1), "Wierszy": n, "Uwagi": ""})
                except Exception as e:
                    wyniki.append({"Sposób": opis, "Czas [ms]": None, "Wierszy": None, "Uwagi": str(e)[:150]})
            st.dataframe(pd.DataFrame(wyniki), hide_index=True)
