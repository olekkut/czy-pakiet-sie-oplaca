"""
Wspólny kod skryptów: klient publicznego API e-sklepu Diagnostyki (api-eshop.diag.pl),
wyliczanie ceny w punkcie pobrań i zapis wyników do CSV/JSON.
"""

import csv
import json
import math
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

API_BASE_URL = "https://api-eshop.diag.pl/api/v1"
REPO_URL = "https://github.com/olekkut/czy-pakiet-sie-oplaca"
# Skrypt przedstawia się wprost, zamiast udawać przeglądarkę.
USER_AGENT = f"czy-pakiet-sie-oplaca/1.0 (+{REPO_URL})"

PER_PAGE = 200  # największa strona, jaką przyjmuje API
DEFAULT_DELAY = 0.5  # przerwa między zapytaniami w sekundach
DEFAULT_INSTITUTION_ID = 295
DEFAULT_INSTITUTION_NAME = "Warszawa, al. Dwudziestolatków 3"
DATA_DIR = Path("dane")

# Mapowanie typów badań na czytelne etykiety
TYPE_LABELS = {
    "bloodTest": "Badanie laboratoryjne",
    "package": "Pakiet badań",
}


def create_session() -> requests.Session:
    """Tworzy sesję HTTP z automatycznym ponawianiem nieudanych żądań."""
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


class DiagClient:
    """Pobiera dane strona po stronie, z przerwą między zapytaniami, żeby nie obciążać serwera."""

    def __init__(self, delay: float = DEFAULT_DELAY, session: Optional[requests.Session] = None):
        self.delay = delay
        self.session = session or create_session()
        self._last_request = 0.0

    def get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        pause = self._last_request + self.delay - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        response = self.session.get(f"{API_BASE_URL}/{path}", params=params, timeout=30)
        self._last_request = time.monotonic()
        response.raise_for_status()
        return response.json()

    def get_all_pages(self, path: str, params: Dict[str, Any], label: str) -> List[Dict[str, Any]]:
        """Pobiera wszystkie strony wyników. Pozycja powtórzona na dwóch stronach liczy się raz."""
        items: Dict[Any, Dict[str, Any]] = {}
        page = last_page = 1
        total = None
        while page <= last_page:
            data = self.get(path, {**params, "page": page, "per_page": PER_PAGE})
            meta = data.get("meta") or {}
            last_page = meta.get("last_page", 1)
            total = meta.get("total", total)
            for item in data.get("data", []):
                items[item.get("id")] = item
            print(f"    -> {label}: strona {page}/{last_page}")
            page += 1

        if total is not None and total != len(items):
            print(f"[!] API podało {total} pozycji ({label}), a pobrano {len(items)}.", file=sys.stderr)
        return list(items.values())

    def search_institutions(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Wyszukuje punkty pobrań po nazwie, mieście lub ulicy."""
        data = self.get("institution-service/institutions/search", {"q": query, "limit": limit})
        return data.get("data", [])

    def fetch_products(self, institution_id: int, ecommerce_only: bool = False) -> List[Dict[str, Any]]:
        """Cennik punktu pobrań: badania pojedyncze i pakiety."""
        params: Dict[str, Any] = {"institution": institution_id}
        if ecommerce_only:
            params["ecommerce"] = 1
        return self.get_all_pages("products", params, "badania")

    def fetch_packages(self, institution_id: int) -> List[Dict[str, Any]]:
        """Pakiety badań razem z listą badań, które wchodzą w ich skład."""
        return self.get_all_pages("packages/search", {"institution": institution_id}, "pakiety")


def point_name(name: str) -> str:
    """'Punkt Pobrań Diagnostyki - Kraków, ul. Olszańska 5' -> 'Kraków, ul. Olszańska 5'"""
    return name.split(" - ", 1)[-1].strip()


def city_of(name: str) -> str:
    """Miasto z nazwy punktu, także w sieciach regionalnych, np. 'Diagnostyka Tarnów - Krosno, ...'."""
    return point_name(name).split(",")[0].strip()


def pick_city_point(results: List[Dict[str, Any]], city: str) -> Optional[Dict[str, Any]]:
    """
    Wybiera punkt reprezentatywny dla miasta, zawsze ten sam: spośród aktywnych punktów
    w mieście bierze te z najczęstszą tam wartością pola testPriceId, a z nich ten o najniższym ID.
    """
    in_city = sorted(
        (inst for inst in results
         if city_of(inst.get("name") or "").lower() == city.lower() and not inst.get("temporaryDisabled")),
        key=lambda inst: inst["id"],
    )
    if not in_city:
        return None
    counts = Counter(inst.get("testPriceId") for inst in in_city)
    price_list = max(counts, key=counts.get)
    return next(inst for inst in in_city if inst.get("testPriceId") == price_list)


def resolve_institution(
    client: DiagClient,
    point: Optional[str],
    institution_id: Optional[int],
) -> Tuple[int, str]:
    """Ustala punkt pobrań: po podanym ID, po wyszukaniu nazwy albo domyślny."""
    if institution_id is not None:
        return institution_id, point or f"punkt ID {institution_id}"
    if not point:
        return DEFAULT_INSTITUTION_ID, DEFAULT_INSTITUTION_NAME

    print(f"[*] Wyszukiwanie punktu pobrań dla zapytania: '{point}'...")
    results = client.search_institutions(point)
    if not results:
        sys.exit(f"[!] Nie znaleziono żadnego punktu dla zapytania '{point}'.")

    print(f"[+] Znalezione punkty pobrań ({len(results)}):")
    for idx, inst in enumerate(results[:5], 1):
        print(f"    {idx}. ID {inst.get('id')}: {inst.get('name')}")
    # Sama nazwa miasta ("Warszawa") trafia też w ulice innych miast, więc najpierw szukamy punktu w tym mieście.
    chosen = pick_city_point(results, point) or results[0]
    name = point_name(chosen.get("name") or point)
    print(f"[*] Wybrano: {name} (ID {chosen['id']}). Inny punkt wskażesz opcją --institution-id.")
    return int(chosen["id"]), name


def point_price(online_price: Optional[float]) -> Optional[float]:
    """
    Cena w punkcie pobrań. Strona diag.pl wylicza ją z ceny online wzorem
    Math.floor(cena_online / 0.95 * 100) / 100, czyli online jest o 5% taniej.
    """
    if online_price is None:
        return None
    return round(math.floor(float(online_price) / 0.95 * 100.0) / 100.0, 2)


def product_row(item: Dict[str, Any]) -> Dict[str, Any]:
    """Konwertuje surowy rekord z API do ustrukturyzowanej formy."""
    raw_type = item.get("type") or ""
    price = item.get("price")
    online = float(price) if price is not None else None
    return {
        "id": item.get("id"),
        "nazwa": (item.get("title") or "").strip(),
        "kod_badania": str(item.get("code") or ""),
        # Pakiety nie mają ceny stacjonarnej.
        "cena_w_punkcie_pln": point_price(online) if raw_type != "package" else None,
        "cena_online_pln": online,
        "typ": TYPE_LABELS.get(raw_type, raw_type),
        "dostepne_online": bool(item.get("isEcommerce")),
        "status_sprzedazy": item.get("saleState") or "",
        "kod_icd": item.get("codeICD") or "",
        "url_szczegoly": item.get("url") or "",
        "oplata_pobranie_zawarta": bool(item.get("isMaterialCollectionFeeInProduct")),
        "stawka_vat": item.get("vatRate") or "",
    }


def csv_cell(value: Any, excel: bool = True) -> Any:
    """Wartość komórki: TAK/NIE, a dla polskiego Excela także przecinek dziesiętny."""
    if isinstance(value, bool):
        return "TAK" if value else "NIE"
    if isinstance(value, float):
        text = f"{value:.2f}"
        return text.replace(".", ",") if excel else text
    return value


def save_csv(rows: List[Dict[str, Any]], path: Path, excel: bool = True) -> None:
    """
    Zapisuje CSV. Domyślnie dla polskiego Excela: UTF-8 z BOM, średnik i przecinek dziesiętny.
    Z excel=False zwykły CSV (przecinek, kropka dziesiętna), który GitHub pokazuje jako tabelę.
    """
    if not rows:
        print("[!] Brak danych do zapisu w CSV.", file=sys.stderr)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig" if excel else "utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=list(rows[0]), delimiter=";" if excel else ",")
        writer.writeheader()
        writer.writerows({key: csv_cell(value, excel) for key, value in row.items()} for row in rows)
    print(f"[+] Zapisano CSV: {path} ({len(rows)} wierszy)")


def save_json(data: Any, path: Path) -> None:
    """Zapisuje dane do pliku JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as jsonfile:
        json.dump(data, jsonfile, indent=2, ensure_ascii=False)
        jsonfile.write("\n")
    print(f"[+] Zapisano JSON: {path}")


def format_pln(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:,.2f} zł".replace(",", " ").replace(".", ",")


def slugify(text: str) -> str:
    """'Zielona Góra, ul. Kupiecka 22' -> 'zielona_gora_ul_kupiecka_22'"""
    text = text.replace("ł", "l").replace("Ł", "L")
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")


def setup_console() -> None:
    """Polskie znaki w konsoli Windows."""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except AttributeError:
                pass
