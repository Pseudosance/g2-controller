# Relaunch G2 Ring on the Flipper (COM3), immediately tell the glasses to dial it
# (inside the 30 s fast-advertising window), and log the Flipper for N seconds.
# usage: python flipper_session.py 300
import serial, sys, time, subprocess
s = serial.Serial("COM3", 230400, timeout=0.3)
s.write(b"\r"); time.sleep(0.5); s.read(8192)
s.write(b"loader close\r"); time.sleep(1.5); s.read(4096)
s.write(b"loader open /ext/apps/g2_ring.fap\r"); time.sleep(0.4); s.read(4096)
s.write(b"log debug\r")
out = open("flipper_log.txt", "wb")
p = subprocess.Popen([sys.executable, "introduce_ring.py", "--ring", "F6:2F:22:26:E1:80", "--glasses", "F0:58:C4:E4:EC:9B", "--wait", "10"], stdout=open("intro_out.txt","w"), stderr=subprocess.STDOUT)
end = time.time() + float(sys.argv[1])
while time.time() < end:
    d = s.read(4096)
    if d: out.write(d); out.flush()
s.write(b"\x03"); s.close()
