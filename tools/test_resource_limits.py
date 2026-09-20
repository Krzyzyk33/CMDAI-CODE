"""Manual test: real GGUF metadata + dynamic limits + 97% threshold."""
import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "src"))

import psutil
from cmdai.core.resource_limits import (
    COMPACTION_THRESHOLD,
    estimate_kv_cache_bytes_per_token,
    find_local_model_file,
    get_dynamic_context_limit,
    read_gguf_metadata,
    should_summarize,
)

print(f"RAM available: {psutil.virtual_memory().available / (1024**3):.1f} GB")
print(f"COMPACTION_THRESHOLD: {COMPACTION_THRESHOLD}")
assert COMPACTION_THRESHOLD == 0.97

seen_ctx, seen_layer = set(), set()
for f in sorted(os.listdir("models")):
    if not f.endswith(".gguf"):
        continue
    m = read_gguf_metadata(os.path.join("models", f))
    print(f"{f}: arch={m['arch']} n_ctx_train={m['n_ctx_train']} n_layer={m['n_layer']} "
          f"n_head_kv={m['n_head_kv']} n_embd_head_k={m['n_embd_head_k']} "
          f"size={m['file_size_gb']:.2f}GB estimated={m['estimated']} "
          f"kvB/tok={estimate_kv_cache_bytes_per_token(m)}")
    seen_ctx.add(m["n_ctx_train"])
    seen_layer.add(m["n_layer"])
    lim = get_dynamic_context_limit(f, "local_gguf",
                                    model_path=find_local_model_file(f, workdir=os.getcwd()))
    print(f"  -> dynamic limit: {lim}")
assert len(seen_ctx) > 1, "n_ctx_train identical for all models!"
assert len(seen_layer) > 1, "n_layer identical for all models!"

assert should_summarize(97000, 100000) is True
assert should_summarize(96999, 100000) is False
print("threshold 100000 -> 97000 OK")

lim_missing = get_dynamic_context_limit("nope.gguf", "local_gguf")
print(f"missing file fallback limit: {lim_missing}")
assert lim_missing >= 2048
print("ALL RESOURCE TESTS OK")
