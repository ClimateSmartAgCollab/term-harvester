"""GeoNames geopolitical vocabulary handler for term_harvester.py.

Downloads GeoNames flat files and produces GeoNames-native LinkML enums.
For Wikidata-native enums see source_geowiki.py.

Content types
-------------
GeoNamesCountries
    ~250 current sovereign states and territories from GeoNames countryInfo.txt,
    plus the 7 GeoNames continents.
GeoNamesADM1
    ~4,000 first-order administrative divisions (states, provinces, etc.) from
    admin1CodesASCII.txt.  Per-country shorthand restricts output to one country.
GeoNamesADM2
    ~40,000 second-order divisions from admin2Codes.txt.  Per-country only; the
    full global file is 2.3 MB; without filtering the Wikidata enrichment would
    require QLever.
GeoNamesADM34
    Third- and/or fourth-order divisions from per-country {CC}.zip allCountries
    file.  Per-country only.  Specify GeoNames_{CC}_ADM3 or GeoNames_{CC}_ADM3ADM4.
    Inherits chain: GeoNamesAdm4{CC} → GeoNamesAdm3{CC} → GeoNamesAdm2{CC} →
    GeoNamesAdm1{CC}.  Each level automatically fetches its ancestors if absent
    (zip only, no config entry): fetching ADM4 also fetches ADM3, ADM2, and ADM1;
    fetching ADM3 also fetches ADM2 and ADM1; fetching ADM2 fetches ADM1.
GeoNamesSettlement
    Populated places (feature class P) from per-country {CC}.zip.  Includes both
    current settlements and historical/abandoned ones (PPLH, PPLCH, PPLQ, PPLW).
    Historical entries (feature codes ending in H) are emitted with status: historical
    so they can be excluded at build time with minus: {status: [historical]} in
    harvester_config.yaml.

Shorthand -a patterns
---------------------
GeoNames_countries              → GeoNamesCountries (countries + continents)
GeoNames_ADM1                   → GeoNamesAdm1 (all ~4,000 globally)
GeoNames_ADM1_{CC}              → GeoNamesAdm1 single country (e.g. GeoNames_ADM1_US)
GeoNames_ADM2_{CC}              → GeoNamesAdm2 single country (e.g. GeoNames_ADM2_CA)
GeoNames_ADM3_{CC}              → GeoNamesAdm3 (ADM3 only) for one country
GeoNames_ADM3ADM4_{CC}          → GeoNamesAdm3 + Adm4 for one country
GeoNames_Settlement_{CC}        → GeoNamesSettlement for one country (e.g. GeoNames_Settlement_NL)
https://download.geonames.org/export/dump/countryInfo.txt → GeoNamesCountries

Permissible value structure
---------------------------
code           geonameId (numeric string, e.g. "6252001")
aliases        [dot-notation or ISO alpha-2 code, e.g. "US" / "US.CA"]
meaning        gnf:{gnid}        (prefix gnf: = https://sws.geonames.org/)
exact_mappings [wd:{QID}]        (Wikidata cross-reference via P1566 reverse lookup)
description    short description from Wikidata if available
is_a           parent geonameId for hierarchy (ADM1 → country header, etc.)

Historical filtering
--------------------
GeoNames countryInfo.txt, admin1CodesASCII.txt, and admin2Codes.txt list only
active current entities.  Codes ending in H (ADM1H, ADMDH, PCLH …) are
historical and do not appear in these flat files, so no additional filtering
is required.

License
-------
GeoNames data: Creative Commons Attribution 4.0 License (geonames.org)

See also
--------
https://unstats.un.org/unsd/geoinfo/ungegn/docs/CHAPTER%2010.pdf
    UNGEGN Chapter 10 — Administrative Divisions

Public API used by term_harvester.py
-------------------------------------
match_geonames_code(url, config_file)
fetch_geonames_source(key, source, config_file)
process_geonames_source(key, source, config_file, locales)
"""

import datetime
import email.utils
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
from collections import defaultdict

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

_GEONAMES_COUNTRY_URL   = "https://download.geonames.org/export/dump/countryInfo.txt"
_GEONAMES_ADM1_URL      = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"
_GEONAMES_ADM2_URL      = "https://download.geonames.org/export/dump/admin2Codes.txt"
_GEONAMES_CC_URL        = "https://download.geonames.org/export/dump/{CC}.zip"


def _geonames_head_date(url):
    """Return the Last-Modified date of a GeoNames URL as 'YYYY-MM-DD', or ''.

    Makes a single HTTP HEAD request — no file body is transferred.
    Parses the standard HTTP date header via email.utils.parsedate.
    Returns '' silently on any error (network, missing header, parse failure).
    """
    try:
        req = urllib.request.Request(url, headers=BROWSER_HEADERS, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            lm = resp.headers.get("Last-Modified", "")
        if lm:
            parsed = email.utils.parsedate(lm)   # → 9-tuple or None
            if parsed:
                return datetime.date(*parsed[:3]).isoformat()
    except Exception:
        pass
    return ""

_GN_URI_BASE     = "https://sws.geonames.org/"
_WD_ENTITY_BASE  = "http://www.wikidata.org/entity/"
_WDQS_ENDPOINT   = "https://query.wikidata.org/sparql"
_QLEVER_ENDPOINT = "https://qlever.cs.uni-freiburg.de/api/wikidata"

_ISO_URI_BASE = "https://www.iso.org/obp/ui/#iso:code:3166:"

_GN_ONTOLOGY_BASE = "http://www.geonames.org/ontology#"

_SOURCE_PREFIXES = {
    "wd":  _WD_ENTITY_BASE,
    "gnf": _GN_URI_BASE,
    "gno": _GN_ONTOLOGY_BASE,
    "iso": _ISO_URI_BASE,
}

# SSSOM output file for GeoNames → Wikidata / ISO exact-match cross-references
_GEONAMES_SSSOM      = os.path.join("sources", "GeoNames.sssom.tsv")
_GEONAMES_SSSOM_ID   = "https://w3id.org/geonames/mappings"
_GEONAMES_SSSOM_DESC = ("Exact-match cross-references between GeoNames entities "
                        "(gnf: URIs) and Wikidata QIDs (wd:) / ISO 3166 codes (iso:).")
_SSSOM_PREFIXES      = {
    "gnf":   _GN_URI_BASE,
    "gno":   _GN_ONTOLOGY_BASE,
    "iso":   _ISO_URI_BASE,
    "semapv": "https://w3id.org/semapv/vocab/",
    "skos":  "http://www.w3.org/2004/02/skos/core#",
    "wd":    _WD_ENTITY_BASE,
}

# GeoNames continent geonameIds (stable; feature code CONT)
_CONTINENTS = [
    {"gnid": "6255146", "code": "AF", "name": "Africa"},
    {"gnid": "6255147", "code": "AS", "name": "Asia"},
    {"gnid": "6255148", "code": "EU", "name": "Europe"},
    {"gnid": "6255149", "code": "NA", "name": "North America"},
    {"gnid": "6255150", "code": "SA", "name": "South America"},
    {"gnid": "6255151", "code": "OC", "name": "Oceania"},
    {"gnid": "6255152", "code": "AN", "name": "Antarctica"},
]

# Note: oceans are NOT included in source_geonames.py because they do not appear
# in GeoNames flat files (countryInfo.txt, admin1CodesASCII.txt, admin2Codes.txt).
# Ocean entries with correct GeoNames IDs are available in source_geowiki.py
# (GeoWikiCountries → GeographicWaterBody hierarchy) via Wikidata P31=Q9430.

# Continent 2-letter code (countryInfo.txt col 8) → geonameId
_CONTINENT_CODE_TO_GNID = {c["code"]: c["gnid"] for c in _CONTINENTS}

# Wikidata P1566 batch size for WDQS
_WD_BATCH = 500

# Wikidata sovereign-state instance types (same as source_geowiki.py)
_WD_SOVEREIGN_TYPES = (
    "Q3624078",   # sovereign state
    "Q15634554",  # member state of the United Nations
)

# Shorthand regex: GeoNames_countries, GeoNames_ADM1, GeoNames_ADM1_US, GeoNames_ADM3_CA, GeoNames_ADM3ADM4_CA, GeoNames_Settlement_NL
_SHORTHAND_RE = re.compile(
    r'^GeoNames_(?:(countries|ADM1)|(ADM3ADM4|ADM[1-4]|Settlement)_([A-Za-z]{2}))$',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHARED_ZIP = os.path.join("sources", "GeoNames.zip")

def _zip_path(key):           # noqa: ARG001  (key ignored; all payloads share one zip)
    return _SHARED_ZIP

def _json_name(key):
    return f"{key}.json"

def _entry_exists(key):
    """Return True if {key}.json exists inside the shared GeoNames.zip."""
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


# Matches "province of …", "territory of …", "federal state of …", etc.
_TYPE_OF_RE = re.compile(r'^(.+?)\s+of\b', re.IGNORECASE)


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
# SPARQL helper (Wikidata P1566 enrichment)
# ---------------------------------------------------------------------------

_SPARQL_RETRY_DELAYS = (15, 60, 120)   # seconds; transient HTTP codes only
_SPARQL_TRANSIENT_CODES = {429, 502, 503, 504}


def _sparql_query(sparql, timeout=60, endpoint=_WDQS_ENDPOINT, use_post=False):
    """Execute a SPARQL query against an endpoint; return bindings list.

    Retries up to 3 times with increasing delays on transient HTTP errors
    (429 Too Many Requests, 502/503/504 gateway errors).
    """
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


def _wikidata_p1566_batch(gnids, locales=None, use_qlever=False):
    """Reverse-lookup Wikidata items by GeoNames ID (P1566) for a batch.

    Returns dict: gnid → {qid, labels: {lang: label}, desc}
    """
    locales  = locales or ["en"]
    endpoint = _QLEVER_ENDPOINT if use_qlever else _WDQS_ENDPOINT
    use_post = True  # always POST — large VALUES clauses exceed GET URL length limits

    values_str     = " ".join(f'"{gid}"' for gid in gnids)
    label_vars     = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} .'
        f' FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )
    sparql = f"""
SELECT ?item ?gnid {label_vars} ?desc WHERE {{
  VALUES ?gnid {{ {values_str} }}
  ?item wdt:P1566 ?gnid .
{label_optionals}
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
}}
"""
    result = {}
    try:
        for row in _sparql_query(sparql, timeout=60, endpoint=endpoint, use_post=use_post):
            gnid = row.get("gnid", {}).get("value", "")
            if not gnid or gnid in result:
                continue
            qid  = _qid_from_uri(row.get("item", {}).get("value", ""))
            labels = {
                lang: row[f"label_{lang}"]["value"]
                for lang in locales if row.get(f"label_{lang}", {}).get("value")
            }
            desc = row.get("desc", {}).get("value", "")
            result[gnid] = {"qid": qid, "labels": labels, "desc": desc}
    except Exception as exc:
        print(f"  Warning: P1566 batch query failed: {exc}", file=sys.stderr)
    return result


def _enrich_with_wikidata(gnids, locales=None, use_qlever=False):
    """Fetch Wikidata enrichment for geonameIds, in batches of _WD_BATCH.

    Returns dict: gnid → {qid, labels, desc}
    """
    locales = locales or ["en"]
    result  = {}
    gnids   = list(gnids)
    total   = len(gnids)
    for i in range(0, total, _WD_BATCH):
        batch = gnids[i : i + _WD_BATCH]
        end   = min(i + _WD_BATCH, total)
        print(f"    Wikidata P1566 lookup {end}/{total} GeoNames IDs ...")
        result.update(_wikidata_p1566_batch(batch, locales=locales, use_qlever=use_qlever))
        if not use_qlever and i + _WD_BATCH < total:
            time.sleep(0.3)
    return result


def _fetch_alternatenames_wkdt(cc, extra_langs=None):
    """Download alternatenames/{CC}.zip and extract Wikidata QID mappings and language labels.

    extra_langs: optional list/set of BCP-47 language codes (e.g. ['fr', 'de']) whose
        preferred labels should also be extracted.  When None or empty, only wkdt entries
        are collected and lang_labels is returned empty.

    Returns (gnid_to_qid, gnid_to_labels):
        gnid_to_qid:    dict gnid → qid (bare, e.g. 'Q99') from wkdt entries
        gnid_to_labels: dict gnid → {lang: label} for each requested language
    Historic entries (isHistoric == '1') and colloquial entries (isColloquial == '1')
    are excluded from language labels.  For each (gnid, lang) pair the isPreferredName
    entry wins; otherwise the first non-historic/non-colloquial entry is kept.
    """
    cc  = cc.upper()
    url = f"https://download.geonames.org/export/dump/alternatenames/{cc}.zip"
    extra_langs_set = set(extra_langs) if extra_langs else set()
    print(f"  Downloading alternatenames/{cc}.zip for wkdt crosswalk"
          + (f" + {sorted(extra_langs_set)} labels" if extra_langs_set else "") + " ...")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            zdata = resp.read()
    except Exception as exc:
        print(f"  Warning: could not download alternatenames/{cc}.zip: {exc}",
              file=sys.stderr)
        return {}, {}
    result = {}
    # Track best label per (gnid, lang): (priority, label) where priority 1=preferred, 0=first-seen
    lang_best: dict = {}   # (gnid, lang) → (priority, label)
    try:
        with zipfile.ZipFile(io.BytesIO(zdata)) as zf:
            fname = f"{cc}.txt"
            with zf.open(fname) as fh:
                for line in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
                    cols = line.rstrip("\n").split("\t")
                    if len(cols) < 4:
                        continue
                    iso_lang = cols[2]
                    gnid     = cols[1].strip()
                    if iso_lang == "wkdt":
                        if len(cols) >= 8 and cols[7] == "1":   # isHistoric
                            continue
                        qid = cols[3].strip()
                        if gnid and qid:
                            result[gnid] = qid
                    elif iso_lang in extra_langs_set:
                        if len(cols) >= 8 and cols[7] == "1":   # isHistoric
                            continue
                        if len(cols) >= 7 and cols[6] == "1":   # isColloquial
                            continue
                        name         = cols[3].strip()
                        is_preferred = len(cols) >= 5 and cols[4] == "1"
                        priority     = 1 if is_preferred else 0
                        key          = (gnid, iso_lang)
                        if key not in lang_best or priority > lang_best[key][0]:
                            lang_best[key] = (priority, name)
    except Exception as exc:
        print(f"  Warning: could not parse alternatenames/{cc}.zip: {exc}",
              file=sys.stderr)
        return {}, {}
    # Flatten lang_best → {gnid: {lang: label}}
    lang_labels: dict = {}
    for (gnid, lang), (_, label) in lang_best.items():
        lang_labels.setdefault(gnid, {})[lang] = label
    print(f"    Loaded {len(result)} wkdt QID mappings from alternatenames/{cc}.zip"
          + (f"; {len(lang_labels)} gnids with ≥1 language label" if lang_labels else ""))
    return result, lang_labels


def _enrich_with_alternatenames_then_sparql(cc, gnids, locales, use_qlever=False):
    """Two-step Wikidata QID crosswalk for a per-country set of GeoNames IDs.

    Step A — GeoNames alternateNames (primary, no API calls):
      Download alternatenames/{CC}.zip and extract wkdt entries.
      When locales contains non-English languages, also extracts translated labels
      from the same file (using isPreferredName where available).

    Step B — P1566 SPARQL fallback (for gnids with no wkdt entry):
      Batched reverse-lookup via _enrich_with_wikidata().

    Reports match counts for each step.
    Returns dict: gnid → {qid, labels, desc}
    """
    extra_langs = [l for l in (locales or []) if l != "en"]
    wkdt_map, lang_labels_map = _fetch_alternatenames_wkdt(
        cc, extra_langs=extra_langs if extra_langs else None
    )
    wd_data   = {}
    unmatched = []
    for gnid in gnids:
        qid = wkdt_map.get(gnid)
        if qid:
            wd_data[gnid] = {"qid": qid, "labels": lang_labels_map.get(gnid, {}), "desc": ""}
        else:
            unmatched.append(gnid)
    n_alt = len(gnids) - len(unmatched)
    pct_alt = 100 * n_alt // len(gnids) if gnids else 0
    print(f"  alternateNames wkdt: {n_alt}/{len(gnids)} matched ({pct_alt}%)")

    if unmatched:
        print(f"  P1566 SPARQL fallback for {len(unmatched)} unmatched gnids ...")
        wd_extra = _enrich_with_wikidata(unmatched, locales=locales, use_qlever=use_qlever)
        n_sparql = sum(1 for v in wd_extra.values() if v.get("qid"))
        print(f"    P1566 fallback matched: {n_sparql}/{len(unmatched)}")
        wd_data.update(wd_extra)

    n_total   = sum(1 for v in wd_data.values() if v.get("qid"))
    pct_total = 100 * n_total // len(gnids) if gnids else 0
    print(f"  Total crosswalk: {n_total}/{len(gnids)} ({pct_total}%) have a Wikidata QID")
    return wd_data


def _wikidata_sovereignty_batch(qids):
    """Query P31 sovereignty + P17 parent country for a batch of Wikidata QIDs.

    Returns dict: qid → {is_sovereign: bool, parent_qid: str}
    """
    values_str = " ".join(f"wd:{q}" for q in qids)
    sov_union  = " UNION ".join(
        f"{{ ?item wdt:P31 wd:{t} . }}" for t in _WD_SOVEREIGN_TYPES
    )
    sparql = f"""
SELECT ?item ?is_sovereign ?parent WHERE {{
  VALUES ?item {{ {values_str} }}
  BIND(EXISTS {{ {sov_union} }} AS ?is_sovereign)
  OPTIONAL {{ ?item wdt:P17 ?parent . }}
}}
"""
    result = {}
    try:
        for row in _sparql_query(sparql, timeout=60, use_post=True):
            item_uri = row.get("item", {}).get("value", "")
            qid      = _qid_from_uri(item_uri)
            if not qid:
                continue
            is_sov  = row.get("is_sovereign", {}).get("value", "false").lower() == "true"
            par_uri = row.get("parent", {}).get("value", "")
            par_qid = _qid_from_uri(par_uri) if par_uri else ""
            result[qid] = {"is_sovereign": is_sov, "parent_qid": par_qid}
    except Exception as exc:
        print(f"  Warning: sovereignty batch query failed: {exc}", file=sys.stderr)
    return result


def _enrich_sovereignty(qids):
    """Fetch sovereignty info for Wikidata QIDs, in batches of _WD_BATCH.

    Returns dict: qid → {is_sovereign: bool, parent_qid: str}
    """
    result = {}
    qids   = list(qids)
    total  = len(qids)
    for i in range(0, total, _WD_BATCH):
        batch = qids[i : i + _WD_BATCH]
        end   = min(i + _WD_BATCH, total)
        print(f"    Wikidata sovereignty check {end}/{total} QIDs ...")
        result.update(_wikidata_sovereignty_batch(batch))
        if i + _WD_BATCH < total:
            time.sleep(0.3)
    return result


# ---------------------------------------------------------------------------
# GeoNames flat-file parsers
# ---------------------------------------------------------------------------

def _fetch_geonames_countries():
    """Download and parse GeoNames countryInfo.txt.

    Returns list of dicts: gnid, iso2, iso3, name, capital, continent_code.
    """
    print("  Downloading GeoNames countryInfo.txt ...")
    req = urllib.request.Request(_GEONAMES_COUNTRY_URL, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")

    countries = []
    for line in raw.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 17:
            continue
        gnid = cols[16].strip()
        iso2 = cols[0].strip()
        if not gnid or not iso2:
            continue
        countries.append({
            "gnid":           gnid,
            "iso2":           iso2,
            "iso3":           cols[1].strip(),
            "name":           cols[4].strip(),
            "capital":        cols[5].strip(),
            "continent_code": cols[8].strip(),
        })
    print(f"    Parsed {len(countries)} countries from countryInfo.txt")
    return countries


def _fetch_geonames_adm1(country_filter=None):
    """Download and parse GeoNames admin1CodesASCII.txt.

    country_filter: optional set of uppercase ISO2 codes (None = all).
    Returns list of dicts: gnid, compound_code, cc, adm1_code, name.
    """
    print("  Downloading GeoNames admin1CodesASCII.txt ...")
    req = urllib.request.Request(_GEONAMES_ADM1_URL, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")

    entries = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        compound = cols[0].strip()   # e.g. "US.CA"
        gnid     = cols[3].strip()
        name     = cols[1].strip()
        if not compound or not gnid or "." not in compound:
            continue
        cc, adm1_code = compound.split(".", 1)
        cc = cc.upper()
        if country_filter and cc not in country_filter:
            continue
        entries.append({
            "gnid":          gnid,
            "compound_code": compound,
            "cc":            cc,
            "adm1_code":     adm1_code,
            "name":          name,
        })
    scope = (f"for {next(iter(country_filter))} "
             if country_filter and len(country_filter) == 1 else "")
    print(f"    Parsed {len(entries)} ADM1 entries {scope}from admin1CodesASCII.txt")
    return entries


def _fetch_geonames_adm2(country_filter):
    """Download and parse GeoNames admin2Codes.txt for a specific country.

    country_filter: non-empty set of uppercase ISO2 codes.
    Returns list of dicts: gnid, compound_code, cc, adm1_code, adm2_code, name.
    """
    if not country_filter:
        print("  Error: ADM2 requires a country_filter to avoid downloading 40,000+ entries.",
              file=sys.stderr)
        return []
    print("  Downloading GeoNames admin2Codes.txt ...")
    req = urllib.request.Request(_GEONAMES_ADM2_URL, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")

    entries = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        compound = cols[0].strip()
        gnid     = cols[3].strip()
        name     = cols[1].strip()
        if not compound or not gnid:
            continue
        parts = compound.split(".")
        if len(parts) < 3:
            continue
        cc = parts[0].upper()
        if cc not in country_filter:
            continue
        entries.append({
            "gnid":          gnid,
            "compound_code": compound,
            "cc":            cc,
            "adm1_code":     parts[1],
            "adm2_code":     parts[2],
            "name":          name,
        })
    scope = (f"for {next(iter(country_filter))} "
             if len(country_filter) == 1 else "")
    print(f"    Parsed {len(entries)} ADM2 entries {scope}from admin2Codes.txt")
    return entries


# Populated-place feature codes — current (active) settlements
_GN_PPL_CODES = frozenset({
    "PPL",   # populated place (general)
    "PPLA",  # seat of first-order admin division
    "PPLA2", "PPLA3", "PPLA4", "PPLA5",  # seats of lower divisions
    "PPLC",  # capital of a political entity
    "PPLF",  # farm village
    "PPLG",  # seat of government
    "PPLL",  # populated locality
    "PPLR",  # religious populated place
    "PPLS",  # populated places (merged)
    "PPLX",  # section of a populated place
})

# Populated-place feature codes — historical / no longer inhabited
_GN_PPL_HIST_CODES = frozenset({
    "PPLH",   # historical populated place (abandoned or renamed)
    "PPLCH",  # historical capital of a political entity
    "PPLQ",   # abandoned populated place
    "PPLW",   # destroyed populated place
})


def _iter_cc_zip_rows(cc, feat_class=None, feat_codes=None):
    """Download GeoNames {CC}.zip and yield matching 19-column rows.

    feat_class: optional single letter e.g. 'A' or 'P' (col 6 filter).
    feat_codes: optional frozenset of feature codes to keep (col 7 filter).
    Yields raw column lists (length ≥ 14).
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


def _fetch_geonames_adm34(cc, levels=("ADM3",)):
    """Parse ADM3/ADM4 entries from GeoNames {CC}.zip."""
    cc     = cc.upper()
    levels = {lv.upper() for lv in levels}
    entries = []
    for cols in _iter_cc_zip_rows(cc, feat_class="A", feat_codes=levels):
        gnid      = cols[0].strip()
        name      = cols[1].strip()
        feat_code = cols[7].strip()
        adm1      = cols[10].strip()
        adm2      = cols[11].strip()
        adm3      = cols[12].strip()
        adm4      = cols[13].strip()
        if not gnid or not adm1:
            continue
        compound = (f"{cc}.{adm1}.{adm2}.{adm3}"
                    if feat_code == "ADM3"
                    else f"{cc}.{adm1}.{adm2}.{adm3}.{adm4}")
        entries.append({
            "gnid":          gnid,
            "compound_code": compound,
            "cc":            cc,
            "adm1_code":     adm1,
            "adm2_code":     adm2,
            "adm3_code":     adm3,
            "adm4_code":     adm4 if feat_code == "ADM4" else "",
            "feat_code":     feat_code,
            "name":          name,
        })
    lv_str = "/".join(sorted(levels))
    print(f"    Parsed {len(entries)} {lv_str} entries for {cc}")
    return entries


def _fetch_geonames_ppl(cc):
    """Parse populated places (feature class P) from GeoNames {CC}.zip.

    Collects both current (_GN_PPL_CODES) and historical (_GN_PPL_HIST_CODES) entries.
    Historical entries are flagged with is_historical=True so callers can mark them
    status: historical in the output YAML.
    """
    cc             = cc.upper()
    all_ppl_codes  = _GN_PPL_CODES | _GN_PPL_HIST_CODES
    entries        = []
    for cols in _iter_cc_zip_rows(cc, feat_class="P", feat_codes=all_ppl_codes):
        gnid      = cols[0].strip()
        name      = cols[1].strip()
        feat_code = cols[7].strip()
        adm1      = cols[10].strip()
        adm2      = cols[11].strip()
        adm3      = cols[12].strip()
        if not gnid:
            continue
        entries.append({
            "gnid":          gnid,
            "name":          name,
            "feat_code":     feat_code,
            "cc":            cc,
            "adm1_code":     adm1,
            "adm2_code":     adm2,
            "adm3_code":     adm3,
            "is_historical": feat_code in _GN_PPL_HIST_CODES,
        })
    n_hist = sum(1 for e in entries if e["is_historical"])
    n_curr = len(entries) - n_hist
    print(f"    Parsed {n_curr} current + {n_hist} historical populated places for {cc}")
    return entries


# ---------------------------------------------------------------------------
# JSON persistence
# ---------------------------------------------------------------------------

def _save_json(key, payload, config_file=None):
    """Store payload as {key}.json inside the shared GeoNames.zip."""
    today = datetime.date.today().isoformat()
    payload["download_date"] = today
    json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    _write_entry(_SHARED_ZIP, _json_name(key), json_bytes)
    if config_file:
        update = {"download_date": today, "file_format": "zip"}
        source_modified = payload.get("source_modified", "")
        if source_modified:
            update["version"] = source_modified
        update_source_config(key, update, config_file)


def _load_json(key):
    """Return the JSON payload for *key* from the shared GeoNames.zip."""
    entry_name = _json_name(key)
    try:
        with zipfile.ZipFile(_SHARED_ZIP) as zf:
            if entry_name in zf.namelist():
                return json.loads(zf.read(entry_name).decode("utf-8"))
    except (zipfile.BadZipFile, FileNotFoundError):
        pass
    raise FileNotFoundError(f"{entry_name} not found in {_SHARED_ZIP}")


def _load_cached_adm_payload(key):
    """Return the JSON payload for *key* if it exists in the shared zip, else None."""
    if not _entry_exists(key):
        return None
    try:
        return _load_json(key)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# YAML builders
# ---------------------------------------------------------------------------

def _build_countries_yaml(key, source, data, locales=None):
    """Write sources/{key}.yaml for GeoNamesCountries scope.

    Produces three enums:
      GeoNamesCountry         — sovereign states + continent headers, sorted A-Z
      GeoNamesCountryExtended — dependent territories, inherits GeoNamesCountry
      GeoNamesContinent       — 7 GeoNames continents
    Sovereignty is determined via Wikidata P31 (stored in payload by
    _fetch_countries_payload); entries without a Wikidata match default to sovereign.
    Code = geonameId; aliases = [ISO2 or continent 2-letter code];
    meaning = gnf:{gnid}; exact_mappings = [wd:{QID}, iso:{ISO2}].
    Note: oceans are not in GeoNames flat files; use GeoWikiOcean.
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]

    countries  = data.get("countries", [])
    continents = data.get("continents", [])
    wd_data    = data.get("wikidata", {})   # gnid → {qid, labels, desc[, is_sovereign, parent_qid]}

    def _lbl(gnid, fallback):
        wd = wd_data.get(gnid, {})
        return (wd.get("labels", {}).get(primary_lang)
                or wd.get("labels", {}).get("en")
                or fallback)

    # Build qid → gnid reverse map for parent lookups
    qid_to_gnid = {v["qid"]: k for k, v in wd_data.items() if v.get("qid")}

    # Split countries into sovereign states and territories using P31 + P17 resolution.
    # GeoNames countryInfo.txt maps to some Wikidata items that are *parts* of a sovereign
    # entity rather than the sovereign entity itself (e.g. Q55 Netherlands → Q29999 Kingdom
    # of Netherlands).  Three-rule classification:
    #  1. is_sovereign=True (P31 check passed) → sovereign
    #  2. is_sovereign=False, parent_qid == own qid (self-referential P17, Wikidata quirk
    #     for some autonomous territories) → territory (e.g. Aruba, Cook Islands)
    #  3. is_sovereign=False, parent in qid_to_gnid → genuine territory (parent is a known
    #     country in our dataset, e.g. French Guiana → France)
    #  4. is_sovereign=False, parent NOT in qid_to_gnid (e.g. Netherlands → Q29999 Kingdom
    #     of NL which is not in countryInfo.txt) → treat as sovereign
    #  5. No Wikidata match → assume sovereign
    sovereign_countries = []
    territory_countries = []
    for country in countries:
        gnid = country["gnid"]
        wd   = wd_data.get(gnid, {})
        qid  = wd.get("qid", "")
        if "is_sovereign" not in wd:
            sovereign_countries.append(country)          # rule 5
        elif wd["is_sovereign"]:
            sovereign_countries.append(country)          # rule 1
        else:
            parent_qid = wd.get("parent_qid", "")
            if parent_qid and parent_qid == qid:
                territory_countries.append(country)      # rule 2: self-referential P17
            elif parent_qid and qid_to_gnid.get(parent_qid):
                territory_countries.append(country)      # rule 3: parent in dataset
            else:
                sovereign_countries.append(country)      # rule 4: unmapped parent

    # Group sovereign countries by continent gnid for header ordering
    sov_by_continent = defaultdict(list)
    for country in sovereign_countries:
        cont_gnid = _CONTINENT_CODE_TO_GNID.get(country.get("continent_code", ""), "")
        sov_by_continent[cont_gnid].append(country)

    # Sort continents in use alphabetically (NFD)
    continents_in_use = sorted(
        sov_by_continent.keys(),
        key=lambda gid: _title_sort_key(_lbl(gid, next(
            (c["name"] for c in _CONTINENTS if c["gnid"] == gid), gid
        ))),
    )

    # --- GeoNamesCountry enum: continent headers (A-Z) then sovereign states globally A-Z ---
    country_pvs = {}

    # 1. Continent headers first, sorted A-Z
    for cont_gnid in continents_in_use:
        cont_entry = next((c for c in _CONTINENTS if c["gnid"] == cont_gnid), None)
        cont_label = _lbl(cont_gnid, cont_entry["name"] if cont_entry else cont_gnid)
        cont_pv = {"title": normalize_text(cont_label), "meaning": f"gnf:{cont_gnid}"}
        cont_wd = wd_data.get(cont_gnid, {})
        if cont_entry:
            cont_pv["aliases"] = [cont_entry["code"]]
        country_pvs[cont_gnid] = cont_pv

    sssom_rows: list = []

    # 2. Sovereign states globally sorted A-Z
    for country in sorted(sovereign_countries, key=lambda c: _title_sort_key(_lbl(c["gnid"], c["name"]))):
        gnid      = country["gnid"]
        iso2      = country["iso2"]
        name      = country["name"]
        cont_gnid = _CONTINENT_CODE_TO_GNID.get(country.get("continent_code", ""), "")
        wd        = wd_data.get(gnid, {})
        qid       = wd.get("qid", "")
        label     = normalize_text(_lbl(gnid, name))
        desc      = normalize_text(wd.get("desc", "")) or None

        entry = add_permissible_value(
            country_pvs, gnid,
            title=label,
            meaning=f"gnf:{gnid}",
            description=desc,
            is_a=cont_gnid or None,
        )
        entry["aliases"] = [iso2]
        if qid:
            sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                               "object_id": f"wd:{qid}", "mapping_justification": "semapv:ManualMappingCuration",
                               "subject_label": label, "object_label": label})
        sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                           "object_id": f"iso:{iso2}", "mapping_justification": "semapv:ManualMappingCuration",
                           "subject_label": label, "object_label": iso2})

    # --- GeoNamesCountryExtended enum: territories only, inherits GeoNamesCountry ---
    extended_pvs = {}
    for country in sorted(territory_countries, key=lambda c: _title_sort_key(_lbl(c["gnid"], c["name"]))):
        gnid      = country["gnid"]
        iso2      = country["iso2"]
        name      = country["name"]
        cont_gnid = _CONTINENT_CODE_TO_GNID.get(country.get("continent_code", ""), "")
        wd        = wd_data.get(gnid, {})
        qid       = wd.get("qid", "")
        label     = normalize_text(_lbl(gnid, name))
        desc      = normalize_text(wd.get("desc", "")) or None

        # Parent sovereign state gnid (for is_a link into inherited GeoNamesCountry).
        # Skip self-referential P17 (parent_qid == own qid) — fall back to continent.
        parent_qid  = wd.get("parent_qid", "")
        parent_gnid = (qid_to_gnid.get(parent_qid, "")
                       if parent_qid and parent_qid != qid else "")

        entry = add_permissible_value(
            extended_pvs, gnid,
            title=label,
            meaning=f"gnf:{gnid}",
            description=desc,
            is_a=parent_gnid or cont_gnid or None,
        )
        entry["aliases"] = [iso2]
        if qid:
            sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                               "object_id": f"wd:{qid}", "mapping_justification": "semapv:ManualMappingCuration",
                               "subject_label": label, "object_label": label})
        sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                           "object_id": f"iso:{iso2}", "mapping_justification": "semapv:ManualMappingCuration",
                           "subject_label": label, "object_label": iso2})

    # --- GeoNamesContinent enum (standalone, sorted alphabetically) ---
    continents_sorted = sorted(_CONTINENTS, key=lambda c: _title_sort_key(_lbl(c["gnid"], c["name"])))
    continent_pvs = {}
    for cont in continents_sorted:
        gnid  = cont["gnid"]
        wd    = wd_data.get(gnid, {})
        qid   = wd.get("qid", "")
        label = normalize_text(_lbl(gnid, cont["name"]))

        entry = add_permissible_value(
            continent_pvs, gnid,
            title=label,
            meaning=f"gnf:{gnid}",
        )
        entry["aliases"] = [cont["code"]]
        if qid:
            sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                               "object_id": f"wd:{qid}", "mapping_justification": "semapv:ManualMappingCuration",
                               "subject_label": label, "object_label": label})

    # Also collect continent cross-refs embedded in country_pvs (built above loop)
    for cont_gnid2 in continents_in_use:
        cont_wd2 = wd_data.get(cont_gnid2, {})
        cont_qid2 = cont_wd2.get("qid", "")
        if cont_qid2:
            cont_lbl = normalize_text(next(
                (c["name"] for c in _CONTINENTS if c["gnid"] == cont_gnid2), cont_gnid2))
            sssom_rows.append({"subject_id": f"gnf:{cont_gnid2}", "predicate_id": "skos:exactMatch",
                               "object_id": f"wd:{cont_qid2}", "mapping_justification": "semapv:ManualMappingCuration",
                               "subject_label": cont_lbl, "object_label": cont_lbl})

    # Crosswalk + sovereignty stats
    n_sov     = len(sovereign_countries)
    n_ter     = len(territory_countries)
    n_c       = len(countries)
    n_matched = sum(1 for c in countries if wd_data.get(c["gnid"], {}).get("qid"))
    pct       = 100 * n_matched // n_c if n_c else 0
    print(f"  Crosswalk: {n_matched}/{n_c} countries ({pct}%) have Wikidata P1566 links")
    print(f"  Sovereignty: {n_sov} sovereign states, {n_ter} territories/dependencies")

    source_url = (source.get("reachable_from") or {}).get("source_ontology", _GEONAMES_COUNTRY_URL)
    enums_dict = {
        "GeoNamesCountry": {
            "name":        "GeoNamesCountry",
            "title":       "Country",
            "description": (
                f"{n_sov} sovereign states from GeoNames, sorted alphabetically. "
                "Code = geonameId; ISO alpha-2 in aliases and exact_mappings. "
                "Continent header entries provide is_a grouping."
            ),
            "permissible_values": country_pvs,
        },
    }
    if extended_pvs:
        enums_dict["GeoNamesCountryExtended"] = {
            "name":        "GeoNamesCountryExtended",
            "title":       "Country Territory",
            "inherits":    "GeoNamesCountry",
            "description": (
                f"{n_ter} dependent territories, overseas regions, and special "
                "administrative areas that have an ISO 3166-1 alpha-2 code but "
                "are not sovereign states (Wikidata P31 ≠ Q3624078/Q15634554). "
                "Inherits GeoNamesCountry so the complete list of sovereign states "
                "plus all territories is available in one enum; territory is_a "
                "links resolve to the administering sovereign state."
            ),
            "permissible_values": extended_pvs,
        }
    enums_dict["GeoNamesContinent"] = {
        "name":        "GeoNamesContinent",
        "title":       "Continent",
        "description": (
            "GeoNames continent entries. Code = geonameId; "
            "2-letter continent code in aliases."
        ),
        "permissible_values": continent_pvs,
    }

    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or "GeoNames Countries and Territories",
        description=(
            f"{n_sov} sovereign states, {n_ter} territories, and "
            f"{len(continents)} continents from GeoNames with Wikidata P1566 cross-references. "
            "Ocean entries available in GeoWikiOcean (source_geowiki.py)."
        ),
        license="CC-BY 4.0",
        prefixes=_SOURCE_PREFIXES,
        enums=enums_dict,
    )

    # Locale extensions (using Wikidata multilingual labels from P1566 enrichment)
    for lang in locales:
        if lang == "en":
            continue
        c_lang_pvs    = {}
        ext_lang_pvs  = {}
        cont_lang_pvs = {}
        for country in sovereign_countries:
            gnid = country["gnid"]
            lbl  = wd_data.get(gnid, {}).get("labels", {}).get(lang, "")
            if lbl:
                default = country_pvs.get(gnid, {}).get("title", "")
                if normalize_text(lbl) != default:
                    c_lang_pvs[gnid] = {"title": normalize_text(lbl)}
        for country in territory_countries:
            gnid = country["gnid"]
            lbl  = wd_data.get(gnid, {}).get("labels", {}).get(lang, "")
            if lbl:
                default = extended_pvs.get(gnid, {}).get("title", "")
                if normalize_text(lbl) != default:
                    ext_lang_pvs[gnid] = {"title": normalize_text(lbl)}
        for cont in continents_sorted:
            gnid = cont["gnid"]
            lbl  = wd_data.get(gnid, {}).get("labels", {}).get(lang, "")
            if lbl:
                default = continent_pvs.get(gnid, {}).get("title", "")
                if normalize_text(lbl) != default:
                    cont_lang_pvs[gnid] = {"title": normalize_text(lbl)}

        locale_enums = {}
        if c_lang_pvs:
            locale_enums["GeoNamesCountry"]   = {"permissible_values": c_lang_pvs}
        if ext_lang_pvs:
            locale_enums["GeoNamesCountryExtended"] = {"permissible_values": ext_lang_pvs}
        if cont_lang_pvs:
            locale_enums["GeoNamesContinent"] = {"permissible_values": cont_lang_pvs}
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
    log_extraction("GeoNamesCountry",   count=len(country_pvs))
    if extended_pvs:
        log_extraction("GeoNamesCountryExtended", count=len(extended_pvs))
    log_extraction("GeoNamesContinent", count=len(continent_pvs))

    if sssom_rows:
        write_sssom_mappings(_GEONAMES_SSSOM, sssom_rows, _SSSOM_PREFIXES,
                             _GEONAMES_SSSOM_ID, _GEONAMES_SSSOM_DESC)
        print(f"  SSSOM: {len(sssom_rows)} mappings written to {_GEONAMES_SSSOM}")


def _build_adm1_section(entries, wd_data, country_name, locales=None):
    """Build type-grouped ADM1 permissible_values for a single country.

    Groups entries by type word from Wikidata description (e.g. "province of
    Canada", "territory of Canada").  When 2+ distinct types have 2+ members,
    type-category headers are the enum roots; individual entries are sorted
    alphabetically immediately after their header.  Single-type countries get a
    flat sorted list.

    Returns (pvs, enum_title, sssom_rows).
      pvs:        ordered dict — type headers (no 'meaning') and province entries
                  (PV code = geonameId, has 'meaning' and 'aliases').
      enum_title: country-specific title e.g. "Province or Territory of Canada"
      sssom_rows: list of exactMatch dicts for the caller to write to SSSOM file.
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]
    sssom_rows: list = []

    def _lbl(entry):
        wd = wd_data.get(entry["gnid"], {})
        return (wd.get("labels", {}).get(primary_lang)
                or wd.get("labels", {}).get("en")
                or entry.get("name", ""))

    def _add_entry(pvs, entry, is_a=None):
        gnid  = entry["gnid"]
        wd    = wd_data.get(gnid, {})
        qid   = wd.get("qid", "")
        label = normalize_text(_lbl(entry))
        pv    = add_permissible_value(
            pvs, gnid,
            title=label,
            meaning=f"gnf:{gnid}",
            description=normalize_text(wd.get("desc", "")) or None,
            is_a=is_a,
        )
        pv["aliases"] = [entry["compound_code"]]
        sssom_rows.append({
            "subject_id":            f"gnf:{gnid}",
            "predicate_id":          "skos:broadMatch",
            "object_id":             "gno:A.ADM1",
            "mapping_justification": "semapv:ManualMappingCuration",
            "subject_label":         label,
            "object_label":          "",
        })
        if qid:
            sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                               "object_id": f"wd:{qid}", "mapping_justification": "semapv:ManualMappingCuration",
                               "subject_label": label, "object_label": label})

    # Bucket entries by type word from Wikidata description
    type_buckets = defaultdict(list)
    for entry in entries:
        wd    = wd_data.get(entry["gnid"], {})
        desc  = (wd.get("desc") or "").strip()
        m     = _TYPE_OF_RE.match(desc)
        tword = m.group(1).lower() if m else ""
        type_buckets[tword].append(entry)

    _MIN_GROUP = 2
    active     = {w: lst for w, lst in type_buckets.items()
                  if w and len(lst) >= _MIN_GROUP}
    has_groups = len(active) >= 2

    type_order  = sorted(active, key=lambda w: (-len(active[w]), w))
    type_counts = [(w, len(active[w])) for w in type_order]
    enum_title  = _adm1_enum_title(type_counts, country_name)

    pvs = {}
    if has_groups:
        for type_word in type_order:
            code = type_word.capitalize() + (f" of {country_name}" if country_name else "")
            pvs[code] = {"title": code}
            for entry in sorted(active[type_word],
                                key=lambda e: _title_sort_key(_lbl(e))):
                _add_entry(pvs, entry, is_a=code)

        ungrouped = [e for w, lst in type_buckets.items()
                     if w not in active for e in lst]
        for entry in sorted(ungrouped, key=lambda e: _title_sort_key(_lbl(e))):
            _add_entry(pvs, entry)
    else:
        for entry in sorted(entries, key=lambda e: _title_sort_key(_lbl(e))):
            _add_entry(pvs, entry)

    return pvs, enum_title, sssom_rows


def _build_adm1_yaml(key, source, data, locales=None):
    """Write sources/{key}.yaml for GeoNamesADM1 scope.

    Per-country: groups entries by Wikidata-description type (e.g. "province of
    Canada", "territory of Canada"); when 2+ distinct types have 2+ members each,
    type-category headers are inserted as the top-level permissible values with
    individual divisions sorted alphabetically beneath them.  When only one type
    is present the list is flat and sorted.  In both cases the country entry itself
    is NOT included — the type category (or the flat list) is the enumeration root.

    Global: flat unsorted listing of all ~4,000 divisions.
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]

    scope        = data.get("scope", "global")
    entries      = data.get("entries", [])
    wd_data      = data.get("wikidata", {})
    country_name = data.get("country_name", scope if scope != "global" else "")

    if scope != "global":
        pvs, enum_title, sssom_rows = _build_adm1_section(entries, wd_data, country_name, locales)
    else:
        # Global scope: flat, preserving download order

        def _lbl_g(entry):
            wd = wd_data.get(entry["gnid"], {})
            return (wd.get("labels", {}).get(primary_lang)
                    or wd.get("labels", {}).get("en")
                    or entry.get("name", ""))

        enum_title = "Province or State"
        pvs        = {}
        sssom_rows = []
        for entry in entries:
            gnid  = entry["gnid"]
            wd    = wd_data.get(gnid, {})
            qid   = wd.get("qid", "")
            label = normalize_text(_lbl_g(entry))
            pv    = add_permissible_value(
                pvs, gnid,
                title=label,
                meaning=f"gnf:{gnid}",
                description=normalize_text(wd.get("desc", "")) or None,
            )
            pv["aliases"] = [entry["compound_code"]]
            sssom_rows.append({
                "subject_id":            f"gnf:{gnid}",
                "predicate_id":          "skos:broadMatch",
                "object_id":             "gno:A.ADM1",
                "mapping_justification": "semapv:ManualMappingCuration",
                "subject_label":         label,
                "object_label":          "",
            })
            if qid:
                sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                                   "object_id": f"wd:{qid}", "mapping_justification": "semapv:ManualMappingCuration",
                                   "subject_label": label, "object_label": label})

    n       = len(entries)
    n_match = sum(1 for e in entries if wd_data.get(e["gnid"], {}).get("qid"))
    pct     = 100 * n_match // n if n else 0
    print(f"  Crosswalk: {n_match}/{n} ADM1 entries ({pct}%) have Wikidata P1566 links")

    source_url  = (source.get("reachable_from") or {}).get("source_ontology", _GEONAMES_ADM1_URL)
    scope_label = f"for {scope}" if scope != "global" else "(global)"
    enum_key    = f"GeoNamesAdm1{scope}" if scope != "global" else "GeoNamesAdm1"
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"GeoNames ADM1 {scope_label}",
        description=f"{n} first-order administrative divisions {scope_label} from GeoNames.",
        license="CC-BY 4.0",
        prefixes=_SOURCE_PREFIXES,
        enums={
            enum_key: {
                "name":        enum_key,
                "title":       enum_title,
                "permissible_values": pvs,
            },
        },
    )
    # Locale extensions — only when non-default locales are configured
    for lang in locales:
        if lang == "en":
            continue
        lang_pvs = {}
        for code, pv in pvs.items():
            lbl = wd_data.get(code, {}).get("labels", {}).get(lang, "")
            if lbl:
                default = pv.get("title", "")
                if normalize_text(lbl) != default:
                    lang_pvs[code] = {"title": normalize_text(lbl)}
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
        write_sssom_mappings(_GEONAMES_SSSOM, sssom_rows, _SSSOM_PREFIXES,
                             _GEONAMES_SSSOM_ID, _GEONAMES_SSSOM_DESC)
        print(f"  SSSOM: {len(sssom_rows)} mappings written to {_GEONAMES_SSSOM}")


def _build_adm2_yaml(key, source, data, locales=None):
    """Write sources/{key}.yaml for GeoNamesADM2 scope (per-country only).

    Produces a 3-level hierarchy that reuses the ADM1 permissible_value structure:
      Province/Territory type headers (e.g. "Province of Canada") — enum roots
        Province entries (gnid as PV code, matching GeoNamesADM1 PV codes)
          ADM2 entries (is_a = province gnid)

    ADM1 data comes from the cached GeoNames_{CC}_ADM1.zip payload; if absent,
    the province section is a flat alphabetical list without type grouping.
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]

    scope             = data.get("scope", "")
    entries           = data.get("entries", [])
    wd_data           = data.get("wikidata", {})
    adm1_entries      = data.get("adm1_entries", [])
    adm1_wd_data      = data.get("adm1_wd_data", {})
    adm1_country_name = data.get("adm1_country_name", scope)
    adm1_gnids        = data.get("adm1_gnids", {})   # adm1_code → gnid

    # If adm1_entries absent (old cached payload), load from ADM1 zip directly
    if not adm1_entries and scope:
        adm1_cache = _load_cached_adm_payload(f"GeoNames_ADM1_{scope}")
        if adm1_cache:
            adm1_entries      = adm1_cache.get("entries", [])
            adm1_wd_data      = adm1_cache.get("wikidata", {})
            adm1_country_name = adm1_cache.get("country_name", scope)

    # Build province/territory hierarchy (Province of Canada → Alberta → …)
    if adm1_entries:
        adm1_pvs, _adm1_title, _adm1_sssom = _build_adm1_section(
            adm1_entries, adm1_wd_data, adm1_country_name, locales
        )
    else:
        # No ADM1 data: build minimal gnid-keyed province headers
        adm1_pvs   = {}
        adm1_names = data.get("adm1_names", {})
        for adm1_code, gnid in sorted(adm1_gnids.items(), key=lambda x: x[1]):
            name = adm1_names.get(adm1_code, f"{scope}.{adm1_code}")
            adm1_pvs[gnid] = {
                "title":   normalize_text(name),
                "meaning": f"gnf:{gnid}",
                "aliases": [f"{scope}.{adm1_code}"],
            }

    # Group ADM2 entries by province gnid
    adm2_by_province = defaultdict(list)   # adm1_gnid → [adm2_entry, …]
    adm2_orphans     = []
    for e in entries:
        adm1_gnid = adm1_gnids.get(e["adm1_code"], "")
        if adm1_gnid and adm1_gnid in adm1_pvs:
            adm2_by_province[adm1_gnid].append(e)
        else:
            adm2_orphans.append(e)

    def _lbl_adm2(entry):
        gnid = entry["gnid"]
        wd = wd_data.get(gnid, {})
        return (wd.get("labels", {}).get(primary_lang)
                or wd.get("labels", {}).get("en")
                or entry.get("name", ""))

    # When ADM1 data is available, use inherits to bring province hierarchy into the
    # combined schema — only ADM2 entries go in pvs; province entries come via inherits.
    # When ADM1 data is absent (old cached payload / no separate ADM1 source), embed
    # minimal province headers directly so the enum is still self-contained.
    has_adm1   = bool(adm1_entries)
    sssom_rows = []

    pvs = {}
    for code, pv_data in adm1_pvs.items():
        if not has_adm1:
            pvs[code] = pv_data   # embed province/type headers (fallback mode)
        # Province entries have 'meaning'; type-category headers do not
        if "meaning" in pv_data:
            province_gnid = code   # PV code IS the gnid for GeoNames ADM1
            for entry in sorted(adm2_by_province.get(province_gnid, []),
                                 key=lambda e: _title_sort_key(_lbl_adm2(e))):
                gnid     = entry["gnid"]
                compound = entry["compound_code"]
                name     = entry["name"]
                wd       = wd_data.get(gnid, {})
                qid      = wd.get("qid", "")
                label    = normalize_text(wd.get("labels", {}).get(primary_lang)
                            or wd.get("labels", {}).get("en") or name)
                pv = add_permissible_value(
                    pvs, gnid,
                    title=label,
                    meaning=f"gnf:{gnid}",
                    description=normalize_text(wd.get("desc", "")) or None,
                    is_a=province_gnid,
                )
                pv["aliases"] = [compound]
                sssom_rows.append({
                    "subject_id":            f"gnf:{gnid}",
                    "predicate_id":          "skos:broadMatch",
                    "object_id":             "gno:A.ADM2",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":         label,
                    "object_label":          "",
                })
                if qid:
                    sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                                       "object_id": f"wd:{qid}", "mapping_justification": "semapv:ManualMappingCuration",
                                       "subject_label": label, "object_label": label})

    # Orphaned ADM2 entries (province gnid not in adm1_pvs): append sorted
    for entry in sorted(adm2_orphans, key=lambda e: _title_sort_key(_lbl_adm2(e))):
        gnid     = entry["gnid"]
        compound = entry["compound_code"]
        name     = entry["name"]
        wd       = wd_data.get(gnid, {})
        qid      = wd.get("qid", "")
        label    = normalize_text(wd.get("labels", {}).get(primary_lang)
                    or wd.get("labels", {}).get("en") or name)
        pv = add_permissible_value(
            pvs, gnid,
            title=label,
            meaning=f"gnf:{gnid}",
            description=normalize_text(wd.get("desc", "")) or None,
        )
        pv["aliases"] = [compound]
        sssom_rows.append({
            "subject_id":            f"gnf:{gnid}",
            "predicate_id":          "skos:broadMatch",
            "object_id":             "gno:A.ADM2",
            "mapping_justification": "semapv:ManualMappingCuration",
            "subject_label":         label,
            "object_label":          "",
        })
        if qid:
            sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                               "object_id": f"wd:{qid}", "mapping_justification": "semapv:ManualMappingCuration",
                               "subject_label": label, "object_label": label})

    n       = len(entries)
    n_match = sum(1 for e in entries if wd_data.get(e["gnid"], {}).get("qid"))
    pct     = 100 * n_match // n if n else 0
    print(f"  Crosswalk: {n_match}/{n} ADM2 entries ({pct}%) have Wikidata P1566 links")

    source_url = (source.get("reachable_from") or {}).get("source_ontology", _GEONAMES_ADM2_URL)
    enum_key   = f"GeoNamesAdm2{scope}"
    enum_dict  = {"name": enum_key, "title": "County or District"}
    if has_adm1:
        enum_dict["inherits"] = f"GeoNamesAdm1{scope}"
    enum_dict["permissible_values"] = pvs
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"GeoNames ADM2 for {scope}",
        description=f"{n} second-order administrative divisions for {scope} from GeoNames.",
        license="CC-BY 4.0",
        prefixes=_SOURCE_PREFIXES,
        enums={enum_key: enum_dict},
    )
    # Locale extensions for ADM2 entries (ADM1 locales live in the companion yaml)
    for lang in locales:
        if lang == "en":
            continue
        lang_pvs = {}
        for gnid, pv in pvs.items():
            lbl = wd_data.get(gnid, {}).get("labels", {}).get(lang, "")
            if lbl:
                default = pv.get("title", "")
                if normalize_text(lbl) != default:
                    lang_pvs[gnid] = {"title": normalize_text(lbl)}
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
        write_sssom_mappings(_GEONAMES_SSSOM, sssom_rows, _SSSOM_PREFIXES,
                             _GEONAMES_SSSOM_ID, _GEONAMES_SSSOM_DESC)
        print(f"  SSSOM: {len(sssom_rows)} mappings written to {_GEONAMES_SSSOM}")

    # Companion ADM1 yaml: cascade up the inherits chain (ADM2 → ADM1)
    if scope and adm1_entries:
        companion1_path = f"sources/GeoNames_ADM1_{scope}.yaml"
        if not os.path.exists(companion1_path):
            adm1_data = {
                "scope":        scope,
                "entries":      adm1_entries,
                "wikidata":     adm1_wd_data,
                "country_name": adm1_country_name,
            }
            print(f"  Companion {companion1_path}: building from cache ...")
            _build_adm1_yaml(f"GeoNames_ADM1_{scope}", {}, adm1_data, locales)
        else:
            print(f"  Companion {companion1_path} already exists; skipping.")


def _build_adm3_yaml(key, source, data, locales=None, only_levels=None):
    """Write sources/{key}.yaml for GeoNamesADM3 or GeoNamesADM4 scope (per-country only).

    Groups entries by ADM2 compound code (synthetic headers as is_a parents).
    compound_code is stored in aliases; geonameId is the PV code.

    only_levels: set of level names to emit, e.g. {"ADM3"} or {"ADM4"}.
    None means emit all levels present in data (backward-compatible default).

    Side-effects: companion yaml files for the full ADM inherits chain are written
    for any ancestor level whose yaml is absent but whose cached payload exists:
    - ADM4-only: writes GeoNames_ADM3_{scope}.yaml companion first
    - ADM3 or ADM4: then writes GeoNames_ADM2_{scope}.yaml (if ADM2 cached), which
      in turn cascades to write GeoNames_ADM1_{scope}.yaml (if ADM1 cached)
    Full chain ADM4 → ADM3 → ADM2 → ADM1 is satisfied automatically when all
    intermediate caches are present in GeoNames.zip.
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]

    scope                 = data.get("scope", "")
    levels                = data.get("levels", ["ADM3"])
    entries               = data.get("entries", [])
    wd_data               = data.get("wikidata", {})
    adm2_compound_to_gnid = data.get("adm2_compound_to_gnid", {})
    adm2_gnid_to_label    = {}
    adm1_code_to_gnid     = {}   # adm1_code → gnid; fallback for city-states with no ADM2

    # Load ADM2 cache for labels (always) and compound→gnid mapping if absent from payload
    if scope:
        adm2_cache = _load_cached_adm_payload(f"GeoNames_ADM2_{scope}")
        if adm2_cache:
            adm2_wd = adm2_cache.get("wikidata", {})
            for e in adm2_cache.get("entries", []):
                ccode = e["compound_code"]
                gnid  = e["gnid"]
                if not adm2_compound_to_gnid:
                    adm2_compound_to_gnid[ccode] = gnid
                wd_e = adm2_wd.get(gnid, {})
                lbl  = (wd_e.get("labels", {}).get(primary_lang)
                        or wd_e.get("labels", {}).get("en")
                        or e.get("name", ""))
                adm2_gnid_to_label[gnid] = normalize_text(lbl) if lbl else ""
            # adm1_gnids lets us resolve compound-code parent keys for city-states
            # (e.g. US.DC.001) that have ADM3 entries but no ADM2 in GeoNames.
            adm1_code_to_gnid = adm2_cache.get("adm1_gnids", {})
        elif not adm2_compound_to_gnid:
            print(f"  Note: no cached GeoNames_ADM2_{scope} — ADM3 parent keys will use "
                  f"compound codes (run '-a GeoNames_ADM2_{scope}' first for gnid keys).",
                  file=sys.stderr)

    # Separate ADM3 and ADM4 entries
    adm3_entries = [e for e in entries if e.get("feat_code") == "ADM3"]
    adm4_entries = [e for e in entries if e.get("feat_code") == "ADM4"]

    # When the ADM2 compound→gnid mapping is available, the enum can inherit
    # GeoNamesAdm2{scope} so is_a links resolve to parent ADM2 entries.  Without
    # ADM2 data, synthetic title-only parent headers are embedded directly.
    has_adm2   = bool(adm2_compound_to_gnid)
    sssom_rows = []

    def _build_pvs(entry_list, parent_key_fn):
        """Build pvs for a list of entries.

        When has_adm2 is True, synthetic parent headers are omitted because the
        parent ADM2 entries come from the inherited GeoNamesAdm2{scope} enum.
        When has_adm2 is False, title-only headers are embedded directly so the
        enum is still navigable as a self-contained file.
        """
        # Group entries by parent key, sorted by compound code within each group
        parent_groups = defaultdict(list)
        for e in sorted(entry_list, key=lambda e: e["compound_code"]):
            pk = parent_key_fn(e)
            parent_groups[pk].append(e)

        pvs = {}
        for pk in sorted(parent_groups.keys()):
            # If the parent key is still a compound code (ADM2 gnid lookup failed),
            # fall back to the ADM1 gnid for city-states / federal districts that
            # have ADM3 entries but no ADM2 rows (e.g. US.DC.001 → DC gnid 4138106).
            resolved_pk = pk
            if has_adm2 and "." in pk:
                parts = pk.split(".")
                adm1_code = parts[1] if len(parts) > 1 else ""
                resolved_pk = adm1_code_to_gnid.get(adm1_code, pk)
            if not has_adm2:
                pk_title = adm2_gnid_to_label.get(pk) or pk
                pvs[resolved_pk] = {"title": pk_title}
            for entry in parent_groups[pk]:
                gnid     = entry["gnid"]
                compound = entry["compound_code"]
                name     = entry["name"]
                wd       = wd_data.get(gnid, {})
                qid      = wd.get("qid", "")
                label    = normalize_text(wd.get("labels", {}).get(primary_lang)
                            or wd.get("labels", {}).get("en") or name)

                feat_code = entry.get("feat_code", "")
                gno_code  = f"gno:A.{feat_code}" if feat_code else "gno:A.ADM3"
                pv = add_permissible_value(
                    pvs, gnid,
                    title=label,
                    meaning=f"gnf:{gnid}",
                    description=normalize_text(wd.get("desc", "")) or None,
                    is_a=resolved_pk,
                )
                pv["aliases"] = [compound]
                if gno_code:
                    sssom_rows.append({
                        "subject_id":            f"gnf:{gnid}",
                        "predicate_id":          "skos:broadMatch",
                        "object_id":             gno_code,
                        "mapping_justification": "semapv:ManualMappingCuration",
                        "subject_label":         label,
                        "object_label":          "",
                    })
                if qid:
                    sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                                       "object_id": f"wd:{qid}", "mapping_justification": "semapv:ManualMappingCuration",
                                       "subject_label": label, "object_label": label})
        return pvs

    enums = {}

    # Compute ADM3 pvs whenever entries exist (needed for enum output OR companion yaml)
    adm3_pvs: dict = {}
    if adm3_entries:
        adm3_pvs = _build_pvs(
            adm3_entries,
            lambda e: (adm2_compound_to_gnid.get(f"{scope}.{e['adm1_code']}.{e['adm2_code']}")
                       or f"{scope}.{e['adm1_code']}.{e['adm2_code']}"),
        )
    if adm3_pvs and (only_levels is None or "ADM3" in only_levels):
        enum3_key  = f"GeoNamesAdm3{scope}"
        enum3_dict = {"name": enum3_key, "title": "Municipality"}
        if has_adm2:
            enum3_dict["inherits"] = f"GeoNamesAdm2{scope}"
        enum3_dict["permissible_values"] = adm3_pvs
        enums[enum3_key] = enum3_dict
        n3 = len(adm3_entries)
        m3 = sum(1 for e in adm3_entries if wd_data.get(e["gnid"], {}).get("qid"))
        print(f"  Crosswalk: {m3}/{n3} ADM3 entries ({100*m3//n3 if n3 else 0}%) "
              f"have Wikidata P1566 links")

    if adm4_entries and (only_levels is None or "ADM4" in only_levels):
        adm4_pvs  = _build_pvs(
            adm4_entries,
            lambda e: (adm2_compound_to_gnid.get(f"{scope}.{e['adm1_code']}.{e['adm2_code']}")
                       or f"{scope}.{e['adm1_code']}.{e['adm2_code']}"),
        )
        enum4_key  = f"GeoNamesAdm4{scope}"
        enum4_dict = {"name": enum4_key, "title": "Sub-municipality"}
        if adm3_entries:
            enum4_dict["inherits"] = f"GeoNamesAdm3{scope}"
        elif has_adm2:
            enum4_dict["inherits"] = f"GeoNamesAdm2{scope}"
        enum4_dict["permissible_values"] = adm4_pvs
        enums[enum4_key] = enum4_dict
        n4 = len(adm4_entries)
        m4 = sum(1 for e in adm4_entries if wd_data.get(e["gnid"], {}).get("qid"))
        print(f"  Crosswalk: {m4}/{n4} ADM4 entries ({100*m4//n4 if n4 else 0}%) "
              f"have Wikidata P1566 links")

    if not enums:
        print(f"  Warning: no ADM3/ADM4 entries to write for {scope}", file=sys.stderr)
        return

    if only_levels == {"ADM3"}:
        lv_str = "ADM3"
    elif only_levels == {"ADM4"}:
        lv_str = "ADM4"
    else:
        lv_str = "/".join(levels)
    source_url = (source.get("reachable_from") or {}).get(
        "source_ontology", _GEONAMES_CC_URL.format(CC=scope))
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"GeoNames {lv_str} for {scope}",
        description=(
            f"{len(entries)} {lv_str} administrative divisions for {scope} from GeoNames."
        ),
        license="CC-BY 4.0",
        prefixes=_SOURCE_PREFIXES,
        enums=enums,
    )
    # Locale extensions for ADM3/ADM4 enums
    for lang in locales:
        if lang == "en":
            continue
        locale_enums = {}
        for ek, ev in enums.items():
            lang_pvs = {}
            for gnid, pv in ev["permissible_values"].items():
                lbl = wd_data.get(gnid, {}).get("labels", {}).get(lang, "")
                if lbl:
                    default = pv.get("title", "")
                    if normalize_text(lbl) != default:
                        lang_pvs[gnid] = {"title": normalize_text(lbl)}
            if lang_pvs:
                locale_enums[ek] = {"permissible_values": lang_pvs}
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
    for ek, ev in enums.items():
        log_extraction(ek, count=len(ev["permissible_values"]))

    if sssom_rows:
        write_sssom_mappings(_GEONAMES_SSSOM, sssom_rows, _SSSOM_PREFIXES,
                             _GEONAMES_SSSOM_ID, _GEONAMES_SSSOM_DESC)
        print(f"  SSSOM: {len(sssom_rows)} mappings written to {_GEONAMES_SSSOM}")

    # Companion ADM3 yaml: written as a side-effect when processing ADM4-only, so that
    # GeoNamesAdm4{scope}'s inherits: GeoNamesAdm3{scope} dependency resolves
    # automatically without requiring a separate GeoNames_ADM3_{scope} config entry.
    if only_levels == {"ADM4"} and adm3_pvs:
        companion_path = f"sources/GeoNames_ADM3_{scope}.yaml"
        if not os.path.exists(companion_path):
            enum3_key  = f"GeoNamesAdm3{scope}"
            enum3_dict = {"name": enum3_key, "title": "Municipality"}
            if has_adm2:
                enum3_dict["inherits"] = f"GeoNamesAdm2{scope}"
            enum3_dict["permissible_values"] = adm3_pvs
            c_schema = make_config_schema(
                id=source_url,
                name=f"GeoNames_ADM3_{scope}",
                title=f"GeoNames ADM3 for {scope}",
                description=(
                    f"Third-order administrative divisions for {scope} from GeoNames. "
                    f"Auto-generated as companion to GeoNames_ADM4_{scope}."
                ),
                license="CC-BY 4.0",
                prefixes=_SOURCE_PREFIXES,
                enums={enum3_key: enum3_dict},
            )
            for lang in locales:
                if lang == "en":
                    continue
                lang_pvs = {}
                for gnid, pv in adm3_pvs.items():
                    lbl = wd_data.get(gnid, {}).get("labels", {}).get(lang, "")
                    if lbl:
                        default = pv.get("title", "")
                        if normalize_text(lbl) != default:
                            lang_pvs[gnid] = {"title": normalize_text(lbl)}
                if lang_pvs:
                    ext = _make_locale_extensions(
                        source_url, f"GeoNames_ADM3_{scope}", "", lang,
                        enums={enum3_key: {"permissible_values": lang_pvs}},
                    )
                    existing = c_schema.get("extensions", {}).get("locales", {}).get("value", {})
                    existing.update(ext.get("locales", {}).get("value", {}))
                    c_schema["extensions"] = {"locales": {"tag": "locales", "value": existing}}
            with open(companion_path, "w") as f:
                yaml.dump(c_schema, f, Dumper=IndentedDumper, default_flow_style=False,
                          sort_keys=False)
            log_extraction(enum3_key, count=len(adm3_pvs))
            print(f"  Companion {companion_path} written ({len(adm3_pvs)} ADM3 terms) "
                  f"to satisfy GeoNamesAdm4{scope} inherits dependency.")
        else:
            print(f"  Companion {companion_path} already exists; skipping.")

    # Companion ADM2 yaml: cascade up the inherits chain (ADM3 → ADM2 → ADM1)
    # _build_adm2_yaml will itself cascade to ADM1 if its cache is present.
    if scope:
        companion2_path = f"sources/GeoNames_ADM2_{scope}.yaml"
        if not os.path.exists(companion2_path):
            adm2_payload = _load_cached_adm_payload(f"GeoNames_ADM2_{scope}")
            if adm2_payload:
                print(f"  Companion {companion2_path}: building from cache ...")
                _build_adm2_yaml(f"GeoNames_ADM2_{scope}", {}, adm2_payload, locales)
            else:
                print(f"  Note: GeoNames_ADM2_{scope} not in cache; "
                      f"companion ADM2 yaml skipped "
                      f"(run '-f GeoNames_ADM2_{scope}' to fetch).", file=sys.stderr)
        else:
            print(f"  Companion {companion2_path} already exists; skipping.")


def _build_ppl_yaml(key, source, data, locales=None):
    """Write sources/{key}.yaml for GeoNamesSettlement scope (per-country only).

    Groups entries by ADM1 compound code (synthetic headers as is_a parents).
    ADM1 header titles use the human-readable province/state name from
    admin1CodesASCII.txt (stored in payload["adm1_names"]).
    Entries are sorted alphabetically within each ADM1 group.
    Each settlement gets an aliases entry with the ADM location path
    ({CC}.{ADM1}.{ADM2} or {CC}.{ADM1}) to provide location context;
    note this is NOT the settlement's own code — geonameId is the unique ID.
    Historical entries (PPLH, PPLCH, PPLQ, PPLW) are marked status: historical.
    Users can suppress them with minus: {status: [historical]} in harvester_config.yaml.
    """
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]

    scope                 = data.get("scope", "")
    entries               = data.get("entries", [])
    wd_data               = data.get("wikidata", {})
    adm1_names            = data.get("adm1_names", {})            # adm1_code → province/state name
    adm1_gnids            = data.get("adm1_gnids", {})            # adm1_code → GeoNames ID
    adm2_compound_to_gnid = data.get("adm2_compound_to_gnid", {}) # compound_code → GeoNames ID

    # Collect unique ADM1 compound codes for synthetic headers
    adm1_compounds = {}
    for e in entries:
        if e.get("adm1_code"):
            adm1_compounds[f"{scope}.{e['adm1_code']}"] = e["adm1_code"]

    # Group entries by ADM1 compound code for interleaved header+children output
    adm1_entry_groups = defaultdict(list)
    no_adm1_entries   = []
    for e in entries:
        if e.get("adm1_code"):
            adm1_entry_groups[f"{scope}.{e['adm1_code']}"].append(e)
        else:
            no_adm1_entries.append(e)

    def _lbl(entry):
        gnid = entry["gnid"]
        wd   = wd_data.get(gnid, {})
        return (wd.get("labels", {}).get(primary_lang)
                or wd.get("labels", {}).get("en")
                or entry.get("name", ""))

    sssom_rows = []
    pvs = {}
    for ac in sorted(adm1_compounds):
        raw_code = adm1_compounds[ac]
        name     = adm1_names.get(raw_code, ac)   # province name, or fall back to compound code
        pvs[ac]  = {"title": normalize_text(name)}

        for entry in sorted(adm1_entry_groups[ac], key=lambda e: _title_sort_key(_lbl(e))):
            gnid      = entry["gnid"]
            wd        = wd_data.get(gnid, {})
            qid       = wd.get("qid", "")
            label     = normalize_text(_lbl(entry))
            adm1_code = entry.get("adm1_code", "")
            adm2_code = entry.get("adm2_code", "")

            # Location path alias: shows which admin division the settlement is in.
            # This is NOT the settlement's own code (geonameId is the unique ID).
            if adm1_code and adm2_code:
                location_alias = f"{scope}.{adm1_code}.{adm2_code}"
            elif adm1_code:
                location_alias = f"{scope}.{adm1_code}"
            else:
                location_alias = None

            feat_code = entry.get("feat_code", "")
            gno_code  = f"gno:P.{feat_code}" if feat_code else None
            pv = add_permissible_value(
                pvs, gnid,
                title=label,
                meaning=f"gnf:{gnid}",
                description=normalize_text(wd.get("desc", "")) or None,
                is_a=ac,
            )
            if location_alias:
                pv["aliases"] = [location_alias]
            if entry.get("is_historical"):
                pv["status"] = "historical"
            if gno_code:
                sssom_rows.append({
                    "subject_id":            f"gnf:{gnid}",
                    "predicate_id":          "skos:broadMatch",
                    "object_id":             gno_code,
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":         label,
                    "object_label":          "",
                })
            if qid:
                sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                                   "object_id": f"wd:{qid}", "mapping_justification": "semapv:ManualMappingCuration",
                                   "subject_label": label, "object_label": label})
            # skos:broader: settlement → containing ADM2 (if gnid known from cached ADM2 payload)
            adm2_gnid = (adm2_compound_to_gnid.get(f"{scope}.{adm1_code}.{adm2_code}")
                         if adm1_code and adm2_code else None)
            if adm2_gnid:
                sssom_rows.append({
                    "subject_id":            f"gnf:{gnid}",
                    "predicate_id":          "skos:broader",
                    "object_id":             f"gnf:{adm2_gnid}",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":         label,
                    "object_label":          "",
                })
            # skos:broader: settlement → containing ADM1 (always available when adm1_code is known)
            adm1_gnid = adm1_gnids.get(adm1_code) if adm1_code else None
            if adm1_gnid:
                sssom_rows.append({
                    "subject_id":            f"gnf:{gnid}",
                    "predicate_id":          "skos:broader",
                    "object_id":             f"gnf:{adm1_gnid}",
                    "mapping_justification": "semapv:ManualMappingCuration",
                    "subject_label":         label,
                    "object_label":          "",
                })

    # Entries with no ADM1 code: append sorted at the end
    for entry in sorted(no_adm1_entries, key=lambda e: _title_sort_key(_lbl(e))):
        gnid  = entry["gnid"]
        wd    = wd_data.get(gnid, {})
        qid   = wd.get("qid", "")
        label = normalize_text(_lbl(entry))
        pv = add_permissible_value(
            pvs, gnid,
            title=label,
            meaning=f"gnf:{gnid}",
            description=normalize_text(wd.get("desc", "")) or None,
        )
        if entry.get("is_historical"):
            pv["status"] = "deprecated"
        if qid:
            sssom_rows.append({"subject_id": f"gnf:{gnid}", "predicate_id": "skos:exactMatch",
                               "object_id": f"wd:{qid}", "mapping_justification": "semapv:ManualMappingCuration",
                               "subject_label": label, "object_label": label})

    n       = len(entries)
    n_hist  = sum(1 for e in entries if e.get("is_historical"))
    n_match = sum(1 for e in entries if wd_data.get(e["gnid"], {}).get("qid"))
    pct     = 100 * n_match // n if n else 0
    print(f"  Settlement: {n - n_hist} current + {n_hist} historical entries")
    print(f"  Crosswalk: {n_match}/{n} entries ({pct}%) have Wikidata P1566 links")

    source_url = (source.get("reachable_from") or {}).get(
        "source_ontology", _GEONAMES_CC_URL.format(CC=scope))
    enum_key = f"GeoNamesSettlement{scope}"
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or f"GeoNames Settlements for {scope}",
        description=(
            f"{n - n_hist} current + {n_hist} historical populated places for {scope} from GeoNames. "
            "Historical entries are marked status: historical."
        ),
        license="CC-BY 4.0",
        prefixes=_SOURCE_PREFIXES,
        enums={
            enum_key: {
                "name":        enum_key,
                "title":       "Town or City",
                "permissible_values": pvs,
            },
        },
    )
    # Locale extensions — only settlement entries (gnid keys); ADM1 headers use plain names
    for lang in locales:
        if lang == "en":
            continue
        lang_pvs = {}
        for gnid, pv in pvs.items():
            lbl = wd_data.get(gnid, {}).get("labels", {}).get(lang, "")
            if lbl:
                default = pv.get("title", "")
                if normalize_text(lbl) != default:
                    lang_pvs[gnid] = {"title": normalize_text(lbl)}
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
        write_sssom_mappings(_GEONAMES_SSSOM, sssom_rows, _SSSOM_PREFIXES,
                             _GEONAMES_SSSOM_ID, _GEONAMES_SSSOM_DESC)
        print(f"  SSSOM: {len(sssom_rows)} mappings written to {_GEONAMES_SSSOM}")


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _fetch_countries_payload(locales, use_qlever=False):
    """Download GeoNames countries + enrich all entries with Wikidata P1566.

    Oceans are excluded: they do not appear in GeoNames flat files and the
    GeoNames IDs for oceans are not reliably captured in P1566.  Use
    source_geowiki.py for ocean entries (queried via P31=Q9430 from Wikidata).
    """
    source_modified = _geonames_head_date(_GEONAMES_COUNTRY_URL)
    countries = _fetch_geonames_countries()

    all_gnids = [c["gnid"] for c in countries]
    all_gnids += [c["gnid"] for c in _CONTINENTS]

    print(f"  Enriching {len(all_gnids)} GeoNames IDs with Wikidata P1566 ...")
    wd_data = _enrich_with_wikidata(all_gnids, locales=locales, use_qlever=use_qlever)

    # Annotate each country's wd_data entry with sovereignty (P31) and parent (P17)
    country_qids = [wd_data[c["gnid"]]["qid"] for c in countries
                    if wd_data.get(c["gnid"], {}).get("qid")]
    if country_qids:
        print(f"  Enriching {len(country_qids)} country QIDs with Wikidata sovereignty info ...")
        qid_to_gnid = {v["qid"]: k for k, v in wd_data.items() if v.get("qid")}
        sovereignty = _enrich_sovereignty(country_qids)
        for qid, sov in sovereignty.items():
            gnid = qid_to_gnid.get(qid)
            if gnid and gnid in wd_data:
                wd_data[gnid]["is_sovereign"] = sov["is_sovereign"]
                wd_data[gnid]["parent_qid"]   = sov.get("parent_qid", "")

    return {
        "type":            "countries",
        "countries":       countries,
        "continents":      _CONTINENTS,
        "wikidata":        wd_data,
        "source_modified": source_modified,
    }


def _fetch_adm1_payload(cc, locales, use_qlever=False):
    """Download GeoNames ADM1 (optionally filtered by country cc) + Wikidata."""
    country_filter = {cc.upper()} if cc else None
    if not cc:
        print("  Note: global GeoNames ADM1 will query ~4,000 Wikidata P1566 lookups.",
              file=sys.stderr)
    source_modified = _geonames_head_date(_GEONAMES_ADM1_URL)
    entries = _fetch_geonames_adm1(country_filter=country_filter)
    gnids   = [e["gnid"] for e in entries]

    if cc and gnids:
        wd_data = _enrich_with_alternatenames_then_sparql(
            cc, gnids, locales, use_qlever=use_qlever)
    elif gnids:
        print(f"  Enriching {len(gnids)} ADM1 IDs with Wikidata P1566 ...")
        wd_data = _enrich_with_wikidata(gnids, locales=locales, use_qlever=use_qlever)
    else:
        wd_data = {}

    payload = {
        "type":            "adm1",
        "scope":           cc.upper() if cc else "global",
        "entries":         entries,
        "wikidata":        wd_data,
        "source_modified": source_modified,
    }
    # Attempt to include country name/gnid for per-country scope
    if cc:
        try:
            countries = _fetch_geonames_countries()
            for country in countries:
                if country["iso2"].upper() == cc.upper():
                    payload["country_gnid"] = country["gnid"]
                    payload["country_name"] = country["name"]
                    break
        except Exception:
            pass
    return payload


def _fetch_adm2_payload(cc, locales, use_qlever=False):
    """Download GeoNames ADM2 for country cc + Wikidata enrichment.

    Also loads the cached ADM1 payload (GeoNames_{CC}_ADM1.zip) so that
    _build_adm2_yaml can embed the full province/territory hierarchy.  If no
    cached ADM1 exists the province section falls back to a flat list.
    """
    source_modified = _geonames_head_date(_GEONAMES_ADM2_URL)
    entries = _fetch_geonames_adm2({cc.upper()})
    gnids   = [e["gnid"] for e in entries]

    if gnids:
        wd_data = _enrich_with_alternatenames_then_sparql(
            cc, gnids, locales, use_qlever=use_qlever)
    else:
        wd_data = {}

    # Always fetch ADM1 flat file for names + gnids (fast, authoritative)
    adm1_rows  = _fetch_geonames_adm1(country_filter={cc.upper()})
    adm1_names = {e["adm1_code"]: e["name"] for e in adm1_rows}
    adm1_gnids = {e["adm1_code"]: e["gnid"] for e in adm1_rows}

    # Load cached ADM1 Wikidata payload for province type grouping.
    # Auto-fetch if absent (inherits chain; zip only, no config entry).
    adm1_key = f"GeoNames_ADM1_{cc.upper()}"
    if not _entry_exists(adm1_key):
        print(f"  Auto-fetching {adm1_key} (inherits chain; no config entry) ...")
        _save_json(adm1_key, _fetch_adm1_payload(cc, locales, use_qlever=use_qlever))
    adm1_payload = _load_cached_adm_payload(adm1_key)
    if adm1_payload:
        adm1_entries      = adm1_payload.get("entries", [])
        adm1_wd_data      = adm1_payload.get("wikidata", {})
        adm1_country_name = adm1_payload.get("country_name", "")
        print(f"  Loaded ADM1 data from cached {adm1_key} for province hierarchy.")
    else:
        # Fallback only if auto-fetch failed (e.g. network error)
        adm1_entries      = adm1_rows
        adm1_wd_data      = {}
        adm1_country_name = ""

    return {
        "type":              "adm2",
        "scope":             cc.upper(),
        "entries":           entries,
        "wikidata":          wd_data,
        "adm1_entries":      adm1_entries,
        "adm1_wd_data":      adm1_wd_data,
        "adm1_country_name": adm1_country_name,
        "adm1_names":        adm1_names,
        "adm1_gnids":        adm1_gnids,
        "source_modified":   source_modified,
    }


def _fetch_ppl_payload(cc, locales, use_qlever=False):
    """Download GeoNames PPL entries for country cc via per-country allCountries zip.

    Also fetches admin1CodesASCII.txt to capture ADM1 names for synthetic headers.
    """
    cc      = cc.upper()
    source_modified = _geonames_head_date(_GEONAMES_CC_URL.format(CC=cc))
    entries = _fetch_geonames_ppl(cc)

    # Fetch ADM1 names and gnids for synthetic headers + skos:broader SSSOM links
    adm1_rows  = _fetch_geonames_adm1(country_filter={cc})
    adm1_names = {e["adm1_code"]: e["name"] for e in adm1_rows}   # e.g. {"07": "Noord-Holland"}
    adm1_gnids = {e["adm1_code"]: e["gnid"] for e in adm1_rows}   # e.g. {"07": "2749879"}

    # Load cached ADM2 compound_code → gnid for skos:broader settlement → ADM2 links
    adm2_key              = f"GeoNames_ADM2_{cc}"
    adm2_payload          = _load_cached_adm_payload(adm2_key)
    adm2_compound_to_gnid = {}
    if adm2_payload:
        for e in adm2_payload.get("entries", []):
            adm2_compound_to_gnid[e["compound_code"]] = e["gnid"]
        print(f"  Loaded ADM2 gnid map ({len(adm2_compound_to_gnid)} entries) "
              f"from cached {adm2_key}.zip for SSSOM broader links.")

    gnids = [e["gnid"] for e in entries]
    if gnids:
        wd_data = _enrich_with_alternatenames_then_sparql(
            cc, gnids, locales, use_qlever=use_qlever)
    else:
        wd_data = {}

    return {
        "type":                   "ppl",
        "scope":                  cc,
        "entries":                entries,
        "wikidata":               wd_data,
        "adm1_names":             adm1_names,
        "adm1_gnids":             adm1_gnids,
        "adm2_compound_to_gnid":  adm2_compound_to_gnid,
        "source_modified":        source_modified,
    }


def _ensure_ancestor_caches_gn(cc, locales, use_qlever=False):
    """Fetch and cache GeoNames ADM1 and ADM2 for *cc* if not already cached.

    Called automatically at the start of every ADM3/ADM4 fetch (covers both -f and
    -a paths).  _fetch_adm2_payload also independently auto-fetches ADM1 if absent,
    so the per-level cascade holds even when ADM2 is fetched directly.

    No config entry is written (config_file omitted → zip-only).  ADM1 is fetched
    before ADM2 because _fetch_adm2_payload loads the ADM1 cache to build the
    province hierarchy embedded in the ADM2 output.
    """
    cc = cc.upper()
    adm1_key = f"GeoNames_ADM1_{cc}"
    if not _entry_exists(adm1_key):
        print(f"  Auto-fetching {adm1_key} (inherits chain; no config entry) ...")
        _save_json(adm1_key, _fetch_adm1_payload(cc, locales, use_qlever=use_qlever))
    adm2_key = f"GeoNames_ADM2_{cc}"
    if not _entry_exists(adm2_key):
        print(f"  Auto-fetching {adm2_key} (inherits chain; no config entry) ...")
        _save_json(adm2_key, _fetch_adm2_payload(cc, locales, use_qlever=use_qlever))


def _fetch_adm34_payload(cc, levels, locales, use_qlever=False):
    """Download GeoNames ADM3/ADM4 for country cc via per-country allCountries zip.

    Auto-fetches and caches ADM1 and ADM2 (zip only, no config entry) if absent,
    so that parent compound_code → gnid resolution and the companion yaml cascade
    have everything they need.
    """
    cc      = cc.upper()
    _ensure_ancestor_caches_gn(cc, locales, use_qlever)
    source_modified = _geonames_head_date(_GEONAMES_CC_URL.format(CC=cc))
    entries = _fetch_geonames_adm34(cc, levels=levels)
    gnids   = [e["gnid"] for e in entries]

    # Skip alternateNames for ADM3/ADM4: wkdt coverage is minimal at this level
    # (~0% match rate vs 22 MB download). Use P1566 SPARQL directly.
    if gnids:
        wd_data = _enrich_with_wikidata(gnids, locales=locales, use_qlever=use_qlever)
    else:
        wd_data = {}

    # Load cached ADM2 payload for compound_code → gnid mapping
    adm2_key     = f"GeoNames_ADM2_{cc}"
    adm2_payload = _load_cached_adm_payload(adm2_key)
    adm2_compound_to_gnid = {}
    if adm2_payload:
        for e in adm2_payload.get("entries", []):
            adm2_compound_to_gnid[e["compound_code"]] = e["gnid"]
        print(f"  Loaded ADM2 gnid map ({len(adm2_compound_to_gnid)} entries) "
              f"from cached {adm2_key}.zip.")
    else:
        print(f"  Note: no cached {adm2_key}.zip — ADM3 parent keys will use "
              f"compound codes (run '-a GeoNames_ADM2_{cc}' first for gnid keys).",
              file=sys.stderr)

    return {
        "type":                   "adm34",
        "scope":                  cc,
        "levels":                 list(levels),
        "entries":                entries,
        "wikidata":               wd_data,
        "adm2_compound_to_gnid":  adm2_compound_to_gnid,
        "source_modified":        source_modified,
    }


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
    key = "GeoNames_countries"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales    = config.get("locales") or ["en"]
    use_qlever = bool(config.get("apis", {}).get("qlever"))
    print(f"  Fetching GeoNames countries + Wikidata enrichment (locales: {locales}) ...")
    payload = _fetch_countries_payload(locales, use_qlever=use_qlever)
    _save_json(key, payload)

    entry = make_source_entry(
        key, _GEONAMES_COUNTRY_URL, "GeoNamesCountries", "zip",
        title="GeoNames Countries, Continents, and Oceans",
        description=(
            "Sovereign states, territories, continents, and major oceans from GeoNames "
            "with Wikidata P1566 cross-references."
        ),
    )
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC-BY 4.0"
    if payload.get("source_modified"):
        entry["version"] = payload["source_modified"]
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_countries_yaml(key, config["sources"][key], payload, locales=locales)
    return True


def _register_adm1(cc, config_file):
    key    = f"GeoNames_ADM1_{cc}" if cc else "GeoNames_ADM1"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales    = config.get("locales") or ["en"]
    use_qlever = bool(config.get("apis", {}).get("qlever"))
    scope      = cc.upper() if cc else None
    print(f"  Fetching GeoNames ADM1 {f'for {cc} ' if cc else '(global) '}+ Wikidata enrichment ...")
    payload = _fetch_adm1_payload(scope, locales, use_qlever=use_qlever)
    if not payload.get("entries"):
        print(f"  Error: no ADM1 entries returned.", file=sys.stderr)
        return True
    _save_json(key, payload)

    title = f"GeoNames ADM1 for {cc}" if cc else "GeoNames First-Order Administrative Divisions"
    entry = make_source_entry(key, _GEONAMES_ADM1_URL, "GeoNamesADM1", "zip", title=title)
    if cc:
        entry["country"] = cc.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC-BY 4.0"
    if payload.get("source_modified"):
        entry["version"] = payload["source_modified"]
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_adm1_yaml(key, config["sources"][key], payload, locales=locales)
    return True


def _register_adm2(cc, config_file):
    key    = f"GeoNames_ADM2_{cc}"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales    = config.get("locales") or ["en"]
    use_qlever = bool(config.get("apis", {}).get("qlever"))
    print(f"  Fetching GeoNames ADM2 for {cc} + Wikidata enrichment ...")
    payload = _fetch_adm2_payload(cc, locales, use_qlever=use_qlever)
    if not payload.get("entries"):
        print(f"  Error: no ADM2 entries returned for {cc}.", file=sys.stderr)
        return True
    _save_json(key, payload)

    entry = make_source_entry(
        key, _GEONAMES_ADM2_URL, "GeoNamesADM2", "zip",
        title=f"GeoNames ADM2 for {cc}",
    )
    entry["country"]  = cc.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC-BY 4.0"
    if payload.get("source_modified"):
        entry["version"] = payload["source_modified"]
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_adm2_yaml(key, config["sources"][key], payload, locales=locales)
    return True


def _register_adm34(cc, levels, config_file):
    """Register GeoNames ADM3 or ADM3+ADM4 for a single country."""
    lv_str = "".join(sorted(levels))          # e.g. "ADM3" or "ADM3ADM4"
    key    = f"GeoNames_{lv_str}_{cc}"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales    = config.get("locales") or ["en"]
    use_qlever = bool(config.get("apis", {}).get("qlever"))
    lv_display = "/".join(sorted(levels))
    print(f"  Fetching GeoNames {lv_display} for {cc} from {cc}.zip ...")
    payload = _fetch_adm34_payload(cc, levels, locales, use_qlever=use_qlever)
    if not payload.get("entries"):
        print(f"  Warning: no {lv_display} entries found for {cc}.", file=sys.stderr)
        return True
    _save_json(key, payload)

    src_url = _GEONAMES_CC_URL.format(CC=cc.upper())
    entry = make_source_entry(
        key, src_url, "GeoNamesADM34", "zip",
        title=f"GeoNames {lv_display} for {cc}",
        description=f"{lv_display} administrative divisions for {cc} from GeoNames {cc}.zip.",
    )
    entry["country"]  = cc.upper()
    entry["levels"]   = sorted(levels)
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC-BY 4.0"
    if payload.get("source_modified"):
        entry["version"] = payload["source_modified"]
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_adm3_yaml(key, config["sources"][key], payload, locales=locales)
    return True


def _register_adm3(cc, config_file):
    """Register GeoNamesADM3 for a single country (municipalities only)."""
    key    = f"GeoNames_ADM3_{cc}"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales    = config.get("locales") or ["en"]
    use_qlever = bool(config.get("apis", {}).get("qlever"))
    print(f"  Fetching GeoNames ADM3 (+ ADM4 data) for {cc} from {cc}.zip ...")
    payload = _fetch_adm34_payload(cc, {"ADM3", "ADM4"}, locales, use_qlever=use_qlever)
    if not payload.get("entries"):
        print(f"  Warning: no ADM3/ADM4 entries found for {cc}.", file=sys.stderr)
        return True
    _save_json(key, payload)

    src_url = _GEONAMES_CC_URL.format(CC=cc.upper())
    entry = make_source_entry(
        key, src_url, "GeoNamesADM3", "zip",
        title=f"GeoNames ADM3 for {cc}",
        description=f"ADM3 administrative divisions for {cc} from GeoNames {cc}.zip.",
    )
    entry["country"]  = cc.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC-BY 4.0"
    if payload.get("source_modified"):
        entry["version"] = payload["source_modified"]
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_adm3_yaml(key, config["sources"][key], payload, locales=locales,
                     only_levels={"ADM3"})
    return True


def _register_adm4(cc, config_file):
    """Register GeoNamesADM4 for a single country (sub-municipalities).

    Reuses the ADM3 cached payload when available to avoid re-downloading.
    """
    key     = f"GeoNames_ADM4_{cc}"
    adm3_key = f"GeoNames_ADM3_{cc}"
    config  = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales    = config.get("locales") or ["en"]
    use_qlever = bool(config.get("apis", {}).get("qlever"))

    if _entry_exists(adm3_key):
        print(f"  Reusing cached {adm3_key} data for GeoNames ADM4 {cc} ...")
        payload = _load_json(adm3_key)
    else:
        print(f"  Fetching GeoNames ADM3+ADM4 for {cc} from {cc}.zip ...")
        payload = _fetch_adm34_payload(cc, {"ADM3", "ADM4"}, locales, use_qlever=use_qlever)
        if payload.get("entries"):
            _save_json(adm3_key, payload)  # cache under ADM3 key too

    if not payload.get("entries"):
        adm4_count = sum(1 for e in payload.get("entries", [])
                         if e.get("feat_code") == "ADM4")
        if adm4_count == 0:
            print(f"  Warning: no ADM4 entries found for {cc}.", file=sys.stderr)
            return True
    _save_json(key, payload)

    src_url = _GEONAMES_CC_URL.format(CC=cc.upper())
    entry = make_source_entry(
        key, src_url, "GeoNamesADM4", "zip",
        title=f"GeoNames ADM4 for {cc}",
        description=f"ADM4 administrative divisions for {cc} from GeoNames {cc}.zip.",
    )
    entry["country"]  = cc.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC-BY 4.0"
    if payload.get("source_modified"):
        entry["version"] = payload["source_modified"]
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_adm3_yaml(key, config["sources"][key], payload, locales=locales,
                     only_levels={"ADM4"})
    return True


def _register_ppl(cc, config_file):
    """Register GeoNames populated places (settlements) for a single country."""
    key    = f"GeoNames_Settlement_{cc}"
    config = _load_config(config_file)
    if key in config.get("sources", {}):
        print(f"  Skipping: '{key}' already in {config_file}", file=sys.stderr)
        return True

    locales    = config.get("locales") or ["en"]
    use_qlever = bool(config.get("apis", {}).get("qlever"))
    print(f"  Fetching GeoNames populated places for {cc} from {cc}.zip ...")
    payload = _fetch_ppl_payload(cc, locales, use_qlever=use_qlever)
    if not payload.get("entries"):
        print(f"  Warning: no populated place entries found for {cc}.", file=sys.stderr)
        return True
    _save_json(key, payload)

    src_url = _GEONAMES_CC_URL.format(CC=cc.upper())
    entry = make_source_entry(
        key, src_url, "GeoNamesSettlement", "zip",
        title=f"GeoNames Settlements for {cc}",
        description=(
            f"Populated places for {cc} from GeoNames {cc}.zip. "
            "Historical entries marked status: historical."
        ),
    )
    entry["country"]  = cc.upper()
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    entry["license"]  = "CC-BY 4.0"
    entry["minus"]    = {"status": ["historical"]}
    if payload.get("source_modified"):
        entry["version"] = payload["source_modified"]
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    _build_ppl_yaml(key, config["sources"][key], payload, locales=locales)
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_geonames_code(url, config_file=MENU_CONFIG):
    """Pre-download handler for GeoNames_* shorthands and GeoNames URLs.

    Matches:
      GeoNames_countries       → register GeoNamesCountries
      GeoNames_ADM1            → register GeoNamesAdm1 (global)
      GeoNames_ADM1_{CC}       → register GeoNamesAdm1 for one country
      GeoNames_ADM2_{CC}       → register GeoNamesAdm2 for one country
      GeoNames_ADM3_{CC}       → register GeoNamesAdm3 for one country
      GeoNames_ADM3ADM4_{CC}   → register both GeoNamesAdm3 and Adm4 for one country
      https://download.geonames.org/export/dump/countryInfo.txt → GeoNamesCountries

    Returns True if the URL was matched (regardless of success).
    """
    url = url.strip()

    if url == _GEONAMES_COUNTRY_URL:
        return _register_countries(config_file)

    m = _SHORTHAND_RE.match(url)
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
            return _register_adm1(None, config_file)
    elif adm_level and cc:
        level = adm_level.upper()
        if level == "ADM1":
            return _register_adm1(cc.upper(), config_file)
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
        if level == "SETTLEMENT":
            return _register_ppl(cc.upper(), config_file)

    return False


def fetch_geonames_source(key, source, config_file=MENU_CONFIG):
    """Re-download GeoNames data and Wikidata P1566 enrichment for *key*."""
    content_type = source.get("content_type", "")
    locales      = _get_project_locales(config_file)
    use_qlever   = _qlever_enabled(source)

    if content_type == "GeoNamesCountries":
        print(f"  Re-fetching GeoNames countries + Wikidata enrichment ...")
        payload = _fetch_countries_payload(locales, use_qlever=use_qlever)
        _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoNamesADM1":
        cc = source.get("country", "")
        print(f"  Re-fetching GeoNames ADM1 {f'for {cc} ' if cc else '(global) '}+ Wikidata ...")
        payload = _fetch_adm1_payload(cc if cc else None, locales, use_qlever=use_qlever)
        _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoNamesADM2":
        cc = source.get("country", "")
        if not cc:
            print(f"  Error: GeoNamesADM2 source '{key}' has no country field.", file=sys.stderr)
            return
        print(f"  Re-fetching GeoNames ADM2 for {cc} + Wikidata enrichment ...")
        payload = _fetch_adm2_payload(cc, locales, use_qlever=use_qlever)
        if payload:
            _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoNamesADM34":
        cc     = source.get("country", "")
        levels = source.get("levels", ["ADM3"])
        if not cc:
            print(f"  Error: GeoNamesADM34 source '{key}' has no country field.", file=sys.stderr)
            return
        lv_display = "/".join(levels)
        print(f"  Re-fetching GeoNames {lv_display} for {cc} ...")
        payload = _fetch_adm34_payload(cc, levels, locales, use_qlever=use_qlever)
        if payload:
            _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoNamesADM3":
        cc = source.get("country", "")
        if not cc:
            print(f"  Error: GeoNamesADM3 source '{key}' has no country field.", file=sys.stderr)
            return
        print(f"  Re-fetching GeoNames ADM3 for {cc} (combined ADM3+ADM4 payload cached for ADM4 reuse) ...")
        payload = _fetch_adm34_payload(cc, {"ADM3", "ADM4"}, locales, use_qlever=use_qlever)
        if payload:
            _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoNamesADM4":
        cc = source.get("country", "")
        if not cc:
            print(f"  Error: GeoNamesADM4 source '{key}' has no country field.", file=sys.stderr)
            return
        adm3_key = f"GeoNames_ADM3_{cc}"
        if _entry_exists(adm3_key):
            print(f"  Reusing cached {adm3_key} payload for GeoNames ADM4 {cc} ...")
            payload = _load_json(adm3_key)
        else:
            print(f"  Re-fetching GeoNames ADM4 for {cc} (combined ADM3+ADM4 payload cached under ADM3 key) ...")
            payload = _fetch_adm34_payload(cc, {"ADM3", "ADM4"}, locales, use_qlever=use_qlever)
            if payload:
                _save_json(adm3_key, payload)   # companion cache — not a config entry
        if payload:
            _save_json(key, payload, config_file=config_file)

    elif content_type == "GeoNamesSettlement":
        cc = source.get("country", "")
        if not cc:
            print(f"  Error: GeoNamesSettlement source '{key}' has no country field.",
                  file=sys.stderr)
            return
        print(f"  Re-fetching GeoNames settlements for {cc} ...")
        payload = _fetch_ppl_payload(cc, locales, use_qlever=use_qlever)
        if payload:
            _save_json(key, payload, config_file=config_file)

    else:
        print(f"  Warning: unknown GeoNames content_type '{content_type}' for '{key}'",
              file=sys.stderr)


def process_geonames_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Build sources/{key}.yaml from the cached GeoNames zip for *key*."""
    content_type = source.get("content_type", "")
    actual_key   = key

    if not _entry_exists(key):
        # Migration fallback: new split keys can reuse old combined ADM3ADM4 cache
        cc = source.get("country", "")
        if content_type in ("GeoNamesADM3", "GeoNamesADM4") and cc:
            old_key = f"GeoNames_ADM3ADM4_{cc}"
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
        if content_type == "GeoNamesADM3":
            _build_adm3_yaml(key, source, data, locales=locales, only_levels={"ADM3"})
        elif content_type == "GeoNamesADM4":
            _build_adm3_yaml(key, source, data, locales=locales, only_levels={"ADM4"})
        else:
            _build_adm3_yaml(key, source, data, locales=locales)
    elif dtype == "ppl":
        _build_ppl_yaml(key, source, data, locales=locales)
    else:
        print(f"  Warning: unknown data type '{dtype}' in {_SHARED_ZIP}", file=sys.stderr)
