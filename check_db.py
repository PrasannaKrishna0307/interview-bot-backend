import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import sys

# Load env vars similar to the app
load_dotenv()

async def check_mongo():
    uri = os.getenv("MONGODB_URI")
    print(f"1. Checking Enviroment Variable...")
    if not uri:
        print("❌ MONGODB_URI is MISSING from .env")
        return
    else:
        # Mask the password for security in logs
        masked_uri = uri.split("@")[-1] if "@" in uri else "..."
        print(f"✅ Found MONGODB_URI: ...{masked_uri}")

    print(f"\n2. Connecting to MongoDB...")
    try:
        client = AsyncIOMotorClient(uri)
        # Force a connection to verify
        await client.admin.command('ping')
        print("✅ Connection Successful!")
    except Exception as e:
        print(f"❌ Connection FAILED: {e}")
        return

    print(f"\n3. Verifying Database 'interview_bot'...")
    db = client.interview_bot
    
    print("   Attempting to write a test document...")
    try:
        result = await db.connection_test.insert_one({"status": "working", "timestamp": "now"})
        print(f"✅ Write Successful! Inserted ID: {result.inserted_id}")
        
        doc = await db.connection_test.find_one({"_id": result.inserted_id})
        print(f"✅ Read Successful! Found: {doc}")
        
        # Cleanup
        await db.connection_test.delete_one({"_id": result.inserted_id})
        print("✅ Cleanup Successful (Deleted test doc)")
        
        print("\nSUMMARY:")
        print(f"Target Database Name: '{db.name}'")
        print("If you are looking in Atlas, make sure you are in the 'interview_bot' database!")
        
    except Exception as e:
        print(f"❌ Write/Read Failed: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(check_mongo())
