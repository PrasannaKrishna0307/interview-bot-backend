from datetime import datetime
from typing import Dict, Any, List


def generate_report_summary(session: Dict[str, Any]) -> Dict[str, Any]:
    responses: List[Dict[str, Any]] = session.get("responses", [])
    scores = [r.get("quality_score", 0) for r in responses if isinstance(r, dict)]
    avg = sum(scores) / len(scores) if scores else 0
    skills = list({s for r in responses for s in r.get("detected_skills", [])})
    rating = "Excellent" if avg > 8 else "Good" if avg > 6 else "Average"

    return {
        "average_score": round(avg, 1),
        "questions": len(scores),
        "skills": skills,
        "rating": rating,
        "completed_at": datetime.utcnow().isoformat(),
    }

