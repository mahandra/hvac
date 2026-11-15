"""MCP server tools: direct calls, plus one end-to-end round trip through the MCP protocol."""

import asyncio
from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from mcp import Client
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from tracker import mcp_server as m
from tracker.models import Photo, Property, Visit

pytestmark = pytest.mark.django_db


@pytest.fixture
def photo(auth_client, visit, jpeg):
    auth_client.post(
        reverse("photo_upload", args=[visit.pk]),
        {"file": SimpleUploadedFile("a.jpg", jpeg(size=(1200, 900)), content_type="image/jpeg")},
    )
    return Photo.objects.get()


def test_list_properties_filters_and_order(prop, other_prop, visit):
    today = timezone.localdate()
    Visit.objects.create(property=other_prop, visit_date=today, paid=True)
    Property.objects.create(address="1 Hidden Rd", is_active=False)

    rows = m.list_properties()
    assert [r["address"] for r in rows] == [other_prop.address, prop.address]  # newest visit first
    by_addr = {r["address"]: r for r in rows}
    assert by_addr[prop.address]["unpaid_visits"] == 1
    assert by_addr[other_prop.address]["due"] is False

    assert [r["id"] for r in m.list_properties(only_unpaid=True)] == [prop.pk]
    assert [r["id"] for r in m.list_properties(search="willow")] == [other_prop.pk]


def test_list_properties_due(prop, other_prop):
    Visit.objects.create(property=prop, visit_date=timezone.localdate() - timedelta(days=10))
    assert [r["id"] for r in m.list_properties(only_due=True)] == [other_prop.pk]


@pytest.mark.parametrize("ref", ["2101 maple ridge dr., springfield", "Maple", "2101 Ridge", "{pk}"])
def test_find_property_by_id_or_address(prop, other_prop, ref):
    assert m._find_property(ref.format(pk=prop.pk)) == prop


def test_find_property_ambiguous_and_missing(prop, other_prop):
    with pytest.raises(ToolError, match="several properties"):
        m._find_property(",")
    with pytest.raises(ToolError, match="No property matches"):
        m._find_property("Nowhere Lane")


def test_get_property_and_visit(visit, photo):
    data = m.get_property(str(visit.property_id))
    assert data["visits"][0]["id"] == visit.pk and data["visits"][0]["photo_count"] == 1
    assert data["maps_url"].startswith("https://www.google.com/maps/")
    v = m.get_visit(visit.pk)
    assert v["filter_size"] == "20x25x1" and v["paid"] is False
    assert v["photos"] == [{"id": photo.pk, "filename": "a.jpg", "taken_at": v["photos"][0]["taken_at"]}]
    with pytest.raises(ToolError):
        m.get_visit(999999)


def test_get_photo_returns_jpeg(photo):
    thumb = m.get_photo(photo.pk)
    full = m.get_photo(photo.pk, full_size=True)
    assert isinstance(thumb, Image) and thumb.data[:2] == b"\xff\xd8"
    assert full.data[:2] == b"\xff\xd8"
    with pytest.raises(ToolError):
        m.get_photo(999999)


def test_create_visit_defaults_and_clash(visit):
    created = m.create_visit("Maple Ridge", outdoor_unit_cleaned=True, note="all good")
    assert created["visit_date"] == timezone.localdate().isoformat()
    assert created["filter_size"] == "20x25x1"  # copied from the previous visit
    assert created["outdoor_unit_cleaned"] and not created["paid"]
    with pytest.raises(ToolError, match="already has a visit"):
        m.create_visit("Maple Ridge")
    with pytest.raises(ToolError, match="Invalid date"):
        m.create_visit("Maple Ridge", visit_date="11/14/2026")


def test_update_visit_changes_only_given_fields(visit):
    data = m.update_visit(visit.pk, filter_replaced=True, note="replaced")
    assert data["filter_replaced"] and data["note"] == "replaced"
    assert data["filter_size"] == "20x25x1" and data["visit_date"] == "2026-10-01"
    Visit.objects.create(property=visit.property, visit_date=date(2026, 10, 5))
    with pytest.raises(ToolError, match="already has a visit"):
        m.update_visit(visit.pk, visit_date="2026-10-05")


def test_set_visit_paid(visit):
    assert m.set_visit_paid(visit.pk)["paid"] is True
    visit.refresh_from_db()
    assert visit.paid
    m.set_visit_paid(visit.pk, paid=False)
    visit.refresh_from_db()
    assert not visit.paid


def test_list_unpaid_visits(visit):
    Visit.objects.create(property=visit.property, visit_date=date(2026, 9, 1), paid=True)
    assert [v["id"] for v in m.list_unpaid_visits()] == [visit.pk]


def test_management_command_is_registered():
    from django.core.management import get_commands

    assert get_commands()["mcp_server"] == "tracker"


@pytest.mark.django_db(transaction=True)  # tools run in worker threads with their own DB connections
def test_protocol_round_trip(visit):
    async def run():
        async with Client(m.build_server()) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            listed = await client.call_tool("list_properties", {})
            paid = await client.call_tool("set_visit_paid", {"visit_id": visit.pk})
            missing = await client.call_tool("get_visit", {"visit_id": 999999})
            return tools, listed, paid, missing

    tools, listed, paid, missing = asyncio.run(run())
    assert set(tools) == {
        "list_properties", "get_property", "get_visit", "list_unpaid_visits", "get_photo",
        "create_visit", "update_visit", "set_visit_paid",
    }
    assert tools["list_properties"].annotations.read_only_hint is True
    assert tools["set_visit_paid"].annotations.destructive_hint is False
    assert not listed.is_error and "Maple Ridge" in listed.content[0].text
    assert not paid.is_error
    assert missing.is_error and "No visit with id" in missing.content[0].text
    visit.refresh_from_db()
    assert visit.paid
