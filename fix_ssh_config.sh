#!/bin/sh
# Corregir el config SSH de los esclavos - usar Puerto 22 en lugar de 2222

NEW_CONFIG="Host *\n    Port 22\n    StrictHostKeyChecking no\n    UserKnownHostsFile /dev/null"

echo "=== Corrigiendo config SSH en Mac slave ==="
ssh -o StrictHostKeyChecking=no 100.108.94.102 "printf 'Host *\n    Port 22\n    StrictHostKeyChecking no\n    UserKnownHostsFile /dev/null\n' > /home/mpiuser/.ssh/config"
ssh -o StrictHostKeyChecking=no 100.108.94.102 "cat /home/mpiuser/.ssh/config"

echo "=== Corrigiendo config SSH en Windows slave ==="
ssh -o StrictHostKeyChecking=no 100.126.201.74 "printf 'Host *\n    Port 22\n    StrictHostKeyChecking no\n    UserKnownHostsFile /dev/null\n' > /home/mpiuser/.ssh/config"
ssh -o StrictHostKeyChecking=no 100.126.201.74 "cat /home/mpiuser/.ssh/config"

echo "=== Verificando SSH inverso: Mac -> Master ==="
ssh -o StrictHostKeyChecking=no 100.108.94.102 "ssh -o StrictHostKeyChecking=no 100.119.107.51 echo 'MAC_TO_MASTER_OK'"

echo "=== Verificando SSH inverso: Windows -> Master ==="
ssh -o StrictHostKeyChecking=no 100.126.201.74 "ssh -o StrictHostKeyChecking=no 100.119.107.51 echo 'WIN_TO_MASTER_OK'"

echo "=== Verificando TCP puerto 10000: Mac -> Master ==="
ssh -o StrictHostKeyChecking=no 100.108.94.102 "python3 /home/mpiuser/reto_final/test_net.py connect 100.119.107.51 10000"

echo "=== DONE ==="
