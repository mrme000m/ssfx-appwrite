#!/usr/bin/env bash
# install-docker.sh — Install Docker Engine, Compose plugin, and Buildx plugin.
#
# Run on the target VM by setup-vm.sh.
# Respects VM_OS_FAMILY (amazonlinux, rhel, ubuntu).

set -euo pipefail

OS_FAMILY="${VM_OS_FAMILY:-amazonlinux}"

# Install Docker
if ! command -v docker >/dev/null 2>&1; then
  echo "[setup-vm] Installing Docker for ${OS_FAMILY} ..."
  case "${OS_FAMILY}" in
    amazonlinux|rhel)
      sudo yum update -y
      sudo yum install -y docker
      ;;
    ubuntu)
      export DEBIAN_FRONTEND=noninteractive
      sudo apt-get update -y
      sudo apt-get install -y docker.io
      ;;
    *)
      echo "ERROR: Unsupported OS family: ${OS_FAMILY}" >&2
      exit 1
      ;;
  esac
  sudo systemctl enable --now docker
  sudo usermod -aG docker "${USER}"
else
  echo "[setup-vm] Docker already installed"
  sudo systemctl start docker || true
fi

# Install Docker Compose plugin
DOCKER_CONFIG="${HOME}/.docker"
mkdir -p "${DOCKER_CONFIG}/cli-plugins"
if [[ ! -x "${DOCKER_CONFIG}/cli-plugins/docker-compose" ]]; then
  echo "[setup-vm] Installing Docker Compose plugin ..."
  COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
  curl -sSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    -o "${DOCKER_CONFIG}/cli-plugins/docker-compose"
  chmod +x "${DOCKER_CONFIG}/cli-plugins/docker-compose"
else
  echo "[setup-vm] Docker Compose plugin already installed"
fi

# Install Docker Buildx plugin
if [[ ! -x "${DOCKER_CONFIG}/cli-plugins/docker-buildx" ]]; then
  echo "[setup-vm] Installing Docker Buildx plugin ..."
  BUILDX_VERSION=$(curl -s https://api.github.com/repos/docker/buildx/releases/latest | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
  curl -sSL "https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-amd64" \
    -o "${DOCKER_CONFIG}/cli-plugins/docker-buildx"
  chmod +x "${DOCKER_CONFIG}/cli-plugins/docker-buildx"
else
  echo "[setup-vm] Docker Buildx plugin already installed"
fi

echo "[setup-vm] Docker versions:"
docker --version
docker compose version
docker buildx version
