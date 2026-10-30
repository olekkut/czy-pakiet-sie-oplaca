"""
Rysuje wykresy do README i wpisuje aktualne liczby do README.md (między znacznikami
<!-- wyniki:start --> i <!-- wyniki:end -->). Domyślnie korzysta z porównania 49 miast
z porownanie_miast.py, ale umie też narysować wynik jednego punktu z analiza_pakietow.py.

docs/hero.png                  baner 1280×640, nadaje się też na podgląd repozytorium
docs/mapa-<motyw>.svg          mapa miast: gdzie pakiety opłacają się najbardziej
docs/miasta-<motyw>.svg        poziom cen badań i oszczędność w każdym mieście
docs/oszczednosci-<motyw>.svg  każdy pakiet jako kropka, według typu pakietu
docs/ranking-<motyw>.svg       10 najbardziej i 10 najmniej opłacalnych pakietów
"""

import argparse
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Circle, PathPatch  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402
from matplotlib.transforms import Bbox  # noqa: E402

from analiza_pakietow import BEZ_ROZNICY, DROZSZY, OPLACALNY  # noqa: E402
from diag_api import format_pln, setup_console  # noqa: E402
from porownanie_miast import RESULTS_DIR  # noqa: E402

Row = Dict[str, Any]
Theme = Dict[str, Any]

DOCS_DIR = Path("docs")
README_PATH = Path("README.md")
DEFAULT_REPORT = RESULTS_DIR / "porownanie.json"
README_MARKERS = ("<!-- wyniki:start -->", "<!-- wyniki:end -->")

# Paleta: niebieski i czerwony to bieguny skali "taniej/drożej", szary to środek, a "ramp"
# to jednobarwna skala dla mapy. Ciemny motyw ma własne odcienie dobrane pod ciemne tło
# (na mapie odwrócone: więcej oszczędności to jaśniejszy, czyli bardziej widoczny kolor).
THEMES = {
    "light": {
        "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "cheaper": "#2a78d6", "same": "#898781", "dearer": "#e34948",
        "ramp": ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"],
    },
    "dark": {
        "surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "cheaper": "#3987e5", "same": "#898781", "dearer": "#e66767",
        "ramp": ["#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"],
    },
}
VERDICT_KEYS = {OPLACALNY: "cheaper", BEZ_ROZNICY: "same", DROZSZY: "dearer"}
LEGEND = [("cheaper", "taniej w pakiecie"), ("same", "bez różnicy"), ("dearer", "drożej w pakiecie")]
GROUPS = ["Zwykłe, 2+ badania", "Z konsultacją", "Wysyłkowe", "Jedno badanie"]

# Mapa: granice klas mediany oszczędności (%) i rzut, w którym Polska ma właściwe proporcje.
SAVINGS_CLASSES = [10, 20, 30, 40]
MAP_X_SCALE = math.cos(math.radians(52))


def setup_matplotlib() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Segoe UI", "DejaVu Sans"],
        "svg.fonttype": "path",  # tekst jako krzywe wygląda tak samo w każdej przeglądarce
        "svg.hashsalt": "czy-pakiet",  # stałe identyfikatory w SVG, czyli czytelne diffy
    })


# --- dane ---------------------------------------------------------------------------------

def compared_rows(report: Dict[str, Any]) -> List[Row]:
    """Pakiety, dla których dało się policzyć oszczędność (od najbardziej opłacalnych)."""
    return [row for row in report["pakiety"] if row["oszczednosc_proc"] is not None]


def compared_cities(report: Dict[str, Any]) -> List[Row]:
    return [city for city in report.get("miasta", []) if city["mediana_oszczednosci_2plus_proc"] is not None]


def package_group(row: Row) -> str:
    """Konsultacja i wysyłka mają pierwszeństwo, bo to one podnoszą cenę pakietu."""
    if row["z_konsultacja"]:
        return GROUPS[1]
    if row["wysylkowy"]:
        return GROUPS[2]
    if row["liczba_badan"] == 1:
        return GROUPS[3]
    return GROUPS[0]


def by_group(rows: List[Row]) -> Dict[str, List[Row]]:
    groups: Dict[str, List[Row]] = {name: [] for name in GROUPS}
    for row in rows:
        groups[package_group(row)].append(row)
    return groups


def group_stats(rows: List[Row]) -> Dict[str, Any]:
    counts = Counter(row["werdykt"] for row in rows)
    return {
        "n": len(rows),
        "counts": counts,
        "share": counts[OPLACALNY] / len(rows) if rows else 0.0,
        "median": statistics.median(row["oszczednosc_proc"] for row in rows) if rows else 0.0,
    }


def multi_test_stats(report: Dict[str, Any]) -> Dict[str, Any]:
    return group_stats([row for row in compared_rows(report) if row["liczba_badan"] > 1])


def city_extremes(cities: List[Row]) -> Tuple[Row, Row]:
    """Miasto z największą i z najmniejszą medianą oszczędności."""
    key = lambda city: city["mediana_oszczednosci_2plus_proc"]  # noqa: E731
    return max(cities, key=key), min(cities, key=key)


# --- formatowanie -------------------------------------------------------------------------

def pct(value: float, decimals: int = 1) -> str:
    """19.74 -> '+19,7%', -0.2 -> '−0,2%' (polski przecinek i typograficzny minus)."""
    text = f"{abs(value):.{decimals}f}".replace(".", ",")
    if round(value, decimals) == 0:
        return f"{text}%"
    return f"{'+' if value > 0 else '−'}{text}%"


def signed_pln(value: float) -> str:
    return ("+" if value > 0 else "−" if value < 0 else "") + format_pln(abs(value))


def count_packages(n: int) -> str:
    if n == 1:
        return "1 pakiet"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} pakiety"
    return f"{n} pakietów"


def polish_date(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{day}.{month}.{year}"


def tag(row: Row) -> str:
    if row["z_konsultacja"]:
        return "konsultacja w cenie"
    if row["wysylkowy"]:
        return "wysyłkowy"
    return ""


def savings_class(value: float) -> int:
    return sum(value >= limit for limit in SAVINGS_CLASSES)


def class_labels() -> List[str]:
    limits = SAVINGS_CLASSES
    return (
        [f"poniżej {limits[0]}%"]
        + [f"{low}–{high}%" for low, high in zip(limits, limits[1:])]
        + [f"{limits[-1]}% i więcej"]
    )


# --- elementy wykresów ---------------------------------------------------------------------

def text(fig: Figure, x: float, y: float, s: str, size: float, color: str, weight: str = "normal", **kwargs):
    """Tekst w calach od lewego dolnego rogu rysunku."""
    return fig.text(x, y, s, transform=fig.dpi_scale_trans, fontsize=size, color=color, fontweight=weight, **kwargs)


def width_in(fig: Figure, artist) -> float:
    return artist.get_window_extent(fig.canvas.get_renderer()).width / fig.dpi


def fit_text(fig: Figure, artist, max_width: float) -> None:
    """Skraca tekst z wielokropkiem, aż zmieści się w podanej szerokości (w calach)."""
    full = artist.get_text()
    keep = len(full)
    while width_in(fig, artist) > max_width and keep > 8:
        keep -= 2
        artist.set_text(full[:keep].rstrip(" ,-–(") + "…")


def draw_legend(fig: Figure, x: float, y: float, theme: Theme, keys: Sequence[str], size: float = 10) -> None:
    for key, label in LEGEND:
        if key not in keys:
            continue
        fig.add_artist(Circle((x + 0.05, y), 0.05, transform=fig.dpi_scale_trans, color=theme[key]))
        label_artist = text(fig, x + 0.15, y, label, size, theme["ink2"], va="center")
        x += 0.15 + width_in(fig, label_artist) + 0.28


def draw_class_legend(fig: Figure, x: float, y: float, theme: Theme, size: float = 10, vertical: bool = True) -> None:
    """Legenda klas mapy, od największej oszczędności (pionowo) albo od najmniejszej (w rzędzie)."""
    labels = class_labels()
    order = reversed(range(len(labels))) if vertical else range(len(labels))
    for index in order:
        fig.add_artist(Circle((x + 0.07, y), 0.07, transform=fig.dpi_scale_trans, color=theme["ramp"][index]))
        label_artist = text(fig, x + 0.22, y, labels[index], size, theme["ink2"], va="center")
        if vertical:
            y -= 0.28
        else:
            x += 0.22 + width_in(fig, label_artist) + 0.25


def swarm(xs: Sequence[float], diameter: float) -> List[float]:
    """
    Pionowe przesunięcia kropek w wykresie typu beeswarm. Każda kropka (xs w punktach,
    rosnąco) trafia na pozycję najbliższą osi, w której nie nachodzi na wcześniejsze.
    """
    placed: List[Tuple[float, float]] = []
    for x in xs:
        near = [(px, py) for px, py in placed if abs(px - x) < diameter]
        candidates = [0.0]
        for px, py in near:
            dy = math.sqrt(diameter ** 2 - (px - x) ** 2)
            candidates += [py + dy, py - dy]
        free = [
            c for c in candidates
            if all((px - x) ** 2 + (py - c) ** 2 >= diameter ** 2 - 1e-6 for px, py in near)
        ]
        placed.append((x, min(free, key=abs)))
    return [y for _, y in placed]


def savings_range(rows: List[Row]) -> Tuple[float, float]:
    values = [row["oszczednosc_proc"] for row in rows]
    return min(-20.0, math.floor(min(values) / 10) * 10 - 4), max(20.0, math.ceil(max(values) / 10) * 10 + 2)


def swarm_layout(groups: Dict[str, List[Row]], x_range: Tuple[float, float], plot_width: float, dot: float):
    """Dla każdej grupy: x (%), y (pt), kolory i połowa wysokości wiersza w punktach."""
    xmin, xmax = x_range
    pt_per_unit = plot_width * 72 / (xmax - xmin)
    layout = {}
    for name, rows in groups.items():
        rows = sorted(rows, key=lambda row: row["oszczednosc_proc"])
        xs = [row["oszczednosc_proc"] for row in rows]
        ys = swarm([(x - xmin) * pt_per_unit for x in xs], dot + 1.0)
        half = max((abs(y) for y in ys), default=0.0) + dot / 2 + 3
        layout[name] = (xs, ys, [VERDICT_KEYS[row["werdykt"]] for row in rows], half)
    return layout


def swarm_row(fig: Figure, theme: Theme, rect: Tuple[float, float, float, float],
              x_range: Tuple[float, float], xs, ys, keys, dot: float):
    """Jeden wiersz kropek; oś y wiersza jest w punktach, więc kropki są okrągłe."""
    left, bottom, width, height = rect
    fig_w, fig_h = fig.get_size_inches()
    ax = fig.add_axes((left / fig_w, bottom / fig_h, width / fig_w, height / fig_h))
    ax.set_xlim(*x_range)
    ax.set_ylim(-height * 36, height * 36)
    ax.axis("off")
    for x in range(int(x_range[0] // 20 * 20), int(x_range[1]) + 1, 20):
        if x_range[0] <= x <= x_range[1]:
            ax.axvline(x, color=theme["muted"] if x == 0 else theme["grid"], lw=1.0 if x == 0 else 0.8, zorder=1)
    ax.scatter(xs, ys, s=dot ** 2, c=[theme[key] for key in keys],
               edgecolors=theme["surface"], linewidths=0.9, zorder=3)
    return ax


def x_tick_labels(ax, theme: Theme, x_range: Tuple[float, float], y: float, size: float) -> None:
    for x in range(int(x_range[0] // 20 * 20), int(x_range[1]) + 1, 20):
        if x_range[0] <= x <= x_range[1]:
            ax.text(x, y, "0" if x == 0 else pct(x, 0), ha="center", va="top",
                    fontsize=size, color=theme["muted"], clip_on=False)


def rounded_bar(x0: float, x1: float, y: float, height: float, radius: float, pt_per_x: float, color: str) -> PathPatch:
    """Poziomy słupek z zaokrąglonym końcem po stronie wartości i prostym przy osi (oś y w punktach)."""
    sign = 1 if x1 >= x0 else -1
    rx = min(radius / pt_per_x, abs(x1 - x0))
    ry = min(radius, height / 2)
    y0, y1 = y - height / 2, y + height / 2
    xe = x1 - sign * rx
    vertices = [(x0, y0), (xe, y0), (x1, y0), (x1, y0 + ry), (x1, y1 - ry), (x1, y1), (xe, y1), (x0, y1), (x0, y0)]
    codes = [MplPath.MOVETO, MplPath.LINETO, MplPath.CURVE3, MplPath.CURVE3, MplPath.LINETO,
             MplPath.CURVE3, MplPath.CURVE3, MplPath.LINETO, MplPath.CLOSEPOLY]
    return PathPatch(MplPath(vertices, codes), facecolor=color, edgecolor="none", zorder=3)


def draw_city_map(fig: Figure, theme: Theme, cities: List[Row], rect: Tuple[float, float, float, float],
                  dot: float, label_all: bool) -> None:
    """
    Miasta jako kropki w położeniu geograficznym, kolor to klasa mediany oszczędności.
    Bez granic: 49 miast równomiernie pokrywa kraj, więc kształt Polski widać i tak.
    """
    left, bottom, max_width, max_height = rect
    xs = [city["dlugosc_geogr"] * MAP_X_SCALE for city in cities]
    ys = [city["szerokosc_geogr"] for city in cities]
    x0, x1 = min(xs) - 0.4, max(xs) + (1.0 if label_all else 0.4)
    y0, y1 = min(ys) - 0.3, max(ys) + 0.3
    scale = min(max_width / (x1 - x0), max_height / (y1 - y0))
    fig_w, fig_h = fig.get_size_inches()
    ax = fig.add_axes((left / fig_w, bottom / fig_h, (x1 - x0) * scale / fig_w, (y1 - y0) * scale / fig_h))
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.axis("off")

    values = [city["mediana_oszczednosci_2plus_proc"] for city in cities]
    ax.scatter(xs, ys, s=dot ** 2, c=[theme["ramp"][savings_class(value)] for value in values],
               edgecolors=theme["surface"], linewidths=1.5, zorder=3)

    best, worst = city_extremes(cities)
    extremes = (best["miasto"], worst["miasto"])
    labels = [
        (index, f"{city['miasto']} · {value:.0f}%" if city["miasto"] in extremes else city["miasto"],
         city["miasto"] in extremes)
        for index, (city, value) in enumerate(zip(cities, values))
        if label_all or city["miasto"] in extremes
    ]
    labels.sort(key=lambda label: not label[2])  # skrajne miasta wybierają miejsce pierwsze
    place_labels(fig, ax, theme, xs, ys, labels, dot)


def place_labels(fig: Figure, ax, theme: Theme, xs: List[float], ys: List[float],
                 labels: List[Tuple[int, str, bool]], dot: float) -> None:
    """Podpis miasta po prawej od kropki, a jeśli tam na coś nachodzi, to po lewej, nad albo pod nią."""
    renderer = fig.canvas.get_renderer()
    radius = dot / 2 * fig.dpi / 72
    centers = ax.transData.transform(list(zip(xs, ys)))
    dots = [Bbox.from_extents(cx - radius, cy - radius, cx + radius, cy + radius) for cx, cy in centers]
    offset = dot / 2 + 3
    spots = [((offset, 0), "left", "center"), ((-offset, 0), "right", "center"),
             ((0, offset), "center", "bottom"), ((0, -offset), "center", "top")]
    taken: List[Bbox] = []
    for index, label, extreme in labels:
        style = {
            "fontsize": 10 if extreme else 8.5, "fontweight": "bold" if extreme else "normal",
            "color": theme["ink"] if extreme else theme["ink2"], "zorder": 4,
        }
        others = [box for number, box in enumerate(dots) if number != index] + taken
        for xytext, ha, va in spots:
            artist = ax.annotate(label, (xs[index], ys[index]), xytext=xytext, textcoords="offset points",
                                 ha=ha, va=va, **style)
            box = artist.get_window_extent(renderer).padded(2)
            if not any(box.overlaps(other) for other in others):
                break
            artist.remove()
        else:  # nigdzie nie ma miejsca: zostaje po prawej
            artist = ax.annotate(label, (xs[index], ys[index]), xytext=spots[0][0], textcoords="offset points",
                                 ha="left", va="center", **style)
            box = artist.get_window_extent(renderer)
        taken.append(box)


# --- wykresy ------------------------------------------------------------------------------

def figure_savings(report: Dict[str, Any], theme: Theme) -> Figure:
    """Każdy pakiet jako kropka: o ile procent jest tańszy od badań kupionych osobno."""
    rows = compared_rows(report)
    groups = by_group(rows)
    x_range = savings_range(rows)
    width, label_w, note_w, dot = 10.0, 2.0, 1.75, 6.0
    plot_w = width - label_w - note_w - 0.25
    layout = swarm_layout(groups, x_range, plot_w, dot)
    row_heights = {name: max(2 * layout[name][3] / 72, 0.45) for name in GROUPS}
    top, bottom, gap = 1.3, 0.75, 0.12
    height = top + sum(row_heights.values()) + gap * (len(GROUPS) - 1) + bottom

    fig = plt.figure(figsize=(width, height), facecolor=theme["surface"])
    text(fig, 0.3, height - 0.42, "Ile oszczędzasz, kupując pakiet zamiast badań osobno?", 15, theme["ink"], "bold")
    text(fig, 0.3, height - 0.7,
         f"Każda kropka to jeden pakiet · {report['punkt']} · ceny z {polish_date(report['data_pobrania'])}",
         10.5, theme["ink2"])
    draw_legend(fig, 0.3, height - 1.0, theme, ["cheaper", "same", "dearer"])

    y_top = height - top
    axes = None
    for index, name in enumerate(GROUPS):
        xs, ys, keys, _ = layout[name]
        row_h = row_heights[name]
        axes = swarm_row(fig, theme, (label_w, y_top - row_h, plot_w, row_h), x_range, xs, ys, keys, dot)
        center = y_top - row_h / 2
        stats = group_stats(groups[name])
        text(fig, 0.3, center + 0.03, name, 11, theme["ink"], "bold", va="bottom")
        text(fig, 0.3, center - 0.01, count_packages(stats["n"]), 9.5, theme["muted"], va="top")
        if stats["n"]:
            note_x = label_w + plot_w + 0.25
            text(fig, note_x, center + 0.03, f"{stats['share']:.0%} tańszych", 11, theme["ink"], "bold", va="bottom")
            text(fig, note_x, center - 0.01, f"mediana {pct(stats['median'])}", 9.5, theme["muted"], va="top")
        if index == 0:
            ax_top = row_h * 36 + 3
            axes.text(-1.5, ax_top, "← drożej", ha="right", va="bottom", fontsize=9, color=theme["muted"], clip_on=False)
            axes.text(1.5, ax_top, "taniej →", ha="left", va="bottom", fontsize=9, color=theme["muted"], clip_on=False)
        y_top -= row_h + gap

    last_h = row_heights[GROUPS[-1]]
    x_tick_labels(axes, theme, x_range, -last_h * 36 - 6, 9.5)
    text(fig, label_w + plot_w / 2, 0.2, "oszczędność względem zakupu tych samych badań osobno",
         9.5, theme["muted"], ha="center")
    return fig


def figure_ranking(report: Dict[str, Any], theme: Theme, count: int = 10) -> Figure:
    """Najbardziej i najmniej opłacalne pakiety jako słupki rozbieżne od zera."""
    rows = compared_rows(report)
    best = rows[:count]
    worst = rows[max(count, len(rows) - count):]
    values = [row["oszczednosc_proc"] for row in best + worst]
    lo, hi = min(min(values), 0.0), max(max(values), 0.0)
    x_range = (lo - 3, hi + 0.38 * (hi - lo))

    width, names_w, pitch, bar_h = 10.0, 4.3, 0.29, 13.0
    plot_left, plot_w = names_w + 0.45, width - names_w - 0.75
    pt_per_x = plot_w * 72 / (x_range[1] - x_range[0])
    top, section_h, bottom = 1.25, 0.45, 0.25
    plot_h = 2 * section_h + (len(best) + len(worst)) * pitch
    height = top + plot_h + bottom

    fig = plt.figure(figsize=(width, height), facecolor=theme["surface"])
    text(fig, 0.3, height - 0.42, "Ranking pakietów", 15, theme["ink"], "bold")
    text(fig, 0.3, height - 0.7,
         f"Oszczędność względem zakupu tych samych badań osobno · {report['punkt']} · "
         f"ceny z {polish_date(report['data_pobrania'])}",
         10.5, theme["ink2"])
    draw_legend(fig, 0.3, height - 1.0, theme, ["cheaper", "dearer"])

    fig_w, fig_h = fig.get_size_inches()
    ax = fig.add_axes((plot_left / fig_w, bottom / fig_h, plot_w / fig_w, plot_h / fig_h))
    ax.set_xlim(*x_range)
    ax.set_ylim(0, plot_h * 72)
    ax.axis("off")
    ax.axvline(0, color=theme["muted"], lw=1.0, zorder=2)

    y = plot_h * 72
    for title, section in (("Najbardziej opłacalne", best), ("Najmniej opłacalne", worst)):
        y -= section_h * 72
        text(fig, 0.3, bottom + y / 72 + 0.1, title, 11.5, theme["ink"], "bold", va="bottom")
        for row in section:
            y_mid = y - pitch * 36
            value = row["oszczednosc_proc"]
            color = theme["cheaper"] if value > 0 else theme["dearer"]
            ax.add_patch(rounded_bar(0.0, value, y_mid, bar_h, 4.0, pt_per_x, color))
            label = pct(value) + (f" · {tag(row)}" if tag(row) else "")
            ax.text(max(value, 0.0) + 1.2, y_mid, label, ha="left", va="center", fontsize=9.5, color=theme["ink2"])
            name = text(fig, 0.3, bottom + y_mid / 72, row["nazwa_pakietu"], 9.5, theme["ink"], va="center")
            fit_text(fig, name, names_w)
            y -= pitch * 72
    return fig


def figure_map(report: Dict[str, Any], theme: Theme) -> Figure:
    """Mapa miast: gdzie pakiety z kilkoma badaniami dają największą oszczędność."""
    cities = compared_cities(report)
    width, height = 10.0, 8.0
    fig = plt.figure(figsize=(width, height), facecolor=theme["surface"])
    text(fig, 0.3, height - 0.42, "Gdzie pakiet opłaca się najbardziej?", 15, theme["ink"], "bold")
    text(fig, 0.3, height - 0.7,
         f"Mediana oszczędności w pakietach z co najmniej dwoma badaniami · {len(cities)} miast · "
         f"ceny z {polish_date(report['data_pobrania'])}",
         10.5, theme["ink2"])
    draw_city_map(fig, theme, cities, (0.3, 0.3, 7.3, height - 1.3), dot=14, label_all=True)

    legend_x = 7.85
    text(fig, legend_x, height - 1.3, "Mediana oszczędności", 10.5, theme["ink"], "bold", va="top")
    draw_class_legend(fig, legend_x, height - 1.75, theme)
    text(fig, legend_x, height - 3.35,
         "Jeden punkt pobrań na miasto.\nWiększość pakietów kosztuje\nwszędzie tyle samo, a ceny\npojedynczych badań zależą\nod miasta.",
         9, theme["muted"], va="top", linespacing=1.35)
    return fig


def figure_cities(report: Dict[str, Any], theme: Theme) -> Figure:
    """
    Miasta w dwóch panelach o wspólnej kolejności: poziom cen pojedynczych badań i mediana
    oszczędności na pakietach. Widać, że pakiet opłaca się tam, gdzie badania są drogie.
    """
    cities = [city for city in compared_cities(report) if city.get("indeks_cen_badan") is not None]
    width, pitch, bar_h = 10.0, 0.2, 9.0
    names_w, gap = 1.75, 0.45
    panel_w = (width - 0.3 - names_w - gap - 0.55) / 2
    top, bottom = 1.55, 0.35
    plot_h = len(cities) * pitch
    height = top + plot_h + bottom
    fig = plt.figure(figsize=(width, height), facecolor=theme["surface"])
    text(fig, 0.3, height - 0.42, "Drogie badania, opłacalne pakiety", 15, theme["ink"], "bold")
    text(fig, 0.3, height - 0.7,
         f"{len(cities)} miast od największej oszczędności · ceny z {polish_date(report['data_pobrania'])}",
         10.5, theme["ink2"])

    fig_w, fig_h = fig.get_size_inches()
    left_x = 0.3 + names_w
    right_x = left_x + panel_w + gap
    indexes = [city["indeks_cen_badan"] for city in cities]
    savings = [city["mediana_oszczednosci_2plus_proc"] for city in cities]
    spread = max(abs(value - 100) for value in indexes) + 4
    panels = (
        (left_x, (100 - spread, 100 + spread), "Poziom cen pojedynczych badań",
         "100 = miasto środkowe; koszt osobno badań z pakietów"),
        (right_x, (min(0.0, min(savings)) - 2, max(savings) + 9), "Mediana oszczędności na pakiecie",
         "pakiety z co najmniej dwoma badaniami"),
    )
    axes = []
    for x, x_range, title, note in panels:
        text(fig, x, height - 1.12, title, 11, theme["ink"], "bold")
        text(fig, x, height - 1.34, note, 9, theme["muted"])
        ax = fig.add_axes((x / fig_w, bottom / fig_h, panel_w / fig_w, plot_h / fig_h))
        ax.set_xlim(*x_range)
        ax.set_ylim(0, plot_h * 72)
        ax.axis("off")
        axes.append(ax)
    pt_index = panel_w * 72 / (panels[0][1][1] - panels[0][1][0])
    pt_savings = panel_w * 72 / (panels[1][1][1] - panels[1][1][0])
    axes[0].axvline(100, color=theme["muted"], lw=1.0, zorder=2)
    axes[1].axvline(0, color=theme["muted"], lw=1.0, zorder=2)

    for row, city in enumerate(cities):
        y = (plot_h - (row + 0.5) * pitch) * 72
        text(fig, 0.3, bottom + y / 72, city["miasto"], 9, theme["ink"], va="center")
        index = city["indeks_cen_badan"]
        axes[0].add_patch(rounded_bar(100.0, index, y, bar_h, 3.0, pt_index, theme["muted"]))
        axes[0].text(index + (1 if index >= 100 else -1), y, f"{index:.0f}", ha="left" if index >= 100 else "right",
                     va="center", fontsize=8, color=theme["ink2"])
        value = city["mediana_oszczednosci_2plus_proc"]
        axes[1].add_patch(rounded_bar(0.0, value, y, bar_h, 3.0, pt_savings, theme["ramp"][savings_class(value)]))
        axes[1].text(max(value, 0.0) + 0.8, y, pct(value), ha="left", va="center", fontsize=8, color=theme["ink2"])
    return fig


def figure_hero(report: Dict[str, Any]) -> Figure:
    """Baner 1280×640 na górę README i podgląd repozytorium w social media."""
    theme = THEMES["dark"]
    width, height = 12.8, 6.4
    fig = plt.figure(figsize=(width, height), facecolor=theme["surface"])
    text(fig, 0.75, 5.65, "czy-pakiet-sie-oplaca", 12, theme["muted"])
    text(fig, 0.75, 5.35, "Czy pakiet badań\nsię opłaca?", 36, theme["ink"], "bold", va="top", linespacing=1.05)
    text(fig, 0.75, 0.42,
         f"{count_packages(len(report['pakiety']))} · {report['punkt']} · "
         f"ceny z {polish_date(report['data_pobrania'])} · projekt niezależny, niezwiązany z Diagnostyka S.A.",
         10.5, theme["muted"])

    cities = compared_cities(report)
    if cities:
        best, worst = city_extremes(cities)
        low, high = worst["mediana_oszczednosci_2plus_proc"], best["mediana_oszczednosci_2plus_proc"]
        text(fig, 0.75, 2.75, f"{low:.0f}–{high:.0f}%", 76, theme["ink"], "bold")
        text(fig, 0.75, 2.45,
             "tyle wynosi mediana oszczędności na pakietach z co\nnajmniej dwoma badaniami, zależnie od miasta.\n"
             f"Najwięcej: {best['miasto']}, najmniej: {worst['miasto']}.",
             14.5, theme["ink2"], va="top", linespacing=1.4)
        draw_class_legend(fig, 6.7, 5.65, theme, size=10.5, vertical=False)
        draw_city_map(fig, theme, cities, (6.7, 0.85, 5.6, 4.45), dot=13, label_all=False)
        return fig

    # Wynik jednego punktu: po prawej pakiety, o których mówi nagłówek, czyli z co najmniej dwoma badaniami.
    multi = multi_test_stats(report)
    text(fig, 0.75, 2.75, f"{round(multi['share'] * 10)} na 10", 76, theme["ink"], "bold")
    text(fig, 0.75, 2.45,
         "pakietów z co najmniej dwoma badaniami jest\ntańszych niż te same badania kupione osobno.\n"
         f"Mediana oszczędności: {multi['median']:.0f}%.",
         14.5, theme["ink2"], va="top", linespacing=1.4)
    rows = [row for row in compared_rows(report) if row["liczba_badan"] > 1]
    x_range = savings_range(rows)
    left, plot_w, dot = 6.55, 5.65, 9.0
    while True:
        xs, ys, keys, half = swarm_layout({"": rows}, x_range, plot_w, dot)[""]
        row_h = 2 * half / 72
        if row_h <= 3.5 or dot <= 4:
            break
        dot -= 0.5

    draw_legend(fig, left, 5.65, theme, ["cheaper", "same", "dearer"], size=10.5)
    text(fig, left, 5.35, "Każda kropka to jeden pakiet z co najmniej dwoma badaniami", 11, theme["ink2"], va="top")
    center = 3.0
    axes = swarm_row(fig, theme, (left, center - row_h / 2, plot_w, row_h), x_range, xs, ys, keys, dot)
    axes.text(-1.5, row_h * 36 + 3, "← drożej", ha="right", va="bottom", fontsize=10, color=theme["muted"], clip_on=False)
    axes.text(1.5, row_h * 36 + 3, "taniej →", ha="left", va="bottom", fontsize=10, color=theme["muted"], clip_on=False)
    x_tick_labels(axes, theme, x_range, -row_h * 36 - 5, 10.5)
    return fig


# --- README -------------------------------------------------------------------------------

def markdown_table(rows: List[Row]) -> List[str]:
    national = "oplaca_sie_w_miastach" in rows[0]
    header = "| Pakiet | Cena pakietu | Badania osobno | Oszczędność |" + (" Opłaca się w |" if national else "")
    lines = [header, "| :--- | ---: | ---: | ---: |" + (" ---: |" if national else "")]
    for row in rows:
        name = row["nazwa_pakietu"].replace("|", "/").replace("[", "(").replace("]", ")")
        link = f"[{name}]({row['url']})" if row["url"] else name
        line = (
            f"| {link} | {format_pln(row['cena_pakietu_pln'])} | {format_pln(row['suma_osobno_pln'])} | "
            f"{signed_pln(row['oszczednosc_pln'])} ({pct(row['oszczednosc_proc'])}) |"
        )
        if national:
            line += f" {row['oplaca_sie_w_miastach']} z {row['miasta_z_danymi']} miast |"
        lines.append(line)
    return lines


def city_table(cities: List[Row]) -> List[str]:
    lines = [
        "| Miasto | Punkt pobrań | Poziom cen badań | Tańsze pakiety z 2+ badaniami | Mediana oszczędności |",
        "| :--- | :--- | ---: | ---: | ---: |",
    ]
    for city in cities:
        lines.append(
            f"| {city['miasto']} | {city['punkt']} | {city['indeks_cen_badan']:.0f} | "
            f"{city['tansze_2plus']} z {city['pakiety_2plus']} ({city['tansze_2plus_proc']:.0f}%) | "
            f"{pct(city['mediana_oszczednosci_2plus_proc'])} |"
        )
    return lines


def results_markdown(report: Dict[str, Any], count: int = 10) -> str:
    rows = compared_rows(report)
    groups = by_group(rows)
    multi = multi_test_stats(report)
    cities = compared_cities(report)
    date = polish_date(report["data_pobrania"])
    lines: List[str] = []

    if cities:
        best, worst = city_extremes(cities)
        priced = [row for row in report["pakiety"] if row["cena_min_pln"] is not None]
        same_price = sum(row["cena_min_pln"] == row["cena_max_pln"] for row in priced)
        lines += [
            f"Stan na **{date}**: {len(cities)} miast i {count_packages(len(report['pakiety']))}.",
            "",
            f"**To, ile oszczędzasz, zależy od miasta.** Mediana oszczędności na pakietach z co najmniej dwoma "
            f"badaniami wynosi od **{pct(worst['mediana_oszczednosci_2plus_proc'])}** ({worst['miasto']}) "
            f"do **{pct(best['mediana_oszczednosci_2plus_proc'])}** ({best['miasto']}). {same_price} z {len(priced)} "
            "pakietów kosztuje we wszystkich miastach tyle samo, a ceny pojedynczych badań zależą od miasta.",
            "",
            "<details>",
            f"<summary>Tabela: {len(cities)} miast od największej oszczędności</summary>",
            "",
            *city_table(cities),
            "",
            "</details>",
            "",
            f"W skali kraju, czyli dla mediany z miast, **{multi['share']:.0%} pakietów z co najmniej dwoma "
            f"badaniami jest tańszych** niż te same badania kupione osobno.",
        ]
    else:
        skipped = len(report["pakiety"]) - len(rows)
        lines += [
            f"Stan na **{date}**, punkt pobrań **{report['punkt']}**: "
            f"{count_packages(len(report['pakiety']))}, z czego {len(rows)} da się porównać"
            + (" (pozostałym brakuje w API składu albo ceny)." if skipped else "."),
            "",
            f"**{multi['share']:.0%} pakietów z co najmniej dwoma badaniami jest tańszych** niż te same badania "
            f"kupione osobno, a mediana oszczędności wynosi **{pct(multi['median'])}**.",
        ]

    lines += [
        "",
        "| Typ pakietu | Pakietów | Taniej w pakiecie | Bez różnicy | Drożej w pakiecie | Mediana oszczędności |",
        "| :--- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in GROUPS:
        stats = group_stats(groups[name])
        counts = stats["counts"]
        lines.append(
            f"| {name} | {stats['n']} | {counts[OPLACALNY]} ({stats['share']:.0%}) | {counts[BEZ_ROZNICY]} | "
            f"{counts[DROZSZY]} | {pct(stats['median'])} |"
        )
    worst_rows = rows[max(count, len(rows) - count):]
    lines += [
        "",
        "<details>",
        f"<summary>Tabela: {count} najbardziej i {count} najmniej opłacalnych pakietów</summary>",
        "",
        "**Najbardziej opłacalne**",
        "",
        *markdown_table(rows[:count]),
        "",
        "**Najmniej opłacalne**",
        "",
        *markdown_table(worst_rows),
        "",
        "</details>",
    ]
    return "\n".join(lines)


def update_readme(report: Dict[str, Any], path: Path) -> None:
    start, end = README_MARKERS
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(content):
        print(f"[!] W {path} brak znaczników {start} ... {end}, pomijam aktualizację.")
        return
    block = f"{start}\n{results_markdown(report)}\n{end}"
    path.write_text(pattern.sub(lambda _: block, content), encoding="utf-8")
    print(f"[+] Zaktualizowano wyniki w {path}")


# --- uruchomienie -------------------------------------------------------------------------

def save(fig: Figure, path: Path) -> None:
    if path.suffix == ".svg":
        fig.savefig(path, format="svg", facecolor=fig.get_facecolor(), metadata={"Date": None})
    else:
        fig.savefig(path, dpi=100, facecolor=fig.get_facecolor(), metadata={"Software": None})
    plt.close(fig)
    print(f"[+] Zapisano wykres: {path}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rysuje wykresy do README z porównania miast albo z analizy jednego punktu."
    )
    parser.add_argument(
        "--dane",
        default=str(DEFAULT_REPORT),
        help=f"plik JSON z porownanie_miast.py lub analiza_pakietow.py (domyślnie: {DEFAULT_REPORT.as_posix()})",
    )
    parser.add_argument("--docs", default=str(DOCS_DIR), help="katalog na wykresy (domyślnie: docs)")
    parser.add_argument("--readme", default=str(README_PATH), help="README do aktualizacji (domyślnie: README.md)")
    return parser.parse_args()


def main():
    setup_console()
    args = parse_arguments()
    report = json.loads(Path(args.dane).read_text(encoding="utf-8"))
    setup_matplotlib()

    docs = Path(args.docs)
    docs.mkdir(parents=True, exist_ok=True)
    save(figure_hero(report), docs / "hero.png")
    for mode, theme in THEMES.items():
        if compared_cities(report):
            save(figure_map(report, theme), docs / f"mapa-{mode}.svg")
            save(figure_cities(report, theme), docs / f"miasta-{mode}.svg")
        save(figure_savings(report, theme), docs / f"oszczednosci-{mode}.svg")
        save(figure_ranking(report, theme), docs / f"ranking-{mode}.svg")
    update_readme(report, Path(args.readme))


if __name__ == "__main__":
    main()
