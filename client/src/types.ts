export type SchemaCol = { key: string; type: "number" | "string" | "date" };

export type Plan =
  | {
      intent: string;
      steps: Array<
        | { action: "add_column"; name: string; expression: string; note?: string }
        | {
            action: "transform_column";
            column: string;
            transform: "trim" | "lower" | "upper" | "replace" | "parse_date";
            args?: Record<string, any>;
            note?: string;
          }
      >;
    };

export type Diff = {
  addedColumns: string[];
  modifiedColumns: string[];
};
