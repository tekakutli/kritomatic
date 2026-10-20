# Project Map

```
                         ┌──────────┐
                         │ room.py  │
                         └────┬─────┘
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        room_walls.json  room.step  room_dimensions.json
        room.stl
              │               │               │
      ┌───────┼───────┬───────┘               │
      ▼       ▼       ▼                       │
 floor_plan  boxes   cable ◄──────────────────┘
   .py      _play    _play
    │        .py      .py
    ▼        ▼         ▼
  .svg     .html     .html
  .png    :8766      :8765
                     │
                     ▼  POST /optimize-leaders
              cable_server.py
                     │
                     ▼  subprocess
              leader_optimizer  (Rust)
```

```
room.py ────────────► leader_optimizer/
                         └─ cargo build --release
```

**Run:**

```
room.py → floor_plan.py → boxes_playground.py → cable_playground.py
```

**Shared:** `playground_fonts.py` · `quiet.py`
