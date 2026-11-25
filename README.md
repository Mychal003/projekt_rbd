# Sieć sklepów – rozproszona baza danych (Oracle + SQL Server)

Jedna aplikacja dla obu sklepów. Który to sklep, decyduje `MOJ_SKLEP` w pliku `.env`.

| Laptop   | Baza                  | Plik wzorcowy         |
|----------|-----------------------|-----------------------|
| Wrocław  | Oracle (katalog sieci)| `.env.wro.przyklad`   |
| Warszawa | SQL Server            | `.env.waw.przyklad`   |

## Uruchomienie

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.wro.przyklad .env        (na laptopie Warszawa: .env.waw.przyklad)
python app\test_systemu.py         (diagnostyka obu baz)
streamlit run app\main.py
```

Na laptopie Wrocław potrzebny jest też Microsoft ODBC Driver 18 for SQL Server.

## Zasady

- Kod zmieniamy tylko w repozytorium; oba laptopy mają ten sam kod.
- `.env` jest inny na każdym laptopie i nie trafia do Gita.
- Każde nowe zapytanie SQL piszemy z parametrami `?` i sprawdzamy w obu sklepach.
