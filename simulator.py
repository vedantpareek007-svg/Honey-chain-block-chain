#!/usr/bin/env python3
"""External IoT simulator — POSTs readings to the API like a real ESP32 would.
Usage: python simulator.py [--url http://localhost:8000] [--hives HIVE001 HIVE003]"""
import argparse, random, time, requests
HIVES=[("HIVE001",31.0,61.0,24.5,26.5775,93.1711),("HIVE002",31.5,63.0,25.2,26.5781,93.1720),
       ("HIVE003",30.5,65.0,24.0,26.9520,94.1660),("HIVE004",32.0,60.0,26.0,26.9495,94.1701),
       ("HIVE005",29.5,68.0,23.5,25.5788,91.8933)]
ap=argparse.ArgumentParser();ap.add_argument("--url",default="http://localhost:8000")
ap.add_argument("--interval",type=float,default=5);ap.add_argument("--hives",nargs="*")
ap.add_argument("--hot",action="store_true");a=ap.parse_args()
sel=[h for h in HIVES if not a.hives or h[0] in a.hives]
state={h[0]:[h[1],h[2],h[3]] for h in sel}
print(f"📡 simulating {len(sel)} hives → {a.url}/api/iot/readings")
while True:
    for code,bt,bh,bw,lat,lon in sel:
        t,h,w=state[code]
        t=min(max(t+random.uniform(-.4,.4),bt-4),bt+7);h=min(max(h+random.uniform(-1,1),35),95)
        w=max(w+random.uniform(-.05,.12),0)
        if a.hot and code=="HIVE004":t=max(t,36.0+random.uniform(0,1.5))
        state[code]=[t,h,w]
        try:
            r=requests.post(f"{a.url}/api/iot/readings",json={
                "hive_id":code,"temperature":round(t,1),"humidity":round(h,1),
                "weight":round(w,1),"latitude":lat,"longitude":lon},timeout=10)
            print(f"  {code}: {t:.1f}°C {h:.1f}% {w:.1f}kg → {r.status_code}")
        except requests.RequestException as e:
            print(f"  ⚠ backend unreachable ({e.__class__.__name__})")
    time.sleep(a.interval)