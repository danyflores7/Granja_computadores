import sys
import os
import time
import re
import subprocess
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QCheckBox, QLineEdit, QPushButton, QGridLayout, 
                             QMessageBox, QDialog, QProgressBar, QAction)
from PyQt5.QtGui import QIntValidator, QPixmap, QColor, QPalette
from PyQt5.QtCore import Qt, QThread, pyqtSignal

class DropArea(QLabel):
    def __init__(self):
        super().__init__("Arrastra imágenes\nmáximo 10\n.bmp")
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setStyleSheet("background-color: #2b2b2b; color: white; padding: 10px; border: 1px solid #555;")
        self.setAcceptDrops(True)
        self.setMinimumSize(300, 200)
        self.image_paths = []

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if path.lower().endswith(".bmp"):
                if path not in self.image_paths:
                    if len(self.image_paths) < 10:
                        self.image_paths.append(path)
                    else:
                        QMessageBox.warning(self, "Límite alcanzado", "No puedes subir más de 10 imágenes.")
                        break
        
        self.update_display()

    def update_display(self):
        if not self.image_paths:
            self.setText("Arrastra imágenes\nmáximo 10\n.bmp")
        else:
            text = "Archivos cargados:\n"
            for p in self.image_paths:
                text += f"- {os.path.basename(p)}\n"
            self.setText(text)

class OddIntValidator(QIntValidator):
    def validate(self, input_str, pos):
        if not input_str:
            return QIntValidator.State.Intermediate, input_str, pos
        try:
            val = int(input_str)
            if val > 0 and val % 2 != 0:
                return QIntValidator.State.Acceptable, input_str, pos
            else:
                return QIntValidator.State.Invalid, input_str, pos
        except ValueError:
            return QIntValidator.State.Invalid, input_str, pos

class MPIWorker(QThread):
    progress_updated = pyqtSignal(int, int) # completadas, totales
    log_received = pyqtSignal(str)
    finished_successfully = pyqtSignal(float) # elapsed time
    finished_with_error = pyqtSignal(int) # exit code
    status_msg = pyqtSignal(str) # Para notificaciones de estado
    
    def __init__(self, total_imagenes, filter_mask, k_grey, k_color, hosts_path, base_dir, in_container):
        super().__init__()
        self.total_imagenes = total_imagenes
        self.filter_mask = filter_mask
        self.k_grey = k_grey
        self.k_color = k_color
        self.hosts_path = hosts_path
        self.base_dir = base_dir
        self.in_container = in_container
        self.start_time = None
        self.process = None
        
    def run(self):
        self.start_time = time.time()
        
        # Helper to execute commands in the Master container or locally
        def run_master_cmd(cmd, root=False, timeout=30):
            if self.in_container:
                if root:
                    return subprocess.run(["sudo"] + cmd, capture_output=True, text=True, timeout=timeout)
                else:
                    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            else:
                prefix = ["docker", "exec"]
                if root:
                    prefix += ["-u", "root"]
                else:
                    prefix += ["-u", "mpiuser"]
                return subprocess.run(prefix + ["mpi_cluster_node"] + cmd, capture_output=True, text=True, timeout=timeout)

        # Get local IPs of the master node container
        master_ips = []
        try:
            res = run_master_cmd(["hostname", "-I"])
            master_ips = res.stdout.strip().split()
        except Exception:
            pass

        def is_local_ip(ip):
            if ip in master_ips:
                return True
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind((ip, 0))
                s.close()
                return True
            except Exception:
                return False

        # Pre-parsear archivo 'hosts' para identificar local_ip y remote_ips
        # Con Tailscale sidecar, las IPs en 'hosts' son IPs de Tailscale (100.x.x.x)
        # que los contenedores ven como propias, por lo que is_local_ip funciona directamente
        local_ip = None
        remote_ips = []
        hosts_ips = []
        hosts_slots = {}
        
        if os.path.exists(self.hosts_path):
            with open(self.hosts_path, "r") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"): continue
                    parts = line.split()
                    ip_slots = parts[0]
                    
                    if ":" in ip_slots:
                        ip, slots = ip_slots.split(":")
                        slots = int(slots)
                    else:
                        ip = ip_slots
                        slots = 1
                        
                    hosts_ips.append(ip)
                    hosts_slots[ip] = slots
                    
                    if is_local_ip(ip):
                        local_ip = ip
                    else:
                        remote_ips.append(ip)

        # 1. Limpieza de procesos previos (sin iptables — ya no se necesita con Tailscale sidecar)
        for ip in hosts_ips:
            is_local = is_local_ip(ip)
            self.status_msg.emit(f"Limpiando nodo {'Local' if is_local else ip}...")
            if is_local:
                run_master_cmd(["sh", "-c", "pkill -9 -f cluster_worker ; pkill -9 -f mpiexec ; pkill -9 -f hydra_pmi_proxy"], root=True)
            else:
                run_master_cmd(["ssh", "-o", "ConnectTimeout=10", ip, "pkill -9 -f cluster_worker ; pkill -9 -f mpiexec ; pkill -9 -f hydra_pmi_proxy"])

        # 2. Compilar el backend de cluster_worker localmente en el Master
        self.status_msg.emit("Compilando backend en nodo Master...")
        run_master_cmd(["mpic++", "-fopenmp", "-o", "/home/mpiuser/reto_final/cluster_worker", "/home/mpiuser/reto_final/cluster_worker.c"])

        # 3. Configurar todos los nodos y preparar appfile.cfg
        # Con Tailscale sidecar las IPs de Tailscale son directas — no se necesitan aliases ni DNAT
        appfile_lines = []
        for ip in hosts_ips:
            is_local = is_local_ip(ip)
            slots = hosts_slots[ip]
            
            if is_local:
                binary_name = "/home/mpiuser/reto_final/cluster_worker"
                host_param = ip  # Usar IP de Tailscale directamente (es la IP real del contenedor)
            else:
                host_param = ip  # IP de Tailscale directa — sin aliases
                
                # Sincronizar directorios y verificar arquitectura de los nodos remotos
                self.status_msg.emit(f"Preparando nodo {ip}...")
                run_master_cmd(["ssh", "-o", "ConnectTimeout=10", ip, "mkdir -p /home/mpiuser/img"])
                run_master_cmd(["ssh", "-o", "ConnectTimeout=10", ip, "ln -sf /home/mpiuser/reto_final/images /home/mpiuser/images"])

                # Determinar nombre del binario según arquitectura del nodo remoto
                try:
                    res_arch = run_master_cmd(["ssh", "-o", "ConnectTimeout=10", ip, "uname -m"])
                    arch_type = res_arch.stdout.strip()
                except Exception:
                    arch_type = "x86_64"
                
                if "arm" in arch_type or "aarch64" in arch_type:
                    binary_name = "/home/mpiuser/reto_final/cluster_worker"
                    # Compilar nativamente en la Mac (ARM64) — ya no necesita binario separado
                    self.status_msg.emit(f"Compilando nativamente en nodo ARM64 ({ip})...")
                    run_master_cmd(["ssh", "-o", "ConnectTimeout=10", ip, "cd /home/mpiuser/reto_final && mpic++ -fopenmp -o cluster_worker cluster_worker.c"], timeout=60)
                else:
                    binary_name = "/home/mpiuser/reto_final/cluster_worker"
                    self.status_msg.emit(f"Compilando en nodo x86_64 ({ip})...")
                    run_master_cmd(["ssh", "-o", "ConnectTimeout=10", ip, "cd /home/mpiuser/reto_final && mpic++ -fopenmp -o cluster_worker cluster_worker.c"], timeout=60)
                
            appfile_lines.append(f"-env MPICH_INTERFACE_HOSTNAME {host_param} -host {host_param} -n {slots} {binary_name} {self.total_imagenes} {self.filter_mask} {self.k_grey} {self.k_color}")

        if not appfile_lines:
            appfile_lines.append(f"-host 127.0.0.1 -n 4 /home/mpiuser/reto_final/cluster_worker {self.total_imagenes} {self.filter_mask} {self.k_grey} {self.k_color}")

        # Escribir el archivo appfile.cfg
        appfile_path = os.path.join(self.base_dir, "appfile.cfg")
        with open(appfile_path, "w") as f:
            f.write("\n".join(appfile_lines) + "\n")

        # -----------------------------------------------------------------------
        # Helper: construir el comando mpiexec con las variables de entorno correctas
        # -----------------------------------------------------------------------
        def build_mpi_cmd(active_hosts_slots, start_img):
            total_slots = sum(active_hosts_slots.values())
            if self.in_container:
                return [
                    "sh", "-c",
                    f"FI_PROVIDER=tcp FI_TCP_IFACE=tailscale0 mpiexec -f hosts -n {total_slots} -iface tailscale0"
                    f" /home/mpiuser/reto_final/cluster_worker {self.total_imagenes} {self.filter_mask}"
                    f" {self.k_grey} {self.k_color} {start_img}"
                ]
            else:
                return [
                    "docker", "exec", "-i",
                    "-u", "mpiuser",
                    "-w", "/home/mpiuser/reto_final",
                    "-e", "FI_PROVIDER=tcp",
                    "-e", "FI_TCP_IFACE=tailscale0",
                    "mpi_cluster_node",
                    "mpiexec",
                    "-f", "hosts",
                    "-n", str(total_slots),
                    "-iface", "tailscale0",
                    "/home/mpiuser/reto_final/cluster_worker",
                    str(self.total_imagenes), str(self.filter_mask),
                    str(self.k_grey), str(self.k_color), str(start_img)
                ]

        # -----------------------------------------------------------------------
        # Helper: detectar qué imágenes ya están completamente procesadas
        # Revisa en TODOS los nodos el directorio img/ para encontrar la primera
        # imagen que NO tiene todos los archivos de filtro requeridos.
        # -----------------------------------------------------------------------
        def get_filter_suffixes():
            suffixes = []
            if self.filter_mask & 1:  suffixes.append("vg")
            if self.filter_mask & 2:  suffixes.append("vc")
            if self.filter_mask & 4:  suffixes.append("hg")
            if self.filter_mask & 8:  suffixes.append("hc")
            if self.filter_mask & 16: suffixes.append("dg")
            if self.filter_mask & 32: suffixes.append("dc")
            return suffixes

        def find_first_unprocessed(active_ips, total_imgs):
            """Revisa img/ en todos los nodos y retorna el primer ID de imagen no completado."""
            suffixes = get_filter_suffixes()
            if not suffixes:
                return None
            completed = set()
            # Verificar en cada nodo
            for ip in active_ips:
                try:
                    check_cmd = " && ".join(
                        [f"ls /home/mpiuser/reto_final/img/imagen_{{:03d}}_{s}.bmp".format(i)
                         for i in range(1, total_imgs + 1)
                         for s in suffixes]
                    )
                    # Construir comando más eficiente: listar todos los bmp y filtrar
                    list_cmd = "ls /home/mpiuser/reto_final/img/*.bmp 2>/dev/null || true"
                    if is_local_ip(ip):
                        res = run_master_cmd(["sh", "-c", list_cmd], timeout=10)
                    else:
                        res = run_master_cmd(["ssh", "-o", "ConnectTimeout=5", ip, list_cmd], timeout=15)
                    files = set(os.path.basename(f.strip()) for f in res.stdout.splitlines() if f.strip())
                    for i in range(1, total_imgs + 1):
                        if all(f"imagen_{i:03d}_{s}.bmp" in files for s in suffixes):
                            completed.add(i)
                except Exception:
                    pass

            for i in range(1, total_imgs + 1):
                if i not in completed:
                    return i
            return None  # Todo completo

        # -----------------------------------------------------------------------
        # Helper: detectar nodos caídos (no responden a SSH)
        # -----------------------------------------------------------------------
        def get_alive_hosts(current_hosts_slots):
            alive = {}
            for ip, slots in current_hosts_slots.items():
                if is_local_ip(ip):
                    alive[ip] = slots  # Master siempre está vivo
                else:
                    try:
                        res = run_master_cmd(["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                                               ip, "echo OK"], timeout=8)
                        if "OK" in res.stdout:
                            alive[ip] = slots
                        else:
                            self.log_received.emit(f"⚠️ Nodo {ip} no responde — excluido del reinicio")
                    except Exception:
                        self.log_received.emit(f"⚠️ Nodo {ip} no responde — excluido del reinicio")
            return alive

        # -----------------------------------------------------------------------
        # Helper: reescribir el archivo hosts con los nodos activos
        # -----------------------------------------------------------------------
        def write_hosts_file(active_hosts_slots):
            with open(self.hosts_path, "w") as f:
                for ip, slots in active_hosts_slots.items():
                    f.write(f"{ip}:{slots}\n")

        # -----------------------------------------------------------------------
        # Ejecución principal con lógica de resiliencia (hasta 2 reintentos)
        # -----------------------------------------------------------------------
        self.status_msg.emit("Iniciando ejecución en clúster...")
        total_slots = sum(hosts_slots.values())

        current_hosts_slots = dict(hosts_slots)
        start_img = 1
        pattern = re.compile(r"\[Master\] Progreso:\s+(\d+)/(\d+)")
        max_retries = 2
        dead_nodes = []       # IPs de nodos que se cayeron
        resilience_events = []  # Registro de eventos de recuperacion

        # -----------------------------------------------------------------------
        # Helper: emitir RESUMEN FINAL completo (reutilizable desde cualquier salida)
        # -----------------------------------------------------------------------
        def emit_resumen(elapsed):
            self.log_received.emit("\n" + "="*55)
            self.log_received.emit("=== RESUMEN FINAL DE EJECUCIÓN ===")
            self.log_received.emit("="*55)

            if dead_nodes:
                self.log_received.emit(f"⚠️  Nodos que se desconectaron: {', '.join(dead_nodes)}")
                for ev in resilience_events:
                    self.log_received.emit(f"    ↳ {ev}")
            else:
                self.log_received.emit("✅  Todos los nodos funcionaron sin interrupciones")

            # Contar imágenes por nodo (esclavos primero para evitar doble conteo por SCP)
            self.log_received.emit("\n📊 Imágenes procesadas por máquina:")
            suffixes = get_filter_suffixes()
            claimed = set()
            node_counts = {}
            slave_ips_r  = [(ip, s) for ip, s in current_hosts_slots.items() if not is_local_ip(ip)]
            master_ips_r = [(ip, s) for ip, s in current_hosts_slots.items() if is_local_ip(ip)]
            dead_ips_r   = [(d, 0) for d in dead_nodes]
            scan_order   = slave_ips_r + master_ips_r + dead_ips_r

            for ip, slots in scan_order:
                try:
                    list_cmd = "ls /home/mpiuser/reto_final/img/*.bmp 2>/dev/null || true"
                    if is_local_ip(ip):
                        res = run_master_cmd(["sh", "-c", list_cmd], timeout=10)
                        label = "Master (este nodo)"
                    else:
                        res = run_master_cmd(["ssh", "-o", "ConnectTimeout=5", ip, list_cmd], timeout=15)
                        label = ip
                    if ip in dead_nodes:
                        label += " ⚠️ (cayó durante ejecución)"
                    files = set(os.path.basename(f.strip()) for f in res.stdout.splitlines() if f.strip())
                    count = 0
                    for i in range(1, self.total_imagenes + 1):
                        if i not in claimed and suffixes and all(f"imagen_{i:03d}_{s}.bmp" in files for s in suffixes):
                            count += 1
                            if ip not in dead_nodes:
                                claimed.add(i)
                    pct = (100.0 * count / self.total_imagenes) if self.total_imagenes > 0 else 0
                    self.log_received.emit(f"    {label}: {count} imágenes ({pct:.1f}%)")
                    node_counts[ip] = count
                except Exception as e:
                    self.log_received.emit(f"    {ip}: no disponible ({e})")

            total_confirmadas = len(claimed)
            self.log_received.emit(f"    ─────────────────────────────")
            self.log_received.emit(f"    TOTAL únicas confirmadas: {total_confirmadas}/{self.total_imagenes} imágenes")
            self.log_received.emit("="*55 + "\n")

            # Restaurar hosts si fue modificado por resiliencia
            if dead_nodes:
                write_hosts_file(hosts_slots)
                self.log_received.emit("🔄 Archivo hosts restaurado con todos los nodos para la próxima ejecución.")

            # SCP Sync en el hilo del worker (no bloquea la UI)
            self.status_msg.emit("Sincronizando imágenes de los esclavos al Master...")
            for ip in hosts_ips:
                if not is_local_ip(ip):
                    try:
                        run_master_cmd(["ssh", "-o", "ConnectTimeout=5", ip,
                                        "mkdir -p /home/mpiuser/reto_final/img && "
                                        "cp -rf /home/mpiuser/img/* /home/mpiuser/reto_final/img/ 2>/dev/null || true"],
                                       timeout=15)
                    except Exception:
                        pass
                    try:
                        run_master_cmd(["scp", "-o", "ConnectTimeout=5", "-r",
                                        f"{ip}:/home/mpiuser/reto_final/img/*",
                                        "/home/mpiuser/reto_final/img/"], timeout=120)
                    except Exception:
                        pass
            self.status_msg.emit("Sincronización completada.")
            self.finished_successfully.emit(elapsed)


        for intento in range(max_retries + 1):
            if intento > 0:
                self.log_received.emit(f"\n🔄 === REINTENTO {intento}/{max_retries}: Buscando imágenes pendientes... ===")
                self.status_msg.emit(f"Recuperando tras fallo — reintento {intento}...")

                # Detectar nodos vivos
                alive = get_alive_hosts(current_hosts_slots)
                if not alive:
                    self.log_received.emit("❌ No hay nodos disponibles para continuar.")
                    break
                if alive != current_hosts_slots:
                    dead = set(current_hosts_slots) - set(alive)
                    for d in dead:
                        self.log_received.emit(f"🔴 Nodo caído detectado: {d} — removido del cluster")
                        dead_nodes.append(d)
                    current_hosts_slots = alive
                    write_hosts_file(current_hosts_slots)
                    self.log_received.emit("📝 Archivo hosts actualizado con nodos sobrevivientes")

                # Encontrar primera imagen sin procesar
                self.status_msg.emit("Escaneando imágenes completadas en todos los nodos...")
                first_pending = find_first_unprocessed(list(current_hosts_slots.keys()), self.total_imagenes)
                if first_pending is None:
                    self.log_received.emit("✅ Todas las imágenes ya están procesadas. No es necesario reiniciar.")
                    elapsed = time.time() - self.start_time
                    emit_resumen(elapsed)
                    return
                start_img = first_pending
                event_msg = (f"Reintento {intento}: reanudado desde imagen {start_img}, "
                             f"nodos caídos: {', '.join(dead_nodes) if dead_nodes else 'ninguno'}")
                resilience_events.append(event_msg)
                self.log_received.emit(f"📌 Reanudando desde imagen {start_img} "
                                       f"({self.total_imagenes - start_img + 1} imágenes pendientes)")

                # Recompilar en nodos sobrevivientes
                for ip, _ in current_hosts_slots.items():
                    if not is_local_ip(ip):
                        self.status_msg.emit(f"Recompilando en nodo sobreviviente {ip}...")
                        run_master_cmd(["ssh", "-o", "ConnectTimeout=10", ip,
                                        "cd /home/mpiuser/reto_final && mpic++ -fopenmp -o cluster_worker cluster_worker.c"],
                                       timeout=60)

            cmd = build_mpi_cmd(current_hosts_slots, start_img)
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, bufsize=1)

            while True:
                line = self.process.stdout.readline()
                if not line and self.process.poll() is not None:
                    break
                if line:
                    line_str = line.strip()
                    self.log_received.emit(line_str)
                    match = pattern.search(line_str)
                    if match:
                        completadas = int(match.group(1))
                        totales = int(match.group(2))
                        self.progress_updated.emit(completadas, totales)

            ret = self.process.poll()
            if ret == 0:
                elapsed = time.time() - self.start_time
                emit_resumen(elapsed)
                return
            else:
                self.log_received.emit(f"\n⚠️ MPI terminó con código {ret} — verificando si fue fallo de nodo...")
                if intento == max_retries:
                    self.log_received.emit("❌ Se agotaron los reintentos.")
                    break

        elapsed = time.time() - self.start_time
        # Restaurar el hosts original si fue modificado por resiliencia
        if dead_nodes:
            write_hosts_file(hosts_slots)  # Restaurar con todos los nodos originales
            self.log_received.emit("\n🔄 Archivo hosts restaurado con todos los nodos originales para la próxima ejecución.")
        self.finished_with_error.emit(ret)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Acerca de")
        self.setFixedSize(400, 300)
        self.setStyleSheet("background-color: #323232; color: white;")
        
        layout = QVBoxLayout(self)
        
        info_label = QLabel(
            "TC3003\n"
            "Tecnológico de Monterrey\n"
            "Campus Puebla\n"
            "Junio 2026\n\n"
            "Equipo:\n"
            "1- Emmanuel Torres Rios\n"
            "2- Daniel Flores Rojas\n"
            "3- Clúster Híbrido Heterogéneo MPICH"
        )
        info_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        info_label.setStyleSheet("padding: 20px;")
        layout.addWidget(info_label)
        
        logo_label = QLabel()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(base_dir, "img", "tec_logo.png")
        logo_pixmap = QPixmap(logo_path)
        if not logo_pixmap.isNull():
            logo_pixmap = logo_pixmap.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(logo_pixmap)
        logo_label.setAlignment(Qt.AlignBottom | Qt.AlignRight)
        layout.addWidget(logo_label)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Procesamiento de imágenes por Clúster MPI")
        self.setFixedSize(750, 550)
        self.setStyleSheet("background-color: #323232; color: #E0E0E0;")
        
        self.init_ui()
        self.init_menu()

    def init_menu(self):
        menubar = self.menuBar()
        menubar.setStyleSheet("background-color: #2b2b2b; color: white;")
        
        acerca_action = QAction("Acerca de", self)
        acerca_action.triggered.connect(self.show_about)
        
        help_menu = menubar.addMenu("Menu")
        help_menu.addAction(acerca_action)

    def show_about(self):
        dlg = AboutDialog(self)
        dlg.exec()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # Left side
        left_layout = QVBoxLayout()
        
        self.drop_area = DropArea()
        left_layout.addWidget(self.drop_area)
        
        tiempo_label = QLabel("Tiempo de ejecución")
        self.tiempo_entry = QLineEdit()
        self.tiempo_entry.setReadOnly(True)
        self.tiempo_entry.setStyleSheet("background-color: #2b2b2b; color: white; border: none; padding: 5px;")
        
        ruta_label = QLabel("Ruta de archivos")
        self.ruta_entry = QLineEdit()
        self.ruta_entry.setReadOnly(True)
        self.ruta_entry.setStyleSheet("background-color: #2b2b2b; color: white; border: none; padding: 5px;")
        
        # Progress Bar and Estimator
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet("QProgressBar { background-color: #2b2b2b; color: white; border: 1px solid #555; text-align: center; height: 20px; } QProgressBar::chunk { background-color: #00aa00; }")
        
        self.lbl_estimador = QLabel("Tiempo restante: N/A")
        self.lbl_estimador.setStyleSheet("color: #aaa; font-size: 11px;")
        
        left_layout.addSpacing(15)
        left_layout.addWidget(tiempo_label)
        left_layout.addWidget(self.tiempo_entry)
        left_layout.addWidget(ruta_label)
        left_layout.addWidget(self.ruta_entry)
        left_layout.addSpacing(15)
        left_layout.addWidget(self.progress_bar)
        left_layout.addWidget(self.lbl_estimador)
        left_layout.addStretch()
        
        # Right side
        right_layout = QVBoxLayout()
        right_layout.addSpacing(10)
        
        self.cb1 = QCheckBox("1- Vertical escala de grises (_vg)")
        self.cb2 = QCheckBox("2- Vertical escala a colores (_vc)")
        self.cb3 = QCheckBox("3- Horizontal escala de grises (_hg)")
        self.cb4 = QCheckBox("4- Horizontal escala a colores (_hc)")
        self.cb5 = QCheckBox("5- Desenfoque escala de grises (_dg)")
        self.cb6 = QCheckBox("6- Desenfoque escala a colores (_dc)")
        
        self.checkboxes = [self.cb1, self.cb2, self.cb3, self.cb4, self.cb5, self.cb6]
        for cb in self.checkboxes:
            right_layout.addWidget(cb)
            
        kernel1_layout = QHBoxLayout()
        kernel1_layout.addWidget(QLabel("     ")) # Indent
        self.kernel1_entry = QLineEdit("27")      # Valor por defecto
        self.kernel1_entry.setFixedWidth(50)
        self.kernel1_entry.setValidator(OddIntValidator())
        kernel1_layout.addWidget(self.kernel1_entry)
        kernel1_layout.addWidget(QLabel("Kernel"))
        kernel1_layout.addStretch()
        
        right_layout.insertLayout(right_layout.indexOf(self.cb5) + 1, kernel1_layout)
        
        kernel2_layout = QHBoxLayout()
        kernel2_layout.addWidget(QLabel("     ")) # Indent
        self.kernel2_entry = QLineEdit("27")      # Valor por defecto
        self.kernel2_entry.setFixedWidth(50)
        self.kernel2_entry.setValidator(OddIntValidator())
        kernel2_layout.addWidget(self.kernel2_entry)
        kernel2_layout.addWidget(QLabel("Kernel"))
        kernel2_layout.addStretch()
        
        right_layout.insertLayout(right_layout.indexOf(self.cb6) + 1, kernel2_layout)
        
        right_layout.addSpacing(10)
        
        todas_layout = QHBoxLayout()
        self.btn_todas = QPushButton("Todas")
        self.btn_todas.clicked.connect(self.select_all)
        self.btn_todas.setStyleSheet("background-color: #555; border-radius: 5px; padding: 5px 20px;")
        todas_layout.addWidget(self.btn_todas)
        todas_layout.addWidget(QLabel("Seleccionar todas las\ntransformaciones"))
        todas_layout.addStretch()
        right_layout.addLayout(todas_layout)
        
        right_layout.addSpacing(20)
        
        self.btn_ejecutar = QPushButton("Ejecutar en Clúster")
        self.btn_ejecutar.clicked.connect(self.ejecutar)
        self.btn_ejecutar.setStyleSheet("background-color: #00aa00; color: white; font-weight: bold; border-radius: 5px; padding: 8px 30px;")
        
        ejecutar_layout = QHBoxLayout()
        ejecutar_layout.addWidget(self.btn_ejecutar)
        ejecutar_layout.addStretch()
        
        right_layout.addLayout(ejecutar_layout)
        right_layout.addStretch()
        
        # Logo bottom right
        logo_layout = QHBoxLayout()
        logo_layout.addStretch()
        self.logo_label = QLabel()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(base_dir, "img", "tec_logo.png")
        logo_pixmap = QPixmap(logo_path)
        if not logo_pixmap.isNull():
            logo_pixmap = logo_pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.logo_label.setPixmap(logo_pixmap)
            self.logo_label.setStyleSheet("background-color: white; padding: 5px; border-radius: 5px;")
        logo_layout.addWidget(self.logo_label)
        
        right_layout.addLayout(logo_layout)
        
        main_layout.addLayout(left_layout, stretch=1)
        main_layout.addSpacing(30)
        main_layout.addLayout(right_layout, stretch=2)

    def select_all(self):
        all_checked = all(cb.isChecked() for cb in self.checkboxes)
        for cb in self.checkboxes:
            cb.setChecked(not all_checked)

    def ejecutar(self):
        total_imagenes = 150
        if self.drop_area.image_paths:
            total_imagenes = len(self.drop_area.image_paths)
            
        any_selected = any(cb.isChecked() for cb in self.checkboxes)
        if not any_selected:
            QMessageBox.warning(self, "Advertencia", "Selecciona al menos una transformación.")
            return
            
        if self.cb5.isChecked() and not self.kernel1_entry.text():
            QMessageBox.warning(self, "Advertencia", "Ingresa un kernel válido para el desenfoque en grises.")
            return
            
        if self.cb6.isChecked() and not self.kernel2_entry.text():
            QMessageBox.warning(self, "Advertencia", "Ingresa un kernel válido para el desenfoque a color.")
            return
            
        # Calcular máscara de bits de filtros activos
        filter_mask = 0
        if self.cb1.isChecked(): filter_mask |= 1
        if self.cb2.isChecked(): filter_mask |= 2
        if self.cb3.isChecked(): filter_mask |= 4
        if self.cb4.isChecked(): filter_mask |= 8
        if self.cb5.isChecked(): filter_mask |= 16
        if self.cb6.isChecked(): filter_mask |= 32
        
        k_grey = int(self.kernel1_entry.text()) if self.kernel1_entry.text() else 27
        k_color = int(self.kernel2_entry.text()) if self.kernel2_entry.text() else 27

        # Configurar UI para ejecución
        self.btn_ejecutar.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_estimador.setText("Iniciando ejecución en clúster...")
        self.tiempo_entry.setText("Procesando...")
        QApplication.processEvents()

        base_dir = os.path.dirname(os.path.abspath(__file__))
        hosts_path = os.path.join(base_dir, "hosts")
        in_container = os.path.exists('/home/mpiuser/reto_final')

        # Iniciar el proceso MPI en segundo plano usando un QThread para evitar congelamiento de la GUI
        self.worker = MPIWorker(total_imagenes, filter_mask, k_grey, k_color, hosts_path, base_dir, in_container)
        self.worker.log_received.connect(self.on_log_received)
        self.worker.progress_updated.connect(self.on_progress_updated)
        self.worker.status_msg.connect(self.lbl_estimador.setText)
        self.worker.finished_successfully.connect(self.on_finished_successfully)
        self.worker.finished_with_error.connect(self.on_finished_with_error)
        
        self.start_time = time.time()
        self.worker.start()

    def on_log_received(self, line):
        print(line, flush=True)

    def on_progress_updated(self, completadas, totales):
        pct = int((completadas / totales) * 100)
        self.progress_bar.setValue(pct)
        
        elapsed = time.time() - self.start_time
        if completadas > 0:
            avg_time = elapsed / completadas
            remaining_images = totales - completadas
            est_remaining = avg_time * remaining_images
            self.lbl_estimador.setText(f"Tiempo restante: {est_remaining:.1f} seg")

    def on_finished_successfully(self, elapsed):
        self.btn_ejecutar.setEnabled(True)
        self.progress_bar.setValue(100)
        self.lbl_estimador.setText("Ejecución completada.")
        self.tiempo_entry.setText(f"{elapsed:.4f} segundos")

        base_dir = os.path.dirname(os.path.abspath(__file__))
        img_dir = os.path.join(base_dir, "img")
        self.ruta_entry.setText(img_dir)

        self.drop_area.image_paths.clear()
        self.drop_area.update_display()

        QMessageBox.information(self, "Ejecución Exitosa",
                                f"Procesamiento distribuido completado.\n"
                                f"Las imágenes se han guardado en ./img/\n"
                                f"Tiempo: {elapsed:.4f} seg")

    def on_finished_with_error(self, exit_code):
        self.btn_ejecutar.setEnabled(True)
        self.progress_bar.setValue(0)
        self.lbl_estimador.setText(f"Error en ejecución (Código {exit_code})")
        self.tiempo_entry.setText("Error")
        
        QMessageBox.critical(self, "Error en Clúster", f"El proceso del clúster falló con código {exit_code}.\nVerifica los logs en consola.")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(50, 50, 50))
    palette.setColor(QPalette.WindowText, QColor(255, 255, 255))
    palette.setColor(QPalette.Base, QColor(35, 35, 35))
    palette.setColor(QPalette.Text, QColor(255, 255, 255))
    palette.setColor(QPalette.Button, QColor(85, 85, 85))
    palette.setColor(QPalette.ButtonText, QColor(255, 255, 255))
    app.setPalette(palette)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
