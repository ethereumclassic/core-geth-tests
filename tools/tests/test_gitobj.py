import os
import subprocess
import tempfile
import unittest

import gitobj
from tests import repo


class GitObjectTest(unittest.TestCase):
    def test_well_known_ids(self):
        self.assertEqual(gitobj.blob_id(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
        self.assertEqual(gitobj.blob_id(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")
        self.assertEqual(gitobj.tree_id([]), "4b825dc642cb6eb9a060e54bf8d69288fbee4904")

    def test_tree_ids_agree_with_git(self):
        """Names chosen to test git's ordering, which compares a directory as if it ended in "/":
        "a-b" and "a.b" sort before the directory "a", and "a0" after it."""
        files = {
            "a-b": b"1\n", "a.b": b"2\n", "a/x": b"3\n", "a/y/z": b"4\n", "a0": b"5\n",
            "run.sh": b"#!/bin/sh\n", "empty": b"",
        }
        with tempfile.TemporaryDirectory() as root:
            repo.git(root, "init", "-q")
            for path, data in files.items():
                full = os.path.join(root, *path.split("/"))
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "wb") as f:
                    f.write(data)
            os.chmod(os.path.join(root, "run.sh"), 0o755)
            os.chmod(os.path.join(root, "a0"), 0o744)
            os.symlink("a-b", os.path.join(root, "link"))
            repo.git(root, "add", "-A")
            want = repo.git(root, "write-tree").strip()
            entries = {p: gitobj.file_entry(os.path.join(root, *p.split("/"))) for p in [*files, "link"]}
            self.assertEqual(entries["run.sh"][0], "100755")
            self.assertEqual(entries["a0"][0], "100755")
            self.assertEqual(entries["link"], ("120000", gitobj.blob_id(b"a-b")))
            self.assertEqual(gitobj.tree_from_entries(entries), want)
            listed = gitobj.parse_ls_tree(subprocess.run(
                ["git", "-C", root, "ls-tree", "-r", "-z", want], capture_output=True, check=True,
                env=repo.ENV).stdout)
            self.assertEqual(listed, entries)

    def test_a_path_cannot_be_a_file_and_a_directory(self):
        with self.assertRaises(ValueError):
            gitobj.tree_from_entries({"a": ("100644", gitobj.blob_id(b"")), "a/b": ("100644", gitobj.blob_id(b""))})

    def test_ls_tree_refuses_anything_but_blobs(self):
        record = b"160000 commit " + b"0" * 40 + b"\tsub\0"
        with self.assertRaises(ValueError):
            gitobj.parse_ls_tree(record)


if __name__ == "__main__":
    unittest.main()
