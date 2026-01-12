"""
FastAPI REST API wrapper for the AI Interview System
This exposes the interview.py functionality as REST endpoints
"""

import os
import sys
import json
import uuid
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import tempfile
import shutil
import pdfplumber
from passlib.context import CryptContext
from ai_engine import generate_ai_question, extract_resume_data, evaluate_response, generate_clarifying_question
from report_engine import generate_report_summary

# Load environment variables
from dotenv import load_dotenv
import pathlib

# Load .env from backend directory (try multiple paths)
env_paths = [
    pathlib.Path(__file__).parent / ".env",  # scripts/backend/.env
    pathlib.Path(__file__).parent.parent.parent / ".env",  # root .env
    pathlib.Path.cwd() / ".env",  # current working directory
]

for env_path in env_paths:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
        print(f"✅ Loaded .env from: {env_path}")
        break
else:
    # Fallback: try loading from default location
    load_dotenv()
    print("⚠️ Using default .env loading")

# Debug: Verify environment variables (before importing ai_engine)
print(f"🔍 Environment check:")
print(f"   MONGODB_URI: {'✅ Set' if os.getenv('MONGODB_URI') else '❌ Missing'}")
print(f"   JWT_SECRET: {'✅ Set' if os.getenv('JWT_SECRET') and os.getenv('JWT_SECRET') != 'change-me' else '❌ Missing or default'}")
# Check for Azure OpenAI (both possible variable names)
azure_endpoint = os.getenv('AZURE_OPENAI_ENDPOINT') or os.getenv('AZURE_OPENAI_BASE')
azure_key = os.getenv('AZURE_OPENAI_KEY')
print(f"   AZURE_OPENAI_ENDPOINT/BASE: {'✅ Set' if azure_endpoint else '❌ Missing'}")
print(f"   AZURE_OPENAI_KEY: {'✅ Set' if azure_key else '❌ Missing'}")
# Note: Detailed Azure OpenAI check will be shown by ai_engine.py after import

# MongoDB for session storage
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from auth import create_access_token, verify_token

# Initialize FastAPI app
app = FastAPI(title="AI Interview Bot API", version="1.0.0")

# Password hashing using Argon2 (more robust than bcrypt on Windows/Newer Python)
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
# JWT security
security = HTTPBearer()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "chrome-extension://jonnmcjmdjdjgpnbgpmjdcjcfccconml",
        "http://localhost:3000",
        "http://localhost:3001"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB connection
MONGODB_URI = os.getenv("MONGODB_URI", "")
db_client = None
db = None

# In-memory session store (fallback when MongoDB not available)
sessions: Dict[str, Dict[str, Any]] = {}
users: Dict[str, Dict[str, Any]] = {}
question_sets: Dict[str, Dict[str, Any]] = {}


def normalize_session(session: Dict[str, Any]) -> Dict[str, Any]:
    session.setdefault("responses", [])
    session.setdefault("questions_asked", [])
    session.setdefault("conversation_history", [])
    session.setdefault("status", "in_progress")
    session.setdefault("total_questions", 15)
    session.setdefault("question_count", 0)
    session.setdefault("current_stage", 0)
    session.setdefault("retry_count", 0)
    session.setdefault("clarification_count", 0)
    session.setdefault("answers", [])
    return session

# Request/Response Models
class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    role: str = "candidate"

class StartInterviewRequest(BaseModel):
    session_id: str

class RespondRequest(BaseModel):
    session_id: str
    response: str

class QuestionSetRequest(BaseModel):
    category: str
    domain: Optional[str] = None
    questions: List[Dict[str, Any]]

class UpdateQuestionSetRequest(BaseModel):
    id: str
    isActive: bool

# Database initialization
@app.on_event("startup")
async def startup_db():
    global db_client, db
    if MONGODB_URI:
        try:
            db_client = AsyncIOMotorClient(MONGODB_URI)
            db = db_client.interview_bot
            print("✅ Connected to MongoDB")
        except Exception as e:
            print(f"⚠️ MongoDB connection failed: {e}. Using in-memory storage.")
    else:
        print("⚠️ No MONGODB_URI set. Using in-memory storage.")
    
    # Debug: Print all registered routes
    print("🔍 Registered Routes:")
    for route in app.routes:
        if hasattr(route, "path"):
            print(f"   {route.methods} {route.path}")

@app.on_event("shutdown")
async def shutdown_db():
    global db_client
    if db_client:
        db_client.close()

# Helper function to get or create session
async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    print(f"Fetching session with ID: {session_id}")
    if db is not None:  # Explicitly check if db is not None
        session = await db.sessions.find_one({"session_id": session_id})
        print(f"Session found in DB: {session}")
        if session is None:
            # Create a fully-initialized session document
            new_session = {
                "session_id": session_id,
                "status": "created",
                "created_at": datetime.utcnow(),
                "current_question": 0,
                "current_stage": 0,
                "question_count": 0,
                "answers": [],
                "responses": [],
                "questions_asked": [],
                "conversation_history": [],
                "total_questions": 15,
            }
            await db.sessions.insert_one(new_session)
            print(f"New session created in DB: {new_session}")
            return new_session

        # Backfill missing fields for older session documents
        updated_fields: Dict[str, Any] = {}
        if "questions_asked" not in session:
            session["questions_asked"] = []
            updated_fields["questions_asked"] = []
        if "conversation_history" not in session:
            session["conversation_history"] = []
            updated_fields["conversation_history"] = []
        if "responses" not in session:
            session["responses"] = []
            updated_fields["responses"] = []
        if "answers" not in session:
            session["answers"] = []
            updated_fields["answers"] = []
        if "total_questions" not in session:
            session["total_questions"] = 15
            updated_fields["total_questions"] = 15
        if "question_count" not in session:
            session["question_count"] = 0
            updated_fields["question_count"] = 0
        if "current_stage" not in session:
            session["current_stage"] = 0
            updated_fields["current_stage"] = 0

        if updated_fields:
            await db.sessions.update_one(
                {"session_id": session_id},
                {"$set": updated_fields}
            )
            print(f"Updated session with missing fields: {updated_fields}")

        return normalize_session(session)

    # In-memory fallback
    session = sessions.get(session_id)
    print(f"Session found in memory: {session}")
    if session is None:
        new_session = {
            "session_id": session_id,
            "status": "created",
            "created_at": datetime.utcnow(),
            "current_question": 0,
            "current_stage": 0,
            "question_count": 0,
            "answers": [],
            "responses": [],
            "questions_asked": [],
            "conversation_history": [],
            "total_questions": 15,
        }
        sessions[session_id] = new_session
        print(f"New session created in memory: {new_session}")
        return new_session
    # Ensure structure exists for old in-memory sessions too
    return normalize_session(session)

async def save_session(session_id: str, data: Dict[str, Any]):
    print(f"Saving session with ID: {session_id}, Data: {data}")
    if db is not None:
        await db.sessions.update_one(
            {"session_id": session_id},
            {"$set": data},
            upsert=True
        )
        print("Session saved to DB")
    else:
        sessions[session_id] = data
        print("Session saved to memory")

# ============ AUTH ENDPOINTS ============

@app.post("/api/auth/register")
async def register(request: RegisterRequest):
    """Register a new user"""
    print(f"🔹 Register Request for: {request.email}")
    if db is not None:
        try:
            existing = await db.users.find_one({"email": request.email})
            if existing:
                print(f"❌ Email {request.email} already exists")
                raise HTTPException(status_code=400, detail="Email already registered")
            
            print(f"🔹 Hashing password with Argon2...")
            pwd = request.password
            hashed = pwd_context.hash(pwd)
            print(f"✅ Generated Hash type: {hashed.split('$')[1] if '$' in hashed else 'UNKNOWN'}")
            print(f"✅ Generated Hash prefix: {hashed[:20]}...")
            
            user_id = str(uuid.uuid4())
            user = {
                "id": user_id,
                "email": request.email,
                "password": hashed,
                "name": request.name,
                "role": request.role,
                "created_at": datetime.utcnow().isoformat()
            }
            await db.users.insert_one(user)
            print(f"✅ User inserted into DB: {user_id}")
            
        except HTTPException as he:
            raise he
        except Exception as e:
            print(f"❌ CRITICAL REGISTER ERROR: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Server Error: {str(e)}")
    else:
        if request.email in users:
            raise HTTPException(status_code=400, detail="Email already registered")
        user_id = str(uuid.uuid4())
        user = {
            "id": user_id,
            "email": request.email,
            "password": pwd_context.hash(request.password),
            "name": request.name,
            "role": request.role,
            "created_at": datetime.utcnow().isoformat()
        }
        users[request.email] = user
    
    # Create a session for the user
    session_id = str(uuid.uuid4())
    session = {
        "session_id": session_id,
        "user_id": user_id,
        "status": "new",
        "questions_asked": [],
        "conversation_history": [],
        "responses": [],
        "answers": [],
        "question_count": 0,
        "current_stage": 0,
        "total_questions": 15,
        "created_at": datetime.utcnow().isoformat()
    }
    await save_session(session_id, session)

    return {
        "success": True,
        "user": {
            "id": user_id,
            "email": request.email,
            "name": request.name,
            "role": request.role
        },
        "session_id": session_id
    }

@app.post("/api/auth/login")
async def login(request: LoginRequest):
    """Login user"""
    print(f"Login attempt for email: {request.email}")
    user = None
    if db is not None:
        user = await db.users.find_one({"email": request.email})
        if user:
            # Convert MongoDB ObjectId to string if needed
            if "_id" in user and "id" not in user:
                user["id"] = str(user["_id"])
            print(f"User found in DB: {user.get('email')}")
    else:
        user = users.get(request.email)
        if user:
            print(f"User found in memory: {user.get('email')}")
    
    if not user:
        print(f"User not found for email: {request.email}")
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate password
    # In a real app, use the hash from DB
    if "password" in user:
        stored_password = user["password"]
        print(f"🔹 Verifying against stored hash prefix: {stored_password[:20]}...")
        # Check if hash type match
        if stored_password.startswith("$argon2"):
             print("✅ Stored hash IS Argon2")
        elif stored_password.startswith("$2b$"):
             print("⚠️ Stored hash IS Bcrypt")
        else:
             print(f"⚠️ Stored hash unknown type: {stored_password[:10]}")

        try:
            is_valid = pwd_context.verify(request.password, stored_password)
            print(f"🔹 Verification Result: {is_valid}")
        except Exception as ve:
             print(f"❌ Verification Exception: {ve}")
             is_valid = False

        if not is_valid:
            print(f"❌ Password mismatch for email: {request.email}")
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        # Fallback for demo users
        print("User has no password (demo mode fallback)")
        is_valid = True
    
    user_id = user.get("id") or str(user.get("_id", ""))
    print(f"Login successful for user: {user.get('email')}")
    
    token = create_access_token({
        "sub": user_id,
        "email": user.get("email"),
        "role": user.get("role")
    })

    return {
        "success": True,
        "user": {
            "id": user_id,
            "email": user.get("email"),
            "name": user.get("name"),
            "role": user.get("role")
        },
        "token": token
    }


def get_authenticated_user(creds: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token_preview = creds.credentials[:30] + "..." if len(creds.credentials) > 30 else creds.credentials
        print(f"🔐 Auth token received: {token_preview}")
        payload = verify_token(creds.credentials)
        print(f"✅ Token verified for user: {payload.get('email', 'unknown')} (role: {payload.get('role', 'unknown')})")
        return payload
    except Exception as e:
        print(f"❌ Token verification failed: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def require_hr(user=Depends(get_authenticated_user)):
    if user.get("role") != "hr":
        raise HTTPException(status_code=403, detail="Forbidden")
    return user

# Define the /api/auth/me route
@app.get("/api/auth/me")
async def auth_me(user=Depends(get_authenticated_user)):
    # Fetch full user profile from DB to get the Name
    if db is not None:
        db_user = await db.users.find_one({"id": user.get("sub")})
        if db_user:
            return {
                "user": {
                    "id": db_user.get("id"),
                    "email": db_user.get("email"),
                    "name": db_user.get("name"), # Now verified to exist
                    "role": db_user.get("role"),
                }
            }
    
    # Fallback if DB fails
    return {
        "user": {
            "id": user.get("sub"),
            "email": user.get("email"),
            "role": user.get("role"),
        }
    }

@app.post("/api/auth/logout")
async def logout():
    """Logout endpoint - client should remove token from localStorage"""
    return {"success": True, "message": "Logged out"}

# ============ INTERVIEW ENDPOINTS ============

@app.post("/api/interview/upload-resume")
async def upload_resume(resume: UploadFile = File(...), user=Depends(get_authenticated_user)):
    """Upload and analyze resume"""
    session_id = str(uuid.uuid4())
    
    # Save uploaded file temporarily
    temp_dir = tempfile.mkdtemp()
    file_path = os.path.join(temp_dir, resume.filename)
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(resume.file, buffer)
        
        # Real PDF Extraction
        text = ""
        try:
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    text += (page.extract_text() or "") + "\n"
        except Exception as e:
            print(f"PDF extract error: {e}")
            text = "Error extracting text."

        # AI Analysis
        analysis = await extract_resume_data(text)
        
        # Create session data
        user_id = user.get("sub")  # Get user ID from JWT token
        session_data = {
            "session_id": session_id,
            "user_id": user_id,  # Link session to user
            "resume_filename": resume.filename,
            "resume_analysis": analysis,
            "status": "resume_uploaded",
            "question_count": 0,
            "total_questions": 15,
            "current_stage": 0,
            "conversation_history": [],
            "questions_asked": [],
            "responses": [],
            "answers": [],
            "created_at": datetime.utcnow().isoformat()
        }
        
        await save_session(session_id, session_data)
        
        return {
            "session_id": session_id,
            "analysis": analysis
        }
        
    finally:
        # Cleanup temp file
        shutil.rmtree(temp_dir, ignore_errors=True)

@app.post("/api/interview/start")
async def start_interview(request: StartInterviewRequest, user=Depends(get_authenticated_user)):
    """Start interview session"""
    session = await get_session(request.session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Ensure required fields always exist to avoid KeyError
    session.setdefault("status", "created")
    session.setdefault("question_count", 0)
    session.setdefault("current_stage", 0)
    session.setdefault("questions_asked", [])
    session.setdefault("conversation_history", [])
    session.setdefault("responses", [])
    session.setdefault("answers", [])
    session.setdefault("total_questions", 15)
    session["clarification_count"] = 0 # Ensure clean start

    # Generate opening question based on resume analysis
    analysis = session.get("resume_analysis", {})
    skills = list(analysis.get("technical_skills", {}).keys())
    
    opening_question = (
        "Hello! I'm your AI interviewer today. I've reviewed your resume and I can see you have "
        f"experience with {', '.join(skills[:3]) if skills else 'various technologies'}. "
        "Let's start with an easy one - can you tell me about yourself and what excites you most "
        "about software development?"
    )
    
    # Update session
    session["status"] = "in_progress"
    session["question_count"] = 1
    session["current_stage"] = 1
    session["questions_asked"].append({
        "number": 1,
        "text": opening_question,
        "type": "intro",
        "stage": 1,
        "source": "resume",
        "asked_at": datetime.utcnow().isoformat()
    })
    session["conversation_history"].append({
        "role": "assistant",
        "content": opening_question
    })
    
    await save_session(request.session_id, session)
    
    return {
        "question": opening_question,
        "question_number": 1,
        "stage": 1,
        "total_questions": 15,
        "stage_info": {
            "current": "Resume-Based Questions",
            "stages": [
                {"name": "Resume-Based", "questions": "1-5", "description": "Questions from your resume skills and projects"},
                {"name": "General Technical", "questions": "6-10", "description": "General technical questions based on your skill set"},
                {"name": "HR & Behavioral", "questions": "11-15", "description": "HR and behavioral interview questions"}
            ]
        }
    }

@app.post("/api/interview/respond")
async def respond_to_question(request: RespondRequest, user=Depends(get_authenticated_user)):
    """Process response and get next question"""
    session = await get_session(request.session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Ensure structure is always present to avoid KeyError
    session.setdefault("responses", [])
    session.setdefault("questions_asked", [])
    session.setdefault("conversation_history", [])
    session.setdefault("question_count", 0)
    session.setdefault("total_questions", 15)
    session.setdefault("current_stage", 0)
    session.setdefault("answers", [])

    current_q = session.get("question_count", 0)
    total_q = session.get("total_questions", 15)
    
    # Analyze response using AI
    response_text = request.response
    
    # Get current question text for context
    current_question_text = "Unknown Question"
    if session.get("questions_asked"):
        current_question_text = session["questions_asked"][-1].get("text", "")

    # AI Evaluation
    evaluation = await evaluate_response(current_question_text, response_text)
    
    quality_score = evaluation.get("quality_score", 5)
    detected_skills = evaluation.get("detected_skills", [])
    feedback_text = evaluation.get("feedback", "")
    
    # Save response
    session["responses"].append({
        "question_number": current_q,
        "text": response_text,
        "quality_score": quality_score,
        "detected_skills": detected_skills,
        "confidence_level": "high" if quality_score > 7 else "medium" if quality_score > 4 else "low",
        "responded_at": datetime.utcnow().isoformat()
    })
    
    session["conversation_history"].append({
        "role": "user",
        "content": response_text
    })
    
    # Check if interview is complete (fix: stop at exactly total_questions)
    if current_q + 1 > total_q:
        session["status"] = "completed"
        session["completed_at"] = datetime.utcnow().isoformat()
        session["report"] = generate_report_summary(session)
        await save_session(request.session_id, session)
        
        return {
            "is_complete": True,
            "message": "Interview completed! Thank you for your time.",
            "question_number": current_q,
            "stage": 3,
            "total_questions": total_q,
            "report": session["report"],
        }
    
    # === Retry Logic for Poor Answers ===
    retry_count = session.get("retry_count", 0)
    
    # If answer is poor (score < 5) and we haven't retried 2 times yet
    if quality_score < 5 and retry_count < 2:
        print(f"⚠️ Poor answer (Score: {quality_score}). Triggering clarification (Retry {retry_count + 1}/2)")
        
        clarifying_q = await generate_clarifying_question(
            original_question=current_question_text,
            candidate_answer=response_text,
            resume_context=session.get("resume_analysis", {})
        )
        
        if clarifying_q:
            # Increment retry count but keep same question number
            session["retry_count"] = retry_count + 1
            
            # Add to history
            full_response = f"{feedback_text or 'I see.'} {clarifying_q}"
            session["conversation_history"].append({
                "role": "assistant",
                "content": full_response
            })
            
            await save_session(request.session_id, session)
            
            return {
                "question": full_response,
                "question_number": current_q, # Stay on same question
                "stage": session.get("current_stage", 1),
                "total_questions": total_q,
                "feedback": feedback_text, # Show feedback but ask to clarify
                "analysis": {
                    "quality_score": quality_score,
                    "detected_skills": detected_skills,
                    "confidence_level": "low"
                }
            }
            
    # Reset retry count if we are moving to next question
    session["retry_count"] = 0
    
    # Generate next question (AI first, fallback static)
    next_q_num = current_q + 1
    stage = 1 if next_q_num <= 5 else 2 if next_q_num <= 10 else 3
    
    analysis = session.get("resume_analysis", {})
    previous_questions_text = [q.get("text", "") for q in session.get("questions_asked", [])]
    hr_technical: List[str] = []
    hr_behavioral: List[str] = []
    if db is not None:
        try:
            async for qs in db.question_sets.find({"isActive": True}):
                qs_questions = qs.get("questions", [])
                cat = qs.get("category", "").lower()
                
                # Determine target list based on category
                target_list = hr_technical
                if any(k in cat for k in ["hr", "behavioral", "soft", "culture", "person"]):
                    target_list = hr_behavioral
                
                for q in qs_questions:
                    if isinstance(q, dict) and "text" in q:
                        target_list.append(q["text"])
                    elif isinstance(q, str):
                        target_list.append(q)
        except Exception:
            pass

    question_type = "resume" if stage == 1 else "general" if stage == 2 else "hr"
    
    # Select pools
    relevant_hr_questions = []
    if question_type == "general":
        relevant_hr_questions = hr_technical
    elif question_type == "hr":
        relevant_hr_questions = hr_behavioral

    ai_question = await generate_ai_question(
        resume_analysis=analysis,
        question_type=question_type,
        question_number=next_q_num,
        previous_questions=previous_questions_text,
        hr_questions=relevant_hr_questions,
    )

    if ai_question:
        next_question = ai_question["question"]
        source = ai_question.get("source", question_type)
    else:
        if stage == 1:
            projects = analysis.get("projects", [])
            skills = analysis.get("technical_skills", {})
            questions_pool = [
                f"Tell me about your experience with {list(skills.get('frameworks', ['web development']))[0] if isinstance(skills.get('frameworks'), list) and skills.get('frameworks') else 'web development'}.",
                f"Can you walk me through one of your projects - {projects[0] if projects else 'a recent project'}?",
                "What was the most challenging technical problem you've solved?",
                "How do you approach learning new technologies?",
                "Describe a time when you had to debug a complex issue."
            ]
            next_question = questions_pool[min(next_q_num - 1, len(questions_pool) - 1)]
            source = "resume"
        elif stage == 2:
            questions_pool = [
                "Explain the difference between REST and GraphQL APIs.",
                "How would you design a scalable microservices architecture?",
                "What are the key principles of clean code?",
                "Describe your approach to testing software.",
                "How do you handle database optimization?"
            ]
            next_question = questions_pool[min(next_q_num - 6, len(questions_pool) - 1)]
            source = "general"
        else:
            questions_pool = [
                "Tell me about a time you had a conflict with a team member. How did you resolve it?",
                "Where do you see yourself in 5 years?",
                "What motivates you in your work?",
                "Describe a situation where you had to meet a tight deadline.",
                "Why are you interested in this position?"
            ]
            next_question = questions_pool[min(next_q_num - 11, len(questions_pool) - 1)]
            source = "hr"
    q_type = "technical" if stage in [1, 2] else "behavioral"
    
    # Add feedback based on AI evaluation
    feedback = ""
    if feedback_text:
        feedback = feedback_text
    elif quality_score > 7:
        feedback = "Great answer! I appreciate the detail you provided."
    elif quality_score > 4:
        feedback = "Good response."
    else:
        feedback = "Thank you for your answer."
    
    # Determine if we need a clarifying question (Low score + max 2 retries)
    clarification_count = session.get("clarification_count", 0)
    
    print(f"🧐 CONFIG CHECK: Clarification Logic -> Score: {quality_score}, Count: {clarification_count}")

    next_question = ""
    source = ""
    stage = session.get("current_stage", 1)
    next_q_num = current_q + 1
    
    # If answer is poor (<= 5) and we haven't reached max retries (2)
    if quality_score <= 5 and clarification_count < 2:
        print(f"⚠️ Triggering Clarification! (Attempt {clarification_count + 1})")
        analysis = session.get("resume_analysis", {})
        clarifying_q = await generate_clarifying_question(
            original_question=current_question_text,
            candidate_answer=response_text,
            resume_context=analysis
        )
        if not clarifying_q:
            print("❌ Clarification generation failed. Using fallback.")
            # Fallback static clarification
            clarifying_q = "Could you please elaborate more on that answer? Providing specific details would really help."
            
        next_question = clarifying_q
        next_q_num = current_q # Remain on same number
        source = "ai_clarification_fallback"
        q_type = "clarification"
        # Adjust feedback to be encouraging
        feedback = f"{feedback} Let's dig a little deeper."
        
        # Increment counter
        session["clarification_count"] = clarification_count + 1
    else:
        print("✅ Answer accepted (or max retries). Moving to next question.")
        # Good answer OR max retries reached -> Move to next
        session["clarification_count"] = 0 # Reset counter

    
    # If no clarification needed (formatted as standard flow)
    if not next_question:
        # Generate next question (AI first, fallback static)
        # Recalculate stage based on next_q_num
        stage = 1 if next_q_num <= 5 else 2 if next_q_num <= 10 else 3
        
        analysis = session.get("resume_analysis", {})
        previous_questions_text = [q.get("text", "") for q in session.get("questions_asked", [])]
        hr_technical: List[str] = []
        hr_behavioral: List[str] = []
        if db is not None:
            try:
                async for qs in db.question_sets.find({"isActive": True}):
                    qs_questions = qs.get("questions", [])
                    cat = qs.get("category", "").lower()
                    
                    # Determine target list based on category
                    target_list = hr_technical
                    if any(k in cat for k in ["hr", "behavioral", "soft", "culture", "person"]):
                        target_list = hr_behavioral
                    
                    for q in qs_questions:
                        if isinstance(q, dict) and "text" in q:
                            target_list.append(q["text"])
                        elif isinstance(q, str):
                            target_list.append(q)
            except Exception:
                pass

        question_type = "resume" if stage == 1 else "general" if stage == 2 else "hr"
        
        # Select pools
        relevant_hr_questions = []
        if question_type == "general":
            relevant_hr_questions = hr_technical
        elif question_type == "hr":
            relevant_hr_questions = hr_behavioral

        ai_question = await generate_ai_question(
            resume_analysis=analysis,
            question_type=question_type,
            question_number=next_q_num,
            previous_questions=previous_questions_text,
            hr_questions=relevant_hr_questions,
        )

        if ai_question:
            next_question = ai_question["question"]
            source = ai_question.get("source", question_type)
        else:
            if stage == 1:
                projects = analysis.get("projects", [])
                skills = analysis.get("technical_skills", {})
                questions_pool = [
                    f"Tell me about your experience with {list(skills.get('frameworks', ['web development']))[0] if isinstance(skills.get('frameworks'), list) and skills.get('frameworks') else 'web development'}.",
                    f"Can you walk me through one of your projects - {projects[0] if projects else 'a recent project'}?",
                    "What was the most challenging technical problem you've solved?",
                    "How do you approach learning new technologies?",
                    "Describe a time when you had to debug a complex issue."
                ]
                # Safe index access
                idx = min(max(0, next_q_num - 1), len(questions_pool) - 1)
                next_question = questions_pool[idx]
                source = "resume"
            elif stage == 2:
                questions_pool = [
                    "Explain the difference between REST and GraphQL APIs.",
                    "How would you design a scalable microservices architecture?",
                    "What are the key principles of clean code?",
                    "Describe your approach to testing software.",
                    "How do you handle database optimization?"
                ]
                idx = min(max(0, next_q_num - 6), len(questions_pool) - 1)
                next_question = questions_pool[idx]
                source = "general"
            else:
                questions_pool = [
                    "Tell me about a time you had a conflict with a team member. How did you resolve it?",
                    "Where do you see yourself in 5 years?",
                    "What motivates you in your work?",
                    "Describe a situation where you had to meet a tight deadline.",
                    "Why are you interested in this position?"
                ]
                idx = min(max(0, next_q_num - 11), len(questions_pool) - 1)
                next_question = questions_pool[idx]
                source = "hr"
        
        q_type = "technical" if stage in [1, 2] else "behavioral"
    
    # Do not prepend feedback to the question text
    full_question = next_question
    
    # Update session
    session["question_count"] = next_q_num
    session["current_stage"] = stage
    session["questions_asked"].append({
        "number": next_q_num,
        "text": next_question,
        "type": q_type,
        "stage": stage,
        "source": source,
        "asked_at": datetime.utcnow().isoformat()
    })
    session["conversation_history"].append({
        "role": "assistant",
        # Save both feedback and question in history for context
        "content": f"{feedback} {next_question}"
    })
    
    await save_session(request.session_id, session)
    
    return {
        "question": full_question,
        "question_number": next_q_num,
        "stage": stage,
        "total_questions": total_q,
        "feedback": feedback.strip() if feedback else None,
        "analysis": {
            "quality_score": quality_score,
            "detected_skills": detected_skills,
            "confidence_level": "high" if quality_score > 7 else "medium" if quality_score > 4 else "low"
        }
    }

@app.get("/api/interview/report/{session_id}")
async def get_report(session_id: str, user=Depends(get_authenticated_user)):
    """Get detailed interview report"""
    session = await get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    analysis = session.get("resume_analysis", {})
    responses = session.get("responses", [])
    questions = session.get("questions_asked", [])
    
    # Calculate metrics
    avg_score = sum(r.get("quality_score", 0) for r in responses) / len(responses) if responses else 0
    all_skills = [s for r in responses for s in r.get("detected_skills", [])]
    skill_counts = {}
    for skill in all_skills:
        skill_counts[skill] = skill_counts.get(skill, 0) + 1
    
    # Build conversation log
    conversation_log = []
    for i, q in enumerate(questions):
        response = responses[i] if i < len(responses) else None
        conversation_log.append({
            "question": q,
            "response": response
        })
    
    # Fetch Candidate Name
    candidate_name = "Candidate"
    candidate_email = ""
    if db is not None:
        user_id = session.get("user_id")
        if user_id:
            user_doc = await db.users.find_one({"id": user_id})
            if user_doc:
                candidate_name = user_doc.get("name", "Candidate")
                candidate_email = user_doc.get("email", "")

    return {
        "interview_summary": {
            "session_id": session_id,
            "candidate_name": candidate_name,
            "candidate_email": candidate_email,
            "total_questions": len(questions),
            "total_responses": len(responses),
            "average_quality_score": round(avg_score, 1),
            "coverage_percentage": (len(responses) / 15) * 100,
            "difficulty_level": "intermediate",
            "status": session.get("status", "unknown"),
            "started_at": session.get("created_at"),
            "completed_at": session.get("completed_at"),
            "duration_minutes": 30
        },
        "skills_assessment": {
            "detected_skills": skill_counts,
            "strongest_skills": sorted(skill_counts.keys(), key=lambda x: skill_counts[x], reverse=True)[:5],
            "weakest_areas": [],
            "primary_technical_level": "intermediate",
            "strength_scores": {skill: min(10, count * 2) for skill, count in skill_counts.items()}
        },
        "recommendations": [
            "Continue developing your technical communication skills",
            "Practice explaining complex concepts simply",
            "Prepare more concrete examples from past projects"
        ],
        "performance_rating": {
            "rating": "Good" if avg_score > 6 else "Average",
            "description": "Shows good potential with room for improvement",
            "score": round(avg_score, 1)
        },
        "question_breakdown": {
            "resume_questions": sum(1 for q in questions if q.get("source") == "resume"),
            "general_questions": sum(1 for q in questions if q.get("source") == "general"),
            "hr_questions": sum(1 for q in questions if q.get("source") == "hr")
        },
        "conversation_log": conversation_log,
        "resume_analysis": analysis
    }

# ============ HR ENDPOINTS ============

@app.get("/api/hr/candidates")
async def get_candidates(user=Depends(require_hr)):
    """Get all candidates for HR dashboard"""
    candidates = []
    
    if db is not None:
        async for session in db.sessions.find({"status": {"$in": ["completed", "in_progress"]}}):
            responses = session.get("responses", [])
            score = sum(r.get("quality_score", 0) for r in responses) / max(len(responses), 1) if responses else 0
            
            # Fetch real name
            name = "Candidate"
            user_id = session.get("user_id")
            if user_id:
                u = await db.users.find_one({"id": user_id})
                if u:
                    name = u.get("name", "Candidate")

            candidates.append({
                "id": session.get("session_id"),
                "name": name,
                "email": f"candidate_{session.get('session_id')[:8]}@example.com",
                "status": session.get("status"),
                "score": score,
                "date": session.get("created_at"),
                "sessionId": session.get("session_id"),
                "totalInterviews": 1
            })
    else:
        for sid, session in sessions.items():
            if session.get("status") in ["completed", "in_progress"]:
                responses = session.get("responses", [])
                score = sum(r.get("quality_score", 0) for r in responses) / max(len(responses), 1) if responses else 0
                candidates.append({
                    "id": sid,
                    "name": "Candidate",
                    "email": f"candidate_{sid[:8]}@example.com",
                    "status": session.get("status"),
                    "score": score,
                    "date": session.get("created_at"),
                    "sessionId": sid,
                    "totalInterviews": 1
                })
    
    return candidates

@app.get("/api/hr/analytics")
async def get_analytics(user=Depends(require_hr)):
    """Get HR analytics dashboard data"""
    total_sessions = 0
    completed = 0
    in_progress = 0
    all_scores = []
    
    if db is not None:
        total_sessions = await db.sessions.count_documents({})
        completed = await db.sessions.count_documents({"status": "completed"})
        in_progress = await db.sessions.count_documents({"status": "in_progress"})
        
        async for session in db.sessions.find({"status": "completed"}):
            responses = session.get("responses", [])
            if responses:
                avg = sum(r.get("quality_score", 0) for r in responses) / len(responses)
                all_scores.append(avg)
    else:
        total_sessions = len(sessions)
        for session in sessions.values():
            if session.get("status") == "completed":
                completed += 1
                responses = session.get("responses", [])
                if responses:
                    avg = sum(r.get("quality_score", 0) for r in responses) / len(responses)
                    all_scores.append(avg)
            elif session.get("status") == "in_progress":
                in_progress += 1
    
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0
    
    return {
        "overview": {
            "totalCandidates": total_sessions,
            "totalInterviews": total_sessions,
            "inProgress": in_progress,
            "completed": completed,
            "averageScore": round(avg_score, 1)
        },
        "scoreDistribution": {
            "excellent": sum(1 for s in all_scores if s >= 8),
            "good": sum(1 for s in all_scores if 6 <= s < 8),
            "average": sum(1 for s in all_scores if 4 <= s < 6),
            "needsImprovement": sum(1 for s in all_scores if s < 4)
        },
        "skillAnalysis": [
            {"skill": "Python", "mentions": 15, "avgScore": 7.5},
            {"skill": "JavaScript", "mentions": 12, "avgScore": 7.2},
            {"skill": "React", "mentions": 10, "avgScore": 6.8}
        ],
        "performanceTrend": [
            {"date": "2024-01", "interviews": 5, "avgScore": 6.5},
            {"date": "2024-02", "interviews": 8, "avgScore": 7.0},
            {"date": "2024-03", "interviews": 12, "avgScore": 7.3}
        ]
    }

@app.get("/api/hr/questions")
async def get_question_sets(user=Depends(require_hr)):
    """Get all HR question sets"""
    sets = []
    
    if db is not None:
        async for qs in db.question_sets.find():
            if "_id" in qs:
                qs["_mongo_id"] = str(qs.pop("_id")) # Keep ref just in case, but usually not needed
            sets.append(qs)
    else:
        sets = list(question_sets.values())
    
    return sets

@app.post("/api/hr/questions")
async def create_question_set(request: QuestionSetRequest, user=Depends(require_hr)):
    """Create a new question set"""
    set_id = str(uuid.uuid4())
    
    new_set = {
        "id": set_id,
        "category": request.category,
        "domain": request.domain,
        "questionCount": len(request.questions),
        "questions": request.questions,
        "isActive": True,
        "uploadedBy": "hr",
        "createdAt": datetime.utcnow().isoformat(),
        "updatedAt": datetime.utcnow().isoformat()
    }
    
    if db is not None:
        await db.question_sets.insert_one(new_set)
    else:
        question_sets[set_id] = new_set
    
    return {"success": True, "id": set_id}

@app.delete("/api/hr/questions")
async def delete_question_set(id: str = Query(..., alias="id"), user=Depends(require_hr)):
    """Delete a question set"""
    if db is not None:
        result = await db.question_sets.delete_one({"id": id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Question set not found")
    else:
        if id not in question_sets:
            raise HTTPException(status_code=404, detail="Question set not found")
        del question_sets[id]
    
    return {"success": True, "message": "Question set deleted"}

class UpdateQuestionSetRequest(BaseModel):
    id: str
    isActive: Optional[bool] = None
    questions: Optional[List[Dict[str, Any]]] = None

@app.patch("/api/hr/questions")
async def update_question_set(request: UpdateQuestionSetRequest, user=Depends(require_hr)):
    """Update question set content or status"""
    update_data = {"updatedAt": datetime.utcnow().isoformat()}
    
    if request.isActive is not None:
        update_data["isActive"] = request.isActive
    if request.questions is not None:
        update_data["questions"] = request.questions
        update_data["questionCount"] = len(request.questions)

    if db is not None:
        result = await db.question_sets.update_one(
            {"id": request.id},
            {"$set": update_data}
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Question set not found")
    else:
        if request.id not in question_sets:
            raise HTTPException(status_code=404, detail="Question set not found")
        question_sets[request.id].update(update_data)
    
    return {"success": True, "message": "Question set updated"}

# ============ HR SETTINGS ENDPOINTS ============

class HRSettingsRequest(BaseModel):
    max_questions: int = 15
    adaptive_difficulty: bool = True
    voice_input: bool = True

@app.get("/api/hr/settings")
async def get_hr_settings(user=Depends(require_hr)):
    """Get HR configuration"""
    if db is not None:
        settings = await db.settings.find_one({"type": "hr_config"})
        if settings:
            return {
                "max_questions": settings.get("max_questions", 15),
                "adaptive_difficulty": settings.get("adaptive_difficulty", True),
                "voice_input": settings.get("voice_input", True)
            }
    # Default or fallback
    return {
        "max_questions": 15,
        "adaptive_difficulty": True,
        "voice_input": True
    }

@app.post("/api/hr/settings")
async def save_hr_settings(request: HRSettingsRequest, user=Depends(require_hr)):
    """Save HR configuration"""
    settings_data = {
        "type": "hr_config",
        "max_questions": request.max_questions,
        "adaptive_difficulty": request.adaptive_difficulty,
        "voice_input": request.voice_input,
        "updated_at": datetime.utcnow().isoformat(),
        "updated_by": user.get("sub")
    }
    
    if db is not None:
        await db.settings.update_one(
            {"type": "hr_config"}, 
            {"$set": settings_data}, 
            upsert=True
        )
        print("✅ HR Configuration saved to DB")
    else:
        print("⚠️ HR Configuration not saved (In-Memory mode)")
        
    return {"success": True, "message": "Settings saved"}

# ============ USER STATS ENDPOINTS ============

@app.get("/api/interview/history")
async def get_interview_history(user=Depends(get_authenticated_user)):
    """Get interview history for current user"""
    history = []
    user_id = user.get("sub")  # Get user ID from JWT token
    
    if db is not None:
        # Filter by user_id to show only current user's sessions
        async for session in db.sessions.find({"user_id": user_id}).sort("created_at", -1).limit(10):
            responses = session.get("responses", [])
            avg_score = sum(r.get("quality_score", 0) for r in responses) / len(responses) if responses else None
            
            history.append({
                "sessionId": session.get("session_id"),
                "status": session.get("status"),
                "questionsAnswered": len(responses),
                "totalQuestions": session.get("total_questions", 15),
                "averageScore": round(avg_score, 1) if avg_score else None,
                "performanceRating": "Good" if avg_score and avg_score > 6 else "Average" if avg_score else None,
                "startedAt": session.get("created_at"),
                "completedAt": session.get("completed_at"),
                "resumeFileName": session.get("resume_filename", "resume.pdf"),
                "strongestSkills": list(set([s for r in responses for s in r.get("detected_skills", [])]))[:3]
            })
    else:
        for sid, session in sorted(sessions.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)[:10]:
            responses = session.get("responses", [])
            avg_score = sum(r.get("quality_score", 0) for r in responses) / len(responses) if responses else None
            
            history.append({
                "sessionId": sid,
                "status": session.get("status"),
                "questionsAnswered": len(responses),
                "totalQuestions": session.get("total_questions", 15),
                "averageScore": round(avg_score, 1) if avg_score else None,
                "performanceRating": "Good" if avg_score and avg_score > 6 else "Average" if avg_score else None,
                "startedAt": session.get("created_at"),
                "completedAt": session.get("completed_at"),
                "resumeFileName": session.get("resume_filename", "resume.pdf"),
                "strongestSkills": list(set([s for r in responses for s in r.get("detected_skills", [])]))[:3]
            })
    
    return history

@app.get("/api/interview/stats")
async def get_user_stats(user=Depends(get_authenticated_user)):
    """Get user statistics"""
    total = 0
    completed = 0
    in_progress = 0
    all_scores = []
    all_skills = []
    user_id = user.get("sub")  # Get user ID from JWT token
    
    if db is not None:
        # Filter by user_id to show only current user's stats
        total = await db.sessions.count_documents({"user_id": user_id})
        completed = await db.sessions.count_documents({"user_id": user_id, "status": "completed"})
        in_progress = await db.sessions.count_documents({"user_id": user_id, "status": "in_progress"})
        
        async for session in db.sessions.find({"user_id": user_id, "status": "completed"}):
            responses = session.get("responses", [])
            if responses:
                avg = sum(r.get("quality_score", 0) for r in responses) / len(responses)
                all_scores.append(avg)
                for r in responses:
                    all_skills.extend(r.get("detected_skills", []))
    else:
        total = len(sessions)
        for session in sessions.values():
            if session.get("status") == "completed":
                completed += 1
                responses = session.get("responses", [])
                if responses:
                    avg = sum(r.get("quality_score", 0) for r in responses) / len(responses)
                    all_scores.append(avg)
                    for r in responses:
                        all_skills.extend(r.get("detected_skills", []))
            elif session.get("status") == "in_progress":
                in_progress += 1
    
    # Get top skills
    skill_counts = {}
    for skill in all_skills:
        skill_counts[skill] = skill_counts.get(skill, 0) + 1
    top_skills = sorted(skill_counts.keys(), key=lambda x: skill_counts[x], reverse=True)[:5]
    
    return {
        "totalInterviews": total,
        "completedInterviews": completed,
        "inProgress": in_progress,
        "averageScore": round(sum(all_scores) / len(all_scores), 1) if all_scores else 0,
        "topSkills": top_skills,
        "skillsImproved": len(set(all_skills)),
        "recentScores": [{"date": f"Interview {i+1}", "score": round(s, 1)} for i, s in enumerate(all_scores[-5:])],
        "lastInterviewDate": None
    }

# Health check
@app.get("/health")
async def health_check():
    return {"status": "healthy", "mongodb": "connected" if db is not None else "in-memory"}


class WarningRequest(BaseModel):
    type: str  # "tab_switch", "fullscreen_exit", "copy_paste", "multiple_faces"
    timestamp: Optional[str] = None
    details: Optional[str] = None

@app.post("/api/interview/{session_id}/warning")
async def report_warning(session_id: str, warning: WarningRequest, user=Depends(get_authenticated_user)):
    """Report a proctoring warning/incident for a session"""
    
    # Verify session belongs to user
    if db is not None:
        session = await db.sessions.find_one({"session_id": session_id})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        user_id = user.get("_id") if isinstance(user, dict) else None
        if not user or "_id" not in user:
            raise HTTPException(status_code=401, detail="Invalid user")
        if session.get("user_id") and session["user_id"] != str(user["_id"]):
            raise HTTPException(status_code=403, detail="Not authorized for this session")

        # Update session with warning
        incident = {
            "type": warning.type,
            "timestamp": warning.timestamp or datetime.utcnow().isoformat(),
            "details": warning.details
        }
        
        await db.sessions.update_one(
            {"session_id": session_id},
            {
                "$inc": {"warning_count": 1},
                "$push": {"incidents": incident}
            }
        )
        return {"success": True, "message": "Warning logged"}
    
    else:
        # In-memory fallback
        if session_id not in sessions:
            raise HTTPException(status_code=404, detail="Session not found")
            
        session = sessions[session_id]
        # Basic auth check fallback (if user_id stored)
        if session.get("user_id") and session["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized")

        if "warning_count" not in session:
            session["warning_count"] = 0
        if "incidents" not in session:
            session["incidents"] = []
            
        incident = {
            "type": warning.type,
            "timestamp": warning.timestamp or datetime.utcnow().isoformat(),
            "details": warning.details
        }
        
        session["warning_count"] += 1
        session["incidents"].append(incident)
        
        return {"success": True, "message": "Warning logged"}

@app.get("/")
async def root():
    return {"message": "API is running"}

if __name__ == "__main__":
    import uvicorn
    
    print("----- REGISTERED ROUTES START -----")
    for route in app.routes:
        if hasattr(route, "path"):
            print(f"Route: {route.methods} {route.path}")
    print("----- REGISTERED ROUTES END -----")

    uvicorn.run(app, host="0.0.0.0", port=8000)

