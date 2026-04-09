"""Subprocess wrappers around vagrant for cluster lifecycle."""
import shutil
import subprocess
import sys
from config import ClusterConfig


def _run(cmd, cwd, check=True):
    print(f"$ ({cwd}) {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check)


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

    _run(["vagrant", "up", seed_node.name], cwd=cwd)
    if other_nodes:
        _run(["vagrant", "up", *other_nodes], cwd=cwd)

    _run(["vagrant", "ssh", seed_node.name, "-c", "nodetool status"], cwd=cwd, check=False)


def destroy_cluster(config: ClusterConfig) -> None:
    _run(["vagrant", "destroy", "-f"], cwd=config.target_folder, check=False)
