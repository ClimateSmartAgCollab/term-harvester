"""FreeText source handler for term_harvester.py.

Uses the Anthropic API (claude-opus-4-8) to extract picklist enumerations from
unstructured text.  Three entry points cover the full lifecycle:

  -a URI --free_text TEXT|FILE
      match_freetext()    — initial add: resolves inline text or file, runs
                            Claude, writes sources/{key}.yaml, registers entry.

  -f [key]  (explicit only; -f all skips FreeText)
      fetch_freetext_source() — downloads the source URI to
                            sources/{key}.{ext} without running Claude.

  -c [key]  (explicit only; -c with no args skips FreeText)
      process_freetext_source() — extracts enums via Claude using, in priority:
                            (1) locally downloaded file from -f [key],
                            (2) temporarily fetched URI text (not saved),
                            (3) stored description from harvester_config.yaml.
                            Prints a diff vs the existing sources/{key}.yaml.

Requires:
    ANTHROPIC_API_KEY environment variable
    pip install anthropic        (always)
    pip install pypdf            (only for PDF input)

Public API:
    match_freetext(url, free_text, config_file)
    fetch_freetext_source(key, source, config_file)
    process_freetext_source(key, source, config_file, locales=None)
"""

import datetime
import html as html_module
import json
import os
import re
import sys
import urllib.request
import yaml

from source_utils import (
    MENU_CONFIG,
    BROWSER_HEADERS,
    IndentedDumper,
    make_config_schema,
    make_source_entry,
    add_permissible_value,
    strip_tags,
    update_source_config,
    write_config,
)


_EXTRACTION_SYSTEM_PROMPT = """\
You are a data standardization assistant. Extract structured picklist/enumeration
data from the provided text and return ONLY a JSON object — no markdown fences,
no explanation, just JSON.

The JSON must have this structure:
{
  "source_key": "PascalCaseIdentifier",
  "source_title": "Human-readable title for the overall source",
  "enums": [
    {
      "key": "PascalCaseEnumIdentifier",
      "title": "Human-readable enum title",
      "description": "Brief description of what this enum represents",
      "permissible_values": [
        {
          "code": "the code or identifier string",
          "title": "short label",
          "description": "explanation of the value (omit key if not available)"
        }
      ]
    }
  ]
}

Rules:
- ORDERING: Always list permissible_values from lowest/worst/least to
  highest/best/most. For numeric scales, list codes in ascending numeric order
  (1, 2, 3 … 9). For categorical scales, use the natural semantic order (e.g.
  absent → trace → low → medium → high). Ordinal intent must be preserved.
- CODES: Use codes exactly as they appear in the source (numbers, letters,
  abbreviations). When the source provides no explicit codes, form a unique
  2–3 letter uppercase abbreviation from each label's significant words
  (e.g. "Poorly aerated" → "PA", "Moderately aerated" → "MA",
  "Well aerated" → "WA", "None" → "NO", "Trace" → "TR"). All abbreviations
  within one enum must be distinct — add a third letter to resolve clashes.
  Only use sequential integers (1, 2, 3 …) when the source document itself
  uses an explicit numeric rating scale. If a numeric range is described
  (e.g. 1–9) with per-value labels in the source, use the integers as codes
  and supply a title for each.
- MULTIPLE ENUMS: Extract one enum per distinct scale or classification in the
  text. Each gets its own entry in "enums".
- TITLES: Keep titles concise (2–6 words). source_key and each enum key must be
  PascalCase identifiers that reflect the content (e.g. "LodgingScale",
  "GrainAppearanceScale").
- Return only the JSON object, no surrounding text."""


def _get_anthropic_client():
    """Return an Anthropic client, or None with a warning if unavailable."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "Warning: ANTHROPIC_API_KEY environment variable not set"
            " — FreeText source type requires the Anthropic API.",
            file=sys.stderr,
        )
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        print(
            "Warning: 'anthropic' package not installed"
            " — run: pip install anthropic",
            file=sys.stderr,
        )
        return None


def _call_claude(free_text):
    """Call Claude to extract enum data from free text. Returns parsed dict or None."""
    client = _get_anthropic_client()
    if client is None:
        return None
    try:
        with client.messages.stream(
            model="claude-opus-4-8",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=[{
                "type": "text",
                "text": _EXTRACTION_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": free_text}],
        ) as stream:
            response = stream.get_final_message()

        raw = next(
            (b.text for b in response.content if b.type == "text"), ""
        ).strip()
        if not raw:
            print("  Error: Claude returned no text content.", file=sys.stderr)
            return None
        return json.loads(raw)

    except json.JSONDecodeError as e:
        print(f"  Error: Claude response was not valid JSON: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Error calling Anthropic API: {e}", file=sys.stderr)
        return None


def _build_schema(result, source_url, key, see_also=None):
    """Convert a Claude extraction dict into a LinkML schema dict."""
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=result.get("source_title", key),
    )
    for enum_def in result.get("enums", []):
        enum_key = enum_def.get("key", key)
        permissible_values = {}
        for pv in enum_def.get("permissible_values", []):
            code = str(pv.get("code", "")).strip()
            if not code:
                continue
            add_permissible_value(
                permissible_values,
                code,
                title=pv.get("title") or None,
                description=pv.get("description") or None,
            )
        entry = {
            "name":  enum_key,
            "title": enum_def.get("title", enum_key),
        }
        if enum_def.get("description"):
            entry["description"] = enum_def["description"]
        if see_also:
            entry["see_also"] = see_also
        if permissible_values:
            entry["permissible_values"] = permissible_values
        schema["enums"][enum_key] = entry
    return schema


def _resolve_free_text(value):
    """Resolve a --free_text value to plain text.

    If *value* is a path to an existing file, read and return its text:
      .txt  — read directly
      .pdf  — extract via pypdf (pip install pypdf)
      other — attempt to read as UTF-8 text with a warning

    If *value* is not a recognised file path, return it as-is (inline text).
    Returns None and prints an error when the file cannot be read.
    Extracted text is capped at 10 000 characters so Claude receives a focused
    excerpt rather than a very large document.
    """
    if not os.path.isfile(value):
        return value  # inline text

    _, ext = os.path.splitext(value.lower())
    print(f"  Reading {value} ...")

    try:
        if ext == ".pdf":
            try:
                import pypdf
            except ImportError:
                print(
                    "  Error: 'pypdf' not installed — run: pip install pypdf",
                    file=sys.stderr,
                )
                return None
            reader = pypdf.PdfReader(value)
            parts = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(parts)

        elif ext == ".txt":
            with open(value, encoding="utf-8", errors="replace") as f:
                text = f.read()

        else:
            print(
                f"  Warning: unrecognised file extension '{ext}'"
                " — attempting to read as plain text.",
                file=sys.stderr,
            )
            with open(value, encoding="utf-8", errors="replace") as f:
                text = f.read()

    except Exception as e:
        print(f"  Error reading {value}: {e}", file=sys.stderr)
        return None

    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > 10000:
        print(f"  Text truncated to 10 000 chars (source: {len(text):,} chars).")
        text = text[:10000]
    if not text:
        print(f"  Error: no readable text extracted from {value}.", file=sys.stderr)
        return None
    return text


def _fetch_uri_text(url):
    """Fetch a URI and return its visible text, or None on failure.

    Strips HTML tags, collapses whitespace, and caps at 10 000 characters to
    avoid excessive token use in the Claude call.
    """
    try:
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read().decode(charset, errors="replace")
        text = strip_tags(raw)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) < 100:
            return None
        return text[:10000]
    except Exception as e:
        print(f"  Warning: could not fetch {url}: {e}", file=sys.stderr)
        return None


# Block-level HTML elements whose boundaries should become spaces when tags
# are stripped, preventing adjacent cell/div text from concatenating.
_BLOCK_TAG_RE = re.compile(
    r'</?(?:td|th|tr|div|p|li|br|h[1-6])\b',
    re.IGNORECASE,
)


def _fetch_for_grounding(url):
    """Fetch a URI and return full visible text for grounding checks.

    Unlike _fetch_uri_text, there is no size cap (the text is not sent to
    Claude) and spaces are injected before block-level HTML elements so that
    adjacent table cells and divs don't concatenate after tag stripping.
    Returns None when the page is unreachable or yields < 100 chars of text.
    """
    try:
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read().decode(charset, errors="replace")
        raw = _BLOCK_TAG_RE.sub(lambda m: " " + m.group(), raw)
        text = strip_tags(raw)
        text = html_module.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text if len(text) >= 100 else None
    except Exception as e:
        print(f"  Warning: could not fetch {url} for grounding check: {e}",
              file=sys.stderr)
        return None


def _diff_report(old_yaml_path, new_schema):
    """Return a human-readable diff string comparing old and new schemas.

    Covers: added/removed enums; added/removed/retitled permissible values.
    Returns a ready-to-print string (may be multi-line).
    """
    new_enums = new_schema.get("enums") or {}

    if not os.path.exists(old_yaml_path):
        lines = ["  No prior YAML — new extraction:"]
        for ek, ed in new_enums.items():
            pv_count = len((ed or {}).get("permissible_values") or {})
            lines.append(f"    + {ek} ({pv_count} values)")
        return "\n".join(lines)

    with open(old_yaml_path) as f:
        old_schema = yaml.safe_load(f) or {}
    old_enums = old_schema.get("enums") or {}

    old_keys = set(old_enums)
    new_keys = set(new_enums)
    lines = []

    for ek in sorted(new_keys - old_keys):
        pv_count = len((new_enums[ek] or {}).get("permissible_values") or {})
        lines.append(f"  + {ek}: new enum ({pv_count} values)")

    for ek in sorted(old_keys - new_keys):
        lines.append(f"  - {ek}: removed")

    for ek in sorted(old_keys & new_keys):
        old_pvs = (old_enums[ek] or {}).get("permissible_values") or {}
        new_pvs = (new_enums[ek] or {}).get("permissible_values") or {}
        old_codes = set(old_pvs)
        new_codes = set(new_pvs)
        enum_lines = []
        for code in sorted(new_codes - old_codes):
            t = (new_pvs[code] or {}).get("title", "")
            enum_lines.append(f"      + '{code}': {t!r}")
        for code in sorted(old_codes - new_codes):
            t = (old_pvs[code] or {}).get("title", "")
            enum_lines.append(f"      - '{code}': {t!r}")
        for code in sorted(old_codes & new_codes):
            ot = (old_pvs[code] or {}).get("title", "")
            nt = (new_pvs[code] or {}).get("title", "")
            if ot != nt:
                enum_lines.append(f"      ~ '{code}': {ot!r} → {nt!r}")
        n_old, n_new = len(old_pvs), len(new_pvs)
        count_str = f"{n_old} values" if n_old == n_new else f"{n_old} → {n_new} values"
        if enum_lines:
            lines.append(f"  ~ {ek} ({count_str}):")
            lines.extend(enum_lines)
        else:
            lines.append(f"    {ek}: no change ({count_str})")

    return "\n".join(lines) if lines else "  No changes detected."


def _normalize_text(s):
    """Lowercase and collapse hyphens, dashes, whitespace, and &nbsp; to spaces."""
    return re.sub(r'[\s \-–—]+', ' ', s).lower().strip()


def _check_grounding(enums, doc_text):
    """Return {enum_key: [titles_not_found]} for PV titles absent from doc_text.

    Matching is case-insensitive with hyphens and dashes normalised to spaces,
    so 'Non-saline', 'non saline', and 'non–saline' all compare equal.
    Returns an empty dict when every title is found at least once.
    """
    doc_norm = _normalize_text(doc_text)
    unmatched = {}
    for enum_def in enums:
        key = enum_def.get("key", "?")
        missing = []
        for pv in enum_def.get("permissible_values", []):
            title = (pv.get("title") or "").strip()
            if title and _normalize_text(title) not in doc_norm:
                missing.append(title)
        if missing:
            unmatched[key] = missing
    return unmatched


_SOURCE_EXTENSIONS = ("pdf", "html", "htm", "txt", "docx")


def _detect_ext(url, content_type_header):
    """Infer a file extension from the URL path and/or Content-Type header."""
    url_path = url.split("?")[0].split("#")[0].rstrip("/")
    stem = url_path.split("/")[-1]
    if "." in stem:
        ext = stem.rsplit(".", 1)[1].lower()
        if ext in _SOURCE_EXTENSIONS:
            return ext
    ct = (content_type_header or "").lower().split(";")[0].strip()
    return {
        "application/pdf": "pdf",
        "text/plain":       "txt",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    }.get(ct, "html")


def _extract_text_from_file(path):
    """Extract and return plain text from a locally downloaded source file.

    Handles PDF (via pypdf), HTML (strip_tags), and plain text.
    Caps output at 10 000 characters.  Returns None on failure.
    """
    _, ext = os.path.splitext(path.lower())
    try:
        if ext == ".pdf":
            try:
                import pypdf
            except ImportError:
                print(
                    "  Error: 'pypdf' not installed — run: pip install pypdf",
                    file=sys.stderr,
                )
                return None
            reader = pypdf.PdfReader(path)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif ext in (".html", ".htm"):
            with open(path, encoding="utf-8", errors="replace") as f:
                text = strip_tags(f.read())
        else:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
    except Exception as e:
        print(f"  Error reading {path}: {e}", file=sys.stderr)
        return None

    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > 10000:
        print(f"  Text truncated to 10 000 chars (source: {len(text):,} chars).")
        text = text[:10000]
    return text or None


def fetch_freetext_source(key, source, config_file=MENU_CONFIG):
    """Download the FreeText source URI to sources/{key}.{ext}.

    Called only by explicit -f [key]; -f all silently skips FreeText sources.
    Does NOT run Claude or write a YAML file — use -c [key] after downloading
    to extract enums from the saved document.
    """
    url = (source.get("reachable_from") or {}).get("source_ontology", "")
    if not url:
        print(f"  Skipping {key}: no source_ontology URL.", file=sys.stderr)
        return

    fetch_url = url.split("#")[0]
    print(f"  Fetching {fetch_url} ...")
    try:
        req = urllib.request.Request(fetch_url, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            ct_header = resp.headers.get("Content-Type", "")
            data = resp.read()
    except Exception as e:
        print(f"  Error fetching {fetch_url}: {e} — not saving.", file=sys.stderr)
        return

    if not data:
        print("  Error: downloaded file is empty — not saving.", file=sys.stderr)
        return

    ext = _detect_ext(url, ct_header)
    output_path = f"sources/{key}.{ext}"

    if os.path.exists(output_path):
        existing_size = os.path.getsize(output_path)
        if existing_size > 0 and len(data) <= existing_size * 0.8:
            print(
                f"  Error: download is {len(data):,} bytes"
                f" ({len(data) / existing_size:.0%} of existing {existing_size:,})"
                f" — keeping existing {output_path}",
                file=sys.stderr,
            )
            return

    with open(output_path, "wb") as f:
        f.write(data)
    print(f"  Saved to {output_path}")
    print(f"  Run '-c {key}' to extract enums via Claude.")

    update_source_config(
        key,
        {"download_date": datetime.date.today().isoformat()},
        config_file,
    )


def match_freetext(url, free_text, config_file=MENU_CONFIG):
    """Handle a URL + free_text -a addition. Returns True if handled.

    Calls Claude to extract enumerations from the provided text, writes
    sources/{key}.yaml, and adds a FreeText entry to the config file.
    The free_text is stored in the 'description' field for later -c re-runs.

    If free_text is a file path the file is copied to sources/{key}.{ext} and
    file_format is set to that extension.  Inline text uses file_format 'yaml'.
    """
    # Remember whether the value is a local file before resolving it to text
    src_file = free_text if os.path.isfile(free_text) else None
    if src_file:
        src_ext = os.path.splitext(src_file)[1].lstrip(".").lower() or "txt"

    # Resolve file path → plain text before anything else
    free_text = _resolve_free_text(free_text)
    if free_text is None:
        return True  # file read failed; caller should not fall through to other matchers

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "Warning: ANTHROPIC_API_KEY not set"
            " — cannot process FreeText source.",
            file=sys.stderr,
        )
        return True  # abort; do not fall through to other matchers

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}

    print("  Extracting picklist data from free text via Claude ...")
    result = _call_claude(free_text)
    if result is None:
        return False

    source_key = (result.get("source_key") or "").strip()
    if not source_key:
        print(
            "  Error: Claude response missing 'source_key' field.",
            file=sys.stderr,
        )
        return False

    if source_key in config.get("sources", {}):
        print(
            f"  Skipping: source key '{source_key}' already exists in {config_file}",
            file=sys.stderr,
        )
        return True

    # Verify extracted values against the source document.
    # Skipped silently when the URI is not fetchable as text (PDFs, network errors).
    fetch_url = url.split("#")[0] if url else ""
    doc_text = _fetch_for_grounding(fetch_url) if fetch_url else None
    if doc_text:
        unmatched = _check_grounding(result.get("enums", []), doc_text)
        if unmatched:
            total = sum(len(v) for v in unmatched.values())
            print(
                f"\n  Warning: {total} enum value(s) not found verbatim"
                f" in the source document:"
            )
            for enum_key, titles in unmatched.items():
                print(f"    {enum_key}:")
                for t in titles:
                    print(f"      - {t!r}")
            print(
                "  These values may come from Claude's training knowledge"
                " rather than the source document."
            )
            try:
                resp = input("\n  Accept into config anyway? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                resp = "n"
            if resp != "y":
                print("  Source not added.")
                return True

    see_also = url or None
    schema = _build_schema(result, url, source_key, see_also=see_also)
    yaml_path = f"sources/{source_key}.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(
            schema, f,
            Dumper=IndentedDumper, default_flow_style=False, sort_keys=False,
        )
    enum_count = len(schema.get("enums") or {})
    print(f"  Saved {enum_count} enum(s) to {yaml_path}")

    # Copy the source document into sources/ so -c [key] can find it later
    file_format = "yaml"
    if src_file:
        doc_path = f"sources/{source_key}.{src_ext}"
        with open(src_file, "rb") as s, open(doc_path, "wb") as d:
            d.write(s.read())
        print(f"  Copied source document to {doc_path}")
        file_format = src_ext

    entry = make_source_entry(
        source_key, url, "FreeText", file_format,
        title=result.get("source_title"),
        description=free_text,
    )
    config.setdefault("sources", {})[source_key] = entry
    write_config(config, config_file)
    print(f"  Added source '{source_key}' to {config_file}")
    return True


def _patch_see_also(yaml_path, see_also):
    """Add see_also to any enum in an existing YAML that is missing it."""
    with open(yaml_path) as f:
        schema = yaml.safe_load(f) or {}
    updated = 0
    for ed in (schema.get("enums") or {}).values():
        if ed and "see_also" not in ed:
            ed["see_also"] = see_also
            updated += 1
    if updated:
        with open(yaml_path, "w") as f:
            yaml.dump(schema, f, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
        print(f"  Added see_also to {updated} enum(s) in {yaml_path}")


def process_freetext_source(key, source, config_file=MENU_CONFIG, locales=None):
    """Extract enums via Claude for an explicitly named FreeText -c [key].

    Text source priority (first non-empty source wins):
      1. Locally downloaded file  sources/{key}.{ext}  (saved by -f [key])
      2. URI fetched temporarily — NOT saved to disk
      3. Stored description text from harvester_config.yaml

    Prints a diff of changes versus the existing sources/{key}.yaml before
    writing the new YAML.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            f"Warning: ANTHROPIC_API_KEY not set — cannot process '{key}'.",
            file=sys.stderr,
        )
        return

    url = (source.get("reachable_from") or {}).get("source_ontology", "")
    text = None

    # 1. Locally downloaded source file
    for ext in _SOURCE_EXTENSIONS:
        candidate = f"sources/{key}.{ext}"
        if os.path.exists(candidate):
            print(f"  Using downloaded file {candidate} ...")
            text = _extract_text_from_file(candidate)
            if text:
                break

    # 2. Temporary URI fetch (not saved)
    if not text and url:
        print(f"  No local source file — fetching {url} temporarily ...")
        text = _fetch_uri_text(url)

    # 3. Stored description fallback
    if not text:
        text = (source.get("description") or "").strip() or None

    if not text:
        print(
            f"  Error: no text source available for '{key}'."
            f" Run '-f {key}' to download the source document first,"
            f" or re-add the source with '-a URI --free_text ...'",
            file=sys.stderr,
        )
        return

    print(f"  Extracting '{key}' via Claude ...")
    result = _call_claude(text)
    if result is None:
        return

    yaml_path = f"sources/{key}.yaml"
    see_also = url or None
    new_schema = _build_schema(result, url, key, see_also=see_also)

    print(_diff_report(yaml_path, new_schema))

    yaml_content = yaml.dump(
        new_schema,
        Dumper=IndentedDumper, default_flow_style=False, sort_keys=False,
    )
    new_size = len(yaml_content.encode("utf-8"))

    if os.path.exists(yaml_path):
        existing_size = os.path.getsize(yaml_path)
        if existing_size > 0 and new_size <= existing_size * 0.8:
            print(
                f"  Error: new extraction is {new_size:,} bytes"
                f" ({new_size / existing_size:.0%} of existing {existing_size:,})"
                f" — keeping existing {yaml_path}",
                file=sys.stderr,
            )
            if see_also:
                _patch_see_also(yaml_path, see_also)
            return

    with open(yaml_path, "w") as f:
        f.write(yaml_content)
    enum_count = len(new_schema.get("enums") or {})
    print(f"  Saved {enum_count} enum(s) to {yaml_path}")
