#!/usr/bin/env python3
"""Transform schema.yaml into entry_code_picklists.json using a mapping/diff file.

The mapping file (picklists_mapping.yaml) records structural differences between
the schema enum representation and the JSON picklist format:
  - enum key renames (schema key → JSON key)
  - per-PV code remaps (schema PV key → JSON Code)
  - extra columns (short_code, SGC_code, Month_number, …)
  - enum-level metadata (id, name.fr, description.fr, keywords)

FR translations for individual permissible values are stored separately in
agrifoodca_sssom.tsv (SSSOM format).  The schema's own locale extensions supply
FR for enums it already knows; the SSSOM file covers the remainder.

Usage:
    python schema_to_picklists.py [--generate] [--build]
        --generate   Write/refresh picklists_mapping.yaml and agrifoodca_sssom.tsv
                     from schema.yaml + existing entry_code_picklists.json.
        --build      Produce entry_code_picklists.json from schema.yaml +
                     picklists_mapping.yaml + agrifoodca_sssom.tsv (default).

Files (all relative to this script's directory):
    schema.yaml                   input schema
    entry_code_picklists.json     reference / output JSON
    picklists_mapping.yaml        generated diff/mapping config
    agrifoodca_sssom.tsv          SSSOM FR translations not in schema.yaml

NOTE: In the future we may move to have the schema.yaml AgriFoodCA_Picklists be
the gold standard over the same named ones in entry_code_picklists.json for the
title, description, and permissible_value entries that are defined in schema.yaml.
"""

import argparse
import csv
import io
import json
import os
import sys
import yaml

_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_PATH   = os.path.join(_DIR, "schema.yaml")
_JSON_PATH     = os.path.join(_DIR, "entry_code_picklists.json")
_MAPPING_PATH  = os.path.join(_DIR, "picklists_mapping.yaml")
_SSSOM_PATH    = os.path.join(_DIR, "agrifoodca_sssom.tsv")

# ---------------------------------------------------------------------------
# schema_key (in schema.yaml enums) → json_key (in entry_code_picklists.json)
# ---------------------------------------------------------------------------
_SCHEMA_TO_JSON = {
    "AgriFoodCA_AgreementScale":        "AgreementScale",
    "Canada":                           "CanadianProvinces",
    "AgriFoodCA_ComfortLevel":          "ComfortLevelScale",
    "AgriFoodCA_ConceptAwareness":      "AwarenessOfConcept",
    "AgriFoodCA_DataQualityCodes":      "DataQualityCodes",
    "AgriFoodCA_Days":                  "Days",
    "STATSCAN_1313722":                 "Highest level of education (Statistics Canada)",
    "AgriFoodCA_EducationLevel":        "EducationLevel",
    "AgriFoodCA_EightPointCardinality": "EightPointCardinality",
    "AgriFoodCA_Frequency":             "FrequencyScale",
    "GenderIdentity":                   "GenderIdentity",
    "AgriFoodCA_HouseholdComposition":  "HouseholdStructure",
    "AgriFoodCA_Months":                "Months",
    "NSDB_PMCHEM1":                     "ParentMaterialChemicalProperty",
    "NSDB_PMTEX1":                      "ParentMaterialTexture",
    "AgriFoodCA_SixteenPointCardinality": "SixteenPointCardinality",
    "AgriFoodCA_SoilAerationStatus":    "SoilAerationStatus",
    "AgriFoodCA_SoilBulkDensity":       "BulkDensityClass",
    "AgriFoodCA_SoilCarbonToNitrogenRatio": "Carbon-to-Nitrogen (C:N) ratio class",
    "AgriFoodCA_SoilColloidFraction":   "SoilColloidFractionClass",
    "AgriFoodCA_SoilCompressibility":   "SoilCompressibilityClass",
    "NSDB_DRAINAGE":                    "SoilDrainageClass",
    "AgriFoodCA_SoilEffectiveRootingDepth": "EffectiveRootingDepthClass",
    "AgriFoodCA_SoilErodibility":       "SoilErodibilityClass",
    "AgriFoodCA_SoilFertility":         "SoilFertilityClass",
    "AgriFoodCA_SoilMineralContentType": "SoilMineralContentType",
    "AgriFoodCA_SoilOrganicMatter":     "SoilOrganicMatterClass",
    "AgriFoodCA_SoilPermeability":       "SoilPermeabilityClass",
    "NRCSSoilFieldBook_ReactionPH":     "SoilPhClass",
    "AgriFoodCA_SoilPlasticity":        "SoilPlasticityClass",
    "AgriFoodCA_SoilPorosity":          "SoilPorosityClass",
    "SoilSalinityClass":                "Soil salinity class (ECe)",
    "AgriFoodCA_SoilSalinityType":      "Soil salinity type (dominant anion)",
    "AgriFoodCA_SoilSodicity":          "Soil sodicity class (SAR/ESP)",
    "SoilStructuralShapeScale":         "SoilStructureType",
    "AgriFoodCA_SoilTexture":           "SoilTextureClass",
    "StandardsMaturityLevel":           "MaturityLevels",
    "AgriFoodCA_SupportConcept":        "SupportScale",
    "AgriFoodCA_ThirtyTwoPointCardinality": "Thirty-two Point Cardinality",
    "NSDB_WATERTBL":                    "WaterTableCharacteristics",
    # CRediT and LodgingScale are in schema but excluded from the JSON picklist
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_schema():
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)

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


# ---------------------------------------------------------------------------
# --generate: build picklists_mapping.yaml from current schema + JSON
# ---------------------------------------------------------------------------

def generate_mapping():
    schema    = _load_schema()
    reference = _load_json()
    schema_enums = schema.get("enums") or {}
    fr_pvs = _schema_fr_pvs(schema)

    # Reverse map: json_key → schema_key
    json_to_schema = {v: k for k, v in _SCHEMA_TO_JSON.items()}

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
            if name_fr:
                sssom_rows.append((f"agrifoodca:{json_key}:name", name_fr))
            elif "fr" in ref_name:
                entry.setdefault("name", {})["fr"] = ""   # explicit empty placeholder
            if ref_desc_en and ref_desc_en != schema_desc_en:
                entry.setdefault("description", {})["en"] = ref_desc_en
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

    header = (
        "# picklists_mapping.yaml — diff/mapping from schema.yaml to entry_code_picklists.json\n"
        "# Edit this file to update names, descriptions, keywords, FR translations,\n"
        "# code remaps, and extra columns.  Re-run --build to regenerate the JSON.\n"
        "# Entries with 'static: true' have no schema source and are fully defined here.\n"
    )
    body = yaml.dump({"enums": ordered}, allow_unicode=True, default_flow_style=False,
                     sort_keys=False, indent=2)
    with open(_MAPPING_PATH, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(body)
    print(f"Wrote {_MAPPING_PATH} ({len(ordered)} entries)")

    _write_sssom(sssom_rows)


# ---------------------------------------------------------------------------
# --build: produce entry_code_picklists.json from schema + mapping
# ---------------------------------------------------------------------------

def build_json():
    schema   = _load_schema()
    schema_enums = schema.get("enums") or {}
    fr_pvs   = _schema_fr_pvs(schema)
    sssom_fr = _load_sssom()   # {json_key: {code: fr_label}}

    with open(_MAPPING_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    mapping = raw.get("enums") or raw  # support both wrapped and bare dict

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

    with open(_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Wrote {_JSON_PATH} ({len(output)} picklists)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--generate", action="store_true",
                        help="Generate/refresh picklists_mapping.yaml")
    parser.add_argument("--build", action="store_true",
                        help="Build entry_code_picklists.json (default)")
    args = parser.parse_args()

    if args.generate:
        generate_mapping()
    if args.build or not args.generate:
        build_json()


if __name__ == "__main__":
    main()
