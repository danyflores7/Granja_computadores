#!/bin/sh
# Script para configurar claves SSH entre todos los nodos del cluster

echo "=== Generando clave SSH en nodo Mac ==="
ssh -o StrictHostKeyChecking=no 100.108.94.102 "ssh-keygen -t rsa -f /home/mpiuser/.ssh/id_rsa -q -N '' 2>/dev/null; echo done"

echo "=== Generando clave SSH en nodo Windows ==="
ssh -o StrictHostKeyChecking=no 100.126.201.74 "ssh-keygen -t rsa -f /home/mpiuser/.ssh/id_rsa -q -N '' 2>/dev/null; echo done"

echo "=== Agregando clave del Mac al Master ==="
ssh -o StrictHostKeyChecking=no 100.108.94.102 cat /home/mpiuser/.ssh/id_rsa.pub >> /home/mpiuser/.ssh/authorized_keys

echo "=== Agregando clave del Windows al Master ==="
ssh -o StrictHostKeyChecking=no 100.126.201.74 cat /home/mpiuser/.ssh/id_rsa.pub >> /home/mpiuser/.ssh/authorized_keys

echo "=== Copiando clave del Master al Mac (para que Mac confie en Master) ==="
cat /home/mpiuser/.ssh/id_rsa.pub | ssh -o StrictHostKeyChecking=no 100.108.94.102 "cat >> /home/mpiuser/.ssh/authorized_keys"

echo "=== Copiando clave del Master al Windows (para que Windows confie en Master) ==="
cat /home/mpiuser/.ssh/id_rsa.pub | ssh -o StrictHostKeyChecking=no 100.126.201.74 "cat >> /home/mpiuser/.ssh/authorized_keys"

echo "=== Copiando clave del Mac al Windows ==="
ssh -o StrictHostKeyChecking=no 100.108.94.102 cat /home/mpiuser/.ssh/id_rsa.pub | ssh -o StrictHostKeyChecking=no 100.126.201.74 "cat >> /home/mpiuser/.ssh/authorized_keys"

echo "=== Copiando clave del Windows al Mac ==="
ssh -o StrictHostKeyChecking=no 100.126.201.74 cat /home/mpiuser/.ssh/id_rsa.pub | ssh -o StrictHostKeyChecking=no 100.108.94.102 "cat >> /home/mpiuser/.ssh/authorized_keys"

echo "=== Verificando conexiones sin contraseña ==="
ssh -o StrictHostKeyChecking=no 100.119.107.51 echo "Master OK"
ssh -o StrictHostKeyChecking=no 100.108.94.102 echo "Mac OK"
ssh -o StrictHostKeyChecking=no 100.126.201.74 echo "Windows OK"

echo "=== Verificando desde Mac hacia Master ==="
ssh -o StrictHostKeyChecking=no 100.108.94.102 "ssh -o StrictHostKeyChecking=no 100.119.107.51 echo MAC_TO_MASTER_OK"

echo "=== Verificando desde Windows hacia Master ==="
ssh -o StrictHostKeyChecking=no 100.126.201.74 "ssh -o StrictHostKeyChecking=no 100.119.107.51 echo WIN_TO_MASTER_OK"

echo "=== CONFIGURACION SSH COMPLETA ==="
