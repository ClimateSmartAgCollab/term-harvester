"""FreeText source handler for term_harvester.py.

Uses the Anthropic API (claude-opus-4-8) to extract picklist enumerations from
unstructured text.  Three entry points cover the full lifecycle:

  -a URI --free_text TEXT|FILE
      match_freetext()    — initial add: resolves inline text or file, runs
                            Claude, writes sources/{key}.yaml, downloads the
                            source URI to sources/{key}.{ext}, registers entry.

  -f [key]  (explicit only; -f all skips FreeText)
      fetch_freetext_source() — re-downloads the source URI to
                            sources/{key}.{ext} without running Claude.

  -c [key]  (explicit only; -c with no args skips FreeText)
      process_freetext_source() — extracts enums via Claude using, in priority:
                            (1) locally downloaded file from -a or -f [key],
                            (2) stored description from harvester_config.yaml.
                            No network access; run -f [key] first if needed.
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
  "version": "detected version string, or null if not found",
  "enums": [
    {
      "key": "PascalCaseEnumIdentifier",
      "title": "Human-readable enum title",
      "description": "Brief description of what this enum represents",
      "permissible_values": [
        {
          "code": "the code or identifier string",
          "title": "short label",
          "description": "explanation of the value (omit key if not available)",
          "is_intermediate": false
        }
      ]
    }
  ]
}

Rules:
- COMPLETENESS: Extract EVERY row from EVERY section of a table without
  exception. Count the rows in each section and verify your output matches.
  Missing values are worse than imperfect labels.
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
- SECTIONS: When a table or list has named sub-sections (e.g. "Brittleness",
  "Fluidity", "Smeariness"), extract each sub-section as its own enum with
  the section name as the enum key and title. Include "has_sections": true at
  the top level so the caller knows sections exist. Each section's rows are
  that enum's permissible_values. Never merge sections silently.
- MULTIPLE ENUMS: Extract one enum per distinct scale, classification, or
  table section. Each gets its own entry in "enums".
- TITLES: Keep titles concise (2–6 words). source_key and each enum key must be
  PascalCase identifiers that reflect the content (e.g. "LodgingScale",
  "GrainAppearanceScale").
- VERSION: If a "Source URL:" line is present, scan it for version patterns
  (e.g. "Ver4", "v2.1", "V4", "-ver-4", ".V4.", "version_2"). Also scan the
  first ~500 characters of text for explicit version labels such as "Version 4",
  "Ver. 4 November 2024", "v1.0", "Edition 3", "Release 2.1". Extract just the
  core version identifier (e.g. "4", "2.1", "Ver. 4"). Return null if nothing
  plausible is found — do not guess.
- INTERMEDIATES: When a numeric scale has only a few explicitly stated anchor
  points (e.g. "1 = Erect, 9 = Prostrate") but implies all integer steps exist,
  you may include the unstated intermediates. Mark each with "is_intermediate": true
  and leave its "title" as "" — a second pass will generate "between X and Y"
  labels. Explicitly stated values must have "is_intermediate": false. Only
  generate intermediates for continuous numeric scales; never for categorical or
  text-ordered lists.
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
        result = json.loads(raw)

        # Normalize: enums must be a list of dicts.  Claude occasionally returns
        # permissible-value lists directly as enum elements when the source text
        # is table-heavy.  Filter out non-dict elements and warn.
        enums = result.get("enums") if isinstance(result, dict) else None
        if not isinstance(enums, list):
            print("  Error: Claude response missing valid 'enums' list.", file=sys.stderr)
            return None
        bad = [i for i, e in enumerate(enums) if not isinstance(e, dict)]
        if bad:
            print(
                f"  Warning: Claude response contained {len(bad)} malformed enum(s)"
                f" (at index {bad}) — skipping those entries.",
                file=sys.stderr,
            )
            result = dict(result)
            result["enums"] = [e for e in enums if isinstance(e, dict)]
        return result

    except json.JSONDecodeError as e:
        print(f"  Error: Claude response was not valid JSON: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Error calling Anthropic API: {e}", file=sys.stderr)
        return None


_INTERMEDIATE_LABEL_PROMPT = """\
You are generating labels for intermediate values on a numeric rating scale.
Return ONLY a JSON object — no markdown fences, no explanation.
Map each intermediate code (as a string key) to a concise "between X and Y" label
where X and Y are brief 1-3 word semantic rephrasing of the nearest stated anchor
values immediately below and above the intermediate code."""


def _pv_sort_key(code):
    """Sort key for PV codes: numeric values first, then alphabetic."""
    try:
        return (0, float(code))
    except (ValueError, TypeError):
        return (1, str(code))


def _label_intermediates(enums):
    """Generate 'between X and Y' titles for is_intermediate PVs via a Haiku call.

    For each enum containing PVs marked is_intermediate, sends the stated anchor
    points and intermediate codes to Claude Haiku and fills in the returned labels.
    Modifies enums in place.  Returns True if any labels were generated.
    """
    client = _get_anthropic_client()
    if client is None:
        return False

    changed = False
    for enum_def in enums:
        pvs = enum_def.get("permissible_values", [])
        inters = [pv for pv in pvs if pv.get("is_intermediate")]
        stated = [pv for pv in pvs if not pv.get("is_intermediate")]
        if not inters or not stated:
            continue

        sorted_pvs = sorted(pvs, key=lambda p: _pv_sort_key(p.get("code", "")))
        anchor_lines = "\n".join(
            f'  {p["code"]}: {p.get("title") or p["code"]}'
            for p in sorted_pvs if not p.get("is_intermediate")
        )
        inter_codes = [str(p["code"]) for p in sorted(inters, key=lambda p: _pv_sort_key(p.get("code", "")))]
        example_key = inter_codes[0]
        user_msg = (
            f'Scale: "{enum_def.get("title") or enum_def.get("key", "?")}"'
            f"\nStated anchors:\n{anchor_lines}"
            f"\nGenerate labels for these intermediate codes: {', '.join(inter_codes)}"
            f'\nReturn JSON: {{"{example_key}": "between ... and ...", ...}}'
        )

        try:
            import anthropic
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=_INTERMEDIATE_LABEL_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text.strip()
            labels = json.loads(raw)
        except Exception as e:
            print(f"  Warning: intermediate label generation failed for"
                  f" {enum_def.get('key','?')}: {e}", file=sys.stderr)
            continue

        for pv in inters:
            code = str(pv.get("code", ""))
            if code in labels and labels[code]:
                pv["title"] = labels[code]
                changed = True

    return changed


def _md_cell(s):
    """Escape pipe chars and collapse newlines for a markdown table cell."""
    return str(s or "").replace("|", "\\|").replace("\n", " ").strip()


def _write_temp_report(enums, key, url):
    """Write temp.md and temp.tsv showing flat/separate/hierarchy views.

    temp.md  — GitHub-Flavored Markdown tables; hierarchy uses &nbsp; indentation.
    temp.tsv — tab-delimited; hierarchy uses two leading spaces in the label column.

    Returns the markdown path written ("temp.md").
    """
    total = sum(len(e.get("permissible_values", [])) for e in enums)
    multi = len(enums) > 1

    def _md_table(rows, label_pad=0):
        pad = "&nbsp;" * label_pad
        lines = [f"| Code | Label{pad} | Description |",
                 f"|------|-------{'---' * label_pad}|-------------|"]
        for code, label, desc in rows:
            lines.append(f"| `{_md_cell(code)}` | {_md_cell(label)} | {_md_cell(desc)} |")
        return "\n".join(lines)

    flat_rows = [
        (pv.get("code", ""), pv.get("title", ""), pv.get("description", ""))
        for e in enums for pv in e.get("permissible_values", [])
    ]

    # ---- Markdown ----
    md = [f"# {key}: Extracted Enumerations\n"]
    if url:
        md.append(f"**Source:** {url}  \n")
    md.append(f"**Extracted:** {len(enums)} section(s), {total} values\n")

    if multi:
        md.append(f"\n## Option 1 — Merge ({total} values merged)\n")
    else:
        md.append(f"\n## Values\n")
    md.append(_md_table(flat_rows))

    if multi:
        md.append(f"\n\n## Option 2 — Separate Enums ({len(enums)} sections)\n")
        for e in enums:
            md.append(f"\n### {e.get('title') or e.get('key', 'Section')}\n")
            rows = [(pv.get("code", ""), pv.get("title", ""), pv.get("description", ""))
                    for pv in e.get("permissible_values", [])]
            md.append(_md_table(rows))

        md.append(f"\n\n## Option 3 — Hierarchy View\n")
        md.append(
            "One enumeration will be created with section items at top-level; "
            "suitable for \"bag of terms\" multiple-selection into one field "
            "with section items set to read-only menu items.\n"
        )
        hier_rows = []
        for e in enums:
            hdr = e.get("key") or re.sub(r'\W+', '', e.get("title", "Section"))
            hier_rows.append((hdr, f"**{e.get('title') or hdr}**", e.get("description", "")))
            for pv in e.get("permissible_values", []):
                hier_rows.append((pv.get("code", ""),
                                  f"&nbsp;&nbsp;{pv.get('title', '')}",
                                  pv.get("description", "")))
        md.append(_md_table(hier_rows, label_pad=15))

    with open("temp.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    # ---- TSV ----
    tsv = [f"# {key} extracted enumerations"]
    if url:
        tsv.append(f"# Source: {url}")

    if multi:
        tsv += ["", "# Option 1: Merge", "Code\tLabel\tDescription"]
        for code, label, desc in flat_rows:
            tsv.append(f"{code}\t{label}\t{desc}")

        tsv += ["", "# Option 2: Separate Enums"]
        for e in enums:
            tsv += [f"\n# Section: {e.get('title') or e.get('key', '')}", "Code\tLabel\tDescription"]
            for pv in e.get("permissible_values", []):
                tsv.append(f"{pv.get('code','')}\t{pv.get('title','')}\t{pv.get('description','')}")

        tsv += ["", "# Option 3: Hierarchy", "Code\tLabel\tDescription"]
        for e in enums:
            hdr = e.get("key") or re.sub(r'\W+', '', e.get("title", "Section"))
            tsv.append(f"{hdr}\t{e.get('title') or hdr}\t{e.get('description', '')}")
            for pv in e.get("permissible_values", []):
                tsv.append(f"{pv.get('code','')}\t  {pv.get('title','')}\t{pv.get('description','')}")
    else:
        tsv += ["", "Code\tLabel\tDescription"]
        for code, label, desc in flat_rows:
            tsv.append(f"{code}\t{label}\t{desc}")

    with open("temp.tsv", "w", encoding="utf-8") as f:
        f.write("\n".join(tsv) + "\n")

    return "temp.md"


def _apply_section_format(enums, merged_key, fmt):
    """Transform a multi-enum list into the requested section format silently.

    fmt: 1 = flat (merge all PVs), 2 = separate (no change), 3 = hierarchical
         (section headers interspersed as terms).
    Returns the (possibly transformed) list of enum dicts.
    """
    if fmt == 2 or len(enums) <= 1:
        return enums

    if fmt == 1:
        merged_pvs = []
        for e in enums:
            merged_pvs.extend(e.get("permissible_values", []))
        return [{"key": merged_key, "title": merged_key, "permissible_values": merged_pvs}]

    # fmt == 3: section headers become terms, their values follow immediately
    merged_pvs = []
    for e in enums:
        header_code = e.get("key") or re.sub(r'\W+', '', e.get("title", "Section"))
        pv = {"code": header_code, "title": e.get("title") or header_code}
        if e.get("description"):
            pv["description"] = e["description"]
        merged_pvs.append(pv)
        merged_pvs.extend(e.get("permissible_values", []))
    return [{"key": merged_key, "title": merged_key, "permissible_values": merged_pvs}]


def _detect_format_from_yaml(yaml_path):
    """Infer section_format 1/2/3 from an existing sources/{key}.yaml.

    Returns an int (1, 2, or 3), or None if the file is absent/unreadable.

    Heuristic:
      • Multiple top-level enums → 2 (separate)
      • Single enum whose PV codes include at least one PascalCase word longer
        than 3 characters (looks like a section-header term) → 3 (hierarchical)
      • Single enum otherwise → 1 (flat)
    """
    if not os.path.exists(yaml_path):
        return None
    try:
        with open(yaml_path) as f:
            schema = yaml.safe_load(f) or {}
        enums = schema.get("enums") or {}
        if len(enums) > 1:
            return 2
        if len(enums) == 1:
            pvs = list(enums.values())[0].get("permissible_values") or {}
            # A PascalCase code longer than 3 chars is likely a section header
            for code in pvs:
                if re.match(r'^[A-Z][a-z]{2,}', str(code)):
                    return 3
            return 1
    except Exception:
        pass
    return None


def _prompt_section_format(enums, merged_key, current_include=None):
    """Interactively ask how to structure a multi-section extraction.

    Called only when Claude returns more than one enum and no stored preference
    is available.  Returns ``(transformed_enums, fmt_int, selected_keys)`` where
    *selected_keys* is a list of enum keys to write to include.concepts (or None
    meaning leave include.concepts unchanged).
    """
    letters = [chr(ord('a') + i) for i in range(len(enums))]
    total = sum(len(e.get("permissible_values", [])) for e in enums)

    # Step 1: enum subset selection
    print(f"\n  Recognized potentially {len(enums)} enumerations, {total} choices total."
          f"  Select which enumerations/choices should appear in output schema:")
    for letter, e in zip(letters, enums):
        title = e.get("title") or e.get("key", "?")
        count = len(e.get("permissible_values", []))
        ek = e.get("key", "")
        marker = "  ✓" if current_include and ek in current_include else ""
        print(f"    {letter}) {title!r}  ({count} values){marker}")
    selected_keys = None
    if current_include:
        key_to_letter = {e.get("key", ""): l for e, l in zip(enums, letters)}
        current_letters = [key_to_letter[k] for k in current_include if k in key_to_letter]
        include_default = ",".join(current_letters) if current_letters else "unchanged"
    else:
        include_default = "all by default"
    try:
        raw = input(f"  Include [{include_default}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return enums, 2, None
    if raw:
        chosen_letters = [c.strip() for c in re.split(r'[,\s]+', raw) if c.strip()]
        indices = [ord(c) - ord('a') for c in chosen_letters
                   if len(c) == 1 and c.isalpha() and 0 <= ord(c) - ord('a') < len(enums)]
        if not indices:
            print("  Unrecognised selection — leaving include unchanged.")
        else:
            selected_keys = [enums[i].get("key") for i in sorted(set(indices))]

    # Step 2: format selection
    print(f"\n  Now, for the selected enumerations, choose whether to:")
    print(f"  [1] Merge     — merge all {total} choices into 1 '{merged_key}' enum and ignore old enum names.")
    print(f"  [2] Separate  — keep enumerations separate  (default)")
    print(f"  [3] Hierarchy — one enum '{merged_key}' with section headers as terms")
    print(f"  (To preview each option, view temp.md or temp.tsv)")

    while True:
        try:
            choice = input("  Choice [2]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return enums, 2, selected_keys
        if not choice:
            choice = "2"
        if choice in ("1", "2", "3"):
            break
        print("  Enter 1, 2, or 3.")

    fmt = int(choice)
    return _apply_section_format(enums, merged_key, fmt), fmt, selected_keys


def _build_schema(result, source_url, key, see_also=None, version=None):
    """Convert a Claude extraction dict into a LinkML schema dict."""
    schema = make_config_schema(
        id=source_url,
        name=key,
        title=result.get("source_title", key),
        version=version or "",
    )
    for enum_def in result.get("enums", []):
        if not isinstance(enum_def, dict):
            print(f"  Warning: skipping malformed enum entry (expected dict, got"
                  f" {type(enum_def).__name__})", file=sys.stderr)
            continue
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
            if pv.get("is_intermediate") and code in permissible_values:
                permissible_values[code]["comments"] = ["intermediate"]
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


_ANCHOR_TIP_PDF  = ("  Tip: add #page=N or #page=N-M to the source URL to target a PDF page, "
                    "or #text=exact+phrase to anchor to matching text in any document type.")
_ANCHOR_TIP_HTML = ("  Tip: add #element-id (matching an id= attribute in the HTML) to anchor "
                    "to a specific section, or use #text=exact+phrase for a text-based anchor.")
_ANCHOR_TIP_TEXT = ("  Tip: add #text=exact+phrase to the source URL to anchor extraction "
                    "to the first occurrence of that phrase.")


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
        if ext == ".pdf":
            print(_ANCHOR_TIP_PDF, file=sys.stderr)
        else:
            print(_ANCHOR_TIP_TEXT, file=sys.stderr)
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


def _fetch_for_grounding_from_file(path):
    """Return full visible text from a locally downloaded HTML/text file for grounding.

    Same treatment as _fetch_for_grounding but reads from disk.
    Returns None for PDFs (binary) and on failure.
    """
    try:
        _, ext = os.path.splitext(path.lower())
        if ext == ".pdf":
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        raw = _BLOCK_TAG_RE.sub(lambda m: " " + m.group(), raw)
        text = strip_tags(raw)
        text = html_module.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text if len(text) >= 100 else None
    except Exception as e:
        print(f"  Warning: could not read {path} for grounding check: {e}",
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
        n_inter = sum(1 for pv in new_pvs.values()
                      if "intermediate" in ((pv or {}).get("comments") or []))
        if n_inter:
            count_str = f"{n_new - n_inter} stated choices, {n_new} total including intermediates"
        elif n_old == n_new:
            count_str = f"{n_new} values"
        else:
            count_str = f"{n_old} → {n_new} values"
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


def _parse_fragment(fragment):
    """Parse a URL fragment into an extraction directive dict.

    Supported forms
    ---------------
    page=N           — PDF page N (1-indexed); stored as 0-indexed
    page=N-M         — PDF pages N through M inclusive
    text=some+phrase — anchor extraction window to first match of the phrase
                       (URL-encoded or literal); works for any file type
    anything-else    — treated as an HTML element-ID anchor

    Returns a dict with a 'type' key, or None if fragment is absent.
    """
    if not fragment:
        return None
    import urllib.parse
    frag = fragment.strip()
    # page=N or page=N-M
    m = re.match(r'^page=(\d+)(?:-(\d+))?$', frag, re.IGNORECASE)
    if m:
        start = int(m.group(1)) - 1
        end   = int(m.group(2)) - 1 if m.group(2) else start
        return {'type': 'page', 'start': max(start, 0), 'end': max(end, start)}
    # text=QUERY  (URL-encoded or literal spaces)
    m = re.match(r'^text=(.+)$', frag, re.IGNORECASE)
    if m:
        return {'type': 'text', 'query': urllib.parse.unquote_plus(m.group(1))}
    # Anything else: treat as an HTML element-ID anchor
    return {'type': 'html_id', 'id': frag}


def _extract_text_from_file(path, url_fragment=None):
    """Extract and return plain text from a locally downloaded source file.

    Handles PDF (via pypdf), HTML (strip_tags), and plain text.
    Caps output at 10 000 characters.  Returns None on failure.

    url_fragment : str or None
        The fragment portion of the source URL (everything after '#').

        Supported anchoring schemes (applied before the 10 000-char cap):

        #page=N or #page=N-M
            PDF only.  Extract only the specified page(s).

        #text=some+exact+phrase
            Any file type.  Find the first case-insensitive match of the
            phrase in the extracted text and start the window 200 chars
            before it, giving Claude up to 10 000 chars from that point.

        #element-id  (bare id, no '=')
            HTML only.  Locate the element with id="element-id" in the raw
            HTML and begin text extraction from that point.

        When no fragment is given and the document exceeds 10 000 characters,
        a tip is printed suggesting the appropriate anchor form.
    """
    _, ext = os.path.splitext(path.lower())
    directive = _parse_fragment(url_fragment)

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
            if directive and directive['type'] == 'page':
                end_pg = min(directive['end'], len(reader.pages) - 1)
                pages  = reader.pages[directive['start'] : end_pg + 1]
                print(f"  Extracting PDF page(s) {directive['start']+1}–{end_pg+1}"
                      f" of {len(reader.pages)}")
            else:
                pages = reader.pages
            text = "\n".join(page.extract_text() or "" for page in pages)

        elif ext in (".html", ".htm"):
            with open(path, encoding="utf-8", errors="replace") as f:
                raw = f.read()
            # HTML element-ID anchor: find id="..." in raw HTML and slice from there
            if directive and directive['type'] == 'html_id':
                html_id = directive['id']
                patterns = [f'id="{html_id}"', f"id='{html_id}'",
                            f'name="{html_id}"', f"name='{html_id}'"]
                found = False
                for pat in patterns:
                    pos = raw.lower().find(pat.lower())
                    if pos >= 0:
                        raw = raw[pos:]
                        print(f"  Anchored to HTML element #{html_id}")
                        found = True
                        break
                if not found:
                    print(f"  Warning: HTML anchor #{html_id!r} not found in document"
                          f" — using full page.", file=sys.stderr)
            text = strip_tags(raw)

        else:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()

    except Exception as e:
        print(f"  Error reading {path}: {e}", file=sys.stderr)
        return None

    text = re.sub(r'\s+', ' ', text).strip()

    # text= anchor: find phrase in extracted text, window from there
    if directive and directive['type'] == 'text':
        query = directive['query']
        pos = text.lower().find(query.lower())
        if pos >= 0:
            start = max(0, pos - 200)
            text = text[start:]
            print(f"  Anchored to text {query!r} at position {pos:,}")
        else:
            print(f"  Warning: anchor text {query!r} not found in document"
                  f" — using full text.", file=sys.stderr)

    if len(text) > 10000:
        print(f"  Text truncated to 10 000 chars (source: {len(text):,} chars).")
        if not directive:
            if ext == ".pdf":
                print(_ANCHOR_TIP_PDF, file=sys.stderr)
            elif ext in (".html", ".htm"):
                print(_ANCHOR_TIP_HTML, file=sys.stderr)
            else:
                print(_ANCHOR_TIP_TEXT, file=sys.stderr)
        text = text[:10000]

    return text or None


def _find_shared_local_file(url, key, config_file):
    """Return path to an existing local file from another source with the same base URL.

    Strips URL fragments before comparing, so a FreeText source whose
    source_ontology ends with '#page=116' will match a sibling source whose
    source_ontology is the bare PDF URL.  Returns None if no match is found.
    """
    base_url = url.split("#")[0]
    try:
        with open(config_file) as f:
            cfg = yaml.safe_load(f) or {}
        for other_key, other_src in (cfg.get("sources") or {}).items():
            if other_key == key:
                continue
            other_url = (
                (other_src.get("reachable_from") or {})
                .get("source_ontology", "")
            ).split("#")[0]
            if not other_url or other_url != base_url:
                continue
            for ext in _SOURCE_EXTENSIONS:
                candidate = f"sources/{other_key}.{ext}"
                if os.path.exists(candidate):
                    return candidate, other_key
    except Exception:
        pass
    return None, None


def fetch_freetext_source(key, source, config_file=MENU_CONFIG):
    """Download the FreeText source URI to sources/{key}.{ext} then extract enums.

    Called only by explicit -f [key]; -f all silently skips FreeText sources.
    After a successful download (or when a shared file is available) immediately
    calls process_freetext_source so the YAML is refreshed in one step.

    If another source has already downloaded the same URL, its local file is
    used directly and no network request is made.
    """
    url = (source.get("reachable_from") or {}).get("source_ontology", "")
    if not url:
        print(f"  Skipping {key}: no source_ontology URL.", file=sys.stderr)
        return

    shared_path, shared_key = _find_shared_local_file(url, key, config_file)
    if shared_path:
        print(f"  Using existing {shared_path} (same URL as '{shared_key}') —"
              f" no download needed.")
        process_freetext_source(key, source, config_file)
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

    update_source_config(
        key,
        {"download_date": datetime.date.today().isoformat()},
        config_file,
    )

    # Immediately extract enums — same as running -c [key] after download
    process_freetext_source(key, source, config_file)


def match_freetext(url, free_text, config_file=MENU_CONFIG):
    """Handle a URL + free_text -a addition. Returns True if handled.

    Calls Claude to suggest a source key and title, prompts the user to
    confirm the key, downloads the source document, writes the config entry,
    then delegates YAML generation to process_freetext_source (same as -c).
    """
    src_file = free_text if os.path.isfile(free_text) else None
    if src_file:
        src_ext = os.path.splitext(src_file)[1].lstrip(".").lower() or "txt"

    # Resolve file path → plain text for the key-suggestion Claude call
    suggestion_text = _resolve_free_text(free_text)
    if suggestion_text is None:
        return True

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "Warning: ANTHROPIC_API_KEY not set"
            " — cannot process FreeText source.",
            file=sys.stderr,
        )
        return True

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}

    print("  Consulting Claude for source key and title ...")
    result = _call_claude(suggestion_text)
    if result is None:
        return False

    source_key = (result.get("source_key") or "").strip()
    if not source_key:
        print(
            "  Error: Claude response missing 'source_key' field.",
            file=sys.stderr,
        )
        return False

    existing_keys = set(config.get("sources", {}).keys())
    enum_names = [e.get("key", "?") for e in (result.get("enums") or [])]
    print(f"  Claude extracted {len(enum_names)} enum(s): {', '.join(enum_names)}")
    print(f"  Suggested source key: '{source_key}'")

    default_key = source_key
    if default_key in existing_keys:
        n = 1
        while f"{source_key}_{n}" in existing_keys:
            n += 1
        default_key = f"{source_key}_{n}"
        print(f"  Warning: '{source_key}' already exists in {config_file}."
              f"  Default adjusted to '{default_key}'.", file=sys.stderr)

    while True:
        try:
            user_input = input(f"  Source key [{default_key}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return True
        chosen = user_input if user_input else default_key
        if chosen in existing_keys:
            print(f"  '{chosen}' already exists in {config_file} — enter a different key.")
            continue
        if not re.match(r'^[A-Za-z][A-Za-z0-9_]*$', chosen):
            print(f"  '{chosen}' is not a valid identifier"
                  f" (letters/digits/underscores only, must start with a letter).")
            continue
        break
    source_key = chosen

    # Download the source document so process_freetext_source can work offline
    _raw_bytes = None
    _raw_ext = "html"
    _raw_source_path = None

    fetch_url = url.split("#")[0] if url else ""
    if src_file:
        _raw_source_path = f"sources/{source_key}.{src_ext}"
        _raw_ext = src_ext
    elif fetch_url and fetch_url.lower().startswith(("http://", "https://")):
        _shared_path, _shared_key = _find_shared_local_file(url, source_key, config_file)
        if _shared_path:
            _raw_source_path = _shared_path
            print(f"  Source document: using existing {_shared_path}"
                  f" (same URL as '{_shared_key}').")
        else:
            print(f"  Downloading source document ...")
            try:
                req = urllib.request.Request(fetch_url, headers=BROWSER_HEADERS)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    _ct_header = resp.headers.get("Content-Type", "")
                    _raw_bytes = resp.read()
                _raw_ext = _detect_ext(url, _ct_header)
                _raw_source_path = f"sources/{source_key}.{_raw_ext}"
            except Exception as e:
                print(f"  Warning: could not download source document: {e}",
                      file=sys.stderr)

    # Save source document to disk before process_freetext_source needs it
    today = datetime.date.today().isoformat()
    file_format = "yaml"
    if src_file:
        doc_path = f"sources/{source_key}.{src_ext}"
        with open(src_file, "rb") as s, open(doc_path, "wb") as d:
            d.write(s.read())
        print(f"  Copied source document to {doc_path}")
        file_format = src_ext
    elif _raw_bytes and _raw_source_path:
        with open(_raw_source_path, "wb") as f:
            f.write(_raw_bytes)
        print(f"  Saved source document to {_raw_source_path}")
        file_format = _raw_ext

    # Register source in config before calling process_freetext_source
    entry = make_source_entry(
        source_key, url, "FreeText", file_format,
        title=result.get("source_title"),
        description=suggestion_text,
    )
    if src_file or _raw_bytes:
        entry["download_date"] = today
    config.setdefault("sources", {})[source_key] = entry
    write_config(config, config_file)
    print(f"  Added source '{source_key}' to {config_file}")

    # Generate YAML using the same code path as -c [source_key]
    process_freetext_source(source_key, entry, config_file)
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


def process_freetext_source(key, source, config_file=MENU_CONFIG, locales=None, debug=False):
    """Extract enums via Claude for an explicitly named FreeText -c [key].

    Text source priority (first non-empty source wins):
      1. Locally downloaded file  sources/{key}.{ext}  (saved by -f [key])
      2. URI fetched temporarily — NOT saved to disk
      3. Stored description text from harvester_config.yaml

    Prints a diff of changes versus the existing sources/{key}.yaml before
    writing the new YAML.  Pass debug=True (--debug flag) to also print the
    full new YAML when the size-guard rejects it.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            f"Warning: ANTHROPIC_API_KEY not set — cannot process '{key}'.",
            file=sys.stderr,
        )
        return

    url = (source.get("reachable_from") or {}).get("source_ontology", "")
    url_fragment = url.split("#")[1] if "#" in url else None
    text = None

    # 1. Locally downloaded source file for this key
    for ext in _SOURCE_EXTENSIONS:
        candidate = f"sources/{key}.{ext}"
        if os.path.exists(candidate):
            print(f"  Using downloaded file {candidate} ...")
            text = _extract_text_from_file(candidate, url_fragment=url_fragment)
            if text:
                break

    # 1b. Another source's local file downloaded from the same base URL
    if not text and url:
        shared_path, shared_key = _find_shared_local_file(url, key, config_file)
        if shared_path:
            print(f"  Using existing {shared_path}"
                  f" (same source URL as '{shared_key}') ...")
            text = _extract_text_from_file(shared_path, url_fragment=url_fragment)

    # 2. No local file — remind user how to download
    if not text and url:
        print(
            f"  No local source file for '{key}'."
            f" Run '-f {key}' to download the source document.",
            file=sys.stderr,
        )

    # 3. Stored description fallback (covers inline-text FreeText sources)
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

    # Prepend source URL so Claude can detect version from URL path patterns
    if url:
        text = f"Source URL: {url}\n\n{text}"

    print(f"  Extracting '{key}' via Claude ...")
    result = _call_claude(text)
    if result is None:
        return

    # Extract and persist detected version
    version = (result.get("version") or "").strip() or None
    if version:
        print(f"  Detected version: {version!r}")
        update_source_config(key, {"version": version}, config_file)

    enums_raw = result.get("enums", [])

    # Generate "between X and Y" labels for any intermediate PVs before previewing
    _has_intermediates = any(
        pv.get("is_intermediate")
        for e in enums_raw for pv in e.get("permissible_values", [])
    )
    if _has_intermediates:
        print("  Generating labels for intermediate scale values ...")
        _label_intermediates(enums_raw)

    # Write temp.md + temp.tsv for the user to review before any prompt
    _write_temp_report(enums_raw, key, url)
    print("  Written temp.md and temp.tsv — review extracted content before confirming.")

    current_include = (source.get("include") or {}).get("concepts") or None

    if len(enums_raw) > 1:
        result = dict(result)
        yaml_path_check = f"sources/{key}.yaml"
        stored_fmt = source.get("section_format") or _detect_format_from_yaml(yaml_path_check)
        if stored_fmt:
            fmt_name = 'flat' if stored_fmt == 1 else 'separate' if stored_fmt == 2 else 'hierarchical'
            msg = f"  Stored: format {stored_fmt} ({fmt_name})"
            if stored_fmt == 2 and current_include:
                enum_keys = [e.get("key") for e in enums_raw]
                skipped = [ek for ek in enum_keys if ek not in current_include]
                msg += f", including: {', '.join(current_include)}"
                if skipped:
                    msg += f" (skipping: {', '.join(skipped)})"
            msg += f" — delete sources/{key}.yaml permissible_values to reprompt format question."
            print(msg)
            result["enums"] = _apply_section_format(result["enums"], key, stored_fmt)
            _new_section_fmt = stored_fmt
        else:
            result["enums"], _new_section_fmt, _selected = _prompt_section_format(
                result["enums"], key, current_include=current_include)
            update_source_config(key, {"section_format": _new_section_fmt}, config_file)
            if _selected is not None:
                update_source_config(key, {"include": {"concepts": _selected}}, config_file)
                print(f"  Set include.concepts: {', '.join(_selected)}")
    else:
        _new_section_fmt = None
        # Single enum — prompt even for flat list (new behaviour)
        if not source.get("section_format"):
            n_vals = len(enums_raw[0].get("permissible_values", [])) if enums_raw else 0
            print(f"\n  Extracted 1 enum with {n_vals} values.")
            try:
                input("  Review temp.md, then press Enter to write YAML [Ctrl+C to abort]: ")
            except (EOFError, KeyboardInterrupt):
                print("\n  Aborted.")
                return

    yaml_path = f"sources/{key}.yaml"
    see_also = url or None
    new_schema = _build_schema(result, url, key, see_also=see_also, version=version)

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
            if debug:
                print(
                    f"  DEBUG: rejected new extraction for '{key}':\n"
                    + "  " + yaml_content.replace("\n", "\n  ").rstrip(),
                    file=sys.stderr,
                )
            if see_also:
                _patch_see_also(yaml_path, see_also)
            return

    with open(yaml_path, "w") as f:
        f.write(yaml_content)
    enum_count = len(new_schema.get("enums") or {})
    print(f"  Saved {enum_count} enum(s) to {yaml_path}")
    return True  # signal to caller that YAML was written
