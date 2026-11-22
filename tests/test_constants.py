from railpay import (
    CHAIN_ID,
    DEFAULT_BASE_URL,
    DEFAULT_FACILITATORS,
    DEFAULT_RPC_URL,
    MIN_X402_AMOUNT,
    NETWORK,
    USDG,
    USDG_DECIMALS,
    USDG_EIP712,
    address_url,
    tx_url,
)


def test_constants_match_the_typescript_sdk() -> None:
    assert CHAIN_ID == 4663
    assert NETWORK == "eip155:4663"
    assert USDG == "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
    assert USDG_DECIMALS == 6
    assert USDG_EIP712 == ("Global Dollar", "1")
    assert USDG_EIP712.name == "Global Dollar"
    assert DEFAULT_FACILITATORS == ("https://x402.t0ll.xyz", "https://x402.primer.systems")
    assert MIN_X402_AMOUNT == 1000
    assert DEFAULT_BASE_URL == "https://dapp.railpay.app"
    assert DEFAULT_RPC_URL == "https://robinhood-rpc.publicnode.com"


def test_explorer_links() -> None:
    assert tx_url("0xabc") == "https://robinhoodchain.blockscout.com/tx/0xabc"
    assert address_url(USDG) == f"https://robinhoodchain.blockscout.com/address/{USDG}"
