#!/bin/sh
# Script dinámico para configurar claves SSH bidireccionales en el clúster

HOSTS_FILE="/home/mpiuser/reto_final/hosts"
MASTER_IP="100.119.107.51"

if [ ! -f "$HOSTS_FILE" ]; then
    echo "❌ Error: No se encontró el archivo de hosts en $HOSTS_FILE"
    exit 1
fi

# 1. Asegurar que el Master tiene su propia clave SSH
if [ ! -f "/home/mpiuser/.ssh/id_rsa" ]; then
    echo "🔑 Generando clave SSH en el Master..."
    ssh-keygen -t rsa -f /home/mpiuser/.ssh/id_rsa -q -N ""
fi

echo "============================================="
echo " Configurando Llaves SSH del Clúster        "
echo "============================================="

# Limpiar authorized_keys del master antes de recolectar claves para evitar duplicados
cat /home/mpiuser/.ssh/id_rsa.pub > /home/mpiuser/.ssh/authorized_keys

for entry in $(cat "$HOSTS_FILE"); do
    ip=$(echo "$entry" | cut -d: -f1)
    
    # Ignorar líneas vacías, comentarios o la IP local del Master
    if [ -z "$ip" ] || echo "$ip" | grep -q "^#" || [ "$ip" = "$MASTER_IP" ]; then
        continue
    fi
    
    echo "⚙️ Configurando nodo esclavo: $ip ..."
    
    # A. Copiar clave del Master al Esclavo usando sshpass (contraseña por defecto 'mpi')
    echo "   -> Copiando clave del Master al Esclavo..."
    sshpass -p 'mpi' ssh-copy-id -f -o StrictHostKeyChecking=no "mpiuser@$ip" 2>/dev/null
    
    if [ $? -eq 0 ]; then
        echo "   ✅ Conexión Master -> Esclavo lista sin contraseña."
        
        # B. Generar clave SSH interna dentro del nodo esclavo si no existe
        echo "   -> Generando clave SSH dentro del Esclavo..."
        ssh -o StrictHostKeyChecking=no "mpiuser@$ip" "if [ ! -f /home/mpiuser/.ssh/id_rsa ]; then ssh-keygen -t rsa -f /home/mpiuser/.ssh/id_rsa -q -N ''; fi"
        
        # C. Copiar clave del Esclavo de regreso al Master para confianza mutua (SSH inverso)
        echo "   -> Registrando clave del Esclavo en el Master..."
        esclavo_pub=$(ssh -o StrictHostKeyChecking=no "mpiuser@$ip" "cat /home/mpiuser/.ssh/id_rsa.pub")
        if [ ! -z "$esclavo_pub" ]; then
            echo "$esclavo_pub" >> /home/mpiuser/.ssh/authorized_keys
            echo "   ✅ Conexión Esclavo -> Master lista sin contraseña."
        else
            echo "   ❌ Error al leer clave pública del esclavo."
        fi
    else
        echo "   ❌ ERROR: No se pudo conectar al esclavo $ip (¿Está apagado o Tailscale está desconectado?)"
    fi
    echo "---------------------------------------------"
done

echo "============================================="
echo " Verificando Conectividad Final...          "
echo "============================================="
sh /home/mpiuser/reto_final/check_hosts.sh
