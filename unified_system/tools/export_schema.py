from __future__ import annotations

from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from unified_system.db import Base
import unified_system.models  # noqa: F401


def main() -> None:
    dialect = postgresql.dialect()
    statements = ["-- Generated from unified_system.models; review migrations before production use.\n"]
    for table in Base.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in sorted(table.indexes, key=lambda idx: idx.name or ""):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    output = Path(__file__).resolve().parents[1] / "postgresql-schema.sql"
    normalized = "\n\n".join("\n".join(line.rstrip() for line in statement.splitlines()) for statement in statements)
    output.write_text(normalized + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
