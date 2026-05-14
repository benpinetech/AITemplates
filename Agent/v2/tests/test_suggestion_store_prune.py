"""``prune_conflicting_in_scope`` — used by the converter app's
auto-persist flow to keep one rewrite per (org, scope, JDA) when the
converter edits the same chip more than once."""

from __future__ import annotations

import pytest

from v2.engine import suggestion_store as ss


JDA = "%[Cust_Name]"
PINE_OLD = "@[Complainant.first.NameFirst]"
PINE_NEW = "@[Complainant.first.NameLast]"


def _accepted_files(tmp_path, org, scope):
    org_dir = tmp_path / "verified" / org
    if scope[0] == ss.SCOPE_TEMPLATE:
        d = org_dir / "by_template" / scope[1]
    elif scope[0] == ss.SCOPE_AUDIENCE:
        d = org_dir / "by_audience" / scope[1]
    else:
        d = org_dir / "global"
    return sorted(p.name for p in d.glob("*.toml")) if d.exists() else []


class TestPruneConflictingInScope:
    def test_removes_prior_with_same_jda_different_pine(self, tmp_path):
        scope = (ss.SCOPE_TEMPLATE, "Letter to C.rtf")
        ss.accept_suggestion(JDA, PINE_OLD, org="oba", scope=scope, root=tmp_path)
        assert len(_accepted_files(tmp_path, "oba", scope)) == 1

        removed = ss.prune_conflicting_in_scope(
            JDA, PINE_NEW, org="oba", scope=scope, root=tmp_path,
        )
        assert len(removed) == 1
        # Pruning alone shouldn't write the new file.
        assert _accepted_files(tmp_path, "oba", scope) == []

    def test_idempotent_when_existing_match_is_identical(self, tmp_path):
        scope = (ss.SCOPE_TEMPLATE, "Letter to C.rtf")
        ss.accept_suggestion(JDA, PINE_OLD, org="oba", scope=scope, root=tmp_path)
        removed = ss.prune_conflicting_in_scope(
            JDA, PINE_OLD, org="oba", scope=scope, root=tmp_path,
        )
        assert removed == []
        assert len(_accepted_files(tmp_path, "oba", scope)) == 1

    def test_does_not_touch_other_scopes(self, tmp_path):
        # An edit in template-X scope must not delete a global suggestion
        # for the same JDA, even with a different rewrite.
        ss.accept_suggestion(JDA, PINE_OLD, org="oba", root=tmp_path)  # global
        scope = (ss.SCOPE_TEMPLATE, "Letter to C.rtf")
        removed = ss.prune_conflicting_in_scope(
            JDA, PINE_NEW, org="oba", scope=scope, root=tmp_path,
        )
        assert removed == []
        assert len(_accepted_files(tmp_path, "oba", (ss.SCOPE_GLOBAL, ""))) == 1

    def test_does_not_touch_other_jda_matches(self, tmp_path):
        scope = (ss.SCOPE_TEMPLATE, "Letter to C.rtf")
        ss.accept_suggestion(
            "%[Cust_Address.City]", "@[ComplainantAddress.first.City]",
            org="oba", scope=scope, root=tmp_path,
        )
        removed = ss.prune_conflicting_in_scope(
            JDA, PINE_NEW, org="oba", scope=scope, root=tmp_path,
        )
        assert removed == []
        assert len(_accepted_files(tmp_path, "oba", scope)) == 1

    def test_accept_after_prune_replaces_cleanly(self, tmp_path):
        scope = (ss.SCOPE_TEMPLATE, "Letter to C.rtf")
        ss.accept_suggestion(JDA, PINE_OLD, org="oba", scope=scope, root=tmp_path)
        ss.prune_conflicting_in_scope(JDA, PINE_NEW, org="oba", scope=scope, root=tmp_path)
        ss.accept_suggestion(JDA, PINE_NEW, org="oba", scope=scope, root=tmp_path)
        files = _accepted_files(tmp_path, "oba", scope)
        assert len(files) == 1

    def test_no_op_when_scope_dir_does_not_exist(self, tmp_path):
        # Fresh tmp_path with nothing written yet.
        removed = ss.prune_conflicting_in_scope(
            JDA, PINE_NEW, org="oba",
            scope=(ss.SCOPE_TEMPLATE, "X.rtf"), root=tmp_path,
        )
        assert removed == []
