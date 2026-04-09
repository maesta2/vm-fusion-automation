"""Unit tests for the ScyllaDB VMware Fusion automation.

Run from the project root:
    PYTHONPATH=. pytest tests/ -v
These tests verify config validation, Vagrantfile rendering, and the
shape of the generated artifacts. They do NOT actually invoke `vagrant up`.
"""
import os
import re
import sys
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import (
    SCYLLA_VERSIONS, OS_BOXES, COMPAT_MATRIX,
    ClusterConfig, DataVolume, NodeSpec,
    build_nodes, round_robin_az, round_robin_dc, dc_name_list,
    validate_combo, validate_balanced_azs,
)
from vagrantfile_gen import render, write_vagrantfile


# ----- helpers ---------------------------------------------------------------

def make_cfg(node_count=3, version="2025.2", os_key="ubuntu-22.04",
             size_gb=50, disk_count=1, az_labels=None, target=None,
             dc_count=1, dc_labels=None):
    az_labels = az_labels or round_robin_az(node_count)
    if dc_labels is None:
        dc_labels = round_robin_dc(node_count, dc_count)
    data = DataVolume(size_gb=size_gb, disk_count=disk_count, raid_level=0)
    nodes = build_nodes(node_count, az_labels, data, dc_labels=dc_labels)
    return ClusterConfig(
        scylla_version=version,
        os_key=os_key,
        box_name=OS_BOXES[os_key],
        node_count=node_count,
        memory_mb=4096,
        cpus=2,
        target_folder=str(target) if target else "/tmp/x",
        dc_name="dc1",
        cluster_name="scylla-lab",
        seed_ip="192.168.100.10",
        nodes=nodes,
        dc_count=dc_count,
        dc_names=dc_name_list(dc_count),
    )


# ----- config / compatibility -----------------------------------------------

class TestCompatibility:
    def test_all_versions_have_matrix_entry(self):
        for v in SCYLLA_VERSIONS:
            assert v in COMPAT_MATRIX
            assert COMPAT_MATRIX[v], f"{v} has no supported OS"

    def test_supported_combo_passes(self):
        validate_combo("2025.2", "ubuntu-22.04")

    def test_old_release_track_rejected(self):
        # 2020.1 is regex-valid but not in the current matrix — should be
        # rejected with a hint to refresh the matrix.
        with pytest.raises(ValueError, match="release track"):
            validate_combo("2020.1", "ubuntu-22.04")

    def test_invalid_version_string_rejected(self):
        with pytest.raises(ValueError, match="Invalid ScyllaDB version"):
            validate_combo("99.9", "ubuntu-22.04")
        with pytest.raises(ValueError, match="Invalid ScyllaDB version"):
            validate_combo("not-a-version", "ubuntu-22.04")

    def test_full_patch_version_accepted(self):
        # Full major.minor.patch should validate via its major.minor.
        validate_combo("2026.1.1", "ubuntu-22.04")
        validate_combo("2025.2.7", "debian-12")

    def test_unknown_os_rejected(self):
        with pytest.raises(ValueError, match="Unknown OS"):
            validate_combo("2025.2", "windows-11")

    def test_parse_scylla_version(self):
        from config import parse_scylla_version
        assert parse_scylla_version("2026.1") == ("2026.1", "2026.1")
        assert parse_scylla_version("2026.1.1") == ("2026.1", "2026.1.1")
        assert parse_scylla_version("2025.2.7") == ("2025.2", "2025.2.7")
        with pytest.raises(ValueError):
            parse_scylla_version("2026")
        with pytest.raises(ValueError):
            parse_scylla_version("26.1")


# ----- node + AZ assignment --------------------------------------------------

class TestNodeBuilding:
    def test_three_node_round_robin(self):
        labels = round_robin_az(3)
        assert labels == ["az1", "az2", "az3"]

    def test_six_node_round_robin_wraps(self):
        assert round_robin_az(6) == ["az1", "az2", "az3", "az1", "az2", "az3"]

    def test_node_count_bounds(self):
        with pytest.raises(ValueError):
            build_nodes(0, [], DataVolume())
        with pytest.raises(ValueError):
            build_nodes(10, ["az1"] * 10, DataVolume())

    def test_az_labels_must_match_node_count(self):
        with pytest.raises(ValueError):
            build_nodes(3, ["az1", "az2"], DataVolume())

    def test_ips_are_sequential(self):
        nodes = build_nodes(3, round_robin_az(3), DataVolume())
        assert [n.ip for n in nodes] == [
            "192.168.100.10", "192.168.100.11", "192.168.100.12"
        ]
        assert [n.name for n in nodes] == [
            "scylla-node1", "scylla-node2", "scylla-node3"
        ]

    def test_balanced_az_six_nodes(self):
        # 6 nodes → 2 per AZ across az1/az2/az3
        labels = round_robin_az(6)
        from collections import Counter
        assert Counter(labels) == {"az1": 2, "az2": 2, "az3": 2}
        validate_balanced_azs(6)  # must not raise

    def test_unbalanced_node_count_rejected_in_auto_mode(self):
        with pytest.raises(ValueError, match="multiple of 3"):
            validate_balanced_azs(5)
        with pytest.raises(ValueError):
            validate_balanced_azs(4)

    def test_balanced_counts_accepted(self):
        for n in (3, 6, 9):
            validate_balanced_azs(n)

    def test_custom_az_assignment_preserved(self):
        nodes = build_nodes(3, ["az3", "az1", "az2"], DataVolume())
        assert [n.az for n in nodes] == ["az3", "az1", "az2"]


# ----- multi-DC --------------------------------------------------------------

class TestMultiDC:
    def test_dc_name_list_defaults(self):
        assert dc_name_list(1) == ["dc1"]
        assert dc_name_list(2) == ["dc1", "dc2"]
        assert dc_name_list(3) == ["dc1", "dc2", "dc3"]

    def test_dc_name_list_rejects_zero(self):
        with pytest.raises(ValueError):
            dc_name_list(0)

    def test_round_robin_dc_single(self):
        assert round_robin_dc(3, 1) == ["dc1", "dc1", "dc1"]

    def test_round_robin_dc_blocks(self):
        # 6 nodes, 2 DCs → dc1,dc1,dc1,dc2,dc2,dc2
        assert round_robin_dc(6, 2) == ["dc1", "dc1", "dc1", "dc2", "dc2", "dc2"]

    def test_round_robin_dc_rejects_unbalanced(self):
        with pytest.raises(ValueError):
            round_robin_dc(5, 2)

    def test_default_dc_is_dc1(self):
        nodes = build_nodes(3, round_robin_az(3), DataVolume())
        assert all(n.dc == "dc1" for n in nodes)

    def test_multi_dc_build_nodes(self):
        dc_labels = round_robin_dc(6, 2)
        az_labels = round_robin_az(3) + round_robin_az(3)
        nodes = build_nodes(6, az_labels, DataVolume(), dc_labels=dc_labels)
        assert [n.dc for n in nodes] == ["dc1"] * 3 + ["dc2"] * 3
        assert [n.az for n in nodes] == ["az1", "az2", "az3"] * 2

    def test_balanced_azs_multi_dc_accepted(self):
        # 6 nodes, 2 DCs, 3 AZs → 3 nodes per DC, one per rack
        validate_balanced_azs(6, num_azs=3, dc_count=2)

    def test_balanced_azs_multi_dc_rejects_uneven(self):
        # 4 nodes, 2 DCs → 2 per DC, not divisible by 3 AZs
        with pytest.raises(ValueError, match="per DC"):
            validate_balanced_azs(4, num_azs=3, dc_count=2)

    def test_balanced_azs_rejects_node_count_not_divisible_by_dc(self):
        with pytest.raises(ValueError, match="multiple of dc_count"):
            validate_balanced_azs(5, num_azs=3, dc_count=2)

    def test_vagrantfile_emits_per_node_dc(self):
        from vagrantfile_gen import render
        cfg = make_cfg(node_count=6, dc_count=2,
                       az_labels=round_robin_az(3) + round_robin_az(3))
        out = render(cfg)
        # Each node's env block should carry its own DC_NAME
        assert out.count('"DC_NAME"        => "dc1"') == 3
        assert out.count('"DC_NAME"        => "dc2"') == 3


# ----- data volume defaults --------------------------------------------------

class TestDataVolume:
    def test_default_50gb_single_disk(self):
        d = DataVolume()
        assert d.size_gb == 50
        assert d.disk_count == 1
        assert d.raid_level == 0

    def test_per_node_data_is_independent(self):
        nodes = build_nodes(2, ["az1", "az2"], DataVolume(size_gb=200, disk_count=2))
        for n in nodes:
            assert n.data.size_gb == 200
            assert n.data.disk_count == 2
        # Mutating one should not affect another
        nodes[0].data.size_gb = 999
        assert nodes[1].data.size_gb == 200


# ----- vagrantfile rendering -------------------------------------------------

class TestVagrantfileRender:
    def test_box_name_present(self):
        cfg = make_cfg(os_key="rocky-9", version="2025.2")
        out = render(cfg)
        assert 'config.vm.box = "bento/rockylinux-9"' in out

    def test_three_node_blocks(self):
        out = render(make_cfg(node_count=3))
        assert out.count('config.vm.define "scylla-node') == 3
        for n in ("scylla-node1", "scylla-node2", "scylla-node3"):
            assert f'"{n}"' in out

    def test_memory_and_cpus_set(self):
        out = render(make_cfg())
        assert 'v.memory         = 4096' in out
        assert 'v.cpus           = 2' in out

    def test_seed_marker_only_on_node1(self):
        out = render(make_cfg())
        assert out.count('"IS_SEED"        => "1"') == 1
        assert out.count('"IS_SEED"        => "0"') == 2

    def test_data_disk_single_default(self):
        out = render(make_cfg(size_gb=50, disk_count=1))
        assert 'v.vmx["nvme0:0.present"]   = "TRUE"' in out
        assert 'scylla-node1-data0.vmdk' in out
        assert 'nvme0:1' not in out

    def test_data_disk_raid_multi(self):
        out = render(make_cfg(disk_count=3, size_gb=100))
        for idx in (0, 1, 2):
            assert f'v.vmx["nvme0:{idx}.present"]   = "TRUE"' in out
        assert out.count('nvme0:0.present') == 3  # 3 nodes, each with disk index 0

    def test_az_env_per_node(self):
        cfg = make_cfg(az_labels=["az3", "az1", "az2"])
        out = render(cfg)
        assert '"NODE_AZ" => "az3"' in out
        assert '"NODE_AZ" => "az1"' in out
        assert '"NODE_AZ" => "az2"' in out

    def test_provisioner_order(self):
        out = render(make_cfg())
        # disk_setup must be provisioned before setup_scylla within each node block
        for block in out.split('config.vm.define')[1:]:
            disk = block.find("disk_setup.sh")
            scylla = block.find("setup_scylla.sh")
            assert disk != -1 and scylla != -1
            assert disk < scylla, "disk_setup must run before setup_scylla"

    def test_writes_to_disk(self, tmp_path):
        cfg = make_cfg(target=tmp_path)
        path = write_vagrantfile(cfg, create_disks=False)
        assert Path(path).exists()
        text = Path(path).read_text()
        assert "scylla-node1" in text
        assert text.endswith("end\n")

    def test_io_cache_dir_scaffolded(self, tmp_path):
        cfg = make_cfg(target=tmp_path)
        write_vagrantfile(cfg, create_disks=False)
        cache = tmp_path / "io-cache"
        assert cache.is_dir()
        assert (cache / "io.conf").exists()
        assert (cache / "io_properties.yaml").exists()

    def test_file_provisioner_only_on_non_seed(self):
        out = render(make_cfg(node_count=3))
        # Two `file` provisioner lines (io.conf + io_properties.yaml) per
        # non-seed node = 2 × 2 = 4 total. Seed (scylla-node1) has none.
        assert out.count('provision "file"') == 4
        assert out.count('source: "io-cache/io.conf"') == 2
        assert out.count('source: "io-cache/io_properties.yaml"') == 2
        # Seed block must not carry them.
        seed_block = out.split('config.vm.define "scylla-node')[1]
        assert 'io-cache/io.conf' not in seed_block

    def test_file_provisioner_before_setup_scylla(self):
        out = render(make_cfg(node_count=3))
        # On each non-seed block, file provisioners must come BEFORE the
        # setup_scylla shell provisioner.
        blocks = out.split('config.vm.define')[1:]
        for block in blocks[1:]:  # skip seed
            file_idx = block.find('provision "file"')
            shell_idx = block.find('setup_scylla.sh')
            assert file_idx != -1 and shell_idx != -1
            assert file_idx < shell_idx


# ----- shell script smoke tests ---------------------------------------------

class TestShellScripts:
    SCRIPTS = ["disk_setup.sh", "rackdc.sh", "setup_scylla.sh"]

    @pytest.mark.parametrize("name", SCRIPTS)
    def test_script_exists(self, name):
        assert (ROOT / "provision" / name).exists()

    @pytest.mark.parametrize("name", SCRIPTS)
    def test_script_has_strict_mode(self, name):
        text = (ROOT / "provision" / name).read_text()
        assert "set -euo pipefail" in text

    @pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
    @pytest.mark.parametrize("name", SCRIPTS)
    def test_script_syntax_valid(self, name):
        path = ROOT / "provision" / name
        result = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"Syntax error in {name}: {result.stderr}"

    def test_rackdc_written_by_setup_scylla(self):
        # rackdc was moved into setup_scylla.sh so it runs AFTER scylla-conf
        # is installed (dpkg conffile prompt otherwise blocks provisioning).
        text = (ROOT / "provision" / "setup_scylla.sh").read_text()
        assert "dc=${DC_NAME}" in text
        assert "rack=${NODE_AZ}" in text
        assert "/etc/scylla/cassandra-rackdc.properties" in text

    def test_rackdc_script_is_noop(self):
        text = (ROOT / "provision" / "rackdc.sh").read_text()
        assert "deferred to setup_scylla.sh" in text

    def test_setup_scylla_uses_web_installer(self):
        text = (ROOT / "provision" / "setup_scylla.sh").read_text()
        assert "get.scylladb.com/server" in text
        assert "--scylla-version" in text
        assert "scylla_setup --no-raid-setup" in text
        assert "--online-discard 1" in text
        assert "--nic eth1" in text
        # io-setup flag is now dynamic (1 on seed, 0 on non-seed after cache)
        assert '--io-setup "${IO_SETUP_FLAG}"' in text
        assert "--no-fstrim-setup" in text
        assert "--no-rsyslog-setup" in text

    def test_setup_scylla_reuses_cached_io_files(self):
        text = (ROOT / "provision" / "setup_scylla.sh").read_text()
        # Non-seed path copies the seed's iotune output into /etc/scylla.d
        assert "/tmp/io.conf" in text
        assert "/tmp/io_properties.yaml" in text
        assert "/etc/scylla.d/io.conf" in text or 'SCYLLA_D/io.conf' in text
        assert "IO_SETUP_FLAG=0" in text

    def test_setup_scylla_polls_seed(self):
        text = (ROOT / "provision" / "setup_scylla.sh").read_text()
        assert "/dev/tcp/${SEED_IP}/7000" in text

    def test_disk_setup_uses_xfs_and_mdadm(self):
        text = (ROOT / "provision" / "disk_setup.sh").read_text()
        assert "mkfs.xfs" in text
        assert "mdadm --create" in text
        assert "/var/lib/scylla" in text
        assert "noatime" in text


# ----- end-to-end dry run ----------------------------------------------------

class TestDryRun:
    def test_full_render_pipeline(self, tmp_path):
        cfg = make_cfg(
            node_count=3,
            version="2025.3",
            os_key="ubuntu-24.04",
            size_gb=100,
            disk_count=2,
            az_labels=["az1", "az2", "az3"],
            target=tmp_path,
        )
        path = write_vagrantfile(cfg, create_disks=False)
        text = Path(path).read_text()

        # Sanity: box, 3 nodes, RAID disks, AZs, seed marker
        assert "bento/ubuntu-24.04" in text
        assert text.count("scylla-node") >= 6  # name + hostname per node
        assert text.count('nvme0:0.present') == 3
        assert text.count('nvme0:1.present') == 3
        assert "az1" in text and "az2" in text and "az3" in text
        assert text.count('IS_SEED"        => "1"') == 1
