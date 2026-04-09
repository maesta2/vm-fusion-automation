"""Subprocess wrappers around vagrant for cluster lifecycle."""
import shutil
import subprocess
import sys
from pathlib import Path
from config import ClusterConfig


def _run(cmd, cwd, check=True):
    print(f"$ ({cwd}) {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check)


def _capture(cmd, cwd) -> str:
    print(f"$ ({cwd}) {' '.join(cmd)}", flush=True)
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
    return res.stdout


IO_FILES = ("io.conf", "io_properties.yaml")


def _extract_io_files(seed_name: str, cwd: str) -> None:
    """After the seed's scylla_setup has generated /etc/scylla.d/io.conf and
    /etc/scylla.d/io_properties.yaml, copy those files from the seed VM into
    the host-side io-cache/ directory so the non-seed nodes can consume them
    via their Vagrant `file` provisioners. Uses `vagrant ssh -c "sudo cat"`
    so we don't need any scp plumbing."""
    cache = Path(cwd) / "io-cache"
    cache.mkdir(parents=True, exist_ok=True)
    for name in IO_FILES:
        remote = f"/etc/scylla.d/{name}"
        print(f"[io-cache] extracting {remote} from {seed_name}", flush=True)
        content = _capture(
            ["vagrant", "ssh", seed_name, "-c", f"sudo cat {remote}"],
            cwd=cwd,
        )
        (cache / name).write_text(content)
        print(f"[io-cache] wrote {cache / name} ({len(content)} bytes)", flush=True)


def check_prereqs() -> list:
    missing = []
    for tool in ("vagrant", "vmrun"):
        if shutil.which(tool) is None:
            missing.append(tool)
    try:
        out = subprocess.run(
            ["vagrant", "plugin", "list"],
            capture_output=True, text=True, check=True,
        ).stdout
        if "vagrant-vmware-desktop" not in out:
            missing.append("vagrant-vmware-desktop plugin")
    except (FileNotFoundError, subprocess.CalledProcessError):
        missing.append("vagrant-vmware-desktop plugin")
    return missing


def provision_cluster(config: ClusterConfig) -> None:
    cwd = config.target_folder
    seed_node = next(n for n in config.nodes if n.ip == config.seed_ip)
    other_nodes = [n.name for n in config.nodes if n.name != seed_node.name]

    # 1. Bring up the seed — this runs scylla_setup with --io-setup 1 and
    #    produces /etc/scylla.d/io.conf + io_properties.yaml on the seed.
    _run(["vagrant", "up", seed_node.name], cwd=cwd)

    # 2. Pull those two files out of the seed and drop them into the
    #    host-side io-cache/ dir so the non-seed nodes' `file` provisioners
    #    can inject them before their setup_scylla.sh runs.
    if other_nodes:
        _extract_io_files(seed_node.name, cwd=cwd)
        _run(["vagrant", "up", *other_nodes], cwd=cwd)

    _run(["vagrant", "ssh", seed_node.name, "-c", "nodetool status"], cwd=cwd, check=False)


def destroy_cluster(config: ClusterConfig) -> None:
    _run(["vagrant", "destroy", "-f"], cwd=config.target_folder, check=False)
