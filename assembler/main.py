# file: assembler/main.py
import os, sys, json, logging
from confluent_kafka import Consumer
from collections import defaultdict

os.makedirs("/logs", exist_ok=True)
os.makedirs("/output", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/logs/assembler.log", encoding="utf-8")
    ],
)

broker = os.getenv("KAFKA_BROKER", "kafka-broker:9092")

c = Consumer({
    'bootstrap.servers': broker,
    'group.id': 'assembler',
    'enable.auto.commit': False,
    'auto.offset.reset': 'earliest'
})
c.subscribe(['summaries.q'])

# Constants
PLACEHOLDER = "No summary available."
REQUIRED_QIDS = {1,2,3,4,5,6,7}

# State per bill
state = {}
completed_bill_ids = set()

def _have_all(summaries: dict) -> bool:
    return set(summaries.keys()) >= REQUIRED_QIDS

def _init_bill(bill_id: str):
    return {
        "bill_id": bill_id,
        "bill_title": "",
        "sponsor_bioguide_id": "",
        "bill_committee_ids": [],
        "summaries": {},   # question_id -> summary str
        "links": set(),    # union of congress.gov links
        "metadata": {}     # merged metadata
    }

def _safe_list_unique(seq):
    out, seen = [], set()
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def _build_article_content(bill_id: str, bill_title: str, sponsor_bioguide_id: str, committee_ids: list, summaries: dict, links: list) -> str:
    def get(qid: int) -> str:
        return (summaries.get(qid, "") or "").strip()

    lines = [f"## {bill_id}\n"]

    # Main summary (what it does + status)
    s1 = get(1)
    if s1:
        lines.append(s1)

    # Structured sections from Q2..Q7 if available
    sections = [
        ("Committees", 2),
        ("Sponsor", 3),
        ("Cosponsors & overlap", 4),
        ("Hearings", 5),
        ("Amendments", 6),
        ("Votes", 7),
    ]
    for title, q in sections:
        sq = get(q)
        if sq:
            lines.append(f"\n\n**{title}**\n\n{sq}")

    return "\n".join(lines).strip()

def _write_articles_file(articles_path: str, article_obj: dict):
    existing = []
    if os.path.exists(articles_path):
        try:
            with open(articles_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    # Upsert by bill_id (replace if exists)
    existing = [r for r in existing if r.get("bill_id") != article_obj.get("bill_id")] + [article_obj]

    tmp = articles_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    try:
        os.replace(tmp, articles_path)
        return
    except OSError:
        try:
            with open(articles_path, "w", encoding="utf-8") as out_f:
                json.dump(existing, out_f, ensure_ascii=False, indent=2)
            return
        except OSError:
            try:
                if os.path.exists(articles_path):
                    os.unlink(articles_path)
                with open(articles_path, "w", encoding="utf-8") as out_f:
                    json.dump(existing, out_f, ensure_ascii=False, indent=2)
                return
            except Exception:
                pass
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass

def try_emit_article(bill_id: str):
    if bill_id in completed_bill_ids:
        return
    b = state.get(bill_id)
    if not b:
        return
    if not _have_all(b["summaries"]):
        return

    article_markdown = _build_article_content(
        bill_id=b["bill_id"],
        bill_title=b["bill_title"],
        sponsor_bioguide_id=b["sponsor_bioguide_id"],
        committee_ids=b["bill_committee_ids"],
        summaries=b["summaries"],
        links=sorted(b["links"])
    )

    article_obj = {
        "bill_id": b["bill_id"],
        "bill_title": b["bill_title"],
        "sponsor_bioguide_id": b["sponsor_bioguide_id"],
        "bill_committee_ids": b["bill_committee_ids"],
        "article_content": article_markdown
    }

    out_path = "/output/articles.json"
    _write_articles_file(out_path, article_obj)
    logging.info(f"✅ Wrote article for {bill_id} to {out_path}")
    completed_bill_ids.add(bill_id)

def handle_message(data: dict):
    bill_id = data.get("bill_id")
    qid = data.get("question_id")
    summary = data.get("summary", "") or ""
    links = data.get("links", []) or []
    metadata = data.get("metadata", {}) or {}

    if not bill_id or not isinstance(qid, int):
        return

    if bill_id in completed_bill_ids:
        return

    if bill_id not in state:
        state[bill_id] = _init_bill(bill_id)

    b = state[bill_id]

    # Merge metadata from fetcher (carried through summarizer)
    # q1: bill_title, bill_url
    # q2: bill_committee_ids
    # q3: sponsor_bioguide_id
    if "bill_title" in metadata and metadata["bill_title"]:
        b["bill_title"] = metadata["bill_title"]
    if "sponsor_bioguide_id" in metadata and metadata["sponsor_bioguide_id"]:
        b["sponsor_bioguide_id"] = metadata["sponsor_bioguide_id"]
    if "bill_committee_ids" in metadata and isinstance(metadata["bill_committee_ids"], list):
        b["bill_committee_ids"] = _safe_list_unique(b["bill_committee_ids"] + [x for x in metadata["bill_committee_ids"] if isinstance(x, str)])

    # Store summary only if not placeholder
    if summary and summary != PLACEHOLDER and qid not in b["summaries"]:
        b["summaries"][qid] = summary

    # Merge congress.gov links
    for u in links:
        if isinstance(u, str) and "www.congress.gov" in u:
            b["links"].add(u)

    logging.info(f"Collected Q{qid} for {bill_id}: {len(b['summaries'])}/7")
    try_emit_article(bill_id)

logging.info("Assembler subscribed to summaries.q")

try:
    while True:
        msg = c.poll(1.0)
        if not msg:
            continue
        if msg.error():
            continue

        try:
            data = json.loads(msg.value())
        except Exception as e:
            logging.error(f"Bad JSON on summaries.q: {e}")
            c.commit(msg)
            continue

        try:
            handle_message(data)
            c.commit(msg)
        except Exception as e:
            logging.exception(f"Error assembling article: {e}")
            c.commit(msg)

except KeyboardInterrupt:
    logging.info("Assembler shutting down")
except Exception as e:
    logging.exception("Assembler crashed")
finally:
    c.close()