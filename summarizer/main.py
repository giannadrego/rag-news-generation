import os, sys, json, textwrap
import requests
from confluent_kafka import Consumer, Producer
import logging
import time

os.makedirs("/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/logs/summarizer.log", encoding="utf-8")
    ],
)

broker = os.getenv("KAFKA_BROKER", "kafka-broker:9092")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma2:2b-instruct-q4_K_M")

PLACEHOLDER = "No summary available."

c = Consumer({
    'bootstrap.servers': broker,
    'group.id': 'summarizer',
    'enable.auto.commit': False,
    'auto.offset.reset': 'earliest'
})
p = Producer({'bootstrap.servers': broker})
c.subscribe(['facts.raw'])

def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit: return s
    return s[:limit-1] + "…"

def build_prompt(question: str, facts: list, links: list) -> str:
    # Keep prompt small for 2B model
    facts_lines = []
    for f in facts[:10]:  # limit number of facts
        txt = _truncate(f.get("text", ""), 220)
        if txt:
            facts_lines.append(f"- {txt}")
    facts_block = "\n".join(facts_lines) if facts_lines else "- (no facts)"

    links_lines = []
    for link in links[:6]:
        if "www.congress.gov" in link:
            links_lines.append(f"- {link}")
    links_block = "\n".join(links_lines) if links_lines else "- (no congress.gov links)"

    return textwrap.dedent(f"""\
        
    You are a careful summarizer. Answer the question ONLY using the provided facts.
    - Be concise (2-5 sentences).
    - If a link is relevant, include it.
    - Only use congress.gov links; do not invent links.
    - Do not add opinions.

    Question:
    {question}

    Facts:
    {facts_block}

    Links:
    {links_block}

    Provide a direct answer for this single question.
    """)

def call_ollama(prompt: str, max_retries: int = 3, backoff_sec: float = 2.0):
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}},
                timeout=60
            )
            r.raise_for_status()
            data = r.json()
            resp = (data.get("response", "") or "").strip()
            return resp if resp else PLACEHOLDER
        except Exception as e:
            logging.error(f"Ollama request failed (attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                time.sleep(backoff_sec * (2 ** (attempt - 1)))
    return None

logging.info("Summarizer subscribed to facts.raw")

try:
    while True:
        msg = c.poll(1.0)
        if not msg:
            continue
        if msg.error():
            continue

        data = json.loads(msg.value())
        bill_id = data["bill_id"]
        question_id = data["question_id"]
        question_text = data["question_text"]
        facts = data.get("facts", [])
        links = data.get("links", [])
        metadata = data.get("metadata", {})
        trace_id = data.get("trace_id", "")

        logging.info(f"Summarizing: {bill_id} Q{question_id}")
        try:
            prompt = build_prompt(question_text, facts, links)
            summary = call_ollama(prompt)

            if not summary or summary == PLACEHOLDER:
                logging.warning(f"Will retry later for {bill_id} Q{question_id} due to missing/placeholder summary; not committing offset")
                time.sleep(5)
                continue

            logging.info(f"LLM summary ({len(summary)} chars): {summary[:140]}...")

            out = {
                "bill_id": bill_id,
                "question_id": question_id,
                "question_text": question_text,
                "summary": summary,
                "links": links,
                "metadata": metadata,
                "trace_id": trace_id
            }

            p.produce('summaries.q', key=bill_id.encode(), value=json.dumps(out))
            p.flush()
            c.commit(msg)
            logging.info(f"✅ Produced to summaries.q: {bill_id} Q{question_id}")
        except Exception as e:
            logging.error(f"❌ Error summarizing {bill_id} Q{question_id}: {str(e)}")
            logging.exception(e)
            c.commit(msg)
except KeyboardInterrupt:
    logging.info("Summarizer shutting down")
except Exception as e:
    logging.exception("Summarizer crashed")
finally:
    c.close()