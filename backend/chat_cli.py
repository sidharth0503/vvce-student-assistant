"""Interactive terminal client for the VVCE Student Assistant API."""
from __future__ import annotations

import os

import requests

API_BASE = os.getenv("VVCE_API_URL", "http://localhost:8000").rstrip("/")


def print_help() -> None:
    print("Commands: /help  /sources  /health  /exit")
    print("Ask a VVCE question by typing it and pressing Enter.")


def request_health() -> dict | None:
    try:
        response = requests.get(f"{API_BASE}/api/health", timeout=5)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"Backend unavailable at {API_BASE}: {exc}")
        return None


def main() -> None:
    print("VVCE Student Assistant — terminal chat")
    print(f"API: {API_BASE}")
    print_help()
    last_sources: list[dict] = []
    history: list[dict[str, str]] = []

    while True:
        try:
            question = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return
        if not question:
            continue
        command = question.lower()
        if command == "/exit":
            print("Goodbye.")
            return
        if command == "/help":
            print_help()
            continue
        if command == "/sources":
            if not last_sources:
                print("No sources yet. Ask a question first.")
            else:
                print("Sources from the latest answer:")
                for source in last_sources:
                    title = source.get("title") or source.get("url", "Source")
                    source_type = "Local document" if source.get("source_type") == "manual" else "VVCE page"
                    print(f"- {title} ({source_type})")
                    print(f"  {source.get('url', '')}")
                    if source.get("date"):
                        print(f"  Date: {source['date']}")
            continue
        if command == "/health":
            health = request_health()
            if health is not None:
                state = "ready" if health.get("knowledge_base_ready", health.get("ready", False)) else "not ready"
                print(f"Backend: online | Knowledge base: {state}")
                print(f"Indexed chunks: {health.get('indexed_chunks', health.get('documents', 0))}")
                print(f"Manual chunks: {health.get('manual_chunks', 0)} | Crawled chunks: {health.get('crawled_chunks', 0)}")
                print(f"Gemini configured: {'yes' if health.get('gemini_configured') else 'no'}")
            continue

        try:
            response = requests.post(f"{API_BASE}/api/chat", json={
                "message": question, "history": history[-8:]
            }, timeout=60)
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not response.ok:
                detail = payload.get("detail") or payload.get("answer") or response.reason
                print(f"Assistant error ({response.status_code}): {detail}")
                continue
            answer = payload.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                print("Assistant returned an empty answer. Please try again.")
                continue
            print(f"\nAssistant>\n{answer}")
            history.extend(({"role": "user", "content": question},
                            {"role": "assistant", "content": answer}))
            history = history[-8:]
            sources = payload.get("sources", [])
            last_sources = sources if isinstance(sources, list) else []
            if last_sources:
                print("\nSources:")
                for source in last_sources:
                    title = source.get("title") or source.get("url", "Source")
                    print(f"- {title}: {source.get('url', '')}")
        except requests.Timeout:
            print("The request timed out. Check whether the backend is running, then try again.")
        except requests.ConnectionError:
            print(f"Cannot connect to {API_BASE}. Start FastAPI in another terminal and retry.")
        except requests.RequestException as exc:
            print(f"Request failed: {exc}")


if __name__ == "__main__":
    main()
