#!/bin/sh
# Linux-only wrapper: retain the caller's UID and confine capabilities/network mounts.
set -eu

if [ "$#" -eq 0 ]; then
    echo 'usage: scripts/run_userns_lab.sh COMMAND [ARG ...]' >&2
    exit 2
fi

exec unshare --user --map-current-user --keep-caps --mount --net sh -ec '
    mount --make-rprivate /
    mount -t tmpfs -o mode=755 tmpfs /run
    mkdir /run/netns
    exec "$@"
' pvcomm-userns "$@"
