import pytest

from shop import settings
from shop.settings import load_settings, on_order, run_hooks


def test_defaults():
    assert load_settings(environ={}) == {"currency": "$", "shop_name": "Upshift Shop",
                                         "send_receipts": True, "tax_rate": 0.0}


def test_ini_file_overrides_defaults(tmp_path):
    ini = tmp_path / "shop.ini"
    ini.write_text("[shop]\ncurrency = €\nsend_receipts = no\ntax_rate = 0.2\n")
    loaded = load_settings(ini, environ={})
    assert loaded["currency"] == "€" and loaded["send_receipts"] is False and loaded["tax_rate"] == 0.2


def test_environment_wins(tmp_path):
    assert load_settings(environ={"SHOP_SEND_RECEIPTS": "off", "SHOP_CURRENCY": "£"})["send_receipts"] is False


def test_invalid_boolean_is_rejected():
    with pytest.raises(ValueError):
        load_settings(environ={"SHOP_SEND_RECEIPTS": "maybe"})


def test_hooks_get_settings_only_when_they_take_two_arguments(monkeypatch):
    monkeypatch.setattr(settings, "HOOKS", [])

    @on_order
    def notify(order):
        return f"notify {order}"

    @on_order
    def bill(order, conf):
        return f"bill {order} in {conf['currency']}"

    assert run_hooks("ord_1", {"currency": "$"}) == ["notify ord_1", "bill ord_1 in $"]
