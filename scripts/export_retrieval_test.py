"""Verify export retrieval reuses jobs and enforces workspace selection."""
import argparse
import asyncio
import uuid
from urllib.parse import urlsplit

import httpx

from mcp_client import call, mcp_session


async def run(url, key, other_key):
    name = "export-test-" + uuid.uuid4().hex[:8]
    async with mcp_session(url, key) as session:
        workspace = await call(session, "workspace", op="new", name=name, activate=False)
        wid = workspace["id"]
        try:
            layer = await call(session, "load", op="inline", table_name="places",
                               rows=[{"name": "Fixture", "lon": 17.3, "lat": 62.4}],
                               source="regression fixture", workspace_id=wid)
            export = await call(session, "export", layers=[layer["table"]], format="csv", workspace_id=wid)
            jid = export["job_id"]
            for _ in range(30):
                export = await call(session, "export", job_id=jid, workspace_id=wid)
                if export.get("status") == "done":
                    break
                assert export.get("status") in ("queued", "running"), export
                await asyncio.sleep(1)
            assert export["status"] == "done"
            path = urlsplit(export["url"]).path
            for _ in range(3):
                retrieved = await call(session, "export", job_id=jid, workspace_id=wid)
                assert retrieved["job_id"] == jid and urlsplit(retrieved["url"]).path == path
            count = await call(session, "query", workspace_id=wid,
                               sql=f"SELECT count(*) FROM app.jobs WHERE kind='export' AND workspace_id='{wid}'")
            assert count["rows"] == [[1]], count
            async with httpx.AsyncClient() as client:
                download = await client.get(retrieved["url"])
                assert download.status_code == 200 and "Fixture" in download.text
                assert (await client.get(retrieved["sidecar_url"])).status_code == 200
            jobs = await call(session, "load", op="jobs", workspace_id=wid)
            assert all(job["workspace_id"] == wid for job in jobs["jobs"])
            async with mcp_session(url, other_key) as stranger:
                for tool, args in (("export", {}), ("load", {"op": "status"}),
                                   ("analyze", {"op": "cancel"})):
                    denied = await call(stranger, tool, job_id=jid, **args)
                    assert denied["error"] == f"no job with id {jid}", denied
            print("PASS repeated retrieval creates no jobs, same artifacts download, and cross-workspace access is denied")
        finally:
            await call(session, "workspace", op="delete", name=name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--other-key", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.key, args.other_key))
