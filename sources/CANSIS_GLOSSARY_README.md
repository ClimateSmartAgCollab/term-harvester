# CANSIS Glossary — FR Translation Workflow

This directory contains the CANSIS Glossary source files and the translation
utility that adds French locale extensions to `CANSIS_GLOSSARY.yaml`.

## Files

| File | Role |
|------|------|
| `CANSIS_GLOSSARY.zip` | Downloaded HTML pages (EN + FR letter pages, 4 table pages) |
| `CANSIS_GLOSSARY.yaml` | Parsed EN glossary enum, updated in-place by `--apply` |
| `source_cansis_translate.py` | Two-phase translation and match script |
| `source_cansis_glossary.py` | term_harvester source module (`-a`, `-f`, `-c`) |

---

## Background

The CANSIS EN glossary (`https://sis.agr.gc.ca/cansis/glossary/`) and the FR
glossary (`https://sis.agr.gc.ca/siscan/glossary/`) are **independent
vocabularies**.  The FR glossary is not a term-by-term translation of the EN
one — it has different entries, different ordering, and different coverage.
Both are included in `CANSIS_GLOSSARY.zip`.

The translation workflow bridges this gap by:

1. Extracting FR terms and definitions from the FR pages in the ZIP.
2. Machine-translating them to English via Google Translate.
3. Fuzzy-matching the translated EN labels against the existing EN glossary
   entries to identify which FR term corresponds to which EN entry.
4. Writing matched FR titles and definitions back into `CANSIS_GLOSSARY.yaml`
   as `extensions.locales.fr` entries.

---

## When to run source_cansis_translate.py

`source_cansis_translate.py` is **not** invoked by `term_harvester.py -a` or `-f`
because it requires an external dependency (`deep-translator`), makes live calls
to Google Translate, and produces output that needs human review before applying.

Run it manually **after** `-c` has generated `CANSIS_GLOSSARY.yaml`:

```bash
pip install deep-translator

# Step 1 — translate FR content to EN, write CANSIS_GLOSSARY_translated_fr.tsv
python ../sources/source_cansis_translate.py --translate
#   → review the TSV and unmatched terms printed to stdout

# Step 2 — match translated labels to EN entries, update CANSIS_GLOSSARY.yaml
python ../sources/source_cansis_translate.py --apply
#   → then re-run -b to incorporate FR extensions into schema.yaml

# Optional: lower the match threshold (default 0.65) to accept weaker matches
python ../sources/source_cansis_translate.py --apply --threshold 0.60
```

Re-run `--translate` only when re-fetching source files (`-f`); the TSV can be
reused with `--apply` across multiple `-c`/`-b` cycles without re-translating.

### `--translate`

Parses all FR letter pages (`a_fr.html` … `z_fr.html`, `_fr.html`) from
`CANSIS_GLOSSARY.zip`, sends the term labels and definitions to Google
Translate (FR → EN) in batches of ≤ 4 800 characters using bracket-numbered
item markers (`[1] text`, `[2] text`, …`), and writes **`CANSIS_GLOSSARY_translated_fr.tsv`** to
the current directory.

`CANSIS_GLOSSARY_translated_fr.tsv` columns (tab-separated, header on row 1):

| Column | Content |
|--------|---------|
| `fr_term` | Original French label |
| `fr_definition` | Original French definition |
| `en_label` | Google-translated English label (used for matching) |
| `en_definition` | Google-translated English definition (for reference) |

Requires `deep-translator`:

```bash
pip install deep-translator
```

### `--apply`

Reads `CANSIS_GLOSSARY_translated_fr.tsv`, then for each row fuzzy-matches `en_label` against
the permissible-value titles in `CANSIS_GLOSSARY.yaml` using
`difflib.SequenceMatcher`.  Rows whose best match scores at or above
`--threshold` (default `0.65`) are written into the YAML as FR locale
extensions:

```yaml
extensions:
  locales:
    value:
      fr:
        enums:
          CANSIS_GLOSSARY:
            permissible_values:
              acid soil:
                title: sol acide
                description: Sol ayant un pH inférieur à 7,0…
```

Unmatched terms are printed to stdout with their best score and translated
label so they can be reviewed manually.

---

## Unmatched FR terms (162)

The following FR terms did not reach the 0.65 match threshold against any EN
entry.  Causes include: FR-only concepts, multi-synonym labels joined with
`/` or `;`, HTML entities (`&#39;`) not decoded before translation, and
translation divergence (e.g. "rigole" → "laughs" instead of "furrow").
These may be resolved by lowering `--threshold`, by manually adding entries
to `CANSIS_GLOSSARY_translated_fr.tsv`, or by improving EN coverage in the source glossary.

```
[0.60] 'alcalinisation ou alcalisation'  →  'alkalization or alkalization'
[0.60] 'alios'  →  'alios'
[0.64] 'amendement du sol'  →  'soil amendment'
[0.56] 'aridoculture; culture sèche'  →  'aridoculture; dry cultivation'
[0.45] 'assise rocheuse, roc sous-jacent roc'  →  'bedrock, underlying rock rock'
[0.62] 'aérer'  →  'ventilate'
[0.57] 'batture'  →  'mud'
[0.53] 'boue de mines'  →  'mining mud'
[0.58] 'boue de mines fine'  →  'fine mining mud'
[0.64] 'brise-vent (coupe-vent)'  →  'windbreak (windbreak)'
[0.62] 'brèche'  →  'breach'
[0.60] 'butte'  →  'mound'
[0.53] 'butte de chablis'  →  'mound of chablis'
[0.45] 'caillou roulé galet'  →  'pebble rolled pebble'
[0.64] 'caillouteux/ en galets'  →  'stony/pebble'
[0.65] 'capacité de diffusion'  →  'broadcast capacity'
[0.64] 'capacité portante; capacité de charge'  →  'bearing capacity; carrying capacity'
[0.53] 'champignons'  →  'mushrooms'
[0.64] 'chimie du sol'  →  'soil chemistry'
[0.61] 'cimenté (induré)'  →  'cemented (hardened)'
[0.59] 'classe de possibilités'  →  'class of possibilities'
[0.49] 'couche holorganique / couverture morte /litière'  →  'holorganic layer / dead cover / litter'
[0.53] 'couche L / litière'  →  'L diaper / litter'
[0.52] 'coupe témoin d&#39;un sol'  →  'witness section of a floor'
[0.62] 'cultiver le sol'  →  'cultivate the soil'
[0.62] 'cône de déjection'  →  'excrement cone'
[0.55] 'degrés d&#39;agrégation du sol'  →  'degrees of soil aggregation'
[0.63] 'drain souterrain (tuyau de drainage)'  →  'underground drain (drainage pipe)'
[0.65] 'drainage entravé'  →  'obstructed drainage'
[0.44] 'drift glaciaire / matériau de transport glaciaire / dépôt glaciaire'  →  'glacial drift / glacial transport material / glacial deposit'
[0.60] 'drumlin à noyau rocheux; rocdrumlin'  →  'rock-core drumlin; rocdrumlin'
[0.55] 'eau libre / eau de gravité'  →  'free water / gravity water'
[0.59] 'en dalles'  →  'in slabs'
[0.57] 'en plaquettes'  →  'in platelets'
[0.62] 'engorgé'  →  'engorged'
[0.48] 'entrave (résistance à l&#39;écoulement)'  →  'hindrance (resistance to flow)'
[0.61] 'enzyme d&#39;induction'  →  'enzyme induction'
[0.60] 'famille de sols'  →  'soil family'
[0.45] 'façon culturale d&#39;enrichissement'  →  'cultural way of enrichment'
[0.62] 'façons culturales (travail du sol)'  →  'cultural methods (tillage)'
[0.64] 'fertilité du sol'  →  'soil fertility'
[0.55] 'feu de terre'  →  'earth fire'
[0.52] 'fond alluvial (plaine alluviale)'  →  'alluvial bottom (alluvial plain)'
[0.56] 'frange capillaire'  →  'hair bangs'
[0.57] 'gel alvéolaire'  →  'alveolar gel'
[0.62] 'genre'  →  'gender'
[0.63] 'genèse des sols, pédogenèse'  →  'soil genesis, pedogenesis'
[0.57] 'graveleux'  →  'gritty'
[0.55] 'grès fin (siltstone)'  →  'fine sandstone (siltstone)'
[0.62] 'gélivation'  →  'frostbite'
[0.52] 'humidité critique (point de flétrissement (biologie))'  →  'critical humidity (wilting point (biology))'
[0.60] 'imperméable'  →  'waterproof'
[0.54] 'indice de plasticité or intervalle de plasticité'  →  'plasticity index or plasticity interval'
[0.56] 'indice de qualité de station.'  →  'station quality index.'
[0.54] 'indice des pores (des vides)'  →  'pore index (voids)'
[0.64] 'irrigation par bassin de retenue'  →  'irrigation by retention basin'
[0.52] 'irrigation par calants / irrigation à la planche'  →  'wedge irrigation / board irrigation'
[0.52] 'lagg, marécage bordier'  →  'lagg, border swamp'
[0.51] 'laisse de marée (slikke)'  →  'tide leash (slikke)'
[0.59] 'lamellaire'  →  'lamellar'
[0.50] 'limite de liquidité limite supérieure de plasticité limite d&#39;Atterberg'  →  'liquidity limit upper limit of plasticity Atterberg limit'
[0.62] 'limite de plasticité (limite d&#39; Atterberg)'  →  'plasticity limit (Atterberg limit)'
[0.55] 'lits entrecroisés'  →  'criss-cross beds'
[0.47] 'luminosité (intensité / brillance)'  →  'brightness (intensity / brightness)'
[0.56] 'luvisol brun gris'  →  'luvisol gray brown'
[0.59] 'marbrures (mouchetures) (taches)'  →  'marbling (speckles) (spots)'
[0.65] 'marmite (cavité glaciaire)'  →  'pot (glacial cavity)'
[0.56] 'marmorisation (marbrures)'  →  'marmorization (marbling)'
[0.64] 'masse cryoconsolidée'  →  'cryoconsolidated mass'
[0.64] 'matrice du sol'  →  'soil matrix'
[0.60] 'matériau originel, parental'  →  'original material, parental'
[0.57] 'meuble'  →  'furniture'
[0.62] 'microstructure du sol, fabrique du sol'  →  'soil microstructure, soil fabric'
[0.62] 'minéral argileux interstratifié/ argile minéralogique interstratifiée'  →  'interstratified clay mineral/interstratified mineralogical clay'
[0.56] 'modelés: formes du terrain'  →  'modeled: land shapes'
[0.55] 'mouvement en masse'  →  'mass movement'
[0.63] 'nappe d&#39;eau perchée'  →  'perched sheet of water'
[0.48] 'nappe phréatique ( niveau phréatique)( niveau hydrostatique)'  →  'water table ( phreatic level)( hydrostatic level)'
[0.62] 'nodule de sol'  →  'soil nodule'
[0.57] 'non-sol'  →  'non-ground'
[0.57] 'paillis (mulch de chaume)'  →  'mulch (thatch mulch)'
[0.49] 'pan argileux / horizon d&#39;accumulation argillique'  →  'clay pan / clay accumulation horizon'
[0.55] 'photo-carte planimétrique'  →  'planimetric photo-map'
[0.58] 'photo-carte topographique'  →  'topographic photo-map'
[0.62] 'point d&#39;adhésion'  →  'adhesion point'
[0.64] 'point de flétrissement'  →  'wilting point'
[0.55] 'polyédrique'  →  'polyhedral'
[0.57] 'pore'  →  'pore'
[0.62] 'pores du sol'  →  'soil pores'
[0.62] 'potentiel d&#39;eau libre (de gravité)'  →  'free water potential (gravity)'
[0.58] 'pourcentage d&#39;eau en poids'  →  'percentage of water by weight'
[0.62] 'pourcentage d&#39;eau en volume'  →  'percentage of water by volume'
[0.59] 'pourcentage de cations échangeables'  →  'percentage of exchangeable cations'
[0.62] 'pourcentage de sodium soluble (PSS)'  →  'percent soluble sodium (PSS)'
[0.62] 'pourcentage de sodium échangeable'  →  'percentage of exchangeable sodium'
[0.53] 'pourcentage poids sec'  →  'percentage dry weight'
[0.53] 'pourcentage à 1/3 bar'  →  'percentage at 1/3 bar'
[0.53] 'pourcentage à 1/3 bar'  →  'percentage at 1/3 bar'
[0.47] 'pourcentage à 15 atmosphères'  →  'percentage at 15 atmospheres'
[0.53] 'pourcentage à 15 bars'  →  'percentage at 15 bars'
[0.53] 'pourcentage à 15 bars'  →  'percentage at 15 bars'
[0.45] 'pourcentage à 60 centimètres'  →  'percentage at 60 centimeters'
[0.43] 'pourcentage à quinze atmosphères'  →  'percentage at fifteen atmospheres'
[0.58] 'pourcentage à un tier atmosphère'  →  'percentage at one third atmosphere'
[0.58] 'profil de sol'  →  'soil profile'
[0.53] 'prospection pédologique ( levé des sols (Pedology))'  →  'pedological prospecting (soil survey (Pedology))'
[0.55] 'présol, sol en formation'  →  'presol, sol in formation'
[0.61] 'pédoclimat (climat du sol)'  →  'pedoclimate (soil climate)'
[0.64] 'remblai détritique'  →  'detrital fill'
[0.61] 'revêtement, enrobement'  →  'coating, covering'
[0.60] 'rigole'  →  'laughs'
[0.59] 'répartition volumétrique des pores'  →  'volumetric distribution of pores'
[0.51] 'résistance à l&#39;érasement'  →  'resistance to scuffing'
[0.62] 'réversion (rétrogradation)'  →  'reversion (retrograde)'
[0.64] 'sable poudreux'  →  'powdery sand'
[0.62] 'salinité du sol'  →  'soil salinity'
[0.58] 'schisteux (feuilleté)'  →  'shale (laminated)'
[0.61] 'science des sols'  →  'soil science'
[0.64] 'sol (1)'  →  'ground (1)'
[0.64] 'sol (2)'  →  'ground (2)'
[0.54] 'sol altéré par le sel'  →  'soil altered by salt'
[0.65] 'sol fossile; sol enfoui'  →  'fossil soil; buried soil'
[0.62] 'sol peu évolué'  →  'poorly evolved soil'
[0.65] 'sol évolué/ sol mûr'  →  'evolved soil/mature soil'
[0.64] 'solonetz solodisé'  →  'solonetz solodized'
[0.62] 'solum(s)'  →  'solum(s)'
[0.55] 'sous-classe de possibilités'  →  'subclass of possibilities'
[0.60] 'structure mailée (réticulée en gnile)'  →  'mesh structure (reticulated in gnile)'
[0.55] 'surface de glissement (miroir de faille)'  →  'slip surface (fault mirror)'
[0.59] 'suspension-dilution: dilution en série'  →  'serial dilution'
[0.59] 'série de sols'  →  'soil series'
[0.64] 'taches lisses'  →  'smooth spots'
[0.56] 'talud'  →  'embankment'
[0.56] 'talus'  →  'embankment'
[0.57] 'taux d&#39;infiltration / taux maximal d&#39;infiltration'  →  'infiltration rate / maximum infiltration rate'
[0.62] 'taxon(s)'  →  'taxon(s)'
[0.62] 'teinte, tonalité'  →  'tint, tone'
[0.58] 'tensiomètre'  →  'blood pressure monitor'
[0.61] 'tension à 60 centimètres'  →  'tension at 60 centimeters'
[0.60] 'terrain accidenté'  →  'rough terrain'
[0.43] 'terrain anthropique, terre rapportée'  →  'anthropogenic terrain, earth brought back'
[0.53] 'terrain d&#39;épandage de pétrole'  →  'oil spreading field'
[0.64] 'terrain inutilisable'  →  'unusable land'
[0.64] 'terrain rocheux'  →  'rocky terrain'
[0.64] 'terrasse en gradins'  →  'stepped terrace'
[0.50] 'terrasse inférieure / basse terrasse'  →  'lower terrace / low terrace'
[0.54] 'terre noire (organique)'  →  'black earth (organic)'
[0.50] 'terril'  →  'slag heap'
[0.50] 'tertre (butte-témoin)'  →  'mound (witness mound)'
[0.62] 'travail du sous-sol'  →  'basement work'
[0.56] 'tuyau de drainage'  →  'drainage pipe'
[0.47] 'type de terrains divers'  →  'various types of terrain'
[0.57] 'unité cartographique'  →  'cartographic unit'
[0.58] 'unité cartographique de sols non différenciés'  →  'cartographic unit of undifferentiated soils'
[0.58] 'vitesse de décharge d&#39;écoulement'  →  'flow discharge speed'
[0.61] 'volume brut'  →  'gross volume'
[0.49] 'zone de marbrures de mouchetures zone marmorisée'  →  'marbling zone of speckles marmorized zone'
[0.61] 'zones d&#39;altitude (zonalité verticale)'  →  'altitude zones (vertical zonality)'
[0.59] 'zones de latitude (zonalité horizontale)'  →  'latitude zones (horizontal zonality)'
[0.64] 'énergie de réseau'  →  'grid energy'
[0.62] 'étage'  →  'floor'
[0.48] 'état d&#39;ameublissement'  →  'state of furnishing'
```

### Notes on common failure patterns

| Pattern | Example | Issue |
|---------|---------|-------|
| `&#39;` in FR term | `'coupe témoin d&#39;un sol'` | HTML entity not decoded before translation; apostrophe is passed literally |
| Translation divergence | `'rigole'` → `'laughs'` (should be "furrow") | Google Translate picks wrong word sense for a short ambiguous term |
| Multi-synonym labels | `'eau libre / eau de gravité'` | Slash-joined synonyms produce a long translated string that doesn't match the shorter EN entry |
| FR-only concepts | `'batture'`, `'alios'`, `'lagg'` | Concepts present in FR glossary but absent from the EN glossary |
| Score just below threshold | many at 0.62–0.64 | Lowering `--threshold` to `0.60` would match many of these |
