"""Verify explicit workspace selection against a disposable MCP stack."""
import argparse
import asyncio
import uuid

from mcp_client import call, mcp_session


async def run(url, key, other_key):
    prefix = "selection-" + uuid.uuid4().hex[:8]
    async with mcp_session(url, key) as a, mcp_session(url, key) as b:
        original = await call(a, "workspace", op="current")
        try:
            first = await call(a, "workspace", op="new", name=prefix + "-a", activate=False)
            second = await call(b, "workspace", op="new", name=prefix + "-b", activate=False)
            assert first["id"] != second["id"] and not first["active"] and not second["active"]
            assert (await call(a, "workspace", op="current"))["id"] == original["id"]
            await call(b, "workspace", op="use", name=prefix + "-b")

            async def write(session, selected, label):
                result = await call(session, "load", op="inline", table_name="same_name",
                                    rows=[{"owner": label}], source="regression fixture",
                                    workspace_id=selected["id"])
                assert result["table"] == selected["ws_schema"] + ".same_name"
                query = await call(session, "query", sql=f"SELECT owner FROM {result['table']}",
                                   workspace_id=selected["id"])
                assert query["rows"] == [[label]]

            await asyncio.gather(write(a, first, "a"), write(b, second, "b"))
            assert (await call(a, "workspace", op="current"))["id"] == second["id"]
            async with mcp_session(url, key) as reconnect:
                current = await call(reconnect, "workspace", workspace_id=first["id"])
                assert current["id"] == first["id"] and "same_name" in current["layers"]
            async with mcp_session(url, other_key) as stranger:
                denied = await call(stranger, "layer", op="list", workspace_id=first["id"])
                assert "auth:" in denied["error"]
            denied = await call(a, "workspace", workspace_id="invalid")
            assert "auth:" in denied["error"]
            tools = (await a.list_tools()).tools
            assert len(tools) == 8
            assert all("workspace_id" in tool.inputSchema["properties"] for tool in tools)
            print("PASS concurrent selection, unchanged default, reconnect, ownership, and all eight schemas")
        finally:
            for suffix in ("-a", "-b"):
                await call(a, "workspace", op="delete", name=prefix + suffix)
            await call(a, "workspace", op="use", name=original["workspace"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--other-key", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.key, args.other_key))
