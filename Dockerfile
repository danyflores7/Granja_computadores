# ==============================================================================
# Dockerfile para Clúster MPICH (Master / Slave)
# Compatible con entornos híbridos Mac M1 (nativo arm64) y Windows (nativo amd64)
# ==============================================================================

# Dejamos la plataforma dinámica para construir nativamente en cada arquitectura
FROM ubuntu:22.04

# Evita diálogos interactivos durante la instalación de paquetes
ENV DEBIAN_FRONTEND=noninteractive

# Instalar dependencias esenciales para compilar y ejecutar MPI sobre SSH
RUN apt-get update && apt-get install -y \
    build-essential \
    mpich \
    libmpich-dev \
    openssh-server \
    openssh-client \
    sudo \
    nano \
    iputils-ping \
    cmake \
    && rm -rf /var/lib/apt/lists/*

# Crear el usuario del sistema 'mpiuser' con UID 1000, contraseña 'mpi' y agregarlo a sudo
RUN useradd -rm -d /home/mpiuser -s /bin/bash -g root -G sudo -u 1000 mpiuser \
    && echo 'mpiuser:mpi' | chpasswd

# Configurar privilegios de sudo sin contraseña para mpiuser
RUN echo "mpiuser ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Configurar el servidor SSH
# 1. Habilitar la autenticación por contraseña para el intercambio inicial de llaves ssh-copy-id
# 2. Deshabilitar el uso de PAM para evitar desconexiones rápidas en contenedores
RUN mkdir /var/run/sshd \
    && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/UsePAM yes/UsePAM no/' /etc/ssh/sshd_config

# Crear directorios clave y configurar permisos
# - '.ssh' para llaves autorizadas
# - 'reto_final' como espacio de trabajo (montado con volumen)
# - 'reto_final/images' para procesamiento de imágenes
RUN mkdir -p /home/mpiuser/.ssh /home/mpiuser/reto_final/images

# Configurar el cliente SSH dentro de la imagen
# Dado que mapeamos el SSH al puerto 2222 en el host, configuramos para que el SSH
# por defecto intente conectarse a ese puerto y omita la verificación interactiva de firmas
RUN echo "Host *\n    Port 2222\n    StrictHostKeyChecking no\n    UserKnownHostsFile /dev/null" > /home/mpiuser/.ssh/config

# Asegurar que todas las carpetas pertenezcan a mpiuser
RUN chown -R mpiuser:root /home/mpiuser \
    && chmod 700 /home/mpiuser/.ssh \
    && chmod 600 /home/mpiuser/.ssh/config

# Definir variables de entorno para restringir los puertos dinámicos de MPICH (Hydra)
# Esto nos permite mapear un rango fijo en docker-compose para que funcione sobre Tailscale
ENV MPICH_PORT_RANGE=10000:10010
ENV MPIEXEC_PORT_RANGE=10000:10010

WORKDIR /home/mpiuser/reto_final

# Puerto expuesto del contenedor
EXPOSE 22

# Iniciar el servicio SSH en primer plano para mantener vivo el contenedor
CMD ["/usr/sbin/sshd", "-D"]
