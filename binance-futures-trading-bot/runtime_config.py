import importlib.util
import os
from pathlib import Path
from typing import Tuple


APP_ROOT = Path(__file__).resolve().parent
TRADING_BOT_DIR = APP_ROOT / "trading-bot"

TESTNET_BASE_URL = "https://testnet.binance.vision/api"
TESTNET_FUTURES_URL = "https://testnet.binancefuture.com/fapi"


def _load_module_attr(module_path: Path, attr_name: str) -> str | None:
    if not module_path.exists():
        return None

    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, attr_name, None)


def _read_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _load_testnet_credentials() -> Tuple[str | None, str | None]:
    api_key = _read_env("BINANCE_TESTNET_API_KEY")
    secret_key = _read_env("BINANCE_TESTNET_SECRET_KEY")
    if api_key and secret_key:
        return api_key, secret_key

    module_path = TRADING_BOT_DIR / "apikey_testnet.py"
    return (
        _load_module_attr(module_path, "testnet_api_key"),
        _load_module_attr(module_path, "testnet_secret_key"),
    )


def _load_mainnet_credentials() -> Tuple[str | None, str | None]:
    api_key = _read_env("BINANCE_API_KEY")
    secret_key = _read_env("BINANCE_SECRET_KEY")
    if api_key and secret_key:
        return api_key, secret_key

    module_path = TRADING_BOT_DIR / "apikey.py"
    return (
        _load_module_attr(module_path, "api_key"),
        _load_module_attr(module_path, "secret_key"),
    )


def get_binance_credentials(use_testnet: bool) -> Tuple[str, str]:
    if use_testnet:
        api_key, secret_key = _load_testnet_credentials()
        required = "BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_SECRET_KEY"
    else:
        api_key, secret_key = _load_mainnet_credentials()
        required = "BINANCE_API_KEY and BINANCE_SECRET_KEY"

    if not api_key or not secret_key:
        raise RuntimeError(
            f"Binance credentials are missing. Set {required} or provide the local key file."
        )

    return api_key, secret_key


def get_spy_predictor_api_url() -> str | None:
    value = _read_env("SPY_PREDICTOR_API_URL")
    if not value:
        return None
    return value.rstrip("/")
