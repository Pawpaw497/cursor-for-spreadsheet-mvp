# 蓝色鲸鱼AI软件工程师大作业：Reimagining the Spreadsheet - The "Cursor" Moment

## **背景**

在代码领域，Cursor 通过深度集成 LLM，重新定义了 IDE 的交互范式（Context-aware, Diff-based edits, Agentic workflow）。然而，在数据处理领域，尽管有 Copilot for Excel , Gemini for sheet等产品，但Spreadsheet 的交互模式依然停留在 20 年前。

**我们认为，Spreadsheet 领域也需要一个 "Cursor"。**

## **你的任务**

你需要试着完成以下三个阶段的工作。我们不看重你在表格 UI 上花了多少时间，也不需要最终全部完成，我们看重你对 **"AI Native Interaction"** 的思考。

## **Phase 1: Market & Product Research (简述)**

- 调研 Quadratic AI, ChatExcel, Rows.com , Copilot for Excel , Gemini for sheet, WPS或类似产品。
- 分析：为什么它们还没有达到 Cursor 的高度？缺了什么？是交互太生硬？还是对 Data Context 理解不够？尝试使用一遍他们的工具，描述你的发现和体验
  - 它对 spreadsheet「做了什么增强」？（功能、交互、范式）
  - 哪些地方「真的有帮助」？（具体到场景）
  - 哪些地方「看上去很聪明，实际很鸡肋」？
  - 它最本质的限制是什么？（不是「模型不够聪明」这种废话）
- *Output:* 一份简短的 Insights 文档（Bullet points 即可）。

## **Phase 2: Define "Cursor for Spreadsheet"**

- 设计你心目中的产品形态。
  - 举 1–2 个典型 persona（例如：运营同学、数据分析师、财务人员……）
  - 他们今天在表格里最痛的 2–3 件事是什么？
- 核心功能应该包括什么？
  - 不要列 20 个 feature，只挑 **3–5 个你认为最关键的能力**，并说明：
    - 用户在做什么？（具体任务）
    - 以前怎么做？哪里痛？
    - 用你的产品怎么做？交互流程是什么？
    - 背后大概需要什么技术支撑？（不需要非常细，但要具体到「需要语义理解整张表」「需要 agent 多步操作表结构」这种层级）
  - **非目标（Non-goals）**

        明确这次你 **不打算解决什么**，以及为什么（例如：不做实时多用户协作、不做 BI 看板……）

## **Phase 3: Build the MVP (重点)**

请实现一个**最小可行性产品**。

- **不要造轮子**：请直接使用成熟的前端表格组件（如 React-Data-Grid, AG Grid 等）。
- 你不需要真的做出一个「新一代 Spreadsheet」，
- 但希望你能做出一个 **真实可跑的 Demo**，体现出你在 Part 2 里某一两点「关键能力」的雏形。
- **（可选项）核心挑战**：实现一个 **Agentic Feature**。
  - *场景举例*：输入 "Cmd+K" (像 Cursor 一样)，Agent 自动理解表格上下文，生成代码或公式，并直接 Apply 到表格中（Show Diff is a plus）。

- 技术栈不限（TypeScript / Python / Web 前端 / 简单 CLI 都可以）
- 只要我们能按你的 README 跑起来，看到「从输入到输出」的完整流程即可
- 可以调用任意大模型 API / 本地模型（openrouter或其他），重点是你怎么 orchestrate，而不是模型本身，涉及部署测试等，我们可以报销50元人民币的LLM 调用credit。

## **评价标准 (Evaluation Criteria)**

1. **Product Sense**: 你是否抓住了 "Cursor-like" 的精髓？（即：不仅仅是对话，而是对现有内容的直接操作与重构）。
2. **技术能力**：代码是否清晰、有结构，agent / tool 调度是否合理，README 是否好懂。
3. **取舍与聚焦**：在有限时间里，你是否能聚焦在一两件代表性的事上，并做到相对完整。
4. **利用 AI 的方式**：你是把 AI 当「更高级的 autocomplete」，还是能设计出「人机协作的工作流」。
5. **表达与文档**：你的文档是否简洁、有观点，有清晰的逻辑，而不是堆砌概念。

