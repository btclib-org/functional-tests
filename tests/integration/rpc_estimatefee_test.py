# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_estimatefee`, one body over each node.

Read from Core's `test/functional/rpc_estimatefee.py`
(`4056908f0fea`, 2026-09-29), a file needing no mechanism the adapter
lacks ([ISS 317](https://github.com/btclib-org/functional-tests/issues/317)):
one clean-chain node refuses `estimatesmartfee` and `estimaterawfee`
given too few or too many arguments, an argument of the wrong type, an
unknown estimate mode, fee rate estimator, named parameter or option key,
or a confirmation target past `1008`, each with Core's own code and
message, and answers each valid call without an error
(`Capability.ESTIMATE_SMART_FEE`). Every assertion of Core's own is kept,
the wrong types included: Core leaves them out only under `--usecli`.

Two steps are per-build
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)),
each read off the node's own `help estimatesmartfee` by `_help_names`:

- `estimatesmartfee`'s `options` argument, from bitcoin/bitcoin#34075,
  is asked of a node whose help lists it as `_OPTIONS_ARGUMENT` does. A
  node without it is asked what Core's file at `v30.3` asks instead, a
  third argument refused as one too many;
- the refusal of an unknown `fee_rate_estimator`, from
  bitcoin/bitcoin#36365, is asked of a node whose help gives `auto` as
  that option's default, as `_AUTO_DEFAULT` does: the commit adding the
  refusal also renames the default to `auto`.

Each `rpc_estimatefee_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from btclib_functional_tests.capability import Capability, require
from tests.integration.wallet_signmessagewithaddress_test import refused

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bitcoin_core_rpc import BitcoinCoreRpcClient

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["fee_estimates_check_their_arguments"]

# `src/rpc/protocol.h`: what either RPC answers a call with the wrong
# number of arguments, its own help text as the message; what it answers
# an argument of the wrong type, or an option key it does not know; and
# what it answers a value it refuses, or a named parameter it does not
# take
_RPC_MISC_ERROR = -1
_RPC_TYPE_ERROR = -3
_RPC_INVALID_PARAMETER = -8

_NOT_A_NUMBER = "JSON value of type string is not of expected type number"
_INVALID_ESTIMATOR = (
    "Invalid fee_rate_estimator parameter, must be one of: "
    '"auto", "block_policy", "mempool_policy"'
)

# how `RPCMethod::ToString` (`src/rpc/util.cpp`) opens the line of
# `estimatesmartfee`'s third argument, its number and its name padded
# with spaces, on a build carrying bitcoin/bitcoin#34075
_OPTIONS_ARGUMENT = "3. options "

# what the same help prints of `fee_rate_estimator`'s default,
# `default=` followed by the default's own `write()`
# (`RPCArg::ToDescriptionString`), on a build carrying
# bitcoin/bitcoin#36365
_AUTO_DEFAULT = 'default="auto"'


def _help_names(node: NodeAdapter, marker: str) -> bool:
    """Return whether `node`'s own `help estimatesmartfee` holds `marker`."""
    help_text = node.rpc.call("help", ["estimatesmartfee"])
    assert isinstance(help_text, str)
    return marker in help_text


def _options_refused(rpc: BitcoinCoreRpcClient) -> None:
    """Core's steps on `estimatesmartfee`'s `options`, at the pin."""
    # wrong type for estimatesmartfee(options.fee_rate_estimator)
    refused(
        rpc,
        "estimatesmartfee",
        [1, "ECONOMICAL", {"fee_rate_estimator": 1}],
        _RPC_TYPE_ERROR,
        "JSON value of type number for field fee_rate_estimator "
        "is not of expected type string",
    )
    # wrong type for estimatesmartfee(options.verbosity)
    refused(
        rpc,
        "estimatesmartfee",
        [1, "ECONOMICAL", {"verbosity": "foo"}],
        _RPC_TYPE_ERROR,
        "JSON value of type string for field verbosity is not of expected type number",
    )


def fee_estimates_check_their_arguments(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Core's `run_test`, in its own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.ESTIMATE_SMART_FEE, node.capabilities, skip_counts)
    rpc = node.rpc
    has_options = _help_names(node, _OPTIONS_ARGUMENT)
    refuses_estimator = _help_names(node, _AUTO_DEFAULT)

    # missing required params
    refused(rpc, "estimatesmartfee", [], _RPC_MISC_ERROR, "estimatesmartfee")
    refused(rpc, "estimaterawfee", [], _RPC_MISC_ERROR, "estimaterawfee")

    # wrong type for conf_target
    refused(rpc, "estimatesmartfee", ["foo"], _RPC_TYPE_ERROR, _NOT_A_NUMBER)
    refused(rpc, "estimaterawfee", ["foo"], _RPC_TYPE_ERROR, _NOT_A_NUMBER)
    # wrong type for estimatesmartfee(estimate_mode)
    refused(
        rpc,
        "estimatesmartfee",
        [1, 1],
        _RPC_TYPE_ERROR,
        "JSON value of type number is not of expected type string",
    )
    if has_options:
        _options_refused(rpc)
    # wrong type for estimaterawfee(threshold)
    refused(rpc, "estimaterawfee", [1, "foo"], _RPC_TYPE_ERROR, _NOT_A_NUMBER)

    refused(
        rpc,
        "estimatesmartfee",
        [1, "foo"],
        _RPC_INVALID_PARAMETER,
        "Invalid estimate_mode parameter, must be one of: "
        '"unset", "economical", "conservative"',
    )
    if refuses_estimator:
        for fee_rate_estimator in ("foo", "none", ""):
            refused(
                rpc,
                "estimatesmartfee",
                [1, "ECONOMICAL", {"fee_rate_estimator": fee_rate_estimator}],
                _RPC_INVALID_PARAMETER,
                _INVALID_ESTIMATOR,
            )
    # Core's `estimatesmartfee(1, fee_rate_estimator=True)`, positional and
    # named at once, as its `AuthServiceProxy` (`authproxy.py`) sends them
    refused(
        rpc,
        "estimatesmartfee",
        {"args": [1], "fee_rate_estimator": True},
        _RPC_INVALID_PARAMETER,
        "Unknown named parameter fee_rate_estimator",
    )
    if has_options:
        refused(
            rpc,
            "estimatesmartfee",
            [1, "ECONOMICAL", {"block_policy_only": True}],
            _RPC_TYPE_ERROR,
            "Unexpected key block_policy_only",
        )
    # extra params
    if has_options:
        for options in ({}, {"verbosity": 1}):
            refused(
                rpc,
                "estimatesmartfee",
                [1, "ECONOMICAL", options, 1],
                _RPC_MISC_ERROR,
                "estimatesmartfee",
            )
    else:
        refused(
            rpc,
            "estimatesmartfee",
            [1, "ECONOMICAL", 1],
            _RPC_MISC_ERROR,
            "estimatesmartfee",
        )
    refused(rpc, "estimaterawfee", [1, 1, 1], _RPC_MISC_ERROR, "estimaterawfee")

    # max value of 1008 per `src/policy/fees/block_policy_estimator.h`
    refused(
        rpc,
        "estimaterawfee",
        [1009],
        _RPC_INVALID_PARAMETER,
        "Invalid conf_target, must be between 1 and 1008",
    )

    # valid calls
    rpc.call("estimatesmartfee", [1])
    rpc.call("estimatesmartfee", [1, "ECONOMICAL"])
    rpc.call("estimatesmartfee", [1, "unset"])
    rpc.call("estimatesmartfee", [1, "conservative"])
    if has_options:
        for valid_options in (
            {"fee_rate_estimator": "block_policy"},
            {"fee_rate_estimator": "mempool_policy"},
            {"verbosity": 1, "fee_rate_estimator": "auto"},
        ):
            rpc.call("estimatesmartfee", [1, "ECONOMICAL", valid_options])

    rpc.call("estimaterawfee", [1])
    rpc.call("estimaterawfee", [1, None])
    rpc.call("estimaterawfee", [1, 1])
