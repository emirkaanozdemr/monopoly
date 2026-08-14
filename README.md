# Exposure AI Academy — Monopoly submission

The entrypoint is [`agent.py`](agent.py) at this root. It rebuilds the engine
from the read-only decision state the worker sends and runs
[`ASU_SLAYER`](DeepRL_Monopoly/ASU_SLAYER/)'s `SlayerV1` policy on it.
[`requirements.txt`](requirements.txt) pins the only runtime dependency, numpy.

## The contract the tournament actually uses

`DeepRL_Monopoly/submission/` documents an older, different contract — a bare
300-float `state`, an optional `Agent` class, an injectable `env`. The
tournament worker uses none of that. What it calls is:

```python
def choose_action(state, player_id, allowed_actions) -> int: ...
```

- `state` is a dict: `vector` (300 floats, actor-relative), `board` (a plain
  snapshot), `actions` (legal-action descriptions), `decision_seed`,
  `schema_version`, `ruleset_version`.
- The live `MonopolyEnv` is never passed, and there is no class form — only a
  module-level `choose_action`.
- The return value must be an element of `allowed_actions`.

Two things `SlayerV1` reads are missing from `board` and are recovered in
`agent.py`: `debt_player`, inferred from the post-roll menu lacking
`END_TURN`, and the pending trade offer, decoded from the tail of `vector`,
which encodes the sender, both deeds and both cash legs.

## Verifying before submitting

Both matter, because the local environment is more forgiving than the
container: it has torch, and it resolves the engine differently.

```sh
python -m pytest DeepRL_Monopoly/tests/test_asu_slayer.py     # policy suite
python -c "import agent; print(agent.choose_action)"          # entrypoint loads
```

The container is `python:3.12-slim` plus `requirements.txt` and nothing else,
so check the import path in a numpy-only virtualenv rather than the local one.
`monopoly_game_engine` imports torch lazily precisely so a game never needs it.

## Limits worth remembering

250 MiB checkout, at most 32 wheel-only requirements, 60 s startup, a hard 2 s
per decision, and 200 rounds or 50,000 actions per match. Three failed
decisions replace the seat with a bot for the rest of that match.
