# Term Harvester

**Authors:** Damion Dooley and Claude (Anthropic claude-sonnet-4-6)

Abstract

Data specifications need to draw upon established or defacto-standards for measurement and metadata variable content, including picklist choices.  There are many online sources of controlled vocabulary that can go into data specifications, ranging from ontologies to SKOS vocabularies like AGROVOC, terminology portals like BioPortal or EMBL-EBI Ontology Lookup Service, and even web pages like STATSCAN or pdf documents from USDA.  

Organizing and keeping up-to-date controlled vocabulary from all these sources is quite a challenge.  The term_harvester.py script and its library of /sources/ fetch-and-parse scripts enable retrieval of known-good vocabulary according to a configuration file for use by a particular project or more broadly, by an agency.

Briefly, we recognize that:
 - Each source can contain one or more variables and their picklist (categorical) choice specifications. 
   - Some picklists are hierarchical. 
   - Some are so large that a more dynamic API driven system for retrieving branches is required.
 - We need the ability to download the resource as a whole, or just by selected variables.
   - A configuration file holds the source URL of the resource, the format the resource is in, its version and download date.
   - A resource can be downloaded again (as for example happens when different agencies run the script to keep their own infrastructures up to date over time.)
 - We need a first pass to convert the relevant parts of these resources into a common format for detailing variables and picklists (enumerations).
 - We use LinkML as the format for storing the variables.
 - From this locally stored shared format, we can then use the configuration file to retrieve or omit certain branches of vocabulary, also taking into account term status (e.g. skip deprecated terms).

The term_harvester.py script fetches vocabulary sources, processes them into LinkML enum YAML files, and assembles them into a `schema.yaml` suitable for use with DataHarmonizer.

---

## Table of Contents

- [Quick-start workflow](#quick-start-workflow)
- [Command reference](#command-reference)
  - [`-a` — Add a source from a URL](#-a--add-a-source-from-a-url)
  - [`-f` — Fetch (download) sources](#-f--fetch-download-sources)
  - [`-c` — Process sources](#-c--process-sources-update-prefix-dicts)
  - [`-b` — Build schema.yaml](#-b--build-schemayaml)
  - [`-l` — Expand reachable_from source nodes via API](#-l--expand-reachable_from-source-nodes-via-api)
  - [`-r` — Enum report](#-r--enum-report)
  - [`--free_text` — Extract from free text](#--free_text--extract-from-free-text)
  - [`-i` — Specify configuration file](#-i--specify-configuration-file)
  - [`--search` — Search for terms](#--search--search-for-terms)
- [Supported source types and auto-detection](#supported-source-types-and-auto-detection)
- [Example `-a` invocations by source type](#example--a-invocations-by-source-type)
  - [SNOMED CT (via OLS4)](#snomed-ct-via-ols4)
  - [OBO ontology terms (ENVO, GO, UBERON, …)](#obo-ontology-terms-envo-go-uberon-)
  - [AGROVOC](#agrovoc)
  - [LinkML](#linkml)
  - [LOINC CodeSystems and ValueSets](#loinc-codesystems-and-valuesets)
  - [OWL ontologies](#owl-ontologies)
  - [NSDB (National Soil DataBase)](#nsdb-national-soil-database)
  - [Statistics Canada](#statistics-canada)
  - [Statistics Canada Census Dictionary tables](#statistics-canada-census-dictionary-tables-statscan_table)
  - [ISO 3166-2 country subdivisions](#iso-3166-2-country-subdivisions-iso_country)
  - [NASIS (USDA NRCS National Soil Information System)](#nasis-usda-nrcs-national-soil-information-system)
  - [NRCS Field Book (field description enumerations)](#nrcs-field-book-field-description-enumerations)
  - [NAPCS Canada](#napcs-canada)
  - [Library of Congress Classification](#library-of-congress-classification-loc_classification)
  - [CRediT (Contributor Roles Taxonomy)](#credit-contributor-roles-taxonomy)
  - [AgriFoodCA picklists](#agrifoodca-picklists)
  - [Codex Alimentarius (GSFA food additives)](#codex-alimentarius-gsfa-food-additives-codex)
  - [E Numbers (Wikidata P628)](#e-numbers-wikidata-p628-e_number)
  - [CANSIS Glossary of Terms in Soil Science](#cansis-glossary-of-terms-in-soil-science-cansis_glossary)
  - [FreeText](#freetext)
- [FreeText source type](#freetext-source-type)
- [Term Search](#term-search)
  - [Basic usage](#basic-usage)
  - [Structured queries (term:description)](#structured-queries-termdescription)
  - [How matching works](#how-matching-works)
  - [`--ai` flag: synonym expansion and AI re-scoring](#--ai-flag-synonym-expansion-and-ai-re-scoring)
  - [Reading the Markdown output](#reading-the-markdown-output)
- [`harvester_config.yaml` — source entry structure](#harvester_configyaml--source-entry-structure)
  - [`minus` — exclude content during `-b` build](#minus--exclude-content-during--b-build)
  - [`include` — restore excluded content, or whitelist-only mode](#include--restore-excluded-content-or-whitelist-only-mode)
  - [`rank` — auto-detected permissible value ordering](#rank--auto-detected-permissible-value-ordering)
  - [`sorted` — alphabetical ordering of unranked permissible values](#sorted--alphabetical-ordering-of-unranked-permissible-values)
  - [`concise` — trim redundant hierarchy nodes during `-b`](#concise--trim-redundant-hierarchy-nodes-during--b)
- [`minus` / `include` processing](#minus--include-processing--which-attributes-apply-to-which-content-types)
- [API configuration](#api-configuration)
  - [OLS4 IRI base auto-detection](#ols4-iri-base-auto-detection)
- [OntologyAPI metadata population](#ontologyapi-metadata-population)
- [SSSOM ontology mappings](#sssom-ontology-mappings)
- [Python code notes](#python-code-notes)
  - [Installation](#installation)
  - [API key setup](#api-key-setup)
  - [Module structure](#module-structure)

---

## Quick-start workflow

**Note that some terminals access python as "python3". ALSO, term_harvester.py needs to be run within the context of the folder you want to generate schema.yaml in. If term_harvester.py location is not set in the shell environment then you will need to reference it using a relative path to the DataHarmonizer script/ folder, e.g. `python ../../../script/menu_manager/term_harvester.py`.**

Install dependencies before first use — see [Python code notes](#python-code-notes).

**The default configuration file is `harvester_config.yaml` in the current directory.  Use `-i`/`--input` to specify a different path.**

```bash
# 1. Add sources (auto-detects type, downloads, adds to harvester_config.yaml)
python term_harvester.py -a https://example.org/some-valueset.json

# 2. Process sources into sources/*.yaml (fills prefix dicts into harvester_config.yaml)
python term_harvester.py -c

# 3. Build schema.yaml (syncs enums and prefixes from all sources)
python term_harvester.py -b

# Full refresh in one line:
python term_harvester.py -f all -c -b
```

One strategy with a new menu management installation is to pick a few libraries that you know contain enumerations / picklists useful in a project.  So for example, soil:
python term_harvester.py -a https://example.org/some-valueset.json


---

## Command reference

### `-a` — Add a source from a URL

Auto-detects the source type, downloads the file, adds an entry to
`harvester_config.yaml`, and runs initial processing.

```bash
python term_harvester.py -a https://example.org/some-valueset.json
```

Combine with `--free_text` to extract a picklist from prose rather than a
structured file (see [`--free_text`](#--free_text--extract-from-free-text) below).

---

### `-f` — Fetch (download) sources

Re-download source files for sources already in `harvester_config.yaml`.

```bash
python term_harvester.py -f all          # fetch every source in harvester_config.yaml
python term_harvester.py -f KEY1 KEY2    # fetch only the named source(s)
python term_harvester.py -c KEY -f       # fetch only the source(s) listed with -c
python term_harvester.py -f              # no-op; prints reminder to use -f all or -c
```

---

### `-c` — Process sources (update prefix dicts)

```bash
python term_harvester.py -c                   # process all sources
python term_harvester.py -c linkml_valuesets  # process one source by key
```

Reads each fetched source file, generates or updates `sources/{key}.yaml`, and
stores the resulting prefix dict in `harvester_config.yaml`.

---

### `-b` — Build schema.yaml

Create or update `schema.yaml` with the LinkML top-level structure, enums, and
prefixes drawn from all sources in `harvester_config.yaml`.

```bash
python term_harvester.py -b
```

On creation, populates default values.  On update, only adds missing keys —
existing values are preserved.  Syncs enums from each source's YAML file and
syncs prefixes from all sources stored in `harvester_config.yaml`.

When `-b` detects that an enum present in `schema.yaml` is no longer in its
source file, it reports the enum key rather than deleting it automatically.
This gives the operator the opportunity to manually review whether the menu
item should be removed or retained.

---

### `-l` — Expand `reachable_from` source nodes via API

Always operates on `schema.yaml`; run after `-b`.

```bash
python term_harvester.py -l                              # expand all enums with reachable_from.source_nodes
python term_harvester.py -l linkml_valuesets             # expand enums imported_from a named source
python term_harvester.py -l MyBiomeEnum                  # expand one enum by name
python term_harvester.py -l linkml_valuesets MyBiomeEnum # mix source key and enum name
```

The API used for each ontology prefix is determined by the `apis` block in
`harvester_config.yaml` (see [API configuration](#api-configuration) below).
OLS4 is the default fallback.  BioPortal requires an `apikey` under
`apis > bioportal > type > rest > apikey`.

> **Known limitation:** `-l` currently assumes `subClassOf` object property
> traversal when expanding `reachable_from.source_nodes`.  Dynamic enumeration
> of other relationship types (e.g. `partOf`, `hasPart`) has not yet been
> implemented.

---

### `-r` — Enum report

```bash
python term_harvester.py -r     # space-padded output
python term_harvester.py -r -t  # tab-delimited output
```

Generates a report of enum keys, titles, `source_domain`, and `source_schema`
for all sources in `harvester_config.yaml`.  Can be run at any stage to inspect what
is configured.

---

### `--free_text` — Extract from free text

Used with `-a` to extract enumerations from a textual document rather than a structured
source file.  See the dedicated [FreeText source type](#freetext-source-type)
section for full usage, file-input support, and API key setup.

---

### `-i` — Specify configuration file

```bash
python term_harvester.py -i path/to/myconfig.yaml -f all -c -b
python term_harvester.py --input examples/agrifoodca_config.yaml -r
```

Overrides the default configuration file name (`harvester_config.yaml`).  All
commands (`-a`, `-f`, `-c`, `-b`, `-d`, `-l`, `-r`, `-s`) read from and write to
the specified file.  Useful when managing multiple independent schemas from the
same working directory, or when using a pre-built config from the `examples/`
folder as a starting point.

---

### `--search` — Search for terms

Search for terms and enumerations across all sources that have been processed
with `-c`.

```bash
# Plain-text report (default)
python term_harvester.py --input myconfig.yaml --search 'soil acidity'

# Markdown table, redirected to a file
python term_harvester.py --input myconfig.yaml --format markdown --search 'soil acidity' > results.md

# Structured query — text after ':' is a description for API/AI context only
python term_harvester.py --input myconfig.yaml --search 'excess drainage:a soil drainage classification'

# AI-assisted: synonym expansion + AI re-scoring
python term_harvester.py --input myconfig.yaml --format markdown --ai --search 'soil acidity' > results.md
```

Results are ranked by relevance using a local FTS5 full-text index (built
during `-c`) combined with optional API and AI scoring.  See
[Term Search](#term-search) for full details.

---

Check source section for details on command line configuration.

## Supported source types and auto-detection

| Detected as | Description |
|---|---|
| **Agricultural** | |
| [`AgriFoodCA`](#agrifoodca-picklists) | AgriFoodData Canada bilingual (EN/FR) agricultural picklist CSVs; add an entire GitHub directory or a single CSV file.<br>(GitHub directory URL for `agrifooddatacanada/picklists_for_schemas`, pre-download; or CSV with first row matching `,title,description,keywords,source`, content-based) |
| [`AGROVOC`](#agrovoc) → `OntologyAPI` | FAO multilingual agricultural thesaurus covering farming, food, fisheries, and natural resources (~40 000 concepts).<br>(URL matches `aims.fao.org/aos/agrovoc/{id}`, pre-download) |
| [`CANSIS_GLOSSARY`](#cansis-glossary-of-terms-in-soil-science-cansis_glossary) | Agriculture and Agri-Food Canada glossary of soil science terms with English definitions and French translations.<br>(Exact URL `https://sis.agr.gc.ca/cansis/glossary/`, pre-download) |
| [`NAPCSCanada`](#napcs-canada) | North American Product Classification System Canada with a hierarchical product and service code taxonomy.<br>(CSV content with NAPCS-specific column headers) |
| [`NASIS`](#nasis-usda-nrcs-national-soil-information-system) | USDA NRCS National Soil Information System domain tables (~450 enumerations) for all categorical soil survey fields.<br>(URL from `nrcs.usda.gov` containing `NASIS` with `.pdf` extension) |
| [`NSDB`](#nsdb-national-soil-database) | Combined Canadian National Soil DataBase soil name and layer classification tables.<br>(URL matches `sis.agr.gc.ca/cansis/nsdb/soil` prefix) |
| [`NSDBSLC`](#nsdb-national-soil-database) | Canadian National Soil DataBase Soil Landscapes of Canada polygon-level classification data.<br>(URL matches `sis.agr.gc.ca` + `/nsdb/slc/`) |
| [`NSDBSLT`](#nsdb-national-soil-database) | Canadian National Soil DataBase Soil Layer Table with horizon-level soil property classifications.<br>(URL contains `/slt/` under the NSDB soil domain) |
| [`NSDBSNT`](#nsdb-national-soil-database) | Canadian National Soil DataBase Soil Name Table with soil series classification and profile data.<br>(URL contains `/snt/` under the NSDB soil domain) |
| **Human and animal health** | |
| [`CODEX`](#codex-alimentarius-gsfa-food-additives-codex) | FAO/WHO Codex General Standard for Food Additives listing all internationally permitted additives by food category.<br>(URL from `fao.org/gsfaonline/docs/` or `fao.org/input/download/standards/4/CXS_192`, pre-download) |
| [`E_NUMBER`](#e-numbers-wikidata-p628-e_number) | EU/UK food additive E numbers with functional-use annotations, organised by numeric range category, sourced from Wikidata.<br>(URL matches `wikidata.org/wiki/Q207810` or Wikidata SPARQL URL containing `P628`, pre-download) |
| [`LOINC`](#loinc-codesystems-and-valuesets) | HL7 LOINC listing page covering all available clinical observation, lab test, and measurement code systems and value sets.<br>(URL from `terminology.hl7.org` with `.html` extension — listing page, not a single ValueSet/CodeSystem detail) |
| [`LOINCCodeSystem`](#loinc-codesystems-and-valuesets) | A single HL7 LOINC clinical terminology code system in FHIR JSON format.<br>(`.json` file with `resourceType: CodeSystem`) |
| [`LOINCValueSet`](#loinc-codesystems-and-valuesets) | A curated HL7 LOINC subset of clinical codes for a specific use case, in FHIR JSON format.<br>(`.json` file with `resourceType: ValueSet`) |
| [`SNOMED CT`](#snomed-ct-via-ols4) → `OntologyAPI` | Comprehensive clinical health terminology covering diseases, procedures, findings, and anatomy.<br>(URL matches `snomed.info/id/{conceptId}`, pre-download) |
| **General** | |
| [`CRediT`](#credit-contributor-roles-taxonomy) | 14-role contributor roles taxonomy for scholarly output attribution, published on Zenodo as a PDF.<br>(URL from `zenodo.org/records/{id}` — bare record URL containing "credit", or a `/files/` path containing "credit" and ".pdf") |
| [`ISO_COUNTRY`](#iso-3166-2-country-subdivisions-iso_country) | ISO 3166-2 first-level country subdivisions (provinces, states, territories) fetched via Wikidata SPARQL.<br>(URL from `iso.org/obp/ui/#iso:code:3166:` followed by a 2-letter country code) |
| [`LinkML`](#linkml) | LinkML YAML schema containing one or more named enumerations with permissible values.<br>(`.yaml`/`.yml` file that is a dict containing `enums` or `id`) |
| [`LOC_CLASSIFICATION`](#library-of-congress-classification-loc_classification) | Library of Congress subject heading hierarchy spanning all academic disciplines, useful for general topic or domain picklists.<br>(Exact URL `https://www.loc.gov/catdir/cpso/lcco/`) |
| [OBO terms](#obo-ontology-terms-envo-go-uberon-) → `OntologyAPI` | Open Biological and Biomedical Ontologies covering environments, gene functions, anatomical structures, and more.<br>(Bare CURIE `ENVO:00010483`, OBO shorthand `ENVO_00010483`, or OBO IRI `http://purl.obolibrary.org/obo/ENVO_00010483`; pre-download, routed to configured API or OLS4) |
| [`OWL`](#owl-ontologies) | Web Ontology Language file defining a class hierarchy with properties and formal logical relationships.<br>(URL extension `.owl`, `.ofn`, `.rdf`, `.ttl`, `.n3`; or file contains RDF/OWL content markers) |
| [`STATSCAN`](#statistics-canada) | Statistics Canada classification variable with hierarchical codes from the IMDB concepts portal.<br>(URL from `statcan.gc.ca` containing `p3VD.pl` and `Function=getVD`) |
| [`STATSCAN_TABLE`](#statistics-canada-census-dictionary-tables-statscan_table) | Statistics Canada Census Dictionary reference table such as province/territory abbreviations.<br>(URL from `www12.statcan.gc.ca/.../ref/dict/tab/index-eng.cfm?ID=`) |

---

## Example `-a` invocations by source type

### SNOMED CT (via OLS4)

```bash
# Heart disease — concept IRI detected pre-download; no file saved
python term_harvester.py -a http://snomed.info/id/56265001

# Then expand the hierarchy via OLS4 and build schema.yaml:
python term_harvester.py -l SNOMED56265001
python term_harvester.py -b
```

`SNOMED` must appear in the `ols` api's `ontologies` list in `harvester_config.yaml`
(see [API configuration](#api-configuration)).  If it is absent, `fetch_api_graph`
still falls back to OLS4 and auto-detects the `http://snomed.info/id/` IRI base
from OLS4 ontology metadata — no explicit `iri_base` key required.

### OBO ontology terms (ENVO, GO, UBERON, …)

Pass either a bare CURIE or a full OBO IRI.  The prefix is looked up in the
`apis` block of `harvester_config.yaml` to choose the API (defaults to OLS4).  The
term label and description are fetched immediately; hierarchy expansion runs
separately with `-l`.

```bash
# OBO shorthand (underscore + numeric ID)
python term_harvester.py -a ENVO_00010483
python term_harvester.py -l ENVO_00010483

# CURIE form (colon separator)
python term_harvester.py -a ENVO:00010483
python term_harvester.py -l ENVO_00010483

# OBO IRI form
python term_harvester.py -a http://purl.obolibrary.org/obo/ENVO_00010483
python term_harvester.py -l ENVO_00010483

# Ontology whose prefix is listed under bioportal in harvester_config.yaml
# will be routed to BioPortal automatically
python term_harvester.py -a MESH:D001234
python term_harvester.py -l MESH_D001234
```

The generated source key is always `{PREFIX}_{localID}` (e.g. `ENVO_00010483`).

### AGROVOC

```bash
python term_harvester.py -a https://aims.fao.org/aos/agrovoc/c_26950
python term_harvester.py -l AGROVOC_c_26950
```

### LinkML

LinkML "ValueSets" are a rich source of various kinds of research dataset fields and metadata.

```bash
python term_harvester.py -a https://raw.githubusercontent.com/linkml/valuesets/refs/heads/main/src/valuesets/merged/merged_hierarchy.yaml
```

### LOINC CodeSystems and ValueSets

```bash
# LOINCDataAbsentReason (LOINCCodeSystem)
python term_harvester.py -a https://terminology.hl7.org/7.1.0/en/CodeSystem-data-absent-reason.json

# LOINCPersonalPronouns (LOINCValueSet)
python term_harvester.py -a https://terminology.hl7.org/7.1.0/en/ValueSet-pronouns.json

# LOINCGenderIdentity (LOINCValueSet)
python term_harvester.py -a https://terminology.hl7.org/en/ValueSet-gender-identity.json

# LOINC valueset listing page (run -c after to fetch all enums)
python term_harvester.py -a https://terminology.hl7.org/en/valuesets.html
```

### OWL ontologies

```bash
python term_harvester.py -a https://purl.obolibrary.org/obo/envo.owl
```

### NSDB (National Soil DataBase)

```bash
# NSDBSoilNameAndLayerV2 (NSDB) — combined Soil Name Table + Soil Layer Table
python term_harvester.py -a https://sis.agr.gc.ca/cansis/nsdb/soil/v2/index.html

# NSDBSNTv2 (NSDBSNT) — Soil Name Table
python term_harvester.py -a https://sis.agr.gc.ca/cansis/nsdb/soil/v2/snt/index.html

# NSDBSLTv2 (NSDBSLT) — Soil Layer Table
python term_harvester.py -a https://sis.agr.gc.ca/cansis/nsdb/soil/v2/slt/index.html

# NSDBSLCv3_2 (NSDBSLC) — Soil Landscapes of Canada
python term_harvester.py -a https://sis.agr.gc.ca/cansis/nsdb/slc/v3.2/index.html
```

### Statistics Canada

Get the variable page URI from https://www.statcan.gc.ca/en/concepts/search by
clicking on a variable name.

```bash
python term_harvester.py -a "https://www23.statcan.gc.ca/imdb/p3VD.pl?Function=getVD&TVD=1368814"
python term_harvester.py -c STATSCAN_1441857
```

### Statistics Canada Census Dictionary tables (STATSCAN_TABLE)

Census Dictionary reference tables from Statistics Canada's 2021 Census (and
other years), e.g. province/territory abbreviation tables.

```bash
# Table 1.8 — Abbreviations and codes for provinces and territories
python term_harvester.py -a "https://www12.statcan.gc.ca/census-recensement/2021/ref/dict/tab/index-eng.cfm?ID=T1_8"
python term_harvester.py -c STATSCAN_TABLE_T1_8
python term_harvester.py -b
```

The source key is `STATSCAN_TABLE_{ID}` (e.g. `STATSCAN_TABLE_T1_8`).  The
enum key and title are derived from the first column header of the table (the
row-stub label, e.g. `ProvinceTerritory` / `Province/Territory`).  The alpha
code column (header containing "alpha") is used as the permissible value key;
if absent, the row-stub text is slugified.  Both English and French pages are
fetched automatically; French names appear in the locale extension.

---

### ISO 3166-2 country subdivisions (ISO_COUNTRY)

Provinces, states, territories, and other first-level subdivisions for any
country listed in ISO 3166-2.  Use the ISO Online Browsing Platform URL as the
source identifier.

```bash
# Canada — provinces and territories (English + French)
python term_harvester.py -a "https://www.iso.org/obp/ui/#iso:code:3166:CA"
python term_harvester.py -c ISO_COUNTRY_CA
python term_harvester.py -b
```

Because the ISO OBP page is a JavaScript application, data is sourced from the
[Wikidata Query Service](https://query.wikidata.org/) via SPARQL.  The alpha-2
code extracted from the ISO OBP URL (e.g. `CA`) drives two queries:

1. **P297** (ISO 3166-1 alpha-2) — resolves the country's English name, used
   as the enum key (e.g. `Canada`).
2. **P300** (ISO 3166-2 code) filtered by the alpha-2 prefix — fetches all
   subdivisions with `rdfs:label` for every project locale listed in
   `harvester_config.yaml`.

The enum itself receives a `description:` drawn from Wikidata's `schema:description`
for the country entity (e.g. *"country in North America"* for Canada).

Each permissible value carries:
- `meaning: wd:Q…` — the Wikidata QID (e.g. `wd:Q1951` for Alberta), a stable
  semantic anchor that links to the full Wikidata entity.
- `exact_mappings: [iso:CA-AB]` — the canonical ISO 3166-2 code as a CURIE.

Both `wd:` and `iso:` prefixes are written to the source YAML and to the config
entry's `prefixes:` block so that `-b` can sync them into `schema.yaml`.
Results are cached in `sources/{key}.json`; the ISO OBP URL is retained in
`source_ontology` as the canonical citation.

The suffix of each 3166-2 code (e.g. `AB` from `CA-AB`) becomes the
permissible value key.

**Config entry produced:**

```yaml
ISO_COUNTRY_CA:
  title: Canada
  name: ISO_COUNTRY_CA
  content_type: ISO_COUNTRY
  file_format: json
  reachable_from:
    source_ontology: https://www.iso.org/obp/ui/#iso:code:3166:CA
  download_date: '2026-06-09'
  prefixes:
    iso: https://www.iso.org/iso-3166-country-codes/
    wd: http://www.wikidata.org/entity/
```

**Enum in `sources/ISO_COUNTRY_CA.yaml`:**

```yaml
prefixes:
  iso: https://www.iso.org/iso-3166-country-codes/
  wd: http://www.wikidata.org/entity/
enums:
  Canada:
    name: Canada
    title: Canada
    description: country in North America
    permissible_values:
      AB:
        title: Alberta
        meaning: wd:Q1951
        exact_mappings:
        - iso:CA-AB
      BC:
        title: British Columbia
        meaning: wd:Q1974
        exact_mappings:
        - iso:CA-BC
      MB:
        title: Manitoba
        meaning: wd:Q1948
        exact_mappings:
        - iso:CA-MB
      # … remaining provinces and territories …
extensions:
  fr:
    enums:
      Canada:
        permissible_values:
          AB: {title: Alberta}
          BC: {title: Colombie-Britannique}
          MB: {title: Manitoba}
          NB: {title: Nouveau-Brunswick}
          NL: {title: Terre-Neuve-et-Labrador}
          NS: {title: Nouvelle-Écosse}
          NT: {title: Territoires du Nord-Ouest}
          NU: {title: Nunavut}
          ON: {title: Ontario}
          PE: {title: Île-du-Prince-Édouard}
          QC: {title: Québec}
          SK: {title: Saskatchewan}
          YT: {title: Yukon}
```

To refresh the data when Wikidata updates:

```bash
python term_harvester.py -f ISO_COUNTRY_CA   # re-queries Wikidata SPARQL, updates sources/ISO_COUNTRY_CA.json
python term_harvester.py -c ISO_COUNTRY_CA   # regenerates sources/ISO_COUNTRY_CA.yaml
python term_harvester.py -b                  # rebuilds schema.yaml, syncs wd: and iso: prefixes
```

---

### NASIS (USDA NRCS National Soil Information System)

NASIS publishes all domain tables (controlled vocabularies for every categorical
field in the NASIS soil survey database) as a single PDF.  Each domain becomes
one LinkML enum.

```bash
python term_harvester.py -a "https://www.nrcs.usda.gov/sites/default/files/2025-07/NASIS%207.4.3%20Domains.pdf"
python term_harvester.py -b
```

The URL is auto-detected as NASIS: the PDF is saved to `sources/NASIS.pdf`, a
source entry (with `concise: true`) is added to `harvester_config.yaml`, and
`process_nasis_source` runs immediately.  To update to a newer release, remove
the `NASIS` key from `harvester_config.yaml` first and re-run `-a` with the new URL.

To process a manually placed PDF (already at `sources/NASIS.pdf`) without
re-downloading:

```bash
python term_harvester.py -c NASIS     # writes sources/NASIS.yaml
python term_harvester.py -b           # adds NASIS enums to schema.yaml
```

The parser extracts 453 enums from the 906-page PDF.  `concise: true` drops
the ~1,950 permissible values marked obsolete in the PDF at `-b` build time.

---

### NRCS Field Book (field description enumerations)

The USDA NRCS *Field Book for Describing and Sampling Soils* (Ver. 4, November 2024)
defines the categorical vocabularies used when recording soil observations.
`source_nrcs.py` parses selected tables from the PDF into LinkML enums.

Auto-detection via `-a` is not yet implemented for this source.  Add it to
`harvester_config.yaml` manually, then run `-c` and `-b`:

```yaml
# harvester_config.yaml — add this entry under sources:
NRCSSoilFieldBook:
  title: NRCS Field Book for Describing and Sampling Soils Ver. 4
  content_type: NRCSSoilFieldBook
  file_format: pdf
  version: "Ver. 4 November 2024"
  reachable_from:
    source_ontology: https://www.nrcs.usda.gov/sites/default/files/2025-05/Field-Book-for-Describing-and-Sampling-Soils-Ver4.pdf
```

```bash
# Download the PDF and generate sources/NRCSSoilFieldBook.yaml
python term_harvester.py -c NRCSSoilFieldBook

# Add enums to schema.yaml
python term_harvester.py -b
```

Each enum's `see_also` field is set to a deep PDF link (`{url}#page=N`) that
opens directly to the source table.

#### Available enumerations (35)

| Enum key | Title | PDF page(s) |
|---|---|---|
| `NRCSSoilFieldBook_WeatherConditions` | Weather Conditions | 19 |
| `NRCSSoilFieldBook_DrainagePattern` | Drainage Pattern | 27–28 |
| `NRCSSoilFieldBook_DrainageClass` | Drainage Class | 28 |
| `NRCSSoilFieldBook_FloodingFrequency` | Flooding Frequency | 30 |
| `NRCSSoilFieldBook_PondingFrequency` | Ponding Frequency | 31 |
| `NRCSSoilFieldBook_BedrockWeatheringClass` | Bedrock Weathering Class | 42 |
| `NRCSSoilFieldBook_HorizonBoundaryDistinctness` | Horizon Boundary Distinctness | 56 |
| `NRCSSoilFieldBook_HorizonBoundaryTopography` | Horizon Boundary Topography | 57 |
| `NRCSSoilFieldBook_ColorMoistureState` | Color Moisture State | 59 |
| `NRCSSoilFieldBook_ColorLocationCondition` | Color Location or Condition | 59 |
| `NRCSSoilFieldBook_ConcentrationShape` | Concentration Shape | 74 |
| `NRCSSoilFieldBook_ConcentrationLocation` | Concentration Location | 75–76 |
| `NRCSSoilFieldBook_ConcentrationBoundary` | Concentration Boundary | 76 |
| `NRCSSoilFieldBook_FragmentRoundness` | Fragment Roundness | 99 |
| `NRCSSoilFieldBook_ArtifactShape` | Artifact Shape | 101 |
| `NRCSSoilFieldBook_ArtifactRoundness` | Artifact Roundness | 101 |
| `NRCSSoilFieldBook_ArtifactCohesion` | Artifact Cohesion | 102 |
| `NRCSSoilFieldBook_ArtifactPenetrability` | Artifact Penetrability | 102 |
| `NRCSSoilFieldBook_ArtifactPersistence` | Artifact Persistence | 102 |
| `NRCSSoilFieldBook_ArtifactSafety` | Artifact Safety | 102 |
| `NRCSSoilFieldBook_StructureGrade` | Structure Grade | 105 |
| `NRCSSoilFieldBook_SurfaceCrustRuptureResistance` | Surface Crust Rupture Resistance | 115 |
| `NRCSSoilFieldBook_CementingAgentKind` | Cementing Agent Kind | 115 |
| `NRCSSoilFieldBook_PenetrationResistanceClass` | Penetration Resistance Class | 119 |
| `NRCSSoilFieldBook_PenetrationOrientation` | Penetration Orientation | 119 |
| `NRCSSoilFieldBook_ExcavationDifficulty` | Excavation Difficulty | 119–120 |
| `NRCSSoilFieldBook_PoreShape` | Pore Shape | 125 |
| `NRCSSoilFieldBook_SoilCrustsKind` | Soil Crusts Kind | 131 |
| `NRCSSoilFieldBook_PermeabilityClass` | Permeability Class | 137 |
| `NRCSSoilFieldBook_ReactionPH` | Reaction (pH) | 137–138 |
| `NRCSSoilFieldBook_EffervescenceClass` | Effervescence Class | 139 |
| `NRCSSoilFieldBook_SalinityClass` | Salinity Class | 141 |
| `NRCSSoilFieldBook_OdorKind` | Odor Kind | 142 |
| `NRCSSoilFieldBook_OdorIntensity` | Odor Intensity | 142 |
| `NRCSSoilFieldBook_ObservationMethod` | Observation Method | 154 |

#### To-do — enumerations requiring specialized parsers (18)

The generic `_parse_generic` table parser covers standard two- and three-column
layouts.  The following enumerations need hand-written parsers in
`sources/source_nrcs.py` due to unusual PDF layout.

**Alternative: extract via FreeText.**  Because these tables exist in a PDF that
is already downloaded locally, the `FreeText` source type with a PDF file input
is a practical workaround while the dedicated parsers are pending.  Point
`--free_text` at the relevant page range excerpt or supply the table text
inline.  Example for the three Manner of Failure tables on page 116:

```bash
# Step 1 — add a FreeText source citing the Field Book PDF
python term_harvester.py \
  -a "https://www.nrcs.usda.gov/sites/default/files/2025-05/Field-Book-for-Describing-and-Sampling-Soils-Ver4.pdf#page=116" \
  --free_text "Manner of Failure classifications for soil consistence: brittleness, fluidity, and smeariness scales from the NRCS Field Book Ver. 4 page 116"

# Step 2 — if the initial extraction used Claude's knowledge rather than the PDF,
# download the PDF and re-extract from it:
python term_harvester.py -f MannerOfFailure   # downloads PDF to sources/MannerOfFailure.pdf
python term_harvester.py -c MannerOfFailure   # re-extracts from actual PDF text

# Step 3 — build schema.yaml
python term_harvester.py -b
```

The source key is derived from the `--free_text` topic string (e.g.
`"Manner Of Failure Brittleness"` → `MannerOfFailureBrittleness`).  Review the extracted YAML in
`sources/{key}.yaml` — Claude may need the text pasted inline for tables whose
pypdf extraction is fragmented.  To supply text inline, paste the relevant table
rows directly as the `--free_text` argument instead of a topic string:

```bash
python term_harvester.py \
  -a "https://www.nrcs.usda.gov/sites/default/files/2025-05/Field-Book-for-Describing-and-Sampling-Soils-Ver4.pdf#page=116" \
  --free_text "Manner of Failure – Brittleness: Non-brittle NB, Slightly brittle SB, Brittle B"
```

| Enum key | Title | Pages | Parsing challenge |
|---|---|---|---|
| `NRCSSoilFieldBook_FloodingDuration` | Flooding Duration | 30 | Dual code columns (Conv. + NASIS); codes differ for "brief" |
| `NRCSSoilFieldBook_PondingDuration` | Ponding Duration | 31 | Dual code columns (Conv. + NASIS) |
| `NRCSSoilFieldBook_ErosionKind` | Erosion Kind | 42 | Hierarchical: `water: —` parent with `sheet/rill/gully/tunnel` sub-entries |
| `NRCSSoilFieldBook_ErosionDegreeClass` | Erosion Degree Class | 42–43 | Numeric codes in `None 0 / 1 1 / 2 2 …` format; table spans page break |
| `NRCSSoilFieldBook_RedoxContrastClass` | Redox Contrast Class | 64 | Criteria expressed as Munsell delta expressions (Δh, Δv, Δc) |
| `NRCSSoilFieldBook_StructureType` | Structure Type | 103–104 | Dual code columns (Conv. + NASIS); spans two pages |
| `NRCSSoilFieldBook_MannerOfFailureBrittleness` | Manner of Failure – Brittleness | 116 | Three sub-tables share one page; sub-section anchoring required |
| `NRCSSoilFieldBook_MannerOfFailureFluidity` | Manner of Failure – Fluidity | 116 | Compound table; title wraps across lines (`moderately \nfluid MF`) |
| `NRCSSoilFieldBook_MannerOfFailureSmeariness` | Manner of Failure – Smeariness | 116 | Compound table; footnote superscripts inside title text |
| `NRCSSoilFieldBook_RootPoreQuantity` | Root and Pore Quantity | 120 | `#` NASIS codes (record actual count); Conv. codes are `1/2/3` but with optional subclasses |
| `NRCSSoilFieldBook_PoreVerticalContinuity` | Pore Vertical Continuity | 125 | Conv. code column is `—` (no formal codes); only NASIS codes present |
| `NRCSSoilFieldBook_CrackKind` | Crack Kind | 128 | Title fragments across 4 lines in PDF column layout |
| `NRCSSoilFieldBook_SpecialFeaturesKind` | Special Features Kind | 132 | Title severely fragmented across multiple lines |
| `NRCSSoilFieldBook_KsatClass` | Ksat Class | 136 | Multi-column table; each cell on its own line in pypdf output |
| `NRCSSoilFieldBook_pHMethod` | pH Method | 138 | Grouped sub-tables; titles include pH ranges; mixed-case method names |
| `NRCSSoilFieldBook_EffervescenceAgent` | Effervescence Agent | 139–140 | Chemical notation (`HCl (1N)`) starts uppercase; grouped sub-headers |
| `NRCSSoilFieldBook_ReducedConditions` | Reduced Conditions | 140 | Row title starts with Greek letter α |
| `NRCSSoilFieldBook_FluidityClass` | Fluidity Class | 160 | Title wraps mid-word across two lines (`moderately \nfluid MF`) |

---

### NAPCS Canada

See https://www.statcan.gc.ca/en/subjects/standard/napcs/2022/index and
https://www.statcan.gc.ca/en/media/5274 for the CSV download.

```bash
# Content auto-detected from CSV headers; year extracted from URL to form the source key
python term_harvester.py -a "https://www.statcan.gc.ca/en/media/5274"
python term_harvester.py -c NAPCSCanada2022
```

### Library of Congress Classification (LOC_CLASSIFICATION)

The [Library of Congress Classification](https://www.loc.gov/catdir/cpso/lcco/) is
a hierarchical system of subject headings used to organise library collections across
all areas of knowledge.  As a broad, well-maintained subject taxonomy it is useful for
picklists that require general topic or discipline selection — for example, a metadata
field for the subject area of a dataset or publication.

The classification is structured in two levels: top-level lettered classes (e.g. `S` —
Agriculture, `Q` — Science, `R` — Medicine) with subclasses beneath them (e.g. `S1-972`
— Agriculture general, `SF1-1100` — Animal culture).  Hierarchy within each subclass
is determined by numeric range containment.  The single enum produced is
`LOCClassification`.

```bash
python term_harvester.py -a "https://www.loc.gov/catdir/cpso/lcco/"
python term_harvester.py -b
```

The source downloads the HTML index page plus approximately 20 class PDFs, extracts
their text immediately via `pypdf`, and stores all text files in
`sources/LOC_CLASSIFICATION.zip`.  The PDFs are not retained after extraction.
The source key is always `LOC_CLASSIFICATION`.

To refresh:

```bash
python term_harvester.py -f LOC_CLASSIFICATION
python term_harvester.py -c LOC_CLASSIFICATION
python term_harvester.py -b
```

---

### CRediT (Contributor Roles Taxonomy)

The [CRediT taxonomy](https://credit.niso.org/) defines 14 standardised roles for
contributors to scholarly output (Conceptualization, Data Curation, Formal
Analysis, …).  The source PDF is published on Zenodo.

```bash
python term_harvester.py -a "https://zenodo.org/records/18421449"
```

Either the bare Zenodo record URL or the full file URL is accepted.  Detection
happens before any download: the URL is matched against `zenodo.org/records/{id}`
and must contain "credit" (bare record URL) or point to a `/files/` path
containing "credit" and ".pdf".

The PDF is fetched via the **Zenodo REST API** (`/api/records/{id}`) rather than
a direct download URL — Zenodo's CDN returns HTTP 403 for scripted clients on
direct file links, but the API endpoint works without authentication.  The
resolved API record URL (`https://zenodo.org/api/records/18421449`) is stored
in `harvester_config.yaml` so subsequent `-f` fetches use the same method.

The PDF is saved to `sources/CRediT.pdf`, processed immediately via `pypdf`, and
`sources/CRediT.yaml` is written in one step — no separate `-c` or `-b` call is
required for the initial add.  Each permissible value carries a `meaning` URI
pointing to the role's canonical page on `credit.niso.org`.

To re-fetch and rebuild from Zenodo:

```bash
python term_harvester.py -f CRediT
python term_harvester.py -b
```

To rebuild from an already-downloaded PDF without re-fetching:

```bash
python term_harvester.py -c CRediT
python term_harvester.py -b
```

---

### AgriFoodCA picklists

Picklist CSV files from https://github.com/agrifooddatacanada/picklists_for_schemas.
Each CSV encodes an enum with bilingual (English/French) labels and optional descriptions.

```bash
# Import all picklists from the GitHub directory in one step (uses GitHub API):
python term_harvester.py -a https://github.com/agrifooddatacanada/picklists_for_schemas/tree/main/picklists

# Or add a single picklist CSV by its raw download URL:
python term_harvester.py -a https://raw.githubusercontent.com/agrifooddatacanada/picklists_for_schemas/main/picklists/soil_drainage.csv

# Re-generate YAML from already-downloaded CSVs:
python term_harvester.py -c AFCSoilDrainage
```

Source keys are derived from filenames: `AFC` + PascalCase(stem), e.g.
`soil_drainage.csv` → `AFCSoilDrainage`, `education_level_stats_can.csv` → `AFCEducationLevelStatsCan`.

When a source document URL is found in the CSV metadata (the `source` column of the
`general` row), it is stored as `see_also` in `harvester_config.yaml`.

---

### Codex Alimentarius (GSFA food additives) (CODEX)

The [Codex General Standard for Food Additives](https://www.fao.org/gsfaonline/)
(CXS 192-1995) is the FAO/WHO international standard governing permitted food
additives.  The source PDF is parsed into three enums per revision year:

| Enum key | Contents |
|---|---|
| `CODEX_{year}_FoodCategories` | Hierarchical food category tree (dotted-decimal codes, e.g. `01.0`, `01.1.1`) |
| `CODEX_{year}_Additives` | Permitted additives from TABLE ONE (INS codes as keys, sorted numerically by INS code) |
| `CODEX_{year}_FOOTNOTES` | Footnote definitions referenced by the permission tables |

```bash
# Add the current revision (URL always points to the latest PDF)
python term_harvester.py -a "https://www.fao.org/gsfaonline/docs/CXS_192e.pdf"

# Or add a specific dated edition:
python term_harvester.py -a "https://www.fao.org/input/download/standards/4/CXS_192_2015e.pdf"

# Rebuild schema.yaml
python term_harvester.py -b
```

The source key is `CODEX_{year}` (e.g. `CODEX_2025`), where the year is extracted
from the "CODEX STAN" or "CXS" revision line in the PDF.  The PDF is extracted
with `pdfplumber` (visual reading order, correct for multi-column tables) and
cached in `sources/{key}.zip` for fast reprocessing.  Requires the `pdfplumber`
package (`pip install pdfplumber`).

To refresh after a new Codex revision is published:

```bash
python term_harvester.py -f CODEX_2025   # re-downloads PDF, re-extracts text
python term_harvester.py -c CODEX_2025   # regenerates sources/CODEX_2025.yaml
python term_harvester.py -b              # rebuilds schema.yaml
```

---

### E Numbers (Wikidata P628) (E_NUMBER)

EU/UK food additive E numbers fetched from Wikidata via SPARQL property P628.
The resulting enum is organised into nine parent permissible values (one per EU
numeric range category) with individual E numbers as children carrying `is_a`
pointing to their range category.

Each permissible value carries:
- `title` — English Wikidata label (e.g. `curcumin`)
- `description` — Wikidata short description and/or P366 "has use" functional-role values collected during fetch
- `is_a` — parent range category key (e.g. `colours`, `preservatives`)
- `meaning: wd:Qxxx` — Wikidata entity CURIE for semantic linking

```bash
# Add the E number source (fetches SPARQL results immediately)
python term_harvester.py -a "https://www.wikidata.org/wiki/Q207810"

# Rebuild schema.yaml
python term_harvester.py -b

# Refresh from Wikidata (re-queries SPARQL, updates sources/E_NUMBER.json)
python term_harvester.py -f E_NUMBER
python term_harvester.py -c E_NUMBER
python term_harvester.py -b
```

The source key is always `E_NUMBER`.  Results are cached in
`sources/E_NUMBER.json`.  The `wd:` prefix is written to the config's
`prefix_dict` and synced into `schema.yaml` by `-b`.

**Parent range categories added to the enum:**

| Key | Title |
|---|---|
| `colours` | Colours (E100-E199) |
| `preservatives` | Preservatives (E200-E299) |
| `antioxidants` | Antioxidants and acidity regulators (E300-E399) |
| `thickeners` | Thickeners, stabilisers and emulsifiers (E400-E499) |
| `acidity_regulators` | Acidity regulators and anti-caking agents (E500-E599) |
| `flavour_enhancers` | Flavour enhancers (E600-E699) |
| `antibiotics` | Antibiotics (E700-E799) |
| `glazing_agents` | Glazing agents, gases and sweeteners (E900-E999) |
| `additional_additives` | Additional additives (E1000-E1599) |

---

### CANSIS Glossary of Terms in Soil Science (CANSIS_GLOSSARY)

The Canadian National Soil DataBase [CANSIS Glossary](https://sis.agr.gc.ca/cansis/glossary/)
contains English and French term/definition pairs for soil science terminology
published by Agriculture and Agri-Food Canada.

```bash
# Add the glossary (downloads all A-Z letter pages, EN and FR)
python term_harvester.py -a "https://sis.agr.gc.ca/cansis/glossary/"

# Rebuild schema.yaml
python term_harvester.py -b
```

The source key is `CANSIS_GLOSSARY`.  All letter pages (A–Z plus numeric
entries) plus four supplementary table pages are downloaded and stored in
`sources/CANSIS_GLOSSARY.zip`.  The `-c` step parses `<dl><dt><dd>` term/
definition pairs from the English pages and writes a flat enum.

**French locale extensions** are handled by a separate utility script
(`sources/source_cansis_translate.py`) that bridges the independent French
glossary via machine translation and fuzzy term matching.  It requires
`pip install deep-translator` and produces output for human review before
applying:

```bash
# Translate FR glossary entries to EN for matching (requires deep-translator)
python sources/source_cansis_translate.py --translate
# Review CANSIS_GLOSSARY_translated_fr.tsv, then apply
python sources/source_cansis_translate.py --apply [--threshold 0.65]
# Re-run -c or -b to incorporate the FR extensions
python term_harvester.py -c CANSIS_GLOSSARY
python term_harvester.py -b
```

To refresh the glossary:

```bash
python term_harvester.py -f CANSIS_GLOSSARY   # re-downloads all pages into zip
python term_harvester.py -c CANSIS_GLOSSARY   # regenerates sources/CANSIS_GLOSSARY.yaml
python term_harvester.py -b
```

---

### FreeText

For picklists described in free prose — inline text, a `.txt` file, or a `.pdf`
document.  See [FreeText source type](#freetext-source-type) for full details.

```bash
# Register the source. With a short topic string Claude draws on its training
# knowledge rather than the source document — useful as a quick placeholder.
# The source URL is stored in source_ontology and added as see_also in the
# generated enum YAML. A #page or section anchor is preserved if supplied.
python term_harvester.py \
  -a "https://static.ixambee.com/public/miscellaneous-pdf/physical_and_chemical_properties_of_soil1720241418.pdf" \
  --free_text "soil aeration status"

# Download the source PDF, then re-extract from its actual text so the enum
# reflects the document rather than Claude's general knowledge.
python term_harvester.py -f SoilAerationStatus
python term_harvester.py -c SoilAerationStatus

# Web page source with a section anchor — anchor stored in config and enum see_also
python3 ../term_harvester.py --input agrifoodca_config.yaml \
  -a "https://www.undrr.org/understanding-disaster-risk/terminology/hips/en0303" \
  --free_text "Soil sodicity class (SAR/ESP)"
```

---

## FreeText source type

The `FreeText` content type lets you extract picklist enumerations from
unstructured text — method-section descriptions, field-book prose, PDF
appendices, or any passage that describes a scale or classification — using
Claude (`claude-opus-4-8`) as the extraction engine.

### When to use

Use FreeText when a controlled vocabulary exists only in human-readable form:
no machine-readable OWL ontology, no LOINC JSON, no STATSCAN classification
page.  Examples: a crop lodging scale described in a journal article, a soil
pH interpretation table in a field guide PDF, a grain quality rating in a
grower's manual.

### How it works

1. You supply a citation URL and enough relevant text (inline or as a file) to scope what you want converted from the source document.
2. Claude reads the text and returns a JSON structure containing **one or more
   named enumerations** — one per distinct scale or classification found in the
   input.  Each enum has ordinal permissible values ordered from lowest/worst
   to highest/best.
3. All extracted enums are written together to a single `sources/{key}.yaml`
   and a `content_type: FreeText` entry is added to `harvester_config.yaml`.
4. The original text is stored in the `description` field of the config entry
   so extraction can be repeated without re-supplying the text.

> **Multiple enumerations from one extraction:** if the input passage describes
> several distinct scales or classifications, Claude extracts all of them in one
> call.  For example, a field-book page covering brittleness, fluidity, and
> smeariness will yield three separate enums — `BrittlenessScale`,
> `FluidityClass`, `SmearyClass` — all stored in a single `sources/{key}.yaml`
> under the one source key.  Use `include` / `minus` in `harvester_config.yaml`
> to select a subset when building `schema.yaml`.

### Input forms

#### URL-only (no `--free_text`)

When the `-a` URL points to an HTML page or a PDF, the file type is detected
from the HTTP `Content-Type` response header — no `.html` or `.pdf` extension
is required in the URL.  The document is fetched automatically and sent to Claude:

```bash
# HTML article — Content-Type: text/html detected from server response
python term_harvester.py \
  -a "https://link.springer.com/article/10.1186/s12870-025-07322-y#text=A visual estimation of lodging before harvest"

# Same paper in PDF form — Content-Type: application/pdf
python term_harvester.py \
  -a "https://bmcplantbiol.biomedcentral.com/counter/pdf/10.1186/s12870-025-07322-y.pdf#text=A visual estimation of lodging before harvest"
```

The `#text=` anchor scopes the 10 000-character extraction window to the paragraph
that begins with that phrase (starting 200 characters before the first match).
The Springer article URL contains no file extension; content-type detection
makes both forms work identically.

#### With `--free_text`

Supply extraction context directly as an inline string or local file:

| Form | Example |
|---|---|
| Inline description | `--free_text "Scale of 1–9 where 9 = no lodging"` |
| Plain-text file | `--free_text methods.txt` |
| PDF file | `--free_text /path/to/appendix.pdf` |

When `--free_text` is an inline string or local file path, the source URL is
stored as a citation only — the document at that URL is **not** fetched.  A
short topic string produces a plausible enum from Claude's training knowledge;
a longer excerpt or file produces a grounded extraction.

#### Anchoring the extraction window

Text extracted from fetched documents is capped at 10 000 characters.  For long
documents the cap will silently discard the relevant section unless you anchor
the extraction window using a URL fragment:

| Fragment | Works for | Effect |
|---|---|---|
| `#page=116` or `#page=116-118` | PDF | Extract only the specified page(s) |
| `#element-id` (bare id, no `=`) | HTML | Start from the element with that `id=` attribute |
| `#text=phrase` | Any | Find the first occurrence of the phrase; start the window 200 chars before it |

When the document is truncated and no anchor is present, the tool prints a tip
suggesting the appropriate form.

For very long PDF field books or specification documents, `#page=N` is the most
reliable anchor.  Use `#text=` when the relevant content spans multiple pages
or the exact page number is not known.

### Code assignment

Claude chooses permissible-value codes according to these rules:

| Source condition | Code strategy |
|---|---|
| Source provides explicit codes (letters, numbers, abbreviations) | Codes are reproduced exactly as they appear |
| Source has no explicit codes | 2–3 letter uppercase abbreviation from each label's significant words (e.g. "Poorly aerated" → `PA`, "Moderately aerated" → `MA`, "Well aerated" → `WA`, "None" → `NO`) |
| Source uses an explicit numeric rating scale (e.g. 1–9) | Integers are used as codes; a label is supplied for each |

Abbreviations within one enum must be distinct — a third letter is added to
resolve clashes.  Sequential integers are only used when the source document
itself uses them; they are not invented for non-numeric vocabularies.

### Workflow

**Initial setup** — `-a` with `--free_text` is self-contained: Claude runs
immediately, `sources/{key}.yaml` is written, and the entry is registered in
`harvester_config.yaml`.  Go straight to `-b` from there.

After extraction Claude prints the enums it found and its suggested source key,
then prompts you to confirm or rename before anything is written:

```
  Claude extracted 3 enum(s): MannerOfFailure, BrittlenessScale, FluidityClass
  Suggested source key: 'MannerOfFailure'
  Source key [MannerOfFailure]: SoilConsistenceFailure
  Added source 'SoilConsistenceFailure' to harvester_config.yaml
```

Press Enter to accept the suggestion, or type a replacement.  The key must be a
valid identifier (letters, digits, underscores; start with a letter).  Entering
a key that already exists in the config is rejected and you are re-prompted.
This prevents `-a` from ever overwriting an existing source.

> **Note:** When `--free_text` is an inline string, Claude receives only that
> string — the URL is stored as a citation but the document at that URL is not
> fetched.  A short topic string (e.g. `"soil aeration status"`) produces a
> plausible enum from Claude's training knowledge rather than from the document.
> For grounded extraction: either use URL-only mode (omit `--free_text` and let
> content-type detection fetch the page), or run `-f [key]` then `-c [key]`
> after the initial `-a`.

```bash
# URL-only: page fetched automatically, enum grounded in actual article text
python term_harvester.py \
  -a "https://link.springer.com/article/10.1186/s12870-025-07322-y#text=A visual estimation of lodging before harvest"

# --free_text topic string: enum drawn from Claude's general knowledge, URL is citation only
python term_harvester.py \
  -a "https://static.ixambee.com/public/miscellaneous-pdf/physical_and_chemical_properties_of_soil1720241418.pdf" \
  --free_text "soil aeration status"

python term_harvester.py -b
```

To ground a `--free_text`-initiated source in the actual document:

**Later, to keep a FreeText source up-to-date**, `-f` and `-c` have distinct,
separated roles:

| Command | What it does |
|---|---|
| `python term_harvester.py -f SoilAerationStatus` | Downloads the source PDF to `sources/SoilAerationStatus.pdf`. No Claude. No YAML change. |
| `python term_harvester.py -c SoilAerationStatus` | Runs Claude on the best available text (downloaded file → temp URI fetch → stored description). Prints a diff. Writes `sources/SoilAerationStatus.yaml`. |

A typical refresh cycle is therefore:

```bash
python term_harvester.py -f SoilAerationStatus   # download fresh copy of the PDF
python term_harvester.py -c SoilAerationStatus   # extract enums, review diff
python term_harvester.py -b                       # rebuild schema.yaml
```

`-f` and `-c` may also be used independently:

- `-c SoilAerationStatus` without a prior `-f` will fetch the URI temporarily
  (without saving to disk) or fall back to the stored `description` text.
- `-f SoilAerationStatus` without a subsequent `-c` simply archives the document
  locally for inspection; `sources/SoilAerationStatus.yaml` is unchanged.

**BECAUSE AI PARSING OF SOURCE IS NONDETERMINISTIC, regeneration of FreeText
content types has to be explicitly and manually performed and reviewed.**
`-f all` and `-c` with no arguments both silently skip FreeText sources.
FreeText sources must always be regenerated consciously via an explicit
`-c [key]`, and the printed diff must be reviewed before running `-b`.

When `-c [key]` runs, text is sourced in this priority order:

1. Locally downloaded file `sources/{key}.{ext}` (saved by a prior `-f [key]`)
2. URI fetched temporarily — not saved to disk
3. Stored `description` text from `harvester_config.yaml`

### Config entry

```yaml
SoilAerationStatus:
  title: Soil Aeration Status
  name: SoilAerationStatus
  version: null
  content_type: FreeText
  file_format: yaml
  reachable_from:
    source_ontology: https://static.ixambee.com/public/miscellaneous-pdf/physical_and_chemical_properties_of_soil1720241418.pdf
  download_date: '2026-06-08'
  description: soil aeration status
```

The `--free_text` string is stored verbatim as the `description` field in `harvester_config.yaml` so that future `-c [key]` runs send the same topic prompt to the LLM.  This is intended to encourage — but not guarantee — determinism and reproducibility: given the same source document and the same description string, LLM re-extraction should produce a structurally similar enum.  LLM output is inherently non-deterministic, so treat the stored description as a best-effort reproducibility seed rather than a strict specification.

See [Python code notes → API key setup](#api-key-setup) for instructions on setting `ANTHROPIC_API_KEY`.

---

## Term Search

The `--search` option lets you find terms and enumerations across all sources
that have been processed with `-c`, without needing the grand unified schema.yaml to be built.
It is useful for exploring what controlled vocabulary is available before building
a data specification, or for quickly verifying that a concept is covered.

### Basic usage

```bash
# Plain-text report (default)
python term_harvester.py --input myconfig.yaml --search 'soil acidity'

# Markdown table, redirect to a file for viewing in a Markdown renderer
python term_harvester.py --input myconfig.yaml --format markdown --search 'soil acidity' > results.md

# Multiple space-separated queries in one run
python term_harvester.py --input myconfig.yaml --format markdown \
  --search 'soil drainage' 'soil texture' 'soil pH'
```

### Structured queries (term:description)

A query may include a description separated by a colon: `term:description`.

```bash
python term_harvester.py --input myconfig.yaml \
  --search 'excess drainage:a categorical drainage classification for soils'
```

The description is **not** used in local keyword scoring — only the term before
the colon drives FTS5 matching and token overlap.  The description is forwarded
to API search endpoints (OLS4, BioPortal) and, when `--ai` is given, to Claude
as semantic context for synonym generation.  This lets you narrow API results
without eliminating local matches that lack the description words.

### How matching works

`--search` uses a two-stage ranking approach.

**Stage 1 — FTS5 full-text index**

During `-c`, a SQLite FTS5 index is built at `sources/search_index.db` using a
Porter stemmer.  The stemmer maps morphological variants to a common root, so
querying `excess drainage` automatically finds *"Excessively Drained"* because
`excess` and `excessive/excessively` share the stem `excess`, and `drain`,
`drainage`, and `drained` share the stem `drain`.

Labels are weighted 5× over definitions during BM25 scoring.  FTS5 scores are
normalised to a 0–0.85 range; exact phrase substring matches in the label or
definition score 1.0.  If the index file is absent, the search falls back to
token-overlap scoring directly against the source YAML files.

**Stage 2 — API search**

When the `apis` block in `harvester_config.yaml` is configured, OLS4 and
BioPortal are queried in parallel for each search term.  API results are merged
with local results, deduplicated by term ID, and re-ranked.

### `--ai` flag: synonym expansion and AI re-scoring

Adding `--ai` activates two additional Claude-powered steps (requires `ANTHROPIC_API_KEY` — see [Python code notes](#python-code-notes)).

**Step 1 — Synonym expansion (Claude Haiku)**

Before search, Claude is called once per query to generate 3–5 close synonyms
that might appear in controlled vocabularies.  All synonyms are searched
alongside the original query, and results are pooled back to the original term.

For example, `--search 'soil acidity'` might expand to:

> *soil pH · soil reaction · hydrogen ion concentration in soil ·
> soil acidification · active acidity in soil*

This catches terms like *"Reaction (pH)"* or *"Soil pH class"* that would not
match the string `soil acidity` literally.

**Step 2 — AI re-scoring (Claude Sonnet)**

After pooling, Claude re-evaluates the full candidate set and assigns a
relevance score (0–1) for each match against the original query intent.  This
re-ranking is especially useful when synonym expansion retrieves many candidates
that are only tangentially related to the original concept.

### Reading the Markdown output

Use `--format markdown` and redirect to a `.md` file to view results in any
Markdown renderer (GitHub, VS Code preview, Obsidian, etc.).

```bash
python term_harvester.py \
  --input examples/agrifoodca_config.yaml \
  --format markdown \
  --ai \
  --search 'soil acidity' > soil_acidity.md
```

Example output:

| Source | Type&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Score | Parent | ID | Label | Definition&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|---|---|---|---|
| NRCSSoilFieldBook | enum<details><summary>8 children</summary>VE · EX · VS · S · SL · N · MH · H</details> | 1.00 | | NRCSSoilFieldBook_ReactionPH | Reaction (pH) | Soil **acidity** and alkalinity expressed as a **pH** value, ranging from very strongly **acid** (≤4.4) to very strongly alkaline (≥9.1). |
| NRCSSoilFieldBook | term | 0.92 | NRCSSoilFieldBook_ReactionPH | VS | Very Strongly Acid | **Soil** **pH** 4.5–5.0. Common in forested mineral soils and many organic soils in humid climates. |
| NRCSSoilFieldBook | term | 0.87 | NRCSSoilFieldBook_ReactionPH | EX | Extremely Acid | **Soil** **pH** < 3.5. Found in acid sulphate soils and highly organic soils with advanced decomposition. |
| NSDBSNTv2 | enum<details><summary>7 children</summary>1 · 2 · 3 · 4 · 5 · 6 · 7</details> | 0.72 | | NSDB_PMCHEM1 | Parent Material Chemical Property | Chemical characteristics of the parent material including **acidity**, base saturation, and mineral weathering potential. |

**Column guide:**

| Column | Contents |
|---|---|
| Source | The source key from `harvester_config.yaml` |
| Type | `enum` (a full enumeration) or `term` (an individual permissible value). For enums, a collapsible `N children` list shows all member terms. |
| Score | Relevance score 0–1.  Scores ≥ 0.85 indicate strong label matches; scores from FTS5 stemming cap at 0.85; API and AI scores fill the range. |
| Parent | For `term` rows, the enum key the term belongs to. |
| ID | The permissible-value key or enum key. |
| Label | The human-readable title. |
| Definition | Truncated definition with **matched query tokens bolded**. |

---

## `harvester_config.yaml` — source entry structure

Each entry under `sources:` may carry the following optional filtering and
configuration attributes in addition to the required `content_type`,
`file_format`, and `reachable_from` fields.

### `minus` — exclude content during `-b` build

```yaml
MySource:
  content_type: OWL
  minus:
    concepts: [environmental feature, quality]   # exclude whole enums by key or label
    permissible_values: [ENVO:00000001]           # exclude specific PV keys
    status: [DEPRECATED]                          # exclude PVs by status field (OWL only)
```

### `include` — restore excluded content, or whitelist-only mode

When used **with** `minus`, `include` is applied after `minus`, restoring
specific items even when an ancestor was excluded.

When used **without** `minus`, `include` acts as a whitelist: all enums (or
permissible values) from the source are excluded by default, and only the items
listed in `include` are imported.

```yaml
# With minus: restore water body despite its ancestor being excluded
MySource:
  content_type: OWL
  minus:
    concepts: [environmental feature]
  include:
    concepts: [water body]                        # restore this subtree despite minus
    permissible_values: [ENVO:00000123]           # restore specific PV keys

# Without minus: import only the listed enums (all others are excluded)
MySource:
  content_type: LinkML
  include:
    concepts: [BiomeEnum, HabitatEnum]            # only these two enums are imported
```

### `rank` — auto-detected permissible value ordering

When permissible values are written by `-c` (for `OntologyAPI` / AGROVOC SKOS
sources) or by `-l` (expansion into `schema.yaml`), the tool scans each sibling
group — values sharing the same `is_a` parent, or values with no `is_a` — for a
leading alphanumeric rank code in the title.

**Detection rule:** if at least two siblings share the same prefix before a
number (e.g. `LP.` in `LP.07 seven leaves visible stage`, or an empty prefix for
bare integers like `3 establishment of tissue systems stage`), and those numbers
form a consecutive integer sequence starting at `0` or `1`, every matched value
receives a `rank:` attribute equal to the parsed integer and the group is sorted
by rank.  Unmatched siblings within the same group are placed after the ranked
values.

```yaml
# Example: children of PO:0007520 after auto-ranking
PO:0007505:
  title: 1 root primordium formation stage
  is_a: PO:0007520
  rank: 1
PO:0007527:
  title: 2 root meristem formation stage
  is_a: PO:0007520
  rank: 2
# … ranks 3-5 …
PO:0025475:
  title: coleorhiza emergence stage   # no code → no rank → follows ranked items
  is_a: PO:0007520
PO:0007015:
  title: radicle emergence stage
  is_a: PO:0007520
```

**Preserving user-set ranks:** if a `rank:` value is already present on a
permissible value in `schema.yaml` (set by the user or from a previous run),
it is never overwritten by auto-detection.  This lets you manually override the
order of any individual term without losing the change on the next `-c` or `-l`
refresh.

---

### `sorted` — alphabetical ordering of unranked permissible values

Set `sorted: true` on a source entry to have unranked permissible values within
each sibling group sorted case-insensitively by title.  This applies wherever
that source's enums are processed: `-c` writes the sorted order to
`sources/{key}.yaml`, and `-l` applies the same ordering when updating
`schema.yaml`.

```yaml
# harvester_config.yaml
MyOntologySource:
  content_type: OntologyAPI
  sorted: true
  reachable_from:
    source_nodes: [ENVO:00000428]
    include_self: true
```

Ranked items (those with an auto-detected or user-set `rank:`) are always placed
first within each group; `sorted: true` controls only the trailing unranked
items.  To add a case-sensitive sort option, a `sorted: case-sensitive` value
may be supported in a future release.

---

### `concise` — trim redundant hierarchy nodes during `-b`

When `concise: true`, nodes whose title exactly matches their parent's title are
dropped during `-b` build and any grandchildren are re-wired to the nearest
surviving ancestor.

```yaml
NAPCSCanada2022:
  content_type: NAPCSCanada
  concise: true
```

**Example:** a NAPCS hierarchy where class `"011"` (title "Crop products") has a
child `"0110"` also titled "Crop products" — the child is redundant and is
dropped.  Any codes that had `is_a: "0110"` are re-wired to `is_a: "011"`.

To keep all nodes, omit `concise` or set `concise: false`.

Behaviour varies by content type:

| Content type | `concise: true` effect |
|---|---|
| Any | Drops permissible values with `status: obsolete` at `-b` build time |
| `NAPCSCanada` | Also drops hierarchy nodes whose title exactly matches their parent's title, re-wiring grandchildren to the nearest surviving ancestor |
| `OWL` | Also applied at `-c` time inside `process_owl_source`, clipping class definitions to two sentences |
| `NASIS` | ~1,950 obsolete-marked domain values dropped; surviving count is written to `sources/NASIS.yaml` |

---

## `minus` / `include` processing — which attributes apply to which content types

All content types pass through the same enum processing loop in `-b`.
The `minus` and `include` attributes are applied uniformly with two exceptions:

| Attribute | All content types | Notes |
|---|---|---|
| `minus.concepts` | Yes | Excludes whole enums by key, `source_schema`, or `source_domain` annotation |
| `minus.permissible_values` | Yes | Removes specific PV keys from every surviving enum |
| `minus.status` | **OWL only** | Removes PVs whose `status` field matches (e.g. `DEPRECATED`). Silently ignored for other types even if present. OntologyAPI sources also emit `status: DEPRECATED` on nodes, so this could logically apply there too |
| `include.concepts` | Yes | Restores excluded enums after `minus` |
| `include.permissible_values` | Yes | Restores specific PV keys in surviving enums |
| `concise` obsolete drop | Yes | Drops PVs with `status: obsolete` at `-b` time |
| `concise` deduplication | **NAPCSCanada only** | Also drops PVs whose title equals their parent's title; grandchildren re-wired |

The processing order is:
1. `minus.concepts` / `minus.permissible_values` / `minus.status` — first pass, excluding items
2. `include.concepts` / `include.permissible_values` — second pass, restoring specific items

---

## API configuration

The `apis` block in `harvester_config.yaml` configures API endpoints for the `-l`
lookup function.  Each key is a service name with a `type` sub-object.  The
`-l` lookup routes each CURIE prefix to the first API whose `ontologies` list
contains it, falling back to OLS4.

```yaml
apis:
  ols:
    type:
      rest:
        uri: http://www.ebi.ac.uk/ols4/api/ontologies/{ontology}/terms/{double_encoded}/graph
    ontologies: [ENVO, GO, UBERON, SNOMED]
  bioportal:
    type:
      rest:
        uri: https://data.bioontology.org
        apikey: YOUR_BIOPORTAL_KEY
    ontologies: [MESH, NCIT, SNOMEDCT]
  agrovoc:
    type:
      sparql:
        uri: https://agrovoc.fao.org/sparql/
    ontologies: [agrovoc]
```

AGROVOC can be added directly as a file but is 60 MB+; using the SPARQL
endpoint is recommended.  A SKOSIMOS API option exists but is not yet
implemented.

### OLS4 IRI base auto-detection

OLS4 serves ontologies with different IRI conventions.  OBO ontologies use the
`http://purl.obolibrary.org/obo/{ONTOLOGY}_{id}` pattern; SNOMED CT uses
`http://snomed.info/id/{id}`.  The `-l` lookup detects the correct base
automatically by querying the OLS4 ontology metadata endpoint
(`/api/ontologies/{ontology}`) and reading `config.baseUris[0]`.  Results are
cached per session so the metadata call is made at most once per ontology.

To override auto-detection (e.g. for a private OLS4 mirror with a different
IRI scheme), add an explicit `iri_base` key under `type.rest`:

```yaml
apis:
  ols:
    type:
      rest:
        uri: https://my-ols.example.org/api/ontologies/{ontology}/terms/{double_encoded}/graph
        iri_base: "http://snomed.info/id/"   # explicit override — skips metadata call
    ontologies: [SNOMED]
```

---

## OntologyAPI metadata population

### Source-level metadata (written to `harvester_config.yaml` by `-a`)

| Source type | `title` | `version` | `description` |
|---|---|---|---|
| AGROVOC | ✓ SPARQL `skos:prefLabel` | — | ✓ SPARQL `skos:scopeNote` |
| SNOMED (via OLS4) | ✓ OLS4 term `label` | ✓ OLS4 `config.versionIri` | ✓ OLS4 term `description`\* |

\* SNOMED CT definitions are present in OLS4 for many concepts but are not
guaranteed for all terms (depends on whether SNOMED has a formal definition for
that concept).

Other OntologyAPI sources (ENVO, GO, UBERON, etc.) do not have a `-a` detection
handler and must be added to `harvester_config.yaml` manually.

### Per-permissible-value metadata (written to `sources/{key}.yaml` by `-l`)

| API back-end | `title` | `description` | `status: DEPRECATED` |
|---|---|---|---|
| OLS4 (`/graph` endpoint) | ✓ term label | — graph endpoint returns only IRI + label | — |
| BioPortal (`/descendants`) | ✓ `prefLabel` | ✓ IAO:0000115 `definition` annotation | — |
| AGROVOC SPARQL | ✓ `skos:prefLabel` | ✓ `skos:definition` / `skos:scopeNote` | ✓ `owl:deprecated` |

OLS4 does not return definitions from its `/graph` endpoint.  Filling them
would require one additional per-term API call per descendant, which is
impractical for large hierarchies.  If per-term definitions are essential,
BioPortal is the better back-end for OBO ontologies (ENVO, GO, etc.).

### OLS4 vs BioPortal for SNOMED CT

OLS4 is the recommended back-end for SNOMED CT for two reasons:

1. **Standard IRIs** — OLS4 exposes SNOMED terms under the international IRI
   base `http://snomed.info/id/{conceptId}`.  BioPortal uses its own IRI scheme
   `http://purl.bioontology.org/ontology/SNOMEDCT/{conceptId}`, which would
   produce non-standard CURIEs in the output YAML.

2. **No API key required** — OLS4 is freely accessible.  BioPortal requires a
   registered API key configured under `apis > bioportal > type > rest > apikey`.

The trade-off is that OLS4's `/graph` endpoint does not return per-term
definitions (see table above), whereas BioPortal does.  If definition text per
permissible value is a hard requirement, BioPortal can be used with the SNOMED
prefix set to `http://purl.bioontology.org/ontology/SNOMEDCT/` in the source
entry — but the resulting CURIEs will not resolve to the international SNOMED
namespace.

---

## SSSOM ontology mappings

SSSOM (Simple Standard for Sharing Ontology Mappings) files can be applied to
`schema.yaml` permissible values using `-s`.  SSSOM maps `subject_id` values
matching a permissible value's `meaning` field to LinkML mapping attributes:

| SSSOM predicate | LinkML attribute |
|---|---|
| `skos:closeMatch` | `close_mappings` |
| `skos:broadMatch` | `broad_mappings` |
| `skos:narrowMatch` | `narrow_mappings` |
| `skos:exactMatch` | `exact_mappings` |
| `skos:relatedMatch` | `related_mappings` |

See https://github.com/mapping-commons/sssom/ and
https://www.w3.org/TR/skos-reference/#mapping for the SSSOM and SKOS mapping
specifications.

---

---

## Python code notes

### Installation

Dependencies are declared in `pyproject.toml`.  Install the core requirement
(`pyyaml`) and whichever optional extras you need:

```bash
pip install -e .                  # core only (pyyaml)
pip install -e ".[pdf]"           # + pypdf   (NASIS, NRCS Field Book, CRediT, FreeText PDF input)
pip install -e ".[owl]"           # + owlready2 (OWL ontologies)
pip install -e ".[freetext]"      # + anthropic (FreeText extraction, --search --ai)
pip install -e ".[all]"           # all optional dependencies
```

| Extra | Package | Required for |
|---|---|---|
| `pdf` | `pypdf` | `content_type: NASIS`, `NRCSSoilFieldBook`, `CRediT`; FreeText PDF input |
| `owl` | `owlready2` | `content_type: OWL` |
| `freetext` | `anthropic` | `content_type: FreeText`; `--search --ai` synonym expansion and re-scoring |

### API key setup

FreeText extraction and AI-assisted search (`--search --ai`) require an Anthropic API
key from [console.anthropic.com](https://console.anthropic.com/) → **API Keys** → **Create Key**.

**Current session only** (forgotten when the terminal closes):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

**Permanently** — add to your shell profile and reload it:

```bash
# macOS (zsh)
echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.zshrc && source ~/.zshrc

# Linux (bash)
echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.bashrc && source ~/.bashrc
```

**Verify the key is visible to the shell:**

```bash
echo $ANTHROPIC_API_KEY
```

> **Note:** keep your API key out of `harvester_config.yaml` and out of version
> control.  The environment variable approach above is the standard and safest
> method.

### Module structure

| Module | Responsibility | Source |
|---|---|---|
| `term_harvester.py` | CLI entry point; `build_schema`, `add_source`, `process_sources`, `expand_reachable_from` | |
| `source_utils.py` | Shared utilities: HTML parsing, YAML output, config management, prefix helpers | |
| `source_linkml.py` | `content_type: LinkML` — YAML schema processing and `match_linkml` detection | |
| `source_owl.py` | `content_type: OWL` — owlready2 class traversal and `match_owl` detection | |
| `source_ontologyapi.py` | `content_type: OntologyAPI` — OLS4 / BioPortal / AGROVOC graph fetching and processing | |
| `source_agrovoc.py` | AGROVOC SPARQL fetchers and `match_agrovoc` detection | [SPARQL endpoint](https://agrovoc.fao.org/sparql/) |
| `source_loinc.py` | LOINC CodeSystem / ValueSet JSON conversion and HL7 table parsing | [HTML / JSON files](https://terminology.hl7.org/) |
| `source_nasis.py` | `content_type: NASIS` — USDA NRCS NASIS Domains PDF parsing | [PDF file](https://www.nrcs.usda.gov/sites/default/files/2025-07/NASIS%207.4.3%20Domains.pdf) |
| `source_nrcs.py` | `content_type: NRCSSoilFieldBook` — USDA NRCS Field Book PDF parsing | [PDF file](https://www.nrcs.usda.gov/sites/default/files/2025-05/Field-Book-for-Describing-and-Sampling-Soils-Ver4.pdf) |
| `source_nsdb.py` | National Soil DataBase HTML parsing | [index](https://sis.agr.gc.ca/cansis/nsdb/index.html) |
| `source_statscan.py` | Statistics Canada classification page scraping | |
| `source_statscan_table.py` | `content_type: STATSCANTable` — Statistics Canada Census Dictionary table pages; auto-fetches French translations from the corresponding `index-fra.cfm` page | |
| `source_iso_country.py` | `content_type: ISO_COUNTRY` — ISO 3166-2 country subdivision codes via Wikidata SPARQL (P297 for country name, P300 prefix filter for subdivisions); each PV carries its Wikidata QID as `meaning`; ISO OBP is a Vaadin SPA, not directly fetchable | [ISO OBP](https://www.iso.org/obp/ui/) |
| `source_napcscanada.py` | NAPCS Canada CSV parsing | [CSV file](https://www.statcan.gc.ca/en/media/5274) |
| `source_agrifoodca.py` | AgriFoodCA picklist CSV parsing and GitHub directory import | [CSV files](https://github.com/agrifooddatacanada/picklists_for_schemas/tree/main/picklists) |
| `source_zenodo.py` | Shared Zenodo REST API utilities: `is_zenodo_record_url`, `to_zenodo_api_url`, `fetch_zenodo_file` — used by `source_credit.py` | |
| `source_credit.py` | `content_type: CRediT` — CRediT contributor roles; fetches PDF via Zenodo API, parses with `pypdf` | |
| `source_loc_classification.py` | `content_type: LOC_CLASSIFICATION` — Library of Congress Classification; downloads HTML index + ~20 class PDFs; hierarchy via numeric range containment | [Index](https://www.loc.gov/catdir/cpso/lcco/) |
| `source_codex.py` | `content_type: CODEX` — FAO/WHO Codex General Standard for Food Additives (CXS 192-1995) PDF; produces three enums per revision year: food categories (dotted-decimal hierarchy), TABLE ONE additives (INS codes), and footnotes | [GSFA PDF](https://www.fao.org/gsfaonline/docs/CXS_192e.pdf) |
| `source_enumber.py` | `content_type: E_NUMBER` — EU/UK food additive E numbers via Wikidata SPARQL property P628; fetches labels, `schema:description`, and P366 functional-use values; produces a two-level enum with nine EU numeric range parent PVs; `meaning` compressed to `wd:Qxxx` | [Wikidata Q207810](https://www.wikidata.org/wiki/Q207810) |
| `source_cansis_glossary.py` | `content_type: CANSIS_GLOSSARY` — CANSIS Glossary of Terms in Soil Science; downloads all A-Z letter pages (EN + FR) into a zip; parses `<dl>` term/definition pairs; FR locale extensions handled by `source_cansis_translate.py` (separate utility, requires `deep-translator`) | [Glossary index](https://sis.agr.gc.ca/cansis/glossary/) |
| `source_cansis_translate.py` | Standalone utility (not a source module); bridges the independent CANSIS French glossary to the EN enum via Google Translate + fuzzy label matching; run manually after `-c` to produce and apply `CANSIS_GLOSSARY_translated_fr.tsv` | |
| `source_freetext.py` | `content_type: FreeText` — Claude API enum extraction from free text | |
