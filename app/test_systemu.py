"""Test całego systemu: obie bazy, Linked Server, replikacja, spójność danych i operacje aplikacji.

Uruchomienie (z katalogu projektu, przy aktywnym .venv):
    python app/test_systemu.py               # pełny test
    python app/test_systemu.py --bez-zapisu  # tylko odczyt, bez testowych rezerwacji

Test korzysta z tych samych plików co aplikacja (config.py, bazy.py, operacje.py)
i z ustawień w .env. Pełny test tworzy rezerwacje z klientem 'TEST-AUTO',
a na koniec je anuluje i usuwa, więc stany magazynowe wracają do poprzednich wartości.
Testy zmieniające ceny i ograniczenia wykonują się w transakcji, która jest wycofywana.
"""
import datetime as dt
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import config
import bazy
import operacje as op
from bazy import NAZWA, SKLEPY, Kursor, polacz, status, zapytanie

BEZ_ZAPISU = "--bez-zapisu" in sys.argv
MOJ = config.MOJ_SKLEP
DRUGI = "WRO" if MOJ == "WAW" else "WAW"
TEST_KLIENT = "TEST-AUTO"

TABELE_WSPOLNE = ["kategorie", "typy_produktow", "stany_magazynowe",
                  "zamowienia", "pozycje_zamowien", "rezerwacje"]
TABELE_TYLKO_WAW = ["raporty_dzienne", "log_synchronizacji"]

wyniki = []          # (kategoria, nazwa, stan, opis, rada)
DOSTEPNY = {}


# ================================================================ mechanika testów
def zapisz(kat, nazwa, stan, opis="", rada=""):
    wyniki.append((kat, nazwa, stan, opis, rada))
    znak = {"OK": "[ OK  ]", "BŁĄD": "[BŁĄD ]", "UWAGA": "[UWAGA]", "POMINIĘTO": "[ --- ]"}[stan]
    print(f"{znak} {nazwa}" + (f" – {opis}" if opis else ""))
    if rada and stan in ("BŁĄD", "UWAGA"):
        print(f"          → {rada}")


def test(kat, nazwa, rada="", wymaga=(), zapis=False):
    """Dekorator: funkcja zwraca (stan, opis) albo rzuca wyjątek (= BŁĄD)."""
    def dekorator(fn):
        brak = [s for s in wymaga if not DOSTEPNY.get(s)]
        if brak:
            zapisz(kat, nazwa, "POMINIĘTO", f"niedostępny węzeł: {', '.join(NAZWA[s] for s in brak)}")
            return fn
        if zapis and BEZ_ZAPISU:
            zapisz(kat, nazwa, "POMINIĘTO", "uruchomiono z --bez-zapisu")
            return fn
        try:
            stan, opis = fn()
        except Exception as e:
            stan, opis = "BŁĄD", f"{type(e).__name__}: {str(e)[:300]}"
        zapisz(kat, nazwa, stan, opis, rada)
        return fn
    return dekorator


def sekcja(tytul):
    print(f"\n=== {tytul} " + "=" * max(0, 60 - len(tytul)))


def skalar(sklep, sql, params=()):
    df, blad = zapytanie(sklep, sql, params)
    if blad:
        raise RuntimeError(blad)
    if df is None or df.empty:
        return None
    return df.iloc[0, 0]


def ramka(sklep, sql, params=()):
    df, blad = zapytanie(sklep, sql, params)
    if blad:
        raise RuntimeError(blad)
    return df


def pusty(x):
    return x is None or (isinstance(x, float) and x != x)


def liczba(x):
    return 0 if pusty(x) else int(float(x))


def oczekuj_bledu(sklep, sql, *params):
    """Wykonuje polecenie w transakcji, która jest zawsze wycofywana.
    Zwraca treść błędu albo None, jeśli polecenie przeszło (czyli źle)."""
    with closing(polacz(sklep)) as con:
        try:
            Kursor(sklep, con.cursor()).execute(sql, *params)
            return None
        except Exception as e:
            return str(e)
        finally:
            con.rollback()


def dzis():
    od = dt.datetime.combine(dt.date.today(), dt.time())
    return od, od + dt.timedelta(days=1)


def sprzatnij_testowe(sklep):
    """Anuluje aktywne testowe rezerwacje i usuwa testowe wiersze rezerwacji."""
    if not DOSTEPNY.get(sklep):
        return
    df, blad = zapytanie(sklep, "SELECT id_rezerwacji FROM rezerwacje "
                                "WHERE klient = ? AND status = 'AKTYWNA'", (TEST_KLIENT,))
    for id_r in ([] if blad or df is None else df["id_rezerwacji"]):
        op.anuluj(sklep, int(id_r))
    try:
        with bazy.transakcja(sklep) as k:
            k.execute("DELETE FROM rezerwacje WHERE klient = ? AND status = 'ANULOWANA'", TEST_KLIENT)
    except Exception as e:
        print(f"          (nie udało się usunąć testowych rezerwacji w sklepie {NAZWA[sklep]}: {e})")


def produkt_ze_stanem(sklep, minimum=1):
    """(id_produktu, ilosc_dostepna, ilosc_zarezerwowana) pierwszego produktu z zapasem."""
    df = ramka(sklep, "SELECT s.id_produktu, s.ilosc_dostepna, s.ilosc_zarezerwowana "
                      "FROM stany_magazynowe s JOIN typy_produktow t ON t.id_produktu = s.id_produktu "
                      "WHERE t.aktywny = 1 AND s.ilosc_dostepna >= ? ORDER BY s.id_produktu", (minimum,))
    if df is None or df.empty:
        return None
    r = df.iloc[0]
    return liczba(r["id_produktu"]), liczba(r["ilosc_dostepna"]), liczba(r["ilosc_zarezerwowana"])


def stan_produktu(sklep, id_p):
    df = ramka(sklep, "SELECT ilosc_dostepna, ilosc_zarezerwowana FROM stany_magazynowe "
                      "WHERE id_produktu = ?", (id_p,))
    return liczba(df.iloc[0, 0]), liczba(df.iloc[0, 1])


# ================================================================ 1. konfiguracja
print(f"Test systemu · stanowisko {NAZWA.get(MOJ, MOJ)} (MOJ_SKLEP={MOJ})"
      + (" · tryb tylko do odczytu" if BEZ_ZAPISU else ""))
print(f"Start: {dt.datetime.now():%d.%m.%Y %H:%M:%S}")

sekcja("1. Konfiguracja")


@test("konfiguracja", "Plik .env", "Utwórz plik .env w folderze nad 'app' (nie .env.txt)")
def _():
    sciezka = Path(config.__file__).resolve().parent.parent / ".env"
    return ("OK", str(sciezka)) if sciezka.exists() else ("BŁĄD", f"brak pliku {sciezka}")


@test("konfiguracja", "MOJ_SKLEP", "W .env ustaw MOJ_SKLEP=WRO albo MOJ_SKLEP=WAW")
def _():
    return ("OK", MOJ) if MOJ in SKLEPY else ("BŁĄD", f"nieznana wartość {MOJ!r}")


@test("konfiguracja", "Sterownik ODBC dla SQL Servera",
      "Zainstaluj 'ODBC Driver 18 for SQL Server' (x64) ze strony Microsoft")
def _():
    import pyodbc
    nazwa = config.MSSQL["driver"]
    zainstalowane = pyodbc.drivers()
    if nazwa in zainstalowane:
        return "OK", nazwa
    return "BŁĄD", f"brak {nazwa!r}; zainstalowane: {', '.join(zainstalowane) or 'żadne'}"


@test("konfiguracja", "Adres Oracle w .env",
      "Na laptopie Wrocław użyj 127.0.0.1, na laptopie Warszawa adresu Tailscale 100.x.y.z")
def _():
    dsn = config.ORA["dsn"]
    if "localhost" in dsn.lower():
        return "UWAGA", f"{dsn} – listener Oracle nie nasłuchuje pod nazwą localhost"
    if not dsn.upper().endswith("/FREEPDB1"):
        return "UWAGA", f"{dsn} – oczekiwana usługa FREEPDB1"
    return "OK", dsn


@test("konfiguracja", "Hasła w .env", "Uzupełnij ORA_PASSWORD i MSSQL_PASSWORD w .env")
def _():
    brak = [n for n, w in (("ORA_PASSWORD", config.ORA["password"]),
                           ("MSSQL_PASSWORD", config.MSSQL["password"])) if not w]
    return ("BŁĄD", "puste: " + ", ".join(brak)) if brak else ("OK", "uzupełnione")


# ================================================================ 2. połączenia
sekcja("2. Połączenia z węzłami")
RADY_POLACZENIE = {
    "WRO": "Sprawdź: Tailscale włączony, 'lsnrctl status' (adres i usługa freepdb1), zapora portu 1521, "
           "ORA_DSN i ORA_PASSWORD w .env",
    "WAW": "Sprawdź: Tailscale włączony, TCP/IP na porcie 1433 w SQL Server Configuration Manager, "
           "zapora portu 1433, logowanie mieszane, login aplikacja_waw i hasło w .env",
}
for s in SKLEPY:
    start = time.perf_counter()
    ok, _opis = status(s)
    czas = (time.perf_counter() - start) * 1000
    DOSTEPNY[s] = ok
    if ok:
        zapisz("połączenia", f"Węzeł {NAZWA[s]}", "OK", f"połączono w {czas:.0f} ms")
    else:
        _df, blad = zapytanie(s, "SELECT 1 FROM DUAL" if s == "WRO" else "SELECT 1")
        zapisz("połączenia", f"Węzeł {NAZWA[s]}", "BŁĄD", blad or "brak połączenia", RADY_POLACZENIE[s])

if not BEZ_ZAPISU:
    for s in SKLEPY:
        sprzatnij_testowe(s)      # pozostałości po przerwanym wcześniejszym teście

# ================================================================ 3. schemat
sekcja("3. Schemat baz")
LICZNOSC = {}

for s in SKLEPY:
    tabele = TABELE_WSPOLNE + (TABELE_TYLKO_WAW if s == "WAW" else [])

    @test("schemat", f"Tabele w sklepie {NAZWA[s]}",
          "Uruchom skrypt tworzący tabele (aktualną wersję z kategorie/typy_produktow)", wymaga=(s,))
    def _(s=s, tabele=tabele):
        brak = []
        for t in tabele:
            try:
                LICZNOSC[(s, t)] = liczba(skalar(s, f"SELECT COUNT(*) FROM {t}"))
            except Exception:
                brak.append(t)
        if brak:
            return "BŁĄD", "brak tabel: " + ", ".join(brak)
        return "OK", ", ".join(f"{t}={LICZNOSC[(s, t)]}" for t in tabele)

    @test("schemat", f"Stara tabela 'produkty' usunięta ({NAZWA[s]})",
          "Usuń tabelę produkty – po uwagach prowadzącego zastąpiły ją kategorie i typy_produktow",
          wymaga=(s,))
    def _(s=s):
        _df, blad = zapytanie(s, "SELECT COUNT(*) FROM produkty")
        return ("OK", "nie istnieje") if blad else ("UWAGA", "tabela produkty nadal istnieje")

    @test("schemat", f"Cena jest lokalna, nie w katalogu ({NAZWA[s]})",
          "Kolumna cena ma być w stany_magazynowe, a nie w typy_produktow", wymaga=(s,))
    def _(s=s):
        _df, blad_kat = zapytanie(s, "SELECT cena FROM typy_produktow WHERE 1 = 0")
        _df, blad_st = zapytanie(s, "SELECT cena FROM stany_magazynowe WHERE 1 = 0")
        if blad_st:
            return "BŁĄD", "brak kolumny cena w stany_magazynowe"
        if not blad_kat:
            return "UWAGA", "typy_produktow ma kolumnę cena – katalog powinien mieć tylko dane stałe"
        return "OK", "cena tylko w stany_magazynowe"


@test("schemat", "Obiekty SQL Servera (procedury i widoki)",
      "Utwórz brakujące obiekty skryptami z katalogu sql/mssql", wymaga=("WAW",))
def _():
    brak = []
    for nazwa, typ in (("dbo.sp_synchronizuj_katalog", "P"), ("dbo.sp_raport_dzienny", "P"),
                       ("dbo.v_dostepnosc_globalna", "V"), ("dbo.v_rezerwacje_globalne", "V")):
        if pusty(skalar("WAW", "SELECT OBJECT_ID(?, ?)", (nazwa, typ))):
            brak.append(nazwa)
    return ("BŁĄD", "brak: " + ", ".join(brak)) if brak else ("OK", "2 procedury i 2 widoki istnieją")


# ================================================================ 4. fragmentacja
sekcja("4. Fragmentacja pozioma")
for s in SKLEPY:
    obcy = "WAW" if s == "WRO" else "WRO"

    @test("fragmentacja", f"Wiersze należą do węzła {NAZWA[s]}",
          "W tabelach fragmentowanych kolumna sklep musi mieć wartość własnego węzła", wymaga=(s,))
    def _(s=s):
        zle = {t: liczba(skalar(s, f"SELECT COUNT(*) FROM {t} WHERE sklep <> ?", (s,)))
               for t in ("stany_magazynowe", "zamowienia", "rezerwacje")}
        zle = {t: n for t, n in zle.items() if n}
        return ("BŁĄD", f"obce wiersze: {zle}") if zle else ("OK", f"wszystkie wiersze mają sklep = '{s}'")

    @test("fragmentacja", f"CHECK blokuje zapis obcego sklepu ({NAZWA[s]})",
          f"Dodaj ograniczenie CHECK (sklep = '{s}') w tabeli zamowienia", wymaga=(s,))
    def _(s=s, obcy=obcy):
        blad = oczekuj_bledu(s, "INSERT INTO zamowienia (sklep, status, wartosc) VALUES (?, 'NOWE', 0)", obcy)
        if blad is None:
            return "BŁĄD", f"baza {NAZWA[s]} przyjęła zamówienie sklepu {obcy}"
        return "OK", "odrzucono (transakcja wycofana)"

    @test("fragmentacja", f"CHECK blokuje ujemny stan ({NAZWA[s]})",
          "Dodaj CHECK (ilosc_dostepna >= 0) w stany_magazynowe", wymaga=(s,))
    def _(s=s):
        id_p = skalar(s, "SELECT MIN(id_produktu) FROM stany_magazynowe")
        if pusty(id_p):
            return "POMINIĘTO", "brak stanów magazynowych"
        blad = oczekuj_bledu(s, "UPDATE stany_magazynowe SET ilosc_dostepna = -1 WHERE id_produktu = ?",
                             liczba(id_p))
        return ("BŁĄD", "ujemny stan został przyjęty") if blad is None else ("OK", "odrzucono")

    @test("fragmentacja", f"Pozycje są razem z zamówieniami ({NAZWA[s]})",
          "Każda pozycja musi wskazywać zamówienie w tej samej bazie (fragmentacja pochodna)", wymaga=(s,))
    def _(s=s):
        n = liczba(skalar(s, "SELECT COUNT(*) FROM pozycje_zamowien p WHERE NOT EXISTS "
                             "(SELECT 1 FROM zamowienia z WHERE z.id_zamowienia = p.id_zamowienia)"))
        return ("BŁĄD", f"{n} pozycji bez zamówienia") if n else ("OK", "fragmentacja pochodna zachowana")


# ================================================================ 5. Linked Server i replikacja
sekcja("5. Linked Server i replikacja katalogu")


@test("linked server", "OPENQUERY do Oracle",
      "Sprawdź Linked Server ORACLE_WRO w SSMS (adres Tailscale Wrocławia, login sklep_wro)",
      wymaga=("WAW", "WRO"))
def _():
    przez_link = liczba(skalar("WAW", "SELECT * FROM OPENQUERY(ORACLE_WRO, "
                                      "'SELECT COUNT(*) N FROM TYPY_PRODUKTOW')"))
    bezposrednio = liczba(skalar("WRO", "SELECT COUNT(*) FROM typy_produktow"))
    if przez_link != bezposrednio:
        return "BŁĄD", f"przez link {przez_link}, bezpośrednio {bezposrednio}"
    return "OK", f"SQL Server widzi {przez_link} typów produktów w Oracle"


@test("replikacja", "Synchronizacja uruchomiona z aplikacji",
      "Sprawdź procedurę dbo.sp_synchronizuj_katalog i Linked Server", wymaga=("WAW", "WRO"), zapis=True)
def _():
    ok, tekst = op.synchronizuj()
    return ("OK" if ok else "BŁĄD"), tekst


def _porownaj(sql_wro, sql_waw, klucz):
    def norm(df):
        df = df.copy()
        for c in df.columns:
            df[c] = df[c].map(lambda x: "" if x is None or (isinstance(x, float) and x != x)
                              else str(int(float(x))) if c != "nazwa" and c != "opis"
                              else str(x).strip())
        return {r[klucz]: tuple(r) for r in df.to_dict("records")}
    m, k = norm(ramka("WRO", sql_wro)), norm(ramka("WAW", sql_waw))
    tylko_m = sorted(set(m) - set(k))
    tylko_k = sorted(set(k) - set(m))
    rozne = sorted(i for i in set(m) & set(k) if m[i] != k[i])
    return tylko_m, tylko_k, rozne, len(m)


@test("replikacja", "Kopia kategorii = oryginał",
      "Uruchom EXEC dbo.sp_synchronizuj_katalog w SSMS i sprawdź log_synchronizacji",
      wymaga=("WAW", "WRO"))
def _():
    sql = "SELECT id_kategorii, nazwa FROM kategorie"
    tm, tk, rz, n = _porownaj(sql, sql, "id_kategorii")
    if tm or rz:
        return "BŁĄD", f"brak w kopii: {tm or '-'}, różne: {rz or '-'}"
    if tk:
        return "UWAGA", f"w kopii są kategorie usunięte w Oracle: {tk}"
    return "OK", f"{n} kategorii zgodnych"


@test("replikacja", "Kopia typów produktów = oryginał",
      "Uruchom EXEC dbo.sp_synchronizuj_katalog w SSMS i sprawdź log_synchronizacji",
      wymaga=("WAW", "WRO"))
def _():
    sql = "SELECT id_produktu, nazwa, id_kategorii, opis, aktywny FROM typy_produktow"
    tm, tk, rz, n = _porownaj(sql, sql, "id_produktu")
    if tm or rz:
        return "BŁĄD", f"brak w kopii: {tm or '-'}, różne: {rz or '-'}"
    if tk:
        nieaktywne = liczba(skalar("WAW", "SELECT COUNT(*) FROM typy_produktow WHERE aktywny = 1 AND "
                                          "id_produktu IN (" + ",".join(map(str, tk)) + ")"))
        if nieaktywne:
            return "BŁĄD", f"produkty usunięte w Oracle są w kopii nadal aktywne: {tk}"
        return "OK", f"{n} zgodnych; usunięte w Oracle oznaczone jako nieaktywne: {tk}"
    return "OK", f"{n} typów produktów zgodnych"


@test("replikacja", "Ostatnia synchronizacja zakończona sukcesem",
      "Przeczytaj kolumnę komunikat w dbo.log_synchronizacji", wymaga=("WAW",))
def _():
    df = ramka("WAW", "SELECT TOP 1 czas, zrodlo, status, komunikat FROM log_synchronizacji "
                      "ORDER BY id_wpisu DESC")
    if df is None or df.empty:
        return "UWAGA", "dziennik synchronizacji jest pusty"
    w = df.iloc[0]
    if str(w["status"]).strip() != "OK":
        return "BŁĄD", f"{w['czas']} · {w['zrodlo']} · {w['komunikat']}"
    return "OK", f"{w['czas']} · {w['zrodlo']}"


@test("replikacja", "Job SQL Server Agent działa (co 5 minut)",
      "Uruchom usługę SQL Server Agent (services.msc, typ: Automatyczny) i sprawdź harmonogram joba",
      wymaga=("WAW",))
def _():
    minuty = skalar("WAW", "SELECT DATEDIFF(minute, MAX(czas), SYSDATETIME()) FROM log_synchronizacji "
                           "WHERE zrodlo = 'job'")
    if pusty(minuty):
        return "BŁĄD", "brak wpisów z joba"
    minuty = liczba(minuty)
    if minuty > 10:
        return "UWAGA", f"ostatnie uruchomienie joba {minuty} min temu"
    return "OK", f"ostatnie uruchomienie {minuty} min temu"


@test("replikacja", "Kopia katalogu tylko do odczytu",
      "Nadaj loginowi aplikacji DENY INSERT, UPDATE, DELETE na kategorie i typy_produktow "
      "(i nie używaj w .env loginu sa)", wymaga=("WAW",))
def _():
    blad = oczekuj_bledu("WAW", "UPDATE typy_produktow SET nazwa = nazwa WHERE id_produktu = ?", -1)
    if blad is None:
        return "UWAGA", f"login {config.MSSQL['user']} może zmieniać kopię katalogu"
    if "229" in blad or "denied" in blad.lower() or "odmow" in blad.lower():
        return "OK", f"login {config.MSSQL['user']} nie może zmieniać kopii"
    return "UWAGA", f"inny błąd niż brak uprawnień: {blad[:150]}"


# ================================================================ 6. widoki globalne
sekcja("6. Widoki rozproszone")


@test("widoki", "v_dostepnosc_globalna = suma fragmentów",
      "Sprawdź definicję widoku (UNION ALL stanów WAW i OPENQUERY do stanów WRO)", wymaga=("WAW", "WRO"))
def _():
    widok = liczba(skalar("WAW", "SELECT COUNT(*) FROM v_dostepnosc_globalna"))
    wro = liczba(skalar("WRO", "SELECT COUNT(*) FROM stany_magazynowe"))
    waw = liczba(skalar("WAW", "SELECT COUNT(*) FROM stany_magazynowe"))
    if widok != wro + waw:
        return "UWAGA", f"widok {widok} wierszy, fragmenty {wro} + {waw} = {wro + waw}"
    return "OK", f"{widok} = {wro} (Wrocław) + {waw} (Warszawa)"


@test("widoki", "v_rezerwacje_globalne = suma fragmentów",
      "Sprawdź definicję widoku (UNION ALL rezerwacji WAW i OPENQUERY do rezerwacji WRO)",
      wymaga=("WAW", "WRO"))
def _():
    widok = liczba(skalar("WAW", "SELECT COUNT(*) FROM v_rezerwacje_globalne"))
    wro = liczba(skalar("WRO", "SELECT COUNT(*) FROM rezerwacje"))
    waw = liczba(skalar("WAW", "SELECT COUNT(*) FROM rezerwacje"))
    if widok != wro + waw:
        return "UWAGA", f"widok {widok} wierszy, fragmenty {wro} + {waw} = {wro + waw}"
    return "OK", f"{widok} = {wro} (Wrocław) + {waw} (Warszawa)"


# ================================================================ 7. spójność danych
sekcja("7. Spójność danych w każdym węźle")
for s in SKLEPY:
    @test("spójność", f"Zarezerwowane sztuki = aktywne rezerwacje ({NAZWA[s]})",
          "Któraś operacja zmieniła stan bez rezerwacji (albo odwrotnie) – sprawdź anuluj/zrealizuj",
          wymaga=(s,))
    def _(s=s):
        df = ramka(s, "SELECT s.id_produktu, s.ilosc_zarezerwowana, COALESCE(r.suma, 0) suma "
                      "FROM stany_magazynowe s LEFT JOIN "
                      "(SELECT id_produktu, SUM(ilosc) suma FROM rezerwacje WHERE status = 'AKTYWNA' "
                      " GROUP BY id_produktu) r ON r.id_produktu = s.id_produktu "
                      "WHERE s.ilosc_zarezerwowana <> COALESCE(r.suma, 0)")
        sieroty = liczba(skalar(s, "SELECT COUNT(*) FROM rezerwacje r WHERE r.status = 'AKTYWNA' AND NOT EXISTS "
                                   "(SELECT 1 FROM stany_magazynowe s WHERE s.id_produktu = r.id_produktu)"))
        if df is not None and not df.empty:
            opis = "; ".join(f"produkt {liczba(r.id_produktu)}: w stanie {liczba(r.ilosc_zarezerwowana)}, "
                             f"w rezerwacjach {liczba(r.suma)}" for r in df.itertuples())
            return "BŁĄD", opis
        if sieroty:
            return "BŁĄD", f"{sieroty} aktywnych rezerwacji produktów, których nie ma w magazynie"
        return "OK", "zgodne"

    @test("spójność", f"Wartość zamówień = suma pozycji ({NAZWA[s]})",
          "Sprawdź, czy zamowienia.wartosc jest liczona z cena_jednostkowa × ilosc", wymaga=(s,))
    def _(s=s):
        df = ramka(s, "SELECT z.id_zamowienia, z.wartosc, SUM(p.ilosc * p.cena_jednostkowa) suma "
                      "FROM zamowienia z JOIN pozycje_zamowien p ON p.id_zamowienia = z.id_zamowienia "
                      "GROUP BY z.id_zamowienia, z.wartosc "
                      "HAVING ABS(z.wartosc - SUM(p.ilosc * p.cena_jednostkowa)) > 0.005")
        bez_pozycji = liczba(skalar(s, "SELECT COUNT(*) FROM zamowienia z WHERE NOT EXISTS "
                                       "(SELECT 1 FROM pozycje_zamowien p WHERE p.id_zamowienia = z.id_zamowienia)"))
        if df is not None and not df.empty:
            return "BŁĄD", f"niezgodne zamówienia: {[liczba(x) for x in df['id_zamowienia']]}"
        if bez_pozycji:
            return "UWAGA", f"{bez_pozycji} zamówień bez pozycji"
        n = LICZNOSC.get((s, "zamowienia"), "?")
        return "OK", f"{n} zamówień zgodnych"

    @test("spójność", f"Stany tylko dla aktywnych produktów ({NAZWA[s]})",
          "Wycofany produkt z towarem na stanie – sprzedajcie go albo przywróćcie w katalogu", wymaga=(s,))
    def _(s=s):
        n = liczba(skalar(s, "SELECT COUNT(*) FROM stany_magazynowe s JOIN typy_produktow t "
                             "ON t.id_produktu = s.id_produktu WHERE t.aktywny = 0 AND s.ilosc_dostepna > 0"))
        return ("UWAGA", f"{n} wycofanych produktów ma towar na stanie") if n else ("OK", "brak")


# ================================================================ 8. operacje aplikacji
sekcja("8. Operacje aplikacji")
for s in SKLEPY:
    @test("operacje", f"Ostatnia sztuka – dwie równoczesne rezerwacje ({NAZWA[s]})",
          "Warunek ilosc_dostepna >= n musi być w samym UPDATE (operacje.zarezerwuj)",
          wymaga=(s,), zapis=True)
    def _(s=s):
        p = produkt_ze_stanem(s)
        if p is None:
            return "UWAGA", "brak produktu na stanie – przyjmij towar i powtórz test"
        id_p, dostepne, _zarez = p
        start, wynik = threading.Barrier(2), {}

        def klient(nazwa):
            start.wait()
            wynik[nazwa] = op.zarezerwuj(s, MOJ, id_p, dostepne, TEST_KLIENT)   # obaj chcą CAŁY zapas

        try:
            watki = [threading.Thread(target=klient, args=(n,)) for n in "AB"]
            for w in watki:
                w.start()
            for w in watki:
                w.join()
        finally:
            sprzatnij_testowe(s)
        po = stan_produktu(s, id_p)[0]
        udane = sum(ok for ok, _ in wynik.values())
        if po != dostepne:
            return "BŁĄD", f"stan po teście {po}, przed {dostepne} – sprawdź ręcznie produkt {id_p}"
        if udane != 1:
            return "BŁĄD", f"udanych rezerwacji: {udane} (powinna być 1); {list(wynik.values())}"
        return "OK", f"produkt {id_p}: z dwóch prób o {dostepne} szt. udała się dokładnie jedna; stan przywrócony"


@test("operacje", f"Rezerwacja w drugim sklepie ({NAZWA[MOJ]} → {NAZWA[DRUGI]})",
      "Rezerwacja ma się zapisać w bazie sklepu z towarem, z sklep_zlecajacy = sklep pracownika",
      wymaga=(MOJ, DRUGI), zapis=True)
def _():
    p = produkt_ze_stanem(DRUGI)
    if p is None:
        return "UWAGA", f"brak towaru w sklepie {NAZWA[DRUGI]}"
    id_p, dost, zarez = p
    try:
        ok, tekst = op.zarezerwuj(DRUGI, MOJ, id_p, 1, TEST_KLIENT)
        if not ok:
            return "BŁĄD", tekst
        d2, z2 = stan_produktu(DRUGI, id_p)
        zlec = skalar(DRUGI, "SELECT sklep_zlecajacy FROM rezerwacje WHERE klient = ? AND status = 'AKTYWNA'",
                      (TEST_KLIENT,))
        widoczna_u_mnie = None
        if DOSTEPNY.get("WAW") and DOSTEPNY.get("WRO"):
            try:
                widoczna_u_mnie = liczba(skalar("WAW", "SELECT COUNT(*) FROM v_rezerwacje_globalne "
                                                       "WHERE klient = ? AND status = 'AKTYWNA'", (TEST_KLIENT,)))
            except Exception:
                widoczna_u_mnie = -1     # widok nie działa – osobny test w sekcji 6
    finally:
        sprzatnij_testowe(DRUGI)
    d3, z3 = stan_produktu(DRUGI, id_p)
    if (d2, z2) != (dost - 1, zarez + 1):
        return "BŁĄD", f"stan po rezerwacji {d2}/{z2}, oczekiwano {dost - 1}/{zarez + 1}"
    if str(zlec).strip() != MOJ:
        return "BŁĄD", f"sklep_zlecajacy = {zlec!r}, oczekiwano {MOJ!r}"
    if (d3, z3) != (dost, zarez):
        return "BŁĄD", f"po anulowaniu stan {d3}/{z3}, oczekiwano {dost}/{zarez}"
    dopisek = {None: "", 1: ", widoczna w v_rezerwacje_globalne",
               -1: ", nie udało się sprawdzić v_rezerwacje_globalne"}.get(
        widoczna_u_mnie, ", NIEWIDOCZNA w v_rezerwacje_globalne")
    return ("OK" if widoczna_u_mnie in (None, 1) else "UWAGA"), \
        f"zapis w bazie {NAZWA[DRUGI]}, zlecił {MOJ}{dopisek}; anulowana, stan przywrócony"


@test("operacje", "Zmiana ceny nie wpływa na drugi sklep ani katalog",
      "Cena musi być w stany_magazynowe danego sklepu", wymaga=(MOJ, DRUGI))
def _():
    wspolny = skalar(MOJ, "SELECT MIN(id_produktu) FROM stany_magazynowe")
    if pusty(wspolny):
        return "UWAGA", "brak produktów w magazynie"
    id_p = liczba(wspolny)

    def migawka():
        cena_drugi = skalar(DRUGI, "SELECT cena FROM stany_magazynowe WHERE id_produktu = ?", (id_p,))
        kat = ramka(DRUGI, "SELECT nazwa, aktywny FROM typy_produktow WHERE id_produktu = ?", (id_p,))
        return cena_drugi, None if kat is None else tuple(map(str, kat.iloc[0])) if not kat.empty else None

    przed = migawka()
    with closing(polacz(MOJ)) as con:
        try:
            k = Kursor(MOJ, con.cursor())
            k.execute("UPDATE stany_magazynowe SET cena = cena + 1 WHERE id_produktu = ?", id_p)
            po = migawka()
        finally:
            con.rollback()
    if po != przed:
        return "BŁĄD", f"zmiana w {NAZWA[MOJ]} zmieniła dane w {NAZWA[DRUGI]}: {przed} → {po}"
    return "OK", f"produkt {id_p}: zmiana ceny w {NAZWA[MOJ]} (wycofana) nie zmieniła {NAZWA[DRUGI]}"


@test("operacje", "Raport dzienny = sprzedaż w obu bazach",
      "Sprawdź procedurę dbo.sp_raport_dzienny i opcję 'remote proc transaction promotion' = false",
      wymaga=("WAW", "WRO"), zapis=True)
def _():
    df, blad = op.raport(dt.date.today())
    if blad:
        return "BŁĄD", blad
    if df is None or df.empty:
        df = ramka("WAW", "SELECT sklep, liczba_zamowien, suma_sprzedazy FROM raporty_dzienne "
                          "WHERE data_raportu = ?", (dt.date.today(),))
    raport = {str(r.sklep).strip(): (liczba(r.liczba_zamowien), round(float(r.suma_sprzedazy), 2))
              for r in df.itertuples()}
    od, do = dzis()
    roznice = []
    for s in SKLEPY:
        d = ramka(s, "SELECT COUNT(*), COALESCE(SUM(wartosc), 0) FROM zamowienia "
                     "WHERE data_zamowienia >= ? AND data_zamowienia < ? AND status <> 'ANULOWANE'", (od, do))
        bezposr = (liczba(d.iloc[0, 0]), round(float(d.iloc[0, 1]), 2))
        if raport.get(s) != bezposr:
            roznice.append(f"{s}: raport {raport.get(s)}, w bazie {bezposr}")
    if roznice:
        return "UWAGA", "; ".join(roznice)
    return "OK", "; ".join(f"{s}: {raport[s][0]} zam., {raport[s][1]:.2f} zł" for s in SKLEPY)


# ================================================================ 9. odporność na awarię
sekcja("9. Odporność na awarię (symulacja niedostępnego serwera)")


def _niedostepny(sklep, podmiana):
    """Podmienia adres węzła na nieistniejący i mierzy, jak szybko aplikacja to zgłasza."""
    stare_ora, stare_ms = dict(bazy.ORA), dict(bazy.MSSQL)
    try:
        podmiana()
        start = time.perf_counter()
        _df, blad = zapytanie(sklep, "SELECT 1 FROM DUAL" if sklep == "WRO" else "SELECT 1")
        czas = time.perf_counter() - start
    finally:
        bazy.ORA.clear(); bazy.ORA.update(stare_ora)
        bazy.MSSQL.clear(); bazy.MSSQL.update(stare_ms)
    if not blad:
        return "BŁĄD", "aplikacja nie wykryła niedostępnego serwera"
    if czas > 10:
        return "UWAGA", f"wykrycie trwało {czas:.0f} s – aplikacja będzie długo „wisieć”"
    return "OK", f"zgłoszono brak połączenia po {czas:.1f} s, bez awarii aplikacji"


@test("awaria", "Niedostępny Oracle", "Ustaw tcp_connect_timeout w polacz_oracle (bazy.py)")
def _():
    return _niedostepny("WRO", lambda: bazy.ORA.update(dsn="192.0.2.1:1521/FREEPDB1"))


@test("awaria", "Niedostępny SQL Server", "Ustaw timeout w pyodbc.connect (bazy.py)")
def _():
    return _niedostepny("WAW", lambda: bazy.MSSQL.update(host="192.0.2.1"))


@test("awaria", f"Drugi sklep działa przy awarii ({NAZWA[MOJ]} bez {NAZWA[DRUGI]})",
      "Odczyty własnego sklepu nie mogą zależeć od drugiego węzła", wymaga=(MOJ,))
def _():
    stare = dict(bazy.ORA) if DRUGI == "WRO" else dict(bazy.MSSQL)
    try:
        if DRUGI == "WRO":
            bazy.ORA.update(dsn="192.0.2.1:1521/FREEPDB1")
        else:
            bazy.MSSQL.update(host="192.0.2.1")
        df, blad = zapytanie(MOJ, "SELECT COUNT(*) FROM stany_magazynowe")
    finally:
        cel = bazy.ORA if DRUGI == "WRO" else bazy.MSSQL
        cel.clear(); cel.update(stare)
    return ("BŁĄD", blad) if blad else ("OK", f"{NAZWA[MOJ]} odpowiada normalnie")


# ================================================================ 10. wydajność
sekcja("10. Wydajność odczytu (informacyjnie)")


@test("wydajność", "Lokalnie vs OPENQUERY vs nazwa czteroczłonowa",
      "Nazwa czteroczłonowa wymaga dostawcy OraOLEDB; przy ODBC może nie działać – wystarczy OPENQUERY",
      wymaga=("WAW", "WRO"))
def _():
    testy = [
        ("lokalnie (SQL Server)", "WAW", "SELECT id_produktu, cena, ilosc_dostepna FROM dbo.stany_magazynowe"),
        ("Oracle bezpośrednio", "WRO", "SELECT id_produktu, cena, ilosc_dostepna FROM stany_magazynowe"),
        ("OPENQUERY", "WAW", "SELECT * FROM OPENQUERY(ORACLE_WRO, "
                             "'SELECT ID_PRODUKTU, CENA, ILOSC_DOSTEPNA FROM STANY_MAGAZYNOWE')"),
        ("czteroczłonowa", "WAW", "SELECT ID_PRODUKTU, CENA, ILOSC_DOSTEPNA "
                                  "FROM ORACLE_WRO..SKLEP_WRO.STANY_MAGAZYNOWE"),
    ]
    czesci, bledy = [], []
    for opis, s, sql in testy:
        try:
            ms, _n = bazy.pomiar(s, sql)
            czesci.append(f"{opis} {ms:.1f} ms")
        except Exception as e:
            bledy.append(f"{opis}: {str(e)[:80]}")
    if bledy:
        return "UWAGA", "; ".join(czesci + bledy)
    return "OK", "; ".join(czesci)


# ================================================================ podsumowanie
sekcja("Podsumowanie")
licz = {k: sum(1 for w in wyniki if w[2] == k) for k in ("OK", "BŁĄD", "UWAGA", "POMINIĘTO")}
print(f"OK: {licz['OK']}   BŁĘDY: {licz['BŁĄD']}   UWAGI: {licz['UWAGA']}   POMINIĘTE: {licz['POMINIĘTO']}")
problemy = [w for w in wyniki if w[2] in ("BŁĄD", "UWAGA")]
if problemy:
    print("\nDo poprawienia:")
    for kat, nazwa, stan, opis, rada in problemy:
        print(f"  [{stan}] {nazwa}: {opis}")
        if rada:
            print(f"          → {rada}")
else:
    print("Wszystko działa.")
print("\nTestów ręcznych nie zastąpi ten skrypt: wyłączenie prawdziwego serwera na prezentacji, "
      "transfer towaru między sklepami, przyciski w aplikacji.")
sys.exit(1 if licz["BŁĄD"] else 0)
