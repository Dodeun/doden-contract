"""The checker's own YAML reader, read against what YAML says.

`contract/yaml_subset.py` decides what `branch-code-holds-no-secret` sees, so
what it reads and what it refuses are pinned here, with the expected values
written out by hand: no YAML library is a dependency of the suite either.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contract.yaml_subset import YamlError, load  # noqa: E402


class WhatItReads(unittest.TestCase):
    def test_on_stays_a_string_as_github_reads_it(self):
        """A YAML 1.1 reader makes `on` True; GitHub reads the key "on"."""
        self.assertEqual({"on": "push"}, load("on: push\n"))
        self.assertEqual({"on": {"push": None}}, load('"on":\n  push:\n'))

    def test_a_flow_sequence_of_events(self):
        self.assertEqual({"on": ["push", "pull_request"]}, load("on: [push, pull_request]\n"))

    def test_flow_collections_nest_and_may_be_empty(self):
        self.assertEqual(
            {"p": {}, "q": {"a": 1, "b": ["x", "y"], "c": None}},
            load("p: {}\nq: { a: 1, b: [x, y], c: }\n"),
        )

    def test_a_flow_collection_over_lines_with_comments(self):
        self.assertEqual(
            {"x": ["a", "b"], "y": 2},
            load("x: [\n  a,  # one\n  b\n]\ny: 2\n"),
        )

    def test_quoted_keys_and_values(self):
        self.assertEqual(
            {"a: b": "it's", "tags": ["v*", "x"]},
            load("\"a: b\": 'it''s'  # comment\ntags: [ 'v*' , \"x\" ]\n"),
        )

    def test_a_comment_needs_a_space_before_it(self):
        self.assertEqual({"k": "a#b"}, load("k: a#b # c\n"))

    def test_a_sequence_beside_its_key_and_compact_mappings_in_one(self):
        self.assertEqual(
            {"b": ["x", {"uses": "y", "with": {"ref": "main"}}], "c": "z"},
            load("b:\n- x\n- uses: y\n  with:\n    ref: main\nc: z\n"),
        )

    def test_a_literal_block_keeps_its_lines(self):
        self.assertEqual({"a": "x\n y\n", "b": 1}, load("a: |\n  x\n   y\n\nb: 1\n"))

    def test_a_folded_block_joins_them(self):
        self.assertEqual(
            {"k": "folded text\npara\n  indented\nend", "n": 1},
            load("k: >-\n  folded\n  text\n\n  para\n    indented\n  end\nn: 1\n"),
        )
        self.assertEqual({"k": "a b\n"}, load("k: >\n  a\n  b\n"))

    def test_keep_chomping_keeps_the_trailing_lines(self):
        self.assertEqual({"k": "keep\n\n\n", "n": 2}, load("k: |+\n  keep\n\n\nn: 2\n"))

    def test_an_explicit_indentation(self):
        self.assertEqual({"k": "  two extra\nbase\n"}, load("k: |2\n    two extra\n  base\n"))

    def test_plain_scalars_resolve_as_yaml_1_2_does(self):
        self.assertEqual(
            {"a": -1, "b": 1.5, "c": None, "d": True, "e": "yes", "f": "http://x:80/y"},
            load("a: -1\nb: 1.5\nc: ~\nd: true\ne: yes\nf: http://x:80/y\n"),
        )

    def test_one_opening_document_marker(self):
        self.assertEqual({"a": 1}, load("---\na: 1\n"))


class WhatItRefuses(unittest.TestCase):
    def refused(self, text, names):
        with self.assertRaises(YamlError) as caught:
            load(text)
        self.assertIn(names, str(caught.exception))

    def test_an_anchor(self):
        self.refused("a: &x 1\n", "anchor")

    def test_an_alias(self):
        self.refused("a: *x\n", "alias")

    def test_a_tag(self):
        self.refused("a: !!str 1\n", "tag")

    def test_a_merge_key(self):
        self.refused("a: 1\n<<: {b: 2}\n", "merge key")

    def test_a_second_document(self):
        self.refused("a: 1\n---\nb: 2\n", "second document")

    def test_a_tab_in_the_indentation(self):
        self.refused("a:\n\tb: 1\n", "tab")

    def test_a_key_twice(self):
        self.refused("permissions: {}\npermissions: write-all\n", "twice")


if __name__ == "__main__":
    unittest.main()
