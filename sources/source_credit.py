"""CRediT (Contributor Roles Taxonomy) PDF source for term_harvester.py.

Parses the CRediT roles PDF to produce one permissible value per contributor
role, with definition text and a meaning URI pointing to credit.niso.org.

Public API used by term_harvester.py:
    fetch_credit_source(key, source, config_file)
    process_credit_source(key, source, locales=None)
    match_credit(url, config_file)
"""

import datetime
import os
import re
import sys
import urllib.parse
import yaml

from source_utils import (
    MENU_CONFIG,
    IndentedDumper,
    add_permissible_value,
    log_extraction,
    make_config_schema,
    make_source_entry,
    update_source_config,
    write_config,
)
from source_zenodo import fetch_zenodo_file, is_zenodo_record_url, to_zenodo_api_url

_ROLE_NAMES = [
    "Conceptualization",
    "Data Curation",
    "Formal Analysis",
    "Funding Acquisition",
    "Investigation",
    "Methodology",
    "Project Administration",
    "Resources",
    "Software",
    "Supervision",
    "Validation",
    "Visualization",
    "Writing – original draft",
    "Writing – review & editing",
]

_NISO_BASE = "https://credit.niso.org/contributor-roles/"


def _role_slug(role):
    """Convert a role name to the NISO URI slug (hyphen-separated lowercase)."""
    s = role.lower()
    s = re.sub(r'[–—‒-]', '-', s)
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-')


def _role_key(role):
    """Return the permissible-value code (underscore-separated lowercase)."""
    return _role_slug(role).replace('-', '_')


def _parse_credit_pdf(pdf_path):
    """Return list of (code, title, definition) tuples from the CRediT PDF.

    Concatenates text from all pages, strips the header, then splits at each
    known role name. The definition is everything before the first § bullet.
    """
    try:
        import pypdf
        import logging
        logging.getLogger("pypdf").setLevel(logging.ERROR)
    except ImportError:
        print("Error: pypdf is required — install with: pip install pypdf",
              file=sys.stderr)
        return []

    try:
        reader = pypdf.PdfReader(pdf_path)
    except Exception as e:
        print(
            f"  Error reading PDF {pdf_path}: {e}\n"
            f"  The file may be corrupt or incomplete. Try re-fetching with -f CRediT.",
            file=sys.stderr,
        )
        return []
    text = " ".join((page.extract_text() or "") for page in reader.pages)
    text = re.sub(r'\s+', ' ', text).strip()

    # Strip the document header line that precedes the first role entry.
    header_pat = re.compile(
        r'CRediT roles and example research tasks.*?Example tasks\s*',
        re.IGNORECASE | re.DOTALL,
    )
    text = header_pat.sub('', text, count=1).strip()

    # Sort longer names first so "Writing – review & editing" beats "Writing".
    sorted_roles = sorted(_ROLE_NAMES, key=len, reverse=True)
    role_pat = re.compile(
        '(' + '|'.join(re.escape(r) for r in sorted_roles) + ')'
    )
    parts = role_pat.split(text)

    results = []
    i = 1
    while i + 1 < len(parts):
        role = parts[i]
        content = parts[i + 1].strip()
        i += 2
        bullet_idx = content.find('§')  # §
        if bullet_idx < 0:
            bullet_idx = content.find('§')
        definition = (content[:bullet_idx].strip() if bullet_idx >= 0
                      else content.strip())
        definition = definition.strip('. ') or None
        results.append((_role_key(role), role, definition))

    return results


def process_credit_source(key, source, locales=None):
    """Build a LinkML enum YAML from the CRediT PDF."""
    pdf_path = f"sources/{key}.pdf"
    if not os.path.exists(pdf_path):
        print(f"Skipping {key}: {pdf_path} not found — run -f to fetch first",
              file=sys.stderr)
        return

    roles = _parse_credit_pdf(pdf_path)
    if not roles:
        print(f"  Warning: no roles extracted from {pdf_path}", file=sys.stderr)
        return

    source_url = (source.get("reachable_from") or {}).get("source_ontology", "")
    permissible_values = {}
    for code, title, description in roles:
        add_permissible_value(
            permissible_values, code,
            title=title,
            description=description,
            meaning=_NISO_BASE + _role_slug(title) + "/",
        )

    schema = make_config_schema(
        id=source_url,
        name=key,
        title=source.get("title") or "CRediT Contributor Roles Taxonomy",
        description=source.get("description") or (
            "The Contributor Roles Taxonomy (CRediT) represents the roles "
            "typically played by contributors to scientific scholarly output."
        ),
        version=source.get("version") or "",
        enums={key: {"permissible_values": permissible_values}},
    )

    yaml_path = f"sources/{key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper,
                  default_flow_style=False, sort_keys=False)
    log_extraction(key, count=len(permissible_values))


# Matches bare Zenodo record URLs and file URLs containing "credit" and ".pdf".
# Both forms are accepted by match_credit; the API record URL is stored in config.
_ZENODO_CREDIT_RE = re.compile(
    r'zenodo\.org/(?:api/)?records/(\d+)'
    r'(?:/files/[^?]*credit[^?]*\.pdf)?',
    re.IGNORECASE,
)


def fetch_credit_source(key, source, config_file=MENU_CONFIG):
    """Re-download the CRediT PDF from Zenodo via the record API.

    Called by -f CRediT.  Uses fetch_zenodo_file() which queries the
    /api/records/{id} endpoint for the file list and downloads via the
    file's self link — no browser headers, which Zenodo CDN blocks (403).
    """
    url = (source.get("reachable_from") or {}).get("source_ontology", "")
    if not url:
        print(f"  Skipping {key}: no source_ontology URL.", file=sys.stderr)
        return
    pdf_path = f"sources/{key}.pdf"
    try:
        data, _, _ = fetch_zenodo_file(url, file_format="pdf")
    except Exception as e:
        keep = f" — keeping existing {pdf_path}" if os.path.exists(pdf_path) else ""
        print(f"  Error: {e}{keep}", file=sys.stderr)
        return
    if not data.startswith(b'%PDF-'):
        print("  Error: downloaded file is not a PDF — check Zenodo record access.",
              file=sys.stderr)
        return
    with open(pdf_path, "wb") as f:
        f.write(data)
    print(f"Saved to {pdf_path}")
    update_source_config(
        key, {"download_date": datetime.date.today().isoformat()}, config_file
    )
    process_credit_source(key, source)


def match_credit(url, config_file=MENU_CONFIG):
    """Return True if *url* is a Zenodo CRediT record or file URL and was handled.

    Accepts:
        https://zenodo.org/records/18421449
        https://zenodo.org/api/records/18421449
        https://zenodo.org/records/18421449/files/CRediT%20...pdf?download=1

    The Zenodo API record URL (https://zenodo.org/api/records/{id}) is stored
    in source_ontology so future -f fetches use the record API rather than
    the direct file URL (which Zenodo CDN blocks for scripted clients).
    """
    decoded = urllib.parse.unquote(url)
    m = _ZENODO_CREDIT_RE.search(decoded)
    if not m:
        return False

    # For bare record URLs (no /files/ path) require "credit" to appear
    # in the URL itself so we don't accidentally claim unrelated Zenodo records.
    if '/files/' not in decoded.lower() and 'credit' not in decoded.lower():
        return False

    key = "CRediT"

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    if key in config.get("sources", {}):
        print(f"  Skipping {url}: source key '{key}' already exists in {config_file}",
              file=sys.stderr)
        return True

    # Always store the clean API record URL rather than the file/download URL.
    record_api_url = to_zenodo_api_url(url)
    pdf_path = f"sources/{key}.pdf"
    try:
        data, _, _ = fetch_zenodo_file(record_api_url, file_format="pdf")
    except Exception as e:
        print(f"  Error: {e}", file=sys.stderr)
        return True
    if not data.startswith(b'%PDF-'):
        print("  Error: downloaded file is not a PDF — check Zenodo record access.",
              file=sys.stderr)
        return True
    with open(pdf_path, "wb") as f:
        f.write(data)
    print(f"Saved to {pdf_path}")

    entry = make_source_entry(
        key, record_api_url, "CRediT", "pdf",
        title="CRediT Contributor Roles Taxonomy",
        version="2022",
        description=(
            "The Contributor Roles Taxonomy (CRediT) is a high-level taxonomy"
            " representing the 14 roles typically played by contributors to"
            " scientific scholarly output. Each role describes a specific"
            " contribution to the scholarly output."
        ),
    )
    entry["see_also"] = "https://credit.niso.org/"

    config.setdefault("sources", {})[key] = entry
    write_config(config, config_file)
    print(f"Added source '{key}' to {config_file}")

    process_credit_source(key, entry)
    return True
