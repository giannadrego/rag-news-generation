## RAG News Generation

#### Author
Gianna Drego

#### Project Description
RAG News Generation is a modular, containerized data pipeline that automates the collection, summarization, and assembly of legislative news from Congress.gov using a retrieval-augmented generation (RAG) workflow. It leverages Kafka for inter-service communication and Ollama for local LLM-powered summarization, resulting in clear, up-to-date articles on legislative action.

### Prerequisites
- Docker and Docker Compose
- A `CONGRESS_API_KEY` (for `api.congress.gov`)

Create a `.env` file at the repo root:
- 'CONGRESS_API_KEY=your_congress_api_key_here'


### Architecture Overview
- **controller**: seeds tasks (questions about a bill) to Kafka topic `tasks.questions`.
- **fetcher**: reads `tasks.questions`, calls Congress.gov API, builds facts and `www.congress.gov` links; writes to `facts.raw`.
- **summarizer**: reads `facts.raw`, prompts the LLM via Ollama, produces concise answers; writes to `summaries.q`.
- **assembler**: reads `summaries.q`, aggregates per-bill answers into an article; writes to `output/articles.json`.
- **ollama**: local LLM runtime used by `summarizer`.

Data flow: `controller` (tasks.questions) → `fetcher` (facts.raw) → `summarizer` (summaries.q) → `assembler` (articles.json)

Kafka topics:
- `tasks.questions`
- `facts.raw`
- `summaries.q`

### Optimization for Speed and Accuracy

**Approach:**  
To maximize both speed and accuracy, the pipeline is structured as four asynchronous, decoupled microservices communicating via Kafka topics. This design allows each stage—question seeding, fact retrieval, summarization, and article assembly—to run concurrently and independently, minimizing bottlenecks and improving throughput. For speed, data flows as soon as it is available without waiting for entire batches, leveraging Kafka's high-throughput message brokering. Batch processing is avoided in favor of streaming, reducing latency. Accuracy is prioritized by sourcing all facts directly from official Congress.gov endpoints and building contextually rich prompts for the local LLM (Ollama), which ensures concise, factual outputs. Failures or bottlenecks in one stage do not block the rest of the pipeline, further improving robustness and reliability.

### Benchmark logs (6 minutes 40 seconds)
#### controller.log(controller.log)
2025-11-03 03:09:59,512 INFO Queued 70 question tasks
#### (assembler.log)
2025-11-03 03:16:39,364 INFO Collected Q7 for HR.1968: 7/7
2025-11-03 03:16:39,366 INFO ✅ Wrote article for HR.1968 to /output/articles.json


### Setup and Run

###Activate Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

1) Start core infra and verify Ollama
```bash
docker compose up -d kafka-broker ollama --wait
docker compose exec ollama ollama list
curl -sS http://localhost:11434/api/generate \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma2:2b-instruct-q4_K_M","prompt":"hi","stream":false}'
```
note: If you don't see any models then explicit pull
```bash
docker compose exec ollama ollama pull gemma2:2b-instruct-q4_K_M
```
suggestion: delete 'articles.json' under 'output' folder before the next step
2) Build and start the pipeline
```bash
docker compose up -d --build fetcher summarizer controller assembler --wait
```

3) Useful commands
```bash
docker compose exec kafka-broker rpk topic list
docker compose logs -f controller
docker compose logs -f fetcher
docker compose logs -f summarizer
docker compose logs -f assembler
docker compose restart controller
```

### How It Works (per service)

- `fetcher/main.py`
  - Subscribes to `tasks.questions`
  - Calls Congress.gov endpoints
  - Emits facts, safe links, and metadata to `facts.raw`

- `summarizer/main.py`
  - Subscribes to `facts.raw`
  - Builds a compact prompt using facts and `www.congress.gov` links
  - Calls Ollama (`gemma2:2b-instruct-q4_K_M`)
  - Emits per-question summaries to `summaries.q`

- `assembler/main.py`
  - Subscribes to `summaries.q`
  - Waits until all required question IDs are present for a bill
  - Writes a consolidated article to `output/articles.json`

### Example Output
`output/articles.json`
```json
[
  {
    "bill_id": "HR.1",
    "bill_title": "One Big Beautiful Bill Act",
    "sponsor_bioguide_id": "A000375",
    "bill_committee_ids": [
      "HSBU00"
    ],
    "article_content": "## HR.1\n\nThe One Big Beautiful Bill Act, passed as Public Law No. 119-21 on July 4, 2025, reduces taxes and alters spending for various federal programs. It also increases the statutory debt limit. [https://www.congress.gov/bill/119th-congress/house-bill/1](https://www.congress.gov/bill/119th-congress/house-bill/1)\n\n\n**Committees**\n\nThis bill is in the House Budget Committee. [https://www.congress.gov/committee/house-budget-committee/hsbu00](https://www.congress.gov/committee/house-budget-committee/hsbu00)\n\n\n**Sponsor**\n\nRep. Arrington, Jodey C. is the sponsor of the bill.  [https://www.congress.gov/member/rep-arrington-jodey-c/A000375](https://www.congress.gov/member/rep-arrington-jodey-c/A000375)\n\n\n**Cosponsors & overlap**\n\nThere are currently no cosponsors listed for this bill.  The provided text does not contain information about whether any of the cosponsors are members of the committee that the bill is in.\n\n\n**Hearings**\n\nNo hearings have been held on the bill.  [https://www.congress.gov/](https://www.congress.gov/)\n\n\n**Amendments**\n\nThere have been 493 amendments proposed to the bill.  Senators Padilla, Alex [D-CA] proposed amendments SAMDT 2851 and SAMDT 2850. Senator Klobuchar, Amy [D-MN] proposed amendment SAMDT 2849 to strike a provision relating to delayed implementation of the supplemental nutrition assistance program matching funds requirements. Senator Graham, Lindsey [R-SC] proposed amendment SAMDT 2848 to improve the bill.  Senator Warner, Mark R. [D-VA] proposed amendment SAMDT 2847 to use revenues from lease payments from Metropolitan Washington Airports for aviation safety improvements and other purposes.\n\n\n**Votes**\n\nThe bill passed the House with a party-line vote.  [House Roll Call 190](https://www.congress.gov/bill/118th-congress/house-bill/3) shows that the vote was 218 to 2, with Republicans voting in favor and Democrats voting against."
  }
]
```


`output/final_articles.json`
```json
[
  {
    "bill_id": "HR.1",
    "bill_title": "One Big Beautiful Bill Act",
    "sponsor_bioguide_id": "A000375",
    "bill_committee_ids": [
      "HSBU00"
    ],
    "article_content": "**House Passes 'One Big Beautiful Bill Act' Reducing Taxes and Altering Spending**\n\nThe House Budget Committee has approved the \"One Big Beautiful Bill Act\", Public Law No. 119-21, which reduces taxes and alters spending for various federal programs. The bill also increases the statutory debt limit. [https://www.congress.gov/bill/119th-congress/house-bill/1](https://www.congress.gov/bill/119th-congress/house-bill/1)\n\nRep. Jodey C. Arrington (R-VA), the sponsor of the bill, [https://www.congress.gov/member/rep-arrington-jodey-c/A000375](https://www.congress.gov/member/rep-arrington-jodey-c/A000375) introduced the legislation.  The bill passed the House with a party-line vote of 218 to 2, with Republicans voting in favor and Democrats voting against. [House Roll Call 190](https://www.congress.gov/bill/118th-congress/house-bill/3)\n\nThe bill has been referred to the House Budget Committee.  [https://www.congress.gov/committee/house-budget-committee/hsbu00](https://www.congress.gov/committee/house-budget-committee/hsbu00) The \"One Big Beautiful Bill Act\" was passed on July 4, 2025 and has been subject to 493 amendments.  Senators Padilla, Alex [D-CA] proposed amendments SAMDT 2851 and SAMDT 2850. Senator Klobuchar, Amy [D-MN] proposed amendment SAMDT 2849 to strike a provision relating to delayed implementation of the supplemental nutrition assistance program matching funds requirements. Senator Graham, Lindsey [R-SC] proposed amendment SAMDT 2848 to improve the bill.  Senator Warner, Mark R. [D-VA] proposed amendment SAMDT 2847 to use revenues from lease payments from Metropolitan Washington Airports for aviation safety improvements and other purposes."
  }
]
```


### Troubleshooting
- Ensure `.env` contains a valid `CONGRESS_API_KEY`.
- Check Ollama health: `docker compose exec ollama ollama list`.
- If `summarizer` shows connection errors, confirm `OLLAMA_HOST` is reachable (`http://ollama:11434` inside Compose).
- Reset topics (optional):
```bash
docker compose exec kafka-broker rpk topic delete tasks.questions facts.raw summaries.q
docker compose exec kafka-broker rpk topic create tasks.questions facts.raw summaries.q
```

### Clean Up
```bash
docker compose down -v
```
