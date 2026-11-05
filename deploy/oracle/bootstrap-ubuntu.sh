#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo 'Run with sudo on the new Ubuntu VM.' >&2
  exit 1
fi

source /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 24.04 ]]; then
  echo 'This bootstrap is for a fresh Ubuntu 24.04 LTS VM.' >&2
  exit 1
fi

architecture=$(dpkg --print-architecture)
if [[ $architecture != arm64 && $architecture != amd64 ]]; then
  echo 'Only ARM64 and AMD64 are supported.' >&2
  exit 1
fi

available_kib=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if (( available_kib < 5 * 1024 * 1024 )); then
  echo 'Allocate at least 6 GB RAM before installing the deployment.' >&2
  exit 1
fi

if command -v docker >/dev/null 2>&1; then
  docker compose version
else
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: ${architecture}
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

systemctl enable --now docker
install -m 0700 -d /opt/basket/config
install -m 0755 -d /opt/basket/releases
echo 'Docker is ready. Install private runtime.env and non-secret compose.env, then deploy the release.'
