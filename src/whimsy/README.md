# Whimsy

A standalone docker-style whimsical name generator (`admiring_agnesi`,
`clever_ramanujan`, ...). Zero dependencies beyond the Python standard library.

## Usage

```python
from whimsy import generate_name  # or `from src.whimsy import generate_name` in-tree

name = generate_name(existing={"admiring_agnesi"})
```

`generate_name(existing, rng=None)` returns an `adjective_surname` combination
guaranteed not to be in `existing`. After 20 colliding attempts it falls back
to appending a numeric suffix (like Docker's container namer).

## License

Word lists adapted from Docker's Apache-2.0-licensed `names-generator.go`;
see `LICENSE-NOTICE.md`.
