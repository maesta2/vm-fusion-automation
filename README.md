# vmware-fusion-automation

Interactive CLI that provisions a ScyllaDB cluster on **VMware Fusion** running on **Apple Silicon (arm64)** Macs. It generates a `Vagrantfile`, prepares per-node XFS data volumes (optionally md RAID0), installs ScyllaDB via the official Web Installer, and wires rack/DC topology for `NetworkTopologyStrategy`.

## Features

- **Free-text ScyllaDB version**: type any `YYYY.M` or `YYYY.M.P` (e.g. `2026.1` or `2026.1.1`) — the full patch version is passed verbatim to the Web Installer's `--scylla-version` flag. Known release tracks: `2026.1`, `2025.4`, `2025.3`, `2025.2`, `2025.1`, `2024.2`, `2024.1`
- **Free-text OS key**: type any OS short key (e.g. `ubuntu-22.04`, `debian-11`, `rocky-8`, `almalinux-9`); validated against the compatibility matrix
- **Live compatibility matrix**: sourced from [docs.scylladb.com os-support-per-version](https://docs.scylladb.com/stable/versioning/os-support-per-version.html); refresh with `python3 tools/refresh_matrix.py --write` to regenerate `compat_matrix.json` from the live docs page
- **Multi-OS**: Ubuntu 24.04 / 22.04, Debian 12 / 11, Rocky 9 / 8, AlmaLinux 9 / 8 (all arm64 via `bento/*` boxes)
- **Configurable node count** (1–9)
- **Multi-datacenter**: configurable DC count (default 1, up to 3) with default names `dc1`, `dc2`, `dc3`; nodes are distributed in contiguous blocks across DCs
- **AZ-aware placement per DC**: auto round-robin across `az1/az2/az3` within each datacenter (balanced — requires `nodes_per_dc % 3 == 0`), or per-node custom labels
- **Dedicated data volume** for `/var/lib/scylla`: XFS, default 50 GB, configurable size and disk count (md RAID0 when > 1 disk)
- **Compatibility matrix gate** rejects unsupported `(version, os)` combinations before generating the Vagrantfile
- **Robust seed-ready wait**: non-seed nodes poll the seed's gossip port (7000) instead of sleeping
- **scylla.yaml patched via PyYAML** (safe against commented-out keys)
- **NVMe controller with pre-created VMDKs**: data disks are created up-front with `vmware-vdiskmanager` and attached via `nvme0:N` vmx keys, working around a `vagrant-vmware-desktop 3.0.5` crash in the `config.vm.disk` API on arm64 bento boxes
- **Non-interactive dpkg**: rackdc file is written *after* `scylla-conf` installs, and an `apt.conf.d` drop-in forces `--force-confdef --force-confnew` so provisioning never stalls on a conffile prompt

## Prerequisites

On the host:

- VMware Fusion 13+ (Apple Silicon build)
- `vagrant-vmware-utility` cask (`brew install --cask vagrant-vmware-utility`) — required for the plugin's TLS client cert
- Vagrant
- `vagrant-vmware-desktop` plugin (`vagrant plugin install vagrant-vmware-desktop`)
- `vmrun` on PATH: `export PATH="/Applications/VMware Fusion.app/Contents/Public:$PATH"`
- Python 3.9+
- `pip install -r requirements.txt`
- `pip install requests beautifulsoup4` — only if you want to run `tools/refresh_matrix.py` to regenerate the compatibility matrix from the live docs page

## Project layout

```
vmware-fusion-automation/
├── main.py                   # Interactive CLI entry point
├── config.py                 # Versions, OS, compatibility matrix, dataclasses
├── vagrantfile_gen.py        # Vagrantfile renderer
├── vm_manager.py             # vagrant up/destroy orchestration
├── tools/
│   └── refresh_matrix.py     # Scrape docs.scylladb.com → compat_matrix.json
├── compat_matrix.json        # (optional) live snapshot, overrides config.py
├── provision/
│   ├── disk_setup.sh         # XFS / md RAID0 → /var/lib/scylla
│   ├── rackdc.sh             # per-node dc / rack
│   └── setup_scylla.sh       # Web Installer + scylla.yaml + seed poll
├── tests/
│   └── test_automation.py    # 53 unit tests
├── requirements.txt
└── README.md
```

## Usage

```bash
# Dry run — render the Vagrantfile, skip vagrant up
python3 main.py --dry-run

# Full provision
python3 main.py
```

You'll be prompted for:

| Prompt              | Notes                                                    |
| ------------------- | -------------------------------------------------------- |
| ScyllaDB version    | free text; `YYYY.M` or `YYYY.M.P`; passed as-is to the Web Installer |
| OS                  | free text; supported list for the chosen release track is shown as a hint and validated against the compat matrix |
| Node count          | 1–9                                                      |
| DC count            | default 1, up to 3 (`dc1`, `dc2`, `dc3`); `node_count` must be a multiple of `dc_count` |
| Memory per node     | default 4096 MB                                          |
| vCPUs per node      | default 2                                                |
| Target folder       | where the Vagrantfile and VM state live                  |
| Data volume size    | GB per disk, default 50                                  |
| Disks per node      | 1 = single XFS, > 1 = md RAID0                           |
| AZ assignment       | auto round-robin (balanced) or customize per node        |

### Example cluster layouts

Single-DC (default, `dc_count=1`):

| Nodes | AZ auto result               | Balanced? |
| ----: | ---------------------------- | :-------: |
|     3 | az1, az2, az3                | ✅        |
|     6 | az1, az2, az3, az1, az2, az3 | ✅        |
|     9 | az1..az3 repeated 3×          | ✅        |
|  4, 5 | rejected in auto mode         | ❌ use custom |

Multi-DC (`dc_count=2`, contiguous blocks):

| Nodes | DC layout              | Per-DC AZs          | Balanced? |
| ----: | ---------------------- | ------------------- | :-------: |
|     6 | 3×dc1, 3×dc2           | az1/az2/az3 per DC  | ✅        |
|     2 | 1×dc1, 1×dc2           | one AZ per DC only  | ❌ (use custom — per-DC count 1 < 3 AZs) |

Multi-DC (`dc_count=3`, 9 nodes → 3 per DC, one per rack per DC) is also balanced.

## Running tests

```bash
pip3 install -r requirements.txt
PYTHONPATH=. python3 -m pytest tests/ -v
```

53 tests cover:

- compatibility matrix (supported / rejected combos, old-track rejection, invalid version strings)
- free-text version parsing (`parse_scylla_version` for `YYYY.M` and `YYYY.M.P`)
- node building and AZ round-robin
- balanced-AZ guard per DC (`nodes_per_dc % num_azs == 0`)
- multi-DC placement (`round_robin_dc`, per-node `dc` field, Vagrantfile `DC_NAME` env)
- data-volume defaults and per-node independence
- Vagrantfile rendering (arm64, seed marker, multi-disk vmx keys, provisioner order, per-node DC_NAME)
- shell script strict mode + `bash -n` syntax checks
- full dry-run pipeline

## Refreshing the compatibility matrix

The version/OS compatibility matrix is sourced from
[docs.scylladb.com/stable/versioning/os-support-per-version.html](https://docs.scylladb.com/stable/versioning/os-support-per-version.html).
A fallback copy lives in `config.py` and an optional override snapshot can be
generated from the live docs page:

```bash
pip3 install requests beautifulsoup4
python3 tools/refresh_matrix.py           # preview (dry run)
python3 tools/refresh_matrix.py --write   # write compat_matrix.json
```

On import, `config.py` loads `compat_matrix.json` if present and merges it
over the built-in defaults — no code edits needed after a refresh. The script
parses the doc page's two-row header (distro family + version number) and
detects supported cells via the `<i class="icon-check">` marker. Rocky 10 and
Amazon Linux 2023 are intentionally dropped from the output because there are
no arm64 `bento/*` boxes for them. Rocky/CentOS/RHEL columns are mirrored to
both `rocky-*` and `almalinux-*` short keys.

## Cluster verification

After `vagrant up` completes:

```bash
vagrant ssh scylla-node1 -c "nodetool status"
```

Expected: all nodes `UN`, `Rack` column reflects the `az1/az2/az3` labels assigned at prompt time.

```bash
vagrant ssh scylla-node1 -c "scylla --version"
```

Confirms the selected ScyllaDB version was installed.

## Teardown

```bash
cd <target-folder>
vagrant destroy -f
```

## Design decisions

- **Regular `scylla_setup` with VM-friendly flags**: `scylla_setup --no-raid-setup --online-discard 1 --nic eth1 --io-setup 1 --no-fstrim-setup --no-rsyslog-setup`. RAID setup is skipped because `disk_setup.sh` already built the md0/XFS volume; `--nic eth1` targets the Vagrant `private_network` interface; `--io-setup 1` runs `iotune` against the prepared data volume; rsyslog and fstrim tuning are disabled for the lab environment. Dev mode is no longer used
- **GossipingPropertyFileSnitch**: per-node `cassandra-rackdc.properties` is written *after* `scylla-conf` is installed but *before* first `scylla-server` start, so rack is registered on bootstrap and dpkg never hits a conffile prompt
- **Seed first, then others**: `vagrant up scylla-node1` runs before the rest so gossip is reachable when they join. In multi-DC mode, the same global seed (dc1's first node) is used for all nodes — sufficient for lab clusters since gossip propagates once connectivity is established. For production, list one seed per DC
- **Per-node DC env**: the Vagrantfile embeds each node's own `DC_NAME` in its `setup_scylla.sh` env block, so `GossipingPropertyFileSnitch` registers the correct datacenter on every node without a shared global
- **Data disk before Scylla install**: `disk_setup.sh` prepares `/var/lib/scylla` so the Scylla package lays its data on the prepared XFS volume from the start
- **NVMe over SCSI on arm64**: the `lsilogic` SCSI controller is x86-only on VMware Fusion for Apple Silicon, so data disks are attached via an NVMe controller (`nvme0.present = TRUE` + `nvme0:N.fileName`) instead of the SCSI/SATA APIs
- **Pre-created VMDKs via `vmware-vdiskmanager`**: Vagrant's `config.vm.disk` API crashes in `vagrant-vmware-desktop 3.0.5` on arm64 bento boxes (`disk.rb:129 no implicit conversion of nil into String`); the generator now shells out to `vmware-vdiskmanager -c` before `vagrant up` and references each VMDK by absolute path in the vmx block
- **udev settle after mdadm**: `mdadm --create` returns before udev finishes creating `/dev/md0`; `disk_setup.sh` runs `udevadm settle` + `mdadm --wait` + a bounded retry loop before `mkfs.xfs` to avoid the race
- **Idempotent disk setup**: `disk_setup.sh` exits early if `/var/lib/scylla` is already mounted as XFS, and strips any stale fstab entry before re-writing, so `vagrant provision` is safe to re-run

## Debug history (fixes applied during on-hardware validation)

These are the issues hit and resolved while bringing up the first real 3-node cluster on an M5 MacBook Pro. Useful context if you hit anything similar.

1. **`Missing prerequisites: vmrun`** — Fusion's `vmrun` binary isn't on PATH by default on Apple Silicon. Fix: `export PATH="/Applications/VMware Fusion.app/Contents/Public:$PATH"` (add to `~/.zshrc`).
2. **`vagrant-utility.client.crt missing`** — the `vagrant-vmware-desktop` plugin needs the companion utility cask. Fix: `brew install --cask vagrant-vmware-utility`.
3. **`path for shell provisioner does not exist`** — the generator only wrote the `Vagrantfile`, not the `provision/*.sh` files next to it. Fix: `vagrantfile_gen.py` now copies `provision/` into the target folder alongside the Vagrantfile.
4. **`lsilogic is not valid device type for scsi1`** — SCSI's `lsilogic` controller is x86-only on Fusion for Apple Silicon. Fix: switched data disks to an NVMe controller (`nvme0.present = TRUE`, `nvme0:N.*` vmx keys).
5. **`vmrun cannot find the virtual disk ...`** — pointed at Vagrant's `config.vm.disk` API, which on `vagrant-vmware-desktop 3.0.5` + arm64 bento crashes with `disk.rb:129 no implicit conversion of nil into String`. Fix: pre-create VMDKs with `vmware-vdiskmanager -c -s <GB>GB -a lsilogic -t 0` and attach via absolute-path vmx keys.
6. **`disk_setup.sh` exited non-zero right after `mdadm: array /dev/md0 started.`** — `mkfs.xfs` raced the udev node creation for `/dev/md0`. Fix: `udevadm settle` + `mdadm --wait` + bounded retry loop before `mkfs.xfs`; also defensive `{ mdadm --detail --scan >> ...; } || true` so pipefail can't trip on the conf-append step.
7. **`no data disks detected — skipping`** — a `--dry-run` re-regenerated the Vagrantfile after `vagrant destroy` without re-running `vmware-vdiskmanager`, leaving the vmx block pointing at deleted VMDK files. Fix: `_create_vmdks()` is now always called by `write_vagrantfile()` regardless of dry-run status.
8. **`dpkg: error processing package scylla-conf ... end of file on stdin at conffile prompt`** — the pre-install `rackdc.sh` provisioner wrote `/etc/scylla/cassandra-rackdc.properties` before `scylla-conf` installed, so dpkg hit a conffile conflict and stalled on closed stdin. Fix: `rackdc.sh` is now a no-op kept for Vagrantfile compatibility; the rackdc file is written inside `setup_scylla.sh` *after* package install but *before* `systemctl enable --now scylla-server`. An `/etc/apt/apt.conf.d/99-noninteractive-scylla` drop-in additionally forces `--force-confdef --force-confnew` as belt-and-braces.
9. **`NODE_AZ: NODE_AZ required`** — after folding rackdc into `setup_scylla.sh`, the Vagrantfile still only passed `DC_NAME`/`NODE_AZ` to the (now no-op) `rackdc.sh` provisioner. Fix: `vagrantfile_gen.py` now injects `DC_NAME` and `NODE_AZ` into the `setup_scylla.sh` env hash too.
10. **`refresh_matrix.py`: RuntimeError: Failed to parse any version rows from docs page** — the first draft of the scraper only looked at a single header row and searched for a text `✓` character in each cell. The actual docs page uses a two-row header (distro family in row 1 with cells spanning, version number in row 2) and marks support with an `<i class="icon-check">` tag. Fix: `_build_column_map()` now walks both header rows and carries the last non-empty family name forward across empty cells, and `_cell_has_check()` looks for any `<i>` tag whose class list contains `check`.

## Out of scope

- Docker-based alternatives
- TLS / Alternator / monitoring stack
- Full production `scylla_setup` tuning (RAID, fstrim, rsyslog are skipped for the lab)
- Cross-DC gossip tuning beyond the shared single-seed layout
