#!/usr/bin/env bash
# NOTE: rackdc is now written inside setup_scylla.sh AFTER scylla-conf is
# installed, because writing /etc/scylla/cassandra-rackdc.properties before
# package install triggers a dpkg conffile prompt that blocks provisioning.
# This script is kept as a no-op for Vagrantfile compatibility.
set -euo pipefail
echo "[rackdc] deferred to setup_scylla.sh (after package install)"
