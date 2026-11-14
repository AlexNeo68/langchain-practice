import json
import os

from dotenv import load_dotenv

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langchain_openrouter import ChatOpenRouter
from langchain.agents.structured_output import ToolStrategy
from langchain_tavily import TavilySearch

from langchain_my.pdf import save_report

from pydantic import BaseModel, Field


load_dotenv()

class Source(BaseModel):
    """Schema for a source used by the agent"""
    url: str = Field(description="Адрес источника")

class AgentResponse(BaseModel):
    """Schema for agent response with answer and sources"""
    answer: str = Field(description="Ответ агента на запрос")
    sources: list[Source] = Field(
        default_factory=list, description="Список источников, использованных для генерации ответа"
    )


@tool
def search(query: str) -> str:
    """Search the web for a query and return results with their URLs."""
    from langchain_tavily import TavilySearch

    tavily_search = TavilySearch(max_results=5)
    raw = tavily_search.invoke({"query": query})
    results = raw.get("results", [])

    if not results:
        return "Nothing found."

    answer = "\n\n".join(
        f"{i}. {r.get('title', 'Untitled')}\n"
        f"URL: {r.get('url', '')}\n"
        f"{r.get('content', '')[:500]}"
        for i, r in enumerate(results, 1)
    )
    sources = [{"url": r["url"]} for r in results if r.get("url")]
    return json.dumps({"answer": answer, "sources": sources}, ensure_ascii=False)

# llm = ChatOpenAI(
#     temperature=.2,
#     model=os.getenv("UNSLOTH_MODEL"),
#     base_url=os.getenv("UNSLOTH_URL"),
#     api_key=os.getenv("UNSLOTH_API_KEY"),
# )

MODEL_NAME = "z-ai/glm-5.3-flash"

llm = ChatOpenRouter(
    model=MODEL_NAME,
    temperature=0.2,
)

# tools = [search]
tools = [TavilySearch()]
agent = create_agent( model=llm, tools=tools, response_format=ToolStrategy(AgentResponse))

def main() -> None:
    question = (
        "Найди 3 вакансии для AI инженера с использованием langchain на linkedin, "
        "или других доступных из России платформах и перечисли их детали"
    )
    result = agent.invoke({"messages": HumanMessage(content=question)})

    response = result.get("structured_response")
    if response is None:
        print("Агент завершился без structured_response. Сообщения:")
        for message in result.get("messages", []):
            print(f"- {type(message).__name__}: {str(message.content)[:300]}")
        return

    pdf_path = save_report(
        question=question,
        response=response,
        model_name=MODEL_NAME,
        out_dir="output",
        title="Вакансии AI-инженера (LangChain)",
    )
    print(f"Ответ: {response.answer}")
    print(f"\nPDF сохранён: {pdf_path}")

if __name__ == "__main__":
    main()