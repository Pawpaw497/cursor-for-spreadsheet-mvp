import json
import os
import re
from typing import Any, Dict, List, Literal, Optional, Union

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

app = FastAPI(title="Cursor for Spreadsheet (Python Server)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # MVP: loosen. Production: restrict.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------- Schemas (Plan) ---------
TransformKind = Literal["trim", "lower", "upper", "replace", "parse_date"]

class AddColumnStep(BaseModel):
    action: Literal["add_column"]
    name: str
    expression: str
    note: Optional[str] = None

class TransformColumnStep(BaseModel):
    action: Literal["transform_column"]
    column: str
    transform: TransformKind
    args: Optional[Dict[str, Any]] = None
    note: Optional[str] = None

Step = Union[AddColumnStep, TransformColumnStep]

class Plan(BaseModel):
    intent: str
    steps: List[Step] = Field(min_length=1)

class PlanRequest(BaseModel):
    prompt: str
    schema: List[Dict[str, Any]]
    sampleRows: List[Dict[str, Any]]
    modelSource: Optional[Literal["cloud", "local"]] = "cloud"

class PlanResponse(BaseModel):
    plan: Plan

# --------- Prompt builder ---------
SYSTEM_PROMPT = """You are an agent that edits a spreadsheet by generating an execution plan.

Output rules (VERY IMPORTANT):
- Output ONLY valid JSON.
- Do NOT include explanations, markdown, or code fences.
- Do NOT include any text outside the JSON.
- The JSON must strictly follow the schema below.
- If ambiguous, choose the simplest reasonable interpretation.

Schema:
{
  "intent": string,
  "steps": [
    { "action": "add_column", "name": string, "expression": string, "note"?: string }
    |
    { "action": "transform_column", "column": string, "transform": "trim"|"lower"|"upper"|"replace"|"parse_date", "args"?: object, "note"?: string }
  ]
}

Rules:
- add_column.expression is a JavaScript expression evaluated as (row) => expression
- Use row.<columnName> to access values
- transform_column.replace args: {"from": string, "to": string}
- transform_column.parse_date args: {"formatHint"?: string}
"""

def build_user_prompt(user_prompt: str, schema: Any, sample_rows: Any) -> str:
    return (
        "Spreadsheet schema:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Sample rows:\n"
        f"{json.dumps(sample_rows, ensure_ascii=False, indent=2)}\n\n"
        "User request:\n"
        f"{user_prompt}\n"
    )

def extract_json(text: str) -> str:
    # Remove ```json fences if present, then trim
    cleaned = re.sub(r"```json|```", "", text).strip()
    return cleaned

# --------- Providers ---------
async def call_ollama(model: str, system: str, user: str) -> str:
    base = os.getenv("OLLAMA_BASE", "http://localhost:11434").rstrip("/")
    url = f"{base}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload, timeout=120)
        if r.status_code >= 400:
            detail = r.text or "<empty body>"
            raise HTTPException(status_code=502, detail=f"Ollama error {r.status_code}: {detail}")
        data = r.json()
        print(f"data:{data}")
        content = data.get("message", {}).get("content", "")
        print("LLM content head:", (content or "")[:120])
        return data.get("message", {}).get("content", "")

async def call_openrouter(api_key: str, model: str, system: str, user: str) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"OpenRouter error: {r.text}")
        data = r.json()
        return data["choices"][0]["message"]["content"]

# --------- API ---------
@app.get("/health")
def health():
    return {"ok": True}

@app.post("/api/plan", response_model=PlanResponse)
async def plan(req: PlanRequest):
    user_prompt = build_user_prompt(req.prompt, req.schema, req.sampleRows)

    model_source = (req.modelSource or "cloud").lower()

    if model_source == "local":
        model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        content = await call_ollama(model=model, system=SYSTEM_PROMPT, user=user_prompt)
    elif model_source == "cloud":
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY missing")
        model = os.getenv("OPENROUTER_MODEL", "auto")
        content = await call_openrouter(api_key=api_key, model=model, system=SYSTEM_PROMPT, user=user_prompt)
    else:
        raise HTTPException(status_code=400, detail="Unknown modelSource")

    json_text = extract_json(content)

    # Parse + validate
    try:
        parsed = json.loads(json_text)
    except Exception:
        # MVP: one retry by asking to output JSON only
        retry_user = user_prompt + "\nReturn ONLY JSON."
        if model_source == "local":
            model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
            content = await call_ollama(model=model, system=SYSTEM_PROMPT, user=retry_user)
        else:
            api_key = os.getenv("OPENROUTER_API_KEY", "")
            model = os.getenv("OPENROUTER_MODEL", "auto")
            content = await call_openrouter(api_key=api_key, model=model, system=SYSTEM_PROMPT, user=retry_user)
        json_text = extract_json(content)
        try:
            parsed = json.loads(json_text)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Model did not return valid JSON: {e}. Raw: {content}")

    try:
        plan_obj = Plan.model_validate(parsed)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plan validation failed: {e}. Raw: {parsed}")

    return PlanResponse(plan=plan_obj)