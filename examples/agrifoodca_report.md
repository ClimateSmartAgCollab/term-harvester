# AgriFoodCA Picklists — Duplicate Enum Analysis

The following `AgriFoodCA_Picklists` enums are conceptually duplicated by more authoritative
sources now present in the schema.  Candidates to add to `minus > concepts` in
`agrifoodca_config.yaml` (in addition to the seven already listed there).

| AgriFoodCA enum | Superseded by | Notes |
|---|---|---|
| `AgriFoodCA_CanadianProvinces` | `Canada` (ISO_COUNTRY_CA) | Same 13 provinces/territories; ISO letter codes (AB, BC…) vs StatsCan numeric codes (48, 59…) |
| `AgriFoodCA_Gender` | `STATSCAN_1326727` | 3-value StatsCan official (1=Man, 2=Woman, 3=Non-binary) vs AgriFoodCA's 5-value (F, M, NB, OTH, PNS) |
| `AgriFoodCA_SoilPh` | `NRCSSoilFieldBook_ReactionPH` | Identical 11-class pH system; NRCS uses full text keys, AgriFoodCA uses abbreviations (UA, EA…) |
| `AgriFoodCA_SoilSalinity` | `SoilSalinityClass` | Same 5 salinity classes; minor code differences (STS→ST, VSS→VS) |
| `AgriFoodCA_SoilPermeability` | `NRCSSoilFieldBook_PermeabilityClass` | NRCS has 8 classes (adds impermeable + 3 intermediate speeds) vs AgriFoodCA's 5 |
| `AgriFoodCA_SoilStructure` | `SoilStructureShape` | Same concept; NRCS splits "Blocky" into Angular/Subangular blocky and adds Wedge |

`AgriFoodCA_SoilTexture` has no equivalent in the current schema and should remain.
