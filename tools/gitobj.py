"""Git object ids for blobs and trees, computed here rather than by git.

A check that asks git for a tree id gets the id of what was committed. Computing the id from the
files on disk instead checks what is actually checked out, which is what a reader of the tree gets.
Ids are SHA-1, as in a repository with git's default object format.
"""

import hashlib
import os
import stat

TREE_MODE = "40000"


def blob_id(data):
    """The id git gives a blob holding `data`."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def tree_id(entries):
    """The id of one tree object, from (mode, name, id) entries for its immediate children.

    Git sorts entries by name as bytes, comparing a subtree's name as if it ended in "/".
    """
    def key(entry):
        mode, name, _ = entry
        raw = name.encode("utf-8")
        return raw + b"/" if mode == TREE_MODE else raw

    body = b"".join(
        mode.encode("ascii") + b" " + name.encode("utf-8") + b"\0" + bytes.fromhex(oid)
        for mode, name, oid in sorted(entries, key=key)
    )
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()


def tree_from_entries(files):
    """The root tree id for a mapping of "/"-separated relative paths to (mode, blob id).

    Directories come from the paths, so a directory with no file in it does not appear, as in git.
    """
    root = {}
    for path, (mode, oid) in files.items():
        parts = path.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"not a relative path: {path!r}")
        node = root
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"{path!r} lies under a file of the same name")
            node = child
        if parts[-1] in node:
            raise ValueError(f"{path!r} is listed twice, or as a file and a directory")
        node[parts[-1]] = (mode, oid)

    def build(node):
        entries = []
        for name, value in node.items():
            if isinstance(value, dict):
                entries.append((TREE_MODE, name, build(value)))
            else:
                entries.append((value[0], name, value[1]))
        return tree_id(entries)

    return build(root)


def file_entry(path):
    """(mode, blob id) for a file on disk, as git would record it."""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        return "120000", blob_id(os.fsencode(os.readlink(path)))
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{path} is not a regular file")
    with open(path, "rb") as f:
        data = f.read()
    return ("100755" if st.st_mode & stat.S_IXUSR else "100644"), blob_id(data)


def parse_ls_tree(output):
    """Parse `git ls-tree -r -z` output into {path: (mode, id)} for its blobs.

    Anything other than a blob (a submodule's commit, say) is refused, since a carried tree here
    holds files only.
    """
    files = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode, kind, oid = meta.decode("ascii").split(" ")
        if kind != "blob":
            raise ValueError(f"{path.decode('utf-8')}: a {kind}, not a blob")
        files[path.decode("utf-8")] = (mode, oid)
    return files
