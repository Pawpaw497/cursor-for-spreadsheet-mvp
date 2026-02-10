export type SchemaCol = { key: string; type: "number" | "string" | "date" };

export type PlanStep =
  | { action: "add_column"; name: string; expression: string; table?: string; note?: string }
  | {
      action: "transform_column";
      column: string;
      transform: "trim" | "lower" | "upper" | "replace" | "parse_date";
      args?: Record<string, any>;
      table?: string;
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
    };

export type Plan = {
  intent: string;
  steps: PlanStep[];
};

export type Diff = {
  addedColumns: string[];
  modifiedColumns: string[];
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
