"""FAO/WHO Codex Alimentarius GSFA Food Category System source helpers for term_harvester.py.

Fetches CXS 192-1995 (General Standard for Food Additives) PDF via curl,
extracts all page text using pypdf, and stores the result in a zip archive
at sources/{key}.zip.

The source key is qualified by the most recent revision year found in the
document's revision history (e.g. CODEX_2025).  The version string stored in
harvester_config.yaml is taken from the line beginning "CODEX STAN" or "CXS"
on page 1 (e.g. "192-1995").

Three enums are written to sources/{key}.yaml:

  CODEX_{year}_FoodCategories
      All food categories from PART I, with is_a hierarchy and optional
      see_also links to footnote CURIEs where footnotes appear in the
      PART II: Food Category Descriptors section.

  CODEX_{year}_Additives
      Food additives from TABLE ONE.  PV key = INS code (e.g. '950');
      title = additive name; description = 'Functional class: ...' text.
      Users may include or minus this enum via harvester_config.yaml.

  CODEX_{year}_FOOTNOTES
      One permissible value per footnote found in PART II.  The PV key is a
      CURIE of the form  codex_{year}:footnote_{n};  the title (rdfs:label)
      is the footnote text as it appears in the document.

Public API used by term_harvester.py:
    process_codex_source(key, source, locales=None)
    fetch_codex_source(key, source, config_file)
    match_codex(url, config_file)
"""

import datetime
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
import yaml

from source_utils import (
    add_permissible_value,
    log_extraction,
    IndentedDumper,
    make_config_schema,
    make_source_entry,
    normalize_text,
    update_source_config,
    write_config,
    MENU_CONFIG,
)

# Direct FAO download URLs for the Codex GSFA (CXS 192-1995) PDF.
# The gsfaonline URL has no year in the filename and tracks the current revision.
CODEX_GSFA_URL = "https://www.fao.org/gsfaonline/docs/CXS_192e.pdf"
CODEX_GSFA_URL_ALT = "https://www.fao.org/input/download/standards/4/CXS_192_2015e.pdf"

# Hardcoded source-level description for harvester_config.yaml.
# Used once when the source entry is first created by match_codex.
_SOURCE_DESCRIPTION = (
    "The Codex General Standard for Food Additives (CXS 192-1995, formerly CODEX STAN "
    "192-1995) is the internationally agreed standard that establishes conditions under "
    "which food additives may be safely used in foods and specifies maximum use levels "
    "across a hierarchical food category system. Maintained by the Codex Alimentarius "
    "Commission (FAO/WHO) and revised periodically by the Codex Committee on Food "
    "Additives (CCFA), the standard is organised into three parts: Part I defines the "
    "food category system - a hierarchical classification of food and food ingredients "
    "that determines the scope of additive permissions; Part II provides prose descriptors "
    "for each food category; and Part III lists permitted food additives (colours, "
    "sweeteners, and others) with their authorised maximum use levels by food category, "
    "including carry-over provisions and additive-specific tables."
)

# Entry name inside sources/{key}.zip
_ZIP_TEXT_ENTRY = "extracted_text.txt"

# Codex food category code pattern: two digits followed by one or more ".N" groups
# e.g. "01.0", "01.1", "01.1.1", "14.1.2.2"
_CAT_CODE_RE = re.compile(r'^\d{2}(?:\.\d+)+')
_CAT_ROW_RE  = re.compile(r'^(\d{2}(?:\.\d+)+)\s{2,}(.+)$')

# Running page headers / table column headings to skip.
# "70CXS 192-1995" is a page number merged with the document reference (no space
# between the page number digit(s) and "CXS").  "Table One" is the section-label
# running header that repeats at the top of each page inside TABLE ONE.
_SKIP_RE = re.compile(
    r'^(?:'
    r'CXS\s+192'
    r'|\d+CXS\s+192'
    r'|Table\s+One\b'
    r'|GENERAL\s+STANDARD\s+FOR\s+FOOD\s+ADDITIVES'
    r'|CODEX\s+STAN'
    r'|\d+\s+CXS'
    r'|Category\s+No\.?'
    r'|Food\s+Category\s+Description'
    r')',
    re.IGNORECASE,
)

# Footnote definition line: "1 Text…" or "12 Text…" where Text starts uppercase
_FOOTNOTE_DEF_RE = re.compile(r'^(\d{1,2})\s+([A-Z].*)$')

# ---------------------------------------------------------------------------
# TABLE ONE: Food Additive patterns
# ---------------------------------------------------------------------------

# Additive names are printed in ALL-CAPS: uppercase letters, digits, spaces,
# commas, hyphens, parentheses, dots, apostrophes, slashes.  At least 3 chars.
# Some names start with a digit (e.g. "4-HEXYLRESORCINOL"), so allow [A-Z0-9]
# as the first character.
_ALL_CAPS_LINE_RE = re.compile(r'^[A-Z0-9][A-Z0-9 ,\-\(\)\.\'\/\+]{2,}$')

# INS codes appear on lines like 'INS 950', 'INS 89', 'INS 160b(i)', 'INS 503(i)'.
# Two alternate branches:
#   group 1 — with 'INS ' prefix: allow 1-4 digits (prefix distinguishes from page numbers)
#   group 2 — without prefix:     require 3-4 digits (avoids matching bare page numbers)
# Use (group(1) or group(2)) to recover the code string.
_INS_CODE_RE = re.compile(
    r'^(?:'
    r'INS\s+(\d{1,4}(?:[a-z]{1,2})?(?:\([ivxIVX]{1,6}\))?)'   # with prefix
    r'|(\d{3,4}(?:[a-z]{1,2})?(?:\([ivxIVX]{1,6}\))?)'          # without prefix
    r')\s*$',
    re.IGNORECASE,
)

# "Functional class(es):" header in TABLE ONE entries
_FUNC_CLASS_RE = re.compile(r'^Functional\s+class(?:es)?[:\s]*(.*)', re.IGNORECASE)

# Food-category permission table row — starts with a Codex category code XX.X
_CAT_TABLE_ROW_RE = re.compile(r'^\d{2}\.\d')

# Column headers of the per-additive food-category permission table
_TABLE_HEADER_RE = re.compile(
    r'^(?:FoodCatNo\.?|Food\s+Cat|MaxLevel|Max\.?\s*Level|Notes?|Year\s+Adopted|Year\s+Last)',
    re.IGNORECASE,
)

# TABLE ONE additive info line detection — format-agnostic patterns that work
# for both pdfplumber (visual/coordinate-sorted) and pypdf (content-stream) output.
#
# pdfplumber extracts columns left-to-right by coordinate:
#   "INS 586 4-Hexylresorcinol Functional Class: Antioxidant, Colour retention agent"
# pypdf merges columns in content-stream order (non-visual):
#   "586INS Antioxidant, Colour retention agentFunctional Class:4-Hexylresorcinol"
#
# Strategy: anchor detection on "Functional Class:" (present in both), then
# extract the INS code from the same line using a two-branch pattern.

# Detects "Functional Class(es):" on the additive info line.
# Capture group 1 = the functional class text that follows the colon.
_FUNC_CLASS_IN_LINE_RE = re.compile(
    r'(?i:Functional\s+Class(?:es)?)[ \t]*:[ \t]*(.*)'
)

# Extracts the INS code from the same line — handles both column orderings:
#   pdfplumber: "\bINS {code} ..."    → group 1
#   pypdf:      "{code}INS ..."       → group 2
_INS_IN_LINE_RE = re.compile(
    r'\bINS[ \t]+(\d{1,4}[a-z]{0,2}(?:\([ivx]{1,6}\))?)'
    r'|(?:^|[ \t])(\d{1,4}[a-z]{0,2}(?:\([ivx]{1,6}\))?)[ \t]*INS\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# pypdf import helper
# ---------------------------------------------------------------------------

def _require_pypdf():
    """Import and return the pypdf module, exiting with a helpful message if absent."""
    try:
        import pypdf
        import logging
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        return pypdf
    except ImportError:
        print(
            "Error: pypdf is required for Codex PDF processing.\n"
            "Install it with:  pip install pypdf",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Download, text extraction, and zip
# ---------------------------------------------------------------------------

def _pdf_to_pages(pdf_path):
    """Return list of (pdf_page_number, page_text) tuples from *pdf_path*, 1-based.

    Tries pdfplumber first (pip install pdfplumber) because its coordinate-
    sorted extraction better preserves column ordering in multi-column tables
    such as TABLE ONE.  Falls back to pypdf if pdfplumber is not installed.
    """
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            print(f"  Extracting text from {total} pages (pdfplumber) ...")
            return [(i, page.extract_text() or "") for i, page in enumerate(pdf.pages, 1)]
    except ImportError:
        pass

    pypdf = _require_pypdf()
    reader = pypdf.PdfReader(pdf_path)
    total = len(reader.pages)
    print(f"  Extracting text from {total} pages (pypdf) ...")
    return [(i, page.extract_text() or "") for i, page in enumerate(reader.pages, 1)]


def _download_and_zip(url, zip_path):
    """Download PDF from *url*, extract all page text, write into *zip_path*.

    The zip contains a single file 'extracted_text.txt'.  Each PDF page is
    preceded by an '===PAGE N===' marker line.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    print(f"  Downloading {url} ...")
    result = subprocess.run(
        [
            "curl", "-L",
            "--connect-timeout", "30",
            "--max-time", "300",
            "--silent", "--show-error",
            "-o", tmp_path,
            url,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  curl error: {result.stderr.strip()}", file=sys.stderr)
        os.unlink(tmp_path)
        sys.exit(1)

    with open(tmp_path, "rb") as fh:
        magic = fh.read(5)
    if magic != b"%PDF-":
        preview = open(tmp_path, "rb").read(200)
        print(
            f"  Error: downloaded content is not a PDF.\n"
            f"  First bytes: {preview.decode('utf-8', errors='replace')[:120]!r}\n"
            f"  Check the URL is still valid: {url}",
            file=sys.stderr,
        )
        os.unlink(tmp_path)
        sys.exit(1)

    pages = _pdf_to_pages(tmp_path)
    os.unlink(tmp_path)

    full_text = "\n".join(f"===PAGE {num}===\n{text}" for num, text in pages)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_ZIP_TEXT_ENTRY, full_text)

    zsize = os.path.getsize(zip_path)
    print(f"  Saved {zip_path} ({zsize:,} bytes, {len(pages)} pages extracted)")


def fetch_codex_source(key, source, config_file=MENU_CONFIG):
    """Download the Codex GSFA PDF, extract text, and save to sources/{key}.zip.

    Called by the -f (fetch) handler in term_harvester.py.
    """
    url = (source.get("reachable_from") or {}).get("source_ontology", CODEX_GSFA_URL)
    zip_path = f"sources/{key}.zip"
    _download_and_zip(url, zip_path)
    update_source_config(key, {"download_date": datetime.date.today().isoformat()}, config_file)


# ---------------------------------------------------------------------------
# Version / key helpers
# ---------------------------------------------------------------------------

def _load_zip_text(zip_path):
    """Read and return the extracted text from sources/{key}.zip."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        return zf.read(_ZIP_TEXT_ENTRY).decode("utf-8")


def _extract_version_string(full_text):
    """Return the standard identifier from the version header line, e.g. '192-1995'.

    Searches the first 2 000 characters for a line beginning with either
    'CODEX STAN' or 'CXS' and extracts the NNN-YYYY identifier that follows,
    e.g.:
        'CODEX STAN 192-1995'  →  '192-1995'
        'CXS 192-1995'         →  '192-1995'

    Falls back to '192-1995' if neither pattern is found.
    """
    _ID_PAT = re.compile(r'(\d+[-\u2013]\d{4})')
    for prefix_pat in (r'(?m)^CODEX\s+STAN\s+', r'(?m)^CXS\s+'):
        m = re.search(prefix_pat + r'(\d+[-\u2013]\d{4})', full_text[:2000])
        if m:
            return m.group(1)
    return "192-1995"


def _extract_revision_year(full_text):
    """Return the most recent revision year from the document's revision history.

    The document's revision paragraph (immediately following the version line)
    lists years such as 'Adopted 1995. Revision 2001, 2003, 2005, ... 2023.'
    Multiple 'Amendment YYYY' lines are also handled.  We return the highest
    year found across all such entries as the qualifier for the source key and
    CURIE prefix.
    """
    header_text = full_text[:4000]
    # Collect all years from every Revision / Amendment / Amended keyword occurrence
    years = []
    for group in re.findall(
        r'(?:Revision|Amendment|Amended)[s:]*\s+([\d,\s.;/]+)',
        header_text,
        re.IGNORECASE,
    ):
        years.extend(int(y) for y in re.findall(r'\b(1\d{3}|20\d{2})\b', group))
    if years:
        return str(max(years))
    # Fallback: highest 20xx year near the start of the document
    fallback = [int(y) for y in re.findall(r'\b(20\d{2})\b', header_text)]
    return str(max(fallback)) if fallback else "unknown"


def _source_key(year):
    return f"CODEX_{year}"


def _footnotes_enum_key(year):
    return f"CODEX_{year}_FOOTNOTES"


def _curie_prefix_name(year):
    return f"codex_{year}"


def _curie_prefix_uri(year):
    # Fragment-style URI: codex_2023:footnote_1 expands to …CXS-192-1995#footnote_1
    return f"https://www.fao.org/fao-who-codexalimentarius/standards/CXS-192-1995#"


def _footnote_curie(year, num):
    return f"codex_{year}:footnote_{num}"


# ---------------------------------------------------------------------------
# Source description extraction
# ---------------------------------------------------------------------------

def _extract_source_description(full_text):
    """Extract the FOOD CATEGORY SYSTEM description block from the PDF text.

    The section-5 heading 'FOOD CATEGORY SYSTEM' is all-caps; the preamble
    (section 6, 'DESCRIPTION OF THE STANDARD') mentions 'food category system'
    in lower/mixed case.  A case-sensitive search finds the correct heading and
    avoids capturing the entire section 6 preamble.

    If multiple all-caps occurrences exist, the one closest (and prior) to the
    first '01.0' category row is used.  Returns '' when nothing useful is found
    or when the captured block exceeds 3 000 characters (wrong section guard).
    """
    # Case-sensitive: section heading is ALL-CAPS; preamble mention is lowercase
    candidates = list(re.finditer(r'FOOD\s+CATEGORY\s+SYSTEM', full_text))
    if not candidates:
        # Fallback: accept any case when no all-caps match exists (alternate PDF)
        candidates = list(re.finditer(r'FOOD\s+CATEGORY\s+SYSTEM', full_text, re.IGNORECASE))
    if not candidates:
        return ""

    # Find the candidate that immediately precedes the first '01.0' row
    cat_pos_m = re.search(r'\n01\.0[\s]', full_text)
    cat_pos = cat_pos_m.start() if cat_pos_m else len(full_text)
    before = [c for c in candidates if c.start() < cat_pos]
    hdr_m = before[-1] if before else candidates[0]

    tail = full_text[hdr_m.end():]
    cat_m = re.search(r'\n01\.0[\s]', tail)
    raw = tail[: cat_m.start() if cat_m else 2000].strip()

    # Safety guard: if captured text is implausibly long we matched the wrong heading
    if len(raw) > 3000:
        return ""

    raw = re.sub(r'===PAGE \d+===', '', raw)
    raw = re.sub(r'CXS\s+192[-\u2013\u2014]\s*\d{4}[^\n]*', '', raw)
    raw = re.sub(r'\s+', ' ', raw).strip()
    return normalize_text(raw) if len(raw) > 30 else ""


# ---------------------------------------------------------------------------
# PART II: footnote definition and reference parsing
# ---------------------------------------------------------------------------

def _find_footnote_refs_in_text(text):
    """Return a set of ints — footnote reference numbers found in *text*.

    pypdf renders superscript footnote markers as inline digits, typically
    appearing immediately after a letter or closing punctuation.  We match
    a trailing cluster of one-or-two-digit numbers at the end of the line.

    The negative lookbehind (?<!\\d\\.) prevents false positives from
    category codes like "14.1.2" whose final digit is preceded by "N.".
    Comma- and space-separated clusters ("milk.1 2" or "milk.1,2") are both
    captured in group(2).
    """
    refs = set()
    m = re.search(
        r'(?<!\d\.)(?<=[A-Za-z.,;)"\'])\s*(\d{1,2})((?:[\s,]+\d{1,2})*)\s*$',
        text,
    )
    if m:
        refs.add(int(m.group(1)))
        for extra in re.findall(r'\d{1,2}', m.group(2) or ""):
            refs.add(int(extra))
    return refs


def _parse_part_ii(full_text):
    """Parse 'PART II: Food Category Descriptors' for descriptions and footnotes.

    The PART II section is located by searching for the 'PART II' text marker
    and ends at the first 'PART III' or 'ANNEX' marker.  Page-number bounds
    are intentionally not hardcoded because they differ between PDF editions.

    Returns a 3-tuple:
        cat_descriptions: dict str → str   — category code → prose description
        footnotes:        dict int → str   — footnote number → footnote text
        cat_footnotes:    dict str → [int] — category code → sorted footnote refs
    """
    # Skip past the TOC: the TOC contains "PART II:" before any food-category
    # codes appear, so anchor the search to after the first '01.0' row.
    first_cat_m = re.search(r'\n01\.0[ \t]', full_text)
    search_from = first_cat_m.start() if first_cat_m else 0
    part2_m = re.search(r'PART\s+II\b', full_text[search_from:], re.IGNORECASE)
    if not part2_m:
        return {}, {}, {}

    tail = full_text[search_from + part2_m.end():]
    part3_m = re.search(r'PART\s+III\b|ANNEX\s+[A-Z]\b', tail, re.IGNORECASE)
    part2_text = tail[: part3_m.start()] if part3_m else tail

    cat_descriptions = {}   # str → str
    footnotes        = {}   # int → str
    cat_footnotes    = {}   # str → set[int]
    current_code     = None
    desc_parts       = []   # accumulated prose for current_code
    last_fn_num      = None

    def flush_desc():
        if current_code and desc_parts:
            desc = normalize_text(" ".join(desc_parts))
            if desc:
                cat_descriptions[current_code] = desc

    for line in part2_text.splitlines():
        stripped = line.strip()
        if not stripped:
            last_fn_num = None
            continue
        if re.match(r'^===PAGE \d+===$', stripped):
            continue
        if _SKIP_RE.match(stripped):
            continue

        # Category code heading — starts a new description accumulation
        cat_row = _try_parse_cat_row(stripped)
        if cat_row:
            flush_desc()
            current_code = cat_row[0]
            desc_parts   = []
            last_fn_num  = None
            continue

        # Footnote definition: "1 Text …" or "12 Text …"
        fn_m = _FOOTNOTE_DEF_RE.match(stripped)
        if fn_m:
            fn_num = int(fn_m.group(1))
            if 1 <= fn_num <= 99:
                footnotes[fn_num] = fn_m.group(2)
                last_fn_num = fn_num
                continue

        # Footnote definition continuation (lowercase start, no leading number)
        if last_fn_num is not None and stripped[0].islower():
            footnotes[last_fn_num] += " " + stripped
            continue

        # Description prose — accumulate and scan for footnote references
        if current_code:
            last_fn_num = None
            refs = _find_footnote_refs_in_text(stripped)
            if refs:
                cat_footnotes.setdefault(current_code, set()).update(refs)
            desc_parts.append(stripped)

    flush_desc()

    # Keep only references that correspond to an actual footnote definition
    known_nums = set(footnotes.keys())
    resolved = {
        code: sorted(nums & known_nums)
        for code, nums in cat_footnotes.items()
        if nums & known_nums
    }
    return cat_descriptions, footnotes, resolved


# ---------------------------------------------------------------------------
# TABLE ONE: per-additive parser
# ---------------------------------------------------------------------------

def _parse_table_one_additives(full_text):
    """Parse TABLE ONE (permitted food additives) from the GSFA PDF.

    Locates the 'TABLE ONE' section heading (bounded by 'TABLE TWO' at the
    end) and extracts each additive entry using a state machine:

    Name detection:
        Any all-caps line is held as a 'pending name'.  Running headers,
        table column headers, and other all-caps noise are naturally
        overwritten when the actual additive name appears immediately before
        its info line.

    Info line detection (format-agnostic):
        Anchors on 'Functional Class:' being present in the line.  Once
        found, _INS_IN_LINE_RE extracts the INS code from the same line,
        handling both pdfplumber output (left-to-right column order:
        "INS {code} {label} Functional Class: {classes}") and pypdf output
        (content-stream order: "{code}INS {classes}Functional Class:{label}").

    Returns a list of dicts: {name, ins_code, description}
        name:        additive name (e.g. 'ACESULFAME POTASSIUM')
        ins_code:    INS code string (e.g. '950')
        description: 'Functional class: Sweeteners' text, or None
    """
    # Locate 'TABLE ONE' section body.  The TOC (before any food-category
    # rows) also contains "TABLE ONE ... 66" and "TABLE TWO ... 230", so
    # naive re.search would find the TOC entry and bound section_text to
    # just the few lines between those two TOC references.  Fix: start the
    # search after the first '01.0' category row, which is well past the TOC.
    first_cat_m = re.search(r'\n01\.0[ \t]', full_text)
    t1_search_from = first_cat_m.start() if first_cat_m else 0
    table1_m = re.search(r'\bTABLE\s+ONE\b', full_text[t1_search_from:], re.IGNORECASE)
    if not table1_m:
        print("  Warning: 'TABLE ONE' heading not found in Codex PDF", file=sys.stderr)
        return []

    tail = full_text[t1_search_from + table1_m.end():]

    # Bound the section at TABLE TWO (or end of document).  Since tail now
    # starts from the real TABLE ONE body (page 66+), any TABLE TWO found
    # in tail is the real TABLE TWO body — the TOC reference is before tail.
    table2_m = re.search(r'\bTABLE\s+TWO\b', tail, re.IGNORECASE)
    section_text = tail[: table2_m.start()] if table2_m else tail

    additives    = []
    pending_name = None   # most-recent all-caps line (candidate additive name)
    current      = None   # {name, ins_code, func_class}

    def flush():
        nonlocal current
        if current is None:
            return
        additives.append({
            "name":        normalize_text(current["name"]),
            "ins_code":    current["ins_code"],
            "description": normalize_text(current["func_class"]) if current["func_class"] else None,
        })
        current = None

    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r'^===PAGE \d+===$', stripped):
            continue
        if _SKIP_RE.match(stripped):
            continue

        # --- Additive info line: contains both INS code and "Functional Class:" ---
        # pdfplumber: "INS 586 4-Hexylresorcinol Functional Class: Antioxidant, ..."
        # pypdf:      "586INS Antioxidant, ...Functional Class:4-Hexylresorcinol"
        # Anchor on "Functional Class:" (present in both), then extract INS code.
        fc_m = _FUNC_CLASS_IN_LINE_RE.search(stripped)
        if fc_m and pending_name is not None:
            ins_m = _INS_IN_LINE_RE.search(stripped)
            if ins_m:
                flush()
                ins_code  = ins_m.group(1) or ins_m.group(2)
                func_text = fc_m.group(1).strip().rstrip(',').strip()
                current = {
                    "name":       pending_name,
                    "ins_code":   ins_code,
                    "func_class": f"Functional class: {func_text}" if func_text else None,
                }
                pending_name = None
                continue

        # --- All-caps line → candidate additive name ---
        # Overwrites any previous candidate; the last all-caps line before an
        # entry line is always the real additive name (running headers and note
        # reference codes like "XS292, XS312" are filtered by _SKIP_RE or are
        # overwritten when the actual name appears on the same page).
        if _ALL_CAPS_LINE_RE.match(stripped) and not re.search(r'[a-z]', stripped):
            pending_name = stripped
            continue

        # Everything else (FoodCatNo table headers, category rows, note codes,
        # notes continuation lines) is silently ignored.

    flush()
    return additives


# ---------------------------------------------------------------------------
# Category row helper (shared by PART I and PART II parsers)
# ---------------------------------------------------------------------------

def _try_parse_cat_row(stripped):
    """Return (code, title) if *stripped* is a food category row, else None.

    Tries the strict pattern first (2+ spaces between code and title), then
    a loose fallback for lines where pypdf collapsed the separator to a single
    space — e.g. "07.0 Bakery wares" instead of "07.0  Bakery wares".  A
    trailing colon (sometimes present in PDF heading renderings) is stripped
    from the title.
    """
    m = _CAT_ROW_RE.match(stripped)
    if m:
        return m.group(1), m.group(2).strip().rstrip(":")
    # Loose fallback: line starts with a category code (XX.Y…) regardless of
    # separator width.  Guard against false positives by requiring the code
    # pattern; bare numbers and footnote lines won't match _CAT_CODE_RE.
    if _CAT_CODE_RE.match(stripped):
        m2 = re.match(r'^(\d{2}(?:\.\d+)+)\s*(.*)', stripped)
        if m2:
            return m2.group(1), m2.group(2).strip().rstrip(":")
    return None


# ---------------------------------------------------------------------------
# PART I: food category hierarchy
# ---------------------------------------------------------------------------

def _parse_food_categories(full_text):
    """Parse food category codes and titles from PART I only.

    Restricts parsing to text before the 'PART II' section marker so that
    PART II prose descriptions cannot contaminate the category table.
    Descriptions are collected separately by _parse_part_ii.

    Recognises rows of the form:
        XX.Y[.Z[.W]]  <2+ spaces>  Category name

    When pypdf collapses the separator to a single space, _try_parse_cat_row
    still detects the code and starts a new entry.  If the category name
    wraps to a continuation line, those lines are appended to the title.

    Returns a list of dicts: {code, title, page}.
    """
    # Restrict to PART I — stop at the PART II section marker, but skip past
    # the TOC which contains "PART II:" before any category codes appear.
    first_cat_m = re.search(r'\n01\.0[ \t]', full_text)
    search_from = first_cat_m.start() if first_cat_m else 0
    part2_m = re.search(r'PART\s+II\b', full_text[search_from:], re.IGNORECASE)
    parse_text = full_text[: search_from + part2_m.start()] if part2_m else full_text

    categories = []
    current = None
    current_page = 0

    def flush():
        nonlocal current
        if current is None:
            return
        title_parts = current["title_parts"]
        if not title_parts:
            # Code appeared on its own line with no title; skip it
            current = None
            return
        categories.append({
            "code":  current["code"],
            "title": normalize_text(" ".join(title_parts)),
            "page":  current["page"],
        })
        current = None

    for line in parse_text.splitlines():
        pm = re.match(r'^===PAGE (\d+)===$', line.strip())
        if pm:
            current_page = int(pm.group(1))
            continue

        stripped = line.strip()
        if not stripped:
            continue
        if _SKIP_RE.match(stripped):
            continue

        cat_row = _try_parse_cat_row(stripped)
        if cat_row:
            flush()
            code, title = cat_row
            current = {
                "code":        code,
                "title_parts": [title] if title else [],
                "page":        current_page,
            }
            continue

        if current is not None:
            if re.match(r'^\d+\s*$', stripped):
                continue  # bare page number
            if re.match(r'^\d+\s+[A-Z]', stripped):
                continue  # looks like a footnote definition
            if re.match(r'^PART\s+[IVX]+\b', stripped, re.IGNORECASE):
                continue  # section heading (PART I, PART II, etc.)
            # Continuation of a wrapped category title
            current["title_parts"].append(stripped)

    flush()
    return categories


# ---------------------------------------------------------------------------
# Hierarchy helper
# ---------------------------------------------------------------------------

def _codex_parent(code, known_codes):
    """Return the parent code for *code*, or None if it is a top-level category.

    Codex dotted hierarchy:
        XX.0     → no parent  (top-level section header)
        XX.Y     → XX.0       (always — this is a structural Codex convention)
        XX.Y.Z   → XX.Y
        XX.Y.Z.W → XX.Y.Z
    """
    parts = code.split(".")
    if len(parts) < 2:
        return None
    if len(parts) == 2:
        if parts[1] == "0":
            return None  # top-level entry
        # XX.Y always belongs under XX.0 (Codex section-header convention),
        # even if XX.0 was not explicitly parsed from the PDF.
        return parts[0] + ".0"
    candidate = ".".join(parts[:-1])
    return candidate if candidate in known_codes else None


# ---------------------------------------------------------------------------
# Sort-key helpers
# ---------------------------------------------------------------------------

def _cat_sort_key(code):
    """Numeric tuple sort key for Codex dotted category codes.

    "01.0" → (1, 0);  "01.1.1" → (1, 1, 1);  "14.1.2.2" → (14, 1, 2, 2).
    Sorts all children of a section immediately after their parent.
    """
    return tuple(int(p) for p in code.split("."))


_ROMAN_VAL = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
              "vii": 7, "viii": 8, "ix": 9, "x": 10}

def _ins_sort_key(code):
    """Numeric sort key for INS codes: (integer, letter_suffix, roman_value).

    "89"       → (89,  '',  0)
    "100a"     → (100, 'a', 0)
    "160b(i)"  → (160, 'b', 1)
    "503(i)"   → (503, '',  1)
    "950"      → (950, '',  0)
    """
    m = re.match(r'^(\d+)([a-z]{0,2})(?:\(([ivx]{1,8})\))?$', code, re.IGNORECASE)
    if not m:
        return (999999, code, 0)
    return (
        int(m.group(1)),
        (m.group(2) or "").lower(),
        _ROMAN_VAL.get((m.group(3) or "").lower(), 0),
    )


# ---------------------------------------------------------------------------
# YAML dumper with consistent quoting for dotted-decimal category codes
# ---------------------------------------------------------------------------

class _CodexYamlDumper(IndentedDumper):
    """IndentedDumper that forces single-quote style for Codex category code strings.

    PyYAML quotes '01.2' (looks like a float) but not '01.2.1' (two dots,
    not a valid scalar type).  This subclass forces consistent single-quote
    quoting for any string matching the dotted-decimal code pattern so that
    all category keys are rendered uniformly.
    """


def _represent_codex_str(dumper, data):
    """Single-quote dotted-decimal category codes; delegate everything else."""
    if re.match(r'^\d{2}(?:\.\d+)+$', data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style="'")
    # Replicate source_utils._represent_str quoting rules
    if "'" in data and '"' not in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    if '"' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style="'")
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


_CodexYamlDumper.add_representer(str, _represent_codex_str)


# ---------------------------------------------------------------------------
# Process function  (-c phase)
# ---------------------------------------------------------------------------

def process_codex_source(key, source, locales=None):
    """Build a LinkML enum YAML for a CODEX source.

    Reads sources/{key}.zip (extracted PDF text) and writes sources/{key}.yaml
    containing two enums:

    1. CODEX_{year}_FoodCategories
       Food category hierarchy from PART I with is_a relationships.
       Category descriptions come from PART II prose.  Permissible values
       that have footnote references carry a see_also list of footnote CURIEs.
       The enum description is the 'FOOD CATEGORY SYSTEM' text from page 5.

    2. CODEX_{year}_FOOTNOTES
       One PV per footnote found in PART II.  PV key = codex_{year}:footnote_{n};
       title (rdfs:label) = footnote text as it appears in the document.
    """
    zip_path  = f"sources/{key}.zip"
    yaml_path = f"sources/{key}.yaml"
    base_url  = (source.get("reachable_from") or {}).get("source_ontology", CODEX_GSFA_URL)

    if not os.path.exists(zip_path):
        print(f"  {zip_path} not found — run -f {key} to download first", file=sys.stderr)
        return

    full_text = _load_zip_text(zip_path)

    # Extract year from source key (CODEX_{year})
    key_m = re.match(r'CODEX_(\w+)$', key)
    year = key_m.group(1) if key_m else _extract_revision_year(full_text)

    prefix_name = _curie_prefix_name(year)
    prefix_uri  = _curie_prefix_uri(year)

    # Parse PART I: category codes and titles only (no descriptions)
    categories  = _parse_food_categories(full_text)

    if not categories:
        print(f"  Warning: no food categories parsed from {zip_path}", file=sys.stderr)
        return

    categories.sort(key=lambda cat: _cat_sort_key(cat["code"]))
    known_codes = {cat["code"] for cat in categories}

    # Parse PART II: prose descriptions, footnote definitions, footnote refs
    cat_descriptions, footnotes, cat_footnotes = _parse_part_ii(full_text)

    # Parse TABLE ONE: food additives with INS codes and functional classes
    additives = _parse_table_one_additives(full_text)
    additives.sort(key=lambda a: _ins_sort_key(a["ins_code"]))

    # Build schema skeleton
    schema = make_config_schema(
        id=base_url,
        name=key,
        title=source.get("title", ""),
        description=source.get("description", ""),
        version=source.get("version", ""),
    )
    schema["prefixes"] = {prefix_name: prefix_uri}

    # ---- Enum 1: Food categories ----------------------------------------

    food_enum_key = f"CODEX_{year}_FoodCategories"
    food_pvs = {}

    for cat in categories:
        code   = cat["code"]
        parent = _codex_parent(code, known_codes)
        pv = add_permissible_value(
            food_pvs,
            code,
            title=cat["title"],
            description=cat_descriptions.get(code),
            is_a=parent,
        )
        footnote_refs = cat_footnotes.get(code, [])
        if footnote_refs:
            pv["see_also"] = [_footnote_curie(year, n) for n in footnote_refs]

    # ---- Enum 2: Footnotes -----------------------------------------------

    fn_enum_key = _footnotes_enum_key(year)
    fn_pvs = {}

    for fn_num in sorted(footnotes.keys()):
        fn_curie = _footnote_curie(year, fn_num)
        fn_text  = normalize_text(footnotes[fn_num])
        add_permissible_value(
            fn_pvs,
            fn_curie,
            title=fn_text,
            meaning=fn_curie,
        )

    # ---- Enum 3: Additives (TABLE ONE) ----------------------------------

    additive_enum_key = f"CODEX_{year}_Additives"
    additive_pvs = {}

    for additive in additives:
        add_permissible_value(
            additive_pvs,
            additive["ins_code"],
            title=additive["name"],
            description=additive["description"],
        )

    # ---- Assemble schema --------------------------------------------------

    # Enum description: page-5 'FOOD CATEGORY SYSTEM' prose (not source-level description)
    enum_description = _extract_source_description(full_text) or source.get("description", "")

    schema["enums"] = {
        food_enum_key: {
            "name":               food_enum_key,
            "title":              "GENERAL STANDARD FOR FOOD ADDITIVES - FOOD CATEGORIES",
            "description":        enum_description,
            "see_also":           base_url,
            "permissible_values": food_pvs,
        },
    }

    if additive_pvs:
        schema["enums"][additive_enum_key] = {
            "name":               additive_enum_key,
            "title":              "GENERAL STANDARD FOR FOOD ADDITIVES - TABLE ONE",
            "description": (
                "Food additives permitted under the Codex General Standard for Food "
                "Additives (CXS 192-1995), Table One.  Each permissible value key is "
                "the INS (International Numbering System) code; the title is the "
                "additive name; the description gives the functional class(es)."
            ),
            "see_also":           base_url,
            "permissible_values": additive_pvs,
        }

    if fn_pvs:
        schema["enums"][fn_enum_key] = {
            "name":               fn_enum_key,
            "title":              f"Codex GSFA ({source.get('version', 'CXS 192-1995')}) Footnotes",
            "description": (
                "Footnotes from Part II: Food Category Descriptors of the Codex General "
                "Standard for Food Additives.  Each permissible value key is a CURIE "
                f"({prefix_name}:footnote_N) whose rdfs:label is the footnote text as it "
                "appears in the document."
            ),
            "see_also":           base_url,
            "permissible_values": fn_pvs,
        }

    log_extraction(food_enum_key, doc_detail="Codex GSFA food categories", count=len(food_pvs))
    if additive_pvs:
        log_extraction(additive_enum_key, doc_detail="Codex GSFA TABLE ONE", count=len(additive_pvs))
    if fn_pvs:
        log_extraction(fn_enum_key, doc_detail="Codex GSFA footnotes", count=len(fn_pvs))

    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=_CodexYamlDumper, default_flow_style=False, sort_keys=False)
    print(f"Updated {yaml_path}")


# ---------------------------------------------------------------------------
# Match function  (-a phase, pre-download detector)
# ---------------------------------------------------------------------------

def match_codex(url, config_file=MENU_CONFIG):
    """Return True if *url* is a Codex GSFA (CXS 192-1995) URL and was handled.

    Pre-download detector: downloads the PDF, extracts page text into a zip,
    determines the source key from the revision year, creates a
    harvester_config.yaml entry, and runs initial -c processing.

    Both known URL forms are accepted and produce the same category hierarchy;
    only labels and descriptions may differ between document editions:
      - https://www.fao.org/gsfaonline/docs/CXS_192e.pdf         (no-year, tracks current)
      - https://www.fao.org/input/download/standards/4/CXS_192_2015e.pdf
    Any fao.org URL whose path contains a CXS 192 identifier also matches.
    """
    decoded = urllib.parse.unquote(url)
    is_gsfa = (
        "fao.org" in decoded
        and ("CXS_192" in decoded or "CXS+192" in decoded or "CXS 192" in decoded)
    )
    if not is_gsfa:
        return False

    # Download to a temporary name; rename once we know the revision year
    tmp_zip = f"sources/_codex_tmp.zip"
    _download_and_zip(url, tmp_zip)

    full_text   = _load_zip_text(tmp_zip)
    version_str = _extract_version_string(full_text)   # e.g. "192-1995"
    year        = _extract_revision_year(full_text)     # e.g. "2025"
    key         = _source_key(year)                     # e.g. "CODEX_2025"

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}

    if key in config.get("sources", {}):
        os.unlink(tmp_zip)
        print(
            f"  Skipping {url}: source key '{key}' already in {config_file}",
            file=sys.stderr,
        )
        return True

    zip_path = f"sources/{key}.zip"
    os.rename(tmp_zip, zip_path)

    version = f"{version_str} (Rev. {year})" if year != "unknown" else version_str

    entry = make_source_entry(
        key, url, "CODEX", "zip",
        title="GENERAL STANDARD FOR FOOD ADDITIVES - FOOD CATEGORIES",
        version=version,
        description=_SOURCE_DESCRIPTION,
    )

    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"Added source '{key}' to {config_file}")

    process_codex_source(key, entry)
    return True
