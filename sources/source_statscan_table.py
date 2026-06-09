"""Statistics Canada Census Dictionary table page handler for term_harvester.py.

Handles URLs of the form:
  https://www12.statcan.gc.ca/census-recensement/2021/ref/dict/tab/index-eng.cfm?ID=T1_8

Parses the pub-table on the page into a single LinkML enum.  The code for each
row is taken from the first column whose header contains "alpha", then "abbrev",
then "code"; if none match, the row-stub text is slugified as the code.  French
translations are fetched automatically from the corresponding index-fra.cfm page.

Public API used by term_harvester.py:
    match_statscan_table(url, tmp_path, config_file)
    process_statscan_table_source(key, source, config_file, locales)
"""

import html as html_module
import os
import re
import sys
import yaml

from source_utils import (
    BROWSER_HEADERS,
    MENU_CONFIG,
    IndentedDumper,
    _make_locale_extensions,
    add_permissible_value,
    fetch_html,
    make_config_schema,
    make_source_entry,
    write_config,
)

_URL_RE = re.compile(
    r'//www12\.statcan\.gc\.ca/census-recensement/[^/]+/ref/dict/tab/'
    r'index-(?:eng|fra)\.cfm',
    re.IGNORECASE,
)


def _fr_url(url):
    """Return the French-language equivalent of a census dictionary tab URL."""
    return re.sub(r'\bindex-eng\.cfm\b', 'index-fra.cfm', url, flags=re.IGNORECASE)


def _id_from_url(url):
    """Extract the ID query parameter value (e.g. 'T1_8') from the URL."""
    m = re.search(r'[?&]ID=([A-Za-z0-9_]+)', url, re.IGNORECASE)
    return m.group(1) if m else None


def _parse_pub_table(html_text):
    """Parse the <table class="pub-table"> from a census dictionary table page.

    Returns:
        page_title (str)  — table title from <span class="h1title">, last <br> segment
        headers    (list) — column header labels (strings); index 0 = row-stub column
        rows       (list) — list of rows; each row is [row_stub, td1, td2, ...]
    """
    def _clean(fragment):
        text = re.sub(r'<[^>]+>', ' ', fragment)
        text = html_module.unescape(text)            # decode &nbsp; → \xa0 before collapsing
        text = text.replace('‑', '-')           # non-breaking hyphen → regular hyphen
        return re.sub(r'[\s\xa0]+', ' ', text).strip()

    # Page title — last non-"Table N.N" segment in the h1title span
    page_title = ""
    span_m = re.search(
        r'<span\b[^>]*\bh1title\b[^>]*>(.*?)</span>',
        html_text, re.IGNORECASE | re.DOTALL,
    )
    if span_m:
        for raw_part in reversed(re.split(r'<br\s*/?>', span_m.group(1), flags=re.IGNORECASE)):
            text = _clean(raw_part)
            if text and not re.match(r'^(?:Table|Tableau)\s+[\d.]+', text, re.IGNORECASE):
                page_title = text
                break

    # Table
    table_m = re.search(
        r'<table\b[^>]*\bpub-table\b[^>]*>(.*?)</table>',
        html_text, re.IGNORECASE | re.DOTALL,
    )
    if not table_m:
        return page_title, [], []

    headers = []
    rows = []

    for tr_m in re.finditer(
            r'<tr\b[^>]*>(.*?)</tr>', table_m.group(1), re.IGNORECASE | re.DOTALL):
        row_html = tr_m.group(1)

        # Header row: cells with class="col-left"
        col_th = re.findall(
            r'<th\b[^>]*\bcol-left\b[^>]*>(.*?)</th>',
            row_html, re.IGNORECASE | re.DOTALL,
        )
        if col_th:
            headers = [_clean(c) for c in col_th]
            continue

        # Data row: row-stub <th> followed by <td> cells
        stub_m = re.search(
            r'<th\b[^>]*\brow-stub\b[^>]*>(.*?)</th>',
            row_html, re.IGNORECASE | re.DOTALL,
        )
        if not stub_m:
            continue
        tds = [_clean(c) for c in re.findall(
            r'<td\b[^>]*>(.*?)</td>', row_html, re.IGNORECASE | re.DOTALL,
        )]
        rows.append([_clean(stub_m.group(1))] + tds)

    return page_title, headers, rows


def _pick_code_col(headers):
    """Return the 0-based row-list index to use as the enum code.

    headers[0] is the row-stub column; remaining indices are TD columns.
    Preference order:
        1. "alpha"  in header   (internationally approved alpha code)
        2. "abbr"   in header   (standard abbreviation)
        3. first non-stub header containing "code"
        4. -1 → fall back: slugify the row-stub text as the code
    """
    for i, h in enumerate(headers):
        if 'alpha' in h.lower():
            return i
    for i, h in enumerate(headers):
        if i == 0:
            continue
        if re.search(r'\babr[eé]v|\babbr', h.lower()):
            return i
    for i, h in enumerate(headers):
        if i == 0:
            continue
        if 'code' in h.lower():
            return i
    return -1


def _stub_to_code(stub):
    """Derive a fallback code from a row-stub label."""
    code = re.sub(r'[^A-Za-z0-9]+', '_', stub).strip('_').upper()
    return code[:40] if code else stub[:20].upper()


def _header_to_enum_key(header):
    """Convert a column header string to a PascalCase identifier.

    "Province/Territory" → "ProvinceTerritory"
    "Age group"          → "AgeGroup"
    """
    words = re.findall(r'[A-Za-z0-9]+', header)
    return ''.join(w.capitalize() for w in words) if words else "Enum"


def _build_permissible_values(headers, rows, fr_rows=None):
    """Return (pv_en, pv_fr) dicts of permissible values from parsed table rows."""
    code_col = _pick_code_col(headers)
    pv_en = {}
    pv_fr = {}

    for i, row in enumerate(rows):
        stub = row[0]
        if not stub:
            continue
        if code_col >= 0 and code_col < len(row):
            code = row[code_col].strip()
        else:
            code = _stub_to_code(stub)
        if not code:
            continue

        add_permissible_value(pv_en, code, title=stub)

        if fr_rows and i < len(fr_rows):
            fr_stub = fr_rows[i][0]
            if fr_stub:
                add_permissible_value(pv_fr, code, title=fr_stub)

    return pv_en, pv_fr


def process_statscan_table_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Build sources/{key}.yaml from a downloaded census dictionary table page.

    Reads sources/{key}.html (English) and sources/{key}_fr.html (French, if
    present and 'fr' is in locales).  Writes a single-enum sources/{key}.yaml.
    """
    html_path = f"sources/{key}.html"
    with open(html_path, encoding="utf-8", errors="replace") as f:
        en_html = f.read()

    page_title, headers, rows = _parse_pub_table(en_html)
    if not rows:
        print(f"  Warning: no data rows found in {html_path}", file=sys.stderr)
        return

    fr_rows = None
    fr_html_path = f"sources/{key}_fr.html"
    if "fr" in (locales or ["en"]) and os.path.exists(fr_html_path):
        with open(fr_html_path, encoding="utf-8", errors="replace") as f:
            fr_html = f.read()
        _, _, fr_rows = _parse_pub_table(fr_html)

    pv_en, pv_fr = _build_permissible_values(headers, rows, fr_rows=fr_rows)

    # Enum key/name/title are derived from the row-stub column header (headers[0])
    enum_key = _header_to_enum_key(headers[0]) if headers else key
    enum_title = headers[0] if headers else key

    source_url = (source.get("reachable_from") or {}).get("source_ontology", "")
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or page_title or key,
        enums={enum_key: {
            "name":               enum_key,
            "title":              enum_title,
            "permissible_values": pv_en,
        }},
    )
    if pv_fr:
        schema["extensions"] = _make_locale_extensions(
            _fr_url(source_url), key, source.get("version") or "", "fr",
            enums={enum_key: {"permissible_values": pv_fr}},
        )

    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    n_fr = len(pv_fr)
    print(f"Updated {yaml_path} ({len(pv_en)} values"
          + (f", {n_fr} French translations" if n_fr else "") + ")")


def match_statscan_table(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is a StatsCan census dictionary table page and was handled.

    Matches:
        https://www12.statcan.gc.ca/census-recensement/2021/ref/dict/tab/index-eng.cfm?ID=T1_8
    """
    if not _URL_RE.search(url):
        return False

    term_id = _id_from_url(url)
    if not term_id:
        print(f"  Warning: could not extract ?ID= from {url}", file=sys.stderr)
        os.unlink(tmp_path)
        return True

    key = f"STATSCAN_TABLE_{term_id}"

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    if key in config.get("sources", {}):
        print(
            f"  Skipping {url}: source key '{key}' already exists in {config_file}",
            file=sys.stderr,
        )
        os.unlink(tmp_path)
        return True

    # Save English HTML
    output_path = f"sources/{key}.html"
    os.rename(tmp_path, output_path)
    print(f"Saved to {output_path}")

    with open(output_path, encoding="utf-8", errors="replace") as f:
        en_html = f.read()
    page_title, _, _ = _parse_pub_table(en_html)
    title = page_title or f"StatsCan Table {term_id}"

    # Fetch and save French HTML
    fr_html_path = f"sources/{key}_fr.html"
    fr_url = _fr_url(url)
    try:
        print(f"  Fetching French page {fr_url} ...")
        with open(fr_html_path, "w", encoding="utf-8") as f:
            f.write(fetch_html(fr_url))
        print(f"Saved to {fr_html_path}")
    except Exception as e:
        print(f"  Warning: could not fetch French page: {e}", file=sys.stderr)

    entry = make_source_entry(key, url, "STATSCAN_TABLE", "html", title=title)
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"Added source '{key}' to {config_file}")
    return True
