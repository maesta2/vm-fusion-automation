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

# Dev mode — skip scylla_setup's IO/RAID/NTP tuning (unreliable in VMs).
scylla_dev_mode_setup --developer-mode 1 || true

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
