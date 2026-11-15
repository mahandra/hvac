import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tracker.images import ImageValidationError, process_image, sha256_of
from tracker.importing import match_property
from tracker.models import Photo, Property, Visit
from tracker.services import addresses_with_hash, save_photo

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


class Command(BaseCommand):
    help = (
        "Import photos from a directory with one subfolder per address. "
        "Photos are grouped into visits by the date they were taken. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument("directory", help="Downloaded Google Drive 'Photo' folder")

    def handle(self, *args, directory, **options):
        root = Path(directory)
        if not root.is_dir():
            raise CommandError(f"Not a directory: {root}")

        properties = list(Property.objects.all())
        stats = defaultdict(int)
        unmatched: list[tuple[str, str]] = []
        duplicates: list[tuple[str, str, list[str]]] = []
        errors: list[tuple[str, str]] = []
        matched: list[tuple[Path, Property]] = []

        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            prop, how = match_property(folder.name, properties)
            if prop is None:
                unmatched.append((folder.name, how))
                self.stdout.write(self.style.WARNING(f"Unmatched folder: {folder.name} ({how})"))
                continue
            label = " (fuzzy match)" if how == "fuzzy" else ""
            self.stdout.write(f"{folder.name} -> {prop.address}{label}")
            matched.append((folder, prop))

        # A file present in folders of *different* properties is misfiled somewhere. Folder order says
        # nothing about which copy is right, so none of the copies is imported; all are reported.
        conflicts = self.find_cross_folder_duplicates(matched)

        for folder, prop in matched:
            self.import_folder(folder, prop, stats, duplicates, errors, conflicts)

        matched_props = {prop.pk for _, prop in matched}

        self.print_summary(len(matched_props), stats, unmatched, duplicates, errors)

    # ------------------------------------------------------------------
    @staticmethod
    def image_files(folder: Path) -> list[Path]:
        return sorted(f for f in folder.rglob("*") if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES)

    def find_cross_folder_duplicates(self, matched) -> dict[str, set[str]]:
        """sha256 -> addresses, for hashes found in folders of more than one property."""
        seen: dict[str, set[str]] = defaultdict(set)
        for folder, prop in matched:
            for path in self.image_files(folder):
                try:
                    seen[sha256_of(path.read_bytes())].add(prop.address)
                except OSError:
                    continue  # reported later by import_folder
        return {digest: addrs for digest, addrs in seen.items() if len(addrs) > 1}

    def import_folder(self, folder: Path, prop: Property, stats, duplicates, errors, conflicts) -> None:
        files = self.image_files(folder)
        visits: dict = {}
        for path in files:
            rel = str(path.relative_to(folder.parent))
            try:
                data = path.read_bytes()
            except OSError as exc:
                errors.append((rel, str(exc)))
                continue
            digest = sha256_of(data)

            if Photo.objects.filter(sha256=digest, visit__property=prop).exists():
                stats["skipped"] += 1
                continue
            others = sorted(set(addresses_with_hash(digest, prop.pk)) | (conflicts.get(digest, set()) - {prop.address}))
            if others:
                duplicates.append((rel, prop.address, others))
                continue

            try:
                processed = process_image(data, path.name)
            except ImageValidationError as exc:
                errors.append((rel, str(exc)))
                continue

            taken = processed.taken_at or timezone.make_aware(datetime.fromtimestamp(os.path.getmtime(path)))
            visit_date = timezone.localtime(taken).date()
            visit = visits.get(visit_date)
            if visit is None:
                visit, created = Visit.objects.get_or_create(
                    property=prop,
                    visit_date=visit_date,
                    defaults={"note": "Imported from photo folder."},
                )
                if created:
                    stats["visits_created"] += 1
                visits[visit_date] = visit
            save_photo(visit, processed, path.name)
            stats["imported"] += 1

    def print_summary(self, matched, stats, unmatched, duplicates, errors) -> None:
        out = self.stdout
        out.write("")
        out.write(self.style.MIGRATE_HEADING("Summary"))
        out.write(f"  Properties matched: {matched}")
        out.write(f"  Visits created:     {stats['visits_created']}")
        out.write(f"  Photos imported:    {stats['imported']}")
        out.write(f"  Skipped (already imported): {stats['skipped']}")
        out.write(f"  Cross-property duplicates (not imported): {len(duplicates)}")
        for rel, target, others in duplicates:
            out.write(f"    - {rel}: folder matches '{target}', same file also belongs to: {'; '.join(others)}")
        out.write(f"  Unmatched folders: {len(unmatched)}")
        for name, reason in unmatched:
            out.write(f"    - {name} ({reason})")
        if errors:
            out.write(f"  Errors: {len(errors)}")
            for rel, msg in errors:
                out.write(f"    - {rel}: {msg}")
