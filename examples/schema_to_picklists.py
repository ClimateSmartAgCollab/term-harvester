#!/usr/bin/env python3
"""Transform schema.yaml into entry_code_picklists.json using a mapping/diff file.

The mapping file (agrifoodca_mapping.yaml) records structural differences between
the schema enum representation and the JSON picklist format:
  - enum key renames (schema key → JSON key)
  - per-PV code remaps (schema PV key → JSON Code)
  - extra columns (short_code, SGC_code, Month_number, …)
  - enum-level metadata (id, name.fr, description.fr, keywords)

FR translations for individual permissible values are stored separately in
agrifoodca_sssom.tsv (SSSOM format).  The schema's own locale extensions supply
FR for enums it already knows; the SSSOM file covers the remainder.

Usage:
    python schema_to_picklists.py [--calibrate] [--build] [--compare]
                                  [--add SchemaEnumKey:json_picklist_key]
        --calibrate  Write/refresh agrifoodca_mapping.yaml and agrifoodca_sssom.tsv
                     from schema.yaml + existing entry_code_picklists.json.
        --build      Produce entry_code_picklists.json from schema.yaml +
                     agrifoodca_mapping.yaml + agrifoodca_sssom.tsv (default).
        --compare    Show a unified diff of what --build would produce vs the
                     existing entry_code_picklists.json without writing the file.
        --add        Register a schema enum as a new JSON picklist entry, populate
                     all permissible values from schema.yaml, and rebuild the JSON.
                     Format: SchemaEnumKey:json_picklist_key

Files (all relative to this script's directory):
    schema.yaml                   input schema
    entry_code_picklists.json     reference / output JSON
    agrifoodca_mapping.yaml       generated diff/mapping config
    agrifoodca_sssom.tsv          SSSOM FR translations not in schema.yaml

NOTE: In the future we may move to have the schema.yaml AgriFoodCA_Picklists be
the gold standard over the same named ones in entry_code_picklists.json for the
title, description, and permissible_value entries that are defined in schema.yaml.
"""

import argparse
import csv
import difflib
import io
import json
import os
import re
import sys
import yaml

_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_PATH   = os.path.join(_DIR, "schema.yaml")
_JSON_PATH     = os.path.join(_DIR, "entry_code_picklists.json")
_MAPPING_PATH  = os.path.join(_DIR, "agrifoodca_mapping.yaml")
_SSSOM_PATH    = os.path.join(_DIR, "agrifoodca_sssom.tsv")

# ---------------------------------------------------------------------------
# Default schema_key → json_key mapping.
# This is used only when agrifoodca_mapping.yaml does not yet exist (bootstrap
# seed for --calibrate).  Once the file exists, its schema_to_json: section is
# the authoritative source; edit that to add or remove picklist entries.
# ---------------------------------------------------------------------------
_SCHEMA_TO_JSON = {
    "AgriFoodCA_AgreementScale":        "agreement_scale",
    "Canada":                           "canadian_provinces",
    "AgriFoodCA_ComfortLevel":          "comfort_level",
    "AgriFoodCA_ConceptAwareness":      "concept_awareness",
    "AgriFoodCA_DataQualityCodes":      "data_quality_codes",
    "AgriFoodCA_Days":                  "days",
    "STATSCAN_1313722":                 "education_level_stats_can",
    "AgriFoodCA_EducationLevel":        "education_level",
    "AgriFoodCA_EightPointCardinality": "eight_point_cardinality",
    "AgriFoodCA_Frequency":             "frequency",
    "GenderIdentity":                   "gender",
    "AgriFoodCA_HouseholdComposition":  "household_composition",
    "AgriFoodCA_Months":                "months",
    "NSDB_PMCHEM1":                     "parent_material_chemical_property",
    "NSDB_PMTEX1":                      "parent_material_texture",
    "AgriFoodCA_SixteenPointCardinality": "sixteen_point_cardinality",
    "AgriFoodCA_SoilAerationStatus":    "soil_aeration_status",
    "AgriFoodCA_SoilBulkDensity":       "soil_bulk_density",
    "AgriFoodCA_SoilCarbonToNitrogenRatio": "soil_carbon_to_nitrogen_ratio",
    "AgriFoodCA_SoilColloidFraction":   "soil_colloid_fraction",
    "AgriFoodCA_SoilCompressibility":   "soil_compressibility",
    "NSDB_DRAINAGE":                    "soil_drainage",
    "AgriFoodCA_SoilEffectiveRootingDepth": "soil_effective_rooting_depth",
    "AgriFoodCA_SoilErodibility":       "soil_erodibility",
    "AgriFoodCA_SoilFertility":         "soil_fertility",
    "AgriFoodCA_SoilMineralContentType": "soil_mineral_content_type",
    "AgriFoodCA_SoilOrganicMatter":     "soil_organic_matter",
    "AgriFoodCA_SoilPermeability":      "soil_permeability",
    "NRCSSoilFieldBook_ReactionPH":     "soil_ph",
    "AgriFoodCA_SoilPlasticity":        "soil_plasticity",
    "AgriFoodCA_SoilPorosity":          "soil_porosity",
    "SoilSalinityClass":                "soil_salinity",
    "AgriFoodCA_SoilSalinityType":      "soil_salinity_type",
    "AgriFoodCA_SoilSodicity":          "soil_sodicity",
    "SoilStructuralShapeScale":         "soil_structure",
    "AgriFoodCA_SoilTexture":           "soil_texture",
    "StandardsMaturityLevel":           "standards_stages",
    "AgriFoodCA_SupportConcept":        "support_concept",
    "AgriFoodCA_ThirtyTwoPointCardinality": "thirty_two_point_cardinality",
    "NSDB_WATERTBL":                    "water_table_characteristics",
    # CRediT and LodgingScale are in schema but excluded from the JSON picklist
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_schema():
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)

def _load_mapping_raw():
    """Load agrifoodca_mapping.yaml as a raw dict; returns {} if the file does not exist."""
    if not os.path.exists(_MAPPING_PATH):
        return {}
    with open(_MAPPING_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def _load_json():
    with open(_JSON_PATH, encoding="utf-8") as f:
        return json.load(f)

def _schema_fr_pvs(schema):
    """Return {enum_key: {pv_key: fr_title}} from schema locale extensions."""
    locales = (schema.get("extensions") or {}).get("locales", {}).get("value") or {}
    fr_enums = (locales.get("fr") or {}).get("enums") or {}
    result = {}
    for enum_key, enum_data in fr_enums.items():
        for pv_key, pv_data in ((enum_data or {}).get("permissible_values") or {}).items():
            result.setdefault(enum_key, {})[pv_key] = (pv_data or {}).get("title", "")
    return result


def _schema_fr_enum_meta(schema, enum_key):
    """Return {"title": ..., "description": ...} (keys only if non-empty) for an enum's FR locale."""
    locales = (schema.get("extensions") or {}).get("locales", {}).get("value") or {}
    fr_enums = (locales.get("fr") or {}).get("enums") or {}
    enum_data = fr_enums.get(enum_key) or {}
    meta = {}
    if enum_data.get("title"):
        meta["title"] = enum_data["title"]
    if enum_data.get("description"):
        meta["description"] = enum_data["description"]
    return meta


_SSSOM_PREAMBLE = """\
# agrifoodca_sssom.tsv — FR translations for picklist entries not covered by schema.yaml
#
# subject_id patterns:
#   agrifoodca:{picklist_key}:name            — enum name translation
#   agrifoodca:{picklist_key}:description     — enum description translation
#   agrifoodca:{picklist_key}:keywords:{kw}   — keyword translation (kw = EN keyword)
#   agrifoodca:{picklist_key}:choice:{Code}   — permissible-value title translation
#
# object_label carries the translation with a BCP-47 language tag, e.g. "Femme"@fr
curie_map:
  agrifoodca: https://github.com/agrifooddatacanada/picklists_for_schemas/blob/main/picklists/
  skos: http://www.w3.org/2004/02/skos/core#
mapping_set_id: agrifoodca:agrifoodca_sssom
mapping_set_description: French translations for AgriFoodCA picklist entries
---
"""


def _write_sssom(rows):
    """Write SSSOM TSV from list of (subject_id, fr_label) tuples."""
    with open(_SSSOM_PATH, "w", encoding="utf-8") as f:
        f.write(_SSSOM_PREAMBLE)
        f.write("subject_id\tpredicate_id\tobject_id\tobject_label\tmapping_justification\tauthor_id\tconfidence\tcomment\n")
        for subject_id, fr_label in rows:
            f.write(f"{subject_id}\tskos:exactMatch\t\t\"{fr_label}\"@fr\t\t\t\t\n")
    print(f"Wrote {_SSSOM_PATH} ({len(rows)} FR translations)")


def _strip_lang_tag(label):
    """Strip surrounding quotes and @fr tag: '"Femme"@fr' → 'Femme'."""
    label = label.strip()
    if label.startswith('"'):
        label = label[1:]
    if label.endswith('"@fr'):
        label = label[:-4]
    elif label.endswith('@fr'):
        label = label[:-3]
    return label


def _load_sssom():
    """Parse agrifoodca_sssom.tsv into a structured dict.

    Returns:
        {
          "name":        {json_key: fr_label},
          "description": {json_key: fr_label},
          "keywords":    {json_key: {kw_en: fr_label}},
          "choice":      {json_key: {code: fr_label}},
        }
    """
    result = {"name": {}, "description": {}, "keywords": {}, "choice": {}}
    if not os.path.exists(_SSSOM_PATH):
        return result
    with open(_SSSOM_PATH, encoding="utf-8") as f:
        raw = f.read()
    tsv_part = raw.split("---\n", 1)[-1]
    reader = csv.DictReader(io.StringIO(tsv_part), delimiter="\t")
    for row in reader:
        subject = row.get("subject_id", "")
        label   = _strip_lang_tag(row.get("object_label", ""))
        if not label:
            continue
        if not subject.startswith("agrifoodca:"):
            continue
        rest = subject[len("agrifoodca:"):]
        # json_key may contain colons, so match by known type suffixes/infixes
        if rest.endswith(":name"):
            result["name"][rest[:-5]] = label
        elif rest.endswith(":description"):
            result["description"][rest[:-12]] = label
        elif ":keywords:" in rest:
            idx = rest.rfind(":keywords:")
            result["keywords"].setdefault(rest[:idx], {})[rest[idx + 10:]] = label
        elif ":choice:" in rest:
            idx = rest.rfind(":choice:")
            result["choice"].setdefault(rest[:idx], {})[rest[idx + 8:]] = label
    return result


def _load_sssom_raw_rows():
    """Return list of (subject_id, fr_label) from the existing agrifoodca_sssom.tsv."""
    rows = []
    if not os.path.exists(_SSSOM_PATH):
        return rows
    with open(_SSSOM_PATH, encoding="utf-8") as f:
        raw = f.read()
    tsv_part = raw.split("---\n", 1)[-1]
    reader = csv.DictReader(io.StringIO(tsv_part), delimiter="\t")
    for row in reader:
        subject = (row.get("subject_id") or "").strip()
        label   = _strip_lang_tag(row.get("object_label", ""))
        if subject and label:
            rows.append((subject, label))
    return rows


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Fuzzy PV matching (used when codes differ and counts don't match)
# ---------------------------------------------------------------------------

def _normalize_for_match(text):
    """Lowercase, strip trailing parentheticals, collapse non-alphanumeric to spaces."""
    text = text.lower()
    text = re.sub(r'\s*\([^)]*\)\s*$', '', text).strip()  # strip "(highest)" etc.
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return text


def _token_jaccard(text1, text2):
    """Jaccard similarity on word token sets (order-independent)."""
    s1 = set(text1.split())
    s2 = set(text2.split())
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def _fuzzy_pv_match(json_rows, schema_pvs, threshold=0.96, jaccard_threshold=0.5):
    """Return {json_code: schema_pv_key} for rows matched by title similarity.

    Two-pass strategy:
      1. SequenceMatcher on normalized labels (threshold 0.96) — handles labels that
         differ only in minor formatting such as trailing parentheticals.
      2. Token Jaccard on word sets (threshold 0.5) — handles labels whose words are
         reordered between JSON and schema (e.g. duration-first vs category-first).

    Handles disambiguation: when two JSON codes would map to the same schema PV,
    the higher-scoring one wins and the other remains unmatched.
    """
    schema_norm = {
        pv_key: _normalize_for_match((pv_data or {}).get("title") or pv_key)
        for pv_key, pv_data in schema_pvs.items()
    }

    def _best_seq(norm):
        best_score, best_key = 0.0, None
        for pv_key, pv_norm in schema_norm.items():
            score = difflib.SequenceMatcher(None, norm, pv_norm).ratio()
            if score > best_score:
                best_score, best_key = score, pv_key
        return best_score, best_key

    # Pass 1: SequenceMatcher
    scores = {}  # json_code → (best_score, schema_pv_key)
    for row in json_rows:
        code = row.get("Code", "")
        norm = _normalize_for_match(row.get("en", "") or code)
        scores[code] = _best_seq(norm)

    claimed = {}  # schema_pv_key → (score, json_code)
    for code, (score, pv_key) in scores.items():
        if score < threshold or pv_key is None:
            continue
        if pv_key not in claimed or score > claimed[pv_key][0]:
            claimed[pv_key] = (score, code)
    matched = {code: pv_key for pv_key, (_, code) in claimed.items()}

    # Pass 2: token Jaccard for codes unmatched by pass 1
    unmatched_codes = [r.get("Code", "") for r in json_rows
                       if r.get("Code") not in matched]
    if unmatched_codes:
        unclaimed_pvs = {k: v for k, v in schema_norm.items() if k not in claimed}
        j_scores = {}
        for row in json_rows:
            code = row.get("Code", "")
            if code not in unmatched_codes:
                continue
            norm = _normalize_for_match(row.get("en", "") or code)
            best_score, best_key = 0.0, None
            for pv_key, pv_norm in unclaimed_pvs.items():
                score = _token_jaccard(norm, pv_norm)
                if score > best_score:
                    best_score, best_key = score, pv_key
            j_scores[code] = (best_score, best_key)

        j_claimed = {}
        for code, (score, pv_key) in j_scores.items():
            if score < jaccard_threshold or pv_key is None:
                continue
            if pv_key not in j_claimed or score > j_claimed[pv_key][0]:
                j_claimed[pv_key] = (score, code)
        for pv_key, (_, code) in j_claimed.items():
            matched[code] = pv_key

    if matched:
        still_unmatched = [r["Code"] for r in json_rows if r.get("Code") not in matched]
        print(f"    fuzzy matched {len(matched)} PV(s)"
              + (f"; unmatched: {still_unmatched}" if still_unmatched else ""))
    return matched


# ---------------------------------------------------------------------------
# Mapping file helpers (shared by --calibrate and --add)
# ---------------------------------------------------------------------------

_MAPPING_HEADER = (
    "# agrifoodca_mapping.yaml — diff/mapping from schema.yaml to entry_code_picklists.json\n"
    "# schema_to_json: controls which schema enums appear in entry_code_picklists.json.\n"
    "#   Add or remove entries here and re-run --calibrate to update the enums section,\n"
    "#   then --build to regenerate the JSON.\n"
    "# enums: per-picklist overrides for names, descriptions, keywords, FR translations,\n"
    "#   code remaps, and extra columns.  Regenerated by --calibrate; consumed by --build.\n"
    "# Entries with 'static: true' have no backing schema enum; content is defined here.\n"
    "# 'was_en' records the schema.yaml value that 'en' overrides (informational only;\n"
    "#   ignored by --build).\n"
)


def _write_mapping_file(schema_to_json, enums):
    """Sort enums by id and write schema_to_json + enums to agrifoodca_mapping.yaml."""
    ordered = dict(sorted(enums.items(), key=lambda kv: kv[1].get("id", 0)))
    body = yaml.dump(
        {"schema_to_json": schema_to_json, "enums": ordered},
        allow_unicode=True, default_flow_style=False, sort_keys=False, indent=2,
    )
    with open(_MAPPING_PATH, "w", encoding="utf-8") as f:
        f.write(_MAPPING_HEADER)
        f.write(body)


# --calibrate: build picklists_mapping.yaml from current schema + JSON
# ---------------------------------------------------------------------------

def generate_mapping():
    schema    = _load_schema()
    reference = _load_json()
    schema_enums = schema.get("enums") or {}
    fr_pvs = _schema_fr_pvs(schema)

    # schema_to_json: read from existing mapping file; fall back to in-script seed.
    existing_raw = _load_mapping_raw()
    schema_to_json = existing_raw.get("schema_to_json") or _SCHEMA_TO_JSON

    # Reverse map: json_key → schema_key
    json_to_schema = {v: k for k, v in schema_to_json.items()}

    mapping = {}
    sssom_rows = []   # (subject_id, fr_label) for translations not in schema

    for json_key, jentry in reference.items():
        schema_key = json_to_schema.get(json_key)
        senum = schema_enums.get(schema_key) if schema_key else None
        is_static = schema_key is None or senum is None

        entry = {}
        if schema_key and schema_key != json_key:
            entry["schema_key"] = schema_key
        if is_static:
            entry["static"] = True
        entry["id"] = jentry["id"]
        # EN name/description: store override in mapping only when it differs from schema.
        # FR always goes to SSSOM.
        schema_name_en = (senum.get("title") if senum else None) or json_key
        schema_desc_en = (senum.get("description") if senum else None) or ""
        ref_name_en = (jentry.get("name") or {}).get("en", "")
        ref_desc_en = (jentry.get("description") or {}).get("en", "")
        ref_name = jentry.get("name") or {}
        ref_desc = jentry.get("description") or {}
        name_fr = ref_name.get("fr", "")
        desc_fr = ref_desc.get("fr", "")
        if is_static:
            entry["name"] = dict(ref_name or {"en": json_key})
            entry["description"] = dict(ref_desc or {"en": ""})
        else:
            if ref_name_en and ref_name_en != schema_name_en:
                entry.setdefault("name", {})["en"] = ref_name_en
                schema_name_actual = (senum.get("title") if senum else "") or ""
                if schema_name_actual:
                    entry.setdefault("name", {})["was_en"] = schema_name_actual
            if name_fr:
                sssom_rows.append((f"agrifoodca:{json_key}:name", name_fr))
            elif "fr" in ref_name:
                entry.setdefault("name", {})["fr"] = ""   # explicit empty placeholder
            if ref_desc_en and ref_desc_en != schema_desc_en:
                entry.setdefault("description", {})["en"] = ref_desc_en
                if schema_desc_en:
                    entry.setdefault("description", {})["was_en"] = schema_desc_en
            if desc_fr:
                sssom_rows.append((f"agrifoodca:{json_key}:description", desc_fr))
            elif "fr" in ref_desc:
                entry.setdefault("description", {})["fr"] = ""   # explicit empty placeholder
        if jentry.get("keywords"):
            kw_map = {}
            for kw, trans in jentry["keywords"].items():
                trans = trans or {}
                kw_fr = trans.get("fr", "")
                kw_entry = {}
                if "en" in trans:
                    kw_entry["en"] = trans["en"]
                kw_map[kw] = kw_entry
                if kw_fr:
                    sssom_rows.append((f"agrifoodca:{json_key}:keywords:{kw}", kw_fr))
            entry["keywords"] = kw_map
        entry["category"]  = jentry.get("category", "general")
        source = jentry.get("source", "")
        if source:
            entry["source"] = source
        entry["languages"] = list(jentry.get("languages") or ["en"])
        extra_cols = [h for h in (jentry.get("headers") or []) if h not in ("Code", "en", "fr")]
        if extra_cols:
            entry["extra_mappings"] = extra_cols

        # Per-PV data: code remaps, EN/FR overrides, extra columns
        json_rows      = jentry.get("rows") or []
        extra_hdrs     = extra_cols
        schema_pvs     = (senum or {}).get("permissible_values") or {}
        schema_pv_keys = list(schema_pvs.keys())
        # Positional matching only makes sense when counts match exactly
        counts_match   = len(json_rows) == len(schema_pv_keys)
        # Fuzzy title matching when codes differ and counts don't align
        fuzzy_map = {}
        if not is_static and not counts_match and schema_pvs:
            fuzzy_map = _fuzzy_pv_match(json_rows, schema_pvs)

        pv_map = {}
        for row_idx, row in enumerate(json_rows):
            code = row["Code"]
            pv_entry = {}

            # Determine schema PV key for this code
            if not is_static:
                if code in schema_pvs:
                    schema_pv_key = code
                elif counts_match and row_idx < len(schema_pv_keys):
                    schema_pv_key = schema_pv_keys[row_idx]
                    if schema_pv_key != code:
                        pv_entry["schema_key"] = schema_pv_key
                elif code in fuzzy_map:
                    schema_pv_key = fuzzy_map[code]
                    if schema_pv_key != code:
                        pv_entry["schema_key"] = schema_pv_key
                else:
                    schema_pv_key = code  # extra or unmatched; no schema backing
            else:
                schema_pv_key = code
                pv_entry["en"] = row.get("en", code)

            # EN override: store when reference title differs from schema
            if not is_static:
                en_from_schema = (schema_pvs.get(schema_pv_key) or {}).get("title") or schema_pv_key
                en_from_json   = row.get("en", "")
                if en_from_json and en_from_json != en_from_schema:
                    pv_entry["en"] = en_from_json
                    en_from_schema_actual = (schema_pvs.get(schema_pv_key) or {}).get("title") or ""
                    if en_from_schema_actual:
                        pv_entry["was_en"] = en_from_schema_actual

            # FR: emit to SSSOM whenever reference differs from schema (covers overrides too)
            fr_from_schema = (fr_pvs.get(schema_key) or {}).get(schema_pv_key, "")
            fr_from_json   = row.get("fr", "")
            if fr_from_json and fr_from_json != fr_from_schema and "fr" in entry["languages"]:
                sssom_rows.append((f"agrifoodca:{json_key}:choice:{code}", fr_from_json))

            # Extra columns
            for h in extra_hdrs:
                if h in row:
                    pv_entry[h] = row[h]

            pv_map[code] = pv_entry  # always store to preserve reference row order

        if any(pv_map.values()):
            entry["permissible_values"] = pv_map

        mapping[json_key] = entry

    # Preserve insertion order matching JSON id ordering
    ordered = dict(sorted(mapping.items(), key=lambda kv: kv[1].get("id", 0)))

    _write_mapping_file(schema_to_json, mapping)
    print(f"Wrote {_MAPPING_PATH} ({len(mapping)} entries)")

    _write_sssom(sssom_rows)


# ---------------------------------------------------------------------------
# --add: register a new schema enum as a JSON picklist entry
# ---------------------------------------------------------------------------

def add_enum(add_arg):
    """Add a new schema enum → JSON picklist entry, then rebuild the JSON.

    add_arg format: "SchemaEnumKey:json_picklist_key"

    Adds the pair to schema_to_json, creates the enum entry in enums with all
    permissible values pre-populated from schema.yaml, writes the mapping file,
    and calls build_json() to update entry_code_picklists.json.
    Keywords, category, and source must be filled in manually afterwards.
    """
    if ":" not in add_arg:
        print("Error: --add argument must be 'SchemaEnumKey:json_picklist_key'",
              file=sys.stderr)
        sys.exit(1)
    schema_enum_key, json_key = add_arg.split(":", 1)
    schema_enum_key = schema_enum_key.strip()
    json_key        = json_key.strip()

    schema       = _load_schema()
    schema_enums = schema.get("enums") or {}
    senum        = schema_enums.get(schema_enum_key)
    if not senum:
        print(f"Error: '{schema_enum_key}' not found in schema.yaml enums", file=sys.stderr)
        sys.exit(1)

    raw            = _load_mapping_raw()
    schema_to_json = dict(raw.get("schema_to_json") or _SCHEMA_TO_JSON)
    enums          = dict(raw.get("enums") or {})

    if schema_enum_key in schema_to_json:
        print(f"Error: '{schema_enum_key}' is already in schema_to_json", file=sys.stderr)
        sys.exit(1)
    if json_key in enums:
        print(f"Error: '{json_key}' is already in the enums section", file=sys.stderr)
        sys.exit(1)

    schema_to_json[schema_enum_key] = json_key

    next_id    = max((e.get("id", 0) for e in enums.values()), default=0) + 1
    schema_pvs = (senum or {}).get("permissible_values") or {}
    fr_pvs     = _schema_fr_pvs(schema).get(schema_enum_key) or {}
    fr_meta    = _schema_fr_enum_meta(schema, schema_enum_key)
    has_fr     = bool(fr_pvs) or bool(fr_meta)
    languages  = ["en", "fr"] if has_fr else ["en"]

    # Pre-populate all PV codes with their EN titles from schema for user visibility.
    pv_map = {
        pv_key: {"en": (pv_data or {}).get("title") or pv_key}
        for pv_key, pv_data in schema_pvs.items()
    }

    entry = {}
    if schema_enum_key != json_key:
        entry["schema_key"] = schema_enum_key
    entry["id"]        = next_id
    entry["category"]  = "general"
    entry["languages"] = languages
    if pv_map:
        entry["permissible_values"] = pv_map

    enums[json_key] = entry

    _write_mapping_file(schema_to_json, enums)

    # If schema.yaml has FR enum title or description, append to SSSOM.
    if fr_meta:
        existing_rows    = _load_sssom_raw_rows()
        existing_subjects = {r[0] for r in existing_rows}
        new_sssom_rows   = []
        if fr_meta.get("title"):
            subj = f"agrifoodca:{json_key}:name"
            if subj not in existing_subjects:
                new_sssom_rows.append((subj, fr_meta["title"]))
        if fr_meta.get("description"):
            subj = f"agrifoodca:{json_key}:description"
            if subj not in existing_subjects:
                new_sssom_rows.append((subj, fr_meta["description"]))
        if new_sssom_rows:
            _write_sssom(existing_rows + new_sssom_rows)
            print(f"  Added {len(new_sssom_rows)} FR enum translation(s) to {_SSSOM_PATH}")

    title = (senum or {}).get("title") or schema_enum_key
    print(f"Added '{schema_enum_key}' → '{json_key}' (id {next_id}, {len(pv_map)} PVs,"
          f" title: {title!r})")
    print(f"  Add keywords/category/source to {_MAPPING_PATH} as needed, then run --build.")
    build_json()


# ---------------------------------------------------------------------------
# --build / --compare: produce entry_code_picklists.json from schema + mapping
# ---------------------------------------------------------------------------

def _build_output():
    """Return the output dict that --build would write, without touching the file."""
    schema   = _load_schema()
    schema_enums = schema.get("enums") or {}
    fr_pvs   = _schema_fr_pvs(schema)
    sssom_fr = _load_sssom()   # {json_key: {code: fr_label}}

    raw = _load_mapping_raw()
    # New format has schema_to_json + enums keys; legacy format was a bare enum dict.
    mapping = raw.get("enums") if "enums" in raw else raw

    output = {}

    for json_key, entry in mapping.items():
        schema_key = entry.get("schema_key", json_key)
        is_static  = entry.get("static", False)
        senum      = schema_enums.get(schema_key) if not is_static else None
        schema_pvs = (senum or {}).get("permissible_values") or {}
        schema_pv_keys = list(schema_pvs.keys())

        # Collect PV overrides keyed by JSON Code
        pv_overrides = entry.get("permissible_values") or {}

        # Build rows: use pv_overrides order if present, else schema PV order
        if pv_overrides:
            row_codes = list(pv_overrides.keys())
        elif schema_pvs:
            row_codes = list(schema_pvs.keys())
        else:
            row_codes = []

        extra_hdrs = entry.get("extra_mappings") or []
        languages = entry.get("languages") or ["en"]
        headers = ["Code"] + extra_hdrs + languages
        has_fr = "fr" in languages

        rows = []
        for code in row_codes:
            pv_over  = pv_overrides.get(code) or {}
            # Schema PV key: use explicit override, else try code directly, else positional
            pv_schema_key = pv_over.get("schema_key", code)
            schema_pv = schema_pvs.get(pv_schema_key) or {}
            en_title = pv_over.get("en") or schema_pv.get("title") or pv_schema_key or code

            row = {"Code": code, "en": en_title}

            # FR priority: SSSOM (explicit reference override) → schema extension → mapping
            if has_fr:
                fr_val = (
                    sssom_fr["choice"].get(json_key, {}).get(code, "")
                    or (fr_pvs.get(schema_key) or {}).get(pv_schema_key, "")
                    or pv_over.get("fr", "")
                )
                row["fr"] = fr_val

            # Extra columns
            for h in extra_hdrs:
                row[h] = pv_over.get(h, "")

            # Reorder row to match headers
            ordered_row = {h: row[h] for h in headers if h in row}
            rows.append(ordered_row)

        # EN from schema; FR from SSSOM (with mapping override as fallback).
        name_en = (senum.get("title") if senum else None) or json_key
        desc_en = (senum.get("description") if senum else None) or ""
        name_map = entry.get("name") or {}
        desc_map = entry.get("description") or {}
        name_out = {"en": name_map.get("en", name_en)}
        desc_out = {"en": desc_map.get("en", desc_en)}
        # Emit fr for name/description when has_fr OR when SSSOM/mapping has explicit fr
        name_fr_val = sssom_fr["name"].get(json_key)          # None = not in SSSOM
        if name_fr_val is None:
            name_fr_val = name_map.get("fr")                  # None = not in mapping
        if name_fr_val is not None or has_fr:
            name_out["fr"] = name_fr_val if name_fr_val is not None else ""
        desc_fr_val = sssom_fr["description"].get(json_key)
        if desc_fr_val is None:
            desc_fr_val = desc_map.get("fr")
        if desc_fr_val is not None or has_fr:
            desc_out["fr"] = desc_fr_val if desc_fr_val is not None else ""

        record = {
            "id":          entry["id"],
            "name":        name_out,
            "description": desc_out,
        }
        kw_sssom = sssom_fr["keywords"].get(json_key, {})
        keywords_out = {}
        for kw, kw_data in (entry.get("keywords") or {}).items():
            kw_out = dict(kw_data)
            kw_fr = kw_sssom.get(kw, "")
            if kw_fr:
                kw_out["fr"] = kw_fr
            keywords_out[kw] = kw_out
        record["keywords"] = keywords_out
        record["category"]  = entry.get("category", "general")
        record["source"]    = entry.get("source") or ""
        record["languages"] = languages
        record["headers"]   = headers
        record["rows"]      = rows

        output[json_key] = record

    return output


def build_json():
    output = _build_output()
    with open(_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Wrote {_JSON_PATH} ({len(output)} picklists)")


def compare_json():
    """Show a unified diff of what --build would produce vs the existing JSON file."""
    new_text = json.dumps(_build_output(), ensure_ascii=False, indent=2) + "\n"
    with open(_JSON_PATH, encoding="utf-8") as f:
        existing_text = f.read()
    if new_text == existing_text:
        print("No differences — --build output matches existing entry_code_picklists.json")
        return
    diff = list(difflib.unified_diff(
        existing_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="entry_code_picklists.json (existing)",
        tofile="entry_code_picklists.json (--build output)",
    ))
    for line in diff:
        print(line, end="")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calibrate", action="store_true",
                        help="Write/refresh agrifoodca_mapping.yaml and agrifoodca_sssom.tsv")
    parser.add_argument("--build", action="store_true",
                        help="Build entry_code_picklists.json (default)")
    parser.add_argument("--compare", action="store_true",
                        help="Show diff of --build output vs existing entry_code_picklists.json"
                             " without writing the file")
    parser.add_argument("--add", metavar="SCHEMA_KEY:JSON_KEY",
                        help="Add a schema enum as a new picklist entry and rebuild the JSON")
    args = parser.parse_args()

    if args.add:
        add_enum(args.add)
    else:
        if args.calibrate:
            generate_mapping()
        if args.compare:
            compare_json()
        elif args.build or not args.calibrate:
            build_json()


if __name__ == "__main__":
    main()
