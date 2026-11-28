# GeoWiki / GeoNames Source Module Notes

This document records Wikidata and GeoNames data-structure behaviours that affect
`source_geowiki.py` and `source_geonames.py`, with notes on how each is handled.

---

## Overview: GeoWiki Enumeration Types

`source_geowiki.py` produces two families of geospatial enumerations: **Geopolitical**
(countries, subdivisions, settlements, and Indigenous nations) and **Waters** (oceans,
international waterbodies, and per-country water features).

### Geopolitical enumerations

| Enum type | Scope | Description |
|-----------|-------|-------------|
| `GeoWikiContinent` | Global | The seven canonical continents plus the Americas supercontinent (Q828), validated against GeoNames. |
| `GeoWikiCountry` | Global | Sovereign states only, grouped under continent headers. Territories and dependencies are excluded. |
| `GeoWikiCountryExtended` | Global | Territories, dependencies, and other non-sovereign entities that have an ISO 3166-1 alpha-2 code. Used alongside `GeoWikiCountry` when broader coverage is needed. |
| `GeoWikiAdm1{CC}` | Per-country | First-order administrative divisions (provinces, states, territories). |
| `GeoWikiAdm2{CC}` | Per-country | Second-order divisions (counties, districts, regional municipalities). Inherits `GeoWikiAdm1{CC}`, so provincial header nodes are included. |
| `GeoWikiAdm3{CC}` | Per-country | Third-order divisions (local municipalities, townships). Inherits `GeoWikiAdm2{CC}`. |
| `GeoWikiSettlement{CC}` | Per-country | Populated places (cities, towns, villages, hamlets) from Wikidata P31 settlement types. ADM ancestor nodes (provinces through municipalities) are inlined for hierarchical grouping; settlement type classification uses `HumanSettlementClassification` mixins. Non-current settlements are emitted as `status: historical` and excluded by default via `minus: {status: [historical]}`. |
| `GeoWikiFirstNations{CC}` | Per-country | Indigenous nations, bands, tribes, and Iwi, grouped under provincial/regional nodes. Derived from the same Wikidata query as ADM2. **Developed and tested for Canada (`CA`); other countries (US, NZ, etc.) are supported in principle via `_INDIGENOUS_TOP_BY_COUNTRY` and `_INDIGENOUS_ADM1_GROUPS` but have not been validated — results for non-CA countries should be reviewed before use.** |

The `{CC}` suffix is the ISO 3166-1 alpha-2 country code (e.g. `CA`, `US`, `NZ`).

### Waters enumerations

| Enum type | Scope | Description |
|-----------|-------|-------------|
| `GeoWikiOcean` | Global | Major oceans and seas. |
| `GeoWikiWaterbody` | Global | International lakes, rivers, straits, bays, and other named waterbodies with Wikidata QIDs. |
| `GeoWikiCountryWaters{CC}` | Per-country | Rivers, lakes, and water features within a country, grouped under ADM1 and ADM2 header nodes. Only ADM1/ADM2 regions that contain at least one water body are included as group headers; regions with no water bodies are pruned. `inherits: GeoWikiAdm2{CC}` provides the structural link to the full administrative hierarchy. |

### Inheritance and term counts

LinkML `inherits:` causes the parent enum's terms to be included in the child at
schema-build time.  This means the reported term count for an inheriting enum includes
its own entries **plus** all header and grouping nodes pulled in from the parent.

Example — agrifoodca picklist counts for Canada (as of 2026-11-27):

| ID | Enum | Terms | Notes |
|----|------|------:|-------|
| 42 | GeoWikiContinent | 8 | |
| 43 | GeoWikiCountry | 209 | Grouped under continent headers |
| 44 | GeoWikiCountryExtended | 54 | Territories/dependencies |
| 45 | GeoWikiFirstNationsCA | 626 | Bands and nations grouped by province/region |
| 46 | GeoWikiAdm1CA | 27 | Provinces and territories |
| 47 | GeoWikiAdm2CA | 2,234 | Inherits GeoWikiAdm1CA (+27 provincial headers) |
| 48 | GeoWikiAdm3CA | 1,326 | Inherits GeoWikiAdm2CA (+ADM2 and ADM1 headers) |
| 49 | GeoWikiSettlementCA | 12,374 | ADM nodes inlined; inherits HumanSettlementClassification for stype mixins |
| 50 | GeoWikiOcean | 10 | |
| 51 | GeoWikiWaterbody | 452 | International waterbodies |
| 52 | GeoWikiCountryWatersCA | 1,951 | ADM1 and ADM2 grouping nodes included only where they have water body children; regions with no water bodies are pruned. `inherits: GeoWikiAdm2CA` provides the structural link. |

Counts will vary with Wikidata snapshot date and any `minus`/`include` filters applied
in `config.yaml`.

---

## 1. Sub-national entities with ISO 3166-1 alpha-2 codes

### Root cause
ISO 3166-1 assigns alpha-2 codes to a small number of entities that are not
sovereign states but have postal or administrative need for a country-level code.
Examples in Wikidata (as of 2026):

| QID       | alpha-2 | Wikidata description |
|-----------|---------|----------------------|
| Q31890709 | AC      | administrative division of Saint Helena, Ascension and Tristan da Cunha |
| Q34625512 | TA      | administrative division of Saint Helena, Ascension and Tristan da Cunha |
| Q3405693  | CQ      | one of the Channel Islands, part of the Bailiwick of Guernsey |

Because `source_geowiki.py` queries Wikidata by `wdt:P297` (ISO 3166-1 alpha-2),
these entities are returned alongside genuine countries and territories.

### Why they have no continent
GeoNames excludes them from `countryInfo.txt` (the authoritative country list) because
they are not country-level entities.  Wikidata does not set `wdt:P30` (continent) for
them either, so they end up with an empty `continent_qid` after all continent-assignment
steps.

### How this is handled
In `_build_countries_yaml` (`source_geowiki.py`), any country-candidate with an empty
`continent_qid` **after** both the Wikidata P30 query and the GeoNames fallback is
treated as a sub-national entity and excluded from the `GeoWikiCountry` enum.  A
warning is printed listing each excluded entry by alpha-2 code and name, so users
are aware of the omission.

These entities are also absent from `GeoNamesCountry` because `_fetch_geonames_countries`
parses only `countryInfo.txt`, which does not include them.

### Are they covered elsewhere?
Potentially.  Ascension and Tristan da Cunha would appear in an ADM1 download for
Saint Helena (`GeoWiki_SH_ADM1`); Sark would appear in a Guernsey ADM1 download.
Neither parent territory is commonly queried, so double-coverage is unlikely in practice.
Users who specifically need these entities can add them via `include` in `config.yaml`.

---

## 2. Wikidata P30 (continent) sub-regional values

### Root cause
Wikidata's `wdt:P30` property is supposed to hold a continent, but editors sometimes
assign sub-regional geographic entities instead of one of the seven standard continents.
Common substitutions observed:

| Expected standard continent | Sub-regional QID seen in P30 | Name |
|-----------------------------|------------------------------|------|
| Q55643 (Oceania)            | Q538                         | Melanesia |
| Q55643 (Oceania)            | Q3960                        | Australian continent |
| Q48 (Asia)                  | Q55614502                    | Insular India |

In a fetch with ~257 sovereign states, roughly 26 entries had sub-regional P30 values
that did not correspond to any of the seven canonical GeoNames continents.  They would
have produced orphaned synthetic headers with QID-only titles (e.g. `Q538: title: Q538`)
in `GeoWikiCountry`.

### How this is handled
`_fetch_countries_payload` in `source_geowiki.py` downloads `countryInfo.txt` from
GeoNames and uses it as the **authoritative continent source** for all countries it
covers.  The `continent_qid` for every alpha-2 code found in GeoNames is set from the
GeoNames continent column, mapped through `_GN_CONT_CODE_TO_WD_QID` to one of the seven
canonical Wikidata QIDs:

| GeoNames code | QID    | Continent name |
|---------------|--------|----------------|
| AF            | Q15    | Africa         |
| AS            | Q48    | Asia           |
| EU            | Q46    | Europe         |
| NA            | Q49    | North America  |
| SA            | Q18    | South America  |
| OC            | Q55643 | Oceania        |
| AN            | Q51    | Antarctica     |

Wikidata P30 values are **overridden** by the GeoNames value for any country present in
`countryInfo.txt`.  Countries not in `countryInfo.txt` retain their Wikidata P30 value
if it is one of the seven canonical QIDs, or end up with an empty `continent_qid` (see
section 1) if P30 is absent or sub-regional.

---

## 3. NFD Unicode sorting

### Root cause
Python's `.casefold()` folds characters such as `Å` to `å` but does not decompose the
combining diacritic, so `Å` sorts after `Z` rather than with `A`.

### How this is handled
Both `source_geowiki.py` and `source_geonames.py` use a `_title_sort_key` helper that
applies Unicode NFD (canonical decomposition) before casefolding:

```python
def _title_sort_key(s):
    return unicodedata.normalize('NFD', s or '').casefold()
```

This causes `Å` → `A` + combining ring above, which sorts correctly alongside other
`A` entries.  Example: *Åland Islands* now sorts after *Austria* within the Europe
section of `GeoNamesCountry` / `GeoWikiCountry`.

---

## 4. Wikidata P1566 crosswalk coverage

`wdt:P1566` stores GeoNames IDs in Wikidata and is the primary crosswalk property used
by both modules.

Observed coverage rates (2026 snapshot):

| Scope | Coverage |
|-------|----------|
| Countries (P297) | ~99–100% |
| ADM1 (per small country) | ~80–100% |
| ADM2 (per country) | ~90–97% |
| ADM3 (per country) | ~30–50% (varies widely) |
| Populated places (P31 in settlement types) | ~40–75% (varies by country) |

Coverage drops sharply at ADM3+ and for populated places.  Entries without a P1566
match are emitted without `exact_mappings` (no GeoNames cross-reference) but are still
included in the enum.

`source_geonames.py` uses P1566 in **reverse**: given a list of GeoNames IDs, it
queries Wikidata for items that hold those IDs as `P1566` values, collecting QIDs and
multilingual labels in return.

---

## 5. Wikidata "Kingdom of the Netherlands" vs "Netherlands"

Wikidata has two items for the Netherlands:

| QID   | alpha-2 | Description |
|-------|---------|-------------|
| Q55   | NL      | Netherlands (constituent country) |
| Q29999 | NL     | Kingdom of the Netherlands (the sovereign state with Caribbean territories) |

Both have `wdt:P297 = "NL"`.  The `seen` deduplication in `_fetch_wikidata_countries`
keeps whichever is returned first by WDQS, and the other is silently skipped.  In
practice Q29999 appears first (it is the sovereign entity), leaving Q55 (the constituent
country) absent.  As a result `GeoWikiCountry` contains Q29999 with an empty `gnid`
(because `countryInfo.txt` carries geonameId 2750405 under Q55, and P1566 on Q29999
may be absent).  This is a known Wikidata modelling split; it does not affect the
GeoNames module, which uses `countryInfo.txt` directly and always lists `NL → 2750405`.

---

## 6. Historical entity filtering

### GeoNames
`countryInfo.txt`, `admin1CodesASCII.txt`, and `admin2Codes.txt` list only **active**
entities; historical divisions are omitted from these flat files.  No additional
filtering is required for those sources.

Per-country ZIP files (`{CC}.zip`) include historical feature codes ending in `H`
(e.g. `ADM1H`, `PCLH`, `ADMDH`).  The ADM3/ADM4 parser (`_fetch_geonames_adm34`)
and the populated-places parser (`_fetch_geonames_ppl`) filter on explicit allowlists
of current feature codes.  Historical populated-place codes (`PPLH`, `PPLCH`, `PPLQ`,
`PPLW`) are collected separately and emitted with `status: deprecated`.

### Wikidata
`wdt:P576` (dissolved/abolished date) is used to flag historical entities.

- **Countries and ADM1/ADM2**: `FILTER NOT EXISTS { ?item wdt:P576 ?end . }` in the
  SPARQL query excludes entities with any dissolution date.
- **Continents and oceans**: same filter applied.
- **Populated places**: the query uses `OPTIONAL { ?item wdt:P576 ?dissolved . }`,
  collecting the date if present.  The build function (`_build_ppl_yaml`) emits these
  as `status: deprecated` so users can suppress them with
  `minus: {status: [deprecated]}` in `config.yaml`.

---

## 7. WDQS query limits and QLever

The public Wikidata Query Service (WDQS) enforces a 60-second timeout per query.
This constrains which queries can run there:

| Query scope | WDQS | QLever |
|-------------|------|--------|
| Countries (~250) | OK | OK |
| Continents (~15) | OK | OK |
| Oceans (~10) | OK | OK |
| ADM1 single country | OK | OK |
| ADM1 global (~4,000) | Timeout | Required |
| ADM2 single country | Usually OK | Recommended |
| Settlements single country (small) | Usually OK | Recommended |
| Settlements single country (large, e.g. DE) | Timeout | Required |

To use QLever, set `apis: {qlever: true}` in the source entry in `config.yaml`.
QLever's endpoint (`https://qlever.dev/api/wikidata`) accepts the same SPARQL syntax
and is queried via HTTP POST.

---

## 8. Wikidata settlement types (P31)

Wikidata's instance-of hierarchy for human settlements is inconsistent: the same real
city may be tagged `Q515` (city), `Q1549591` (big city), `Q3957` (town), or `Q486972`
(human settlement) depending on the editor.  `source_geowiki.py` uses an explicit
allowlist of settlement type QIDs (`_WD_SETTLEMENT_TYPES`) rather than a hierarchy
walk to avoid including non-settlement entities:

```python
_WD_SETTLEMENT_TYPES = frozenset({
    "Q1637706",   # million city
    "Q1549591",   # big city
    "Q129676344", # large city
    "Q515",       # city
    "Q896881",    # average city
    "Q18466176",  # small city
    "Q3957",      # town
    "Q532",       # village
    "Q5084",      # hamlet
    "Q129676371", # rural settlement
    "Q7930989",   # city/town
    "Q747074",    # urban municipality
    "Q27676416",  # city or town of Quebec (cité ou ville)
    "Q27676422",  # township municipality of Quebec (municipalité de canton)
    "Q486972",    # human settlement
    "Q2095140",   # populated place below civil division level
})

# Non-current settlements — fetched but emitted as status: historical
_WD_SETTLEMENT_ARCHAIC_TYPES = frozenset({
    "Q350614",    # ghost town
    "Q2974842",   # abandoned village / deserted settlement
    "Q350895",    # archaeological site
    "Q106071744", # former town
    "Q21751582",  # deserted medieval village
    "Q22674925",  # former settlement
    "Q14616455",  # destroyed city
    "Q1434274",   # archaic settlement type
})
```

These lists may need expanding for countries whose editors use different instance types.
Check the Wikidata item for a known town in the target country if settlement coverage
appears low.

---

## 9. Duplicate continent items in Wikidata (P31=Q5107)

### Root cause
Wikidata assigns `P31=Q5107` (continent) to more than the seven canonical continents.
Many geographic concepts — geological landmasses, supercontinent groupings, indigenous
names, and sub-regional models — also carry this tag even though no country uses them
as `P30` (continent).

The pairing most likely to cause confusion:

| Canonical (used as P30 by countries) | Geological/conceptual duplicate (P30 usage = 0) |
|---------------------------------------|--------------------------------------------------|
| Q49 North America (gnid 6255149) | Q17173147 "North American continent" |
| Q18 South America (gnid 6255150) | Q17173219 "South American continent" |
| Q15 Africa (gnid 6255146) | Q11284672 "African Continent" |
| Q55643 Oceania (gnid 6255151) | Q3960 "Australian continent" |

Additional non-canonical items also tagged P31=Q5107:

| QID | Name | Nature |
|-----|------|--------|
| Q828 | Americas | Supercontinent (NA + SA combined); **has** GeoNames ID 10861432 |
| Q27527 | Afro-Eurasia | Supercontinent (Europe + Asia + Africa combined) |
| Q7856408 | Turtle Island | Indigenous Anishinaabe name for North America |
| Q55614502 | Insular India | Sub-regional concept within Asia |

**How to distinguish them:** the canonical continents all have `wdt:P1566` (GeoNames ID)
and are used as `wdt:P30` by actual countries.  The duplicates have neither.

### How this is handled
`_fetch_wikidata_continents` now requires `wdt:P1566 ?gnid` as a **mandatory** triple
rather than `OPTIONAL`.  This filters the result from 15 items down to 8
(the 7 canonical continents plus Q828 Americas, which has a GeoNames entry).
The `GeoWikiContinent` enum therefore contains only GeoNames-validated continent concepts.

---

## 10. ADM1 via P131 vs P17, and the P31/P279* administrative type filter

### Why two geographic filters are not enough

For ADM1 queries, `source_geowiki.py` requires **both**:
- `wdt:P17 wd:{country_qid}` — the item belongs to that country
- `wdt:P131 wd:{country_qid}` — the item is directly contained within that country
  (i.e. a direct first-order child, not a lower-level division)

Using P17 alone returns all sub-national divisions at all levels; the P131 filter
restricts to first-order divisions only.  This is imperfect for countries where P131
chains are inconsistently populated, but it is the best available approximation without
downloading and parsing the full GeoNames administrative hierarchy file (`hierarchy.zip`).

### P131 is not restricted to geographic entities

Many non-geographic Wikidata items — airports, railway lines, sporting events,
organizations, disease outbreaks — carry `wdt:P131 = <country>` because they are
*located in* or *associated with* the country.  The combination `P17=country AND
P131=country` therefore returns a large amount of garbage alongside the true
administrative divisions.  Testing against Canada (Q16) returned 59 items when only
13 provinces/territories were expected.

### How this is handled

A third mandatory filter restricts results to items whose `P31` (instance of) traces
via `wdt:P279*` (subclass of, zero or more hops) to **Q10864048**
("first-level administrative country subdivision"):

```sparql
?item wdt:P31/wdt:P279* wd:Q10864048 .
```

This SPARQL property path traverses the Wikidata class hierarchy.  It keeps:
- Provinces, states, territories, cantons, gouvernorats, etc. — all trace to Q10864048
- Explicitly excludes ecclesiastical territories (dioceses, ordinariate), airports,
  organisations, and other non-governmental entities

Using the broader `wd:Q56061` (administrative territorial entity) was tried first but
kept the Military Ordinariate of Canada (Q1521875), which Wikidata classifies as an
administrative territory via its ecclesiastical type hierarchy.  Q10864048 is the
correct cutoff: it is the Wikidata class for exactly "first-level administrative
subdivision of a country", which is what ADM1 means.

### Performance note

`wdt:P31/wdt:P279*` is a SPARQL property path requiring a hierarchy traversal.  For
per-country ADM1 queries (typically 10–60 items), this adds negligible latency on WDQS.
For global ADM1 queries (~4,000 items), QLever is recommended regardless.
