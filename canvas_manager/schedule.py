"""Schedule data model and storage layer for canvas-manager.

Block schema (TypedDict):
    id:     str   -- unique identifier, format "blk_" + 8 random hex chars;
                     auto-generated when absent or empty
    start:  str   -- "HH:MM" 24-hour
    end:    str   -- "HH:MM" 24-hour
    title:  str
    type:   Literal["class", "assignment", "personal", "study", "other"]
    source: Literal["canvas", "gcal", "ical", "manual"]

Storage layout:
    ~/.canvas_manager/plans/YYYY-MM-DD.json
    One file per day; each file is a JSON array of Blocks sorted by start.
"""

from __future__ import annotations

import json
import secrets
from datetime import date
from pathlib import Path
from typing import Literal, TypedDict

PLANS_DIR: Path = Path.home() / ".canvas_manager" / "plans"


class Block(TypedDict):
    id: str
    start: str
    end: str
    title: str
    type: Literal["class", "assignment", "personal", "study", "other"]
    source: Literal["canvas", "gcal", "ical", "manual"]


def plan_path(d: date) -> Path:
    """Return the storage path for a given date.

    Args:
        d: The calendar date whose plan path is requested.

    Returns:
        Absolute path to the JSON plan file for that date.
    """
    return PLANS_DIR / d.strftime("%Y-%m-%d.json")


def save_plan(d: date, blocks: list[Block]) -> None:
    """Sort blocks by start time and write to disk as JSON.

    Creates PLANS_DIR (and any parents) if it does not already exist.

    Args:
        d: The calendar date being saved.
        blocks: Blocks to persist; need not be sorted on input.
    """
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    plan_path(d).write_text(json.dumps(sorted(blocks, key=lambda b: b["start"]), indent=2))


def load_plan(d: date) -> list[Block]:
    """Load blocks for a given date from disk.

    Args:
        d: The calendar date to load.

    Returns:
        List of Block dicts in file order, or [] when no file exists.

    Raises:
        ValueError: If the file exists but contains malformed JSON.
    """
    path = plan_path(d)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed plan file {path}: {exc}") from exc


def list_blocks(d: date) -> list[Block]:
    """Return all blocks for a date, sorted by start time.

    Args:
        d: The calendar date to query.

    Returns:
        Blocks sorted ascending by start.
    """
    return sorted(load_plan(d), key=lambda b: b["start"])


def add_block(d: date, block: Block) -> Block:
    """Append a block to a day's plan.

    Auto-generates an id if block["id"] is absent or empty. Rejects the
    block if its time range overlaps any block already saved for that day.

    Args:
        d: The calendar date to add the block to.
        block: Block to add.

    Returns:
        The block as stored, with a guaranteed non-empty id.

    Raises:
        ValueError: If the block's time range overlaps an existing block.
    """
    if not block.get("id"):
        block = {**block, "id": _generate_id()}
    existing = load_plan(d)
    for other in existing:
        if _blocks_overlap(block, other):
            raise ValueError(
                f"Block '{block['title']}' ({block['start']}–{block['end']}) "
                f"overlaps existing block '{other['title']}' "
                f"({other['start']}–{other['end']})"
            )
    existing.append(block)
    save_plan(d, existing)
    return block


def update_block(d: date, block_id: str, updates: dict) -> Block:
    """Merge updates into an existing block and persist.

    Args:
        d: The calendar date the block belongs to.
        block_id: id of the block to update.
        updates: Mapping of fields to overwrite.

    Returns:
        The fully updated Block.

    Raises:
        KeyError: If no block with block_id exists on that date.
        ValueError: If the update creates a time overlap with another block.
    """
    blocks = load_plan(d)
    idx = next((i for i, b in enumerate(blocks) if b["id"] == block_id), None)
    if idx is None:
        raise KeyError(f"No block with id '{block_id}' on {d}")
    updated: Block = {**blocks[idx], **updates}
    others = [b for i, b in enumerate(blocks) if i != idx]
    for other in others:
        if _blocks_overlap(updated, other):
            raise ValueError(
                f"Updated block '{updated['title']}' ({updated['start']}–{updated['end']}) "
                f"overlaps '{other['title']}' ({other['start']}–{other['end']})"
            )
    blocks[idx] = updated
    save_plan(d, blocks)
    return updated


def delete_block(d: date, block_id: str) -> None:
    """Remove a block from a day's plan.

    Args:
        d: The calendar date the block belongs to.
        block_id: id of the block to delete.

    Raises:
        KeyError: If no block with block_id exists on that date.
    """
    blocks = load_plan(d)
    remaining = [b for b in blocks if b["id"] != block_id]
    if len(remaining) == len(blocks):
        raise KeyError(f"No block with id '{block_id}' on {d}")
    save_plan(d, remaining)


def _generate_id() -> str:
    """Generate a unique block id.

    Returns:
        A string of the form ``"blk_"`` followed by 8 random hex characters.
    """
    return f"blk_{secrets.token_hex(4)}"


def _blocks_overlap(a: Block, b: Block) -> bool:
    """Return True when two blocks have overlapping time ranges.

    Uses the half-open interval model: a.start < b.end and b.start < a.end.
    Touching at exactly one boundary (a.end == b.start) is NOT an overlap.

    Args:
        a: First block.
        b: Second block.

    Returns:
        True if the intervals overlap, False if disjoint or merely touching.
    """
    return a["start"] < b["end"] and b["start"] < a["end"]
