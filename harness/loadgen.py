import httpx, sys, random
GATEWAY = "http://127.0.0.1:8000/handle"

BASE = 100.0      # starting heap, MB
SLOPE = 8.0       # MB added per trace when leaking (the real signal)
SIGMA = 15.0      # noise stddev (the jitter the operator must see through)

def run(n=10, leak=False):
    for i in range(n):
        if leak:
            heap = BASE + SLOPE * i + random.gauss(0, SIGMA)   # climbs through noise
        else:
            heap = BASE + random.gauss(0, SIGMA)               # flat jitter
        r = httpx.get(GATEWAY, params={"leak": leak, "heap_mb": round(heap, 1)})
        print(i, r.json()["trace_id"], "leak" if leak else "clean", "heap", round(heap, 1))

if __name__ == "__main__":
    leak = "--leak" in sys.argv
    # --n N  -> number of traces; default 10
    n = 10
    if "--n" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1])
    run(n=n, leak=leak)