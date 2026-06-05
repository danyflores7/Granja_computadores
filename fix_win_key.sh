#!/bin/sh
# Regenerar clave en Windows slave y agregarla al Master
ssh 100.126.201.74 'ssh-keygen -t rsa -f /home/mpiuser/.ssh/id_rsa -q -N "" 2>/dev/null; echo KEYGEN_DONE'
ssh 100.126.201.74 cat /home/mpiuser/.ssh/id_rsa.pub >> /home/mpiuser/.ssh/authorized_keys
echo "Clave Windows slave agregada al Master"

# Copiar clave del Master al Windows slave para que Master confie tambien
cat /home/mpiuser/.ssh/id_rsa.pub | ssh 100.126.201.74 'cat >> /home/mpiuser/.ssh/authorized_keys'
echo "Clave Master copiada al Windows slave"

# Verificar SSH inverso Windows -> Master
ssh 100.126.201.74 'ssh -o StrictHostKeyChecking=no 100.119.107.51 echo WIN_TO_MASTER_OK'
