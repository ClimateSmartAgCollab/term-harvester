"""Library of Congress Classification source for term_harvester.py.

On -a/-f: downloads each class PDF, immediately extracts its text via pypdf,
stores all text files in a single zip (sources/LOC_CLASSIFICATION.zip).
The PDFs are discarded after text extraction — pypdf is only needed at fetch
time, not during -c processing.

On -c: reads text files from the zip and parses them into a LOCClassification
enum with is_a hierarchy.  Hierarchy within each subclass is determined by
numeric range containment: AC1-195 (1–195) ⊂ AC1-999 (1–999), so AC1-195
is a child of AC1-999.

Public API used by term_harvester.py:
    process_loc_source(key, source, locales=None)
    fetch_loc_source(key, source, config_file)
    match_loc(url, config_file)
"""

import datetime
import os
import re
import sys
import tempfile
import urllib.request
import zipfile
import yaml

from source_utils import (
    BROWSER_HEADERS,
    IndentedDumper,
    MENU_CONFIG,
    add_permissible_value,
    log_extraction,
    make_config_schema,
    make_source_entry,
    normalize_text,
    update_source_config,
    write_config,
)

_LOC_INDEX_URL = "https://www.loc.gov/catdir/cpso/lcco/"
_LOC_BASE = "https://www.loc.gov"
_ZIP_PATH = "sources/LOC_CLASSIFICATION.zip"
_ENUM_KEY = "LOCClassification"

_CLASS_TITLE_RE = re.compile(
    r'^CLASS\s+([A-Z]+(?:-[A-Z]+)?)\s+[-–]+\s+(.+)')
_SUBCLASS_RE = re.compile(
    r'^Subclass(?:es)?\s+([A-Z][A-Z0-9-]*)\s*(.*)')
_CLASS_SECTION_RE = re.compile(r'^Class\s+([A-Z]+)\s*$')
_CODE_RE = re.compile(
    r'^([A-Z]{1,3}(?:[\d.()/-][^\s]*)?)\s(\S.*)', re.DOTALL)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _require_pypdf():
    try:
        import pypdf
        import logging
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        return pypdf
    except ImportError:
        print("Error: pypdf is required to fetch LOC Classification PDFs.\n"
              "Install it with:  pip install pypdf", file=sys.stderr)
        sys.exit(1)


def _fetch_bytes(url):
    """Fetch URL and return raw bytes (no file written)."""
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _fetch_url(url, dest_path):
    data = _fetch_bytes(url)
    with open(dest_path, "wb") as f:
        f.write(data)
    return data


def _pdf_to_text(pdf_path):
    """Extract all page text from a LOC Classification PDF, joined by newlines."""
    pypdf = _require_pypdf()
    reader = pypdf.PdfReader(pdf_path)
    pages = []
    for page in reader.pages:
        t = page.extract_text() or ""
        pages.append(t)
    return "\n".join(pages)


def _get_pdf_links_from_html(html):
    """Return list of (abs_pdf_url, txt_filename) from the LOC index HTML string.

    Deduplicates PDF URLs (E and F share lcco_ef.pdf).
    """
    mb = html.find('id="main_body"')
    ul_start = html.find("<ul>", mb)
    ul_end = html.find("</ul>", ul_start)
    ul_html = html[ul_start:ul_end]

    seen = set()
    links = []
    for m in re.finditer(
            r'<a\s+href="([^"]+\.pdf)"[^>]*>', ul_html, re.IGNORECASE):
        href = m.group(1)
        abs_url = _LOC_BASE + href if href.startswith("/") else href
        if abs_url in seen:
            continue
        seen.add(abs_url)
        filename = href.rstrip("/").split("/")[-1]
        txt_name = filename.replace(".pdf", ".txt")
        links.append((abs_url, txt_name))
    return links


def _sanitize_code(code):
    return re.sub(r"[()]", "", code).strip()


def _parse_code_range(code):
    """Return (letters, start_float, end_float) or None."""
    clean = re.sub(r"[()]", "", code)
    m = re.match(r"^([A-Z]{1,3})([\d.]+)(?:-([\d.]+))?$", clean)
    if not m:
        return None
    try:
        letters = m.group(1)
        start = float(m.group(2))
        end = float(m.group(3)) if m.group(3) else start
        return (letters, start, end)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Text parsing
# ---------------------------------------------------------------------------

def _parse_loc_text(text):
    """Parse extracted text from one LOC Classification PDF.

    Returns dict:
        'classes':          {letter: title}
        'subclasses':       {code: {'title': str, 'class': str}}
        'subclass_entries': {code_or_letter: [(code, title, desc), ...]}
    """
    result = {"classes": {}, "subclasses": {}, "subclass_entries": {}}

    current_subclass = None   # subclass code while processing detail pages
    in_ef_class = None        # class letter for EF-style PDFs (no Subclass layer)
    last_entry_ctx = None     # (context, index) for appending continuations

    for raw_line in text.splitlines():
        line = normalize_text(raw_line.strip())
        if not line:
            continue

        # Class title on cover page: "CLASS A - GENERAL WORKS"
        m = _CLASS_TITLE_RE.match(line)
        if m:
            class_title = re.sub(r"\s+", " ", m.group(2)).strip()
            for letter in m.group(1).split("-"):
                result["classes"][letter] = class_title
            continue

        # Skip boilerplate
        if line.startswith("LIBRARY OF CONGRESS") or line.startswith("(Click"):
            continue

        # Subclass line — could be cover entry ("Subclass AC Collections...")
        # or detail header ("Subclass AC" with no title).
        m = _SUBCLASS_RE.match(line)
        if m:
            sub_code = m.group(1)
            sub_title = re.sub(r"\s+", " ", m.group(2)).strip()
            if sub_title:
                # Cover page: record subclass title
                result["subclasses"][sub_code] = {
                    "title": sub_title,
                    "class": sub_code[0],
                }
            else:
                # Detail page header: switch active subclass
                current_subclass = sub_code
                in_ef_class = None
                last_entry_ctx = None
                result["subclass_entries"].setdefault(sub_code, [])
                if sub_code not in result["subclasses"]:
                    result["subclasses"][sub_code] = {
                        "title": "",
                        "class": sub_code[0],
                    }
            continue

        # EF-style class header: "Class E"
        m = _CLASS_SECTION_RE.match(line)
        if m:
            in_ef_class = m.group(1)
            current_subclass = None
            last_entry_ctx = None
            result["subclass_entries"].setdefault(in_ef_class, [])
            continue

        # Code entry: starts with 1-3 uppercase letters then digit/(
        m = _CODE_RE.match(line)
        if m:
            context = in_ef_class or current_subclass
            if not context:
                continue
            code = _sanitize_code(m.group(1))
            title = re.sub(r"\s+", " ", m.group(2)).strip()
            entries = result["subclass_entries"].setdefault(context, [])
            idx = len(entries)
            entries.append((code, title, ""))
            last_entry_ctx = (context, idx)
            continue

        # Anything else: continuation/description for the previous code entry
        if last_entry_ctx:
            ctx, idx = last_entry_ctx
            old = result["subclass_entries"][ctx][idx]
            new_desc = ((old[2] or "") + " " + line).strip()
            result["subclass_entries"][ctx][idx] = (old[0], old[1], new_desc)

    return result


# ---------------------------------------------------------------------------
# Downloading (fetch + convert to text, store in zip)
# ---------------------------------------------------------------------------

def _download_and_zip(html_content, pdf_links, zip_path):
    """Download each PDF, extract its text, store index.html + .txt files in zip_path."""
    _require_pypdf()  # ensure pypdf available before starting downloads
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", html_content)
        for pdf_url, txt_name in pdf_links:
            print(f"  Downloading {txt_name.replace('.txt', '.pdf')} ...")
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
            os.close(tmp_fd)
            try:
                data = _fetch_url(pdf_url, tmp_path)
                if not data.startswith(b"%PDF-"):
                    print(f"    Warning: response may not be a valid PDF",
                          file=sys.stderr)
                text = _pdf_to_text(tmp_path)
                zf.writestr(txt_name, text)
                print(f"    Extracted text → {txt_name} ({len(text):,} chars)")
            except Exception as e:
                print(f"    Error: {e}", file=sys.stderr)
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
    size = os.path.getsize(zip_path)
    print(f"  Saved {zip_path} ({size:,} bytes, {len(pdf_links)} files)")


# ---------------------------------------------------------------------------
# Processing (-c)
# ---------------------------------------------------------------------------

def process_loc_source(key, source, locales=None):
    """Parse text from LOC_CLASSIFICATION.zip and write sources/{key}.yaml."""
    if not os.path.exists(_ZIP_PATH):
        print(f"  Skipping {key}: {_ZIP_PATH} not found — run -f to fetch first",
              file=sys.stderr)
        return

    permissible_values = {}

    with zipfile.ZipFile(_ZIP_PATH) as zf:
        txt_names = sorted(n for n in zf.namelist() if n.endswith(".txt"))
        for txt_name in txt_names:
            text = zf.read(txt_name).decode("utf-8", errors="replace")
            data = _parse_loc_text(text)

            # Class-level PVs
            for letter, class_title in data["classes"].items():
                class_key = f"CLASS_{letter}"
                if class_key not in permissible_values:
                    add_permissible_value(
                        permissible_values, class_key,
                        title=class_title.title(),
                    )

            # Subclass-level PVs
            for sub_code, sub_info in data["subclasses"].items():
                if sub_code not in permissible_values:
                    add_permissible_value(
                        permissible_values, sub_code,
                        title=sub_info["title"] or sub_code,
                    )
                    permissible_values[sub_code]["is_a"] = (
                        f"CLASS_{sub_info['class']}")

            # Code-level PVs with range-containment hierarchy
            for context, entries in data["subclass_entries"].items():
                context_pv_key = (f"CLASS_{context}"
                                  if context in data["classes"]
                                  else context)
                stack = []  # (letters, start, end, pv_key)

                for code, title, desc in entries:
                    if not code:
                        continue
                    # Skip bare subclass entry (same key as subclass PV)
                    if code == context and code in permissible_values:
                        continue

                    parsed = _parse_code_range(code)
                    if parsed:
                        letters, start, end = parsed
                        while stack:
                            sl, ss, se = stack[-1][0], stack[-1][1], stack[-1][2]
                            if sl == letters and ss <= start and se >= end:
                                break
                            stack.pop()
                        parent_key = stack[-1][3] if stack else context_pv_key
                        stack.append((letters, start, end, code))
                    else:
                        parent_key = context_pv_key

                    if code not in permissible_values:
                        add_permissible_value(
                            permissible_values, code,
                            title=title or None,
                            description=desc or None,
                        )
                        permissible_values[code]["is_a"] = parent_key

    # Reorder into DFS hierarchy: CLASS_X → subclasses → code ranges, so each
    # subclass's code entries appear immediately after the subclass PV.
    _children = {}
    _roots = []
    for _k, _pv in permissible_values.items():
        _parent = (_pv or {}).get("is_a")
        if _parent and _parent in permissible_values:
            _children.setdefault(_parent, []).append(_k)
        else:
            _roots.append(_k)

    def _dfs(k):
        yield k
        for c in _children.get(k, []):
            yield from _dfs(c)

    _ordered = {}
    for _root in sorted(_roots):
        for _k in _dfs(_root):
            _ordered[_k] = permissible_values[_k]
    for _k in permissible_values:  # include any orphans at end
        if _k not in _ordered:
            _ordered[_k] = permissible_values[_k]
    permissible_values = _ordered

    source_url = (source.get("reachable_from") or {}).get(
        "source_ontology", _LOC_INDEX_URL)
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or "Library of Congress Classification",
        description=source.get("description") or (
            "The Library of Congress Classification (LCC) is a system of"
            " library classification developed by the Library of Congress."
        ),
        version=source.get("version") or "",
        enums={_ENUM_KEY: {"permissible_values": permissible_values}},
    )

    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper,
                  default_flow_style=False, sort_keys=False)
    log_extraction(key, count=len(permissible_values))


# ---------------------------------------------------------------------------
# Fetch (-f)
# ---------------------------------------------------------------------------

def fetch_loc_source(key, source, config_file=MENU_CONFIG):
    """Re-download LOC Classification PDFs, convert to text, store in zip."""
    print(f"  Fetching {_LOC_INDEX_URL} ...")
    try:
        html_bytes = _fetch_bytes(_LOC_INDEX_URL)
    except Exception as e:
        print(f"  Error fetching index: {e}", file=sys.stderr)
        return
    html_content = html_bytes.decode("utf-8", errors="replace")
    print(f"  Fetched index ({len(html_bytes):,} bytes).")

    pdf_links = _get_pdf_links_from_html(html_content)
    if not pdf_links:
        print("  Warning: no PDF links found in index", file=sys.stderr)
        return
    print(f"  Found {len(pdf_links)} class PDF(s).")

    _download_and_zip(html_content, pdf_links, _ZIP_PATH)
    update_source_config(
        key, {"download_date": datetime.date.today().isoformat()}, config_file)
    process_loc_source(key, source)


# ---------------------------------------------------------------------------
# Match / initial add (-a)
# ---------------------------------------------------------------------------

def match_loc(url, config_file=MENU_CONFIG):
    """Return True if url is the LOC Classification index and was handled."""
    clean = url.split("#")[0].rstrip("/") + "/"
    if clean != _LOC_INDEX_URL:
        return False

    try:
        with open(config_file) as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config = {}

    key = "LOC_CLASSIFICATION"
    if key in config.get("sources", {}):
        print(f"  Skipping {url}: source key '{key}' already exists in"
              f" {config_file}", file=sys.stderr)
        return True

    print(f"  Fetching {_LOC_INDEX_URL} ...")
    try:
        html_bytes = _fetch_bytes(_LOC_INDEX_URL)
    except Exception as e:
        print(f"  Error fetching index: {e}", file=sys.stderr)
        return True
    html_content = html_bytes.decode("utf-8", errors="replace")
    print(f"  Fetched index ({len(html_bytes):,} bytes).")

    pdf_links = _get_pdf_links_from_html(html_content)
    if not pdf_links:
        print("  Warning: no PDF links found in index", file=sys.stderr)
        return True
    print(f"  Found {len(pdf_links)} class PDF(s).")

    _download_and_zip(html_content, pdf_links, _ZIP_PATH)

    entry = make_source_entry(
        key, _LOC_INDEX_URL, "LOC_CLASSIFICATION", "zip",
        title="Library of Congress Classification",
        version=str(datetime.date.today().year),
        description=(
            "The Library of Congress Classification (LCC) is a system of"
            " library classification developed by the Library of Congress."
        ),
    )
    entry["see_also"] = "https://www.loc.gov/aba/cataloging/classification/"

    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    process_loc_source(key, entry)
    return True
