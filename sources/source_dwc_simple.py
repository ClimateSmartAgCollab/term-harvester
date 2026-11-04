"""Darwin Core Simple Record source for term_harvester.py.

Provides schema (slot + enum) generation for the Simple Darwin Core standard
(https://dwc.tdwg.org/xml/tdwg_dwc_simple.xsd, version 2023-09-14).

Unlike other sources, this module always generates slots (one per DWC Simple
field) in the output YAML regardless of the config.yaml 'slots' setting.
Only a subset of fields reference enumerations; most are free-text strings.

Field hierarchy is loaded from a versioned TSV file shipped with this module:
  source_dwc_simple_v{VERSION}.txt  —  7 tab-separated columns:
    1. category       — section name (repeated on every row in the section)
    2. field_name     — verbatim DWC term name (namespace prefix for non-dwc: terms)
    3. slot_name      — snake_case slot key
    4. data_type      — string | integer | decimal | date | datetime | time |
                        boolean | uri | enumeration
    5. enumerations   — comma-sep enum key names; '-' for none
    6. description    — DWC definition
    7. comments       — additional notes, recommendations, and examples

A draw.io mind-map diagram is generated / refreshed when -a or -f is run:
  sources/TDWG_DWC_Simple.drawio  —  sample as central node, 10 branches.

A new-fields report is printed when -f detects differences between the live
XSD and the field list encoded in the versioned .txt file.

Public API used by term_harvester.py:
    match_dwc_simple(url, config_file) -> bool
    fetch_dwc_simple_source(key, source, config_file) -> None
    process_dwc_simple_source(key, source, config_file) -> None
"""

import datetime
import math
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import yaml

from source_utils import (
    MENU_CONFIG,
    BROWSER_HEADERS,
    IndentedDumper,
    make_config_schema,
    make_source_entry,
    write_config,
    update_source_config,
    camel_to_snake,
    sort_prefixes,
)

# ---------------------------------------------------------------------------
# Version constants and paths
# ---------------------------------------------------------------------------

_DWC_SIMPLE_VERSION = "2023-09-14"
_DWC_XSD_URL = "https://dwc.tdwg.org/xml/tdwg_dwc_simple.xsd"
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_DWC_TXT_FILENAME = f"source_dwc_simple_v{_DWC_SIMPLE_VERSION}.txt"
_DWC_TXT_PATH = os.path.join(_MODULE_DIR, _DWC_TXT_FILENAME)
_DWC_TRANSLATIONS_FILENAME = f"source_dwc_simple_v{_DWC_SIMPLE_VERSION}_translations.yaml"
_DWC_TRANSLATIONS_PATH = os.path.join(_MODULE_DIR, _DWC_TRANSLATIONS_FILENAME)

# GBIF-hosted DWC-A schema XMLs — term-level metadata with multilingual translations
_GBIF_EXT_NS  = "http://rs.gbif.org/extension/"
_DC_NS        = "http://purl.org/dc/terms/"
_XML_LANG_ATT = "{http://www.w3.org/XML/1998/namespace}lang"
_GBIF_SCHEMA_URLS = [
    "https://raw.githubusercontent.com/tdwg/dwc/master/dwc-a/schemas/core/occurrence.xml",
    "https://raw.githubusercontent.com/tdwg/dwc/master/dwc-a/schemas/core/taxon.xml",
    "https://raw.githubusercontent.com/tdwg/dwc/master/dwc-a/schemas/core/event.xml",
]

# TDWG vocabulary XMLs on GitHub — enum-level multilingual labels
_THESAURUS_NS      = "http://rs.gbif.org/thesaurus/"
_DC_CONCEPT_URI_ATT = "{http://purl.org/dc/terms/}URI"
_DC_TITLE_ATT      = "{http://purl.org/dc/terms/}title"
_GBIF_VOCAB_TRANSLATIONS_FILENAME = f"source_dwc_simple_v{_DWC_SIMPLE_VERSION}_vocab_translations.yaml"
_GBIF_VOCAB_TRANSLATIONS_PATH = os.path.join(_MODULE_DIR, _GBIF_VOCAB_TRANSLATIONS_FILENAME)
_GBIF_VOCAB_URLS = {
    "DwCPathway":               "https://raw.githubusercontent.com/tdwg/dwc/master/dwc-a/schemas/vocabulary/pathway.xml",
    "DwCEstablishmentMeans":    "https://raw.githubusercontent.com/tdwg/dwc/master/dwc-a/schemas/vocabulary/establishment_means.xml",
    "DwCDegreeOfEstablishment": "https://raw.githubusercontent.com/tdwg/dwc/master/dwc-a/schemas/vocabulary/degree_of_establishment.xml",
}

# Namespace URI map (prefix → base IRI)
_NS_URIS = {
    "dwc":     "http://rs.tdwg.org/dwc/terms/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
}

# ---------------------------------------------------------------------------
# DWC-specific controlled-vocabulary enumerations
# ---------------------------------------------------------------------------

_DWC_ENUMS = {
    "DCMIType": {
        "description": "DCMI Type Vocabulary — the nature or genre of a resource",
        "see_also": ["https://www.dublincore.org/specifications/dublin-core/dcmi-type-vocabulary/"],
        "permissible_values": {
            "Collection":          {"title": "Collection",          "description": "An aggregation of resources.", "meaning": "dcterms:Collection"},
            "Dataset":             {"title": "Dataset",             "description": "Data encoded in a defined structure.", "meaning": "dcterms:Dataset"},
            "Event":               {"title": "Event",               "description": "A non-persistent, time-based occurrence.", "meaning": "dcterms:Event"},
            "Image":               {"title": "Image",               "description": "A visual representation other than text (superset of StillImage and MovingImage).", "meaning": "dcterms:Image"},
            "InteractiveResource": {"title": "Interactive Resource", "description": "A resource requiring interaction from the user to be understood.", "meaning": "dcterms:InteractiveResource"},
            "MovingImage":         {"title": "Moving Image",        "description": "A series of visual representations of movements.", "meaning": "dcterms:MovingImage"},
            "PhysicalObject":      {"title": "Physical Object",     "description": "An inanimate, three-dimensional object or substance.", "meaning": "dcterms:PhysicalObject"},
            "Service":             {"title": "Service",             "description": "A system that provides one or more functions.", "meaning": "dcterms:Service"},
            "Software":            {"title": "Software",            "description": "A computer program in source or compiled form.", "meaning": "dcterms:Software"},
            "Sound":               {"title": "Sound",               "description": "A resource whose content is primarily intended to be rendered as audio.", "meaning": "dcterms:Sound"},
            "StillImage":          {"title": "Still Image",         "description": "A static visual representation.", "meaning": "dcterms:StillImage"},
            "Text":                {"title": "Text",                "description": "A resource whose content is primarily words for reading.", "meaning": "dcterms:Text"},
        },
    },

    "DwCBasisOfRecord": {
        "description": "The specific nature of the data record (Darwin Core controlled vocabulary)",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_basisOfRecord"],
        "examples": ["PreservedSpecimen", "LivingSpecimen", "FossilSpecimen", "HumanObservation",
                     "MachineObservation", "MaterialEntity", "MaterialCitation", "Occurrence"],
    },

    "DwCSex": {
        "description": "The sex of biological individuals represented in the Occurrence",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_sex"],
        "examples": ["male", "female", "hermaphrodite", "indeterminate", "notApplicable", "undetermined"],
    },

    "DwCLifeStage": {
        "description": "The age class or life stage of the biological individual",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_lifeStage"],
        "examples": ["adult", "juvenile", "larva", "egg", "pupa", "nymph", "seedling",
                     "sporophyte", "gametophyte", "spore", "gamete", "zygote", "embryo",
                     "flowering", "fruiting", "dormant"],
    },

    "DwCVitality": {
        "description": "Whether the organism was alive or dead at the time of collection or observation",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_vitality"],
        "examples": ["alive", "dead", "mixedLot", "uncertain", "notApplicable"],
    },

    "DwCEstablishmentMeans": {
        "description": "Establishment Means Controlled Vocabulary List of Terms",
        "see_also": ["https://dwc.tdwg.org/em/"],
        "enum_uri": "http://rs.tdwg.org/dwcem/values/e",
        "permissible_values": {
            "native":                         {"title": "native (indigenous)",                               "description": "A taxon occurring within its natural range.",                                                                                                                                                                       "meaning": "dwcem:e001"},
            "nativeReintroduced":             {"title": "native: reintroduced",                              "description": "A taxon re-established by direct introduction by humans into an area that was once part of its natural range, but from where it had become extinct.",                                                              "meaning": "dwcem:e002"},
            "introduced":                     {"title": "introduced (alien, exotic, non-native, nonindigenous)", "description": "Establishment of a taxon by human agency into an area that is not part of its natural range.",                                                                                                               "meaning": "dwcem:e003"},
            "introducedAssistedColonisation": {"title": "introduced: assisted colonisation",                 "description": "Establishment of a taxon specifically with the intention of creating a self-sustaining wild population in an area that is not part of the taxon's natural range.",                                               "meaning": "dwcem:e004"},
            "vagrant":                        {"title": "vagrant (casual)",                                  "description": "The temporary occurrence of a taxon far outside its natural or migratory range.",                                                                                                                                "meaning": "dwcem:e005"},
            "uncertain":                      {"title": "uncertain (unknown, cryptogenic)",                  "description": "The origin of the occurrence of the taxon in an area is obscure.",                                                                                                                                              "meaning": "dwcem:e006"},
            "nativeEndemic":                  {"title": "native: endemic",                                   "description": "A taxon with a natural distribution restricted to a single geographical area.",                                                                                                                                  "meaning": "dwcem:e007"},
        },
    },

    "DwCDegreeOfEstablishment": {
        "description": "Degree of Establishment Controlled Vocabulary List of Terms",
        "see_also": [
            "https://dwc.tdwg.org/doe/",
            "https://fredi.hepvs.ch/documents/301998/files/bach_puf.pdf",
        ],
        "enum_uri": "http://rs.tdwg.org/dwcdoe/values/d",
        "permissible_values": {
            "A":  {"title": "native",              "description": "Not transported beyond limits of native range.",                                                                                                                                                                                                                       "meaning": "dwcdoe:d001"},
            "B1": {"title": "captive",             "description": "Individuals in captivity or quarantine (i.e. individuals provided with conditions suitable for them, but explicit measures of containment are in place)",                                                                                                               "meaning": "dwcdoe:d002"},
            "B2": {"title": "cultivated",          "description": "Individuals in cultivation (i.e., individuals provided with conditions suitable for them, but explicit measures to prevent dispersal are limited at best).",                                                                                                            "meaning": "dwcdoe:d003"},
            "B3": {"title": "released",            "description": "Individuals directly released into novel environment.",                                                                                                                                                                                                               "meaning": "dwcdoe:d004"},
            "C0": {"title": "failing",             "description": "Individuals released outside of captivity or cultivation in a location, but incapable of surviving for a significant period.",                                                                                                                                         "meaning": "dwcdoe:d005"},
            "C1": {"title": "casual",              "description": "Individuals surviving outside of captivity or cultivation in a location with no reproduction.",                                                                                                                                                                       "meaning": "dwcdoe:d006"},
            "C2": {"title": "reproducing",         "description": "Individuals surviving outside of captivity or cultivation in a location. Reproduction is occurring, but population not self-sustaining.",                                                                                                                             "meaning": "dwcdoe:d007"},
            "C3": {"title": "established",         "description": "Individuals surviving outside of captivity or cultivation in a location. Reproduction occurring, and population self-sustaining.",                                                                                                                                    "meaning": "dwcdoe:d008"},
            "D1": {"title": "colonising",          "description": "Self-sustaining population outside of captivity or cultivation, with individuals surviving a significant distance from the original point of introduction.",                                                                                                           "meaning": "dwcdoe:d009"},
            "D2": {"title": "invasive",            "description": "Self-sustaining population outside of captivity or cultivation, with individuals surviving and reproducing a significant distance from the original point of introduction.",                                                                                           "meaning": "dwcdoe:d010"},
            "E":  {"title": "widespread invasive", "description": "Fully invasive species, with individuals dispersing, surviving and reproducing at multiple sites across a spectrum of habitats and geographic range.",                                                                                                                "meaning": "dwcdoe:d011"},
        },
    },

    "DwCPathway": {
        "description": "Pathway Controlled Vocabulary List of Terms",
        "see_also": [
            "https://dwc.tdwg.org/pw/",
            "https://nora.nerc.ac.uk/id/eprint/519129/1/N519129CR.pdf",
        ],
        "enum_uri": "http://rs.tdwg.org/dwcpw/values/p",
        "permissible_values": {
            "biologicalControl":             {"title": "biological control",                                                           "description": "Organisms occurring in an area because they were introduced for the purpose of biological control of another organism.",                                                                                                                                             "meaning": "dwcpw:p001"},
            "erosionControl":                {"title": "erosion control",                                                              "description": "Organisms introduced for the purpose of erosion control/dune stabilization (windbreaks, hedges, etc.).",                                                                                                                                                "meaning": "dwcpw:p002"},
            "fisheryInTheWild":              {"title": "fishery in the wild",                                                          "description": "Fish stocked into the wild either to create a fishery or for recreational angling.",                                                                                                                                                                 "meaning": "dwcpw:p003"},
            "hunting":                       {"title": "hunting",                                                                      "description": "Animals stocked into the wild specifically with the intention that they would be hunted for sport.",                                                                                                                                                 "meaning": "dwcpw:p004"},
            "landscapeImprovement":          {"title": "landscape improvement",                                                        "description": "Landscape/flora/fauna \"improvement\" in the wild.",                                                                                                                                                                                       "meaning": "dwcpw:p005"},
            "conservationOrWildlifeManagement": {"title": "conservation or wildlife management",                                       "description": "Organisms introduced for conservation purposes or wildlife management.",                                                                                                                                                                       "meaning": "dwcpw:p006"},
            "releasedForUse":                {"title": "released for use",                                                             "description": "Release in nature for use (other than above, e.g., fur, transport, medical use).",                                                                                                                                                             "meaning": "dwcpw:p007"},
            "otherIntentionalRelease":       {"title": "other intentional release",                                                    "description": "A catch-all term for intentional releases not for human use that are not covered by other more specific terms.",                                                                                                                               "meaning": "dwcpw:p008"},
            "agriculture":                   {"title": "agriculture (including biofuel feedstocks)",                                   "description": "Plants grown with the intention of harvesting.",                                                                                                                                                                                           "meaning": "dwcpw:p009"},
            "aquacultureMariculture":        {"title": "aquaculture/mariculture",                                                      "description": "The analog of agriculture and farmed animals, specifically related to aquatic organisms.",                                                                                                                                                   "meaning": "dwcpw:p010"},
            "publicGardenZooAquaria":        {"title": "botanic garden/zoo/aquaria (excluding domestic aquaria)",                      "description": "Organisms in public collections of plants and/or animals.",                                                                                                                                                                                 "meaning": "dwcpw:p011"},
            "pet":                           {"title": "pet/aquarium/terrarium species (including live food for such species)",        "description": "Privately kept animals.",                                                                                                                                                                                                                 "meaning": "dwcpw:p012"},
            "farmedAnimals":                 {"title": "farmed animals (including animals left under limited control)",                "description": "Animals cared for and bred with the specific intention of using their products, such as meat and milk.",                                                                                                                                       "meaning": "dwcpw:p013"},
            "forestry":                      {"title": "forestry (including reforestation)",                                           "description": "Trees specifically introduced to provide timber and other forestry products.",                                                                                                                                                               "meaning": "dwcpw:p014"},
            "fur":                           {"title": "fur farms",                                                                    "description": "Organisms escaped from a fur farm, including unauthorised releases.",                                                                                                                                                                       "meaning": "dwcpw:p015"},
            "horticulture":                  {"title": "horticulture",                                                                 "description": "Plants distributed by the ornamental and decorative plants industry.",                                                                                                                                                                       "meaning": "dwcpw:p016"},
            "ornamentalNonHorticulture":     {"title": "ornamental purpose other than horticulture",                                   "description": "Ornamental plants introduced through pathways other than the horticultural industry.",                                                                                                                                                       "meaning": "dwcpw:p017"},
            "research":                      {"title": "research and ex-situ breeding (in facilities)",                                "description": "Plants and animals introduced for the purpose of breeding or scientific and medical research, including science education.",                                                                                                                   "meaning": "dwcpw:p018"},
            "liveFoodLiveBait":              {"title": "live food and live bait",                                                      "description": "Live food imported for human consumption or live bait, such as shellfish and snails.",                                                                                                                                                       "meaning": "dwcpw:p019"},
            "otherEscape":                   {"title": "other escape from confinement",                                                "description": "Organisms brought into an area with the intention of keeping them in captivity permanently, but that have subsequently escaped.",                                                                                                               "meaning": "dwcpw:p020"},
            "contaminantNursery":            {"title": "contaminant nursery material",                                                 "description": "Organisms transported into an area together with plant material.",                                                                                                                                                                         "meaning": "dwcpw:p021"},
            "contaminateBait":               {"title": "contaminated bait",                                                            "description": "Contaminants, pathogens and parasites transported with live, frozen or preserved bait used to catch fish or other organisms.",                                                                                                               "meaning": "dwcpw:p022"},
            "foodContaminant":               {"title": "food contaminant (including of live food)",                                    "description": "Foods for human consumption, whether they are transported live or dead.",                                                                                                                                                                   "meaning": "dwcpw:p023"},
            "contaminantOnAnimals":          {"title": "contaminant on animals (except parasites, organisms transported by host/vector)", "description": "Contaminants carried either on or in the body of transported animals.",                                                                                                                                                             "meaning": "dwcpw:p024"},
            "parasitesOnAnimals":            {"title": "parasites on animals (including organisms transported by host and vector)",    "description": "Parasitic and pathogenic organisms transported with their host or vector animal.",                                                                                                                                                       "meaning": "dwcpw:p025"},
            "contaminantOnPlants":           {"title": "contaminant on plants (except parasites, species transported by host/vector)", "description": "Organisms transported on plant material.",                                                                                                                                                                                             "meaning": "dwcpw:p026"},
            "parasitesOnPlants":             {"title": "parasites on plants (including species transported by host and vector)",       "description": "Parasitic and pathogenic organisms transported with their host or vector plant.",                                                                                                                                                       "meaning": "dwcpw:p027"},
            "seedContaminant":               {"title": "seed contaminant",                                                             "description": "Organisms contaminating transported seeds.",                                                                                                                                                                                           "meaning": "dwcpw:p028"},
            "timberTrade":                   {"title": "timber trade",                                                                 "description": "Contaminants on unprocessed timber, processed wood and wood-derived products.",                                                                                                                                                         "meaning": "dwcpw:p029"},
            "transportationHabitatMaterial": {"title": "transportation of habitat material (soil, vegetation, wood etc)",              "description": "Organisms transported with their habitat material to a new location.",                                                                                                                                                                 "meaning": "dwcpw:p030"},
            "fishingEquipment":              {"title": "angling/fishing equipment",                                                    "description": "Aquatic organisms moved between sites on equipment of recreational anglers and professional fishermen.",                                                                                                                                 "meaning": "dwcpw:p031"},
            "containerBulk":                 {"title": "container/bulk",                                                               "description": "Stowaways transported in or on cargo containers or bulk cargo units.",                                                                                                                                                                 "meaning": "dwcpw:p032"},
            "hitchhikersAirplane":           {"title": "hitchhikers in or on airplane",                                                "description": "Organisms that enter airplanes or other aircraft, such as helicopters, and are transported by them to another location.",                                                                                                               "meaning": "dwcpw:p033"},
            "hitchhikersShip":               {"title": "hitchhikers on ship/boat (excluding ballast water and hull fouling)",          "description": "Organisms that enter directly onto boats or ships and are transported by them to another location.",                                                                                                                                   "meaning": "dwcpw:p034"},
            "machineryEquipment":            {"title": "machinery/equipment",                                                          "description": "Organisms carried on the surfaces of or within heavy machinery and equipment.",                                                                                                                                                         "meaning": "dwcpw:p035"},
            "people":                        {"title": "people and their luggage/equipment (in particular tourism)",                   "description": "Organisms transported on people and/or their personal luggage.",                                                                                                                                                                       "meaning": "dwcpw:p036"},
            "packingMaterial":               {"title": "organic packing material, in particular wood packaging",                       "description": "Organic material, particularly unprocessed plant material that is used to pack transported goods.",                                                                                                                                     "meaning": "dwcpw:p037"},
            "ballastWater":                  {"title": "ship/boat ballast water",                                                      "description": "Organisms transported within the water pumped into boats and ships to provide ballast.",                                                                                                                                               "meaning": "dwcpw:p038"},
            "hullFouling":                   {"title": "ship/boat hull fouling",                                                       "description": "Organisms that attach themselves to the subsurface hull of boats and ships.",                                                                                                                                                         "meaning": "dwcpw:p039"},
            "vehicles":                      {"title": "vehicles (car, train, etc.)",                                                  "description": "Other vehicle hitchhikers that have been unintentionally dispersed, but are not covered by other terms.",                                                                                                                               "meaning": "dwcpw:p040"},
            "otherTransport":                {"title": "other means of transport",                                                     "description": "A catchall term for any transport-related dispersal that is not covered in other terms.",                                                                                                                                               "meaning": "dwcpw:p041"},
            "waterwaysBasinsSeas":           {"title": "interconnected waterways/basins/seas",                                         "description": "Organisms that dispersed through artificial waterways created to connect previously unconnected water bodies.",                                                                                                                       "meaning": "dwcpw:p042"},
            "tunnelsBridges":                {"title": "tunnels and land bridges",                                                     "description": "Unintentional dispersal by organisms using artificial tunnels, bridges, roads and railways.",                                                                                                                                         "meaning": "dwcpw:p043"},
            "naturalDispersal":              {"title": "natural dispersal across borders of invasive alien organisms",                 "description": "Dispersal of organisms to new regions by natural dispersal from regions in which they are alien.",                                                                                                                                   "meaning": "dwcpw:p044"},
            "releaseInNature":               {"title": "release in nature",                                                            "description": "Organisms transported and released by humans in a (semi)natural environment with the intention that they should live there without further human aid.",                                                                                 "meaning": "dwcpw:p045"},
            "escapeFromConfinement":         {"title": "escape from confinement",                                                      "description": "Organisms intentionally transported by humans and intended to be kept in captivity or cultivation, but having inadvertently escaped from human control.",                                                                               "meaning": "dwcpw:p046"},
            "transportContaminant":          {"title": "transport-contaminant",                                                        "description": "An umbrella term for all species transported as contaminants in other products.",                                                                                                                                                   "meaning": "dwcpw:p047"},
            "transportStowaway":             {"title": "transport-stowaway",                                                           "description": "An umbrella term for all species transported by riding on forms of transport where the organism has a direct interaction with the transport and is not merely carried as part of, or a contaminant of cargo.",                       "meaning": "dwcpw:p048"},
            "corridor":                      {"title": "corridor",                                                                     "description": "Infrastructures, such as bridges, tunnels and canals, have removed natural barriers to dispersal and allowed a species to move into a novel location.",                                                                                 "meaning": "dwcpw:p049"},
            "unaided":                       {"title": "unaided",                                                                      "description": "Organisms that spread by natural dispersal, without action or assistance by humans, from regions in which they are also alien.",                                                                                                       "meaning": "dwcpw:p050"},
            "intentional":                   {"title": "intentional",                                                                  "description": "Organisms were brought to a new area with the specific intention of keeping them alive in the new region, regardless of whether they were intended to be cultivated or released into the wild.",                                     "meaning": "dwcpw:p051"},
            "unintentional":                 {"title": "unintentional",                                                                "description": "The organism was unintentionally brought to a new region.",                                                                                                                                                                         "meaning": "dwcpw:p052"},
            "corridorAndDispersal":          {"title": "corridor and dispersal",                                                       "description": "Organisms dispersed naturally, even if that dispersal was aided by changes in the landscape created by humans.",                                                                                                                     "meaning": "dwcpw:p053"},
        },
    },

    "DwCOccurrenceStatus": {
        "description": "Statement about the presence or absence of a Taxon at a Location",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_occurrenceStatus"],
        "examples": ["detected", "notDetected"],
    },

    "DwCContinent": {
        "description": "Name of the continent in which the dcterms:Location occurs",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_continent"],
        "examples": ["Africa", "Antarctica", "Asia", "Europe", "NorthAmerica", "Oceania", "SouthAmerica"],
    },

    "DwCMaterialEntityType": {
        "description": "The kind of material entity being described",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_materialEntityType"],
        "examples": ["Tissue", "Blood", "Bone", "Feather", "Scale", "Shell", "Skull",
                     "Seed", "Leaf", "Root", "Flower", "Fossil", "Rock", "Soil",
                     "Water", "DNA", "RNA", "WholeOrganism"],
    },

    "DwCDisposition": {
        "description": "The current state of a dwc:MaterialEntity with respect to a collection",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_disposition"],
        "examples": ["inCollection", "missing", "voucherElsewhere", "discarded",
                     "destroyed", "duplicate", "lostInDebt", "onLoan"],
    },

    "DwCGeoreferenceVerificationStatus": {
        "description": "Categorical description of the extent to which the georeference has been verified",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_georeferenceVerificationStatus"],
        "examples": ["requiresVerification", "verified", "notVerified"],
    },

    "DwCTaxonRank": {
        "description": "The taxonomic rank of the most specific name in the scientificName",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_taxonRank"],
        "examples": ["kingdom", "phylum", "class", "order", "superfamily", "family",
                     "subfamily", "tribe", "subtribe", "genus", "subgenus", "section",
                     "series", "species", "subspecies", "variety", "subvariety",
                     "form", "cultivar", "strain"],
    },

    "DwCNomenclaturalCode": {
        "description": "The nomenclatural code under which the scientificName is constructed",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_nomenclaturalCode"],
        "examples": ["ICZN", "ICNafp", "ICNCP", "ICTV", "ICNB", "BioCode", "PhyloCode"],
    },

    "DwCTaxonomicStatus": {
        "description": "The status of the use of the scientificName as a label for a taxon",
        "see_also": ["https://dwc.tdwg.org/list/#dwc_taxonomicStatus"],
        "examples": ["accepted", "synonym", "heterotypicSynonym", "homotypicSynonym",
                     "proParteSynonym", "misapplied", "doubtful", "artificialHybrid"],
    },
}

# ---------------------------------------------------------------------------
# Human-readable section metadata (for the draw.io diagram)
# ---------------------------------------------------------------------------

_SECTION_META = {
    "Record-Level Terms": {
        "key_fields": ["basisOfRecord", "type", "institutionCode", "collectionCode", "license"],
        "fill": "#dae8fc", "stroke": "#6c8ebf",
    },
    "Occurrence": {
        "key_fields": ["occurrenceID", "sex", "lifeStage", "occurrenceStatus", "recordedBy"],
        "fill": "#fff2cc", "stroke": "#d6b656",
    },
    "Organism": {
        "key_fields": ["organismID", "organismName", "associatedOrganisms"],
        "fill": "#f8cecc", "stroke": "#b85450",
    },
    "Material Entity": {
        "key_fields": ["materialEntityID", "materialEntityType", "preparations", "disposition"],
        "fill": "#ffe6cc", "stroke": "#d79b00",
    },
    "Material Sample": {
        "key_fields": ["materialSampleID"],
        "fill": "#e1d5e7", "stroke": "#9673a6",
    },
    "Event (Collection / Observation)": {
        "key_fields": ["eventDate", "habitat", "samplingProtocol", "eventType", "eventID"],
        "fill": "#c8f0d8", "stroke": "#339966",
    },
    "Location (Geography)": {
        "key_fields": ["decimalLatitude", "decimalLongitude", "country", "locality", "geodeticDatum"],
        "fill": "#c0e0ff", "stroke": "#3366cc",
    },
    "Geological Context": {
        "key_fields": ["formation", "member", "bed", "earliestEpochOrLowestSeries"],
        "fill": "#fff0b0", "stroke": "#cc9900",
    },
    "Identification (Taxonomic)": {
        "key_fields": ["identifiedBy", "identificationQualifier", "typeStatus", "dateIdentified"],
        "fill": "#ffd8d8", "stroke": "#cc3333",
    },
    "Taxon (Classification)": {
        "key_fields": ["scientificName", "taxonRank", "kingdom→family", "NCBITaxon recommended"],
        "fill": "#d5e8d4", "stroke": "#82b366",
    },
}

# ---------------------------------------------------------------------------
# Parse the versioned TSV field hierarchy file
# ---------------------------------------------------------------------------

def _parse_txt_file(txt_path=None):
    """Parse the versioned TSV field hierarchy file.

    File format (7 tab-separated columns):
        1. category     — section name (repeated on every row in the section)
        2. field_name   — verbatim DWC term name (with namespace prefix for non-dwc: terms)
        3. slot_name    — snake_case slot key
        4. data_type    — string | integer | decimal | date | datetime | time | boolean | uri | enumeration
        5. enumerations — comma-sep enum keys or '-'
        6. description  — DWC definition
        7. comments     — additional notes, recommendations, and examples
    Section header rows have an empty field_name and data_type '-'.

    Returns a list of section dicts, each with:
        section:     str — section name
        description: str — section description (from section header row)
        fields:      list of field dicts, each with:
            name:         str — full field name (e.g. 'dc:type' or 'occurrenceID')
            namespace:    str — 'dc', 'dcterms', or 'dwc'
            local_name:   str — local name without namespace prefix
            snake_name:   str — snake_case slot key (from col 3)
            data_type:    str — 'string', 'integer', 'decimal', 'date', etc.
            enumerations: list[str] — recommended enum keys
            description:  str — DWC definition
            comments:     str — additional notes, recommendations, examples
    """
    if txt_path is None:
        txt_path = _DWC_TXT_PATH
    if not os.path.exists(txt_path):
        print(f"Warning: DWC Simple field file not found: {txt_path}", file=sys.stderr)
        return []

    sections_map = {}   # section_name → section dict (preserves first-seen order)
    sections_order = []

    with open(txt_path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if not line or line.startswith("#"):
                continue

            parts = line.split("\t")
            while len(parts) < 7:
                parts.append("-")
            cat_col, fname_col, snake_col, type_col, enum_col, desc_col, comm_col = parts[:7]

            cat = cat_col.strip()
            if not cat:
                continue   # blank category — skip

            # Ensure section exists
            if cat not in sections_map:
                sections_map[cat] = {"section": cat, "description": "", "fields": []}
                sections_order.append(cat)

            # Section header row: empty field name
            if not fname_col.strip():
                sec_desc = desc_col.strip()
                if sec_desc and sec_desc != "-":
                    sections_map[cat]["description"] = sec_desc
                continue

            # Field row
            field_name = fname_col.strip()
            snake_name = snake_col.strip()

            if ":" in field_name:
                namespace, local_name = field_name.split(":", 1)
            else:
                namespace  = "dwc"
                local_name = field_name

            # snake_name from file is authoritative; fall back to camel_to_snake
            if not snake_name or snake_name == "-":
                snake_name = camel_to_snake(local_name)

            enums = (
                [e.strip() for e in enum_col.split(",") if e.strip() and e.strip() != "-"]
                if enum_col.strip() not in ("", "-")
                else []
            )

            sections_map[cat]["fields"].append({
                "name":         field_name,
                "namespace":    namespace,
                "local_name":   local_name,
                "snake_name":   snake_name,
                "data_type":    type_col.strip() if type_col.strip() not in ("", "-") else "string",
                "enumerations": enums,
                "description":  desc_col.strip() if desc_col.strip() not in ("", "-") else "",
                "comments":     comm_col.strip() if comm_col.strip() not in ("", "-") else "",
            })

    return [sections_map[k] for k in sections_order]


def _camel_to_title(name):
    """Convert camelCase (or PascalCase) to 'Title Case' words, keeping acronyms intact."""
    # Split at lower→upper boundary (e.g. "occurrenceID" → "occurrence ID")
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    # Split before last uppercase in an all-caps run followed by lowercase (e.g. "HTMLParser" → "HTML Parser")
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    # Capitalise first letter of each word; leave all-caps words (acronyms) intact
    return " ".join(w if w.isupper() else w.capitalize() for w in s.split())


def _all_field_local_names(sections):
    """Return a flat set of all local field names parsed from sections."""
    return {f["local_name"] for s in sections for f in s["fields"]}


# ---------------------------------------------------------------------------
# XSD version / field-change check
# ---------------------------------------------------------------------------

def _extract_xsd_info(xsd_text):
    """Return (version_string, set_of_element_local_names) from XSD text.

    Looks for a date-like version in a comment or schemaVersion attribute,
    then collects all xs:element @name values (excluding root-level wrappers).
    Returns (None, set()) on parse errors.
    """
    # Try to extract version date from comments
    version = None
    m = re.search(r"version[:\s]+(\d{4}-\d{2}-\d{2})", xsd_text, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", xsd_text[:500])
    if m:
        version = m.group(1)

    # Extract element names via regex (avoids namespace prefix confusion)
    names = set(re.findall(r'<xs:element[^>]+name=["\']([^"\']+)["\']', xsd_text))
    # Remove root-level wrapper names
    names.discard("SimpleDarwinRecordSet")
    names.discard("SimpleDarwinRecord")
    names.discard("SimpleDarwinExtensions")
    names.discard("anyExtension")

    return version, names


def _check_xsd_fields(key, source, config_file):
    """Download the XSD, check version and field list, report any changes.

    Saves the XSD to sources/{key}.xsd and updates download_date in config.
    Returns the XSD text (str) or None on error.
    """
    url = (source.get("reachable_from") or {}).get("source_ontology", _DWC_XSD_URL)
    print(f"  Fetching {url} ...")
    try:
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            xsd_bytes = resp.read()
        xsd_text = xsd_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  Warning: could not fetch XSD: {e}", file=sys.stderr)
        return None

    xsd_path = f"sources/{key}.xsd"
    with open(xsd_path, "w", encoding="utf-8") as fh:
        fh.write(xsd_text)
    print(f"  XSD saved to {xsd_path}")

    live_version, live_names = _extract_xsd_info(xsd_text)
    known_sections = _parse_txt_file()
    known_names = _all_field_local_names(known_sections)

    # Version comparison
    if live_version and live_version != _DWC_SIMPLE_VERSION:
        print(
            f"\n  *** DWC Simple XSD version changed: {_DWC_SIMPLE_VERSION} → {live_version} ***\n"
            f"      Review changes below and create:\n"
            f"        sources/source_dwc_simple_v{live_version}.txt\n"
            f"      then update _DWC_SIMPLE_VERSION in source_dwc_simple.py\n",
            file=sys.stderr,
        )
    elif live_version:
        print(f"  XSD version: {live_version} (matches module version)")

    # Field comparison
    new_fields = live_names - known_names
    removed_fields = known_names - live_names if live_names else set()

    if new_fields:
        print(
            f"\n  *** New fields in live XSD (not in {_DWC_TXT_FILENAME}): ***\n"
            + "\n".join(f"    + {n}" for n in sorted(new_fields)),
            file=sys.stderr,
        )
    if removed_fields and live_names:
        print(
            f"\n  *** Fields in {_DWC_TXT_FILENAME} but NOT in live XSD: ***\n"
            + "\n".join(f"    - {n}" for n in sorted(removed_fields)),
            file=sys.stderr,
        )
    if not new_fields and not removed_fields and live_names:
        print(f"  Field list matches ({len(known_names)} fields)")

    update_source_config(key, {"download_date": datetime.date.today().isoformat()}, config_file)
    return xsd_text


# ---------------------------------------------------------------------------
# Draw.io diagram generator
# ---------------------------------------------------------------------------

def _generate_drawio_diagram(output_path, sections=None):
    """Generate a draw.io diagram file for the DWC Simple Record structure.

    Writes an A3-landscape .drawio file with the Darwin Core Simple Record
    as the central ellipse and 10 category boxes arranged in a rectangular
    grid around it.  For other section counts falls back to a radial layout.
    """
    if sections is None:
        sections = _parse_txt_file()

    page_w, page_h = 1654, 1169
    box_w, box_h = 220, 130

    # Rectangular grid positions (x, y) for 10 sections arranged clockwise
    # from top-left.  Index matches section order in the TSV.
    _GRID_POSITIONS = [
        (711, 320),   # s0  Record-Level Terms  — top centre-right
        (952, 320),   # s1  Occurrence           — top right
        (1210, 395),  # s2  Organism             — right top
        (1210, 642),  # s3  Material Entity      — right bottom
        (952, 700),   # s4  Material Sample      — bottom right
        (711, 700),   # s5  Event               — bottom centre
        (470, 700),   # s6  Location            — bottom left
        (205, 642),   # s7  Geological Context   — left bottom (wider box)
        (220, 395),   # s8  Identification       — left top
        (470, 320),   # s9  Taxon               — top left
    ]
    # s7 (Geological Context) needs extra width for long field names
    _WIDE_SECTIONS = {7: 250}

    section_names = [s["section"] for s in sections]
    n_sections    = len(section_names)
    use_grid      = (n_sections == len(_GRID_POSITIONS))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (f'<mxGraphModel dx="1475" dy="1159" grid="0" gridSize="10" guides="1" '
         f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
         f'pageWidth="{page_w}" pageHeight="{page_h}" math="0" shadow="0">'),
        "  <root>",
        "    <mxCell id=\"0\"/>",
        "    <mxCell id=\"1\" parent=\"0\"/>",
    ]

    edge_style_base = (
        "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;"
        "jettySize=auto;strokeWidth=2;fontSize=17;"
    )

    for i, sec_name in enumerate(section_names):
        if use_grid:
            sec_x, sec_y = _GRID_POSITIONS[i]
            w = _WIDE_SECTIONS.get(i, box_w)
        else:
            # Radial fallback for non-standard section counts
            cx, cy = page_w // 2, page_h // 2
            radius = 400
            angle  = -math.pi / 2 + 2 * math.pi * i / n_sections
            sec_x  = int(cx + radius * math.cos(angle) - box_w / 2)
            sec_y  = int(cy + radius * math.sin(angle) - box_h / 2)
            w = box_w

        meta       = _SECTION_META.get(sec_name, {"key_fields": [], "fill": "#f5f5f5", "stroke": "#666666"})
        fill       = meta["fill"]
        stroke     = meta["stroke"]
        key_fields = meta.get("key_fields", [])[:5]

        kf_html    = "".join(f"&lt;br/&gt;• {kf}" for kf in key_fields)
        short_name = re.sub(r'\s*\(.*?\)', '', sec_name).strip()
        label      = f"&lt;b&gt;{short_name}&lt;/b&gt;" + kf_html

        sec_id    = f"s{i}"
        sec_style = (
            f"rounded=1;whiteSpace=wrap;html=1;arcSize=10;shadow=1;"
            f"fillColor={fill};strokeColor={stroke};strokeWidth=2;"
            f"verticalAlign=top;align=left;spacingLeft=5;fontSize=17;"
        )
        lines.append(
            f'    <mxCell id="{sec_id}" value="{label}" style="{sec_style}" '
            f'parent="1" vertex="1">'
            f'<mxGeometry x="{sec_x}" y="{sec_y}" width="{w}" height="{box_h}" as="geometry"/>'
            f'</mxCell>'
        )

        # Edge from centre to section — no fixed entry/exit points so draw.io
        # auto-snaps the connector to the nearest point on each shape.
        e_style = edge_style_base + f"strokeColor={stroke};"
        lines.append(
            f'    <mxCell id="e{i}" style="{e_style}" parent="1" '
            f'source="c0" target="{sec_id}" edge="1">'
            f'<mxGeometry relative="1" as="geometry"/>'
            f'</mxCell>'
        )

    # Centre node last so section edges resolve cleanly
    if use_grid:
        cnode_x, cnode_y, cnode_w, cnode_h = 725, 530, 193, 110
    else:
        cx, cy = page_w // 2, page_h // 2
        cnode_x, cnode_y, cnode_w, cnode_h = cx - 97, cy - 55, 193, 110

    center_style = (
        "ellipse;whiteSpace=wrap;html=1;"
        "fillColor=#0050ef;fontColor=#ffffff;strokeColor=#001DBC;"
        "fontSize=21;fontStyle=1;shadow=1;arcSize=50;"
    )
    lines.append(
        f'    <mxCell id="c0" value="Darwin Core Simple Record" '
        f'style="{center_style}" parent="1" vertex="1">'
        f'<mxGeometry x="{cnode_x}" y="{cnode_y}" width="{cnode_w}" height="{cnode_h}" as="geometry"/>'
        f'</mxCell>'
    )

    lines += [
        "  </root>",
        "</mxGraphModel>",
    ]

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"  Diagram saved to {output_path}")


# ---------------------------------------------------------------------------
# Public API: match, fetch, process
# ---------------------------------------------------------------------------

def match_dwc_simple(url, config_file=MENU_CONFIG):
    """Return True if url is the DWC Simple XSD URL and was handled.

    This is a pre-download detector; it handles its own setup and processing
    so the caller can skip the normal download/detection pipeline.
    """
    if not re.search(r"tdwg[_.-]dwc[_.-]simple\.xsd", url, re.IGNORECASE):
        return False

    key = "TDWG_DWC_Simple"

    # Check for existing registration
    try:
        with open(config_file) as fh:
            config = yaml.safe_load(fh) or {}
    except (FileNotFoundError, OSError):
        config = {}

    if key in config.get("sources", {}):
        print(f"  Source '{key}' is already registered in {config_file} — skipping.",
              file=sys.stderr)
        return True

    # Register source entry
    entry = make_source_entry(
        key, url, "TDWG_DWC_Simple", "xsd",
        title="Darwin Core Simple Record",
        version=_DWC_SIMPLE_VERSION,
        description="Simple Darwin Core field definitions for biodiversity data exchange",
    )
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"Added source '{key}' (content_type: TDWG_DWC_Simple, v{_DWC_SIMPLE_VERSION})")

    # Fetch (XSD check + diagram) then process (YAML generation)
    fetch_dwc_simple_source(key, entry, config_file)
    process_dwc_simple_source(key, entry, config_file)
    return True


def _fetch_gbif_term_data():
    """Fetch GBIF DWC-A schema XMLs and return a qualName → term-data dict.

    Fetches occurrence.xml, taxon.xml, and event.xml from the TDWG/GBIF GitHub
    repository.  Each <property> element carries English label, definition
    (dc:description), comments (notes), and examples as attributes, plus
    <translation> children for cs/es/fr/ru/zh.

    Returns:
        dict: qualName (str) → {
            "label":        str,
            "description":  str,
            "comments":     str,
            "examples":     str,
            "translations": {lang_code: {"label": str, "description": str, "comments": str}}
        }
    Terms are deduplicated across files; first occurrence wins.
    """
    prop_tag  = f"{{{_GBIF_EXT_NS}}}property"
    trans_tag = f"{{{_GBIF_EXT_NS}}}translation"
    desc_att  = f"{{{_DC_NS}}}description"

    terms = {}
    for url in _GBIF_SCHEMA_URLS:
        print(f"  Fetching {url} ...")
        try:
            req = urllib.request.Request(url, headers=BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                xml_text = resp.read().decode("utf-8")
        except Exception as e:
            print(f"  Warning: could not fetch {url}: {e}", file=sys.stderr)
            continue

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            print(f"  Warning: XML parse error in {url}: {e}", file=sys.stderr)
            continue

        for prop in root.iter(prop_tag):
            qual_name = prop.get("qualName")
            if not qual_name or qual_name in terms:
                continue

            translations = {}
            for trans in prop.findall(trans_tag):
                lang = trans.get(_XML_LANG_ATT)
                if lang:
                    translations[lang] = {
                        "label":       trans.get("label", ""),
                        "description": trans.get(desc_att, ""),
                        "comments":    trans.get("comments", ""),
                    }

            terms[qual_name] = {
                "label":        prop.get("label", ""),
                "description":  prop.get(desc_att, ""),
                "comments":     prop.get("comments", ""),
                "examples":     prop.get("examples", ""),
                "translations": translations,
            }

    return terms


def _update_txt_from_gbif(terms_data):
    """Update the versioned TSV file's comments column from GBIF term data.

    For each field row whose qualName has a non-empty 'comments' in terms_data,
    replaces column 7 with that text (sanitised for TSV).  Also writes
    multilingual translations to a versioned sidecar YAML file alongside
    the TSV so locale-aware consumers can use them later.
    """
    if not terms_data:
        return
    if not os.path.exists(_DWC_TXT_PATH):
        print(f"  Warning: TSV file not found: {_DWC_TXT_PATH}", file=sys.stderr)
        return

    def _to_qual_name(field_name):
        if ":" in field_name:
            prefix, local = field_name.split(":", 1)
        else:
            prefix, local = "dwc", field_name
        base = _NS_URIS.get(prefix)
        return (base + local) if base else None

    with open(_DWC_TXT_PATH, encoding="utf-8") as fh:
        raw = fh.read()

    lines = raw.splitlines()
    out   = []
    updated = 0

    for line in lines:
        if not line or line.startswith("#"):
            out.append(line)
            continue

        parts = line.split("\t")
        while len(parts) < 7:
            parts.append("-")

        field_name = parts[1].strip()
        if not field_name:
            # Section header row — leave untouched
            out.append(line)
            continue

        qual_name = _to_qual_name(field_name)
        if qual_name and qual_name in terms_data:
            gbif_comments = terms_data[qual_name].get("comments", "").strip()
            if gbif_comments:
                # Sanitise: no tabs or newlines inside a TSV cell
                gbif_comments = gbif_comments.replace("\t", " ").replace("\r", "").replace("\n", " ")
                parts[6] = gbif_comments
                updated += 1

        out.append("\t".join(parts))

    with open(_DWC_TXT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out) + "\n")

    print(f"  Updated {updated} term comments in {_DWC_TXT_FILENAME}")

    # Save translations sidecar
    translations_data = {
        qn: td["translations"]
        for qn, td in terms_data.items()
        if td.get("translations")
    }
    if translations_data:
        with open(_DWC_TRANSLATIONS_PATH, "w", encoding="utf-8") as fh:
            yaml.dump(
                translations_data, fh, Dumper=IndentedDumper,
                default_flow_style=False, sort_keys=True, allow_unicode=True,
            )
        print(f"  Saved translations ({len(translations_data)} terms) → {_DWC_TRANSLATIONS_FILENAME}")


def _fetch_gbif_vocab_translations():
    """Fetch TDWG vocabulary XMLs from GitHub and return per-enum, per-lang, per-key title maps.

    Returns:
        dict: {enum_name: {lang_code: {controlled_value: title_str}}}
    Only preferred-term labels are present; descriptions are English-only in these files.
    """
    concept_tag   = f"{{{_THESAURUS_NS}}}concept"
    preferred_tag = f"{{{_THESAURUS_NS}}}preferred"
    term_tag      = f"{{{_THESAURUS_NS}}}term"

    result = {}
    for enum_name, url in _GBIF_VOCAB_URLS.items():
        print(f"  Fetching {url} ...")
        try:
            req = urllib.request.Request(url, headers=BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                xml_bytes = resp.read()
        except Exception as e:
            print(f"  Warning: could not fetch vocab translations for {enum_name}: {e}", file=sys.stderr)
            continue
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as e:
            print(f"  Warning: XML parse error for {enum_name}: {e}", file=sys.stderr)
            continue

        lang_map = {}
        for concept in root.iter(concept_tag):
            concept_iri = concept.get(_DC_CONCEPT_URI_ATT)
            if not concept_iri:
                continue
            preferred = concept.find(preferred_tag)
            if preferred is None:
                continue
            for term in preferred.findall(term_tag):
                lang  = term.get(_XML_LANG_ATT)
                title = term.get(_DC_TITLE_ATT, "")
                if lang and title:
                    lang_map.setdefault(lang, {})[concept_iri] = title

        result[enum_name] = lang_map
        counts = {l: len(v) for l, v in lang_map.items()}
        print(f"    {enum_name}: {counts}")

    return result


def fetch_dwc_simple_source(key, source, config_file=MENU_CONFIG):
    """Check live XSD for version/field changes and regenerate the diagram.

    Downloads the XSD to sources/{key}.xsd, compares field names against
    the versioned .txt file, reports any differences, and writes/updates
    sources/{key}.drawio.
    """
    _check_xsd_fields(key, source, config_file)

    # Fetch GBIF schema XMLs → update TSV comments + save translations sidecar
    terms_data = _fetch_gbif_term_data()
    if terms_data:
        _update_txt_from_gbif(terms_data)

    # Fetch TDWG vocabulary XMLs → save enum vocab translations sidecar
    vocab_trans = _fetch_gbif_vocab_translations()
    if vocab_trans:
        with open(_GBIF_VOCAB_TRANSLATIONS_PATH, "w", encoding="utf-8") as fh:
            yaml.dump(
                vocab_trans, fh, Dumper=IndentedDumper,
                default_flow_style=False, sort_keys=True, allow_unicode=True,
            )
        total = sum(len(km) for em in vocab_trans.values() for km in em.values())
        print(f"  Vocab translations sidecar: {total} entries → {_GBIF_VOCAB_TRANSLATIONS_FILENAME}")

    diagram_path = os.path.join(_MODULE_DIR, f"{key}.drawio")
    sections = _parse_txt_file()
    if sections:
        _generate_drawio_diagram(diagram_path, sections)
    else:
        print(f"  Warning: no field data loaded — diagram not generated", file=sys.stderr)


def process_dwc_simple_source(key, source, config_file=MENU_CONFIG):
    """Generate sources/{key}.yaml with all DWC Simple slots and enum definitions.

    Reads field definitions from the versioned .txt file shipped with this
    module.  Always generates full slot definitions regardless of the global
    config.yaml 'slots' setting.

    Output YAML includes:
        slots  — one slot per DWC Simple field (snake_case key, full metadata)
        enums  — DWC-specific controlled vocabularies with permissible values
        classes — DarwinCoreSimpleRecord class listing all slots
        prefixes — dwc:, dc:, dcterms:
    """
    sections = _parse_txt_file()
    if not sections:
        print(f"  Error: could not load field data from {_DWC_TXT_PATH}", file=sys.stderr)
        return

    slots            = {}
    qn_to_slot       = {}   # qualName IRI → slot_name, for locale translation lookup
    referenced_enums = set()

    for sec in sections:
        sg_name = sec["section"]

        for field in sec["fields"]:
            local_name  = field["local_name"]
            namespace   = field["namespace"]
            data_type   = field["data_type"]
            enums       = field["enumerations"]

            # Use slot_name from file (col 3) if available; fall back to camel_to_snake
            slot_name        = field.get("snake_name") or camel_to_snake(local_name)
            qual_iri         = _NS_URIS.get(namespace, "") + local_name
            qn_to_slot[qual_iri] = slot_name
            title            = _camel_to_title(local_name)
            slot_uri         = f"{namespace}:{local_name}"
            description_text = field.get("description", "")
            comments_text    = field.get("comments", "")

            # Determine LinkML range
            if data_type == "enumeration" and enums:
                # Only constrain range to enum when it has prescribed permissible_values
                primary_enum = enums[0]
                if primary_enum in _DWC_ENUMS and "permissible_values" in _DWC_ENUMS[primary_enum]:
                    range_val = primary_enum
                    referenced_enums.add(primary_enum)
                else:
                    range_val = "string"
            elif data_type == "integer":
                range_val = "integer"
            elif data_type == "decimal":
                range_val = "decimal"
            elif data_type == "date":
                range_val = "date"
            elif data_type == "datetime":
                range_val = "datetime"
            elif data_type == "boolean":
                range_val = "boolean"
            elif data_type == "uri":
                range_val = "uri"
            else:
                # string, time (LinkML has no time type), or fallback
                range_val = "string"

            slot = {
                "name":        slot_name,
                "title":       title,
                "slot_uri":    slot_uri,
                "slot_group":  sg_name,
                "range":       range_val,
                "multivalued": False,
            }
            if description_text:
                slot["description"] = description_text
            if comments_text:
                slot["comments"] = comments_text
            if enums:
                # Append external vocabulary references to comments
                ext_enums = [e for e in enums if e not in _DWC_ENUMS and e != "-"]
                if ext_enums:
                    existing = slot.get("comments", "")
                    addendum = f"External vocabularies: {', '.join(ext_enums)}"
                    slot["comments"] = (existing + (" — " if existing else "") + addendum).strip()
                # Attach examples from examples-only enum defs to the slot
                if data_type == "enumeration":
                    primary = enums[0]
                    enum_def = _DWC_ENUMS.get(primary, {})
                    if "examples" in enum_def and "permissible_values" not in enum_def:
                        slot["examples"] = enum_def["examples"]

            slots[slot_name] = slot

    # Include only enums that are both referenced and defined in this module
    enums_dict = {k: v for k, v in _DWC_ENUMS.items() if k in referenced_enums}

    # Class listing all slots
    class_name = "DarwinCoreSimpleRecord"
    classes = {
        class_name: {
            "name":        class_name,
            "description": "A Simple Darwin Core Record — a flat representation of biodiversity data",
            "tree_root":   True,
            "slots":       list(slots.keys()),
        }
    }

    prefixes = sort_prefixes({
        "dc":      "http://purl.org/dc/elements/1.1/",
        "dcterms": "http://purl.org/dc/terms/",
        "dwc":     "http://rs.tdwg.org/dwc/terms/",
        "dwcdoe":  "http://rs.tdwg.org/dwcdoe/values/",
        "dwcem":   "http://rs.tdwg.org/dwcem/values/",
        "dwcpw":   "http://rs.tdwg.org/dwcpw/values/",
    })

    schema = make_config_schema(
        id=_DWC_XSD_URL,
        name=key,
        title="Darwin Core Simple Record",
        description=(
            "Field (slot) definitions for the Simple Darwin Core standard "
            f"(https://dwc.tdwg.org/xml/tdwg_dwc_simple.xsd, v{_DWC_SIMPLE_VERSION}). "
            "Provides one slot per DWC Simple field and controlled vocabulary enumerations."
        ),
        version=_DWC_SIMPLE_VERSION,
        license="CC0",
        prefixes=prefixes,
        default_prefix="dwc",
        classes=classes,
        enums=enums_dict,
        slots=slots,
    )

    # Inject locale extensions for any non-English locales listed in config
    try:
        with open(config_file, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except (FileNotFoundError, OSError):
        cfg = {}

    extra_locales = [l for l in (cfg.get("locales") or ["en"]) if l != "en"]

    if extra_locales and os.path.exists(_DWC_TRANSLATIONS_PATH):
        with open(_DWC_TRANSLATIONS_PATH, encoding="utf-8") as fh:
            trans_by_qn = yaml.safe_load(fh) or {}

        locale_slots = {}  # lang → {slot_name: {title, description, comments}}
        for qual_name, lang_map in trans_by_qn.items():
            slot_name = qn_to_slot.get(qual_name)
            if not slot_name:
                continue
            for lang, trans in lang_map.items():
                if lang not in extra_locales:
                    continue
                slot_trans = {}
                if trans.get("label"):
                    slot_trans["title"] = trans["label"]
                if trans.get("description"):
                    slot_trans["description"] = trans["description"]
                if trans.get("comments"):
                    slot_trans["comments"] = trans["comments"]
                if slot_trans:
                    locale_slots.setdefault(lang, {})[slot_name] = slot_trans

        if locale_slots:
            locale_value = {
                lang: {
                    "id":          _DWC_XSD_URL,
                    "name":        key,
                    "version":     _DWC_SIMPLE_VERSION,
                    "in_language": lang,
                    "slots":       translated_slots,
                }
                for lang, translated_slots in sorted(locale_slots.items())
            }
            schema["extensions"] = {
                "locales": {"tag": "locales", "value": locale_value}
            }
            lang_counts = {l: len(s) for l, s in locale_slots.items()}
            print(
                "  Locale extensions: "
                + ", ".join(f"{l}({n} slots)" for l, n in sorted(lang_counts.items()))
            )

    # Inject enum vocab translations for extra locales
    if extra_locales and os.path.exists(_GBIF_VOCAB_TRANSLATIONS_PATH):
        with open(_GBIF_VOCAB_TRANSLATIONS_PATH, encoding="utf-8") as fh:
            vocab_trans = yaml.safe_load(fh) or {}

        enum_locale_counts = {}
        for enum_name, lang_map in vocab_trans.items():
            if enum_name not in enums_dict:
                continue
            # Build IRI → pv_key map by expanding each pv's meaning CURIE
            iri_to_pv_key = {}
            for pv_key, pv_data in enums_dict[enum_name].get("permissible_values", {}).items():
                meaning = (pv_data or {}).get("meaning", "")
                if ":" in meaning:
                    pfx, local = meaning.split(":", 1)
                    base = prefixes.get(pfx, "")
                    if base:
                        iri_to_pv_key[base + local] = pv_key

            for lang in extra_locales:
                pv_trans = {}
                for iri, title in lang_map.get(lang, {}).items():
                    pv_key = iri_to_pv_key.get(iri)
                    if pv_key:
                        pv_trans[pv_key] = {"title": title}
                if not pv_trans:
                    continue
                # Navigate into / create extensions.locales.value.{lang}
                loc_val = (
                    schema
                    .setdefault("extensions", {})
                    .setdefault("locales", {"tag": "locales", "value": {}})
                    ["value"]
                )
                lang_entry = loc_val.setdefault(lang, {
                    "id": _DWC_XSD_URL, "name": key,
                    "version": _DWC_SIMPLE_VERSION, "in_language": lang,
                })
                lang_entry.setdefault("enums", {})[enum_name] = {"permissible_values": pv_trans}
                enum_locale_counts.setdefault(lang, {})[enum_name] = len(pv_trans)

        if enum_locale_counts:
            print(
                "  Enum locale extensions: "
                + ", ".join(
                    f"{l}(" + ", ".join(f"{e}:{n}" for e, n in sorted(ec.items())) + ")"
                    for l, ec in sorted(enum_locale_counts.items())
                )
            )

    output_path = f"sources/{key}.yaml"
    with open(output_path, "w", encoding="utf-8") as fh:
        yaml.dump(schema, fh, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)

    # Persist prefixes back to config
    update_source_config(key, {"prefixes": prefixes}, config_file)

    total_fields = sum(len(s["fields"]) for s in sections)
    print(
        f"{key}: wrote {total_fields} slots + {len(enums_dict)} enums "
        f"+ 1 class → {output_path}"
    )
