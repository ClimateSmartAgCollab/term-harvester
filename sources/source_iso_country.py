"""ISO 3166-2 country subdivision handler for term_harvester.py.

Two modes:

1. Single country  — triggered by an ISO OBP URL of the form:
       https://www.iso.org/obp/ui/#iso:code:3166:CA
   Fetches Wikidata for that alpha-2 code only; stored as
   sources/ISO_COUNTRY_CA.zip  →  sources/ISO_COUNTRY_CA.yaml (one enum).

   For single-country sources, a second SPARQL query fetches the Wikidata
   P31 (instance of) type for each subdivision (e.g. "province of Canada",
   "territory of Canada").  When two or more distinct types are found the
   generated YAML groups the permissible values: one synthetic header entry
   per type (most-common type first) followed by the subdivisions in that
   group with ``is_a`` pointing to the header.  The enum description also
   gains a sentence summarising the type counts, e.g.
   "Includes 10 provinces of Canada and 3 territories of Canada."

2. All countries — triggered by:
       https://www.iso.org/iso-3166-country-codes.html
   Fetches all ISO 3166-1 alpha-2 country names and all ISO 3166-2 subdivision
   codes in two broad SPARQL queries; stored as
   sources/ISO_COUNTRY.zip  →  sources/ISO_COUNTRY.yaml (one enum per country).
   Type grouping is not applied in all-countries mode (too expensive).

The ISO OBP page is a Vaadin SPA; the downloaded tmp_path content is discarded
and Wikidata is queried instead.  Each subdivision's Wikidata QID is stored as
the permissible value's ``meaning`` (e.g. wd:Q1951 for Alberta).

Public API used by term_harvester.py:
    match_iso_country_code(url, config_file)       # pre-download: ISO_COUNTRY_XX / ISO_COUNTRY
    match_iso_country(url, tmp_path, config_file)  # post-download: full OBP URL
    match_iso_country_all(url, config_file)        # pre-download: all-countries landing page
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


def _pluralize_type_label(label):
    """Pluralize the main noun in a Wikidata type label.

    Pluralizes the last word before the first preposition ("of", "in", etc.),
    which handles both simple labels ('province') and compound labels
    ('special municipality', 'constituent country').

    Examples:
      'province of Canada'                              → 'provinces of Canada'
      'territory of Canada'                             → 'territories of Canada'
      'special municipality of the Netherlands'         → 'special municipalities of the Netherlands'
      'constituent country of the Kingdom ...'          → 'constituent countries of the Kingdom ...'
      'municipality'                                    → 'municipalities'
    """
    if not label:
        return label
    words = label.split()
    # Default: pluralize the last word (handles "federal district", "U.S. state", etc.)
    # When a preposition ("of", "in", …) is found, pluralize the word before it
    # ("province of Canada" → pluralize "province", not "Canada").
    _PREPOSITIONS = {"of", "in", "from", "at", "for", "with", "by"}
    noun_idx = len(words) - 1
    for i, w in enumerate(words):
        if w.lower() in _PREPOSITIONS:
            noun_idx = max(0, i - 1)
            break
    w = words[noun_idx]
    if w.endswith("y") and len(w) > 1 and w[-2].lower() not in "aeiou":
        w = w[:-1] + "ies"
    elif w.endswith(("s", "sh", "ch", "x", "z")):
        w = w + "es"
    else:
        w = w + "s"
    words[noun_idx] = w
    return " ".join(words)


def _group_code(type_label):
    """Convert a Wikidata type label to a PV key for the group header entry."""
    return type_label[0].upper() + type_label[1:] if type_label else ""


def _fetch_subdivision_types(alpha2):
    """Query Wikidata for P31 (instance of) types for all ISO 3166-2 subdivisions of *alpha2*.

    Returns dict: iso_code → list of (type_qid, type_label_en) sorted by label.
    An empty dict is returned when the query fails or returns no results.
    """
    sparql = f"""
SELECT ?item ?iso_code ?type ?typeLabel WHERE {{
  ?item wdt:P300 ?iso_code .
  FILTER(STRSTARTS(?iso_code, "{alpha2}-"))
  ?item wdt:P31 ?type .
  ?type rdfs:label ?typeLabel .
  FILTER(LANG(?typeLabel) = "en")
}}
ORDER BY ?iso_code ?typeLabel
"""
    result = {}
    try:
        for row in _sparql_query(sparql, timeout=30):
            code      = row.get("iso_code",   {}).get("value", "")
            type_uri  = row.get("type",        {}).get("value", "")
            type_lbl  = row.get("typeLabel",   {}).get("value", "")
            if code and type_uri and type_lbl:
                result.setdefault(code, []).append((_qid_from_uri(type_uri), type_lbl))
    except Exception as e:
        print(f"  Warning: could not fetch subdivision types for {alpha2}: {e}", file=sys.stderr)
    return result


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

    # Fetch P31 (instance of) types so the YAML builder can group by subdivision kind
    if subdivisions:
        print(f"  Fetching subdivision types for {alpha2} ...")
        type_data = _fetch_subdivision_types(alpha2)
        if type_data:
            # Count how often each (qid, label) pair appears across ALL subdivisions
            all_type_freq: Counter = Counter()
            for types_list in type_data.values():
                for t in types_list:
                    all_type_freq[t] += 1
            # Assign each subdivision its most-frequent type (tie-break: alphabetical label)
            for sub in subdivisions:
                code  = sub.get("code", "")
                types = type_data.get(code, [])
                if types:
                    best = sorted(types, key=lambda t: (-all_type_freq[t], t[1]))[0]
                    sub["type_qid"]   = best[0]
                    sub["type_label"] = best[1]

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

    # --- Group subdivisions by Wikidata P31 type ---
    type_counter: Counter = Counter()
    for sub in subdivisions:
        tl = sub.get("type_label", "")
        if tl:
            type_counter[tl] += 1

    # Ordered groups: most-common type first, alphabetical tie-break
    ordered_groups = sorted(type_counter.items(), key=lambda x: (-x[1], x[0]))

    # Map type_label → sorted list of subdivisions
    grouped: defaultdict = defaultdict(list)
    ungrouped = []
    for sub in subdivisions:
        tl = sub.get("type_label", "")
        if tl:
            grouped[tl].append(sub)
        else:
            ungrouped.append(sub)
    for tl in grouped:
        grouped[tl].sort(key=lambda s: s.get("code", ""))

    # Drop types with fewer than 2 members — their codes join the ungrouped tail
    # so that singleton types (e.g. one federal district) don't get a header of
    # their own while the rest of the country is neatly grouped.
    _MIN_GROUP = 2
    small_labels = {lbl for lbl, cnt in ordered_groups if cnt < _MIN_GROUP}
    if small_labels:
        for tl in small_labels:
            ungrouped.extend(grouped.pop(tl, []))
        ordered_groups = [(lbl, cnt) for lbl, cnt in ordered_groups if cnt >= _MIN_GROUP]
    ungrouped.sort(key=lambda s: s.get("code", ""))

    has_groups = len(ordered_groups) >= 2

    # Build type-summary sentence for enum description
    type_desc = ""
    if ordered_groups:
        parts = [f"{cnt} {_pluralize_type_label(lbl)}" for lbl, cnt in ordered_groups]
        if len(parts) == 1:
            type_desc = f"Includes {parts[0]}."
        else:
            type_desc = "Includes " + ", ".join(parts[:-1]) + " and " + parts[-1] + "."

    enum_key = _country_name_to_key(country_name) if country_name else key
    pv_en: dict = {}
    pv_fr: dict = {}

    # Subdivision PVs helper
    def _add_sub(sub, is_a=None):
        code = sub.get("code", "")
        if not code:
            return
        labels  = sub.get("labels", {})
        qid     = sub.get("qid", "")
        title   = labels.get(primary_lang) or labels.get("en") or code
        meaning = (_WD_ENTITY_BASE + qid) if qid else None
        add_permissible_value(pv_en, code, title=title, meaning=meaning,
                              prefixes=_WD_PREFIXES, is_a=is_a)
        if request_fr:
            title_fr = labels.get("fr", "")
            if title_fr:
                add_permissible_value(pv_fr, code, title=title_fr)

    # Emit each group header immediately followed by its members so that
    # is_a children always appear directly after their parent in the YAML.
    for type_label, _cnt in ordered_groups:
        gc = _group_code(type_label) if has_groups else None
        if gc:
            type_qid = next(
                (s.get("type_qid", "") for s in grouped[type_label] if s.get("type_qid")),
                "",
            )
            _plural = _pluralize_type_label(type_label)
            header: dict = {"title": (_plural[0].upper() + _plural[1:]) if _plural else ""}
            if type_qid:
                header["meaning"] = f"wd:{type_qid}"
            pv_en[gc] = header
        for sub in grouped[type_label]:
            _add_sub(sub, is_a=gc)
    for sub in ungrouped:
        _add_sub(sub)

    source_url = (source.get("reachable_from") or {}).get("source_ontology", "")

    # Combine Wikidata country description with type-summary sentence
    full_desc = normalize_text(country_desc) if country_desc else ""
    if type_desc:
        if full_desc and not full_desc.endswith("."):
            full_desc += "."
        full_desc = (full_desc + " " + type_desc).strip() if full_desc else type_desc

    enum_entry = {"name": enum_key, "title": normalize_text(country_name), "permissible_values": pv_en}
    if full_desc:
        enum_entry["description"] = full_desc
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


def _add_single_country(alpha2, url, config_file, tmp_path=None):
    """Core logic for registering a single ISO 3166-2 country source.

    *url* is the canonical ISO OBP URL stored in the config entry.
    *tmp_path* is removed when provided (the Vaadin bootstrap file from -a downloads).
    Returns True (always, once the URL matched).
    """
    if tmp_path:
        os.unlink(tmp_path)

    key = f"ISO_COUNTRY_{alpha2}"
    try:
        with open(config_file) as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config = {}

    if key in config.get("sources", {}):
        print(f"  Skipping: source key '{key}' already exists in {config_file}",
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


def match_iso_country(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is a single-country ISO OBP page and was handled."""
    alpha2 = _alpha2_from_url(url)
    if not alpha2:
        return False
    return _add_single_country(alpha2, url, config_file, tmp_path=tmp_path)


# Matches ISO_COUNTRY_XX (single country) or bare ISO_COUNTRY (all countries)
_SHORTHAND_RE = re.compile(r'^ISO_COUNTRY(?:_([A-Za-z]{2}))?$', re.IGNORECASE)


def match_iso_country_code(url, config_file=MENU_CONFIG):
    """Pre-download handler for ISO_COUNTRY_XX / ISO_COUNTRY shorthands.

    Accepts:
      ISO_COUNTRY_CA   →  sources/ISO_COUNTRY_CA.zip/.yaml  (single country)
      ISO_COUNTRY_US   →  sources/ISO_COUNTRY_US.zip/.yaml
      ISO_COUNTRY      →  sources/ISO_COUNTRY.zip/.yaml     (all countries)

    The shorthand mirrors the source key produced by the tool, so users
    never need to look up the ISO OBP URL.  The canonical OBP URL is still
    stored in harvester_config.yaml for reference.
    Returns True if the shorthand matched (regardless of outcome).
    """
    m = _SHORTHAND_RE.match(url.strip())
    if not m:
        return False
    alpha2 = m.group(1)
    if alpha2:
        alpha2 = alpha2.upper()
        canon_url = f"https://www.iso.org/obp/ui/#iso:code:3166:{alpha2}"
        return _add_single_country(alpha2, canon_url, config_file)
    # No alpha-2 suffix → all-countries
    return match_iso_country_all(_ISO_BASE, config_file)


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
