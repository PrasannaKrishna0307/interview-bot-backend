import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import sys

load_dotenv()

async def check_users():
    uri = os.getenv("MONGODB_URI")
    if not uri:
        print("❌ MONGODB_URI missing")
        return

    client = AsyncIOMotorClient(uri)
    db = client.interview_bot
    
    print("\nChecking 'users' collection:")
    count = await db.users.count_documents({})
    print(f"Total Users: {count}")
    
    async for user in db.users.find({}).limit(5):
        print(f" - Found User: Email={user.get('email')}, Name={user.get('name')}, Role={user.get('role')}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(check_users())
