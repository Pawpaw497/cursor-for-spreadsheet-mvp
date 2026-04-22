from pydantic import BaseModel


class UserIntent(BaseModel):
    intent_type: str              # aggregation / filter / sort / join / visualization
    target_columns: list[str]     # 涉及哪些列
    conditions: list[Condition]   # 过滤条件
    aggregations: list[Aggregation]
    groupby: list[str]
    order_by: list[str]
    limit: int | None
    time_range: str | None
