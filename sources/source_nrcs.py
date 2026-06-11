"""USDA NRCS source helpers for term_harvester.py.

Fetches the NRCS Field Book for Describing and Sampling Soils PDF and
extracts selected enumeration tables into LinkML enum YAML format.

Public API used by term_harvester.py:
    process_nrcs_source(key, source, locales=None)
    match_nrcs(url, tmp_path, config_file)
"""

import os
import re
import subprocess
import sys
import urllib.request
import yaml

from source_utils import (
    add_permissible_value,
    IndentedDumper,
    make_config_schema,
    BROWSER_HEADERS,
    MENU_CONFIG,
)


NRCS_FIELD_BOOK_PDF = (
    "https://www.nrcs.usda.gov/sites/default/files/2025-05/"
    "Field-Book-for-Describing-and-Sampling-Soils-Ver4.pdf"
)

# Shared PDF cache — all NRCSSoilFieldBook source entries use the same file
_NRCS_PDF_CACHE = "sources/NRCSSoilFieldBook.pdf"

_ENUM_DEFS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sources_nrcs_terms.yaml"
)


def _load_enum_definitions():
    """Load enum extraction registry from sources/sources_nrcs_terms.yaml."""
    try:
        with open(_ENUM_DEFS_PATH) as f:
            data = yaml.safe_load(f) or {}
        return data.get("enum_definitions", [])
    except FileNotFoundError:
        print(
            f"Warning: {_ENUM_DEFS_PATH} not found;"
            " NRCS enum extraction unavailable.",
            file=sys.stderr,
        )
        return []


ENUM_DEFINITIONS = _load_enum_definitions()


# ---------------------------------------------------------------------------
# PDF utilities
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
            "Error: pypdf is required for NRCS PDF processing.\n"
            "Install it with:  pip install pypdf",
            file=sys.stderr,
        )
        sys.exit(1)


def fetch_pdf(url, dest_path):
    """Download *url* to *dest_path* using browser-like headers."""
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req) as response:
        data = response.read()
    with open(dest_path, "wb") as f:
        f.write(data)
    print(f"  Downloaded {url} → {dest_path} ({len(data):,} bytes)")


def fetch_nrcs_pdf():
    """Download the NRCS Field Book PDF to the shared cache path via curl.

    Uses curl rather than urllib so that connection/stall timeouts apply and
    government server restrictions are less likely to block the download.
    Multiple NRCSSoilFieldBook source entries share this one file; the -f loop
    in term_harvester.py deduplicates calls.
    """
    print(f"  Downloading NRCS Field Book PDF from {NRCS_FIELD_BOOK_PDF} ...")
    result = subprocess.run(
        [
            "curl", "-L",
            "--connect-timeout", "30",
            "--max-time", "300",
            "--silent", "--show-error",
            "-o", _NRCS_PDF_CACHE,
            NRCS_FIELD_BOOK_PDF,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  Error downloading PDF: {result.stderr.strip()}", file=sys.stderr)
        return
    with open(_NRCS_PDF_CACHE, 'rb') as fh:
        magic = fh.read(5)
    if magic != b'%PDF-':
        preview = (magic + open(_NRCS_PDF_CACHE, 'rb').read(195))[:200]
        print(
            f"  Error: downloaded content is not a PDF.\n"
            f"  First bytes: {preview.decode('utf-8', errors='replace')[:120]!r}\n"
            f"  Check the URL is still valid: {NRCS_FIELD_BOOK_PDF}",
            file=sys.stderr,
        )
        return
    size = os.path.getsize(_NRCS_PDF_CACHE)
    print(f"  Saved to {_NRCS_PDF_CACHE} ({size:,} bytes)")


def extract_page_text(pdf_path, page_num):
    """Return extracted text from *page_num* (1-based) of *pdf_path*."""
    pypdf = _require_pypdf()
    reader = pypdf.PdfReader(pdf_path)
    return reader.pages[page_num - 1].extract_text()


# ---------------------------------------------------------------------------
# Discussion paragraph extractor (shared across all parsers)
# ---------------------------------------------------------------------------

def _extract_discussion(page_text, marker):
    """Return the discussion paragraph following *marker* + '—'.

    Captures text from the dash after *marker* up to the first "Note:" line,
    a blank line followed by a capital, or the next field label (text ending
    with '—').  Double spaces left by pypdf column layout are collapsed.
    """
    text = page_text.replace("—", "—").replace("–", "—")
    m = re.search(
        re.escape(marker) + r"[—-](.*?)(?=\nNote:|\n\n[A-Z]|\n[A-Z][^—\n]+—|\Z)",
        text, re.DOTALL,
    )
    if not m:
        return ""
    raw = m.group(1).strip()
    raw = re.sub(r"-\n\s*", "", raw)      # rejoin hyphenated line-breaks
    raw = re.sub(r"\s+", " ", raw)        # collapse all whitespace runs
    return raw.strip()


# ---------------------------------------------------------------------------
# Table parsers  (one per enum)
#
# Each parser receives the full extracted text of its PDF page and returns
# a list of dicts: {code, title, description}.
# ---------------------------------------------------------------------------

def _parse_salinity_class(page_text, defn=None):
    """Parse the Salinity Class table (PDF page 141, book page 2-91).

    Table layout after header rows:
        nonsaline            0   < 2
        very slightly saline 1   2 to < 4
        slightly saline      2   4 to < 8
        moderately saline    3   8 to < 16
        strongly saline      4   ≥ 16

    Columns: Salinity Class (title) | Code | Saturated Paste – ECe dS/m
    Description = "ECe {range} dS/m"
    """
    rows = []
    # Each data row: one or more lowercase words, a single-digit code, then range text.
    # The two-space gap between code and range distinguishes range from title words.
    for m in re.finditer(
        r"^([a-z][a-z ]*?)\s+(\d)\s{2,}(.+)$",
        page_text, re.MULTILINE,
    ):
        rows.append({
            "code":        m.group(2),
            "title":       m.group(1).strip(),
            "description": f"ECe {m.group(3).strip()} dS/m",
        })
    return rows


def _parse_observation_method(page_text, defn=None):
    """Parse the Observation Method table (PDF page 154, book page 2-104).

    Table layout:
        Kind (title) | Code | Criteria: Tools and Methods (description)

    The Kind column's text wraps across the line above the code in the PDF
    column layout.  A state machine tracks kind accumulation vs description
    accumulation, using a trailing-space heuristic to distinguish a truncated
    kind word from a description continuation when both precede a code line.

    Descriptions are prefixed with "device:" or "method:" based on keywords
    in the Kind name: dive/observation/video → method, all others → device.
    """
    section_m = re.search(r"Kind\s+Code\s+Criteria[^\n]*\n(.*)", page_text, re.DOTALL)
    if not section_m:
        return []

    METHOD_KEYWORDS = {"observation", "dive", "video"}

    rows = []
    kind_parts = []
    current_code = None
    desc_parts = []

    def flush():
        if current_code:
            kind = " ".join(kind_parts).strip()
            desc = " ".join(desc_parts).strip()
            prefix = "method" if any(w in kind.lower() for w in METHOD_KEYWORDS) else "device"
            rows.append({
                "code":        current_code,
                "title":       kind,
                "description": f"{prefix}: {desc}" if desc else "",
            })

    raw_lines = section_m.group(1).split("\n")
    for i, raw_line in enumerate(raw_lines):
        stripped = raw_line.strip()
        if not stripped:
            continue

        # Look-ahead: find next non-empty line and check if it contains a code
        next_stripped = next(
            (raw_lines[j].strip() for j in range(i + 1, len(raw_lines))
             if raw_lines[j].strip()),
            None,
        )
        next_has_code = bool(next_stripped and re.search(r"\b[A-Z]{2}\b", next_stripped))

        code_m = re.search(r"\b([A-Z]{2})\b", stripped)
        if code_m:
            pre  = stripped[:code_m.start()].strip()
            post = stripped[code_m.end():].strip()
            if current_code is not None:
                flush()
                kind_parts = [pre] if pre else []
            else:
                if pre:
                    kind_parts.append(pre)
            current_code = code_m.group(1)
            desc_parts = [post] if post else []
        elif current_code is not None and next_has_code:
            # Distinguish a truncated kind word (precedes next entry's code line)
            # from a description continuation that happens to be followed by a code
            # line.  Truncated kind words end with a trailing space in the raw PDF
            # output and are short noun fragments (≤3 words, no commas).
            ends_with_space = raw_line.endswith(" ")
            if ends_with_space and len(stripped.split()) <= 3 and "," not in stripped:
                flush()
                kind_parts = [stripped]
                current_code = None
                desc_parts = []
            else:
                desc_parts.append(stripped)
        else:
            (kind_parts if current_code is None else desc_parts).append(stripped)

    flush()
    return rows


def _parse_reaction_ph(page_text, defn=None):
    """Parse the Reaction (pH) table (PDF pages 137–138, book pages 2-87/2-88).

    Table layout:
        Descriptive Term  #  Criteria: pH Range

    The '#' code means no formal codes are assigned; the descriptive term
    itself is used as the permissible-value key.
    """
    rows = []
    for m in re.finditer(
        r"^([a-z][a-z ]+?)\s+#\s+(.+)$",
        page_text, re.MULTILINE,
    ):
        term     = m.group(1).strip()
        ph_range = m.group(2).strip()
        rows.append({
            "code":        term,
            "title":       term,
            "description": f"pH {ph_range}",
        })
    return rows


def _parse_generic(page_text, defn):
    """Generic parser for standard two- or three-column NRCS tables.

    Algorithm:
    1. Anchor to defn['table_header'] — skip text before it.
    2. Pre-join wrapped titles (up to three source lines):
       - 2-line: "moderately \\nfluid MF" → title-word ends with space, next
         line ends with an all-caps code token.
       - 3-line: "extremely \\nhigh\\nEH criteria…" — title fragment ends with
         space, middle word has no code, third line starts with code + space.
    3. Scan for row lines: start with lowercase, contain an all-caps/digit
       1–6-char code token, optionally followed by criteria text.
       Inline footnote digits (e.g. "occasional 2 OC") are stripped.
    4. Criteria that wraps onto subsequent lines is accumulated.
    5. Stop at a field-definition line: starts uppercase or '(' AND contains
       an em/en dash NOT flanked by digits (so "USDA NRCS 1–10" is safe).

    Returns list of {code, title, description} dicts.
    """
    table_header = defn.get("table_header", "") if defn else ""

    # 1. Anchor
    if table_header:
        idx = page_text.find(table_header)
        if idx >= 0:
            page_text = page_text[idx + len(table_header):]

    # 2. Pre-join wrapped titles (2- and 3-line variants)
    _ENDS_WITH_CODE  = re.compile(r'\s+[A-Z][A-Z0-9]{0,5}$')
    _STARTS_WITH_CODE = re.compile(r'^[A-Z][A-Z0-9]{0,5}\s')
    lines = page_text.split("\n")
    joined: list[str] = []
    i = 0
    while i < len(lines):
        l0 = lines[i]
        l1 = lines[i + 1].strip() if i + 1 < len(lines) else ""
        l2 = lines[i + 2].strip() if i + 2 < len(lines) else ""
        if l0.endswith(" ") and _ENDS_WITH_CODE.search(l1):
            # 2-line join: title-fragment + "word CODE"
            joined.append(l0.rstrip() + " " + l1)
            i += 2
        elif (l0.endswith(" ") and l1
              and not l1[0].isupper()
              and not _ENDS_WITH_CODE.search(l1)
              and _STARTS_WITH_CODE.match(l2)):
            # 3-line join: title-word + plain-word + "CODE criteria"
            joined.append(l0.rstrip() + " " + l1 + " " + l2)
            i += 3
        else:
            joined.append(l0)
            i += 1

    # 3 & 4. State-machine row extraction
    _ROW = re.compile(
        r'^([a-z][^"\n]*?)'               # title: starts with lowercase
        r'\s+'
        r'(?:\d+(?:[,\s]+\d+)*\s+)?'     # optional inline footnote markers
        r'([A-Z0-9][A-Z0-9]{0,5})'       # code: 1–6 all-caps/digit chars
        r'(?:\s+(.+))?$'                 # optional same-line criteria
    )
    # Field-definition line: starts uppercase or '(', contains em/en dash
    # NOT flanked by digits (excludes USDA page headers like "1–10 November").
    _FIELD_HEADER = re.compile(r'^[A-Z(].*(?<!\d)[—–](?!\d)')

    def _semicolon_outside_parens(s: str) -> bool:
        """True if *s* contains ';' outside of any parentheses."""
        depth = 0
        for c in s:
            if c == "(":
                depth += 1
            elif c == ")":
                depth = max(0, depth - 1)
            elif c == ";" and depth == 0:
                return True
        return False

    rows: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current:
            rows.append({
                "code":        current["code"],
                "title":       current["title"],
                "description": " ".join(current["desc"]).strip(),
            })
            current = None

    for line in joined:
        stripped = line.strip()
        if not stripped:
            continue

        if _FIELD_HEADER.match(stripped):
            flush()
            break
        if re.match(r'^\d+\s', stripped):  # footnote line
            flush()
            continue

        m = _ROW.match(stripped)
        if m:
            code  = m.group(2)
            title = re.sub(r'\s+\d+(?:[,\s]+\d+)*\s*$', '', m.group(1)).strip()
            # Reject prose false-positives (check title, not full line):
            #   • ';' outside parens in title → description sentence, not label
            #   • title ends with '.' → sentence boundary (footnote continuation)
            #   • '. ' before non-digit in title → internal sentence break
            #   • code is a chemical formula (letter–digit–letter e.g. H2S)
            #   • numeric code with complex title: valid numeric codes (StructureGrade
            #     0–3) have pure-lowercase single-word titles; "< 1 mm" context does not
            if (_semicolon_outside_parens(title)
                    or title.endswith(".")
                    or re.search(r'\. [^0-9]', title)
                    or re.match(r'[A-Z]\d+[A-Z]', code)
                    or (code.isdigit()
                        and (len(code) > 1 or not re.match(r'^[a-z]+$', title)))):
                m = None
        if m:
            flush()
            criteria = (m.group(3) or "").strip()
            current = {
                "code":  code,
                "title": title,
                "desc":  [criteria] if criteria else [],
            }
        elif current is not None and not stripped[0].isupper() and len(stripped) < 80:
            current["desc"].append(stripped)

    flush()
    return rows


# Map parser name strings → callables (used by process_nrcs_source dispatch)
_PARSERS = {
    "_parse_generic":            _parse_generic,
    "_parse_reaction_ph":        _parse_reaction_ph,
    "_parse_salinity_class":     _parse_salinity_class,
    "_parse_observation_method": _parse_observation_method,
}


# ---------------------------------------------------------------------------
# Processing function (called from menu_manager.process_sources)
# ---------------------------------------------------------------------------

def process_nrcs_source(key, source, locales=None):
    """Build a LinkML enum YAML for an NRCSSoilFieldBook source.

    Downloads the PDF to sources/{key}.pdf if not already cached, then
    iterates ENUM_DEFINITIONS to extract each enum and writes
    sources/{key}.yaml.
    """
    base_url  = (source.get("reachable_from") or {}).get("source_ontology", "")
    pdf_path  = _NRCS_PDF_CACHE
    yaml_path = f"sources/{key}.yaml"

    if not os.path.exists(pdf_path):
        print(
            f"Skipping {key}: {pdf_path} not found — run -f {key} to download first",
            file=sys.stderr,
        )
        return
    print(f"  Using cached PDF {pdf_path}")

    schema = make_config_schema(
        id=base_url, name=key,
        title=source.get("title", ""),
        description=source.get("description", ""),
        version=source.get("version", ""),
    )
    schema["enums"] = {}

    pdf_base = base_url.split("#")[0]

    _missing_parsers = []

    for defn in ENUM_DEFINITIONS:
        enum_key    = defn["enum_key"]
        title       = defn["title"]
        pdf_pages   = defn["pdf_pages"]
        disc_marker = defn.get("discussion_marker")
        parser_name = defn["table_parser"]
        see_also    = f"{pdf_base}#page={pdf_pages[0]}"

        parser = _PARSERS.get(parser_name)
        if parser is None:
            _missing_parsers.append(enum_key.rsplit("_", 1)[-1])
            continue

        page_label = (
            f"pages {pdf_pages[0]}–{pdf_pages[-1]}"
            if len(pdf_pages) > 1
            else f"page {pdf_pages[0]}"
        )
        print(f"  Extracting enum {enum_key} from PDF {page_label} ...", end="", flush=True)
        page_text = "\n".join(extract_page_text(pdf_path, p) for p in pdf_pages)

        if "description" in defn:
            description = defn["description"]
        else:
            description = _extract_discussion(page_text, disc_marker)

        rows = parser(page_text, defn)
        if not rows:
            print(f" Warning: no table rows parsed", file=sys.stderr)
            continue

        permissible_values = {}
        for row in rows:
            add_permissible_value(
                permissible_values, row["code"],
                title=row["title"],
                description=row.get("description"),
            )

        schema["enums"][enum_key] = {
            "name":               enum_key,
            "title":              title,
            "description":        description,
            "see_also":           see_also,
            "permissible_values": permissible_values,
        }
        print(f" added ({len(permissible_values)} values)")

    if _missing_parsers:
        print(f"  Warning: no parser(s) for: {', '.join(_missing_parsers)}", file=sys.stderr)

    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    print(f"Updated {yaml_path}")


def match_nrcs(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is an NRCS Field Book PDF URL and was handled.

    Placeholder for future -a auto-detection support.
    """
    if "nrcs.usda.gov" not in url or not url.lower().endswith(".pdf"):
        return False
    # TODO: implement -a add-source detection for NRCS PDF URLs
    return False
