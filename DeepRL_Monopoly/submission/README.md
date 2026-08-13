# Submission format

Your repository needs exactly one new file: **`agent.py` at the root**, defining

```python
def choose_action(state, allowed_actions) -> int: ...
```

Everything else about your repository is yours. No layout, no framework, no
base class to inherit.

| | |
| --- | --- |
| `state` | `float32` vector of length 300, built for **your** seat |
| `allowed_actions` | `list[int]` — the action indices that are legal right now |
| return | one element of `allowed_actions` |

Copy [`template/agent.py`](template/agent.py) as a starting point. It is a
complete, valid submission on its own.

## Submitting

You submit a URL and a commit, not an upload:

- **GitHub over HTTPS only** — `https://github.com/<owner>/<repo>`. SSH,
  `git://`, plain HTTP, other hosts, and URLs carrying credentials are rejected.
- **A full 40-character commit SHA, pinned at submit time.** Branch and tag
  names are rejected. The pinned commit is what gets scored, so a later push to
  your repository cannot change your result — and cannot break it either.
- **100 MB cap** on the checkout, matching the harness limit for repositories.
  Submodules are never fetched; a submission is one commit of one repository.

Check your own repository before you submit:

```bash
python -m submission.validate \
    --repo https://github.com/<owner>/<repo> \
    --commit <40-hex-sha> --pretty
```

Or, while you are still working locally:

```bash
python -m submission.validate --local . --pretty
```

Exit status 0 means accepted; 1 prints the reason as JSON.

## What validation actually checks

1. URL is GitHub HTTPS, commit is a full pinned SHA
2. the commit fetches and the checkout is under the cap, with no symlink
   pointing outside it
3. `agent.py` imports and exposes a conforming `choose_action`
4. the agent plays complete games **from every seat** against scripted
   opponents without breaking any of the rules below

## Rules your agent must respect

**Return only legal actions.** Returning anything outside `allowed_actions`
fails the match — there is no silent fallback. Filter the legal list rather
than assuming an action is available.

**Do not touch the global RNG.** Seed your own `random.Random(...)` instead of
calling `random.random()`, `np.random.*`, or `torch.rand*` at module level.
Scoring pairs the same seeds across all four seats, and a submission that moves
the global stream breaks the pairing. The harness restores the stream after
every call and counts the violations in your report.

**Stay under the per-decision time limit** (5 s by default). A game runs to a
few thousand decisions.

## Optional: taking the environment

Declare `env` and/or `player_id` after the two required parameters and the
harness passes them by keyword:

```python
def choose_action(state, allowed_actions, env, player_id):
    ...
```

Take `env` only if you need the board itself — a search policy that clones the
environment, or a rule that reads pending trades. The 300-float state vector
cannot be turned back into an environment, so this is the only way to write
one. `ASU_SLAYER` is exactly this case; as a submission it is six lines:

```python
from ASU_SLAYER.policy import SlayerV1

class Agent:
    def __init__(self, player_id):
        self.inner = SlayerV1(player_id)

    def choose_action(self, state, allowed_actions, env):
        return self.inner.choose_action(env)
```

If your agent only needs the vector, omit both and the two-argument signature
stands.

## Optional: a class instead of a function

If your agent holds weights or per-seat state, expose a class named `Agent`.
It is constructed once per seat, with `player_id` if the constructor accepts it:

```python
class Agent:
    def __init__(self, player_id):
        self.model = load_my_model()

    def choose_action(self, state, allowed_actions):
        ...
```

## Scoring a validated submission

An accepted checkout plugs into the repository's seat-balanced evaluator like
any other policy:

```bash
python -m ASU_FROZEN_TEACHER.evaluate \
    --focus submission:/path/to/checkout \
    --opponents fixed-a fixed-b fixed-c \
    --seeds 0 1 2 3 4 --pretty
```

The focus policy plays every seat on every seed, and the result file records a
content digest of the checkout alongside the win rates.

## For whoever runs the harness

`submission.validate` **imports and executes untrusted code** in its own
process. Nothing in this package sandboxes it. Run validation inside a
container or throwaway VM, with no credentials mounted and no network access
beyond the fetch itself.
