"""Run the Make-discovered verify targets, serially within an isolated shard.

CircleCI gives each shard a separate checkout/container. Local runs default to
all targets; this runner never introduces concurrent writes to a checkout.
"""
import argparse
import os
import subprocess
import tempfile
import time


def select_targets(targets: list[str], index: int, total: int) -> list[str]:
    if total < 1 or not 0 <= index < total:
        raise ValueError(f"shard index must be in [0, total), got {index}/{total}")
    if not targets or len(targets) != len(set(targets)):
        raise ValueError("expected a nonempty list of unique verify targets")
    return sorted(targets)[index::total]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--make", default="make")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-total", type=int, default=1)
    parser.add_argument("targets", nargs="+")
    args = parser.parse_args()
    try:
        targets = select_targets(args.targets, args.shard_index, args.shard_total)
    except ValueError as e:
        parser.error(str(e))
    print(f"====> verify-all: shard {args.shard_index + 1}/{args.shard_total}, "
          f"{len(targets)}/{len(args.targets)} targets", flush=True)
    # Make recipes use fixed basenames. A private directory prevents another
    # verify-all invocation (or a direct verify target) from replacing a render
    # between its production and assertion. Python tempfile users inherit it too.
    with tempfile.TemporaryDirectory(prefix="agent-platform-verify-") as scratch:
        env = {**os.environ, "TMPDIR": scratch}
        for target in targets:
            start = time.monotonic()
            result = subprocess.run([args.make, "--no-print-directory", target,
                                     f"TMPDIR={scratch}", f"VERIFY_TMP={scratch}"], env=env)
            print(f"<==== {target}: {int((time.monotonic() - start) * 1000)} ms", flush=True)
            if result.returncode:
                print(f"FAIL: {target}", flush=True)
                return 1
    print(f"all {len(targets)} verify targets of this shard passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
