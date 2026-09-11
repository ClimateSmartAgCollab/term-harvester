"""Canadian System of Soil Classification (CSSC) source helpers for term_harvester.py.

Handles the Third Edition (1998) CSSC taxonomic classification available at
https://sis.agr.gc.ca/cansis/taxa/cssc3/

Produces a single enumeration rooted at Soil Order:
  CSSCv3_SoilOrder — all three taxonomic levels in one hierarchy:
                       • Order (10 taxa, top level, no is_a)
                       • Great Group (~31 taxa, is_a → order code)
                       • Subgroup (~231 taxa, is_a → great group code)

This allows reachable_from/source_nodes filtering to traverse any branch of
the taxonomy.  For example, specifying source_node BR retrieves the entire
Brunisolic branch including all great groups and subgroups.

Download/process split
----------------------
* -a URL (match_cssc)          — detects CSSC index/chapter URL, creates the
                                 CSSCv3_SoilOrder config entry, downloads the
                                 full zip (chapters + GG pages), runs initial
                                 processing.
* -f key (fetch_cssc_source)   — rebuilds sources/CSSCv3_SoilOrder.zip and
                                 updates download_date.
* -c key (process_cssc_source) — writes sources/{key}.yaml from the zip.

Zip format
----------
sources/CSSCv3_SoilOrder.zip contains:
  manifest.json   — {url: entry_filename} mapping
  0000.html …     — raw HTML for each URL (index, chapters 4-13, all GG pages,
                    EN + FR), UTF-8 encoded

Public API used by term_harvester.py:
    cssc_fr_url(url)
    fetch_cssc_source(key, source, config_file, locales)
    process_cssc_source(key, source, config_file, locales)
    match_cssc(url, tmp_path, config_file)
"""

import datetime
import json
import os
import re
import sys
import urllib.parse
import yaml
import zipfile

from source_utils import (
    fetch_html,
    strip_tags,
    normalize_text,
    add_permissible_value,
    log_extraction,
    _make_locale_extensions,
    IndentedDumper,
    make_config_schema,
    make_source_entry,
    update_source_config,
    write_config,
    MENU_CONFIG,
)

_CSSC_SEE_ALSO = "https://sis.agr.gc.ca/cansis/taxa/cssc3/index.html"
_CSSC_VERSION  = "3"   # Third Edition (1998)

# Chapters 4-13 map one-to-one to the 10 CSSC soil orders.
# Codes are the standard CSSC order abbreviations found in great-group notation.
_ORDER_CHAPTERS = [
    ("chpt04", "BR"),  # Brunisolic
    ("chpt05", "CH"),  # Chernozemic
    ("chpt06", "CY"),  # Cryosolic
    ("chpt07", "GL"),  # Gleysolic
    ("chpt08", "LU"),  # Luvisolic
    ("chpt09", "OR"),  # Organic
    ("chpt10", "PZ"),  # Podzolic
    ("chpt11", "RG"),  # Regosolic
    ("chpt12", "SZ"),  # Solonetzic
    ("chpt13", "VE"),  # Vertisolic
]

# ---------------------------------------------------------------------------
# Hardcoded order data
# Titles and descriptions extracted via textual analysis of the CSSC Third
# Edition HTML pages (sis.agr.gc.ca/cansis/taxa/cssc3/, 2026-09-10).
# Descriptions are the verbatim opening paragraph(s) of each order chapter,
# capped at 5 sentences.  FR descriptions are from the /siscan/ mirror.
# ---------------------------------------------------------------------------

_ORDER_TITLES = {
    "BR": {"en": "Brunisolic Order",   "fr": "Ordre Brunisolique"},
    "CH": {"en": "Chernozemic Order",  "fr": "Ordre Chernozémique"},
    "CY": {"en": "Cryosolic Order",    "fr": "Ordre Cryosolique"},
    "GL": {"en": "Gleysolic Order",    "fr": "Ordre Gleysolique"},
    "LU": {"en": "Luvisolic Order",    "fr": "Ordre Luvisolique"},
    "OR": {"en": "Organic Order",      "fr": "Ordre Organique"},
    "PZ": {"en": "Podzolic Order",     "fr": "Ordre Podzolique"},
    "RG": {"en": "Regosolic Order",    "fr": "Ordre Régosolique"},
    "SZ": {"en": "Solonetzic Order",   "fr": "Ordre Solonetzique"},
    "VE": {"en": "Vertisolic Order",   "fr": "Ordre Vertisolique"},
}

_PV_DESCRIPTIONS = {
    "BR": {
        "en": (
            "Soils of the Brunisolic order have sufficient development to exclude them from "
            "the Regosolic order, but they lack the degree or kind of horizon development "
            "specified for soils of other orders."
        ),
        "fr": (
            "Les sols de l'ordre brunisolique ont un développement suffisant pour les exclure "
            "de l'ordre régosolique, mais ils n'ont pas le degré ou le genre de développement "
            "d'horizons spécifié pour les sols des autres ordres."
        ),
    },
    "CH": {
        "en": (
            "The general concept of the Chernozemic order is that of well to imperfectly "
            "drained soils having surface horizons darkened by the accumulation of organic "
            "matter from the decomposition of xerophytic or mesophytic grasses and forbs "
            "representative of grassland communities or of grassland-forest communities with "
            "associated shrubs and forbs."
        ),
        "fr": (
            "Le concept général de l'ordre chernozémique est celui de sols bien à "
            "imparfaitement drainés, dont l'horizon de surface est noirci par l'accumulation "
            "de matière organique provenant de la décomposition de graminées et de plantes "
            "herbacées xérophiles ou mésophiles, typiques des ensembles des prairies ou des "
            "ensembles de transition prairie-forêt avec arbustes et plantes herbacées associées."
        ),
    },
    "CY": {
        "en": (
            "Soils of the Cryosolic order occupy much of the northern third of Canada where "
            "permafrost exists close to the surface of both mineral and organic deposits. "
            "Cryosolic soils predominate north of the tree line, are common in the subarctic "
            "forest area in fine-textured soils, and extend into the boreal forest in some "
            "organic materials and into some alpine areas of mountainous regions. "
            "Cryoturbation of these soils is common and may be indicated by patterned ground "
            "features such as sorted and nonsorted nets, circles, polygons, stripes, and "
            "earth hummocks."
        ),
        "fr": (
            "Les sols de l'ordre cryosolique occupent une grande partie du tiers septentrional "
            "du Canada où le pergélisol demeure près de la surface, dans les dépôts minéraux "
            "aussi bien qu'organiques."
        ),
    },
    "GL": {
        "en": (
            "Gleysolic soils are defined on the basis of color and mottling, which are "
            "considered to indicate the influence of periodic or sustained reducing conditions "
            "during their genesis."
        ),
        "fr": (
            "Les sols de l'ordre gleysoliques se définissent d'après les couleurs et la "
            "marmorisation qui sont considérées comme indicatrices de l'influence de conditions "
            "de réduction périodique ou permanente au cours de la pédogenèse du sol."
        ),
    },
    "LU": {
        "en": (
            "Soils of the Luvisolic order generally have light-colored, eluvial horizons and "
            "have illuvial B horizons in which silicate clay has accumulated."
        ),
        "fr": (
            "Les sols de l'ordre luvisolique ont généralement un horizon éluvial de couleur "
            "pâle et un horizon B illuvial dans lequel l'argile silicatée s'est accumulée."
        ),
    },
    "OR": {
        "en": (
            "Soils of the Organic order are composed largely of organic materials. "
            "They include most of the soils commonly known as peat, muck, or bog and fen soils. "
            "Most Organic soils are saturated with water for prolonged periods. "
            "These soils occur widely in poorly and very poorly drained depressions and level "
            "areas in regions of subhumid to perhumid climate and are derived from vegetation "
            "that grows in such sites. "
            "However, one group of Organic soils (Folisols) consists of upland (folic) organic "
            "materials, generally of forest origin."
        ),
        "fr": (
            "Les sols de l'ordre organique sont principalement composés de matériaux organiques. "
            "Ils comprennent la plupart des sols, généralement connus sous les noms de tourbe, "
            "de terre noire, de tourbière ou fen. "
            "La plupart des sols organiques sont saturés d'eau pour une durée prolongée."
        ),
    },
    "PZ": {
        "en": (
            "Soils of the Podzolic order have B horizons in which the dominant accumulation "
            "product is amorphous material composed mainly of humified organic matter combined "
            "in varying degrees with Al and Fe."
        ),
        "fr": (
            "Les sols de l'ordre podzolique ont des horizons B dans lesquels le produit dominant "
            "d'accumulation est un matériau amorphe constitué principalement de matière organique "
            "humifiée combinée, à divers degrés, à de l'aluminium (Al) et du fer (Fe). "
            "Typiquement, les sols podzoliques se rencontrent sur des matériaux parentaux acides "
            "de texture grossière à moyenne, sous une végétation de forêt ou de bruyère, dans des "
            "pédoclimats frais à très froid, humide à perhumide. "
            "Cependant, certains sols se rencontrent sous des conditions d'environnement différentes."
        ),
    },
    "RG": {
        "en": (
            "Regosolic soils do not contain a recognizable B horizon at least 5 cm thick and "
            "are therefore referred to as weakly developed."
        ),
        "fr": (
            "Les sols de l'ordre régosolique n'ont pas d'horizon B diagnostique d'au moins "
            "5 cm d'épaisseur et sont reconnus pour être faiblement développés."
        ),
    },
    "SZ": {
        "en": (
            "Soils of the Solonetzic order have B horizons that are very hard when dry and "
            "swell to a sticky mass of very low permeability when wet. "
            "Typically the solonetzic B horizon has prismatic or columnar macrostructure that "
            "breaks to hard to extremely hard (when dry) blocky peds with dark coatings. "
            "They occur on saline parent materials in some areas of the semiarid to subhumid "
            "Interior Plains in association with Chernozemic soils and to a lesser extent with "
            "Luvisolic and Gleysolic soils."
        ),
        "fr": (
            "Les sols de l'ordre solonetzique ont des horizons B qui sont très durs à l'état "
            "sec et qui se gonflent en une masse collante de très faible perméabilité lorsque "
            "trempés."
        ),
    },
    "VE": {
        "en": (
            "Soils of the Vertisolic order occur in heavy textured materials (\u226560% clay "
            "of which at least half is smectite) and have shrink-swell characteristics that "
            "are diagnostic of Vertisolic soils."
        ),
        "fr": (
            "Les sols de l'ordre vertisolique sont présents dans les matériaux à texture fine "
            "(60\u00a0% ou plus d'argile dont au moins la moitié est constituée de smectique) "
            "et ont un comportement de retrait-gonflement diagnostique des sols vertiques."
        ),
    },
}

# Enum-level description for the unified CSSC_SoilOrder enum (all three levels).
_ENUM_DESCRIPTION = {
    "en": (
        "The hierarchical taxonomy of the Canadian System of Soil Classification (CSSC, Third "
        "Edition), covering all three categorical levels in a single enumeration. "
        "Soil Order codes identify the 10 highest-level taxa (e.g., BR for Brunisolic); "
        "Great Group codes append the group abbreviation after a period (e.g., BR.MB for "
        "Melanic Brunisol); Subgroup codes place the subgroup prefix before the period "
        "(e.g., O.MB for Orthic Melanic Brunisol, is_a BR.MB). "
        "The is_a hierarchy enables reachable_from filtering to retrieve any branch of the "
        "taxonomy from a single source node."
    ),
    "fr": (
        "La taxonomie hiérarchique du Système canadien de classification des sols (SCCS, "
        "troisième édition), couvrant les trois niveaux catégoriels dans une seule "
        "énumération. Les codes d'ordre identifient les 10 taxons de plus haut niveau "
        "(p. ex., BR pour brunisolique); les codes de grand groupe ajoutent l'abréviation "
        "du groupe après un point (p. ex., BR.MB pour le brunisol mélanique); les codes de "
        "sous-groupe placent le préfixe avant le point (p. ex., O.MB pour le brunisol "
        "mélanique orthique, is_a BR.MB). La hiérarchie is_a permet de filtrer par "
        "reachable_from pour récupérer n'importe quelle branche de la taxonomie."
    ),
}

# Paragraph prefixes indicating horizon-sequence text to skip in GG page parsing.
_HORIZON_SEQ_RE = re.compile(
    r'^(Common horizon sequence|S[eé]quence typique)',
    re.IGNORECASE,
)

# Derived source key
_SOIL_ORDER_KEY = f"CSSCv{_CSSC_VERSION}_SoilOrder"   # "CSSCv3_SoilOrder"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def cssc_fr_url(url):
    """Return the French-language equivalent of a CSSC URL (/siscan/ mirror)."""
    return url.replace("/cansis/", "/siscan/")


def _cssc_base_dir(url):
    """Return the base directory URL for a CSSC page (with trailing slash)."""
    clean = url.split("#")[0].split("?")[0]
    return urllib.parse.urljoin(clean, ".")


def _zip_path_for(_key=None):
    """Return the single shared CSSC zip path (all keys use the same archive)."""
    return f"sources/{_SOIL_ORDER_KEY}.zip"


# ---------------------------------------------------------------------------
# Zip cache helpers
# ---------------------------------------------------------------------------

def _load_zip_cache(zip_path):
    """Load all HTML pages from a sources zip.

    Returns a ``{url: html_text}`` dict, or ``None`` if the zip does not exist.
    """
    if not os.path.exists(zip_path):
        return None
    try:
        cache = {}
        with zipfile.ZipFile(zip_path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            for url, entry in manifest.items():
                cache[url] = zf.read(entry).decode("utf-8", errors="replace")
        return cache
    except Exception as e:
        print(f"  Warning: could not load {zip_path}: {e}", file=sys.stderr)
        return None


def _html_get(url, cache, indent="  "):
    """Return HTML for *url* from *cache*, falling back to a live fetch."""
    if cache is not None:
        if url in cache:
            return cache[url]
        print(f"{indent}Warning: {url} not in local archive — fetching live",
              file=sys.stderr)
    return fetch_html(url)


# ---------------------------------------------------------------------------
# Chapter table parser — GG URL discovery
# ---------------------------------------------------------------------------

def _parse_chapter_gg_urls(html):
    """Extract (gg_url, gg_name) list from a chapter page's GG/Subgroup table.

    Finds ``<th headers="t201">`` elements (the great-group column cells) that
    contain a hyperlink to the GG page.
    """
    pattern = re.compile(
        r'<th\b[^>]*\bheaders="t201"[^>]*>\s*<a\s+href="([^"]+)"[^>]*>([^<]+)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    results = []
    for m in pattern.finditer(html):
        href = m.group(1).strip()
        name = m.group(2).strip()
        url  = urllib.parse.urljoin("https://sis.agr.gc.ca", href)
        results.append((url, name))
    return results


# ---------------------------------------------------------------------------
# GG page parser — descriptions and subgroup list
# ---------------------------------------------------------------------------

_GG_URL_RE = re.compile(r'/taxa/cssc3/[A-Z]+/([A-Z]+)/index\.html', re.IGNORECASE)


def _gg_code_from_url(url):
    """Extract the GG abbreviation from a GG page URL (e.g. 'MB' from .../BR/MB/index.html)."""
    m = _GG_URL_RE.search(url)
    return m.group(1).upper() if m else None


def _parse_gg_page(html):
    """Parse a CSSC Great Group page (EN or FR).

    Returns
    -------
    gg_code  : str | None   — abbreviation in parentheses of h1, e.g. "MB"
    gg_title : str | None   — name before the parentheses, e.g. "Melanic Brunisol"
    gg_desc  : str          — first 1-2 substantive paragraphs
    subgroups: list of (sg_code, sg_title, sg_desc)

    FR pages return FR-specific codes (e.g. "BM" not "MB"); callers that need
    EN codes should use ``_gg_code_from_url`` on the URL instead.
    """
    main_m = re.search(r'<main\b[^>]*>(.*?)</main>', html, re.DOTALL | re.IGNORECASE)
    body = main_m.group(1) if main_m else html

    h1_m = re.search(r'<h1[^>]*>\s*(.*?)\(([A-Z0-9.]+)\)\s*</h1>', body,
                     re.DOTALL | re.IGNORECASE)
    if not h1_m:
        return None, None, "", []
    gg_title = normalize_text(strip_tags(h1_m.group(1)).strip())
    gg_code  = h1_m.group(2).strip()

    # Description: <p> tags between h1 and first <section>
    after_h1   = body[h1_m.end():]
    sec_pos    = after_h1.find('<section')
    before_sec = after_h1[:sec_pos] if sec_pos >= 0 else after_h1

    desc_parts = []
    for p_m in re.finditer(r'<p[^>]*>(.*?)</p>', before_sec, re.DOTALL):
        text = normalize_text(strip_tags(p_m.group(1)).strip())
        if text and len(text) > 40:
            desc_parts.append(text)
        if len(desc_parts) >= 2:
            break
    gg_desc = " ".join(desc_parts)

    # Subgroup sections
    subgroups = []
    for sec_m in re.finditer(r'<section[^>]*>(.*?)</section>', body,
                             re.DOTALL | re.IGNORECASE):
        sec_html = sec_m.group(1)
        h2_m = re.search(
            r'<h2[^>]*>\s*(.*?)\(([^)]+)\)\s*</h2>', sec_html,
            re.DOTALL | re.IGNORECASE,
        )
        if not h2_m:
            continue
        sg_title = normalize_text(strip_tags(h2_m.group(1)).strip())
        sg_code  = h2_m.group(2).strip()

        after_h2 = sec_html[h2_m.end():]
        sg_desc  = ""
        for p_m in re.finditer(r'<p[^>]*>(.*?)</p>', after_h2, re.DOTALL):
            text = normalize_text(strip_tags(p_m.group(1)).strip())
            if text and not _HORIZON_SEQ_RE.match(text) and len(text) > 30:
                sg_desc = text
                break

        subgroups.append((sg_code, sg_title, sg_desc))

    return gg_code, gg_title, gg_desc, subgroups


# ---------------------------------------------------------------------------
# Zip builder (shared by -a and -f)
# ---------------------------------------------------------------------------

def _crawl_and_save_cssc_zip(key, base_url, locales, index_html=None):
    """Fetch index, chapters 4-13, and all GG pages (EN + FR); write zip.

    The resulting zip is stored at ``sources/{_SOIL_ORDER_KEY}.zip`` regardless
    of which *key* triggered the fetch, since all CSSC source keys share one
    archive.
    """
    zip_path = _zip_path_for()
    pages    = {}
    fetch_fr = "fr" in locales

    def _get(url, label=""):
        if url in pages:
            return pages[url]
        try:
            html = fetch_html(url)
            pages[url] = html
            return html
        except Exception as e:
            tag = f" ({label})" if label else ""
            print(f"  Warning: failed to fetch {url}{tag}: {e}", file=sys.stderr)
            return None

    # Index page
    index_url = base_url.split("#")[0].split("?")[0]
    if index_html is not None:
        pages[index_url] = index_html
    else:
        _get(index_url, "index")
    if fetch_fr:
        _get(cssc_fr_url(index_url), "FR index")

    # Chapter pages + all GG pages discovered from each chapter
    base_dir = _cssc_base_dir(base_url)
    for chpt, order_code in _ORDER_CHAPTERS:
        en_chpt = urllib.parse.urljoin(base_dir, f"{chpt}.html")
        chpt_html = _get(en_chpt, order_code)
        if fetch_fr:
            _get(cssc_fr_url(en_chpt), f"FR {order_code}")

        if chpt_html:
            for gg_url, gg_name in _parse_chapter_gg_urls(chpt_html):
                _get(gg_url, f"{order_code}/{gg_name}")
                if fetch_fr:
                    _get(cssc_fr_url(gg_url), f"FR {order_code}/{gg_name}")

    # Write zip
    manifest = {}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, (url, html) in enumerate(pages.items()):
            entry = f"{i:04d}.html"
            zf.writestr(entry, (html or "").encode("utf-8"))
            manifest[url] = entry
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    ok = sum(1 for h in pages.values() if h)
    print(f"  Saved {ok}/{len(pages)} pages to {zip_path}")


# ---------------------------------------------------------------------------
# Enum builder — unified three-level taxonomy
# ---------------------------------------------------------------------------

def _build_taxonomy_enum(locales, cache, base_dir):
    """Build the unified CSSC_SoilOrder enum with all three taxonomic levels.

    Level 1 — Soil Orders: 10 PVs from hardcoded data, no is_a.
    Level 2 — Great Groups: ~31 PVs parsed from chapter tables + GG pages,
               is_a → order code (e.g., BR.MB is_a BR).
    Level 3 — Subgroups: ~231 PVs parsed from GG page sections,
               is_a → great group code (e.g., O.MB is_a BR.MB).

    FR titles/descriptions are matched positionally to EN subgroup sections
    (same section order on both language pages).
    """
    fetch_fr = "fr" in locales
    permissible_values = {}
    fr_pvs = {}

    # Single pass: add each order PV then immediately its GGs and subgroups,
    # so the output dict preserves depth-first insertion order.
    for chpt, order_code in _ORDER_CHAPTERS:
        # --- Level 1: Soil Order ---
        add_permissible_value(
            permissible_values, order_code,
            title=_ORDER_TITLES[order_code]["en"],
            description=_PV_DESCRIPTIONS[order_code]["en"],
        )
        if fetch_fr:
            add_permissible_value(
                fr_pvs, order_code,
                title=_ORDER_TITLES[order_code]["fr"],
                description=_PV_DESCRIPTIONS[order_code]["fr"],
            )

        # --- Levels 2 & 3: Great Groups and Subgroups (from HTML) ---
        en_chpt_url = urllib.parse.urljoin(base_dir, f"{chpt}.html")
        chpt_html   = _html_get(en_chpt_url, cache)
        if not chpt_html:
            print(f"  Warning: no HTML for {en_chpt_url}", file=sys.stderr)
            continue

        for gg_url, gg_name in _parse_chapter_gg_urls(chpt_html):
            gg_code = _gg_code_from_url(gg_url)
            if not gg_code:
                print(f"  Warning: could not parse GG code from {gg_url}",
                      file=sys.stderr)
                continue
            gg_key = f"{order_code}.{gg_code}"

            # EN: GG description + subgroup list
            gg_html = _html_get(gg_url, cache)
            gg_desc      = ""
            en_subgroups = []
            if gg_html:
                _, _, gg_desc, en_subgroups = _parse_gg_page(gg_html)

            # FR: title, description, subgroup list (matched positionally)
            fr_gg_title  = None
            fr_gg_desc   = None
            fr_subgroups = []
            if fetch_fr:
                fr_html = _html_get(cssc_fr_url(gg_url), cache)
                if fr_html:
                    _, fr_gg_title, fr_gg_desc, fr_subgroups = _parse_gg_page(fr_html)

            # Add Great Group PV (Level 2)
            pv = add_permissible_value(
                permissible_values, gg_key,
                title=gg_name,
                description=gg_desc or None,
                is_a=order_code,
            )
            pv["see_also"] = gg_url

            if fetch_fr:
                add_permissible_value(
                    fr_pvs, gg_key,
                    title=fr_gg_title or None,
                    description=fr_gg_desc or None,
                    is_a=order_code,
                )

            # Add Subgroup PVs (Level 3)
            for i, (sg_code, sg_title, sg_desc) in enumerate(en_subgroups):
                pv = add_permissible_value(
                    permissible_values, sg_code,
                    title=sg_title,
                    description=sg_desc or None,
                    is_a=gg_key,
                )
                pv["see_also"] = gg_url

                if fetch_fr and i < len(fr_subgroups):
                    _, fr_sg_title, fr_sg_desc = fr_subgroups[i]
                    add_permissible_value(
                        fr_pvs, sg_code,
                        title=fr_sg_title or None,
                        description=fr_sg_desc or None,
                        is_a=gg_key,
                    )

    return permissible_values, fr_pvs


# ---------------------------------------------------------------------------
# Public fetch function
# ---------------------------------------------------------------------------

def fetch_cssc_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Re-download all CSSC pages and rebuild sources/CSSCv3_SoilOrder.zip."""
    base_url = (source.get("reachable_from") or {}).get("source_ontology", "")
    if not base_url:
        print(f"  Skipping {key}: no source_ontology URL.", file=sys.stderr)
        return
    if locales is None:
        try:
            with open(config_file) as f:
                locales = (yaml.safe_load(f) or {}).get("locales") or ["en"]
        except Exception:
            locales = ["en"]

    print(f"  Fetching all pages for '{key}' (CSSC) ...")
    _crawl_and_save_cssc_zip(key, base_url, locales)
    update_source_config(key, {"download_date": datetime.date.today().isoformat()},
                         config_file)


# ---------------------------------------------------------------------------
# Public process function
# ---------------------------------------------------------------------------

def process_cssc_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Build the unified CSSC_SoilOrder LinkML enum YAML.

    All CSSC source keys (SoilOrder, GreatGroup, Subgroup) dispatch here and
    produce the same combined taxonomy enum.  The SoilOrder key is canonical;
    GreatGroup and Subgroup keys are accepted for backward compatibility.
    """
    yaml_path = f"sources/{key}.yaml"
    zip_path  = _zip_path_for()
    base_url  = (source.get("reachable_from") or {}).get("source_ontology", "")

    if locales is None:
        try:
            with open(config_file) as f:
                locales = (yaml.safe_load(f) or {}).get("locales") or ["en"]
        except Exception:
            locales = ["en"]

    if not os.path.exists(zip_path):
        print(
            f"  Error: {zip_path} not found. "
            f"Run '-f {_SOIL_ORDER_KEY}' to download first.",
            file=sys.stderr,
        )
        return

    cache    = _load_zip_cache(zip_path)
    base_dir = _cssc_base_dir(base_url) if base_url else ""

    permissible_values, fr_pvs = _build_taxonomy_enum(locales, cache, base_dir)

    enum_key = "CSSC_SoilOrder"
    schema = make_config_schema(
        id=base_url,
        name=key,
        title=source.get("title", "CSSC Soil Classification"),
        description=_ENUM_DESCRIPTION["en"],
        version=source.get("version", _CSSC_VERSION),
    )
    schema["enums"] = {}
    schema.pop("extensions", None)

    schema["enums"][enum_key] = {
        "name":               enum_key,
        "title":              "CSSC Soil Classification",
        "description":        _ENUM_DESCRIPTION["en"],
        "permissible_values": permissible_values,
    }

    lang_counts = {"fr": len(fr_pvs)} if fr_pvs else None
    log_extraction(enum_key, count=len(permissible_values), lang_counts=lang_counts)

    if "fr" in locales and (fr_pvs or _ENUM_DESCRIPTION.get("fr")):
        schema["extensions"] = _make_locale_extensions(
            cssc_fr_url(base_url) if base_url else "",
            key,
            source.get("version", _CSSC_VERSION),
            "fr",
            description=_ENUM_DESCRIPTION.get("fr"),
            enums={enum_key: {"permissible_values": fr_pvs}} if fr_pvs else None,
        )

    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper,
                  default_flow_style=False, sort_keys=False)
    print(f"  Written {yaml_path}")


# ---------------------------------------------------------------------------
# Match function (-a handler)
# ---------------------------------------------------------------------------

def match_cssc(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is a CSSC index or order-chapter page and was handled.

    Creates a single ``CSSCv3_SoilOrder`` config entry containing the full
    three-level taxonomy (Order → Great Group → Subgroup) in one enum.
    """
    if "sis.agr.gc.ca" not in url or "/taxa/cssc3/" not in url:
        return False

    if "/siscan/" in url:
        os.unlink(tmp_path)
        return True

    with open(tmp_path, encoding="utf-8", errors="replace") as f:
        html_text = f.read()

    url_path = url.split("#")[0].rstrip("/")
    basename = url_path.rsplit("/", 1)[-1].lower()

    _chpt_re   = re.compile(r"^chpt0[4-9]\.html$|^chpt1[0-3]\.html$")
    is_index   = basename in ("index.html", "index.htm", "")
    is_chapter = bool(_chpt_re.match(basename))

    if not (is_index or is_chapter):
        return False

    os.unlink(tmp_path)

    base_dir  = _cssc_base_dir(url)
    index_url = urllib.parse.urljoin(base_dir, "index.html")

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}

    locales = config.get("locales", ["en"])

    if _SOIL_ORDER_KEY in config.get("sources", {}):
        print(
            f"  Source key '{_SOIL_ORDER_KEY}' already exists in {config_file} — skipping.",
            file=sys.stderr,
        )
    else:
        entry = make_source_entry(
            _SOIL_ORDER_KEY, index_url, "CSSC", "zip",
            title="CSSC Soil Classification",
            version=_CSSC_VERSION,
            description=_ENUM_DESCRIPTION["en"],
        )
        entry["see_also"] = _CSSC_SEE_ALSO
        config.setdefault("sources", {})[_SOIL_ORDER_KEY] = entry
        write_config(config, config_file)
        print(f"Added source '{_SOIL_ORDER_KEY}' to {config_file}")

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}

    so_source   = config.get("sources", {}).get(_SOIL_ORDER_KEY, {})
    _index_html = html_text if is_index else None
    _crawl_and_save_cssc_zip(_SOIL_ORDER_KEY, index_url, locales,
                             index_html=_index_html)
    process_cssc_source(_SOIL_ORDER_KEY, so_source, config_file, locales=locales)
    return True
