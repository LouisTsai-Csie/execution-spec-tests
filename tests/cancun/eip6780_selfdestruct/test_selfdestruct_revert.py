"""tests for selfdestruct interaction with revert."""

from typing import Dict

import pytest

from ethereum_test_forks import Cancun
from ethereum_test_tools import (
    EOA,
    Account,
    Address,
    Alloc,
    Bytecode,
    Environment,
    Initcode,
    StateTestFiller,
    Storage,
    Transaction,
    YulCompiler,
    compute_create_address,
)
from ethereum_test_tools.vm.opcode import Opcodes as Op

REFERENCE_SPEC_GIT_PATH = "EIPS/eip-6780.md"
REFERENCE_SPEC_VERSION = "1b6a0e94cc47e859b9866e570391cf37dc55059a"

SELFDESTRUCT_ENABLE_FORK = Cancun


@pytest.fixture
def entry_code_address(sender: EOA) -> Address:
    """Address where the entry code will run."""
    return compute_create_address(address=sender, nonce=0)


@pytest.fixture
def recursive_revert_contract_address_init_balance() -> int:
    """Return initial balance for recursive_revert_contract_address."""
    return 3


@pytest.fixture
def recursive_revert_contract_address(
    pre: Alloc,
    recursive_revert_contract_code: Bytecode,
    recursive_revert_contract_address_init_balance: int,
) -> Address:
    """Address where the recursive revert contract address exists."""
    return pre.deploy_contract(
        code=recursive_revert_contract_code,
        balance=recursive_revert_contract_address_init_balance,
    )


@pytest.fixture
def selfdestruct_on_outer_call() -> int:
    """Whether to selfdestruct the target contract in the outer call scope."""
    return 0


@pytest.fixture
def recursive_revert_contract_code(
    yul: YulCompiler,
    selfdestruct_on_outer_call: int,
    selfdestruct_with_transfer_contract_code: Bytecode,
    selfdestruct_with_transfer_contract_address: Address,
) -> Bytecode:
    """
    Contract code that:
        Given selfdestructable contract A, transfer value to A and call A.selfdestruct.
        Then, recurse into a new call which transfers value to A,
        call A.selfdestruct, and reverts.
    """
    optional_outer_call_code_1 = ""
    optional_outer_call_code_2 = ""
    optional_outer_call_code = f"""
        mstore(0, 1)
        pop(call(gaslimit(), {selfdestruct_with_transfer_contract_address}, 1, 0, 32, 0, 0))"""
    if selfdestruct_on_outer_call == 1:
        optional_outer_call_code_1 = optional_outer_call_code
    elif selfdestruct_on_outer_call == 2:
        optional_outer_call_code_2 = optional_outer_call_code

    """Contract code which calls selfdestructable contract, and also makes use of revert"""
    return yul(
        f"""
        {{
            let operation := calldataload(0)
            let op_outer_call := 0
            let op_inner_call := 1

            switch operation
            case 0 /* outer call */ {{
                // transfer value to contract and make it selfdestruct
                {optional_outer_call_code_1}

                // transfer value to the selfdestructed contract
                mstore(0, 0)
                pop(call(gaslimit(), {selfdestruct_with_transfer_contract_address}, 1, 0, 32, 0, 0))

                // recurse into self
                mstore(0, op_inner_call)
                pop(call(gaslimit(), address(), 0, 0, 32, 0, 0))

                // store the selfdestructed contract's balance for verification
                sstore(1, balance({selfdestruct_with_transfer_contract_address}))

                // transfer value to contract and make it selfdestruct
                {optional_outer_call_code_2}

                return(0, 0)
            }}
            case 1 /* inner call */ {{
                // trigger previously-selfdestructed contract to self destruct
                // and then revert

                mstore(0, 1)
                pop(call(gaslimit(), {selfdestruct_with_transfer_contract_address}, 1, 0, 32, 0, 0))
                revert(0, 0)
            }}
            default {{
                stop()
            }}
        }}
        """  # noqa: E272, E201, E202, E221, E501
    )


@pytest.fixture
def selfdestruct_with_transfer_contract_address(
    pre: Alloc,
    entry_code_address: Address,
    selfdestruct_with_transfer_contract_code: Bytecode,
    same_tx: bool,
) -> Address:
    """Contract address for contract that can selfdestruct and receive value."""
    if same_tx:
        return compute_create_address(address=entry_code_address, nonce=1)
    # We need to deploy the contract before.
    return pre.deploy_contract(selfdestruct_with_transfer_contract_code)


@pytest.fixture
def selfdestruct_with_transfer_contract_code(
    yul: YulCompiler, selfdestruct_recipient_address: Address
) -> Bytecode:
    """Contract that can selfdestruct and receive value."""
    return yul(
        f"""
        {{
            let operation := calldataload(0)

            switch operation
            case 0 /* no-op used for transferring value to this contract */ {{
                let times_called := sload(0)
                times_called := add(times_called, 1)
                sstore(0, times_called)
                return(0, 0)
            }}
            case 1 /* trigger the contract to selfdestruct */ {{
                let times_called := sload(1)
                times_called := add(times_called, 1)
                sstore(1, times_called)
                selfdestruct({selfdestruct_recipient_address})
            }}
            default /* unsupported operation */ {{
                stop()
            }}
        }}
        """  # noqa: E272, E201, E202, E221
    )


@pytest.fixture
def selfdestruct_with_transfer_contract_initcode(
    selfdestruct_with_transfer_contract_code: Bytecode,
) -> Bytecode:
    """Initcode for selfdestruct_with_transfer_contract_code."""
    return Initcode(deploy_code=selfdestruct_with_transfer_contract_code)


@pytest.fixture
def selfdestruct_with_transfer_initcode_copy_from_address(
    pre: Alloc,
    selfdestruct_with_transfer_contract_initcode: Bytecode,
) -> Address:
    """Address of a pre-existing contract we use to simply copy initcode from."""
    addr = pre.deploy_contract(selfdestruct_with_transfer_contract_initcode)
    return addr


@pytest.mark.parametrize(
    "same_tx",
    [True],
    ids=["same_tx"],
)
@pytest.mark.parametrize(
    "selfdestruct_on_outer_call",
    [0, 1, 2],
    ids=[
        "no_outer_selfdestruct",
        "outer_selfdestruct_before_inner_call",
        "outer_selfdestruct_after_inner_call",
    ],
)
@pytest.mark.valid_from("Cancun")
def test_selfdestruct_created_in_same_tx_with_revert(  # noqa SC200
    state_test: StateTestFiller,
    sender: EOA,
    env: Environment,
    pre: Alloc,
    entry_code_address: Address,
    selfdestruct_on_outer_call: int,
    selfdestruct_with_transfer_contract_code: Bytecode,
    selfdestruct_with_transfer_contract_initcode: Bytecode,
    selfdestruct_with_transfer_contract_address: Address,
    selfdestruct_recipient_address: Address,
    selfdestruct_with_transfer_initcode_copy_from_address: Address,
    recursive_revert_contract_address: Address,
    recursive_revert_contract_code: Bytecode,
):
    """
    Given:
        Contract A which has methods to receive balance and selfdestruct, and was created in current tx
    Test the following call sequence:
         Transfer value to A and call A.selfdestruct.
         Recurse into a new call from transfers value to A, calls A.selfdestruct, and reverts.
    """  # noqa: E501
    entry_code = Op.EXTCODECOPY(
        selfdestruct_with_transfer_initcode_copy_from_address,
        0,
        0,
        len(bytes(selfdestruct_with_transfer_contract_initcode)),
    )

    entry_code += Op.SSTORE(
        0,
        Op.CREATE(
            0,
            0,
            len(bytes(selfdestruct_with_transfer_contract_initcode)),  # Value  # Offset
        ),
    )

    entry_code += Op.CALL(
        Op.GASLIMIT(),
        recursive_revert_contract_address,
        0,  # value
        0,  # arg offset
        0,  # arg length
        0,  # ret offset
        0,  # ret length
    )

    post: Dict[Address, Account] = {
        entry_code_address: Account(
            code="0x",
            storage=Storage(
                {
                    0: selfdestruct_with_transfer_contract_address,  # type: ignore
                }
            ),
        ),
        selfdestruct_with_transfer_initcode_copy_from_address: Account(
            code=selfdestruct_with_transfer_contract_initcode,
        ),
        recursive_revert_contract_address: Account(
            code=recursive_revert_contract_code,
            storage=Storage({1: 1}),  # type: ignore
        ),
    }

    if selfdestruct_on_outer_call > 0:
        post[selfdestruct_with_transfer_contract_address] = Account.NONEXISTENT  # type: ignore
        post[selfdestruct_recipient_address] = Account(
            balance=1 if selfdestruct_on_outer_call == 1 else 2,
        )
    else:
        post[selfdestruct_with_transfer_contract_address] = Account(
            balance=1,
            code=selfdestruct_with_transfer_contract_code,
            storage=Storage(
                {
                    # 2 value transfers (1 in outer call, 1 in reverted inner call)
                    0: 1,  # type: ignore
                    # 1 selfdestruct in reverted inner call
                    1: 0,  # type: ignore
                }
            ),
        )
        post[selfdestruct_recipient_address] = Account.NONEXISTENT  # type: ignore

    tx = Transaction(
        value=0,
        data=entry_code,
        sender=sender,
        to=None,
        gas_limit=20_000_000,
    )

    state_test(env=env, pre=pre, post=post, tx=tx)


@pytest.mark.parametrize(
    "recursive_revert_contract_address_init_balance",
    [2],
    ids=["init_balance_2"],
)
@pytest.mark.parametrize(
    "same_tx",
    [False],
    ids=["not_same_tx"],
)
@pytest.mark.parametrize(
    "selfdestruct_on_outer_call",
    [0, 1, 2],
    ids=[
        "no_outer_selfdestruct",
        "outer_selfdestruct_before_inner_call",
        "outer_selfdestruct_after_inner_call",
    ],
)
@pytest.mark.valid_from("Cancun")
def test_selfdestruct_not_created_in_same_tx_with_revert(
    state_test: StateTestFiller,
    sender: EOA,
    env: Environment,
    entry_code_address: Address,
    pre: Alloc,
    selfdestruct_on_outer_call: int,
    selfdestruct_with_transfer_contract_code: Bytecode,
    selfdestruct_with_transfer_contract_address: Address,
    selfdestruct_recipient_address: Address,
    recursive_revert_contract_address: Address,
    recursive_revert_contract_code: Bytecode,
):
    """
    Same test as selfdestruct_created_in_same_tx_with_revert except selfdestructable contract
    is pre-existing.
    """
    entry_code = Op.CALL(
        Op.GASLIMIT(),
        recursive_revert_contract_address,
        0,  # value
        0,  # arg offset
        0,  # arg length
        0,  # ret offset
        0,  # ret length
    )

    post: Dict[Address, Account] = {
        entry_code_address: Account(code="0x"),
    }

    if selfdestruct_on_outer_call > 0:
        post[selfdestruct_with_transfer_contract_address] = Account(
            balance=1 if selfdestruct_on_outer_call == 1 else 0,
            code=selfdestruct_with_transfer_contract_code,
            storage=Storage(
                {
                    # 2 value transfers: 1 in outer call, 1 in reverted inner call
                    0: 1,  # type: ignore
                    # 1 selfdestruct in reverted inner call
                    1: 1,  # type: ignore
                }
            ),
        )
        post[selfdestruct_recipient_address] = Account(
            balance=1 if selfdestruct_on_outer_call == 1 else 2
        )
    else:
        post[selfdestruct_with_transfer_contract_address] = Account(
            balance=1,
            code=selfdestruct_with_transfer_contract_code,
            storage=Storage(
                {
                    # 2 value transfers: 1 in outer call, 1 in reverted inner call
                    0: 1,  # type: ignore
                    # 2 selfdestructs: 1 in outer call, 1 in reverted inner call # noqa SC100
                    1: 0,  # type: ignore
                }
            ),
        )
        post[selfdestruct_recipient_address] = Account.NONEXISTENT  # type: ignore

    tx = Transaction(
        value=0,
        data=entry_code,
        sender=sender,
        to=None,
        gas_limit=20_000_000,
    )

    state_test(env=env, pre=pre, post=post, tx=tx)
