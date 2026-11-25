"""Test „ostatniej sztuki”: dwa wątki jednocześnie rezerwują ostatnią sztukę.
Dokładnie jeden powinien wygrać.

Uruchomienie (z katalogu projektu):
    python app/test_wspolbieznosc.py WAW 1     # sklep, id_produktu
    python app/test_wspolbieznosc.py WRO 1

Po teście stan magazynu wraca do poprzedniej wartości.
"""
import sys
import threading

from bazy import NAZWA, transakcja, zapytanie
from operacje import anuluj, zarezerwuj

sklep = sys.argv[1] if len(sys.argv) > 1 else "WAW"
id_p = int(sys.argv[2]) if len(sys.argv) > 2 else 1

df, blad = zapytanie(sklep, "SELECT ilosc_dostepna FROM stany_magazynowe WHERE id_produktu = ?", (id_p,))
if blad or df.empty:
    sys.exit(blad or f"Brak produktu {id_p} w sklepie {sklep}")
poprzednio = int(df.iloc[0, 0])

print(f"Sklep {NAZWA[sklep]}, produkt {id_p}: ustawiam stan na 1 sztukę (było {poprzednio})")
with transakcja(sklep) as k:
    k.execute("UPDATE stany_magazynowe SET ilosc_dostepna = 1 WHERE id_produktu = ?", id_p)

start = threading.Barrier(2)
wyniki = {}


def klient(nazwa):
    start.wait()  # oba wątki ruszają w tej samej chwili
    wyniki[nazwa] = zarezerwuj(sklep, sklep, id_p, 1, f"test-{nazwa}")


watki = [threading.Thread(target=klient, args=(n,)) for n in ("A", "B")]
for w in watki:
    w.start()
for w in watki:
    w.join()

for nazwa, (ok, tekst) in sorted(wyniki.items()):
    print(f"  klient {nazwa}: {'WYGRAŁ ' if ok else 'odrzucony'} – {tekst}")
wygrani = sum(ok for ok, _ in wyniki.values())
print("WYNIK:", "OK – dokładnie jedna rezerwacja" if wygrani == 1 else f"BŁĄD – {wygrani} rezerwacji")

# sprzątanie: anuluj testowe rezerwacje i przywróć stan
df, _ = zapytanie(sklep, "SELECT id_rezerwacji FROM rezerwacje "
                         "WHERE klient LIKE 'test-%' AND status = 'AKTYWNA'")
for id_r in ([] if df is None else df["id_rezerwacji"]):
    anuluj(sklep, int(id_r))
with transakcja(sklep) as k:
    k.execute("UPDATE stany_magazynowe SET ilosc_dostepna = ? WHERE id_produktu = ?", poprzednio, id_p)
print(f"Stan przywrócony do {poprzednio} szt.")
