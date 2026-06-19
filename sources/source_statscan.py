"""Statistics Canada (StatsCan) source helpers for term_harvester.py.

Provides HTML parsing utilities and processing functions for StatsCan IMDB
classification pages used by process_sources() and the add_source() detection
block.

Download/process split
----------------------
*  -a  URL  (match_statscan)         — downloads the main TVD page, adds the
   config entry, then calls _crawl_and_save_statscan_zip to fetch every linked
   page (CPV, structure, definitions, EN + FR) into sources/{key}.zip.
*  -f  key  (fetch_statscan_source)  — same crawl, rebuilds sources/{key}.zip
   and updates download_date in the config.
*  -c  key  (process_statscan_source) — reads sources/{key}.zip only; no
   network access.  Falls back to sources/{key}.html + live fetches with a
   warning for sources added before the zip format was introduced.

Zip format
----------
sources/{key}.zip contains:
  manifest.json   — {url: entry_filename} mapping
  0000.html …     — raw HTML for each URL, UTF-8 encoded

Public API used by term_harvester.py:
    statscan_fr_url(url)
    parse_statscan_definitions(html_text)
    parse_statscan_structure(html_text)
    fetch_statscan_source(key, source, config_file, locales)
    process_statscan_source(key, source, config_file=None, locales=None)
    match_statscan(url, tmp_path, config_file)

Classification catalog:
    parse_classification_catalog(html_text) → list[dict]
    fetch_classification_catalog(cache_path, force_refresh) → list[dict]
    ENUM_DEFINITIONS  — list of available classifications; loaded from the
                        local cache at import time (empty until first fetch).

Each ENUM_DEFINITIONS entry:
    tvd_id  — StatsCan TVD identifier
    key     — harvester source key (STATSCAN_{tvd_id})
    title   — classification name
    subject — subject area (closest available description from the search page)
    url     — dedicated StatsCan IMDB page URL

To populate the catalog and cache it locally run (once per project directory):
    from source_statscan import fetch_classification_catalog
    fetch_classification_catalog()

To add any entry directly to a project:
    python term_harvester.py -a {url}
"""

import datetime
import html
import json
import os
import re
import sys
import yaml
import zipfile
from source_utils import (
    strip_tags as _strip_tags,
    strip_tags,
    fetch_html,
    add_permissible_value,
    log_extraction,
    make_config_schema,
    _make_locale_extensions,
    IndentedDumper,
    make_source_entry,
    normalize_text,
    update_source_config,
    write_config,
    MENU_CONFIG,
)


# ---------------------------------------------------------------------------
# Classification catalog
# ---------------------------------------------------------------------------

CLASSIFICATIONS_SEARCH_URL = (
    "https://www.statcan.gc.ca/en/concepts/search?show=all"
)
CLASSIFICATIONS_SEARCH_URL_FR = (
    "https://www.statcan.gc.ca/fr/concepts/recherche?show=all"
)

_DEFAULT_CATALOG_PATH = "sources/sources_statscan_terms.yaml"


def parse_classification_catalog(html_text):
    """Parse the StatsCan concepts search page (?show=all).

    Expects a table with three columns per row: title link, subject, type.
    All entry types are included (Classification, Variable, Statistical unit).

    URL patterns handled:
        p3VD.pl?Function=getVD&TVD=NNN  — Classifications
        p3Var.pl?Function=DEC&Id=NNN    — Variables
        p3Var.pl?Function=Unit&Id=NNN   — Statistical units

    Returns a list of dicts sorted alphabetically by title:
        tvd_id     – numeric identifier (TVD for Classifications, Id for others)
        key        – harvester source key  (STATSCAN_{tvd_id})
        title      – entry name
        subject    – subject area (e.g. "Education", "Housing")
        entry_type – "Classification", "Variable", or "Statistical unit"
        url        – dedicated StatsCan IMDB page URL
    """
    entries = []
    for tr_m in re.finditer(r'<tr\b[^>]*>(.*?)</tr>', html_text, re.IGNORECASE | re.DOTALL):
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', tr_m.group(1), re.IGNORECASE | re.DOTALL)
        if len(cells) < 3:
            continue

        # Classification: p3VD.pl?Function=getVD&TVD=NNN
        tvd_m = re.search(
            r'href=["\']([^"\']*p3VD\.pl[^"\']*[?&](?:amp;)?TVD=(\d+)[^"\']*)["\']',
            cells[0], re.IGNORECASE,
        )
        # Variable / Statistical unit: p3Var.pl?Function=DEC|Unit&Id=NNN
        var_m = re.search(
            r'href=["\']([^"\']*p3Var\.pl[^"\']*[?&](?:amp;)?Id=(\d+)[^"\']*)["\']',
            cells[0], re.IGNORECASE,
        )
        link_m = tvd_m or var_m
        if not link_m:
            continue

        url        = html.unescape(link_m.group(1))
        numeric_id = link_m.group(2)
        title      = html.unescape(_strip_tags(cells[0])).strip()
        subject    = html.unescape(_strip_tags(cells[1])).strip()
        entry_type = html.unescape(_strip_tags(cells[2])).strip()
        if not title:
            continue

        entries.append({
            "tvd_id":     numeric_id,
            "key":        f"STATSCAN_{numeric_id}",
            "title":      title,
            "subject":    subject,
            "entry_type": entry_type,
            "url":        url,
        })

    return sorted(entries, key=lambda e: e["title"].lower())


def fetch_classification_catalog(
    cache_path=_DEFAULT_CATALOG_PATH,
    force_refresh=False,
):
    """Return the list of Statistics Canada classification enumerations.

    First call (or when force_refresh=True): fetches CLASSIFICATIONS_SEARCH_URL,
    parses the results table, and writes the result to cache_path as YAML.
    Subsequent calls load from the cache without hitting the network.

    Args:
        cache_path:     Path for the local YAML cache.  Pass None to skip caching.
        force_refresh:  Re-fetch from the web even if the cache exists.

    Returns:
        list[dict] — each entry has keys tvd_id, key, title, subject, url.
    """
    if not force_refresh and cache_path and os.path.exists(cache_path):
        with open(cache_path) as f:
            data = yaml.safe_load(f) or {}
        cached = data.get("classifications", [])
        if cached:
            return cached

    print(f"  Fetching StatsCan classification catalog from {CLASSIFICATIONS_SEARCH_URL} ...")
    html_text = fetch_html(CLASSIFICATIONS_SEARCH_URL)
    entries = parse_classification_catalog(html_text)

    if cache_path and entries:
        with open(cache_path, "w") as f:
            yaml.dump(
                {"classifications": entries},
                f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False,
            )
        print(f"  Cached {len(entries)} classifications to {cache_path}")

    return entries


def _load_enum_definitions():
    """Load ENUM_DEFINITIONS from the local cache file at import time, if present."""
    if os.path.exists(_DEFAULT_CATALOG_PATH):
        try:
            with open(_DEFAULT_CATALOG_PATH) as f:
                data = yaml.safe_load(f) or {}
            return data.get("classifications", [])
        except Exception:
            pass
    return []


# Available Statistics Canada classification enumerations.
# Empty until fetch_classification_catalog() has been called at least once.
# After the first fetch the cache at sources/sources_statscan_terms.yaml is
# loaded automatically on every subsequent import.
ENUM_DEFINITIONS = _load_enum_definitions()


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------

def statscan_fr_url(url):
    """Return the French-language equivalent of a StatsCan IMDB page URL.

    StatsCan exposes French pages by inserting ``_f`` into the CGI script name,
    e.g. ``p3VD.pl`` → ``p3VD_f.pl``.  Works for all STATSCAN sub-pages
    (getVD, getVDStruct, display-definitions, CPV detail, etc.).
    """
    return re.sub(r'(?<=/)([\w]+)(\.pl)(?=\?)', r'\1_f\2', url)


def parse_statscan_definitions(html_text):
    """Parse a StatsCan 'Display definitions' page.

    Returns {code: definition_text} where each code is the alphanumeric identifier
    (e.g. '111') extracted from <h2 class="bg-def-1"> headings of the form
    'CODE - Name', and the definition is the text of the first <p> that follows.
    """
    definitions = {}
    # Work within the panel-body section
    panel_m = re.search(
        r'<div\b[^>]*\bpanel-body\b[^>]*>(.*?)(?=<div\b[^>]*\bpanel\b|$)',
        html_text, re.IGNORECASE | re.DOTALL)
    body = panel_m.group(1) if panel_m else html_text

    for h2_m in re.finditer(
            r'<h2\b[^>]*\bbg-def-1\b[^>]*>(.*?)</h2>(.*?)(?=<h2\b[^>]*\bbg-def-1\b|$)',
            body, re.IGNORECASE | re.DOTALL):
        heading_text = html.unescape(_strip_tags(h2_m.group(1))).strip()
        # Heading format: "CODE - Name" or "CODE Name"
        code_m = re.match(r'^(\S+)', heading_text)
        if not code_m:
            continue
        code = code_m.group(1)
        after = h2_m.group(2)
        p_m = re.search(r'<p\b[^>]*>(.*?)</p>', after, re.IGNORECASE | re.DOTALL)
        if p_m:
            definition = html.unescape(_strip_tags(p_m.group(1))).strip()
            if definition:
                definitions[code] = definition
    return definitions


def parse_statscan_structure(html_text):
    """Parse a StatsCan 'Display structure' page.

    Returns an ordered list of (code, name, indent_level) tuples extracted from
    <li class="list-group-item indent-N"> items inside the panel-body <ul>.
    indent_level is 1-based (1 = top of this subtree).
    """
    items = []
    panel_m = re.search(
        r'<div\b[^>]*\bpanel-body\b[^>]*>(.*?)(?=<div\b[^>]*class=["\'][^"\']*(?:panel|footer)|$)',
        html_text, re.IGNORECASE | re.DOTALL)
    body = panel_m.group(1) if panel_m else html_text

    ul_m = re.search(r'<ul\b[^>]*\blist-group\b[^>]*>(.*?)(?=</ul>|$)', body, re.IGNORECASE | re.DOTALL)
    if not ul_m:
        return items
    ul_html = ul_m.group(1)

    # Some <li> items lack a closing </li>; capture up to the next <li> or end
    for li_m in re.finditer(
            r'<li\b[^>]*\bindent-(\d+)[^>]*>(.*?)(?=<li\b|$)',
            ul_html, re.IGNORECASE | re.DOTALL):
        indent = int(li_m.group(1))
        content = li_m.group(2)
        # Prefer <a> link text, fall back to plain text content
        a_m = re.search(r'<a\b[^>]*>(.*?)</a>', content, re.IGNORECASE | re.DOTALL)
        raw_text = html.unescape(_strip_tags(a_m.group(1) if a_m else content)).strip()
        # Format is "CODE - Name" or "CODE Name"
        sep_m = re.match(r'^(\S+)\s*[-\u2013]\s*(.*)', raw_text)
        if sep_m:
            code, name = sep_m.group(1), sep_m.group(2).strip()
        else:
            parts = raw_text.split(None, 1)
            code, name = parts[0], (parts[1] if len(parts) > 1 else "")
        if code:
            items.append((code, name, indent))
    return items


# ---------------------------------------------------------------------------
# Processing function (called from menu_manager.process_sources)
# ---------------------------------------------------------------------------

def process_statscan_source(key, source, config_file=None, locales=None):
    """Build a LinkML enum YAML for a STATSCAN source.

    Reads sources/{key}.zip (built by -a or -f [key]).  For each top-level
    classification code linked from the main page it:
      1. Reads the code's detail page.
      2. Reads the 'Display structure' page → builds the full code hierarchy.
      3. Reads the 'Display definitions' page → collects code definitions.

    Falls back to sources/{key}.html + live fetches with a warning for sources
    added before the zip format was introduced.

    For codes that appear in the source page table without a hyperlink (i.e.
    leaf codes with no child entries), a permissible_value is created directly
    from the table row: the Code column provides the code and the Category (or
    Group/Class) column provides the title.  Definitions for these codes are
    taken from the source-level 'Display definitions' page.

    Writes sources/{key}.yaml with a single enum whose permissible_values carry
    name, title, optional description, and is_a.
    """
    source_url = (source.get("reachable_from") or {}).get("source_ontology", "")

    # Prefer zip; fall back to individual HTML files for backward compat
    zip_path = f"sources/{key}.zip"
    page_cache = _load_statscan_zip(zip_path)

    if page_cache is not None:
        print(f"  Reading {len(page_cache)} pages from {zip_path}")
        source_html = page_cache.get(source_url, "")
        if not source_html:
            print(f"  Warning: main page not in {zip_path} — fetching live", file=sys.stderr)
            source_html = fetch_html(source_url) if source_url else ""
    else:
        html_path = f"sources/{key}.html"
        if os.path.exists(html_path):
            print(f"  Warning: {zip_path} not found — using {html_path} with live fetches."
                  f" Run '-f {key}' to build the offline archive.", file=sys.stderr)
            with open(html_path) as f:
                source_html = f.read()
        else:
            print(f"  Error: neither {zip_path} nor {html_path} found."
                  f" Run '-f {key}' to download.", file=sys.stderr)
            return

    # ---- 1. Source-level Display definitions (top-level codes) ----------
    definitions = {}
    src_def_m = re.search(
        r'href=["\']([^"\']*Function=getVD[^"\']*&amp;D=1[^"\']*)["\']',
        source_html, re.IGNORECASE)
    if src_def_m:
        src_def_url = html.unescape(src_def_m.group(1))
        try:
            if page_cache is None:
                print(f"  Fetching source-level definitions {src_def_url} ...")
            definitions.update(parse_statscan_definitions(_html_get(src_def_url, page_cache)))
        except Exception as e:
            print(f"  Warning: could not fetch source definitions: {e}", file=sys.stderr)

    # ---- 2. Find each top-level CPV link on the source page --------------
    seen_urls = set()
    cpv_urls = []
    for m in re.finditer(
            r'href=["\']([^"\']*Function=getVD[^"\']*&amp;CPV=[^"\']+)["\']',
            source_html, re.IGNORECASE):
        u = html.unescape(m.group(1))
        if u not in seen_urls:
            seen_urls.add(u)
            cpv_urls.append(u)

    # ---- 2b. Collect unlinked (leaf) codes directly from source table ---
    permissible_values = {}
    table_m = re.search(r'<table\b[^>]*>(.*?)</table>', source_html, re.IGNORECASE | re.DOTALL)
    if table_m:
        cat_td_index = 0
        header_m = (
            re.search(r'<thead\b[^>]*>(.*?)</thead>', table_m.group(1), re.IGNORECASE | re.DOTALL)
            or re.search(r'<tr\b[^>]*>(.*?)</tr>', table_m.group(1), re.IGNORECASE | re.DOTALL)
        )
        if header_m:
            header_cells = re.findall(
                r'<t[hd]\b[^>]*>(.*?)</t[hd]>', header_m.group(1), re.IGNORECASE | re.DOTALL)
            for i, cell in enumerate(header_cells[1:], start=1):
                cell_text = html.unescape(strip_tags(cell)).strip().lower()
                if any(w in cell_text for w in ('category', 'group', 'class', 'name')):
                    cat_td_index = i - 1
                    break
        data_rows = re.findall(r'<tr\b[^>]*>(.*?)</tr>', table_m.group(1), re.IGNORECASE | re.DOTALL)
        for row_html in data_rows[1:]:
            th_m = re.search(r'<th\b[^>]*>(.*?)</th>', row_html, re.IGNORECASE | re.DOTALL)
            if not th_m:
                continue
            th_content = th_m.group(1)
            if re.search(r'<a\b', th_content, re.IGNORECASE):
                continue
            code = html.unescape(strip_tags(th_content)).strip()
            if not code:
                continue
            td_cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row_html, re.IGNORECASE | re.DOTALL)
            title = ""
            if td_cells:
                idx = cat_td_index if cat_td_index < len(td_cells) else 0
                title = html.unescape(strip_tags(td_cells[idx])).strip()
            add_permissible_value(permissible_values, code, title=title)

    # ---- 3. For each CPV page: read structure + definitions -------------
    for cpv_url in cpv_urls:
        if page_cache is None:
            print(f"  Fetching CPV page {cpv_url} ...")
        try:
            cpv_html = _html_get(cpv_url, page_cache)
        except Exception as e:
            print(f"  Warning: could not fetch {cpv_url}: {e}", file=sys.stderr)
            continue

        struct_m = re.search(
            r'href=["\']([^"\']*Function=getVDStruct[^"\']*)["\']',
            cpv_html, re.IGNORECASE)
        if struct_m:
            struct_url = html.unescape(struct_m.group(1))
            try:
                if page_cache is None:
                    print(f"    Fetching structure {struct_url} ...")
                struct_items = parse_statscan_structure(_html_get(struct_url, page_cache))
                processed = []
                for code, title, indent in struct_items:
                    is_a = None
                    if indent > 1:
                        for prev_code, prev_indent in reversed(processed):
                            if prev_indent == indent - 1:
                                is_a = prev_code
                                break
                    processed.append((code, indent))
                    add_permissible_value(permissible_values, code, title=title, is_a=is_a)
            except Exception as e:
                print(f"    Warning: structure fetch failed: {e}", file=sys.stderr)
        else:
            # Leaf CPV with no structure sub-page: extract code+title from the CPV page.
            # The panel-body contains <h2 class="bg-def-1">CODE - Name</h2> followed by
            # a <p>description</p> for leaf entries.
            cpv_code_m = re.search(r'[?&]CPV=([^&"\'<>\s]+)', cpv_url)
            if cpv_code_m:
                leaf_code = html.unescape(cpv_code_m.group(1))
                if leaf_code not in permissible_values:
                    leaf_title = ""
                    pb_m = re.search(r'<div\b[^>]*\bpanel-body\b[^>]*>(.*?)</div>',
                                     cpv_html, re.IGNORECASE | re.DOTALL)
                    if pb_m:
                        h2_m = re.search(r'<h2\b[^>]*\bbg-def-1\b[^>]*>(.*?)</h2>',
                                         pb_m.group(1), re.IGNORECASE | re.DOTALL)
                        if h2_m:
                            heading = html.unescape(strip_tags(h2_m.group(1))).strip()
                            sep_m2 = re.match(r'^\S+\s*[-\u2013]\s*(.*)', heading)
                            leaf_title = normalize_text(
                                sep_m2.group(1).strip() if sep_m2 else heading)
                        p_m = re.search(r'<p\b[^>]*>(.*?)</p>',
                                        pb_m.group(1), re.IGNORECASE | re.DOTALL)
                        if p_m:
                            leaf_def = normalize_text(
                                html.unescape(strip_tags(p_m.group(1))).strip())
                            if leaf_def:
                                definitions[leaf_code] = leaf_def
                    add_permissible_value(permissible_values, leaf_code, title=leaf_title)

        def_m = re.search(
            r'href=["\']([^"\']*Function=getVD[^"\']*&amp;D=1[^"\']*)["\']',
            cpv_html, re.IGNORECASE)
        if def_m:
            def_url = html.unescape(def_m.group(1))
            try:
                if page_cache is None:
                    print(f"    Fetching definitions {def_url} ...")
                definitions.update(parse_statscan_definitions(_html_get(def_url, page_cache)))
            except Exception as e:
                print(f"    Warning: definitions fetch failed: {e}", file=sys.stderr)

    # ---- 4. Merge definitions into permissible_values -------------------
    for code, entry in permissible_values.items():
        if code in definitions:
            entry["description"] = normalize_text(definitions[code])

    # ---- 5. Build French permissible_values -----------------------------
    fr_permissible_values = {}
    fr_definitions = {}

    fr_source_html = ""
    if "fr" in (locales or ["en"]):
        if page_cache is not None:
            fr_source_html = page_cache.get(statscan_fr_url(source_url), "")
            if not fr_source_html:
                print(f"  Warning: French main page not in {zip_path} — skipping FR.",
                      file=sys.stderr)
        else:
            fr_html_path = f"sources/{key}_fr.html"
            if os.path.exists(fr_html_path):
                with open(fr_html_path) as f:
                    fr_source_html = f.read()

    if fr_source_html:
        src_def_m = re.search(
            r'href=["\']([^"\']*Function=getVD[^"\']*&amp;D=1[^"\']*)["\']',
            fr_source_html, re.IGNORECASE)
        if src_def_m:
            fr_src_def_url = html.unescape(src_def_m.group(1))
            try:
                if page_cache is None:
                    print(f"  Fetching French source definitions {fr_src_def_url} ...")
                fr_definitions.update(
                    parse_statscan_definitions(_html_get(fr_src_def_url, page_cache)))
            except Exception as e:
                print(f"  Warning: could not fetch French source definitions: {e}", file=sys.stderr)

        fr_table_m = re.search(r'<table\b[^>]*>(.*?)</table>', fr_source_html, re.IGNORECASE | re.DOTALL)
        if fr_table_m:
            cat_td_index = 0
            header_m = (
                re.search(r'<thead\b[^>]*>(.*?)</thead>', fr_table_m.group(1), re.IGNORECASE | re.DOTALL)
                or re.search(r'<tr\b[^>]*>(.*?)</tr>', fr_table_m.group(1), re.IGNORECASE | re.DOTALL)
            )
            if header_m:
                header_cells = re.findall(
                    r'<t[hd]\b[^>]*>(.*?)</t[hd]>', header_m.group(1), re.IGNORECASE | re.DOTALL)
                for i, cell in enumerate(header_cells[1:], start=1):
                    cell_text = html.unescape(strip_tags(cell)).strip().lower()
                    if any(w in cell_text for w in ('catégorie', 'category', 'groupe', 'group',
                                                     'classe', 'class', 'nom', 'name')):
                        cat_td_index = i - 1
                        break
            fr_data_rows = re.findall(
                r'<tr\b[^>]*>(.*?)</tr>', fr_table_m.group(1), re.IGNORECASE | re.DOTALL)
            for row_html in fr_data_rows[1:]:
                th_m = re.search(r'<th\b[^>]*>(.*?)</th>', row_html, re.IGNORECASE | re.DOTALL)
                if not th_m or re.search(r'<a\b', th_m.group(1), re.IGNORECASE):
                    continue
                code = html.unescape(strip_tags(th_m.group(1))).strip()
                if not code:
                    continue
                td_cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row_html, re.IGNORECASE | re.DOTALL)
                title = ""
                if td_cells:
                    idx = cat_td_index if cat_td_index < len(td_cells) else 0
                    title = html.unescape(strip_tags(td_cells[idx])).strip()
                add_permissible_value(fr_permissible_values, code, title=title)

        for cpv_url in cpv_urls:
            fr_cpv_url = statscan_fr_url(cpv_url)
            if page_cache is None:
                print(f"  Fetching French CPV page {fr_cpv_url} ...")
            try:
                fr_cpv_html = _html_get(fr_cpv_url, page_cache)
            except Exception as e:
                print(f"  Warning: could not fetch French CPV {fr_cpv_url}: {e}", file=sys.stderr)
                continue

            struct_m = re.search(
                r'href=["\']([^"\']*Function=getVDStruct[^"\']*)["\']',
                fr_cpv_html, re.IGNORECASE)
            if struct_m:
                fr_struct_url = html.unescape(struct_m.group(1))
                try:
                    if page_cache is None:
                        print(f"    Fetching French structure {fr_struct_url} ...")
                    fr_struct_items = parse_statscan_structure(
                        _html_get(fr_struct_url, page_cache))
                    for code, title, indent in fr_struct_items:
                        add_permissible_value(fr_permissible_values, code, title=title)
                except Exception as e:
                    print(f"    Warning: French structure fetch failed: {e}", file=sys.stderr)
            else:
                # Leaf CPV: extract FR code+title from the FR CPV page panel-body.
                cpv_code_m = re.search(r'[?&]CPV=([^&"\'<>\s]+)', fr_cpv_url)
                if cpv_code_m:
                    leaf_code = html.unescape(cpv_code_m.group(1))
                    if leaf_code not in fr_permissible_values:
                        leaf_title = ""
                        pb_m = re.search(r'<div\b[^>]*\bpanel-body\b[^>]*>(.*?)</div>',
                                         fr_cpv_html, re.IGNORECASE | re.DOTALL)
                        if pb_m:
                            h2_m = re.search(r'<h2\b[^>]*\bbg-def-1\b[^>]*>(.*?)</h2>',
                                             pb_m.group(1), re.IGNORECASE | re.DOTALL)
                            if h2_m:
                                heading = html.unescape(strip_tags(h2_m.group(1))).strip()
                                sep_m2 = re.match(r'^\S+\s*[-\u2013]\s*(.*)', heading)
                                leaf_title = normalize_text(
                                    sep_m2.group(1).strip() if sep_m2 else heading)
                            p_m = re.search(r'<p\b[^>]*>(.*?)</p>',
                                            pb_m.group(1), re.IGNORECASE | re.DOTALL)
                            if p_m:
                                leaf_def = normalize_text(
                                    html.unescape(strip_tags(p_m.group(1))).strip())
                                if leaf_def:
                                    fr_definitions[leaf_code] = leaf_def
                        add_permissible_value(fr_permissible_values, leaf_code,
                                              title=leaf_title)

            def_m = re.search(
                r'href=["\']([^"\']*Function=getVD[^"\']*&amp;D=1[^"\']*)["\']',
                fr_cpv_html, re.IGNORECASE)
            if def_m:
                fr_def_url = html.unescape(def_m.group(1))
                try:
                    if page_cache is None:
                        print(f"    Fetching French definitions {fr_def_url} ...")
                    fr_definitions.update(
                        parse_statscan_definitions(_html_get(fr_def_url, page_cache)))
                except Exception as e:
                    print(f"    Warning: French definitions fetch failed: {e}", file=sys.stderr)

        for code, fr_entry in fr_permissible_values.items():
            if code in fr_definitions:
                fr_entry["description"] = normalize_text(fr_definitions[code])

    # ---- 6. Write YAML --------------------------------------------------
    # Title and description: prefer config values (user-editable); extract from
    # source HTML when absent so that -c alone is sufficient for existing sources.
    title = source.get("title") or ""
    if not title and source_html:
        for meta_m in re.finditer(r'<meta\b([^>]*)>', source_html, re.IGNORECASE):
            attrs = meta_m.group(1)
            if re.search(r'\bname=["\']dcterms\.title["\']', attrs, re.IGNORECASE):
                content_m = re.search(r'\bcontent=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
                if content_m:
                    title = normalize_text(content_m.group(1).strip())
                    break

    description = source.get("description") or ""
    if not description and source_html:
        panel_m = re.search(r'<div\b[^>]*\bpanel-body\b[^>]*>(.*?)</div>',
                            source_html, re.IGNORECASE | re.DOTALL)
        if panel_m:
            p_m = re.search(r'<p\b[^>]*>(.*?)</p>', panel_m.group(1), re.IGNORECASE | re.DOTALL)
            if p_m:
                desc_text = html.unescape(re.sub(r'<[^>]+>', '', p_m.group(1))).strip()
                description = normalize_text(re.sub(r'\s+', ' ', desc_text))

    schema = make_config_schema(
        id=source_url, name=key, title=title,
        description=description, version=source.get("version", ""),
        prefixes={"statscan": "https://www23.statcan.gc.ca/imdb/p3VD.pl?Function=getVD&TVD="},
        enums={key: {
            "name":               key,
            "title":              title,
            "description":        description,
            "permissible_values": permissible_values,
        }},
    )

    if fr_permissible_values:
        fr_enum = {"permissible_values": fr_permissible_values}
        if fr_source_html:
            for meta_m in re.finditer(r'<meta\b([^>]*)>', fr_source_html, re.IGNORECASE):
                attrs = meta_m.group(1)
                if re.search(r'\bname=["\']dcterms\.title["\']', attrs, re.IGNORECASE):
                    content_m = re.search(r'\bcontent=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
                    if content_m:
                        fr_enum["title"] = normalize_text(content_m.group(1).strip())
                        break
        schema["extensions"] = _make_locale_extensions(
            statscan_fr_url(source_url), key, source.get("version") or "", "fr",
            enums={key: fr_enum},
        )

    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    log_extraction(key, count=len(permissible_values),
                   lang_counts={"fr": len(fr_permissible_values)} if fr_permissible_values else None)


# ---------------------------------------------------------------------------
# Zip helpers (shared by -a match function and -f fetch function)
# ---------------------------------------------------------------------------

def _load_statscan_zip(zip_path):
    """Load all HTML pages from a sources zip.

    Returns a {url: html_text} dict, or None if the zip does not exist.
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
    """Return HTML for url from cache dict, falling back to a live fetch."""
    if cache is not None:
        if url in cache:
            return cache[url]
        print(f"{indent}Warning: {url} not in local archive — fetching live",
              file=sys.stderr)
    return fetch_html(url)


def _crawl_and_save_statscan_zip(key, url, locales, index_html=None):
    """Crawl all pages for a STATSCAN source and save to sources/{key}.zip.

    Starting from the main TVD page, discovers and fetches:
      - Source-level definitions page (Function=getVD … &D=1)
      - Each top-level CPV page (Function=getVD … &CPV=…)
        - Its structure sub-page (Function=getVDStruct)
        - Its definitions sub-page (Function=getVD … &D=1)
      - French equivalents of all the above (when 'fr' in locales)
    """
    zip_path = f"sources/{key}.zip"
    pages = {}  # {url: html_text}
    fetch_fr = "fr" in (locales or ["en"])

    def _get(u, label=""):
        if u in pages:
            return pages[u]
        try:
            h = fetch_html(u)
            pages[u] = h
            return h
        except Exception as e:
            tag = f" ({label})" if label else ""
            print(f"  Warning: failed to fetch {u}{tag}: {e}", file=sys.stderr)
            pages[u] = None
            return None

    # Main page
    if index_html is not None:
        pages[url] = index_html
    else:
        index_html = _get(url, "main page")
    if not index_html:
        print(f"  Error: could not fetch {url} — zip not saved.", file=sys.stderr)
        return

    if fetch_fr:
        _get(statscan_fr_url(url), "FR main page")

    # Source-level definitions page
    src_def_m = re.search(
        r'href=["\']([^"\']*Function=getVD[^"\']*&amp;D=1[^"\']*)["\']',
        index_html, re.IGNORECASE)
    if src_def_m:
        src_def_url = html.unescape(src_def_m.group(1))
        _get(src_def_url, "source definitions")
        if fetch_fr:
            _get(statscan_fr_url(src_def_url), "FR source definitions")

    # CPV pages and their sub-pages
    seen_cpv: set = set()
    for m in re.finditer(
            r'href=["\']([^"\']*Function=getVD[^"\']*&amp;CPV=[^"\']+)["\']',
            index_html, re.IGNORECASE):
        cpv_url = html.unescape(m.group(1))
        if cpv_url in seen_cpv:
            continue
        seen_cpv.add(cpv_url)
        cpv_html = _get(cpv_url, "CPV")
        if fetch_fr:
            _get(statscan_fr_url(cpv_url), "FR CPV")
        if cpv_html:
            struct_m = re.search(
                r'href=["\']([^"\']*Function=getVDStruct[^"\']*)["\']',
                cpv_html, re.IGNORECASE)
            if struct_m:
                struct_url = html.unescape(struct_m.group(1))
                _get(struct_url, "structure")
                if fetch_fr:
                    _get(statscan_fr_url(struct_url), "FR structure")
            def_m = re.search(
                r'href=["\']([^"\']*Function=getVD[^"\']*&amp;D=1[^"\']*)["\']',
                cpv_html, re.IGNORECASE)
            if def_m:
                def_url = html.unescape(def_m.group(1))
                _get(def_url, "definitions")
                if fetch_fr:
                    _get(statscan_fr_url(def_url), "FR definitions")

    manifest = {}
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, (u, h) in enumerate(pages.items()):
            entry = f"{i:04d}.html"
            zf.writestr(entry, (h or "").encode('utf-8'))
            manifest[u] = entry
        zf.writestr('manifest.json', json.dumps(manifest, indent=2))

    ok = sum(1 for h in pages.values() if h)
    print(f"  Saved {ok}/{len(pages)} pages to {zip_path}")


def fetch_statscan_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Re-download all pages for a STATSCAN source and save to sources/{key}.zip.

    Called by explicit -f [key].  Rebuilds the zip and updates download_date.
    """
    url = (source.get("reachable_from") or {}).get("source_ontology", "")
    if not url:
        print(f"  Skipping {key}: no source_ontology URL.", file=sys.stderr)
        return
    if locales is None:
        try:
            with open(config_file) as f:
                locales = (yaml.safe_load(f) or {}).get("locales") or ["en"]
        except Exception:
            locales = ["en"]
    print(f"  Crawling all pages for '{key}' ...")
    _crawl_and_save_statscan_zip(key, url, locales)
    update_source_config(key, {
        "download_date": datetime.date.today().isoformat(),
        "file_format": "zip",
    }, config_file)


def match_statscan_catalog(url, config_file=MENU_CONFIG):
    """Pre-download handler for the StatsCan classifications catalog search page.

    Detected URLs (English or French):
        https://www.statcan.gc.ca/en/concepts/search?datatype=classification...
        https://www.statcan.gc.ca/fr/concepts/recherche?datatype=classification...

    Fetches (or refreshes) the catalog, prints a subject-grouped summary, and
    caches the result to sources/sources_statscan_terms.yaml.  Does NOT add
    any entry to harvester_config.yaml — this is a discovery tool.  To add a
    classification after browsing the list, run:
        python term_harvester.py -a "{url}"

    Returns True if the URL matched (caller should skip further processing).
    """
    if "statcan.gc.ca" not in url:
        return False
    if "concepts/search" not in url and "concepts/recherche" not in url:
        return False

    entries = fetch_classification_catalog(force_refresh=True)
    if not entries:
        print("  Warning: catalog fetch returned no entries.", file=sys.stderr)
        return True

    # Group by subject for a compact summary
    by_subject: dict[str, list] = {}
    for e in entries:
        by_subject.setdefault(e["subject"], []).append(e)

    print(f"\n  {len(entries)} Statistics Canada entries"
          f" (cached to {_DEFAULT_CATALOG_PATH})\n")
    for subject in sorted(by_subject):
        group = by_subject[subject]
        print(f"  {subject} ({len(group)})")
        for e in group:
            print(f"    {e['key']:<30}  {e.get('entry_type', ''):<22}  {e['title']}")

    print(f"\n  To add any classification to your project:")
    print(f"    python term_harvester.py -a \"<url>\"")
    print(f"  where <url> is from the list above, e.g.:")
    print(f"    python term_harvester.py -a \"{entries[0]['url']}\"")
    return True


def match_statscan(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is a Statistics Canada variable definition page and was handled.

    Matches URLs like:
      https://www23.statcan.gc.ca/imdb/p3VD.pl?Function=getVD&TVD=1441857

    Reads the already-downloaded main page, then crawls all linked pages
    (CPV, structure, definitions, EN + FR) into sources/{key}.zip.
    """
    if not ("statcan.gc.ca" in url and "p3VD.pl" in url and "Function=getVD" in url):
        return False

    tvd_m = re.search(r'[?&]TVD=(\d+)', url)
    tvd_id = tvd_m.group(1) if tvd_m else "unknown"
    key = f"STATSCAN_{tvd_id}"

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    if key in config.get("sources", {}):
        print(f"  Skipping {url}: source key '{key}' already exists in {config_file}",
              file=sys.stderr)
        os.unlink(tmp_path)
        return True

    # Read the main page from the temp file, then discard it (goes into the zip)
    with open(tmp_path, encoding="utf-8", errors="replace") as f:
        html_text = f.read()
    os.unlink(tmp_path)

    # Extract title from <meta name="dcterms.title" content="..."> (attr order varies)
    title = ""
    for meta_m in re.finditer(r'<meta\b([^>]*)>', html_text, re.IGNORECASE):
        attrs = meta_m.group(1)
        if re.search(r'\bname=["\']dcterms\.title["\']', attrs, re.IGNORECASE):
            content_m = re.search(r'\bcontent=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
            if content_m:
                title = content_m.group(1).strip()
                break
    if not title:
        title = f"StatsCan Variable {tvd_id}"

    # Extract description from the first <p> inside the panel-body (status/scope line)
    description = None
    panel_m = re.search(r'<div\b[^>]*\bpanel-body\b[^>]*>(.*?)</div>',
                        html_text, re.IGNORECASE | re.DOTALL)
    if panel_m:
        p_m = re.search(r'<p\b[^>]*>(.*?)</p>', panel_m.group(1), re.IGNORECASE | re.DOTALL)
        if p_m:
            desc_text = html.unescape(re.sub(r'<[^>]+>', '', p_m.group(1))).strip()
            desc_text = re.sub(r'\s+', ' ', desc_text)
            if desc_text:
                description = desc_text

    # Version: prefer "version X" in title; fall back to dcterms.issued date
    version_m = re.search(r'\bversion\s+([A-Za-z0-9][A-Za-z0-9._-]*)', title, re.IGNORECASE)
    version = version_m.group(1) if version_m else None
    if not version:
        issued_m = re.search(
            r'<meta\b[^>]+dcterms\.issued[^>]+content=["\']([^"\']+)["\']',
            html_text, re.IGNORECASE)
        if issued_m:
            version = issued_m.group(1).strip()

    entry = make_source_entry(key, url, "STATSCAN", "zip",
                              title=title, description=description, version=version)
    entry["download_date"] = datetime.date.today().isoformat()
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"Added source '{key}' to {config_file}")

    locales = config.get("locales") or ["en"]
    _crawl_and_save_statscan_zip(key, url, locales, index_html=html_text)
    return True
