#include <mpi.h>
#include <omp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "selec_proc.h"

#define TAG_TRABAJO 1
#define TAG_FIN 2
#define TAG_ARCH 99

char detect_architecture() {
    #if defined(__arm64__) || defined(__aarch64__) || defined(__APPLE__)
        return 'M'; // Mac
    #endif

    FILE *f = fopen("/proc/cpuinfo", "r");
    if (!f) {
        return 'W'; // Fallback por defecto a Windows
    }
    char line[256];
    char arch = 'W';
    while (fgets(line, sizeof(line), f)) {
        if (strstr(line, "Features") != NULL && strstr(line, "asimd") != NULL) {
            arch = 'M';
            break;
        }
        if (strstr(line, "CPU implementer") != NULL && strstr(line, "0x61") != NULL) {
            arch = 'M';
            break;
        }
    }
    fclose(f);
    return arch;
}

int main(int argc, char** argv) {
    int provisto;
    // Inicialización de MPI con soporte multihilo
    MPI_Init_thread(&argc, &argv, MPI_THREAD_FUNNELED, &provisto);

    // Configurar explícitamente el uso obligatorio de 8 hilos OpenMP
    omp_set_num_threads(8);

    int world_size, world_rank;
    MPI_Comm_size(MPI_COMM_WORLD, &world_size);
    MPI_Comm_rank(MPI_COMM_WORLD, &world_rank);

    int total_imagenes = 150; // Por defecto 150 imágenes del lote
    int filter_mask = 63;     // 111111 en binario (todos los filtros activos por defecto)
    int k_grey = 27;          // Tamaño de kernel para desenfoque gris por defecto
    int k_color = 27;         // Tamaño de kernel para desenfoque color por defecto

    if (argc > 1) total_imagenes = atoi(argv[1]);
    if (argc > 2) filter_mask = atoi(argv[2]);
    if (argc > 3) k_grey = atoi(argv[3]);
    if (argc > 4) k_color = atoi(argv[4]);

    // Asegurar que el directorio de salida 'img' existe localmente en este nodo
    system("mkdir -p img");

    if (world_rank == 0) {
        printf("\n=== MASTER: Iniciando orquestación de %d imágenes ===\n", total_imagenes);
        printf("[Master] Máscara de filtros activa: %d\n", filter_mask);
        printf("[Master] Kernels configurados - Gris: %d, Color: %d\n", k_grey, k_color);
        fflush(stdout);
        
        // Registrar arquitecturas de todos los nodos
        char architectures[100];
        memset(architectures, 0, sizeof(architectures));
        architectures[0] = 'W'; // El Master es Windows

        printf("[Master] Esperando reporte de arquitectura de los esclavos...\n");
        fflush(stdout);
        for (int esclavo = 1; esclavo < world_size; esclavo++) {
            char arch;
            MPI_Status status;
            MPI_Recv(&arch, 1, MPI_CHAR, MPI_ANY_SOURCE, TAG_ARCH, MPI_COMM_WORLD, &status);
            int esclavo_rank = status.MPI_SOURCE;
            architectures[esclavo_rank] = arch;
            printf("[Master] Nodo %d se conectó y reportó arquitectura: %s\n", 
                   esclavo_rank, (arch == 'W') ? "Windows (x86_64)" : "macOS (ARM64)");
            fflush(stdout);
        }

        // Medir dimensiones del primer archivo de imagen para calcular métricas de rendimiento
        int width = 3000;
        int height = 3000;
        FILE *f_test = fopen("images/imagen_001.bmp", "rb");
        if (f_test) {
            unsigned char header[54];
            if (fread(header, sizeof(unsigned char), 54, f_test) == 54) {
                width = *(int*)&header[18];
                height = *(int*)&header[22];
                printf("[Master] Dimensiones de imagen detectadas: %dx%d px\n", width, height);
            }
            fclose(f_test);
        } else {
            printf("[Master] Advertencia: No se pudo abrir images/imagen_001.bmp para leer dimensiones. Asumiendo %dx%d px por defecto.\n", width, height);
        }

        double t_start = MPI_Wtime();
        int imagen_actual = 1;
        int imagenes_completadas = 0;

        // 1. Enviar trabajo inicial a todos los esclavos
        for (int esclavo = 1; esclavo < world_size; esclavo++) {
            if (imagen_actual <= total_imagenes) {
                MPI_Send(&imagen_actual, 1, MPI_INT, esclavo, TAG_TRABAJO, MPI_COMM_WORLD);
                printf("[Master] Asigna imagen %d al Nodo %d [%s]\n", 
                       imagen_actual, esclavo, (architectures[esclavo] == 'W') ? "Windows" : "Mac");
                imagen_actual++;
            } else {
                int fin = -1;
                MPI_Send(&fin, 1, MPI_INT, esclavo, TAG_FIN, MPI_COMM_WORLD);
            }
        }

        // 2. Escuchar reportes y asignar más trabajo (Balanceo Dinámico)
        while (imagenes_completadas < total_imagenes) {
            int id_imagen_terminada;
            MPI_Status status;
            MPI_Recv(&id_imagen_terminada, 1, MPI_INT, MPI_ANY_SOURCE, TAG_TRABAJO, MPI_COMM_WORLD, &status);
            imagenes_completadas++;
            int esclavo_libre = status.MPI_SOURCE;

            printf("[Master] Progreso: %d/%d (Nodo %d [%s] terminó la imagen %d)\n", 
                   imagenes_completadas, total_imagenes, esclavo_libre, 
                   (architectures[esclavo_libre] == 'W') ? "Windows" : "Mac", id_imagen_terminada);
            fflush(stdout); // Asegurar que la GUI de Python lea la salida inmediatamente

            if (imagen_actual <= total_imagenes) {
                MPI_Send(&imagen_actual, 1, MPI_INT, esclavo_libre, TAG_TRABAJO, MPI_COMM_WORLD);
                printf("[Master] Re-asigna imagen %d al Nodo %d [%s]\n", 
                       imagen_actual, esclavo_libre, (architectures[esclavo_libre] == 'W') ? "Windows" : "Mac");
                imagen_actual++;
            } else {
                int fin = -1;
                MPI_Send(&fin, 1, MPI_INT, esclavo_libre, TAG_FIN, MPI_COMM_WORLD);
            }
        }
        
        double t_end = MPI_Wtime();
        double tiempo_total = t_end - t_start;
        
        // Calcular métrica de rendimiento en notación científica
        double total_pixeles = (double)total_imagenes * width * height;
        double pixeles_por_segundo = total_pixeles / tiempo_total;

        printf("\n=======================================================\n");
        printf("=== MASTER: ¡Lote de imágenes procesado por completo! ===\n");
        printf("Tiempo total de ejecución: %.6f segundos\n", tiempo_total);
        printf("Píxeles totales procesados: %.0f\n", total_pixeles);
        // Formateado en Notación Científica estricta (%e)
        printf("Rendimiento: %e píxeles/segundo\n", pixeles_por_segundo);
        printf("=======================================================\n\n");
        fflush(stdout);

    } else {
        // Ranks Esclavos
        // Determinar arquitectura mediante detección dinámica (procfs/compilador)
        char arch = detect_architecture();

        // Saludar al master con la arquitectura
        MPI_Send(&arch, 1, MPI_CHAR, 0, TAG_ARCH, MPI_COMM_WORLD);

        while (1) {
            int id_imagen_recibida;
            MPI_Status status;
            MPI_Recv(&id_imagen_recibida, 1, MPI_INT, 0, MPI_ANY_TAG, MPI_COMM_WORLD, &status);

            if (status.MPI_TAG == TAG_FIN) {
                printf("[Nodo %d] Recibió señal de apagado. Finalizando.\n", world_rank);
                break;
            }

            char ruta_entrada[100];
            sprintf(ruta_entrada, "images/imagen_%03d.bmp", id_imagen_recibida);
            
            // Resiliencia: Validar la existencia y tamaño local del archivo antes de procesar
            FILE *f_check = fopen(ruta_entrada, "rb");
            if (f_check == NULL) {
                printf("[Nodo %d] ⚠️ ADVERTENCIA: La imagen %s no existe localmente. Omitiendo procesamiento.\n", 
                       world_rank, ruta_entrada);
            } else {
                fseek(f_check, 0, SEEK_END);
                long file_size = ftell(f_check);
                fclose(f_check);
                
                if (file_size < 54) {
                    printf("[Nodo %d] ⚠️ ADVERTENCIA: La imagen %s está corrupta o vacía (tamaño %ld bytes). Omitiendo procesamiento.\n", 
                           world_rank, ruta_entrada, file_size);
                } else {
                    printf("[Nodo %d] 🚀 Procesando %s con filtros OpenMP...\n", world_rank, ruta_entrada);
                    
                    // Nomenclaturas de salida:
                    char mask_vg[50]; sprintf(mask_vg, "imagen_%03d_vg", id_imagen_recibida);
                    char mask_vc[50]; sprintf(mask_vc, "imagen_%03d_vc", id_imagen_recibida);
                    char mask_hg[50]; sprintf(mask_hg, "imagen_%03d_hg", id_imagen_recibida);
                    char mask_hc[50]; sprintf(mask_hc, "imagen_%03d_hc", id_imagen_recibida);
                    char mask_dg[50]; sprintf(mask_dg, "imagen_%03d_dg", id_imagen_recibida);
                    char mask_dc[50]; sprintf(mask_dc, "imagen_%03d_dc", id_imagen_recibida);
                    
                    // Ejecutar filtros activos según la máscara de bits
                    if (filter_mask & 1) inv_img(mask_vg, ruta_entrada);
                    if (filter_mask & 2) inv_img_color(mask_vc, ruta_entrada);
                    if (filter_mask & 4) inv_img_grey_horizontal(mask_hg, ruta_entrada);
                    if (filter_mask & 8) inv_img_color_horizontal(mask_hc, ruta_entrada);
                    if (filter_mask & 16) desenfoque_grey(ruta_entrada, mask_dg, k_grey);
                    if (filter_mask & 32) desenfoque(ruta_entrada, mask_dc, k_color);
                }
            }

            // Reportar de regreso al Master indicando que el Nodo está libre
            MPI_Send(&id_imagen_recibida, 1, MPI_INT, 0, TAG_TRABAJO, MPI_COMM_WORLD);
        }
    }

    MPI_Finalize();
    return 0;
}
