from datetime import date, datetime
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from openpyxl import Workbook

from tracker.importing import address_key, match_property, one_line_address, split_type_prefix
from tracker.models import Photo, Property, Visit
from tracker.tests.conftest import make_jpeg

pytestmark = pytest.mark.django_db

REAL_XLSX = Path(__file__).resolve().parents[2] / "data" / "import" / "properties.xlsx"


def run(*args) -> str:
    out = StringIO()
    call_command(*args, stdout=out, stderr=out)
    return out.getvalue()


# --- helpers ----------------------------------------------------------------------------


def test_split_type_prefix_and_one_line():
    assert split_type_prefix("Townhouse:\n40017 Cedr Hollow Ct.\nSpringfield, ST 00001") == (
        "townhouse", "40017 Cedr Hollow Ct., Springfield, ST 00001")
    assert split_type_prefix("Condo: \n8008 Birch lane Apt O, \nLake Town, ST 00002") == (
        "condo", "8008 Birch lane Apt O, Lake Town, ST 00002")
    assert split_type_prefix("4321 Oakmont Dr. Springfield") == ("house", "4321 Oakmont Dr. Springfield")
    assert one_line_address("  a  \n\n b ") == "a, b"


def test_address_key():
    assert address_key("Condo: 7070 Willow Bend Circle #305") == ("7070", "willow")
    assert address_key("2101 maple ridge") == ("2101", "maple")
    assert address_key("No number here") is None


def test_match_property_rules(db):
    props = [
        Property.objects.create(address="2101 Maple Ridge Dr., Springfield"),
        Property.objects.create(address="2121 Maple Ridge Dr. Springfield"),
        Property.objects.create(address="40017 Cedr Hollow Ct., Springfield, ST 00001"),
    ]
    assert match_property("2101 MAPLE RIDGE DRIVE", props) == (props[0], "exact")
    assert match_property("Townhouse 40017 Cedar Hollow", props) == (props[2], "fuzzy")
    assert match_property("Maple Ridge 2121", props) == (props[1], "exact")
    assert match_property("Cedar Hollow Ct 40017 photos", props) == (props[2], "fuzzy")
    assert match_property("2101 Ashford", props)[0] is None
    assert match_property("9999 Nowhere Rd", props)[0] is None
    assert match_property("Misc photos", props)[0] is None


# --- import_properties -------------------------------------------------------------------


@pytest.mark.skipif(not REAL_XLSX.exists(), reason="real spreadsheet not present")
def test_import_real_spreadsheet_is_idempotent():
    out = run("import_properties", str(REAL_XLSX))
    assert "Properties created: 16" in out
    assert Property.objects.count() == 16
    types = {t: Property.objects.filter(property_type=t).count() for t in ("house", "townhouse", "condo")}
    assert types == {"house": 6, "townhouse": 4, "condo": 6}

    # Only the structure of the private file is checked here, never its addresses or tenant names.
    assert Property.objects.exclude(tenant_name="").exists()
    assert not any(a.lower().startswith(("condo", "townhouse")) or "\n" in a
                   for a in Property.objects.values_list("address", flat=True))

    snapshot = list(Property.objects.order_by("pk").values())
    out = run("import_properties", str(REAL_XLSX))
    assert "Properties created: 0, updated: 0, unchanged: 16" in out
    assert list(Property.objects.order_by("pk").values()) == snapshot


def _write_xlsx(path: Path, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["AC Maintenance plans:"])
    ws.append(["Property", "Tenant", "AC maintenance done\nDate", "Note"])
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_import_creates_visits_for_done_dates(tmp_path):
    xlsx = tmp_path / "p.xlsx"
    _write_xlsx(xlsx, [
        ["Condo:\n1 Main St #2\nSpringfield, FL", "Bob", datetime(2026, 10, 3), "Gate code 5678"],
        ["2 Oak Ave, Springfield", "Vacant", "10/15/2026", None],
        ["3 Pine Rd, Springfield", None, None, None],
        [None, None, None, None],
    ])
    run("import_properties", str(xlsx))
    assert Property.objects.count() == 3
    p1 = Property.objects.get(address="1 Main St #2, Springfield, FL")
    assert p1.property_type == "condo" and p1.note == "Gate code 5678"
    assert list(p1.visits.values_list("visit_date", flat=True)) == [date(2026, 10, 3)]
    assert Visit.objects.get(property__address="2 Oak Ave, Springfield").visit_date == date(2026, 10, 15)
    assert Visit.objects.count() == 2
    run("import_properties", str(xlsx))
    assert Visit.objects.count() == 2 and Property.objects.count() == 3


def test_import_if_empty_flag(tmp_path):
    xlsx = tmp_path / "p.xlsx"
    _write_xlsx(xlsx, [["1 Main St", "Bob", None, None]])
    Property.objects.create(address="Existing 9 Rd")
    out = run("import_properties", str(xlsx), "--if-empty")
    assert "skipping" in out
    assert Property.objects.count() == 1


def test_import_missing_header_fails(tmp_path):
    from django.core.management.base import CommandError

    wb = Workbook()
    wb.active.append(["nothing", "here"])
    wb.save(tmp_path / "bad.xlsx")
    with pytest.raises(CommandError):
        run("import_properties", str(tmp_path / "bad.xlsx"))


# --- import_photos -----------------------------------------------------------------------


@pytest.fixture
def photo_tree(tmp_path):
    """A Drive-like 'Photo' folder with mismatched names, a typo and a misfiled photo."""
    Property.objects.create(address="2101 Maple Ridge Dr., Springfield")
    Property.objects.create(address="40017 Cedr Hollow Ct., Springfield, ST 00001",
                            property_type="townhouse")
    Property.objects.create(address="7070 Willow Bend Circle #305, Rivertn, ST 00002", property_type="condo")

    root = tmp_path / "Photo"
    maple = root / "2101 maple ridge drive"
    cedar = root / "40017 Cedar Hollow"  # typo vs spreadsheet ("Cedr")
    willow = root / "7070 Willow Bend Cir 305 Riverton"
    unknown = root / "Old stuff"
    for d in (maple, cedar, willow, unknown):
        d.mkdir(parents=True)

    misfiled = make_jpeg(color=(1, 2, 3))
    (maple / "IMG_20250410_101010.jpg").write_bytes(make_jpeg(color=(10, 0, 0)))
    (maple / "IMG_20250410_101500.jpg").write_bytes(make_jpeg(color=(20, 0, 0)))
    (maple / "IMG_20251020_090000.jpg").write_bytes(make_jpeg(color=(30, 0, 0)))
    (maple / "IMG_20251020_090500.jpg").write_bytes(misfiled)
    # EXIF date wins over the file name
    (cedar / "IMG_20200101_000000.jpg").write_bytes(make_jpeg(color=(40, 0, 0), taken="2025:10:21 08:00:00"))
    (willow / "IMG_20251022_120000.jpg").write_bytes(make_jpeg(color=(50, 0, 0)))
    (willow / "IMG_20251022_120100.jpg").write_bytes(misfiled)  # same file as in maple folder
    (willow / "notes.txt").write_text("not a photo")
    (unknown / "IMG_20250101_000000.jpg").write_bytes(make_jpeg(color=(60, 0, 0)))
    return root


def test_import_photos(photo_tree):
    out = run("import_photos", str(photo_tree))

    maple = Property.objects.get(address__startswith="2101")
    cedr = Property.objects.get(address__startswith="40017")
    willow = Property.objects.get(address__startswith="7070")

    assert sorted(maple.visits.values_list("visit_date", flat=True)) == [date(2025, 4, 10), date(2025, 10, 20)]
    assert maple.visits.get(visit_date=date(2025, 4, 10)).photos.count() == 2
    assert maple.visits.get(visit_date=date(2025, 10, 20)).photos.count() == 1
    assert list(cedr.visits.values_list("visit_date", flat=True)) == [date(2025, 10, 21)]
    # The misfiled file sits in two properties' folders: neither copy is imported.
    assert willow.visits.get().photos.count() == 1
    assert Photo.objects.count() == 5

    assert "Properties matched: 3" in out
    assert "Visits created:     4" in out
    assert "Photos imported:    5" in out
    assert "Cross-property duplicates (not imported): 2" in out
    assert "IMG_20251022_120100.jpg" in out and "IMG_20251020_090500.jpg" in out
    assert maple.address in out and willow.address in out
    assert "Unmatched folders: 1" in out and "Old stuff" in out


def test_photo_already_in_db_under_other_property_is_not_imported(photo_tree):
    from tracker.services import add_uploaded_photo

    other = Property.objects.create(address="777 Elsewhere Rd")
    v = Visit.objects.create(property=other, visit_date=date(2025, 1, 1))
    data = (photo_tree / "40017 Cedar Hollow" / "IMG_20200101_000000.jpg").read_bytes()
    add_uploaded_photo(v, data, "uploaded.jpg")
    out = run("import_photos", str(photo_tree))
    cedr = Property.objects.get(address__startswith="40017")
    assert not Photo.objects.filter(visit__property=cedr).exists()
    assert "777 Elsewhere Rd" in out


def test_import_photos_is_idempotent(photo_tree):
    run("import_photos", str(photo_tree))
    before = (Photo.objects.count(), Visit.objects.count())
    out = run("import_photos", str(photo_tree))
    assert (Photo.objects.count(), Visit.objects.count()) == before
    assert "Photos imported:    0" in out
    assert "Visits created:     0" in out
    assert "Skipped (already imported): 5" in out
    assert "Cross-property duplicates (not imported): 2" in out
