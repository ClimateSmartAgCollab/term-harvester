#!/usr/bin/env python3
"""Translate CANSIS FR glossary entries to EN and apply FR locale extensions.

Two-phase workflow:

  python source_cansis_translate.py --translate
      Extracts (fr_term, fr_definition) pairs from the CANSIS_GLOSSARY.zip FR
      pages, sends them to Google Translate (FR -> EN) in batches of ≤5000
      characters, and writes CANSIS_GLOSSARY_translated_fr.tsv.

  python source_cansis_translate.py --apply [--threshold 0.65]
      Reads CANSIS_GLOSSARY_translated_fr.tsv, fuzzy-matches each EN-translated term label
      against CANSIS_GLOSSARY.yaml permissible value titles, and writes FR
      locale extensions back into CANSIS_GLOSSARY.yaml.  Reports unmatched
      terms to stdout.

CANSIS_GLOSSARY_translated_fr.tsv columns (tab-separated, header on row 1):
  fr_term           Original French term label
  fr_definition     Original French definition
  en_label          Google-translated English term label  (for matching)
  en_definition     Google-translated English definition   (for reference)

Requires: pip install deep-translator
"""

import argparse
import difflib
import os
import re
import sys
import time
import zipfile
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from source_utils import IndentedDumper  # noqa: E402

_ZIP_PATH  = os.path.join(_HERE, "CANSIS_GLOSSARY.zip")
_EN_YAML   = os.path.join(_HERE, "CANSIS_GLOSSARY.yaml")
_TSV_PATH  = os.path.join(_HERE, "CANSIS_GLOSSARY_translated_fr.tsv")
_ENUM_KEY  = "CANSIS_GLOSSARY"
_FR_BASE   = "https://sis.agr.gc.ca/siscan/glossary/"
_LETTERS   = list("abcdefghijklmnopqrstuvwxyz") + ["_"]
_THRESHOLD = 0.65  # default SequenceMatcher ratio for a match


# ---------------------------------------------------------------------------
# Google Translate helpers
# ---------------------------------------------------------------------------

def _require_translator():
    try:
        from deep_translator import GoogleTranslator
        return GoogleTranslator
    except ImportError:
        print(
            "Error: deep-translator is required.\n"
            "Install it with:  pip install deep-translator",
            file=sys.stderr,
        )
        sys.exit(1)


def _translate_list(translator_cls, texts, src="fr", tgt="en",
                    max_chars=4800, delay=0.25):
    """Translate a list of single-line strings in batches, FR -> EN.

    Items are sent as a numbered list (``1. text\\n2. text\\n…``).  Google
    Translate treats each numbered list item as an independent unit and
    preserves the numbering, making this far more reliable than blank-line
    paragraph splitting.  Multi-line translation output for a single item is
    collected as continuation lines until the next numbered marker.

    If the returned item count doesn't match the input the batch is retried
    item-by-item.

    Returns a list of translated strings with the same length as `texts`.
    """
    if not texts:
        return []

    results = [""] * len(texts)

    def _translate_one(text):
        t = translator_cls(source=src, target=tgt)
        try:
            out = t.translate(text.replace("\n", " ").strip())
            return (out or "").strip()
        except Exception as e:
            print(f"  Warning: translation error: {e}", file=sys.stderr)
            return ""

    def _flush(indices, lines):
        if not indices:
            return
        # [N] bracket markers — treated as reference annotations by Google
        # Translate and passed through untouched, unlike "N." list numbers.
        block = "\n".join(f"[{i + 1}] {line}" for i, line in enumerate(lines))
        t = translator_cls(source=src, target=tgt)
        try:
            translated = (t.translate(block) or "").strip()
        except Exception as e:
            print(f"  Warning: batch translation error ({e}); retrying individually",
                  file=sys.stderr)
            for idx, line in zip(indices, lines):
                results[idx] = _translate_one(line)
                time.sleep(delay)
            return

        # Sequential parser: only promote a line to a new item when its
        # number equals next_expected.  This prevents continuation lines
        # that happen to start with a digit pattern from being misread as
        # new items.  Also accepts N. and N) in case Google reformats [N].
        items = {}
        current_num = None
        current_parts = []
        next_expected = 1

        for raw in translated.splitlines():
            m = (re.match(r'^\[(\d+)\]\s*(.*)', raw) or
                 re.match(r'^(\d+)[.)]\s+(.*)', raw))
            if m and int(m.group(1)) == next_expected:
                if current_num is not None:
                    items[current_num] = " ".join(current_parts).strip()
                current_num = next_expected
                next_expected += 1
                current_parts = [m.group(2).strip()]
            elif current_num is not None and raw.strip():
                current_parts.append(raw.strip())

        if current_num is not None:
            items[current_num] = " ".join(current_parts).strip()

        n = len(indices)
        if len(items) == n and set(items) == set(range(1, n + 1)):
            for i, idx in enumerate(indices):
                results[idx] = items.get(i + 1, "")
        else:
            print(
                f"  Warning: expected {n} bracketed items, got {len(items)};"
                " retrying individually",
                file=sys.stderr,
            )
            for idx, line in zip(indices, lines):
                results[idx] = _translate_one(line)
                time.sleep(delay)
        time.sleep(delay)

    batch_indices = []
    batch_lines   = []
    batch_chars   = 0

    for i, text in enumerate(texts):
        clean = text.replace("\n", " ").replace("\r", " ").strip()
        # Overhead: "N. " prefix (allow up to 6 chars) + newline separator
        overhead = 7

        # Oversized single item: translate alone
        if len(clean) + overhead > max_chars:
            _flush(batch_indices, batch_lines)
            batch_indices, batch_lines, batch_chars = [], [], 0
            results[i] = _translate_one(clean[:max_chars])
            time.sleep(delay)
            continue

        needed = len(clean) + overhead
        if batch_lines and batch_chars + needed > max_chars:
            _flush(batch_indices, batch_lines)
            batch_indices, batch_lines, batch_chars = [], [], 0

        batch_indices.append(i)
        batch_lines.append(clean)
        batch_chars += needed

    _flush(batch_indices, batch_lines)
    return results


# ---------------------------------------------------------------------------
# FR page parsing
# ---------------------------------------------------------------------------

def _parse_fr_pages(zip_path):
    """Return list of (fr_term, fr_definition) from all FR letter pages."""
    entries = []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for letter in _LETTERS:
            name = f"{letter}_fr.html"
            if name not in names:
                continue
            html = zf.read(name).decode("utf-8", errors="replace")
            dl_m = re.search(r'<dl>(.*?)</dl>', html, re.IGNORECASE | re.DOTALL)
            if not dl_m:
                continue
            parts = re.split(r'<dt>(.*?)</dt>', dl_m.group(1),
                             flags=re.IGNORECASE | re.DOTALL)
            for i in range(1, len(parts), 2):
                term = re.sub(r'<[^>]+>', '', parts[i]).strip()
                term = re.sub(r'\s+', ' ', term).strip()
                after = parts[i + 1] if i + 1 < len(parts) else ""
                dd_m  = re.match(r'\s*<dd>(.*?)(?:</dd>|$)', after,
                                 re.IGNORECASE | re.DOTALL)
                dd_html = dd_m.group(1) if dd_m else after
                defn = re.sub(r'<[^>]+>', ' ', dd_html)
                defn = re.sub(r'\s+', ' ', defn).strip()
                if term:
                    entries.append((term, defn))
    return entries


# ---------------------------------------------------------------------------
# Phase 1: --translate
# ---------------------------------------------------------------------------

def run_translate():
    GoogleTranslator = _require_translator()

    if not os.path.exists(_ZIP_PATH):
        print(f"Error: {_ZIP_PATH} not found.", file=sys.stderr)
        sys.exit(1)

    print("Parsing FR pages from ZIP ...")
    entries = _parse_fr_pages(_ZIP_PATH)
    print(f"  Found {len(entries)} FR entries.")

    fr_terms = [t for t, _ in entries]
    fr_defs  = [d for _, d in entries]

    print("Translating FR term labels to EN ...")
    en_labels = _translate_list(GoogleTranslator, fr_terms)
    print(f"  Done ({len(en_labels)} labels).")

    print("Translating FR definitions to EN ...")
    en_defs = _translate_list(GoogleTranslator, fr_defs)
    print(f"  Done ({len(en_defs)} definitions).")

    with open(_TSV_PATH, "w", encoding="utf-8") as f:
        f.write("fr_term\tfr_definition\ten_label\ten_definition\n")
        for (fr_t, fr_d), en_l, en_d in zip(entries, en_labels, en_defs):
            cols = [fr_t, fr_d, en_l, en_d]
            f.write("\t".join(c.replace("\t", " ") for c in cols) + "\n")

    print(f"Wrote {_TSV_PATH} ({len(entries)} rows).")


# ---------------------------------------------------------------------------
# Phase 2: --apply
# ---------------------------------------------------------------------------

def _normalize(s):
    """Lowercase, strip punctuation and extra whitespace."""
    s = s.lower()
    s = re.sub(r'[;,\(\)\[\]/]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _best_match(en_label, norm_pv_map, threshold):
    """Return (pv_key, score) for the closest EN PV title, or (None, score)."""
    norm = _normalize(en_label)
    best_key, best_score = None, 0.0
    for pv_key, norm_title in norm_pv_map.items():
        score = difflib.SequenceMatcher(None, norm, norm_title).ratio()
        if score > best_score:
            best_score = score
            best_key   = pv_key
    if best_score >= threshold:
        return best_key, best_score
    return None, best_score


def run_apply(threshold):
    if not os.path.exists(_TSV_PATH):
        print(f"Error: {_TSV_PATH} not found — run --translate first.",
              file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(_EN_YAML):
        print(f"Error: {_EN_YAML} not found.", file=sys.stderr)
        sys.exit(1)

    # --- Read TSV ---
    rows = []
    with open(_TSV_PATH, encoding="utf-8") as f:
        f.readline()  # header
        for line in f:
            cols = line.rstrip("\n").split("\t")
            while len(cols) < 4:
                cols.append("")
            rows.append(cols[:4])
    print(f"Read {len(rows)} rows from {_TSV_PATH}.")

    # --- Load EN YAML ---
    with open(_EN_YAML, encoding="utf-8") as f:
        schema = yaml.safe_load(f)

    pv_en = (schema.get("enums", {})
                   .get(_ENUM_KEY, {})
                   .get("permissible_values", {}))

    norm_pv_map = {key: _normalize(key) for key in pv_en}

    # --- Match ---
    pv_fr     = {}
    unmatched = []

    for fr_term, fr_def, en_label, en_def in rows:
        if not fr_term or not en_label:
            continue
        pv_key, score = _best_match(en_label, norm_pv_map, threshold)
        if pv_key:
            entry = {"title": fr_term}
            if fr_def:
                entry["description"] = fr_def
            pv_fr[pv_key] = entry
        else:
            unmatched.append((fr_term, en_label, score))

    print(f"Matched: {len(pv_fr)} / {len(rows)}  |  Unmatched: {len(unmatched)}")

    # --- Attach FR locale extension ---
    if pv_fr:
        fr_locale = {
            "id": _FR_BASE,
            "name": _ENUM_KEY,
            "version": schema.get("version") or "",
            "in_language": "fr",
            "enums": {
                _ENUM_KEY: {"permissible_values": pv_fr}
            },
        }
        schema.setdefault("extensions", {})["locales"] = {
            "tag": "locales",
            "value": {"fr": fr_locale},
        }

    with open(_EN_YAML, "w", encoding="utf-8") as f:
        yaml.dump(schema, f, Dumper=IndentedDumper,
                  default_flow_style=False, sort_keys=False)
    print(f"Updated {_EN_YAML} with {len(pv_fr)} FR locale entries.")

    # --- Report unmatched ---
    if unmatched:
        print(f"\nUnmatched FR terms ({len(unmatched)}):")
        for fr_term, en_label, score in sorted(unmatched,
                                               key=lambda x: x[0].lower()):
            print(f"  [{score:.2f}] {fr_term!r}  →  {en_label!r}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--translate", action="store_true",
                   help=f"Translate FR pages → {_TSV_PATH}")
    p.add_argument("--apply", action="store_true",
                   help=f"Apply {_TSV_PATH} matches → {_EN_YAML}")
    p.add_argument("--threshold", type=float, default=_THRESHOLD,
                   help=f"Fuzzy-match threshold 0–1 (default {_THRESHOLD})")
    args = p.parse_args()

    if not args.translate and not args.apply:
        p.print_help()
        sys.exit(0)

    if args.translate:
        run_translate()
    if args.apply:
        run_apply(args.threshold)


if __name__ == "__main__":
    main()
