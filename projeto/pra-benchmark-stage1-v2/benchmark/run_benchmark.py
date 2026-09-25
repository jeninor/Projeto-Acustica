import argparse, csv, os, platform, resource, statistics, time
from pathlib import Path
import numpy as np
import psutil
import pyroomacoustics as pra
from pyroomacoustics.directivities import Cardioid, DirectionVector

ROOM_DIM=np.array([6.0,5.0,3.0]); SOURCE_POS=np.array([2.0,2.5,1.5])
FS=16000; MAX_ORDER=10; ABSORPTION=0.35

def mic_grid():
    xs=np.linspace(1.0,5.0,5); ys=np.linspace(0.75,4.25,5); z=1.2
    return np.asarray([[x,y,z] for y in ys for x in xs],float)

def directivity(pattern):
    if pattern=="omni": return None
    return Cardioid(orientation=DirectionVector(azimuth=0,colatitude=90,degrees=True),gain=1.0)

def make_room(pattern):
    room=pra.ShoeBox(ROOM_DIM,fs=FS,materials=pra.Material(ABSORPTION),max_order=MAX_ORDER)
    room.add_source(SOURCE_POS,directivity=directivity(pattern))
    for m in mic_grid(): room.add_microphone(m)
    return room

def rir_summary(room):
    # PRA convention: room.rir[microphone][source]
    rirs=[np.asarray(room.rir[m][0],float) for m in range(len(room.rir))]
    if len(rirs)!=25: raise RuntimeError(f"Expected 25 microphones, got {len(rirs)}")
    lengths=np.array([len(r) for r in rirs])
    energies=np.array([np.sum(r*r) for r in rirs],float)
    peaks=np.array([np.max(np.abs(r)) for r in rirs],float)
    em=float(energies.mean()); es=float(energies.std(ddof=1))
    return dict(
        n_mics=len(rirs),rir_len_min=int(lengths.min()),rir_len_max=int(lengths.max()),
        rir_energy_mean=em,rir_energy_std=es,rir_energy_cv=es/em if em else float("nan"),
        rir_energy_min=float(energies.min()),rir_energy_max=float(energies.max()),
        rir_energy_sum=float(energies.sum()),rir_peak_mean=float(peaks.mean()),
        rir_peak_min=float(peaks.min()),rir_peak_max=float(peaks.max())
    )

def run_once(pattern):
    room=make_room(pattern); proc=psutil.Process(os.getpid())
    before=proc.memory_info().rss/(1024**2)
    t0=time.perf_counter(); room.compute_rir(); elapsed=time.perf_counter()-t0
    after=proc.memory_info().rss/(1024**2)
    return dict(pattern=pattern,elapsed_s=elapsed,rss_before_mb=before,rss_after_mb=after,
                peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024.0,
                **rir_summary(room))

def envinfo():
    cpu="unknown"
    try:
        for line in open("/proc/cpuinfo",encoding="utf-8"):
            if line.lower().startswith("model name"):
                cpu=line.split(":",1)[1].strip(); break
    except Exception: pass
    return dict(environment="local-docker",python=platform.python_version(),
                pyroomacoustics=getattr(pra,"__version__","unknown"),numpy=np.__version__,
                platform=platform.platform(),cpu=cpu,cpu_count_logical=psutil.cpu_count(True),
                cpu_count_physical=psutil.cpu_count(False),pra_num_threads=os.getenv("PRA_NUM_THREADS",""),
                omp_num_threads=os.getenv("OMP_NUM_THREADS",""),openblas_num_threads=os.getenv("OPENBLAS_NUM_THREADS",""),
                mkl_num_threads=os.getenv("MKL_NUM_THREADS",""),room_x_m=6.0,room_y_m=5.0,room_z_m=3.0,
                source_x_m=2.0,source_y_m=2.5,source_z_m=1.5,fs_hz=FS,max_order=MAX_ORDER,absorption=ABSORPTION)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--pattern",choices=["omni","cardioid","both"],default="both")
    ap.add_argument("--warmup",type=int,default=3); ap.add_argument("--repetitions",type=int,default=20)
    ap.add_argument("--output",default="/app/results/local_baseline.csv"); a=ap.parse_args()
    pats=["omni","cardioid"] if a.pattern=="both" else [a.pattern]
    env=envinfo(); rows=[]
    print("=== Pyroomacoustics Stage 1 v2 ===")
    for k,v in env.items(): print(f"{k}: {v}")
    chk=make_room("omni")
    print("configured_mics:",chk.mic_array.R.shape[1]); print("configured_sources:",len(chk.sources))
    for p in pats:
        print(f"\n--- {p.upper()} ---")
        for i in range(a.warmup): print(f"warm-up {i+1}/{a.warmup}"); run_once(p)
        vals=[]
        for rep in range(1,a.repetitions+1):
            r=run_once(p); vals.append(r["elapsed_s"]); rows.append({**env,"repeat":rep,**r})
            print(f"repeat {rep:02d}: {r['elapsed_s']:.6f} s | n_mics={r['n_mics']} | energy_mean={r['rir_energy_mean']:.6e}")
        print(f"{p}: median={statistics.median(vals):.6f} s | mean={statistics.mean(vals):.6f} s | min={min(vals):.6f} s | max={max(vals):.6f} s")
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print(f"\nCSV saved: {out}")

if __name__=="__main__": main()
