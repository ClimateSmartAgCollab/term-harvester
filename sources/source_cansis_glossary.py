"""CANSIS Glossary of Terms in Soil Science source for term_harvester.py.

On -a/-f: downloads all A-Z and numeric letter pages (EN and FR) plus four
supplementary table pages from the CANSIS website and stores them in a single
zip (sources/CANSIS_GLOSSARY.zip).  FR pages are stored for future use but
are not currently parsed (the FR glossary is an independent French vocabulary,
not a direct translation of the EN entries).

On -c: reads EN letter HTML files from the zip, parses <dl><dt><dd> term/
definition pairs, and writes a flat CANSIS_GLOSSARY enum.

French locale extensions (optional, run manually after -c):
    source_cansis_translate.py is a separate utility that bridges the EN and FR
    glossaries via machine translation and fuzzy matching.  It is NOT run as
    part of -a or -f because it requires an external dependency (deep-translator),
    makes live calls to Google Translate, and produces output that needs human
    review before applying.  Run it once after -c has generated CANSIS_GLOSSARY.yaml:

        pip install deep-translator
        python sources/source_cansis_translate.py --translate
            # review CANSIS_GLOSSARY_translated_fr.tsv and unmatched terms
        python sources/source_cansis_translate.py --apply [--threshold 0.65]
            # re-run -c or -b to incorporate the FR extensions into schema.yaml

    Re-run --translate only when re-fetching source files (-f); the TSV can be
    reused with --apply across multiple -c/-b cycles without re-translating.

Public API used by term_harvester.py:
    process_cansis_glossary_source(key, source, locales=None)
    fetch_cansis_glossary_source(key, source, config_file)
    match_cansis_glossary(url, config_file)
"""

import datetime
import os
import re
import sys
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

_CANSIS_BASE = "https://sis.agr.gc.ca"
_CANSIS_INDEX_URL = "https://sis.agr.gc.ca/cansis/glossary/"
_CANSIS_EN_TMPL = "https://sis.agr.gc.ca/cansis/glossary/{letter}/index.html"
_CANSIS_FR_TMPL = "https://sis.agr.gc.ca/siscan/glossary/{letter}/index.html"
_TABLE_URLS = {
    "table-2.html": "https://sis.agr.gc.ca/cansis/glossary/table-2.html",
    "table-3.html": "https://sis.agr.gc.ca/cansis/glossary/table-3.html",
    "table-4.html": "https://sis.agr.gc.ca/cansis/glossary/table-4.html",
    "cssc_ed3.html": (
        "https://sis.agr.gc.ca/cansis/publications/manuals/1998-cssc-ed3/index.html"
    ),
}
_LETTERS = list("abcdefghijklmnopqrstuvwxyz") + ["_"]
_ZIP_PATH = "sources/CANSIS_GLOSSARY.zip"
_ENUM_KEY = "CANSIS_GLOSSARY"

_TABLE_URL_SET = set(_TABLE_URLS.values())


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _fetch_bytes(url):
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _parse_dl_html(html):
    """Parse <dl><dt><dd> pairs from a CANSIS glossary HTML page.

    Returns list of (term, definition, see_also) where see_also is a
    (possibly empty) list of absolute URLs extracted from <a href> tags
    inside the <dd> that point to supplementary table pages.
    """
    dl_m = re.search(r'<dl>(.*?)</dl>', html, re.IGNORECASE | re.DOTALL)
    if not dl_m:
        return []
    dl_content = dl_m.group(1)

    # Split on <dt>...</dt> boundaries.
    # Yields [pre, term1, after1, term2, after2, ...] where after_i begins
    # with the <dd>...</dd> for term_i.
    parts = re.split(r'<dt>(.*?)</dt>', dl_content,
                     flags=re.IGNORECASE | re.DOTALL)

    entries = []
    for i in range(1, len(parts), 2):
        term_html = parts[i]
        after_html = parts[i + 1] if i + 1 < len(parts) else ""

        term = normalize_text(re.sub(r'<[^>]+>', '', term_html).strip())
        if not term:
            continue

        # Content of the <dd> element
        dd_m = re.match(r'\s*<dd>(.*?)(?:</dd>|$)', after_html,
                        re.IGNORECASE | re.DOTALL)
        dd_html = dd_m.group(1) if dd_m else after_html

        # Collect links to supplementary table pages
        see_also = []
        for link_m in re.finditer(r'<a\s[^>]*href="([^"]+)"', dd_html,
                                  re.IGNORECASE):
            href = link_m.group(1)
            if not href.startswith("http"):
                href = _CANSIS_BASE + (href if href.startswith("/") else "/" + href)
            if href in _TABLE_URL_SET and href not in see_also:
                see_also.append(href)

        definition = normalize_text(
            re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', dd_html)).strip()
        )
        entries.append((term, definition, see_also))
    return entries


# ---------------------------------------------------------------------------
# Downloading (-f / -a)
# ---------------------------------------------------------------------------

def _download_to_zip(zip_path):
    """Fetch all CANSIS glossary pages and write them into zip_path."""
    count_ok = 0
    count_err = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for letter in _LETTERS:
            for lang, tmpl in (("en", _CANSIS_EN_TMPL),
                               ("fr", _CANSIS_FR_TMPL)):
                url = tmpl.format(letter=letter)
                zip_name = f"{letter}.html" if lang == "en" else f"{letter}_fr.html"
                print(f"  Fetching {url} ...", end=" ", flush=True)
                try:
                    data = _fetch_bytes(url)
                    zf.writestr(zip_name, data)
                    print(f"ok ({len(data):,} bytes)")
                    count_ok += 1
                except Exception as e:
                    print(f"error: {e}", file=sys.stderr)
                    count_err += 1

        for zip_name, url in _TABLE_URLS.items():
            print(f"  Fetching {url} ...", end=" ", flush=True)
            try:
                data = _fetch_bytes(url)
                zf.writestr(zip_name, data)
                print(f"ok ({len(data):,} bytes)")
                count_ok += 1
            except Exception as e:
                print(f"error: {e}", file=sys.stderr)
                count_err += 1

    size = os.path.getsize(zip_path)
    suffix = f", {count_err} errors" if count_err else ""
    print(f"  Saved {zip_path} ({size:,} bytes, {count_ok} files{suffix})")


# ---------------------------------------------------------------------------
# Processing (-c)
# ---------------------------------------------------------------------------

def process_cansis_glossary_source(key, source, locales=None):
    """Parse EN letter pages from CANSIS_GLOSSARY.zip and write sources/{key}.yaml."""
    if not os.path.exists(_ZIP_PATH):
        print(f"  Skipping {key}: {_ZIP_PATH} not found — run -f to fetch first",
              file=sys.stderr)
        return

    permissible_values = {}
    seen_lower = set()

    with zipfile.ZipFile(_ZIP_PATH) as zf:
        names_in_zip = set(zf.namelist())
        for letter in _LETTERS:
            zip_name = f"{letter}.html"
            if zip_name not in names_in_zip:
                continue
            html = zf.read(zip_name).decode("utf-8", errors="replace")
            for term, definition, see_also in _parse_dl_html(html):
                if not term:
                    continue
                # Deduplicate: skip if the same term (case-insensitive) was
                # already added from a different letter page.
                term_lower = term.lower()
                if term_lower in seen_lower:
                    continue
                seen_lower.add(term_lower)

                add_permissible_value(
                    permissible_values, term,
                    title=term,
                    description=definition or None,
                )
                if see_also:
                    permissible_values[term]["see_also"] = (
                        see_also[0] if len(see_also) == 1 else see_also
                    )

    source_url = _CANSIS_INDEX_URL
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=(source.get("title")
               or "CANSIS Glossary of Terms in Soil Science"),
        description=(source.get("description") or (
            "Glossary of soil science terms from the Canadian Soil Information"
            " Service (CANSIS), Agriculture and Agri-Food Canada."
        )),
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

def fetch_cansis_glossary_source(key, source, config_file=MENU_CONFIG):
    """Re-download all CANSIS glossary pages and store in zip."""
    print(f"  Fetching CANSIS Glossary pages ...")
    _download_to_zip(_ZIP_PATH)
    update_source_config(
        key, {"download_date": datetime.date.today().isoformat()}, config_file)
    process_cansis_glossary_source(key, source)


# ---------------------------------------------------------------------------
# Match / initial add (-a)
# ---------------------------------------------------------------------------

def match_cansis_glossary(url, config_file=MENU_CONFIG):
    """Return True if url is the CANSIS Glossary index and was handled."""
    clean = url.split("#")[0].rstrip("/") + "/"
    if clean != _CANSIS_INDEX_URL:
        return False

    try:
        with open(config_file) as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config = {}

    key = "CANSIS_GLOSSARY"
    if key in config.get("sources", {}):
        print(f"  Skipping {url}: source key '{key}' already exists in"
              f" {config_file}", file=sys.stderr)
        return True

    print(f"  Fetching CANSIS Glossary pages ...")
    _download_to_zip(_ZIP_PATH)

    entry = make_source_entry(
        key, _CANSIS_INDEX_URL, "CANSIS_GLOSSARY", "zip",
        title="CANSIS Glossary of Terms in Soil Science",
        version=str(datetime.date.today().year),
        description=(
            "Glossary of soil science terms from the Canadian Soil Information"
            " Service (CANSIS), Agriculture and Agri-Food Canada."
        ),
    )
    entry["see_also"] = _CANSIS_INDEX_URL

    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"  Added source '{key}' to {config_file}")

    process_cansis_glossary_source(key, entry)
    return True
