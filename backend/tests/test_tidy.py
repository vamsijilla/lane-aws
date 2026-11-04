"""Code's corrections to the model's answer before facts: roles in date columns, and cleaners."""

from lane.domain.normalize import drop_presence_markers
from lane.extract import catalog as cat
from lane.extract.engine_v2 import tidy


def test_owner_in_a_date_column_becomes_the_role_and_the_date_is_empty() -> None:
    data = {"drivers": [{"name": "Ana Garcia", "hire_date": "OWNER", "quote": "Ana Garcia OWNER"}]}
    notes = tidy("quote_form", data)
    row = data["drivers"][0]
    assert row["role"] == "OWNER"
    assert row["hire_date"] is None
    assert notes == ["drivers[0].hire_date: 'OWNER' is a role, not a date"]


def test_a_real_date_is_left_alone() -> None:
    data = {"drivers": [{"name": "Ana Garcia", "hire_date": "03/01/2022"}]}
    assert tidy("quote_form", data) == []
    assert data["drivers"][0]["hire_date"] == "03/01/2022"
    assert "role" not in data["drivers"][0]


def test_the_role_field_is_never_asked_of_the_model() -> None:
    schema = cat.pdf_schema("quote_form")
    assert schema is not None
    drivers = schema.model_fields["drivers"].annotation
    assert "role" not in str(drivers.__args__[0].model_fields)  # type: ignore[union-attr]
    assert "- role:" not in cat.prompt_fields("quote_form", "pdf")
    assert cat.entry_for("quote_form", "drivers[0].role") is not None


def test_license_type_loses_its_legal_presence_designation() -> None:
    data = {
        "license_type": {
            "value": "COMMERCIAL DRIVER LICENSE, LIMITED-TERM",
            "quote": "COMMERCIAL DRIVER LICENSE, LIMITED-TERM",
            "page": 1,
        }
    }
    tidy("drivers_license", data)
    assert data["license_type"]["value"] == "COMMERCIAL DRIVER LICENSE"
    assert data["license_type"]["quote"] == "COMMERCIAL DRIVER LICENSE"


def test_drop_presence_markers() -> None:
    assert drop_presence_markers("CDL / NON-DOMICILED") == "CDL"
    assert drop_presence_markers("Limited Term") is None
    assert drop_presence_markers("COMMERCIAL DRIVER LICENSE") == "COMMERCIAL DRIVER LICENSE"
