"""National Soil Database (NSDB) source helpers for term_harvester.py.

Provides HTML-parsing, URL utilities, and processing functions for NSDB
classification pages used by the add_source() NSDB detection blocks and
process_sources().

Download/process split
----------------------
*  -a  URL  (match_nsdb_*)     — downloads the index page, adds the config
   entry, then calls _crawl_and_save_nsdb_zip to fetch every linked attribute
   page (EN + FR) into sources/{key}.zip.
*  -f  key  (fetch_nsdb_*)     — same crawl, rebuilds sources/{key}.zip from
   scratch and updates download_date in the config.
*  -c  key  (process_nsdb_*)   — reads sources/{key}.zip only; no network
   access.  Falls back to live fetches with a warning when no zip exists
   (backward compatibility with sources added before this change).

Zip format
----------
sources/{key}.zip contains:
  manifest.json     — {url: entry_filename} mapping
  0000.html …       — raw HTML for each URL, UTF-8 encoded

Public API used by term_harvester.py:
    nsdb_fr_url(url)
    find_section_paragraph(html_text, section_name)
    find_links_by_text(html_text, link_texts, base_url)
    find_named_section_links(html_text, section_name, base_url)
    find_contents_table_links(html_text, base_url)
    find_list_section_links(html_text, section_text, base_url)
    parse_attribute_page(html_text)
    fetch_nsdb_html_source(key, source, config_file, locales)
    fetch_nsdb_source(key, source, config_file, locales)
    process_nsdb_html_source(key, source, enum_prefix, locales)
    process_nsdb_source(key, source, locales)
    match_nsdb_snt(url, tmp_path, config_file)
    match_nsdb_slt(url, tmp_path, config_file)
    match_nsdb_soil(url, tmp_path, config_file)
    match_nsdb_slc(url, tmp_path, config_file)
"""

import datetime
import json
import os
import re
import sys
import urllib.parse
import yaml
import zipfile

from source_utils import (
    strip_tags as _strip_tags,
    strip_tags,
    fetch_html,
    add_permissible_value,
    log_extraction,
    _make_locale_extensions,
    IndentedDumper,
    make_config_schema,
    make_source_entry,
    update_source_config,
    write_config,
    MENU_CONFIG,
)

_NSDB_SEE_ALSO = "https://sis.agr.gc.ca/cansis/nsdb/index.html"


def nsdb_fr_url(url):
    """Return the French-language equivalent of an NSDB URL.

    The French NSDB pages live under ``/siscan/`` rather than ``/cansis/``.
    """
    return url.replace('/cansis/', '/siscan/')


def find_section_paragraph(html_text, section_name):
    """Return plain text of the first paragraph following a header containing section_name."""
    m = re.search(
        r'<h[2-4][^>]*>[^<]*' + re.escape(section_name) + r'[^<]*</h[2-4]>'
        r'(?:\s*<[^>]+>)*\s*<p[^>]*>(.*?)</p>',
        html_text, re.IGNORECASE | re.DOTALL
    )
    return _strip_tags(m.group(1)) if m else ""


def find_links_by_text(html_text, link_texts, base_url):
    """Find anchor links whose display text matches any entry in link_texts.

    Returns {display_text: absolute_url}.
    """
    results = {}
    for m in re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                         html_text, re.IGNORECASE | re.DOTALL):
        text = _strip_tags(m.group(2))
        if text in link_texts:
            results[text] = urllib.parse.urljoin(base_url, m.group(1))
    return results


def find_named_section_links(html_text, section_name, base_url):
    """Find Name-column links from the table in a named section.

    Locates a heading whose text contains section_name (case-insensitive) and
    returns links from the Name column of the first table in that section.
    Falls back to all <a> hrefs in the section if no Name-column table is found.

    Returns a list of (name, absolute_url) tuples.
    """
    m = re.search(
        r'<(h[2-4])[^>]*>[^<]*' + re.escape(section_name) + r'[^<]*</\1>(.*?)(?=<h[2-4]|\Z)',
        html_text, re.IGNORECASE | re.DOTALL
    )
    if not m:
        return []
    section_html = m.group(2)
    table_m = re.search(r'<table[^>]*>(.*?)</table>', section_html, re.IGNORECASE | re.DOTALL)
    if table_m:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_m.group(1), re.IGNORECASE | re.DOTALL)
        if rows:
            header_cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', rows[0], re.IGNORECASE | re.DOTALL)
            header_texts = [_strip_tags(h).lower() for h in header_cells]
            name_col = next((i for i, h in enumerate(header_texts) if h == 'name'), None)
            if name_col is not None:
                results = []
                for row in rows[1:]:
                    cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
                    if len(cells) > name_col:
                        link_m = re.search(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                                            cells[name_col], re.IGNORECASE | re.DOTALL)
                        if link_m:
                            results.append((_strip_tags(link_m.group(2)),
                                             urllib.parse.urljoin(base_url, link_m.group(1))))
                return results
    # Fallback: collect all <a> hrefs in the section
    results = []
    for link_m in re.finditer(r'<a\s[^>]*href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>',
                               section_html, re.IGNORECASE | re.DOTALL):
        text = _strip_tags(link_m.group(2)).strip()
        if text:
            results.append((text, urllib.parse.urljoin(base_url, link_m.group(1))))
    return results


def find_contents_table_links(html_text, base_url):
    """Find Name-column links from the Contents section table.

    Returns a list of (name, absolute_url) tuples.
    """
    return find_named_section_links(html_text, "Contents", base_url)


def find_list_section_links(html_text, section_text, base_url):
    """Find <a> links from the <ol> nested inside a <li> whose label contains section_text.

    Handles structures like:
      <li><abbr title="...">SLC</abbr> attribute tables <ol><li><a href="...">...</a></li></ol></li>
      <li>Ecological Framework Tables <ol>...</ol></li>

    For each <li>, strips tags from the content before the first nested <ol> to form the
    label; if the label contains section_text (case-insensitive), returns all <a> links
    found within that <ol>.

    Returns a list of (name, absolute_url) tuples.
    """
    for li_m in re.finditer(r'<li\b[^>]*>', html_text, re.IGNORECASE):
        after_li = html_text[li_m.end():]
        ol_m = re.search(r'<ol\b[^>]*>(.*?)</ol>', after_li, re.IGNORECASE | re.DOTALL)
        if not ol_m:
            continue
        label_text = _strip_tags(after_li[:ol_m.start()]).strip()
        if section_text.lower() not in label_text.lower():
            continue
        results = []
        for link_m in re.finditer(
                r'<a\s[^>]*href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>',
                ol_m.group(1), re.IGNORECASE | re.DOTALL):
            text = _strip_tags(link_m.group(2)).strip()
            if text:
                results.append((text, urllib.parse.urljoin(base_url, link_m.group(1))))
        if results:
            return results
    return []


def parse_attribute_page(html_text):
    """Parse an NSDB attribute definition page.

    Returns (label, title, description, pv_tables) where pv_tables is a list
    of lists of {'code', 'class_', 'description'} dicts — one inner list per
    permissible-value table found on the page.
    """
    label = title = description = ""

    def norm_key(raw):
        """Normalise a cell key: strip tags, collapse whitespace/underscores, remove trailing colon."""
        return re.sub(r'[\s_]+', ' ', _strip_tags(raw)).lower().rstrip(':').strip()

    # Scan all tables for attribute definition rows (label, title, definition/description).
    # This avoids relying on "Attribute Definition" heading placement in the HTML.
    all_tables = re.findall(r'<table[^>]*>(.*?)</table>', html_text, re.IGNORECASE | re.DOTALL)
    for table_html in all_tables:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.IGNORECASE | re.DOTALL)
        for row in rows:
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.IGNORECASE | re.DOTALL)
            if len(cells) >= 2:
                k = norm_key(cells[0])
                v = _strip_tags(cells[1])
                if k in ('attribute label', 'label'):
                    label = v
                elif k in ('attribute title', 'title'):
                    title = v
                elif k in ('attribute definition', 'attribute description', 'definition', 'description'):
                    description = v
        if label:
            break  # Found the attribute definition table; stop scanning

    pv_tables = []
    for table_html in re.findall(r'<table[^>]*>(.*?)</table>', html_text, re.IGNORECASE | re.DOTALL):
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.IGNORECASE | re.DOTALL)
        if not rows:
            continue
        header_cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', rows[0], re.IGNORECASE | re.DOTALL)
        header_texts = [_strip_tags(h).lower() for h in header_cells]
        if 'code' not in header_texts:
            continue
        code_idx = header_texts.index('code')
        class_idx = next((i for i, h in enumerate(header_texts)
                          if h in ('class', 'classe', 'catégorie', 'categorie')), None)
        desc_idx  = next((i for i, h in enumerate(header_texts)
                          if h in ('description', 'définition', 'definition')), None)
        pv_rows = []
        for row in rows[1:]:
            cells = [_strip_tags(c) for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)]
            if len(cells) > code_idx and cells[code_idx]:
                pv_rows.append({
                    'code': cells[code_idx],
                    'class_': cells[class_idx] if class_idx is not None and len(cells) > class_idx else "",
                    'description': cells[desc_idx] if desc_idx is not None and len(cells) > desc_idx else "",
                })
        if pv_rows:
            pv_tables.append(pv_rows)

    return label, title, description, pv_tables


# ---------------------------------------------------------------------------
# Zip cache helpers
# ---------------------------------------------------------------------------

def _load_zip_cache(zip_path):
    """Load all HTML pages from a sources zip.

    Returns a {url: html_text} dict, or None if the zip does not exist.
    Prints a warning and returns None if the zip is unreadable.
    """
    if not os.path.exists(zip_path):
        return None
    try:
        cache = {}
        with zipfile.ZipFile(zip_path, 'r') as zf:
            manifest = json.loads(zf.read('manifest.json').decode('utf-8'))
            for url, entry in manifest.items():
                cache[url] = zf.read(entry).decode('utf-8', errors='replace')
        return cache
    except Exception as e:
        print(f"  Warning: could not load {zip_path}: {e}", file=sys.stderr)
        return None


def _html_get(url, cache, indent="  "):
    """Return HTML for url from cache dict, falling back to a live fetch.

    When cache is not None but the URL is absent, a warning is printed and a
    live fetch is attempted so that processing is resilient to partial zips.
    """
    if cache is not None:
        if url in cache:
            return cache[url]
        print(f"{indent}Warning: {url} not in local archive — fetching live", file=sys.stderr)
    return fetch_html(url)


# ---------------------------------------------------------------------------
# Zip builder (shared by -a match functions and -f fetch functions)
# ---------------------------------------------------------------------------

def _crawl_and_save_nsdb_zip(key, base_url, content_type, locales, index_html=None):
    """Crawl all NSDB pages for a source and save them to sources/{key}.zip.

    Parameters
    ----------
    key : str
        Source key (used for the zip filename).
    base_url : str
        The source_ontology URL (the entry/index page).
    content_type : str
        One of 'NSDBSNT', 'NSDBSLT', 'NSDBSLC', 'NSDB'.
    locales : list[str]
        Project locales; French pages are fetched when 'fr' is in the list.
    index_html : str or None
        Already-fetched HTML for base_url (avoids a redundant request during
        -a, which has already downloaded the page).  When None the index is
        fetched.
    """
    zip_path = f"sources/{key}.zip"
    pages = {}   # {url: html_text}
    fetch_fr = "fr" in locales

    def _cache(url, html):
        pages[url] = html
        return html

    def _get(url, label=""):
        if url in pages:
            return pages[url]
        try:
            html = fetch_html(url)
            pages[url] = html
            return html
        except Exception as e:
            tag = f" ({label})" if label else ""
            print(f"  Warning: failed to fetch {url}{tag}: {e}", file=sys.stderr)
            return None

    # -- Index page ----------------------------------------------------------
    if index_html is not None:
        _cache(base_url, index_html)
    else:
        index_html = _get(base_url, "index")
    if index_html is None:
        print(f"  Error: could not fetch index page {base_url} — zip not saved.",
              file=sys.stderr)
        return

    if fetch_fr:
        _get(nsdb_fr_url(base_url), "FR index")

    # -- Discover and fetch sub-pages ----------------------------------------
    if content_type in ("NSDBSNT", "NSDBSLT"):
        attr_links = find_contents_table_links(index_html, base_url)
        print(f"  {key}: found {len(attr_links)} attribute links")
        for name, url in attr_links:
            _get(url, name)
            if fetch_fr:
                _get(nsdb_fr_url(url), f"FR {name}")

    elif content_type == "NSDBSLC":
        _slc_sections = ["SLC attribute tables", "Ecological Framework Tables"]
        comp_links = []
        for sect in _slc_sections:
            comp_links.extend(find_list_section_links(index_html, sect, base_url))
        print(f"  {key}: found {len(comp_links)} component links")
        for comp_name, comp_url in comp_links:
            comp_html = _get(comp_url, comp_name)
            if fetch_fr:
                _get(nsdb_fr_url(comp_url), f"FR {comp_name}")
            if comp_html:
                attr_links = find_contents_table_links(comp_html, comp_url)
                for attr_name, attr_url in attr_links:
                    _get(attr_url, attr_name)
                    if fetch_fr:
                        _get(nsdb_fr_url(attr_url), f"FR {attr_name}")

    elif content_type == "NSDB":
        table_links = find_links_by_text(
            index_html, ["Soil Name Table", "Soil Layer Table"], base_url)
        for table_name, table_url in table_links.items():
            table_html = _get(table_url, table_name)
            if fetch_fr:
                _get(nsdb_fr_url(table_url), f"FR {table_name}")
            if table_html:
                attr_links = find_contents_table_links(table_html, table_url)
                for attr_name, attr_url in attr_links:
                    _get(attr_url, attr_name)
                    if fetch_fr:
                        _get(nsdb_fr_url(attr_url), f"FR {attr_name}")

    # -- Write zip -----------------------------------------------------------
    manifest = {}
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, (url, html) in enumerate(pages.items()):
            entry = f"{i:04d}.html"
            zf.writestr(entry, html.encode('utf-8'))
            manifest[url] = entry
        zf.writestr('manifest.json', json.dumps(manifest, indent=2))

    ok = sum(1 for h in pages.values() if h)
    print(f"  Saved {ok}/{len(pages)} pages to {zip_path}")


# ---------------------------------------------------------------------------
# Public fetch functions (called from -f handler in term_harvester.py)
# ---------------------------------------------------------------------------

def fetch_nsdb_html_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Re-download all NSDB HTML pages for an NSDBSNT/SLT/SLC source.

    Builds sources/{key}.zip and updates download_date in the config.
    """
    base_url = (source.get("reachable_from") or {}).get("source_ontology", "")
    if not base_url:
        print(f"  Skipping {key}: no source_ontology URL.", file=sys.stderr)
        return
    content_type = source.get("content_type", "")
    if locales is None:
        try:
            with open(config_file) as f:
                locales = (yaml.safe_load(f) or {}).get("locales") or ["en"]
        except Exception:
            locales = ["en"]
    print(f"  Fetching all pages for '{key}' ({content_type}) ...")
    _crawl_and_save_nsdb_zip(key, base_url, content_type, locales)
    update_source_config(key, {"download_date": datetime.date.today().isoformat()}, config_file)


def fetch_nsdb_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Re-download all NSDB HTML pages for an NSDB-type source.

    Builds sources/{key}.zip and updates download_date in the config.
    Alias for fetch_nsdb_html_source — the crawl logic is content_type-aware.
    """
    fetch_nsdb_html_source(key, source, config_file, locales=locales)


# ---------------------------------------------------------------------------
# Processing functions (called from menu_manager.process_sources)
# ---------------------------------------------------------------------------

def _build_nsdb_enum(attr_html, enum_prefix, require_qualifying=True):
    """Parse an NSDB attribute page and return ``(enum_key, enum_dict)`` or ``(None, None)``.

    Parameters
    ----------
    attr_html : str
        Raw HTML of the attribute page.
    enum_prefix : str
        Prefix for the enum key, e.g. ``"NSDB"`` or ``"NSDBSLC"``.
    require_qualifying : bool
        When True (default) only return an enum when the pv_tables have more
        than 2 tables, or any table has more than 2 rows.  Set to False for
        single-attribute-page sources where every attribute should be included.

    Returns
    -------
    tuple
        ``(enum_key, enum_dict)`` on success, ``(None, None)`` otherwise.
    """
    label, title, attr_desc, pv_tables = parse_attribute_page(attr_html)
    if not label:
        return None, None
    if require_qualifying and not (
            len(pv_tables) > 2 or any(len(rows) > 2 for rows in pv_tables)):
        return None, None
    enum_key = f"{enum_prefix}_{label}"
    permissible_values = {}
    for rows in pv_tables:
        for row in rows:
            add_permissible_value(permissible_values, row['code'],
                                  title=row['class_'], description=row['description'])
    enum_dict = {
        "name":               label,
        "title":              title,
        "description":        attr_desc,
        "permissible_values": permissible_values,
    }
    return enum_key, enum_dict




def _write_nsdb_yaml(schema, yaml_path, base_url, key, source, fr_enums_pvs, fr_description):
    """Attach fr_locale extensions to *schema* and write YAML.

    If either *fr_enums_pvs* or *fr_description* is non-empty the function
    builds a ``extensions.locales.value.fr`` block and attaches it to *schema*
    before writing.
    """
    if fr_enums_pvs or fr_description:
        schema["extensions"] = _make_locale_extensions(
            nsdb_fr_url(base_url), key, source.get("version") or "", "fr",
            description=fr_description or None,
            enums={ek: {"permissible_values": pvs} for ek, pvs in fr_enums_pvs.items()} if fr_enums_pvs else None,
        )
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)


def _fetch_and_build_enums(attr_links, schema_enums, enum_prefix, indent="  ",
                            page_cache=None, fetch_fr=False):
    """Fetch each attribute URL, build its enum, populate *schema_enums* in-place.

    When *fetch_fr* is True, French translations are fetched immediately after
    each EN enum so the count can be reported on the same log line.

    Returns ``({enum_key: attr_url}, {enum_key: fr_pvs})``.
    """
    attr_url_by_enum = {}
    fr_enums_pvs = {}
    for attr_name, attr_url in attr_links:
        try:
            attr_html = _html_get(attr_url, page_cache, indent=indent)
        except Exception as e:
            print(f"{indent}Error fetching {attr_url}: {e}", file=sys.stderr)
            continue
        enum_key, enum_dict = _build_nsdb_enum(attr_html, enum_prefix)
        if enum_key is None:
            continue
        schema_enums[enum_key] = enum_dict
        attr_url_by_enum[enum_key] = attr_url
        en_count = len(enum_dict['permissible_values'])

        lang_counts = {}
        if fetch_fr:
            try:
                fr_attr_html = _html_get(nsdb_fr_url(attr_url), page_cache, indent=indent)
                _, _, _, fr_pv_tables = parse_attribute_page(fr_attr_html)
                en_codes = set(enum_dict['permissible_values'])
                fr_pvs = {}
                for rows in fr_pv_tables:
                    for row in rows:
                        if row['code'] in en_codes:
                            add_permissible_value(fr_pvs, row['code'],
                                                  title=row['class_'],
                                                  description=row['description'])
                if fr_pvs:
                    fr_enums_pvs[enum_key] = fr_pvs
                    lang_counts["fr"] = len(fr_pvs)
            except Exception as e:
                print(f"{indent}Warning: French attr fetch failed for {enum_key}: {e}",
                      file=sys.stderr)

        log_extraction(enum_key, count=en_count, lang_counts=lang_counts or None, indent=indent)
    return attr_url_by_enum, fr_enums_pvs


def process_nsdb_html_source(key, source, enum_prefix, locales=None):
    """Build a LinkML enum YAML for an NSDB HTML source.

    Reads all HTML from sources/{key}.zip (built by -a or -f).  Falls back to
    live fetches with a warning when no zip exists (legacy support).

    Supports three URL forms for source_ontology, detected by page content:

    * SLC index page — page contains "SLC attribute tables" or "Ecological
      Framework Tables" sections.  Two-level processing: for each component
      follows attribute links to individual attribute pages.
    * Component / index page — page has a Contents table of attribute links
      but no SLC-specific sections.  One-level processing.
    * Single attribute page — no Contents table.  Parses the page directly
      and writes a single enum.

    In all cases also fetches French equivalents via nsdb_fr_url() and writes
    extensions.locales.value.fr when French content is found.

    Parameters
    ----------
    enum_prefix : str
        Prefix for enum keys, e.g. ``"NSDB"`` for SNT/SLT sources or
        ``"NSDBSLC"`` for SLC sources.
    """
    yaml_path = f"sources/{key}.yaml"
    base_url  = (source.get("reachable_from") or {}).get("source_ontology", "")
    html_path = f"sources/{key}.html"
    zip_path  = f"sources/{key}.zip"

    # Load page cache from zip; fall back to legacy html + live fetches with a warning.
    page_cache = _load_zip_cache(zip_path)
    if page_cache is None:
        if not os.path.exists(html_path):
            print(f"  Skipping {key}: no archive or HTML file found"
                  f" — run '-f {key}' to download first.", file=sys.stderr)
            return
        print(f"  Warning: {zip_path} not found — using {html_path} with live fetches."
              f" Run '-f {key}' to build a local archive.", file=sys.stderr)

    if os.path.exists(yaml_path):
        with open(yaml_path) as f:
            schema = yaml.safe_load(f) or {}
    else:
        schema = make_config_schema(id=base_url, name=key, title=source.get("title", ""),
                             description=source.get("description", ""),
                             version=source.get("version", ""))
    schema["enums"] = {}
    schema.pop("extensions", None)

    # Get the index page HTML
    if page_cache is not None and base_url in page_cache:
        page_html = page_cache[base_url]
    elif os.path.exists(html_path):
        with open(html_path, encoding="utf-8", errors="replace") as f:
            page_html = f.read()
    else:
        print(f"  Error: index HTML not available for {key}.", file=sys.stderr)
        return

    # Default description from first <p>
    p_m = re.search(r'<p[^>]*>(.*?)</p>', page_html, re.IGNORECASE | re.DOTALL)
    schema["description"] = _strip_tags(p_m.group(1)).strip() if p_m else source.get("description", "")

    fr_enums_pvs = {}
    fr_description = ""

    # ---- Detect page type by content ----------------------------------------
    # SLC index pages contain named sections; all other NSDB pages do not.
    _slc_sections = ["SLC attribute tables", "Ecological Framework Tables"]
    component_links = []
    for _sect in _slc_sections:
        _links = find_list_section_links(page_html, _sect, base_url)
        if _links:
            print(f"  Section '{_sect}': {len(_links)} links")
        component_links.extend(_links)

    if component_links:
        # ---- SLC index: two-level component → attribute processing ----------
        for comp_name, comp_url in component_links:
            print(f"  Processing component '{comp_name}' ...")
            try:
                comp_html = _html_get(comp_url, page_cache)
            except Exception as e:
                print(f"  Error fetching {comp_url}: {e}", file=sys.stderr)
                continue

            comp_desc = find_section_paragraph(comp_html, "Description")
            if comp_desc:
                existing = schema.get("description", "")
                schema["description"] = (existing + "\n" + comp_desc).strip() if existing else comp_desc

            attr_links = find_contents_table_links(comp_html, comp_url)
            print(f"    Found {len(attr_links)} attribute links")

            if "fr" in (locales or ["en"]):
                fr_comp_url = nsdb_fr_url(comp_url)
                try:
                    fr_comp_html = _html_get(fr_comp_url, page_cache, indent="    ")
                    fr_desc = find_section_paragraph(fr_comp_html, "Description")
                    if fr_desc:
                        fr_description = (fr_description + "\n" + fr_desc).strip() if fr_description else fr_desc
                except Exception as e:
                    print(f"    Warning: French component page failed: {e}", file=sys.stderr)

            _, new_fr_pvs = _fetch_and_build_enums(
                attr_links, schema["enums"], enum_prefix, indent="    ", page_cache=page_cache,
                fetch_fr="fr" in (locales or ["en"]))
            fr_enums_pvs.update(new_fr_pvs)

    else:
        # ---- Component/index page or direct attribute page ------------------
        attr_links = find_contents_table_links(page_html, base_url)

        if attr_links:
            # ---- Has Contents table: process attribute links ----------------
            print(f"  Found {len(attr_links)} attribute links")

            # Try dcterms.title (SNT/SLT index pages carry this)
            _meta_m = re.search(
                r'<meta\s[^>]*name=["\']dcterms\.title["\'][^>]*content=["\']([^"\']+)["\']',
                page_html, re.IGNORECASE)
            if not _meta_m:
                _meta_m = re.search(
                    r'<meta\s[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']dcterms\.title["\']',
                    page_html, re.IGNORECASE)
            if _meta_m:
                schema["title"] = _meta_m.group(1).strip()

            desc = find_section_paragraph(page_html, "Description")
            if desc:
                schema["description"] = desc

            if "fr" in (locales or ["en"]):
                fr_page_url = nsdb_fr_url(base_url)
                try:
                    fr_page_html = _html_get(fr_page_url, page_cache)
                    fr_description = find_section_paragraph(fr_page_html, "Description") or ""
                except Exception as e:
                    print(f"  Warning: French page failed: {e}", file=sys.stderr)

            _, fr_enums_pvs = _fetch_and_build_enums(
                attr_links, schema["enums"], enum_prefix, indent="  ", page_cache=page_cache,
                fetch_fr="fr" in (locales or ["en"]))

        else:
            # ---- Direct attribute page: one enum ----------------------------
            enum_key, enum_dict = _build_nsdb_enum(page_html, enum_prefix, require_qualifying=False)
            if enum_key is None:
                print(f"  Warning: could not parse attribute label from {html_path}", file=sys.stderr)
                return
            schema["enums"][enum_key] = enum_dict
            schema["description"] = source.get("description") or enum_dict["description"]

            lang_counts = {}
            if "fr" in (locales or ["en"]):
                try:
                    fr_html = _html_get(nsdb_fr_url(base_url), page_cache)
                    _, _, fr_description, fr_pv_tables = parse_attribute_page(fr_html)
                    en_codes = set(enum_dict["permissible_values"])
                    fr_pvs = {}
                    for rows in fr_pv_tables:
                        for row in rows:
                            if row['code'] in en_codes:
                                add_permissible_value(fr_pvs, row['code'],
                                                      title=row['class_'],
                                                      description=row['description'])
                    if fr_pvs:
                        fr_enums_pvs[enum_key] = fr_pvs
                        lang_counts["fr"] = len(fr_pvs)
                except Exception as e:
                    print(f"  Warning: French attr page failed: {e}", file=sys.stderr)
            log_extraction(enum_key, count=len(enum_dict['permissible_values']),
                           lang_counts=lang_counts or None)

    _write_nsdb_yaml(schema, yaml_path, base_url, key, source, fr_enums_pvs, fr_description)


def process_nsdb_source(key, source, locales=None):
    """Build a combined LinkML enum YAML by merging SNT and SLT enums.

    Reads all HTML from sources/{key}.zip (built by -a or -f).  Falls back to
    live fetches with a warning when no zip exists (legacy support).

    Fetches the NSDB index page (source_ontology), finds the "Soil Name Table"
    and "Soil Layer Table" component links, and for each processes all
    attribute pages using the shared NSDB helpers.
    """
    yaml_path = f"sources/{key}.yaml"
    base_url = (source.get("reachable_from") or {}).get("source_ontology", "")
    zip_path  = f"sources/{key}.zip"

    page_cache = _load_zip_cache(zip_path)
    if page_cache is None:
        print(f"  Warning: {zip_path} not found — fetching live."
              f" Run '-f {key}' to build a local archive.", file=sys.stderr)

    if os.path.exists(yaml_path):
        with open(yaml_path) as f:
            schema = yaml.safe_load(f) or {}
    else:
        schema = make_config_schema(id=base_url, name=key, title=source.get("title", ""),
                             description=source.get("description", ""),
                             version=source.get("version", ""))
    schema["description"] = source.get("description", "")
    schema["enums"] = {}
    schema.pop("extensions", None)

    try:
        index_html = _html_get(base_url, page_cache)
    except Exception as e:
        print(f"  Error fetching NSDB index {base_url}: {e}", file=sys.stderr)
        return

    table_links = find_links_by_text(index_html, ["Soil Name Table", "Soil Layer Table"], base_url)
    if not table_links:
        print(f"  Warning: 'Soil Name Table' / 'Soil Layer Table' links not found", file=sys.stderr)

    fr_enums_pvs = {}
    fr_description = ""

    for table_name, table_url in table_links.items():
        print(f"  Processing '{table_name}' ...")
        try:
            table_html = _html_get(table_url, page_cache)
        except Exception as e:
            print(f"  Error fetching {table_url}: {e}", file=sys.stderr)
            continue

        desc = find_section_paragraph(table_html, "Description")
        if desc:
            existing = schema.get("description", "")
            schema["description"] = (existing + "\n" + desc).strip() if existing else desc

        attr_links = find_contents_table_links(table_html, table_url)
        print(f"    Found {len(attr_links)} attribute links")

        if "fr" in (locales or ["en"]):
            fr_table_url = nsdb_fr_url(table_url)
            try:
                fr_table_html = _html_get(fr_table_url, page_cache, indent="    ")
                fr_desc = find_section_paragraph(fr_table_html, "Description")
                if fr_desc:
                    fr_description = (fr_description + "\n" + fr_desc).strip() if fr_description else fr_desc
            except Exception as e:
                print(f"    Warning: French table page failed: {e}", file=sys.stderr)

        _, new_fr_pvs = _fetch_and_build_enums(
            attr_links, schema["enums"], "NSDB", indent="    ", page_cache=page_cache,
            fetch_fr="fr" in (locales or ["en"]))
        fr_enums_pvs.update(new_fr_pvs)

    _write_nsdb_yaml(schema, yaml_path, base_url, key, source, fr_enums_pvs, fr_description)


def _nsdb_meta_title(html_text, fallback):
    """Extract dcterms.title from HTML meta tag, or return fallback."""
    m = re.search(
        r'<meta\s[^>]*name=["\']dcterms\.title["\'][^>]*content=["\']([^"\']+)["\']',
        html_text, re.IGNORECASE)
    if not m:
        m = re.search(
            r'<meta\s[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']dcterms\.title["\']',
            html_text, re.IGNORECASE)
    return m.group(1).strip() if m else fallback


def match_nsdb_snt(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is an NSDB SNT page and was handled."""
    if not (url.startswith("https://sis.agr.gc.ca/cansis/nsdb/") and "/snt/" in url):
        return False

    url_no_query = url.split("?")[0]
    ver_m = re.search(r'/nsdb/[^/]+/([^/]+)/snt/', url_no_query)
    version = re.sub(r'^[vV]', '', ver_m.group(1)) if ver_m else ""

    with open(tmp_path, encoding="utf-8", errors="replace") as f:
        html_text = f.read()

    is_index = url_no_query.rstrip("/").endswith("/index.html")
    if is_index:
        title = _nsdb_meta_title(html_text, "NSDB Soil Name Table")
        desc  = find_section_paragraph(html_text, "Description")
        ver_key = version.replace(".", "_") if version else ""
        key = f"NSDBSNTv{ver_key}" if ver_key else "NSDBSNT"
    else:
        label, title, desc, _ = parse_attribute_page(html_text)
        if not label:
            print(f"  Warning: could not parse attribute label from {url} — skipping",
                  file=sys.stderr)
            os.unlink(tmp_path)
            return True
        key = f"NSDBSNT_{label}"

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    if key in config.get("sources", {}):
        print(f"  Skipping {url}: source key '{key}' already exists in {config_file}",
              file=sys.stderr)
        os.unlink(tmp_path)
        return True

    output_path = f"sources/{key}.html"
    os.rename(tmp_path, output_path)
    print(f"Saved to {output_path}")

    entry = make_source_entry(key, url, "NSDBSNT", "html",
                              title=title, version=version, description=desc)
    entry["see_also"] = _NSDB_SEE_ALSO
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"Added source '{key}' to {config_file}")

    locales = config.get("locales", ["en"])
    if is_index:
        yaml_path = f"sources/{key}.yaml"
        schema = make_config_schema(id=url, name=key, title=title,
                                    description=desc, version=version)
        with open(yaml_path, "w") as f:
            yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
        print(f"Created {yaml_path}")
        _crawl_and_save_nsdb_zip(key, url, "NSDBSNT", locales, index_html=html_text)
    else:
        process_nsdb_html_source(key, entry, enum_prefix="NSDB", locales=locales)
    return True


def match_nsdb_slt(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is an NSDB SLT page and was handled."""
    if not (url.startswith("https://sis.agr.gc.ca/cansis/nsdb/") and "/slt/" in url):
        return False

    url_no_query = url.split("?")[0]
    ver_m = re.search(r'/nsdb/[^/]+/([^/]+)/slt/', url_no_query)
    version = re.sub(r'^[vV]', '', ver_m.group(1)) if ver_m else ""

    with open(tmp_path, encoding="utf-8", errors="replace") as f:
        html_text = f.read()

    is_index = url_no_query.rstrip("/").endswith("/index.html")
    if is_index:
        title = _nsdb_meta_title(html_text, "NSDB Soil Layer Table")
        desc  = find_section_paragraph(html_text, "Description")
        ver_key = version.replace(".", "_") if version else ""
        key = f"NSDBSLTv{ver_key}" if ver_key else "NSDBSLT"
    else:
        label, title, desc, _ = parse_attribute_page(html_text)
        if not label:
            print(f"  Warning: could not parse attribute label from {url} — skipping",
                  file=sys.stderr)
            os.unlink(tmp_path)
            return True
        key = f"NSDBSLT_{label}"

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    if key in config.get("sources", {}):
        print(f"  Skipping {url}: source key '{key}' already exists in {config_file}",
              file=sys.stderr)
        os.unlink(tmp_path)
        return True

    output_path = f"sources/{key}.html"
    os.rename(tmp_path, output_path)
    print(f"Saved to {output_path}")

    entry = make_source_entry(key, url, "NSDBSLT", "html",
                              title=title, version=version, description=desc)
    entry["see_also"] = _NSDB_SEE_ALSO
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"Added source '{key}' to {config_file}")

    locales = config.get("locales", ["en"])
    if is_index:
        yaml_path = f"sources/{key}.yaml"
        schema = make_config_schema(id=url, name=key, title=title,
                                    description=desc, version=version)
        with open(yaml_path, "w") as f:
            yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
        print(f"Created {yaml_path}")
        _crawl_and_save_nsdb_zip(key, url, "NSDBSLT", locales, index_html=html_text)
    else:
        process_nsdb_html_source(key, entry, enum_prefix="NSDB", locales=locales)
    return True


def match_nsdb_soil(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is an NSDB Soil Name-and-Layer index page and was handled."""
    if not url.startswith("https://sis.agr.gc.ca/cansis/nsdb/soil"):
        return False

    url_no_query = url.split("?")[0].rstrip("/")
    parts = url_no_query.split("/")
    if "index.html" in parts:
        version_label = parts[parts.index("index.html") - 1]
        base_url = url_no_query[: url_no_query.rfind("/index.html") + 1]
    else:
        version_label = parts[-1]
        base_url = url_no_query + "/"
    version_num = re.sub(r"^[vV]", "", version_label)
    key = f"NSDBSoilNameAndLayerV{version_num}"

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    if key in config.get("sources", {}):
        print(f"  Skipping {url}: source key '{key}' already exists in {config_file}",
              file=sys.stderr)
        os.unlink(tmp_path)
        return True

    output_path = f"sources/{key}.html"
    os.rename(tmp_path, output_path)
    print(f"Saved to {output_path}")

    entry = make_source_entry(
        key, url, "NSDB", "html",
        title="NSDB Soil Name and Layer Tables", version=version_label,
        description="This schema contains summary information for named soils within the Canadian soil surveys NSDB database")
    entry["see_also"] = _NSDB_SEE_ALSO
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"Added source '{key}' to {config_file}")

    yaml_path = f"sources/{key}.yaml"
    schema = make_config_schema(id=base_url, name=key, title=entry["title"],
                                description=entry["description"], license="CC0",
                                default_prefix="menu")
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    print(f"Created {yaml_path}")

    with open(output_path, encoding="utf-8", errors="replace") as f:
        index_html = f.read()
    locales = config.get("locales", ["en"])
    _crawl_and_save_nsdb_zip(key, url, "NSDB", locales, index_html=index_html)
    return True


def match_nsdb_slc(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is an NSDB Soil Landscapes of Canada page and was handled."""
    if not ("sis.agr.gc.ca" in url and "/nsdb/slc/" in url):
        return False

    with open(tmp_path, encoding="utf-8", errors="replace") as f:
        html_text = f.read()

    slc_title = _nsdb_meta_title(html_text, "NSDB Soil Landscapes of Canada")
    ver_m = re.search(r'[Vv]ersion\s+([\d.]+)', slc_title)
    slc_version = ver_m.group(1) if ver_m else ""
    ver_key = slc_version.replace(".", "_") if slc_version else ""
    key = f"NSDBSLCv{ver_key}" if ver_key else "NSDBSLC"

    p_m = re.search(r'<p[^>]*>(.*?)</p>', html_text, re.IGNORECASE | re.DOTALL)
    slc_desc = strip_tags(p_m.group(1)).strip() if p_m else ""

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    if key in config.get("sources", {}):
        print(f"  Skipping {url}: source key '{key}' already exists in {config_file}",
              file=sys.stderr)
        os.unlink(tmp_path)
        return True

    output_path = f"sources/{key}.html"
    os.rename(tmp_path, output_path)
    print(f"Saved to {output_path}")

    entry = make_source_entry(key, url, "NSDBSLC", "html",
                              title=slc_title, version=slc_version, description=slc_desc)
    entry["see_also"] = _NSDB_SEE_ALSO
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"Added source '{key}' to {config_file}")

    yaml_path = f"sources/{key}.yaml"
    schema = make_config_schema(id=url, name=key, title=slc_title,
                                description=slc_desc, version=slc_version)
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    print(f"Created {yaml_path}")

    locales = config.get("locales", ["en"])
    _crawl_and_save_nsdb_zip(key, url, "NSDBSLC", locales, index_html=html_text)
    return True
