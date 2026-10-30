"""
Czy pakiet się opłaca? Porównuje cenę każdego pakietu badań Diagnostyki
z ceną tych samych badań kupionych osobno w tym samym punkcie pobrań.
"""

import argparse
import statistics
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from diag_api import (
    API_BASE_URL,
    DATA_DIR,
    DEFAULT_DELAY,
    DEFAULT_INSTITUTION_ID,
    DEFAULT_INSTITUTION_NAME,
    DiagClient,
    format_pln,
    product_row,
    resolve_institution,
    save_csv,
    save_json,
    setup_console,
)

OPLACALNY = "OPŁACALNY"
BEZ_ROZNICY = "BEZ RÓŻNICY"
DROZSZY = "DROŻSZY NIŻ OSOBNO"
BRAK_DANYCH = "BRAK DANYCH"
VERDICTS = [OPLACALNY, BEZ_ROZNICY, DROZSZY, BRAK_DANYCH]
# Różnica poniżej złotówki to zaokrąglenia rabatu online, a nie realna oszczędność.
TOLERANCE_PLN = 1.0

DEFAULT_OUTPUT = DATA_DIR / "analiza_oplacalnosci_pakietow"

Catalog = Dict[Any, Dict[str, Any]]


def build_catalog(products: List[Dict[str, Any]]) -> Catalog:
    """Cennik punktu pobrań (badania i pakiety) po ID produktu."""
    return {item.get("id"): product_row(item) for item in products}


def separate_price(test: Dict[str, Any]) -> Optional[float]:
    """
    Najtańszy zakup badania osobno: online, a jeśli sklep online go nie sprzedaje,
    to w punkcie pobrań. Cena 0 oznacza brak ceny, a nie darmowe badanie.
    """
    if not test["cena_online_pln"] or test["status_sprzedazy"] == "institution-not-supported":
        return None
    return test["cena_online_pln"] if test["dostepne_online"] else test["cena_w_punkcie_pln"]


def package_price(package: Dict[str, Any], catalog: Catalog) -> Optional[float]:
    """
    Cena pakietu w punkcie. Wyszukiwarka pakietów zwraca dla części z nich 0,
    choć cennik ma prawdziwą cenę, więc najpierw sprawdzamy cennik.
    """
    listed = catalog.get(package.get("id")) or {}
    if listed and listed["status_sprzedazy"] != "available":
        return None
    price = listed.get("cena_online_pln") or package.get("price")
    return float(price) if price else None


def verdict(savings: Optional[float]) -> str:
    if savings is None:
        return BRAK_DANYCH
    if savings >= TOLERANCE_PLN:
        return OPLACALNY
    if savings <= -TOLERANCE_PLN:
        return DROZSZY
    return BEZ_ROZNICY


def analyze_package(package: Dict[str, Any], catalog: Catalog) -> Dict[str, Any]:
    """Porównuje cenę pakietu z sumą cen jego badań kupionych osobno."""
    components = package.get("bloodTests") or []
    price = package_price(package, catalog)

    names, unpriced = [], []
    separately = 0.0
    for component in components:
        test = catalog.get(component.get("id"))
        name = test["nazwa"] if test else (component.get("name") or "").strip()
        names.append(name)
        single = separate_price(test) if test else None
        if single is None:
            unpriced.append(name)
        else:
            separately += single

    notes = []
    if price is None:
        notes.append("pakiet bez ceny w tym punkcie")
    if not components:
        notes.append("API nie podaje składu pakietu")
    if unpriced:
        notes.append("brak ceny badania: " + ", ".join(unpriced))

    total = savings = percent = None
    if not notes:
        total = round(separately, 2)
        savings = round(total - price, 2)
        percent = round(savings / total * 100.0, 1)

    return {
        "id": package.get("id"),
        "nazwa_pakietu": (package.get("title") or "").strip(),
        "kod_pakietu": str(package.get("code") or ""),
        "cena_pakietu_pln": price,
        "liczba_badan": len(components),
        "suma_osobno_pln": total,
        "oszczednosc_pln": savings,
        "oszczednosc_proc": percent,
        "werdykt": verdict(savings),
        "wysylkowy": bool(package.get("isBoxPackage")),
        "z_konsultacja": bool(package.get("haveConsultation")),
        "uwagi": "; ".join(notes),
        "badania_w_pakiecie": " | ".join(names),
        "url": package.get("url") or "",
    }


def analyze_packages(packages: List[Dict[str, Any]], products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Analizuje wszystkie pakiety; najbardziej opłacalne na początku, bez danych na końcu."""
    catalog = build_catalog(products)
    rows = [analyze_package(package, catalog) for package in packages]
    rows.sort(key=lambda row: (
        row["oszczednosc_proc"] is None,
        -(row["oszczednosc_proc"] or 0.0),
        row["nazwa_pakietu"].lower(),
    ))
    return rows


def print_summary(rows: List[Dict[str, Any]]) -> None:
    counts = Counter(row["werdykt"] for row in rows)
    print(f"\n=== PODSUMOWANIE: {len(rows)} pakietów ===")
    for name in VERDICTS:
        print(f"  {name}: {counts.get(name, 0)}")

    multi = [row["oszczednosc_proc"] for row in rows if row["oszczednosc_proc"] is not None and row["liczba_badan"] > 1]
    if multi:
        print(f"  Mediana oszczędności w pakietach z co najmniej 2 badaniami: {statistics.median(multi):.1f}%")

    compared = [row for row in rows if row["oszczednosc_pln"] is not None]
    print("\nNajwiększa oszczędność procentowa:")
    for row in compared[:5]:
        print(f"  + {row['oszczednosc_proc']:5.1f}%  {row['nazwa_pakietu']} ({format_pln(row['cena_pakietu_pln'])})")
    print("Najbardziej przepłacone:")
    for row in reversed(compared[-5:]):
        print(f"  - {row['oszczednosc_proc']:5.1f}%  {row['nazwa_pakietu']} ({format_pln(row['cena_pakietu_pln'])})")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sprawdza, czy pakiety badań Diagnostyki są tańsze niż te same badania kupione osobno."
    )
    parser.add_argument(
        "-p", "--point",
        help=f"wyszukaj punkt pobrań po nazwie, mieście lub ulicy (domyślnie: '{DEFAULT_INSTITUTION_NAME}')",
    )
    parser.add_argument(
        "-i", "--institution-id",
        type=int,
        help=f"ID punktu pobrań; ma pierwszeństwo przed --point (domyślnie: {DEFAULT_INSTITUTION_ID})",
    )
    parser.add_argument(
        "-o", "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"ścieżka plików wynikowych bez rozszerzenia (domyślnie: {DEFAULT_OUTPUT.as_posix()})",
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
    institution_id, institution_name = resolve_institution(client, args.point, args.institution_id)

    print("=== ANALIZA OPŁACALNOŚCI PAKIETÓW ===")
    print(f"Punkt: {institution_name} (ID: {institution_id})")
    products = client.fetch_products(institution_id)
    packages = client.fetch_packages(institution_id)
    rows = analyze_packages(packages, products)

    save_csv(rows, Path(f"{args.output}.csv"))
    save_json(
        {
            "punkt": institution_name,
            "id_punktu": institution_id,
            "data_pobrania": date.today().isoformat(),
            "zrodlo": API_BASE_URL,
            "pakiety": rows,
        },
        Path(f"{args.output}.json"),
    )
    print_summary(rows)


if __name__ == "__main__":
    main()
