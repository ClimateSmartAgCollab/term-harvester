"""Shared utilities for term_harvester.py and its source helper modules.

Provides HTML parsing helpers, YAML output utilities, permissible-value
construction, and config-file management that are used by term_harvester.py
as well as source_nsdb.py, source_statscan.py, and other source helpers.

Public API:
    MENU_CONFIG
    BROWSER_HEADERS
    IndentedDumper
    strip_tags(html)
    fetch_html(url)
    normalize_text(s)
    log_extraction(key, *, term_type, doc_detail, count, lang_counts, indent)
    sort_prefixes(prefixes)
    make_config_schema(id, name, title, ...)
    add_permissible_value(permissible_values, code, *, ...)
    _make_locale_extensions(locale_id, name, version, lang, *, ...)
    find_description_before_table(html_text)
    find_labeled_field(html_text, label)
    to_pascal_case_key(text, drop_trailing_acronym=True)
    make_source_entry(key, url, content_type, file_format, ...)
    write_config(config, config_file)
    update_source_config(source_key, fields, config_file)
    rename_source_key(old_key, new_key, config_file)
    keys_from_minus(value)
    is_curie(value)
    _get_type_conf(api_conf, type_name)
"""

import datetime
import os
import re
import urllib.request
import yaml


MENU_CONFIG = "harvester_config.yaml"


# Browser-like headers used for all HTTP fetches.  Some servers (e.g. those
# behind a Barracuda WAF) reject requests that lack a realistic User-Agent,
# Accept, or Accept-Language header.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


class IndentedDumper(yaml.Dumper):
    """YAML dumper that indents list items one level under their parent key."""
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow=flow, indentless=False)


# Matches CURIE-style strings such as OBI:0000094 or oboInOwl:ObsoleteClass.
# The colon must be followed by a letter or digit (excludes URLs whose colon is
# followed by '//') and the string must start with a letter.
_CURIE_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9]*:[A-Za-z0-9]')


def _represent_str(dumper, data):
    """Single-quote strings that require it for safe YAML output.

    Strings containing a single-quote character are double-quoted to avoid the
    ugly '' escaping that YAML single-quote style requires.

    Strings containing a double-quote character are single-quoted: PyYAML may
    emit them as plain block scalars that wrap to a new line, and a continuation
    line starting with '"' is rejected by many YAML parsers.

    CURIEs (e.g. wd:Q1951, ENVO:00000428) are left as plain scalars — a colon
    is only a YAML key-value separator when followed by a space, so these are
    unambiguous without quotes.
    """
    if "'" in data and '"' not in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    if '"' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style="'")
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


IndentedDumper.add_representer(str, _represent_str)


# Typographic Unicode → ASCII equivalents.  Accented letters are intentionally
# excluded — they carry meaning in French, Spanish, and other language content.
_TYPO_MAP = str.maketrans({
    "–": "-",    # en dash
    "—": "-",    # em dash
    "‘": "'",    # left single quotation mark
    "’": "'",    # right single quotation mark / apostrophe
    "‚": ",",    # single low-9 quotation mark (looks like a comma)
    "“": '"',    # left double quotation mark
    "”": '"',    # right double quotation mark
    "„": '"',    # double low-9 quotation mark
    "…": "...",  # horizontal ellipsis
    " ": " ",    # non-breaking space
    "­": "",     # soft hyphen (invisible, remove)
    "•": "*",    # bullet
    "‹": "<",    # single left-pointing angle quotation mark
    "›": ">",    # single right-pointing angle quotation mark
    "«": "<",    # left-pointing double angle quotation mark
    "»": ">",    # right-pointing double angle quotation mark
    "≤": "<=",   # less-than or equal to
    "≥": ">=",   # greater-than or equal to
})


def get_api_key(env_var, config_value=None):
    """Return API key from environment variable, falling back to config_value."""
    return os.environ.get(env_var) or config_value or ""


def normalize_text(s):
    """Replace typographic Unicode with plain ASCII equivalents.

    Handles curly quotes, dashes, ellipsis, angle quotation marks, and similar
    decorative characters.  Accented letters (é, ñ, ü, etc.) are left intact.
    Returns *s* unchanged when it is None or empty.
    """
    if not s:
        return s
    return s.translate(_TYPO_MAP)


def log_extraction(key, *, term_type="enum", doc_detail=None, count=None,
                   lang_counts=None, indent="  "):
    """Print a standard extraction progress line.

    Format::
        {indent}Extracting {term_type} {key}[ from {doc_detail}][: ({count} terms)][ + fr(n), es(n) ...]

    key:         Enum or term key being extracted.
    term_type:   Label for the artefact type; defaults to ``"enum"``.
    doc_detail:  Optional source location, e.g. ``"PDF page 19"`` or ``"PDF pages 3-5"``.
    count:       Number of terms/values extracted; omitted when None.
    lang_counts: Ordered dict ``{lang_code: count}`` of translation counts; omitted when empty.
    indent:      Leading whitespace (default two spaces).
    """
    msg = f"{indent}Extracting {term_type} {key}"
    if doc_detail:
        msg += f" from {doc_detail}"
    if count is not None:
        msg += f": ({count} terms)"
    if lang_counts:
        lang_parts = [f"{lang}({n})" for lang, n in lang_counts.items() if n]
        if lang_parts:
            msg += " + " + ", ".join(lang_parts)
    print(msg)


def strip_tags(html):
    """Remove all HTML tags from a string and return stripped plain text."""
    return re.sub(r'<[^>]+>', '', html).strip()


def fetch_html(url):
    """Fetch a URL and return its decoded text content."""
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def add_permissible_value(permissible_values, code, *, title=None, description=None,
                          is_a=None, status=None, comments=None,
                          meaning=None, prefixes=None, exact_mappings=None):
    """Add one permissible value entry to *permissible_values* in-place.

    All keyword arguments are optional; a field is only written to the entry
    when its value is truthy.  'text' is omitted when it equals the key
    (always the case: key == code), since in LinkML the key already carries
    the text value.  Returns the entry.

    permissible_values: dict to mutate.
    code:           The permissible value code (used as the dict key).
    title:          Human-readable label.
    description:    Free-text definition.
    is_a:           Parent code for hierarchy.
    status:         Status string (e.g. 'ACTIVE').
    comments:       Additional commentary (LOINC-specific).
    meaning:        IRI for the concept; compressed to a CURIE when prefixes is provided.
    prefixes:       Dict of prefix→URI used to compress meaning to a CURIE.
    exact_mappings: List of CURIEs/IRIs that are exact matches for this concept.
    """
    entry = {}
    if title:
        title = normalize_text(title)
        if title.lower() != code.lower():
            entry["title"] = title
    if description: entry["description"] = normalize_text(description)
    if is_a:        entry["is_a"]        = is_a
    if status:      entry["status"]      = status
    if comments:    entry["comments"]    = normalize_text(comments)
    if meaning:
        if prefixes:
            for pfx, uri in prefixes.items():
                if meaning.startswith(uri):
                    meaning = f"{pfx}:{meaning[len(uri):]}"
                    break
        entry["meaning"] = meaning
    if exact_mappings:
        entry["exact_mappings"] = list(exact_mappings)
    permissible_values[code] = entry
    return entry


def _make_locale_extensions(locale_id, name, version, lang, *, description=None, enums=None):
    """Return a schema extensions dict with a single locale entry.

    locale_id:   IRI for the locale.
    name:        Schema / locale name field.
    version:     Version string.
    lang:        BCP 47 language tag (e.g. 'fr', 'en').
    description: Optional description; omitted when None/empty.
    enums:       Optional {enum_key: {"permissible_values": {…}}} mapping.
    """
    locale = {
        "id":          locale_id,
        "name":        name,
        "version":     version,
        "in_language": lang,
    }
    if description:
        locale["description"] = description
    if enums:
        locale["enums"] = enums
    return {"locales": {"tag": "locales", "value": {lang: locale}}}


def sort_prefixes(prefixes):
    """Return a copy of *prefixes* sorted case-insensitively by key.

    Accepts a dict or any falsy value (returns {} for falsy input).
    """
    if not prefixes:
        return {}
    return dict(sorted(prefixes.items(), key=lambda x: x[0].lower()))


def make_config_schema(id="", name="", title="", description="", version="",
                       license="", prefixes=None, default_prefix="",
                       in_language="en", classes={}, enums={}, slots={}):
    """Return a skeleton LinkML schema dict with all standard top-level fields.

    All parameters are optional; absent fields are included with empty/default
    values so callers always get a consistent structure.  'imports' is always
    set to ['linkml:types'].

    id:            Schema IRI / source URL.
    name:          Schema identifier (usually the source key).
    title:         Human-readable title.
    description:   Free-text description.
    version:       Version string.
    license:       License identifier (e.g. 'CC0').
    prefixes:      Prefix dict {'PREFIX': 'uri', ...}.
    default_prefix: Default prefix string.
    in_language:   BCP 47 language tag for the schema content (default 'en').
    classes:       Pre-built classes dict to embed (default empty).
    enums:         Pre-built enums dict to embed (default empty).
    slots:         Pre-built slots dict to embed (default empty).
    """
    return {
        "id":             id,
        "name":           name,
        "title":          title,
        "description":    description,
        "version":        version,
        "license":        license,
        "in_language":    in_language,
        "imports":        ["linkml:types"],
        "prefixes":       sort_prefixes(prefixes),
        "default_prefix": default_prefix,
        "classes":        dict(classes) if classes else {},
        "enums":          dict(enums) if enums else {},
        "slots":          dict(slots) if slots else {},
    }


def find_description_before_table(html_text):
    """Return the text of the paragraph immediately preceding the first table
    with a 'Name' column, typically the introductory description of the listing.

    Locates the byte-position of the matching table, then finds the last <p>
    element that ends before that position.  Returns an empty string if no
    suitable paragraph is found.
    """
    # Find start position of the first table that has a Name column header
    table_start = None
    for m in re.finditer(r'<table[^>]*>(.*?)</table>', html_text, re.IGNORECASE | re.DOTALL):
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(1), re.IGNORECASE | re.DOTALL)
        if not rows:
            continue
        header_cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', rows[0], re.IGNORECASE | re.DOTALL)
        if 'name' in [strip_tags(h).strip().lower() for h in header_cells]:
            table_start = m.start()
            break

    if table_start is None:
        return ""

    # Last <p>...</p> before the table
    before_table = html_text[:table_start]
    paragraphs = list(re.finditer(r'<p[^>]*>(.*?)</p>', before_table, re.IGNORECASE | re.DOTALL))
    if paragraphs:
        return strip_tags(paragraphs[-1].group(1)).strip()

    return ""


def find_labeled_field(html_text, label):
    """Return the first paragraph of content from a named field in the HTML.

    Compares the inner text (HTML tags stripped) of each cell to the label so
    that formatting such as <b>Definition</b> is handled transparently.

    Searches:
    - Table rows: a cell whose inner text matches label, then uses the next cell
    - Definition lists: <dt> whose inner text matches label, then uses <dd>

    Returns the text of the first <p> inside the content, or the full stripped
    content if no <p> tags are present.  Returns "" if the label is not found.
    """
    label_lower = label.strip().lower()

    def _extract(content):
        p_m = re.search(r'<p[^>]*>(.*?)</p>', content, re.IGNORECASE | re.DOTALL)
        return strip_tags(p_m.group(1) if p_m else content).strip()

    # Table rows: label must be the first cell (properties/metadata tables);
    # this avoids false matches on expansion table column headers where the
    # label may appear at an arbitrary position mid-row.
    for row_html in re.findall(r'<tr[^>]*>(.*?)</tr>', html_text, re.IGNORECASE | re.DOTALL):
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row_html, re.IGNORECASE | re.DOTALL)
        if len(cells) >= 2 and strip_tags(cells[0]).strip().lower() == label_lower:
            return _extract(cells[1])

    # Definition lists: check inner text of <dt>, return content of <dd>
    for dt_m in re.finditer(r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>',
                             html_text, re.IGNORECASE | re.DOTALL):
        if strip_tags(dt_m.group(1)).strip().lower() == label_lower:
            return _extract(dt_m.group(2))

    return ""


def to_pascal_case_key(text, drop_trailing_acronym=True):
    """Convert a phrase to a PascalCase identifier.

    Words are split on any non-alphanumeric run.  All-caps words of two or more
    characters (acronyms) are kept as-is; all other words are title-cased.

    When *drop_trailing_acronym* is True (the default), the last word is omitted
    if it is an exact uppercase match for the concatenated first letters of all
    preceding words — i.e. it is a redundant trailing initialism.

    Examples::

        to_pascal_case_key("Farm Product Price Index - FPPI")
        # → "FarmProductPriceIndex"   (FPPI == F+P+P+I, dropped)

        to_pascal_case_key("Farm Product Price Index - FPPI", drop_trailing_acronym=False)
        # → "FarmProductPriceIndexFPPI"

        to_pascal_case_key("Consumer Price Index")
        # → "ConsumerPriceIndex"      (no trailing acronym)

        to_pascal_case_key("FPPI")
        # → "FPPI"                    (single word, never dropped)
    """
    words = [w for w in re.split(r'[^A-Za-z0-9]+', text) if w]
    if drop_trailing_acronym and len(words) >= 2:
        initials = ''.join(w[0].upper() for w in words[:-1])
        if words[-1].upper() == initials:
            words = words[:-1]
    return ''.join(w if (w.isupper() and len(w) > 1) else w.capitalize() for w in words)


def make_source_entry(key, url, content_type, file_format, *,
                      title=None, version=None, description=None):
    """Return a standard config source entry dict.

    Builds the fields shared by every source type: name, title, version,
    content_type, file_format, reachable_from.source_ontology, and
    download_date.  description is included only when provided (not None).
    Callers can add extra fields (see_also, prefixes, …) after the call.

    key:          Source key used as 'name' and in the file path.
    url:          Canonical URL written to reachable_from.source_ontology.
    content_type: Content type string (e.g. 'NSDBSNT', 'LOINC').
    file_format:  File extension without dot (e.g. 'html', 'json', 'csv').
    title:        Human-readable title (None → stored as null).
    version:      Version string (None → stored as null).
    description:  Free-text description; omitted from the dict when None.
    """
    entry = {
        "title":          title,
        "name":           key,
        "version":        version,
        "content_type":   content_type,
        "file_format":    file_format,
        "reachable_from": {"source_ontology": url},
        "download_date":  datetime.date.today().isoformat(),
    }
    if description is not None:
        entry["description"] = description
    return entry


def write_config(config, config_file=MENU_CONFIG):
    """Write config to config_file, sorting sources keys alphabetically.

    Leading ``# …`` comment lines are preserved from the existing file.  If
    the file does not yet exist (or has no leading comments), any ``comment``
    key in *config* is written as ``# …`` lines instead.  Comment text is
    never stored as YAML data, so colons, quotes, and other special characters
    never cause parser issues.
    """
    # Preserve leading # lines from the existing file (yaml.safe_load discards them).
    existing_comments = []
    if os.path.exists(config_file):
        with open(config_file) as f:
            for line in f:
                if line.startswith('#'):
                    existing_comments.append(line.rstrip('\n'))
                else:
                    break

    comment_lines = existing_comments or [f"# {l}" for l in (config.get("comment") or [])]
    data = {k: v for k, v in config.items() if k != "comment"}
    if "sources" in data and isinstance(data["sources"], dict):
        data["sources"] = dict(sorted(data["sources"].items(), key=lambda x: x[0].lower()))
    with open(config_file, "w") as f:
        if comment_lines:
            for line in comment_lines:
                f.write(line + "\n")
            f.write("\n")
        yaml.dump(data, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)


def update_source_config(source_key, fields, config_file=MENU_CONFIG):
    """Update one or more fields on a source entry in harvester_config.yaml."""
    with open(config_file, "r") as f:
        config = yaml.safe_load(f)
    config["sources"][source_key].update(fields)
    write_config(config, config_file)


def rename_source_key(old_key, new_key, config_file=MENU_CONFIG):
    """Rename a source key in harvester_config.yaml, preserving entry order."""
    with open(config_file, "r") as f:
        config = yaml.safe_load(f)
    config["sources"] = {
        (new_key if k == old_key else k): v
        for k, v in config["sources"].items()
    }
    write_config(config, config_file)


def _get_type_conf(api_conf, type_name):
    """Return the sub-dict for a given api type (e.g. 'rest', 'sparql'), or {}."""
    return ((api_conf or {}).get("type") or {}).get(type_name) or {}


def is_curie(value):
    """Return True if *value* looks like a CURIE (prefix:localpart) rather than a full URI."""
    if not value or ":" not in value:
        return False
    pfx = value.split(":")[0]
    return bool(pfx) and "/" not in pfx and pfx.lower() not in ("http", "https", "urn")


def keys_from_minus(value):
    """Normalise a minus concepts/permissible_values entry to a set of string keys.

    Accepts a dict (keys used), a list/tuple (items used), a bare string
    (treated as a single key), or None/falsy (returns empty set).
    """
    if not value:
        return set()
    if isinstance(value, dict):
        return set(value)
    if isinstance(value, (list, tuple)):
        return set(value)
    return {str(value)}
