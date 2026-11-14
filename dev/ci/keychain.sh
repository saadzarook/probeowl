#!/bin/bash
# Author: @SaadZarook
# A Secret Service for a Linux CI job, as a desktop session has one: a D-Bus session bus and
# gnome-keyring's secrets component, with a login keyring unlocked by a random password that is
# never printed. ProbeOwl keeps passwords only in a system keychain (backend/probeowl/secrets.py),
# so CI uses a real one, never a file.
#   bash dev/ci/keychain.sh               in GitHub Actions: later steps get DBUS_SESSION_BUS_ADDRESS
#   eval "$(bash dev/ci/keychain.sh)"     elsewhere: exports it in this shell
# Installs gnome-keyring with sudo if it is missing.
set -euo pipefail

if ! command -v gnome-keyring-daemon >/dev/null || ! command -v dbus-daemon >/dev/null; then
  sudo apt-get update -qq >/dev/null
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    gnome-keyring dbus-daemon >/dev/null
fi

address=$(dbus-daemon --session --fork --print-address=1 | head -1)
[ -n "$address" ] || { echo "the D-Bus session bus did not start" >&2; exit 1; }
export DBUS_SESSION_BUS_ADDRESS="$address"

# Creates the login keyring on first use, then unlocks it; Secret Service then answers without a
# prompt, as it does in an unlocked desktop session.
head -c 24 /dev/urandom | base64 | tr -d '\n' |
  gnome-keyring-daemon --daemonize --unlock --components=secrets >/dev/null

# Wait until Secret Service answers on the bus.
for _ in $(seq 50); do
  if dbus-send --session --print-reply --dest=org.freedesktop.secrets /org/freedesktop/secrets \
    org.freedesktop.DBus.Properties.Get string:org.freedesktop.Secret.Service string:Collections \
    >/dev/null 2>&1; then
    if [ -n "${GITHUB_ENV:-}" ]; then
      echo "DBUS_SESSION_BUS_ADDRESS=$address" >>"$GITHUB_ENV"
    else
      echo "export DBUS_SESSION_BUS_ADDRESS='$address'"
    fi
    echo "Secret Service is up on the session bus" >&2
    exit 0
  fi
  sleep 0.2
done
echo "Secret Service did not answer on the session bus" >&2
exit 1
