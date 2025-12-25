import os
import json
import pathlib
from typing import List, Dict, Any, Optional

import httpx
from dotenv import load_dotenv

# Ensure env vars are loaded even if this module is imported before interview_api.py loads .env
env_paths = [
    pathlib.Path(__file__).parent / ".env",  # scripts/backend/.env
    pathlib.Path(__file__).parent.parent.parent / ".env",  # repo root .env
    pathlib.Path.cwd() / ".env",  # current working directory
]
for env_path in env_paths:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
        break
else:
    load_dotenv()

AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("AZURE_OPENAI_BASE")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "o4-mini")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

# Debug: Show Azure OpenAI configuration status
print(f"🤖 Azure OpenAI Configuration:")
print(f"   KEY: {'✅ Set' if AZURE_OPENAI_KEY else '❌ Missing'}")
print(f"   ENDPOINT: {'✅ Set' if AZURE_OPENAI_ENDPOINT else '❌ Missing'} (checked: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_BASE)")
print(f"   DEPLOYMENT: {AZURE_OPENAI_DEPLOYMENT}")
print(f"   API_VERSION: {AZURE_OPENAI_API_VERSION}")
if AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT:
    print(f"   ✅ Azure OpenAI is CONFIGURED and ready to use")
else:
    print(f"   ⚠️ Azure OpenAI will use FALLBACK (static questions)")


def _is_configured() -> bool:
    ok = bool(AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT)
    if not ok:
        print("[ai] Azure OpenAI config missing: ",
              f"KEY={'set' if AZURE_OPENAI_KEY else 'missing'}, ",
              f"ENDPOINT={'set' if AZURE_OPENAI_ENDPOINT else 'missing'}")
    return ok


async def _call_azure_openai(messages: List[Dict[str, str]], max_tokens: int = 300) -> Optional[str]:
    if not _is_configured():
        return None

    url = (
        f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{AZURE_OPENAI_DEPLOYMENT}/chat/completions?api-version={AZURE_OPENAI_API_VERSION}"
    )
    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_OPENAI_KEY,
    }
    payload = {
        "messages": messages,
        "max_completion_tokens": max_tokens,  # o-series models use this instead of max_tokens
        #"temperature": 0.6, # o-series models usually don't support temperature (fixed at 1)
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            try:
                err = resp.text
            except Exception:
                err = str(resp.status_code)
            print(f"[ai] Azure OpenAI error: {err}")
            return None
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content")


def _flatten_skills(resume_analysis: Dict[str, Any]) -> List[str]:
    # ... (unchanged helper functions) ...
    tech = resume_analysis.get("technical_skills") or resume_analysis.get("technicalSkills") or {}
    if isinstance(tech, dict):
        all_values = []
        for v in tech.values():
            if isinstance(v, list):
                all_values.extend([str(x) for x in v])
        return all_values
    return []

def _projects(resume_analysis: Dict[str, Any]) -> List[str]:
    projects = resume_analysis.get("projects") or []
    return projects if isinstance(projects, list) else []

async def generate_ai_question(
    resume_analysis: Dict[str, Any],
    question_type: str,
    question_number: int,
    previous_questions: List[str],
    hr_questions: Optional[List[str]] = None,
) -> Optional[Dict[str, str]]:
    """
    Returns {"question": str, "source": str} or None if AI not available.
    """
    hr_questions = hr_questions or []
    skills = _flatten_skills(resume_analysis)
    projects = _projects(resume_analysis)
    experience = resume_analysis.get("experience_level") or resume_analysis.get("experienceLevel") or ""

    # Prioritize uploaded HR questions if available
    if (question_type == "hr" or question_type == "general") and hr_questions:
        unused = [q for q in hr_questions if all(q.lower() not in pq.lower() for pq in previous_questions)]
        if unused:
            return {"question": unused[0], "source": "hr_uploaded"}

    if question_type == "resume":
        system_prompt = (
            "You are a Senior Technical Interviewer at a top-tier tech company (e.g., Google, Amazon). "
            "Generate one deep technical question based on their resume. "
            "Avoid basic definitions. Focus on architectural decisions, trade-offs, and challenges. "
            "Ask 'Why' and 'How' regarding their specific projects and skills. "
            "Example: 'Why did you choose MongoDB over PostgreSQL for that project?' or 'How did you handle state management at scale?'"
        )
        user_prompt = (
            f"Skills: {', '.join(skills[:15])}\n"
            f"Projects: {', '.join(projects[:5])}\n"
            f"Experience: {experience}\n"
            f"Previous questions: {' | '.join(previous_questions[-3:])}\n"
            f"Generate specific question #{question_number} challenging their depth of knowledge."
        )
    elif question_type == "general":
        system_prompt = (
            "You are a Principal Software Engineer assessing a candidate for a Senior role. "
            "Generate one scenario-based technical question testing System Design, Scalability, or Debugging skills. "
            "Do NOT ask basic syntax or 'What is' questions. "
            "Topics: Distributed Systems, Database Consistency, High Concurrency, Memory Leaks, or Architecture. "
            "Example: 'How would you design a URL shortener?', 'Explain how you would debug a high latency issue in production.', 'Compare Optimistic vs Pessimistic locking.'"
        )
        user_prompt = (
            f"Skills: {', '.join(skills[:15])}\n"
            f"Experience: {experience}\n"
            f"Previous questions: {' | '.join(previous_questions[-3:])}\n"
            f"Generate a challenging System Design or Advanced Concept question #{question_number}."
        )
    else:
        system_prompt = (
            "You are a Hiring Manager at a major tech firm. "
            "Generate one Behavioral question using the STAR method format. "
            "Focus on conflict resolution, leadership, failure analysis, or ambition. "
            "Avoid generic 'What is your weakness?' cliches. "
            "Example: 'Tell me about a time you disagreed with a technical decision. How did you handle it?'"
        )
        user_prompt = (
            f"Previous HR questions: {' | '.join(previous_questions[-3:])}\n"
            f"Generate a meaningful behavioral question #{question_number}."
        )

    response = await _call_azure_openai(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=200,
    )

    if response:
        return {"question": response.strip(), "source": question_type}

    return None

async def extract_resume_data(text: str) -> Dict[str, Any]:
    # ... (start of function logic unchanged) ...
    if not _is_configured():
        # Fallback Mock Data if AI is not configured
        return {
            "technical_skills": {
                "programming": ["Python (Mock)", "JavaScript (Mock)"],
                "frameworks": ["Django", "React"],
                "tools": ["Git", "Docker"],
                "databases": ["PostgreSQL"]
            },
            "experience_level": "intermediate",
            "education": {"mentions": ["University Degree"], "level": "bachelor"},
            "projects": ["Mock Project 1", "Mock Project 2"],
            "soft_skills": ["Communication", "Teamwork"],
            "certifications": [],
            "completeness_score": 75
        }

    system_prompt = (
        "You are an expert resume parser. Extract structured data from the resume text. "
        "Return ONLY the valid JSON object. Do not include markdown formatting or explanations."
    )
    user_prompt = f"Resume Text:\n{text[:4000]}\n\nExtract data as JSON."

    response = await _call_azure_openai(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1000,
    )

    if response:
        try:
            # Clean up potential markdown formatting in response (e.g. ```json ... ```)
            cleaned = response.replace("```json", "").replace("```", "").strip()
            # Try to find JSON start/end if extra text exists
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                cleaned = cleaned[start:end]
            
            data = json.loads(cleaned)
            return data
        except Exception as e:
            print(f"[ai] JSON parse error in resume analysis: {e}")
            print(f"[ai] Raw response was: {response[:200]}...") # Log beginning of failed response
            
    return {}


async def evaluate_response(question: str, answer: str) -> Dict[str, Any]:
    # ... (docstring) ...
    if not _is_configured():
         # ... (fallback logic unchanged) ...
        wc = len(answer.split())
        return {
            "quality_score": min(10, max(1, wc // 5)),
            "feedback": "Good effort (Mock Evaluation).",
            "detected_skills": [],
            "confidence": "low"
        }

    system_prompt = (
        "You are a technical interviewer. Evaluate the candidate's answer. "
        "Return ONLY JSON: { \"quality_score\": number, \"feedback\": string, \"detected_skills\": [string] }"
    )
    
    user_prompt = f"Question: {question}\nCandidate Answer: {answer}"

    response = await _call_azure_openai(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=400,
    )

    if response:
        try:
            cleaned = response.replace("```json", "").replace("```", "").strip()
            # Try to find JSON start/end if extra text exists
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                cleaned = cleaned[start:end]
            
            return json.loads(cleaned)
        except Exception as e:
            print(f"[ai] JSON parse error in evaluation: {e}")
            print(f"[ai] Raw response was: {response[:200]}...")

    # Default fallback on failure
    return {
        "quality_score": 5,
        "feedback": "Unable to evaluate response at this time.",
        "detected_skills": [],
        "confidence": "low"
    }

