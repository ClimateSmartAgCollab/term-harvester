"""Zenodo record API fetch utilities for term_harvester.py.

Fetches file download URLs from the Zenodo /api/records/{id} endpoint and
downloads without custom browser headers (Zenodo's CDN returns HTTP 403 when
a browser User-Agent is present, but allows plain script requests).

Public API used by other source modules:
    is_zenodo_record_url(url)   -> bool
    to_zenodo_api_url(url)      -> str   (normalise to API record URL)
    fetch_zenodo_file(record_url, file_format=None, file_pattern=None)
                                -> (bytes, filename, ext)
"""

import json
import os
import re
import urllib.request


_ZENODO_RECORD_RE = re.compile(
    r'^https?://zenodo\.org/(?:api/)?records/(\d+)',
    re.IGNORECASE,
)


def is_zenodo_record_url(url):
    """Return True if url is a Zenodo record URL (bare record or API record)."""
    return bool(_ZENODO_RECORD_RE.match(url.split('?')[0].rstrip('/')))


def to_zenodo_api_url(url):
    """Normalise any Zenodo record or file URL to the API record endpoint.

    Accepts:
        https://zenodo.org/records/18421449
        https://zenodo.org/records/18421449/files/foo.pdf?download=1
        https://zenodo.org/api/records/18421449
    Returns:
        https://zenodo.org/api/records/18421449
    """
    m = _ZENODO_RECORD_RE.match(url)
    if m:
        return f"https://zenodo.org/api/records/{m.group(1)}"
    return url


def fetch_zenodo_file(record_url, file_format=None, file_pattern=None):
    """Download a file from a Zenodo record via the REST API.

    Calls GET /api/records/{id} to list files, selects the first file
    matching *file_format* (extension, case-insensitive) or *file_pattern*
    (regex on filename), then downloads via the file's ``self`` link.
    No custom headers are sent — Zenodo's CDN blocks browser User-Agents
    on the content endpoint.

    Args:
        record_url:   any Zenodo record or file URL; normalised internally
        file_format:  extension to match, e.g. ``'pdf'``
        file_pattern: compiled or string regex applied to filename when
                      file_format is not given

    Returns:
        ``(data_bytes, filename, ext)``

    Raises:
        ValueError         if the record has no files or no match found
        urllib.error.URLError  on network errors
    """
    api_url = to_zenodo_api_url(record_url)
    print(f"  Fetching Zenodo record {api_url} ...")
    with urllib.request.urlopen(api_url, timeout=15) as r:
        meta = json.load(r)

    files = meta.get('files', [])
    if not files:
        raise ValueError(f"No files found in Zenodo record {api_url}")

    if file_format:
        candidates = [f for f in files
                      if f['key'].lower().endswith('.' + file_format.lower())]
    elif file_pattern:
        pat = re.compile(file_pattern, re.IGNORECASE) if isinstance(file_pattern, str) else file_pattern
        candidates = [f for f in files if pat.search(f['key'])]
    else:
        candidates = files

    if not candidates:
        available = ', '.join(f['key'] for f in files)
        spec = f'.{file_format}' if file_format else repr(file_pattern)
        raise ValueError(
            f"No {spec} file in Zenodo record. Available: {available}"
        )

    target = candidates[0]
    download_url = target['links']['self']
    filename = target['key']
    _, dot_ext = os.path.splitext(filename)
    ext = dot_ext.lstrip('.').lower() or 'bin'

    print(f"  Downloading {filename} ...")
    with urllib.request.urlopen(download_url, timeout=60) as r:
        data = r.read()

    return data, filename, ext
