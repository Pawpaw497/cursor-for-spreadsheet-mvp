from pydantic import BaseModel


class TableContext(BaseModel):
    topic: str
    description: str
    granularity: str
    columns: list[ColumnSchema]
    stats: dict
    sample_rows: list[list]
