import serial, sys, time
s = serial.Serial("COM3", 230400, timeout=0.3)
s.write(b"\r"); time.sleep(0.5); s.read(8192)
s.write(b"log debug\r")
out = open(sys.argv[2], "wb"); end = time.time() + float(sys.argv[1])
while time.time() < end:
    d = s.read(4096)
    if d: out.write(d); out.flush()
s.write(b"\x03"); s.close()
