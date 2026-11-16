#!/bin/sh
# Abre un túnel SSH privado al panel del servidor. Uso: sh abrir-panel-servidor.sh [alias-ssh]
# El alias se toma del primer argumento, de JG_SERVER o, por defecto, "mi-servidor" (defínalo en ~/.ssh/config).
set -eu
HOST="${1:-${JG_SERVER:-mi-servidor}}"
printf '%s\n' "Abriendo conexión privada con $HOST. Mantenga esta ventana abierta." 'Panel: http://127.0.0.1:8766'
exec ssh -F "$HOME/.ssh/config" -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -N -L 127.0.0.1:8766:127.0.0.1:8765 "$HOST"
