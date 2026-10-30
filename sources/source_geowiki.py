"""Wikidata geopolitical vocabulary handler for term_harvester.py.

Queries Wikidata SPARQL for current geopolitical entities and produces
Wikidata-native LinkML enums.  For GeoNames-native enums see source_geonames.py.

Content types
-------------
GeoWikiCountries
    Sovereign states (wdt:P297), continents (P31=Q5107), and major oceans
    (P31=Q9430) from Wikidata.  Historical entities filtered by absence of
    wdt:P576 (dissolved/abolished date).
GeoWikiADM1
    First-order administrative divisions queried via P131/P17 from Wikidata.
    Per-country supported on WDQS; global mode requires QLever.
GeoWikiADM2
    Second-order divisions.  Per-country only on WDQS.
GeoWikiSettlement
    Populated places (P31 in settlement type set, P17 = country) from Wikidata.
    All non-current settlements are emitted as status: historical — both archaic
    P31 types (ghost towns, former settlements, etc.) and formally dissolved items
    (P576 set).  The default config entry adds minus: {status: [historical]} so
    historical places are excluded from picklists unless the user opts in.
    Uses a 2-minute WDQS timeout; set apis.qlever: true for large countries.

Shorthand -a patterns
---------------------
GeoWiki_countries       → GeoWikiCountries (sovereign states + continents + oceans)
GeoWiki_ADM1            → GeoWikiADM1 (all ADM1; QLever recommended globally)
GeoWiki_{CC}_ADM1       → GeoWikiADM1 single country (e.g. GeoWiki_US_ADM1)
GeoWiki_{CC}_ADM2       → GeoWikiADM2 single country (stub)
GeoWiki_{CC}_PPL        → GeoWikiSettlement for one country (e.g. GeoWiki_NL_PPL)

Permissible value structure
---------------------------
code           Wikidata QID (e.g. "Q30")
meaning        wd:{QID}         (prefix wd: = http://www.wikidata.org/entity/)
description    Wikidata schema:description (English)
is_a           AdministrativeLevel | GeographicLandmass | GeographicWaterBody
               (top-level) or parent QID for ADM1/ADM2

Cross-references (SSSOM)
------------------------
GeoNames P1566 (gnf:) and ISO 3166 alpha-2 (iso:) mappings are written to
sources/GeoWiki.sssom.tsv as skos:exactMatch rows rather than inline
exact_mappings.  Users can merge this file into the -b build with the
sssom option in harvester_config.yaml.

Top-level hierarchy headers (no is_a)
--------------------------------------
AdministrativeLevel — countries, territories, admin divisions
GeographicLandmass  — continents
GeographicWaterBody — oceans, seas

Historical filtering
--------------------
Countries:  must have wdt:P297 (ISO 3166-1 alpha-2) AND
            FILTER NOT EXISTS { ?item wdt:P576 ?end . }
Continents: queried by P31=wd:Q5107, same dissolution filter
Oceans:     queried by P31=wd:Q9430; nearly all are non-dissolved
ADM1/ADM2:  must have wdt:P131 (admin parent) pointing to expected entity AND
            FILTER NOT EXISTS { ?item wdt:P576 ?end . }

QLever support
--------------
Configure apis.qlever: true in the source entry in harvester_config.yaml to
use QLever (https://qlever.cs.uni-freiburg.de/api/wikidata) instead of WDQS.
Required for global ADM1 queries (~4,000 items) and any ADM2 queries.

License
-------
Wikidata data: Creative Commons CC0 1.0 Universal Public Domain Dedication

Public API used by term_harvester.py
-------------------------------------
match_geowiki_code(url, config_file)
fetch_geowiki_source(key, source, config_file)
process_geowiki_source(key, source, config_file, locales)
"""

import datetime
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import yaml
import zipfile
from collections import Counter, defaultdict

from source_utils import (
    BROWSER_HEADERS,
    MENU_CONFIG,
    IndentedDumper,
    _make_locale_extensions,
    add_permissible_value,
    log_extraction,
    make_config_schema,
    make_source_entry,
    normalize_text,
    update_source_config,
    write_config,
    write_sssom_mappings,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GN_URI_BASE     = "https://sws.geonames.org/"
_WD_ENTITY_BASE  = "http://www.wikidata.org/entity/"
_WDQS_ENDPOINT   = "https://query.wikidata.org/sparql"
_QLEVER_ENDPOINT = "https://qlever.cs.uni-freiburg.de/api/wikidata"
_ISO_URI_BASE    = "https://www.iso.org/obp/ui/#iso:code:3166:"

_GN_ONTOLOGY_BASE = "http://www.geonames.org/ontology#"

_SOURCE_PREFIXES = {
    "wd":  _WD_ENTITY_BASE,
    "gnf": _GN_URI_BASE,
    "gno": _GN_ONTOLOGY_BASE,
    "iso": _ISO_URI_BASE,
}

# SSSOM output file for GeoWiki → GeoNames / ISO exact-match cross-references
_GEOWIKI_SSSOM      = os.path.join("sources", "GeoWiki.sssom.tsv")
_GEOWIKI_SSSOM_ID   = "https://w3id.org/wikidata-geo/mappings"
_GEOWIKI_SSSOM_DESC = ("Exact-match cross-references between Wikidata geographic entities "
                       "(wd: URIs) and GeoNames IDs (gnf:) / ISO 3166 codes (iso:).")
_SSSOM_PREFIXES     = {
    "gnf":    _GN_URI_BASE,
    "gno":    _GN_ONTOLOGY_BASE,
    "iso":    _ISO_URI_BASE,
    "semapv": "https://w3id.org/semapv/vocab/",
    "skos":   "http://www.w3.org/2004/02/skos/core#",
    "wd":     _WD_ENTITY_BASE,
}

# Canonical GeoNames download URL (used as source_ontology reference)
_GEONAMES_COUNTRY_URL = "https://download.geonames.org/export/dump/countryInfo.txt"
_GEONAMES_ADM1_URL    = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"
_GEONAMES_ADM2_URL    = "https://download.geonames.org/export/dump/admin2Codes.txt"

# Wikidata entity classes for geographic types
_WD_CLASS_SOVEREIGN_STATE = "Q3624078"   # sovereign state
_WD_CLASS_CONTINENT       = "Q5107"      # continent
_WD_CLASS_OCEAN           = "Q9430"      # ocean

# GeoNames 2-letter continent code → Wikidata QID (stable, canonical)
_GN_CONT_CODE_TO_WD_QID = {
    "AF": "Q15",    # Africa
    "AS": "Q48",    # Asia
    "EU": "Q46",    # Europe
    "NA": "Q49",    # North America
    "SA": "Q18",    # South America
    "OC": "Q55643", # Oceania
    "AN": "Q51",    # Antarctica
}

# Wikidata instance types that represent human settlements / populated places
# Used for GeoWikiSettlement queries (P31 in this set, P17 = country)
_WD_SETTLEMENT_TYPES = frozenset({
    "Q1637706",   # million city
    "Q1549591",   # big city
    "Q129676344", # large city
    "Q515",       # city
    "Q896881",    # average city
    "Q18466176",  # small city
    "Q3957",      # town
    "Q532",       # village
    "Q5084",      # hamlet
    "Q129676371", # rural settlement
    "Q7930989",   # city/town
    "Q747074",    # urban municipality
    "Q486972",    # human settlement
    "Q2095140",   # populated place below civil division level
})

# Wikidata P31 types that identify archaic, abandoned, or otherwise non-current
# settlements.  Items carrying any of these types are fetched but marked
# status: historical in the output YAML so they can be excluded from
# picklists by default (minus: {status: [historical]}) while remaining
# available to users who explicitly opt in.
# An item with BOTH an active type and an archaic type (e.g. Q532 village AND
# Q350614 ghost town) will be marked historical regardless.
_WD_SETTLEMENT_ARCHAIC_TYPES = frozenset({
    "Q350614",    # ghost town
    "Q2974842",   # abandoned village / deserted settlement
    "Q350895",    # archaeological site
    "Q106071744", # former town
    "Q21751582",  # deserted medieval village
    "Q22674925",  # former settlement
    "Q14616455",  # destroyed city
    "Q1434274",   # archaic settlement type
})

# Priority order for settlement size types: most specific (largest) first.
# When an item has multiple matching P31 stypes, keep the most specific.
_STYPE_PRIORITY: list = [
    # Active settlement types — most specific (largest) first
    "Q1637706",    # million city
    "Q1549591",    # big city
    "Q129676344",  # large city
    "Q515",        # city
    "Q896881",     # average city
    "Q18466176",   # small city
    "Q7930989",    # city or town
    "Q3957",       # town
    "Q747074",     # urban municipality
    "Q532",        # village
    "Q5084",       # hamlet
    "Q129676371",  # rural settlement
    "Q486972",     # human settlement
    "Q2095140",    # populated place below civil division level
    # Archaic/abandoned types — lowest priority; active types always win dedup
    "Q350614",     # ghost town
    "Q2974842",    # abandoned village / deserted settlement
    "Q350895",     # archaeological site
    "Q106071744",  # former town
    "Q21751582",   # deserted medieval village
    "Q22674925",   # former settlement
    "Q14616455",   # destroyed city
    "Q1434274",    # archaic settlement
]
_STYPE_RANK: dict = {q: i for i, q in enumerate(_STYPE_PRIORITY)}

# Human-readable labels for settlement size type headers in the enum
_STYPE_LABELS: dict = {
    "Q1637706":    "million city",
    "Q1549591":    "big city",
    "Q129676344":  "large city",
    "Q515":        "city",
    "Q896881":     "average city",
    "Q18466176":   "small city",
    "Q7930989":    "city or town",
    "Q3957":       "town",
    "Q747074":     "urban municipality",
    "Q532":        "village",
    "Q5084":       "hamlet",
    "Q129676371":  "rural settlement",
    "Q486972":     "human settlement",
    "Q2095140":    "populated place",
    "Q350614":     "ghost town",
    "Q2974842":    "abandoned village",
    "Q350895":     "archaeological site",
    "Q106071744":  "former town",
    "Q21751582":   "deserted medieval village",
    "Q22674925":   "former settlement",
    "Q14616455":   "destroyed city",
    "Q1434274":    "archaic settlement",
}

# Top-level WD enum hierarchy headers
_WD_ROOT_ADMIN    = "AdministrativeLevel"
_WD_ROOT_LANDMASS = "GeographicLandmass"
_WD_ROOT_WATER    = "GeographicWaterBody"

# Descriptions for ADM1 type-group header terms (keyed by capitalised type code,
# i.e. the value of `tl[0].upper() + tl[1:]` used as the PV code).
_ADM1_TYPE_HEADER_DESCRIPTIONS: dict[str, str] = {
    "U.S. state": (
        "One of the 50 constituent political entities of the United States."
    ),
    "Insular area of the United States": (
        "A U.S. jurisdiction that is not one of the 50 U.S. states and not "
        "the District of Columbia."
    ),
}

# Wikidata query batch size for WDQS
_WD_BATCH = 50

# Manual overrides for GeoNames gnid → Wikidata QID pairs where Wikidata is
# missing a P1566 value.  Keyed by GeoNames numeric ID (string).  Entries here
# are applied as a fallback after the P1566 reverse-lookup, so they only take
# effect for gnids that the SPARQL query could not match.  Add new entries
# whenever a known P1566 gap is discovered during a fetch run.
_GNID_QID_OVERRIDES: dict[str, str] = {
    "5391997": "Q5413764",  # City and County of San Francisco, CA, US
}

# Maps Wikidata QIDs of known intermediate administrative entities to the ADM1
# QID they sit within.  Used in _build_adm2_yaml when an ADM2 entry's adm1_qid
# is not directly in the ADM1 enum because Wikidata P131 points one level too
# deep.  Add entries whenever "-c {CC}_ADM2" prints an orphan warning.
_ADM2_INTERMEDIATE_PARENT_QIDS: dict[str, str] = {
    "Q1474662":  "Q797",  # Unorganized Borough of Alaska → Alaska
    "Q13188841": "Q99",   # City and County of San Francisco entity → California
}

# Direct ADM2 QID → ADM1 QID overrides for entries where Wikidata returned no
# P131 at all (adm1_qid empty).  Add entries whenever an entry appears as an
# orphan and Wikidata lacks a P131 link.
_ADM2_NO_PARENT_OVERRIDES: dict[str, str] = {
    "Q25479087": "Q1370",  # City of Suffolk, Virginia → Virginia
}

# Shorthand regex: GeoWiki_countries, GeoWiki_ADM1, GeoWiki_US_ADM1, GeoWiki_CA_ADM2, GeoWiki_NL_PPL
_SHORTHAND_RE = re.compile(
    r'^GeoWiki_(?:(countries|ADM1)|([A-Za-z]{2})_(ADM[1-4]|PPL))$',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHARED_ZIP = os.path.join("sources", "GeoWiki.zip")

def _zip_path(key):           # noqa: ARG001  (key ignored; all payloads share one zip)
    return _SHARED_ZIP

def _json_name(key):
    return f"{key}.json"

def _entry_exists(key):
    """Return True if {key}.json exists inside the shared GeoWiki.zip."""
    if not os.path.exists(_SHARED_ZIP):
        return False
    try:
        with zipfile.ZipFile(_SHARED_ZIP) as zf:
            return _json_name(key) in zf.namelist()
    except zipfile.BadZipFile:
        return False

def _write_entry(zip_path, entry_name, data_bytes):
    """Add or update *entry_name* inside *zip_path*, preserving all other entries."""
    existing = {}
    if os.path.exists(zip_path):
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    if name != entry_name:
                        existing[name] = zf.read(name)
        except zipfile.BadZipFile:
            pass
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in existing.items():
            zf.writestr(name, data)
        zf.writestr(entry_name, data_bytes)

def _qid_from_uri(uri):
    if uri and uri.startswith(_WD_ENTITY_BASE):
        return uri[len(_WD_ENTITY_BASE):]
    return uri.rsplit("/", 1)[-1] if uri else ""

def _get_project_locales(config_file=MENU_CONFIG):
    try:
        with open(config_file) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("locales") or ["en"]
    except Exception:
        return ["en"]

def _qlever_enabled(source):
    return bool((source or {}).get("apis", {}).get("qlever"))


def _title_sort_key(s):
    """Unicode-aware sort key: NFD decomposition so Å sorts with A, é with e."""
    return unicodedata.normalize('NFD', s or '').casefold()


def _adm1_enum_title(type_words_by_freq, country_name):
    """Build a country-specific ADM1 enum title from discovered type words.

    type_words_by_freq: list of (type_word_lower, count), most-common first.
    Returns e.g. "Province or Territory of Canada".
    """
    if not type_words_by_freq:
        return f"Province or State of {country_name}" if country_name else "Province or State"
    parts = [w.capitalize() for w, _ in type_words_by_freq]
    suffix = f" of {country_name}" if country_name else ""
    if len(parts) == 1:
        return f"{parts[0]}{suffix}"
    if len(parts) == 2:
        return f"{parts[0]} or {parts[1]}{suffix}"
    return f"{', '.join(parts[:-1])}, or {parts[-1]}{suffix}"


# ---------------------------------------------------------------------------
# SPARQL helper
# ---------------------------------------------------------------------------

def _sparql_query(sparql, timeout=60, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Execute a SPARQL query; return the bindings list."""
    headers = {
        **BROWSER_HEADERS,
        "Accept": "application/sparql-results+json",
        "User-Agent": (
            "term-harvester/1.0 (https://github.com/agrifooddatacanada/term-harvester)"
        ),
    }
    if use_post:
        body = urllib.parse.urlencode({"query": sparql}).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(endpoint, data=body, headers=headers)
    else:
        params = urllib.parse.urlencode({"query": sparql, "format": "json"})
        req = urllib.request.Request(f"{endpoint}?{params}", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["results"]["bindings"]


# ---------------------------------------------------------------------------
# Wikidata fetch functions
# ---------------------------------------------------------------------------

def _fetch_wikidata_countries(locales=None, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Query Wikidata for current sovereign states via P297 (ISO alpha-2).

    Returns list of dicts: qid, alpha2, labels, desc, gnid.
    """
    locales = locales or ["en"]
    label_vars     = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
        f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )

    # Sovereign-state P31 types: Q3624078 (sovereign state) + Q15634554 (partially
    # recognised state, covers Kosovo, Palestine, etc.)
    sovereign_union = (
        "{ ?item wdt:P31 wd:Q3624078 . } "
        "UNION { ?item wdt:P31 wd:Q15634554 . }"
    )

    sparql = f"""
SELECT ?item ?alpha2 {label_vars} ?desc ?gnid ?continent ?is_sovereign ?parent WHERE {{
  ?item wdt:P297 ?alpha2 .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
  OPTIONAL {{ ?item wdt:P1566 ?gnid . }}
  OPTIONAL {{ ?item wdt:P30 ?continent . }}
  BIND(EXISTS {{ {sovereign_union} }} AS ?is_sovereign)
  OPTIONAL {{ ?item wdt:P17 ?parent . }}
}}
ORDER BY ?alpha2
"""
    print("  Querying Wikidata for P297 entities (sovereign states + territories) ...")
    rows = _sparql_query(sparql, timeout=30, endpoint=endpoint, use_post=use_post)

    countries = []
    seen = set()
    for row in rows:
        qid = _qid_from_uri(row.get("item", {}).get("value", ""))
        if not qid or qid in seen:
            continue
        seen.add(qid)
        alpha2        = row.get("alpha2",       {}).get("value", "")
        gnid          = row.get("gnid",         {}).get("value", "")
        cont_uri      = row.get("continent",    {}).get("value", "")
        parent_uri    = row.get("parent",       {}).get("value", "")
        continent_qid = _qid_from_uri(cont_uri)    if cont_uri    else ""
        parent_qid    = _qid_from_uri(parent_uri)  if parent_uri  else ""
        is_sovereign  = row.get("is_sovereign", {}).get("value", "false").lower() == "true"
        labels = {
            lang: row[f"label_{lang}"]["value"]
            for lang in locales if row.get(f"label_{lang}", {}).get("value")
        }
        desc = row.get("desc", {}).get("value", "")
        countries.append({
            "qid": qid, "alpha2": alpha2, "labels": labels, "desc": desc,
            "gnid": gnid, "continent_qid": continent_qid,
            "is_sovereign": is_sovereign, "parent_qid": parent_qid,
        })

    n_sov = sum(1 for c in countries if c["is_sovereign"])
    n_ter = len(countries) - n_sov
    print(f"    Found {n_sov} sovereign states + {n_ter} territories/dependencies.")
    return countries


def _fetch_wikidata_continents(locales=None, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Query Wikidata for continents (P31=Q5107) that have a GeoNames P1566 ID.

    Requiring P1566 filters out geological/conceptual duplicates that also carry
    P31=Q5107 but are never used as P30 (continent) by any country:

      Kept (have GeoNames IDs, used as P30):
        Q15 Africa, Q48 Asia, Q46 Europe, Q49 North America,
        Q18 South America, Q55643 Oceania, Q51 Antarctica, Q828 Americas

      Excluded (no GeoNames ID, zero P30 usage):
        Q17173147 North American continent (geological landmass model)
        Q17173219 South American continent (geological landmass model)
        Q11284672 African Continent (duplicate conceptualisation)
        Q3960     Australian continent (geological plate concept)
        Q27527    Afro-Eurasia (supercontinent grouping)
        Q7856408  Turtle Island (indigenous North American name)
        Q55614502 Insular India (sub-regional concept)

    Returns list of dicts: qid, labels, desc, gnid.
    """
    locales = locales or ["en"]
    label_vars     = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
        f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )

    sparql = f"""
SELECT ?item {label_vars} ?desc ?gnid WHERE {{
  ?item wdt:P31 wd:{_WD_CLASS_CONTINENT} .
  ?item wdt:P1566 ?gnid .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
}}
"""
    print("  Querying Wikidata for continents (P31=Q5107) ...")
    rows = _sparql_query(sparql, timeout=20, endpoint=endpoint, use_post=use_post)

    continents = []
    seen = set()
    for row in rows:
        qid = _qid_from_uri(row.get("item", {}).get("value", ""))
        if not qid or qid in seen:
            continue
        seen.add(qid)
        gnid   = row.get("gnid",   {}).get("value", "")
        labels = {
            lang: row[f"label_{lang}"]["value"]
            for lang in locales if row.get(f"label_{lang}", {}).get("value")
        }
        desc = row.get("desc", {}).get("value", "")
        continents.append({"qid": qid, "labels": labels, "desc": desc, "gnid": gnid})

    print(f"    Found {len(continents)} continents.")
    return continents


def _fetch_wikidata_oceans(locales=None, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Query Wikidata for oceans (P31=Q9430).

    Returns list of dicts: qid, labels, desc, gnid.
    """
    locales = locales or ["en"]
    label_vars     = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
        f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )

    sparql = f"""
SELECT ?item {label_vars} ?desc ?gnid WHERE {{
  ?item wdt:P31 wd:{_WD_CLASS_OCEAN} .
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
  OPTIONAL {{ ?item wdt:P1566 ?gnid . }}
}}
"""
    print("  Querying Wikidata for oceans (P31=Q9430) ...")
    rows = _sparql_query(sparql, timeout=20, endpoint=endpoint, use_post=use_post)

    oceans = []
    seen = set()
    for row in rows:
        qid = _qid_from_uri(row.get("item", {}).get("value", ""))
        if not qid or qid in seen:
            continue
        seen.add(qid)
        gnid   = row.get("gnid",   {}).get("value", "")
        labels = {
            lang: row[f"label_{lang}"]["value"]
            for lang in locales if row.get(f"label_{lang}", {}).get("value")
        }
        desc = row.get("desc", {}).get("value", "")
        oceans.append({"qid": qid, "labels": labels, "desc": desc, "gnid": gnid})

    print(f"    Found {len(oceans)} oceans.")
    return oceans


def _fetch_wikidata_adm1_for_country(alpha2, locales=None,
                                      endpoint=_WDQS_ENDPOINT, use_post=False):
    """Query Wikidata for ADM1 divisions of one country via P17 and P131.

    Uses the ISO alpha-2 code to find the country item, then fetches all items
    where P17 = that country AND P131 = that country (direct children).

    Returns list of dicts: qid, labels, desc, gnid, type_label.
    """
    locales = locales or ["en"]

    # Step 1: find the country QID
    country_sparql = f"""
SELECT ?item WHERE {{
  ?item wdt:P297 "{alpha2}" .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
}}
LIMIT 1
"""
    country_qid = ""
    try:
        rows = _sparql_query(country_sparql, timeout=15, endpoint=endpoint, use_post=use_post)
        if rows:
            country_qid = _qid_from_uri(rows[0].get("item", {}).get("value", ""))
    except Exception as exc:
        print(f"  Warning: could not find country QID for {alpha2}: {exc}", file=sys.stderr)
        return []

    if not country_qid:
        print(f"  Warning: no Wikidata item found for ISO alpha-2 '{alpha2}'", file=sys.stderr)
        return []

    # Step 2: fetch first-order divisions (P131 = country, direct children)
    label_vars     = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
        f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )

    adm1_sparql = f"""
SELECT ?item {label_vars} ?desc ?gnid ?typeLabel WHERE {{
  ?item wdt:P17 wd:{country_qid} .
  ?item wdt:P131 wd:{country_qid} .
  ?item wdt:P31/wdt:P279* wd:Q10864048 .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
  OPTIONAL {{ ?item wdt:P1566 ?gnid . }}
  OPTIONAL {{
    ?item wdt:P31 ?type .
    ?type rdfs:label ?typeLabel .
    FILTER(LANG(?typeLabel) = "en")
  }}
}}
ORDER BY ?item
"""
    print(f"  Querying Wikidata ADM1 for {alpha2} (wd:{country_qid}) ...")
    try:
        rows = _sparql_query(adm1_sparql, timeout=30, endpoint=endpoint, use_post=use_post)
    except Exception as exc:
        print(f"  Warning: ADM1 query failed for {alpha2}: {exc}", file=sys.stderr)
        return []

    entries = []
    seen = set()
    for row in rows:
        qid = _qid_from_uri(row.get("item", {}).get("value", ""))
        if not qid or qid in seen:
            continue
        seen.add(qid)
        gnid   = row.get("gnid",      {}).get("value", "")
        tlabel = row.get("typeLabel", {}).get("value", "")
        labels = {
            lang: row[f"label_{lang}"]["value"]
            for lang in locales if row.get(f"label_{lang}", {}).get("value")
        }
        desc = row.get("desc", {}).get("value", "")
        entries.append({
            "qid": qid, "labels": labels, "desc": desc, "gnid": gnid,
            "type_label": tlabel, "country_qid": country_qid,
        })

    print(f"    Found {len(entries)} ADM1 items for {alpha2}.")
    return entries


def _fetch_wikidata_settlements(alpha2, locales=None, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Query Wikidata for populated places within a country.

    Finds items where P17 = country AND P31 ∈ _WD_SETTLEMENT_TYPES.
    Items with P576 (dissolved/abolished date) are flagged is_dissolved=True so
    callers can emit them as status: historical in the output YAML.

    Returns list of dicts: qid, labels, desc, gnid, is_dissolved, country_qid.

    Note: large countries (US, China, India …) may time out on WDQS.
    Set apis.qlever: true in harvester_config.yaml to use QLever instead.
    """
    locales = locales or ["en"]

    # Step 1 — resolve country QID from ISO alpha-2
    country_sparql = f"""
SELECT ?item WHERE {{
  ?item wdt:P297 "{alpha2}" .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
}}
LIMIT 1
"""
    country_qid = ""
    try:
        rows = _sparql_query(country_sparql, timeout=15, endpoint=endpoint, use_post=use_post)
        if rows:
            country_qid = _qid_from_uri(rows[0].get("item", {}).get("value", ""))
    except Exception as exc:
        print(f"  Warning: could not find country QID for {alpha2}: {exc}", file=sys.stderr)
        return []

    if not country_qid:
        print(f"  Warning: no Wikidata item found for ISO alpha-2 '{alpha2}'", file=sys.stderr)
        return []

    # Step 2 — query settlements (active + archaic types combined)
    all_types_str   = " ".join(
        f"wd:{t}" for t in sorted(_WD_SETTLEMENT_TYPES | _WD_SETTLEMENT_ARCHAIC_TYPES)
    )
    label_vars      = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
        f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )

    sparql = f"""
SELECT ?item ?stype {label_vars} ?desc ?gnid ?dissolved WHERE {{
  ?item wdt:P17 wd:{country_qid} .
  VALUES ?stype {{ {all_types_str} }}
  ?item wdt:P31 ?stype .
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
  OPTIONAL {{ ?item wdt:P1566 ?gnid . }}
  OPTIONAL {{ ?item wdt:P576 ?dissolved . }}
}}
ORDER BY ?item
"""
    print(f"  Querying Wikidata settlements for {alpha2} (wd:{country_qid}) ...")
    try:
        rows = _sparql_query(sparql, timeout=120, endpoint=endpoint, use_post=use_post)
    except Exception as exc:
        print(f"  Warning: settlements query failed for {alpha2}: {exc}", file=sys.stderr)
        print(f"  Tip: set apis.qlever: true for better performance on large countries.",
              file=sys.stderr)
        return []

    # Deduplicate by QID, keeping most specific stype per item.
    # An item may appear multiple times if it has several matching P31 values.
    seen_map: dict = {}
    for row in rows:
        qid = _qid_from_uri(row.get("item", {}).get("value", ""))
        if not qid:
            continue
        stype        = _qid_from_uri(row.get("stype", {}).get("value", ""))
        gnid         = row.get("gnid",      {}).get("value", "")
        is_dissolved = bool(row.get("dissolved", {}).get("value", ""))
        labels = {
            lang: row[f"label_{lang}"]["value"]
            for lang in locales if row.get(f"label_{lang}", {}).get("value")
        }
        desc = row.get("desc", {}).get("value", "")
        is_archaic = stype in _WD_SETTLEMENT_ARCHAIC_TYPES
        if qid in seen_map:
            # Update stype if this row has a more specific (lower rank) type
            existing_rank = _STYPE_RANK.get(seen_map[qid]["stype"], 999)
            new_rank      = _STYPE_RANK.get(stype, 999)
            if new_rank < existing_rank:
                seen_map[qid]["stype"] = stype
            # Archaic flag is sticky: once set, stays set even if a higher-
            # priority active stype row was already recorded for this QID.
            if is_archaic:
                seen_map[qid]["is_archaic"] = True
            # Merge gnid / dissolved / labels if missing
            if not seen_map[qid]["gnid"] and gnid:
                seen_map[qid]["gnid"] = gnid
            if is_dissolved:
                seen_map[qid]["is_dissolved"] = True
            for lang, lbl in labels.items():
                seen_map[qid]["labels"].setdefault(lang, lbl)
        else:
            seen_map[qid] = {
                "qid":          qid,
                "stype":        stype,
                "labels":       labels,
                "desc":         desc,
                "gnid":         gnid,
                "is_dissolved": is_dissolved,
                "is_archaic":   is_archaic,
                "country_qid":  country_qid,
            }
    entries = list(seen_map.values())

    n_archaic   = sum(1 for e in entries if e.get("is_archaic"))
    n_dissolved = sum(1 for e in entries if e["is_dissolved"] and not e.get("is_archaic"))
    n_current   = len(entries) - n_archaic - n_dissolved
    print(f"    Found {n_current} current + {n_dissolved} dissolved + {n_archaic} archaic settlements for {alpha2}.")
    return entries


# ---------------------------------------------------------------------------
# JSON persistence
# ---------------------------------------------------------------------------

def _save_json(key, payload, config_file=None):
    today = datetime.date.today().isoformat()
    payload["download_date"] = today
    json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    _write_entry(_SHARED_ZIP, _json_name(key), json_bytes)
    if config_file:
        update_source_config(key, {"download_date": today, "file_format": "zip"}, config_file)


def _load_json(key):
    """Return the JSON payload for *key* from the shared GeoWiki.zip."""
    entry_name = _json_name(key)
    try:
        with zipfile.ZipFile(_SHARED_ZIP) as zf:
            if entry_name in zf.namelist():
                return json.loads(zf.read(entry_name).decode("utf-8"))
    except (zipfile.BadZipFile, FileNotFoundError):
        pass
    raise FileNotFoundError(f"{entry_name} not found in {_SHARED_ZIP}")


# ---------------------------------------------------------------------------
# YAML builders
# ---------------------------------------------------------------------------

def _build_countries_yaml(key, source, data, locales=None):
    """Write sources/{key}.yaml for GeoWikiCountries scope.

    Produces four separate enums, each sorted alphabetically by title:
      GeoWikiCountry         — sovereign states (P31=Q3624078 or Q15634554)
      GeoWikiCountryExtended — dependent territories/special regions that have
                               their own ISO alpha-2 code but are not sovereign
                               (P17 for parent, P31 for type); inherits
                               GeoWikiCountry so territory is_a links resolve
                               to inherited sovereign state entries
      GeoWikiContinent       — continents (P31=Q5107)
      GeoWikiOcean           — oceans (P31=Q9430)

    Code = Wikidata QID; meaning = wd:{QID};
    exact_mappings = [gnf:{gnid}, iso:{alpha2}] for countries/territories.

    Backward compatibility: if the cached payload pre-dates the is_sovereign
    field (all entries lack it), all countries are placed in GeoWikiCountry
    and GeoWikiCountryExtended is left empty.
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]

    countries  = data.get("countries",  [])
    continents = data.get("continents", [])
    oceans     = data.get("oceans",     [])

    def _lbl(item):
        return (item.get("labels", {}).get(primary_lang)
                or item.get("labels", {}).get("en")
                or item.get("qid", ""))

    # --- Split sovereign states vs territories ---
    # Old cached payloads lack 'is_sovereign'; treat all as sovereign in that case.
    has_sovereignty_data = any("is_sovereign" in c for c in countries)
    if has_sovereignty_data:
        # Wikidata sometimes assigns the ISO alpha-2 code to a sub-entity rather than
        # the top-level sovereign entity (e.g. Q35 Denmark has P297=DK but P31≠Q3624078
        # because Q756617 "Kingdom of Denmark" is the formal sovereign).  Four-rule
        # classification mirrors what source_geonames.py uses:
        #  1. is_sovereign=True (P31 check passed) → sovereign
        #  2. is_sovereign=False, parent_qid == own qid (self-referential P17) → territory
        #  3. is_sovereign=False, parent_qid in our P297 items set → territory
        #     (parent is a known entry, sovereign or not; e.g. Sark → Guernsey → UK)
        #  4. is_sovereign=False, parent_qid NOT in P297 items set → sovereign
        #     (parent is an out-of-scope political entity like "Kingdom of Denmark";
        #     the P297 item is the effective country representative)
        #  5. is_sovereign=False, parent_qid empty → sovereign (e.g. Antarctica)
        all_country_qids = {c["qid"] for c in countries}
        sovereign_states = []
        territories      = []
        for c in countries:
            qid        = c["qid"]
            parent_qid = c.get("parent_qid", "")
            if c.get("is_sovereign"):
                sovereign_states.append(c)                    # rule 1
            elif parent_qid and parent_qid == qid:
                territories.append(c)                         # rule 2: self-referential P17
            elif parent_qid and parent_qid in all_country_qids:
                territories.append(c)                         # rule 3: parent in dataset
            else:
                sovereign_states.append(c)                    # rule 4/5: unmapped parent
        if territories:
            print(f"  Split: {len(sovereign_states)} sovereign states, "
                  f"{len(territories)} territories/dependencies.")
    else:
        sovereign_states = countries
        territories      = []
        print("  Note: cached payload lacks sovereignty data — re-fetch with "
              "-f to split GeoWikiCountry / GeoWikiCountryExtended.",
              file=sys.stderr)

    # Build continent lookup: qid → continent item
    cont_by_qid = {c["qid"]: c for c in continents}

    # Build sovereign state lookup for is_a resolution in extended enum
    sovereign_by_qid = {c["qid"]: c for c in sovereign_states}

    # --- GeoWikiCountry enum: continent headers + sovereign states grouped ---
    by_continent = defaultdict(list)
    for country in sovereign_states:
        by_continent[country.get("continent_qid", "")].append(country)

    # Sort continents alphabetically (NFD); unknown continent "" goes last
    def _cont_sort(qid):
        if not qid:
            return "\xff"
        cont = cont_by_qid.get(qid, {})
        return _title_sort_key(_lbl(cont) if cont else qid)

    continents_in_use = sorted(by_continent.keys(), key=_cont_sort)

    # Sovereign states with no continent after GeoNames override: report and exclude.
    # (These are very rare — typically only happen with brand-new ISO codes.)
    no_continent = by_continent.pop("", [])
    if no_continent:
        names = ", ".join(f"{c.get('alpha2','')}={_lbl(c)}" for c in no_continent)
        print(
            f"  Warning: {len(no_continent)} sovereign state(s) have no continent "
            f"assignment — excluded from GeoWikiCountry: {names}",
            file=sys.stderr,
        )
        continents_in_use = [q for q in continents_in_use if q != ""]

    sssom_rows = []
    country_pvs = {}
    for cont_qid in continents_in_use:
        cont = cont_by_qid.get(cont_qid, {})
        if cont_qid and cont:
            cont_gnid = cont.get("gnid", "")
            country_pvs[cont_qid] = {
                "title":   normalize_text(_lbl(cont)),
                "meaning": f"wd:{cont_qid}",
            }
            if cont_gnid:
                sssom_rows.append({
                    "subject_id":           f"wd:{cont_qid}",
                    "predicate_id":         "skos:exactMatch",
                    "object_id":            f"gnf:{cont_gnid}",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":        normalize_text(_lbl(cont)),
                    "object_label":         "",
                })
        elif cont_qid:
            country_pvs[cont_qid] = {"title": cont_qid, "meaning": f"wd:{cont_qid}"}

        for item in sorted(by_continent[cont_qid],
                            key=lambda x: _title_sort_key(_lbl(x))):
            qid    = item["qid"]
            gnid   = item.get("gnid", "")
            alpha2 = item.get("alpha2", "")
            lbl    = normalize_text(_lbl(item))
            pv = add_permissible_value(
                country_pvs, qid,
                title=lbl,
                meaning=f"wd:{qid}",
                description=normalize_text(item.get("desc", "")) or None,
                is_a=cont_qid,
            )
            if alpha2:
                pv["aliases"] = [alpha2]
            sssom_rows.append({
                "subject_id":            f"wd:{qid}",
                "predicate_id":          "skos:broadMatch",
                "object_id":             "gno:A.PCLI",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         lbl,
                "object_label":          "",
            })
            if gnid:
                sssom_rows.append({
                    "subject_id":            f"gnf:{gnid}",
                    "predicate_id":          "skos:broadMatch",
                    "object_id":             "gno:A.PCLI",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":         lbl,
                    "object_label":          "",
                })
            if gnid:
                sssom_rows.append({
                    "subject_id":           f"wd:{qid}",
                    "predicate_id":         "skos:exactMatch",
                    "object_id":            f"gnf:{gnid}",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":        lbl,
                    "object_label":         "",
                })
            if alpha2:
                sssom_rows.append({
                    "subject_id":           f"wd:{qid}",
                    "predicate_id":         "skos:exactMatch",
                    "object_id":            f"iso:{alpha2}",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":        lbl,
                    "object_label":         alpha2,
                })

    # --- GeoWikiCountryExtended enum: territories grouped by parent sovereign ---
    # Each parent sovereign state is emitted first as a stand-alone entry so
    # the is_a references resolve within the enum itself.  Territories follow
    # alphabetically under their parent.  Territories without a recognized
    # parent appear last (no is_a).
    def _parent_sort_key(pqid):
        if not pqid:
            return "\xff"
        parent = sovereign_by_qid.get(pqid, {})
        return _title_sort_key(_lbl(parent) if parent else pqid)

    parent_groups = defaultdict(list)
    for t in territories:
        parent_groups[t.get("parent_qid", "")].append(t)

    # extended_pvs contains only territories; sovereign parents are resolved
    # via inherits: GeoWikiCountry on the enum definition.
    extended_pvs = {}
    for pqid in sorted(parent_groups.keys(), key=_parent_sort_key):
        for item in sorted(parent_groups[pqid],
                           key=lambda x: _title_sort_key(_lbl(x))):
            qid    = item["qid"]
            gnid   = item.get("gnid", "")
            alpha2 = item.get("alpha2", "")
            lbl    = normalize_text(_lbl(item))
            # Skip self-referential is_a (parent == self, from rule-2 territories)
            effective_is_a = pqid if (pqid and pqid != qid) else None
            pv = add_permissible_value(
                extended_pvs, qid,
                title=lbl,
                meaning=f"wd:{qid}",
                description=normalize_text(item.get("desc", "")) or None,
                is_a=effective_is_a,
            )
            if alpha2:
                pv["aliases"] = [alpha2]
            if gnid:
                sssom_rows.append({
                    "subject_id":           f"wd:{qid}",
                    "predicate_id":         "skos:exactMatch",
                    "object_id":            f"gnf:{gnid}",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":        lbl,
                    "object_label":         "",
                })
            if alpha2:
                sssom_rows.append({
                    "subject_id":           f"wd:{qid}",
                    "predicate_id":         "skos:exactMatch",
                    "object_id":            f"iso:{alpha2}",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":        lbl,
                    "object_label":         alpha2,
                })

    # --- GeoWikiContinent enum (standalone, sorted alphabetically) ---
    continents_sorted = sorted(continents, key=lambda x: _title_sort_key(_lbl(x)))
    continent_pvs = {}
    for item in continents_sorted:
        qid  = item["qid"]
        gnid = item.get("gnid", "")
        lbl  = normalize_text(_lbl(item))
        add_permissible_value(
            continent_pvs, qid,
            title=lbl,
            meaning=f"wd:{qid}",
            description=normalize_text(item.get("desc", "")) or None,
        )
        sssom_rows.append({
            "subject_id":            f"wd:{qid}",
            "predicate_id":          "skos:broadMatch",
            "object_id":             "gno:A.CONT",
            "mapping_justification": "semapv:ManualMappingCuration",
            "subject_label":         lbl,
            "object_label":          "",
        })
        if gnid:
            sssom_rows.append({
                "subject_id":            f"gnf:{gnid}",
                "predicate_id":          "skos:broadMatch",
                "object_id":             "gno:A.CONT",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         lbl,
                "object_label":          "",
            })
        if gnid:
            sssom_rows.append({
                "subject_id":           f"wd:{qid}",
                "predicate_id":         "skos:exactMatch",
                "object_id":            f"gnf:{gnid}",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":        lbl,
                "object_label":         "",
            })

    # --- GeoWikiOcean enum (sorted alphabetically) ---
    oceans_sorted = sorted(oceans, key=lambda x: _title_sort_key(_lbl(x)))
    ocean_pvs = {}
    for item in oceans_sorted:
        qid  = item["qid"]
        gnid = item.get("gnid", "")
        lbl  = normalize_text(_lbl(item))
        add_permissible_value(
            ocean_pvs, qid,
            title=lbl,
            meaning=f"wd:{qid}",
            description=normalize_text(item.get("desc", "")) or None,
        )
        sssom_rows.append({
            "subject_id":            f"wd:{qid}",
            "predicate_id":          "skos:broadMatch",
            "object_id":             "gno:H.OCN",
            "mapping_justification": "semapv:ManualMappingCuration",
            "subject_label":         lbl,
            "object_label":          "",
        })
        if gnid:
            sssom_rows.append({
                "subject_id":            f"gnf:{gnid}",
                "predicate_id":          "skos:broadMatch",
                "object_id":             "gno:H.OCN",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         lbl,
                "object_label":          "",
            })
        if gnid:
            sssom_rows.append({
                "subject_id":           f"wd:{qid}",
                "predicate_id":         "skos:exactMatch",
                "object_id":            f"gnf:{gnid}",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":        lbl,
                "object_label":         "",
            })

    n_sov  = len(sovereign_states)
    n_ter  = len(territories)
    n_all  = n_sov + n_ter
    n_gn   = sum(1 for c in countries if c.get("gnid"))
    pct    = 100 * n_gn // n_all if n_all else 0
    print(f"  Crosswalk: {n_gn}/{n_all} entries ({pct}%) have GeoNames P1566 links")

    source_url = (source.get("reachable_from") or {}).get("source_ontology",
                                                           "https://www.wikidata.org/")
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or "Wikidata Countries, Continents, and Oceans",
        description=(
            f"{n_sov} sovereign states + {n_ter} territories, "
            f"{len(continents)} continents, and {len(oceans)} oceans from Wikidata "
            "(historical entities excluded via P576 filter). "
            "GeoNames P1566 and ISO alpha-2 cross-references included."
        ),
        license="CC0 1.0",
        prefixes=_SOURCE_PREFIXES,
        enums={
            "GeoWikiCountry": {
                "name":        "GeoWikiCountry",
                "title":       "Country",
                "description": (
                    f"{n_sov} Wikidata sovereign states (P31=Q3624078 or Q15634554), "
                    "grouped by continent. Code = QID; exact_mappings include "
                    "GeoNames URI and ISO alpha-2."
                ),
                "permissible_values": country_pvs,
            },
            "GeoWikiCountryExtended": {
                "name":        "GeoWikiCountryExtended",
                "title":       "Country Territory",
                "inherits":    "GeoWikiCountry",
                "description": (
                    f"{n_ter} dependent territories, overseas regions, and special "
                    "administrative areas that have an ISO 3166-1 alpha-2 code but "
                    "are not sovereign states (P31 ≠ Q3624078/Q15634554). "
                    "Inherits GeoWikiCountry so the complete list of sovereign states "
                    "plus all territories is available in one enum; territory is_a "
                    "links resolve to inherited sovereign state entries."
                ),
                "permissible_values": extended_pvs,
            },
            "GeoWikiContinent": {
                "name":        "GeoWikiContinent",
                "title":       "Continent",
                "description": "Wikidata continents (P31=Q5107), sorted alphabetically.",
                "permissible_values": continent_pvs,
            },
            "GeoWikiOcean": {
                "name":        "GeoWikiOcean",
                "title":       "Ocean",
                "description": "Wikidata oceans and major water bodies (P31=Q9430).",
                "permissible_values": ocean_pvs,
            },
        },
    )

    # Multilingual extensions
    for lang in locales:
        if lang == "en":
            continue
        locale_enums = {}
        for items_list, enum_key in [
            (sovereign_states, "GeoWikiCountry"),
            (territories,      "GeoWikiCountryExtended"),
            (continents_sorted, "GeoWikiContinent"),
            (oceans_sorted,     "GeoWikiOcean"),
        ]:
            lang_pvs = {
                item["qid"]: {"title": normalize_text(item["labels"][lang])}
                for item in items_list
                if item.get("labels", {}).get(lang)
            }
            if lang_pvs:
                locale_enums[enum_key] = {"permissible_values": lang_pvs}
        if locale_enums:
            ext = _make_locale_extensions(
                source_url, key, source.get("version") or "", lang,
                enums=locale_enums,
            )
            existing = schema.get("extensions", {}).get("locales", {}).get("value", {})
            existing.update(ext.get("locales", {}).get("value", {}))
            schema["extensions"] = {"locales": {"tag": "locales", "value": existing}}

    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    log_extraction("GeoWikiCountry",         count=len(country_pvs))
    log_extraction("GeoWikiCountryExtended", count=len(extended_pvs))
    log_extraction("GeoWikiContinent",       count=len(continent_pvs))
    log_extraction("GeoWikiOcean",           count=len(ocean_pvs))
    if sssom_rows:
        write_sssom_mappings(
            _GEOWIKI_SSSOM, sssom_rows, _SSSOM_PREFIXES,
            _GEOWIKI_SSSOM_ID, _GEOWIKI_SSSOM_DESC,
        )
        print(f"  SSSOM: {len(sssom_rows)} mappings written to {_GEOWIKI_SSSOM}")


def _pluralize(label):
    """Simple English pluralization for type labels (e.g. 'state' → 'states')."""
    if not label:
        return label
    if label.endswith("y") and len(label) > 1 and label[-2].lower() not in "aeiou":
        return label[:-1] + "ies"
    if label.endswith(("s", "sh", "ch", "x", "z")):
        return label + "es"
    return label + "s"


def _build_adm1_yaml(key, source, data, locales=None):
    """Write sources/{key}.yaml for GeoWikiADM1 scope.

    Per-country: groups ADM1 entries by their P31 type label (e.g. "province of
    Canada", "territory of Canada").  When 2+ distinct types each have 2+ members,
    type-category headers are inserted as enumeration roots (no is_a); individual
    divisions are sorted alphabetically beneath them.  When only one type is present
    the list is flat and sorted.  The country item itself is never included — the
    type category (or flat list) is the enumeration root.

    Global: flat listing of all ADM1 divisions (QLever recommended).
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]

    scope        = data.get("scope", "global")
    entries      = data.get("entries", [])
    country_qid  = data.get("country_qid", "")
    country_name = data.get("country_name", scope if scope != "global" else "")

    def _lbl(entry):
        return (entry.get("labels", {}).get(primary_lang)
                or entry.get("labels", {}).get("en")
                or entry.get("qid", ""))

    sssom_rows = []
    pvs = {}

    def _adm1_sssom(qid, gnid, lbl):
        if gnid:
            sssom_rows.append({
                "subject_id":           f"wd:{qid}",
                "predicate_id":         "skos:exactMatch",
                "object_id":            f"gnf:{gnid}",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":        lbl,
                "object_label":         "",
            })

    if scope != "global":
        # Group by P31 type label (must have 2+ members to get its own header)
        type_counter: Counter = Counter()
        for e in entries:
            tl = e.get("type_label", "")
            if tl:
                type_counter[tl] += 1

        _MIN_GROUP = 2
        active_types = {lbl: cnt for lbl, cnt in type_counter.items() if cnt >= _MIN_GROUP}
        has_groups   = len(active_types) >= 2

        # Most-common-first ordering
        type_order = sorted(active_types, key=lambda l: (-active_types[l], l))

        # Derive enum title from type labels + country name
        type_words = []
        for tl in type_order:
            word = tl.split(" of ")[0].strip() if " of " in tl else tl
            type_words.append((word, active_types[tl]))
        enum_title = _adm1_enum_title(type_words, country_name)

        if has_groups:
            # Add each type header immediately followed by its sorted children
            for tl in type_order:
                type_word = tl.split(" of ")[0].strip() if " of " in tl else tl
                code  = tl[0].upper() + tl[1:]   # e.g. "Province of Canada"
                title = type_word.capitalize() + (f" of {country_name}"
                                                   if country_name else "")
                hdr = {"title": title}
                if code in _ADM1_TYPE_HEADER_DESCRIPTIONS:
                    hdr["description"] = _ADM1_TYPE_HEADER_DESCRIPTIONS[code]
                pvs[code] = hdr

                group = [e for e in entries if e.get("type_label") == tl]
                for entry in sorted(group, key=lambda e: _title_sort_key(_lbl(e))):
                    qid  = entry["qid"]
                    gnid = entry.get("gnid", "")
                    lbl  = normalize_text(_lbl(entry))
                    add_permissible_value(
                        pvs, qid,
                        title=lbl,
                        meaning=f"wd:{qid}",
                        description=normalize_text(entry.get("desc", "")) or None,
                        is_a=code,
                    )
                    sssom_rows.append({
                        "subject_id":            f"wd:{qid}",
                        "predicate_id":          "skos:broadMatch",
                        "object_id":             "gno:A.ADM1",
                        "mapping_justification": "semapv:ManualMappingCuration",
                        "subject_label":         lbl,
                        "object_label":          "",
                    })
                    if gnid:
                        sssom_rows.append({
                            "subject_id":            f"gnf:{gnid}",
                            "predicate_id":          "skos:broadMatch",
                            "object_id":             "gno:A.ADM1",
                            "mapping_justification": "semapv:ManualMappingCuration",
                            "subject_label":         lbl,
                            "object_label":          "",
                        })
                    _adm1_sssom(qid, gnid, lbl)

            # Entries with no type label or a rare type: append sorted
            ungrouped = [e for e in entries
                         if e.get("type_label", "") not in active_types]
            for entry in sorted(ungrouped, key=lambda e: _title_sort_key(_lbl(e))):
                qid  = entry["qid"]
                gnid = entry.get("gnid", "")
                lbl  = normalize_text(_lbl(entry))
                add_permissible_value(
                    pvs, qid,
                    title=lbl,
                    meaning=f"wd:{qid}",
                    description=normalize_text(entry.get("desc", "")) or None,
                )
                sssom_rows.append({
                    "subject_id":            f"wd:{qid}",
                    "predicate_id":          "skos:broadMatch",
                    "object_id":             "gno:A.ADM1",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":         lbl,
                    "object_label":          "",
                })
                if gnid:
                    sssom_rows.append({
                        "subject_id":            f"gnf:{gnid}",
                        "predicate_id":          "skos:broadMatch",
                        "object_id":             "gno:A.ADM1",
                        "mapping_justification": "semapv:ManualMappingCuration",
                        "subject_label":         lbl,
                        "object_label":          "",
                    })
                _adm1_sssom(qid, gnid, lbl)
        else:
            # Single type or no type data: flat sorted list
            for entry in sorted(entries, key=lambda e: _title_sort_key(_lbl(e))):
                qid  = entry["qid"]
                gnid = entry.get("gnid", "")
                lbl  = normalize_text(_lbl(entry))
                add_permissible_value(
                    pvs, qid,
                    title=lbl,
                    meaning=f"wd:{qid}",
                    description=normalize_text(entry.get("desc", "")) or None,
                )
                sssom_rows.append({
                    "subject_id":            f"wd:{qid}",
                    "predicate_id":          "skos:broadMatch",
                    "object_id":             "gno:A.ADM1",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":         lbl,
                    "object_label":          "",
                })
                if gnid:
                    sssom_rows.append({
                        "subject_id":            f"gnf:{gnid}",
                        "predicate_id":          "skos:broadMatch",
                        "object_id":             "gno:A.ADM1",
                        "mapping_justification": "semapv:ManualMappingCuration",
                        "subject_label":         lbl,
                        "object_label":          "",
                    })
                _adm1_sssom(qid, gnid, lbl)
    else:
        # Global scope: flat listing
        enum_title = "Province or State"
        for entry in entries:
            qid  = entry["qid"]
            gnid = entry.get("gnid", "")
            lbl  = normalize_text(_lbl(entry))
            add_permissible_value(
                pvs, qid,
                title=lbl,
                meaning=f"wd:{qid}",
                description=normalize_text(entry.get("desc", "")) or None,
            )
            sssom_rows.append({
                "subject_id":            f"wd:{qid}",
                "predicate_id":          "skos:broadMatch",
                "object_id":             "gno:A.ADM1",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         lbl,
                "object_label":          "",
            })
            if gnid:
                sssom_rows.append({
                    "subject_id":            f"gnf:{gnid}",
                    "predicate_id":          "skos:broadMatch",
                    "object_id":             "gno:A.ADM1",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":         lbl,
                    "object_label":          "",
                })
            _adm1_sssom(qid, gnid, lbl)

    n       = len(entries)
    n_match = sum(1 for e in entries if e.get("gnid"))
    pct     = 100 * n_match // n if n else 0
    print(f"  Crosswalk: {n_match}/{n} ADM1 entries ({pct}%) have GeoNames P1566 links")

    scope_label = f"for {scope}" if scope != "global" else "(global)"
    enum_key    = f"GeoWikiADM1_{scope}" if scope != "global" else "GeoWikiADM1"
    source_url  = (source.get("reachable_from") or {}).get("source_ontology",
                                                             "https://www.wikidata.org/")
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"Wikidata ADM1 {scope_label}",
        description=f"{n} first-order administrative divisions {scope_label} from Wikidata.",
        license="CC0 1.0",
        prefixes=_SOURCE_PREFIXES,
        enums={
            enum_key: {
                "name":        enum_key,
                "title":       enum_title,
                "permissible_values": pvs,
            },
        },
    )
    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    log_extraction(enum_key, count=len(pvs))
    if sssom_rows:
        write_sssom_mappings(
            _GEOWIKI_SSSOM, sssom_rows, _SSSOM_PREFIXES,
            _GEOWIKI_SSSOM_ID, _GEOWIKI_SSSOM_DESC,
        )
        print(f"  SSSOM: {len(sssom_rows)} mappings written to {_GEOWIKI_SSSOM}")


def _build_ppl_yaml(key, source, data, locales=None):
    """Write sources/{key}.yaml for GeoWikiSettlement scope (per-country only).

    Produces a flat enum sorted alphabetically by name.
    All non-current settlements are marked status: historical — both archaic P31
    types (ghost town, former settlement, etc.) and items with P576
    (dissolved/abolished date).  All can be suppressed at build time with
    minus: {status: [historical]}.  The default config entry includes this so
    historical settlements are excluded from picklists unless the user opts in.
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]

    scope   = data.get("scope", "")
    entries = data.get("entries", [])

    def _lbl(item):
        return (item.get("labels", {}).get(primary_lang)
                or item.get("labels", {}).get("en")
                or item.get("qid", ""))

    sorted_entries = sorted(entries, key=lambda x: _title_sort_key(_lbl(x)))

    # Collect which stype headers are actually needed (in priority order)
    used_stypes = [
        st for st in _STYPE_PRIORITY
        if any(e.get("stype") == st for e in entries)
    ]

    sssom_rows = []
    pvs = {}
    # Emit size-category headers first (in priority order, no meaning/is_a)
    for st in used_stypes:
        label = _STYPE_LABELS.get(st, st)
        pvs[label] = {"title": label}

    for entry in sorted_entries:
        qid  = entry["qid"]
        gnid = entry.get("gnid", "")
        lbl  = normalize_text(_lbl(entry))
        stype_label = _STYPE_LABELS.get(entry.get("stype", ""), None)

        pv = add_permissible_value(
            pvs, qid,
            title=lbl,
            meaning=f"wd:{qid}",
            description=normalize_text(entry.get("desc", "")) or None,
            is_a=stype_label,
        )
        if entry.get("is_archaic") or entry.get("is_dissolved"):
            pv["status"] = "historical"
        if gnid:
            sssom_rows.append({
                "subject_id":           f"wd:{qid}",
                "predicate_id":         "skos:exactMatch",
                "object_id":            f"gnf:{gnid}",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":        lbl,
                "object_label":         "",
            })
        stype = entry.get("stype", "")
        if stype in {"Q5084", "Q532", "Q129676371"}:
            sssom_rows.append({
                "subject_id":           f"wd:{qid}",
                "predicate_id":         "skos:broader",
                "object_id":            f"wd:{stype}",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":        lbl,
                "object_label":         _STYPE_LABELS.get(stype, ""),
            })

    n          = len(entries)
    n_archaic  = sum(1 for e in entries if e.get("is_archaic"))
    n_dissolved = sum(1 for e in entries if e.get("is_dissolved") and not e.get("is_archaic"))
    n_hist     = n_archaic + n_dissolved
    n_current  = n - n_hist
    n_gn       = sum(1 for e in entries if e.get("gnid"))
    pct        = 100 * n_gn // n if n else 0
    print(f"  Settlement: {n_current} current + {n_dissolved} dissolved + {n_archaic} archaic = {n_hist} historical entries")
    print(f"  Crosswalk: {n_gn}/{n} entries ({pct}%) have GeoNames P1566 links")

    source_url = (source.get("reachable_from") or {}).get("source_ontology",
                                                           "https://www.wikidata.org/")
    enum_key = f"GeoWikiSettlement_{scope}"
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"Wikidata Settlements for {scope}",
        description=(
            f"{n_current} current + {n_hist} historical ({n_dissolved} dissolved, {n_archaic} archaic) "
            f"settlements for {scope} from Wikidata."
        ),
        license="CC0 1.0",
        prefixes=_SOURCE_PREFIXES,
        enums={
            enum_key: {
                "name":        enum_key,
                "title":       "Town or City",
                "permissible_values": pvs,
            },
        },
    )

    # Multilingual locale extensions
    for lang in locales:
        if lang == "en":
            continue
        lang_pvs = {
            e["qid"]: {"title": normalize_text(e["labels"][lang])}
            for e in sorted_entries
            if e.get("labels", {}).get(lang)
        }
        if lang_pvs:
            ext = _make_locale_extensions(
                source_url, key, source.get("version") or "", lang,
                enums={enum_key: {"permissible_values": lang_pvs}},
            )
            existing = schema.get("extensions", {}).get("locales", {}).get("value", {})
            existing.update(ext.get("locales", {}).get("value", {}))
            schema["extensions"] = {"locales": {"tag": "locales", "value": existing}}

    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    log_extraction(enum_key, count=len(pvs))
    if sssom_rows:
        write_sssom_mappings(
            _GEOWIKI_SSSOM, sssom_rows, _SSSOM_PREFIXES,
            _GEOWIKI_SSSOM_ID, _GEOWIKI_SSSOM_DESC,
        )
        print(f"  SSSOM: {len(sssom_rows)} mappings written to {_GEOWIKI_SSSOM}")


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _get_endpoint(source):
    if _qlever_enabled(source):
        return _QLEVER_ENDPOINT, True
    return _WDQS_ENDPOINT, False


def _fetch_countries_payload(locales, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Query Wikidata for sovereign states, continents, and oceans.

    GeoNames countryInfo.txt is used as the authoritative continent source:
    for every country in GeoNames, the continent_qid is set from the GeoNames
    continent code (mapped to one of the 7 canonical Wikidata continent QIDs).
    Wikidata P30 can give sub-regional values (e.g. Q538 Melanesia instead of
    Q55643 Oceania), so GeoNames overrides it for entries it covers.  Countries
    not in GeoNames (e.g. Ascension, Sark, Tristan da Cunha) are left with an
    empty continent_qid, which _build_countries_yaml treats as sub-national
    entities and excludes from GeoWikiCountry.
    """
    countries  = _fetch_wikidata_countries(locales, endpoint, use_post)
    continents = _fetch_wikidata_continents(locales, endpoint, use_post)
    oceans     = _fetch_wikidata_oceans(locales, endpoint, use_post)

    # Apply GeoNames countryInfo.txt as authoritative continent assignment
    # (overrides Wikidata P30 sub-regions; fills gaps for P30-less entries)
    try:
        print("  Fetching GeoNames countryInfo.txt for authoritative continent assignment ...")
        req = urllib.request.Request(_GEONAMES_COUNTRY_URL, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
        alpha2_to_cont_code = {}
        for line in raw.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            cols = line.split("\t")
            if len(cols) >= 9:
                alpha2_to_cont_code[cols[0].strip()] = cols[8].strip()
        filled = overridden = 0
        for c in countries:
            gn_cont_code = alpha2_to_cont_code.get(c.get("alpha2", ""), "")
            gn_qid = _GN_CONT_CODE_TO_WD_QID.get(gn_cont_code, "")
            if not gn_qid:
                continue  # not in GeoNames — leave continent_qid as-is (usually "")
            if not c.get("continent_qid"):
                c["continent_qid"] = gn_qid
                filled += 1
            elif c["continent_qid"] != gn_qid:
                c["continent_qid"] = gn_qid
                overridden += 1
        msg_parts = []
        if filled:
            msg_parts.append(f"filled {filled}")
        if overridden:
            msg_parts.append(f"normalised {overridden} sub-regional P30 values")
        if msg_parts:
            print(f"    GeoNames continent: {'; '.join(msg_parts)}.")
    except Exception as exc:
        print(f"  Warning: GeoNames continent assignment failed: {exc}", file=sys.stderr)

    return {
        "type":       "countries",
        "countries":  countries,
        "continents": continents,
        "oceans":     oceans,
    }


def _fetch_ppl_payload(alpha2, locales, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Query Wikidata settlements for a single country identified by ISO alpha-2."""
    entries     = _fetch_wikidata_settlements(alpha2, locales, endpoint, use_post)
    country_qid = entries[0]["country_qid"] if entries else ""
    return {
        "type":        "ppl",
        "scope":       alpha2.upper() if alpha2 else "",
        "entries":     entries,
        "country_qid": country_qid,
    }


def _fetch_geonames_adm2_rows(alpha2):
    """Fetch GeoNames admin2Codes.txt and return rows for the given country.

    Returns list of dicts: gnid, name, compound_code (e.g. "CA.01.4801003").
    """
    print(f"  Fetching GeoNames admin2Codes.txt for {alpha2} ...")
    req = urllib.request.Request(_GEONAMES_ADM2_URL, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
    prefix = alpha2.upper() + "."
    rows = []
    for line in raw.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        compound = parts[0].strip()
        if not compound.startswith(prefix):
            continue
        rows.append({
            "compound_code": compound,
            "name":          parts[1].strip(),
            "gnid":          parts[3].strip(),
        })
    print(f"    Found {len(rows)} GeoNames ADM2 rows for {alpha2}.")
    return rows


def _enrich_adm2_with_wikidata(gn_rows, locales=None, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Batch-query Wikidata for ADM2 entities via P1566 (GeoNames ID) reverse-lookup.

    Takes the GeoNames ADM2 rows as the authoritative list (ensures we get exactly
    the county-level entities that GeoNames recognises, not all municipalities).
    Returns a dict: gnid → {qid, labels, desc, adm1_qid}.
    """
    locales = locales or ["en"]
    label_vars      = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
        f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )

    gnids = [r["gnid"] for r in gn_rows if r.get("gnid")]
    if not gnids:
        return {}

    print(f"  Querying Wikidata P1566 reverse-lookup for {len(gnids)} ADM2 gnids ...")
    wd_by_gnid = {}
    batch_size = 200

    for i in range(0, len(gnids), batch_size):
        batch = gnids[i : i + batch_size]
        values = " ".join(f'"{g}"' for g in batch)
        sparql = f"""
SELECT ?item ?gnid {label_vars} ?desc ?adm1 WHERE {{
  VALUES ?gnid {{ {values} }}
  ?item wdt:P1566 ?gnid .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
  OPTIONAL {{ ?item wdt:P131 ?adm1 . }}
}}
"""
        try:
            rows = _sparql_query(sparql, timeout=60, endpoint=endpoint, use_post=use_post)
        except Exception as exc:
            print(f"  Warning: Wikidata batch {i//batch_size + 1} failed: {exc}", file=sys.stderr)
            continue

        seen = set()
        for row in rows:
            gnid = row.get("gnid", {}).get("value", "")
            if not gnid or gnid in seen:
                continue
            seen.add(gnid)
            qid      = _qid_from_uri(row.get("item", {}).get("value", ""))
            adm1_uri = row.get("adm1", {}).get("value", "")
            adm1_qid = _qid_from_uri(adm1_uri) if adm1_uri else ""
            labels = {
                lang: row[f"label_{lang}"]["value"]
                for lang in locales if row.get(f"label_{lang}", {}).get("value")
            }
            desc = row.get("desc", {}).get("value", "")
            wd_by_gnid[gnid] = {"qid": qid, "labels": labels, "desc": desc,
                                 "adm1_qid": adm1_qid}

    n_matched = sum(1 for v in wd_by_gnid.values() if v.get("qid"))
    print(f"    Wikidata P1566 crosswalk: {n_matched}/{len(gnids)} ADM2 entries matched.")
    return wd_by_gnid


def _fetch_adm1_payload(alpha2, locales, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Query Wikidata ADM1 for a single country identified by ISO alpha-2."""
    entries = _fetch_wikidata_adm1_for_country(alpha2, locales, endpoint, use_post)

    country_qid  = entries[0]["country_qid"] if entries else ""
    country_name = ""
    if entries:
        # Attempt to get country name from a separate query
        try:
            rows = _sparql_query(
                f'SELECT ?label WHERE {{ wd:{country_qid} rdfs:label ?label . '
                f'FILTER(LANG(?label) = "en") }} LIMIT 1',
                timeout=10, endpoint=endpoint, use_post=use_post
            )
            if rows:
                country_name = rows[0].get("label", {}).get("value", "")
        except Exception:
            pass

    return {
        "type":         "adm1",
        "scope":        alpha2.upper() if alpha2 else "global",
        "entries":      entries,
        "country_qid":  country_qid,
        "country_name": country_name,
    }


def _fetch_adm2_payload(alpha2, locales, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Build ADM2 payload for a single country.

    Uses GeoNames admin2Codes.txt as the authoritative county-level list, then
    enriches with Wikidata via P1566 (GeoNames ID) reverse-lookup.  This avoids
    the intractable problem of distinguishing counties from municipalities using
    Wikidata class hierarchies alone.

    Output entries have: gnid, name, compound_code, qid (if P1566 matched),
    labels, desc, adm1_qid (from Wikidata P131).
    """
    # Fetch GeoNames ADM2 rows (authoritative list)
    try:
        gn_rows = _fetch_geonames_adm2_rows(alpha2)
    except Exception as exc:
        print(f"  Error fetching GeoNames ADM2 for {alpha2}: {exc}", file=sys.stderr)
        gn_rows = []

    if not gn_rows:
        return {"type": "adm2", "scope": alpha2.upper(), "entries": [],
                "adm1_entries": [], "country_qid": "", "country_name": ""}

    # Enrich with Wikidata via P1566
    wd_by_gnid = _enrich_adm2_with_wikidata(gn_rows, locales, endpoint, use_post)

    # Resolve country QID from Wikidata
    country_qid  = ""
    country_name = ""
    try:
        country_sparql = f'''
SELECT ?item ?label WHERE {{
  ?item wdt:P297 "{alpha2.upper()}" .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
  ?item rdfs:label ?label .
  FILTER(LANG(?label) = "en")
}} LIMIT 1
'''
        rows = _sparql_query(country_sparql, timeout=15, endpoint=endpoint, use_post=use_post)
        if rows:
            country_qid  = _qid_from_uri(rows[0].get("item", {}).get("value", ""))
            country_name = rows[0].get("label", {}).get("value", "")
    except Exception as exc:
        print(f"  Warning: could not resolve country QID for {alpha2}: {exc}", file=sys.stderr)

    # Build unified entries list
    entries = []
    for gn in gn_rows:
        gnid = gn["gnid"]
        wd   = wd_by_gnid.get(gnid, {})
        entries.append({
            "gnid":         gnid,
            "name":         gn["name"],
            "compound_code": gn["compound_code"],
            "qid":          wd.get("qid", ""),
            "labels":       wd.get("labels", {}),
            "desc":         wd.get("desc", ""),
            "adm1_qid":     wd.get("adm1_qid", ""),
            "country_qid":  country_qid,
        })

    # Load cached ADM1 payload for province hierarchy embedding
    adm1_key     = f"GeoWiki_{alpha2.upper()}_ADM1"
    adm1_payload = None
    if _entry_exists(adm1_key):
        try:
            adm1_payload = _load_json(adm1_key)
            print(f"  Loaded ADM1 data from cached {adm1_key} for province hierarchy.")
        except Exception:
            pass

    if adm1_payload:
        saved_adm1_entries = adm1_payload.get("entries", [])
        saved_country_name = adm1_payload.get("country_name", country_name)
    else:
        saved_adm1_entries = []
        saved_country_name = country_name
        print(f"  Note: no cached {adm1_key} — province hierarchy will be flat.",
              file=sys.stderr)

    return {
        "type":         "adm2",
        "scope":        alpha2.upper(),
        "entries":      entries,
        "adm1_entries": saved_adm1_entries,
        "country_name": saved_country_name,
        "country_qid":  country_qid,
    }


def _build_adm2_yaml(key, source, data, locales=None):
    """Write sources/{key}.yaml for GeoWikiADM2 scope (per-country only).

    Produces a 3-level hierarchy that reuses the GeoWikiADM1 structure:
      Province/Territory type headers (no is_a)
        Province entries (QID as PV code, matching GeoWikiADM1 PV codes)
          ADM2 entries (is_a = parent ADM1 QID)

    ADM1 data comes from the cached GeoWiki_{CC}_ADM1.zip payload; if absent,
    the province section is flat without type grouping.
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]

    scope        = data.get("scope", "")
    entries      = data.get("entries", [])
    adm1_entries = data.get("adm1_entries", [])
    country_name = data.get("country_name", scope)

    def _lbl(entry):
        return (entry.get("labels", {}).get(primary_lang)
                or entry.get("labels", {}).get("en")
                or entry.get("name", "")
                or entry.get("qid", ""))

    # If adm1_entries absent (old cached payload), try loading from shared zip
    if not adm1_entries and scope:
        adm1_key = f"GeoWiki_{scope}_ADM1"
        if _entry_exists(adm1_key):
            try:
                adm1_cache   = _load_json(adm1_key)
                adm1_entries = adm1_cache.get("entries", [])
                country_name = adm1_cache.get("country_name", country_name)
            except Exception:
                pass

    # Build ADM1 province/territory hierarchy (same grouping logic as _build_adm1_yaml)
    type_counter: Counter = Counter()
    for e in adm1_entries:
        tl = e.get("type_label", "")
        if tl:
            type_counter[tl] += 1

    _MIN_GROUP  = 2
    active_types = {lbl: cnt for lbl, cnt in type_counter.items() if cnt >= _MIN_GROUP}
    has_groups   = len(active_types) >= 2
    type_order   = sorted(active_types, key=lambda l: (-active_types[l], l))

    sssom_rows = []

    def _adm1_sssom_embed(qid, gnid, lbl):
        if gnid:
            sssom_rows.append({
                "subject_id":           f"wd:{qid}",
                "predicate_id":         "skos:exactMatch",
                "object_id":            f"gnf:{gnid}",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":        lbl,
                "object_label":         "",
            })

    # Build ADM1 pvs (province headers + province entries keyed by QID)
    adm1_pvs = {}
    if has_groups:
        for tl in type_order:
            type_word = tl.split(" of ")[0].strip() if " of " in tl else tl
            code  = tl[0].upper() + tl[1:]
            title = type_word.capitalize() + (f" of {country_name}" if country_name else "")
            adm1_pvs[code] = {"title": title}
            group = [e for e in adm1_entries if e.get("type_label") == tl]
            for entry in sorted(group, key=lambda e: _title_sort_key(_lbl(e))):
                qid  = entry["qid"]
                gnid = entry.get("gnid", "")
                lbl  = normalize_text(_lbl(entry))
                pv = add_permissible_value(
                    adm1_pvs, qid,
                    title=lbl,
                    meaning=f"wd:{qid}",
                    description=normalize_text(entry.get("desc", "")) or None,
                    is_a=code,
                )
                pv.pop("aliases", None)   # no aliases needed in embedded section
                sssom_rows.append({
                    "subject_id":            f"wd:{qid}",
                    "predicate_id":          "skos:broadMatch",
                    "object_id":             "gno:A.ADM1",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":         lbl,
                    "object_label":          "",
                })
                if gnid:
                    sssom_rows.append({
                        "subject_id":            f"gnf:{gnid}",
                        "predicate_id":          "skos:broadMatch",
                        "object_id":             "gno:A.ADM1",
                        "mapping_justification": "semapv:ManualMappingCuration",
                        "subject_label":         lbl,
                        "object_label":          "",
                    })
                _adm1_sssom_embed(qid, gnid, lbl)
        ungrouped = [e for e in adm1_entries
                     if e.get("type_label", "") not in active_types]
        for entry in sorted(ungrouped, key=lambda e: _title_sort_key(_lbl(e))):
            qid  = entry["qid"]
            gnid = entry.get("gnid", "")
            lbl  = normalize_text(_lbl(entry))
            add_permissible_value(
                adm1_pvs, qid,
                title=lbl,
                meaning=f"wd:{qid}",
                description=normalize_text(entry.get("desc", "")) or None,
            )
            sssom_rows.append({
                "subject_id":            f"wd:{qid}",
                "predicate_id":          "skos:broadMatch",
                "object_id":             "gno:A.ADM1",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         lbl,
                "object_label":          "",
            })
            if gnid:
                sssom_rows.append({
                    "subject_id":            f"gnf:{gnid}",
                    "predicate_id":          "skos:broadMatch",
                    "object_id":             "gno:A.ADM1",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":         lbl,
                    "object_label":          "",
                })
            _adm1_sssom_embed(qid, gnid, lbl)
    else:
        for entry in sorted(adm1_entries, key=lambda e: _title_sort_key(_lbl(e))):
            qid  = entry["qid"]
            gnid = entry.get("gnid", "")
            lbl  = normalize_text(_lbl(entry))
            add_permissible_value(
                adm1_pvs, qid,
                title=lbl,
                meaning=f"wd:{qid}",
                description=normalize_text(entry.get("desc", "")) or None,
            )
            sssom_rows.append({
                "subject_id":            f"wd:{qid}",
                "predicate_id":          "skos:broadMatch",
                "object_id":             "gno:A.ADM1",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         lbl,
                "object_label":          "",
            })
            if gnid:
                sssom_rows.append({
                    "subject_id":            f"gnf:{gnid}",
                    "predicate_id":          "skos:broadMatch",
                    "object_id":             "gno:A.ADM1",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":         lbl,
                    "object_label":          "",
                })
            _adm1_sssom_embed(qid, gnid, lbl)

    # Group ADM2 entries by parent ADM1 QID
    adm2_by_adm1 = defaultdict(list)
    adm2_orphans = []
    for e in entries:
        adm1_qid = e.get("adm1_qid", "")
        qid       = e.get("qid", "")
        # Resolve empty adm1_qid via direct ADM2→ADM1 override
        if not adm1_qid and qid in _ADM2_NO_PARENT_OVERRIDES:
            adm1_qid = _ADM2_NO_PARENT_OVERRIDES[qid]
        # Resolve intermediate parent (one P131 hop too deep)
        if adm1_qid and adm1_qid not in adm1_pvs and adm1_qid in _ADM2_INTERMEDIATE_PARENT_QIDS:
            adm1_qid = _ADM2_INTERMEDIATE_PARENT_QIDS[adm1_qid]
        if adm1_qid and adm1_qid in adm1_pvs:
            adm2_by_adm1[adm1_qid].append(e)
        else:
            adm2_orphans.append(e)

    def _adm2_pv(pvs, entry, is_a=None):
        """Add one ADM2 entry to pvs; fall back to GeoNames ID when QID is missing.

        If the resolved code does not start with 'Q' (i.e. no Wikidata QID was
        found via P1566 reverse-lookup), consult _GNID_QID_OVERRIDES before
        accepting the gnid as the final code.
        """
        qid  = entry.get("qid", "")
        gnid = entry.get("gnid", "")
        # Apply manual override when QID is absent or not a Wikidata QID
        if (not qid or not qid.startswith("Q")) and gnid in _GNID_QID_OVERRIDES:
            qid = _GNID_QID_OVERRIDES[gnid]
        if qid:
            pv_code = qid
            meaning = f"wd:{qid}"
        elif gnid:
            pv_code = gnid
            meaning = f"gnf:{gnid}"
        else:
            return   # nothing to key on
        lbl = normalize_text(_lbl(entry))
        add_permissible_value(
            pvs, pv_code,
            title=lbl,
            meaning=meaning,
            description=normalize_text(entry.get("desc", "")) or None,
            is_a=is_a,
        )
        if qid:
            sssom_rows.append({
                "subject_id":            f"wd:{qid}",
                "predicate_id":          "skos:broadMatch",
                "object_id":             "gno:A.ADM2",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         lbl,
                "object_label":          "",
            })
        if gnid:
            sssom_rows.append({
                "subject_id":            f"gnf:{gnid}",
                "predicate_id":          "skos:broadMatch",
                "object_id":             "gno:A.ADM2",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         lbl,
                "object_label":          "",
            })
        if qid and gnid:
            sssom_rows.append({
                "subject_id":           f"wd:{qid}",
                "predicate_id":         "skos:exactMatch",
                "object_id":            f"gnf:{gnid}",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":        lbl,
                "object_label":         "",
            })

    # When ADM1 data is available, use inherits to bring province hierarchy into the
    # combined schema — only ADM2 entries go in pvs; province entries come via inherits.
    has_adm1 = bool(adm1_entries)

    # Interleave: each ADM1 entry immediately followed by its ADM2 children.
    # When has_adm1, skip copying adm1_pvs into pvs (they come via inherits).
    pvs = {}
    for code, pv_data in adm1_pvs.items():
        if not has_adm1:
            pvs[code] = pv_data   # embed province/type headers (fallback mode)
        if "meaning" in pv_data:      # province entry (not type-category header)
            for entry in sorted(adm2_by_adm1.get(code, []),
                                 key=lambda e: _title_sort_key(_lbl(e))):
                _adm2_pv(pvs, entry, is_a=code)

    # Orphaned ADM2 (parent ADM1 not in adm1_pvs): append sorted
    for entry in sorted(adm2_orphans, key=lambda e: _title_sort_key(_lbl(e))):
        _adm2_pv(pvs, entry)

    n       = len(entries)
    n_match = sum(1 for e in entries if e.get("qid"))
    pct     = 100 * n_match // n if n else 0
    if adm2_orphans:
        print(f"  Note: {len(adm2_orphans)} ADM2 entries had no matching ADM1 parent.")
    print(f"  Crosswalk: {n_match}/{n} ADM2 entries ({pct}%) have Wikidata QID links")

    type_words  = []
    for tl in type_order:
        word = tl.split(" of ")[0].strip() if " of " in tl else tl
        type_words.append((word, active_types.get(tl, 0)))
    enum_title  = _adm1_enum_title(type_words, country_name) if type_words else "District or County"
    # Tweak title for ADM2 context
    enum_title  = enum_title.replace("Province or Territory", "County or District")

    enum_key   = f"GeoWikiADM2_{scope}"
    source_url = (source.get("reachable_from") or {}).get("source_ontology",
                                                            "https://www.wikidata.org/")
    enum_dict  = {"name": enum_key, "title": enum_title}
    if has_adm1:
        enum_dict["inherits"] = f"GeoWikiADM1_{scope}"
    enum_dict["permissible_values"] = pvs
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"Wikidata ADM2 for {scope}",
        description=f"{n} second-order administrative divisions for {scope} from Wikidata.",
        license="CC0 1.0",
        prefixes=_SOURCE_PREFIXES,
        enums={enum_key: enum_dict},
    )
    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    log_extraction(enum_key, count=len(pvs))
    if sssom_rows:
        write_sssom_mappings(
            _GEOWIKI_SSSOM, sssom_rows, _SSSOM_PREFIXES,
            _GEOWIKI_SSSOM_ID, _GEOWIKI_SSSOM_DESC,
        )
        print(f"  SSSOM: {len(sssom_rows)} mappings written to {_GEOWIKI_SSSOM}")


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------

def _load_config(config_file):
    try:
        with open(config_file) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _register_countries(config_file):
    key    = "GeoWiki_countries"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales  = config.get("locales") or ["en"]
    use_qlvr = bool(config.get("apis", {}).get("qlever"))
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, False)

    print(f"  Querying Wikidata for countries, continents, oceans (locales: {locales}) ...")
    payload = _fetch_countries_payload(locales, endpoint, use_post)
    if not payload.get("countries"):
        print("  Error: no countries returned from Wikidata.", file=sys.stderr)
        return True

    _save_json(key, payload)

    entry = make_source_entry(
        key, "https://www.wikidata.org/wiki/Wikidata:Main_Page",
        "GeoWikiCountries", "zip",
        title="Wikidata Countries, Continents, and Oceans",
        description=(
            "Current sovereign states (P297), continents (P31=Q5107), and oceans "
            "(P31=Q9430) from Wikidata with GeoNames P1566 cross-references."
        ),
    )
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC0 1.0"
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_countries_yaml(key, config["sources"][key], payload, locales=locales)
    return True


def _register_adm1(alpha2, config_file):
    key    = f"GeoWiki_{alpha2}_ADM1" if alpha2 else "GeoWiki_ADM1"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales  = config.get("locales") or ["en"]
    use_qlvr = bool(config.get("apis", {}).get("qlever"))
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, False)

    if not alpha2:
        print("  Note: global GeoWiki ADM1 may timeout on WDQS — set apis.qlever: true for reliability.",
              file=sys.stderr)
        # Global ADM1: not yet implemented (needs QLever and batching by country)
        print("  Global GeoWiki ADM1 not yet implemented; use per-country shorthand "
              "(e.g. GeoWiki_US_ADM1).", file=sys.stderr)
        return True

    print(f"  Querying Wikidata ADM1 for {alpha2} ...")
    payload = _fetch_adm1_payload(alpha2, locales, endpoint, use_post)
    if not payload.get("entries"):
        print(f"  Error: no ADM1 entries returned for {alpha2}.", file=sys.stderr)
        return True

    _save_json(key, payload)

    entry = make_source_entry(
        key, f"https://www.wikidata.org/wiki/Q{payload.get('country_qid', '')}",
        "GeoWikiADM1", "zip",
        title=f"Wikidata ADM1 for {alpha2}",
        description=f"First-order administrative divisions of {alpha2} from Wikidata.",
    )
    entry["country"]  = alpha2.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC0 1.0"
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_adm1_yaml(key, config["sources"][key], payload, locales=locales)
    return True


def _register_adm2(alpha2, config_file):
    """Register GeoWikiADM2 for a single country identified by ISO alpha-2."""
    key    = f"GeoWiki_{alpha2}_ADM2"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales  = config.get("locales") or ["en"]
    use_qlvr = bool(config.get("apis", {}).get("qlever"))
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, False)

    print(f"  Querying Wikidata ADM2 for {alpha2} ...")
    payload = _fetch_adm2_payload(alpha2, locales, endpoint, use_post)
    if not payload.get("entries"):
        print(f"  Error: no ADM2 entries returned for {alpha2}.", file=sys.stderr)
        return True

    _save_json(key, payload)

    entry = make_source_entry(
        key, f"https://www.wikidata.org/wiki/Q{payload.get('country_qid', '')}",
        "GeoWikiADM2", "zip",
        title=f"Wikidata ADM2 for {alpha2}",
        description=f"Second-order administrative divisions of {alpha2} from Wikidata.",
    )
    entry["country"]  = alpha2.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC0 1.0"
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_adm2_yaml(key, config["sources"][key], payload, locales=locales)
    return True


def _register_ppl(alpha2, config_file):
    """Register GeoWiki populated places (settlements) for a single country."""
    key    = f"GeoWiki_{alpha2}_PPL"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales  = config.get("locales") or ["en"]
    use_qlvr = bool(config.get("apis", {}).get("qlever"))
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, False)

    print(f"  Querying Wikidata settlements for {alpha2} ...")
    if not use_qlvr:
        print(f"  Tip: set apis.qlever: true in config for better performance on large countries.",
              file=sys.stderr)
    payload = _fetch_ppl_payload(alpha2, locales, endpoint, use_post)
    if not payload.get("entries"):
        print(f"  Error: no settlement entries returned for {alpha2}.", file=sys.stderr)
        return True

    _save_json(key, payload)

    entry = make_source_entry(
        key, "https://www.wikidata.org/",
        "GeoWikiSettlement", "zip",
        title=f"Wikidata Settlements for {alpha2}",
        description=(
            f"Populated places for {alpha2} from Wikidata. "
            "All non-current settlements (archaic types and P576-dissolved) marked "
            "status: historical."
        ),
    )
    entry["country"]  = alpha2.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC0 1.0"
    # Default: suppress all historical settlements from picklists.
    # Remove this entry to include them.
    entry["minus"]    = {"status": ["historical"]}
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_ppl_yaml(key, config["sources"][key], payload, locales=locales)
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_geowiki_code(url, config_file=MENU_CONFIG):
    """Pre-download handler for GeoWiki_* shorthands.

    Matches:
      GeoWiki_countries      → register GeoWikiCountries
      GeoWiki_ADM1           → global GeoWikiADM1 (QLever recommended)
      GeoWiki_{CC}_ADM1      → per-country GeoWikiADM1
      GeoWiki_{CC}_ADM2      → per-country GeoWikiADM2

    Returns True if the URL was matched (regardless of success).
    """
    url = url.strip()
    m   = _SHORTHAND_RE.match(url)
    if not m:
        return False

    scope_global = m.group(1)   # "countries" or "ADM1" (or None)
    cc           = m.group(2)   # two-letter country code (or None)
    adm_level    = m.group(3)   # "ADM1"/"ADM2"/… (or None)

    if scope_global:
        sg = scope_global.lower()
        if sg == "countries":
            return _register_countries(config_file)
        if sg == "adm1":
            return _register_adm1(None, config_file)   # global stub
    elif cc and adm_level:
        level = adm_level.upper()
        if level == "ADM1":
            return _register_adm1(cc.upper(), config_file)
        if level == "PPL":
            return _register_ppl(cc.upper(), config_file)
        if level == "ADM2":
            return _register_adm2(cc.upper(), config_file)
        if level in ("ADM3", "ADM4"):
            print(f"  GeoWiki {level} not yet implemented.", file=sys.stderr)
            return True

    return False


def fetch_geowiki_source(key, source, config_file=MENU_CONFIG):
    """Re-download Wikidata data for *key*."""
    content_type    = source.get("content_type", "")
    locales         = _get_project_locales(config_file)
    endpoint, use_p = _get_endpoint(source)

    if content_type == "GeoWikiCountries":
        print(f"  Re-querying Wikidata for countries, continents, oceans ...")
        payload = _fetch_countries_payload(locales, endpoint, use_p)
        _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoWikiADM1":
        alpha2 = source.get("country", "")
        if not alpha2:
            print(f"  Error: GeoWikiADM1 source '{key}' has no country field.", file=sys.stderr)
            return
        print(f"  Re-querying Wikidata ADM1 for {alpha2} ...")
        payload = _fetch_adm1_payload(alpha2, locales, endpoint, use_p)
        _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoWikiADM2":
        alpha2 = source.get("country", "")
        if not alpha2:
            print(f"  Error: GeoWikiADM2 source '{key}' has no country field.", file=sys.stderr)
            return
        print(f"  Re-querying Wikidata ADM2 for {alpha2} ...")
        payload = _fetch_adm2_payload(alpha2, locales, endpoint, use_p)
        _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoWikiSettlement":
        alpha2 = source.get("country", "")
        if not alpha2:
            print(f"  Error: GeoWikiSettlement source '{key}' has no country field.",
                  file=sys.stderr)
            return
        print(f"  Re-querying Wikidata settlements for {alpha2} ...")
        payload = _fetch_ppl_payload(alpha2, locales, endpoint, use_p)
        _save_json(key, payload, config_file=config_file)

    else:
        print(f"  Warning: unknown GeoWiki content_type '{content_type}' for '{key}'",
              file=sys.stderr)


def process_geowiki_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Build sources/{key}.yaml from the cached GeoWiki zip for *key*."""
    if not _entry_exists(key):
        print(f"  Error: {key}.json not found in {_SHARED_ZIP} — run '-f {key}' first",
              file=sys.stderr)
        return

    data    = _load_json(key)
    locales = locales or _get_project_locales(config_file)
    dtype   = data.get("type", "")

    if dtype == "countries":
        _build_countries_yaml(key, source, data, locales=locales)
    elif dtype == "adm1":
        _build_adm1_yaml(key, source, data, locales=locales)
    elif dtype == "adm2":
        _build_adm2_yaml(key, source, data, locales=locales)
    elif dtype == "ppl":
        _build_ppl_yaml(key, source, data, locales=locales)
    else:
        print(f"  Warning: unknown data type '{dtype}' in {_SHARED_ZIP}", file=sys.stderr)
