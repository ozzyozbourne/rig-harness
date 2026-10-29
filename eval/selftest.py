"""Each checker must fail the untouched test repo and pass a reference solution."""
import os, shutil, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tasks import FAMILIES, SPDX
from run import make_repo

REF = {
    "changelog": {"mathx/stats.py": "def mean(xs):\n    return sum(xs) / len(xs) if xs else 0.0\n",
                  "CHANGELOG.md": "# Changelog\n\n## Unreleased\n- mean([]) returns 0.0.\n\n## 0.4.0\n- Added median.\n"},
    "regression_test": {"dates/util.py": "def is_leap(year):\n    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n",
                        "tests/test_reg.py": "import unittest\nfrom dates.util import is_leap\n\n\nclass R(unittest.TestCase):\n    def test_regression_1900(self):\n        self.assertFalse(is_leap(1900))\n"},
    "spdx_header": {"units/temperature.py": SPDX + "\ndef c_to_f(c):\n    return c * 9 / 5 + 32\n\n\ndef f_to_c(f):\n    return (f - 32) * 5 / 9\n"},
    "logging": {"cache/store.py": "import logging\n\nlogger = logging.getLogger(__name__)\n\n\nclass Store:\n    def __init__(self):\n        self.data = {}\n\n    def put(self, key, value):\n        self.data[key] = value\n\n    def get(self, key):\n        if key not in self.data:\n            logger.warning('missing key %s', key)\n        return self.data.get(key)\n"},
    "answer_format": {"wordcount/count.py": "def count_words(text):\n    return len(text.split())\n"},
    "app_env": {"shipping/fees.py": "from shipping import settings\n\n\ndef fee(total):\n    if total > settings.FREE_SHIPPING_OVER:\n        return 0.0\n    return 5.0\n"},
    "type_hints": {"lists/ops.py": "def chunk(items: list, size: int) -> list:\n    \"\"\"Chunks.\"\"\"\n    return [items[i:i + size] for i in range(0, len(items), size)]\n"},
    "app_error": {"orders/qty.py": "from orders.errors import AppError\n\n\ndef set_qty(n):\n    if n < 1:\n        raise AppError('bad')\n    return n\n"},
}
ANSWER = {"answer_format": "Fixed.\n\n**Risk: low**"}
bad = 0
for fam, spec in FAMILIES.items():
    repo = f"/tmp/selftest/{fam}"
    make_repo(repo, spec["test_files"])
    before = spec["check"](repo, "Done.")
    for name, text in REF[fam].items():
        with open(os.path.join(repo, name), "w") as f:
            f.write(text)
    after = spec["check"](repo, ANSWER.get(fam, "Done."))
    good = before[0] is False and after[0] is True and after[1] in (True, None) and before[1] in (False, None)
    bad += not good
    print(f"{fam:16} untouched={before} reference={after} {'OK' if good else 'BAD'}")
sys.exit(bad)
