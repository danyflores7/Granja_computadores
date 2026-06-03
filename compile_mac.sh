#!/bin/bash
echo "== Iniciando compilación nativa ARM64 en Mac M1 == "
mpic++ -fopenmp -o cluster_worker_mac cluster_worker.c
if [ $? -eq 0 ]; then
    echo "== Compilación exitosa: cluster_worker_mac generado =="
else
    echo "== Error en la compilación =="
    exit 1
fi
