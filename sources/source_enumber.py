"""Wikidata E number source for term_harvester.py.

Fetches food additive E numbers from Wikidata via the SPARQL endpoint using
property P628 (E number).  The fetch phase (``-a`` / ``-f``) also retrieves
each additive's English short description (``schema:description`` via the
label service) and any "has use" values (P366) declared in Wikidata, so the
cached JSON carries functional-role information without requiring a second
network round-trip during processing.

The canonical entry point URL is the Wikidata page for the E number concept:
  https://www.wikidata.org/wiki/Q207810

The process phase (``-c``) produces a two-level enum:
  - Nine parent permissible values, one per EU numeric range (colours,
    preservatives, antioxidants, …), with a ``title`` and numeric range.
  - One child permissible value per E number with ``is_a`` pointing to its
    parent range category, ``title`` from the Wikidata label, ``description``
    from the Wikidata short description and/or P366 functional uses, and
    ``meaning`` compressed to a ``wd:Qxxx`` CURIE.

Results are cached as ``sources/{key}.json`` (raw SPARQL bindings).

Public API used by term_harvester.py:
    fetch_enumber_source(key, source, config_file)
    process_enumber_source(key, source, locales=None)
    match_enumber(url, config_file)
"""

import datetime
import json
import os
import re
import urllib.parse
import urllib.request
import yaml

from source_utils import (
    MENU_CONFIG,
    IndentedDumper,
    add_permissible_value,
    log_extraction,
    make_config_schema,
    make_source_entry,
    update_source_config,
    write_config,
)


# Canonical Wikidata concept for E numbers (used as the config source_ontology URL).
WIKIDATA_ENUMBER_URL = "https://www.wikidata.org/wiki/Q207810"

_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

# Fetches E number codes, labels, short descriptions (via label service), and
# "has use" (P366) functional-role values.  P366 can produce multiple rows per
# item, so the caller must group by ?item / ?enumber after loading.
_SPARQL_QUERY = """\
SELECT ?item ?itemLabel ?itemDescription ?enumber ?useLabel WHERE {
  ?item wdt:P628 ?enumber .
  OPTIONAL {
    ?item wdt:P366 ?use .
    ?use rdfs:label ?useLabel .
    FILTER(LANG(?useLabel) = "en")
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
ORDER BY ?enumber
"""

# Wikidata entity URI base — used for CURIE compression (wd:Qxxx).
_WD_ENTITY_BASE = "http://www.wikidata.org/entity/"
_WD_PREFIXES = {"wd": _WD_ENTITY_BASE}

# Roman numeral values for sort key.
_ROMAN_VAL = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
}

# EU numeric range → (parent PV key, title).
# E800-E899 is unallocated in the EU scheme and intentionally absent.
_RANGE_CATEGORIES = [
    (100,  199,  "colours",             "Colours (E100-E199)"),
    (200,  299,  "preservatives",       "Preservatives (E200-E299)"),
    (300,  399,  "antioxidants",        "Antioxidants and acidity regulators (E300-E399)"),
    (400,  499,  "thickeners",          "Thickeners, stabilisers and emulsifiers (E400-E499)"),
    (500,  599,  "acidity_regulators",  "Acidity regulators and anti-caking agents (E500-E599)"),
    (600,  699,  "flavour_enhancers",   "Flavour enhancers (E600-E699)"),
    (700,  799,  "antibiotics",         "Antibiotics (E700-E799)"),
    (900,  999,  "glazing_agents",      "Glazing agents, gases and sweeteners (E900-E999)"),
    (1000, 1599, "additional_additives","Additional additives (E1000-E1599)"),
]


def _enumber_sort_key(code):
    """Numeric sort key for E number codes: (integer, letter_suffix, roman_value).

    "E89"       → (89,  '',  0)
    "E100a"     → (100, 'a', 0)
    "E160b(i)"  → (160, 'b', 1)
    "456"       → (456, '',  0)   # Wikidata has one entry without the E prefix
    """
    s = code.lstrip("Ee")
    m = re.match(r'^(\d+)([a-z]{0,2})(?:\(([ivx]{1,8})\))?', s, re.IGNORECASE)
    if not m:
        return (999999, code, 0)
    return (
        int(m.group(1)),
        (m.group(2) or "").lower(),
        _ROMAN_VAL.get((m.group(3) or "").lower(), 0),
    )


def _range_category_key(code):
    """Return the parent PV key for an E number based on its EU numeric range.

    Returns None for codes that fall outside all defined ranges (e.g. E800-E899).
    """
    s = code.lstrip("Ee")
    m = re.match(r'^(\d+)', s)
    if not m:
        return None
    n = int(m.group(1))
    for lo, hi, cat_key, _ in _RANGE_CATEGORIES:
        if lo <= n <= hi:
            return cat_key
    return None


def _fetch_sparql(query, endpoint=_SPARQL_ENDPOINT):
    """Run a SPARQL SELECT query and return the parsed JSON response dict."""
    params = urllib.parse.urlencode({"query": query, "format": "json"})
    req = urllib.request.Request(
        f"{endpoint}?{params}",
        headers={
            "User-Agent": (
                "term-harvester/1.0 (https://github.com/ClimateSmartAgCollab/term-harvester) "
                "Python-urllib/3"
            ),
            "Accept": "application/sparql-results+json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_enumber_source(key, source, config_file=MENU_CONFIG):
    """Download E number data from Wikidata SPARQL and save as sources/{key}.json.

    The SPARQL query fetches labels, short descriptions, and P366 (has use)
    functional-role values so that all enrichment data is available offline
    for the -c processing step.

    Called by -f E_NUMBER and by match_enumber during -a.
    """
    json_path = f"sources/{key}.json"
    print(f"  Fetching E numbers from Wikidata SPARQL (P628 + P366)…")
    try:
        data = _fetch_sparql(_SPARQL_QUERY)
    except Exception as e:
        keep = f" — keeping existing {json_path}" if os.path.exists(json_path) else ""
        print(f"  Error: {e}{keep}")
        return

    bindings = (data.get("results") or {}).get("bindings") or []
    if not bindings:
        print(f"  Warning: no bindings returned from Wikidata SPARQL", flush=True)
        return

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(bindings)} bindings to {json_path}")

    update_source_config(
        key,
        {"download_date": datetime.date.today().isoformat()},
        config_file,
    )


def process_enumber_source(key, source, locales=None):
    """Build a LinkML enum YAML from sources/{key}.json (Wikidata SPARQL results).

    Writes sources/{key}.yaml with a two-level enum:
    - Nine parent PVs (one per EU numeric range category).
    - One child PV per E number with is_a pointing to its parent range.

    PV keys are the E number codes (e.g. ``E100``), titles are Wikidata labels,
    descriptions combine the Wikidata short description with P366 functional uses,
    and meaning values are compressed to ``wd:Qxxx`` CURIEs.
    """
    json_path = f"sources/{key}.json"
    if not os.path.exists(json_path):
        print(
            f"Skipping {key}: {json_path} not found — run -f to fetch first",
            flush=True,
        )
        return

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    bindings = (data.get("results") or {}).get("bindings") or []
    if not bindings:
        print(f"  Warning: no bindings in {json_path}", flush=True)
        return

    # Group multi-row bindings by normalised E number code.
    # Each dict accumulates: entity_uri, label, desc, uses (list of P366 values).
    items = {}  # code → dict
    for b in bindings:
        raw_code = (b.get("enumber") or {}).get("value", "").strip()
        if not raw_code:
            continue
        # Normalise: ensure uppercase E prefix.
        if raw_code[0].isdigit():
            code = "E" + raw_code
        elif raw_code[0].lower() == "e":
            code = "E" + raw_code[1:]
        else:
            code = raw_code

        if code not in items:
            entity_uri = (b.get("item") or {}).get("value", "").strip()
            label      = (b.get("itemLabel") or {}).get("value", "").strip()
            desc       = (b.get("itemDescription") or {}).get("value", "").strip()
            # Wikidata auto-generates a label equal to the Q-ID when no English
            # label exists; treat those as absent.
            if re.fullmatch(r'Q\d+', label):
                label = ""
            items[code] = {
                "entity_uri": entity_uri,
                "label":      label or None,
                "desc":       desc  or None,
                "uses":       [],
            }

        use_label = (b.get("useLabel") or {}).get("value", "").strip()
        if use_label and use_label not in items[code]["uses"]:
            items[code]["uses"].append(use_label)

    log_extraction(key, count=len(items))

    # Sort child codes.
    sorted_codes = sorted(items.keys(), key=_enumber_sort_key)

    # Build the enum: parent range-category PVs first, then children.
    permissible_values = {}

    for _lo, _hi, cat_key, cat_title in _RANGE_CATEGORIES:
        add_permissible_value(permissible_values, cat_key, title=cat_title)

    for code in sorted_codes:
        entry    = items[code]
        cat_key  = _range_category_key(code)

        # Build description: Wikidata short desc + P366 uses (when informative).
        desc_parts = []
        if entry["desc"]:
            desc_parts.append(entry["desc"])
        if entry["uses"]:
            uses_str = "; ".join(sorted(entry["uses"]))
            # Only append uses when they add information not already in the desc.
            if not entry["desc"] or uses_str.lower() not in entry["desc"].lower():
                desc_parts.append(f"Functional uses: {uses_str}")
        description = ".  ".join(desc_parts) if desc_parts else None

        add_permissible_value(
            permissible_values, code,
            title=entry["label"],
            description=description,
            is_a=cat_key,
            meaning=entry["entity_uri"],
            prefixes=_WD_PREFIXES,
        )

    source_url = (source.get("reachable_from") or {}).get("source_ontology", "")
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or "E Numbers (Wikidata P628)",
        description=source.get("description") or (
            "Food additive E numbers as recorded in Wikidata via property P628. "
            "Covers EU/UK approved additives and related international INS codes. "
            "Organised into EU numeric range categories with P366 functional-use annotations."
        ),
        version=source.get("version") or "",
        prefixes=dict(_WD_PREFIXES),
        enums={key: {"permissible_values": permissible_values}},
    )

    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper,
                  default_flow_style=False, sort_keys=False)
    print(f"  Written {len(permissible_values)} values to {yaml_path} "
          f"({len(_RANGE_CATEGORIES)} categories + {len(items)} E numbers)")


def match_enumber(url, config_file=MENU_CONFIG):
    """Return True if *url* points to the Wikidata E number concept and was handled.

    Accepts:
        https://www.wikidata.org/wiki/Q207810
        https://query.wikidata.org/sparql?query=...P628...

    On match: downloads via SPARQL, writes sources/{key}.json, creates a config
    entry, and runs process_enumber_source.  Returns True so term_harvester
    skips its generic download path.
    """
    if not (
        re.search(r'wikidata\.org/wiki/Q207810', url, re.IGNORECASE)
        or (re.search(r'wikidata\.org/sparql', url, re.IGNORECASE)
            and 'P628' in url)
    ):
        return False

    key = "E_NUMBER"

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    if key in config.get("sources", {}):
        print(
            f"  Skipping {url}: source key '{key}' already exists in {config_file}",
            flush=True,
        )
        return True

    json_path = f"sources/{key}.json"
    print(f"Fetching E_NUMBER from Wikidata SPARQL (P628 + P366)…")
    try:
        data = _fetch_sparql(_SPARQL_QUERY)
    except Exception as e:
        print(f"  Error fetching from Wikidata: {e}", flush=True)
        return True

    bindings = (data.get("results") or {}).get("bindings") or []
    if not bindings:
        print("  Warning: no bindings returned — config entry not created.", flush=True)
        return True

    os.makedirs("sources", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(bindings)} bindings to {json_path}")

    entry = make_source_entry(
        key,
        WIKIDATA_ENUMBER_URL,
        content_type="E_NUMBER",
        file_format="json",
        title="E Numbers (Wikidata P628)",
        description=(
            "Food additive E numbers as recorded in Wikidata via property P628. "
            "Covers EU/UK approved additives and related international INS codes. "
            "Organised into EU numeric range categories with P366 functional-use annotations."
        ),
    )
    entry["download_date"] = datetime.date.today().isoformat()
    entry["prefix_dict"] = {"wd": _WD_ENTITY_BASE}

    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added '{key}' to {config_file}")

    source = config["sources"][key]
    process_enumber_source(key, source)
    return True
