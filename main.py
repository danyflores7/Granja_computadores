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
        
        def is_local_ip(ip):
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind((ip, 0))
                s.close()
                return True
            except Exception:
                return False

        # Obtener el hostname del contenedor master (local)
        if self.in_container:
            import socket
            master_hostname = socket.gethostname()
        else:
            try:
                res = subprocess.run(["docker", "exec", "mpi_cluster_node", "hostname"], capture_output=True, text=True, timeout=5)
                master_hostname = res.stdout.strip()
            except Exception:
                master_hostname = "mpi_cluster_node"

        # Pre-parsear archivo 'hosts' para identificar local_ip y remote_ips para las reglas SNAT
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

        def cleanup_and_setup_vip(ip, is_local):
            self.status_msg.emit(f"Configurando red en {'Local' if is_local else ip}...")
            # 1. Limpieza de procesos huérfanos de cluster_worker y gestores MPI/Hydra
            if is_local:
                if self.in_container:
                    cmd_kill = ["sh", "-c", "pkill -9 -f cluster_worker ; pkill -9 -f mpiexec ; pkill -9 -f hydra_pmi_proxy"]
                else:
                    cmd_kill = ["docker", "exec", "-u", "mpiuser", "mpi_cluster_node", "sh", "-c", "pkill -9 -f cluster_worker ; pkill -9 -f mpiexec ; pkill -9 -f hydra_pmi_proxy"]
            else:
                ssh_prefix = [] if self.in_container else ["docker", "exec", "-u", "mpiuser", "mpi_cluster_node"]
                cmd_kill = ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "pkill -9 -f cluster_worker ; pkill -9 -f mpiexec ; pkill -9 -f hydra_pmi_proxy"]
            try:
                subprocess.run(cmd_kill, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
            except Exception:
                pass

            # 2. Creación y configuración de la interfaz virtual vip0 y NAT iptables
            if is_local:
                # Intentar obtener la IP interna del contenedor Windows
                if self.in_container:
                    import socket
                    try:
                        container_ip = socket.gethostbyname(socket.gethostname())
                    except Exception:
                        container_ip = "172.18.0.2"
                else:
                    try:
                        res = subprocess.run(["docker", "exec", "mpi_cluster_node", "hostname", "-I"], capture_output=True, text=True, timeout=5)
                        container_ip = res.stdout.strip().split()[0]
                    except Exception:
                        container_ip = "172.18.0.2"

                # Calcular la IP del gateway local basándonos en la IP de la subred del contenedor
                local_gateway = ".".join(container_ip.split(".")[:3]) + ".1"

                if self.in_container:
                    cmds = [
                        ["sudo", "ip", "link", "delete", "vip0"],
                        ["sudo", "ip", "link", "add", "vip0", "type", "dummy"],
                        ["sudo", "ip", "addr", "add", f"{ip}/32", "dev", "vip0"],
                        ["sudo", "ip", "link", "set", "vip0", "up"],
                        ["sudo", "iptables", "-t", "nat", "-F", "PREROUTING"],
                        ["sudo", "iptables", "-t", "nat", "-A", "PREROUTING", "-d", f"{container_ip}/32", "-p", "tcp", "-m", "tcp", "--dport", "10000:10010", "-j", "DNAT", "--to-destination", ip],
                        ["sudo", "iptables", "-t", "nat", "-F", "INPUT"]
                    ]
                    for rip in remote_ips:
                        cmds.append(["sudo", "iptables", "-t", "nat", "-A", "INPUT", "-s", f"{local_gateway}/32", "-p", "tcp", "-m", "tcp", "--dport", "10000:10010", "-j", "SNAT", "--to-source", rip])
                else:
                    cmds = [
                        ["docker", "exec", "-u", "root", "mpi_cluster_node", "ip", "link", "delete", "vip0"],
                        ["docker", "exec", "-u", "root", "mpi_cluster_node", "ip", "link", "add", "vip0", "type", "dummy"],
                        ["docker", "exec", "-u", "root", "mpi_cluster_node", "ip", "addr", "add", f"{ip}/32", "dev", "vip0"],
                        ["docker", "exec", "-u", "root", "mpi_cluster_node", "ip", "link", "set", "vip0", "up"],
                        ["docker", "exec", "-u", "root", "mpi_cluster_node", "iptables", "-t", "nat", "-F", "PREROUTING"],
                        ["docker", "exec", "-u", "root", "mpi_cluster_node", "iptables", "-t", "nat", "-A", "PREROUTING", "-d", f"{container_ip}/32", "-p", "tcp", "-m", "tcp", "--dport", "10000:10010", "-j", "DNAT", "--to-destination", ip],
                        ["docker", "exec", "-u", "root", "mpi_cluster_node", "iptables", "-t", "nat", "-F", "INPUT"]
                    ]
                    for rip in remote_ips:
                        cmds.append(["docker", "exec", "-u", "root", "mpi_cluster_node", "iptables", "-t", "nat", "-A", "INPUT", "-s", f"{local_gateway}/32", "-p", "tcp", "-m", "tcp", "--dport", "10000:10010", "-j", "SNAT", "--to-source", rip])
            else:
                ssh_prefix = [] if self.in_container else ["docker", "exec", "-u", "mpiuser", "mpi_cluster_node"]
                
                # Intentar obtener la IP interna del contenedor Mac via SSH
                try:
                    res = subprocess.run(ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "hostname -I"], capture_output=True, text=True, timeout=8)
                    remote_container_ip = res.stdout.strip().split()[0]
                except Exception:
                    remote_container_ip = "172.19.0.2"

                # Detectar la arquitectura del nodo remoto
                try:
                    res_arch = subprocess.run(ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "uname -m"], capture_output=True, text=True, timeout=5)
                    arch_type = res_arch.stdout.strip()
                except Exception:
                    arch_type = "x86_64"

                # Calcular la IP del gateway remoto basándonos en la IP de la subred del contenedor esclavo
                remote_gateway = ".".join(remote_container_ip.split(".")[:3]) + ".1"

                cmds = [
                    ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "sudo ip link delete vip0"],
                    ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "sudo ip link add vip0 type dummy"],
                    ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, f"sudo ip addr add {ip}/32 dev vip0"],
                    ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "sudo ip link set vip0 up"],
                    ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "sudo iptables -t nat -F PREROUTING"],
                    ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, f"sudo iptables -t nat -A PREROUTING -d {remote_container_ip}/32 -p tcp -m tcp --dport 10000:10010 -j DNAT --to-destination {ip}"],
                    ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "sudo iptables -t nat -F INPUT"],
                    ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "mkdir -p /home/mpiuser/img"],
                    ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "ln -sf /home/mpiuser/reto_final/images /home/mpiuser/images"]
                ]

                # Copiar o compilar el binario correcto según la arquitectura
                if "arm" in arch_type or "aarch64" in arch_type:
                    cmds.append(ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "cp /home/mpiuser/reto_final/cluster_worker_mac /home/mpiuser/cluster_worker_mac"])
                else:
                    # En x86_64, compilar el binario para compatibilidad de librerías locales
                    cmds.append(ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "mpic++ -fopenmp -o /home/mpiuser/reto_final/cluster_worker /home/mpiuser/reto_final/cluster_worker.c"])

                # Usar la IP del master local como origen de SNAT en el esclavo
                active_local_ip = local_ip if local_ip else "192.168.1.73"
                cmds.append(ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, f"sudo iptables -t nat -A INPUT -s {remote_gateway}/32 -p tcp -m tcp --dport 10000:10010 -j SNAT --to-source {active_local_ip}"])
                
                # 3. Mapear el hostname del master en el archivo /etc/hosts del nodo remoto
                if master_hostname:
                    active_local_ip = local_ip if local_ip else "192.168.1.73"
                    cmds.append(ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, f"sudo sh -c 'grep -q {master_hostname} /etc/hosts || echo {active_local_ip} {master_hostname} >> /etc/hosts'"])
            
            for cmd in cmds:
                try:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
                except Exception:
                    pass

        # Generar alias para cada IP remota para saltarse el chequeo de IP de Hydra en Docker Desktop
        ip_to_alias = {}
        for idx, rip in enumerate(remote_ips):
            ip_to_alias[rip] = f"esclavo_{idx + 1}"

        if remote_ips:
            ssh_config_lines = ["Host *", "    Port 2222", "    StrictHostKeyChecking no", "    UserKnownHostsFile /dev/null", ""]
            for rip, alias in ip_to_alias.items():
                ssh_config_lines.append(f"Host {alias}")
                ssh_config_lines.append(f"    HostName {rip}")
                ssh_config_lines.append(f"    Port 2222")
                ssh_config_lines.append("")
            
            ssh_config_content = "\n".join(ssh_config_lines)
            if self.in_container:
                try:
                    with open("/home/mpiuser/.ssh/config", "w") as sf:
                        sf.write(ssh_config_content)
                except Exception:
                    pass
            else:
                try:
                    import tempfile
                    with tempfile.NamedTemporaryFile(mode='w', delete=False) as tf:
                        tf.write(ssh_config_content)
                        tf_path = tf.name
                    subprocess.run(["docker", "cp", tf_path, "mpi_cluster_node:/home/mpiuser/.ssh/config"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    subprocess.run(["docker", "exec", "-u", "root", "mpi_cluster_node", "chown", "mpiuser:root", "/home/mpiuser/.ssh/config"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    subprocess.run(["docker", "exec", "-u", "root", "mpi_cluster_node", "chmod", "600", "/home/mpiuser/.ssh/config"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    os.unlink(tf_path)
                except Exception:
                    pass

            etc_hosts_line = "172.18.0.1 " + " ".join(ip_to_alias.values())
            if self.in_container:
                try:
                    subprocess.run(["sudo", "sh", "-c", f"grep -q '{etc_hosts_line}' /etc/hosts || echo '{etc_hosts_line}' >> /etc/hosts"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
            else:
                try:
                    subprocess.run(["docker", "exec", "-u", "root", "mpi_cluster_node", "sh", "-c", f"grep -q '{etc_hosts_line}' /etc/hosts || echo '{etc_hosts_line}' >> /etc/hosts"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

        # Configurar todos los nodos del clúster
        appfile_lines = []
        for ip in hosts_ips:
            is_local = is_local_ip(ip)
            slots = hosts_slots[ip]
            
            if is_local:
                binary_name = "/home/mpiuser/reto_final/cluster_worker"
                host_param = "127.0.0.1"
            else:
                alias = ip_to_alias[ip]
                host_param = alias
                
                # Determinar nombre del binario según arquitectura del nodo remoto
                ssh_prefix = [] if self.in_container else ["docker", "exec", "-u", "mpiuser", "mpi_cluster_node"]
                try:
                    res_arch = subprocess.run(ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", host_param, "uname -m"], capture_output=True, text=True, timeout=5)
                    arch_type = res_arch.stdout.strip()
                except Exception:
                    arch_type = "x86_64"
                
                if "arm" in arch_type or "aarch64" in arch_type:
                    binary_name = "/home/mpiuser/cluster_worker_mac"
                else:
                    binary_name = "/home/mpiuser/reto_final/cluster_worker"
                
            cleanup_and_setup_vip(ip, is_local)
            
            appfile_lines.append(f"-env MPICH_INTERFACE_HOSTNAME {ip} -env FI_TCP_IFACE vip0 -host {host_param} -n {slots} {binary_name} {self.total_imagenes} {self.filter_mask} {self.k_grey} {self.k_color}")

        if not appfile_lines:
            appfile_lines.append(f"-env MPICH_INTERFACE_HOSTNAME 127.0.0.1 -host 127.0.0.1 -n 4 /home/mpiuser/reto_final/cluster_worker {self.total_imagenes} {self.filter_mask} {self.k_grey} {self.k_color}")

        # Escribir el archivo appfile.cfg
        appfile_path = os.path.join(self.base_dir, "appfile.cfg")
        with open(appfile_path, "w") as f:
            f.write("\n".join(appfile_lines) + "\n")

        # Iniciar ejecución de MPI
        self.status_msg.emit("Iniciando ejecución en clúster...")
        
        # Determinar si estamos en el contenedor Docker o en el Host Windows
        if self.in_container:
            os.environ["MPIEXEC_PORT_RANGE"] = "10000:10010"
            os.environ["MPICH_PORT_RANGE"] = "10001:10010"
            cmd = [
                "mpiexec",
                "-f", "hosts",
                "-genv", "FI_PROVIDER", "tcp",
                "-genv", "FI_TCP_PORT_LOW_RANGE", "10001",
                "-genv", "FI_TCP_PORT_HIGH_RANGE", "10010",
                "-configfile", "appfile.cfg"
            ]
        else:
            cmd = [
                "docker", "exec", "-i", 
                "-u", "mpiuser", 
                "-e", "MPIEXEC_PORT_RANGE=10000:10010", 
                "-e", "MPICH_PORT_RANGE=10001:10010",
                "-w", "/home/mpiuser/reto_final",
                "mpi_cluster_node",
                "mpiexec",
                "-f", "hosts",
                "-genv", "FI_PROVIDER", "tcp",
                "-genv", "FI_TCP_PORT_LOW_RANGE", "10001",
                "-genv", "FI_TCP_PORT_HIGH_RANGE", "10010",
                "-configfile", "appfile.cfg"
            ]

        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        
        pattern = re.compile(r"\[Master\] Progreso:\s+(\d+)/(\d+)")
        
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
                    
        elapsed = time.time() - self.start_time
        ret = self.process.poll()
        if ret == 0:
            self.finished_successfully.emit(elapsed)
        else:
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
        self.lbl_estimador.setText("Sincronizando imágenes de la Mac...")
        QApplication.processEvents()
        
        # Sincronizar imágenes procesadas localmente en la Mac hacia el volumen compartido del Host
        base_dir = os.path.dirname(os.path.abspath(__file__))
        hosts_path = os.path.join(base_dir, "hosts")
        hosts_ips = []
        if os.path.exists(hosts_path):
            with open(hosts_path, "r") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        parts = line.split()
                        ip = parts[0].split(":")[0]
                        hosts_ips.append(ip)
                        
        def is_local_ip(ip):
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind((ip, 0))
                s.close()
                return True
            except Exception:
                return False

        in_container = os.path.exists('/home/mpiuser/reto_final')
        ssh_prefix = [] if in_container else ["docker", "exec", "-u", "mpiuser", "mpi_cluster_node"]
        
        for ip in hosts_ips:
            if not is_local_ip(ip):
                cmd_sync = ssh_prefix + ["ssh", "-o", "ConnectTimeout=5", "-p", "2222", ip, "mkdir -p /home/mpiuser/reto_final/img && cp -rf /home/mpiuser/img/* /home/mpiuser/reto_final/img/"]
                try:
                    subprocess.run(cmd_sync, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

        self.lbl_estimador.setText("Ejecución completada.")
        self.tiempo_entry.setText(f"{elapsed:.4f} segundos")
        
        img_dir = os.path.join(base_dir, "img")
        self.ruta_entry.setText(img_dir)
        
        self.drop_area.image_paths.clear()
        self.drop_area.update_display()
        
        QMessageBox.information(self, "Ejecución Exitosa", f"Procesamiento distribuido completado.\nLas imágenes se han guardado en ./img/\nTiempo: {elapsed:.4f} seg")

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
