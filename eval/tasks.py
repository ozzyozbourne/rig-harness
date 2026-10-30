"""Task families for the continual-learning eval.

Each family has a learning repo and a test repo that share one convention
(a team rule or a project quirk). The learning episode is a task, then one
round of reviewer feedback stating the convention, then /refine. The test
task, in a different repo, never mentions the convention. A checker grades
the task itself and the convention separately.
"""

import ast
import os
import re
import subprocess
import sys
import textwrap


def d(s):
    return textwrap.dedent(s).lstrip("\n")


def py(repo, code, env=None):
    """Runs `code` in `repo`; returns (ok, output)."""
    full_env = {**os.environ, **(env or {})}
    r = subprocess.run([sys.executable, "-c", code], cwd=repo, capture_output=True, text=True, timeout=60, env=full_env)
    return r.returncode == 0, (r.stdout + r.stderr)[-2000:]


def unittest_ok(repo, env=None):
    full_env = {**os.environ, **(env or {})}
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=repo,
                       capture_output=True, text=True, timeout=120, env=full_env)
    return r.returncode == 0


def read(repo, path):
    try:
        with open(os.path.join(repo, path)) as f:
            return f.read()
    except OSError:
        return ""


def tests_text(repo):
    """The text of every .py file in the repo's tests/ directory."""
    tests = os.path.join(repo, "tests")
    if not os.path.isdir(tests):
        return ""
    return "".join(read(repo, os.path.join("tests", n)) for n in os.listdir(tests) if n.endswith(".py"))


def has_hints(repo, path, fn):
    """Whether function `fn` in `path` annotates every argument and its return, and has a docstring."""
    try:
        tree = ast.parse(read(repo, path))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn:
            args = node.args.args + node.args.kwonlyargs
            return all(a.annotation is not None for a in args) and node.returns is not None \
                and ast.get_docstring(node) is not None
    return False


def ends_with_risk(answer):
    """Whether the answer's last non-empty line is `Risk: low|medium|high`."""
    lines = [l.strip().strip("*_` ") for l in (answer or "").splitlines() if l.strip()]
    return bool(re.fullmatch(r"\**risk\**\s*:\s*\**\s*(low|medium|high)\.?", lines[-1] if lines else "", re.I))


# ---------------------------------------------------------------- families

def changelog_check(repo, answer):
    ok_task, _ = py(repo, "from mathx.stats import mean; assert mean([]) == 0.0; assert mean([1,2,3]) == 2")
    text = read(repo, "CHANGELOG.md")
    m = re.search(r"## Unreleased\n(.*?)(\n## |\Z)", text, re.S)
    ok_conv = bool(m and re.search(r"^\s*[-*] \S", m.group(1), re.M))
    return ok_task, ok_conv


def regression_check(repo, answer):
    ok_task, _ = py(repo, "from dates.util import is_leap as f; assert not f(1900) and f(2000) and f(2024) and not f(2023)")
    ok_conv = bool(re.search(r"def test_regression_\w*\(", tests_text(repo))) and unittest_ok(repo)
    return ok_task, ok_conv


SPDX = "# SPDX-License-Identifier: Apache-2.0"


def spdx_check(repo, answer):
    ok_task, _ = py(repo, "from units.temperature import c_to_f, f_to_c; assert abs(c_to_f(100)-212)<1e-9 and abs(f_to_c(32))<1e-9")
    first = read(repo, "units/temperature.py").splitlines()[:1]
    return ok_task, first == [SPDX]


def logging_check(repo, answer):
    code = d("""
        import logging
        from cache.store import Store
        records = []
        class H(logging.Handler):
            def emit(self, r): records.append(r)
        logging.getLogger().addHandler(H()); logging.getLogger().setLevel(logging.DEBUG)
        import warnings
        warnings.simplefilter('always')
        s = Store(); s.put('a', 1)
        assert s.get('a') == 1
        with warnings.catch_warnings(record=True) as caught:
            assert s.get('missing-key-42') is None
        logged = any(r.levelno == logging.WARNING and 'missing-key-42' in r.getMessage() for r in records)
        warned = any('missing-key-42' in str(w.message) for w in caught)
        print(f'@@logged={int(logged)} warned={int(warned)}')
    """)
    ok, out = py(repo, code)
    logged = "@@logged=1" in out
    # Printed by the code itself (stdout/stderr), outside our marker line.
    printed = "missing-key-42" in "\n".join(l for l in out.splitlines() if not l.startswith("@@"))
    # The task: None and a warning naming the key, by any means (logging, warnings, or print).
    ok_task = ok and (logged or "warned=1" in out or printed)
    src = read(repo, "cache/store.py")
    ok_conv = ok and logged and "print(" not in src and "getLogger(__name__)" in src
    return ok_task, ok_conv


def risk_check(repo, answer):
    ok_task, _ = py(repo, "from wordcount.count import count_words as c; assert c('a  b') == 2 and c(' x y z ') == 3 and c('') == 0")
    return ok_task, ends_with_risk(answer)


def appenv_check(repo, answer):
    ok_task, _ = py(repo, "from shipping.fees import fee; assert fee(10) == 5.0 and fee(0) == 5.0 and fee(25) == 0.0",
                    env={"APP_ENV": "test"})
    return ok_task and unittest_ok(repo, env={"APP_ENV": "test"}), None  # efficiency family: no convention to grade


def hints_check(repo, answer):
    ok_task, _ = py(repo, "from lists.ops import chunk; assert chunk([1,2,3,4,5],2)==[[1,2],[3,4],[5]] and chunk([],3)==[]")
    return ok_task, has_hints(repo, "lists/ops.py", "chunk")


def apperror_check(repo, answer):
    code = d("""
        from orders.qty import set_qty
        from orders.errors import AppError
        assert set_qty(2) == 2
        try:
            set_qty(0)
        except AppError:
            raise SystemExit(0)
        except Exception as e:
            raise SystemExit(3)
        raise SystemExit(4)
    """)
    r = subprocess.run([sys.executable, "-c", code], cwd=repo, capture_output=True, timeout=60)
    ok_task = r.returncode in (0, 3)  # rejects 0 with some exception
    return ok_task, r.returncode == 0


APPENV_SETTINGS = d("""
    import os

    ENV = os.environ.get("APP_ENV")
    if ENV not in ("dev", "test", "prod"):
        raise RuntimeError(f"settings: unknown environment {ENV!r}")
    FREE_SHIPPING_OVER = 20
""")

FAMILIES = {
    "changelog": dict(
        learn_files={
            "textkit/__init__.py": "",
            "textkit/slug.py": "def slugify(text):\n    return '-'.join(text.split())\n",
            "tests/__init__.py": "",
            "tests/test_slug.py": "import unittest\nfrom textkit.slug import slugify\n\n\nclass T(unittest.TestCase):\n    def test_words(self):\n        self.assertEqual(slugify('a b'), 'a-b')\n",
            "CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n## 1.2.0\n- Added truncate helper.\n",
        },
        learn_task="slugify('Hello World') should return 'hello-world' but returns 'Hello-World'. Fix it.",
        feedback="Review feedback: in all my repos, every change must add a bullet describing it under `## Unreleased` in CHANGELOG.md. Please do that now, and keep doing it in all future work.",
        test_files={
            "mathx/__init__.py": "",
            "mathx/stats.py": "def mean(xs):\n    return sum(xs) / len(xs)\n",
            "tests/__init__.py": "",
            "tests/test_stats.py": "import unittest\nfrom mathx.stats import mean\n\n\nclass T(unittest.TestCase):\n    def test_mean(self):\n        self.assertEqual(mean([2, 4]), 3)\n",
            "CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n## 0.4.0\n- Added median.\n",
        },
        test_task="mean([]) crashes with ZeroDivisionError; it should return 0.0. Fix it.",
        check=changelog_check,
    ),
    "regression_test": dict(
        learn_files={
            "inventory/__init__.py": "",
            "inventory/stock.py": "class Stock:\n    def __init__(self):\n        self.counts = {}\n\n    def add(self, item, n):\n        self.counts[item] = self.counts.get(item, 0) + n\n\n    def remove(self, item, n):\n        self.counts[item] = self.counts.get(item, 0) - n\n",
            "tests/__init__.py": "",
            "tests/test_stock.py": "import unittest\nfrom inventory.stock import Stock\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        s = Stock(); s.add('x', 2)\n        self.assertEqual(s.counts['x'], 2)\n",
        },
        learn_task="Stock.remove lets stock go negative. Removing more than is available should raise ValueError and leave the count unchanged. Fix it.",
        feedback="Review feedback: in all my repos, every bug fix must come with a regression test in tests/, in a test method whose name starts with `test_regression_`. Add one now, and keep doing this for every future fix.",
        test_files={
            "dates/__init__.py": "",
            "dates/util.py": "def is_leap(year):\n    return year % 4 == 0\n",
            "tests/__init__.py": "",
            "tests/test_util.py": "import unittest\nfrom dates.util import is_leap\n\n\nclass T(unittest.TestCase):\n    def test_2024(self):\n        self.assertTrue(is_leap(2024))\n",
        },
        test_task="is_leap(1900) returns True, but 1900 was not a leap year. Fix it.",
        check=regression_check,
    ),
    "spdx_header": dict(
        learn_files={
            "shapes/__init__.py": "",
            "shapes/square.py": "def area(side):\n    return side * side\n",
        },
        learn_task="Add a module shapes/circle.py with a function area(r) returning the area of a circle of radius r.",
        feedback=f"Review feedback: every new source file in my repos must start with the line `{SPDX}`. Add it now, and do this for every new file you create from now on.",
        test_files={
            "units/__init__.py": "",
            "units/length.py": "def m_to_ft(m):\n    return m * 3.28084\n",
        },
        test_task="Add a module units/temperature.py with functions c_to_f(c) and f_to_c(f) converting between Celsius and Fahrenheit.",
        check=spdx_check,
    ),
    "logging": dict(
        learn_files={
            "loader/__init__.py": "",
            "loader/config.py": "import json\nimport os\n\n\ndef load(path):\n    if not os.path.exists(path):\n        return {}\n    with open(path) as f:\n        return json.load(f)\n",
        },
        learn_task="When the config file is missing, load() should still return {} but report a warning saying which file is missing.",
        feedback="Review feedback: library code in my repos never uses print(). Warnings go through a module-level `logger = logging.getLogger(__name__)` and `logger.warning(...)`. Change it to that now, and follow this rule from now on.",
        test_files={
            "cache/__init__.py": "",
            "cache/store.py": "class Store:\n    def __init__(self):\n        self.data = {}\n\n    def put(self, key, value):\n        self.data[key] = value\n\n    def get(self, key):\n        return self.data.get(key)\n",
        },
        test_task="When a key is missing, Store.get() should still return None but report a warning that names the missing key.",
        check=logging_check,
    ),
    "answer_format": dict(
        learn_files={
            "greet/__init__.py": "",
            "greet/hello.py": "def greet(name):\n    return 'Hi ' + name\n",
        },
        learn_task="Make greet(name) return 'Hello, <name>!' instead of 'Hi <name>'.",
        feedback="Review feedback: when you finish a task, always end your final reply with a line `Risk: low`, `Risk: medium` or `Risk: high`, rating how risky the change is. Give me that line now, and always end your final replies this way from now on.",
        test_files={
            "wordcount/__init__.py": "",
            "wordcount/count.py": "def count_words(text):\n    return len(text.split(' '))\n",
        },
        test_task="count_words('a  b') returns 3 but should return 2, and count_words('') should be 0. Fix it.",
        check=risk_check,
    ),
    "app_env": dict(
        learn_files={
            "billing/__init__.py": "",
            "billing/settings.py": APPENV_SETTINGS.replace("FREE_SHIPPING_OVER = 20", "TAX_RATE = 0.2"),
            "billing/tax.py": "from billing import settings\n\n\ndef tax(amount):\n    return amount * settings.TAX_RATE + 1\n",
            "tests/__init__.py": "",
            "tests/test_tax.py": "import unittest\nfrom billing.tax import tax\n\n\nclass T(unittest.TestCase):\n    def test_tax(self):\n        self.assertEqual(tax(10), 2.0)\n",
        },
        learn_task="The test suite is failing. Make the tests pass by fixing the code (not the tests).",
        feedback="Note for the future: in all my repos, tests only run with `APP_ENV=test` set, e.g. `APP_ENV=test python3 -m unittest discover -s tests -t .`. Remember this for next time.",
        test_files={
            "shipping/__init__.py": "",
            "shipping/settings.py": APPENV_SETTINGS,
            "shipping/fees.py": "from shipping import settings\n\n\ndef fee(total):\n    if total > settings.FREE_SHIPPING_OVER:\n        return 0.0\n    return 5\n",
            "tests/__init__.py": "",
            "tests/test_fees.py": "import unittest\nfrom shipping.fees import fee\n\n\nclass T(unittest.TestCase):\n    def test_fee(self):\n        self.assertEqual(fee(10), 5.0)\n        self.assertEqual(fee(25), 0.0)\n        self.assertIsInstance(fee(10), float)\n",
        },
        test_task="The test suite is failing. Make the tests pass by fixing the code (not the tests).",
        check=appenv_check,
    ),
    "type_hints": dict(
        learn_files={
            "strings/__init__.py": "",
            "strings/ops.py": "def shout(s):\n    return s.upper() + '!'\n",
        },
        learn_task="Add a function reverse_words(s) to strings/ops.py that reverses the order of the words in s.",
        feedback="Review feedback: in my repos every public function must have full type hints (all parameters and the return type) and a docstring. Add them now, and follow this for all new functions from now on.",
        test_files={
            "lists/__init__.py": "",
            "lists/ops.py": "def first(items):\n    return items[0] if items else None\n",
        },
        test_task="Add a function chunk(items, size) to lists/ops.py that splits a list into consecutive chunks of length size (the last chunk may be shorter).",
        check=hints_check,
    ),
    "app_error": dict(
        learn_files={
            "users/__init__.py": "",
            "users/errors.py": "class AppError(Exception):\n    pass\n",
            "users/validate.py": "def validate_age(age):\n    return age >= 0\n",
        },
        learn_task="validate_age should reject negative ages by raising an exception instead of returning False (return True for valid ages).",
        feedback="Review feedback: in my repos, every error raised by library code must be `AppError` from the package's errors.py, never ValueError or other built-ins. Change it now, and do this from now on.",
        test_files={
            "orders/__init__.py": "",
            "orders/errors.py": "class AppError(Exception):\n    pass\n",
            "orders/qty.py": "def set_qty(n):\n    return n\n",
        },
        test_task="set_qty should reject quantities below 1 by raising an exception; valid quantities are returned unchanged.",
        check=apperror_check,
    ),
}
