"""
Pobiera cennik badań Diagnostyki dla wybranego punktu pobrań i zapisuje go do CSV/JSON.
Domyślny punkt: Warszawa, al. Dwudziestolatków 3 (ID: 295)
"""

import argparse
from pathlib import Path

from diag_api import (
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
    slugify,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pobiera cennik badań Diagnostyki dla wybranego punktu pobrań."
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
        "-a", "--all",
        action="store_true",
        help="pobierz też badania dostępne tylko stacjonarnie (domyślnie tylko te ze sklepu online)",
    )
    parser.add_argument(
        "-o", "--output",
        help="ścieżka pliku wyjściowego bez rozszerzenia (domyślnie: dane/badania_<punkt>)",
    )
    parser.add_argument(
        "-f", "--format",
        choices=["all", "csv", "json"],
        default="all",
        help="format pliku wyjściowego (domyślnie: 'all', czyli CSV i JSON)",
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

    print("=== POBIERANIE CENNIKA ===")
    print(f"Punkt: {institution_name} (ID: {institution_id})")
    print(f"Zakres: {'wszystkie badania, także stacjonarne' if args.all else 'badania ze sklepu online'}")

    items = client.fetch_products(institution_id, ecommerce_only=not args.all)
    rows = sorted((product_row(item) for item in items), key=lambda row: row["nazwa"].lower())

    suffix = "_wszystkie" if args.all else ""
    base = args.output or str(DATA_DIR / f"badania_{slugify(institution_name)}{suffix}")
    if args.format in ("all", "csv"):
        save_csv(rows, Path(f"{base}.csv"))
    if args.format in ("all", "json"):
        save_json(rows, Path(f"{base}.json"))

    print(f"\n=== PODSUMOWANIE: {len(rows)} pozycji ===")
    for row in rows[:5]:
        print(
            f"  - {row['nazwa']} (kod: {row['kod_badania']}) | "
            f"w punkcie: {format_pln(row['cena_w_punkcie_pln'])} | online: {format_pln(row['cena_online_pln'])}"
        )


if __name__ == "__main__":
    main()
