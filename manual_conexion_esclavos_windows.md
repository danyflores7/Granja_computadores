# Manual de Integración del Clúster MPI - Nodos Esclavos Windows

Este manual proporciona las instrucciones paso a paso para configurar cualquier computadora Windows en el equipo como un nodo esclavo en nuestro clúster híbrido MPICH, permitiendo el procesamiento distribuido de imágenes sin errores de red.

---

## 📋 Requisitos Previos

Antes de comenzar, asegúrate de tener instalados y configurados los siguientes programas en tu máquina host de Windows:

1. **Tailscale**:
   * Descarga e instala [Tailscale para Windows](https://tailscale.com/download/windows).
   * **Únete a la red compartida:** En lugar de iniciar sesión con una cuenta común, dale clic al **enlace de invitación del equipo** para unir tu cuenta de Tailscale a la red de distribución del clúster:
     👉 [https://login.tailscale.com/admin/invite/NYmJPU9i2bfyLBvmQh2911](https://login.tailscale.com/admin/invite/NYmJPU9i2bfyLBvmQh2911)
   * Abre la interfaz de tu cliente de Tailscale en Windows y anota tu **IP de Tailscale** (comienza con `100.x.x.x`). Esta será tu `<TU_IP_TAILSCALE>`.

2. **Docker Desktop**:
   * Descarga e instala [Docker Desktop para Windows](https://www.docker.com/products/docker-desktop/).
   * Asegúrate de habilitar el uso del motor de **WSL2** en la configuración: *Settings (Engranaje) ➡️ General ➡️ Use the WSL 2 based engine*.

---

## 🚀 Paso 1: Levantar el Contenedor Docker

1. Abre tu terminal de Windows (PowerShell o CMD) en la carpeta raíz del proyecto (`Granja_computadoras`).
2. Construye y levanta el contenedor en segundo plano:
   ```powershell
   docker compose up -d --build
   ```
3. Verifica que el contenedor esté corriendo con el nombre `mpi_cluster_node`:
   ```powershell
   docker ps
   ```
   *(Debe mostrar los puertos redireccionados `2222->22/tcp` y `10000-10010->10000-10010/tcp`)*.

---

## 🌐 Paso 2: Redirección del Puerto de WSL2 a Windows (Crítico)

Dado que Docker en Windows se ejecuta dentro de la máquina virtual ligera de WSL2, Windows bloquea el tráfico de red externo hacia esa máquina virtual. Para permitir que el Master se conecte a tu contenedor por medio de tu IP de Tailscale:

1. Presiona la tecla **Windows**, escribe **PowerShell**, haz clic derecho y selecciona **Ejecutar como Administrador**.
2. Limpia cualquier regla previa conflictiva ejecutando:
   ```powershell
   netsh interface portproxy reset
   ```
3. Agrega la regla de reenvío para el puerto de SSH (`2222`) y el rango de comunicación de MPI (`10000-10010`) asociándolos a tu IP de Tailscale:
   ```powershell
   # Redireccionar el puerto de control SSH (2222)
   netsh interface portproxy add v4tov4 listenport=2222 listenaddress=<TU_IP_TAILSCALE> connectport=2222 connectaddress=localhost

   # Redireccionar el rango de comunicación de datos de MPI (10000 al 10010)
   for ($i=10000; $i -le 10010; $i++) {
       netsh interface portproxy add v4tov4 listenport=$i listenaddress=<TU_IP_TAILSCALE> connectport=$i connectaddress=localhost
   }
   ```
   *(Reemplaza `<TU_IP_TAILSCALE>` por tu IP real de Tailscale de 100.x.x.x, por ejemplo: `100.75.9.4`)*.

4. Reinicia el servicio de Windows auxiliar para aplicar los cambios de inmediato:
   ```powershell
   Restart-Service -Name iphlpsvc
   ```
5. Verifica que los mapeos estén activos:
   ```powershell
   netsh interface portproxy show all
   ```

---

## 🛡️ Paso 3: Configurar el Firewall de Windows

El Firewall de Windows bloquea conexiones entrantes externas a puertos no estándar de manera predeterminada. Debemos crear reglas de entrada para habilitar el tráfico:

1. Presiona la tecla **Windows**, escribe **Firewall de Windows Defender con seguridad avanzada** y ábrelo.
2. En la columna de la izquierda, haz clic en **Reglas de entrada**.
3. En la columna de la derecha, haz clic en **Nueva regla...** y configúrala de la siguiente manera:
   * **Tipo de regla:** Puerto.
   * **Protocolo y puertos:** TCP, y en *Puertos locales específicos* escribe: `2222, 10000-10010`.
   * **Acción:** Permitir la conexión.
   * **Perfil:** Marca las casillas de **Dominio**, **Privado** y **Público** (este último es sumamente importante para que Tailscale permita el enlace).
   * **Nombre:** Ponle un nombre descriptivo como `MPI Cluster - Entrada`.
4. Haz clic en **Finalizar**.

---

## 🛠️ Diagnóstico y Solución de Inconvenientes Comunes

### 1. Error de dirección en uso (`Address already in use`)
Si ejecutas una simulación y se queda colgada o arroja un error de puerto ocupado, significa que quedaron procesos de MPI o Hydra congelados en segundo plano.
* **Solución:** Abre PowerShell en Windows y corre el siguiente comando para limpiar todos los procesos colgados en tu contenedor de un solo golpe:
  ```powershell
  docker exec -u mpiuser mpi_cluster_node sh -c "pkill -9 -f cluster_worker ; pkill -9 -f mpiexec ; pkill -9 -f hydra_pmi_proxy"
  ```

### 2. Error de conexión rechazada (`Connection refused`)
Si el nodo Master no logra conectarse contigo por SSH en el puerto 2222.
* **Solución:**
  1. Verifica que tu contenedor Docker esté corriendo (`docker ps`).
  2. Confirma que tu IP de Tailscale esté activa y que puedas hacerle ping desde otra máquina.
  3. Asegúrate de haber ejecutado el comando de PowerShell `netsh interface portproxy` como **Administrador** y con la IP de Tailscale escrita de forma correcta.
