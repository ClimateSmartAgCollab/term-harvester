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
GeoWikiADM34
    Third- and/or fourth-order administrative divisions.  GeoNames {CC}.zip
    provides the authoritative entity list; Wikidata QIDs are resolved via a
    two-stage enrichment: (1) P1566 reverse-lookup, (2) name + P131/P17 search
    for entries not matched in stage 1.  All permissible-value keys are Wikidata
    QIDs; entries with no QID found are omitted from the output YAML.  GeoNames
    numeric IDs and GeoNames → Wikidata cross-references are written to
    GeoWiki.sssom.tsv.  Per-country only.
    Inherits chain: GeoWikiAdm4{CC} → GeoWikiAdm3{CC} → GeoWikiAdm2{CC} →
    GeoWikiAdm1{CC}.  Each level automatically fetches its ancestors if absent
    (zip only, no config entry): fetching ADM4 also fetches ADM3, ADM2, and ADM1;
    fetching ADM3 also fetches ADM2 and ADM1; fetching ADM2 fetches ADM1.
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
GeoWiki_ADM1            → GeoWikiAdm1 (all ADM1; QLever recommended globally)
GeoWiki_ADM1_{CC}       → GeoWikiAdm1 single country (e.g. GeoWiki_ADM1_US)
GeoWiki_ADM2_{CC}       → GeoWikiAdm2 single country (e.g. GeoWiki_ADM2_CA)
GeoWiki_ADM3_{CC}       → GeoWikiAdm3 ADM3 only for one country
GeoWiki_ADM3ADM4_{CC}   → GeoWikiAdm3 + Adm4 for one country (e.g. GeoWiki_ADM3ADM4_CA)
GeoWiki_Settlement_{CC} → GeoWikiSettlement for one country (e.g. GeoWiki_Settlement_NL)

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
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
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
_QLEVER_ENDPOINT = "https://qlever.dev/api/wikidata"
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
_GEONAMES_CC_URL      = "https://download.geonames.org/export/dump/{CC}.zip"

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

# Wikidata query batch size for WDQS (unused — batch sizes are set per-function)
_WD_BATCH = 500

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

# Shorthand regex: GeoWiki_countries, GeoWiki_ADM1, GeoWiki_ADM1_US, GeoWiki_ADM2_CA, GeoWiki_Settlement_NL
_SHORTHAND_RE = re.compile(
    r'^GeoWiki_(?:(countries|ADM1)|(ADM3ADM4|ADM[1-4]|Settlement)_([A-Za-z]{2}))$',
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


def _best_label(item, locales, *fallback_keys):
    """Return the best available label for a Wikidata item dict.

    Tries each locale in *locales* order, then falls back through *fallback_keys*
    (field names on *item*).  Returns "" if nothing is found.
    """
    lbls = item.get("labels", {})
    for lang in locales:
        if lbls.get(lang):
            return lbls[lang]
    for key in fallback_keys:
        if item.get(key):
            return item[key]
    return ""


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

# Standard Wikidata prefix block required by QLever (WDQS has these built in)
_WIKIDATA_PREFIXES = """\
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX schema: <http://schema.org/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""

_SPARQL_RETRY_DELAYS = (15, 60, 120)   # seconds; transient HTTP codes only
_SPARQL_TRANSIENT_CODES = {429, 502, 503, 504}


def _sparql_query(sparql, timeout=60, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Execute a SPARQL query; return the bindings list.

    When endpoint is QLever, standard Wikidata PREFIX declarations are prepended
    automatically (QLever requires explicit prefixes; WDQS has them built in).

    Retries up to 3 times with increasing delays on transient HTTP errors
    (429 Too Many Requests, 502/503/504 gateway errors).
    """
    if endpoint == _QLEVER_ENDPOINT and not sparql.lstrip().upper().startswith("PREFIX"):
        sparql = _WIKIDATA_PREFIXES + sparql
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
    for attempt, delay in enumerate((*_SPARQL_RETRY_DELAYS, None)):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["results"]["bindings"]
        except urllib.error.HTTPError as exc:
            if exc.code not in _SPARQL_TRANSIENT_CODES or delay is None:
                raise
            print(f"  SPARQL HTTP {exc.code} (attempt {attempt + 1}); "
                  f"retrying in {delay}s ...", file=sys.stderr)
            time.sleep(delay)


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

    # Historical P31 types: former/historical entities that may have P297 set but
    # lack P576 (dissolution date) in Wikidata (e.g. Soviet Union SU, Yugoslavia YU).
    # Q3024240 historical country, Q1620908 former country,
    # Q19953632 historical sovereign state, Q41710 historical state,
    # Q11514315 historical period
    historical_types = (
        "wd:Q3024240 wd:Q1620908 wd:Q19953632 wd:Q41710 wd:Q11514315"
    )

    sparql = f"""
SELECT ?item ?alpha2 {label_vars} ?desc ?gnid ?continent ?is_sovereign ?parent WHERE {{
  ?item wdt:P297 ?alpha2 .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
  FILTER NOT EXISTS {{ ?item wdt:P31 ?htype . VALUES ?htype {{ {historical_types} }} }}
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
SELECT ?item ?stype {label_vars} ?desc ?gnid ?dissolved ?parent WHERE {{
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
  OPTIONAL {{ ?item wdt:P131 ?parent . }}
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
    # An item may appear multiple times if it has several matching P31 or P131 values.
    seen_map: dict = {}
    for row in rows:
        qid = _qid_from_uri(row.get("item", {}).get("value", ""))
        if not qid:
            continue
        stype        = _qid_from_uri(row.get("stype", {}).get("value", ""))
        gnid         = row.get("gnid",      {}).get("value", "")
        is_dissolved = bool(row.get("dissolved", {}).get("value", ""))
        parent       = _qid_from_uri(row.get("parent", {}).get("value", ""))
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
            # Merge gnid / dissolved / labels / parent_qids if missing
            if not seen_map[qid]["gnid"] and gnid:
                seen_map[qid]["gnid"] = gnid
            if is_dissolved:
                seen_map[qid]["is_dissolved"] = True
            for lang, lbl in labels.items():
                seen_map[qid]["labels"].setdefault(lang, lbl)
            if parent:
                seen_map[qid]["parent_qids"].add(parent)
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
                "parent_qids":  {parent} if parent else set(),
            }
    # Convert parent_qids sets to sorted lists for JSON serialisation
    for e in seen_map.values():
        e["parent_qids"] = sorted(e["parent_qids"])
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

    def _lbl(item): return _best_label(item, locales, "qid")

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
            lang_pvs = {}
            for item in items_list:
                lbl = item.get("labels", {}).get(lang, "")
                if not lbl:
                    continue
                default = normalize_text(_lbl(item))
                if normalize_text(lbl) != default:
                    lang_pvs[item["qid"]] = {"title": normalize_text(lbl)}
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

    def _lbl(entry): return _best_label(entry, locales, "qid")

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
    enum_key    = f"GeoWikiAdm1{scope}" if scope != "global" else "GeoWikiAdm1"
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

    def _lbl(item): return _best_label(item, locales, "qid")

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
        # skos:broader: settlement → containing ADM entity (P131, one row per parent)
        for parent_qid in entry.get("parent_qids", []):
            sssom_rows.append({
                "subject_id":           f"wd:{qid}",
                "predicate_id":         "skos:broader",
                "object_id":            f"wd:{parent_qid}",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":        lbl,
                "object_label":         "",
            })

    # Mark stype headers status: historical when every child is historical so that
    # minus: {status: [historical]} also prunes the now-empty category headers.
    # Only synthetic headers (no "meaning") are eligible; real entities are not touched.
    _hdr_all_hist: dict = {}   # header_key → True if all children seen so far are historical
    for pv_val in pvs.values():
        parent = pv_val.get("is_a")
        if parent and parent in pvs and "meaning" not in pvs[parent]:
            child_hist = pv_val.get("status") == "historical"
            if parent not in _hdr_all_hist:
                _hdr_all_hist[parent] = child_hist
            else:
                _hdr_all_hist[parent] = _hdr_all_hist[parent] and child_hist
    for hdr_key, all_hist in _hdr_all_hist.items():
        if all_hist:
            pvs[hdr_key]["status"] = "historical"

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
    enum_key = f"GeoWikiSettlement{scope}"
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
        lang_pvs = {}
        for e in sorted_entries:
            lbl = e.get("labels", {}).get(lang, "")
            if not lbl:
                continue
            default = normalize_text(_lbl(e))
            if normalize_text(lbl) != default:
                lang_pvs[e["qid"]] = {"title": normalize_text(lbl)}
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

    # Load cached ADM1 payload for province hierarchy embedding.
    # Auto-fetch if absent (inherits chain; zip only, no config entry).
    adm1_key = f"GeoWiki_ADM1_{alpha2.upper()}"
    if not _entry_exists(adm1_key):
        print(f"  Auto-fetching {adm1_key} (inherits chain; no config entry) ...")
        _save_json(adm1_key, _fetch_adm1_payload(alpha2, locales,
                                                   endpoint=endpoint, use_post=use_post))
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

    def _lbl(entry): return _best_label(entry, locales, "name", "qid")

    # If adm1_entries absent (old cached payload), try loading from shared zip
    if not adm1_entries and scope:
        adm1_key = f"GeoWiki_ADM1_{scope}"
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

    enum_key   = f"GeoWikiAdm2{scope}"
    source_url = (source.get("reachable_from") or {}).get("source_ontology",
                                                            "https://www.wikidata.org/")
    enum_dict  = {"name": enum_key, "title": enum_title}
    if has_adm1:
        enum_dict["inherits"] = f"GeoWikiAdm1{scope}"
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

    # Companion ADM1 yaml: cascade up the inherits chain (ADM2 → ADM1)
    if scope and adm1_entries:
        companion1_path = f"sources/GeoWiki_ADM1_{scope}.yaml"
        if not os.path.exists(companion1_path):
            adm1_data = {
                "scope":        scope,
                "entries":      adm1_entries,
                "country_name": country_name,
                "country_qid":  data.get("country_qid", ""),
            }
            print(f"  Companion {companion1_path}: building from cache ...")
            _build_adm1_yaml(f"GeoWiki_ADM1_{scope}", {}, adm1_data, locales)
        else:
            print(f"  Companion {companion1_path} already exists; skipping.")


# ---------------------------------------------------------------------------
# GeoWikiADM34 — third- and fourth-order administrative divisions
# ---------------------------------------------------------------------------

def _iter_cc_zip_rows(cc, feat_class=None, feat_codes=None):
    """Download GeoNames {CC}.zip and yield matching 19-column rows.

    feat_class : optional single letter e.g. 'A' (col 6 filter).
    feat_codes : optional set of feature codes to keep (col 7 filter).
    """
    import io
    cc  = cc.upper()
    url = _GEONAMES_CC_URL.format(CC=cc)
    print(f"  Downloading GeoNames {cc}.zip ...")
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=120) as resp:
        zip_bytes = resp.read()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        txt_name = f"{cc}.txt"
        if txt_name not in zf.namelist():
            matches = [n for n in zf.namelist() if n.lower() == txt_name.lower()]
            if not matches:
                print(f"  Error: {txt_name} not found in {cc}.zip", file=sys.stderr)
                return
            txt_name = matches[0]
        with zf.open(txt_name) as fh:
            for raw_line in fh:
                cols = raw_line.decode("utf-8").rstrip("\n").split("\t")
                if len(cols) < 14:
                    continue
                if feat_class and cols[6].strip() != feat_class:
                    continue
                if feat_codes and cols[7].strip() not in feat_codes:
                    continue
                yield cols


def _fetch_geonames_adm34_rows(alpha2, levels=("ADM3",)):
    """Parse ADM3/ADM4 entries from GeoNames {CC}.zip.

    Returns list of dicts: gnid, name, compound_code, parent_compound,
    feat_code, adm1_code, adm2_code, adm3_code.

    Tries the shared GeoNames.zip cache first (populated by source_geonames.py);
    falls back to a fresh download from GeoNames if not cached.
    """
    cc     = alpha2.upper()
    levels = {lv.upper() for lv in levels}
    lv_str = "/".join(sorted(levels))

    # --- Try loading from shared GeoNames.zip cache ---
    gn_zip_path  = os.path.join("sources", "GeoNames.zip")
    gn_cache_key = f"GeoNames_ADM3ADM4_{cc}.json"
    if os.path.exists(gn_zip_path):
        try:
            with zipfile.ZipFile(gn_zip_path) as zf:
                if gn_cache_key in zf.namelist():
                    with zf.open(gn_cache_key) as fh:
                        cached = json.load(fh)
                    raw_entries = cached.get("entries", [])
                    entries = []
                    for e in raw_entries:
                        feat_code = e.get("feat_code", "")
                        if feat_code not in levels:
                            continue
                        compound      = e.get("compound_code", "")
                        parts         = compound.split(".")
                        parent_compound = ".".join(parts[:-1]) if len(parts) > 1 else ""
                        entries.append({
                            "gnid":            e.get("gnid", ""),
                            "name":            e.get("name", ""),
                            "compound_code":   compound,
                            "parent_compound": parent_compound,
                            "feat_code":       feat_code,
                            "adm1_code":       e.get("adm1_code", ""),
                            "adm2_code":       e.get("adm2_code", ""),
                            "adm3_code":       e.get("adm3_code", ""),
                        })
                    print(f"    Loaded {len(entries)} {lv_str} entries from GeoNames cache.")
                    return entries
        except Exception as exc:
            print(f"  Note: could not read GeoNames cache ({exc}); "
                  f"downloading {cc}.zip ...", file=sys.stderr)

    # --- Fallback: download from GeoNames ---
    entries = []
    for cols in _iter_cc_zip_rows(cc, feat_class="A", feat_codes=levels):
        gnid      = cols[0].strip()
        name      = cols[1].strip()
        feat_code = cols[7].strip()
        adm1      = cols[10].strip()
        adm2      = cols[11].strip()
        adm3      = cols[12].strip()
        if not gnid or not adm1:
            continue
        if feat_code == "ADM3":
            compound        = f"{cc}.{adm1}.{adm2}.{adm3}"
            parent_compound = f"{cc}.{adm1}.{adm2}"
        else:   # ADM4
            adm4 = cols[13].strip()
            compound        = f"{cc}.{adm1}.{adm2}.{adm3}.{adm4}"
            parent_compound = f"{cc}.{adm1}.{adm2}.{adm3}"
        entries.append({
            "gnid":            gnid,
            "name":            name,
            "compound_code":   compound,
            "parent_compound": parent_compound,
            "feat_code":       feat_code,
            "adm1_code":       adm1,
            "adm2_code":       adm2,
            "adm3_code":       adm3,
        })
    print(f"    Parsed {len(entries)} {lv_str} entries for {cc}.")
    return entries


def _fetch_alternatenames_wkdt(cc):
    """Download alternatenames/{CC}.zip and extract Wikidata QID mappings.

    Returns dict: gnid (str) → qid (str, bare, e.g. 'Q99')
    Historic entries (isHistoric == '1') are excluded.
    """
    cc  = cc.upper()
    url = f"https://download.geonames.org/export/dump/alternatenames/{cc}.zip"
    print(f"  Downloading alternatenames/{cc}.zip for wkdt crosswalk ...")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            zdata = resp.read()
    except Exception as exc:
        print(f"  Warning: could not download alternatenames/{cc}.zip: {exc}",
              file=sys.stderr)
        return {}
    result = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zdata)) as zf:
            fname = f"{cc}.txt"
            with zf.open(fname) as fh:
                for line in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
                    cols = line.rstrip("\n").split("\t")
                    if len(cols) < 4:
                        continue
                    if cols[2] != "wkdt":
                        continue
                    if len(cols) >= 8 and cols[7] == "1":   # isHistoric
                        continue
                    gnid = cols[1].strip()
                    qid  = cols[3].strip()
                    if gnid and qid:
                        result[gnid] = qid
    except Exception as exc:
        print(f"  Warning: could not parse alternatenames/{cc}.zip: {exc}",
              file=sys.stderr)
        return {}
    print(f"    Loaded {len(result)} wkdt QID mappings from alternatenames/{cc}.zip")
    return result


def _fetch_wikidata_by_qids(qids, locales=None, endpoint=_WDQS_ENDPOINT, use_post=True):
    """Fetch labels, descriptions, and P131 parent for a list of Wikidata QIDs.

    Returns dict: qid → {labels: {lang: str}, desc: str, parent_qid: str}
    """
    locales = locales or ["en"]
    label_vars      = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
        f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )
    result    = {}
    batch_size = 200
    qid_list  = list(qids)
    for i in range(0, len(qid_list), batch_size):
        batch  = qid_list[i : i + batch_size]
        values = " ".join(f"wd:{q}" for q in batch)
        sparql = f"""
SELECT ?item {label_vars} ?desc ?parent WHERE {{
  VALUES ?item {{ {values} }}
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
  OPTIONAL {{ ?item wdt:P131 ?parent . }}
}}
"""
        try:
            rows = _sparql_query(sparql, timeout=60, endpoint=endpoint, use_post=use_post)
        except Exception as exc:
            print(f"  Warning: QID batch query failed: {exc}", file=sys.stderr)
            continue
        for row in rows:
            qid = _qid_from_uri(row.get("item", {}).get("value", ""))
            if not qid or qid in result:
                continue
            labels = {
                lang: row[f"label_{lang}"]["value"]
                for lang in locales if row.get(f"label_{lang}", {}).get("value")
            }
            desc       = row.get("desc",   {}).get("value", "")
            parent_uri = row.get("parent", {}).get("value", "")
            parent_qid = _qid_from_uri(parent_uri) if parent_uri else ""
            result[qid] = {"labels": labels, "desc": desc, "parent_qid": parent_qid}
    return result


def _enrich_adm34_with_wikidata(gn_rows, locales=None, endpoint=_WDQS_ENDPOINT,
                                use_post=True, gnid_to_qid=None):
    """Build gnid → {qid, labels, desc, parent_qid} for ADM3/ADM4 entries.

    If gnid_to_qid is provided (from _fetch_alternatenames_wkdt), uses those QIDs
    directly and queries Wikidata only for labels/desc/parent_qid via QID-based
    batches.  Falls back to P1566 SPARQL reverse-lookup for unmatched gnids.
    Reports match counts for each stage.

    Returns dict: gnid → {qid, labels, desc, parent_qid}.
    """
    locales     = locales or ["en"]
    gnid_to_qid = gnid_to_qid or {}
    gnids       = [r["gnid"] for r in gn_rows if r.get("gnid")]
    if not gnids:
        return {}

    # --- Stage A: QIDs from GeoNames alternateNames wkdt
    wd_by_gnid:    dict = {}
    matched_qids:  list = []
    unmatched_gnids: list = []
    for gnid in gnids:
        qid = gnid_to_qid.get(gnid)
        if qid:
            wd_by_gnid[gnid] = {"qid": qid, "labels": {}, "desc": "", "parent_qid": ""}
            matched_qids.append(qid)
        else:
            unmatched_gnids.append(gnid)
    n_alt = len(matched_qids)
    pct_alt = 100 * n_alt // len(gnids) if gnids else 0
    print(f"  alternateNames wkdt: {n_alt}/{len(gnids)} matched ({pct_alt}%)")

    # --- Stage B: Wikidata labels/desc/P131 for matched QIDs
    if matched_qids:
        print(f"  Fetching labels/desc/P131 for {len(matched_qids)} matched QIDs ...")
        qid_details = _fetch_wikidata_by_qids(matched_qids, locales, endpoint, use_post)
        for gnid, info in wd_by_gnid.items():
            qid = info.get("qid")
            if qid and qid in qid_details:
                info.update(qid_details[qid])

    # --- Stage C: P1566 SPARQL fallback for gnids with no wkdt entry
    if unmatched_gnids:
        label_vars      = " ".join(f"?label_{lang}" for lang in locales)
        label_optionals = "\n".join(
            f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
            f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
            for lang in locales
        )
        print(f"  P1566 SPARQL fallback for {len(unmatched_gnids)} unmatched gnids ...")
        batch_size = 200
        n_sparql = 0
        for i in range(0, len(unmatched_gnids), batch_size):
            batch  = unmatched_gnids[i : i + batch_size]
            values = " ".join(f'"{g}"' for g in batch)
            sparql = f"""
SELECT ?item ?gnid {label_vars} ?desc ?parent WHERE {{
  VALUES ?gnid {{ {values} }}
  ?item wdt:P1566 ?gnid .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
  OPTIONAL {{ ?item wdt:P131 ?parent . }}
}}
"""
            try:
                rows = _sparql_query(sparql, timeout=60, endpoint=endpoint,
                                     use_post=use_post)
            except Exception as exc:
                print(f"  Warning: P1566 fallback batch {i // batch_size + 1} failed: {exc}",
                      file=sys.stderr)
                continue
            seen: set = set()
            for row in rows:
                gnid = row.get("gnid", {}).get("value", "")
                if not gnid or gnid in seen:
                    continue
                seen.add(gnid)
                qid        = _qid_from_uri(row.get("item",   {}).get("value", ""))
                parent_uri = row.get("parent", {}).get("value", "")
                parent_qid = _qid_from_uri(parent_uri) if parent_uri else ""
                labels     = {
                    lang: row[f"label_{lang}"]["value"]
                    for lang in locales if row.get(f"label_{lang}", {}).get("value")
                }
                desc = row.get("desc", {}).get("value", "")
                wd_by_gnid[gnid] = {"qid": qid, "labels": labels, "desc": desc,
                                    "parent_qid": parent_qid}
                if qid:
                    n_sparql += 1
        print(f"    P1566 fallback matched: {n_sparql}/{len(unmatched_gnids)}")

    n_total = sum(1 for v in wd_by_gnid.values() if v.get("qid"))
    pct     = 100 * n_total // len(gnids) if gnids else 0
    print(f"  Total crosswalk: {n_total}/{len(gnids)} ({pct}%) have a Wikidata QID")
    return wd_by_gnid


def _secondary_adm34_lookup(unmatched_rows, adm2_comp_to_qid, country_qid, locales,
                            endpoint=_WDQS_ENDPOINT, use_post=False):
    """Secondary Wikidata lookup for GeoNames ADM3/ADM4 entries with no P1566 match.

    Groups rows by their parent ADM2 QID (from adm2_comp_to_qid) and queries
    Wikidata for items with wdt:P131 = {adm2_qid} whose English label matches
    the GeoNames name.  Rows whose parent compound is not in adm2_comp_to_qid
    fall back to a country-level P17 = {country_qid} search.

    Returns dict: gnid → {qid, labels, desc, parent_qid}.
    """
    locales = locales or ["en"]

    # Build (parent_qid, name, gnid) triples; separate ADM2-parent vs country-parent
    by_adm2:    dict = defaultdict(list)  # adm2_qid → [(name, gnid), ...]
    by_country: list = []                 # [(name, gnid), ...]

    for row in unmatched_rows:
        name = row.get("name", "")
        gnid = row.get("gnid", "")
        if not (name and gnid):
            continue
        parent_compound = row.get("parent_compound", "")
        adm2_qid = adm2_comp_to_qid.get(parent_compound, "")
        if adm2_qid:
            by_adm2[adm2_qid].append((name, gnid))
        elif country_qid:
            by_country.append((name, gnid))

    n_adm2    = sum(len(v) for v in by_adm2.values())
    n_country = len(by_country)
    if not (n_adm2 + n_country):
        return {}

    print(f"  Secondary name+P131 lookup: {n_adm2} entries with ADM2 parent, "
          f"{n_country} fallback to country P17 ...")

    wd_by_gnid: dict = {}
    batch_size = 80

    def _run_batch(name_to_gnid, parent_qid, use_p17=False):
        """Query Wikidata for names under a known parent; update wd_by_gnid."""
        values = "\n    ".join(
            f'"{n.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}"@en'
            for n in name_to_gnid
        )
        prop = "wdt:P17" if use_p17 else "wdt:P131"
        sparql = f"""
SELECT DISTINCT ?item ?name ?desc WHERE {{
  VALUES ?name {{
    {values}
  }}
  ?item rdfs:label ?name .
  ?item {prop} wd:{parent_qid} .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
}}
"""
        try:
            rows = _sparql_query(sparql, timeout=30, endpoint=endpoint, use_post=use_post)
        except Exception as exc:
            print(f"    Warning: secondary lookup ({parent_qid}) failed: {exc}", file=sys.stderr)
            return
        for rrow in rows:
            name = rrow.get("name", {}).get("value", "")
            qid  = _qid_from_uri(rrow.get("item", {}).get("value", ""))
            if name and qid and name in name_to_gnid:
                gnid = name_to_gnid[name]
                if gnid not in wd_by_gnid:
                    wd_by_gnid[gnid] = {
                        "qid":        qid,
                        "labels":     {"en": name},
                        "desc":       rrow.get("desc", {}).get("value", ""),
                        "parent_qid": parent_qid,
                    }

    # ADM2-parent batches (P131)
    for adm2_qid, pairs in by_adm2.items():
        for i in range(0, len(pairs), batch_size):
            chunk = dict(pairs[i : i + batch_size])  # name → gnid
            _run_batch(chunk, adm2_qid, use_p17=False)

    # Country fallback batches (P17)
    if by_country and country_qid:
        for i in range(0, len(by_country), batch_size):
            chunk = dict(by_country[i : i + batch_size])
            _run_batch(chunk, country_qid, use_p17=True)

    n_found = len(wd_by_gnid)
    n_total = n_adm2 + n_country
    print(f"    Secondary lookup: found {n_found}/{n_total} additional QIDs.")
    return wd_by_gnid


def _ensure_ancestor_caches_gw(alpha2, locales, endpoint=_WDQS_ENDPOINT, use_post=True):
    """Fetch and cache GeoWiki ADM1 and ADM2 for *alpha2* if not already cached.

    Called automatically at the start of every ADM3/ADM4 fetch (covers both -f and
    -a paths).  _fetch_adm2_payload also independently auto-fetches ADM1 if absent,
    so the per-level cascade holds even when ADM2 is fetched directly.

    No config entry is written (config_file omitted → zip-only).  ADM1 is fetched
    before ADM2 because _fetch_adm2_payload loads the ADM1 cache to build the
    province hierarchy embedded in the ADM2 output.
    """
    alpha2 = alpha2.upper()
    adm1_key = f"GeoWiki_ADM1_{alpha2}"
    if not _entry_exists(adm1_key):
        print(f"  Auto-fetching {adm1_key} (inherits chain; no config entry) ...")
        _save_json(adm1_key, _fetch_adm1_payload(alpha2, locales,
                                                   endpoint=endpoint, use_post=use_post))
    adm2_key = f"GeoWiki_ADM2_{alpha2}"
    if not _entry_exists(adm2_key):
        print(f"  Auto-fetching {adm2_key} (inherits chain; no config entry) ...")
        _save_json(adm2_key, _fetch_adm2_payload(alpha2, locales,
                                                   endpoint=endpoint, use_post=use_post))


def _fetch_adm34_payload(alpha2, levels, locales, endpoint=_WDQS_ENDPOINT, use_post=True):
    """Build ADM3/ADM4 payload for a single country.

    Uses GeoNames {CC}.zip as the authoritative municipality list, then enriches
    with Wikidata QIDs via two-stage lookup:
      1. Primary: P1566 (GeoNames ID) reverse-lookup. (alternateNames wkdt is skipped
         for ADM3/ADM4 — wkdt coverage is ~0% at this level.)
      2. Secondary: name + P131 (parent ADM2) / P17 (country) for entries not
         matched in stage 1.
    Auto-fetches and caches ADM1 and ADM2 (zip only, no config entry) if absent,
    so that parent compound_code → QID maps and the companion yaml cascade have
    everything they need.
    """
    cc = alpha2.upper()
    _ensure_ancestor_caches_gw(alpha2, locales, endpoint=endpoint, use_post=use_post)

    # Load cached ADM2 payload FIRST — needed for secondary enrichment and is_a links
    adm2_key     = f"GeoWiki_ADM2_{cc}"
    adm2_entries: list = []
    country_qid  = ""
    country_name = ""
    if _entry_exists(adm2_key):
        try:
            adm2_payload = _load_json(adm2_key)
            adm2_entries = adm2_payload.get("entries", [])
            country_qid  = adm2_payload.get("country_qid", "")
            country_name = adm2_payload.get("country_name", "")
            print(f"  Loaded ADM2 data from cached {adm2_key} for province hierarchy.")
        except Exception:
            pass
    if not adm2_entries:
        print(f"  Note: no cached {adm2_key} — ADM3 parent links will be absent.",
              file=sys.stderr)

    # Fall back to country QID from Wikidata if ADM2 didn't have it
    if not country_qid:
        try:
            rows = _sparql_query(
                f'SELECT ?item ?label WHERE {{ '
                f'?item wdt:P297 "{cc}" . '
                f'FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }} '
                f'?item rdfs:label ?label . FILTER(LANG(?label) = "en") '
                f'}} LIMIT 1',
                timeout=15, endpoint=endpoint, use_post=use_post,
            )
            if rows:
                country_qid  = _qid_from_uri(rows[0].get("item",  {}).get("value", ""))
                country_name = rows[0].get("label", {}).get("value", "")
        except Exception:
            pass

    # Fetch GeoNames ADM3/ADM4 rows from the per-country zip
    try:
        gn_rows = _fetch_geonames_adm34_rows(cc, levels)
    except Exception as exc:
        print(f"  Error fetching GeoNames ADM3/ADM4 for {cc}: {exc}", file=sys.stderr)
        gn_rows = []

    if not gn_rows:
        return {"type": "adm34", "scope": cc, "entries": [], "levels": sorted(levels),
                "adm2_entries": adm2_entries, "country_qid": country_qid,
                "country_name": country_name}

    # Skip alternateNames for ADM3/ADM4: wkdt coverage is minimal at this level
    # (~0% match rate vs 22 MB download). Use P1566 SPARQL directly.
    wd_by_gnid = _enrich_adm34_with_wikidata(
        gn_rows, locales, endpoint, use_post)

    # Stage 2: Secondary name + P131/P17 lookup for entries still missing a QID
    adm2_comp_to_qid = {
        e["compound_code"]: e["qid"]
        for e in adm2_entries
        if e.get("qid") and e.get("compound_code")
    }
    unmatched = [gn for gn in gn_rows
                 if not wd_by_gnid.get(gn["gnid"], {}).get("qid")]
    if unmatched and (adm2_comp_to_qid or country_qid):
        wd_extra = _secondary_adm34_lookup(
            unmatched, adm2_comp_to_qid, country_qid, locales, endpoint, use_post
        )
        for gnid, info in wd_extra.items():
            if gnid not in wd_by_gnid or not wd_by_gnid[gnid].get("qid"):
                wd_by_gnid[gnid] = info

    # Build unified entries list
    entries = []
    for gn in gn_rows:
        gnid = gn["gnid"]
        wd   = wd_by_gnid.get(gnid, {})
        entries.append({
            "gnid":            gnid,
            "name":            gn["name"],
            "compound_code":   gn["compound_code"],
            "parent_compound": gn["parent_compound"],
            "feat_code":       gn["feat_code"],
            "qid":             wd.get("qid", ""),
            "labels":          wd.get("labels", {}),
            "desc":            wd.get("desc", ""),
            "parent_qid":      wd.get("parent_qid", ""),
        })

    return {
        "type":         "adm34",
        "scope":        cc,
        "entries":      entries,
        "levels":       sorted(levels),
        "adm2_entries": adm2_entries,
        "country_qid":  country_qid,
        "country_name": country_name,
    }


def _build_adm34_yaml(key, source, data, locales=None, only_levels=None):
    """Write sources/{key}.yaml for GeoWikiADM34 scope.

    Produces one or two enums (GeoWikiAdm3{cc} and optionally GeoWikiAdm4{cc}).
    ADM3 entries carry is_a = parent ADM2 QID resolved via compound-code lookup.
    ADM4 entries carry is_a = parent ADM3 QID resolved the same way.
    Uses inherits: GeoWikiAdm2{cc} when ADM2 data is available so the combined
    hierarchy (province → county → municipality) is available in a single picklist.

    only_levels: set of level names to emit, e.g. {"ADM3"} or {"ADM4"}.
    None means emit all levels present in data (backward-compatible default).
    Note: ADM3 compound→QID mapping is always built (needed for ADM4 is_a links),
    but the ADM3 enum is only added to output when only_levels includes "ADM3" or is None.

    Side-effects: companion yaml files for the full ADM inherits chain are written
    for any ancestor level whose yaml is absent but whose cached payload exists:
    - ADM4-only: writes GeoWiki_ADM3_{scope}.yaml companion first
    - ADM3 or ADM4: then writes GeoWiki_ADM2_{scope}.yaml (if ADM2 cached), which
      in turn cascades to write GeoWiki_ADM1_{scope}.yaml (if ADM1 cached)
    Full chain ADM4 → ADM3 → ADM2 → ADM1 is satisfied automatically when all
    intermediate caches are present in GeoWiki.zip.
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]

    scope        = data.get("scope", "")
    entries      = data.get("entries", [])
    adm2_entries = data.get("adm2_entries", [])
    levels       = set(data.get("levels", ["ADM3"]))

    def _lbl(entry): return _best_label(entry, locales, "name", "qid", "gnid")

    # Build compound_code → pv_code from ADM2 entries (QID preferred, gnid fallback)
    adm2_compound_to_code: dict = {}
    for e in adm2_entries:
        cc_code = e.get("compound_code", "")
        if not cc_code:
            continue
        adm2_compound_to_code[cc_code] = e.get("qid") or e.get("gnid", "")

    has_adm2      = bool(adm2_entries)
    adm2_enum_key = f"GeoWikiAdm2{scope}"

    # Separate ADM3 and ADM4 entries
    adm3_entries = [e for e in entries if e.get("feat_code") == "ADM3"]
    adm4_entries = [e for e in entries if e.get("feat_code") == "ADM4"]

    sssom_rows: list = []

    def _emit_pv(pvs, entry, feat_code, is_a_code):
        """Emit one ADM3 or ADM4 PV; return the QID used (or None).

        GeoWiki requires a Wikidata QID for every permissible value.  Entries
        with no QID (not matched by P1566 or the secondary lookup) are skipped.
        GeoNames IDs (gnid) are stored only in the SSSOM cross-reference file.
        """
        qid  = entry.get("qid", "")
        gnid = entry.get("gnid", "")
        if not qid:
            # GeoWiki entries must use Wikidata QID keys; skip if no QID found
            return None
        pv_code  = qid
        meaning  = f"wd:{qid}"
        lbl      = normalize_text(_lbl(entry))
        gno_type = f"gno:A.{feat_code}"
        add_permissible_value(
            pvs, pv_code,
            title=lbl,
            meaning=meaning,
            description=normalize_text(entry.get("desc", "")) or None,
            is_a=is_a_code or None,
        )
        sssom_rows.append({
            "subject_id":            f"wd:{qid}",
            "predicate_id":          "skos:broadMatch",
            "object_id":             gno_type,
            "mapping_justification": "semapv:ManualMappingCuration",
            "subject_label":         lbl,
            "object_label":          "",
        })
        if gnid:
            sssom_rows.append({
                "subject_id":            f"gnf:{gnid}",
                "predicate_id":          "skos:broadMatch",
                "object_id":             gno_type,
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         lbl,
                "object_label":          "",
            })
            sssom_rows.append({
                "subject_id":            f"wd:{qid}",
                "predicate_id":          "skos:exactMatch",
                "object_id":             f"gnf:{gnid}",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         lbl,
                "object_label":          "",
            })
        return pv_code

    # Build ADM3 pvs; track compound_code → pv_code for ADM4 is_a lookup
    adm3_pvs: dict                  = {}
    adm3_compound_to_code: dict     = {}
    for entry in sorted(adm3_entries, key=lambda e: _title_sort_key(_lbl(e))):
        parent_compound = entry.get("parent_compound", "")
        is_a = adm2_compound_to_code.get(parent_compound, "")
        code = _emit_pv(adm3_pvs, entry, "ADM3", is_a)
        if code:
            adm3_compound_to_code[entry.get("compound_code", "")] = code

    # Build ADM4 pvs
    adm4_pvs: dict = {}
    if "ADM4" in levels and adm4_entries:
        for entry in sorted(adm4_entries, key=lambda e: _title_sort_key(_lbl(e))):
            parent_compound = entry.get("parent_compound", "")
            is_a = adm3_compound_to_code.get(parent_compound, "")
            _emit_pv(adm4_pvs, entry, "ADM4", is_a)

    # Stats
    n3         = len(adm3_entries)
    n3_wd      = sum(1 for e in adm3_entries if e.get("qid"))
    n3_skipped = n3 - len(adm3_pvs)
    n3_isa     = sum(1 for pv in adm3_pvs.values()
                     if isinstance(pv, dict) and pv.get("is_a"))
    n4         = len(adm4_entries)
    n4_wd      = sum(1 for e in adm4_entries if e.get("qid"))
    pct3       = 100 * n3_wd // n3 if n3 else 0
    print(f"  Crosswalk: {n3_wd}/{n3} ADM3 entries ({pct3}%) have Wikidata QIDs; "
          f"{n3_isa}/{n3} have ADM2 parent links")
    if n3_skipped:
        print(f"  Skipped {n3_skipped} ADM3 entries with no Wikidata QID (not in output).")
    if n4:
        n4_skipped = n4 - len(adm4_pvs)
        pct4 = 100 * n4_wd // n4 if n4 else 0
        print(f"  Crosswalk: {n4_wd}/{n4} ADM4 entries ({pct4}%) have Wikidata QIDs")
        if n4_skipped:
            print(f"  Skipped {n4_skipped} ADM4 entries with no Wikidata QID (not in output).")

    source_url = (source.get("reachable_from") or {}).get(
        "source_ontology",
        "https://www.wikidata.org/",
    )

    enums: dict = {}

    # ADM3 enum (omit when only_levels restricts to ADM4)
    enum3_key = f"GeoWikiAdm3{scope}"
    if only_levels is None or "ADM3" in only_levels:
        n3_out = len(adm3_pvs)
        enum3_dict: dict = {
            "name":        enum3_key,
            "title":       f"Municipalities of {scope}",
            "description": (
                f"{n3_out} third-order administrative divisions for {scope} from GeoNames "
                f"identified by Wikidata QID ({n3_out}/{n3} GeoNames entries matched)."
            ),
        }
        if has_adm2:
            enum3_dict["inherits"] = adm2_enum_key
        enum3_dict["permissible_values"] = adm3_pvs
        enums[enum3_key] = enum3_dict
        log_extraction(enum3_key, count=len(adm3_pvs))

    # ADM4 enum (only when entries exist and level is requested)
    if adm4_pvs and (only_levels is None or "ADM4" in only_levels):
        enum4_key  = f"GeoWikiAdm4{scope}"
        n4_out = len(adm4_pvs)
        enum4_dict: dict = {
            "name":        enum4_key,
            "title":       f"Sub-municipal divisions of {scope}",
            "description": (
                f"{n4_out} fourth-order administrative divisions for {scope} from GeoNames "
                f"identified by Wikidata QID ({n4_out}/{n4} GeoNames entries matched)."
            ),
            "inherits": enum3_key,
            "permissible_values": adm4_pvs,
        }
        enums[enum4_key] = enum4_dict
        log_extraction(enum4_key, count=len(adm4_pvs))

    # Companion ADM3 yaml: written as a side-effect when processing ADM4-only, so
    # that GeoWikiAdm4{scope}'s inherits: GeoWikiAdm3{scope} dependency resolves
    # automatically without requiring a separate GeoWiki_ADM3_{scope} config entry.
    if only_levels == {"ADM4"} and adm3_pvs:
        companion_path = f"sources/GeoWiki_ADM3_{scope}.yaml"
        if not os.path.exists(companion_path):
            n3_out = len(adm3_pvs)
            c_enum3_dict: dict = {
                "name":        enum3_key,
                "title":       f"Municipalities of {scope}",
                "description": (
                    f"{n3_out} third-order administrative divisions for {scope} from GeoNames "
                    f"identified by Wikidata QID ({n3_out}/{n3} GeoNames entries matched). "
                    f"Auto-generated as companion to GeoWiki_ADM4_{scope}."
                ),
            }
            if has_adm2:
                c_enum3_dict["inherits"] = adm2_enum_key
            c_enum3_dict["permissible_values"] = adm3_pvs
            c_schema = make_config_schema(
                id=source_url,
                name=f"GeoWiki_ADM3_{scope}",
                title=f"Wikidata ADM3 for {scope}",
                description=(
                    f"Third-order administrative divisions for {scope} from GeoNames "
                    f"with Wikidata QIDs. Auto-generated as companion to "
                    f"GeoWiki_ADM4_{scope}."
                ),
                license="CC0 1.0",
                prefixes=_SOURCE_PREFIXES,
                enums={enum3_key: c_enum3_dict},
            )
            with open(companion_path, "w") as f:
                yaml.dump(c_schema, f, Dumper=IndentedDumper, default_flow_style=False,
                          sort_keys=False)
            log_extraction(enum3_key, count=n3_out)
            print(f"  Companion {companion_path} written ({n3_out} ADM3 terms) "
                  f"to satisfy GeoWikiAdm4{scope} inherits dependency.")
        else:
            print(f"  Companion {companion_path} already exists; skipping.")

    # Companion ADM2 yaml: cascade up the inherits chain (ADM3 → ADM2 → ADM1)
    # _build_adm2_yaml will itself cascade to ADM1 if its cache is present.
    if scope:
        companion2_path = f"sources/GeoWiki_ADM2_{scope}.yaml"
        if not os.path.exists(companion2_path):
            if _entry_exists(f"GeoWiki_ADM2_{scope}"):
                try:
                    adm2_payload = _load_json(f"GeoWiki_ADM2_{scope}")
                    print(f"  Companion {companion2_path}: building from cache ...")
                    _build_adm2_yaml(f"GeoWiki_ADM2_{scope}", {}, adm2_payload, locales)
                except Exception as exc:
                    print(f"  Note: could not load GeoWiki_ADM2_{scope} from cache: {exc}",
                          file=sys.stderr)
            else:
                print(f"  Note: GeoWiki_ADM2_{scope} not in cache; "
                      f"companion ADM2 yaml skipped "
                      f"(run '-f GeoWiki_ADM2_{scope}' to fetch).", file=sys.stderr)
        else:
            print(f"  Companion {companion2_path} already exists; skipping.")

    if only_levels == {"ADM3"}:
        lv_str = "ADM3"
    elif only_levels == {"ADM4"}:
        lv_str = "ADM4"
    else:
        lv_str = "ADM3 and ADM4" if "ADM4" in levels else "ADM3"
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"Wikidata {lv_str} for {scope}",
        description=(
            f"{'Third' if only_levels != {'ADM4'} else 'Fourth'}-order"
            f"{' and fourth-order' if only_levels is None and 'ADM4' in levels else ''} "
            f"administrative divisions for {scope} from GeoNames with Wikidata QIDs."
        ),
        license="CC0 1.0",
        prefixes=_SOURCE_PREFIXES,
        enums=enums,
    )
    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False,
                  sort_keys=False)
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
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, True)

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
    key    = f"GeoWiki_ADM1_{alpha2}" if alpha2 else "GeoWiki_ADM1"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales  = config.get("locales") or ["en"]
    use_qlvr = bool(config.get("apis", {}).get("qlever"))
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, True)

    if not alpha2:
        print("  Note: global GeoWiki ADM1 may timeout on WDQS — set apis.qlever: true for reliability.",
              file=sys.stderr)
        # Global ADM1: not yet implemented (needs QLever and batching by country)
        print("  Global GeoWiki ADM1 not yet implemented; use per-country shorthand "
              "(e.g. GeoWiki_ADM1_US).", file=sys.stderr)
        return True

    print(f"  Querying Wikidata ADM1 for {alpha2} ...")
    payload = _fetch_adm1_payload(alpha2, locales, endpoint, use_post)
    if not payload.get("entries"):
        print(f"  Error: no ADM1 entries returned for {alpha2}.", file=sys.stderr)
        return True

    _save_json(key, payload)

    entry = make_source_entry(
        key, "https://www.wikidata.org/",
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
    key    = f"GeoWiki_ADM2_{alpha2}"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales  = config.get("locales") or ["en"]
    use_qlvr = bool(config.get("apis", {}).get("qlever"))
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, True)

    print(f"  Querying Wikidata ADM2 for {alpha2} ...")
    payload = _fetch_adm2_payload(alpha2, locales, endpoint, use_post)
    if not payload.get("entries"):
        print(f"  Error: no ADM2 entries returned for {alpha2}.", file=sys.stderr)
        return True

    _save_json(key, payload)

    entry = make_source_entry(
        key, "https://www.wikidata.org/",
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


def _register_adm34(alpha2, levels, config_file):
    """Register GeoWikiADM34 (ADM3 and/or ADM4) for a single country."""
    lv_str = "ADM3ADM4" if "ADM4" in {lv.upper() for lv in levels} else "ADM3"
    key    = f"GeoWiki_{lv_str}_{alpha2}"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales  = config.get("locales") or ["en"]
    use_qlvr = bool(config.get("apis", {}).get("qlever"))
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, True)

    levels_set = {lv.upper() for lv in levels}
    print(f"  Fetching GeoNames {lv_str} for {alpha2} and enriching with Wikidata ...")
    payload = _fetch_adm34_payload(alpha2, levels_set, locales, endpoint, use_post)
    if not payload.get("entries"):
        print(f"  Error: no ADM3/ADM4 entries returned for {alpha2}.", file=sys.stderr)
        return True

    _save_json(key, payload)

    entry = make_source_entry(
        key, "https://www.wikidata.org/",
        "GeoWikiADM34", "zip",
        title=f"Wikidata {lv_str} for {alpha2}",
        description=(
            f"Third-order{' and fourth-order' if 'ADM4' in levels_set else ''} "
            f"administrative divisions of {alpha2} from GeoNames with Wikidata QIDs."
        ),
    )
    entry["country"]  = alpha2.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC0 1.0"
    entry["levels"]   = sorted(levels_set)
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_adm34_yaml(key, config["sources"][key], payload, locales=locales)
    return True


def _register_adm3(alpha2, config_file):
    """Register GeoWikiADM3 for a single country (municipalities only)."""
    key    = f"GeoWiki_ADM3_{alpha2}"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales  = config.get("locales") or ["en"]
    use_qlvr = bool(config.get("apis", {}).get("qlever"))
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, True)

    print(f"  Fetching GeoNames ADM3 for {alpha2} (combined ADM3+ADM4 payload cached for ADM4 reuse) ...")
    payload = _fetch_adm34_payload(alpha2, {"ADM3", "ADM4"}, locales, endpoint, use_post)
    if not payload.get("entries"):
        print(f"  Error: no ADM3/ADM4 entries returned for {alpha2}.", file=sys.stderr)
        return True
    _save_json(key, payload)

    entry = make_source_entry(
        key, "https://www.wikidata.org/",
        "GeoWikiADM3", "zip",
        title=f"Wikidata ADM3 for {alpha2}",
        description=(
            f"Third-order administrative divisions of {alpha2} from GeoNames with Wikidata QIDs."
        ),
    )
    entry["country"]  = alpha2.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC0 1.0"
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_adm34_yaml(key, config["sources"][key], payload, locales=locales,
                      only_levels={"ADM3"})
    return True


def _register_adm4(alpha2, config_file):
    """Register GeoWikiADM4 for a single country (sub-municipalities).

    Reuses the ADM3 cached payload when available to avoid re-downloading.
    """
    key      = f"GeoWiki_ADM4_{alpha2}"
    adm3_key = f"GeoWiki_ADM3_{alpha2}"
    config   = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales  = config.get("locales") or ["en"]
    use_qlvr = bool(config.get("apis", {}).get("qlever"))
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, True)

    if _entry_exists(adm3_key):
        print(f"  Reusing cached {adm3_key} data for GeoWiki ADM4 {alpha2} ...")
        payload = _load_json(adm3_key)
    else:
        print(f"  Fetching GeoNames ADM4 for {alpha2} (combined ADM3+ADM4 payload cached under ADM3 key) ...")
        payload = _fetch_adm34_payload(alpha2, {"ADM3", "ADM4"}, locales, endpoint, use_post)
        if payload.get("entries"):
            _save_json(adm3_key, payload)  # cache under ADM3 key too

    if not payload.get("entries"):
        print(f"  Error: no ADM3/ADM4 entries returned for {alpha2}.", file=sys.stderr)
        return True
    _save_json(key, payload)

    entry = make_source_entry(
        key, "https://www.wikidata.org/",
        "GeoWikiADM4", "zip",
        title=f"Wikidata ADM4 for {alpha2}",
        description=(
            f"Fourth-order administrative divisions of {alpha2} from GeoNames with Wikidata QIDs."
        ),
    )
    entry["country"]  = alpha2.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC0 1.0"
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_adm34_yaml(key, config["sources"][key], payload, locales=locales,
                      only_levels={"ADM4"})
    return True


def _register_ppl(alpha2, config_file):
    """Register GeoWiki populated places (settlements) for a single country."""
    key    = f"GeoWiki_Settlement_{alpha2}"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales  = config.get("locales") or ["en"]
    use_qlvr = bool(config.get("apis", {}).get("qlever"))
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, True)

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
      GeoWiki_countries        → register GeoWikiCountries
      GeoWiki_ADM1             → global GeoWikiAdm1 (QLever recommended)
      GeoWiki_ADM1_{CC}        → per-country GeoWikiAdm1
      GeoWiki_ADM2_{CC}        → per-country GeoWikiAdm2
      GeoWiki_ADM3_{CC}        → per-country GeoWikiAdm3 (municipalities)
      GeoWiki_ADM3ADM4_{CC}    → per-country GeoWikiAdm3 + Adm4

    ADM3/ADM4 use GeoNames {CC}.zip as the authoritative entity list with Wikidata
    QIDs added via P1566 reverse-lookup.  Requires GeoWiki_ADM2_{CC} to be cached
    first for county → municipality parent links; run
      python term_harvester.py -a GeoWiki_ADM2_{CC}
    before adding ADM3/ADM4.

    Returns True if the URL was matched (regardless of success).
    """
    url = url.strip()
    m   = _SHORTHAND_RE.match(url)
    if not m:
        return False

    scope_global = m.group(1)   # "countries" or "ADM1" (or None)
    adm_level    = m.group(2)   # "ADM1"/"ADM2"/… (or None) — per-country level
    cc           = m.group(3)   # two-letter country code (or None)

    if scope_global:
        sg = scope_global.lower()
        if sg == "countries":
            return _register_countries(config_file)
        if sg == "adm1":
            return _register_adm1(None, config_file)   # global stub
    elif adm_level and cc:
        level = adm_level.upper()
        if level == "ADM1":
            return _register_adm1(cc.upper(), config_file)
        if level == "SETTLEMENT":
            return _register_ppl(cc.upper(), config_file)
        if level == "ADM2":
            return _register_adm2(cc.upper(), config_file)
        if level == "ADM3":
            return _register_adm3(cc.upper(), config_file)
        if level == "ADM4":
            return _register_adm4(cc.upper(), config_file)
        if level == "ADM3ADM4":
            # Backward-compat: register both as separate sources
            _register_adm3(cc.upper(), config_file)
            return _register_adm4(cc.upper(), config_file)

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

    elif content_type == "GeoWikiADM34":
        alpha2 = source.get("country", "")
        if not alpha2:
            print(f"  Error: GeoWikiADM34 source '{key}' has no country field.",
                  file=sys.stderr)
            return
        levels = set(source.get("levels", ["ADM3"]))
        lv_str = "/".join(sorted(levels))
        print(f"  Re-fetching GeoNames {lv_str} for {alpha2} and re-enriching "
              f"with Wikidata ...")
        payload = _fetch_adm34_payload(alpha2, levels, locales, endpoint, use_p)
        _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoWikiADM3":
        alpha2 = source.get("country", "")
        if not alpha2:
            print(f"  Error: GeoWikiADM3 source '{key}' has no country field.", file=sys.stderr)
            return
        print(f"  Re-fetching GeoNames ADM3 for {alpha2} (combined ADM3+ADM4 payload cached for ADM4 reuse) ...")
        payload = _fetch_adm34_payload(alpha2, {"ADM3", "ADM4"}, locales, endpoint, use_p)
        _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoWikiADM4":
        alpha2 = source.get("country", "")
        if not alpha2:
            print(f"  Error: GeoWikiADM4 source '{key}' has no country field.", file=sys.stderr)
            return
        adm3_key = f"GeoWiki_ADM3_{alpha2}"
        if _entry_exists(adm3_key):
            print(f"  Reusing cached {adm3_key} data for GeoWiki ADM4 {alpha2} ...")
            payload = _load_json(adm3_key)
        else:
            print(f"  Re-fetching GeoNames ADM4 for {alpha2} (combined ADM3+ADM4 payload cached under ADM3 key) ...")
            payload = _fetch_adm34_payload(alpha2, {"ADM3", "ADM4"}, locales, endpoint, use_p)
            if payload:
                _save_json(adm3_key, payload)   # companion cache — not a config entry
        if payload:
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
    content_type = source.get("content_type", "")
    actual_key   = key

    if not _entry_exists(key):
        # Migration fallback: new split keys can reuse old combined ADM3ADM4 cache
        alpha2 = source.get("country", "")
        if content_type in ("GeoWikiADM3", "GeoWikiADM4") and alpha2:
            old_key = f"GeoWiki_ADM3ADM4_{alpha2}"
            if _entry_exists(old_key):
                print(f"  Note: using cached {old_key} data for {key} (migration) ...")
                actual_key = old_key
            else:
                print(f"  Error: {key}.json not found in {_SHARED_ZIP} — run '-f {key}' first",
                      file=sys.stderr)
                return
        else:
            print(f"  Error: {key}.json not found in {_SHARED_ZIP} — run '-f {key}' first",
                  file=sys.stderr)
            return

    data    = _load_json(actual_key)
    locales = locales or _get_project_locales(config_file)
    dtype   = data.get("type", "")

    if dtype == "countries":
        _build_countries_yaml(key, source, data, locales=locales)
    elif dtype == "adm1":
        _build_adm1_yaml(key, source, data, locales=locales)
    elif dtype == "adm2":
        _build_adm2_yaml(key, source, data, locales=locales)
    elif dtype == "adm34":
        if content_type == "GeoWikiADM3":
            _build_adm34_yaml(key, source, data, locales=locales, only_levels={"ADM3"})
        elif content_type == "GeoWikiADM4":
            _build_adm34_yaml(key, source, data, locales=locales, only_levels={"ADM4"})
        else:
            _build_adm34_yaml(key, source, data, locales=locales)
    elif dtype == "ppl":
        _build_ppl_yaml(key, source, data, locales=locales)
    else:
        print(f"  Warning: unknown data type '{dtype}' in {_SHARED_ZIP}", file=sys.stderr)
