"""Undo / redo stacks.

Two independent stacks:

  opLog     the "before" states of every applied operation.
            Pushed by pushOp(op), popped by popOp().
            Capped at OP_LOG_MAX entries.

  redoLog   the "after" states of every undone operation.
            Pushed by pushRedo(op), popped by popRedo().
            Same cap. Cleared by every new pushOp — a fresh
            action invalidates the redo history, which is the
            standard undo/redo contract.

pushOp has an optional `clearRedo` flag. Ordinary action flow
passes true (default). The redo handler passes false, because
the "new" op it pushes is itself a redo — clearing the redo
stack would prevent a second redo.

No draw hook: the caller schedules the redraw because it usually
has other state (selected mask, panel fields) to touch first."""

OPLOG_JS = r"""
import { board } from "./pg_state.js";
import { OP_LOG_MAX } from "./pg_tuning.js";

export function pushOp(op, clearRedo = true) {
  board.opLog.push(op);
  if (board.opLog.length > OP_LOG_MAX) board.opLog.shift();
  if (clearRedo) board.redoLog = [];
}

export function popOp() {
  return board.opLog.pop() || null;
}

export function pushRedo(op) {
  board.redoLog.push(op);
  if (board.redoLog.length > OP_LOG_MAX) board.redoLog.shift();
}

export function popRedo() {
  return board.redoLog.pop() || null;
}

/* ============================================================
   Snapshot helpers
   ============================================================ */

export function snapshotRefine(maskIdx) {
  const m = board.masks[maskIdx];
  if (!m) return null;
  return { type: 'refine', maskIdx, prevB64: m.b64 };
}

export function snapshotSegmentAll() {
  return {
    type: 'segmentAll',
    prevMasks: board.masks.map(m => ({
      b64: m.b64,
      points: m.points.map(p => ({ x: p.x, y: p.y, isPositive: p.isPositive })),
      textPrompt: m.textPrompt || "",
    })),
    prevSelection: board.selectedMaskIndices.slice(),
    prevStagingPoints: board.stagingPoints.map(p =>
      ({ x: p.x, y: p.y, isPositive: p.isPositive })),
  };
}
"""
