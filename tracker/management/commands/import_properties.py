from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from tracker.importing import normalize_header, parse_date, split_type_prefix
from tracker.models import Property, Visit

COLUMNS = {
    "property": "property",
    "tenant": "tenant",
    "ac maintenance done date": "done_date",
    "note": "note",
}


class Command(BaseCommand):
    help = "Import properties (and done dates as visits) from the AC maintenance spreadsheet. Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the .xlsx file")
        parser.add_argument(
            "--if-empty",
            action="store_true",
            help="Do nothing unless the properties table is empty (used on first container start).",
        )

    def handle(self, *args, path, if_empty=False, **options):
        xlsx = Path(path)
        if if_empty and Property.objects.exists():
            self.stdout.write("Properties already present; skipping spreadsheet import.")
            return
        if not xlsx.is_file():
            raise CommandError(f"File not found: {xlsx}")

        rows = self.read_rows(xlsx)
        counts = {"created": 0, "updated": 0, "unchanged": 0, "visits": 0}
        with transaction.atomic():
            for row in rows:
                self.import_row(row, counts)

        self.stdout.write(
            self.style.SUCCESS(
                f"Rows: {len(rows)}. Properties created: {counts['created']}, "
                f"updated: {counts['updated']}, unchanged: {counts['unchanged']}. "
                f"Visits created: {counts['visits']}."
            )
        )

    def read_rows(self, xlsx: Path) -> list[dict]:
        wb = load_workbook(xlsx, data_only=True, read_only=True)
        ws = wb.worksheets[0]
        header_idx = None
        rows = []
        for values in ws.iter_rows(values_only=True):
            if header_idx is None:
                if values and normalize_header(values[0]) == "property":
                    header_idx = {
                        COLUMNS[normalize_header(v)]: i
                        for i, v in enumerate(values)
                        if normalize_header(v) in COLUMNS
                    }
                continue
            get = lambda name: values[header_idx[name]] if name in header_idx and header_idx[name] < len(values) else None  # noqa: E731
            if not str(get("property") or "").strip():
                continue
            rows.append(
                {
                    "property": get("property"),
                    "tenant": get("tenant"),
                    "done_date": get("done_date"),
                    "note": get("note"),
                }
            )
        wb.close()
        if header_idx is None:
            raise CommandError("Header row (first cell 'Property') not found.")
        return rows

    def import_row(self, row: dict, counts: dict) -> None:
        prop_type, address = split_type_prefix(row["property"])
        tenant = str(row["tenant"] or "").strip()
        note = str(row["note"] or "").strip()

        prop = Property.objects.filter(address__iexact=address).first()
        if prop is None:
            prop = Property.objects.create(
                address=address, property_type=prop_type, tenant_name=tenant, note=note
            )
            counts["created"] += 1
            self.stdout.write(f"  + {address} ({prop.get_property_type_display()})")
        else:
            changed = []
            for field, value in (("property_type", prop_type), ("tenant_name", tenant), ("note", note)):
                if getattr(prop, field) != value:
                    setattr(prop, field, value)
                    changed.append(field)
            if changed:
                prop.save(update_fields=changed)
                counts["updated"] += 1
                self.stdout.write(f"  ~ {address}: updated {', '.join(changed)}")
            else:
                counts["unchanged"] += 1

        done = parse_date(row["done_date"])
        if done is None and row["done_date"] not in (None, ""):
            self.stderr.write(f"  ! {address}: could not parse done date {row['done_date']!r}")
        if done:
            _, created = Visit.objects.get_or_create(
                property=prop,
                visit_date=done,
                defaults={
                    "outdoor_unit_cleaned": True,
                    "filter_replaced": True,
                    "note": "Imported from spreadsheet.",
                },
            )
            if created:
                counts["visits"] += 1
