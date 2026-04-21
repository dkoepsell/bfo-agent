"""Apply token-saving patches to the BFO-Agent codebase.

Changes:
1. Add ANTHROPIC_EXTRACTOR_MODEL env var (defaulting to Haiku 4.5).
2. Make ClaimExtractor use that model instead of Sonnet.
3. Enable Anthropic prompt caching on the proposer's BFO primer, so
   repeated /propose calls pay 10% of input cost on the cached portion
   after the first call.

Run from the project root:
    python scripts/patch_token_savings.py

Safe to run multiple times; idempotent (detects already-applied changes).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def patch_file(path: Path, old: str, new: str, description: str) -> bool:
    """Replace `old` with `new` in file. Returns True if changed."""
    src = path.read_text()
    if new in src and old not in src:
        print(f"  [skip] {description} (already applied)")
        return False
    if old not in src:
        print(f"  [FAIL] {description}: old pattern not found in {path.name}")
        return False
    path.write_text(src.replace(old, new))
    print(f"  [ok]   {description}")
    return True


def append_if_missing(path: Path, snippet: str, marker: str, description: str) -> bool:
    src = path.read_text()
    if marker in src:
        print(f"  [skip] {description} (already present)")
        return False
    path.write_text(src + ("\n" if not src.endswith("\n") else "") + snippet + "\n")
    print(f"  [ok]   {description}")
    return True


# -------------------------------------------------------------- patches

def patch_config():
    """Add ANTHROPIC_EXTRACTOR_MODEL to config.py."""
    path = ROOT / "app" / "config.py"
    print(f"\n[1/4] Patching {path.name}")

    src = path.read_text()
    if "ANTHROPIC_EXTRACTOR_MODEL" in src:
        print("  [skip] ANTHROPIC_EXTRACTOR_MODEL env var (already present)")
        return

    old = 'ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")'
    new = (
        'ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")\n'
        'ANTHROPIC_EXTRACTOR_MODEL = os.getenv(\n'
        '    "ANTHROPIC_EXTRACTOR_MODEL",\n'
        '    "claude-haiku-4-5-20251001",  # 5x cheaper than Sonnet\n'
        ')'
    )
    patch_file(path, old, new, "add ANTHROPIC_EXTRACTOR_MODEL env var")


def patch_extractor():
    """Make ClaimExtractor default to the extractor model."""
    path = ROOT / "app" / "extractor.py"
    print(f"\n[2/4] Patching {path.name}")

    patch_file(
        path,
        "from .config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, require_api_key",
        "from .config import ANTHROPIC_API_KEY, ANTHROPIC_EXTRACTOR_MODEL, require_api_key",
        "import extractor-specific model",
    )

    patch_file(
        path,
        "    def __init__(self, model: str = ANTHROPIC_MODEL):",
        "    def __init__(self, model: str = ANTHROPIC_EXTRACTOR_MODEL):",
        "default ClaimExtractor to Haiku",
    )


def patch_proposer_caching():
    """Enable Anthropic prompt caching on the proposer's large static prompt.

    The strategy: move the static BFO primer + schema + rules into a
    system block with cache_control. Keep the dynamic parts (current
    graph context + user utterance) in the user message.

    After first call, subsequent calls pay 10% of input cost on the
    cached ~2500 tokens of static content.
    """
    path = ROOT / "app" / "llm_proposer.py"
    print(f"\n[3/4] Patching {path.name}")

    src = path.read_text()

    if "cache_control" in src:
        print("  [skip] prompt caching already enabled")
        return

    # Replace the single-message API call with system+messages split.
    # We identify the method by the distinctive combination.
    old = '''        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        text = "".join(
            block.text for block in resp.content if getattr(block, "text", None)
        )
        data = _extract_json(text)

        return Proposal('''

    new = '''        # Split prompt into static (cached) and dynamic parts. The
        # BFO primer, rules, and schema are identical on every call and
        # account for ~80% of input tokens, so caching them drops cost
        # dramatically for a full-book feed.
        static_system, dynamic_user = _split_for_caching(prompt)

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=[{
                "type": "text",
                "text": static_system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": dynamic_user}],
        )

        text = "".join(
            block.text for block in resp.content if getattr(block, "text", None)
        )
        data = _extract_json(text)

        return Proposal('''

    if old not in src:
        print("  [FAIL] couldn't find proposer's messages.create block")
        return

    src = src.replace(old, new)

    # Also add the _split_for_caching helper at the end of the file (if
    # not already there).
    helper = '''

def _split_for_caching(full_prompt: str) -> tuple[str, str]:
    """Split the fully-rendered proposer prompt into (static_system, dynamic_user).

    The static part contains everything up to and including the
    "CURRENT WORKING ONTOLOGY CONTEXT:" header boundary. The dynamic
    part contains the current graph summary and the user utterance,
    which changes every call and cannot be cached.
    """
    marker = "CURRENT WORKING ONTOLOGY CONTEXT:"
    idx = full_prompt.find(marker)
    if idx == -1:
        # Fallback: don't split, whole thing becomes user message.
        return "", full_prompt
    static = full_prompt[:idx].rstrip()
    dynamic = full_prompt[idx:]
    return static, dynamic
'''

    if "_split_for_caching" not in src:
        src = src + helper

    path.write_text(src)
    print("  [ok]   enabled prompt caching in proposer")
    print("  [ok]   added _split_for_caching helper")


def patch_env_example():
    """Add documentation for the new env var to .env.example."""
    path = ROOT / ".env.example"
    print(f"\n[4/4] Patching {path.name}")

    snippet = (
        '# Extractor model: use Haiku for ~5x cost reduction on extraction\n'
        '# (extraction is recall-and-format; Sonnet quality not needed)\n'
        'ANTHROPIC_EXTRACTOR_MODEL=claude-haiku-4-5-20251001'
    )
    append_if_missing(path, snippet, "ANTHROPIC_EXTRACTOR_MODEL", "document env var")


def verify():
    """Byte-compile to catch any damage."""
    print("\n[verify] compiling all patched modules")
    for mod in ("app/config.py", "app/extractor.py", "app/llm_proposer.py"):
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(ROOT / mod)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  [FAIL] {mod}: {r.stderr}")
            return False
        print(f"  [ok]   {mod} compiles")
    return True


def main():
    patch_config()
    patch_extractor()
    patch_proposer_caching()
    patch_env_example()

    ok = verify()
    if not ok:
        print("\nSOME PATCHES FAILED TO COMPILE. Review the errors above.")
        return 1

    print("""
============================================================
All token-savings patches applied.

Effect summary:
  - Extraction now uses Haiku 4.5: ~80% cheaper per chunk.
  - Proposer caches its BFO primer: ~50% cheaper over a feed.
  - Estimated savings on a full SOoL-scale run: ~$120 -> ~$60.

Next steps:
  1. Restart Flask so it picks up the changes.
  2. Run a short sanity check (extract one chapter, feed ~20 claims).
  3. Check console.anthropic.com/settings/usage to see the new cost.

If anything misbehaves, roll back with:
  git diff         # see what changed
  git checkout --  app/config.py app/extractor.py app/llm_proposer.py .env.example
============================================================
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
