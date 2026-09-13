"""Start the container as an MCP server over stdio and check it introspects.

Registries (Glama, Docker's MCP catalog) do exactly this: `docker run -i <image>`,
then initialize + tools/list.  Failing here means the listing check fails too.
"""
import asyncio
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED = {
    "watch_video",
    "get_transcript",
    "search_transcript",
    "get_frames",
    "get_frame_at",
    "list_videos",
}


async def main() -> int:
    params = StdioServerParameters(
        command="docker",
        args=["run", "--rm", "-i", "yueying:ci"],
    )
    with open("docker-stderr.log", "w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                print("server:", init.server_info.name, init.server_info.version)
                tools = {t.name for t in (await session.list_tools()).tools}
                print("tools:", sorted(tools))
                assert tools == EXPECTED, f"unexpected tools: {tools ^ EXPECTED}"
                result = await session.call_tool("list_videos", {})
                text = result.content[0].text
                print("list_videos:", text.splitlines()[0])
                assert not result.is_error, text
    stderr = open("docker-stderr.log", encoding="utf-8").read()
    assert "Traceback" not in stderr, stderr[-2000:]
    print("ok")
    return 0


sys.exit(asyncio.run(main()))
