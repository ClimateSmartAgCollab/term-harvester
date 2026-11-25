"""Wikidata geopolitical vocabulary handler for term_harvester.py.

Queries Wikidata SPARQL for current geopolitical entities and produces
Wikidata-native LinkML enums.  For GeoNames-native enums see source_geonames.py.

Content types
-------------
GeoWikiCountries
    Sovereign states (wdt:P297), continents (P31=Q5107), major oceans
    (P31=Q9430), and international waterbodies (lakes/rivers with P17 spanning
    2+ countries; seas, gulfs, straits, bays; all with 30+ Wikipedia sitelinks)
    from Wikidata.  Historical entities filtered by absence of wdt:P576
    (dissolved/abolished date).
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
GeoWikiCountryWaters
    Per-country water bodies (seas, gulfs, straits, bays, lakes, rivers) where
    Wikidata P17 references the country.  Requires _WD_COUNTRY_WATERS_MIN_SITELINKS
    (5) Wikipedia sitelinks.  A body shared by two countries (e.g. Lake Ontario for
    CA and US) appears in each country's enum; duplication is intentional.
    If GeoWiki_ADM1_{CC} and/or GeoWiki_ADM2_{CC} data are already cached in
    GeoWiki.zip, the Wikidata P131 (located in administrative entity) property
    is used to assign each water body to its province/territory (ADM1) or
    district/county (ADM2) rather than the country root.  Bodies with no
    resolvable P131 fall back to is_a: {country_qid}.  The enum gains
    ``inherits: GeoWikiAdm2{CC}`` (or ADM1) when ADM data is available.
    Coastal waters like Amundsen Gulf (Canada) are included; for truly
    international bodies use GeoWikiWaterbody.
GeoWikiFirstNations
    Indigenous entities (First Nations bands for CA, federally recognized tribes
    for US, iwi for NZ) from Wikidata.  Built from the cached GeoWiki_ADM2_{CC}
    JSON (no extra SPARQL query); requires include_indigenous: true in config.yaml
    so the ADM2 fetch includes indigenous entities.  Automatically registered when
    GeoWiki_ADM2_{CC} is added with include_indigenous: true.
    Hierarchy: top-level root (e.g. Q392316 "First Nations" for CA) →
    provincial/regional grouping nodes → individual bands/nations.
    For CA the provincial groups come from _INDIGENOUS_ADM1_GROUPS; for US/NZ
    grouping is by adm1_qid (state/region).  Also available as a shorthand:
      python term_harvester.py -a GeoWiki_FirstNations_CA

Shorthand -a patterns
---------------------
GeoWiki_countries           → GeoWikiCountries (sovereign states + continents + oceans + waterbodies)
GeoWiki_ADM1                → GeoWikiAdm1 (all ADM1; QLever recommended globally)
GeoWiki_ADM1_{CC}           → GeoWikiAdm1 single country (e.g. GeoWiki_ADM1_US)
GeoWiki_ADM2_{CC}           → GeoWikiAdm2 single country (e.g. GeoWiki_ADM2_CA)
GeoWiki_ADM3_{CC}           → GeoWikiAdm3 ADM3 only for one country
GeoWiki_ADM3ADM4_{CC}       → GeoWikiAdm3 + Adm4 for one country (e.g. GeoWiki_ADM3ADM4_CA)
GeoWiki_Settlement_{CC}     → GeoWikiSettlement for one country (e.g. GeoWiki_Settlement_NL)
GeoWiki_CountryWaters_{CC}  → GeoWikiCountryWaters for one country (e.g. GeoWiki_CountryWaters_CA)
GeoWiki_FirstNations_{CC}   → GeoWikiFirstNations indigenous entities (e.g. GeoWiki_FirstNations_CA)

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
GeographicWaterBody — oceans, seas, international lakes and rivers

Historical filtering
--------------------
Countries:  must have wdt:P297 (ISO 3166-1 alpha-2) AND
            FILTER NOT EXISTS { ?item wdt:P576 ?end . }
Continents: queried by P31=wd:Q5107, same dissolution filter
Oceans:     queried by P31=wd:Q9430; nearly all are non-dissolved
Waterbodies: lakes (P31=Q23397) and rivers (P31=Q4022) with P17 spanning 2+
            countries; seas, gulfs, straits, bays with 30+ Wikipedia sitelinks;
            oceans excluded
CountryWaters: same water types with P17 = country; 5+ Wikipedia sitelinks
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
_GEOWIKI_SSSOM      = os.path.join("sources", "GeoWiki.sssom.zip")
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
_WD_CLASS_LAKE            = "Q23397"     # lake
_WD_CLASS_RIVER           = "Q4022"      # river
_WD_CLASS_SEA             = "Q165"       # sea (use P31/P279* to catch marginal/inland/etc.)
_WD_CLASS_GULF            = "Q1322134"   # gulf
_WD_CLASS_STRAIT          = "Q37901"     # strait
_WD_CLASS_BAY             = "Q39594"     # bay

# Minimum Wikipedia sitelinks for all items in GeoWikiWaterbody.
# Threshold of 30 filters out thousands of obscure cross-border streams and
# minor border lakes (e.g. small Norway/Sweden border lakes) while retaining
# all major international water bodies.
_WD_WATERBODY_MIN_SITELINKS = 30

# Minimum Wikipedia sitelinks for per-country water body queries (GeoWikiCountryWaters).
# Lower than the international threshold since we scope to one country and want
# nationally significant bodies (e.g. Amundsen Gulf for Canada) that wouldn't
# clear the global 30-sitelinks bar.
_WD_COUNTRY_WATERS_MIN_SITELINKS = 5

# Explicit sea subtypes used in GeoWikiWaterbody (avoids expensive P279* traversal).
# Covers the main Wikidata sea subclasses; using direct P31 VALUES is much faster
# on WDQS than ?item wdt:P31/wdt:P279* wd:Q165.
_WD_SEA_TYPES = [
    "Q165",       # sea (generic — e.g. Persian Gulf)
    "Q204894",    # marginal sea (e.g. Baltic, North Sea)
    "Q2578218",   # inland sea (e.g. Hudson Bay)
    "Q12734855",  # epicontinental sea (e.g. Hudson Bay also)
    "Q1973404",   # adjacent sea (e.g. Mediterranean Sea)
    "Q986177",    # mediterranean-type sea (enclosed; e.g. Gulf of Mexico)
]

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
    # US
    "Q1474662":  "Q797",   # Unorganized Borough of Alaska → Alaska
    "Q13188841": "Q99",    # City and County of San Francisco entity → California
    # Canada — Ontario (Q1904)
    "Q21070830": "Q1904",  # Lake Helen 53A Indian Reserve → Ontario
    "Q22541508": "Q1904",  # Lake Helen 53A (alternate QID) → Ontario
    "Q22360801": "Q1904",  # Red Rock 53 Indian Reserve → Ontario
    "Q22364531": "Q1904",  # Sucker Creek Indian Reserve No. 23 → Ontario
    "Q22494407": "Q1904",  # Fort William Indian Reserve 52 → Ontario
    "Q22612246": "Q1904",  # Whitesand Indian Reserve → Ontario
    "Q22614638": "Q1904",  # M'Chigeeng Indian Reserve 22 → Ontario
    "Q22627721": "Q1904",  # Garden River Indian Reserve 14 → Ontario
    "Q7999949":  "Q1904",  # Wikwemikong Unceded Indian Reserve 26 → Ontario
    "Q382508":   "Q1904",  # Muskoka District Municipality → Ontario
    "Q953088":   "Q1904",  # Essex County → Ontario
    "Q991601":   "Q1904",  # Bruce County → Ontario
    "Q1406913":  "Q1904",  # Lambton County → Ontario
    "Q1436530":  "Q1904",  # Nipissing District → Ontario
    "Q1588807":  "Q1904",  # Hastings County → Ontario
    "Q1638386":  "Q1904",  # Kenora District → Ontario
    "Q1798108":  "Q1904",  # Parry Sound District → Ontario
    "Q1931946":  "Q1904",  # Middlesex County → Ontario
    "Q2140691":  "Q1904",  # Algoma District → Ontario
    # Canada — Quebec (Q176)
    "Q222948":   "Q176",   # Outaouais → Quebec
    "Q321529":   "Q176",   # Abitibi-Témiscamingue → Quebec
    "Q615914":   "Q176",   # Saguenay–Lac-Saint-Jean → Quebec
    "Q669673":   "Q176",   # Eeyou Istchee → Quebec
    "Q728139":   "Q176",   # Gaspésie–Îles-de-la-Madeleine → Quebec
    "Q764294":   "Q176",   # Centre-du-Québec → Quebec
    "Q927561":   "Q176",   # Nord-du-Québec → Quebec
    "Q1124109":  "Q176",   # Côte-Nord → Quebec
    "Q3285285":  "Q176",   # Manawan → Quebec
    "Q3529001":  "Q176",   # Timiskaming (QC region) → Quebec
    # Canada — British Columbia (Q1973)
    "Q116187917": "Q1973", # Yale Division Yale Land District → BC
    "Q1138212":  "Q1973",  # Cowichan Valley Regional District → BC
    "Q1552137":  "Q1973",  # Strathcona Regional District → BC
    "Q2095362":  "Q1973",  # Regional District of Nanaimo → BC
    "Q2138250":  "Q1973",  # Regional District of Kitimat-Stikine → BC
    "Q5600798":  "Q1973",  # Greater Vancouver → BC
    "Q6475523":  "Q1973",  # Lake Cowichan → BC
    # Canada — New Brunswick (Q1965)
    "Q2138":     "Q1965",  # (St. Mary's FN secondary P131) → New Brunswick
    "Q2542413":  "Q1965",  # Kent County → New Brunswick
    "Q3365804":  "Q1965",  # Dalhousie Parish → New Brunswick
    "Q3365810":  "Q1965",  # Douglas Parish → New Brunswick
    "Q937673":   "Q1965",  # Northumberland County → New Brunswick
    # Canada — Northwest Territories (Q2007)
    "Q338686":   "Q2007",  # Inuvik → Northwest Territories
    "Q420187":   "Q2007",  # Aklavik → Northwest Territories
    "Q1014088":  "Q2007",  # Tsiigehtchic → Northwest Territories
    "Q1014119":  "Q2007",  # Fort McPherson → Northwest Territories
    # Canada — Manitoba (Q1948)
    "Q48789915": "Q1948",  # Unorganized Division No. 19 → Manitoba
}

# Direct ADM2 QID → ADM1 QID overrides for entries where Wikidata returned no
# P131 at all (adm1_qid empty).  Add entries whenever an entry appears as an
# orphan and Wikidata lacks a P131 link.
_ADM2_NO_PARENT_OVERRIDES: dict[str, str] = {
    # US
    "Q25479087": "Q1370",  # City of Suffolk, Virginia → Virginia
    # Canada — British Columbia (Q1973)
    "Q2382930":  "Q1973",  # T'sou-ke Nation → BC
    "Q2457355":  "Q1973",  # Tsartlip First Nation → BC
    "Q5028135":  "Q1973",  # Wei Wai Kum First Nation → BC
    "Q5149748":  "Q1973",  # ʔakisq̓nuk First Nation → BC
    "Q5623217":  "Q1973",  # Gwa'Sala-'Nakwaxda'xw First Nation → BC
    "Q5674878":  "Q1973",  # Gitga'at First Nation → BC
    "Q6322485":  "Q1973",  # K'ómoks First Nation → BC
    "Q6417896":  "Q1973",  # Kitasoo/Xaixais First Nation → BC
    "Q6450195":  "Q1973",  # Kwantlen First Nation → BC
    "Q6693555":  "Q1973",  # Lower Kootenay First Nation → BC
    "Q7030489":  "Q1973",  # Nicomen First Nation → BC
    "Q7590342":  "Q1973",  # ʔaq̓am → BC
    "Q11708752": "Q1973",  # Heiltsuk Nation → BC
    "Q12635666": "Q1973",  # Lytton First Nation → BC
    "Q16212986": "Q1973",  # Mamalilikulla-Qwe'Qwa'Sot'Em First Nation → BC
    "Q17027468": "Q1973",  # Wet'suwet'en First Nation → BC
    "Q2621606":  "Q1973",  # Esquimalt First Nation → BC
    # Canada — Alberta (Q1951)
    "Q123033":   "Q1951",  # Tsuut'ina Nation → Alberta
    "Q5471652":  "Q1951",  # Fort McMurray First Nation → Alberta
    "Q7132507":  "Q1951",  # Papaschase → Alberta
    "Q16242678": "Q1951",  # Michel Band → Alberta
    # Canada — Ontario (Q1904)
    "Q5101691":  "Q1904",  # Chippewas of Nawash Unceded First Nation → Ontario
    "Q15256001": "Q1904",  # Moose Cree First Nation → Ontario
    "Q16212442": "Q1904",  # Lac des Mille Lacs First Nation → Ontario
    "Q20880558": "Q1904",  # Bearfoot Onondaga First Nation → Ontario
    # Canada — Quebec (Q176)
    "Q27655424": "Q176",   # Oujé-Bougoumou Cree Nation → Quebec
    "Q85792671": "Q176",   # Pekuakamiulnuatsh First Nation (duplicate item) → Quebec
    # Canada — Manitoba (Q1948)
    "Q7060844":  "Q1948",  # Norway House Cree Nation → Manitoba
    "Q15262579": "Q1948",  # (unlabeled First Nations band in Manitoba) → Manitoba
}

# Band-QID → province-QID overrides for First Nations bands whose Wikidata P131
# does not resolve through _ADM2_INTERMEDIATE_PARENT_QIDS or _ADM2_NO_PARENT_OVERRIDES.
# Extend whenever _build_first_nations_pvs prints orphan warnings.
# These will be contributed back to Wikidata upstream.
_FN_BAND_ADM1_OVERRIDES: dict[str, str] = {
    # Canada — Ontario (Q1904)
    "Q4661506": "Q1904",   # Aamjiwnaang First Nation → Ontario
}

# Water-body-QID → province/territory-QID overrides for country-waters entries
# whose Wikidata P131 chain does not resolve to a province (adm_qid points to a
# municipality, international region, or is absent).
# Extend whenever _build_country_waters_yaml logs "country fallback" for an entry.
# These will be contributed back to Wikidata upstream.
_CW_WATER_OVERRIDES: dict[str, str] = {
    # Canada — Quebec (Q176)
    "Q1061490":  "Q176",   # Champlain Sea (prehistoric; primarily QC lowlands)
    "Q22364469": "Q176",   # Lac Cacaoui (Côte-Nord, QC)
    "Q3214781":  "Q176",   # Lac Cameron (Les Laurentides, QC)
    "Q3215007":  "Q176",   # Lac Leamy (Gatineau, QC)
    "Q3215091":  "Q176",   # Lac Nicolet (Centre-du-Québec)
    "Q3215214":  "Q176",   # Lac Stukely (Estrie, QC)
    "Q3215277":  "Q176",   # Wapizagonke Lake (Mauricie, QC)
    "Q3215671":  "Q176",   # Lac des Nations (Sherbrooke, QC)
    "Q3377256":  "Q176",   # Petit Lac Nominingue (Laurentides, QC)
    "Q15303027": "Q176",   # Coaticook River (Estrie, QC; source in Vermont)
    "Q16025268": "Q176",   # North Branch Missisquoi River (Estrie, QC; flows into Vermont)
    # Canada — Nunavut (Q2023)
    "Q1285117":  "Q2023",  # Lancaster Sound (Qikiqtaaluk Region, NU)
    "Q243125":   "Q2023",  # Lincoln Sea (northern Ellesmere Island, NU)
    "Q1932718":  "Q2023",  # Strait of Anián (mythical NW Passage; Wikidata coords in NU)
    # Canada — Nova Scotia (Q1952)
    "Q876470":   "Q1952",  # Gulf of Maine (borders NB and NS; NS has more coastline)
}

# Maps ISO alpha-2 scope → {province_qid → (grouping_qid, en_label)}.
# Provides "First Nations in [Province/Territory]" (or equivalent) Wikidata
# items as intermediate grouping nodes between a province and its individual
# First Nation bands in the GeoWikiAdm2 enum.
# Provinces/territories handled by _INDIGENOUS_REGIONAL_NODES (NL, NS) or
# lacking a dedicated item entirely (NT, NU) are absent from this dict.
_INDIGENOUS_ADM1_GROUPS: dict[str, dict[str, tuple]] = {
    "CA": {
        "Q1951": ("Q5453438",    "First Nations in Alberta"),
        "Q1973": ("Q5453439",    "First Nations in British Columbia"),
        "Q1948": ("Q5453440",    "First Nations in Manitoba"),
        "Q1965": ("Q3402267",    "First Nations in New Brunswick"),
        # Q2003 (NL) and Q1952 (NS): handled via _INDIGENOUS_REGIONAL_NODES below
        "Q2007": ("Q8460333",    "First Nations in the Northwest Territories"),
        # Q2023 (NU): no dedicated item — bands fall directly under province
        "Q1904": ("Q5453441",    "First Nations in Ontario"),
        "Q1978": ("Q122809634",  "First Nations in Prince Edward Island"),
        "Q176":  ("Q2872543",    "Indigenous peoples in Quebec"),
        "Q1989": ("Q5453442",    "First Nations in Saskatchewan"),
        "Q2009": ("Q4200690",    "Indigenous peoples of Yukon"),
    },
}

# Maps ISO alpha-2 scope → list of multi-province regional grouping specs.
# When top_level is True, the group_qid node is inserted with no is_a (a sibling
# of "Province of Canada" in the rendered list).  Bands from all adm1_qids in the
# spec are placed under group_qid.
# When top_level is absent/False, the legacy two-level hierarchy is used:
#   regional_node (is_a=primary_adm1) → group_node → indigenous bands
# adm1_qids: set of province QIDs whose indigenous bands are routed to this spec.
# group_qid / group_label: the grouping node (e.g. "First Nations in Atlantic Canada").
_INDIGENOUS_REGIONAL_NODES: dict[str, list] = {
    "CA": [
        {
            # NL (Q2003) and NS (Q1952) both lack a province-specific grouping item.
            # Their bands are placed under "First Nations in Atlantic Canada" (Q5453437)
            # which appears as a top-level sibling of "Province of Canada" in the enum.
            "top_level":   True,
            "adm1_qids":   {"Q2003", "Q1952"},  # Newfoundland & Labrador and Nova Scotia
            "group_qid":   "Q5453437",
            "group_label": "First Nations in Atlantic Canada",
        },
    ],
}

# Maps ISO alpha-2 country code → Wikidata P31 QIDs for indigenous administrative
# entities at approximately the ADM2 level (bands, tribes, iwi, etc.).
# Used by _fetch_indigenous_entities when config has include_indigenous: true.
# Extend this table as more countries are verified.  Counts reflect Wikidata as
# of November 2026; validate with: SELECT (COUNT(?i) AS ?n) WHERE { ?i wdt:P17
# wd:{country_qid} ; wdt:P31 wd:{type_qid} . }
_INDIGENOUS_P31_BY_COUNTRY: dict[str, list[str]] = {
    "CA": ["Q2882257"],   # First Nation band                    (~620)
    "US": ["Q7840353"],   # federally recognized Native American tribe (~451)
    "NZ": ["Q1676081"],   # iwi (Māori)                          (~95)
    # AU: Q108750500 (Aboriginal and Torres Strait Islander corporation)
    #     has only ~22 Wikidata entries — too sparse; revisit when coverage improves.
    # GL: Inuit community types not yet verified.
    # NO/SE/FI: Sámi siida/village types not yet verified.
}

# Maps ISO alpha-2 → (top_level_qid, en_label) for each country's First Nations root node.
# Used by _build_first_nations_pvs and _build_first_nations_yaml.
_INDIGENOUS_TOP_BY_COUNTRY: dict[str, tuple[str, str]] = {
    "CA": ("Q392316", "First Nations"),
    "US": ("Q9174982", "Native Americans"),
    "NZ": ("Q1198765", "Māori people"),
}

# Short description for each country's First Nations enum (used as enum-level description).
_INDIGENOUS_ENUM_DESC_BY_COUNTRY: dict[str, str] = {
    "CA": "Indigenous peoples in Canada who are neither Inuit nor Métis",
    "US": "Federally recognized Native American tribes in the United States",
    "NZ": "Indigenous Polynesian people of mainland New Zealand",
}

# Shorthand regex: GeoWiki_countries, GeoWiki_ADM1, GeoWiki_ADM1_US, GeoWiki_ADM2_CA, GeoWiki_Settlement_NL
_SHORTHAND_RE = re.compile(
    r'^GeoWiki_(?:(countries|ADM1)|(ADM3ADM4|ADM[1-4]|Settlement|CountryWaters|FirstNations)_([A-Za-z]{2}))$',
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


def _fetch_wikidata_waterbodies(locales=None, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Query Wikidata for international water bodies, excluding oceans (GeoWikiOcean).

    Included:
      - Lakes (P31=Q23397) with P17 spanning two or more countries
        (e.g. Great Lakes shared by US and Canada, Caspian Sea, Lake Victoria)
      - Rivers (P31=Q4022) with P17 spanning two or more countries
        (e.g. Rhine, Danube, Mekong, Nile)
      - Seas (P31 in _WD_SEA_TYPES: sea, marginal sea, inland sea, etc.) with no
        P17 or P17 spanning two or more countries (e.g. Mediterranean, Caribbean,
        Baltic Sea).  Single-country seas like the Sea of Marmara (Turkey) are
        excluded.
      - Gulfs (Q1322134), straits (Q37901), bays (Q39594) with no P17 or P17
        spanning two or more countries (e.g. Strait of Gibraltar, Gulf of Oman).
        Single-country bodies like Amundsen Gulf (Canada) or Tokyo Bay (Japan)
        are excluded.

    All queries require at least _WD_WATERBODY_MIN_SITELINKS Wikipedia sitelinks,
    filtering out the thousands of minor cross-border streams and border lakes
    (e.g. the many small Norway/Sweden border lakes) that have P17 set for both
    countries but are too local to be useful as sampling region identifiers.

    Items with P31=Q9430 (ocean) are excluded because they appear in GeoWikiOcean.
    Historical items (P576 set) are excluded.

    Implementation note: the original single 6-arm UNION query reliably timed out
    on WDQS due to the cross-product join on P17 for lakes and rivers combined with
    the sitelinks filter.  The function now runs four separate, simpler queries and
    merges the results in Python.  Each individual query completes well within the
    60-second timeout; failures on any one sub-query are logged as warnings rather
    than propagating as exceptions so that partial results are still returned.

    Returns list of dicts: qid, labels, desc, gnid.
    """
    locales = locales or ["en"]
    label_vars      = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
        f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )
    tail = f"""\
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
  OPTIONAL {{ ?item wdt:P1566 ?gnid . }}
  OPTIONAL {{ ?item wdt:P30 ?continent . }}
}}
"""
    common_filters = f"""\
  ?item wikibase:sitelinks ?sl .
  FILTER(?sl >= {_WD_WATERBODY_MIN_SITELINKS})
  FILTER NOT EXISTS {{ ?item wdt:P31 wd:{_WD_CLASS_OCEAN} . }}
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
"""
    sea_values = " ".join(f"wd:{q}" for q in _WD_SEA_TYPES)
    gsb_values = (f"wd:{_WD_CLASS_GULF} wd:{_WD_CLASS_STRAIT} wd:{_WD_CLASS_BAY}")

    # Query 1: international lakes (P17 spans 2+ countries)
    q_lakes = f"""
SELECT DISTINCT ?item {label_vars} ?desc ?gnid ?continent WHERE {{
  ?item wdt:P31 wd:{_WD_CLASS_LAKE} .
  ?item wdt:P17 ?c1 . ?item wdt:P17 ?c2 . FILTER(?c1 != ?c2)
{common_filters}{tail}"""

    # Query 2: international rivers (P17 spans 2+ countries)
    q_rivers = f"""
SELECT DISTINCT ?item {label_vars} ?desc ?gnid ?continent WHERE {{
  ?item wdt:P31 wd:{_WD_CLASS_RIVER} .
  ?item wdt:P17 ?c1 . ?item wdt:P17 ?c2 . FILTER(?c1 != ?c2)
{common_filters}{tail}"""

    # Query 3: seas — multi-country P17 or no P17 (single-country excluded)
    q_seas = f"""
SELECT DISTINCT ?item {label_vars} ?desc ?gnid ?continent WHERE {{
  VALUES ?sea_type {{ {sea_values} }}
  ?item wdt:P31 ?sea_type .
  {{ ?item wdt:P17 ?cs1 . ?item wdt:P17 ?cs2 . FILTER(?cs1 != ?cs2) }}
  UNION
  {{ FILTER NOT EXISTS {{ ?item wdt:P17 ?cs0 . }} }}
{common_filters}{tail}"""

    # Query 4: gulfs/straits/bays — multi-country P17 or no P17
    q_gsb = f"""
SELECT DISTINCT ?item {label_vars} ?desc ?gnid ?continent WHERE {{
  VALUES ?gsb_type {{ {gsb_values} }}
  ?item wdt:P31 ?gsb_type .
  {{ ?item wdt:P17 ?cg1 . ?item wdt:P17 ?cg2 . FILTER(?cg1 != ?cg2) }}
  UNION
  {{ FILTER NOT EXISTS {{ ?item wdt:P17 ?cg0 . }} }}
{common_filters}{tail}"""

    print(f"  Querying Wikidata for international waterbodies "
          f"(sitelinks >= {_WD_WATERBODY_MIN_SITELINKS}) ...")

    seen = {}   # qid → row  (deduplicates across sub-queries)
    sub_queries = [
        ("lakes",             q_lakes),
        ("rivers",            q_rivers),
        ("seas",              q_seas),
        ("gulfs/straits/bays", q_gsb),
    ]
    for label, sparql in sub_queries:
        try:
            rows = _sparql_query(sparql, timeout=60, endpoint=endpoint, use_post=use_post)
            for row in rows:
                qid = _qid_from_uri(row.get("item", {}).get("value", ""))
                if qid and qid not in seen:
                    seen[qid] = row
            print(f"    {label}: {len(rows)} results (running total {len(seen)})")
        except Exception as exc:
            print(f"  Warning: waterbody sub-query '{label}' failed: {exc}",
                  file=sys.stderr)

    _canonical_cont = set(_GN_CONT_CODE_TO_WD_QID.values())

    waterbodies = []
    for qid, row in seen.items():
        gnid   = row.get("gnid",   {}).get("value", "")
        labels = {
            lang: row[f"label_{lang}"]["value"]
            for lang in locales if row.get(f"label_{lang}", {}).get("value")
        }
        desc = row.get("desc", {}).get("value", "")
        raw_cont = _qid_from_uri(row.get("continent", {}).get("value", ""))
        continent_qid = raw_cont if raw_cont in _canonical_cont else ""
        waterbodies.append({"qid": qid, "labels": labels, "desc": desc, "gnid": gnid,
                            "continent_qid": continent_qid})

    with_cont = sum(1 for w in waterbodies if w["continent_qid"])
    print(f"    Found {len(waterbodies)} international waterbodies "
          f"({with_cont} with P30 continent).")
    return waterbodies


def _fetch_wikidata_country_waters(country_qid, locales=None,
                                    endpoint=_WDQS_ENDPOINT, use_post=False):
    """Query Wikidata for water bodies associated with a single country.

    Returns all water bodies where wdt:P17 = country_qid, covering:
      - Seas (_WD_SEA_TYPES): sea, marginal sea, inland sea, adjacent sea, etc.
      - Gulfs (Q1322134), straits (Q37901), bays (Q39594)
      - Lakes (Q23397) — includes inland seas recognized as lakes (e.g. Caspian)
      - Rivers (Q4022)
    All items must have at least _WD_COUNTRY_WATERS_MIN_SITELINKS Wikipedia
    sitelinks.  Oceans (P31=Q9430) and historical items (P576) are excluded.

    A body shared with another country (e.g. Lake Ontario for both CA and US)
    will appear in both country enums; duplication is intentional.

    Returns list of dicts: qid, labels, desc, gnid.
    """
    locales = locales or ["en"]
    label_vars      = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
        f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )

    all_water_types = " ".join(
        f"wd:{q}" for q in _WD_SEA_TYPES + [
            _WD_CLASS_GULF, _WD_CLASS_STRAIT, _WD_CLASS_BAY,
            _WD_CLASS_LAKE, _WD_CLASS_RIVER,
        ]
    )

    sparql = f"""
SELECT DISTINCT ?item {label_vars} ?desc ?gnid ?adm WHERE {{
  VALUES ?wtype {{ {all_water_types} }}
  ?item wdt:P31 ?wtype .
  ?item wdt:P17 wd:{country_qid} .
  ?item wikibase:sitelinks ?sl .
  FILTER(?sl >= {_WD_COUNTRY_WATERS_MIN_SITELINKS})
  FILTER NOT EXISTS {{ ?item wdt:P31 wd:{_WD_CLASS_OCEAN} . }}
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
  OPTIONAL {{ ?item wdt:P1566 ?gnid . }}
  OPTIONAL {{ ?item wdt:P131 ?adm . }}
}}
"""
    print(f"  Querying Wikidata for country waters "
          f"(wd:{country_qid}, sitelinks >= {_WD_COUNTRY_WATERS_MIN_SITELINKS}) ...")
    rows = _sparql_query(sparql, timeout=120, endpoint=endpoint, use_post=use_post)

    entries = []
    seen = set()
    for row in rows:
        qid = _qid_from_uri(row.get("item", {}).get("value", ""))
        if not qid or qid in seen:
            continue
        seen.add(qid)
        gnid    = row.get("gnid", {}).get("value", "")
        adm_qid = _qid_from_uri(row.get("adm",  {}).get("value", ""))
        labels  = {
            lang: row[f"label_{lang}"]["value"]
            for lang in locales if row.get(f"label_{lang}", {}).get("value")
        }
        desc = row.get("desc", {}).get("value", "")
        entries.append({"qid": qid, "labels": labels, "desc": desc, "gnid": gnid,
                        "adm_qid": adm_qid})

    print(f"    Found {len(entries)} water bodies for wd:{country_qid}.")

    # Supplemental batch query: fetch ALL P131 values + P1566/P518 qualifier values
    # per water body.  Used in _build_country_waters_yaml to populate mixins for
    # bodies that span multiple provinces (e.g. a lake straddling two provinces).
    all_qids = [e["qid"] for e in entries if e.get("qid")]
    if all_qids:
        multi_adm = _fetch_waterbody_multi_adm(all_qids, endpoint, use_post)
        for entry in entries:
            extras = multi_adm.get(entry["qid"], [])
            if extras:
                entry["extra_adm_qids"] = extras

    return entries


def _fetch_waterbody_multi_adm(qids, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Batch-query all P131 values and P1566/P518 qualifier values for water bodies.

    For each QID returns all candidate administrative entity QIDs from:
      - wdt:P131  (located in administrative territorial entity — may be multiple)
      - p:P1566/pq:P518  (GeoNames ID qualified with "applies to part" = province)

    The P1566+P518 pattern is used by cross-boundary features (e.g. a lake
    straddling two provinces has two P1566 statements each qualified with the
    respective province).  P131 may also return multiple values for such features.

    Returns dict: qid → list of candidate administrative QIDs (deduplicated).
    """
    if not qids:
        return {}

    result: defaultdict = defaultdict(list)
    seen_pairs: set = set()
    batch_size = 100

    for i in range(0, len(qids), batch_size):
        batch  = qids[i : i + batch_size]
        values = " ".join(f"wd:{q}" for q in batch)
        sparql = f"""
SELECT ?item ?adm WHERE {{
  VALUES ?item {{ {values} }}
  {{
    ?item wdt:P131 ?adm .
  }} UNION {{
    ?item p:P1566 ?stmt .
    ?stmt pq:P518 ?adm .
  }}
}}
"""
        try:
            rows = _sparql_query(sparql, timeout=60, endpoint=endpoint, use_post=use_post)
        except Exception as exc:
            print(f"  Warning: multi-adm batch {i // batch_size + 1} failed: {exc}",
                  file=sys.stderr)
            continue

        for row in rows:
            qid = _qid_from_uri(row.get("item", {}).get("value", ""))
            adm = _qid_from_uri(row.get("adm",  {}).get("value", ""))
            if qid and adm and (qid, adm) not in seen_pairs:
                seen_pairs.add((qid, adm))
                result[qid].append(adm)

    n_multi = sum(1 for v in result.values() if len(v) > 1)
    print(f"    Multi-adm: {len(result)} water bodies have P131/P1566+P518 data "
          f"({n_multi} with >1 candidate).")
    return dict(result)


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

    Produces five separate enums, each sorted alphabetically by title:
      GeoWikiCountry         — sovereign states (P31=Q3624078 or Q15634554)
      GeoWikiCountryExtended — dependent territories/special regions that have
                               their own ISO alpha-2 code but are not sovereign
                               (P17 for parent, P31 for type); inherits
                               GeoWikiCountry so territory is_a links resolve
                               to inherited sovereign state entries
      GeoWikiContinent       — continents (P31=Q5107)
      GeoWikiOcean           — oceans (P31=Q9430)
      GeoWikiWaterbody       — international water bodies (lakes/rivers with P17
                               spanning 2+ countries; seas, gulfs, straits, bays
                               with 30+ Wikipedia sitelinks); inherits GeoWikiOcean

    Code = Wikidata QID; meaning = wd:{QID};
    exact_mappings = [gnf:{gnid}, iso:{alpha2}] for countries/territories.

    Backward compatibility: if the cached payload pre-dates the is_sovereign
    field (all entries lack it), all countries are placed in GeoWikiCountry
    and GeoWikiCountryExtended is left empty.
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]

    countries    = data.get("countries",    [])
    continents   = data.get("continents",   [])
    oceans       = data.get("oceans",       [])
    waterbodies  = data.get("waterbodies",  [])

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

    # --- GeoWikiWaterbody enum (grouped by continent, inherits GeoWikiOcean) ---
    # Group waterbodies by continent_qid; bodies with no continent go last.
    wb_by_cont: defaultdict = defaultdict(list)
    for _item in waterbodies:
        wb_by_cont[_item.get("continent_qid", "")].append(_item)
    _wb_unassigned = wb_by_cont.pop("", [])

    # Reuse _cont_sort from the country section above (already defined).
    continents_wb = sorted(wb_by_cont.keys(), key=_cont_sort)

    def _wb_sssom(qid, gnid, lbl):
        if gnid:
            sssom_rows.append({
                "subject_id":           f"wd:{qid}",
                "predicate_id":         "skos:exactMatch",
                "object_id":            f"gnf:{gnid}",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":        lbl,
                "object_label":         "",
            })

    waterbody_pvs = {}
    for _cont_qid in continents_wb:
        _cont = cont_by_qid.get(_cont_qid, {})
        # Add continent header with meaning so is_a chain resolves
        waterbody_pvs[_cont_qid] = {
            "title":   normalize_text(_lbl(_cont)) if _cont else _cont_qid,
            "meaning": f"wd:{_cont_qid}",
        }
        for _wbitem in sorted(wb_by_cont[_cont_qid],
                               key=lambda x: _title_sort_key(_lbl(x))):
            _qid  = _wbitem["qid"]
            _gnid = _wbitem.get("gnid", "")
            _lbl2 = normalize_text(_lbl(_wbitem))
            add_permissible_value(
                waterbody_pvs, _qid,
                title=_lbl2,
                meaning=f"wd:{_qid}",
                description=normalize_text(_wbitem.get("desc", "")) or None,
                is_a=_cont_qid,
            )
            _wb_sssom(_qid, _gnid, _lbl2)

    # Waterbodies with no continent assignment — append flat (no is_a)
    for _wbitem in sorted(_wb_unassigned, key=lambda x: _title_sort_key(_lbl(x))):
        _qid  = _wbitem["qid"]
        _gnid = _wbitem.get("gnid", "")
        _lbl2 = normalize_text(_lbl(_wbitem))
        add_permissible_value(
            waterbody_pvs, _qid,
            title=_lbl2,
            meaning=f"wd:{_qid}",
            description=normalize_text(_wbitem.get("desc", "")) or None,
        )
        _wb_sssom(_qid, _gnid, _lbl2)

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
            f"{len(continents)} continents, {len(oceans)} oceans, and "
            f"{len(waterbodies)} international waterbodies from Wikidata "
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
            "GeoWikiWaterbody": {
                "name":        "GeoWikiWaterbody",
                "title":       "International Waterbody",
                "inherits":    "GeoWikiOcean",
                "description": (
                    f"{len(waterbody_pvs)} international water bodies (lakes, rivers, "
                    "seas, gulfs, straits, bays) with "
                    f"{_WD_WATERBODY_MIN_SITELINKS}+ Wikipedia sitelinks, spanning "
                    "two or more countries, plus all oceans inherited from GeoWikiOcean. "
                    "Lakes and rivers require Wikidata P17 to reference at least two "
                    "countries; seas, gulfs, straits, and bays are international by "
                    "nature and need only meet the sitelinks threshold."
                ),
                "permissible_values": waterbody_pvs,
            },
        },
    )

    # Multilingual extensions
    for lang in locales:
        if lang == "en":
            continue
        locale_enums = {}
        for items_list, enum_key in [
            (sovereign_states,   "GeoWikiCountry"),
            (territories,        "GeoWikiCountryExtended"),
            (continents_sorted,  "GeoWikiContinent"),
            (oceans_sorted,      "GeoWikiOcean"),
            (waterbodies,        "GeoWikiWaterbody"),
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
    log_extraction("GeoWikiWaterbody",       count=len(waterbody_pvs))
    if sssom_rows:
        write_sssom_mappings(
            _GEOWIKI_SSSOM, sssom_rows, _SSSOM_PREFIXES,
            _GEOWIKI_SSSOM_ID, _GEOWIKI_SSSOM_DESC,
        )
        print(f"  SSSOM: {len(sssom_rows)} mappings written to {_GEOWIKI_SSSOM}")


def _build_country_waters_yaml(key, source, data, locales=None):
    """Write sources/{key}.yaml for GeoWikiCountryWaters scope.

    Produces a single enum GeoWikiCountryWaters{alpha2} with:
      - Country item as the root entry (no is_a) — used as fallback for bodies
        with no resolved ADM location.
      - Each water body sorted alphabetically with is_a pointing to its best
        resolved administrative location (ADM2 > ADM1 > country root).
      - ``inherits: GeoWikiAdm2{alpha2}`` (or ADM1) when ADM cache is available,
        so that the ADM ancestor QIDs used as is_a values are reachable.

    A body shared with another country (e.g. Lake Ontario for CA and US)
    appears in both country enums; duplication is intentional.
    """
    locales       = locales or ["en"]

    alpha2        = data.get("alpha2", "")
    country_qid   = data.get("country_qid", "")
    country_name  = data.get("country_name", alpha2)
    entries       = data.get("entries", [])

    # Resolve ADM groupings from cached ADM1/ADM2 data so that this logic
    # runs both on -f (after fetch) and on -c (from cached JSON).
    adm1_qids, adm2_qids, _adm2_to_adm1, inherits_enum = _load_adm_sets(alpha2)

    # Build ADM1 name → QID for description-based province fallback.
    # Many water bodies have P131 pointing to a district/municipality rather than
    # directly to a province; parsing the Wikidata description string resolves most.
    _adm1_name_to_qid: dict = {}
    try:
        _adm1_payload = _load_json(f"GeoWiki_ADM1_{alpha2.upper()}")
        for _e in (_adm1_payload.get("entries") or []):
            _qid = _e.get("qid", "")
            if not _qid:
                continue
            for _lbl_str in (_e.get("labels") or {}).values():
                if _lbl_str:
                    _adm1_name_to_qid[_lbl_str.lower()] = _qid
            if _e.get("name"):
                _adm1_name_to_qid[_e["name"].lower()] = _qid
    except Exception:
        pass

    # Build ADM2 name → QID for a secondary description-based fallback.
    # Handles descriptions that name a district/region (e.g. "Nord-du-Québec",
    # "Saguenay–Lac-Saint-Jean") rather than the province itself.
    _adm2_name_to_qid: dict = {}
    try:
        _adm2_payload = _load_json(f"GeoWiki_ADM2_{alpha2.upper()}")
        for _e in (_adm2_payload.get("entries") or []):
            _qid2 = _e.get("qid", "")
            if not _qid2 or _qid2 not in adm2_qids:
                continue
            for _lbl_str in (_e.get("labels") or {}).values():
                if _lbl_str:
                    _adm2_name_to_qid[_lbl_str.lower()] = _qid2
            if _e.get("name"):
                _adm2_name_to_qid[_e["name"].lower()] = _qid2
    except Exception:
        pass

    adm_adm2 = adm_adm1 = adm_desc = adm_override = adm_country = adm_multi = 0
    for _entry in entries:
        raw_adm   = _entry.get("adm_qid", "")
        extra_adm = _entry.get("extra_adm_qids", [])

        # Collect all province/territory QIDs from both the primary P131 value and
        # the supplemental P131+P1566/P518 values, in encounter order (primary first).
        _all_candidates = ([raw_adm] if raw_adm else []) + extra_adm
        _seen_c: set = set()
        _provinces: list = []
        for _c in _all_candidates:
            if _c in _seen_c:
                continue
            _seen_c.add(_c)
            if _c in adm1_qids:
                _provinces.append(_c)
            elif _c in adm2_qids:
                _provinces.append(_c)   # ADM2 as direct parent; ADM1 via inherits chain

        if _provinces:
            _entry["is_a_qid"]   = _provinces[0]
            _entry["mixin_qids"] = _provinces[1:]
            if _provinces[0] in adm2_qids:
                adm_adm2 += 1
            else:
                adm_adm1 += 1
            if len(_provinces) > 1:
                adm_multi += 1
        else:
            # Fallback: scan the Wikidata description for known province/territory names
            _prov = _desc_find_adm1(_entry.get("desc", "") or "", _adm1_name_to_qid)
            if not _prov:
                # Secondary fallback: try known ADM2 region names (e.g. "Nord-du-Québec")
                _prov = _desc_find_adm1(_entry.get("desc", "") or "", _adm2_name_to_qid)
            if _prov:
                _entry["is_a_qid"]   = _prov
                _entry["mixin_qids"] = []
                adm_desc += 1
            else:
                _override = _CW_WATER_OVERRIDES.get(_entry.get("qid", ""))
                if _override:
                    _entry["is_a_qid"]   = _override
                    _entry["mixin_qids"] = []
                    adm_override += 1
                else:
                    _entry["is_a_qid"]   = country_qid
                    _entry["mixin_qids"] = []
                    adm_country += 1
    if inherits_enum:
        multi_note = f", {adm_multi} \u2192 multi-province (mixins)" if adm_multi else ""
        ovr_note   = f", {adm_override} \u2192 override" if adm_override else ""
        print(f"    ADM resolved: {adm_adm2} \u2192 ADM2, {adm_adm1} \u2192 ADM1, "
              f"{adm_desc} \u2192 desc{ovr_note}, "
              f"{adm_country} \u2192 country fallback{multi_note}  (inherits: {inherits_enum})")
    else:
        print(f"    No ADM cache found for {alpha2}; all {adm_country} entries use country root.")

    # Collect the set of ADM QIDs that have at least one waterbody child.
    _used_adm_qids: set = set()
    for _e in entries:
        _ia = _e.get("is_a_qid", "")
        if _ia and _ia != country_qid:
            _used_adm_qids.add(_ia)
        for _mx in (_e.get("mixin_qids") or []):
            if _mx and _mx != country_qid:
                _used_adm_qids.add(_mx)

    # Load full ADM1 and ADM2 entry details so only needed nodes are inlined.
    _adm1_full_map: dict = {}  # qid → entry
    _adm2_full_map: dict = {}  # qid → entry
    try:
        for _e in (_load_json(f"GeoWiki_ADM1_{alpha2.upper()}").get("entries") or []):
            if _e.get("qid"):
                _adm1_full_map[_e["qid"]] = _e
    except Exception:
        pass
    try:
        for _e in (_load_json(f"GeoWiki_ADM2_{alpha2.upper()}").get("entries") or []):
            if _e.get("qid"):
                _adm2_full_map[_e["qid"]] = _e
    except Exception:
        pass

    # Determine the minimal ADM node sets; every used ADM2 also needs its ADM1 parent.
    _needed_adm2: set = _used_adm_qids & adm2_qids
    _needed_adm1: set = _used_adm_qids & adm1_qids
    for _q2 in _needed_adm2:
        _p1 = _adm2_to_adm1.get(_q2) or (_adm2_full_map.get(_q2) or {}).get("adm1_qid", "")
        if _p1 and _p1 in adm1_qids:
            _needed_adm1.add(_p1)

    if adm1_qids or adm2_qids:
        _pruned_a1 = len(adm1_qids) - len(_needed_adm1)
        _pruned_a2 = len(adm2_qids) - len(_needed_adm2)
        print(f"    Pruned {_pruned_a1} ADM1, {_pruned_a2} ADM2 entries "
              f"with no waterbody children "
              f"(kept {len(_needed_adm1)} ADM1, {len(_needed_adm2)} ADM2).")

    def _lbl(item): return _best_label(item, locales, "qid")

    enum_key = f"GeoWikiCountryWaters{alpha2}"

    # Country as root/fallback; ADM nodes and waters sorted alphabetically beneath it.
    pvs = {}
    if country_qid:
        pvs[country_qid] = {
            "title":   normalize_text(country_name),
            "meaning": f"wd:{country_qid}",
        }

    # Inline the needed ADM1 entries grouped by type label (replaces inherits directive).
    _adm1_by_type: defaultdict = defaultdict(list)
    for _q1 in _needed_adm1:
        _e1 = _adm1_full_map.get(_q1, {})
        _adm1_by_type[_e1.get("type_label", "")].append(_q1)

    _has_multi_type = sum(1 for _g in _adm1_by_type.values() if len(_g) >= 2) >= 2

    for _tl, _grp in sorted(_adm1_by_type.items(), key=lambda x: (-len(x[1]), x[0])):
        _add_hdr = _has_multi_type and len(_grp) >= 2 and _tl
        _hdr_code = (_tl[0].upper() + _tl[1:]) if _add_hdr else ""
        if _add_hdr and _hdr_code not in pvs:
            _tw = (_tl.split(" of ")[0].strip().capitalize()
                   if " of " in _tl else _tl.capitalize())
            _hdr_pv: dict = {"title": _tw + (f" of {country_name}" if country_name else "")}
            if _hdr_code in _ADM1_TYPE_HEADER_DESCRIPTIONS:
                _hdr_pv["description"] = _ADM1_TYPE_HEADER_DESCRIPTIONS[_hdr_code]
            pvs[_hdr_code] = _hdr_pv
        for _q1 in sorted(_grp, key=lambda q: _title_sort_key(
                _best_label(_adm1_full_map.get(q, {"qid": q}), locales, "qid"))):
            _e1 = _adm1_full_map.get(_q1, {"qid": _q1})
            _lbl1 = normalize_text(_best_label(_e1, locales, "qid"))
            _ia1 = _hdr_code if (_add_hdr and _hdr_code) else None
            add_permissible_value(
                pvs, _q1,
                title=_lbl1,
                meaning=f"wd:{_q1}",
                description=normalize_text(_e1.get("desc", "")) or None,
                is_a=_ia1,
            )

    # Inline the needed ADM2 entries.
    for _q2 in sorted(_needed_adm2, key=lambda q: _title_sort_key(
            _best_label(_adm2_full_map.get(q, {"qid": q}), locales, "qid"))):
        _e2 = _adm2_full_map.get(_q2, {"qid": _q2})
        _lbl2 = normalize_text(_best_label(_e2, locales, "qid"))
        _ia2 = _adm2_to_adm1.get(_q2) or _e2.get("adm1_qid") or country_qid
        if _ia2 not in pvs:
            # Non-standard ADM1 parent (e.g. "Northern Ontario") — try description
            _ia2 = _desc_find_adm1(_e2.get("desc", ""), _adm1_name_to_qid) or country_qid
            if _ia2 not in pvs:
                _ia2 = country_qid
        add_permissible_value(
            pvs, _q2,
            title=_lbl2,
            meaning=f"wd:{_q2}",
            description=normalize_text(_e2.get("desc", "")) or None,
            is_a=_ia2,
        )

    sssom_rows = []
    entries_sorted = sorted(entries, key=lambda x: _title_sort_key(_lbl(x)))
    for item in entries_sorted:
        qid    = item["qid"]
        gnid   = item.get("gnid", "")
        lbl    = normalize_text(_lbl(item))
        is_a       = item.get("is_a_qid") or (country_qid if country_qid else None)
        mixin_qids = item.get("mixin_qids") or []
        add_permissible_value(
            pvs, qid,
            title=lbl,
            meaning=f"wd:{qid}",
            description=normalize_text(item.get("desc", "")) or None,
            is_a=is_a,
            mixins=mixin_qids or None,
        )
        if gnid:
            sssom_rows.append({
                "subject_id":            f"wd:{qid}",
                "predicate_id":          "skos:exactMatch",
                "object_id":             f"gnf:{gnid}",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         lbl,
                "object_label":          "",
            })

    # Disambiguate entries that share an identical title within the same parent group.
    # Appends a short location hint from the Wikidata description in parentheses,
    # e.g. "Canada - Rivière du Loup" × 3 → "… (Louiseville)", "… (Rivière-du-Loup)",
    # "… (Saint-Georges)".
    _parent_title_map: defaultdict = defaultdict(list)
    for _pk, _pv in pvs.items():
        _parent_title_map[(_pv.get("is_a", ""), _pv.get("title", ""))].append(_pk)
    disambig_hints: dict = {}   # qid → hint string (populated for disambiguated entries)
    for (_par, _ttl), _dqids in _parent_title_map.items():
        if len(_dqids) <= 1:
            continue
        for _dq in _dqids:
            _hint = _desc_location_hint(pvs[_dq].get("description", "") or "")
            if _hint:
                pvs[_dq]["title"] = f"{_ttl} ({_hint})"
                disambig_hints[_dq] = _hint
    if disambig_hints:
        print(f"    Disambiguated {len(disambig_hints)} duplicate-titled water body entries.")

    # Build hierarchy description for the enum
    if _needed_adm1 or _needed_adm2:
        hier_note = (
            f"Water bodies are grouped by province/territory (ADM1) or "
            f"district/county (ADM2) via Wikidata P131 when available; "
            f"bodies with no resolved ADM location have is_a: {country_qid} (country root). "
        )
    else:
        hier_note = f"Country entry ({country_qid}) is the root; each water body has is_a: {country_qid}. "

    enum_def = {
        "name":        enum_key,
        "title":       f"Waters of {country_name}",
        "description": (
            f"{len(entries)} water bodies associated with {country_name}. "
            + hier_note
            + f"Seas, gulfs, straits, bays, lakes, and rivers "
            f"with {_WD_COUNTRY_WATERS_MIN_SITELINKS}+ Wikipedia sitelinks."
        ),
        "permissible_values": pvs,
    }
    # Only use inherits when no ADM cache is available (ADM nodes are inlined above).
    if inherits_enum and not (_needed_adm1 or _needed_adm2):
        enum_def["inherits"] = inherits_enum

    source_url = (source.get("reachable_from") or {}).get("source_ontology",
                                                           "https://www.wikidata.org/")
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"Wikidata Country Waters for {alpha2}",
        description=(
            f"{len(entries)} water bodies (seas, gulfs, straits, bays, lakes, rivers) "
            f"associated with {country_name} (wd:{country_qid}) from Wikidata. "
            f"Requires {_WD_COUNTRY_WATERS_MIN_SITELINKS}+ Wikipedia sitelinks. "
            "Bodies shared with other countries appear in each country's enum."
        ),
        license="CC0 1.0",
        prefixes=_SOURCE_PREFIXES,
        enums={enum_key: enum_def},
    )

    # Multilingual extensions
    for lang in locales:
        if lang == "en":
            continue
        lang_pvs = {}
        # ADM1 multilingual labels
        for _q1 in _needed_adm1:
            _e1 = _adm1_full_map.get(_q1, {})
            _l1 = (_e1.get("labels") or {}).get(lang, "")
            if not _l1:
                continue
            _default = normalize_text(_best_label(_e1, locales, "qid"))
            _foreign = normalize_text(_l1)
            if _foreign != _default:
                lang_pvs[_q1] = {"title": _foreign}
        # ADM2 multilingual labels
        for _q2 in _needed_adm2:
            _e2 = _adm2_full_map.get(_q2, {})
            _l2 = (_e2.get("labels") or {}).get(lang, "")
            if not _l2:
                continue
            _default = normalize_text(_best_label(_e2, locales, "qid"))
            _foreign = normalize_text(_l2)
            if _foreign != _default:
                lang_pvs[_q2] = {"title": _foreign}
        # Waterbody multilingual labels
        for item in entries_sorted:
            lbl  = item.get("labels", {}).get(lang, "")
            hint = disambig_hints.get(item["qid"], "")
            if not lbl:
                continue  # no translated label; EN PV (which already has hint) is used
            fr_title = normalize_text(lbl)
            if hint:
                fr_title = f"{fr_title} ({hint})"
            default = normalize_text(_lbl(item))
            if hint:
                default = f"{default} ({hint})"
            if fr_title != default:
                lang_pvs[item["qid"]] = {"title": fr_title}
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


def _pluralize(label):
    """Simple English pluralization for type labels (e.g. 'state' → 'states')."""
    if not label:
        return label
    if label.endswith("y") and len(label) > 1 and label[-2].lower() not in "aeiou":
        return label[:-1] + "ies"
    if label.endswith(("s", "sh", "ch", "x", "z")):
        return label + "es"
    return label + "s"


def _load_indigenous_from_adm2(alpha2, locales):
    """Load indigenous entries from the cached GeoWiki_ADM2_{alpha2} JSON.

    Returns a list of entry dicts with entity_type=="indigenous", or [] if the
    cache is absent or contains no indigenous entries.
    """
    adm2_key = f"GeoWiki_ADM2_{alpha2.upper()}"
    if not _entry_exists(adm2_key):
        return []
    try:
        payload = _load_json(adm2_key)
    except Exception:
        return []
    return [e for e in payload.get("entries", []) if e.get("entity_type") == "indigenous"]


def _build_adm1_yaml(key, source, data, locales=None, include_indigenous=False):
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

    # Optionally append indigenous (First Nations / Tribes / Iwi) hierarchy.
    # In ADM1: add top node + group nodes only; individual bands live in ADM2.
    if include_indigenous and scope != "global":
        _indig = _load_indigenous_from_adm2(scope, locales)
        if _indig:
            _build_first_nations_pvs(pvs, _indig, scope, locales,
                                     add_top_node=True, include_bands=False)

    n       = len(entries)
    n_match = sum(1 for e in entries if e.get("gnid"))
    pct     = 100 * n_match // n if n else 0
    print(f"  Crosswalk: {n_match}/{n} ADM1 entries ({pct}%) have GeoNames P1566 links")

    scope_label = f"for {scope}" if scope != "global" else "(global)"
    enum_key    = f"GeoWikiAdm1{scope}" if scope != "global" else "GeoWikiAdm1"
    source_url  = (source.get("reachable_from") or {}).get("source_ontology",
                                                             "https://www.wikidata.org/")
    enum_desc = (source.get("description") or
                 f"{n} first-order administrative divisions {scope_label} from Wikidata.")
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"Wikidata ADM1 {scope_label}",
        description=enum_desc,
        license="CC0 1.0",
        prefixes=_SOURCE_PREFIXES,
        enums={
            enum_key: {
                "name":        enum_key,
                "title":       enum_title,
                "description": enum_desc,
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
    """Query Wikidata for sovereign states, continents, oceans, and waterbodies.

    GeoNames countryInfo.txt is used as the authoritative continent source:
    for every country in GeoNames, the continent_qid is set from the GeoNames
    continent code (mapped to one of the 7 canonical Wikidata continent QIDs).
    Wikidata P30 can give sub-regional values (e.g. Q538 Melanesia instead of
    Q55643 Oceania), so GeoNames overrides it for entries it covers.  Countries
    not in GeoNames (e.g. Ascension, Sark, Tristan da Cunha) are left with an
    empty continent_qid, which _build_countries_yaml treats as sub-national
    entities and excludes from GeoWikiCountry.
    """
    countries    = _fetch_wikidata_countries(locales, endpoint, use_post)
    continents   = _fetch_wikidata_continents(locales, endpoint, use_post)
    oceans       = _fetch_wikidata_oceans(locales, endpoint, use_post)
    waterbodies  = _fetch_wikidata_waterbodies(locales, endpoint, use_post)

    # Fallback continent assignment via P17 (country) → country's continent_qid.
    # Most rivers/lakes/seas don't have P30 set directly in Wikidata, but they
    # have P17 (country), and the countries data already has continent_qid resolved
    # via GeoNames.  For multi-country bodies we take the most-frequent continent
    # among their P17 countries; ties broken by canonical continent sort order.
    _canonical_cont = set(_GN_CONT_CODE_TO_WD_QID.values())
    _country_to_cont = {c["qid"]: c.get("continent_qid", "")
                        for c in countries if c.get("continent_qid")}

    missing_cont = [w for w in waterbodies if not w.get("continent_qid")]
    if missing_cont:
        _batch = 80
        # Map waterbody QID → list of P17 country QIDs
        qid_to_p17_counts: dict[str, dict[str, int]] = {}
        for _i in range(0, len(missing_cont), _batch):
            _slice = missing_cont[_i:_i + _batch]
            _vals  = " ".join(f"wd:{w['qid']}" for w in _slice if w.get("qid"))
            if not _vals:
                continue
            _sparql = f"""
SELECT ?item ?country WHERE {{
  VALUES ?item {{ {_vals} }}
  ?item wdt:P17 ?country .
}}
"""
            try:
                _rows = _sparql_query(_sparql, timeout=30, endpoint=endpoint,
                                      use_post=use_post)
                for _row in _rows:
                    _qid     = _qid_from_uri(_row.get("item",    {}).get("value", ""))
                    _country = _qid_from_uri(_row.get("country", {}).get("value", ""))
                    if _qid and _country:
                        qid_to_p17_counts.setdefault(_qid, {})
                        _cont = _country_to_cont.get(_country, "")
                        if _cont:
                            qid_to_p17_counts[_qid][_cont] = \
                                qid_to_p17_counts[_qid].get(_cont, 0) + 1
            except Exception as _exc:
                print(f"  Warning: P17 continent batch failed: {_exc}", file=sys.stderr)

        filled_cont = 0
        for w in missing_cont:
            _qid = w.get("qid", "")
            counts = qid_to_p17_counts.get(_qid, {})
            if counts:
                # Pick continent with highest count; break ties by canonical order
                _canon_order = list(_GN_CONT_CODE_TO_WD_QID.values())
                best = max(counts, key=lambda q: (counts[q], -_canon_order.index(q)
                                                  if q in _canon_order else -99))
                w["continent_qid"] = best
                filled_cont += 1
        if filled_cont:
            print(f"    P17 continent fallback: filled continent for "
                  f"{filled_cont}/{len(missing_cont)} waterbodies via country lookup.")
        still_missing = sum(1 for w in waterbodies if not w.get("continent_qid"))
        if still_missing:
            print(f"    {still_missing} waterbodies have no P17 country with continent "
                  f"(will appear ungrouped).")

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
        "type":        "countries",
        "countries":   countries,
        "continents":  continents,
        "oceans":      oceans,
        "waterbodies": waterbodies,
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


def _load_adm_sets(alpha2):
    """Load ADM1 and ADM2 QID sets for *alpha2* from the shared GeoWiki.zip cache.

    Returns a 4-tuple:
        adm1_qids    — set of QID strings for first-order divisions
        adm2_qids    — set of QID strings for second-order divisions
        adm2_to_adm1 — dict {adm2_qid: adm1_qid} from the ADM2 entries
        inherits_enum — LinkML enum name to use in the CountryWaters enum's
                        ``inherits`` field (e.g. "GeoWikiAdm2CA"), or "" if
                        no ADM data is available.

    Returns empty structures if neither ADM cache entry is found.
    """
    alpha2 = alpha2.upper()
    adm1_qids: set  = set()
    adm2_qids: set  = set()
    adm2_to_adm1    = {}
    inherits_enum   = ""

    # ADM1 ----------------------------------------------------------------
    try:
        payload = _load_json(f"GeoWiki_ADM1_{alpha2}")
        for e in (payload.get("entries") or []):
            qid = e.get("qid", "")
            if qid:
                adm1_qids.add(qid)
        if adm1_qids:
            inherits_enum = f"GeoWikiAdm1{alpha2}"
    except (FileNotFoundError, Exception):
        pass

    # ADM2 ----------------------------------------------------------------
    try:
        payload = _load_json(f"GeoWiki_ADM2_{alpha2}")
        for e in (payload.get("entries") or []):
            qid      = e.get("qid",      "")
            adm1_qid = e.get("adm1_qid", "")
            if qid:
                adm2_qids.add(qid)
                if adm1_qid:
                    adm2_to_adm1[qid] = adm1_qid
        if adm2_qids:
            inherits_enum = f"GeoWikiAdm2{alpha2}"   # ADM2 inherits ADM1, so covers both
    except (FileNotFoundError, Exception):
        pass

    return adm1_qids, adm2_qids, adm2_to_adm1, inherits_enum


def _fetch_country_waters_payload(alpha2, locales, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Resolve alpha2 → Wikidata QID, then fetch water bodies for that country."""
    locales = locales or ["en"]

    country_sparql = f"""
SELECT ?item ?label WHERE {{
  ?item wdt:P297 "{alpha2.upper()}" .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
  OPTIONAL {{ ?item rdfs:label ?label . FILTER(LANG(?label) = "en") }}
}}
LIMIT 1
"""
    country_qid  = ""
    country_name = alpha2.upper()
    try:
        rows = _sparql_query(country_sparql, timeout=15, endpoint=endpoint, use_post=use_post)
        if rows:
            country_qid = _qid_from_uri(rows[0].get("item", {}).get("value", ""))
            lbl = rows[0].get("label", {}).get("value", "")
            if lbl:
                country_name = lbl
    except Exception as exc:
        print(f"  Warning: could not find country QID for {alpha2}: {exc}", file=sys.stderr)

    if not country_qid:
        print(f"  Warning: no Wikidata item found for ISO alpha-2 '{alpha2}'", file=sys.stderr)
        return {
            "type": "country_waters", "country_qid": "", "alpha2": alpha2.upper(),
            "country_name": alpha2.upper(), "entries": [],
        }

    entries = _fetch_wikidata_country_waters(country_qid, locales, endpoint, use_post)
    return {
        "type":         "country_waters",
        "country_qid":  country_qid,
        "alpha2":       alpha2.upper(),
        "country_name": country_name,
        "entries":      entries,
    }


def _desc_location_hint(desc: str) -> str:
    """Extract the most specific location phrase from a Wikidata description.

    Used to disambiguate water bodies that share an identical title and parent.

    Examples::
        "watercourse in Louiseville, Quebec, Canada"          → "Louiseville"
        "watercourse in Clearwater County, Alberta, Canada"   → "Clearwater County"
        "watercourse in Saskatchewan and Alberta in Canada"   → "Saskatchewan and Alberta"
        "watercourse in Capital Regional District, BC, Canada"→ "Capital Regional District"
    """
    if not desc:
        return ""
    # "in PLACE in Country" (no comma between PLACE and 'in Country')
    m = re.search(r'\bin\s+(.+?)\s+in\s+\w[\w\s]*$', desc, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # "in PLACE, [Province, Country]" — take everything before the first comma
    m = re.search(r'\bin\s+([^,]+)', desc, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _desc_find_adm1(desc: str, name_to_qid: dict) -> str:
    """Scan comma-separated parts of a Wikidata description for a known ADM1 name.

    Works right-to-left since the province/territory is typically the second-to-last
    segment (before the country name).  Case-insensitive.  Returns the matching
    QID on the first match, or "" if no match.

    Handles several description styles:
      "lake in Kenora District, Ontario, Canada"               → Q1904 (Ontario)
      "watercourse in Alberta, Canada"                         → Q1951 (Alberta)
      "river in Yukon Territory, British Columbia, and Alaska" → Q1973 (British Columbia)
      "watercourse in Ontario (Canada)"                        → Q1904 (Ontario)
      "branch of the Saguenay River (Quebec, Canada)"          → Q176  (Quebec)
      "lake crossed by the Chensagi River, Northwestern Quebec, Canada"  → Q176
      "lake in Kenora District, Ontario, Canada"               → Q1904
      "marginal sea of the Arctic Ocean"                       → ""
    """
    if not desc or not name_to_qid:
        return ""

    def _token_search(text: str) -> str:
        """Try all contiguous sub-sequences of cleaned tokens of *text* against name_to_qid."""
        _toks = re.sub(r'[^a-z0-9\s]', ' ', text.lower()).split()
        for _s in range(len(_toks)):
            for _e in range(len(_toks), _s, -1):
                _sub = " ".join(_toks[_s:_e])
                if len(_sub) > 2 and _sub in name_to_qid:
                    return name_to_qid[_sub]
        return ""

    # Protect commas inside parentheses before splitting
    # so "River (Quebec, Canada)" stays as one part during the right-to-left scan.
    _desc_norm = re.sub(r'\(([^)]*)\)',
                        lambda m: '(' + m.group(1).replace(',', '\x00') + ')', desc)
    for part in reversed(_desc_norm.split(",")):
        part = part.replace('\x00', ',').strip()
        part_lower = part.lower()
        # Strip leading "in " or "and " (e.g. " and Alaska" → "alaska")
        for prefix in ("in ", "and "):
            if part_lower.startswith(prefix):
                part_lower = part_lower[len(prefix):]
                break
        # 1. Direct match (also try with parenthetical suffix stripped,
        #    e.g. "Nord-du-Québec (Canada)" → "nord-du-québec")
        if part_lower in name_to_qid:
            return name_to_qid[part_lower]
        _pl_no_paren = re.sub(r'\s*\([^)]*\)', '', part_lower).strip()
        if _pl_no_paren != part_lower and _pl_no_paren in name_to_qid:
            return name_to_qid[_pl_no_paren]
        # 2. Content inside parentheses (e.g. "River (Quebec, Canada)" → "quebec")
        for _paren in re.findall(r'\(([^)]*)\)', part):
            for _pk in _paren.split(','):
                _pk = _pk.strip().lower()
                if _pk and _pk in name_to_qid:
                    return name_to_qid[_pk]
        # 3. Regex: extract tail after " in " — also retry with parentheses stripped
        _part_for_regex = part
        m = re.search(r'\bin\s+(\S[\w\s\-]*)$', _part_for_regex, re.IGNORECASE)
        if not m:
            _part_no_paren = re.sub(r'\s*\([^)]*\)', '', part).strip()
            if _part_no_paren != part:
                m = re.search(r'\bin\s+(\S[\w\s\-]*)$', _part_no_paren, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().lower()
            if candidate in name_to_qid:
                return name_to_qid[candidate]
            # Handle "X and Y" (e.g. "Ontario and Quebec") — try each token
            for _tok in re.split(r'\s+and\s+', candidate, flags=re.IGNORECASE):
                _tok = _tok.strip()
                if _tok in name_to_qid:
                    return name_to_qid[_tok]
            # Token-stripping: handles "Eastern Ontario", "Northern British Columbia", etc.
            _r = _token_search(candidate)
            if _r:
                return _r
        # 4. Token-stripping on the whole part as a last resort
        # (handles "Northwestern Quebec" as a direct comma-part with no "in" keyword)
        _r = _token_search(part_lower)
        if _r:
            return _r
    return ""


def _entity_type_hint(desc: str) -> str:
    """Extract the entity type (the part before 'in' or 'of') from a Wikidata description.

    Used to disambiguate administrative divisions that share a name and parent.

    Examples::
        "city in Estrie, Quebec, Canada"                      → "city"
        "parish municipality in Quebec, Canada"               → "parish municipality"
        "township municipality in Estrie, Quebec, Canada"     → "township municipality"
        "town of Estrie, Quebec, Canada"                      → "town"
    """
    if not desc:
        return ""
    m = re.match(r'^(.+?)\s+(?:in|of)\s+', desc.strip(), re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _fetch_geonames_adm1_rows(alpha2):
    """Fetch GeoNames admin1CodesASCII.txt and return rows for *alpha2*.

    Returns list of dicts: admin1_code (e.g. "12"), name, gnid.
    The admin1_code is the numeric/alphabetic code that appears as the second
    segment of compound_codes in GeoNames ADM2 data (e.g. "12" in "CA.12.xxx").
    """
    print(f"  Fetching GeoNames admin1CodesASCII.txt for {alpha2} ...")
    req = urllib.request.Request(_GEONAMES_ADM1_URL, headers=BROWSER_HEADERS)
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
            "admin1_code": compound[len(prefix):],
            "name":        parts[1].strip(),
            "gnid":        parts[3].strip(),
        })
    print(f"    Found {len(rows)} GeoNames ADM1 rows for {alpha2}.")
    return rows


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


def _fetch_indigenous_entities(country_qid, p31_types, locales=None,
                                endpoint=_WDQS_ENDPOINT, use_post=False):
    """Query Wikidata for indigenous entities (e.g. First Nations) in a country.

    Args:
        country_qid: Wikidata QID for the country (e.g. "Q16" for Canada).
        p31_types: list of Wikidata P31 type QIDs (e.g. ["Q2882257"] for Canadian First Nation bands).
        locales: list of language codes.

    Returns list of entry dicts compatible with _fetch_adm2_payload entries:
        {qid, gnid, name, compound_code, labels, desc, adm1_qid,
         country_qid, native_label, entity_type}
    """
    locales = locales or ["en"]
    if not country_qid or not p31_types:
        return []

    label_vars      = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
        f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )
    type_values = " ".join(f"wd:{t}" for t in p31_types)

    sparql = f"""
SELECT ?item ?gnid {label_vars} ?desc ?native_label ?adm1 WHERE {{
  ?item wdt:P17 wd:{country_qid} .
  ?item wdt:P31 ?type .
  VALUES ?type {{ {type_values} }}
  FILTER NOT EXISTS {{ ?item wdt:P576 ?end . }}
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
  OPTIONAL {{ ?item wdt:P1566 ?gnid . }}
  OPTIONAL {{ ?item wdt:P1705 ?native_label . }}
  OPTIONAL {{ ?item wdt:P131 ?adm1 . }}
}}
"""
    try:
        rows = _sparql_query(sparql, timeout=120, endpoint=endpoint, use_post=use_post)
    except Exception as exc:
        print(f"  Warning: indigenous entities query failed: {exc}", file=sys.stderr)
        return []

    entries: list = []
    seen: set = set()
    for row in rows:
        qid = _qid_from_uri(row.get("item", {}).get("value", ""))
        if not qid or qid in seen:
            continue
        seen.add(qid)
        gnid         = row.get("gnid", {}).get("value", "")
        adm1_uri     = row.get("adm1", {}).get("value", "")
        adm1_qid     = _qid_from_uri(adm1_uri) if adm1_uri else ""
        native_label = row.get("native_label", {}).get("value", "")
        labels       = {
            lang: row[f"label_{lang}"]["value"]
            for lang in locales if row.get(f"label_{lang}", {}).get("value")
        }
        name = labels.get("en") or labels.get(locales[0], "") or qid
        desc = row.get("desc", {}).get("value", "")
        entries.append({
            "gnid":          gnid,
            "name":          name,
            "compound_code": "",
            "qid":           qid,
            "labels":        labels,
            "desc":          desc,
            "adm1_qid":      adm1_qid,
            "country_qid":   country_qid,
            "native_label":  native_label,
            "entity_type":   "indigenous",
        })

    print(f"    Fetched {len(entries)} indigenous entities for {country_qid}.")
    return entries


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


def _fetch_adm2_payload(alpha2, locales, endpoint=_WDQS_ENDPOINT, use_post=False,
                        include_indigenous=False):
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

    # Fetch GeoNames ADM1 code → gnid mapping.  Stored in the payload so that
    # _build_adm2_yaml can resolve admin1 codes for GeoNames-only entries
    # (e.g. Yukon CA.12 / NWT CA.13) that have no Wikidata adm1_qid.
    gn_adm1_code_map: dict = {}
    try:
        for _row in _fetch_geonames_adm1_rows(alpha2):
            gn_adm1_code_map[_row["admin1_code"]] = _row["gnid"]
    except Exception as _exc:
        print(f"  Warning: could not fetch GeoNames ADM1 codes for {alpha2}: {_exc}",
              file=sys.stderr)

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

    # Optionally append indigenous entities (e.g. First Nations)
    if include_indigenous and country_qid:
        p31_types = _INDIGENOUS_P31_BY_COUNTRY.get(alpha2.upper(), [])
        if p31_types:
            print(f"  Fetching indigenous entities (P31 ∈ {p31_types}) for {alpha2} ...")
            indig_entries = _fetch_indigenous_entities(
                country_qid, p31_types, locales, endpoint, use_post
            )
            entries.extend(indig_entries)
        else:
            print(f"  Note: include_indigenous=true but no P31 types defined for {alpha2};"
                  f" add to _INDIGENOUS_P31_BY_COUNTRY to enable.",
                  file=sys.stderr)

    return {
        "type":             "adm2",
        "scope":            alpha2.upper(),
        "entries":          entries,
        "adm1_entries":     saved_adm1_entries,
        "country_name":     saved_country_name,
        "country_qid":      country_qid,
        "gn_adm1_code_map": gn_adm1_code_map,
    }


def _build_first_nations_pvs(pvs, indig_entries, alpha2, locales,
                             add_top_node=True, include_bands=True):
    """Populate *pvs* with a First Nations / Tribes / Iwi hierarchy.

    Parameters
    ----------
    add_top_node : bool
        When True (default, ADM1 case): Q392316 is added as a PV with no is_a,
        and group nodes have is_a=top_qid.
        When False (standalone GeoWikiFirstNations enum): Q392316 is NOT added
        as a PV (it becomes enum_uri on the enum), and group nodes have no is_a.
    include_bands : bool
        When True (default, standalone enum): individual band entries are emitted
        under each group node.
        When False (ADM1 case): only the top node and group nodes are emitted;
        bands are deferred to the ADM2 enum.

    Structure (for CA, add_top_node=True, include_bands=False — ADM1):
      top_qid  (Q392316 "First Nations") — no is_a
        regional group nodes  (is_a=top_qid)  — from _INDIGENOUS_REGIONAL_NODES
        province group nodes  (is_a=top_qid)  — from _INDIGENOUS_ADM1_GROUPS

    Structure (for CA, add_top_node=False, include_bands=True — standalone enum):
      regional group nodes  (no is_a)
        individual bands   (is_a=grp_qid)
      province group nodes  (no is_a)
        individual bands   (is_a=grp_qid)
      orphan bands          (no is_a)

    For countries not in _INDIGENOUS_ADM1_GROUPS (US, NZ): group by adm1_qid
    directly; each distinct adm1_qid becomes a node with is_a=top_qid (or no
    is_a when add_top_node=False).
    """
    scope = alpha2.upper()
    top   = _INDIGENOUS_TOP_BY_COUNTRY.get(scope)
    if not top:
        return
    top_qid, top_label = top

    def _lbl(entry): return _best_label(entry, locales, "name", "qid")

    # Add top-level root node (only when add_top_node=True)
    if add_top_node and top_qid not in pvs:
        add_permissible_value(pvs, top_qid, title=top_label, meaning=f"wd:{top_qid}")

    # is_a value for group nodes: top_qid when add_top_node=True, None otherwise
    group_is_a = top_qid if add_top_node else None

    indg_group_map = _INDIGENOUS_ADM1_GROUPS.get(scope, {})
    indg_regional  = _INDIGENOUS_REGIONAL_NODES.get(scope, [])

    if indg_group_map or indg_regional:
        # CA-style: build adm1→region map, adm1→group map

        # Pre-resolve each entry's adm1_qid through the full override chain.
        # We need the set of "known" adm1 QIDs (province/territory group keys).
        known_adm1: set[str] = set(indg_group_map.keys())
        for r in indg_regional:
            known_adm1.update(r.get("adm1_qids", set()))

        def _resolve_adm1(qid_band: str, raw_adm1: str) -> str:
            a1 = raw_adm1
            # Step 1: empty → check _ADM2_NO_PARENT_OVERRIDES
            if not a1 and qid_band in _ADM2_NO_PARENT_OVERRIDES:
                a1 = _ADM2_NO_PARENT_OVERRIDES[qid_band]
            # Step 2: intermediate parent one hop too deep
            if a1 and a1 not in known_adm1 and a1 in _ADM2_INTERMEDIATE_PARENT_QIDS:
                a1 = _ADM2_INTERMEDIATE_PARENT_QIDS[a1]
            # Step 3: FN-specific override for remaining orphans
            if (not a1 or a1 not in known_adm1) and qid_band in _FN_BAND_ADM1_OVERRIDES:
                a1 = _FN_BAND_ADM1_OVERRIDES[qid_band]
            return a1

        adm1_to_region: dict = {}
        for r in indg_regional:
            for a1 in r.get("adm1_qids", set()):
                adm1_to_region[a1] = r

        # Group indig_entries by resolved adm1_qid
        by_adm1: dict = defaultdict(list)
        for e in indig_entries:
            raw = e.get("adm1_qid", "")
            resolved = _resolve_adm1(e.get("qid", ""), raw)
            by_adm1[resolved].append(e)

        # Determine order: regional groups first (sorted by label), then province
        # groups (sorted by label), then orphans.
        group_nodes = []  # (grp_qid, grp_label, adm1_key_or_set, is_regional)
        emitted_adm1: set = set()

        for a1, r in adm1_to_region.items():
            if r["group_qid"] not in {gn[0] for gn in group_nodes}:
                group_nodes.append((r["group_qid"], r["group_label"],
                                    r["adm1_qids"], True))

        for a1, (grp_qid, grp_label) in sorted(
            indg_group_map.items(), key=lambda kv: kv[1][1]
        ):
            if grp_qid not in {gn[0] for gn in group_nodes}:
                group_nodes.append((grp_qid, grp_label, {a1}, False))

        # Sort all group nodes by label
        group_nodes.sort(key=lambda gn: _title_sort_key(gn[1]))

        for grp_qid, grp_label, adm1_set, _is_regional in group_nodes:
            if grp_qid not in pvs:
                add_permissible_value(pvs, grp_qid,
                                      title=grp_label,
                                      meaning=f"wd:{grp_qid}",
                                      is_a=group_is_a)
            if not include_bands:
                for a1 in adm1_set:
                    emitted_adm1.add(a1)
                continue
            bands_for_group = []
            for a1 in adm1_set:
                bands_for_group.extend(by_adm1.get(a1, []))
                emitted_adm1.add(a1)
            for entry in sorted(bands_for_group, key=lambda e: _title_sort_key(_lbl(e))):
                qid = entry.get("qid", "")
                if not qid:
                    continue
                lbl = normalize_text(_lbl(entry))
                if lbl == qid:
                    continue  # no Wikidata label — skip unlabelled entries
                _desc = normalize_text(entry.get("desc", "")) or ""
                _native = entry.get("native_label", "")
                if _native:
                    _native_norm = normalize_text(_native)
                    _desc = (f"{_desc}; Native name: {_native_norm}" if _desc
                             else f"Native name: {_native_norm}")
                add_permissible_value(pvs, qid, title=lbl, meaning=f"wd:{qid}",
                                      description=_desc or None, is_a=grp_qid)

        if include_bands:
            # Orphans: bands whose resolved adm1_qid was not handled above
            orphans = []
            for a1, entries_list in by_adm1.items():
                if a1 not in emitted_adm1:
                    orphans.extend(entries_list)
            if orphans:
                print(f"  Note: {len(orphans)} First Nations bands remain as orphans "
                      f"for {scope}. Add their QIDs to _FN_BAND_ADM1_OVERRIDES:")
                for entry in sorted(orphans, key=lambda e: e.get("qid", "")):
                    print(f"    \"{entry.get('qid', '?')}\": \"Q???\",  "
                          f"# {entry.get('name', '?')} → [province QID]")
            for entry in sorted(orphans, key=lambda e: _title_sort_key(_lbl(e))):
                qid = entry.get("qid", "")
                if not qid:
                    continue
                lbl = normalize_text(_lbl(entry))
                if lbl == qid:
                    continue  # no Wikidata label — skip unlabelled entries
                _desc = normalize_text(entry.get("desc", "")) or ""
                _native = entry.get("native_label", "")
                if _native:
                    _native_norm = normalize_text(_native)
                    _desc = (f"{_desc}; Native name: {_native_norm}" if _desc
                             else f"Native name: {_native_norm}")
                add_permissible_value(pvs, qid, title=lbl, meaning=f"wd:{qid}",
                                      description=_desc or None, is_a=group_is_a)

    else:
        # US/NZ-style: group by adm1_qid; try to get labels from cached ADM1
        adm1_labels: dict = {}
        adm1_key = f"GeoWiki_ADM1_{scope}"
        if _entry_exists(adm1_key):
            try:
                adm1_cache = _load_json(adm1_key)
                for e in adm1_cache.get("entries", []):
                    if e.get("qid"):
                        adm1_labels[e["qid"]] = (
                            _best_label(e, locales, "name", "qid") or e["qid"]
                        )
            except Exception:
                pass

        by_adm1: dict = defaultdict(list)
        for e in indig_entries:
            by_adm1[e.get("adm1_qid", "")].append(e)

        # Sort groups by their label
        sorted_adm1 = sorted(
            ((a1, lst) for a1, lst in by_adm1.items() if a1),
            key=lambda kv: _title_sort_key(adm1_labels.get(kv[0], kv[0])),
        )
        for a1_qid, entries_list in sorted_adm1:
            grp_label = adm1_labels.get(a1_qid, a1_qid)
            if a1_qid not in pvs:
                add_permissible_value(pvs, a1_qid, title=grp_label,
                                      meaning=f"wd:{a1_qid}", is_a=group_is_a)
            if not include_bands:
                continue
            for entry in sorted(entries_list, key=lambda e: _title_sort_key(_lbl(e))):
                qid = entry.get("qid", "")
                if not qid:
                    continue
                lbl = normalize_text(_lbl(entry))
                if lbl == qid:
                    continue  # no Wikidata label — skip unlabelled entries
                _desc = normalize_text(entry.get("desc", "")) or ""
                _native = entry.get("native_label", "")
                if _native:
                    _native_norm = normalize_text(_native)
                    _desc = (f"{_desc}; Native name: {_native_norm}" if _desc
                             else f"Native name: {_native_norm}")
                add_permissible_value(pvs, qid, title=lbl, meaning=f"wd:{qid}",
                                      description=_desc or None, is_a=a1_qid)

        if include_bands:
            # Orphans: entries with no adm1_qid
            for entry in sorted(by_adm1.get("", []), key=lambda e: _title_sort_key(_lbl(e))):
                qid = entry.get("qid", "")
                if not qid:
                    continue
                lbl = normalize_text(_lbl(entry))
                if lbl == qid:
                    continue  # no Wikidata label — skip unlabelled entries
                _desc = normalize_text(entry.get("desc", "")) or ""
                _native = entry.get("native_label", "")
                if _native:
                    _native_norm = normalize_text(_native)
                    _desc = (f"{_desc}; Native name: {_native_norm}" if _desc
                             else f"Native name: {_native_norm}")
                add_permissible_value(pvs, qid, title=lbl, meaning=f"wd:{qid}",
                                      description=_desc or None, is_a=group_is_a)


def _build_first_nations_yaml(key, source, adm2_data, locales=None):
    """Write sources/{key}.yaml for GeoWikiFirstNations scope.

    Builds a standalone First Nations / Tribes / Iwi hierarchy enum, reusing
    the indigenous entries already cached in the ADM2 JSON payload.
    """
    locales = locales or ["en"]
    alpha2  = source.get("country", "")
    scope   = alpha2.upper()

    indig_entries = [e for e in adm2_data.get("entries", [])
                     if e.get("entity_type") == "indigenous"]
    if not indig_entries:
        print(f"  Warning: no indigenous entries in ADM2 cache for {scope}.",
              file=sys.stderr)
        return

    top = _INDIGENOUS_TOP_BY_COUNTRY.get(scope)
    if not top:
        print(f"  Warning: no _INDIGENOUS_TOP_BY_COUNTRY entry for {scope}.",
              file=sys.stderr)
        return
    top_qid, top_label = top

    pvs: dict = {}
    # Standalone enum: top node becomes enum_uri, not a PV; include individual bands.
    _build_first_nations_pvs(pvs, indig_entries, scope, locales,
                              add_top_node=False, include_bands=True)

    n        = len(indig_entries)
    enum_key = f"GeoWikiFirstNations{scope}"
    enum_desc = _INDIGENOUS_ENUM_DESC_BY_COUNTRY.get(scope, "")
    source_url = (source.get("reachable_from") or {}).get("source_ontology",
                                                           "https://www.wikidata.org/")
    enum_dict: dict = {
        "name":               enum_key,
        "title":              top_label,
        "enum_uri":           f"wd:{top_qid}",
        "permissible_values": pvs,
    }
    if enum_desc:
        enum_dict["description"] = enum_desc
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"Wikidata First Nations for {scope}",
        description=(
            f"{n} indigenous entities (First Nations, Tribes, Iwi) for {scope} "
            "from Wikidata."
        ),
        license="CC0 1.0",
        prefixes=_SOURCE_PREFIXES,
        enums={enum_key: enum_dict},
    )
    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    log_extraction(enum_key, count=len(pvs))


def _build_adm2_yaml(key, source, data, locales=None, include_indigenous=False):
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

    # Identify ADM2 entries that are "subsumed" by ADM3.  Two patterns:
    #
    # (a) GeoNames-only ADM2 entries whose 3rd compound segment (the admin2 code)
    #     also appears as the final segment of a 4-part GeoNames-only ADM3
    #     compound_code.  These entities that GeoNames classifies as both ADM2
    #     and ADM3 (e.g. NWT communities appear as ADM2 leaves AND ADM3 leaves
    #     under their region).
    #
    # (b) GeoNames-only ADM2 entries whose full compound_code is used as the
    #     parent_compound by a Wikidata-enriched ADM3 entry with the same name.
    #     This covers single-tier municipalities like Haldimand County (ON) that
    #     GeoNames records at both ADM2 (the county container, one gnid) and ADM3
    #     (the municipality proper, a different gnid matched by Wikidata P1566).
    #     Including both would create a self-parenting entry in the ADM3 picklist.
    adm3_leaf_admin_codes: set = set()
    adm3_wikidata_parent_names: defaultdict = defaultdict(set)
    _adm3_key = f"GeoWiki_ADM3_{scope}"
    if _entry_exists(_adm3_key):
        try:
            _adm3_payload = _load_json(_adm3_key)
            for _ae in (_adm3_payload.get("entries") or []):
                _cc3 = _ae.get("compound_code", "")
                _p3 = _cc3.split(".")
                if len(_p3) == 4:                      # CC.admin1.admin2.admin3
                    adm3_leaf_admin_codes.add(_p3[3])
                    if _ae.get("qid"):                 # pattern (b): Wikidata-enriched
                        _parent_cc = ".".join(_p3[:3])
                        _ae_name = _ae.get("name", "")
                        if _ae_name:
                            adm3_wikidata_parent_names[_parent_cc].add(_ae_name.lower())
        except Exception:
            pass

    _subsumed_count = 0
    if adm3_leaf_admin_codes or adm3_wikidata_parent_names:
        filtered = []
        for _e in entries:
            _cc = _e.get("compound_code", "")
            _parts = _cc.split(".")
            _e_name = _e.get("name", "").lower()
            if (not _e.get("qid")                     # GeoNames-only
                    and len(_parts) == 3               # 3-part ADM2 compound
                    and (_parts[2] in adm3_leaf_admin_codes                    # pattern (a)
                         or _e_name in adm3_wikidata_parent_names.get(         # pattern (b)
                             _cc, set()))):
                _subsumed_count += 1
            else:
                filtered.append(_e)
        entries = filtered
    if _subsumed_count:
        print(f"    Excluded {_subsumed_count} ADM2 GeoNames-only entries subsumed by ADM3.")

    # Build a mapping from GeoNames admin1 code (from compound_code) → ADM1 QID.
    # This is derived from entries that are already correctly resolved to a known
    # province QID, and used as a fallback for two classes of problematic entries:
    #   (a) GeoNames-only entries (no Wikidata QID) with an empty adm1_qid
    #   (b) Wikidata entries whose P131 points to an intermediate entity that is
    #       not itself a first-level province/territory (e.g. "Northern Ontario").
    gn_adm1_code_to_qid: dict[str, str] = {}
    for _e in entries:
        _cc  = _e.get("compound_code", "")
        _a1q = _e.get("adm1_qid", "")
        if _a1q and _a1q in adm1_pvs and _cc:
            _parts = _cc.split(".")
            if len(_parts) >= 2:
                gn_adm1_code_to_qid.setdefault(_parts[1], _a1q)

    # Supplement with GeoNames ADM1 code map (stored in payload by _fetch_adm2_payload).
    # This covers territories like Yukon (CA.12) and NWT (CA.13) whose ADM2 entries
    # are GeoNames-only (no Wikidata QID, no adm1_qid), so the dynamic seeding above
    # yields nothing for those admin1 code numbers.
    _gn_adm1_code_map = data.get("gn_adm1_code_map", {})
    if _gn_adm1_code_map and adm1_entries:
        _adm1_gnid_to_qid = {
            _e.get("gnid", ""): _e.get("qid", "")
            for _e in adm1_entries
            if _e.get("gnid") and _e.get("qid")
        }
        for _code, _gnid in _gn_adm1_code_map.items():
            if _code not in gn_adm1_code_to_qid:
                _qid = _adm1_gnid_to_qid.get(_gnid, "")
                if _qid and _qid in adm1_pvs:
                    gn_adm1_code_to_qid[_code] = _qid

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
        # Fallback: derive province from GeoNames compound_code when adm1_qid is
        # still empty or points to an intermediate entity not in adm1_pvs.
        if not adm1_qid or adm1_qid not in adm1_pvs:
            _cc    = e.get("compound_code", "")
            _parts = _cc.split(".")
            if len(_parts) >= 2:
                _fallback = gn_adm1_code_to_qid.get(_parts[1], "")
                if _fallback and _fallback in adm1_pvs:
                    adm1_qid = _fallback
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
        _desc = normalize_text(entry.get("desc", "")) or ""
        _native = entry.get("native_label", "")
        if _native:
            _native_norm = normalize_text(_native)
            _desc = (f"{_desc}; Native name: {_native_norm}" if _desc
                     else f"Native name: {_native_norm}")
        add_permissible_value(
            pvs, pv_code,
            title=lbl,
            meaning=meaning,
            description=_desc or None,
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

    # When both ADM1 is available AND include_indigenous is True AND this country has a
    # First Nations top node, the group nodes (Q5453437, Q5453438, etc.) are defined in
    # the ADM1 enum via _build_adm1_yaml + include_indigenous and come into this enum
    # via inherits.  In that case we skip re-emitting group nodes here (but still emit
    # the bands with is_a=grp_qid so they reference the inherited nodes).
    first_nations_in_adm1 = (
        has_adm1
        and include_indigenous
        and bool(_INDIGENOUS_TOP_BY_COUNTRY.get(scope.upper() if scope else ""))
    )

    # Per-scope indigenous grouping nodes (e.g. "First Nations in Ontario").
    _indg_group_map = _INDIGENOUS_ADM1_GROUPS.get(scope, {})
    # Per-scope supra-provincial regional grouping nodes (e.g. Atlantic Canada).
    _indg_regional = _INDIGENOUS_REGIONAL_NODES.get(scope, [])
    _adm1_to_region: dict = {}   # adm1_qid → regional spec dict
    for _r in _indg_regional:
        for _a1 in _r.get("adm1_qids", set()):
            _adm1_to_region[_a1] = _r
    _added_regional_qids: set = set()   # tracks which regional nodes are in pvs

    # Interleave: each ADM1 entry immediately followed by its ADM2 children.
    # When has_adm1, skip copying adm1_pvs into pvs (they come via inherits).
    # Regular ADM2 entries are listed first under each province; then:
    #  - provinces in _INDIGENOUS_REGIONAL_NODES get a supra-provincial region
    #    node (e.g. "Atlantic Canada") with a "First Nations in [Region]" child;
    #  - provinces in _INDIGENOUS_ADM1_GROUPS get a "First Nations in [Province]"
    #    node directly;
    #  - provinces in neither dict receive their bands directly.
    pvs = {}
    for code, pv_data in adm1_pvs.items():
        if not has_adm1:
            pvs[code] = pv_data   # embed province/type headers (fallback mode)
        if "meaning" in pv_data:      # province entry (not type-category header)
            all_adm2  = adm2_by_adm1.get(code, [])
            regular   = [e for e in all_adm2 if e.get("entity_type") != "indigenous"]
            indig     = [e for e in all_adm2 if e.get("entity_type") == "indigenous"]
            for entry in sorted(regular, key=lambda e: _title_sort_key(_lbl(e))):
                _adm2_pv(pvs, entry, is_a=code)
            if indig:
                region = _adm1_to_region.get(code)
                if region:
                    grp_qid = region["group_qid"]
                    if not first_nations_in_adm1:
                        if region.get("top_level"):
                            # Top-level grouping: group_qid appears at enum root (no is_a),
                            # as a sibling of "Province of Canada".  Insert it once.
                            if grp_qid not in _added_regional_qids:
                                add_permissible_value(pvs, grp_qid,
                                                      title=region["group_label"],
                                                      meaning=f"wd:{grp_qid}")
                                _added_regional_qids.add(grp_qid)
                        else:
                            # Legacy: supra-provincial hierarchy
                            #   regional_node (is_a=primary_adm1) → group_node → bands
                            reg_qid = region["regional_qid"]
                            if reg_qid not in _added_regional_qids:
                                _primary = region.get("primary_adm1", code)
                                _mixins  = sorted(
                                    a for a in region.get("adm1_qids", set())
                                    if a != _primary
                                )
                                add_permissible_value(pvs, reg_qid,
                                                      title=region["regional_label"],
                                                      meaning=f"wd:{reg_qid}",
                                                      is_a=_primary,
                                                      mixins=_mixins or None)
                                add_permissible_value(pvs, grp_qid,
                                                      title=region["group_label"],
                                                      meaning=f"wd:{grp_qid}",
                                                      is_a=reg_qid)
                                _added_regional_qids.add(reg_qid)
                    for entry in sorted(indig, key=lambda e: _title_sort_key(_lbl(e))):
                        _adm2_pv(pvs, entry, is_a=grp_qid)
                else:
                    grouping = _indg_group_map.get(code)
                    if grouping:
                        grp_qid, grp_label = grouping
                        if not first_nations_in_adm1:
                            add_permissible_value(pvs, grp_qid,
                                                  title=grp_label,
                                                  meaning=f"wd:{grp_qid}",
                                                  is_a=code)
                        for entry in sorted(indig, key=lambda e: _title_sort_key(_lbl(e))):
                            _adm2_pv(pvs, entry, is_a=grp_qid)
                    else:
                        for entry in sorted(indig, key=lambda e: _title_sort_key(_lbl(e))):
                            _adm2_pv(pvs, entry, is_a=code)

    # Orphaned ADM2 (parent ADM1 not in adm1_pvs): separate indigenous from regular
    _indig_orphans   = [e for e in adm2_orphans if e.get("entity_type") == "indigenous"]
    _regular_orphans = [e for e in adm2_orphans if e.get("entity_type") != "indigenous"]
    for entry in sorted(_regular_orphans, key=lambda e: _title_sort_key(_lbl(e))):
        _adm2_pv(pvs, entry)
    for entry in sorted(_indig_orphans, key=lambda e: _title_sort_key(_lbl(e))):
        _adm2_pv(pvs, entry)

    n       = len(entries)
    n_match = sum(1 for e in entries if e.get("qid"))
    pct     = 100 * n_match // n if n else 0
    if _regular_orphans:
        print(f"  Note: {len(_regular_orphans)} non-indigenous ADM2 entries had no matching ADM1 parent.")
    if _indig_orphans:
        print(f"  Note: {len(_indig_orphans)} indigenous entities remain at top level "
              f"(province not resolved). Add their P131 intermediary to _ADM2_INTERMEDIATE_PARENT_QIDS:")
        for e in sorted(_indig_orphans, key=lambda e2: e2.get("name", "")):
            print(f"    {e.get('qid', '?')}: {e.get('name', '?')}"
                  f"  (adm1_qid from P131: {e.get('adm1_qid', 'none')})")
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
    adm2_desc  = (source.get("description") or
                  f"{n} second-order administrative divisions for {scope} from Wikidata.")
    enum_dict  = {"name": enum_key, "title": enum_title, "description": adm2_desc}
    if has_adm1:
        enum_dict["inherits"] = f"GeoWikiAdm1{scope}"
    enum_dict["permissible_values"] = pvs
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"Wikidata ADM2 for {scope}",
        description=adm2_desc,
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
            _build_adm1_yaml(f"GeoWiki_ADM1_{scope}", {}, adm1_data, locales,
                             include_indigenous=include_indigenous)
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

    # Build admin1 code → ADM1 QID so we can promote orphaned ADM3 entries to province.
    adm1_code_to_qid: dict = {}
    for _e in adm2_entries:
        _cc = _e.get("compound_code", "")
        _a1q = _e.get("adm1_qid", "")
        if _cc and _a1q:
            _parts = _cc.split(".")
            if len(_parts) >= 2:
                adm1_code_to_qid.setdefault(_parts[1], _a1q)

    has_adm2      = bool(adm2_entries)
    adm2_enum_key = f"GeoWikiAdm2{scope}"

    # Separate ADM3 and ADM4 entries
    adm3_entries = [e for e in entries if e.get("feat_code") == "ADM3"]
    adm4_entries = [e for e in entries if e.get("feat_code") == "ADM4"]

    # Identify GeoNames-only ADM2 entries that are "subsumed by ADM3" (pattern b
    # from _build_adm2_yaml): a GeoNames-only ADM2 entry whose compound_code is
    # claimed as parent_compound by a Wikidata-enriched ADM3 entry with the same
    # name.  E.g. the old Haldimand-Norfolk region (gnid 5969093, labelled
    # "Haldimand County" in GeoNames) is subsumed by Q385346 (Haldimand County ADM3).
    # Both Q385346 and its sibling Q385229 (Norfolk County) reference 5969093 as
    # is_a; after 5969093 is removed from ADM2 that reference would be dangling.
    # We replicate the pattern-b detection here so those ADM3 entries can be
    # reparented directly to the province.
    _adm3_wd_parent_names: defaultdict = defaultdict(set)
    for _ae in adm3_entries:
        if not _ae.get("qid"):
            continue
        _cc3 = _ae.get("compound_code", "")
        _p3 = _cc3.split(".")
        if len(_p3) == 4:
            _par_cc = ".".join(_p3[:3])
            _ae_nm = _ae.get("name", "")
            if _ae_nm:
                _adm3_wd_parent_names[_par_cc].add(_ae_nm.lower())

    _subsumed_adm2_gnids: set = set()
    for _e in adm2_entries:
        if _e.get("qid"):
            continue
        _gnid = _e.get("gnid", "")
        _cc_e = _e.get("compound_code", "")
        _nm_e = _e.get("name", "").lower()
        if _gnid and _nm_e in _adm3_wd_parent_names.get(_cc_e, set()):
            _subsumed_adm2_gnids.add(_gnid)

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
        # If is_a points to a GeoNames-only ADM2 entry that is "subsumed by ADM3"
        # (pattern b: removed from ADM2 because a Wikidata ADM3 entry claims it
        # as parent with the same name), promote is_a to the province level so
        # the reference is not dangling.  Siblings sharing the same obsolete ADM2
        # container (e.g. Norfolk County alongside Haldimand County under the old
        # Haldimand-Norfolk region) are promoted the same way.
        if is_a and not is_a.startswith("Q") and is_a in _subsumed_adm2_gnids:
            _a1_code = (parent_compound.split(".")[1]
                        if parent_compound.count(".") >= 1 else "")
            is_a = adm1_code_to_qid.get(_a1_code, "") or is_a
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

    # Disambiguate ADM3 entries that share an identical (is_a, title) pair.
    # Tries in order: (1) location hint, (2) entity-type hint, (3) QID suffix.
    _adm3_pt_map: defaultdict = defaultdict(list)
    for _pk, _pv in adm3_pvs.items():
        if isinstance(_pv, dict):
            _adm3_pt_map[(_pv.get("is_a", ""), _pv.get("title", ""))].append(_pk)
    _adm3_disambig_count = 0
    for (_par, _ttl), _dqids in _adm3_pt_map.items():
        if len(_dqids) <= 1:
            continue
        loc_hints = {
            _dq: _desc_location_hint(adm3_pvs[_dq].get("description", "") or "")
            for _dq in _dqids
        }
        if (all(loc_hints.values())
                and len(set(loc_hints.values())) == len(_dqids)):
            for _dq, _hint in loc_hints.items():
                adm3_pvs[_dq]["title"] = f"{_ttl} ({_hint})"
            _adm3_disambig_count += len(_dqids)
            continue
        type_hints = {
            _dq: _entity_type_hint(adm3_pvs[_dq].get("description", "") or "")
            for _dq in _dqids
        }
        if (all(type_hints.values())
                and len(set(type_hints.values())) == len(_dqids)):
            for _dq, _hint in type_hints.items():
                adm3_pvs[_dq]["title"] = f"{_ttl} ({_hint})"
            _adm3_disambig_count += len(_dqids)
            continue
        # Fallback: QID suffix
        for _dq in _dqids:
            adm3_pvs[_dq]["title"] = f"{_ttl} ({_dq})"
        _adm3_disambig_count += len(_dqids)
    if _adm3_disambig_count:
        print(f"    Disambiguated {_adm3_disambig_count} ADM3 entries sharing a (parent, title) pair.")

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
                    _inc_indig = bool(_load_config(MENU_CONFIG).get("include_indigenous"))
                    _build_adm2_yaml(f"GeoWiki_ADM2_{scope}", {}, adm2_payload, locales,
                                     include_indigenous=_inc_indig)
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

    print(f"  Querying Wikidata for countries, continents, oceans, waterbodies (locales: {locales}) ...")
    payload = _fetch_countries_payload(locales, endpoint, use_post)
    if not payload.get("countries"):
        print("  Error: no countries returned from Wikidata.", file=sys.stderr)
        return True

    _save_json(key, payload)

    entry = make_source_entry(
        key, "https://www.wikidata.org/wiki/Wikidata:Main_Page",
        "GeoWikiCountries", "zip",
        title="Wikidata Countries, Continents, Oceans, and Waterbodies",
        description=(
            "Current sovereign states (P297), continents (P31=Q5107), oceans "
            "(P31=Q9430), and international waterbodies (lakes/rivers with P17 "
            "spanning 2+ countries; seas, gulfs, straits, bays with 30+ sitelinks) "
            "from Wikidata with GeoNames P1566 cross-references."
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

    locales   = config.get("locales") or ["en"]
    inc_indig = bool(config.get("include_indigenous"))
    use_qlvr  = bool(config.get("apis", {}).get("qlever"))
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

    _build_adm1_yaml(key, config["sources"][key], payload, locales=locales,
                     include_indigenous=inc_indig)
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

    inc_indig = bool(config.get("include_indigenous"))
    print(f"  Querying Wikidata ADM2 for {alpha2} ...")
    payload = _fetch_adm2_payload(alpha2, locales, endpoint, use_post,
                                  include_indigenous=inc_indig)
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

    _build_adm2_yaml(key, config["sources"][key], payload, locales=locales,
                     include_indigenous=inc_indig)

    # Auto-register GeoWikiFirstNations when include_indigenous and country is supported
    if inc_indig and alpha2.upper() in _INDIGENOUS_P31_BY_COUNTRY:
        _register_first_nations(alpha2.upper(), config_file, adm2_data=payload)

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


def _register_country_waters(alpha2, config_file):
    """Register GeoWikiCountryWaters for a single country identified by ISO alpha-2."""
    key    = f"GeoWiki_CountryWaters_{alpha2}"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales  = config.get("locales") or ["en"]
    use_qlvr = bool(config.get("apis", {}).get("qlever"))
    endpoint, use_post = (_QLEVER_ENDPOINT, True) if use_qlvr else (_WDQS_ENDPOINT, True)

    print(f"  Querying Wikidata country waters for {alpha2} ...")
    payload = _fetch_country_waters_payload(alpha2, locales, endpoint, use_post)
    if not payload.get("entries"):
        print(f"  Warning: no water body entries returned for {alpha2}.", file=sys.stderr)

    _save_json(key, payload)

    entry = make_source_entry(
        key, "https://www.wikidata.org/",
        "GeoWikiCountryWaters", "zip",
        title=f"Wikidata Country Waters for {alpha2}",
        description=(
            f"Water bodies (seas, gulfs, straits, bays, lakes, rivers) "
            f"associated with {alpha2} from Wikidata."
        ),
    )
    entry["country"]  = alpha2.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC0 1.0"
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_country_waters_yaml(key, config["sources"][key], payload, locales=locales)
    return True


def _register_first_nations(alpha2, config_file, adm2_data=None):
    """Register GeoWikiFirstNations for a single country (ISO alpha-2).

    Reuses the cached GeoWiki_ADM2_{alpha2} JSON (no new SPARQL query).  When
    *adm2_data* is provided (e.g. called from _register_adm2 immediately after
    fetching), the in-memory payload is used directly to avoid re-loading.

    Returns True if the source was registered (or was already present).
    """
    key    = f"GeoWiki_FirstNations_{alpha2.upper()}"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    # Country must have a First Nations top node
    if not _INDIGENOUS_TOP_BY_COUNTRY.get(alpha2.upper()):
        print(f"  Warning: no _INDIGENOUS_TOP_BY_COUNTRY entry for {alpha2.upper()}; "
              f"skipping GeoWikiFirstNations registration.", file=sys.stderr)
        return False

    locales = config.get("locales") or ["en"]

    if adm2_data is None:
        adm2_key = f"GeoWiki_ADM2_{alpha2.upper()}"
        if not _entry_exists(adm2_key):
            print(f"  Error: {adm2_key} not cached — run '-a GeoWiki_ADM2_{alpha2.upper()}' first",
                  file=sys.stderr)
            return False
        try:
            adm2_data = _load_json(adm2_key)
        except Exception as exc:
            print(f"  Error loading {adm2_key}: {exc}", file=sys.stderr)
            return False

    _top_qid, top_label = _INDIGENOUS_TOP_BY_COUNTRY[alpha2.upper()]
    entry = make_source_entry(
        key, "https://www.wikidata.org/",
        "GeoWikiFirstNations", "zip",
        title=f"Wikidata First Nations for {alpha2.upper()}",
        description=(
            f"Indigenous entities (First Nations, Tribes, Iwi) for {alpha2.upper()} "
            "from Wikidata."
        ),
    )
    entry["country"]  = alpha2.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC0 1.0"
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_first_nations_yaml(key, config["sources"][key], adm2_data, locales=locales)
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_geowiki_code(url, config_file=MENU_CONFIG):
    """Pre-download handler for GeoWiki_* shorthands.

    Matches:
      GeoWiki_countries              → register GeoWikiCountries
      GeoWiki_ADM1                   → global GeoWikiAdm1 (QLever recommended)
      GeoWiki_ADM1_{CC}              → per-country GeoWikiAdm1
      GeoWiki_ADM2_{CC}              → per-country GeoWikiAdm2
      GeoWiki_ADM3_{CC}              → per-country GeoWikiAdm3 (municipalities)
      GeoWiki_ADM3ADM4_{CC}          → per-country GeoWikiAdm3 + Adm4
      GeoWiki_CountryWaters_{CC}     → per-country water bodies (seas, bays, lakes …)
      GeoWiki_FirstNations_{CC}      → indigenous entities (First Nations, Tribes, Iwi)

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
        if level == "COUNTRYWATERS":
            return _register_country_waters(cc.upper(), config_file)
        if level == "FIRSTNATIONS":
            return _register_first_nations(cc.upper(), config_file)

    return False


def fetch_geowiki_source(key, source, config_file=MENU_CONFIG):
    """Re-download Wikidata data for *key*."""
    content_type    = source.get("content_type", "")
    locales         = _get_project_locales(config_file)
    endpoint, use_p = _get_endpoint(source)

    if content_type == "GeoWikiCountries":
        print(f"  Re-querying Wikidata for countries, continents, oceans, and waterbodies ...")
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
        _top_config  = _load_config(config_file) if config_file else {}
        inc_indig    = bool(_top_config.get("include_indigenous"))
        print(f"  Re-querying Wikidata ADM2 for {alpha2} ...")
        payload = _fetch_adm2_payload(alpha2, locales, endpoint, use_p,
                                      include_indigenous=inc_indig)
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

    elif content_type == "GeoWikiCountryWaters":
        alpha2 = source.get("country", "")
        if not alpha2:
            print(f"  Error: GeoWikiCountryWaters source '{key}' has no country field.",
                  file=sys.stderr)
            return
        print(f"  Re-querying Wikidata country waters for {alpha2} ...")
        payload = _fetch_country_waters_payload(alpha2, locales, endpoint, use_p)
        _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoWikiFirstNations":
        alpha2 = source.get("country", "")
        if not alpha2:
            print(f"  Error: GeoWikiFirstNations source '{key}' has no country field.",
                  file=sys.stderr)
            return
        adm2_key = f"GeoWiki_ADM2_{alpha2}"
        if not _entry_exists(adm2_key):
            print(f"  Error: {adm2_key} not cached — run '-f {adm2_key}' first",
                  file=sys.stderr)
            return
        _build_first_nations_yaml(key, source, _load_json(adm2_key), locales=locales)
        update_source_config(key, {"download_date": datetime.date.today().isoformat()},
                             config_file)

    else:
        print(f"  Warning: unknown GeoWiki content_type '{content_type}' for '{key}'",
              file=sys.stderr)


def process_geowiki_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Build sources/{key}.yaml from the cached GeoWiki zip for *key*."""
    content_type = source.get("content_type", "")
    actual_key   = key

    locales = locales or _get_project_locales(config_file)

    _top_config = _load_config(config_file) if config_file else {}
    inc_indig   = bool(_top_config.get("include_indigenous"))

    # GeoWikiFirstNations reuses the ADM2 cache; it has no own zip entry.
    if content_type == "GeoWikiFirstNations":
        alpha2 = source.get("country", "")
        if not alpha2:
            print(f"  Error: GeoWikiFirstNations source '{key}' has no country field.",
                  file=sys.stderr)
            return
        adm2_key = f"GeoWiki_ADM2_{alpha2}"
        if not _entry_exists(adm2_key):
            print(f"  Error: {adm2_key} not cached — run '-f {adm2_key}' first",
                  file=sys.stderr)
            return
        _build_first_nations_yaml(key, source, _load_json(adm2_key), locales=locales)
        return

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
    dtype   = data.get("type", "")

    if dtype == "countries":
        _build_countries_yaml(key, source, data, locales=locales)
    elif dtype == "adm1":
        _build_adm1_yaml(key, source, data, locales=locales,
                         include_indigenous=inc_indig)
    elif dtype == "adm2":
        _build_adm2_yaml(key, source, data, locales=locales,
                         include_indigenous=inc_indig)
    elif dtype == "adm34":
        if content_type == "GeoWikiADM3":
            _build_adm34_yaml(key, source, data, locales=locales, only_levels={"ADM3"})
        elif content_type == "GeoWikiADM4":
            _build_adm34_yaml(key, source, data, locales=locales, only_levels={"ADM4"})
        else:
            _build_adm34_yaml(key, source, data, locales=locales)
    elif dtype == "ppl":
        _build_ppl_yaml(key, source, data, locales=locales)
    elif dtype == "country_waters":
        _build_country_waters_yaml(key, source, data, locales=locales)
    else:
        print(f"  Warning: unknown data type '{dtype}' in {_SHARED_ZIP}", file=sys.stderr)
