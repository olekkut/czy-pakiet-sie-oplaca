"""
Porównuje opłacalność pakietów w 49 miastach. W każdym mieście bierze jeden punkt pobrań
wybrany stałą regułą, zapisuje jego cennik, analizuje pakiety tak samo jak analiza_pakietow.py
i zestawia wyniki dla miast oraz dla pakietów.

wyniki/cenniki/<miasto>.csv   pełny cennik punktu (badania i pakiety)
wyniki/analizy/<miasto>.json  analiza każdego pakietu w mieście
wyniki/*.csv, porownanie.json zestawienia dla miast i pakietów
"""

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from analiza_pakietow import OPLACALNY, analyze_packages
from diag_api import (
    DEFAULT_DELAY,
    DiagClient,
    pick_city_point,
    point_name,
    product_row,
    save_csv,
    save_json,
    setup_console,
    slugify,
)

# 49 miast i ich współrzędne z Wikipedii (szerokość, długość).
CITIES = {
    "Biała Podlaska": (52.032, 23.116),
    "Białystok": (53.132, 23.169),
    "Bielsko-Biała": (49.822, 19.045),
    "Bydgoszcz": (53.124, 18.003),
    "Chełm": (51.134, 23.470),
    "Ciechanów": (52.882, 20.609),
    "Częstochowa": (50.812, 19.128),
    "Elbląg": (54.161, 19.410),
    "Gdańsk": (54.352, 18.646),
    "Gorzów Wielkopolski": (52.731, 15.238),
    "Jelenia Góra": (50.903, 15.735),
    "Kalisz": (51.761, 18.087),
    "Katowice": (50.264, 19.024),
    "Kielce": (50.872, 20.631),
    "Konin": (52.229, 18.245),
    "Koszalin": (54.190, 16.182),
    "Kraków": (50.062, 19.938),
    "Krosno": (49.692, 21.767),
    "Legnica": (51.206, 16.163),
    "Leszno": (51.840, 16.580),
    "Lublin": (51.251, 22.575),
    "Łomża": (53.173, 22.069),
    "Łódź": (51.759, 19.459),
    "Nowy Sącz": (49.620, 20.702),
    "Olsztyn": (53.776, 20.488),
    "Opole": (50.668, 17.929),
    "Ostrołęka": (53.081, 21.571),
    "Piła": (53.151, 16.738),
    "Piotrków Trybunalski": (51.409, 19.686),
    "Płock": (52.546, 19.687),
    "Poznań": (52.411, 16.938),
    "Przemyśl": (49.782, 22.774),
    "Radom": (51.401, 21.158),
    "Rzeszów": (50.041, 21.999),
    "Siedlce": (52.164, 22.288),
    "Sieradz": (51.596, 18.732),
    "Skierniewice": (51.956, 20.147),
    "Słupsk": (54.467, 17.027),
    "Suwałki": (54.101, 22.928),
    "Szczecin": (53.425, 14.551),
    "Tarnobrzeg": (50.569, 21.677),
    "Tarnów": (50.013, 20.988),
    "Toruń": (53.016, 18.619),
    "Wałbrzych": (50.771, 16.274),
    "Warszawa": (52.230, 21.012),
    "Włocławek": (52.655, 19.066),
    "Wrocław": (51.108, 17.039),
    "Zamość": (50.723, 23.252),
    "Zielona Góra": (51.939, 15.504),
}

RESULTS_DIR = Path("wyniki")
ANALYSES_DIR = RESULTS_DIR / "analizy"
PRICE_LISTS_DIR = RESULTS_DIR / "cenniki"


def city_report(client: DiagClient, city: str, refresh: bool) -> Optional[Dict[str, Any]]:
    """Cennik i analiza pakietów w mieście. Wynik pobrany dzisiaj jest używany ponownie."""
    path = ANALYSES_DIR / f"{slugify(city)}.json"
    price_list = PRICE_LISTS_DIR / f"{slugify(city)}.csv"
    today = date.today().isoformat()
    if path.exists() and price_list.exists() and not refresh:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("data_pobrania") == today:
            print(f"[=] {city}: wynik z dzisiaj już jest, pomijam pobieranie.")
            return report

    point = pick_city_point(client.search_institutions(city), city)
    if point is None:
        print(f"[!] {city}: nie znaleziono punktu pobrań.", file=sys.stderr)
        return None
    print(f"[*] {city}: {point_name(point['name'])} (ID {point['id']})")
    products = client.fetch_products(point["id"])
    rows = analyze_packages(client.fetch_packages(point["id"]), products)
    save_csv(sorted((product_row(item) for item in products), key=lambda row: row["nazwa"].lower()),
             price_list, excel=False)
    report = {
        "miasto": city,
        "punkt": point_name(point["name"]),
        "id_punktu": point["id"],
        "data_pobrania": today,
        "pakiety": rows,
    }
    save_json(report, path)
    return report


def package_stats(report: Dict[str, Any]) -> Dict[str, Any]:
    """Jak bardzo w danym cenniku opłacają się pakiety z co najmniej dwoma badaniami."""
    multi = [
        row for row in report["pakiety"]
        if row["oszczednosc_proc"] is not None and row["liczba_badan"] > 1
    ]
    cheaper = sum(row["werdykt"] == OPLACALNY for row in multi)
    return {
        "pakiety_2plus": len(multi),
        "tansze_2plus": cheaper,
        "tansze_2plus_proc": round(cheaper / len(multi) * 100.0, 1) if multi else None,
        "mediana_oszczednosci_2plus_proc": (
            round(statistics.median(row["oszczednosc_proc"] for row in multi), 1) if multi else None
        ),
    }


def city_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    latitude, longitude = CITIES[report["miasto"]]
    return {
        "miasto": report["miasto"],
        "punkt": report["punkt"],
        "id_punktu": report["id_punktu"],
        **package_stats(report),
        "szerokosc_geogr": latitude,
        "dlugosc_geogr": longitude,
    }


def package_summary(city_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pakiet w skali kraju: wartości z miasta środkowego (mediana oszczędności) i liczba miast,
    w których się opłaca. Klucze jak w analizie jednego punktu, więc wykresy działają tak samo.
    """
    first = city_rows[0]
    compared = sorted(
        (row for row in city_rows if row["oszczednosc_proc"] is not None),
        key=lambda row: row["oszczednosc_proc"],
    )
    # Ceny, oszczędność i werdykt z jednego miasta, żeby się ze sobą zgadzały.
    middle = compared[(len(compared) - 1) // 2] if compared else first
    # Większość pakietów kosztuje wszędzie tyle samo, ale część (np. Sprawdzam) wycenia punkt.
    prices = [row["cena_pakietu_pln"] for row in city_rows if row["cena_pakietu_pln"] is not None]
    return {
        "id": first["id"],
        "nazwa_pakietu": first["nazwa_pakietu"],
        "kod_pakietu": first["kod_pakietu"],
        "cena_pakietu_pln": middle["cena_pakietu_pln"],
        "cena_min_pln": min(prices, default=None),
        "cena_max_pln": max(prices, default=None),
        "liczba_badan": first["liczba_badan"],
        "suma_osobno_pln": middle["suma_osobno_pln"],
        "oszczednosc_pln": middle["oszczednosc_pln"],
        "oszczednosc_proc": middle["oszczednosc_proc"],
        "werdykt": middle["werdykt"],
        "miasta_z_danymi": len(compared),
        "oplaca_sie_w_miastach": sum(row["werdykt"] == OPLACALNY for row in compared),
        "min_oszczednosc_proc": min((row["oszczednosc_proc"] for row in compared), default=None),
        "max_oszczednosc_proc": max((row["oszczednosc_proc"] for row in compared), default=None),
        "wysylkowy": first["wysylkowy"],
        "z_konsultacja": first["z_konsultacja"],
        "url": first["url"],
    }


def price_index(reports: List[Dict[str, Any]]) -> List[Optional[float]]:
    """
    Poziom cen pojedynczych badań w mieście: ile kosztują osobno badania ze wszystkich pakietów,
    które da się porównać w każdym mieście, względem miasta środkowego (100 = mediana miast).
    """
    sums = [
        {row["id"]: row["suma_osobno_pln"] for row in report["pakiety"] if row["suma_osobno_pln"] is not None}
        for report in reports
    ]
    common = set.intersection(*(set(city) for city in sums)) if sums else set()
    if not common:
        return [None] * len(reports)
    baskets = [sum(city[package_id] for package_id in common) for city in sums]
    middle = statistics.median(baskets)
    return [round(basket / middle * 100.0, 1) for basket in baskets]


def compare(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Zestawienie dla wszystkich miast: podsumowanie miast, pakiety w skali kraju i macierz oszczędności."""
    summaries = [city_summary(report) for report in reports]
    for summary, index in zip(summaries, price_index(reports)):
        summary["indeks_cen_badan"] = index
    cities = sorted(
        summaries,
        key=lambda row: (row["mediana_oszczednosci_2plus_proc"] is None, -(row["mediana_oszczednosci_2plus_proc"] or 0.0)),
    )

    by_package: Dict[Any, List[Dict[str, Any]]] = {}
    savings_by_city: Dict[Any, Dict[str, Optional[float]]] = {}
    for report in reports:
        for row in report["pakiety"]:
            by_package.setdefault(row["id"], []).append(row)
            savings_by_city.setdefault(row["id"], {})[report["miasto"]] = row["oszczednosc_proc"]

    packages = [package_summary(rows) for rows in by_package.values()]
    packages.sort(key=lambda row: (
        row["oszczednosc_proc"] is None,
        -(row["oszczednosc_proc"] or 0.0),
        row["nazwa_pakietu"].lower(),
    ))
    # Kolejność alfabetyczna po polsku, tak jak w CITIES (zwykłe sortowanie wstawiłoby Łódź za Zieloną Górę).
    fetched = {report["miasto"] for report in reports}
    city_names = [city for city in CITIES if city in fetched]
    matrix = [
        {"id": row["id"], "nazwa_pakietu": row["nazwa_pakietu"],
         **{city: savings_by_city[row["id"]].get(city) for city in city_names}}
        for row in packages
    ]
    return {"miasta": cities, "pakiety": packages, "macierz": matrix}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Porównuje opłacalność pakietów w {len(CITIES)} miastach."
    )
    parser.add_argument(
        "-m", "--miasta",
        nargs="+",
        choices=list(CITIES),
        metavar="MIASTO",
        help="tylko wybrane miasta (domyślnie wszystkie 49)",
    )
    parser.add_argument(
        "--odswiez",
        action="store_true",
        help="pobierz dane od nowa, nawet jeśli dzisiejszy wynik miasta już jest w wyniki/",
    )
    parser.add_argument(
        "-d", "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"przerwa między zapytaniami do API w sekundach (domyślnie: {DEFAULT_DELAY})",
    )
    return parser.parse_args()


def main():
    setup_console()
    args = parse_arguments()
    client = DiagClient(delay=args.delay)
    cities = args.miasta or list(CITIES)

    reports, failed = [], []
    for number, city in enumerate(cities, 1):
        print(f"\n=== [{number}/{len(cities)}] {city} ===")
        try:
            report = city_report(client, city, args.odswiez)
        except requests.RequestException as exc:
            print(f"[!] {city}: błąd połączenia z API: {exc}", file=sys.stderr)
            report = None
        if report is None:
            failed.append(city)
        else:
            reports.append(report)

    if not reports:
        sys.exit("[!] Nie udało się pobrać danych dla żadnego miasta.")

    result = compare(reports)
    save_csv(result["miasta"], RESULTS_DIR / "miasta.csv", excel=False)
    save_csv(result["pakiety"], RESULTS_DIR / "pakiety.csv", excel=False)
    save_csv(result["macierz"], RESULTS_DIR / "oszczednosci_w_miastach.csv", excel=False)
    save_json(
        {
            "punkt": f"mediana z {len(reports)} miast",
            "data_pobrania": max(report["data_pobrania"] for report in reports),
            "miasta": result["miasta"],
            "pakiety": result["pakiety"],
        },
        RESULTS_DIR / "porownanie.json",
    )

    print(f"\n=== PODSUMOWANIE: {len(reports)}/{len(cities)} miast ===")
    for row in result["miasta"]:
        print(
            f"  {row['miasto']:22s} tańszych pakietów 2+: {row['tansze_2plus_proc']}%  "
            f"mediana oszczędności: {row['mediana_oszczednosci_2plus_proc']}%"
        )
    if failed:
        print(f"[!] Bez danych: {', '.join(failed)}. Uruchom ponownie, żeby dociągnąć brakujące miasta.", file=sys.stderr)


if __name__ == "__main__":
    main()
