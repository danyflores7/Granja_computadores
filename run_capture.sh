#!/bin/bash
# 1. Start tcpdump in background as root
tcpdump -i eth0 -w /home/mpiuser/reto_final/debug_capture.pcap port 2222 or portrange 10000-10010 -U &
TCPDUMP_PID=$!
sleep 1

# 2. Run mpiexec as mpiuser with env variables preserved and 15 seconds timeout
sudo -u mpiuser MPIEXEC_PORT_RANGE=10000:10010 MPICH_PORT_RANGE=10000:10010 timeout 15 mpiexec -f debug_hosts -genv FI_PROVIDER tcp -genv FI_TCP_PORT_LOW_RANGE 10000 -genv FI_TCP_PORT_HIGH_RANGE 10010 -configfile debug_appfile.cfg

# 3. Kill tcpdump
kill $TCPDUMP_PID
sleep 1

# 4. Print packet summary
tcpdump -r /home/mpiuser/reto_final/debug_capture.pcap -n -nn
