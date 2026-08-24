"""Local serializers for Studio representations."""

from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path
from typing import Any


class StudioSerializationClient:
    """Write already-selected representation values without RPC or HTTP access."""

    @staticmethod
    async def write_text(output_path: str, content: str) -> str:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(output.write_text, content, encoding="utf-8")
        return str(output)

    @staticmethod
    async def write_json_string(output_path: str, content: str) -> str:
        value = json.loads(content)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        def _write() -> None:
            with output.open("w", encoding="utf-8") as stream:
                json.dump(value, stream, indent=2, ensure_ascii=False)

        await asyncio.to_thread(_write)
        return str(output)

    @staticmethod
    async def write_csv(
        output_path: str,
        headers: list[Any],
        rows: list[list[Any]],
    ) -> str:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        def _write() -> None:
            with output.open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(headers)
                writer.writerows(rows)

        await asyncio.to_thread(_write)
        return str(output)


__all__ = ["StudioSerializationClient"]
