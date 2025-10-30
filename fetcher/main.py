import os, sys, json, time, re
import requests
from confluent_kafka import Consumer, Producer
import logging

os.makedirs("/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/logs/fetcher.log", encoding="utf-8")
    ],
)

# Configuration
API_KEY = os.getenv("CONGRESS_API_KEY", "")
BASE = "https://api.congress.gov/v3"
broker = os.getenv("KAFKA_BROKER", "kafka-broker:9092")

# Initialize Kafka
c = Consumer({
    'bootstrap.servers': broker,
    'group.id': 'fetcher',
    'enable.auto.commit': False,
    'auto.offset.reset': 'earliest'
})
p = Producer({'bootstrap.servers': broker})
c.subscribe(['tasks.questions'])

# Helper function to make API requests
def _get(url: str) -> dict:
    """Make API request and return JSON, return empty dict on error"""
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        if not r.text.strip():
            logging.warning(f"Empty response from {url}")
            return {}
        return r.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed for {url}: {e}")
        return {}
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error for {url}: {e}")
        return {}

# API endpoint helpers
def bill_root(congress, bill_type, number):
    return _get(f"{BASE}/bill/{congress}/{bill_type}/{number}?api_key={API_KEY}")

def bill_committees(congress, bill_type, number):
    return _get(f"{BASE}/bill/{congress}/{bill_type}/{number}/committees?api_key={API_KEY}")

def bill_cosponsors(congress, bill_type, number):
    return _get(f"{BASE}/bill/{congress}/{bill_type}/{number}/cosponsors?api_key={API_KEY}")

def bill_amendments(congress, bill_type, number):
    return _get(f"{BASE}/bill/{congress}/{bill_type}/{number}/amendments?api_key={API_KEY}")

def bill_actions(congress, bill_type, number):
    return _get(f"{BASE}/bill/{congress}/{bill_type}/{number}/actions?api_key={API_KEY}")

def bill_summaries(congress, bill_type, number):
    return _get(f"{BASE}/bill/{congress}/{bill_type}/{number}/summaries?api_key={API_KEY}")

# Helper to extract nested lists
def _list_at(node, *keys):
    """Walk dict keys and return list if present"""
    cur = node
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return []
    if isinstance(cur, list):
        return cur
    return []

# Clean HTML from text
def clean_html(text):
    """Remove HTML tags and clean up text"""
    if not text:
        return ""
    clean_text = re.sub(r'<[^>]+>', '', text)
    clean_text = re.sub(r'\s+', ' ', clean_text)
    return clean_text.strip()

# Build committee URL from committee data
def build_committee_url(committee: dict) -> str:
    """Build www.congress.gov URL from committee data"""
    code = committee.get("systemCode", "").lower()
    name = committee.get("name", "")
    if code and name:
        url_name = name.lower().replace(" ", "-").replace(",", "").replace("&", "and")
        # Determine chamber
        name_lower = name.lower()
        if "house" in name_lower or "H." in name:
            chamber = "house"
        elif "senate" in name_lower or "S." in name:
            chamber = "senate"
        else:
            chamber = "house"
        return f"https://www.congress.gov/committee/{chamber}-{url_name}/{code}"
    return ""

# Build member URL from member data
def build_member_url(member: dict) -> str:
    """Build www.congress.gov URL from member data"""
    bioguide_id = member.get("bioguideId", "")
    full_name = member.get("fullName", "")
    if bioguide_id and full_name:
        name_part = full_name.split('[')[0].strip()
        url_name = name_part.lower().replace(" ", "-").replace(",", "").replace(".", "").replace("'", "")
        return f"https://www.congress.gov/member/{url_name}/{bioguide_id}"
    return ""

# Build facts and links per question
def build_facts_for_question(qid: int, congress: int, bill_type: str, number: int):
    """Build facts and links for each question, filtering to only www.congress.gov links"""
    logging.info(f"Building facts for question {qid}, bill {congress}/{bill_type}/{number}")
    facts = []
    links = []
    metadata = {}
    
    if qid == 1:  # What does this bill do? Where is it in the process?
        root = bill_root(congress, bill_type, number)
        sums = bill_summaries(congress, bill_type, number)
        bill = root.get("bill", {})
        
        # Extract summary
        summaries = sums.get("summaries", [])
        bill_description = ""
        if summaries:
            summary_text = summaries[0].get("text", "")
            bill_description = clean_html(summary_text)
        
        # Get status
        latest_action = bill.get("latestAction", {})
        status = latest_action.get("text", "No status")
        status_date = latest_action.get("actionDate", "")
        
        facts.append({
            "text": f"Bill Title: {bill.get('title', 'No title')}",
            "link": ""
        })
        
        if bill_description:
            facts.append({
                "text": f"Summary: {bill_description[:500]}",  # Truncate for small LLM
                "link": ""
            })
        
        facts.append({
            "text": f"Current Status: {status} (Date: {status_date})",
            "link": ""
        })
        
        # Only www.congress.gov bill URL
        legislation_url = bill.get("legislationUrl")
        if legislation_url and "www.congress.gov" in legislation_url:
            links.append(legislation_url)
            facts.append({
                "text": f"View full bill details",
                "link": legislation_url
            })
        
        metadata = {
            "bill_title": bill.get("title", ""),
            "bill_url": legislation_url or ""
        }
        
    elif qid == 2:  # What committees is this bill in?
        data = bill_committees(congress, bill_type, number)
        committees = data.get("committees", [])
        
        for committee in committees[:10]:  # Limit for small LLM
            name = committee.get("name", "")
            code = committee.get("systemCode", "")
            
            if name:
                facts.append({
                    "text": f"Committee: {name}",
                    "link": ""
                })
            
            # Build www.congress.gov committee URL
            url = build_committee_url(committee)
            if url:
                links.append(url)
                facts.append({
                    "text": f"View {name} committee page",
                    "link": url
                })
        
        metadata = {
            "bill_committee_ids": [
                (c.get("systemCode", "") or "").upper()
                for c in committees if c.get("systemCode")
            ]
        }
        
    elif qid == 3:  # Who is the sponsor?
        root = bill_root(congress, bill_type, number)
        bill = root.get("bill", {})
        sponsors = bill.get("sponsors", [])
        
        for sponsor in sponsors:
            name = sponsor.get("fullName", "")
            party = sponsor.get("party", "")
            state = sponsor.get("state", "")
            district = sponsor.get("district", "")
            
            sponsor_text = f"Sponsor: {name}"
            if party and state:
                sponsor_text += f" ({party}-{state}"
                if district:
                    sponsor_text += f"-{district}"
                sponsor_text += ")"
            
            facts.append({
                "text": sponsor_text,
                "link": ""
            })
            
            # Build www.congress.gov member URL
            url = build_member_url(sponsor)
            if url:
                links.append(url)
                facts.append({
                    "text": f"View sponsor profile: {name}",
                    "link": url
                })
        
        bioguide_id = sponsors[0].get("bioguideId", "") if sponsors else ""
        metadata = {"sponsor_bioguide_id": bioguide_id}
        
    elif qid == 4:  # Who cosponsored this bill? Are any cosponsors on committees?
        root = bill_root(congress, bill_type, number)
        cos = bill_cosponsors(congress, bill_type, number)
        com = bill_committees(congress, bill_type, number)
        
        # Limit to top 5 cosponsors for small LLM
        cos_list = _list_at(cos, "cosponsors", "cosponsors") or _list_at(cos, "cosponsors")
        limited_cosponsors = cos_list[:5]
        
        facts.append({
            "text": f"Total cosponsors: {len(cos_list)}",
            "link": ""
        })
        
        facts.append({
            "text": "Top 5 cosponsors:",
            "link": ""
        })
        
        for cosponsor in limited_cosponsors:
            name = cosponsor.get("fullName", "Unknown")
            party = cosponsor.get("party", "")
            
            facts.append({
                "text": f"- {name} ({party})",
                "link": ""
            })
            
            # Build www.congress.gov member URL
            url = build_member_url(cosponsor)
            if url:
                links.append(url)
                facts.append({
                    "text": f"View cosponsor profile: {name}",
                    "link": url
                })
        
        # Add committee information
        committees = com.get("committees", [])
        if committees:
            facts.append({
                "text": f"Committees: {', '.join([c.get('name', '') for c in committees[:5]])}",
                "link": ""
            })
            
            for committee in committees[:5]:
                url = build_committee_url(committee)
                if url:
                    links.append(url)
        
        metadata = {
            "cosponsor_count": len(cos_list),
            "committee_codes": [c.get("systemCode", "") for c in committees if c.get("systemCode")]
        }
        
    elif qid == 5:  # Have any hearings happened?
        facts.append({
            "text": "Hearings data not available via Congress.gov API",
            "link": ""
        })
        metadata = {}
        
    elif qid == 6:  # Have any amendments been proposed?
        amd = bill_amendments(congress, bill_type, number)
        amendments = amd.get("amendments", [])
        
        if amendments:
            facts.append({
                "text": f"Total amendments: {len(amendments)}",
                "link": ""
            })
            
            for amendment in amendments[:5]:  # Limit for small LLM
                title = amendment.get("title", "Unknown amendment")
                facts.append({
                    "text": f"Amendment: {title[:200]}",
                    "link": ""
                })
        else:
            facts.append({
                "text": "No amendments proposed",
                "link": ""
            })
        
        metadata = {"amendment_count": len(amendments)}
        
    elif qid == 7:  # Have any votes happened?
        acts = bill_actions(congress, bill_type, number)
        actions = acts.get("actions", [])
        
        # Look for vote-related actions
        vote_actions = []
        for action in actions:
            action_text = action.get("text", "").lower()
            if any(kw in action_text for kw in ["vote", "passed", "failed", "yea", "nay", "roll call"]):
                vote_actions.append({
                    "text": action.get("text", ""),
                    "date": action.get("actionDate", "")
                })
        
        if vote_actions:
            facts.append({
                "text": f"Found {len(vote_actions)} vote-related actions",
                "link": ""
            })
            for vote in vote_actions[:3]:  # Limit for small LLM
                facts.append({
                    "text": f"Vote: {vote['text']} (Date: {vote['date']})",
                    "link": ""
                })
        else:
            facts.append({
                "text": "No vote data found in actions",
                "link": ""
            })
        
        metadata = {"vote_action_count": len(vote_actions)}
        
    else:
        logging.warning(f"Unknown question ID: {qid}")
        facts.append({
            "text": "Unknown question",
            "link": ""
        })
    
    # Ensure all links are www.congress.gov only (filter out api.congress.gov)
    links = [link for link in links if "www.congress.gov" in link]
    
    logging.info(f"Built {len(facts)} facts, {len(links)} links")
    return facts, links, metadata

# Main loop
logging.info("Fetcher subscribed to tasks.questions")

try:
    while True:
        msg = c.poll(1.0)
        if not msg:
            continue
        if msg.error():
            continue
        
        task = json.loads(msg.value())
        congress = task["congress"]
        bill_type = task["bill_type"]
        number = task["number"]
        qid = task["question_id"]
        question_text = task["question_text"]
        bill_id = task["bill_id"]
        
        logging.info(f"Processing: {bill_id} Question {qid}")
        
        try:
            # Build facts and links
            facts, links, metadata = build_facts_for_question(qid, congress, bill_type, number)
            
            # Build output
            out = {
                "bill_id": bill_id,
                "congress": congress,
                "bill_type": bill_type,
                "number": number,
                "question_id": qid,
                "question_text": question_text,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "facts": facts,
                "links": links,
                "metadata": metadata,
                "trace_id": task.get("trace_id", "")
            }
            
            # Produce to facts.raw topic
            p.produce('facts.raw', key=bill_id.encode(), value=json.dumps(out))
            p.flush()
            c.commit(msg)
            logging.info(f"✅ Produced: {bill_id} Q{qid}")
            logging.info(f"Question: {question_text}")
            logging.info(f"Facts: {json.dumps(facts)}")
            logging.info(f"Links: {json.dumps(links)}")
            logging.info(f"Metadata: {json.dumps(metadata)}")
            
        except Exception as e:
            logging.error(f"❌ Error processing {bill_id} Q{qid}: {str(e)}")
            logging.exception(e)
            c.commit(msg)
            
except KeyboardInterrupt:
    logging.info("Fetcher shutting down")
except Exception as e:
    logging.exception("Fetcher crashed")
finally:
    c.close()