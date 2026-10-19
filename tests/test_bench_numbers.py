"""The README's results table is what the recorded results say (bench/verify_numbers.py, offline)."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GROUPING = ROOT / "bench" / "results" / "guided-rerun-grouping"


@pytest.mark.skipif(not (GROUPING / "r17-hash-leak.json").exists() or
                    not (ROOT / "bench" / "results" / "r17-hash-leak.json").exists(),
                    reason="the recorded benchmark results aren't in this checkout")
def test_the_quoted_numbers_come_out_of_the_recorded_results():
    out = subprocess.run([sys.executable, str(ROOT / "bench" / "verify_numbers.py")], capture_output=True,
                         text=True, check=True).stdout
    main = out.split("== heldout")[0]
    for line in ("App's own tests            12/20         0/10", "AI code review             17/20         1/10",
                 "Review-guided Doppel       17/20         2/10", "Tests + Doppel             20/20         2/10",
                 "AI review + Doppel         19/20         3/10", "the tests missed, Doppel caught 8."):
        assert line in main, line
    assert "Review-guided Doppel        7/7          0/3" in out.split("== heldout")[1]
