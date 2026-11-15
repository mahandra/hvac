from django.core.management.base import BaseCommand

from tracker.mcp_server import build_server


class Command(BaseCommand):
    help = "Run the MCP server over stdio (for Claude Code / Claude Desktop). Nothing else may write to stdout."

    def handle(self, *args, **options):
        build_server().run("stdio")
