"""ISO 3166-2 country subdivision handler for term_harvester.py.

Detects URLs of the form:
    https://www.iso.org/obp/ui/#iso:code:3166:CA

The ISO OBP page is a Vaadin SPA and cannot be fetched directly.  Data is
sourced instead from the corresponding Wikipedia ISO 3166-2 article:
    https://en.wikipedia.org/wiki/ISO_3166-2:CA

The country "short name lower case" (extracted from the Wikipedia intro) forms
the enum key and name.  The suffix of each 3166-2 code (the part after the
first '-', e.g. 'AB' from 'CA-AB') becomes the permissible value key.  The
subdivision name in the requested language (default 'en') becomes the title.

Public API used by term_harvester.py:
    match_iso_country(url, tmp_path, config_file)
    fetch_iso_country_source(key, source, config_file)
    process_iso_country_source(key, source, config_file, locales)
"""

import datetime
import html as html_module
import os
import re
import sys
import urllib.request
import yaml

from source_utils import (
    BROWSER_HEADERS,
    MENU_CONFIG,
    IndentedDumper,
    _make_locale_extensions,
    add_permissible_value,
    make_config_schema,
    make_source_entry,
    update_source_config,
    write_config,
)

# Matches with or without scheme/www, e.g.:
#   https://www.iso.org/obp/ui/#iso:code:3166:CA
#   iso.org/obp/ui/#iso:code:3166:CA
_URL_RE = re.compile(
    r'iso\.org/obp/ui/(?:.*)?#iso:code:3166:([A-Za-z]{2})\s*$',
    re.IGNORECASE,
)

# English language-name → BCP 47 code, for Wikipedia column headers of the form
# "Subdivision name in English", "Subdivision name in French", etc.
_LANG_NAMES = {
    'english': 'en',  'french': 'fr',   'german': 'de',  'spanish': 'es',
    'italian': 'it',  'arabic': 'ar',   'japanese': 'ja', 'chinese': 'zh',
    'russian': 'ru',  'portuguese': 'pt', 'dutch': 'nl',  'korean': 'ko',
    'polish': 'pl',   'czech': 'cs',    'hungarian': 'hu', 'romanian': 'ro',
    'slovenian': 'sl', 'croatian': 'hr', 'bosnian': 'bs', 'serbian': 'sr',
    'macedonian': 'mk', 'albanian': 'sq', 'greek': 'el',  'turkish': 'tr',
    'ukrainian': 'uk', 'bulgarian': 'bg', 'latvian': 'lv', 'lithuanian': 'lt',
    'estonian': 'et', 'finnish': 'fi',  'swedish': 'sv',  'norwegian': 'no',
    'danish': 'da',   'icelandic': 'is', 'welsh': 'cy',   'irish': 'ga',
    'catalan': 'ca',  'basque': 'eu',
}


def _alpha2_from_url(url):
    """Extract the ISO 3166-1 alpha-2 code from an ISO OBP URL."""
    m = _URL_RE.search(url)
    return m.group(1).upper() if m else None


def _alpha2_from_key(key):
    """Extract the alpha-2 code from a source key like 'ISO_COUNTRY_CA'."""
    m = re.match(r'ISO_COUNTRY_([A-Z]{2})$', key, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _wikipedia_url(alpha2):
    return f"https://en.wikipedia.org/wiki/ISO_3166-2:{alpha2}"


def _country_name_to_key(name):
    """Convert a country name to a PascalCase identifier.

    "Canada"                 → "Canada"
    "United States"          → "UnitedStates"
    "Bosnia and Herzegovina" → "BosniaAndHerzegovina"
    """
    words = re.findall(r'[A-Za-z0-9]+', name)
    return ''.join(w.capitalize() for w in words) if words else "Country"


def _detect_lang_from_header(header):
    """Return BCP 47 language code from a Wikipedia subdivision-name column header.

    Handles two patterns:
      1. '( xx )' — explicit 2-3-char lowercase code inside parentheses
      2. 'in {language_name}' — matched against the _LANG_NAMES table
    Returns None when the header does not describe a subdivision name column.
    """
    h = header.lower()
    if 'name' not in h:
        return None
    # Explicit code: "Subdivision name ( en )" or "Name (fr)"
    code_m = re.search(r'\(\s*([a-z]{2,3})\s*\)', h)
    if code_m:
        return code_m.group(1)
    # Language name: "Subdivision name in English"
    for lang_name, lang_code in _LANG_NAMES.items():
        if f'in {lang_name}' in h:
            return lang_code
    return None


def _parse_wikipedia_iso3166(html_text):
    """Parse all wikitables from a Wikipedia ISO 3166-2:XX article.

    Returns:
        country_name (str)     — extracted from the intro paragraph
        rows (list[dict])      — each row: {suffix, names: {lang: title}, category}
        lang_order (list[str]) — language codes found, in first-encountered order
    """
    def _clean(fragment):
        text = re.sub(r'<[^>]+>', ' ', fragment)
        text = html_module.unescape(text)
        text = text.replace('‑', '-')   # non-breaking hyphen → regular hyphen
        return re.sub(r'[\s\xa0]+', ' ', text).strip()

    # Extract country name: "ISO 3166-2:CA is the entry for Canada in ISO 3166-2"
    plain = html_module.unescape(re.sub(r'<[^>]+>', ' ', html_text))
    plain = re.sub(r'[\s\xa0]+', ' ', plain)
    intro_m = re.search(r'is the entry for (?:the )?([A-Z][^.]{1,60}?) in ISO 3166-2', plain)
    country_name = intro_m.group(1).strip() if intro_m else ""

    all_rows = []
    lang_order = []   # first-seen order across all tables

    for table_m in re.finditer(
            r'<table\b[^>]*\bwikitable\b[^>]*>(.*?)</table>',
            html_text, re.IGNORECASE | re.DOTALL):
        table_html = table_m.group(1)
        tr_list = re.findall(r'<tr\b[^>]*>(.*?)</tr>', table_html, re.IGNORECASE | re.DOTALL)
        if not tr_list:
            continue

        # Parse header row
        hdr_cells = re.findall(
            r'<t[hd]\b[^>]*>(.*?)</t[hd]>', tr_list[0], re.IGNORECASE | re.DOTALL)
        headers = [_clean(c) for c in hdr_cells]

        lang_col = {}    # {lang_code: col_index}
        category_col = -1
        for i, h in enumerate(headers):
            if i == 0:
                continue   # Code column
            if 'categor' in h.lower():
                category_col = i
                continue
            lang = _detect_lang_from_header(h)
            if lang and lang not in lang_col:
                lang_col[lang] = i
                if lang not in lang_order:
                    lang_order.append(lang)

        # Parse data rows — only rows whose first cell looks like "XX-YYY"
        for tr_html in tr_list[1:]:
            cells_html = re.findall(
                r'<t[hd]\b[^>]*>(.*?)</t[hd]>', tr_html, re.IGNORECASE | re.DOTALL)
            if not cells_html:
                continue
            values = [_clean(c) for c in cells_html]
            code_raw = values[0] if values else ""
            if not re.match(r'^[A-Za-z]{2}-\S', code_raw):
                continue   # footer, note row, etc.

            suffix = code_raw.split('-', 1)[1] if '-' in code_raw else code_raw

            names = {
                lang: values[col_i]
                for lang, col_i in lang_col.items()
                if col_i < len(values) and values[col_i]
            }
            category = (
                values[category_col]
                if category_col >= 0 and category_col < len(values) else ""
            )
            all_rows.append({"suffix": suffix, "names": names, "category": category})

    return country_name, all_rows, lang_order


def fetch_iso_country_source(key, source, config_file=MENU_CONFIG):
    """Re-download the Wikipedia ISO 3166-2 article for the given source.

    Called by the explicit -f [key] handler in term_harvester.py for ISO_COUNTRY
    sources.  The ISO OBP source_ontology URL is a Vaadin SPA, so this function
    fetches the Wikipedia equivalent instead.
    """
    alpha2 = _alpha2_from_key(key) or _alpha2_from_url(
        (source.get("reachable_from") or {}).get("source_ontology", "")
    )
    if not alpha2:
        print(f"  Skipping {key}: cannot determine alpha-2 code.", file=sys.stderr)
        return

    wiki_url = _wikipedia_url(alpha2)
    output_path = f"sources/{key}.html"
    print(f"  Fetching {wiki_url} ...")
    try:
        req = urllib.request.Request(wiki_url, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception as e:
        print(f"  Error fetching {wiki_url}: {e}", file=sys.stderr)
        return

    if os.path.exists(output_path):
        existing = os.path.getsize(output_path)
        if existing > 0 and len(data) <= existing * 0.8:
            print(
                f"  Error: new download is {len(data):,} bytes"
                f" ({len(data) / existing:.0%} of existing {existing:,})"
                f" — keeping existing {output_path}",
                file=sys.stderr,
            )
            return

    with open(output_path, "wb") as f:
        f.write(data)
    print(f"  Saved to {output_path}")
    update_source_config(
        key, {"download_date": datetime.date.today().isoformat()}, config_file)


def process_iso_country_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Build sources/{key}.yaml from a downloaded Wikipedia ISO 3166-2 article."""
    html_path = f"sources/{key}.html"
    with open(html_path, encoding="utf-8", errors="replace") as f:
        html_text = f.read()

    country_name, rows, lang_order = _parse_wikipedia_iso3166(html_text)
    if not rows:
        print(f"  Warning: no subdivision rows found in {html_path}", file=sys.stderr)
        return

    # Primary language: 'en' if available, else first language found in the table
    primary_lang = "en" if "en" in lang_order else (lang_order[0] if lang_order else "en")

    enum_key = _country_name_to_key(country_name) if country_name else key
    enum_title = country_name or key

    pv_en = {}
    pv_fr = {}
    request_fr = "fr" in (locales or ["en"])

    for row in rows:
        suffix = row["suffix"]
        if not suffix:
            continue
        title = row["names"].get(primary_lang) or next(iter(row["names"].values()), suffix)
        add_permissible_value(pv_en, suffix, title=title)
        if request_fr:
            title_fr = row["names"].get("fr", "")
            if title_fr:
                add_permissible_value(pv_fr, suffix, title=title_fr)

    source_url = (source.get("reachable_from") or {}).get("source_ontology", "")
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or enum_title,
        enums={enum_key: {
            "name":               enum_key,
            "title":              enum_title,
            "permissible_values": pv_en,
        }},
    )
    if pv_fr:
        schema["extensions"] = _make_locale_extensions(
            source_url, key, source.get("version") or "", "fr",
            enums={enum_key: {"permissible_values": pv_fr}},
        )

    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    n_fr = len(pv_fr)
    print(f"Updated {yaml_path} ({len(pv_en)} subdivisions"
          + (f", {n_fr} French translations" if n_fr else "") + ")")


def match_iso_country(url, tmp_path, config_file=MENU_CONFIG):
    """Return True if *url* is an ISO OBP 3166-2 country page and was handled.

    The ISO OBP page is a Vaadin SPA; the downloaded tmp_path content is
    discarded and the Wikipedia ISO 3166-2 article is fetched instead.
    """
    alpha2 = _alpha2_from_url(url)
    if not alpha2:
        return False

    os.unlink(tmp_path)   # discard useless Vaadin bootstrap HTML

    key = f"ISO_COUNTRY_{alpha2}"

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    if key in config.get("sources", {}):
        print(f"  Skipping {url}: source key '{key}' already exists in {config_file}",
              file=sys.stderr)
        return True

    wiki_url = _wikipedia_url(alpha2)
    output_path = f"sources/{key}.html"
    print(f"  Fetching {wiki_url} ...")
    try:
        req = urllib.request.Request(wiki_url, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            html_bytes = resp.read()
    except Exception as e:
        print(f"  Error fetching {wiki_url}: {e}", file=sys.stderr)
        return True

    html_text = html_bytes.decode("utf-8", errors="replace")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    print(f"Saved to {output_path}")

    country_name, rows, _ = _parse_wikipedia_iso3166(html_text)
    title = country_name or f"ISO 3166-2:{alpha2}"

    entry = make_source_entry(key, url, "ISO_COUNTRY", "html", title=title)
    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"Added source '{key}' to {config_file}")
    return True
