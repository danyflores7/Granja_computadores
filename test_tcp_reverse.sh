#!/bin/sh
# Test de conectividad TCP bidireccional: Esclavos -> Master en puertos 10000-10010

# Primero iniciar listener en background y capturar su PID
python3 /home/mpiuser/reto_final/test_net_long.py listen 10001 &
LISTENER_PID=$!
sleep 1

echo "=== Listener activo en Master:10001 (PID=$LISTENER_PID) ==="

# Test desde Mac
ssh -o StrictHostKeyChecking=no 100.108.94.102 "python3 /home/mpiuser/reto_final/test_net.py connect 100.119.107.51 10001"
echo "Mac->Master:10001 => $?"

# Test desde Windows
ssh -o StrictHostKeyChecking=no 100.126.201.74 "python3 /home/mpiuser/reto_final/test_net.py connect 100.119.107.51 10001"
echo "Win->Master:10001 => $?"

wait $LISTENER_PID 2>/dev/null
echo "=== DONE ==="
