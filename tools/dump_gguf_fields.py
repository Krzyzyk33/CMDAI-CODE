"""Diagnostic: dump GGUF metadata fields for real user models.

Usage: python tools/dump_gguf_fields.py [models_dir]
Step 1 of MCP+vision plan - no guessing field names from memory.
"""
import os
import sys

try:
    from gguf import GGUFReader
except ImportError:
    print("gguf package not installed")
    sys.exit(1)


def dump(path: str) -> None:
    print("=" * 70)
    print(f"FILE: {path} ({os.path.getsize(path) / (1024**2):.1f} MB)")
    print("=" * 70)
    reader = GGUFReader(path)
    names = sorted(f.name for f in reader.fields.values())
    print(f"FIELD COUNT: {len(names)}")
    for n in names:
        print(f"  - {n}")
    interesting = [n for n in names if n.startswith("clip.") or "vision" in n.lower()
                   or "mmproj" in n.lower() or "tokenizer" in n.lower()
                   or "chat_template" in n.lower() or " ILM" in n]
    print(f"\nINTERESTING ({len(interesting)}):")
    for n in interesting:
        print(f"  * {n}")
    # tokenizer special tokens check
    try:
        toks = reader.fields.get("tokenizer.ggml.tokens")
        types = reader.fields.get("tokenizer.ggml.token_type")
        if toks is not None and types is not None:
            raw_toks = toks.parts[toks.data[0]] if toks.data else None
            print("\nTOKENIZER: present (check <think> below via strings)")
    except Exception as e:
        print(f"\nTOKENIZER check failed: {e}")
    print()


def main() -> None:
    models_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.getcwd(), "models")
    if not os.path.isdir(models_dir):
        print(f"no dir: {models_dir}")
        sys.exit(1)
    for f in sorted(os.listdir(models_dir)):
        if f.endswith(".gguf"):
            dump(os.path.join(models_dir, f))


if __name__ == "__main__":
    main()
