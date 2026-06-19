"""ISO 3166-2 country subdivision handler for term_harvester.py.

Two modes:

1. Single country  — triggered by an ISO OBP URL of the form:
       https://www.iso.org/obp/ui/#iso:code:3166:CA
   Fetches Wikidata for that alpha-2 code only; stored as
   sources/ISO_COUNTRY_CA.zip  →  sources/ISO_COUNTRY_CA.yaml (one enum).

2. All countries — triggered by:
       https://www.iso.org/iso-3166-country-codes.html
   Fetches all ISO 3166-1 alpha-2 country names and all ISO 3166-2 subdivision
   codes in two broad SPARQL queries; stored as
   sources/ISO_COUNTRY.zip  →  sources/ISO_COUNTRY.yaml (one enum per country).

The ISO OBP page is a Vaadin SPA; the downloaded tmp_path content is discarded
and Wikidata is queried instead.  Each subdivision's Wikidata QID is stored as
the permissible value's ``meaning`` (e.g. wd:Q1951 for Alberta).

Public API used by term_harvester.py:
    match_iso_country(url, tmp_path, config_file)
    fetch_iso_country_source(key, source, config_file)
    process_iso_country_source(key, source, config_file, locales)
"""

import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import yaml
import zipfile

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
)

# Single-country OBP URL: ...#iso:code:3166:CA
_URL_RE = re.compile(
    r'iso\.org/obp/ui/(?:.*)?#iso:code:3166:([A-Za-z]{2})\s*$',
    re.IGNORECASE,
)
# All-countries landing page
_ALL_URL_RE = re.compile(r'iso\.org/iso-3166-country-codes', re.IGNORECASE)

_WDQS_ENDPOINT  = "https://query.wikidata.org/sparql"
_WD_ENTITY_BASE = "http://www.wikidata.org/entity/"
_ISO_BASE        = "https://www.iso.org/iso-3166-country-codes.html"
_SOURCE_PREFIXES = {"iso": _ISO_BASE, "wd": _WD_ENTITY_BASE}
_WD_PREFIXES     = {"wd": _WD_ENTITY_BASE}

_ALL_KEY = "ISO_COUNTRY"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _alpha2_from_url(url):
    m = _URL_RE.search(url)
    return m.group(1).upper() if m else None


def _alpha2_from_key(key):
    m = re.match(r'ISO_COUNTRY_([A-Z]{2})$', key, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _country_name_to_key(name):
    words = re.findall(r'[A-Za-z0-9]+', name)
    return ''.join(w.capitalize() for w in words) if words else "Country"


def _qid_from_uri(uri):
    if uri.startswith(_WD_ENTITY_BASE):
        return uri[len(_WD_ENTITY_BASE):]
    return uri.rsplit("/", 1)[-1]


def _sparql_query(sparql, timeout=90):
    """Execute a SPARQL query against Wikidata; return the bindings list."""
    params = urllib.parse.urlencode({"query": sparql, "format": "json"})
    url = f"{_WDQS_ENDPOINT}?{params}"
    headers = {
        **BROWSER_HEADERS,
        "Accept": "application/sparql-results+json",
        "User-Agent": (
            "term-harvester/1.0 (https://github.com/agrifooddatacanada/term-harvester)"
        ),
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["results"]["bindings"]


def _get_project_locales(config_file=MENU_CONFIG):
    try:
        with open(config_file) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("locales") or ["en"]
    except Exception:
        return ["en"]


def _zip_path(key):
    return f"sources/{key}.zip"

def _json_name(key):
    return f"{key}.html"


# ---------------------------------------------------------------------------
# Wikidata fetch — single country
# ---------------------------------------------------------------------------

def _fetch_wikidata(alpha2, locales=None):
    """Query Wikidata for one country's name, description, and ISO 3166-2 subdivisions.

    Returns (country_name, country_desc, subdivisions).
    """
    locales = locales or ["en"]

    country_sparql = f"""
SELECT ?item ?label ?desc WHERE {{
  ?item wdt:P297 "{alpha2}" .
  ?item rdfs:label ?label .
  FILTER(LANG(?label) = "en")
  OPTIONAL {{
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }}
}}
LIMIT 1
"""
    country_name = country_desc = country_qid = ""
    try:
        bindings = _sparql_query(country_sparql)
        if bindings:
            country_name = bindings[0].get("label", {}).get("value", "")
            country_desc = bindings[0].get("desc",  {}).get("value", "")
            country_qid  = _qid_from_uri(bindings[0].get("item", {}).get("value", ""))
    except Exception as e:
        print(f"  Warning: could not get country name for {alpha2}: {e}", file=sys.stderr)

    label_vars     = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} . FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )
    sub_sparql = f"""
SELECT ?item ?iso_code {label_vars} WHERE {{
  ?item wdt:P300 ?iso_code .
  FILTER(STRSTARTS(?iso_code, "{alpha2}-"))
{label_optionals}
}}
ORDER BY ?iso_code
"""
    subdivisions = []
    seen = set()
    try:
        bindings = _sparql_query(sub_sparql)
        for row in bindings:
            code = row.get("iso_code", {}).get("value", "")
            if not code or code in seen:
                continue
            seen.add(code)
            suffix = code.split("-", 1)[1] if "-" in code else code
            qid    = _qid_from_uri(row.get("item", {}).get("value", ""))
            labels = {lang: row[f"label_{lang}"]["value"]
                      for lang in locales if row.get(f"label_{lang}", {}).get("value")}
            subdivisions.append({"code": code, "suffix": suffix, "qid": qid, "labels": labels})
    except Exception as e:
        print(f"  Error querying subdivisions for {alpha2}: {e}", file=sys.stderr)

    return country_name, country_desc, country_qid, subdivisions


# ---------------------------------------------------------------------------
# Wikidata fetch — all countries
# ---------------------------------------------------------------------------

def _fetch_all_wikidata(locales=None):
    """Fetch all ISO 3166-1 country names and all ISO 3166-2 subdivision codes.

    Uses two broad SPARQL queries with a generous timeout.
    Returns dict: alpha2 → {name, subdivisions: [...]}.
    """
    locales = locales or ["en"]

    # Step 1: all country names, descriptions, and QIDs via P297 (ISO 3166-1 alpha-2)
    print("  Step 1/2: querying all country names ...")
    country_sparql = """
SELECT ?item ?alpha2 ?label ?desc WHERE {
  ?item wdt:P297 ?alpha2 .
  ?item rdfs:label ?label .
  FILTER(LANG(?label) = "en")
  OPTIONAL {
    ?item schema:description ?desc .
    FILTER(LANG(?desc) = "en")
  }
}
ORDER BY ?alpha2
"""
    countries = {}
    try:
        for row in _sparql_query(country_sparql, timeout=15):
            alpha2 = row.get("alpha2", {}).get("value", "").upper()
            name   = row.get("label",  {}).get("value", "")
            desc   = row.get("desc",   {}).get("value", "")
            qid    = _qid_from_uri(row.get("item",  {}).get("value", ""))
            if alpha2:
                countries[alpha2] = {"name": name, "desc": desc, "qid": qid, "subdivisions": []}
    except Exception as e:
        print(f"  Error fetching country names from Wikidata: {e}", file=sys.stderr)
        return {}
    print(f"    Found {len(countries)} countries.")

    # Step 2: subdivisions batched by first letter of alpha-2 code (26 small queries).
    # A single global query reliably times out on Wikidata's SPARQL endpoint.
    label_vars      = " ".join(f"?label_{lang}" for lang in locales)
    label_optionals = "\n".join(
        f'  OPTIONAL {{ ?item rdfs:label ?label_{lang} . FILTER(LANG(?label_{lang}) = "{lang}") }}'
        for lang in locales
    )
    seen   = set()
    n_subs = 0
    print("  Step 2/2: querying ISO 3166-2 subdivisions in 26 letter-batches ...")
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        sparql = f"""
SELECT ?item ?iso_code {label_vars} WHERE {{
  ?item wdt:P300 ?iso_code .
  FILTER(STRSTARTS(?iso_code, "{letter}"))
{label_optionals}
}}
ORDER BY ?iso_code
"""
        try:
            rows = _sparql_query(sparql, timeout=30)
        except Exception as e:
            print(f"    Warning: batch '{letter}' failed: {e}", file=sys.stderr)
            time.sleep(2)
            continue

        for row in rows:
            code = row.get("iso_code", {}).get("value", "")
            if not code or code in seen:
                continue
            seen.add(code)
            alpha2 = code.split("-", 1)[0].upper() if "-" in code else ""
            if not alpha2 or alpha2 not in countries:
                continue
            suffix = code.split("-", 1)[1] if "-" in code else code
            qid    = _qid_from_uri(row.get("item", {}).get("value", ""))
            labels = {lang: row[f"label_{lang}"]["value"]
                      for lang in locales if row.get(f"label_{lang}", {}).get("value")}
            countries[alpha2]["subdivisions"].append(
                {"code": code, "suffix": suffix, "qid": qid, "labels": labels}
            )
            n_subs += 1

        time.sleep(0.5)  # polite pacing between Wikidata requests

    print(f"    Found {n_subs} subdivisions across {len(countries)} countries.")
    return countries


# ---------------------------------------------------------------------------
# JSON persistence
# ---------------------------------------------------------------------------

def _save_single_json(key, alpha2, country_name, country_desc, country_qid, subdivisions,
                      config_file=None):
    today = datetime.date.today().isoformat()
    payload = {
        "type": "single",
        "alpha2": alpha2,
        "country_name": country_name,
        "country_desc": country_desc,
        "country_qid": country_qid,
        "download_date": today,
        "subdivisions": subdivisions,
    }
    path = _zip_path(key)
    json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_json_name(key), json_bytes)
    print(f"  Saved {len(subdivisions)} subdivisions to {path}")
    if config_file:
        update_source_config(key, {"download_date": today, "file_format": "zip"}, config_file)


def _save_all_json(countries, config_file=None):
    """Persist all-countries data as a compressed zip.  Stamps config only when config_file given."""
    today = datetime.date.today().isoformat()
    payload = {
        "type": "all",
        "download_date": today,
        "countries": [
            {"alpha2": a2, **data}
            for a2, data in sorted(countries.items())
            if data.get("subdivisions")
        ],
    }
    path = _zip_path(_ALL_KEY)
    json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_json_name(_ALL_KEY), json_bytes)
    total = sum(len(c["subdivisions"]) for c in payload["countries"])
    n_countries = len(payload["countries"])
    print(f"  Saved {total} subdivisions across {n_countries} countries to {path}")
    if config_file:
        update_source_config(
            _ALL_KEY, {"download_date": today, "file_format": "zip"}, config_file
        )
    return payload


# ---------------------------------------------------------------------------
# YAML builders
# ---------------------------------------------------------------------------

def _build_single_yaml(key, source, data, locales=None):
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]
    request_fr   = "fr" in locales

    country_name = data.get("country_name", "")
    country_desc = data.get("country_desc", "")
    country_qid  = data.get("country_qid", "")
    subdivisions = data.get("subdivisions", [])

    if not subdivisions:
        print(f"  Warning: no subdivision data in {_zip_path(key)}/{_json_name(key)}", file=sys.stderr)
        return

    enum_key = _country_name_to_key(country_name) if country_name else key
    pv_en = {}
    pv_fr = {}

    for sub in subdivisions:
        code = sub.get("code", "")
        if not code:
            continue
        labels  = sub.get("labels", {})
        qid     = sub.get("qid", "")
        title   = labels.get(primary_lang) or labels.get("en") or code
        meaning = (_WD_ENTITY_BASE + qid) if qid else None
        add_permissible_value(pv_en, code, title=title, meaning=meaning,
                              prefixes=_WD_PREFIXES)
        if request_fr:
            title_fr = labels.get("fr", "")
            if title_fr:
                add_permissible_value(pv_fr, code, title=title_fr)

    source_url = (source.get("reachable_from") or {}).get("source_ontology", "")
    enum_entry = {"name": enum_key, "title": normalize_text(country_name), "permissible_values": pv_en}
    if country_desc:
        enum_entry["description"] = normalize_text(country_desc)
    if country_qid:
        enum_entry["enum_uri"] = f"wd:{country_qid}"

    schema = make_config_schema(
        id=source_url, name=key,
        title=source.get("title") or country_name,
        prefixes=_SOURCE_PREFIXES,
        enums={enum_key: enum_entry},
    )
    if pv_fr:
        schema["extensions"] = _make_locale_extensions(
            source_url, key, source.get("version") or "", "fr",
            enums={enum_key: {"permissible_values": pv_fr}},
        )

    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    n_fr = len(pv_fr)
    log_extraction(enum_key, count=len(pv_en), lang_counts={"fr": n_fr} if n_fr else None)


def _build_all_yaml(source, data, locales=None):
    locales      = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]
    request_fr   = "fr" in locales

    enums        = {}
    pv_fr_all    = {}
    total        = 0

    for country in data.get("countries", []):
        country_name  = country.get("name", "")
        country_desc  = country.get("desc", "")
        country_qid   = country.get("qid", "")
        alpha2        = country.get("alpha2", "")
        subdivisions  = country.get("subdivisions", [])
        if not subdivisions:
            continue

        enum_key = _country_name_to_key(country_name) if country_name else alpha2
        pv_en = {}
        pv_fr = {}

        for sub in subdivisions:
            code = sub.get("code", "")
            if not code:
                continue
            labels  = sub.get("labels", {})
            qid     = sub.get("qid", "")
            title   = labels.get(primary_lang) or labels.get("en") or code
            meaning = (_WD_ENTITY_BASE + qid) if qid else None
            add_permissible_value(pv_en, code, title=title, meaning=meaning,
                                  prefixes=_WD_PREFIXES)
            if request_fr:
                title_fr = labels.get("fr", "")
                if title_fr:
                    add_permissible_value(pv_fr, code, title=title_fr)

        if pv_en:
            enum_entry = {
                "name":   enum_key,
                "title":  normalize_text(country_name or alpha2),
                "permissible_values": pv_en,
            }
            if country_desc:
                enum_entry["description"] = normalize_text(country_desc)
            if country_qid:
                enum_entry["enum_uri"] = f"wd:{country_qid}"
            enums[enum_key] = enum_entry
            total += len(pv_en)
        if pv_fr:
            pv_fr_all[enum_key] = {"permissible_values": pv_fr}

    source_url = (source.get("reachable_from") or {}).get("source_ontology", _ISO_BASE)
    schema = make_config_schema(
        id=source_url,
        name=_ALL_KEY,
        title=source.get("title") or "ISO 3166-2 Country Subdivisions",
        description="All ISO 3166-2 country subdivision codes sourced from Wikidata.",
        prefixes=_SOURCE_PREFIXES,
        enums=enums,
    )
    if pv_fr_all:
        schema["extensions"] = _make_locale_extensions(
            source_url, _ALL_KEY, source.get("version") or "", "fr", enums=pv_fr_all
        )

    yaml_path = f"sources/{_ALL_KEY}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    log_extraction(_ALL_KEY, count=total)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_iso_country_source(key, source, config_file=MENU_CONFIG):
    """Re-download Wikidata ISO 3166-2 data for the given source."""
    if key == _ALL_KEY:
        locales = _get_project_locales(config_file)
        print(f"  Querying Wikidata for all ISO 3166-2 codes (locales: {locales}) ...")
        countries = _fetch_all_wikidata(locales=locales)
        if not countries:
            print("  Error: no data returned.", file=sys.stderr)
            return
        _save_all_json(countries, config_file=config_file)
        return

    # Single-country
    alpha2 = _alpha2_from_key(key) or _alpha2_from_url(
        (source.get("reachable_from") or {}).get("source_ontology", "")
    )
    if not alpha2:
        print(f"  Skipping {key}: cannot determine alpha-2 code.", file=sys.stderr)
        return
    locales = _get_project_locales(config_file)
    print(f"  Querying Wikidata for ISO 3166-2:{alpha2} (locales: {locales}) ...")
    country_name, country_desc, country_qid, subdivisions = _fetch_wikidata(alpha2, locales=locales)
    if not subdivisions:
        print(f"  Warning: no subdivisions found for {alpha2}", file=sys.stderr)
        return
    _save_single_json(key, alpha2, country_name, country_desc, country_qid, subdivisions, config_file)


def process_iso_country_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Build sources/{key}.yaml from the downloaded Wikidata zip ({key}.html inside)."""
    zip_path = _zip_path(key)
    with zipfile.ZipFile(zip_path) as zf:
        data = json.loads(zf.read(_json_name(key)).decode("utf-8"))

    locales = locales or _get_project_locales(config_file)

    if data.get("type") == "all":
        _build_all_yaml(source, data, locales=locales)
    else:
        _build_single_yaml(key, source, data, locales=locales)


def match_iso_country(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is a single-country ISO OBP page and was handled."""
    # Single-country OBP page
    alpha2 = _alpha2_from_url(url)
    if not alpha2:
        return False

    os.unlink(tmp_path)   # discard Vaadin bootstrap HTML

    key = f"ISO_COUNTRY_{alpha2}"
    try:
        with open(config_file) as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config = {}

    if key in config.get("sources", {}):
        print(f"  Skipping {url}: source key '{key}' already exists in {config_file}",
              file=sys.stderr)
        return True

    locales = config.get("locales") or ["en", "fr"]
    print(f"  Querying Wikidata for ISO 3166-2:{alpha2} (locales: {locales}) ...")
    country_name, country_desc, country_qid, subdivisions = _fetch_wikidata(alpha2, locales=locales)
    if not subdivisions:
        print(f"  Error: no subdivisions found for ISO 3166-2:{alpha2}", file=sys.stderr)
        return True

    title = country_name or f"ISO 3166-2:{alpha2}"
    _save_single_json(key, alpha2, country_name, country_desc, country_qid, subdivisions)

    entry = make_source_entry(key, url, "ISO_COUNTRY", "zip", title=title)
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    process_iso_country_source(key, config["sources"][key], config_file, locales=locales)
    return True


def match_iso_country_all(url, config_file=MENU_CONFIG):
    """Pre-download handler for -a on the ISO 3166 all-countries landing page.

    Called before any HTTP fetch attempt so that a 404 from the ISO site never
    blocks recognition.  Returns True if the URL matched (regardless of outcome).
    """
    if not _ALL_URL_RE.search(url):
        return False
    try:
        with open(config_file) as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config = {}

    if _ALL_KEY in config.get("sources", {}):
        print(f"  Skipping: source key '{_ALL_KEY}' already exists in {config_file}",
              file=sys.stderr)
        return True

    locales = config.get("locales") or ["en"]
    print(f"  Querying Wikidata for all ISO 3166-2 codes (locales: {locales}) ...")
    countries = _fetch_all_wikidata(locales=locales)
    if not countries:
        print(f"  Wikidata unavailable — retry later with: -a '{_ISO_BASE}'",
              file=sys.stderr)
        return True

    payload = _save_all_json(countries)   # no config_file — source not yet registered

    source_url = _ISO_BASE
    entry = make_source_entry(
        _ALL_KEY, source_url, "ISO_COUNTRY", "json",
        title="ISO 3166-2 Country Subdivisions",
        description="All ISO 3166-2 country subdivision codes sourced from Wikidata.",
    )
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    config.setdefault("sources", {})[_ALL_KEY] = entry
    write_config(config, config_file)
    print(f"  Added source '{_ALL_KEY}' to {config_file}")

    _build_all_yaml(entry, payload, locales=locales)
    return True
