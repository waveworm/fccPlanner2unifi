#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="pco-unifi-sync"

case "${1:-}" in
  install)
    echo "Install the systemd unit with:" 
    echo "  cp ./deploy/${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service"
    echo "  systemctl daemon-reload"
    echo "  systemctl enable ${SERVICE_NAME}"
    ;;
  start)
    systemctl start "${SERVICE_NAME}"
    ;;
  stop)
    systemctl stop "${SERVICE_NAME}"
    ;;
  restart)
    systemctl restart "${SERVICE_NAME}"
    ;;
  status)
    systemctl status "${SERVICE_NAME}" --no-pager
    ;;
  logs)
    journalctl -u "${SERVICE_NAME}" -f --no-pager
    ;;
  *)
    echo "Usage: $0 {install|start|stop|restart|status|logs}" >&2
    exit 2
    ;;
esac
