import pytest

from signalpost import orgnr


@pytest.mark.parametrize("raw", ["923609016", "923 609 016", "NO 923 609 016 MVA", "923.609.016", " 923609016 "])
def test_parse_accepts_common_formats(raw):
    assert orgnr.parse(raw) == "923609016"


@pytest.mark.parametrize("raw", ["123456789", "92360901", "9236090160", "abcdefghi", ""])
def test_parse_rejects_invalid_numbers(raw):
    with pytest.raises(orgnr.InvalidOrgnr):
        orgnr.parse(raw)


def test_checksum_rejects_every_number_whose_control_digit_would_be_ten():
    # Prefix 10000013 has weighted-sum remainder 1, so its control digit would be 10: no valid number exists.
    assert not any(orgnr.checksum_ok(f"10000013{d}") for d in range(10))


def test_checksum_accepts_known_valid_number():
    assert orgnr.checksum_ok("100000016")


def test_pretty_groups_digits():
    assert orgnr.pretty("923609016") == "923 609 016"
