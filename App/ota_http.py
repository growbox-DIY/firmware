# ota_http.py - HTTP routes for OTA updates (logic in ota.py)
import machine
import uasyncio as asyncio

import display
import ota
import server


async def _reset_soon():
    # a moment to close the connection before the board goes away
    await asyncio.sleep(1)
    machine.reset()


async def handle(method, route, request, body, reader, writer, cors):
    """OTA routes; returns False when route is not an OTA route."""

    # GET /api/update  (state, files with sha256, files waiting for apply)

    if method == "GET" and route == "/api/update":
        query = server._parse_query(request.split("\r\n", 1)[0].split(" ")[1])
        names = query.get("files")
        await server._send_json(
            writer, cors, "200 OK",
            await ota.status(names.split(",") if names else None)
        )
        return True

    # PUT /api/files/<file>?sha256=<hex>

    if method == "PUT" and route.startswith("/api/files/"):
        name = route[len("/api/files/"):]
        display.show_update("RECEIVING")

        try:
            length = int(server._header(request, "content-length") or "0")
            query = server._parse_query(request.split("\r\n", 1)[0].split(" ")[1])
            digest = await ota.receive(
                name, reader, length, body, query.get("sha256")
            )
        except ota.OtaError as e:
            await server._send_json(
                writer, cors, "400 Bad Request",
                {"status": "error", "error": str(e)}
            )
            return True

        print("OTA: staged", name, length, "B")

        await server._send_json(
            writer, cors, "200 OK",
            {"status": "staged", "name": name, "sha256": digest}
        )
        return True

    # POST /api/update/apply  (swap files and reset)

    if method == "POST" and route == "/api/update/apply":
        try:
            files = ota.apply()
        except ota.OtaError as e:
            await server._send_json(
                writer, cors, "400 Bad Request",
                {"status": "error", "error": str(e)}
            )
            return True

        print("OTA: applied", files, "-> reset")
        display.show_update("INSTALLING")

        await server._send_json(
            writer, cors, "200 OK",
            {"status": "applied", "files": files}
        )
        asyncio.create_task(_reset_soon())
        return True

    # POST /api/update/discard  (remove .new files after an interrupted upload)

    if method == "POST" and route == "/api/update/discard":
        display.end_update()
        await server._send_json(
            writer, cors, "200 OK",
            {"status": "discarded", "files": ota.discard()}
        )
        return True

    return False
