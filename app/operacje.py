"""Operacje biznesowe. Każda to JEDNA lokalna transakcja w JEDNEJ bazie –
bez transakcji rozproszonej Oracle↔SQL Server.

Rezerwacja zawsze trafia do bazy sklepu, w którym leży towar (także gdy
składa ją pracownik drugiego sklepu). Kolumna sklep_zlecajacy pamięta,
kto ją złożył.

Wszystkie funkcje zwracają (True/False, komunikat).
"""
from decimal import Decimal
from functools import wraps

from bazy import NAZWA, Niedostepny, procedura, transakcja


class Odmowa(Exception):
    """Operacja odrzucona z powodów biznesowych (np. za mało sztuk)."""


def _bezpiecznie(fn):
    @wraps(fn)
    def opakowana(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Odmowa as e:
            return False, str(e)
        except Niedostepny as e:
            return False, f"{e} – operacja niemożliwa"
        except Exception as e:
            return False, f"Błąd: {e}"
    return opakowana


def _kwota(x):
    return Decimal(str(x)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------- rezerwacje
@_bezpiecznie
def zarezerwuj(sklep_docelowy, sklep_zlecajacy, id_produktu, n, klient=None):
    """Atomowa rezerwacja – rozwiązuje problem „ostatniej sztuki”.

    Warunek ilosc_dostepna >= n jest w samym UPDATE. Baza blokuje wiersz,
    więc z dwóch równoczesnych prób tylko jedna zmieni wiersz, a druga
    dostanie 0 zmienionych wierszy.
    """
    with transakcja(sklep_docelowy) as k:
        k.execute("UPDATE stany_magazynowe "
                  "SET ilosc_dostepna = ilosc_dostepna - ?, "
                  "    ilosc_zarezerwowana = ilosc_zarezerwowana + ? "
                  "WHERE id_produktu = ? AND ilosc_dostepna >= ?",
                  n, n, id_produktu, n)
        if k.rowcount == 0:
            raise Odmowa("Za mało sztuk – rezerwacja odrzucona")
        k.execute("INSERT INTO rezerwacje (sklep_zlecajacy, klient, id_produktu, ilosc) "
                  "VALUES (?, ?, ?, ?)", sklep_zlecajacy, klient, id_produktu, n)
    return True, f"Zarezerwowano {n} szt. w sklepie {NAZWA[sklep_docelowy]}"


def _zamknij_rezerwacje(k, id_rezerwacji, nowy_status):
    k.execute("UPDATE rezerwacje SET status = ? "
              "WHERE id_rezerwacji = ? AND status = 'AKTYWNA'", nowy_status, id_rezerwacji)
    if k.rowcount == 0:
        raise Odmowa("Rezerwacja nie istnieje albo nie jest już aktywna")
    id_p, n = k.execute("SELECT id_produktu, ilosc FROM rezerwacje WHERE id_rezerwacji = ?",
                        id_rezerwacji).fetchone()
    return int(id_p), int(n)


@_bezpiecznie
def anuluj(sklep, id_rezerwacji):
    """Zarezerwowane sztuki wracają do dostępnych."""
    with transakcja(sklep) as k:
        id_p, n = _zamknij_rezerwacje(k, id_rezerwacji, "ANULOWANA")
        k.execute("UPDATE stany_magazynowe "
                  "SET ilosc_dostepna = ilosc_dostepna + ?, "
                  "    ilosc_zarezerwowana = ilosc_zarezerwowana - ? "
                  "WHERE id_produktu = ?", n, n, id_p)
    return True, f"Rezerwacja {id_rezerwacji} anulowana"


@_bezpiecznie
def zrealizuj(sklep, id_rezerwacji):
    """Klient odebrał towar: zarezerwowane sztuki znikają ze stanu,
    powstaje zamówienie z lokalną ceną z chwili sprzedaży."""
    with transakcja(sklep) as k:
        id_p, n = _zamknij_rezerwacje(k, id_rezerwacji, "ZREALIZOWANA")
        (cena,) = k.execute("SELECT cena FROM stany_magazynowe WHERE id_produktu = ?",
                            id_p).fetchone()
        cena = _kwota(cena)
        k.execute("UPDATE stany_magazynowe "
                  "SET ilosc_zarezerwowana = ilosc_zarezerwowana - ? "
                  "WHERE id_produktu = ?", n, id_p)
        id_zam = k.nowe_zamowienie(cena * n)
        k.execute("INSERT INTO pozycje_zamowien (id_zamowienia, id_produktu, ilosc, cena_jednostkowa) "
                  "VALUES (?, ?, ?, ?)", id_zam, id_p, n, cena)
    return True, f"Rezerwacja {id_rezerwacji} zrealizowana – zamówienie nr {id_zam} na {cena * n} zł"


# ---------------------------------------------------------------- magazyn i ceny (tylko własny sklep)
@_bezpiecznie
def przyjmij_towar(sklep, id_produktu, n, cena=None):
    with transakcja(sklep) as k:
        k.execute("UPDATE stany_magazynowe SET ilosc_dostepna = ilosc_dostepna + ? "
                  "WHERE id_produktu = ?", n, id_produktu)
        if k.rowcount == 0:
            if not cena:
                raise Odmowa("Tego produktu nie ma jeszcze w sklepie – podaj cenę")
            k.execute("INSERT INTO stany_magazynowe (id_produktu, cena, ilosc_dostepna) "
                      "VALUES (?, ?, ?)", id_produktu, _kwota(cena), n)
            return True, f"Nowy produkt w sklepie {NAZWA[sklep]}: {n} szt. po {_kwota(cena)} zł"
    return True, f"Przyjęto {n} szt. do sklepu {NAZWA[sklep]}"


@_bezpiecznie
def zmien_cene(sklep, id_produktu, cena):
    with transakcja(sklep) as k:
        k.execute("UPDATE stany_magazynowe SET cena = ? WHERE id_produktu = ?",
                  _kwota(cena), id_produktu)
        if k.rowcount == 0:
            raise Odmowa("Tego produktu nie ma w sklepie")
    return True, f"Nowa cena w sklepie {NAZWA[sklep]}: {_kwota(cena)} zł"


# ---------------------------------------------------------------- katalog (master w Oracle)
@_bezpiecznie
def dodaj_kategorie(nazwa):
    with transakcja("WRO") as k:
        k.execute("INSERT INTO kategorie (nazwa) VALUES (?)", nazwa)
    return True, f"Dodano kategorię „{nazwa}” w Oracle (do SQL Servera trafi po synchronizacji)"


@_bezpiecznie
def dodaj_produkt(nazwa, id_kategorii, opis=None):
    with transakcja("WRO") as k:
        k.execute("INSERT INTO typy_produktow (nazwa, id_kategorii, opis) VALUES (?, ?, ?)",
                  nazwa, id_kategorii, opis)
    return True, f"Dodano typ produktu „{nazwa}” w Oracle (do SQL Servera trafi po synchronizacji)"


@_bezpiecznie
def ustaw_aktywny(id_produktu, aktywny):
    with transakcja("WRO") as k:
        k.execute("UPDATE typy_produktow SET aktywny = ? WHERE id_produktu = ?",
                  1 if aktywny else 0, id_produktu)
    return True, f"Produkt {id_produktu}: {'aktywny' if aktywny else 'wycofany'}"


@_bezpiecznie
def synchronizuj():
    """Pobranie katalogu z Wrocławia: procedura w SQL Serverze czyta Oracle przez Linked Server."""
    procedura("WAW", "EXEC dbo.sp_synchronizuj_katalog @zrodlo = ?", ("aplikacja",))
    wpis = procedura("WAW", "SELECT TOP 1 produktow, zmian FROM dbo.log_synchronizacji "
                            "ORDER BY id_wpisu DESC")
    if wpis is None or wpis.empty:
        return True, "Katalog pobrany z Wrocławia"
    produktow, zmian = int(wpis.iloc[0, 0] or 0), int(wpis.iloc[0, 1] or 0)
    return True, (f"Katalog pobrany z Wrocławia: {produktow} produktów, "
                  f"{'brak zmian' if zmian == 0 else f'{zmian} zmienionych wierszy'}")


# ---------------------------------------------------------------- sprzedaż
@_bezpiecznie
def sprzedaj(sklep, id_produktu, n):
    """Sprzedaż z własnego magazynu: zmiana stanu + zamówienie + pozycja – jedna lokalna transakcja.
    Ten sam warunek ilosc_dostepna >= n co przy rezerwacji chroni przed sprzedażą nieistniejącej sztuki."""
    with transakcja(sklep) as k:
        k.execute("UPDATE stany_magazynowe SET ilosc_dostepna = ilosc_dostepna - ? "
                  "WHERE id_produktu = ? AND ilosc_dostepna >= ?", n, id_produktu, n)
        if k.rowcount == 0:
            raise Odmowa("Za mało sztuk – sprzedaż odrzucona")
        (cena,) = k.execute("SELECT cena FROM stany_magazynowe WHERE id_produktu = ?",
                            id_produktu).fetchone()
        cena = _kwota(cena)
        id_zam = k.nowe_zamowienie(cena * n)
        k.execute("INSERT INTO pozycje_zamowien (id_zamowienia, id_produktu, ilosc, cena_jednostkowa) "
                  "VALUES (?, ?, ?, ?)", id_zam, id_produktu, n, cena)
    return True, f"Sprzedano {n} szt. za {cena * n} zł – zamówienie nr {id_zam}"


# ---------------------------------------------------------------- transfer między sklepami
@_bezpiecznie
def przekaz(sklep_z, sklep_do, id_produktu, n):
    """Przekazanie towaru do drugiego sklepu – zmiana w DWÓCH bazach.

    Zamiast transakcji rozproszonej (2PC) dwie lokalne transakcje + kompensacja:
      krok 1: zdejmij n sztuk w sklepie źródłowym (commit),
      krok 2: dodaj n sztuk w sklepie docelowym (commit),
      jeśli krok 2 się nie uda – kompensacja: oddaj n sztuk w sklepie źródłowym.
    """
    with transakcja(sklep_z) as k:
        k.execute("UPDATE stany_magazynowe SET ilosc_dostepna = ilosc_dostepna - ? "
                  "WHERE id_produktu = ? AND ilosc_dostepna >= ?", n, id_produktu, n)
        if k.rowcount == 0:
            raise Odmowa("Za mało sztuk do przekazania")
        (cena,) = k.execute("SELECT cena FROM stany_magazynowe WHERE id_produktu = ?",
                            id_produktu).fetchone()
    try:
        with transakcja(sklep_do) as k:
            k.execute("UPDATE stany_magazynowe SET ilosc_dostepna = ilosc_dostepna + ? "
                      "WHERE id_produktu = ?", n, id_produktu)
            if k.rowcount == 0:   # nowy produkt w sklepie docelowym – startowo cena nadawcy
                k.execute("INSERT INTO stany_magazynowe (id_produktu, cena, ilosc_dostepna) "
                          "VALUES (?, ?, ?)", id_produktu, _kwota(cena), n)
    except Exception as e:
        try:
            with transakcja(sklep_z) as k:
                k.execute("UPDATE stany_magazynowe SET ilosc_dostepna = ilosc_dostepna + ? "
                          "WHERE id_produktu = ?", n, id_produktu)
        except Exception as e2:
            return False, (f"UWAGA: towar zdjęty w sklepie {NAZWA[sklep_z]}, ale nie dodany w sklepie "
                           f"{NAZWA[sklep_do]} i kompensacja się nie udała ({e2}). "
                           f"Dodaj ręcznie {n} szt. produktu {id_produktu} w sklepie {NAZWA[sklep_z]}.")
        return False, (f"Sklep {NAZWA[sklep_do]} nie przyjął towaru ({e}). "
                       f"Wykonano kompensację: {n} szt. wróciło do sklepu {NAZWA[sklep_z]}.")
    return True, f"Przekazano {n} szt. ze sklepu {NAZWA[sklep_z]} do sklepu {NAZWA[sklep_do]}"


def raport(dzien):
    """Raport dzienny z obu sklepów (procedura w SQL Serverze). Zwraca (DataFrame, błąd)."""
    try:
        return procedura("WAW", "EXEC dbo.sp_raport_dzienny @data = ?", (dzien,)), None
    except Exception as e:
        return None, f"Nie udało się wygenerować raportu: {e}"
