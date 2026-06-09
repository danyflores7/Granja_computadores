#!/bin/sh
# Script para verificar conectividad SSH a los nodos del cluster

HOSTS_FILE="/home/mpiuser/reto_final/hosts"

if [ ! -f "$HOSTS_FILE" ]; then
    echo "Error: No se encontró el archivo de hosts en $HOSTS_FILE"
    exit 1
fi

echo "============================================="
echo " Verificando conectividad SSH del Clúster    "
echo "============================================="

for entry in $(cat "$HOSTS_FILE"); do
    # Extraer la IP (remover los :slots si existen, ej: 100.1.2.3:2 -> 100.1.2.3)
    ip=$(echo "$entry" | cut -d: -f1)
    
    if [ -z "$ip" ]; then
        continue
    fi
    
    # Intentar hacer SSH rápido con timeout de 8 segundos y en modo Batch (sin password)
    echo -n "🔑 Conectando a Nodo $ip ... "
    ssh -o ConnectTimeout=8 -o BatchMode=yes "$ip" "echo 'CONECTADO ✅'" 2>/dev/null
    
    if [ $? -ne 0 ]; then
        echo "FALLÓ ❌ (Desconectado, offline o falta llave SSH)"
    fi
done
echo "============================================="
