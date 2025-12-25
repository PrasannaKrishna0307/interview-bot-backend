import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import sys

load_dotenv()

async def clear_users():
    uri = os.getenv("MONGODB_URI")
    if not uri:
        print("❌ MONGODB_URI missing")
        return

    client = AsyncIOMotorClient(uri)
    db = client.interview_bot
    
    print("\n⚠️  Clearing 'users' collection to remove incompatible bcrypt hashes...")
    result = await db.users.delete_many({})
    print(f"✅ Deleted {result.deleted_count} users.")
    print("Now you can register fresh users with Argon2 hashes.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(clear_users())
