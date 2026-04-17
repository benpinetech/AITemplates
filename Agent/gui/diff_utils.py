"""Diff utilities for comparing template outputs."""
import difflib


def make_diff_html(
    a: str,
    b: str,
    fromdesc: str = "Generated",
    todesc: str = "Ground Truth",
) -> str:
    """Return a self-contained HtmlDiff page comparing two strings line by line."""
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    differ = difflib.HtmlDiff(wrapcolumn=100)
    return differ.make_file(a_lines, b_lines, fromdesc=fromdesc, todesc=todesc, context=True, numlines=3)


def unified_diff_lines(a: str, b: str, fromdesc: str = "generated", todesc: str = "ground_truth") -> list[str]:
    """Return unified diff lines (with +/- prefixes) as a list of strings."""
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    return list(difflib.unified_diff(a_lines, b_lines, fromfile=fromdesc, tofile=todesc, lineterm=""))
