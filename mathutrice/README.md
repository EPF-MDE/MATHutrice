# Package conventions in `mathutrice`

The aim is that you learn everything a package here offers by reading **one**
file — its `__init__.py` — and can trust that reading. Today's packages do not
have such an interface yet, and callers import their modules directly. What is
already checked is the boundary: `tach check` fails on any import that reaches a
private name from outside its package. This is a constraint, not a suggestion.

## The rule

**Private means a name starting with `_`.** From outside a package, you may
import a name only if every segment of its path starts with something other than
an underscore.

```python
from mathutrice.billing import total_due              # fine: the package's public surface
from mathutrice.billing.quote import estimate         # fine: a further public entry point
from mathutrice.billing._internal._totals import total_due   # fails `tach check`
```

Inside a package, its own modules import each other however they like. The rule
governs what crosses the package boundary, not how the implementation behind it
is arranged.

## The shape

```
mathutrice/
  billing/
    __init__.py     the public surface: named re-exports + __all__
    quote.py        a further entry point — public, because no leading underscore
    _internal/      everything else, unreachable from outside
      _totals.py
```

`billing` above is an illustration, not necessarily a package in this repo:
the shape applies to every package under `mathutrice/`, whichever they are.

Adding a package, or a private folder inside one, needs no edit to `tach.toml`.
The rule is written once, generically, and applies to whatever is there.

## Writing an interface

Re-export explicit names and list them in `__all__`:

```python
from ._internal._invoice import Invoice
from ._internal._totals import total_due

__all__ = ["Invoice", "total_due"]
```

**Never `from ._internal import *`.** A star re-export makes the public surface
unenumerable, which defeats the one thing this whole arrangement buys: that a
reader — human or agent — learns the package from a single file. `tach` checks
the import boundary, not the re-export style, so this rule is yours to keep.

Keep the surface small. A package with thirty exported names is not a deep
module; it is a folder. When one gets that large, the question is what it is
hiding, and usually the answer is: not enough.

## Running the check

```sh
uv run tach check
uv run python scripts/check_cycles.py
```

A clean exit means every import in the repo goes through a public surface. A
violation names the offending import, so there is nothing to hunt for.
