# Cursor for Spreadsheet — MVP

This is a runnable demo that implements a **Cursor-like Cmd+K workflow** for a spreadsheet-like grid:

- Context-aware prompt (sends schema + sample rows)
- LLM returns a **structured plan**
- UI shows a **Diff preview**
- One click **Apply** mutates the grid

## What’s included (MVP scope)

### Implemented
1) Cmd+K "AI Edit" modal  
2) Two actions:
   - `add_column`: add a derived column computed from a JS expression over `row`
   - `transform_column`: clean/transform an existing column (trim/lower/upper/replace/parse_date)

3) Diff preview (added columns + modified columns)
4) Undo last Apply (snapshot-based)

### Not implemented (by design)
- Collaborative editing
- Complex formula engine / full Excel compatibility
- Multi-sheet lineage graph
- External data connectors

---

## Requirements
- Node.js 18+

## Setup

### 1) Get an OpenRouter API key
Create a key on OpenRouter and export it.

### 2) Run the backend
```bash
cd server
cp .env.example .env
# edit OPENROUTER_API_KEY in .env
npm install
npm run dev
```

Backend runs on http://localhost:8787

### 3) Run the frontend
In another terminal:

```bash
cd client
npm install
npm run dev
```

Open the URL shown by Vite (usually http://localhost:5173).

---

## Try prompts

- `Add a column total_price = price * quantity`
- `Transform column email to lowercase`
- `Trim whitespace in column name`
- `Replace "-" with "" in column phone`
- `Parse column signup_date as date`

---

## How it works (high level)

1) Frontend captures:
   - column schema
   - a few sample rows
   - which range is selected (optional)
2) Backend asks the LLM to output a strict JSON plan:
   - `steps[]` of actions
3) Frontend validates + renders Diff
4) Apply runs a tiny transform engine in the browser

---

## Notes on safety / correctness (demo)
- For `add_column`, the demo evaluates an expression with `new Function("row", ...)`.
  This is **not** production-safe. In production you’d use a sandboxed expression language.
