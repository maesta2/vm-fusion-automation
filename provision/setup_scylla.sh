#!/usr/bin/env bash
# Install ScyllaDB via the Web Installer, write rackdc AFTER package install
# (to avoid dpkg conffile prompt), patch scylla.yaml, and start scylla-server.
set -euo pipefail

: "${SCYLLA_VERSION:?SCYLLA_VERSION required}"
: "${MY_IP:?MY_IP required}"
: "${SEED_IP:?SEED_IP required}"
: "${CLUSTER_NAME:?CLUSTER_NAME required}"
: "${IS_SEED:?IS_SEED required}"
: "${DC_NAME:=dc1}"
: "${NODE_AZ:?NODE_AZ required}"

export DEBIAN_FRONTEND=noninteractive
# Make any apt invocation (including ones inside the web-installer) auto-accept
# maintainer versions of conffiles without prompting.
APT_CONFD=/etc/apt/apt.conf.d/99-noninteractive-scylla
cat > "$APT_CONFD" <<'EOF'
Dpkg::Options { "--force-confdef"; "--force-confnew"; };
APT::Get::Assume-Yes "true";
EOF

# If a previous provisioner wrote rackdc BEFORE scylla-conf was installed, it
# would cause a dpkg conffile prompt. Remove it; we'll recreate after install.
rm -f /etc/scylla/cassandra-rackdc.properties

# If a previous failed install left things half-configured, finish it.
if dpkg -l | awk '/^iU|^iF/ {exit 0} END {exit 1}'; then
  dpkg --configure -a || true
fi

if ! command -v scylla >/dev/null 2>&1; then
  echo "[scylla] installing ${SCYLLA_VERSION} via web installer"
  curl -sSf get.scylladb.com/server | sudo bash -s -- \
    --scylla-version "${SCYLLA_VERSION}"
fi

# Rackdc must exist before first scylla-server start so the snitch picks it up.
mkdir -p /etc/scylla
cat > /etc/scylla/cassandra-rackdc.properties <<EOF
dc=${DC_NAME}
rack=${NODE_AZ}
prefer_local=true
EOF
echo "[scylla] rackdc written: dc=${DC_NAME} rack=${NODE_AZ}"

# Ensure scylla owns its data dir (disk_setup.sh created it as root).
if id scylla >/dev/null 2>&1; then
  chown -R scylla:scylla /var/lib/scylla || true
fi

# Run the regular scylla_setup with VM-friendly flags. We skip RAID setup
# (disk_setup.sh already prepared /var/lib/scylla), rsyslog (journald is
# fine for a lab), and fstrim. --nic eth1 is the private_network interface
# created by Vagrant.
#
# iotune speed-up: only the seed node actually runs `--io-setup 1` (which
# invokes iotune and takes 1–3 minutes on the XFS/md0 volume). Non-seed
# nodes receive the seed's generated /etc/scylla.d/io.conf + io_properties.yaml
# via Vagrant `file` provisioners (dropped to /tmp/io.conf and
# /tmp/io_properties.yaml before this script runs), copy them into place, and
# pass `--io-setup 0` to skip iotune entirely. Because every VM has the same
# shape (same vCPU/memory, identical XFS-on-md0 data volume), the iotune
# results on the seed are valid for all nodes.
SCYLLA_D=/etc/scylla.d
mkdir -p "$SCYLLA_D"

IO_SETUP_FLAG=1
if [ "${IS_SEED}" != "1" ]; then
  if [ -f /tmp/io.conf ] && [ -f /tmp/io_properties.yaml ]; then
    echo "[scylla] reusing cached io.conf + io_properties.yaml from seed"
    install -o root -g root -m 0644 /tmp/io.conf            "$SCYLLA_D/io.conf"
    install -o root -g root -m 0644 /tmp/io_properties.yaml "$SCYLLA_D/io_properties.yaml"
    IO_SETUP_FLAG=0
  else
    echo "[scylla] WARNING: non-seed node but no cached iotune files found;" \
         "falling back to --io-setup 1 (slow)"
  fi
fi

scylla_setup --no-raid-setup --online-discard 1 --nic eth1 \
             --io-setup "${IO_SETUP_FLAG}" --no-fstrim-setup --no-rsyslog-setup

# Patch scylla.yaml robustly via PyYAML.
python3 - <<PY
import yaml
p = "/etc/scylla/scylla.yaml"
with open(p) as f:
    cfg = yaml.safe_load(f) or {}
cfg["cluster_name"] = "${CLUSTER_NAME}"
cfg["listen_address"] = "${MY_IP}"
cfg["rpc_address"] = "${MY_IP}"
cfg["broadcast_address"] = "${MY_IP}"
cfg["broadcast_rpc_address"] = "${MY_IP}"
cfg["endpoint_snitch"] = "GossipingPropertyFileSnitch"
cfg["seed_provider"] = [{
    "class_name": "org.apache.cassandra.locator.SimpleSeedProvider",
    "parameters": [{"seeds": "${SEED_IP}"}],
}]
with open(p, "w") as f:
    yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)
PY

# Non-seed nodes: wait for seed gossip port before starting.
if [ "${IS_SEED}" != "1" ]; then
  echo "[scylla] waiting for seed ${SEED_IP}:7000..."
  for i in $(seq 1 120); do
    if (echo > /dev/tcp/${SEED_IP}/7000) >/dev/null 2>&1; then
      echo "[scylla] seed reachable after ${i}s"
      break
    fi
    sleep 1
  done
fi

systemctl enable --now scylla-server
echo "[scylla] scylla-server started on ${MY_IP}"
