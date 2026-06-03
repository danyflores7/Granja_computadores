#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <omp.h>
#include "selec_proc.h"

int main(int argc, char *argv[]) {
    int num_threads = 6; // Default a 6
    if (argc > 1) {
        num_threads = atoi(argv[1]);
    }
    omp_set_num_threads(num_threads);

    FILE *fptr;
    char data[80] = "arcl.txt";
    fptr = fopen(data, "w");
    if (fptr == NULL) {
        printf("Error\n");
        exit(1);
    }
    fprintf(fptr, "Ejemplo prueba OPENMP\n");
    fprintf(fptr, "Daniel Flores Rojas\n");
    fclose(fptr);

    // Medir tiempo
    double t_start = omp_get_wtime();

    // Las tres imágenes grandes ("img1.bmp", "img2.bmp", "img3.bmp") deben 
    // estar dentro de "./img/". Se asume que existen.
    
    #pragma omp parallel
    {
        #pragma omp sections
        {
            // ================== IMAGEN 1 ==================
            #pragma omp section
            inv_img("1_inv_ver", "./img/img1.bmp");
            
            #pragma omp section
            inv_img_color("1_col_ver", "./img/img1.bmp");
            
            #pragma omp section
            inv_img_grey_horizontal("1_inv_hor", "./img/img1.bmp");
            
            #pragma omp section
            inv_img_color_horizontal("1_col_hor", "./img/img1.bmp");
            
            #pragma omp section
            desenfoque("./img/img1.bmp", "1_desenf_col", 27);
            
            #pragma omp section
            desenfoque_grey("./img/img1.bmp", "1_desenf_gry", 27);


            // ================== IMAGEN 2 ==================
            #pragma omp section
            inv_img("2_inv_ver", "./img/img2.bmp");
            
            #pragma omp section
            inv_img_color("2_col_ver", "./img/img2.bmp");
            
            #pragma omp section
            inv_img_grey_horizontal("2_inv_hor", "./img/img2.bmp");
            
            #pragma omp section
            inv_img_color_horizontal("2_col_hor", "./img/img2.bmp");
            
            #pragma omp section
            desenfoque("./img/img2.bmp", "2_desenf_col", 27);
            
            #pragma omp section
            desenfoque_grey("./img/img2.bmp", "2_desenf_gry", 27);


            // ================== IMAGEN 3 ==================
            #pragma omp section
            inv_img("3_inv_ver", "./img/img3.bmp");
            
            #pragma omp section
            inv_img_color("3_col_ver", "./img/img3.bmp");
            
            #pragma omp section
            inv_img_grey_horizontal("3_inv_hor", "./img/img3.bmp");
            
            #pragma omp section
            inv_img_color_horizontal("3_col_hor", "./img/img3.bmp");
            
            #pragma omp section
            desenfoque("./img/img3.bmp", "3_desenf_col", 27);
            
            #pragma omp section
            desenfoque_grey("./img/img3.bmp", "3_desenf_gry", 27);
        }
    }

    double t_end = omp_get_wtime();
    printf("Procesamiento terminado con %d threads.\n", num_threads);
    printf("Tiempo total de ejecucion: %f segundos\n", t_end - t_start);

    return 0;
}
