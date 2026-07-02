#!/usr/bin/env bash
# remote-services/cleanup-vm.sh — Clean up old deployments on Azure VM.
# Run this ON the VM (or via SSH) before fresh deployment.

set -euo pipefail

echo "[cleanup-vm] Stopping old Python services ..."

# Stop old dataservice processes (running from /home/m/dataservice/)
for pid in $(pgrep -f "/home/m/dataservice/venv/bin/python" || true); do
  echo "  Killing old dataservice PID $pid"
  sudo kill -TERM "$pid" 2>/dev/null || true
  sleep 2
  sudo kill -KILL "$pid" 2>/dev/null || true
done

# Stop old ctrader process (running from /home/m/ssfx/v2/)
for pid in $(pgrep -f "/home/m/ssfx/v2/venv/bin/python" || true); do
  echo "  Killing old ctrader PID $pid"
  sudo kill -TERM "$pid" 2>/dev/null || true
  sleep 2
  sudo kill -KILL "$pid" 2>/dev/null || true
done

# Stop old ssfx-server if running outside Docker
for pid in $(pgrep -f "/home/m/ssfx.*ssfx_server" || true); do
  echo "  Killing old ssfx-server PID $pid"
  sudo kill -TERM "$pid" 2>/dev/null || true
  sleep 2
  sudo kill -KILL "$pid" 2>/dev/null || true
done

echo "[cleanup-vm] Removing old Docker containers ..."
# Remove any old ctrader-services containers
docker rm -f ctrader-services ctrader-services-test 2>/dev/null || true

# Remove old unused Docker images and volumes
docker system prune -f --volumes 2>/dev/null || true

echo "[cleanup-vm] Cleaning old deployment directories ..."
# Archive old deployments (don't delete, just move aside)
if [[ -d /home/m/dataservice && ! -L /home/m/dataservice ]]; then
  sudo mv /home/m/dataservice /home/m/dataservice.bak.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true
  echo "  Archived /home/m/dataservice"
fi

# Keep /home/m/ssfx/v2 as it may have other things — just stop the services
echo "[cleanup-vm] Cleanup complete."
echo "[cleanup-vm] Remaining listening ports:"
ss -tlnp 2>/dev/null | grep -E ':(8000|9000|9001|9002|9300|9301|9099)\b' || echo "  (none)"
