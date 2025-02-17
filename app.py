import io
import csv
import json
import os
import time
import uuid
import asyncio
import logging
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import PyPDF2
import docx
import uvicorn
import httpx

# For testing only: set environment variables directly.

# Read environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Please set the OPENAI_API_KEY environment variable.")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

app = FastAPI(
    title="Resume Ranking API",
    description="Extract ranking criteria and score resumes using an in‑memory queue. See /docs for interactive documentation.",
    version="1.0"
)

# ------------------ Global In-Memory Queue & Results ------------------
resume_scoring_queue = asyncio.Queue()
resume_results = {}  # Maps job_id to result

# ------------------ Helper Functions: File Text Extraction ------------------
def extract_text_pdf(file_bytes: bytes) -> str:
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        logger.info("Extracted text from PDF successfully.")
        return text
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        raise HTTPException(status_code=400, detail=f"Error processing PDF: {e}")

def extract_text_docx(file_bytes: bytes) -> str:
    try:
        document = docx.Document(io.BytesIO(file_bytes))
        text = "\n".join(para.text for para in document.paragraphs)
        logger.info("Extracted text from DOCX successfully.")
        return text
    except Exception as e:
        logger.error(f"DOCX extraction error: {e}")
        raise HTTPException(status_code=400, detail=f"Error processing DOCX: {e}")

def extract_text(file: UploadFile) -> str:
    contents = file.file.read()
    if file.filename.lower().endswith(".pdf"):
        return extract_text_pdf(contents)
    elif file.filename.lower().endswith((".docx", ".doc")):
        return extract_text_docx(contents)
    else:
        logger.error("Unsupported file type.")
        raise HTTPException(status_code=400, detail="Unsupported file type")

# ------------------ OpenAI REST Call for Criteria Extraction ------------------
async def get_ranking_criteria(text: str) -> list:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o",  # Adjust as needed.
        "messages": [
            {"role": "system", "content": "You are an HR expert. Extract ranking criteria from a job description."},
            {"role": "user", "content": (
                "Extract key ranking criteria from the following job description. "
                "Include required skills, certifications, experience, and qualifications. "
                "Return your answer as a JSON object with a key 'criteria' that is a JSON array of strings.\n\n"
                "Job Description:\n" + text
            )}
        ],
        "temperature": 0.0,
        "max_tokens": 1000,
        "response_format": {"type": "json_object"}
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)
        logger.info(f"OpenAI API response status: {response.status_code}")
    except Exception as e:
        logger.error(f"HTTP request error: {e}")
        raise HTTPException(status_code=500, detail=f"Error calling OpenAI API: {e}")
    if response.status_code != 200:
        logger.error(f"OpenAI API error response: {response.text}")
        raise HTTPException(status_code=500, detail=f"Error calling OpenAI API: {response.text}")
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"].strip()
        logger.info(f"Raw OpenAI response content: {content}")
        criteria_data = json.loads(content)
        if "criteria" in criteria_data:
            return criteria_data["criteria"]
        else:
            logger.error("Missing 'criteria' key in OpenAI response.")
            raise ValueError("Missing 'criteria' key in response")
    except Exception as e:
        logger.error(f"Error parsing OpenAI response: {e}")
        raise HTTPException(status_code=500, detail=f"Error parsing response: {e}")

# ------------------ OpenAI REST Call for Resume Scoring ------------------
async def score_resume_via_openai(candidate_name: str, resume_text: str, criteria: list) -> dict:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    prompt = (
        "Score the following resume for the given criteria. For each criterion, assign a score from 0 to 5. "
        "Also, calculate the total score as the sum of individual scores.\n\n"
        f"Candidate Name: {candidate_name}\n\n"
        f"Resume Text:\n{resume_text}\n\n"
        f"Criteria: {json.dumps(criteria)}\n\n"
        "Return your answer as a JSON object with a key 'scores' that maps each criterion to its score and includes a 'Total Score'."
    )
    payload = {
        "model": "gpt-4o",  # Adjust as needed.
        "messages": [
            {"role": "system", "content": "You are an HR expert who scores resumes based on given criteria."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"}
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)
        logger.info(f"OpenAI API (scoring) response status: {response.status_code}")
    except Exception as e:
        logger.error(f"HTTP request error (scoring): {e}")
        raise Exception(f"Error calling OpenAI API: {e}")
    if response.status_code != 200:
        logger.error(f"OpenAI API error (scoring): {response.text}")
        raise Exception(f"Error calling OpenAI API: {response.text}")
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"].strip()
        logger.info(f"Raw OpenAI scoring response content: {content}")
        scores_data = json.loads(content)
        if "scores" in scores_data:
            return scores_data
        else:
            raise ValueError("Missing 'scores' key in response")
    except Exception as e:
        logger.error(f"Error parsing OpenAI scoring response: {e}")
        raise Exception(f"Error parsing response: {e}")

# ------------------ Helper for Candidate Name ------------------
def extract_candidate_name(file: UploadFile) -> str:
    print(file.filename,"file",file.filename.rsplit(".", 1)[0])
    return file.filename.rsplit(".", 1)[0]

# ------------------ Background Worker ------------------
async def resume_worker():
    logger.info("Background worker started for resume scoring.")
    while True:
        job = await resume_scoring_queue.get()
        try:
            logger.info(f"Processing job: {job}")
            result = await score_resume_via_openai(
                job["candidate_name"],
                job["resume_text"],
                job["criteria"]
            )
            # Store result using job_id as key.
            print(result,"resulttt",job["job_id"])
            resume_results[job["job_id"]] = result
            resume_results[job["job_id"]]["candidate_name"] = job["candidate_name"]
            logger.info(f"Job {job['job_id']} processed. Result: {result}")
        except Exception as e:
            logger.error(f"Error processing job {job.get('job_id')}: {e}")
        resume_scoring_queue.task_done()

# ------------------ Global In-Memory Queue & Results ------------------
resume_scoring_queue = asyncio.Queue()
resume_results = {}  # Maps job_id to result

# ------------------ Startup Event ------------------
@app.on_event("startup")
async def startup_event():
    # Start the background worker
    asyncio.create_task(resume_worker())

# ------------------ API Endpoints ------------------

@app.post("/extract-criteria", response_class=JSONResponse, summary="Extract Ranking Criteria")
async def extract_criteria_endpoint(file: UploadFile = File(...)):
    logger.info("Received /extract-criteria request.")
    text = extract_text(file)
    criteria = await get_ranking_criteria(text)
    logger.info(f"Extracted criteria: {criteria}")
    return {"criteria": criteria}

@app.post("/score-resumes", summary="Enqueue Resume Scoring Jobs and Return CSV Results")
async def score_resumes_endpoint(
    criteria: str = Form(...),
    files: list[UploadFile] = File(...)
):
    logger.info("Received /score-resumes request.")
    try:
        criteria_list = json.loads(criteria)
        if not isinstance(criteria_list, list):
            raise ValueError("Criteria must be a list of strings.")
    except Exception as e:
        logger.error(f"Criteria parsing error: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid criteria format: {e}")
    
    job_ids = []
    for file in files:
        resume_text = extract_text(file)
        candidate_name = extract_candidate_name(file)
        job_id = str(uuid.uuid4())
        job_data = {
            "job_id": job_id,
            "job_type": "score_resume",
            "candidate_name": candidate_name,
            "resume_text": resume_text,
            "criteria": criteria_list
        }
        print(job_data,"job_data")
        await resume_scoring_queue.put(job_data)
        job_ids.append(job_id)
        logger.info(f"Job enqueued with id: {job_id}")
    
    # Wait until all jobs are processed by the background worker.
    timeout = 90  # seconds
    start_time = time.time()
    while any(job_id not in resume_results for job_id in job_ids):
        await asyncio.sleep(0.5)
        if time.time() - start_time > timeout:
            logger.error("Timeout waiting for resume scoring results.")
            raise HTTPException(status_code=504, detail="Timeout waiting for resume scoring results")
    
    # Generate CSV from the results.
    output = io.StringIO()
    writer = csv.writer(output)
    header = ["Candidate Name"] + criteria_list + ["Total Score"]
    writer.writerow(header)
    for job_id in job_ids:
        res = resume_results.get(job_id, {})
        print(res,"result")
        candidate = res.get("candidate_name", "Unknown")
        scores = res.get("scores", {})
        row = [candidate] + [scores.get(crit, 0) for crit in criteria_list] + [res.get("Total Score", 0)]
        writer.writerow(row)
    output.seek(0)
    logger.info("CSV file generated, sending response.")
    headers_resp = {"Content-Disposition": "attachment; filename=resumes_scores.csv"}
    return StreamingResponse(output, media_type="text/csv", headers=headers_resp)

if __name__ == "__main__":
    import uuid  # Ensure uuid is imported
    uvicorn.run(app, host="0.0.0.0", port=8000)
