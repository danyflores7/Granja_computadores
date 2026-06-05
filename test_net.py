import socket
import sys
import subprocess
import time

# usage:
# python test_net.py listen <port>
# python test_net.py connect <ip> <port>

mode = sys.argv[1]

if mode == "listen":
    port = int(sys.argv[2])
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('0.0.0.0', port))
        s.listen(1)
        print(f"LISTENING ON {port}", flush=True)
        s.settimeout(5)
        conn, addr = s.accept()
        print(f"ACCEPTED FROM {addr}", flush=True)
        conn.close()
    except socket.timeout:
        print("TIMEOUT", flush=True)
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
    finally:
        s.close()

elif mode == "connect":
    ip = sys.argv[2]
    port = int(sys.argv[3])
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect((ip, port))
        print("SUCCESS", flush=True)
    except Exception as e:
        print(f"FAIL: {e}", flush=True)
    finally:
        s.close()
