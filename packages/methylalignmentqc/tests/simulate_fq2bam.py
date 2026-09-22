import time
import sys
import datetime

# Simulate fq2bam output
print("Starting fq2bam simulation...")

def log(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%b-%d %H:%M:%S")
    print(f"[PB Info {timestamp}] {msg}")
    sys.stdout.flush()

# Phase 1
log("------------------------------------------------------------------------------")
log("||                 Parabricks accelerated Genomics Pipeline                 ||")
log("||                              Version 4.6.0-1                             ||")
log("||                      GPU-PBBWA mem, Sorting Phase-I                      ||")
log("------------------------------------------------------------------------------")
time.sleep(0.2)
# Simulate Phase I lines: # 10 ... pool: 1 <bases> bases/GPU/minute: <rate>
for i, bases in enumerate([9906648, 168396395, 316975713]):
    time.sleep(0.2)
    # [PB Info 2025-Dec-24 23:19:06] # 10  0  1  0  0   0 pool:  1 9906648 bases/GPU/minute: 59439888.0
    log(f"# 10  0  1  0  0   0 pool:  1 {bases} bases/GPU/minute: 59439888.0")

# Phase 2
log("------------------------------------------------------------------------------")
log("||                 Parabricks accelerated Genomics Pipeline                 ||")
log("||                              Version 4.6.0-1                             ||")
log("||                             Sorting Phase-II                             ||")
log("------------------------------------------------------------------------------")
time.sleep(0.2)
for p in [5.5, 33.3, 89.9, 100.0]:
    time.sleep(0.2)
    log(f"{p}")

log("Done.")
