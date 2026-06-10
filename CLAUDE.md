# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

`term_harvester.py` is a CLI tool that fetches controlled vocabulary from heterogeneous sources (ontologies, SKOS vocabularies, HTML pages, PDFs, CSVs, REST APIs) and assembles them into a LinkML `schema.yaml` for use with DataHarmonizer. It must be run from within the target project folder (the folder where `schema.yaml` and `harvester_config.yaml` will live), and the script referenced by path if not on `$PATH`.

## Common commands

```bash
# Add a new source (auto-detects type, downloads, adds entry to harvester_config.yaml)
python term_harvester.py -a https://example.org/some-valueset.json

# Process source files into sources/*.yaml (also fills prefix dicts in harvester_config.yaml)
python term_harvester.py -c

# Build/update schema.yaml from all configured sources
python term_harvester.py -b

# Full refresh: re-download all sources, process, and rebuild schema.yaml
python term_harvester.py -f all -c -b

# Expand reachable_from.source_nodes hierarchies via API (run after -b)
python term_harvester.py -l

# Enum report (space-padded; add -t for TSV)
python term_harvester.py -r
```

Optional heavy dependencies (not installed by default):
- `owlready2` — required for OWL source type (`pip install owlready2`)
- `pypdf` — required for NASIS and NRCS PDF source types (`pip install pypdf`)

## Architecture

### Execution model

`term_harvester.py` is the sole entry point. It imports all `sources/source_*.py` modules at startup (path is inserted dynamically at line 172). There is no package `__init__.py`; modules are loaded via `sys.path` manipulation.

The pipeline has four distinct phases, each triggered by a CLI flag:
1. **`-a` (add)** — detect source type from URL or content, download, create `harvester_config.yaml` entry, run initial processing.
2. **`-f` (fetch)** — re-download source files for entries already in `harvester_config.yaml`.
3. **`-c` (config/process)** — parse each fetched source file and write `sources/{key}.yaml` (a per-source LinkML fragment), storing the resulting prefix dict back into `harvester_config.yaml`.
4. **`-b` (build)** — merge all `sources/{key}.yaml` fragments into `schema.yaml`, applying `minus`/`include`/`concise` filters from `harvester_config.yaml`. Flags orphaned enums rather than deleting them.

### Key data files (runtime, not in this repo)

- **`harvester_config.yaml`** — the central registry; one entry per source with `content_type`, `file_format`, `url`, `version`, `download_date`, `prefix_dict`, and optional `minus`/`include`/`concise`/`apis` blocks.
- **`sources/{key}.yaml`** — intermediate LinkML YAML per source, produced by `-c`.
- **`schema.yaml`** — the assembled output consumed by DataHarmonizer.

### Source module responsibilities

Each `sources/source_*.py` exposes a `match_*` detection function and a `process_*_source` function. `term_harvester.py` calls `match_*` functions in order to identify the source type during `-a`, then calls the corresponding `process_*` during `-c`.

| Module | Source types handled |
|---|---|
| `source_utils.py` | Shared utilities: `fetch_html`, `write_config`, `add_permissible_value`, `make_source_entry`, YAML output via `IndentedDumper` |
| `source_linkml.py` | `LinkML` — YAML schemas with `enums` or `id` key |
| `source_owl.py` | `OWL` — `.owl`/`.rdf`/`.ttl`/`.n3`/`.ofn` files via owlready2 |
| `source_ontologyapi.py` | `OntologyAPI` — OLS4, BioPortal, AGROVOC graph fetching; also handles SNOMED and bare CURIEs |
| `source_agrovoc.py` | AGROVOC SPARQL endpoint |
| `source_loinc.py` | `LOINCCodeSystem`, `LOINCValueSet`, `LOINC` (HL7 listing page) |
| `source_nasis.py` | `NASIS` — USDA NRCS PDF (requires pypdf) |
| `source_nrcs.py` | `NRCSSoilFieldBook` — USDA NRCS Field Book PDF (requires pypdf) |
| `source_nsdb.py` | `NSDB`, `NSDBSNT`, `NSDBSLT`, `NSDBSLC` — Canadian National Soil DataBase HTML |
| `source_statscan.py` | `STATSCAN` — Statistics Canada classification pages |
| `source_statscan_table.py` | `STATSCANTable` — Statistics Canada Census Dictionary table pages; auto-fetches FR from `index-fra.cfm` |
| `source_iso_country.py` | `ISO_COUNTRY` — ISO 3166-2 country subdivision codes via Wikidata SPARQL (P300 prefix filter); stores `wd:Q…` as PV meaning; ISO OBP is a Vaadin SPA not directly fetchable |
| `source_napcscanada.py` | `NAPCSCanada` — NAPCS Canada CSV |
| `source_agrifoodca.py` | `AgriFoodCA` — GitHub directory or individual CSV picklists |
| `source_credit.py` | Attribution/credit metadata utilities |
| `source_freetext.py` | `FreeText` — Claude API enum extraction from free text (`freetext` extra) |

### `-b` build filtering (minus/include/concise)

Filters are applied per-source during `-b` in this order:
1. `minus.concepts` / `minus.permissible_values` / `minus.status` — exclusion pass
2. `include.concepts` / `include.permissible_values` — restoration pass (or whitelist mode when no `minus` is present)
3. `concise: true` — drops `status: obsolete` PVs; for `NAPCSCanada` also deduplicates hierarchy nodes whose title matches their parent's.

`minus.status` (e.g. `status: [DEPRECATED]`) only applies to OWL and OntologyAPI content types.

### API routing for `-l` (reachable_from expansion)

The `apis` block in `harvester_config.yaml` maps CURIE prefixes to REST or SPARQL endpoints. OLS4 is the default fallback. BioPortal requires an `apikey`. OLS4 IRI bases are auto-detected per ontology via a metadata call to `/api/ontologies/{ontology}` and cached per session; override with an explicit `iri_base` key if needed.
