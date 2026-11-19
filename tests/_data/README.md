# Test data

Where each file under `tests/_data/` came from, and how a reader makes it
again. Section 7 of the organization's standard, *Test data is vendored,
never fetched*, is the rule.

## Not vendored from anywhere

### `tests/_data/invalid_txs.json`

```text
program   Core's test/functional/data/invalid_txs.py,
          at bitcoin/bitcoin@079437cca7a8 (2026-11-19)
calls     iter_all_templates(), then get_tx() of each template
          on the spend transaction the file holds
recorded  2026-11-20
```

Verdict: **recorded**. There is no upstream file: this is what Core's own
templates build from one spend transaction, with each template's name,
its four fields, and its transaction serialized with its witness, in
Core's order. `tests/invalid_txs_test.py` holds
`btclib_functional_tests.invalid_txs` to it.

The spend transaction is one input and two outputs, so that a template
summing every output differs from one reading output 0 alone. Its hex is
the file's `spend_tx`. With `<core>` a checkout of bitcoin/bitcoin, run
from this tree's root, this replaces the file only where its Python step
exits 0:

```shell
core=<core>
out=$(mktemp -d)
git -C "${core:?}" archive 079437cca7a8 test/functional | tar -x -C "$out" &&
  python3 -I - "$out/test/functional" > "$out/invalid_txs.json" <<'EOF' &&
import json
import sys

sys.path.insert(0, sys.argv[1])
from data import invalid_txs
from test_framework.messages import CTransaction, from_hex

SPEND_TX = (
    "0200000001010101010101010101010101010101010101010101010101010101"
    "01010101010000000000ffffffff023200000000000000015107000000000000"
    "00015100000000"
)
spend_tx = from_hex(CTransaction(), SPEND_TX)
templates = [
    {
        "name": template.__name__,
        "reject_reason": template.reject_reason,
        "std_reject_reason": template.std_reject_reason,
        "block_reject_reason": template.block_reject_reason,
        "valid_in_block": template.valid_in_block,
        "tx": template(spend_tx=spend_tx).get_tx().serialize().hex(),
    }
    for template in invalid_txs.iter_all_templates()
]
print(json.dumps({"spend_tx": SPEND_TX, "templates": templates}, indent=4))
EOF
  mv "$out/invalid_txs.json" tests/_data/invalid_txs.json
rm -rf "${out:?}"
```

The commit is the pin of the `feature_block.py` row of `TF2.md`'s
per-test ledger, where `invalid_txs.py` is read. A new pin there records
this file again.
