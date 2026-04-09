"""Refresh the ScyllaDB version↔OS support matrix from the official docs.

Usage:
    python3 tools/refresh_matrix.py          # fetch + print diff, don't write
    python3 tools/refresh_matrix.py --write  # write compat_matrix.json

The script scrapes
https://docs.scylladb.com/stable/versioning/os-support-per-version.html
and emits a JSON override that config.py loads at import time. No code edits
to config.py are required after a refresh — the JSON takes precedence.

Dependencies: `pip install requests beautifulsoup4` (both stdlib-adjacent).
"""
import argparse
import json
import re
import sys
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: install requests + beautifulsoup4:", file=sys.stderr)
    print("  pip3 install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)

DOCS_URL = "https://docs.scylladb.com/stable/versioning/os-support-per-version.html"

# Map the OS column headers we see in the docs to our short keys. The doc
# lumps Rocky/CentOS/RHEL together; we map to our bento-backed short keys.
# (distro family, version string from 2nd header row) -> bento-backed short
# key(s). The docs page uses a two-row header — row 1 has family names
# (Ubuntu, Debian, Rocky/CentOS/RHEL, Amazon Linux) with each family spanning
# multiple unnamed columns, and row 2 has the version numbers (22.04, 24.04,
# 11, 12, 8, 9, 10, 2023). We combine both rows to build a column map.
FAMILY_VERSION_MAP = {
    ("ubuntu", "22.04"):         "ubuntu-22.04",
    ("ubuntu", "24.04"):         "ubuntu-24.04",
    ("debian", "11"):            "debian-11",
    ("debian", "12"):            "debian-12",
    ("rocky/centos/rhel", "8"):  ["rocky-8", "almalinux-8"],
    ("rocky/centos/rhel", "9"):  ["rocky-9", "almalinux-9"],
    ("rocky/centos/rhel", "10"): None,   # no bento arm64 box yet
    ("amazon linux", "2023"):    None,   # no bento arm64 box
}

VERSION_RE = re.compile(r"(\d{4})\.(\d+)")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _cell_has_check(cell) -> bool:
    """Support is indicated by an <i class="icon-check"> tag inside the cell."""
    if cell is None:
        return False
    for i_tag in cell.find_all("i"):
        classes = i_tag.get("class") or []
        if any("check" in c for c in classes):
            return True
    txt = _norm(cell.get_text())
    return txt in ("✓", "yes", "y")


def _build_column_map(thead) -> dict:
    """Walk the two header rows and build {col_index -> short_os_key(s)}.

    Row 1 carries distro family names with empty cells where the family spans
    further columns; we carry the last non-empty family name forward. Row 2
    carries the per-column version number. Together they form the key we look
    up in FAMILY_VERSION_MAP.
    """
    header_rows = thead.find_all("tr")
    if len(header_rows) < 2:
        return {}

    row1_cells = header_rows[0].find_all(["th", "td"])
    row2_cells = header_rows[1].find_all(["th", "td"])

    # Carry the last non-empty family name forward across empty cells.
    families = []
    current = ""
    for c in row1_cells:
        t = _norm(c.get_text())
        if t:
            current = t
        families.append(current)

    col_to_os = {}
    for idx, ver_cell in enumerate(row2_cells):
        if idx == 0:
            continue  # column 0 = version label
        version = _norm(ver_cell.get_text())
        family = families[idx] if idx < len(families) else ""
        if not version or not family:
            continue
        # Normalize "ubuntu" family prefix etc.
        family_key = family
        mapped = FAMILY_VERSION_MAP.get((family_key, version))
        if mapped is None:
            # Allow slight variation, e.g. "rocky / centos / rhel"
            family_key = family.replace(" ", "")
            for (fam, ver), m in FAMILY_VERSION_MAP.items():
                if fam.replace(" ", "") == family_key and ver == version:
                    mapped = m
                    break
        if mapped is not None:
            col_to_os[idx] = mapped
    return col_to_os


def fetch_matrix() -> dict:
    print(f"Fetching {DOCS_URL}...")
    r = requests.get(DOCS_URL, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    matrix = {}
    versions_found = []

    for table in soup.find_all("table"):
        thead = table.find("thead")
        if thead is None:
            continue
        col_to_os = _build_column_map(thead)
        if not col_to_os:
            continue

        tbody = table.find("tbody") or table
        for row in tbody.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            ver_cell = _norm(cells[0].get_text())
            m = VERSION_RE.search(ver_cell)
            if not m:
                continue
            major_minor = f"{m.group(1)}.{m.group(2)}"
            if major_minor not in versions_found:
                versions_found.append(major_minor)
            os_set = matrix.setdefault(major_minor, set())
            for idx, short in col_to_os.items():
                if idx >= len(cells):
                    continue
                if not _cell_has_check(cells[idx]):
                    continue
                if isinstance(short, list):
                    os_set.update(short)
                else:
                    os_set.add(short)

    if not matrix:
        raise RuntimeError("Failed to parse any version rows from docs page")

    # Sort versions newest-first.
    versions_sorted = sorted(
        matrix.keys(),
        key=lambda v: tuple(int(x) for x in v.split(".")),
        reverse=True,
    )
    return {
        "source": DOCS_URL,
        "versions": versions_sorted,
        "os_boxes": {
            "ubuntu-24.04": "bento/ubuntu-24.04",
            "ubuntu-22.04": "bento/ubuntu-22.04",
            "debian-12":    "bento/debian-12",
            "debian-11":    "bento/debian-11",
            "rocky-9":      "bento/rockylinux-9",
            "rocky-8":      "bento/rockylinux-8",
            "almalinux-9":  "bento/almalinux-9",
            "almalinux-8":  "bento/almalinux-8",
        },
        "matrix": {v: sorted(matrix[v]) for v in versions_sorted},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="write compat_matrix.json next to config.py")
    args = ap.parse_args()

    data = fetch_matrix()
    out_path = Path(__file__).resolve().parent.parent / "compat_matrix.json"

    print(f"Parsed {len(data['matrix'])} release tracks:")
    for v in data["versions"]:
        print(f"  {v}: {', '.join(data['matrix'][v])}")

    if args.write:
        out_path.write_text(json.dumps(data, indent=2) + "\n")
        print(f"Wrote {out_path}")
    else:
        print(f"(dry run — pass --write to save to {out_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
