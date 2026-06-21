"""Tests for the CreateVar HITL learning loop.

Covers parsing CreateVars into structured facts, persisting them to the
learned store, the loader merge, and the payoff: a previously-unknown
entity's prelude getting generated after the correction is learned.
"""

from __future__ import annotations

import pipeline.grammar.loaders as L
from pipeline.engine.createvar_learning import _camel_to_snake, learn_createvars
from pipeline.engine.prelude import generate_prelude, parse_createvar
from pipeline.parser.pine_parser import parse as parse_pine


# ── parse_createvar ──────────────────────────────────────────────────────

class TestParseCreateVar:
    def test_involvement_root(self):
        f = parse_createvar(
            '@[CreateVar(@Guardian, @CaseInvolvement.GetByQuery'
            '("CaseID":@[builtin.CaseID],"Type":"GRD"))]'
        )
        assert f is not None
        assert f.kind == "involvement"
        assert f.var_name == "Guardian"
        assert f.type_code == "GRD"
        assert f.source_table == "CaseInvolvement"

    def test_assignment_root(self):
        f = parse_createvar(
            '@[CreateVar(@OBAAttorney, @CaseAssignment.GetByQuery'
            '("CaseID":@[builtin.CaseID],"Type":"CIT14"))]'
        )
        assert f.kind == "assignment"
        assert f.type_code == "CIT14"

    def test_child_name_source(self):
        f = parse_createvar(
            '@[CreateVar(@GuardianAddress, @NameAddress.GetByQuery'
            '("NameID":@[Guardian.first.NameID]))]'
        )
        assert f.kind == "child"
        assert f.parent == "Guardian"
        assert f.suffix == "Address"
        assert f.source_table == "NameAddress"

    def test_child_personnel_source(self):
        f = parse_createvar(
            '@[CreateVar(@ProsecutorPhone, @PersonnelPhone.GetByQuery'
            '("PersonnelID":@[Prosecutor.first.PersonnelID]))]'
        )
        assert f.kind == "child"
        assert f.parent == "Prosecutor"
        assert f.suffix == "Phone"

    def test_non_createvar_returns_none(self):
        assert parse_createvar("@[Respondent.first.NameFirstName]") is None

    def test_camel_to_snake(self):
        assert _camel_to_snake("GuardianAddress") == "guardian_address"
        assert _camel_to_snake("Guardian") == "guardian"


# ── learn + merge + prelude payoff ───────────────────────────────────────

class TestLearningLoop:
    def _load_with_learned(self, agency_dir, learned_dir, agency):
        data = L._read_toml(agency_dir / f"{agency}.toml")["agency"]
        for table, entries in L._load_learned_tables(agency, learned_dir).items():
            merged = dict(data.get(table, {}))
            merged.update(entries)
            data[table] = merged
        return L.AgencyRoot.model_validate(data)

    def test_unknown_entity_prelude_after_learning(self, tmp_path):
        agency_dir = tmp_path / "agencies"
        learned_dir = tmp_path / "learned"
        agency_dir.mkdir()
        (agency_dir / "newpd.toml").write_text(
            '[agency]\nid = "newpd"\ndescription = "New PD"\n'
        )
        body = [parse_pine("@[GuardianAddress.first.City]")]

        # Before learning: agency knows nothing about Guardian.
        before = generate_prelude(body, self._load_with_learned(agency_dir, learned_dir, "newpd"))
        assert before == []

        res = learn_createvars(
            "newpd",
            [
                '@[CreateVar(@Guardian, @CaseInvolvement.GetByQuery'
                '("CaseID":@[builtin.CaseID],"Type":"GRD"))]',
                '@[CreateVar(@GuardianAddress, @NameAddress.GetByQuery'
                '("NameID":@[Guardian.first.NameID]))]',
            ],
            learned_dir=learned_dir,
        )
        assert res["learned"] == 2
        assert set(res["entities"]) == {"Guardian", "GuardianAddress"}

        # After learning: the prelude emits both declarations.
        after = generate_prelude(body, self._load_with_learned(agency_dir, learned_dir, "newpd"))
        assert any("CreateVar(@Guardian," in line for line in after)
        assert any("CreateVar(@GuardianAddress," in line for line in after)

    def test_relearning_updates_entry(self, tmp_path):
        learned_dir = tmp_path / "learned"
        learn_createvars(
            "newpd",
            ['@[CreateVar(@Guardian, @CaseInvolvement.GetByQuery("Type":"OLD"))]'],
            learned_dir=learned_dir,
        )
        learn_createvars(
            "newpd",
            ['@[CreateVar(@Guardian, @CaseInvolvement.GetByQuery("Type":"GRD"))]'],
            learned_dir=learned_dir,
        )
        tables = L._load_learned_tables("newpd", learned_dir)
        assert tables["involvement_roles"]["guardian"]["type_code"] == "GRD"

    def test_non_createvar_tokens_ignored(self, tmp_path):
        learned_dir = tmp_path / "learned"
        res = learn_createvars(
            "newpd",
            ["@[Respondent.first.NameFirstName]", "  ", "@[builtin.CaseID]"],
            learned_dir=learned_dir,
        )
        assert res["learned"] == 0
        assert not (learned_dir / "newpd.toml").exists()
