## RAG News Generation

####Author
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

### Benchmark logs (7 minutes 23 seconds)
#### controller.log(controller.log)
2025-10-30 16:48:03,341 INFO Queued 70 question tasks
#### (fetcher.log)
2025-10-30 16:48:03,421 INFO Fetcher subscribed to tasks.questions
2025-10-30 16:48:42,872 INFO Processing: HR.1 Question 1
#### (summarizer.log)
2025-10-30 16:48:03,953 INFO Summarizer subscribed to facts.raw
2025-10-30 16:48:44,354 INFO Summarizing: HR.1 Q1
#### (assembler.log)
2025-10-30 16:55:26,235 INFO Collected Q7 for HR.1968: 7/7
2025-10-30 16:55:26,236 INFO ✅ Wrote article for HR.1968 to /output/articles.json


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
    "bill_title": "Lower Energy Costs Act",
    "sponsor_bioguide_id": "S001176",
    "bill_committee_ids": [
      "HSII00",
      "HSIF00",
      "HSPW00",
      "HSBU00",
      "HSAG00"
    ],
    "article_content": "## HR.1\n\nThe Lower Energy Costs Act (H.R. 1) provides for the exploration, development, importation, and exportation of energy resources such as oil, gas, and minerals. The bill is currently in the process of being corrected by the Clerk before it can be formally considered by the House. [https://www.congress.gov/bill/118th-congress/house-bill/1](https://www.congress.gov/bill/118th-congress/house-bill/1)\n\n\n**Committees**\n\nThis bill is in the Natural Resources Committee, Energy and Commerce Committee, Transportation and Infrastructure Committee, Budget Committee, and Agriculture Committee. [https://www.congress.gov/committee/house-natural-resources-committee/hsii00](https://www.congress.gov/committee/house-natural-resources-committee/hsii00) [https://www.congress.gov/committee/house-energy-and-commerce-committee/hsif00](https://www.congress.gov/committee/house-energy-and-commerce-committee/hsif00) [https://www.congress.gov/committee/house-transportation-and-infrastructure-committee/hspw00](https://www.congress.gov/committee/house-transportation-and-infrastructure-committee/hspw00) [https://www.congress.gov/committee/house-budget-committee/hsbu00](https://www.congress.gov/committee/house-budget-committee/hsbu00) [https://www.congress.gov/committee/house-agriculture-committee/hsag00](https://www.congress.gov/committee/house-agriculture-committee/hsag00)\n\n\n**Sponsor**\n\nRep. Scalise, Steve is the sponsor of the bill. [https://www.congress.gov/member/rep-scalise-steve/S001176](https://www.congress.gov/member/rep-scalise-steve/S001176)\n\n\n**Cosponsors & overlap**\n\nRep. McMorris Rodgers, Cathy [R-WA-5], Rep. Westerman, Bruce [R-AR-4], and Rep. Graves, Sam [R-MO-6] are the top 5 cosponsors of the bill.  Rep. McMorris Rodgers, Cathy [R-WA-5], is a member of the House Natural Resources Committee. [https://www.congress.gov/member/rep-mcmorris-rodgers-cathy/M001159](https://www.congress.gov/member/rep-mcmorris-rodgers-cathy/M001159)  [https://www.congress.gov/committee/house-natural-resources-committee/hsii00](https://www.congress.gov/committee/house-natural-resources-committee/hsii00)\n\n\n**Hearings**\n\nNo hearings have been held on the bill as of yet.  [Find more information about the bill](https://www.congress.gov/).\n\n\n**Amendments**\n\nThe provided text states there were 20 total amendments proposed on the bill, but it does not provide information about who proposed them or what they do. [Link to relevant page](https://www.congress.gov/)\n\n\n**Votes**\n\nThe bill was passed by the Yeas and Nays vote of 225 to 204 on March 30, 2023. This was a bipartisan vote.  [Vote: On passage Passed by the Yeas and Nays: 225 - 204 (Roll no. 182).](https://www.congress.gov/bill/118th-congress/house-bill/1737/votes)"
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
