"""
Wszystkie punkty pobrań Diagnostyki: do którego cennika należy każdy punkt i ile w nim
oszczędzasz na pakietach.

1. Lista punktów z wyszukiwarki (puste zapytanie zwraca wszystkie, około 1250).
2. Dla każdego punktu adres ze współrzędnymi i pierwsza strona cennika (200 pozycji), która
   działa jak odcisk palca: punkty z tymi samymi cenami korzystają z tego samego cennika.
3. Pełny cennik i analiza pakietów tylko raz na cennik. Cenniki 49 miast z porownanie_miast.py
   są używane ponownie, więc nowe pobiera się tylko dla cenników, których jeszcze nie ma.

wyniki/punkty.csv    każdy punkt: miasto, województwo, współrzędne, cennik i oszczędność
wyniki/cenniki.csv   każdy cennik: ile punktów i miast go używa, poziom cen i oszczędność
dane/punkty/         odciski cenników punktów, tylko lokalnie (pozwalają wznowić przerwane pobieranie)
"""

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from analiza_pakietow import analyze_packages
from diag_api import (
    DATA_DIR,
    DEFAULT_DELAY,
    PER_PAGE,
    DiagClient,
    point_name,
    product_row,
    save_csv,
    save_json,
    setup_console,
    slugify,
)
from porownanie_miast import ANALYSES_DIR, PRICE_LISTS_DIR, RESULTS_DIR, package_stats, price_index

POINTS_DIR = DATA_DIR / "punkty"
SEARCH_PAGE = 50  # wyszukiwarka punktów zwraca najwyżej 50 wyników na stronę
# Punkt należy do cennika, jeśli na pierwszej stronie zgadza się co najmniej tyle wspólnych cen.
MIN_COMMON_PRICES = 80

Prices = Dict[str, float]


def list_points(client: DiagClient) -> List[Dict[str, Any]]:
    """Wszystkie aktywne punkty: wyszukiwarka z pustym zapytaniem zwraca całą listę stronami."""
    points: Dict[int, Dict[str, Any]] = {}
    page = last_page = 1
    while page <= last_page:
        data = client.get("institution-service/institutions/search", {"q": "", "limit": SEARCH_PAGE, "page": page})
        last_page = (data.get("meta") or {}).get("last_page", 1)
        for point in data.get("data", []):
            if not point.get("temporaryDisabled"):
                points[point["id"]] = point
        print(f"    -> lista punktów: strona {page}/{last_page}")
        page += 1
    return sorted(points.values(), key=lambda point: point["id"])


def point_fingerprint(client: DiagClient, point: Dict[str, Any], refresh: bool) -> Dict[str, Any]:
    """Adres, współrzędne i pierwsza strona cennika punktu. Wynik leży w dane/punkty/ i jest używany ponownie."""
    path = POINTS_DIR / f"{point['id']}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))

    detail = client.get(f"institution-service/institutions/{point['id']}", {})
    detail = detail.get("data", detail)
    address = detail.get("address") or {}
    city = address.get("city") or {}
    geo = detail.get("geolocation") or {}
    page = client.get("products", {"institution": point["id"], "page": 1, "per_page": PER_PAGE})
    info = {
        "id": point["id"],
        "punkt": point_name(point.get("name") or ""),
        "miasto": city.get("name") or "",
        "wojewodztwo": (city.get("state") or {}).get("name") or "",
        "typ": (detail.get("institutionType") or {}).get("name") or "",
        "szerokosc_geogr": geo.get("latitude"),
        "dlugosc_geogr": geo.get("longitude"),
        "ceny": {str(item["id"]): item["price"] for item in page.get("data", []) if item.get("price")},
    }
    save_json(info, path)
    return info


def load_price_lists() -> Dict[str, Prices]:
    """Cenniki zapisane już w wyniki/cenniki/ (ceny online po ID produktu)."""
    lists = {}
    for path in sorted(PRICE_LISTS_DIR.glob("*.csv")):
        with path.open(encoding="utf-8") as csvfile:
            lists[path.stem] = {
                row["id"]: float(row["cena_online_pln"]) for row in csv.DictReader(csvfile) if row["cena_online_pln"]
            }
    return lists


def matching_list(prices: Prices, lists: Dict[str, Prices]) -> Optional[str]:
    """Cennik, z którym zgadzają się wszystkie wspólne ceny z pierwszej strony punktu."""
    for name, known in lists.items():
        common = [product_id for product_id in prices if product_id in known]
        if len(common) >= MIN_COMMON_PRICES and all(abs(known[pid] - prices[pid]) < 0.005 for pid in common):
            return name
    return None


def fetch_price_list(client: DiagClient, info: Dict[str, Any], name: str) -> Prices:
    """Pełny cennik i analiza pakietów dla nowego cennika, zapisane pod nazwą miasta punktu."""
    print(f"[*] Nowy cennik '{name}': {info['punkt']} (ID {info['id']})")
    products = client.fetch_products(info["id"])
    rows = analyze_packages(client.fetch_packages(info["id"]), products)
    catalog = sorted((product_row(item) for item in products), key=lambda row: row["nazwa"].lower())
    save_csv(catalog, PRICE_LISTS_DIR / f"{name}.csv", excel=False)
    save_json(
        {"miasto": info["miasto"], "punkt": info["punkt"], "id_punktu": info["id"],
         "data_pobrania": date.today().isoformat(), "pakiety": rows},
        ANALYSES_DIR / f"{name}.json",
    )
    return {str(row["id"]): row["cena_online_pln"] for row in catalog if row["cena_online_pln"]}


def assign_price_lists(client: DiagClient, infos: List[Dict[str, Any]]) -> Dict[int, Optional[str]]:
    """Przypisuje każdemu punktowi cennik; brakujące cenniki pobiera w całości."""
    lists = load_price_lists()
    assigned: Dict[int, Optional[str]] = {}
    for info in infos:
        prices = {pid: float(price) for pid, price in info["ceny"].items()}
        if len(prices) < MIN_COMMON_PRICES:
            assigned[info["id"]] = None  # punkt bez cen w sklepie (np. punkt partnera)
            continue
        name = matching_list(prices, lists)
        if name is None:
            name = slugify(info["miasto"] or info["punkt"])
            if name in lists:
                name = f"{name}_{info['id']}"
            lists[name] = fetch_price_list(client, info, name)
        assigned[info["id"]] = name
    return assigned


def fill_missing_coordinates(infos: List[Dict[str, Any]]) -> None:
    """Punkt bez współrzędnych dostaje średnie położenie innych punktów z tego samego miasta."""
    by_city: Dict[str, List[Dict[str, Any]]] = {}
    for info in infos:
        if info["szerokosc_geogr"] is not None:
            by_city.setdefault(info["miasto"], []).append(info)
    for info in infos:
        known = by_city.get(info["miasto"])
        if info["szerokosc_geogr"] is None and known:
            info["szerokosc_geogr"] = round(statistics.mean(i["szerokosc_geogr"] for i in known), 5)
            info["dlugosc_geogr"] = round(statistics.mean(i["dlugosc_geogr"] for i in known), 5)


def summarize(infos: List[Dict[str, Any]], assigned: Dict[int, Optional[str]]) -> Dict[str, List[Dict[str, Any]]]:
    """Wiersze punktów i cenników do wyniki/punkty.csv i wyniki/cenniki.csv."""
    used = sorted({name for name in assigned.values() if name})
    reports = [json.loads((ANALYSES_DIR / f"{name}.json").read_text(encoding="utf-8")) for name in used]
    stats = {}
    for name, report, index in zip(used, reports, price_index(reports)):
        stats[name] = {**package_stats(report), "indeks_cen_badan": index}

    points = []
    for info in infos:
        name = assigned.get(info["id"])
        group = stats.get(name, {})
        points.append({
            "id": info["id"],
            "punkt": info["punkt"],
            "miasto": info["miasto"],
            "wojewodztwo": info["wojewodztwo"],
            "typ": info["typ"],
            "szerokosc_geogr": info["szerokosc_geogr"],
            "dlugosc_geogr": info["dlugosc_geogr"],
            "cennik": name or "",
            "indeks_cen_badan": group.get("indeks_cen_badan"),
            "tansze_2plus_proc": group.get("tansze_2plus_proc"),
            "mediana_oszczednosci_2plus_proc": group.get("mediana_oszczednosci_2plus_proc"),
        })

    lists = []
    for name in used:
        members = [point for point in points if point["cennik"] == name]
        cities = Counter(point["miasto"] for point in members)
        lists.append({
            "cennik": name,
            "punkty": len(members),
            "miasta": len(cities),
            "glowne_miasta": ", ".join(city for city, _ in cities.most_common(5)),
            "wojewodztwa": ", ".join(sorted({point["wojewodztwo"] for point in members if point["wojewodztwo"]})),
            "indeks_cen_badan": stats[name]["indeks_cen_badan"],
            "tansze_2plus_proc": stats[name]["tansze_2plus_proc"],
            "mediana_oszczednosci_2plus_proc": stats[name]["mediana_oszczednosci_2plus_proc"],
        })
    lists.sort(key=lambda row: -row["punkty"])
    return {"punkty": points, "cenniki": lists}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Przypisuje cennik każdemu punktowi pobrań Diagnostyki i liczy w nim opłacalność pakietów."
    )
    parser.add_argument("--odswiez", action="store_true",
                        help="pobierz odciski punktów od nowa, nawet jeśli już są w dane/punkty/")
    parser.add_argument("-d", "--delay", type=float, default=DEFAULT_DELAY,
                        help=f"przerwa między zapytaniami do API w sekundach (domyślnie: {DEFAULT_DELAY})")
    return parser.parse_args()


def main():
    setup_console()
    args = parse_arguments()
    client = DiagClient(delay=args.delay)

    points = list_points(client)
    print(f"[+] Aktywnych punktów: {len(points)}")
    infos, failed = [], []
    for number, point in enumerate(points, 1):
        try:
            infos.append(point_fingerprint(client, point, args.odswiez))
        except requests.RequestException as exc:
            print(f"[!] Punkt {point['id']}: błąd połączenia z API: {exc}", file=sys.stderr)
            failed.append(point["id"])
        if number % 50 == 0:
            print(f"[*] Odciski cenników: {number}/{len(points)}")

    assigned = assign_price_lists(client, infos)
    fill_missing_coordinates(infos)
    result = summarize(infos, assigned)
    save_csv(result["punkty"], RESULTS_DIR / "punkty.csv", excel=False)
    save_csv(result["cenniki"], RESULTS_DIR / "cenniki.csv", excel=False)
    save_json({"data_pobrania": date.today().isoformat(), **result}, RESULTS_DIR / "punkty.json")

    print(f"\n=== PODSUMOWANIE: {len(infos)} punktów, {len(result['cenniki'])} cenników ===")
    for row in result["cenniki"]:
        print(f"  {row['cennik']:28s} punktów {row['punkty']:4d}  indeks {row['indeks_cen_badan']}  "
              f"mediana {row['mediana_oszczednosci_2plus_proc']}%  ({row['glowne_miasta']})")
    without = sum(1 for name in assigned.values() if name is None)
    if without:
        print(f"[!] Punkty bez cen w sklepie (pominięte w cennikach): {without}")
    if failed:
        print(f"[!] Bez danych: {len(failed)} punktów. Uruchom ponownie, żeby je dociągnąć.", file=sys.stderr)


if __name__ == "__main__":
    main()
