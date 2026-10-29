#!/usr/bin/env python3
"""Run bounded CI groups; refuse gaps/duplicates against unittest discovery."""
import argparse
import json
from pathlib import Path
import sys
import unittest

TESTS = Path(__file__).resolve().parents[1] / "tests"
GROUPS = {
    "profiles": ("test_hardening", "test_namespace_probe", "test_asar_product_name", "test_product_name", "test_framework_digest", "test_share_profile", "test_duo_tools", "test_loop_duo", "test_duo_send", "test_links"),
    "storage": ("test_checkpoints", "test_integrity", "test_maintenance", "test_settled_verification", "test_transactions"),
    "toolsets": ("test_toolsets",),
}


def cases(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from cases(case)
        else:
            yield case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=["all", "inventory", *GROUPS])
    args = parser.parse_args()
    sys.path.insert(0, str(TESTS))
    loader = unittest.TestLoader()
    discovered = loader.discover(str(TESTS))
    groups = {name: loader.loadTestsFromNames(modules) for name, modules in GROUPS.items()}
    if loader.errors:
        parser.error("test import/discovery failed: " + "\n".join(loader.errors))
    expected = [case.id() for case in cases(discovered)]
    assigned = [case.id() for suite in groups.values() for case in cases(suite)]
    if len(assigned) != len(set(assigned)) or len(expected) != len(set(expected)) or set(assigned) != set(expected):
        parser.error("groups must cover discovery exactly once; review newly added test modules")
    counts = {name: suite.countTestCases() for name, suite in groups.items()}
    print(json.dumps({"group_counts": counts, "total": len(expected), "inventory": "complete and disjoint"}), flush=True)
    if args.group == "inventory":
        return 0
    suite = discovered if args.group == "all" else groups[args.group]
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
