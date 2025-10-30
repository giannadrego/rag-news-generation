# generate_final_articles.py
import os, json, time, textwrap, requests
from datetime import datetime

# Run from repo root
ARTICLES_PATH = "output/articles.json"
OUTPUT_PATH = "output/final_article.json"

# Ollama settings
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma2:2b-instruct-q4_K_M")

def call_ollama(prompt: str, temperature: float = 0.2, max_retries: int = 3, timeout: int = 60) -> str:
    url = f"{OLLAMA_HOST}/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            resp = (data.get("response") or "").strip()
            if resp:
                return resp
        except Exception as e:
            last_err = e
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"Ollama request failed after {max_retries} attempts: {last_err}")

def build_prompt(item: dict) -> str:
    markdown_input = (item.get("article_content") or "").strip()
    return textwrap.dedent(f"""\
    You are a helpful writing assistant that converts legislative or policy markdown summaries into a clear, news-style article.

    Instructions:
    1. Read the markdown input carefully.
    2. Write a full natural-language news article based entirely on the information provided.
    3. Ensure all links are inline Markdown links with descriptive anchor text; convert any bare URLs into inline links. Use entity names as anchors, e.g., [Rep. Jodey C. Arrington](URL), [House Budget Committee](URL).
    4. Use Markdown for the final output:
       - Begin with a **bold headline**.
       - Use clear, factual paragraphs only (no bullet points, no lists).
       - Keep a neutral, professional tone similar to a news wire or government press report.
    5. Do NOT invent or assume information that isn’t in the input.
    6. If information is unclear or missing, omit it without speculation.

    Input Markdown:
    ---
    {markdown_input}
    ---

    Output Format:
    - Return your answer in Markdown only, following the above rules.
    """).strip()

def main():
    if not os.path.exists(ARTICLES_PATH):
        raise FileNotFoundError(f"Missing input file: {ARTICLES_PATH}")

    with open(ARTICLES_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list):
        raise ValueError("Expected a JSON array in output/articles.json")

    results = []
    total = len(items)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    for i, item in enumerate(items, start=1):
        bill_id = item.get("bill_id") or f"UNKNOWN_{i}"
        try:
            prompt = build_prompt(item)
            generated_md = call_ollama(prompt)

            # Output with the same core fields, and the generated article as article_content
            rec = {
                "bill_id": bill_id,
                "bill_title": item.get("bill_title", ""),
                "sponsor_bioguide_id": item.get("sponsor_bioguide_id", ""),
                "bill_committee_ids": item.get("bill_committee_ids", []),
                "article_content": generated_md
            }
            results.append(rec)

            # Write incrementally so you can see progress
            with open(OUTPUT_PATH, "w", encoding="utf-8") as out_f:
                json.dump(results, out_f, ensure_ascii=False, indent=2)
                out_f.flush()
                os.fsync(out_f.fileno())

            print(f"[{i}/{total}] OK {bill_id} ({len(generated_md)} chars)")
        except Exception as e:
            print(f"[{i}/{total}] ERROR {bill_id}: {e}")
            rec = {
                "bill_id": bill_id,
                "bill_title": item.get("bill_title", ""),
                "sponsor_bioguide_id": item.get("sponsor_bioguide_id", ""),
                "bill_committee_ids": item.get("bill_committee_ids", []),
                "article_content": ""
            }
            results.append(rec)
            with open(OUTPUT_PATH, "w", encoding="utf-8") as out_f:
                json.dump(results, out_f, ensure_ascii=False, indent=2)
                out_f.flush()
                os.fsync(out_f.fileno())

    print(f"Wrote {len(results)} items to {OUTPUT_PATH}")

if __name__ == "__main__":
    main()