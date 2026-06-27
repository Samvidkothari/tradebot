"""
test_schemas.py — unit tests for the JSON contract validator (schemas.py).
"""

import schemas


def test_valid_payload_passes_and_returns_data():
    payload = {k: None for k in schemas.REQUIRED["risk.json"]}
    assert schemas.validate("risk.json", payload) is payload


def test_missing_key_raises_with_name():
    payload = {"strategies": {}}                       # data_quality needs more
    try:
        schemas.validate("data_quality.json", payload)
        assert False, "expected SchemaError"
    except schemas.SchemaError as e:
        assert "data_quality.json" in str(e)
        assert "missing" in str(e).lower()


def test_unknown_file_raises():
    try:
        schemas.validate("nope.json", {})
        assert False, "expected SchemaError"
    except schemas.SchemaError:
        pass


def test_non_dict_raises():
    try:
        schemas.validate("risk.json", ["not", "a", "dict"])
        assert False, "expected SchemaError"
    except schemas.SchemaError:
        pass


def test_every_required_entry_is_nonempty():
    for name, keys in schemas.REQUIRED.items():
        assert keys, f"{name} has no required keys"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
