"""Constants and dataclasses for the ScyllaDB VMware Fusion cluster automation.

The compatibility matrix below tracks
https://docs.scylladb.com/stable/versioning/os-support-per-version.html

It can be regenerated from the live docs via `python3 tools/refresh_matrix.py`
(see that script for details). This file is the source of truth the CLI reads
from; the refresh script only writes to this file.
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# Known major.minor ScyllaDB release tracks. The user may type any full
# major.minor.patch (e.g. "2026.1.1") at the CLI prompt — this list is only
# used as the suggestion / default and to gate (major.minor) against the
# compatibility matrix.
SCYLLA_VERSIONS = ["2026.1", "2025.4", "2025.3", "2025.2", "2025.1", "2024.2", "2024.1"]

# Short OS key -> bento/* box name. bento ships official arm64 images for
# all of these on Apple Silicon.
OS_BOXES = {
    "ubuntu-24.04":  "bento/ubuntu-24.04",
    "ubuntu-22.04":  "bento/ubuntu-22.04",
    "debian-12":     "bento/debian-12",
    "debian-11":     "bento/debian-11",
    "rocky-9":       "bento/rockylinux-9",
    "rocky-8":       "bento/rockylinux-8",
    "almalinux-9":   "bento/almalinux-9",
    "almalinux-8":   "bento/almalinux-8",
}

# (scylla major.minor) -> set of supported os keys.
# Source: docs.scylladb.com/stable/versioning/os-support-per-version.html
# (Note: Amazon Linux 2023 is listed upstream but no bento/* box exists for it
# on arm64, so it's omitted here.)
_ALL_MODERN_OS = {
    "ubuntu-24.04", "ubuntu-22.04",
    "debian-12", "debian-11",
    "rocky-9", "rocky-8",
    "almalinux-9", "almalinux-8",
}

COMPAT_MATRIX = {
    "2026.1": set(_ALL_MODERN_OS),
    "2025.4": set(_ALL_MODERN_OS),
    "2025.3": set(_ALL_MODERN_OS),
    "2025.2": set(_ALL_MODERN_OS),
    "2025.1": set(_ALL_MODERN_OS),
    "2024.2": set(_ALL_MODERN_OS),
    "2024.1": set(_ALL_MODERN_OS),  # applies to 2024.1.9 and later
}

# Optional override file — `tools/refresh_matrix.py` writes a JSON snapshot
# here after fetching the docs. If present, it is merged into the in-memory
# matrix at import time, so regenerating the matrix does NOT require editing
# this file.
_OVERRIDE_PATH = Path(__file__).resolve().parent / "compat_matrix.json"
if _OVERRIDE_PATH.exists():
    try:
        _data = json.loads(_OVERRIDE_PATH.read_text())
        SCYLLA_VERSIONS = list(_data.get("versions", SCYLLA_VERSIONS))
        OS_BOXES.update(_data.get("os_boxes", {}))
        COMPAT_MATRIX = {k: set(v) for k, v in _data.get("matrix", {}).items()}
    except Exception as _e:  # noqa: BLE001
        print(f"[config] WARNING: failed to load {_OVERRIDE_PATH}: {_e}")


_VERSION_RE = re.compile(r"^(\d{4})\.(\d+)(?:\.(\d+))?$")


def parse_scylla_version(raw: str) -> Tuple[str, str]:
    """Parse a user-supplied ScyllaDB version string.

    Accepts either `YYYY.M` (e.g. `2026.1`) or `YYYY.M.P` (e.g. `2026.1.1`).
    Returns a tuple of (major_minor, full_version), where full_version is
    what should be passed to the Web Installer's `--scylla-version` flag
    (falls back to major.minor when no patch was supplied).
    """
    raw = raw.strip()
    m = _VERSION_RE.match(raw)
    if not m:
        raise ValueError(
            f"Invalid ScyllaDB version '{raw}'. "
            f"Expected YYYY.M or YYYY.M.P (e.g. 2026.1 or 2026.1.1)."
        )
    year, minor, patch = m.groups()
    major_minor = f"{year}.{minor}"
    full = raw if patch is not None else major_minor
    return major_minor, full

DEFAULT_SUBNET_PREFIX = "192.168.100"
DEFAULT_SEED_LAST_OCTET = 10
DEFAULT_MEMORY_MB = 4096
DEFAULT_CPUS = 2
DEFAULT_DATA_GB = 50
DEFAULT_DISK_COUNT = 1
DEFAULT_RAID_LEVEL = 0
DEFAULT_DC_NAME = "dc1"
DEFAULT_DC_COUNT = 1
DEFAULT_CLUSTER_NAME = "scylla-lab"


def dc_name_list(count: int) -> List[str]:
    """Default DC names: dc1, dc2, dc3, ..."""
    if count < 1:
        raise ValueError("dc_count must be >= 1")
    return [f"dc{i + 1}" for i in range(count)]


@dataclass
class DataVolume:
    size_gb: int = DEFAULT_DATA_GB
    disk_count: int = DEFAULT_DISK_COUNT
    raid_level: int = DEFAULT_RAID_LEVEL  # 0 only for now


@dataclass
class NodeSpec:
    name: str
    ip: str
    az: str                      # rack, e.g. "az1"
    dc: str = DEFAULT_DC_NAME    # datacenter, e.g. "dc1"
    data: DataVolume = field(default_factory=DataVolume)


@dataclass
class ClusterConfig:
    scylla_version: str
    os_key: str
    box_name: str
    node_count: int
    memory_mb: int
    cpus: int
    target_folder: str
    dc_name: str                 # primary / default DC name (dc1)
    cluster_name: str
    seed_ip: str
    nodes: List[NodeSpec]
    dc_count: int = DEFAULT_DC_COUNT
    dc_names: List[str] = field(default_factory=lambda: [DEFAULT_DC_NAME])


def validate_combo(version: str, os_key: str) -> None:
    """Validate a (version, os) pair.

    `version` may be either a bare major.minor ("2026.1") or a full
    major.minor.patch ("2026.1.1"); only the major.minor is used to look up
    the compatibility matrix.
    """
    major_minor, _full = parse_scylla_version(version)
    if major_minor not in COMPAT_MATRIX:
        known = ", ".join(sorted(COMPAT_MATRIX.keys(), reverse=True))
        raise ValueError(
            f"Unknown ScyllaDB release track: {major_minor}. "
            f"Known tracks: {known}. Run `python3 tools/refresh_matrix.py` "
            f"to refresh from docs.scylladb.com."
        )
    if os_key not in OS_BOXES:
        supported = ", ".join(sorted(OS_BOXES.keys()))
        raise ValueError(f"Unknown OS: {os_key}. Supported: {supported}")
    if os_key not in COMPAT_MATRIX[major_minor]:
        supported = ", ".join(sorted(COMPAT_MATRIX[major_minor]))
        raise ValueError(
            f"ScyllaDB {major_minor} is not supported on {os_key}. "
            f"Supported: {supported}"
        )


def build_nodes(node_count: int,
                az_labels: List[str],
                data: DataVolume,
                dc_labels: List[str] = None,
                subnet_prefix: str = DEFAULT_SUBNET_PREFIX,
                seed_last_octet: int = DEFAULT_SEED_LAST_OCTET) -> List[NodeSpec]:
    if node_count < 1 or node_count > 9:
        raise ValueError("node_count must be between 1 and 9")
    if len(az_labels) != node_count:
        raise ValueError("az_labels length must match node_count")
    if dc_labels is None:
        dc_labels = [DEFAULT_DC_NAME] * node_count
    if len(dc_labels) != node_count:
        raise ValueError("dc_labels length must match node_count")
    return [
        NodeSpec(
            name=f"scylla-node{i+1}",
            ip=f"{subnet_prefix}.{seed_last_octet + i}",
            az=az_labels[i],
            dc=dc_labels[i],
            data=DataVolume(data.size_gb, data.disk_count, data.raid_level),
        )
        for i in range(node_count)
    ]


def round_robin_az(node_count: int, num_azs: int = 3) -> List[str]:
    return [f"az{(i % num_azs) + 1}" for i in range(node_count)]


def round_robin_dc(node_count: int, dc_count: int = 1) -> List[str]:
    """Distribute nodes across DCs in contiguous blocks: with 6 nodes and
    2 DCs → [dc1, dc1, dc1, dc2, dc2, dc2]. Block layout (not interleaved)
    makes it easy to pick a per-DC seed as the first node of each block."""
    if dc_count < 1:
        raise ValueError("dc_count must be >= 1")
    if node_count % dc_count != 0:
        raise ValueError(
            f"node_count ({node_count}) must be a multiple of "
            f"dc_count ({dc_count}) for balanced placement"
        )
    per_dc = node_count // dc_count
    return [f"dc{(i // per_dc) + 1}" for i in range(node_count)]


def validate_balanced_azs(node_count: int, num_azs: int = 3,
                          dc_count: int = 1) -> None:
    """Auto AZ mode requires nodes_per_dc to divide evenly across AZs so each
    rack within each DC holds the same number of nodes (required for
    NetworkTopologyStrategy with RF=num_azs per DC to place one replica
    per rack)."""
    if dc_count < 1:
        raise ValueError("dc_count must be >= 1")
    if node_count % dc_count != 0:
        raise ValueError(
            f"node_count ({node_count}) must be a multiple of dc_count "
            f"({dc_count}) so each DC has the same number of nodes."
        )
    nodes_per_dc = node_count // dc_count
    if nodes_per_dc % num_azs != 0:
        raise ValueError(
            f"Auto AZ assignment requires nodes_per_dc to be a multiple of "
            f"{num_azs} (got {nodes_per_dc} per DC, {node_count} total across "
            f"{dc_count} DCs). Use a balanced layout (e.g. 3, 6, 9 per DC) "
            f"or switch to 'customize per node'."
        )
