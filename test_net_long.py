import socket
import sys

if len(sys.argv) < 3:
    print("Usage: python3 test_net_long.py listen <port>")
    sys.exit(1)

mode = sys.argv[1]
port = int(sys.argv[2])

if mode == "listen":
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', port))
    s.listen(5)
    print(f"LISTENING ON {port}", flush=True)
    s.settimeout(30)  # 30 second timeout
    accepted = 0
    while accepted < 3:
        try:
            conn, addr = s.accept()
            print(f"ACCEPTED FROM {addr}", flush=True)
            conn.close()
            accepted += 1
        except socket.timeout:
            print("TIMEOUT after 30s", flush=True)
            break
        except Exception as e:
            print(f"ERROR: {e}", flush=True)
            break
    s.close()
    print("LISTENER DONE", flush=True)
