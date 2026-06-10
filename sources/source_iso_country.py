"""ISO 3166-2 country subdivision handler for term_harvester.py.

Detects URLs of the form:
    https://www.iso.org/obp/ui/#iso:code:3166:CA

The ISO OBP page is a Vaadin SPA and cannot be fetched directly.  Data is
sourced from the Wikidata Query Service (SPARQL) using property P300
(ISO 3166-2 code), filtered by the alpha-2 prefix.  Each subdivision's
Wikidata QID is stored as the permissible value's ``meaning`` (e.g. wd:Q1951
for Alberta).

The country entity is resolved via P297 (ISO 3166-1 alpha-2) to obtain its
English name, which becomes the enum key.  Language labels for all project
locales are requested in a single SPARQL query using OPTIONAL rdfs:label
clauses.

Fetched data is stored as ``sources/{key}.json`` (a clean summary, not raw
SPARQL JSON).  Existing ``sources/{key}.html`` files from the earlier
Wikipedia-based implementation are no longer read; re-run ``-f {key}`` to
migrate.

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
import urllib.parse
import urllib.request
import yaml

from source_utils import (
    BROWSER_HEADERS,
    MENU_CONFIG,
    IndentedDumper,
    _make_locale_extensions,
    add_permissible_value,
    make_config_schema,
    make_source_entry,
    update_source_config,
    write_config,
)

# Matches with or without scheme/www, e.g.:
#   https://www.iso.org/obp/ui/#iso:code:3166:CA
#   iso.org/obp/ui/#iso:code:3166:CA
_URL_RE = re.compile(
    r'iso\.org/obp/ui/(?:.*)?#iso:code:3166:([A-Za-z]{2})\s*$',
    re.IGNORECASE,
)

_WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
_WD_ENTITY_BASE = "http://www.wikidata.org/entity/"
_ISO_BASE = "https://www.iso.org/iso-3166-country-codes/"
_SOURCE_PREFIXES = {"iso": _ISO_BASE, "wd": _WD_ENTITY_BASE}
_WD_PREFIXES = {"wd": _WD_ENTITY_BASE}  # for CURIE compression in add_permissible_value


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
    """Convert a country name to a PascalCase identifier.

    "Canada"                 → "Canada"
    "United States"          → "UnitedStates"
    "Bosnia and Herzegovina" → "BosniaAndHerzegovina"
    """
    words = re.findall(r'[A-Za-z0-9]+', name)
    return ''.join(w.capitalize() for w in words) if words else "Country"


def _qid_from_uri(uri):
    """Extract 'Q1951' from 'http://www.wikidata.org/entity/Q1951'."""
    if uri.startswith(_WD_ENTITY_BASE):
        return uri[len(_WD_ENTITY_BASE):]
    return uri.rsplit("/", 1)[-1]


def _sparql_query(sparql):
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
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["results"]["bindings"]


def _get_project_locales(config_file=MENU_CONFIG):
    """Read the global locales list from harvester_config.yaml."""
    try:
        with open(config_file) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("locales") or ["en"]
    except Exception:
        return ["en"]


def _json_path(key):
    return f"sources/{key}.json"


# ---------------------------------------------------------------------------
# Wikidata fetch
# ---------------------------------------------------------------------------

def _fetch_wikidata(alpha2, locales=None):
    """Query Wikidata for country name and ISO 3166-2 subdivisions.

    Returns:
        country_name (str)
        subdivisions (list[dict]) — each: {code, suffix, qid, labels: {lang: str}}
    """
    locales = locales or ["en"]

    # Country name via P297 (ISO 3166-1 alpha-2 code)
    country_sparql = f"""
SELECT ?item ?label WHERE {{
  ?item wdt:P297 "{alpha2}" .
  ?item rdfs:label ?label .
  FILTER(LANG(?label) = "en")
}}
LIMIT 1
"""
    country_name = ""
    try:
        bindings = _sparql_query(country_sparql)
        if bindings:
            country_name = bindings[0].get("label", {}).get("value", "")
    except Exception as e:
        print(f"  Warning: could not get country name for {alpha2}: {e}", file=sys.stderr)

    # Subdivisions via P300 prefix, with optional labels per locale
    label_vars = " ".join(f"?label_{lang}" for lang in locales)
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
            qid = _qid_from_uri(row.get("item", {}).get("value", ""))
            labels = {}
            for lang in locales:
                val = row.get(f"label_{lang}", {}).get("value", "")
                if val:
                    labels[lang] = val
            subdivisions.append({"code": code, "suffix": suffix, "qid": qid, "labels": labels})
    except Exception as e:
        print(f"  Error querying subdivisions for {alpha2}: {e}", file=sys.stderr)

    return country_name, subdivisions


def _save_json(key, alpha2, country_name, subdivisions, config_file=MENU_CONFIG):
    """Persist fetched data as JSON and stamp download_date in config."""
    today = datetime.date.today().isoformat()
    payload = {
        "alpha2": alpha2,
        "country_name": country_name,
        "download_date": today,
        "subdivisions": subdivisions,
    }
    path = _json_path(key)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(subdivisions)} subdivisions to {path}")
    update_source_config(key, {"download_date": today, "file_format": "json"}, config_file)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_iso_country_source(key, source, config_file=MENU_CONFIG):
    """Re-download Wikidata ISO 3166-2 data for the given source."""
    alpha2 = _alpha2_from_key(key) or _alpha2_from_url(
        (source.get("reachable_from") or {}).get("source_ontology", "")
    )
    if not alpha2:
        print(f"  Skipping {key}: cannot determine alpha-2 code.", file=sys.stderr)
        return

    locales = _get_project_locales(config_file)
    print(f"  Querying Wikidata for ISO 3166-2:{alpha2} (locales: {locales}) ...")
    country_name, subdivisions = _fetch_wikidata(alpha2, locales=locales)
    if not subdivisions:
        print(f"  Warning: no subdivisions found for {alpha2}", file=sys.stderr)
        return
    _save_json(key, alpha2, country_name, subdivisions, config_file)


def process_iso_country_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Build sources/{key}.yaml from the downloaded Wikidata JSON."""
    json_path = _json_path(key)

    if not os.path.exists(json_path):
        print(f"  {json_path} not found — fetching from Wikidata ...", file=sys.stderr)
        fetch_iso_country_source(key, source, config_file)

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    country_name = data.get("country_name", "")
    subdivisions = data.get("subdivisions", [])

    if not subdivisions:
        print(f"  Warning: no subdivision data in {json_path}", file=sys.stderr)
        return

    locales = locales or ["en"]
    primary_lang = "en" if "en" in locales else locales[0]
    request_fr = "fr" in locales

    enum_key = _country_name_to_key(country_name) if country_name else key
    enum_title = country_name or key

    pv_en = {}
    pv_fr = {}

    for sub in subdivisions:
        suffix = sub.get("suffix", "")
        if not suffix:
            continue
        labels = sub.get("labels", {})
        qid = sub.get("qid", "")
        title = labels.get(primary_lang) or labels.get("en") or suffix
        meaning = (_WD_ENTITY_BASE + qid) if qid else None

        add_permissible_value(pv_en, suffix, title=title, meaning=meaning, prefixes=_WD_PREFIXES,
                              exact_mappings=[f"iso:{sub['code']}"])

        if request_fr:
            title_fr = labels.get("fr", "")
            if title_fr:
                add_permissible_value(pv_fr, suffix, title=title_fr)

    source_url = (source.get("reachable_from") or {}).get("source_ontology", "")
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or enum_title,
        prefixes=_SOURCE_PREFIXES,
        enums={enum_key: {
            "name":               enum_key,
            "title":              enum_title,
            "permissible_values": pv_en,
        }},
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
    print(f"Updated {yaml_path} ({len(pv_en)} subdivisions"
          + (f", {n_fr} French translations" if n_fr else "") + ")")


def match_iso_country(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is an ISO OBP 3166-2 country page and was handled.

    The ISO OBP page is a Vaadin SPA; the downloaded tmp_path content is
    discarded and Wikidata is queried instead.
    """
    alpha2 = _alpha2_from_url(url)
    if not alpha2:
        return False

    os.unlink(tmp_path)   # discard useless Vaadin bootstrap HTML

    key = f"ISO_COUNTRY_{alpha2}"

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    if key in config.get("sources", {}):
        print(f"  Skipping {url}: source key '{key}' already exists in {config_file}",
              file=sys.stderr)
        return True

    locales = config.get("locales") or ["en", "fr"]
    print(f"  Querying Wikidata for ISO 3166-2:{alpha2} (locales: {locales}) ...")
    country_name, subdivisions = _fetch_wikidata(alpha2, locales=locales)
    if not subdivisions:
        print(f"  Error: no subdivisions found for ISO 3166-2:{alpha2}", file=sys.stderr)
        return True

    title = country_name or f"ISO 3166-2:{alpha2}"

    # Save JSON cache directly — cannot use _save_json here because the source
    # key doesn't exist in the config yet, so update_source_config would crash.
    today = datetime.date.today().isoformat()
    path = _json_path(key)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"alpha2": alpha2, "country_name": country_name,
                   "download_date": today, "subdivisions": subdivisions},
                  f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(subdivisions)} subdivisions to {path}")

    entry = make_source_entry(key, url, "ISO_COUNTRY", "json", title=title)
    entry["prefixes"] = dict(_SOURCE_PREFIXES)
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"Added source '{key}' to {config_file}")
    return True
