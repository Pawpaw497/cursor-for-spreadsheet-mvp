export type SchemaCol = { key: string; type: "number" | "string" | "date" };

export type AggregationSpec = {
  column: string;
  op: "sum" | "avg" | "count" | "max" | "min";
  as: string;
};

export type LookupColumnMapping = {
  from: string;
  to: string;
};

export type PlanStep =
  | {
      action: "add_column";
      name: string;
      expression: string;
      table?: string;
      note?: string;
    }
  | {
      action: "transform_column";
      column: string;
      transform: "trim" | "lower" | "upper" | "replace" | "parse_date";
      args?: Record<string, any>;
      table?: string;
      note?: string;
    }
  | {
      action: "sort_table";
      table?: string;
      column: string;
      order?: "ascending" | "descending";
      note?: string;
    }
  | {
      action: "filter_rows";
      table?: string;
      condition: string;
      note?: string;
    }
  | {
      action: "delete_rows";
      table?: string;
      condition: string;
      note?: string;
    }
  | {
      action: "deduplicate_rows";
      table?: string;
      keys: string[];
      keep?: "first" | "last";
      note?: string;
    }
  | {
      action: "rename_column";
      table?: string;
      fromName: string;
      toName: string;
      note?: string;
    }
  | {
      action: "fill_missing";
      table?: string;
      column: string;
      strategy: "constant" | "mean" | "median" | "mode";
      value?: any;
      note?: string;
    }
  | {
      action: "cast_column_type";
      table?: string;
      column: string;
      targetType: "number" | "string" | "date";
      note?: string;
    }
  | {
      action: "join_tables";
      left: string;
      right: string;
      leftKey: string;
      rightKey: string;
      resultTable: string;
      joinType?: "inner" | "left" | "right";
      note?: string;
    }
  | {
      action: "create_table";
      name: string;
      source: string;
      expression?: string;
      note?: string;
    }
  | {
      action: "aggregate_table";
      source: string;
      groupBy: string[];
      aggregations: AggregationSpec[];
      resultTable: string;
      note?: string;
    }
  | {
      action: "union_tables";
      sources: string[];
      resultTable: string;
      mode?: "strict" | "relaxed";
      note?: string;
    }
  | {
      action: "lookup_column";
      mainTable: string;
      lookupTable: string;
      mainKey: string;
      lookupKey: string;
      columns: LookupColumnMapping[];
      note?: string;
    }
  | {
      action: "delete_column";
      column: string;
      table?: string;
      note?: string;
    }
  | {
      action: "reorder_columns";
      columns: string[];
      table?: string;
      note?: string;
    }
  | {
      action: "validate_table";
      table?: string;
      rules: string[];
      level?: "warn" | "error";
      note?: string;
    }
  | {
      action: "pivot_table";
      source: string;
      index: string[];
      columns: string;
      values: string;
      agg?: "sum" | "count" | "avg" | "max" | "min";
      resultTable: string;
      note?: string;
    }
  | {
      action: "unpivot_table";
      source: string;
      idVars: string[];
      valueVars: string[];
      varName?: string;
      valueName?: string;
      resultTable: string;
      note?: string;
    };

export type Plan = {
  intent: string;
  steps: PlanStep[];
};

export type Diff = {
  addedColumns: string[];
  modifiedColumns: string[];
  validationWarnings: string[];
  validationErrors: string[];
};

export type TableData = {
  name: string;
  rows: Record<string, any>[];
  schema: SchemaCol[];
};

/** 单元格格式，用于 AG Grid cellStyle */
export type CellFormat = {
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  fontFamily?: string;
  fontSize?: number;
  textAlign?: "left" | "center" | "right";
  backgroundColor?: string;
};
