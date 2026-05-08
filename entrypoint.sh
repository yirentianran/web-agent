#!/bin/bash
set -e

CONTAINER_UID=${CONTAINER_UID:-1000}
CONTAINER_GID=${CONTAINER_GID:-1000}

if [ "$(id -u agent 2>/dev/null || echo 0)" != "$CONTAINER_UID" ] || [ "$(id -g agent 2>/dev/null || echo 0)" != "$CONTAINER_GID" ]; then
    echo "Adapting agent user: uid=$(id -u agent) -> $CONTAINER_UID gid=$(id -g agent) -> $CONTAINER_GID"
    groupmod -g "$CONTAINER_GID" agent 2>/dev/null || groupadd -g "$CONTAINER_GID" agent
    usermod -u "$CONTAINER_UID" -g "$CONTAINER_GID" agent 2>/dev/null || useradd -u "$CONTAINER_UID" -g "$CONTAINER_GID" -d /home/agent agent
    chown -R agent:agent /app /home/agent
fi

exec runuser -u agent -- "$@"
