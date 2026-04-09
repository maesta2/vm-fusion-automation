#!/usr/bin/env bash
# Prepare /var/lib/scylla on dedicated data disk(s).
# - 1 disk  -> mkfs.xfs directly
# - >1 disk -> md RAID0 across them, then mkfs.xfs on /dev/md0
set -euo pipefail

MOUNT=/var/lib/scylla

# Idempotency: already mounted as xfs? bail out.
if mountpoint -q "$MOUNT" && findmnt -no FSTYPE "$MOUNT" | grep -qx xfs; then
  echo "[disk_setup] $MOUNT already mounted as xfs — nothing to do"
  exit 0
fi

# Detect root disk so we exclude it from candidates.
ROOT_SRC=$(findmnt -no SOURCE /)
ROOT_PK=$(lsblk -no PKNAME "$ROOT_SRC" 2>/dev/null || true)
[ -z "$ROOT_PK" ] && ROOT_PK=$(basename "$ROOT_SRC" | sed 's/[0-9]*$//')

# Enumerate all whole disks that are NOT the root disk and have NO partitions/children.
mapfile -t DATA_DISKS < <(
  lsblk -dn -o NAME,TYPE | awk '$2=="disk"{print $1}' |
  while read -r d; do
    [ "$d" = "$ROOT_PK" ] && continue
    # skip if it has children (already-partitioned)
    if [ "$(lsblk -n -o NAME "/dev/$d" | wc -l)" -gt 1 ]; then
      continue
    fi
    echo "/dev/$d"
  done
)

if [ "${#DATA_DISKS[@]}" -eq 0 ]; then
  echo "[disk_setup] no data disks detected — skipping"
  exit 0
fi

echo "[disk_setup] data disks: ${DATA_DISKS[*]}"

export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y mdadm xfsprogs
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y mdadm xfsprogs
fi

if [ "${#DATA_DISKS[@]}" -eq 1 ]; then
  TARGET="${DATA_DISKS[0]}"
else
  TARGET=/dev/md0
  if [ ! -b "$TARGET" ]; then
    mdadm --create --verbose --force --run "$TARGET" \
      --level=0 --raid-devices="${#DATA_DISKS[@]}" "${DATA_DISKS[@]}"
  fi
  # Wait for the array to settle before mkfs — this is where the script was
  # failing previously. mdadm returns as soon as the kernel accepts the
  # command, but udev hasn't finished creating /dev/md0 yet.
  udevadm settle || true
  mdadm --wait "$TARGET" || true
  for _ in 1 2 3 4 5; do
    [ -b "$TARGET" ] && break
    sleep 1
  done
  # Persist array config (best-effort; don't fail under pipefail).
  {
    if [ -f /etc/mdadm/mdadm.conf ]; then
      mdadm --detail --scan >> /etc/mdadm/mdadm.conf
    elif [ -f /etc/mdadm.conf ]; then
      mdadm --detail --scan >> /etc/mdadm.conf
    else
      mdadm --detail --scan > /etc/mdadm.conf
    fi
  } || true
fi

echo "[disk_setup] mkfs.xfs on $TARGET"
mkfs.xfs -f "$TARGET"

mkdir -p "$MOUNT"
UUID=$(blkid -s UUID -o value "$TARGET")
if [ -z "$UUID" ]; then
  echo "[disk_setup] blkid returned no UUID for $TARGET" >&2
  exit 1
fi

# Remove any stale fstab entry for this mountpoint.
sed -i.bak "\# $MOUNT #d" /etc/fstab || true
echo "UUID=$UUID $MOUNT xfs noatime,nofail 0 0" >> /etc/fstab

mount "$MOUNT"
echo "[disk_setup] mounted $TARGET on $MOUNT"
df -h "$MOUNT"
