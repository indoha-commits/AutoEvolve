from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from core.models import cloud_model
from tools.research import search_web, fetch_page


PROMPT = (
    Path(__file__).parent.parent
    / "prompts"
    / "researcher.md"
).read_text()


class Source(BaseModel):
    title: str
    url: str
    supports: str


class ResearchReport(BaseModel):
    title: str
    executive_summary: str

    problem: str

    target_users: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)

    verified_findings: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)

    technical_requirements: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)

    recommended_mvp: list[str] = Field(default_factory=list)

    sources: list[Source] = Field(default_factory=list)

    decision: str
    decision_reason: str


research_agent = Agent(
    cloud_model("reasoning"),
    instructions=PROMPT,
    output_type=ResearchReport,
)


@research_agent.tool_plain
async def web_search(query: str) -> list[dict]:
    """
    Search the public web.

    Use multiple focused queries instead of one very broad query.

    Search-result snippets are discovery aids only.
    They must not be treated as verified evidence.

    If a search fails, continue with another query where possible.
    """
    return await search_web(query)


@research_agent.tool_plain
async def read_web_page(url: str) -> dict:
    """
    Read a source discovered during web research.

    The returned dictionary contains:

    - ok: whether the page was successfully read
    - url: final resolved URL
    - status_code: HTTP status code when available
    - title: extracted page title
    - content_type: response content type
    - text: extracted readable text
    - error: failure reason when ok=false

    IMPORTANT:

    A failed source is normal and MUST NOT stop the research task.

    If ok=true:
    - inspect the returned text
    - use the source as evidence only for claims actually supported by it
    - include the final returned URL in the report's sources

    If ok=false:
    - do not cite the source as verified evidence
    - do not retry the exact same URL repeatedly
    - search for another authoritative source covering the same claim
    - continue the research
    - if no reliable alternative can be found, record the issue under unknowns

    Search snippets alone are not evidence.

    A source must be successfully read before it can support a verified finding.

    PDF, blocked, stale, missing, timed-out, and unsupported sources may return
    ok=false. Treat these as recoverable research failures rather than agent errors.
    """
    result = await fetch_page(url)

    return {
        "ok": result.get("ok", False),
        "url": result.get("url", url),
        "status_code": result.get("status_code"),
        "title": result.get("title", ""),
        "content_type": result.get("content_type", ""),
        "text": result.get("text", ""),
        "error": result.get("error"),
    }


async def run_research(question: str) -> ResearchReport:
    prompt = f"""
Research this question:

{question}

You MUST use web_search before producing the final report.

For important claims:

1. Search for relevant sources.
2. Prefer primary and authoritative sources.
3. Open relevant sources with read_web_page.
4. Only treat claims as verified when the underlying source was successfully read.
5. Distinguish verified findings from inference.
6. Include successfully inspected source URLs in sources.
7. Explain what each source supports.

Prefer:

- government sources
- regulators
- official institutions
- official company documentation
- international organizations
- standards bodies
- academic and research institutions
- established industry publications
- reputable news organizations

Do not invent statistics.

Do not use search snippets as verified evidence.

If a source fails:
- continue the research
- search for an alternative
- do not crash or abandon the task

If a number or factual claim cannot be verified:
- do not guess
- place the uncertainty under unknowns

Actively search for evidence that could weaken or invalidate the proposed idea.

The final decision must be exactly one of:

Proceed
Proceed with conditions
Do not proceed
Need more research
"""

    result = await research_agent.run(prompt)

    return result.output
