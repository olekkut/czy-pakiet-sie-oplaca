"""Testy logiki bez połączenia z API: python -m unittest -v"""

import unittest

from analiza_pakietow import (
    BEZ_ROZNICY,
    BRAK_DANYCH,
    DROZSZY,
    OPLACALNY,
    analyze_packages,
)
from diag_api import DiagClient, city_of, csv_cell, pick_city_point, point_price, slugify
from porownanie_miast import compare


def api_test(test_id, price, ecommerce=True, state="available"):
    return {
        "id": test_id,
        "type": "bloodTest",
        "title": f"Badanie {test_id}",
        "code": str(test_id),
        "price": price,
        "isEcommerce": ecommerce,
        "saleState": state,
    }


def api_package(package_id, price, component_ids):
    return {
        "id": package_id,
        "title": f"Pakiet {package_id}",
        "code": str(package_id),
        "price": price,
        "bloodTests": [{"id": test_id, "name": f"Badanie {test_id}"} for test_id in component_ids],
    }


def analyze_one(package, products):
    [row] = analyze_packages([package], products)
    return row


class PointPriceTest(unittest.TestCase):
    def test_online_is_five_percent_cheaper(self):
        self.assertEqual(point_price(71.25), 75.00)
        self.assertEqual(point_price(185.25), 195.00)
        self.assertEqual(point_price(282.15), 297.00)

    def test_missing_price(self):
        self.assertIsNone(point_price(None))


class AnalyzePackageTest(unittest.TestCase):
    def test_package_cheaper_than_tests(self):
        row = analyze_one(api_package(1, 120, [10, 11]), [api_test(10, 100), api_test(11, 50)])
        self.assertEqual(row["suma_osobno_pln"], 150.0)
        self.assertEqual(row["oszczednosc_pln"], 30.0)
        self.assertEqual(row["oszczednosc_proc"], 20.0)
        self.assertEqual(row["werdykt"], OPLACALNY)

    def test_package_more_expensive(self):
        row = analyze_one(api_package(1, 249, [10]), [api_test(10, 236.55)])
        self.assertEqual(row["oszczednosc_pln"], -12.45)
        self.assertEqual(row["werdykt"], DROZSZY)

    def test_difference_below_one_zloty_is_no_difference(self):
        row = analyze_one(api_package(1, 150.5, [10, 11]), [api_test(10, 100), api_test(11, 50)])
        self.assertEqual(row["werdykt"], BEZ_ROZNICY)

    def test_test_not_sold_online_uses_point_price(self):
        # 616,55 zł online to 649 zł w punkcie, a online tego badania kupić się nie da.
        row = analyze_one(api_package(1, 649, [10]), [api_test(10, 616.55, ecommerce=False, state="not-sellable")])
        self.assertEqual(row["suma_osobno_pln"], 649.0)
        self.assertEqual(row["werdykt"], BEZ_ROZNICY)

    def test_zero_price_in_package_search_falls_back_to_catalog(self):
        package = api_package(1, 0, [10])
        catalog_entry = {**api_package(1, 190, []), "type": "package", "isEcommerce": True, "saleState": "available"}
        row = analyze_one(package, [api_test(10, 250), catalog_entry])
        self.assertEqual(row["cena_pakietu_pln"], 190.0)
        self.assertEqual(row["werdykt"], OPLACALNY)

    def test_zero_price_everywhere_is_not_free(self):
        row = analyze_one(api_package(1, 0, [10]), [api_test(10, 250)])
        self.assertIsNone(row["cena_pakietu_pln"])
        self.assertEqual(row["werdykt"], BRAK_DANYCH)
        self.assertIn("bez ceny", row["uwagi"])

    def test_component_without_price_gives_no_verdict(self):
        row = analyze_one(api_package(1, 100, [10, 11]), [api_test(10, 80), api_test(11, 0, ecommerce=False)])
        self.assertIsNone(row["oszczednosc_pln"])
        self.assertEqual(row["werdykt"], BRAK_DANYCH)
        self.assertIn("Badanie 11", row["uwagi"])

    def test_unknown_component(self):
        row = analyze_one(api_package(1, 100, [10, 99]), [api_test(10, 80)])
        self.assertEqual(row["werdykt"], BRAK_DANYCH)

    def test_package_without_components(self):
        row = analyze_one(api_package(1, 100, []), [])
        self.assertEqual(row["werdykt"], BRAK_DANYCH)
        self.assertIn("składu", row["uwagi"])

    def test_rows_sorted_by_savings_with_missing_data_last(self):
        products = [api_test(10, 100), api_test(11, 100)]
        rows = analyze_packages(
            [api_package(1, 0, [10]), api_package(2, 90, [10]), api_package(3, 50, [11])],
            products,
        )
        self.assertEqual([row["id"] for row in rows], [3, 2, 1])


class FormattingTest(unittest.TestCase):
    def test_csv_cell_for_polish_excel(self):
        self.assertEqual(csv_cell(True), "TAK")
        self.assertEqual(csv_cell(False), "NIE")
        self.assertEqual(csv_cell(1234.5), "1234,50")
        self.assertEqual(csv_cell(3), 3)
        self.assertIsNone(csv_cell(None))

    def test_csv_cell_for_plain_csv(self):
        self.assertEqual(csv_cell(1234.5, excel=False), "1234.50")

    def test_slugify(self):
        self.assertEqual(slugify("Zielona Góra, ul. Kupiecka 22"), "zielona_gora_ul_kupiecka_22")
        self.assertEqual(slugify("Łódź, Piotrkowska"), "lodz_piotrkowska")


def institution(inst_id, city, price_list="A", disabled=False):
    return {
        "id": inst_id,
        "name": f"Punkt Pobrań Diagnostyki - {city}, ul. Testowa {inst_id}",
        "testPriceId": price_list,
        "temporaryDisabled": disabled,
    }


class CityPointTest(unittest.TestCase):
    def test_city_from_point_name(self):
        self.assertEqual(city_of("Punkt Pobrań Diagnostyki - Bielsko-Biała, ul. Żywiecka 71"), "Bielsko-Biała")
        self.assertEqual(city_of("Diagnostyka Tarnów - Krosno, ul. Grodzka 47"), "Krosno")

    def test_street_named_after_city_does_not_count(self):
        results = [{"id": 1, "name": 'Punkt Pobrań Diagnostyki - Mińsk Mazowiecki, ul. 1 Pułku "Warszawa" 5'}]
        self.assertIsNone(pick_city_point(results, "Warszawa"))

    def test_most_common_test_price_id_then_lowest_id(self):
        results = [institution(5, "Kraków", "B"), institution(3, "Kraków", "A"),
                   institution(9, "Kraków", "B"), institution(7, "Kraków", "B")]
        self.assertEqual(pick_city_point(results, "Kraków")["id"], 5)

    def test_disabled_points_are_skipped(self):
        results = [institution(1, "Opole", disabled=True), institution(2, "Opole")]
        self.assertEqual(pick_city_point(results, "Opole")["id"], 2)


class CompareTest(unittest.TestCase):
    def city(self, name, test_price):
        package = api_package(1, 150, [10, 11])
        products = [api_test(10, test_price), api_test(11, test_price)]
        return {
            "miasto": name, "punkt": f"{name}, ul. Testowa 1", "id_punktu": 1,
            "data_pobrania": "2026-10-30", "pakiety": analyze_packages([package], products),
        }

    def test_package_takes_values_from_the_median_city(self):
        # Osobno: Kraków 200 zł (+25%), Łódź 180 zł (+16,7%), Gdańsk 120 zł (−25%) przy pakiecie za 150 zł.
        result = compare([self.city("Kraków", 100), self.city("Gdańsk", 60), self.city("Łódź", 90)])
        [package] = result["pakiety"]
        self.assertEqual(package["suma_osobno_pln"], 180.0)
        self.assertEqual((package["cena_min_pln"], package["cena_max_pln"]), (150.0, 150.0))
        self.assertEqual(package["oszczednosc_pln"], 30.0)
        self.assertEqual(package["werdykt"], OPLACALNY)
        self.assertEqual(package["miasta_z_danymi"], 3)
        self.assertEqual(package["oplaca_sie_w_miastach"], 2)
        self.assertEqual((package["min_oszczednosc_proc"], package["max_oszczednosc_proc"]), (-25.0, 25.0))

    def test_cities_ranked_by_median_savings(self):
        result = compare([self.city("Gdańsk", 60), self.city("Kraków", 100)])
        self.assertEqual([row["miasto"] for row in result["miasta"]], ["Kraków", "Gdańsk"])
        self.assertEqual(result["macierz"][0]["Gdańsk"], -25.0)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    """Dwie strony wyników; pozycja 2 pojawia się na obu."""

    def __init__(self):
        self.pages = {
            1: {"data": [{"id": 1}, {"id": 2}], "meta": {"last_page": 2, "total": 3}},
            2: {"data": [{"id": 2}, {"id": 3}], "meta": {"last_page": 2, "total": 3}},
        }
        self.requested = []

    def get(self, url, params, timeout):
        self.requested.append(params["page"])
        return FakeResponse(self.pages[params["page"]])


class PaginationTest(unittest.TestCase):
    def test_fetches_every_page_and_skips_duplicates(self):
        session = FakeSession()
        items = DiagClient(delay=0, session=session).get_all_pages("products", {}, "badania")
        self.assertEqual(session.requested, [1, 2])
        self.assertEqual(sorted(item["id"] for item in items), [1, 2, 3])



class PriceListMatchTest(unittest.TestCase):
    def setUp(self):
        self.lists = {"krakow": {str(i): 100.0 + i for i in range(300)}}

    def test_same_prices_match(self):
        from wszystkie_punkty import matching_list
        prices = {str(i): 100.0 + i for i in range(200)}
        self.assertEqual(matching_list(prices, self.lists), "krakow")

    def test_one_different_price_is_another_list(self):
        from wszystkie_punkty import matching_list
        prices = {str(i): 100.0 + i for i in range(200)}
        prices["7"] = 1.0
        self.assertIsNone(matching_list(prices, self.lists))

    def test_too_few_common_prices(self):
        from wszystkie_punkty import matching_list
        prices = {str(i): 100.0 + i for i in range(50)}
        self.assertIsNone(matching_list(prices, self.lists))


if __name__ == "__main__":
    unittest.main()
