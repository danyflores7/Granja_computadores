#include <mpi.h>
#include <omp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h> // Para simular retraso si no hay imágenes reales aún

// ==========================================
// PEGA AQUÍ TUS FUNCIONES DEL ENTREGABLE INTERMEDIO
// ==========================================
void aplicar_filtros_openmp(int id_imagen, int rank) {
  char ruta_entrada[100];
  char ruta_salida[100];
  sprintf(ruta_entrada, "images/imagen_%03d.bmp", id_imagen);
  sprintf(ruta_salida, "images/imagen_%03d_procesada.bmp", id_imagen);

  // [SIMULACIÓN O LÓGICA REAL]
  // Aquí abres el BMP, lees cabeceras, etc.

  printf("[Nodo %d] 🚀 Iniciando OpenMP para imagen_%03d.bmp\n", rank,
         id_imagen);

// Tu código de OpenMP del entregable intermedio va aquí adentro:
#pragma omp parallel for
  for (int i = 0; i < 1000; i++) {
    // Aquí adentro procesabas los pixeles (Grises, Espejo, Blur)
    // Por ahora simulamos una micro-tarea matemática por cada hilo:
    int id_hilo = omp_get_thread_num();
    if (i == 0) {
      // Solo imprimimos una vez para verificar que OpenMP use múltiples núcleos
      printf("   -> [Nodo %d] Hilo OpenMP %d trabajando en la imagen %d\n",
             rank, id_hilo, id_imagen);
    }
  }

  // Aquí guardas el archivo resultante
  // GuardarBMP(ruta_salida);
}

// ==========================================
// LÓGICA PRINCIPAL DEL CLÚSTER (MPI)
// ==========================================
int main(int argc, char **argv) {
  // Inicialización requerida por MPICH con soporte multihilo
  int provisto;
  MPI_Init_thread(&argc, &argv, MPI_THREAD_FUNNELED, &provisto);

  int world_size, world_rank;
  MPI_Comm_size(MPI_COMM_WORLD, &world_size);
  MPI_Comm_rank(MPI_COMM_WORLD, &world_rank);

  int total_imagenes = 20; // Para pruebas rápidas usa 20, luego súbelo a 150
  int TAG_TRABAJO = 1;
  int TAG_FIN = 2;

  if (world_rank == 0) {
    // ==================================
    // MASTER (Tu Windows)
    // ==================================
    printf("\n=== MASTER: Iniciando orquestación de %d imágenes ===\n",
           total_imagenes);
    int imagen_actual = 1;
    int imagenes_completadas = 0;

    // 1. Enviar una imagen inicial a cada Esclavo disponible
    for (int esclavo = 1; esclavo < world_size; esclavo++) {
      if (imagen_actual <= total_imagenes) {
        MPI_Send(&imagen_actual, 1, MPI_INT, esclavo, TAG_TRABAJO,
                 MPI_COMM_WORLD);
        printf("[Master] Asigna imagen %d al Nodo %d\n", imagen_actual,
               esclavo);
        imagen_actual++;
      }
    }

    // 2. Escuchar reportes y asignar más trabajo (Balanceo Dinámico)
    while (imagenes_completadas < total_imagenes) {
      int id_imagen_terminada;
      MPI_Status status;

      // Recibe la señal de CUALQUIER esclavo que terminó
      MPI_Recv(&id_imagen_terminada, 1, MPI_INT, MPI_ANY_SOURCE, TAG_TRABAJO,
               MPI_COMM_WORLD, &status);
      imagenes_completadas++;
      int esclavo_libre = status.MPI_SOURCE;

      printf("[Master] Nodo %d reportó terminada la imagen %d. (Progreso: "
             "%d/%d)\n",
             esclavo_libre, id_imagen_terminada, imagenes_completadas,
             total_imagenes);

      // Si quedan imágenes, le manda otra de inmediato al que se desocupó
      if (imagen_actual <= total_imagenes) {
        MPI_Send(&imagen_actual, 1, MPI_INT, esclavo_libre, TAG_TRABAJO,
                 MPI_COMM_WORLD);
        printf("[Master] Re-asigna imagen %d al Nodo %d\n", imagen_actual,
               esclavo_libre);
        imagen_actual++;
      } else {
        // Si ya no hay imágenes, le manda señal de apagado a ese nodo
        int fin = -1;
        MPI_Send(&fin, 1, MPI_INT, esclavo_libre, TAG_FIN, MPI_COMM_WORLD);
      }
    }
    printf("=== MASTER: ¡Lote de imágenes procesado por completo! ===\n\n");

  } else {
    // ==================================
    // ESCLAVOS (Tu Mac M1 y futuros compañeros)
    // ==================================
    while (1) {
      int id_imagen_recibida;
      MPI_Status status;

      // Espera órdenes del Master
      MPI_Recv(&id_imagen_recibida, 1, MPI_INT, 0, MPI_ANY_TAG, MPI_COMM_WORLD,
               &status);

      // Si el Master manda TAG_FIN, salimos del ciclo
      if (status.MPI_TAG == TAG_FIN) {
        printf("[Nodo %d] Recibió señal de apagado. Finalizando.\n",
               world_rank);
        break;
      }

      // Ejecuta el filtro local usando los hilos de OpenMP
      aplicar_filtros_openmp(id_imagen_recibida, world_rank);

      // Devuelve el ID de la imagen procesada al Master como confirmación
      MPI_Send(&id_imagen_recibida, 1, MPI_INT, 0, TAG_TRABAJO, MPI_COMM_WORLD);
    }
  }

  MPI_Finalize();
  return 0;
}