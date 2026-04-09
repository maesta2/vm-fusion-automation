"""Interactive CLI for ScyllaDB cluster automation on VMware Fusion (Apple Silicon)."""
import os
import sys
from pathlib import Path

try:
    import questionary
except ImportError:
    questionary = None

from config import (
    SCYLLA_VERSIONS, OS_BOXES, COMPAT_MATRIX,
    DEFAULT_MEMORY_MB, DEFAULT_CPUS, DEFAULT_DATA_GB, DEFAULT_DISK_COUNT,
    DEFAULT_DC_NAME, DEFAULT_DC_COUNT, DEFAULT_CLUSTER_NAME,
    DEFAULT_SUBNET_PREFIX, DEFAULT_SEED_LAST_OCTET,
    ClusterConfig, DataVolume, build_nodes, round_robin_az, round_robin_dc,
    dc_name_list, parse_scylla_version, validate_combo, validate_balanced_azs,
)
from vagrantfile_gen import write_vagrantfile
from vm_manager import check_prereqs, provision_cluster


def _select(msg, choices, default=None):
    if questionary is None:
        print(f"{msg} {choices} [{default}]: ", end="")
        ans = input().strip() or default
        return ans
    return questionary.select(msg, choices=choices, default=default).ask()


def _text(msg, default):
    if questionary is None:
        print(f"{msg} [{default}]: ", end="")
        return input().strip() or default
    return questionary.text(msg, default=str(default)).ask()


def gather_config() -> ClusterConfig:
    # Version is free-text so user can pin a full major.minor.patch that gets
    # passed verbatim to the Web Installer. Tracks list is shown as a hint.
    tracks_hint = ", ".join(SCYLLA_VERSIONS)
    print(f"Known ScyllaDB release tracks: {tracks_hint}")
    print("  (type full version e.g. 2026.1 or 2026.1.1)")
    version_raw = _text("ScyllaDB version:", SCYLLA_VERSIONS[0])
    major_minor, full_version = parse_scylla_version(version_raw)

    supported_os = sorted(COMPAT_MATRIX[major_minor]) if major_minor in COMPAT_MATRIX else sorted(OS_BOXES.keys())
    print(f"Supported OS for {major_minor}: {', '.join(supported_os)}")
    default_os = supported_os[0] if supported_os else sorted(OS_BOXES.keys())[0]
    os_key = _text("Operating system:", default_os)
    validate_combo(full_version, os_key)

    node_count = int(_text("Number of nodes (1-9):", 3))
    dc_count = int(_text("Number of datacenters (1-3):", DEFAULT_DC_COUNT))
    if dc_count < 1 or dc_count > 3:
        raise ValueError("dc_count must be between 1 and 3")
    if node_count % dc_count != 0:
        raise ValueError(
            f"Node count ({node_count}) must be a multiple of dc_count "
            f"({dc_count}) so each DC has the same number of nodes."
        )
    dc_names = dc_name_list(dc_count)
    memory_mb = int(_text("Memory per node (MB):", DEFAULT_MEMORY_MB))
    cpus = int(_text("vCPUs per node:", DEFAULT_CPUS))
    target_folder = _text("Target folder:", str(Path.home() / "vagrant-scylla-cluster"))

    size_gb = int(_text("Data volume size per disk (GB):", DEFAULT_DATA_GB))
    disk_count = int(_text("Number of data disks per node (RAID0 if >1):", DEFAULT_DISK_COUNT))
    data = DataVolume(size_gb=size_gb, disk_count=disk_count, raid_level=0)

    az_mode = _select(
        "AZ assignment:",
        ["auto round-robin (az1..az3)", "customize per node"],
        default="auto round-robin (az1..az3)",
    )
    dc_labels = round_robin_dc(node_count, dc_count)
    if az_mode.startswith("auto"):
        validate_balanced_azs(node_count, num_azs=3, dc_count=dc_count)
        # Round-robin AZ assignment *within* each DC so each rack gets an
        # equal number of nodes per datacenter.
        per_dc = node_count // dc_count
        az_labels = []
        for d in range(dc_count):
            az_labels.extend(round_robin_az(per_dc))
    else:
        az_labels = []
        for i in range(node_count):
            az_labels.append(
                _text(f"  AZ for scylla-node{i+1} ({dc_labels[i]}):",
                      f"az{(i % 3) + 1}")
            )

    nodes = build_nodes(node_count, az_labels, data, dc_labels=dc_labels)
    seed_ip = f"{DEFAULT_SUBNET_PREFIX}.{DEFAULT_SEED_LAST_OCTET}"

    return ClusterConfig(
        scylla_version=full_version,
        os_key=os_key,
        box_name=OS_BOXES[os_key],
        node_count=node_count,
        memory_mb=memory_mb,
        cpus=cpus,
        target_folder=target_folder,
        dc_name=DEFAULT_DC_NAME,
        cluster_name=DEFAULT_CLUSTER_NAME,
        seed_ip=seed_ip,
        nodes=nodes,
        dc_count=dc_count,
        dc_names=dc_names,
    )


def main() -> int:
    missing = check_prereqs()
    if missing:
        print("Missing prerequisites: " + ", ".join(missing), file=sys.stderr)
        print("Install vagrant + VMware Fusion + 'vagrant plugin install vagrant-vmware-desktop'", file=sys.stderr)
        # Continue anyway if user passes --dry-run
        if "--dry-run" not in sys.argv:
            return 1

    config = gather_config()
    path = write_vagrantfile(config)
    print(f"Wrote {path}")
    print(f"Cluster: {config.node_count} nodes, {config.dc_count} DC(s) "
          f"({', '.join(config.dc_names)}), ScyllaDB {config.scylla_version} "
          f"on {config.os_key}")
    for n in config.nodes:
        print(f"  {n.name}  ip={n.ip}  dc={n.dc}  rack={n.az}  "
              f"data={n.data.disk_count}x{n.data.size_gb}GB")

    if "--dry-run" in sys.argv:
        print("Dry run — skipping vagrant up")
        return 0

    provision_cluster(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
