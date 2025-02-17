# atschecker
Resume Ranking API Documentation
Overview
The Resume Ranking API is a FastAPI‑based application that extracts ranking criteria from a job description and scores resumes based on those criteria using OpenAI’s GPT model. The solution uses an in‑memory queue (via Python’s asyncio.Queue) to hold resume scoring jobs. A background worker processes each job by calling the OpenAI API and stores the results in a global dictionary. Once all jobs are processed, the API returns the results as a CSV file.

Note: This solution is intended for testing or single‑instance deployments. In production, a distributed queue system is recommended.

Features
Criteria Extraction:
Upload a job description (in PDF or DOCX format) and receive ranking criteria (e.g. required certifications, experience, skills) as JSON.
Endpoint: POST /extract-criteria

Resume Scoring:
Upload multiple resume files along with criteria. The API enqueues a job for each resume in an in‑memory queue. A background worker processes the jobs by calling OpenAI and stores the results. Once complete, a CSV file with each candidate’s scores is returned.
Endpoint: POST /score-resumes

Background Processing:
A background worker is launched at startup to continuously process resume scoring jobs from the in‑memory queue.

Swagger UI:
Interactive API documentation is automatically generated and available at /docs.

Architecture & Flow
Extract Criteria Flow:

The client sends a POST /extract-criteria request with a job description file.
The API extracts text from the file (supports PDF and DOCX).
A REST call is made to the OpenAI API to extract ranking criteria.
The response (a JSON object with a "criteria" key) is returned to the client.
Score Resumes Flow:

The client sends a POST /score-resumes request with:
A JSON array string of criteria.
Multiple resume files (PDF or DOCX).
For each resume, the API:
Extracts the text.
Generates a unique job ID.
Enqueues a job (containing job ID, candidate name, resume text, and criteria) into an in‑memory queue.
A background worker (started on startup) continuously processes jobs from the queue:
Calls the OpenAI API with the resume details and criteria.
Saves the result (including scores and total score) in a global dictionary keyed by job ID.
The endpoint polls until results for all jobs are available.
Finally, a CSV file is generated with columns for candidate name, individual criterion scores, and total score, and returned to the client.
API Endpoints
1. POST /extract-criteria
Description:
Extract ranking criteria from a job description file.

Request:

Content-Type: multipart/form-data
Parameter:
file (file, required): A PDF or DOCX file containing the job description.
Response Example:

json
Copy
{
  "criteria": [
    "Must have certification XYZ",
    "5+ years of experience in Python development",
    "Strong background in Machine Learning"
  ]
}



2. POST /score-resumes
Description:
Enqueue resume scoring jobs for multiple resumes and return a CSV file with scores.

Request:

Content-Type: multipart/form-data
Parameters:
criteria (string, required): A JSON array string of ranking criteria.
Example: ["Must have certification XYZ", "5+ years of experience in Python development", "Strong background in Machine Learning"]
files (file[], required): One or more resume files (PDF or DOCX).
Response:
A CSV file containing the candidate name, scores for each criterion, and the total score.
