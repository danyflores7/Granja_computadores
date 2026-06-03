#include <mpi.h>
#include <omp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

void aplicar_filtros_openmp(int id_imagen, int rank) {
    char ruta_entrada[100];
    char ruta_salida[100];
    sprintf(ruta_entrada, "images/imagen_%03d.bmp", id_imagen);
    sprintf(ruta_salida, "images/imagen_%03d_procesada.bmp", id_imagen);
    
    printf("[Nodo %d] 🚀 Iniciando OpenMP para imagen_%03d.bmp\n", rank, id_imagen);

    #pragma omp parallel for
    for (int i = 0; i < 1000; i++) {
        int id_hilo = omp_get_thread_num();
        if (i == 0) { 
            printf("   -> [Nodo %d] Hilo OpenMP %d trabajando en la imagen %d\n", rank, id_hilo, id_imagen);
        }
    }
}

int main(int argc, char** argv) {
    int provisto;
    MPI_Init_thread(&argc, &argv, MPI_THREAD_FUNNELED, &provisto);

    int world_size, world_rank;
    MPI_Comm_size(MPI_COMM_WORLD, &world_size);
    MPI_Comm_rank(MPI_COMM_WORLD, &world_rank);

    int total_imagenes = 20; 
    int TAG_TRABAJO = 1;
    int TAG_FIN = 2;

    if (world_rank == 0) {
        printf("\n=== MASTER: Iniciando orquestación de %d imágenes ===\n", total_imagenes);
        int imagen_actual = 1;
        int imagenes_completadas = 0;

        for (int esclavo = 1; esclavo < world_size; esclavo++) {
            if (imagen_actual <= total_imagenes) {
                MPI_Send(&imagen_actual, 1, MPI_INT, esclavo, TAG_TRABAJO, MPI_COMM_WORLD);
                printf("[Master] Asigna imagen %d al Nodo %d\n", imagen_actual, esclavo);
                imagen_actual++;
            }
        }

        while (imagenes_completadas < total_imagenes) {
            int id_imagen_terminada;
            MPI_Status status;
            MPI_Recv(&id_imagen_terminada, 1, MPI_INT, MPI_ANY_SOURCE, TAG_TRABAJO, MPI_COMM_WORLD, &status);
            imagenes_completadas++;
            int esclavo_libre = status.MPI_SOURCE;

            printf("[Master] Nodo %d reportó terminada la imagen %d. (Progreso: %d/%d)\n", esclavo_libre, id_imagen_terminada, imagenes_completadas, total_imagenes);

            if (imagen_actual <= total_imagenes) {
                MPI_Send(&imagen_actual, 1, MPI_INT, esclavo_libre, TAG_TRABAJO, MPI_COMM_WORLD);
                printf("[Master] Re-asigna imagen %d al Nodo %d\n", imagen_actual, esclavo_libre);
                imagen_actual++;
            } else {
                int fin = -1;
                MPI_Send(&fin, 1, MPI_INT, esclavo_libre, TAG_FIN, MPI_COMM_WORLD);
            }
        }
        printf("=== MASTER: ¡Lote de imágenes procesado por completo! ===\n\n");

    } else {
        while (1) {
            int id_imagen_recibida;
            MPI_Status status;
            MPI_Recv(&id_imagen_recibida, 1, MPI_INT, 0, MPI_ANY_TAG, MPI_COMM_WORLD, &status);

            if (status.MPI_TAG == TAG_FIN) {
                printf("[Nodo %d] Recibió señal de apagado. Finalizando.\n", world_rank);
                break;
            }

            aplicar_filtros_openmp(id_imagen_recibida, world_rank);
            MPI_Send(&id_imagen_recibida, 1, MPI_INT, 0, TAG_TRABAJO, MPI_COMM_WORLD);
        }
    }

    MPI_Finalize();
    return 0;
}
