"""Tests for canvas_manager.schedule -- storage layer."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import canvas_manager.schedule as schedule
from canvas_manager.schedule import (
    Block,
    add_block,
    delete_block,
    load_plan,
    save_plan,
    update_block,
    _blocks_overlap,
)

TODAY = date(2026, 4, 20)


def _block(**overrides) -> Block:
    base: Block = {
        "id": "blk_test0001",
        "start": "09:00",
        "end": "10:00",
        "title": "Lecture",
        "type": "class",
        "source": "manual",
    }
    return {**base, **overrides}


@pytest.fixture(autouse=True)
def isolated_plans_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "plans"
    monkeypatch.setattr(schedule, "PLANS_DIR", d)
    return d


# ---------------------------------------------------------------------------
# save / load roundtrip
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_all_fields():
    block = _block()
    save_plan(TODAY, [block])
    assert load_plan(TODAY) == [block]


def test_load_returns_empty_list_when_no_file():
    assert load_plan(TODAY) == []


def test_load_raises_value_error_on_malformed_json(isolated_plans_dir: Path):
    isolated_plans_dir.mkdir(parents=True, exist_ok=True)
    (isolated_plans_dir / "2026-04-20.json").write_text("not valid json {{{")
    with pytest.raises(ValueError, match="Malformed"):
        load_plan(TODAY)


# ---------------------------------------------------------------------------
# add_block
# ---------------------------------------------------------------------------

def test_add_block_autogenerates_id_when_empty():
    result = add_block(TODAY, _block(id=""))
    assert result["id"].startswith("blk_")
    assert len(result["id"]) == len("blk_") + 8


def test_add_block_autogenerates_id_when_key_absent():
    block = {k: v for k, v in _block().items() if k != "id"}
    result = add_block(TODAY, block)
    assert result["id"].startswith("blk_")


def test_add_block_raises_on_overlap():
    add_block(TODAY, _block(id="blk_aaa00001", start="09:00", end="11:00", title="A"))
    with pytest.raises(ValueError, match="overlap"):
        add_block(TODAY, _block(id="", start="10:00", end="12:00", title="B"))


def test_touching_boundaries_are_not_conflicts():
    add_block(TODAY, _block(id="blk_aaa00001", start="09:00", end="10:00", title="A"))
    add_block(TODAY, _block(id="", start="10:00", end="11:00", title="B"))  # must not raise
    assert len(load_plan(TODAY)) == 2


# ---------------------------------------------------------------------------
# update_block
# ---------------------------------------------------------------------------

def test_update_block_changes_only_target():
    a = add_block(TODAY, _block(id="", start="09:00", end="10:00", title="A"))
    b = add_block(TODAY, _block(id="", start="11:00", end="12:00", title="B"))
    update_block(TODAY, a["id"], {"title": "A Updated"})
    by_id = {bl["id"]: bl for bl in load_plan(TODAY)}
    assert by_id[a["id"]]["title"] == "A Updated"
    assert by_id[b["id"]]["title"] == "B"


def test_update_block_raises_key_error_for_unknown_id():
    with pytest.raises(KeyError):
        update_block(TODAY, "blk_nonexistent", {"title": "X"})


def test_update_block_raises_on_new_overlap():
    a = add_block(TODAY, _block(id="", start="09:00", end="10:00", title="A"))
    add_block(TODAY,     _block(id="", start="11:00", end="12:00", title="B"))
    with pytest.raises(ValueError, match="overlap"):
        update_block(TODAY, a["id"], {"start": "10:30", "end": "11:30"})


# ---------------------------------------------------------------------------
# delete_block
# ---------------------------------------------------------------------------

def test_delete_block_removes_only_target():
    a = add_block(TODAY, _block(id="", start="09:00", end="10:00", title="A"))
    b = add_block(TODAY, _block(id="", start="11:00", end="12:00", title="B"))
    delete_block(TODAY, a["id"])
    remaining = load_plan(TODAY)
    assert len(remaining) == 1
    assert remaining[0]["id"] == b["id"]


def test_delete_block_raises_key_error_for_unknown_id():
    with pytest.raises(KeyError):
        delete_block(TODAY, "blk_nonexistent")


# ---------------------------------------------------------------------------
# sort order
# ---------------------------------------------------------------------------

def test_blocks_sorted_by_start_on_save():
    add_block(TODAY, _block(id="", start="14:00", end="15:00", title="Afternoon"))
    add_block(TODAY, _block(id="", start="09:00", end="10:00", title="Morning"))
    add_block(TODAY, _block(id="", start="11:00", end="12:00", title="Midday"))
    starts = [b["start"] for b in load_plan(TODAY)]
    assert starts == sorted(starts)


# ---------------------------------------------------------------------------
# _blocks_overlap
# ---------------------------------------------------------------------------

def _b(start: str, end: str) -> Block:
    return _block(start=start, end=end)


def test_overlap_partial():
    assert _blocks_overlap(_b("09:00", "11:00"), _b("10:00", "12:00"))


def test_overlap_full_containment():
    assert _blocks_overlap(_b("09:00", "12:00"), _b("10:00", "11:00"))


def test_overlap_identical_ranges():
    assert _blocks_overlap(_b("09:00", "11:00"), _b("09:00", "11:00"))


def test_overlap_disjoint():
    assert not _blocks_overlap(_b("09:00", "10:00"), _b("11:00", "12:00"))


def test_overlap_touching_not_overlap():
    assert not _blocks_overlap(_b("09:00", "10:00"), _b("10:00", "11:00"))


def test_overlap_touching_reverse_order():
    assert not _blocks_overlap(_b("10:00", "11:00"), _b("09:00", "10:00"))
