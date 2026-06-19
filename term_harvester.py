#!/usr/bin/env python3
# Authors: Damion Dooley and Claude (Anthropic claude-sonnet-4-6)
#
# TODO: The -l lookup function currently assumes subClassOf object property
# traversal when expanding reachable_from.source_nodes.
# Dynamic enumeration of reachable_from relationship_types (e.g. partOf,
# hasPart, etc.) has not yet been implemented.
#
#
# Usage examples:
#   Build or update schema.yaml with default LinkML top-level structure:
#     python term_harvester.py -b
#
#   Fetch (download) sources — -f behaviour:
#     python term_harvester.py -f all          # fetch every source in harvester_config.yaml
#     python term_harvester.py -f KEY1 KEY2    # fetch only the named source(s)
#     python term_harvester.py -c KEY -f       # fetch only the source(s) listed with -c
#     python term_harvester.py -f              # no-op; prints reminder to use -f all or -c
#
#   Generate enum report for all sources in harvester_config.yaml:
#     python term_harvester.py -r
#
#   Either of the above with tab-delimited output:
#     python term_harvester.py -f -t
#     python term_harvester.py -r -t
#
#   Expand reachable_from.source_nodes enums via API — always operates on schema.yaml:
#     python term_harvester.py -l                    # expand all enums with reachable_from.source_nodes
#     python term_harvester.py -l linkml_valuesets   # expand enums imported_from a named source
#     python term_harvester.py -l MyBiomeEnum        # expand one enum by name
#     python term_harvester.py -l linkml_valuesets MyBiomeEnum  # source key and enum name mixed
#
#   The API used for each ontology prefix is determined by the harvester_config.yaml
#   'apis' block (see DEFAULT_CONFIG_COMMENTS).  OLS4 is the default fallback.
#   BioPortal requires an apikey in the apis > bioportal > type > rest > apikey field.
#   Agrovoc has its own API, either a sparql endpoint (implemented), or a 
#   skosimos API (not implemented).  It is possible to add Agrovoc directly
#   as a file but it is 60Mb+. 
#
#   Full refresh — fetch all sources, process into source YAMLs, rebuild schema.yaml:
#     python term_harvester.py -f all -c -b
#
#   Add a new source from a URL (auto-detects type, adds to harvester_config.yaml, and processes it):
#     python term_harvester.py -a https://example.org/some-valueset.json
#
#   Update harvester_config.yaml with prefix dicts from all sources:
#     python term_harvester.py -c
#
#   Update harvester_config.yaml for only the linkml_valuesets source:
#     python term_harvester.py -c linkml_valuesets
#
#   Build schema.yaml (sync enums and prefixes from all sources):
#     python term_harvester.py -b
#
#   Note: when -b detects that an enum present in schema.yaml is no longer
#   in its source file, it reports the enum key rather than deleting it.
#   This gives the menu manager the opportunity to manually review whether
#   the menu item should be removed from their system or retained.
#
#   Regenerate harvester_config.yaml from scratch with -a (one source per call):
#
#     # linkml_valuesets (LinkML)
#     python term_harvester.py -a https://raw.githubusercontent.com/linkml/valuesets/refs/heads/main/src/valuesets/merged/merged_hierarchy.yaml
#
#     # FUTURE: SNOMED VIA OLS4
#     # http://snomed.info/id/128603005 
#     # https://www.ebi.ac.uk/ols4/api/ontologies/snomed/terms/http%253A%252F%252Fsnomed.info%252Fid%252F128603005/graph?lang=fr
#     # https://snowstorm.ihtsdotools.org/snowstorm/snomed-ct/swagger-ui/index.html
#     # python3 term_harvester.py -a "https://snowstorm.ihtsdotools.org/snowstorm/snomed-ct/MAIN/concepts/762766007/descendants?limit=2296"
#
#   Add Loinc CodeSsytems & Valuesets by supplying the raw json link:
#
#     # LOINC_DataAbsentReason (LOINCCodeSystem)
#     python term_harvester.py -a https://terminology.hl7.org/7.1.0/en/CodeSystem-data-absent-reason.json
#
#     # LOINC_PersonalPronouns (LOINCValueSet)
#     python term_harvester.py -a https://terminology.hl7.org/7.1.0/en/ValueSet-pronouns.json
#
#     # LOINC_GenderIdentity (LOINCValueSet)
#     python term_harvester.py -a https://terminology.hl7.org/en/ValueSet-gender-identity.json
#
#     # NSDBSoilNameAndLayerV2 (NSDB) — National Soil DataBase, combined Soil Name Table + Soil Layer Table
#     python term_harvester.py -a https://sis.agr.gc.ca/cansis/nsdb/soil/v2/index.html
#
#     # NSDBSNTv2 (NSDBSNT) — National Soil DataBase, Soil Name Table
#     python term_harvester.py -a https://sis.agr.gc.ca/cansis/nsdb/soil/v2/snt/index.html
#
#     # NSDBSLTv2 (NSDBSLT) — National Soil DataBase, Soil Layer Table
#     python term_harvester.py -a https://sis.agr.gc.ca/cansis/nsdb/soil/v2/slt/index.html
#
#     # NSDBSLCv3_2 (NSDBSLC) — National Soil DataBase, Soil Landscapes of Canada
#     python term_harvester.py -a https://sis.agr.gc.ca/cansis/nsdb/slc/v3.2/index.html
#
#     # LOINCValuesets (LOINC valueset listing page — run -c after to fetch all enums)
#     python term_harvester.py -a https://terminology.hl7.org/en/valuesets.html
#
#   Fetch enum YAML for a STATSCAN source (follows each classification code's
#   Display structure and Display definitions pages to build the full hierarchy):
#   Get variable page URI from https://www.statcan.gc.ca/en/concepts/search, by 
#   clicking on a variable name.
#
#     python term_harvester.py -a "https://www23.statcan.gc.ca/imdb/p3VD.pl?Function=getVD&TVD=1368814"
#     python term_harvester.py -c STATSCAN_1441857
#    
#   North American Product Classification System (NAPCS) Canada 2022 Version 1.0
#   See https://www.statcan.gc.ca/en/subjects/standard/napcs/2022/index
#   and csv version: https://www.statcan.gc.ca/en/media/5274
#   USA: See https://www.census.gov/naics/napcs/?8976654?yearbck=2022
#   Mexico: https://www.inegi.org.mx/contenidos/app/scpm/scpm_completo.xlsx
#
#   Add and process a NAPCSCanada source (content_type auto-detected from CSV headers;
#   year is extracted from the URL to form the source key):
#     python term_harvester.py -a "https://www.statcan.gc.ca/en/media/5274"
#     python term_harvester.py -c NAPCSCanada_2022
#
#   The optional "concise: true" source attribute (in harvester_config.yaml) trims
#   redundant hierarchy nodes during -b build.  A node is dropped when its
#   title exactly matches its parent's title — the child adds no new label —
#   and any grandchildren are re-wired to the nearest surviving ancestor.
#   Currently supported for content_type: NAPCSCanada.
#
#   Example: a NAPCS hierarchy where class "011" (title "Crop products") has
#   a child "0110" also titled "Crop products" — the child is redundant and
#   is dropped.  Any codes that had is_a: "0110" are re-wired to is_a: "011".
#
#   In harvester_config.yaml:
#     NAPCSCanada_2022:
#       content_type: NAPCSCanada
#       concise: true
#       ...
#
#   To build without concise filtering (keep all nodes), omit the attribute
#   or set concise: false.
#
#   Add ISO 3166-2 country subdivision codes (content_type: ISO_COUNTRY) by supplying
#   the ISO Online Browsing Platform (OBP) URL for any country.  Because the OBP page
#   is a Vaadin single-page application that cannot be fetched directly, the handler
#   queries the Wikidata Query Service (SPARQL) instead using two queries:
#     1. Property P297 (ISO 3166-1 alpha-2) to resolve the country's English name and
#        use it as the enum key (e.g. "Canada").
#     2. Property P300 (ISO 3166-2 code) filtered by the alpha-2 prefix to retrieve
#        all subdivisions.  rdfs:label is fetched for every project locale (from the
#        top-level locales: list in harvester_config.yaml).
#   Each permissible value carries:
#     meaning:        wd:Q…  (Wikidata QID, e.g. wd:Q1951 for Alberta)
#     exact_mappings: [iso:CA-AB]
#   Results are cached in sources/{key}.json; re-run -f {key} to refresh from Wikidata.
#
#     # Canada — provinces and territories
#     python term_harvester.py -a "https://www.iso.org/obp/ui/#iso:code:3166:CA"
#     python term_harvester.py -c ISO_COUNTRY_CA
#
#     # United States — states and territories
#     python term_harvester.py -a "https://www.iso.org/obp/ui/#iso:code:3166:US"
#     python term_harvester.py -c ISO_COUNTRY_US
#
#     # Refresh after Wikidata updates
#     python term_harvester.py -f ISO_COUNTRY_CA
#     python term_harvester.py -c ISO_COUNTRY_CA
#
#   OWL ontologies (content_type: OWL) require owlready2 (pip install owlready2).
#   The source file is saved as sources/{key}.text regardless of original suffix.
#   Auto-detected from URL extension (.owl, .ofn, .rdf, .ttl) or file content:
#
#     python term_harvester.py -a https://purl.obolibrary.org/obo/envo.owl
#
#   The optional minus/include concept lists in harvester_config.yaml filter OWL
#   classes by their English rdfs:label (case-insensitive).  minus removes a
#   class and its entire subtree; include restores specific labels even when
#   an ancestor was excluded (re-wiring children to the nearest kept ancestor):
#
#     Envo:
#       content_type: OWL
#       file_format: text
#       reachable_from:
#         source_ontology: https://purl.obolibrary.org/obo/envo.owl
#       minus:
#         concepts: [environmental feature, quality]
#       include:
#         concepts: [water body]
#

import argparse
import concurrent.futures
import csv
import datetime
import html
import json
import os
import re
import sqlite3
import sys
import tempfile
import urllib.parse
import urllib.request
from collections import defaultdict
import yaml

# Locate companion source_* modules in sources/ regardless of CWD.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources"))

from source_ontologyapi import (
    iri_to_curie,
    get_ols4_inner_iri,
    fetch_api_graph,
    process_skos_source,
    match_snomed,
    match_ontology_term,
)
from source_linkml import (
    apply_sorted_prefixes,
    process_linkml_source,
    match_linkml,
)
from source_owl import (
    _extract_owl_metadata,
    process_owl_source,
    match_owl,
)
from source_agrovoc import (
    _fetch_agrovoc_concept_info,
    _fetch_agrovoc_sparql_graph,
    match_agrovoc,
)
from source_napcscanada import (
    process_napcscanada_source,
    match_napcs_csv,
)
from source_agrifoodca import (
    process_agrifood_dir_source,
    refetch_agrifood_dir,
    match_agrifood_csv,
    match_agrifood_dir,
)
from source_statscan import (
    statscan_fr_url,
    parse_statscan_definitions,
    parse_statscan_structure,
    fetch_statscan_source,
    process_statscan_source,
    match_statscan,
    match_statscan_catalog,
    ENUM_DEFINITIONS as STATSCAN_CATALOG,
)
from source_statscan_table import (
    process_statscan_table_source,
    match_statscan_table,
)
from source_iso_country import (
    process_iso_country_source,
    fetch_iso_country_source,
    match_iso_country,
    match_iso_country_all,
)
from source_loinc import (
    to_camel_case,
    collect_loinc_concepts,
    convert_loinc_codesystem_to_linkml,
    collect_loinc_valueset_concepts,
    convert_loinc_valueset_to_linkml,
    parse_loinc_table_page,
    parse_loinc_valueset_html_page,
    fill_loinc_source_metadata,
    process_loinc_table_source,
    match_loinc_table,
)
from source_nsdb import (
    nsdb_fr_url,
    find_section_paragraph,
    find_links_by_text,
    find_named_section_links,
    find_contents_table_links,
    find_list_section_links,
    parse_attribute_page,
    fetch_nsdb_html_source,
    fetch_nsdb_source,
    process_nsdb_html_source,
    process_nsdb_source,
    match_nsdb_snt,
    match_nsdb_slt,
    match_nsdb_soil,
    match_nsdb_slc,
)
from source_nrcs import (
    process_nrcs_source,
    fetch_nrcs_pdf,
    match_nrcs,
)
from source_nasis import (
    process_nasis_source,
    match_nasis,
)
from source_credit import (
    process_credit_source,
    fetch_credit_source,
    match_credit,
)
from source_loc_classification import (
    process_loc_source,
    fetch_loc_source,
    match_loc,
)
from source_cansis_glossary import (
    process_cansis_glossary_source,
    fetch_cansis_glossary_source,
    match_cansis_glossary,
)
from source_freetext import (
    match_freetext,
    process_freetext_source,
    fetch_freetext_source,
)
from source_utils import (
    MENU_CONFIG,
    BROWSER_HEADERS,
    IndentedDumper,
    strip_tags,
    fetch_html,
    sort_prefixes,
    make_config_schema,
    add_permissible_value,
    _make_locale_extensions,
    find_description_before_table,
    find_labeled_field,
    normalize_text,
    to_pascal_case_key,
    make_source_entry,
    write_config,
    update_source_config,
    rename_source_key,
    keys_from_minus,
    is_curie,
)

# SSSOM predicate_id → LinkML permissible_value attribute name.
# See https://github.com/mapping-commons/sssom/ for the SSSOM specification
# and https://www.w3.org/TR/skos-reference/#mapping for SKOS mapping properties.
SSSOM_PREDICATE_MAP = {
    "skos:closeMatch":   "close_mappings",
    "skos:broadMatch":   "broad_mappings",
    "skos:narrowMatch":  "narrow_mappings",
    "skos:exactMatch":   "exact_mappings",
    "skos:relatedMatch": "related_mappings",
}

DEFAULT_CONFIG_COMMENTS = [
    'See docs on "reachable_from": https://linkml.io/linkml-model/latest/docs/reachable_from/',
    "Config below doesn't support LinkML dynamic enumeration \"inherits\", and is limited custom version of LinkML dynamic enumerations, not quite in context of LinkML schema.",
    'Note that "minus" list is implemented before "includes" list, which restores subordinate items that would otherwise have been eliminated by minus list items and their underlying items.',
    "See https://linkml.io/linkml-model/latest/docs/EnumExpression/",
    "Here 'reachable_from' is acted on via 'term_harvester.py -c' configuration to generate the LinkML .yaml schema file for the given source.  Over in schema.yaml, 'reachable_from' is acted on via 'term_harvester.py -l' for lookup function to populate schema.yaml enums.",
    "The optional top-level 'apis' object configures API endpoints for the -l lookup function.",
    "Each key is a service name (e.g. 'ols', 'bioportal', 'agrovoc') with a 'type' sub-object",
    "containing protocol-keyed configs (e.g. 'rest', 'sparql'), each with 'uri' and optional 'apikey',",
    "plus an 'ontologies' list.  The -l lookup routes each CURIE prefix to the first api whose",
    "'ontologies' list contains it; falls back to OLS4.",
    "Example apis block:",
    "  apis:",
    "    ols:",
    "      type:",
    "        rest:",
    "          uri: http://www.ebi.ac.uk/ols4/api/ontologies/{ontology}/terms/{double_encoded}/graph",
    "      ontologies: [ENVO, GO, UBERON]",
    "    bioportal:",
    "      type:",
    "        rest:",
    "          uri: https://data.bioontology.org",
    "          apikey: YOUR_BIOPORTAL_KEY",
    "      ontologies: [MESH, NCIT, SNOMEDCT]",
    "    agrovoc:",
    "      type:",
    "        sparql:",
    "          uri: https://agrovoc.fao.org/sparql/",
    "      ontologies: [agrovoc]",
]


def enum_in_minus(enum_key, enum_def, minus_set):
    """Return True if enum_key or its annotations source_domain/source_schema match minus_set."""
    if not minus_set:
        return False
    if enum_key in minus_set:
        return True
    ann = (enum_def.get("annotations") or {}) if enum_def else {}
    return ann.get("source_domain") in minus_set or ann.get("source_schema") in minus_set



def update_download_date(source_key, config_file=MENU_CONFIG):
    """Update the download_date for a source entry in harvester_config.yaml."""
    update_source_config(source_key, {"download_date": datetime.date.today().isoformat()}, config_file)


def _load_sssom(path_or_uri):
    """Load a SSSOM (Simple Standard for Sharing Ontology Mappings) TSV file.

    SSSOM is a community standard for representing ontology/vocabulary mappings
    in a tabular format.  See https://github.com/mapping-commons/sssom/ for the
    full specification.

    The file may begin with '#'-prefixed metadata lines (including an embedded
    YAML curie_map block) followed by a tab-separated header row and data rows.
    Required columns: subject_id, predicate_id, object_id.
    Other columns (subject_label, object_label, match_type, Comments, …) are
    allowed by the standard and are preserved in each row dict but not used here.

    path_or_uri: local file path or http/https URL.

    Returns a dict {subject_id: [row_dict, …]} indexed by subject_id for fast
    lookup.  Rows whose subject_id is empty are silently skipped.
    """
    import csv, io

    if path_or_uri.startswith(("http://", "https://")):
        req = urllib.request.Request(path_or_uri, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            content = resp.read().decode(charset, errors="replace")
    else:
        with open(path_or_uri, "r", encoding="utf-8") as f:
            content = f.read()

    # Strip leading '#' metadata/comment lines; the first non-comment line is
    # the TSV header.
    data_lines = [ln for ln in content.splitlines() if not ln.startswith("#")]
    if not data_lines:
        return {}

    index = {}
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)), delimiter="\t")
    for row in reader:
        subject_id = (row.get("subject_id") or "").strip()
        if subject_id:
            index.setdefault(subject_id, []).append(row)
    return index


def apply_sssom_mappings(predicates=None, schema_file="schema.yaml", config_file=MENU_CONFIG):
    """Apply SSSOM ontology mappings to permissible_values in schema.yaml.

    Reads SSSOM files listed in the top-level 'sssom' array of harvester_config.yaml
    (each entry may be a local relative path or an http/https URL), then for
    every permissible_value in schema.yaml whose 'meaning' field matches a
    subject_id in the SSSOM data, writes the matching object_id values into the
    appropriate mapping attribute on the permissible_value.

    SSSOM predicate_id → LinkML permissible_value attribute:
      skos:closeMatch   → close_mappings
      skos:broadMatch   → broad_mappings
      skos:narrowMatch  → narrow_mappings
      skos:exactMatch   → exact_mappings
      skos:relatedMatch → related_mappings

    predicates: list of predicate_id strings to apply (e.g. ['skos:closeMatch']).
                Pass None or [] to apply all five.
    """
    with open(config_file, "r") as f:
        config = yaml.safe_load(f) or {}

    sssom_files = config.get("sssom") or []
    if not sssom_files:
        print("No 'sssom' files listed in harvester_config.yaml — nothing to apply", file=sys.stderr)
        return

    # Resolve which predicates to apply
    if predicates:
        unknown = [p for p in predicates if p not in SSSOM_PREDICATE_MAP]
        for p in unknown:
            print(
                f"Warning: unknown predicate '{p}' — valid values: "
                f"{', '.join(SSSOM_PREDICATE_MAP)}",
                file=sys.stderr,
            )
        active = {p: SSSOM_PREDICATE_MAP[p] for p in predicates if p in SSSOM_PREDICATE_MAP}
    else:
        active = dict(SSSOM_PREDICATE_MAP)

    if not active:
        print("No valid predicates to apply — aborting", file=sys.stderr)
        return

    # Load and merge all SSSOM files into one index
    sssom_index = {}
    for sssom_path in sssom_files:
        try:
            partial = _load_sssom(sssom_path)
            for subj, rows in partial.items():
                sssom_index.setdefault(subj, []).extend(rows)
            print(f"Loaded SSSOM: {len(partial)} subject IDs from {sssom_path}")
        except Exception as _e:
            print(f"Warning: could not load SSSOM file '{sssom_path}': {_e}", file=sys.stderr)

    if not sssom_index:
        print("No SSSOM mappings loaded — nothing to apply")
        return

    if not os.path.exists(schema_file):
        print(f"{schema_file} not found — run -b first", file=sys.stderr)
        return

    with open(schema_file, "r") as f:
        schema = yaml.safe_load(f) or {}

    mapping_counts = {attr: 0 for attr in active.values()}
    pv_updated = 0

    for enum_def in (schema.get("enums") or {}).values():
        if not isinstance(enum_def, dict):
            continue
        pvs = enum_def.get("permissible_values") or {}
        for pv_code, pv in pvs.items():
            meaning = (pv or {}).get("meaning", "")
            if not meaning:
                continue
            rows = sssom_index.get(meaning, [])
            if not rows:
                continue
            changed = False
            pv = dict(pv)  # copy before mutating
            for predicate, attr in active.items():
                object_ids = [
                    r["object_id"].strip()
                    for r in rows
                    if r.get("predicate_id", "").strip() == predicate
                    and r.get("object_id", "").strip()
                ]
                if object_ids:
                    pv[attr] = object_ids
                    mapping_counts[attr] += len(object_ids)
                    changed = True
            if changed:
                pvs[pv_code] = pv
                pv_updated += 1

    with open(schema_file, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)

    print(f"Updated {schema_file}: {pv_updated} permissible_value(s) received mappings")
    for attr, count in mapping_counts.items():
        if count:
            print(f"  {attr}: {count} mapping(s)")


def _normalize_enum_def(enum_def):
    """Normalize typographic Unicode in text fields of a LinkML enum definition.

    Applies normalize_text() to enum-level title/description and to each
    permissible value's title/description/comments.  Mutates and returns the dict.
    This ensures stale intermediate source YAMLs cannot carry unclean characters
    into schema.yaml regardless of when they were last regenerated by -c/-f.
    """
    if not isinstance(enum_def, dict):
        return enum_def
    for field in ("title", "description", "comments"):
        if enum_def.get(field):
            enum_def[field] = normalize_text(enum_def[field])
    for pv in (enum_def.get("permissible_values") or {}).values():
        if not isinstance(pv, dict):
            continue
        for field in ("title", "description", "comments"):
            if pv.get(field):
                pv[field] = normalize_text(pv[field])
    return enum_def


# ---------------------------------------------------------------------------
# Translation SSSOM helpers  (used by --translate and -b)
# ---------------------------------------------------------------------------

_TRANSLATE_SSSOM_PREAMBLE = """\
# {config_stem}_sssom.tsv — machine translations for schema.yaml locale extensions
# Generated by term_harvester.py --translate; review before relying on translations.
#
# subject_id patterns:
#   {ns}:{{source_key}}:{{enum_key}}:name         — enum title translation
#   {ns}:{{source_key}}:{{enum_key}}:description  — enum description translation
#   {ns}:{{source_key}}:{{enum_key}}:choice:{{code}} — permissible-value title translation
#
# object_label carries the translation with a BCP-47 language tag, e.g. "Texto"@es or "Texte"@fr
# Run -b after editing this file to apply translations to schema.yaml.
curie_map:
  {ns}: {schema_id}
  skos: http://www.w3.org/2004/02/skos/core#
mapping_set_id: {ns}:{config_stem}_sssom
mapping_set_description: Machine translations for {schema_name} schema
---
"""


def _write_translate_sssom(rows, path, config_stem, schema_id="", schema_name=""):
    """Write translate SSSOM TSV from list of (subject_id, lang, text) tuples."""
    ns = config_stem
    preamble = _TRANSLATE_SSSOM_PREAMBLE.format(
        config_stem=config_stem,
        ns=ns,
        schema_id=schema_id or f"https://example.org/{config_stem}",
        schema_name=schema_name or config_stem,
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(preamble)
        f.write("subject_id\tpredicate_id\tobject_id\tobject_label\t"
                "mapping_justification\tauthor_id\tconfidence\tcomment\n")
        for subject_id, lang, text in rows:
            f.write(f"{subject_id}\tskos:exactMatch\t\t\"{text}\"@{lang}\t\t\t\t\n")
    print(f"Wrote {path} ({len(rows)} translation entries)")


def _load_translate_sssom_raw(path):
    """Return list of (subject_id, lang, text) from existing translate SSSOM file."""
    import csv as _csv, io as _io
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        content = f.read()
    tsv_part = content.split("---\n", 1)[-1]
    reader = _csv.DictReader(_io.StringIO(tsv_part), delimiter="\t")
    for row in reader:
        subject   = (row.get("subject_id") or "").strip()
        label_raw = (row.get("object_label") or "").strip()
        if not subject or not label_raw:
            continue
        m = re.match(r'^"(.*)"\@([a-z]{2,3})$', label_raw)
        if not m:
            continue
        rows.append((subject, m.group(2), m.group(1)))
    return rows


def _apply_translate_sssom(schema, sssom_path, config_stem):
    """Merge translations from translate SSSOM into schema.yaml locale extensions.

    Subject format: {config_stem}:{source_key}:{enum_key}:name|description|choice:{pv_key}
    Applies across all source keys (enum_key is the index into schema.yaml enums).
    """
    raw_rows = _load_translate_sssom_raw(sssom_path)
    if not raw_rows:
        return
    ns_prefix = config_stem + ":"
    applied = 0
    for subject, lang, text in raw_rows:
        if not subject.startswith(ns_prefix) or not text:
            continue
        rest = subject[len(ns_prefix):]
        # rest = "{source_key}:{enum_key}:name|description|choice:{pv_key}"
        # source_key has no ":", so parts[0] is source_key; remainder is enum+type
        colon_idx = rest.find(":")
        if colon_idx < 0:
            continue
        colon_rest = rest[colon_idx + 1:]   # "{enum_key}:name" etc.

        # Determine type and enum_key
        if colon_rest.endswith(":name"):
            enum_key = colon_rest[:-5]
            field    = "title"
            pv_key   = None
        elif colon_rest.endswith(":description"):
            enum_key = colon_rest[:-12]
            field    = "description"
            pv_key   = None
        elif ":choice:" in colon_rest:
            idx      = colon_rest.rfind(":choice:")
            enum_key = colon_rest[:idx]
            field    = "pv_title"
            pv_key   = colon_rest[idx + 8:]
        else:
            continue

        # Get or create the lang locale in schema extensions
        ext      = schema.setdefault("extensions", {})
        loc      = ext.setdefault("locales", {"tag": "locales", "value": {}})
        lang_loc = loc.setdefault("value", {}).setdefault(lang, {
            "id":          schema.get("id", ""),
            "name":        schema.get("name", ""),
            "version":     str(schema.get("version", "")),
            "in_language": lang,
            "enums":       {},
        })
        lang_loc.setdefault("enums", {})

        if field in ("title", "description"):
            lang_loc["enums"].setdefault(enum_key, {})[field] = text
            applied += 1
        elif field == "pv_title":
            pv_dict = lang_loc["enums"].setdefault(enum_key, {}).setdefault("permissible_values", {})
            pv_dict.setdefault(pv_key, {})["title"] = text
            applied += 1

    if applied:
        print(f"Applied {applied} translation(s) from {sssom_path}")


def build_schema(schema_file="schema.yaml", config_file=MENU_CONFIG, keys=None):
    """Create or update schema.yaml with LinkML top-level structure, enums, and prefixes.

    On creation, populates default values. On update, only adds keys that are
    missing — existing values are preserved.

    Syncs enums from each source's yaml file in the sources/ folder:
    - Upserts enums tagged with imported_from into schema["enums"].
    - Reports enums no longer present in their source for manual review.

    Syncs prefixes from all sources stored in harvester_config.yaml:
    - Upserts prefix key+URI pairs from each source's 'prefixes' dict.
    - Removes schema prefixes absent from every source's stored prefix list.
    - Warns if any source has no stored prefix list (run -c to populate).
    - Sorts prefixes alphabetically (case-insensitive) and warns on case variants.
    """
    folder = os.path.basename(os.path.abspath("."))

    defaults = {
        "id": f"https://example.org/{folder}",
        "name": "example_name",
        "title": "Example title",
        "description": "Example description ...",
        "version": "",
        "license": "CC0",
        "prefixes": {"anthropics": "https://github.com/anthropics/"},
        "default_prefix": "menu",
        "imports": ["linkml:types"],
        "slots": {},
        "enums": {}
    }

    if os.path.exists(schema_file):
        with open(schema_file, "r") as f:
            schema = yaml.safe_load(f) or {}
        for key, value in defaults.items():
            if key not in schema:
                schema[key] = value
        action = "Updated"
    else:
        schema = defaults
        action = "Created"

    # Sync enums and prefixes from harvester_config.yaml sources
    prefix_conflicts = []  # collected at end for stdout summary
    if not os.path.exists(config_file):
        print(f"Warning: {config_file} not found — no sources to import. "
              f"Run -a to add sources or ensure you are in the correct project directory.")
    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            config = yaml.safe_load(f) or {}
        all_sources = config.get("sources", {})

        # Sync enums from each source's yaml file into schema
        sources_to_build = {k: v for k, v in all_sources.items() if keys is None or k in keys}
        _build_rows = []  # (key, added, updated, reported, excl, deleted, empty_names, conflicts, concepts, pvs)
        if keys:
            missing = [k for k in keys if k not in all_sources]
            for k in missing:
                print(f"Warning: source key '{k}' not found in {config_file}", file=sys.stderr)
        for key, source in sources_to_build.items():
            source_path = f"sources/{key}.yaml"
            if not os.path.exists(source_path):
                print(f"Skipping {key} enums: {source_path} not found — run -f and -c first", file=sys.stderr)
                continue

            with open(source_path, "r") as f:
                source_data = yaml.safe_load(f)

            # Build full prefix map for this source (source YAML + any config additions).
            # Only prefixes actually referenced in permissible_value meanings are added
            # to schema — avoids polluting schema.yaml with unused namespace declarations.
            source_prefix_map = {
                **(source_data.get("prefixes") or {}),
                **(source.get("prefixes") or {}),
            }
            for enum_def in (source_data.get("enums") or {}).values():
                for pv in ((enum_def or {}).get("permissible_values") or {}).values():
                    meaning = (pv or {}).get("meaning", "")
                    if is_curie(meaning):
                        pfx = meaning.split(":")[0]
                        if pfx not in source_prefix_map:
                            continue
                        new_uri = source_prefix_map[pfx]
                        if pfx in schema["prefixes"]:
                            if schema["prefixes"][pfx] != new_uri:
                                prefix_conflicts.append(
                                    f"  prefix conflict: '{pfx}' already mapped to "
                                    f"'{schema['prefixes'][pfx]}' but '{key}' requires "
                                    f"'{new_uri}' — skipping"
                                )
                        else:
                            schema["prefixes"][pfx] = new_uri

            source_enums = source_data.get("enums") or {}
            enum_added = enum_updated = enum_reported = enum_excluded = enum_deleted = enum_conflicts = 0
            enum_concepts_included = enum_pvs_included = 0

            minus = source.get("minus") or {}
            minus_concepts = keys_from_minus(minus.get("concepts"))
            minus_pvs = keys_from_minus(minus.get("permissible_values"))
            minus_status = keys_from_minus(minus.get("status"))

            include = source.get("include") or {}
            include_concepts = keys_from_minus(include.get("concepts"))
            include_pvs = keys_from_minus(include.get("permissible_values"))

            # include without minus → implicit "exclude all, restore only listed"
            exclude_all_concepts = bool(include_concepts) and not minus_concepts
            exclude_all_pvs = bool(include_pvs) and not minus_pvs

            # Pre-compute empty enums (no permissible_values or reachable_from, not
            # minus-excluded or include-excluded) so is_a references to them can be
            # cleaned up in the copy loop regardless of iteration order.
            empty_enum_set = {
                ek for ek, ev in source_enums.items()
                if not enum_in_minus(ek, ev, minus_concepts)
                and not (exclude_all_concepts and not enum_in_minus(ek, ev, include_concepts))
                and not (ev or {}).get("permissible_values")
                and not (ev or {}).get("reachable_from")
            }
            empty_enum_names = sorted(empty_enum_set)

            existing_from_source = {
                k for k, v in schema["enums"].items()
                if isinstance(v, dict)
                and isinstance(v.get("annotations"), dict)
                and v["annotations"].get("imported_from") == key
            }

            # Delete from schema any excluded enums whose source_file matches
            # this source, so stale entries are cleaned up on re-build.
            if minus_concepts or exclude_all_concepts:
                for enum_key in list(schema["enums"]):
                    existing_def = schema["enums"][enum_key]
                    excluded = (
                        enum_in_minus(enum_key, existing_def, minus_concepts)
                        or (exclude_all_concepts
                            and not enum_in_minus(enum_key, existing_def, include_concepts))
                    )
                    if not excluded:
                        continue
                    ann = (existing_def.get("annotations") or {})
                    if ann.get("source_file") == source_path:
                        del schema["enums"][enum_key]
                        enum_deleted += 1

            for enum_key, enum_def in source_enums.items():
                if (enum_in_minus(enum_key, enum_def, minus_concepts)
                        or (exclude_all_concepts
                            and not enum_in_minus(enum_key, enum_def, include_concepts))):
                    enum_excluded += 1
                    continue
                if enum_key in empty_enum_set:
                    continue
                enum_def = dict(enum_def) if enum_def else {}
                if enum_def.get("is_a") in empty_enum_set:
                    del enum_def["is_a"]
                if enum_def.get("status"):
                    enum_def["status"] = str(enum_def["status"]).upper()
                if (minus_pvs or exclude_all_pvs) and enum_def.get("permissible_values"):
                    enum_def["permissible_values"] = {
                        k: v for k, v in enum_def["permissible_values"].items()
                        if k not in minus_pvs
                        and (not exclude_all_pvs or k in include_pvs)
                    }
                if (minus_status and source.get("content_type") == "OWL"
                        and enum_def.get("permissible_values")):
                    enum_def["permissible_values"] = {
                        k: v for k, v in enum_def["permissible_values"].items()
                        if (v or {}).get("status") not in minus_status
                    }

                # concise: true — drop permissible_values with status: obsolete
                if source.get("concise") and enum_def.get("permissible_values"):
                    pvs = enum_def["permissible_values"]
                    before = len(pvs)
                    enum_def["permissible_values"] = {
                        k: v for k, v in pvs.items()
                        if (v or {}).get("status") != "obsolete"
                    }
                    n_dropped = before - len(enum_def["permissible_values"])
                    if n_dropped:
                        print(f"  concise: dropped {n_dropped} obsolete pv(s) from {enum_key}")

                # concise: true — for supported content types, drop any permissible_value
                # whose title is identical to its parent's title, then re-wire is_a
                # references that pointed to a dropped entry up to the nearest surviving
                # ancestor so the hierarchy remains consistent.
                if (source.get("concise")
                        and source.get("content_type") in {"NAPCSCanada"}
                        and enum_def.get("permissible_values")):
                    pvs = enum_def["permissible_values"]
                    title_by_code  = {c: (pv or {}).get("title", "") for c, pv in pvs.items()}
                    parent_by_code = {c: (pv or {}).get("is_a")       for c, pv in pvs.items()}

                    # Entries to drop: has a parent AND shares the parent's title
                    dropped = {
                        c for c, pv in pvs.items()
                        if parent_by_code.get(c)
                        and title_by_code.get(c)
                        and title_by_code.get(c) == title_by_code.get(parent_by_code[c])
                    }

                    if dropped:
                        def _resolve_ancestor(code, _seen=None):
                            """Walk is_a chain (original parents) to first non-dropped ancestor."""
                            if _seen is None:
                                _seen = set()
                            p = parent_by_code.get(code)
                            if p is None or p in _seen:
                                return None
                            if p not in dropped:
                                return p
                            _seen.add(p)
                            return _resolve_ancestor(p, _seen)

                        new_pvs = {}
                        for code, pv in pvs.items():
                            if code in dropped:
                                continue
                            pv = dict(pv) if pv else {}
                            if pv.get("is_a") in dropped:
                                ancestor = _resolve_ancestor(code)
                                if ancestor:
                                    pv["is_a"] = ancestor
                                else:
                                    pv.pop("is_a", None)
                            new_pvs[code] = pv
                        enum_def["permissible_values"] = new_pvs
                        print(f"  concise: dropped {len(dropped)} redundant pv(s) from {enum_key}")

                if source.get("see_also"):
                    enum_def["see_also"] = source["see_also"]

                annotations = dict(enum_def.get("annotations") or {})
                annotations["imported_from"] = key
                annotations["source_file"] = source_path
                enum_def["annotations"] = annotations

                # For each language present in the source YAML's extensions.locales.value,
                # carry translated permissible_values for surviving codes into the
                # matching schema["extensions"]["locales"]["value"][lang]["enums"] block.
                _source_locales = (
                    (source_data.get("extensions") or {})
                    .get("locales", {})
                    .get("value", {})
                )
                if _source_locales:
                    surviving_codes = set(enum_def.get("permissible_values") or {})
                    for lang, _lang_locale in _source_locales.items():
                        _lang_pvs_all = (
                            (_lang_locale.get("enums") or {})
                            .get(enum_key, {})
                            .get("permissible_values") or {}
                        )
                        if not _lang_pvs_all:
                            continue
                        _lang_pvs = {c: pv for c, pv in _lang_pvs_all.items()
                                     if c in surviving_codes}
                        if not _lang_pvs:
                            continue
                        _ext  = schema.setdefault("extensions", {})
                        _loc  = _ext.setdefault("locales", {"tag": "locales", "value": {}})
                        _lang = _loc.setdefault("value", {}).setdefault(lang, {
                            "id":          schema.get("id", ""),
                            "name":        schema.get("name", ""),
                            "title":       schema.get("title", ""),
                            "description": schema.get("description", ""),
                            "in_language": lang,
                            "enums":       {},
                        })
                        _lang.setdefault("enums", {})[enum_key] = {"permissible_values": _lang_pvs}

                _normalize_enum_def(enum_def)
                if enum_key not in schema["enums"]:
                    schema["enums"][enum_key] = enum_def
                    enum_added += 1
                else:
                    existing_annotations = schema["enums"][enum_key].get("annotations") or {}
                    existing_source_file = existing_annotations.get("source_file", "")
                    if existing_source_file and existing_source_file != source_path:
                        print(
                            f"  Error: enum '{enum_key}' already defined in '{existing_source_file}';"
                            f" '{source_path}' also defines it — skipping",
                            file=sys.stderr
                        )
                        enum_conflicts += 1
                    elif schema["enums"][enum_key] != enum_def:
                        schema["enums"][enum_key] = enum_def
                        enum_updated += 1

            # Report enums gone from source (but not intentionally excluded via minus)
            for enum_key in sorted(existing_from_source - set(source_enums.keys())):
                if enum_in_minus(enum_key, schema["enums"].get(enum_key), minus_concepts):
                    continue
                print(f"  Review: '{enum_key}' is no longer in {key} source — remove manually if no longer needed")
                enum_reported += 1

            # Second pass: restore concepts and permissible_values listed in include,
            # overriding any minus exclusions for those specific items.
            if include_concepts:
                for concept_label in include_concepts:
                    # Match by direct enum key first, then by source_schema/source_domain annotation
                    if concept_label in source_enums:
                        matched = [(concept_label, source_enums[concept_label])]
                    else:
                        matched = [
                            (ek, ev) for ek, ev in source_enums.items()
                            if (ev or {}).get("annotations", {}).get("source_schema") == concept_label
                            or (ev or {}).get("annotations", {}).get("source_domain") == concept_label
                        ]
                    if not matched:
                        print(f"  Warning: include concept '{concept_label}' not found in {source_path}", file=sys.stderr)
                        continue
                    for enum_key, raw_def in matched:
                        enum_def = dict(raw_def) if raw_def else {}
                        if enum_def.get("status"):
                            enum_def["status"] = str(enum_def["status"]).upper()
                        if enum_def.get("permissible_values"):
                            pvs = {k: v for k, v in enum_def["permissible_values"].items()
                                   if k not in minus_pvs or k in include_pvs}
                            enum_def["permissible_values"] = pvs
                        if source.get("see_also"):
                            enum_def["see_also"] = source["see_also"]
                        annotations = dict(enum_def.get("annotations") or {})
                        annotations["imported_from"] = key
                        annotations["source_file"] = source_path
                        enum_def["annotations"] = annotations
                        schema["enums"][enum_key] = enum_def
                        enum_concepts_included += 1

            if include_pvs:
                for enum_key, existing_def in schema["enums"].items():
                    ann = (existing_def.get("annotations") or {})
                    if ann.get("source_file") != source_path:
                        continue
                    orig_pvs = (source_enums.get(enum_key) or {}).get("permissible_values") or {}
                    current_pvs = dict(existing_def.get("permissible_values") or {})
                    for pv_key in include_pvs:
                        if pv_key in orig_pvs and pv_key not in current_pvs:
                            current_pvs[pv_key] = orig_pvs[pv_key]
                            enum_pvs_included += 1
                    existing_def["permissible_values"] = current_pvs

            _build_rows.append((
                key, enum_added, enum_updated, enum_reported,
                enum_excluded, enum_deleted, list(empty_enum_names),
                enum_conflicts, enum_concepts_included, enum_pvs_included,
            ))

        # --- Per-source columnar build report ---
        if _build_rows:
            has_excl     = any(r[4] for r in _build_rows)
            has_del      = any(r[5] for r in _build_rows)
            has_empty    = any(r[6] for r in _build_rows)
            has_conf     = any(r[7] for r in _build_rows)
            has_concepts = any(r[8] for r in _build_rows)
            has_pvs      = any(r[9] for r in _build_rows)

            headers = ["Source", "Added", "Updated", "To review"]
            if has_excl:     headers.append("Excluded")
            if has_del:      headers.append("Del")
            if has_empty:    headers.append("Empty")
            if has_conf:     headers.append("Conf")
            if has_concepts: headers.append("Restored")
            if has_pvs:      headers.append("PVs+")

            table = []
            for r in _build_rows:
                key_r, added, updated, reported, excl, deleted, empty_names, conf, concepts, pvs = r
                row = [key_r, str(added), str(updated), str(reported)]
                if has_excl:     row.append(str(excl))
                if has_del:      row.append(str(deleted))
                if has_empty:    row.append(str(len(empty_names)))
                if has_conf:     row.append(str(conf))
                if has_concepts: row.append(str(concepts))
                if has_pvs:      row.append(str(pvs))
                table.append((row, empty_names))

            widths = [max(len(headers[i]), max(len(r[0][i]) for r in table))
                      for i in range(len(headers))]

            def _fmt_row(cells):
                return "  ".join(
                    cells[i].ljust(widths[i]) if i == 0 else cells[i].rjust(widths[i])
                    for i in range(len(cells))
                )

            print(_fmt_row(headers))
            print("-" * len(_fmt_row(headers)))
            for row, empty_names in table:
                print(_fmt_row(row))
                if empty_names:
                    print(f"  Skipped — no permissible_values or reachable_from: "
                          f"{', '.join(sorted(empty_names))}")
            print()

        sources_missing = [k for k, v in all_sources.items() if "prefixes" not in v]
        if sources_missing:
            print(
                f"Warning: prefix lists not stored for: {', '.join(sources_missing)}. "
                f"Run -c to populate them. Skipping prefix sync.",
                file=sys.stderr
            )
        else:
            # Collect all prefixes from every source
            protected = {}
            prefix_sources = defaultdict(list)  # prefix key -> [source keys that define it]
            for src_key, src in all_sources.items():
                for k, v in (src.get("prefixes") or {}).items():
                    protected[k] = v
                    prefix_sources[k].append(src_key)

            prefix_added = prefix_updated = 0
            for prefix, uri in protected.items():
                if prefix not in schema["prefixes"]:
                    schema["prefixes"][prefix] = uri
                    prefix_added += 1
                elif schema["prefixes"][prefix] != uri:
                    schema["prefixes"][prefix] = uri
                    prefix_updated += 1

            schema["prefixes"] = sort_prefixes(schema["prefixes"])

            print(f"Prefixes: {prefix_added} added, {prefix_updated} updated")

            # Report prefix keys that are identical except for case
            case_groups = defaultdict(list)
            for prefix in schema["prefixes"]:
                case_groups[prefix.lower()].append(prefix)
            collisions = [keys for keys in case_groups.values() if len(keys) > 1]
            for keys in sorted(collisions):
                detail = ", ".join(
                    f"{k} ({', '.join(prefix_sources[k])})" for k in keys
                )
                print(f"  Warning: case-variant prefix keys: {detail}", file=sys.stderr)

    # Report enums in schema.yaml not attributed to any current harvester_config.yaml source
    if os.path.exists(config_file):
        known_sources = set(all_sources.keys())
        orphans = []
        for enum_key, enum_def in schema.get("enums", {}).items():
            ann = (enum_def.get("annotations") or {}) if isinstance(enum_def, dict) else {}
            imported_from = ann.get("imported_from", "")
            if not imported_from or imported_from not in known_sources:
                orphans.append((enum_key, imported_from))
        if orphans:
            print(f"\nWarning: {len(orphans)} enum(s) not linked to any harvester_config.yaml source:")
            for enum_key, imported_from in sorted(orphans):
                if imported_from:
                    print(f"  {enum_key}  (imported_from: '{imported_from}' — source not in config)")
                else:
                    print(f"  {enum_key}  (no imported_from annotation)")

    if schema.get("enums"):
        schema["enums"] = dict(sorted(schema["enums"].items(), key=lambda x: x[0].lower()))

    if schema.get("prefixes"):
        schema["prefixes"] = sort_prefixes(schema["prefixes"])

    # Apply translation SSSOM if present ({config_stem}_sssom.tsv).
    config_stem = os.path.splitext(os.path.basename(config_file))[0]
    trans_sssom_path = f"{config_stem}_sssom.tsv"
    if os.path.exists(trans_sssom_path):
        _apply_translate_sssom(schema, trans_sssom_path, config_stem)

    with open(schema_file, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)

    print(f"{action} {schema_file}")

    if prefix_conflicts:
        print(f"\nPrefix conflicts ({len(prefix_conflicts)}):")
        for msg in prefix_conflicts:
            print(msg)



def _fetch_to_file(url, dest_path, timeout=60):
    """Download *url* (fragment stripped) to *dest_path*.

    Tries curl first — it handles more server configurations (TLS quirks,
    redirects, government CDNs) than Python's urllib.  Falls back to urllib
    if curl is not on PATH or exits non-zero.

    Returns (success: bool, content_disposition: str, content_type: str).
    """
    import shutil as _shutil, subprocess as _subprocess
    fetch_url = url.split("#")[0]

    curl = _shutil.which("curl")
    if curl:
        result = _subprocess.run(
            [curl, "-L",
             "--max-time", str(timeout),
             "--connect-timeout", "15",
             "--silent", "--show-error", "--fail",
             "-A", "Mozilla/5.0 (compatible; term-harvester/1.0)",
             "--write-out", "%{content_type}",
             "-o", dest_path,
             fetch_url],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and os.path.getsize(dest_path) > 0:
            return True, "", result.stdout.strip()
        if result.returncode != 0:
            print(f"  curl failed (exit {result.returncode})"
                  f" — falling back to urllib", file=sys.stderr)
            if result.stderr.strip():
                print(f"  {result.stderr.strip()}", file=sys.stderr)

    try:
        req = urllib.request.Request(fetch_url, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            cd = resp.headers.get("Content-Disposition", "")
            ct = resp.headers.get("Content-Type", "")
            with open(dest_path, "wb") as f:
                f.write(resp.read())
        return True, cd, ct
    except Exception as e:
        return False, str(e), ""


def add_source(urls, config_file=MENU_CONFIG, free_text=None):
    """Add sources from URLs to harvester_config.yaml and process them.

    For each URL, downloads the file and detects its type:
    - AGROVOC concept IRI (aims.fao.org/aos/agrovoc/{id})  -> content_type: OntologyAPI
    - SNOMED concept IRI (snomed.info/id/{id})             -> content_type: OntologyAPI
    - AgriFoodCA GitHub directory URL                      -> content_type: AgriFoodCA
    - AgriFoodCA individual picklist CSV (content-based)   -> content_type: AgriFoodCA
    - URL matching https://sis.agr.gc.ca/cansis/nsdb/soil  -> content_type: NSDB
    - ISO OBP URL (iso.org/obp/ui/#iso:code:3166:XX)       -> content_type: ISO_COUNTRY
      (OBP is a Vaadin SPA; data is fetched from Wikidata SPARQL using P297/P300;
       saved as sources/{key}.json with wd: meanings and iso: exact_mappings)
    - URL from terminology.hl7.org with .html extension (not a single ValueSet/
      CodeSystem detail page)             -> content_type: LOINC
    - JSON with resourceType CodeSystem   -> content_type: LOINCCodeSystem
    - JSON with resourceType ValueSet     -> content_type: LOINCValueSet
    - YAML with LinkML schema structure   -> content_type: LinkML

    Creates a harvester_config.yaml entry, saves the file to sources/, fills in
    metadata, and runs process_sources for the new key.
    """
    os.makedirs("sources", exist_ok=True)

    if not os.path.exists(config_file):
        write_config({"comment": DEFAULT_CONFIG_COMMENTS, "locales": ["en"], "sources": {}}, config_file)
        print(f"Created {config_file}")

    for url in urls:
        # Unescape HTML entities (e.g. &amp; → &) so the server receives a valid URL
        url = html.unescape(url)

        # Resolve a bare STATSCAN key (e.g. STATSCAN_1313722) to its full URL.
        # Prefers the catalog-stored URL so Variables route to p3Var.pl (not
        # a constructed p3VD.pl URL).  Falls back to p3VD.pl when not cached.
        _statscan_key_m = re.match(r'^(STATSCAN_(\d+))$', url, re.IGNORECASE)
        if _statscan_key_m:
            _numeric_id = _statscan_key_m.group(2)
            _catalog_entry = next(
                (e for e in STATSCAN_CATALOG if e["tvd_id"] == _numeric_id), None
            )
            if _catalog_entry:
                url = _catalog_entry["url"]
                print(f"  {_statscan_key_m.group(1)} → {_catalog_entry['title']}"
                      f" [{_catalog_entry.get('entry_type', 'Classification')}]"
                      f" ({_catalog_entry['subject']})")
            else:
                url = (f"https://www23.statcan.gc.ca/imdb/p3VD.pl"
                       f"?Function=getVD&TVD={_numeric_id}")
                print(f"  {_statscan_key_m.group(1)} → {url}")

        # FreeText: extract from provided text via Claude — no download needed.
        # Always stop after the call; do not fall through to other matchers
        # even if the API key is missing or extraction fails.
        if free_text:
            try:
                with open(config_file) as _pf:
                    _pre_keys = set((yaml.safe_load(_pf) or {}).get("sources", {}).keys())
            except Exception:
                _pre_keys = set()
            match_freetext(url, free_text, config_file)
            try:
                with open(config_file) as _pf:
                    _post_keys = set((yaml.safe_load(_pf) or {}).get("sources", {}).keys())
            except Exception:
                _post_keys = set()
            for _new_key in (_post_keys - _pre_keys):
                _upsert_source_in_index(_new_key)
            continue

        # Pre-download detectors: handle their own download (or need none)
        if match_agrovoc(url, config_file):
            continue
        if match_snomed(url, config_file):
            continue
        if match_ontology_term(url, config_file):
            continue
        if match_agrifood_dir(url, config_file):
            continue
        if match_nasis(url, config_file):
            continue
        if match_credit(url, config_file):
            continue
        if match_loc(url, config_file):
            continue
        if match_cansis_glossary(url, config_file):
            continue
        if match_statscan_catalog(url, config_file):
            continue
        if match_iso_country_all(url, config_file):
            continue

        # Check if base URL already matches an existing source; skip download if so.
        _base_add_url = url.split("#")[0]
        _dup_key = None
        _dup_local = None
        try:
            with open(config_file) as _cf:
                _cfcfg = yaml.safe_load(_cf) or {}
            for _ek, _es in (_cfcfg.get("sources") or {}).items():
                _eo = ((_es.get("reachable_from") or {}).get("source_ontology", "")).split("#")[0]
                if _eo and _eo == _base_add_url:
                    _dup_key = _ek
                    for _ext in ("pdf", "html", "htm", "txt", "yaml", "yml", "json", "csv", "zip"):
                        _cand = f"sources/{_ek}.{_ext}"
                        if os.path.exists(_cand):
                            _dup_local = _cand
                            break
                    break
        except Exception:
            pass
        if _dup_key:
            _msg = f"  URL already registered as source '{_dup_key}'"
            if _dup_local:
                _msg += f" (local file: {_dup_local})"
            print(_msg + " — skipping.", file=sys.stderr)
            if _dup_local:
                print(f"  Tip: to extract enumerations from this file via Claude, use"
                      f" -a 'URL' --free_text 'topic'", file=sys.stderr)
            continue

        print(f"Fetching {url} ...")
        tmp_fd, tmp_path = tempfile.mkstemp()
        os.close(tmp_fd)
        downloaded_filename = ""
        ok, cd_or_err, _http_ct = _fetch_to_file(url, tmp_path)
        if not ok:
            print(f"  Error fetching {url}: {cd_or_err}", file=sys.stderr)
            os.unlink(tmp_path)
            continue
        if cd_or_err:
            fn_m = re.search(r'filename\s*=\s*["\']?([^"\';\r\n]+)["\']?',
                             cd_or_err, re.IGNORECASE)
            if fn_m:
                downloaded_filename = fn_m.group(1).strip().strip('"\'')

        if os.path.getsize(tmp_path) == 0:
            print(f"  Error: downloaded file is empty — skipping {url}", file=sys.stderr)
            os.unlink(tmp_path)
            continue

        # URL-pattern and content-based detection (matcher returns True if handled)
        if (match_nsdb_snt(url, tmp_path, config_file) or
                match_nsdb_slt(url, tmp_path, config_file) or
                match_nsdb_soil(url, tmp_path, config_file) or
                match_nsdb_slc(url, tmp_path, config_file) or
                match_loinc_table(url, tmp_path, config_file) or
                match_statscan_table(url, tmp_path, config_file) or
                match_statscan(url, tmp_path, config_file) or
                match_iso_country(url, tmp_path, config_file) or
                match_napcs_csv(url, tmp_path, config_file, downloaded_filename) or
                match_agrifood_csv(url, tmp_path, config_file)):
            continue

        if match_owl(url, tmp_path, config_file, process_fn=process_sources):
            continue

        if match_linkml(url, tmp_path, config_file, process_fn=process_sources):
            continue

        # Remaining: JSON (LOINC) or document (→ FreeText)
        _url_base = url.split("?")[0].rstrip("/").split("/")[-1]
        _ext = _url_base.rsplit(".", 1)[1].lower() if "." in _url_base else ""
        if not _ext and _http_ct:
            # No file extension in URL — infer type from HTTP Content-Type header
            _ct_base = _http_ct.lower().split(";")[0].strip()
            _ext = {
                "application/json": "json",
                "application/pdf": "pdf",
                "text/html": "html",
                "text/plain": "txt",
                "text/csv": "csv",
            }.get(_ct_base, "")
            if _ext:
                print(f"  Content-Type: {_ct_base} — treating as .{_ext}")
        if _ext != "json":
            os.unlink(tmp_path)
            if not _ext or _ext in ("html", "htm", "pdf", "txt"):
                # Web page or document — route to FreeText handler
                try:
                    with open(config_file) as _pf:
                        _pre_keys = set((yaml.safe_load(_pf) or {}).get("sources", {}).keys())
                except Exception:
                    _pre_keys = set()
                match_freetext(url, None, config_file)
                try:
                    with open(config_file) as _pf:
                        _post_keys = set((yaml.safe_load(_pf) or {}).get("sources", {}).keys())
                except Exception:
                    _post_keys = set()
                for _new_key in (_post_keys - _pre_keys):
                    _upsert_source_in_index(_new_key)
            else:
                print(f"  Skipping {url}", file=sys.stderr)
                print(f"  REASON: unrecognised file extension '{_ext}'"
                      " — expected .json, .yaml, or .yml", file=sys.stderr)
            continue

        try:
            with open(tmp_path) as f:
                record = json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  Skipping {url}: failed to parse as JSON: {e}", file=sys.stderr)
            os.unlink(tmp_path)
            continue

        resource_type = record.get("resourceType", "")
        if resource_type == "CodeSystem":
            content_type = "LOINCCodeSystem"
        elif resource_type == "ValueSet":
            content_type = "LOINCValueSet"
        else:
            print(f"  Skipping {url}: JSON resourceType '{resource_type}' not supported",
                  file=sys.stderr)
            os.unlink(tmp_path)
            continue

        raw_name = record.get("name", "")
        if raw_name:
            parts = re.split(r"[\s\-_]+", raw_name)
            key = "LOINC_" + (to_camel_case(raw_name) if len(parts) > 1 else raw_name)
        else:
            key = _url_base.rsplit(".", 1)[0]

        with open(config_file) as f:
            config = yaml.safe_load(f) or {}
        if key in config.get("sources", {}):
            print(f"  Skipping {url}: source key '{key}' already exists in {config_file}",
                  file=sys.stderr)
            os.unlink(tmp_path)
            continue

        output_path = f"sources/{key}.json"
        os.rename(tmp_path, output_path)
        print(f"Saved to {output_path}")

        entry = make_source_entry(key, url, content_type, "json")
        entry["see_also"] = url + ".html"
        config.setdefault("sources", {})[key] = entry
        write_config(config, config_file)
        print(f"Added source '{key}' to {config_file}")

        key, output_path = fill_loinc_source_metadata(output_path, key, config_file)
        process_sources([key], config_file)


def _require_source_file(key, ext, fallback_ext=None):
    """Return the source file path if it exists, else print a warning and return None.

    When fallback_ext is given, also accepts sources/{key}.{fallback_ext} if the
    primary extension is not found (used for sources that migrated from one format
    to another, e.g. STATSCAN html → zip).
    """
    path = f"sources/{key}.{ext}"
    if os.path.exists(path):
        return path
    if fallback_ext:
        fallback = f"sources/{key}.{fallback_ext}"
        if os.path.exists(fallback):
            return fallback
    print(f"Skipping {key}: sources/{key}.{ext} not found — run -f to fetch first", file=sys.stderr)
    return None



def process_sources(source_keys=None, config_file=MENU_CONFIG, debug=False):
    """Store per-source prefix dicts into harvester_config.yaml from fetched source files.

    For OntologyAPI sources: fetches the concept hierarchy via the configured API
    (e.g. AGROVOC SPARQL, OLS4) and writes a LinkML enum YAML to sources/{key}.yaml
    directly — no downloaded file required.
    For NSDB sources: runs process_nsdb_source to fetch and parse HTML attribute pages.
    For LOINC sources: parses the saved HTML listing page, fetches each ValueSet JSON
    by constructing the URL from the Name column, and writes the combined YAML file.
    For LOINCCodeSystem/LOINCValueSet sources: converts the fetched JSON to a LinkML
    YAML file and stores the resulting prefix dict in harvester_config.yaml.
    For yaml sources: reads the source file and stores its prefix dict in
    harvester_config.yaml.

    Does not modify schema.yaml — use -b (build_schema) to sync enums and
    prefixes into schema.yaml.

    source_keys: list of source key names to process, or None/empty for all sources.
    """
    with open(config_file, "r") as f:
        config = yaml.safe_load(f)

    locales = config.get("locales") or ["en"]
    all_sources = config.get("sources", {})
    keys_to_process = list(all_sources.keys()) if not source_keys else source_keys

    invalid = [k for k in keys_to_process if k not in all_sources]
    if invalid:
        print(f"Unknown source key(s): {', '.join(invalid)}", file=sys.stderr)
        sys.exit(1)

    _freetext_ran = 0   # FreeText sources explicitly processed
    _freetext_wrote = 0  # of those, how many wrote a YAML
    _non_freetext_ran = False  # whether any non-FreeText source was processed

    for key in keys_to_process:
        source = all_sources[key]
        file_format = source.get("file_format", "yaml")
        content_type = source.get("content_type", "")

        if content_type != "FreeText":
            _non_freetext_ran = True

        if content_type == "OWL":
            if not _require_source_file(key, source.get("file_format", "owl")): continue
            process_owl_source(key, source, config_file)
            continue

        if content_type == "STATSCAN":
            if not _require_source_file(key, "zip", fallback_ext="html"): continue
            process_statscan_source(key, source, config_file, locales=locales)
            continue

        if content_type == "STATSCAN_TABLE":
            if not _require_source_file(key, "zip", fallback_ext="html"): continue
            process_statscan_table_source(key, source, config_file, locales=locales)
            continue

        if content_type == "ISO_COUNTRY":
            _zip = f"sources/{key}.zip"
            _html = f"sources/{key}.html"
            if not os.path.exists(_zip) and not os.path.exists(_html):
                print(f"Skipping {key}: no archive found — run '-f {key}' to fetch first",
                      file=sys.stderr)
                continue
            process_iso_country_source(key, source, config_file, locales=locales)
            continue

        if content_type == "NAPCSCanada":
            if not _require_source_file(key, "csv"): continue
            process_napcscanada_source(key, source, config_file, locales=locales)
            continue

        if content_type == "AgriFoodCA":
            if not _require_source_file(key, "zip"): continue
            process_agrifood_dir_source(key, source, config_file, locales=locales)
            continue

        if content_type in ("NSDBSNT", "NSDBSLT"):
            _zip = f"sources/{key}.zip"
            _html = f"sources/{key}.html"
            if not os.path.exists(_zip) and not os.path.exists(_html):
                print(f"Skipping {key}: no archive found — run '-f {key}' to download first",
                      file=sys.stderr)
                continue
            process_nsdb_html_source(key, source, enum_prefix="NSDB", locales=locales)
            continue

        if content_type == "NSDB":
            _zip = f"sources/{key}.zip"
            _html = f"sources/{key}.html"
            if not os.path.exists(_zip) and not os.path.exists(_html):
                print(f"Skipping {key}: no archive found — run '-f {key}' to download first",
                      file=sys.stderr)
                continue
            process_nsdb_source(key, source, locales=locales)
            continue

        if content_type == "NSDBSLC":
            _zip = f"sources/{key}.zip"
            _html = f"sources/{key}.html"
            if not os.path.exists(_zip) and not os.path.exists(_html):
                print(f"Skipping {key}: no archive found — run '-f {key}' to download first",
                      file=sys.stderr)
                continue
            process_nsdb_html_source(key, source, enum_prefix="NSDBSLC", locales=locales)
            continue

        if content_type == "LOINC":
            if not _require_source_file(key, "html"): continue
            process_loinc_table_source(key, source, config_file)
            continue

        if content_type in ("LOINCCodeSystem", "LOINCValueSet"):
            if not _require_source_file(key, "json"): continue
            if content_type == "LOINCCodeSystem":
                yaml_path = convert_loinc_codesystem_to_linkml(key, source)
            else:
                yaml_path = convert_loinc_valueset_to_linkml(key, source)
            with open(yaml_path) as f:
                generated = yaml.safe_load(f)
            config_additions = dict(source.get("prefixes") or {})
            if config_additions:
                merged = sort_prefixes({**(generated.get("prefixes") or {}), **config_additions})
                if list(merged.items()) != list(sort_prefixes(generated.get("prefixes") or {}).items()):
                    generated["prefixes"] = merged
                    with open(yaml_path, "w") as f:
                        yaml.dump(generated, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
            continue

        if content_type == "OntologyAPI":
            process_skos_source(key, source, config_file, locales=locales)
            continue

        if content_type == "NRCSSoilFieldBook":
            process_nrcs_source(key, source, locales=locales)
            continue

        if content_type == "NASIS":
            process_nasis_source(key, source, locales=locales)
            continue

        if content_type == "CRediT":
            process_credit_source(key, source, locales=locales)
            continue

        if content_type == "LOC_CLASSIFICATION":
            process_loc_source(key, source, locales=locales)
            continue

        if content_type == "CANSIS_GLOSSARY":
            process_cansis_glossary_source(key, source, locales=locales)
            continue

        if content_type == "FreeText":
            if not source_keys:
                continue  # skip on -c with no args; only process when explicitly named
            _freetext_ran += 1
            if process_freetext_source(key, source, config_file, locales=locales, debug=debug):
                _freetext_wrote += 1
            continue

        process_linkml_source(key, source, config_file)

    # Skip index rebuild only when every explicitly-run source was FreeText and none wrote a YAML
    if _freetext_ran and _freetext_wrote == 0 and not _non_freetext_ran:
        pass
    else:
        _rebuild_fts_index(config_file)

    _report_missing_yamls(config_file)


# Content types whose yaml is generated via -c (API fetch), not -f (file download).
_REGEN_C_TYPES = {"OntologyAPI", "AGROVOC"}


def _report_missing_yamls(config_file):
    """Print all config sources that have no sources/{key}.yaml, with regen command."""
    try:
        with open(config_file) as f:
            all_sources = (yaml.safe_load(f) or {}).get("sources", {})
    except Exception:
        return
    missing = [
        (k, "-c" if v.get("content_type") in _REGEN_C_TYPES else "-f")
        for k, v in all_sources.items()
        if not os.path.exists(f"sources/{k}.yaml")
    ]
    if not missing:
        return
    print("\nMissing sources yaml — run to regenerate:")
    for key, flag in missing:
        print(f"  {flag} {key}")


def expand_reachable_from(yaml_path, enum_filter=None, apis=None, locales=None):
    """For each enum with reachable_from.source_nodes, fetch graph data via the
    appropriate API and populate permissible_values with CURIE keys, titles,
    and is_a hierarchy.

    source_nodes entries must be in CURIE format: PREFIX:ID (e.g. ENVO:00000428).
    enum_filter: optional set/list of enum keys to restrict processing to.
    apis: dict loaded from harvester_config.yaml 'apis' key; used by fetch_api_graph
          to route each ontology prefix to the correct API service.
    Writes the updated schema back to yaml_path if any enums were expanded.
    """
    with open(yaml_path, "r") as f:
        schema = yaml.safe_load(f)
    if not schema:
        return

    OBSOLETE_CLASS_IRI = "http://www.geneontology.org/formats/oboInOwl#ObsoleteClass"
    OBOINOWL_URI = "http://www.geneontology.org/formats/oboInOwl#"
    OBOINOWL_KEY = "oboInOwl"

    prefixes = schema.get("prefixes") or {}
    enums = schema.get("enums") or {}
    changed = False
    prefix_added = False
    expanded = {}   # enum_key -> permissible_value count

    for enum_key, enum_def in enums.items():
        if enum_filter is not None and enum_key not in enum_filter:
            continue
        if not enum_def:
            continue
        reachable_from = enum_def.get("reachable_from") or {}
        source_nodes = reachable_from.get("source_nodes")
        if not source_nodes:
            continue

        print(f"  Expanding '{enum_key}' ({len(source_nodes)} source node(s))")

        # Collect all nodes and edges from every source_node graph fetch
        all_nodes = {}   # iri -> {"label": str, "curie": str}
        all_edges = []   # [{"child_curie": str, "parent_curie": str}]

        for node_ref in source_nodes:
            if ":" not in node_ref:
                print(f"    Warning: source_node '{node_ref}' is not PREFIX:ID format — skipping", file=sys.stderr)
                continue
            ontology, term_id = node_ref.split(":", 1)
            graph = fetch_api_graph(ontology, term_id, apis=apis, locales=locales)
            if not graph:
                continue

            # Determine which IRIs to skip for this source_node:
            # - the root node itself (unless include_self is true)
            # - any ancestor nodes, i.e. targets of (root -subClassOf-> target)
            inner_iri = get_ols4_inner_iri(ontology, term_id, apis=apis)
            include_self = reachable_from.get("include_self", False)
            skip_iris = set()
            if not include_self:
                skip_iris.add(inner_iri)
            graph_node_iris = {n.get("iri") for n in (graph.get("nodes") or []) if n.get("iri")}
            for edge in (graph.get("edges") or []):
                if (edge.get("label") == "subClassOf"
                        and edge.get("source") == inner_iri
                        and edge.get("target") in graph_node_iris):
                    skip_iris.add(edge.get("target"))

            # Pass 1: build node map with current prefixes, skipping root and ancestors
            for node in (graph.get("nodes") or []):
                iri = node.get("iri") or ""
                if iri and iri not in skip_iris:
                    # OLS4 /graph nodes only carry iri+label; definition is unavailable without
                    # per-term calls.  BioPortal nodes carry 'definition' (list or str).
                    # AGROVOC nodes carry 'definition' and 'deprecated'.
                    raw_def = node.get("definition") or ""
                    definition = (raw_def[0] if isinstance(raw_def, list) else raw_def) or ""
                    all_nodes[iri] = {
                        "label": node.get("label") or "",
                        "curie": iri_to_curie(iri, prefixes),
                        "definition": definition,
                        "deprecated": bool(node.get("deprecated")),
                    }

            # Pass 2: scan edges for obsolete class references before building all_edges
            graph_edges = graph.get("edges") or []
            for edge in graph_edges:
                if edge.get("label") != "subClassOf":
                    continue
                tgt_iri = edge.get("target") or ""
                src_iri = edge.get("source") or ""
                if tgt_iri != OBSOLETE_CLASS_IRI:
                    continue
                # Ensure oboInOwl prefix is registered
                if OBOINOWL_KEY not in prefixes:
                    prefixes[OBOINOWL_KEY] = OBOINOWL_URI
                    schema["prefixes"] = prefixes
                    prefix_added = True
                    # Re-compute CURIEs for any oboInOwl IRIs already collected
                    for iri in list(all_nodes):
                        if iri.startswith(OBOINOWL_URI):
                            all_nodes[iri]["curie"] = iri_to_curie(iri, prefixes)
                # Ensure the ObsoleteClass node itself is in all_nodes
                if OBSOLETE_CLASS_IRI not in all_nodes:
                    all_nodes[OBSOLETE_CLASS_IRI] = {
                        "label": "ObsoleteClass",
                        "curie": f"{OBOINOWL_KEY}:ObsoleteClass",
                    }
                if src_iri in all_nodes:
                    print(
                        f"    Warning: term '{all_nodes[src_iri]['curie']}' is obsolete"
                        f" (subClassOf oboInOwl:ObsoleteClass)",
                        file=sys.stderr,
                    )

            # Pass 3: build all_edges (oboInOwl prefix now in place if needed)
            for edge in graph_edges:
                if edge.get("label") == "subClassOf":
                    src_iri = edge.get("source") or ""
                    tgt_iri = edge.get("target") or ""
                    if src_iri in all_nodes and tgt_iri in all_nodes:
                        all_edges.append({
                            "child_curie": all_nodes[src_iri]["curie"],
                            "parent_curie": all_nodes[tgt_iri]["curie"],
                        })

        if not all_nodes:
            continue

        # Build child→parent lookup (last edge wins for any duplicate child)
        is_a_map = {e["child_curie"]: e["parent_curie"] for e in all_edges}

        # Build permissible_values dict
        permissible_values = {}
        for iri, info in all_nodes.items():
            curie = info["curie"]
            pv = {}
            if info["label"]:
                pv["title"] = info["label"]
                pv["text"] = curie
            pv["meaning"] = curie
            if info.get("definition"):
                pv["description"] = info["definition"]
            if curie in is_a_map:
                pv["is_a"] = is_a_map[curie]
            if info.get("deprecated"):
                pv["status"] = "DEPRECATED"
            permissible_values[curie] = pv

        enum_def["permissible_values"] = permissible_values
        expanded[enum_key] = len(permissible_values)
        changed = True

    if changed or prefix_added:
        with open(yaml_path, "w") as f:
            yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
        print(f"Updated {yaml_path} with expanded reachable_from values")

    # If oboInOwl prefix was added and we were working on a source file, also update schema.yaml
    if prefix_added and os.path.abspath(yaml_path) != os.path.abspath("schema.yaml"):
        schema_file = "schema.yaml"
        if os.path.exists(schema_file):
            with open(schema_file, "r") as f:
                schema_data = yaml.safe_load(f) or {}
            existing_prefixes = schema_data.get("prefixes") or {}
            if OBOINOWL_KEY not in existing_prefixes:
                existing_prefixes[OBOINOWL_KEY] = OBOINOWL_URI
                schema_data["prefixes"] = existing_prefixes
                with open(schema_file, "w") as f:
                    yaml.dump(schema_data, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
                print(f"Added '{OBOINOWL_KEY}' prefix to schema.yaml")

    return expanded


# ---------------------------------------------------------------------------
# --search: term search across sources/*.yaml
# ---------------------------------------------------------------------------

_SEARCH_INDEX_DB = "sources/search_index.db"

_STOPWORDS = frozenset({
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'for', 'of', 'in', 'on', 'at', 'to', 'with',
    'by', 'from', 'that', 'this', 'these', 'those', 'and', 'or', 'but',
    'not', 'if', 'as', 'it', 'its', 'any', 'all', 'each', 'some', 'such',
    'used', 'use', 'using', 'also', 'other', 'than', 'which', 'when',
    'where', 'who', 'how', 'what', 'their', 'they', 'them', 'there',
    'no', 'so', 'up', 'out', 'about', 'into', 'than', 'more', 'can',
})


def _tokenize(text):
    """Return a frozenset of lowercase word tokens, filtering stopwords."""
    return frozenset(
        w for w in re.findall(r'[a-zA-Z]+', text.lower())
        if w not in _STOPWORDS and len(w) > 1
    )


def _overlap_score(query_tokens, target_tokens):
    """Fraction of query tokens present in target tokens (recall-weighted)."""
    if not query_tokens or not target_tokens:
        return 0.0
    return len(query_tokens & target_tokens) / len(query_tokens)


def _is_verbatim(query, source_label, source_def):
    """True if the query text, or either half of a term:description query, appears
    literally as a substring in the source label or definition (case-insensitive)."""
    term = (query.get("term") or "").strip()
    desc = (query.get("description") or "").strip()
    haystack = ((source_label or "") + " " + (source_def or "")).lower()
    candidates = [s for s in [term, desc, f"{term}: {desc}" if desc else ""] if len(s) > 2]
    return any(s.lower() in haystack for s in candidates)


def _match_score(query_tokens, query_text, label_text, def_text):
    """Score a match against label and definition fields separately.

    1.0 is returned only when the exact query phrase appears as a substring in
    the label or definition.  Token-overlap matches (where query words appear
    scattered rather than as a contiguous phrase) are capped at 0.9 for label
    matches and 0.5 for definition-only matches.
    """
    qt = query_text.lower()
    if qt in (label_text or "").lower():
        return 1.0
    if def_text and qt in def_text.lower():
        return 1.0
    label_score = _overlap_score(query_tokens, _tokenize(label_text))
    def_score = _overlap_score(query_tokens, _tokenize(def_text))
    combined = max(label_score * 0.9, def_score * 0.5)
    return combined


def _parse_search_queries(text):
    """Parse --search input into a list of {term, description} dicts.

    Supports three input forms (auto-detected):
      - Free text:           "soil with poor drainage"
      - Structured pair:     "SoilDrainage:classification of how well soil drains"
      - Batch (either form): entries separated by ';' or newlines

    Returns a list of dicts with keys 'term' (str) and 'description' (str|None).
    """
    raw_entries = [e.strip() for e in re.split(r'[;\n]+', text) if e.strip()]
    queries = []
    for entry in raw_entries:
        # Detect "name:description" — colon not part of a URL (no preceding /)
        m = re.match(r'^([^:/]{1,80}):\s*(.+)$', entry, re.DOTALL)
        if m:
            queries.append({"term": m.group(1).strip(), "description": m.group(2).strip()})
        else:
            queries.append({"term": entry, "description": None})
    return queries


def _search_sources(queries, config_file=MENU_CONFIG, top_n=10):
    """Search all sources/*.yaml for terms matching the given queries.

    For each query, scores every enum and permissible_value by token overlap
    between the query text and the target's name/title/description fields.
    Returns a list of {query, matches} dicts; matches are sorted by score desc.

    Each match dict contains:
      source, id, label, type ('enum'|'permissible_value'), parent (label),
      definition, def_source ('verbatim'|''), score.
    """
    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    all_sources = config.get("sources", {})

    source_yamls = {}
    for key in all_sources:
        path = f"sources/{key}.yaml"
        if os.path.exists(path):
            with open(path) as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                source_yamls[key] = data

    # Build enum title map for parent-label lookup
    enum_title_map = {}   # (source_key, enum_name) -> title
    for source_key, data in source_yamls.items():
        for enum_name, enum_def in (data.get("enums") or {}).items():
            title = (enum_def or {}).get("title", "") or enum_name
            enum_title_map[(source_key, enum_name)] = title

    results = []
    for query in queries:
        query_text = query["term"]
        q_tokens = _tokenize(query_text)

        matches = []
        for source_key, data in source_yamls.items():
            for enum_name, enum_def in (data.get("enums") or {}).items():
                enum_def = enum_def or {}
                enum_title = enum_def.get("title", "") or enum_name

                # Score enum itself
                enum_label_text = f"{enum_name} {enum_title}"
                enum_desc = enum_def.get("description", "") or ""
                score = _match_score(q_tokens, query_text, enum_label_text, enum_desc)
                if score > 0:
                    matches.append({
                        "source":     source_key,
                        "id":         enum_name,
                        "term_uri":   "",
                        "label":      enum_title,
                        "type":       "enum",
                        "parent":     None,
                        "parent_uri": "",
                        "definition": enum_desc,
                        "def_source": "verbatim" if _is_verbatim(query, enum_title, enum_desc) else "",
                        "score":      score,
                        "children":   _get_local_children(data, enum_name, "enum"),
                    })

                # Score each permissible value
                for pv_code, pv_def in (enum_def.get("permissible_values") or {}).items():
                    pv_def = pv_def or {}
                    pv_label = pv_def.get("title", "") or str(pv_code)
                    pv_desc = pv_def.get("description", "") or ""
                    pv_score = _match_score(q_tokens, query_text,
                                            f"{pv_code} {pv_label}", pv_desc)
                    if pv_score > 0:
                        matches.append({
                            "source":     source_key,
                            "id":         pv_code,
                            "term_uri":   pv_def.get("meaning") or "",
                            "label":      pv_label,
                            "type":       "term",
                            "parent":     enum_title_map.get((source_key, enum_name)) or enum_name,
                            "parent_uri": "",
                            "definition": pv_desc,
                            "def_source": "verbatim" if _is_verbatim(query, pv_label, pv_desc) else "",
                            "score":      pv_score,
                            "children":   _get_local_children(data, pv_code, "term"),
                        })

        # Sort: score desc, enums before terms at equal score, then alpha by id
        matches.sort(key=lambda m: (-m["score"], m["type"] != "enum", str(m["id"]).lower()))
        results.append({"query": query, "matches": matches[:top_n]})

    return results


def _rebuild_fts_index(config_file=MENU_CONFIG):
    """Rebuild the FTS5 full-text search index from all sources/*.yaml files.

    Uses SQLite FTS5 with the built-in Porter stemmer so that morphological
    variants like 'excess' / 'excessive' / 'excessively' and 'drain' /
    'drainage' / 'drained' all resolve to the same stem and match each other.
    Called automatically at the end of process_sources(-c).
    Falls back silently if this SQLite build lacks FTS5 support.
    """
    try:
        conn = sqlite3.connect(_SEARCH_INDEX_DB)
        conn.execute("DROP TABLE IF EXISTS terms")
        conn.execute(
            "CREATE VIRTUAL TABLE terms USING fts5("
            "source_key UNINDEXED, id UNINDEXED, type UNINDEXED, "
            "parent UNINDEXED, term_uri UNINDEXED, label, definition, "
            "tokenize='porter ascii')"
        )
    except Exception as e:
        print(f"  Warning: FTS5 index unavailable ({e}) — token overlap used for --search.",
              file=sys.stderr)
        return

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}

    total = 0
    for key in config.get("sources", {}):
        path = f"sources/{key}.yaml"
        if not os.path.exists(path):
            continue
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            continue

        rows = []
        for enum_name, enum_def in (data.get("enums") or {}).items():
            enum_def = enum_def or {}
            enum_title = enum_def.get("title") or enum_name
            enum_desc = enum_def.get("description") or ""
            rows.append((key, enum_name, "enum", "", "", enum_title, enum_desc))

            for pv_code, pv_def in (enum_def.get("permissible_values") or {}).items():
                pv_def = pv_def or {}
                pv_label = pv_def.get("title") or str(pv_code)
                pv_desc = pv_def.get("description") or ""
                pv_uri = pv_def.get("meaning") or ""
                rows.append((key, str(pv_code), "term", enum_title, pv_uri, pv_label, pv_desc))

        conn.executemany(
            "INSERT INTO terms(source_key,id,type,parent,term_uri,label,definition) "
            "VALUES(?,?,?,?,?,?,?)",
            rows,
        )
        total += len(rows)

    # Also index the StatsCan classification catalog for discovery
    _STATSCAN_CATALOG = "sources/sources_statscan_terms.yaml"
    if os.path.exists(_STATSCAN_CATALOG):
        try:
            with open(_STATSCAN_CATALOG) as f:
                cat_data = yaml.safe_load(f) or {}
            cat_rows = []
            for entry in cat_data.get("classifications", []):
                cat_rows.append((
                    "__statscan_catalog__",
                    entry.get("key", ""),
                    "catalog",
                    entry.get("subject", ""),
                    entry.get("url", ""),
                    entry.get("title", ""),
                    entry.get("entry_type", ""),
                ))
            conn.executemany(
                "INSERT INTO terms(source_key,id,type,parent,term_uri,label,definition) "
                "VALUES(?,?,?,?,?,?,?)",
                cat_rows,
            )
            total += len(cat_rows)
        except Exception as e:
            print(f"  Warning: could not index StatsCan catalog: {e}", file=sys.stderr)

    conn.commit()
    conn.close()
    print(f"  Search index: {total} terms indexed in {_SEARCH_INDEX_DB}")


def _upsert_source_in_index(key):
    """Add or replace rows for one source in the FTS5 index.

    Creates the DB/table if they don't exist yet (first call after -a before
    any -c has been run).  Silently no-ops if FTS5 is unavailable.
    """
    yaml_path = f"sources/{key}.yaml"
    if not os.path.exists(yaml_path):
        return
    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return
    except Exception:
        return

    try:
        conn = sqlite3.connect(_SEARCH_INDEX_DB)
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS terms USING fts5("
            "source_key UNINDEXED, id UNINDEXED, type UNINDEXED, "
            "parent UNINDEXED, term_uri UNINDEXED, label, definition, "
            "tokenize='porter ascii')"
        )
        conn.execute("DELETE FROM terms WHERE source_key=?", (key,))
    except Exception as e:
        print(f"  Warning: FTS5 index unavailable ({e}) — --search may miss new terms.",
              file=sys.stderr)
        return

    rows = []
    for enum_name, enum_def in (data.get("enums") or {}).items():
        enum_def = enum_def or {}
        enum_title = enum_def.get("title") or enum_name
        enum_desc = enum_def.get("description") or ""
        rows.append((key, enum_name, "enum", "", "", enum_title, enum_desc))
        for pv_code, pv_def in (enum_def.get("permissible_values") or {}).items():
            pv_def = pv_def or {}
            pv_label = pv_def.get("title") or str(pv_code)
            pv_desc = pv_def.get("description") or ""
            pv_uri = pv_def.get("meaning") or ""
            rows.append((key, str(pv_code), "term", enum_title, pv_uri, pv_label, pv_desc))

    conn.executemany(
        "INSERT INTO terms(source_key,id,type,parent,term_uri,label,definition) "
        "VALUES(?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    print(f"  Search index: {len(rows)} terms added for '{key}'")


def _fts5_search_local(queries, config_file=MENU_CONFIG, top_n=10):
    """Search local source YAML files using SQLite FTS5 with Porter stemming.

    Porter stemming lets 'excess drainage' match 'Excessively Drained' because
    'excess*' and 'drain*' share the same stems.  Falls back to token-overlap
    search (_search_sources) if the FTS5 index does not exist yet (run -c first).

    Scoring combines FTS5 BM25 (label weighted 5×, definition 1×) with the
    existing phrase/token-overlap scorer; the higher of the two is used.
    FTS5-only matches (morphological variants) are capped at 0.85 so they rank
    below exact phrase matches (1.0).
    """
    if not os.path.exists(_SEARCH_INDEX_DB):
        return _search_sources(queries, config_file=config_file, top_n=top_n)

    try:
        conn = sqlite3.connect(_SEARCH_INDEX_DB)
        conn.execute("SELECT count(*) FROM terms").fetchone()
    except Exception:
        return _search_sources(queries, config_file=config_file, top_n=top_n)

    # Lazily loaded source YAMLs, keyed by source_key, for children lookup.
    loaded_yamls: dict = {}

    def _yaml_for(source_key):
        if source_key not in loaded_yamls:
            path = f"sources/{source_key}.yaml"
            try:
                with open(path) as _f:
                    loaded_yamls[source_key] = yaml.safe_load(_f) or {}
            except Exception:
                loaded_yamls[source_key] = {}
        return loaded_yamls[source_key]

    results = []
    for query in queries:
        # Use only the term (not description) for retrieval and scoring.
        # The description is context for the user / AI re-scorer; including it
        # in FTS5 queries and token overlap would require those words to appear
        # in source text, eliminating valid matches.
        term_text = query["term"]
        q_tokens = _tokenize(term_text)

        fts_words = [
            w.lower() for w in re.findall(r'[a-zA-Z0-9]+', term_text)
            if w.lower() not in _STOPWORDS and len(w) > 1
        ]
        if not fts_words:
            results.append({"query": query, "matches": []})
            continue

        fts_query = " ".join(fts_words)
        try:
            rows = conn.execute(
                "SELECT source_key, id, type, parent, term_uri, label, definition, "
                "bm25(terms, 5.0, 1.0) as rank "
                "FROM terms WHERE terms MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, top_n * 3),
            ).fetchall()
        except sqlite3.OperationalError as e:
            print(f"  Warning: FTS5 query error for '{term_text[:40]}': {e}", file=sys.stderr)
            rows = []

        if not rows:
            results.append({"query": query, "matches": []})
            continue

        best_rank = min(r[7] for r in rows)

        matches = []
        for row in rows:
            source_key, id_, type_, parent, term_uri, label, definition, rank = row
            token_score = _match_score(q_tokens, term_text,
                                       f"{id_} {label}", definition)
            fts5_score = (rank / best_rank) * 0.85 if best_rank < 0 else 0.0
            score = max(token_score, fts5_score)
            if score <= 0:
                continue
            matches.append({
                "source":     source_key,
                "id":         id_,
                "term_uri":   term_uri or "",
                "label":      label or "",
                "type":       type_,
                "parent":     parent or None,
                "parent_uri": "",
                "definition": definition or "",
                "def_source": "verbatim" if _is_verbatim(query, label, definition) else "",
                "score":      round(score, 4),
                "children":   _get_local_children(_yaml_for(source_key), id_, type_),
            })

        matches.sort(key=lambda m: (-m["score"], m["type"] != "enum", str(m["id"]).lower()))
        results.append({"query": query, "matches": matches[:top_n]})

    conn.close()
    return results


def _dedup_matches_by_id(matches):
    """Merge rows that share the same term id, combining their source names.

    When the same CURIE (e.g. ENVO:06105241) appears in both OLS4 and BioPortal
    results, collapse to a single row with sources joined as 'src1, src2'.
    The highest score, longest definition, and any verbatim flag are kept.
    """
    seen = {}   # id -> index in deduped
    deduped = []
    for m in matches:
        mid = str(m["id"])
        if mid in seen:
            ex = deduped[seen[mid]]
            if m["source"] not in ex["source"].split(", "):
                ex["source"] = ex["source"] + ", " + m["source"]
            if m["score"] > ex["score"]:
                ex["score"] = m["score"]
            if m.get("def_source") == "verbatim":
                ex["def_source"] = "verbatim"
            if not ex.get("definition") and m.get("definition"):
                ex["definition"] = m["definition"]
        else:
            seen[mid] = len(deduped)
            deduped.append(dict(m))
    return deduped


def _ai_expand_queries(queries, model="claude-haiku-4-5-20251001"):
    """Use Claude to generate close synonyms for each query term.

    Returns a list of (orig_idx, sub_query) pairs — one per original query plus
    one per generated synonym.  The orig_idx links each sub-query back to its
    parent so results can be pooled.  Synonyms share the original description
    (if any) so that API searches receive the same semantic context.

    Requires the ``anthropic`` package and ``ANTHROPIC_API_KEY``.  Falls back
    to the original queries unchanged if either is missing.
    """
    try:
        import anthropic as _anthropic
    except ImportError:
        return [(i, q) for i, q in enumerate(queries)]

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return [(i, q) for i, q in enumerate(queries)]

    client = _anthropic.Anthropic(api_key=api_key)
    pairs = []

    for i, query in enumerate(queries):
        pairs.append((i, query))
        term = query["term"]
        desc = query.get("description") or ""
        context = f" — {desc}" if desc else ""

        prompt = (
            f'For the controlled-vocabulary search term "{term}"{context}, '
            f'list 3 to 5 close synonyms or alternative phrasings as they would '
            f'appear in scientific ontologies (e.g. for "flash freezing" you might '
            f'suggest "quick freezing", "cryogenic freezing", "rapid freeze"). '
            f'Include domain-specific alternatives and common variant phrasings. '
            f'Return ONLY a compact JSON array of strings, no explanation:\n'
            f'["synonym1", "synonym2", ...]'
        )
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=120,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
            m = re.search(r'\[.*?\]', raw, re.DOTALL)
            if not m:
                continue
            synonyms = [
                s.strip() for s in json.loads(m.group())
                if isinstance(s, str) and s.strip()
                and s.strip().lower() != term.lower()
            ][:5]
            if synonyms:
                print(f"  Expanding \"{term}\" → {', '.join(synonyms)}", file=sys.stderr)
                for syn in synonyms:
                    pairs.append((i, {"term": syn, "description": desc or None}))
        except Exception as e:
            print(f"  Warning: query expansion failed for '{term}': {e}", file=sys.stderr)

    return pairs


def _ai_rescore(results, model="claude-haiku-4-5-20251001"):
    """Re-score search results using Claude for semantic relevance.

    Sends one API call per query with the full candidate list and asks Claude
    to assign a 0.00-1.00 relevance score for each term.  Requires the
    ``anthropic`` package and ``ANTHROPIC_API_KEY`` in the environment.
    Returns the same results structure with scores (and score_source='ai') updated.
    """
    try:
        import anthropic as _anthropic
    except ImportError:
        print("  Warning: 'anthropic' package not installed — skipping AI re-scoring."
              "  Install with: pip install anthropic", file=sys.stderr)
        return results

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  Warning: ANTHROPIC_API_KEY not set — skipping AI re-scoring.", file=sys.stderr)
        return results

    client = _anthropic.Anthropic(api_key=api_key)

    for result in results:
        matches = result["matches"]
        if not matches:
            continue
        q = result["query"]
        query_text = q["term"] + (f": {q['description']}" if q.get("description") else "")

        term_lines = []
        for i, m in enumerate(matches):
            label = m.get("label") or str(m["id"])
            defn  = (m.get("definition") or "")[:200]
            entry = f'{i}. "{label}"'
            if defn:
                entry += f' — {defn}'
            term_lines.append(entry)

        prompt = (
            f'Rate each term\'s semantic relevance to the search query: "{query_text}"\n\n'
            f'Score 0.00–1.00 where:\n'
            f'  1.00 = the term is precisely what was searched for\n'
            f'  0.80–0.99 = highly relevant (e.g. a domain synonym or directly related concept)\n'
            f'  0.50–0.79 = moderately relevant (shares meaningful subject matter)\n'
            f'  0.30–0.49 = tangential (peripheral connection, not really about the query)\n'
            f'  0.00–0.29 = irrelevant (coincidentally matches a word but is clearly about '
            f'something else — these will be excluded from results)\n\n'
            f'Terms:\n' + '\n'.join(term_lines) + '\n\n'
            f'Respond with ONLY a JSON array of objects, one per term in order:\n'
            f'[{{"index": 0, "score": 0.95}}, {{"index": 1, "score": 0.72}}, ...]\n'
            f'No explanation, no markdown fences — raw JSON only.'
        )

        try:
            response = client.messages.create(
                model=model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            json_match = re.search(r'\[.*\]', raw, re.DOTALL)
            if not json_match:
                print(f"  Warning: AI re-scoring returned unexpected response for '{query_text[:40]}'",
                      file=sys.stderr)
                continue
            scored = json.loads(json_match.group())
            for item in scored:
                idx   = item.get("index")
                score = item.get("score")
                if idx is not None and score is not None and 0 <= idx < len(matches):
                    matches[idx]["score"] = round(float(score), 2)
                    matches[idx]["score_source"] = "ai"
        except Exception as e:
            print(f"  Warning: AI re-scoring failed for '{query_text[:40]}': {e}", file=sys.stderr)

    return results


def _format_search_report(results, fmt="text", tsv=False):
    """Format search results.  fmt is 'text' (default), 'tsv', or 'markdown'.
    The legacy tsv=True kwarg is equivalent to fmt='tsv'.

    Column order: source, type, score, parent, id, label, definition.
    """
    if tsv and fmt == "text":
        fmt = "tsv"

    _DEF_MAX = 100

    def _trunc(s):
        return (s[:_DEF_MAX] + "…") if len(s) > _DEF_MAX else s

    def _query_str(q):
        return q["term"] + (f": {q['description']}" if q.get("description") else "")

    def _score_str(m):
        """Format score, appending '*' when assigned by AI re-scoring."""
        s = f"{m['score']:.2f}"
        return s + "*" if m.get("score_source") == "ai" else s

    def _type_with_count(m):
        n = len(m.get("children") or [])
        return m["type"] + (f" ({n})" if n else "")

    # ------------------------------------------------------------------ TSV --
    if fmt == "tsv":
        lines = ["\t".join(
            ["query", "source", "type", "score", "score_source", "parent", "parent_uri",
             "id", "label", "definition", "def_source", "children"]
        )]
        for result in results:
            qs = _query_str(result["query"])
            if not result["matches"]:
                lines.append("\t".join([qs, "(no matches)", *[""] * 10]))
                continue
            for m in result["matches"]:
                n_ch = len(m.get("children") or [])
                lines.append("\t".join([
                    qs,
                    m["source"],
                    _type_with_count(m),
                    f"{m['score']:.2f}",
                    m.get("score_source") or "token",
                    m.get("parent") or "",
                    m.get("parent_uri") or "",
                    str(m["id"]),
                    m.get("label") or "",
                    m.get("definition") or "",
                    m.get("def_source") or "",
                    str(n_ch) if n_ch else "",
                ]))
        return "\n".join(lines)

    # ------------------------------------------------------------ Markdown --
    if fmt == "markdown":
        def _cell(s):
            return str(s).replace("|", "\\|").replace("\n", " ")

        def _parent_md(m):
            parent = m.get("parent") or ""
            uri    = m.get("parent_uri") or ""
            if not parent:
                return "—"
            if uri:
                # "ID: label" → use everything after the first ": " as link text
                plabel = parent.split(": ", 1)[1] if ": " in parent else parent
                return f"[{_cell(plabel)}]({uri})"
            return _cell(parent)

        def _children_md(m):
            kids = m.get("children") or []
            if not kids:
                return ""
            n = len(kids)
            _MAX_SHOWN = 20
            shown = kids[:_MAX_SHOWN]
            items = " · ".join(
                f"[{_cell(c.get('label') or c['id'])}]({c['uri']})"
                if c.get("uri", "").startswith(("http://", "https://", "urn:"))
                else _cell(c.get("label") or c["id"])
                for c in shown
            )
            if n > _MAX_SHOWN:
                items += " · …"
            return f"<details><summary>{n} children</summary>{items}</details>"

        def _type_md(m):
            """Type label with optional collapsible children appended."""
            return m["type"] + _children_md(m)

        def _bold_query_words(text, q_tokens):
            """Wrap each query-token occurrence in **bold** (whole-word, case-insensitive)."""
            if not text or not q_tokens:
                return text
            spans = []
            for token in q_tokens:
                for hit in re.finditer(r'\b' + re.escape(token) + r'\b', text, re.IGNORECASE):
                    spans.append([hit.start(), hit.end()])
            if not spans:
                return text
            spans.sort()
            merged = []
            for start, end in spans:
                if merged and start < merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            out, prev = [], 0
            for start, end in merged:
                out.append(text[prev:start])
                out.append(f"**{text[start:end]}**")
                prev = end
            out.append(text[prev:])
            return "".join(out)

        # Pad narrow column headers to enforce minimum visual width in renderers.
        _NB = "&nbsp;"
        type_header = "Type" + _NB * 26   # ~30 chars
        def_header  = "Definition" + _NB * 45   # ~55 chars

        lines = [
            "<style>",
            "body, .markdown-body {",
            "    max-width: 100% !important;",
            "    padding: 20px !important;",
            "}",
            "</style>",
        ]
        for result in results:
            qs = _query_str(result["query"])
            lines.append(f"\n## Search: {qs}\n")
            if not result["matches"]:
                lines.append("*No matches found.*\n")
                continue
            lines.append(f"| Source | {type_header} | Score | Parent | ID | Label | {def_header} |")
            lines.append("|--------|------|------:|--------|-----|-------|------------|")
            q_tokens_bold = _tokenize(result["query"]["term"])
            for m in result["matches"]:
                defn = m.get("definition") or ""
                defn_md = _cell(_bold_query_words(_trunc(defn), q_tokens_bold)) if defn else ""
                tid = str(m["id"])
                turi = m.get("term_uri") or ""
                id_md = f"[{_cell(tid)}]({turi})" if turi else _cell(tid)
                lines.append(
                    f"| {_cell(m['source'])} | {_type_md(m)} | {_score_str(m)}"
                    f" | {_parent_md(m)} | {id_md}"
                    f" | {_cell(m.get('label') or '')} | {defn_md} |"
                )
        return "\n".join(lines)

    # --------------------------------------------------------- Space-padded --
    lines = []
    col_source = 24
    col_type   = 24
    col_score  =  6
    col_parent = 28
    col_id     = 24
    col_label  = 28

    header_row = (
        f"  {'Source':<{col_source}}  {'Type':<{col_type}}  {'Score':>{col_score}}"
        f"  {'Parent':<{col_parent}}  {'ID':<{col_id}}  {'Label':<{col_label}}"
        f"  Definition"
    )
    rule = "  " + "-" * (col_source + col_type + col_score + col_parent +
                          col_id + col_label + 24)

    for result in results:
        qs = _query_str(result["query"])
        lines.append(f"\nSearch: {qs}")
        if not result["matches"]:
            lines.append("  (no matches found)")
            continue
        lines.append(header_row)
        lines.append(rule)
        for m in result["matches"]:
            defn_str = ""
            if m.get("definition"):
                defn_str = f'"{_trunc(m["definition"])}"'
                if m.get("def_source"):
                    defn_str += f" [{m['def_source']}]"
            parent = m.get("parent") or "—"
            label  = m.get("label") or ""
            type_str = _type_with_count(m)
            lines.append(
                f"  {m['source']:<{col_source}}  {type_str:<{col_type}}  {_score_str(m):>{col_score}}"
                f"  {parent:<{col_parent}}  {str(m['id']):<{col_id}}  {label:<{col_label}}"
                f"  {defn_str}"
            )

    return "\n".join(lines)


_OLS4_DEFAULT_SEARCH_URI = "https://www.ebi.ac.uk/ols4/api"


def _fetch_ols4_children(api_base, ontology, iri, max_n=10):
    """Return list of {id, label, uri} dicts for direct hierarchical children, or []."""
    double_enc = urllib.parse.quote(urllib.parse.quote(iri, safe=""), safe="")
    url = f"{api_base}/ontologies/{ontology}/terms/{double_enc}/hierarchicalChildren?size={max_n}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        terms = (data.get("_embedded") or {}).get("terms") or []
        result = []
        for t in terms[:max_n]:
            cid = t.get("obo_id") or t.get("short_form") or ""
            clabel = t.get("label") or ""
            ciri = t.get("iri") or ""
            if not ciri and cid and ":" in cid:
                prefix, local = cid.split(":", 1)
                ciri = f"http://purl.obolibrary.org/obo/{prefix}_{local}"
            result.append({"id": cid, "label": clabel, "uri": ciri})
        return result
    except Exception:
        return []


def _fetch_bioportal_children(base_uri, apikey, ontology, iri, max_n=10):
    """Return list of {id, label, uri} dicts for direct children, or []."""
    encoded = urllib.parse.quote(iri, safe="")
    url = (f"{base_uri}/ontologies/{ontology}/classes/{encoded}/children"
           f"?apikey={apikey}&include=prefLabel&pagesize={max_n}")
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("collection") or []
        result = []
        for item in items[:max_n]:
            ciri = item.get("@id") or ""
            cid = ciri.rsplit("/", 1)[-1].replace("_", ":") if ciri else ""
            clabel = item.get("prefLabel") or ""
            result.append({"id": cid, "label": clabel, "uri": ciri})
        return result
    except Exception:
        return []


def _curie_to_uri(value, prefixes):
    """Return a full http(s) URI for *value*, or '' if it cannot be resolved.

    Accepts full URIs (returned as-is) and CURIEs like 'wd:Q1234' resolved
    via *prefixes* (a dict mapping prefix → base URI).
    """
    if not value:
        return ""
    s = str(value)
    if s.startswith(("http://", "https://", "urn:")):
        return s
    if ":" in s:
        prefix, local = s.split(":", 1)
        base = (prefixes or {}).get(prefix)
        if base:
            return base + local
    return ""


def _get_local_children(source_yaml, id_, type_):
    """Return list of {id, label, uri} children from an already-loaded source YAML dict.

    For enums: returns the permissible values.
    For terms: returns sibling PVs whose is_a equals this term's id.
    CURIE meanings (e.g. wd:Q1234) are resolved to full URIs via the source's
    prefix map so that the markdown report can hyperlink them correctly.
    """
    prefixes = source_yaml.get("prefixes") or {}

    def _child(code, pv_def):
        pv_def = pv_def or {}
        return {
            "id": str(code),
            "label": pv_def.get("title") or str(code),
            "uri": _curie_to_uri(pv_def.get("meaning") or "", prefixes),
        }

    if type_ == "enum":
        enum_def = (source_yaml.get("enums") or {}).get(id_) or {}
        pvs = enum_def.get("permissible_values") or {}
        return [_child(code, pv_def) for code, pv_def in pvs.items()]

    if type_ == "term":
        for enum_def in (source_yaml.get("enums") or {}).values():
            pvs = (enum_def or {}).get("permissible_values") or {}
            if id_ in pvs:
                return [
                    _child(code, pv_def) for code, pv_def in pvs.items()
                    if (pv_def or {}).get("is_a") == id_
                ]
    return []


def _fetch_ols4_parent(api_base, ontology, iri):
    """Return (display_str, parent_uri) for the first hierarchical parent, or ('', '')."""
    double_enc = urllib.parse.quote(urllib.parse.quote(iri, safe=""), safe="")
    url = f"{api_base}/ontologies/{ontology}/terms/{double_enc}/hierarchicalParents"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        terms = (data.get("_embedded") or {}).get("terms") or []
        if not terms:
            return "", ""
        t = terms[0]
        pid = t.get("obo_id") or t.get("short_form") or ""
        plabel = t.get("label") or ""
        piri = t.get("iri") or ""
        if not piri and pid and ":" in pid:
            prefix, local = pid.split(":", 1)
            piri = f"http://purl.obolibrary.org/obo/{prefix}_{local}"
        display = f"{pid}: {plabel}" if pid and plabel else (plabel or pid)
        return display, piri
    except Exception:
        return "", ""


def _fetch_bioportal_parent(base_uri, apikey, ontology, iri):
    """Return (display_str, parent_uri) for the first parent of a term, or ('', '')."""
    encoded = urllib.parse.quote(iri, safe="")
    url = (f"{base_uri}/ontologies/{ontology}/classes/{encoded}/parents"
           f"?apikey={apikey}&include=prefLabel&pagesize=1")
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        items = data if isinstance(data, list) else (data.get("collection") or [])
        if not items:
            return "", ""
        item = items[0]
        parent_iri = item.get("@id") or ""
        parent_id = parent_iri.rsplit("/", 1)[-1].replace("_", ":") if parent_iri else ""
        plabel = item.get("prefLabel") or ""
        display = f"{parent_id}: {plabel}" if parent_id and plabel else (plabel or parent_id)
        return display, parent_iri
    except Exception:
        return "", ""


_OLS4_AI_SEARCH_MODEL = "llama-embed-nemotron-8b_pca512"


def _search_ols4(query, api_conf, api_name, top_n=10, ai_mode=False):
    """Search OLS4 for terms matching query. Returns list of match dicts.

    When ai_mode=True, passes searchModel=llama-embed-nemotron-8b_pca512 to
    request EBI's embedding-based AI search instead of BM25 keyword search.
    The full query (term + description) is sent to the API for better semantic
    retrieval, but scoring against returned results uses only the term text.
    """
    # Pass full text (term + description) to the API — the extra context helps
    # semantic/BM25 ranking on the server side.
    api_query_text = query["term"] + (" " + query["description"] if query.get("description") else "")
    # Score returned results against the term only so description words don't
    # dilute token overlap (e.g. "a soil related term" shouldn't require those
    # words to appear in the matched label/definition).
    term_text = query["term"]
    q_tokens = _tokenize(term_text)

    rest_conf = ((api_conf or {}).get("type") or {}).get("rest") or {}
    uri_template = rest_conf.get("uri") or f"{_OLS4_DEFAULT_SEARCH_URI}/ontologies/{{ontology}}/terms/{{double_encoded}}/graph"
    # Strip to api_base: everything up to and including /api
    api_base = uri_template.split("/ontologies/")[0].rstrip("/")

    ontologies = api_conf.get("ontologies") or []
    params = {
        "q": api_query_text,
        "rows": str(top_n * 2),
        "fieldList": "label,description,short_form,iri,obo_id,ontology_name,type",
    }
    if ontologies:
        params["ontology"] = ",".join(o.lower() for o in ontologies)
    if ai_mode:
        params["searchModel"] = _OLS4_AI_SEARCH_MODEL

    url = f"{api_base}/search?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  Warning: OLS4 search failed for '{api_query_text[:40]}': {e}", file=sys.stderr)
        return []
    # Collect candidates, then deduplicate by term_id keeping the primary ontology
    candidates = []
    for doc in (data.get("response") or {}).get("docs") or []:
        label = doc.get("label") or ""
        desc_raw = doc.get("description") or ""
        description = (desc_raw[0] if isinstance(desc_raw, list) and desc_raw else desc_raw) or ""
        term_id = doc.get("obo_id") or doc.get("short_form") or doc.get("iri") or ""
        ontology = doc.get("ontology_name") or ""
        score = _match_score(q_tokens, term_text, label, description)
        if score <= 0:
            continue
        candidates.append({
            "source":     f"{api_name}:{ontology}" if ontology else api_name,
            "id":         term_id,
            "term_uri":   doc.get("iri") or "",
            "label":      label,
            "type":       "term",
            "parent":     ontology.upper() if ontology else "",
            "parent_uri": "",
            "definition": description,
            "def_source": "verbatim" if _is_verbatim(query, label, description) else "",
            "score":      score,
        })

    # Deduplicate: when the same term appears in multiple ontologies (because one
    # imports from another), keep only the result whose ontology matches the term's
    # own IRI prefix (e.g. ENVO:01001370 → prefer ontology_name=="envo").
    seen: dict = {}
    for c in candidates:
        tid = c["id"]
        id_prefix = tid.split(":")[0].upper() if ":" in str(tid) else ""
        if tid not in seen:
            seen[tid] = c
        elif id_prefix and c["parent"] == id_prefix and seen[tid]["parent"] != id_prefix:
            seen[tid] = c  # replace imported copy with the owning ontology's copy
    matches = list(seen.values())[:top_n]

    # Fetch parent and children for each match in parallel
    if not matches:
        return matches
    iris = [m.get("term_uri", "") for m in matches]
    onts = [m["parent"].lower() for m in matches]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(matches) * 2, 12)) as ex:
        parent_futs = [ex.submit(_fetch_ols4_parent, api_base, ont, iri)
                       for ont, iri in zip(onts, iris)]
        child_futs  = [ex.submit(_fetch_ols4_children, api_base, ont, iri)
                       for ont, iri in zip(onts, iris)]
        for m, pfut, cfut in zip(matches, parent_futs, child_futs):
            try:
                parent_str, parent_uri = pfut.result()
                if parent_str:
                    m["parent"] = parent_str
                    m["parent_uri"] = parent_uri
            except Exception:
                pass
            try:
                m["children"] = cfut.result()
            except Exception:
                m["children"] = []

    matches.sort(key=lambda m: (-m["score"], str(m["id"]).lower()))
    return matches


def _search_bioportal(query, api_conf, api_name, top_n=10):
    """Search BioPortal for terms matching query. Returns list of match dicts.

    The full query (term + description) is sent to the API for richer retrieval,
    but scoring against returned results uses only the term text so that
    description words (e.g. 'a soil related term') don't dilute token overlap.
    """
    api_query_text = query["term"] + (" " + query["description"] if query.get("description") else "")
    term_text = query["term"]
    q_tokens = _tokenize(term_text)

    rest_conf = ((api_conf or {}).get("type") or {}).get("rest") or {}
    base_uri = (rest_conf.get("uri") or "https://data.bioontology.org").rstrip("/")
    apikey = rest_conf.get("apikey") or ""
    if not apikey:
        print(f"  Warning: BioPortal search skipped for '{api_name}': no apikey configured",
              file=sys.stderr)
        return []

    ontologies = api_conf.get("ontologies") or []
    params = {
        "q": api_query_text,
        "pagesize": str(top_n * 2),
        "include": "prefLabel,definition",
        "apikey": apikey,
    }
    if ontologies:
        params["ontologies"] = ",".join(o.upper() for o in ontologies)

    url = f"{base_uri}/search?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  Warning: BioPortal search failed for '{api_query_text[:40]}': {e}", file=sys.stderr)
        return []

    matches = []
    for item in (data.get("collection") or []):
        label = item.get("prefLabel") or ""
        def_raw = item.get("definition") or ""
        description = (def_raw[0] if isinstance(def_raw, list) and def_raw else def_raw) or ""
        ont_url = (item.get("links") or {}).get("ontology") or ""
        ontology = ont_url.rstrip("/").rsplit("/", 1)[-1] if ont_url else ""
        iri = item.get("@id") or ""
        term_id = iri.rsplit("/", 1)[-1].replace("_", ":") if iri else ""
        score = _match_score(q_tokens, term_text, label, description)
        if score <= 0:
            continue
        matches.append({
            "source":     f"{api_name}:{ontology}" if ontology else api_name,
            "id":         term_id,
            "term_uri":   iri,
            "label":      label,
            "type":       "term",
            "parent":     ontology.upper() if ontology else "",
            "parent_uri": "",
            "definition": description,
            "def_source": "verbatim" if _is_verbatim(query, label, description) else "",
            "score":      score,
        })

    matches = matches[:top_n]

    # Fetch parent and children for each match in parallel
    if not matches:
        return matches
    iris = [m.get("term_uri", "") for m in matches]
    onts = [m["parent"] for m in matches]  # uppercase ontology name
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(matches) * 2, 12)) as ex:
        parent_futs = [ex.submit(_fetch_bioportal_parent, base_uri, apikey, ont, iri)
                       for ont, iri in zip(onts, iris)]
        child_futs  = [ex.submit(_fetch_bioportal_children, base_uri, apikey, ont, iri)
                       for ont, iri in zip(onts, iris)]
        for m, pfut, cfut in zip(matches, parent_futs, child_futs):
            try:
                parent_str, parent_uri = pfut.result()
                if parent_str:
                    m["parent"] = parent_str
                    m["parent_uri"] = parent_uri
            except Exception:
                pass
            try:
                m["children"] = cfut.result()
            except Exception:
                m["children"] = []

    matches.sort(key=lambda m: (-m["score"], str(m["id"]).lower()))
    return matches


_OLS4_URI_MARKERS = ("ols4", "ebi.ac.uk/ols")


def _combined_q_and_tokens(queries):
    """Return (or_query_str, all_tokens_list) for a list of query dicts.

    Produces a Lucene-style OR expression:  "soil acidity" OR "soil pH" OR ...
    Tokens are the union of all per-term tokens, used for local match scoring.
    """
    or_q = " OR ".join(f'"{q["term"]}"' for q in queries)
    tokens = frozenset(t for q in queries for t in _tokenize(q["term"]))
    return or_q, tokens


def _search_bioportal_combined(queries, api_conf, api_name, top_n=10):
    """Single BioPortal request for multiple synonym queries joined with OR.

    Returns a flat list of match dicts (no per-query attribution needed —
    caller broadcasts these to all query slots).
    """
    rest_conf = ((api_conf or {}).get("type") or {}).get("rest") or {}
    base_uri = (rest_conf.get("uri") or "https://data.bioontology.org").rstrip("/")
    apikey = rest_conf.get("apikey") or ""
    if not apikey:
        return []

    combined_q, all_tokens = _combined_q_and_tokens(queries)
    ontologies = api_conf.get("ontologies") or []
    pagesize = min(top_n * max(len(queries), 2), 60)
    params = {
        "q": combined_q,
        "pagesize": str(pagesize),
        "include": "prefLabel,definition",
        "apikey": apikey,
    }
    if ontologies:
        params["ontologies"] = ",".join(o.upper() for o in ontologies)

    url = f"{base_uri}/search?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  Warning: BioPortal combined search failed: {e}", file=sys.stderr)
        return []

    combined_term_text = " ".join(q["term"] for q in queries)
    matches = []
    for item in (data.get("collection") or []):
        label = item.get("prefLabel") or ""
        def_raw = item.get("definition") or ""
        description = (def_raw[0] if isinstance(def_raw, list) and def_raw else def_raw) or ""
        ont_url = (item.get("links") or {}).get("ontology") or ""
        ontology = ont_url.rstrip("/").rsplit("/", 1)[-1] if ont_url else ""
        iri = item.get("@id") or ""
        term_id = iri.rsplit("/", 1)[-1].replace("_", ":") if iri else ""
        score = _match_score(all_tokens, combined_term_text, label, description)
        if score <= 0:
            continue
        matches.append({
            "source":     f"{api_name}:{ontology}" if ontology else api_name,
            "id":         term_id,
            "term_uri":   iri,
            "label":      label,
            "type":       "term",
            "parent":     ontology.upper() if ontology else "",
            "parent_uri": "",
            "definition": description,
            "def_source": "",
            "score":      score,
            "children":   [],
        })

    matches = matches[:top_n]
    if not matches:
        return matches

    iris = [m.get("term_uri", "") for m in matches]
    onts = [m["parent"] for m in matches]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(matches) * 2, 12)) as ex:
        parent_futs = [ex.submit(_fetch_bioportal_parent, base_uri, apikey, ont, iri)
                       for ont, iri in zip(onts, iris)]
        child_futs  = [ex.submit(_fetch_bioportal_children, base_uri, apikey, ont, iri)
                       for ont, iri in zip(onts, iris)]
        for m, pfut, cfut in zip(matches, parent_futs, child_futs):
            try:
                parent_str, parent_uri = pfut.result()
                if parent_str:
                    m["parent"] = parent_str
                    m["parent_uri"] = parent_uri
            except Exception:
                pass
            try:
                m["children"] = cfut.result()
            except Exception:
                pass

    matches.sort(key=lambda m: (-m["score"], str(m["id"]).lower()))
    return matches


def _search_ols4_combined(queries, api_conf, api_name, top_n=10, ai_mode=False):
    """Single OLS4 request for multiple synonym queries joined with OR.

    Returns a flat list of match dicts (caller broadcasts to all query slots).
    """
    combined_q, all_tokens = _combined_q_and_tokens(queries)
    combined_term_text = " ".join(q["term"] for q in queries)

    rest_conf = ((api_conf or {}).get("type") or {}).get("rest") or {}
    uri_template = rest_conf.get("uri") or f"{_OLS4_DEFAULT_SEARCH_URI}/ontologies/{{ontology}}/terms/{{double_encoded}}/graph"
    api_base = uri_template.split("/ontologies/")[0].rstrip("/")

    ontologies = api_conf.get("ontologies") or []
    pagesize = min(top_n * max(len(queries), 2), 60)
    params = {
        "q": combined_q,
        "rows": str(pagesize),
        "fieldList": "label,description,short_form,iri,obo_id,ontology_name,type",
    }
    if ontologies:
        params["ontology"] = ",".join(o.lower() for o in ontologies)
    if ai_mode:
        params["searchModel"] = _OLS4_AI_SEARCH_MODEL

    url = f"{api_base}/search?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  Warning: OLS4 combined search failed: {e}", file=sys.stderr)
        return []

    candidates = []
    for doc in (data.get("response") or {}).get("docs") or []:
        label = doc.get("label") or ""
        desc_raw = doc.get("description") or ""
        description = (desc_raw[0] if isinstance(desc_raw, list) and desc_raw else desc_raw) or ""
        term_id = doc.get("obo_id") or doc.get("short_form") or doc.get("iri") or ""
        ontology = doc.get("ontology_name") or ""
        score = _match_score(all_tokens, combined_term_text, label, description)
        if score <= 0:
            continue
        candidates.append({
            "source":     f"{api_name}:{ontology}" if ontology else api_name,
            "id":         term_id,
            "term_uri":   doc.get("iri") or "",
            "label":      label,
            "type":       "term",
            "parent":     ontology.upper() if ontology else "",
            "parent_uri": "",
            "definition": description,
            "def_source": "",
            "score":      score,
            "children":   [],
        })

    seen: dict = {}
    for c in candidates:
        tid = c["id"]
        id_prefix = tid.split(":")[0].upper() if ":" in str(tid) else ""
        if tid not in seen:
            seen[tid] = c
        elif id_prefix and c["parent"] == id_prefix and seen[tid]["parent"] != id_prefix:
            seen[tid] = c
    matches = list(seen.values())[:top_n]

    if not matches:
        return matches

    iris = [m.get("term_uri", "") for m in matches]
    onts = [m["parent"].lower() for m in matches]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(matches) * 2, 12)) as ex:
        parent_futs = [ex.submit(_fetch_ols4_parent, api_base, ont, iri)
                       for ont, iri in zip(onts, iris)]
        child_futs  = [ex.submit(_fetch_ols4_children, api_base, ont, iri)
                       for ont, iri in zip(onts, iris)]
        for m, pfut, cfut in zip(matches, parent_futs, child_futs):
            try:
                parent_str, parent_uri = pfut.result()
                if parent_str:
                    m["parent"] = parent_str
                    m["parent_uri"] = parent_uri
            except Exception:
                pass
            try:
                m["children"] = cfut.result()
            except Exception:
                pass

    matches.sort(key=lambda m: (-m["score"], str(m["id"]).lower()))
    return matches


def _search_apis(queries, apis, top_n=10, ai_mode=False):
    """Search configured REST APIs (OLS4, BioPortal) for each query.

    Returns a list parallel to queries: [{query, matches}, ...].
    BioPortal: detected by presence of apikey in rest config.
    OLS4: detected by uri containing 'ols4'/'ebi.ac.uk/ols', or no uri (uses default).
    Other REST endpoints (e.g. agrovoc browse API) are skipped — use sparql type for those.
    When ai_mode=True, OLS4 requests use the llama-embed-nemotron embedding model.

    When multiple queries are given (e.g. AI synonym expansion), a single OR-combined
    request is made per API instead of N sequential requests, avoiding rate-limit errors.
    The combined results are broadcast to all query slots; downstream dedup handles overlap.
    """
    results = [{"query": q, "matches": []} for q in queries]
    for api_name, api_conf in (apis or {}).items():
        rest_conf = ((api_conf or {}).get("type") or {}).get("rest") or {}
        if not rest_conf:
            continue
        apikey = rest_conf.get("apikey") or ""
        uri = rest_conf.get("uri") or ""
        is_bioportal = bool(apikey)
        is_ols4 = not is_bioportal and (not uri or any(m in uri for m in _OLS4_URI_MARKERS))
        if not is_bioportal and not is_ols4:
            continue  # non-OLS4, non-BioPortal REST endpoint — skip for search

        if len(queries) > 1:
            # Multiple synonym queries: one combined OR request, broadcast to all slots.
            if is_bioportal:
                combined = _search_bioportal_combined(queries, api_conf, api_name, top_n)
            else:
                combined = _search_ols4_combined(queries, api_conf, api_name, top_n, ai_mode=ai_mode)
            for r in results:
                r["matches"].extend(combined)
        else:
            if is_bioportal:
                api_matches = _search_bioportal(queries[0], api_conf, api_name, top_n)
            else:
                api_matches = _search_ols4(queries[0], api_conf, api_name, top_n, ai_mode=ai_mode)
            results[0]["matches"].extend(api_matches)
    return results


def generate_enum_report(yaml_file, tsv=False, output=sys.stdout, header=None):
    """Generate a report of enum keys, titles, source_domain, and source_schema.

    Works on any LinkML schema YAML file that contains an 'enums' section with
    annotations including source_domain and source_schema.

    Output is space-padded columns by default, or tab-delimited if tsv=True.
    If header is provided it is printed on its own line before the table.
    """
    if header:
        print(header, file=output)

    with open(yaml_file, "r") as f:
        try:
            schema = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"Warning: {yaml_file} is not valid YAML (downloaded content may be HTML or redirected): {e}", file=sys.stderr)
            return

    if not isinstance(schema, dict):
        print(f"Warning: {yaml_file} did not parse as a YAML mapping (got {type(schema).__name__}) — skipping enum report.", file=sys.stderr)
        return

    enums = schema.get("enums", {})
    rows = []

    for key, enum_def in enums.items():
        if not enum_def:
            enum_def = {}
        title = enum_def.get("title") or ""
        annotations = enum_def.get("annotations") or {}
        source_domain = annotations.get("source_domain") or ""
        source_schema = annotations.get("source_schema") or ""
        rows.append((source_domain, source_schema, key, title))

    rows.sort(key=lambda r: (r[0], r[1], r[2]))

    headers = ["source_domain", "source_schema", "enum_key", "title"]
    all_rows = [headers] + rows

    if tsv:
        for row in all_rows:
            print("\t".join(row), file=output)
    else:
        widths = [max(len(r[i]) for r in all_rows) for i in range(len(headers))]
        for row in all_rows:
            print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip(), file=output)


# ---------------------------------------------------------------------------
# --translate: machine-translate a source's enum/PV labels to target locales
# ---------------------------------------------------------------------------

def translate_source(key, config_file):
    """Translate enum titles, descriptions, and PV titles for a source.

    Reads sources/{key}.yaml, translates all EN strings to the non-English
    locales listed in the config file, shows results for review, then (on
    confirmation) writes entries to {config_stem}_sssom.tsv.

    Run -b afterwards to merge translations into schema.yaml locale extensions.
    """
    # Load config
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    all_sources = config.get("sources") or {}
    if key not in all_sources:
        print(f"Error: source '{key}' not found in {config_file}", file=sys.stderr)
        sys.exit(1)

    # Determine target locales
    locales_cfg  = config.get("locales") or ["en"]
    target_langs = [l for l in locales_cfg if l.lower() != "en"]
    if not target_langs:
        print(f"No non-English locales configured in {config_file}. "
              f"Add languages to 'locales:' to enable translation.")
        return

    # Load source YAML
    yaml_path = f"sources/{key}.yaml"
    if not os.path.exists(yaml_path):
        print(f"Error: {yaml_path} not found — run -f and -c first", file=sys.stderr)
        sys.exit(1)

    with open(yaml_path, encoding="utf-8") as f:
        source_schema = yaml.safe_load(f) or {}

    enums = source_schema.get("enums") or {}
    if not enums:
        print(f"No enums found in {yaml_path}.")
        return

    # Determine SSSOM output path and check for existing entries
    config_stem  = os.path.splitext(os.path.basename(config_file))[0]
    sssom_path   = f"{config_stem}_sssom.tsv"
    source_prefix = f"{config_stem}:{key}:"
    existing_rows = _load_translate_sssom_raw(sssom_path)   # [(subject_id, lang, text)]
    already_exist = any(r[0].startswith(source_prefix) for r in existing_rows)
    if already_exist:
        print(f"Warning: {sssom_path} already has translations for source '{key}'")
        answer = input("Override existing translations for this source? [yes/no]: ").strip().lower()
        if answer not in ("yes", "y"):
            print("Aborted.")
            return
        # Drop existing entries for this source; keep everything else
        existing_rows = [r for r in existing_rows if not r[0].startswith(source_prefix)]

    # Require deep-translator
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        print(
            "Error: deep-translator is required for --translate.\n"
            "Install it with:  pip install deep-translator",
            file=sys.stderr,
        )
        sys.exit(1)

    from source_cansis_translate import _translate_list

    # Build flat list of strings to translate
    items = []   # (enum_key, field, pv_key_or_None)
    texts = []   # EN text parallel to items

    for enum_key, enum_data in enums.items():
        if not isinstance(enum_data, dict):
            continue
        if enum_data.get("title"):
            items.append((enum_key, "enum_title", None))
            texts.append(enum_data["title"])
        if enum_data.get("description"):
            items.append((enum_key, "enum_description", None))
            texts.append(enum_data["description"])
        for pv_key, pv_data in (enum_data.get("permissible_values") or {}).items():
            pv_title = (pv_data or {}).get("title") or ""
            if pv_title:
                items.append((enum_key, "pv_title", pv_key))
                texts.append(pv_title)

    if not texts:
        print(f"No translatable strings found in {yaml_path} (no enum or PV titles/descriptions).")
        return

    # Translate for each target language
    lang_results = {}
    for lang in target_langs:
        print(f"Translating {len(texts)} string(s) to '{lang}'...")
        lang_results[lang] = _translate_list(GoogleTranslator, texts, src="en", tgt=lang)
        print(f"  Done.")

    # Reconstruct per-lang per-enum result
    lang_enum_data = {}
    for lang, translated in lang_results.items():
        enum_trans = {}
        for (enum_key, field, pv_key), tr_text in zip(items, translated):
            et = enum_trans.setdefault(enum_key, {"pvs": {}})
            if field == "enum_title":
                et["title"] = tr_text
            elif field == "enum_description":
                et["description"] = tr_text
            elif field == "pv_title":
                et["pvs"][pv_key] = tr_text
        lang_enum_data[lang] = enum_trans

    # Display for review
    for lang in target_langs:
        print(f"\n{'='*60}")
        print(f"  Translations: {key} → {lang}")
        print(f"{'='*60}")
        for enum_key, et in lang_enum_data[lang].items():
            orig = enums.get(enum_key) or {}
            print(f"\n  [{enum_key}]")
            if "title" in et:
                print(f"    Title:  {(orig.get('title') or '')!r}")
                print(f"          → {et['title']!r}")
            if "description" in et:
                orig_d   = orig.get("description") or ""
                snip     = orig_d[:70] + ("..." if len(orig_d) > 70 else "")
                snip_tr  = et["description"][:70] + ("..." if len(et["description"]) > 70 else "")
                print(f"    Desc:  {snip!r}")
                print(f"         → {snip_tr!r}")
            pvs = et.get("pvs") or {}
            if pvs:
                pv_list = list(pvs.items())
                show_n  = min(len(pv_list), 10)
                print(f"    PVs ({len(pv_list)}):")
                for pv_key, pv_tr in pv_list[:show_n]:
                    orig_pv = (orig.get("permissible_values") or {}).get(pv_key) or {}
                    orig_t  = orig_pv.get("title") if isinstance(orig_pv, dict) else ""
                    print(f"      {pv_key}: {(orig_t or pv_key)!r} → {pv_tr!r}")
                if len(pv_list) > show_n:
                    print(f"      ... and {len(pv_list) - show_n} more")

    # Confirmation prompt
    print()
    answer = input("Apply translations? [ok/skip]: ").strip().lower()
    if answer not in ("ok", "yes", "y"):
        print("Translations not applied.")
        return

    # Build new SSSOM rows
    new_rows = []
    for lang in target_langs:
        for enum_key, et in lang_enum_data[lang].items():
            if et.get("title"):
                new_rows.append((f"{config_stem}:{key}:{enum_key}:name", lang, et["title"]))
            if et.get("description"):
                new_rows.append((f"{config_stem}:{key}:{enum_key}:description", lang, et["description"]))
            for pv_key, pv_tr in (et.get("pvs") or {}).items():
                if pv_tr:
                    new_rows.append((f"{config_stem}:{key}:{enum_key}:choice:{pv_key}", lang, pv_tr))

    # Load schema.yaml for SSSOM metadata
    schema_id = schema_name = ""
    if os.path.exists("schema.yaml"):
        with open("schema.yaml", encoding="utf-8") as f:
            _sd = yaml.safe_load(f) or {}
        schema_id   = _sd.get("id") or ""
        schema_name = _sd.get("name") or ""

    all_rows = existing_rows + new_rows
    _write_translate_sssom(all_rows, sssom_path, config_stem, schema_id, schema_name)
    pv_count = sum(len((et.get("pvs") or {})) for et in lang_enum_data.get(target_langs[0], {}).values())
    print(f"  {len(new_rows)} entries for {len(enums)} enum(s), {pv_count} PV title(s)")
    print(f"  Run -b to apply translations to schema.yaml")


def main():
    parser = argparse.ArgumentParser(description="Fetch LinkML and other Value Sets and merge into a LinkML schema.yaml file; as well generate enum reports from fetched files.")
    parser.add_argument("-a", "--add", nargs="+", metavar="URL", help="Add one or more sources by URL, auto-detecting type and processing into harvester_config.yaml")
    parser.add_argument("-b", "--build", nargs="?", const=True, metavar="SOURCE_KEY", help="Build or update schema.yaml; optionally supply a source key to rebuild only that source")
    parser.add_argument("-f", "--fetch", nargs="*", metavar="SOURCE_KEY|all", help="Fetch sources. Use '-f all' to fetch every source in harvester_config.yaml, or supply specific source keys. Without arguments, fetches only sources listed with -c.")
    parser.add_argument("-c", "--config", nargs="*", metavar="SOURCE_KEY", help="Update harvester_config.yaml with prefix dicts from source files; omit keys to process all sources")
    parser.add_argument("-d", "--delete", nargs="+", metavar="SOURCE_KEY", help="Remove one or more source keys from harvester_config.yaml")
    parser.add_argument("-l", "--lookup", nargs="*", metavar="SOURCE_KEY", help="Expand reachable_from.source_nodes enums via OLS4 API for YAML sources; omit keys to process all sources")
    parser.add_argument("-r", "--report", action="store_true", help="Generate enum report for all sources in harvester_config.yaml")
    parser.add_argument("-s", "--sssom", nargs="*", metavar="PREDICATE",
        help=(
            "Apply SSSOM ontology mappings from the top-level 'sssom' file list in "
            "harvester_config.yaml to permissible_values in schema.yaml.  Optionally supply "
            "one or more predicate_id values to restrict which mapping types are written "
            f"({', '.join(SSSOM_PREDICATE_MAP)}); omit to apply all."
        ))
    parser.add_argument("-t", "--tabformat", action="store_true", help="Output report as tab-delimited TSV (default is space-padded columns)")
    parser.add_argument("-i", "--input", metavar="CONFIG_FILE", default=None, help="Path to the configuration file (default: harvester_config.yaml)")
    parser.add_argument("--free_text", metavar="TEXT", default=None, help="Free text describing a picklist to extract via Claude API (used with -a; requires ANTHROPIC_API_KEY)")
    parser.add_argument("--search", metavar="TEXT", default=None,
        help=(
            "Search configured sources for matching terms.  Accepts free text, "
            "'term:description' structured pairs, or ';'/newline-separated batches of either.  "
            "Searches enum names/titles/descriptions and permissible value codes/titles/descriptions "
            "in all sources/*.yaml files.  Use --format to control output format."
        ))
    parser.add_argument("--format", dest="output_format",
                        choices=["text", "tsv", "markdown"], default="text",
                        help="Output format for --search results: text (default), tsv, or markdown.")
    parser.add_argument("--ai", action="store_true",
        help="Use Claude AI to semantically rank search results and synthesize missing definitions (requires ANTHROPIC_API_KEY; reserved for future implementation).")
    parser.add_argument("--debug", action="store_true",
        help="Print extra diagnostic output. For FreeText -c: shows the full new extraction when the size-guard rejects it.")
    parser.add_argument("--translate", metavar="SOURCE_KEY",
        help=(
            "Translate enum titles, descriptions, and PV titles for the named source to the "
            "non-English locales listed in the config file (requires: pip install deep-translator). "
            "Translations are written to {config_stem}_sssom.tsv and applied to schema.yaml on "
            "the next -b run.  Warns and prompts before overriding existing translations."
        ))
    args = parser.parse_args()

    config_file = args.input if args.input else MENU_CONFIG

    if args.input and not os.path.isfile(config_file):
        parser.error(f"config file not found: {config_file!r}\n"
                     f"  Check the path passed to -i/--input, or omit it to use the default '{MENU_CONFIG}'.")
    elif not args.input and not args.add and not os.path.isfile(config_file):
        parser.error(f"'{MENU_CONFIG}' not found in the current directory.\n"
                     f"  Use -i <path> to specify a config file, or use -a <URL> to create one.")

    if args.add:
        add_source(args.add, config_file, free_text=args.free_text)
    if args.delete:
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)
        all_sources = config.get("sources", {})

        # Delete any keys that are config file source entries
        config_keys = [k for k in args.delete if k in all_sources]
        for key in config_keys:
            del config["sources"][key]
            print(f"Deleted source '{key}' from {config_file}")
            # Remove all sources/ files associated with this key
            _source_exts = ("yaml", "zip", "html", "htm", "pdf", "json", "csv", "txt")
            for _ext in _source_exts:
                _p = f"sources/{key}.{_ext}"
                if os.path.exists(_p):
                    os.remove(_p)
                    print(f"  Deleted {_p}")
        if config_keys:
            write_config(config, config_file)

        # Delete matching enums from schema.yaml:
        # - enum key directly matches a given key, OR
        # - enum's imported_from annotation matches a given key (source deletion)
        delete_set = set(args.delete)
        schema_file = "schema.yaml"
        removed = []
        if os.path.exists(schema_file):
            with open(schema_file, "r") as f:
                schema = yaml.safe_load(f) or {}
            enums = schema.get("enums") or {}
            for enum_key in list(enums):
                ann = (enums[enum_key].get("annotations") or {}) if isinstance(enums[enum_key], dict) else {}
                if enum_key in delete_set or ann.get("imported_from") in delete_set:
                    del enums[enum_key]
                    removed.append(enum_key)
            if removed:
                schema["enums"] = enums
                with open(schema_file, "w") as f:
                    yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
                print(f"Deleted {len(removed)} enum(s) from {schema_file}: {', '.join(sorted(removed))}")

        acted_on = set(config_keys) | set(removed)
        not_found = [k for k in args.delete if k not in acted_on]
        if not_found:
            print(f"Warning: key(s) not found in {config_file} or {schema_file}: {', '.join(not_found)}", file=sys.stderr)

        # Remove deleted keys from the search index
        if os.path.exists(_SEARCH_INDEX_DB):
            try:
                _conn = sqlite3.connect(_SEARCH_INDEX_DB)
                for _key in acted_on:
                    _conn.execute("DELETE FROM terms WHERE source_key=?", (_key,))
                _conn.commit()
                _conn.close()
                if acted_on:
                    print(f"  Search index: removed entries for {', '.join(sorted(acted_on))}")
            except Exception as _e:
                print(f"  Warning: could not update search index: {_e}", file=sys.stderr)
    if args.fetch is not None:
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)
        all_sources = config.get("sources", {})
        if "all" in args.fetch:
            keys_to_download = list(all_sources.keys())
        elif args.fetch:
            keys_to_download = args.fetch
        elif args.config:
            keys_to_download = args.config
        else:
            print("No sources fetched. Provide source keys with -c, or use '-f all' to fetch every source.", file=sys.stderr)
            keys_to_download = []
        invalid = [k for k in keys_to_download if k not in all_sources]
        if invalid:
            print(f"Unknown source key(s): {', '.join(invalid)}", file=sys.stderr)
            sys.exit(1)
        os.makedirs("sources", exist_ok=True)
        locales_cfg = config.get("locales") or ["en"]
        _nrcs_pdf_fetched = False  # deduplicate: all NRCSSoilFieldBook entries share one PDF
        for key in keys_to_download:
            source = all_sources[key]
            content_type = source.get("content_type", "")
            # FreeText: skip silently on -f all; allow only when explicitly named.
            if content_type == "FreeText":
                if "all" in args.fetch:
                    continue
                fetch_freetext_source(key, source, config_file)
                continue
            # NRCSSoilFieldBook: all source entries share one PDF; download only once per run.
            if content_type == "NRCSSoilFieldBook":
                if not _nrcs_pdf_fetched:
                    fetch_nrcs_pdf()
                    _nrcs_pdf_fetched = True
                else:
                    print(f"  Skipping {key}: NRCSSoilFieldBook PDF already downloaded this run")
                process_nrcs_source(key, source, locales=locales_cfg)
                continue
            # STATSCAN: multi-page crawl — build sources/{key}.zip.
            if content_type == "STATSCAN":
                fetch_statscan_source(key, source, config_file, locales=locales_cfg)
                process_statscan_source(key, source, config_file, locales=locales_cfg)
                continue
            # ISO_COUNTRY: source_ontology is a Vaadin SPA URL; fetch Wikipedia instead.
            if content_type == "ISO_COUNTRY":
                fetch_iso_country_source(key, source, config_file)
                process_iso_country_source(key, source, config_file, locales=locales_cfg)
                continue
            # NSDB sources: multi-page crawl — build sources/{key}.zip, not a single HTML.
            if content_type in ("NSDBSNT", "NSDBSLT", "NSDBSLC"):
                fetch_nsdb_html_source(key, source, config_file, locales=locales_cfg)
                _enum_prefix = "NSDBSLC" if content_type == "NSDBSLC" else "NSDB"
                process_nsdb_html_source(key, source, enum_prefix=_enum_prefix, locales=locales_cfg)
                continue
            if content_type == "NSDB":
                fetch_nsdb_source(key, source, config_file, locales=locales_cfg)
                process_nsdb_source(key, source, locales=locales_cfg)
                continue
            # AgriFoodCA: re-download CSVs to zip and rebuild YAML.
            if content_type == "AgriFoodCA":
                refetch_agrifood_dir(key, source, config_file=config_file, locales=locales_cfg)
                continue
            # CRediT: Zenodo direct download returns 403; use API content endpoint instead.
            if content_type == "CRediT":
                fetch_credit_source(key, source, config_file)
                continue
            if content_type == "LOC_CLASSIFICATION":
                fetch_loc_source(key, source, config_file)
                continue
            if content_type == "CANSIS_GLOSSARY":
                fetch_cansis_glossary_source(key, source, config_file)
                continue
            uri = (source.get("reachable_from") or {}).get("source_ontology")
            if not uri:
                print(f"Skipping {key}: no source_ontology URL found (API-based sources must be fetched via -c)", file=sys.stderr)
                continue
            file_format = source.get("file_format", "yaml")
            output_path = f"sources/{key}.{file_format}"
            print(f"Fetching {uri} ...")
            tmp_fd, tmp_path = tempfile.mkstemp(dir="sources")
            os.close(tmp_fd)
            try:
                req = urllib.request.Request(uri, headers=BROWSER_HEADERS)
                with urllib.request.urlopen(req) as response:
                    with open(tmp_path, "wb") as tmp_f:
                        tmp_f.write(response.read())
            except Exception as e:
                _keep = f" — keeping existing {output_path}" if os.path.exists(output_path) else ""
                print(f"  Error fetching {uri}: {e}{_keep}", file=sys.stderr)
                os.unlink(tmp_path)
                continue
            new_size = os.path.getsize(tmp_path)
            if new_size == 0:
                _keep = f" — keeping existing {output_path}" if os.path.exists(output_path) else ""
                print(f"  Error: downloaded file is empty{_keep}", file=sys.stderr)
                os.unlink(tmp_path)
                continue
            if os.path.exists(output_path):
                existing_size = os.path.getsize(output_path)
                if existing_size > 0 and new_size <= existing_size * 0.8:
                    print(f"  Error: new download is {new_size:,} bytes "
                          f"({new_size / existing_size:.0%} of existing {existing_size:,}) "
                          f"— keeping existing {output_path}", file=sys.stderr)
                    os.unlink(tmp_path)
                    continue
            os.replace(tmp_path, output_path)
            print(f"Saved to {output_path}")
            update_source_config(key, {"download_date": datetime.date.today().isoformat()}, config_file)
            content_type = source.get("content_type", "")
            if content_type in ("LOINCCodeSystem", "LOINCValueSet"):
                fill_loinc_source_metadata(output_path, key, config_file)
            process_sources([key], config_file)
        # Report all config sources still missing a yaml (not just those fetched this run).
        _report_missing_yamls(config_file)
    if args.config is not None:
        process_sources(args.config, config_file, debug=getattr(args, "debug", False))
    if args.build is not None:
        build_schema(keys=[args.build] if isinstance(args.build, str) else None, config_file=config_file)
    # -s must run after -b: SSSOM mappings are applied to the schema.yaml that
    # -b produces, so this order must be preserved.
    if args.sssom is not None:
        apply_sssom_mappings(predicates=args.sssom or None, config_file=config_file)
    if args.lookup is not None:
        schema_file = "schema.yaml"
        if not os.path.exists(schema_file):
            print(f"schema.yaml not found — run -b first", file=sys.stderr)
        else:
            lookup_results = {}   # enum_key -> pv count

            # Load apis and locales config once for all lookup calls
            with open(config_file, "r") as f:
                _lconfig = yaml.safe_load(f) or {}
            _apis    = _lconfig.get("apis") or {}
            _locales = _lconfig.get("locales") or ["en"]

            if not args.lookup:
                # -l with no args: expand every enum in schema.yaml that has reachable_from.source_nodes
                lookup_results.update(expand_reachable_from(schema_file, apis=_apis, locales=_locales))
            else:
                all_sources = _lconfig.get("sources", {})
                with open(schema_file, "r") as f:
                    schema_data = yaml.safe_load(f) or {}
                schema_enums = schema_data.get("enums") or {}

                # Partition given keys: recognised source keys vs direct enum names
                source_keys = [k for k in args.lookup if k in all_sources]
                enum_keys   = [k for k in args.lookup if k not in all_sources]

                enum_filter = set()

                if source_keys:
                    # Resolve to enum keys in schema.yaml whose imported_from matches
                    from_source = {
                        ek for ek, ev in schema_enums.items()
                        if isinstance(ev, dict)
                        and (ev.get("annotations") or {}).get("imported_from") in source_keys
                    }
                    if not from_source:
                        print(
                            f"Warning: no enums in schema.yaml with imported_from in:"
                            f" {', '.join(source_keys)}",
                            file=sys.stderr,
                        )
                    enum_filter |= from_source

                if enum_keys:
                    not_found = [k for k in enum_keys if k not in schema_enums]
                    if not_found:
                        print(
                            f"Warning: enum(s) not found in schema.yaml: {', '.join(not_found)}",
                            file=sys.stderr,
                        )
                    enum_filter |= {k for k in enum_keys if k in schema_enums}

                if enum_filter:
                    lookup_results.update(expand_reachable_from(
                        schema_file, enum_filter=enum_filter, apis=_apis, locales=_locales))

            if lookup_results:
                print("\nLookup report:")
                for enum_key, count in sorted(lookup_results.items()):
                    print(f"  {enum_key}: {count} permissible_values")
                print(f"  Total: {sum(lookup_results.values())} permissible_values across {len(lookup_results)} enum(s)")
            else:
                print("Lookup: no reachable_from.source_nodes enums found to expand")
    if args.report:
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)
        all_sources = config.get("sources", {})
        first = True
        for key, source in all_sources.items():
            source_path = f"sources/{key}.yaml"
            if not os.path.exists(source_path):
                print(f"Skipping {key}: {source_path} not found — run -f and -c first", file=sys.stderr)
                continue
            if not first:
                print()
            first = False
            name = source.get("name") or key
            title = source.get("title") or ""
            header = f"{name}: {title}" if title else name
            generate_enum_report(source_path, tsv=args.tabformat, header=header)
    if args.search:
        top_n = 10
        queries = _parse_search_queries(args.search)

        # In --ai mode, expand each query with Claude-generated synonyms before
        # searching so that morphological and domain-specific variants are found.
        if args.ai:
            expanded = _ai_expand_queries(queries)
        else:
            expanded = [(i, q) for i, q in enumerate(queries)]

        exp_queries = [q for _, q in expanded]

        with open(config_file) as _f:
            _cfg = yaml.safe_load(_f) or {}
        apis = _cfg.get("apis") or {}

        # Search all expanded queries
        exp_local = _fts5_search_local(exp_queries, config_file=config_file)
        if apis:
            exp_api = _search_apis(exp_queries, apis, top_n, ai_mode=args.ai)
            for local_r, api_r in zip(exp_local, exp_api):
                local_r["matches"] = local_r["matches"] + api_r["matches"]

        # Pool synonym results back into their parent original query
        pooled = [{"query": queries[i], "matches": []} for i in range(len(queries))]
        for (orig_idx, _), exp_r in zip(expanded, exp_local):
            pooled[orig_idx]["matches"].extend(exp_r["matches"])

        for r in pooled:
            deduped = _dedup_matches_by_id(r["matches"])
            deduped.sort(key=lambda m: (m["type"] != "enum", -m["score"], str(m["id"]).lower()))
            r["matches"] = deduped[:top_n]

        _AI_MIN_SCORE = 0.30
        results = pooled
        if args.ai:
            results = _ai_rescore(results)
            for r in results:
                r["matches"] = [m for m in r["matches"]
                                if m.get("score_source") != "ai"
                                or m.get("score", 0) >= _AI_MIN_SCORE]
                r["matches"].sort(key=lambda m: (m["type"] != "enum", -m["score"],
                                                 str(m["id"]).lower()))
        fmt = args.output_format
        if args.tabformat and fmt == "text":
            fmt = "tsv"
        print(_format_search_report(results, fmt=fmt))
    if args.translate:
        translate_source(args.translate, config_file)
    if not any([args.add, args.build is not None, args.delete, args.fetch is not None,
                args.config is not None, args.sssom is not None, args.report, args.search,
                args.translate]):
        print("No action taken. Use -a to add sources, -b to build schema.yaml, -c to update harvester_config.yaml, -d to delete sources, -f to fetch sources, -r to report on all sources, -s to apply SSSOM mappings, --search to search sources, or --translate SOURCE_KEY to generate translations.")


if __name__ == "__main__":
    main()
