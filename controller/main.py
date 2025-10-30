from confluent_kafka import Producer
import json, uuid, logging, os, sys

os.makedirs("/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/logs/controller.log", encoding="utf-8")
    ],
)

p = Producer({"bootstrap.servers": "kafka-broker:9092"})

QUESTIONS = {
  1: "What does this bill do? Where is it in the process?",
  2: "What committees is this bill in?",
  3: "Who is the sponsor?",
  4: "Who cosponsored this bill? Are any of the cosponsors on the committee that the bill is in?",
  5: "Have any hearings happened on the bill? If so, what were the findings?",
  6: "Have any amendments been proposed on the bill? If so, who proposed them and what do they do?",
  7: "Have any votes happened on the bill? If so, was it a party-line vote or a bipartisan one?"
}

BILLS = [
  {"congress":118,"bill_type":"hr","number":1},
  {"congress":118,"bill_type":"hr","number":5371},
  {"congress":118,"bill_type":"hr","number":5401},
  {"congress":118,"bill_type":"s","number":2296},
  {"congress":118,"bill_type":"s","number":24},
  {"congress":118,"bill_type":"s","number":2882},
  {"congress":118,"bill_type":"s","number":499},
  {"congress":118,"bill_type":"sres","number":412},
  {"congress":118,"bill_type":"hres","number":353},
  {"congress":118,"bill_type":"hr","number":1968},
]

def on_delivery(err, msg):
    if err:
        logging.error(f"Delivery failed: {err}")
    else:
        logging.debug(f"Delivered to {msg.topic()} [{msg.partition()}] @ {msg.offset()}")

total = 0
for b in BILLS:
    bill_id = f"{b['bill_type']}.{b['number']}".upper()
    for qid, question_text in QUESTIONS.items():
        payload = {
          "bill_id": bill_id,
          "congress": b["congress"],
          "bill_type": b["bill_type"],
          "number": b["number"],
          "question_id": qid,
          "question_text": question_text,
          "trace_id": str(uuid.uuid4())
        }
        p.produce(
            "tasks.questions", 
            key=bill_id.encode("utf-8"), 
            value=json.dumps(payload).encode("utf-8"),
            headers=[("trace_id", payload["trace_id"].encode()),
                     ("bill_id", bill_id.encode()),
                     ("question_id", str(qid).encode())],
            callback=on_delivery
        )
        total += 1
        p.poll(0)
p.flush()
logging.info(f"Queued {total} question tasks")
